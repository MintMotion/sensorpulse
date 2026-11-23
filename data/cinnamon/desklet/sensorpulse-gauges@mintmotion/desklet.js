// SensorPulse — desklet de Cinnamon: indicadores sobre el escritorio (X11 y Wayland).
// Dibuja con cairo los mismos estilos que el programa (HUD, anillos, barras).
// Los datos vienen del servicio «sensorpulse-service» por D-Bus de sesión.

const Desklet = imports.ui.desklet;
const Settings = imports.ui.settings;
const St = imports.gi.St;
const Gio = imports.gi.Gio;
const GLib = imports.gi.GLib;
const Mainloop = imports.mainloop;
const Cairo = imports.cairo;
const Pango = imports.gi.Pango;
const PangoCairo = imports.gi.PangoCairo;
const Util = imports.misc.util;
const PopupMenu = imports.ui.popupMenu;

const BUS_NAME = "io.github.mintmotion.SensorPulse";
const BUS_PATH = "/io/github/mintmotion/SensorPulse";
const BUS_IFACE = "io.github.mintmotion.SensorPulse";

function stateSym(it) {
    if (it.asleep || it.kind !== "temp" || it.warn === null || it.value === null || it.value === undefined) return "";
    if (it.crit !== null && it.value >= it.crit) return "‼ ";
    if (it.throttling || it.value >= it.warn) return "⚠ ";
    return "✓ ";
}

function mix(a, b, t) {
    t = Math.max(0, Math.min(1, t));
    return a.map((x, i) => x + (b[i] - x) * t);
}

let COLORBLIND = false;

function valueColor(v, warn, crit) {
    if (v === null || v === undefined) return [0.55, 0.58, 0.62];
    if (warn === null || crit === null || warn === undefined) return [0.13, 0.83, 0.93];
    const g = COLORBLIND ? [0.0, 0.45, 0.70] : [0.13, 0.77, 0.37];
    const y = COLORBLIND ? [0.90, 0.62, 0.0] : [0.92, 0.70, 0.03];
    const o = COLORBLIND ? [0.90, 0.62, 0.0] : [0.98, 0.45, 0.09];
    const r = COLORBLIND ? [0.80, 0.47, 0.65] : [0.94, 0.27, 0.27];
    if (v <= warn - 15) return g;
    if (v <= warn) return mix(g, y, (v - (warn - 15)) / 15);
    if (v <= crit) {
        const t = (v - warn) / Math.max(1, crit - warn);
        return t < 0.5 ? mix(y, o, t * 2) : mix(o, r, (t - 0.5) * 2);
    }
    return r;
}

function fmt(ch, asleep) {
    if (asleep) return ["zZ", ""];
    const v = ch.anim;
    if (v === null || v === undefined) return ["–", ch.unit || ""];
    if (ch.kind === "temp") return [String(Math.round(v)), ch.unit || "°C"];
    if (ch.kind === "percent") return [String(Math.round(v)), "%"];
    if (ch.kind === "fan") return [String(Math.round(v)), "RPM"];
    if (ch.kind === "power") return [v.toFixed(v >= 10 ? 0 : 1), "W"];
    if (ch.kind === "volt") return [v.toFixed(2), "V"];      // 1,2 V no es «1»
    return [String(Math.round(v)), ch.unit || ""];
}

