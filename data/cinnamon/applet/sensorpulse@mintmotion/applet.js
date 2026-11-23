// SensorPulse — applet del panel de Cinnamon.
// Solo muestra datos: todo lo lee el servicio «sensorpulse-service» (D-Bus de sesión).
// Si el servicio no está en marcha, el bus lo arranca solo (activación D-Bus).

const Applet = imports.ui.applet;
const PopupMenu = imports.ui.popupMenu;
const St = imports.gi.St;
const Clutter = imports.gi.Clutter;
const Gio = imports.gi.Gio;
const GLib = imports.gi.GLib;
const Util = imports.misc.util;
const Mainloop = imports.mainloop;
const Cairo = imports.cairo;

const BUS_NAME = "io.github.mintmotion.SensorPulse";
const BUS_PATH = "/io/github/mintmotion/SensorPulse";
const BUS_IFACE = "io.github.mintmotion.SensorPulse";

// textos del menú: el servicio los publica ya traducidos al idioma elegido (views.tr), así
// que valen para los 8 idiomas sin repetir traducciones aquí. Esta tabla solo se usa hasta
// que llega la primera foto (o con un servicio antiguo).
const TR = {
    es: { open: "Abrir ventana", settings: "Ajustes", mute: "Silenciar alarmas 1 hora", waiting: "Esperando datos…", rename: "Cambiar el nombre de un sensor…", more: "Más valores" },
    fr: { open: "Ouvrir la fenêtre", settings: "Paramètres", mute: "Couper les alarmes 1 heure", waiting: "En attente de données…", rename: "Renommer un capteur…", more: "Autres valeurs" },
    it: { open: "Apri la finestra", settings: "Impostazioni", mute: "Silenzia gli allarmi per 1 ora", waiting: "In attesa di dati…", rename: "Rinomina un sensore…", more: "Altri valori" },
    de: { open: "Fenster öffnen", settings: "Einstellungen", mute: "Alarme 1 Stunde stummschalten", waiting: "Warte auf Daten…", rename: "Sensor umbenennen…", more: "Weitere Werte" },
    pt: { open: "Abrir janela", settings: "Definições", mute: "Silenciar alarmes durante 1 hora", waiting: "A aguardar dados…", rename: "Mudar o nome de um sensor…", more: "Mais valores" },
    ja: { open: "ウィンドウを開く", settings: "設定", mute: "アラームを1時間消音", waiting: "データを待っています…", rename: "センサーの名前を変更…", more: "その他の値" },
    zh: { open: "打开窗口", settings: "设置", mute: "静音警报 1 小时", waiting: "正在等待数据…", rename: "重命名传感器…", more: "更多数值" },
    en: { open: "Open window", settings: "Settings", mute: "Mute alarms for 1 hour", waiting: "Waiting for data…", rename: "Rename a sensor…", more: "More values" },
};
let CURRENT_LANG = null;   // el elegido en Ajustes (lo publica el servicio en views.lang)
let SERVICE_TR = null;     // textos ya traducidos por el servicio (views.tr)

function tr(key) {
    if (SERVICE_TR && SERVICE_TR[key]) return SERVICE_TR[key];
    if (CURRENT_LANG && TR[CURRENT_LANG]) return TR[CURRENT_LANG][key];
    if (CURRENT_LANG) return TR.en[key];
    for (const l of GLib.get_language_names()) {
        const code = l.split("_")[0].split(".")[0];
        if (TR[code]) return TR[code][key];
    }
    return TR.en[key];
}

function hexRGB(h, fallback) {
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(h || "");
    if (!m) return fallback || [0.55, 0.58, 0.62];
    return [parseInt(m[1], 16) / 255, parseInt(m[2], 16) / 255, parseInt(m[3], 16) / 255];
}

const GREY = "#8c949e";