class SensorPulseDesklet extends Desklet.Desklet {
    constructor(metadata, deskletId) {
        super(metadata, deskletId);
        this.settings = new Settings.DeskletSettings(this, metadata.uuid, deskletId);
        this.settings.bind("style", "style", () => this._relayout());
        this.settings.bind("size", "size", () => this._relayout());
        this.settings.bind("count", "count", () => this._relayout());
        this.items = [];
        this.anims = {};
        this.alarms = [];
        this.pulse = 0;
        this.canvas = new St.DrawingArea();
        this.canvas.connect("repaint", (area) => this._draw(area));
        this.setContent(this.canvas);
        this.setHeader("SensorPulse");
        this._relayout();
        this._sub = Gio.DBus.session.signal_subscribe(
            BUS_NAME, BUS_IFACE, "ViewsUpdated", BUS_PATH, null, Gio.DBusSignalFlags.NONE,
            (c, s, p, i, sig, params) => {
                try { this._update(JSON.parse(params.deep_unpack()[0])); } catch (e) { global.logError(e); }
            });
        this._fetch();
        this._timer = 0;   // la animación se arranca bajo demanda y se para sola
        this._rx = 0;
        this._seen = false;
        this._staleAfter = 15;
        // si el servicio desaparece o se atasca, no se sigue mostrando el último valor
        this._watch = Gio.bus_watch_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameWatcherFlags.NONE,
            () => this._fetch(), () => {
                // ya funcionaba y desaparece: no se rearranca por D-Bus (systemd lo reinicia
                // si cayó; si no, alguien lo paró a propósito). Al reaparecer, _fetch().
                this._markStale();
                if (!this._seen) this._fetch();
            });
        this._staleId = Mainloop.timeout_add_seconds(5, () => {
            // desde que llegó la última foto, con reloj monótono (Date.now() fallaba si el
            // reloj del sistema saltaba hacia atrás)
            if (this._rx && (GLib.get_monotonic_time() - this._rx) / 1e6 > this._staleAfter)
                this._markStale();
            return true;
        });
    }

    _markStale() {
        if (this._isStale) return;
        this._isStale = true;
        for (const it of this.items) { it.value = null; it.target = null; this.anims[it.id] = null; }
        this.alarms = [];
        this._relayout();
        this._wake();
    }

    _wake() {
        if (!this._timer)
            this._timer = Mainloop.timeout_add(33, () => this._animate());
    }

    _fetch() {
        Gio.DBus.session.call(BUS_NAME, BUS_PATH, BUS_IFACE, "GetSnapshot", null,
            new GLib.VariantType("(s)"),
            this._seen ? Gio.DBusCallFlags.NO_AUTO_START : Gio.DBusCallFlags.NONE, 5000, null, (conn, res) => {
                let snap = null;
                try { snap = JSON.parse(conn.call_finish(res).deep_unpack()[0]); } catch (e) { snap = null; }
                if (this._removed) return;
                if (snap) { this._retryDelay = 0; this._update(snap); return; }
                // el servicio aún no responde: reintento cada vez más espaciado (hasta 5 min),
                // cancelado al quitar el desklet
                this._retryDelay = Math.min(300, (this._retryDelay || 2.5) * 2);
                if (!this._retryId && !this._seen)
                    this._retryId = Mainloop.timeout_add_seconds(this._retryDelay, () => {
                        this._retryId = 0;
                        this._fetch();
                        return false;
                    });
            });
    }

    _update(snap) {
        // la misma selección que el widget flotante, ya con nombres, °C/°F y estado
        this._rx = GLib.get_monotonic_time();
        this._seen = true;
        this._staleAfter = snap.stale_after || 15;
        this._isStale = false;
        const v = snap.views;
        if (!v) return;
        COLORBLIND = !!v.colorblind;
        this.items = v.floating.slice(0, this.count).map(it => Object.assign({}, it));
        for (const it of this.items) {
            const a = this.anims[it.id];
            if (a === undefined || a === null || it.value === null) this.anims[it.id] = it.value;
            it.target = it.value;
        }
        this.alarms = v.blink ? this.items.filter(it => it.alarm).map(it => it.id) : [];
        this._fillRename(v);
        this._relayout();
        this._wake();
    }

    _fillRename(v) {
        // menú del botón derecho: cambiar el nombre de un sensor desde el propio escritorio
        if (!this._menu || !this._menu.addMenuItem) return;
        const key = this.items.map(it => it.id + "=" + it.label).join("|");
        if (key === this._renameKey) return;
        this._renameKey = key;
        if (!this._renameSub) {
            this._menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            this._renameSub = new PopupMenu.PopupSubMenuMenuItem((v.tr && v.tr.rename) || "Rename a sensor…");
            this._menu.addMenuItem(this._renameSub);
        } else if (v.tr && v.tr.rename) {
            this._renameSub.label.set_text(v.tr.rename);
        }
        this._renameSub.menu.removeAll();
        for (const it of this.items) {
            const mi = new PopupMenu.PopupMenuItem(it.label || it.id);
            mi.connect("activate", () => Util.spawn(["sensorpulse", "--rename", it.id]));
            this._renameSub.menu.addMenuItem(mi);
        }
    }

    _cell() {
        const s = this.size;
        if (this.style === "hud") return [176 * s, 162 * s];
        if (this.style === "rings") return [110 * s, 120 * s];
        return [300 * s, 46 * s];
    }

    _relayout() {
        const n = Math.max(1, this.items.length);
        const [cw, ch] = this._cell();
        const cols = this.style === "bars" ? 1 : Math.min(n, 3);
        const rows = Math.ceil(n / cols);
        this.canvas.set_width(Math.round(cols * cw + 16));
        this.canvas.set_height(Math.round(rows * ch + 16));
        this.canvas.queue_repaint();
    }

    _animate() {
        let moving = false;
        for (const it of this.items) {
            const cur = this.anims[it.id];
            if (cur === null || cur === undefined || it.target === null) continue;
            const d = it.target - cur;
            if (Math.abs(d) > 0.05) { this.anims[it.id] = cur + d * 0.17; moving = true; }
            else this.anims[it.id] = it.target;
        }
        const alarm = this.items.some(it => this.alarms.indexOf(it.id) >= 0);
        // el nivel de la ALARMA (como el applet y la extensión), no el estado de la temperatura:
        // una regla crítica propia por debajo del límite del sensor también late a 1 Hz
        const crit = this.items.some(it => it.alarm && it.alarm_level === "critical");
        // «respiración»: crítico ≈1 Hz, aviso ≈0,5 Hz (sin parpadeos rápidos)
        this.pulse = alarm ? 0.5 + 0.5 * Math.sin(Date.now() / 1000 * 2 * Math.PI / (crit ? 1.0 : 2.0)) : 0;
        if (moving || alarm) {
            this.canvas.queue_repaint();
            return true;
        }
        this.canvas.queue_repaint();
        this._timer = 0;
        return false;   // nada se mueve: parar hasta el siguiente dato
    }

    // PangoCairo (no la API de texto simple de cairo): sustituye fuentes, así que el
    // japonés y el chino se ven bien en lugar de salir como cuadrados.
    _text(cr, s, x, y, size, rgb, align, bold) {
        const layout = PangoCairo.create_layout(cr);
        const fd = Pango.FontDescription.from_string("Inter, Cantarell, Ubuntu, Noto Sans, DejaVu Sans, sans-serif");
        fd.set_absolute_size(Math.max(1, size) * Pango.SCALE);
        fd.set_weight(bold ? Pango.Weight.BOLD : Pango.Weight.NORMAL);
        layout.set_font_description(fd);
        layout.set_text(String(s), -1);
        const [w, h] = layout.get_pixel_size();
        let px = x;
        if (align === "center") px = x - w / 2;
        else if (align === "right") px = x - w;
        cr.setSourceRGBA(rgb[0], rgb[1], rgb[2], 1);
        cr.moveTo(px, y - h / 2);
        PangoCairo.show_layout(cr, layout);
        return w;
    }

    _draw(area) {
        const cr = area.get_context();
        const [W, H] = area.get_surface_size();
        cr.setOperator(Cairo.Operator.CLEAR);
        cr.paint();
        cr.setOperator(Cairo.Operator.OVER);
        const [cw, ch] = this._cell();
        const cols = this.style === "bars" ? 1 : Math.min(Math.max(1, this.items.length), 3);
        this.items.forEach((it, i) => {
            cr.newPath();
            const x = 8 + (i % cols) * cw, y = 8 + Math.floor(i / cols) * ch;
            const v = this.anims[it.id];
            const lo = it.vmin, hi = it.vmax;
            const f = (v === null || v === undefined || hi <= lo) ? 0 : Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
            const col = it.asleep ? [0.55, 0.58, 0.62] : valueColor(v, it.warn, it.crit);
            const alarm = this.alarms.indexOf(it.id) >= 0;
            const [val, unit] = fmt(Object.assign({}, it, { anim: v }), it.asleep);
            if (this.style === "bars") {
                this._text(cr, stateSym(it) + it.label, x, y + 10, 13 * this.size, [0.75, 0.78, 0.82], "left", false);
                this._text(cr, val + " " + unit, x + cw - 10, y + 10, 15 * this.size, [0.95, 0.96, 0.97], "right", true);
                const by = y + 24 * this.size, bh = 10 * this.size, bw = cw - 10;
                cr.setSourceRGBA(1, 1, 1, 0.10);
                cr.rectangle(x, by, bw, bh);
                cr.fill();
                cr.setSourceRGBA(col[0], col[1], col[2], 1);
                cr.rectangle(x, by, bw * f, bh);
                cr.fill();
                if (alarm) { cr.setSourceRGBA(0.94, 0.27, 0.27, 0.3 + 0.6 * this.pulse); cr.setLineWidth(2); cr.rectangle(x - 2, by - 2, bw + 4, bh + 4); cr.stroke(); }
                return;
            }
            const cx = x + cw / 2;
            if (this.style === "rings") {
                const r = 38 * this.size, cy = y + r + 6;
                cr.setLineWidth(r * 0.14);
                cr.setSourceRGBA(1, 1, 1, 0.10);
                cr.newPath();
                cr.arc(cx, cy, r, 0, 2 * Math.PI); cr.stroke();
                cr.setSourceRGBA(col[0], col[1], col[2], 1);
                cr.newPath();
                cr.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * f); cr.stroke();
                // con °F también lleva su unidad (antes solo °C y %)
                this._text(cr, val + (unit === "°C" || unit === "°F" || unit === "%" ? unit : ""), cx, cy, r * 0.42, [0.95, 0.96, 0.97], "center", true);
                this._text(cr, stateSym(it) + it.label, cx, cy + r + 14 * this.size, 11 * this.size, [0.75, 0.78, 0.82], "center", false);
                if (alarm) { cr.setSourceRGBA(0.94, 0.27, 0.27, 0.25 + 0.6 * this.pulse); cr.setLineWidth(3); cr.newPath(); cr.arc(cx, cy, r + r * 0.3, 0, 2 * Math.PI); cr.stroke(); }
                return;
            }
            // HUD: arco de 270° con degradado de calor, marcas y marcador
            const r = 62 * this.size, cy = y + r + 14 * this.size;
            const a0 = 0.75 * Math.PI, sweep = 1.5 * Math.PI, th = r * 0.11;
            cr.setLineWidth(th);
            cr.setSourceRGBA(1, 1, 1, 0.09);
            cr.newPath();
            cr.arc(cx, cy, r, a0, a0 + sweep); cr.stroke();
            for (let k = 0; k < 28; k++) {
                const t = k / 27, ang = a0 + sweep * t, major = k % 3 === 0;
                const r1 = r - th * (major ? 1.3 : 1.0), r2 = r - th * (major ? 2.2 : 1.6);
                const lit = t <= f;
                const c = lit ? valueColor(lo + t * (hi - lo), it.warn, it.crit) : [0.6, 0.63, 0.7];
                cr.setSourceRGBA(c[0], c[1], c[2], lit ? 0.9 : 0.3);
                cr.setLineWidth(major ? r * 0.018 : r * 0.01);
                cr.moveTo(cx + r1 * Math.cos(ang), cy + r1 * Math.sin(ang));
                cr.lineTo(cx + r2 * Math.cos(ang), cy + r2 * Math.sin(ang));
                cr.stroke();
            }
            const steps = Math.max(2, Math.floor(60 * f));
            for (const [w, al] of [[th * 2.2, 0.18], [th, 1.0]]) {
                cr.setLineWidth(w);
                for (let s = 0; s < steps; s++) {
                    const t0 = f * s / steps, t1 = Math.min(f, f * (s + 1) / steps + 0.002);
                    const c = valueColor(lo + ((t0 + t1) / 2) * (hi - lo), it.warn, it.crit);
                    cr.setSourceRGBA(c[0], c[1], c[2], al);
                    cr.newPath();
                    cr.arc(cx, cy, r, a0 + sweep * t0, a0 + sweep * t1);
                    cr.stroke();
                }
            }
            const ang = a0 + sweep * f, rin = r - th * 3.2, rout = r - th * 0.9;
            cr.setSourceRGBA(col[0], col[1], col[2], 0.95);
            cr.setLineWidth(r * 0.03);
            cr.newPath();
            cr.moveTo(cx + rin * Math.cos(ang), cy + rin * Math.sin(ang));
            cr.lineTo(cx + rout * Math.cos(ang), cy + rout * Math.sin(ang));
            cr.stroke();
            const tw = this._text(cr, val, cx, cy + r * 0.02, r * 0.44, [0.95, 0.96, 0.97], "center", true);
            this._text(cr, unit, cx + tw / 2 + 2, cy - r * 0.12, r * 0.15, [0.6, 0.63, 0.7], "left", false);
            this._text(cr, stateSym(it) + it.label, cx, cy + r * 0.42, r * 0.15, [0.75, 0.78, 0.82], "center", false);
            if (alarm) {
                cr.setSourceRGBA(0.94, 0.27, 0.27, 0.25 + 0.6 * this.pulse);
                cr.setLineWidth(r * 0.05);
                cr.newPath();
                cr.arc(cx, cy, r + th * 1.8, 0, 2 * Math.PI);
                cr.stroke();
            }
        });
        cr.$dispose();
    }

    on_desklet_clicked() {
        Util.spawnCommandLine("sensorpulse");
    }

    on_desklet_removed() {
        this._removed = true;
        if (this._retryId) Mainloop.source_remove(this._retryId);
        this._retryId = 0;
        if (this._sub) Gio.DBus.session.signal_unsubscribe(this._sub);
        if (this._timer) Mainloop.source_remove(this._timer);
        this._timer = 0;
        if (this._staleId) Mainloop.source_remove(this._staleId);
        this._staleId = 0;
        if (this._watch) Gio.bus_unwatch_name(this._watch);
        this._watch = 0;
    }
}

function main(metadata, deskletId) {
    return new SensorPulseDesklet(metadata, deskletId);
}