class SensorPulseApplet extends Applet.Applet {
    // Muestra exactamente lo mismo que el resto de vistas: el servicio publica en
    // snapshot.views la selección del panel ya con nombres, °C/°F, estado y color.
    // Dos aspectos (Ajustes › Panel): «nombre y número» o indicadores tipo cuentakilómetros.
    constructor(metadata, orientation, panelHeight, instanceId) {
        super(orientation, panelHeight, instanceId);
        this.setAllowedLayout(Applet.AllowedLayout.BOTH);
        // nombre propio: Cinnamon define _panelHeight en Applet como propiedad de solo
        // lectura (getter) y asignarla lanza un error que impide cargar el applet
        this._spPanelHeight = panelHeight || 28;
        this.box = new St.BoxLayout({ style_class: "applet-box", reactive: true, y_align: Clutter.ActorAlign.CENTER });
        this.actor.add(this.box);
        this.labels = [];          // un elemento por valor de la barra (texto o indicador)
        this._layoutKey = "";
        this.snapshot = null;
        this._phase = 0;
        this._blinkId = 0;
        this.set_applet_tooltip("SensorPulse");

        this.menuManager = new PopupMenu.PopupMenuManager(this);
        this.menu = new Applet.AppletPopupMenu(this, orientation);
        this.menuManager.addMenu(this.menu);

        this._sub = Gio.DBus.session.signal_subscribe(
            BUS_NAME, BUS_IFACE, "ViewsUpdated", BUS_PATH, null, Gio.DBusSignalFlags.NONE,
            (conn, sender, path, iface, signal, params) => {
                try { this._update(JSON.parse(params.deep_unpack()[0])); } catch (e) { global.logError(e); }
            });
        this._fetch();
        this._rx = 0;
        this._seen = false;
        this._staleAfter = 15;
        this._stale = false;
        // si el servicio desaparece o se atasca, no se sigue mostrando el último valor en verde
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

    on_panel_height_changed() {
        this._spPanelHeight = this.panel ? this.panel.height : this._spPanelHeight;
        this._layoutKey = "";          // rehacer los indicadores con el tamaño nuevo
        if (this.snapshot) this._update(this.snapshot);
    }

    _markStale() {
        if (this._stale || this._removed) return;
        this._stale = true;
        if (this._blinkId) {
            Mainloop.source_remove(this._blinkId);
            this._blinkId = 0;
        }
        for (const l of this.labels) {
            l._it = Object.assign({}, l._it || {}, { short: "–", tiny: "–", frac: 0, color: GREY, alarm: false, state: null });
            this._paint(l);
            l.opacity = 255;
        }
        if (!this.labels.length) this._setText("–");
        this.set_applet_tooltip("SensorPulse\n" + tr("waiting"));
    }

    _fetch() {
        Gio.DBus.session.call(BUS_NAME, BUS_PATH, BUS_IFACE, "GetSnapshot", null,
            new GLib.VariantType("(s)"),
            this._seen ? Gio.DBusCallFlags.NO_AUTO_START : Gio.DBusCallFlags.NONE, 5000, null,
            (conn, res) => {
                try {
                    const snap = JSON.parse(conn.call_finish(res).deep_unpack()[0]);
                    if (this._removed) return;
                    this._retryDelay = 0;
                    this._update(snap);
                } catch (e) {
                    if (this._removed) return;
                    this._setText("–");
                    // reintento si el servicio aún está arrancando, cada vez más espaciado
                    // (5, 10, 20… hasta 5 min); se cancela al quitar el applet
                    this._retryDelay = Math.min(300, (this._retryDelay || 2.5) * 2);
                    if (!this._retryId && !this._seen)
                        this._retryId = Mainloop.timeout_add_seconds(this._retryDelay, () => {
                            this._retryId = 0;
                            this._fetch();
                            return false;
                        });
                }
            });
    }

    _setText(t) {
        this.box.destroy_all_children();
        this.labels = [];
        this._layoutKey = "";
        const l = new St.Label({ text: t, style_class: "applet-label", y_align: Clutter.ActorAlign.CENTER });
        this.box.add(l);
    }

    // ------------------------------------------------------------ elementos de la barra
    _makeText(names) {
        const name = new St.Label({ style_class: "applet-label", y_align: Clutter.ActorAlign.CENTER });
        name.set_style("padding: 0 0 0 4px;");
        name.opacity = 190;
        name.visible = names;
        const l = new St.Label({ style_class: "applet-label", y_align: Clutter.ActorAlign.CENTER });
        this.box.add(name);
        this.box.add(l);
        l._name = name;
        l._kind = "text";
        return l;
    }

    _makeGauge(names) {
        // indicador tipo cuentakilómetros: arco de 240° dibujado con cairo y la cifra encima
        const size = Math.max(16, Math.round(this._spPanelHeight * 0.9));
        const g = new St.Widget({ layout_manager: new Clutter.BinLayout(), width: size, height: size,
                                  y_align: Clutter.ActorAlign.CENTER, style: "margin: 0 2px 0 4px;" });
        const area = new St.DrawingArea({ width: size, height: size });
        area.connect("repaint", a => this._paintArc(a, g));
        const lbl = new St.Label({ x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER,
                                   x_expand: true, y_expand: true });
        g.add_child(area);
        g.add_child(lbl);
        g._area = area;
        g._lbl = lbl;
        g._size = size;
        g._kind = "gauge";
        const name = new St.Label({ style_class: "applet-label", y_align: Clutter.ActorAlign.CENTER });
        name.set_style("padding: 0 4px 0 1px; font-size: 0.85em;");
        name.opacity = 190;
        name.visible = names;
        this.box.add(g);
        this.box.add(name);
        g._name = name;
        return g;
    }

    _paintArc(area, g) {
        const it = g._it || {};
        const cr = area.get_context();
        const [w, h] = area.get_surface_size();
        let fg = [0.9, 0.9, 0.9];
        try {
            const c = area.get_theme_node().get_foreground_color();
            fg = [c.red / 255, c.green / 255, c.blue / 255];
        } catch (e) { /* tema sin color: gris claro */ }
        const lw = Math.max(2, Math.round(h * 0.12));
        const r = Math.min(w, h) / 2 - lw / 2 - 0.5;
        const cx = w / 2, cy = h / 2 + h * 0.05;
        const a0 = 150 * Math.PI / 180, sweep = 240 * Math.PI / 180;
        cr.setLineCap(Cairo.LineCap.ROUND);
        cr.setLineWidth(lw);
        cr.setSourceRGBA(fg[0], fg[1], fg[2], 0.22);
        cr.arc(cx, cy, r, a0, a0 + sweep);
        cr.stroke();
        const f = Math.max(0, Math.min(1, it.frac || 0));
        if (f > 0.001) {
            const col = hexRGB(it.color || this._accent, [0.13, 0.83, 0.93]);
            cr.setSourceRGBA(col[0], col[1], col[2], 1);
            cr.arc(cx, cy, r, a0, a0 + sweep * f);
            cr.stroke();
        }
        cr.$dispose();
    }

    _paint(l) {
        // el estilo (color, tipografía) solo cambia con un dato nuevo; la «respiración» de
        // la alarma mueve únicamente la opacidad (10 veces por segundo sería muy caro)
        const it = l._it || {};
        const col = it.color || "";
        if (l._kind === "gauge") {
            const txt = it.tiny || it.short || "–";
            const fs = Math.max(7, Math.round(l._size * (txt.length <= 3 ? 0.36 : 0.29)));
            l._lbl.set_text(txt);
            l._lbl.set_style("font-weight: bold; font-size: " + fs + "px; font-feature-settings: \"tnum\";");
            l._name.set_text(it.label || "");
            l._area.queue_repaint();
        } else {
            const sym = it.state && it.state !== "normal" ? (it.symbol || "") : "";
            l._name.set_text(it.label || "");
            l.set_text(sym + (it.short || "–"));
            l.set_style("font-weight: bold; padding: 0 4px 0 3px; font-feature-settings: \"tnum\";" +
                        (col ? "color: " + col + ";" : ""));
        }
        this._opacity(l);
    }

    _opacity(l) {
        const op = l._it && l._it.alarm && this._blinkId ? (0.45 + 0.55 * (0.5 + 0.5 * Math.sin(this._phase))) : 1;
        l.opacity = Math.round(255 * op);
    }

    _update(snap) {
        this.snapshot = snap;
        this._rx = GLib.get_monotonic_time();
        this._seen = true;
        this._staleAfter = snap.stale_after || 15;
        this._stale = false;
        const v = snap.views;
        if (!v) { this._setText("SP"); return; }
        if (v.lang) CURRENT_LANG = v.lang;
        if (v.tr) SERVICE_TR = v.tr;
        this._accent = v.accent || "#22d3ee";
        const items = v.panel.slice(0, v.panel_count);
        const style = v.panel_style === "gauge" ? "gauge" : "text";
        const names = v.panel_labels !== false;
        // los elementos se crean una vez y se actualizan en su sitio (sin destruirlos y
        // recrearlos cada 2 s); se rehacen al cambiar el número, el aspecto o el panel
        const key = style + ":" + names + ":" + items.length + ":" + this._spPanelHeight;
        if (key !== this._layoutKey) {
            this.box.destroy_all_children();
            this.labels = items.map(() => style === "gauge" ? this._makeGauge(names) : this._makeText(names));
            this._layoutKey = key;
        }
        items.forEach((it, i) => {
            const l = this.labels[i];
            l._it = it;
            this._paint(l);
        });
        if (!items.length) this._setText("SP");
        const tip = ["SensorPulse"];
        if (v.muted_text) tip.push(v.muted_text);
        for (const it of v.summary) tip.push((it.symbol ? it.symbol + " " : "") + it.device_name + ": " + it.short);
        this.set_applet_tooltip(tip.join("\n"));
        // temporizador de «respiración» solo mientras haya una alarma a la vista
        const alarm = v.blink && items.some(it => it.alarm);
        if (alarm && !this._blinkId) {
            this._blinkId = Mainloop.timeout_add(100, () => {
                // «respiración»: crítico 1 Hz, aviso 0,5 Hz (como el resto de vistas)
                const crit = this.labels.some(l => l._it && l._it.alarm && l._it.alarm_level === "critical");
                this._phase += 2 * Math.PI * 0.1 / (crit ? 1.0 : 2.0);
                for (const l of this.labels) if (l._it && l._it.alarm) this._opacity(l);
                return true;
            });
        } else if (!alarm && this._blinkId) {
            Mainloop.source_remove(this._blinkId);
            this._blinkId = 0;
            for (const l of this.labels) this._opacity(l);
        }
        if (this.menu.isOpen) this._buildMenu();
    }

    _valueItem(text, color) {
        const mi = new PopupMenu.PopupMenuItem(text, { reactive: false });
        if (color) mi.label.set_style("color: " + color + ";");
        return mi;
    }

    _buildMenu() {
        this.menu.removeAll();
        const v = this.snapshot && this.snapshot.views;
        if (v && this._stale) {
            this.menu.addMenuItem(new PopupMenu.PopupMenuItem(tr("waiting"), { reactive: false }));
            this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        } else if (v) {
            if (v.muted_text)
                this.menu.addMenuItem(new PopupMenu.PopupMenuItem(v.muted_text, { reactive: false }));
            // los valores elegidos para el panel que no caben en la barra (o «solo al hacer clic»)
            const extra = v.panel_extra || [];
            const seen = new Set((v.panel || []).map(it => it.id));
            for (const it of extra) {
                seen.add(it.id);
                const unit = it.kind === "fan" && it.value !== null ? " RPM" : "";
                this.menu.addMenuItem(this._valueItem((it.symbol ? it.symbol + " " : "") + it.label +
                                                      "    " + it.short + unit, it.color));
            }
            if (extra.length) this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            for (const it of v.summary) {
                if (extra.length && seen.has(it.id)) continue;
                const unit = it.kind === "fan" && it.value !== null ? " RPM" : "";
                this.menu.addMenuItem(this._valueItem((it.symbol ? it.symbol + " " : "") + it.device_name +
                                                      "    " + it.short + unit, it.color));
            }
            this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        }
        const open = new PopupMenu.PopupIconMenuItem(tr("open"), "sensorpulse", St.IconType.FULLCOLOR);
        open.connect("activate", () => Util.spawn(["sensorpulse", "--window"]));
        this.menu.addMenuItem(open);
        const set = new PopupMenu.PopupIconMenuItem(tr("settings"), "emblem-system-symbolic", St.IconType.SYMBOLIC);
        set.connect("activate", () => Util.spawn(["sensorpulse", "--settings"]));
        this.menu.addMenuItem(set);
        // cambiar el nombre de un sensor directamente desde el panel
        const list = v ? (v.panel || []).concat(v.panel_extra || []) : [];
        if (list.length) {
            const sub = new PopupMenu.PopupSubMenuMenuItem(tr("rename"));
            for (const it of list) {
                const mi = new PopupMenu.PopupMenuItem(it.label || it.id);
                mi.connect("activate", () => Util.spawn(["sensorpulse", "--rename", it.id]));
                sub.menu.addMenuItem(mi);
            }
            this.menu.addMenuItem(sub);
        }
        const mute = new PopupMenu.PopupIconMenuItem(tr("mute"), "audio-volume-muted-symbolic", St.IconType.SYMBOLIC);
        mute.connect("activate", () => {
            Gio.DBus.session.call(BUS_NAME, BUS_PATH, BUS_IFACE, "Mute", new GLib.Variant("(i)", [60]),
                null, Gio.DBusCallFlags.NONE, 5000, null, null);
        });
        this.menu.addMenuItem(mute);
    }

    on_applet_clicked() {
        this._buildMenu();
        this.menu.toggle();
    }

    on_applet_removed_from_panel() {
        this._removed = true;
        if (this._retryId) Mainloop.source_remove(this._retryId);
        this._retryId = 0;
        if (this._sub) Gio.DBus.session.signal_unsubscribe(this._sub);
        if (this._blinkId) Mainloop.source_remove(this._blinkId);
        if (this._staleId) Mainloop.source_remove(this._staleId);
        this._staleId = 0;
        if (this._watch) Gio.bus_unwatch_name(this._watch);
        this._watch = 0;
    }
}

function main(metadata, orientation, panelHeight, instanceId) {
    return new SensorPulseApplet(metadata, orientation, panelHeight, instanceId);
}
