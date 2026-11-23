// SensorPulse — extensión de GNOME Shell (45 o posterior).
// Muestra en la barra superior los valores elegidos en SensorPulse, con su color, su
// símbolo de estado y parpadeo si hay alarma. Los datos llegan del servicio
// «sensorpulse-service» por D-Bus de sesión: la extensión no lee ningún sensor.

import GObject from 'gi://GObject';
import St from 'gi://St';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import Cairo from 'cairo';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const BUS_NAME = 'io.github.mintmotion.SensorPulse';
const BUS_PATH = '/io/github/mintmotion/SensorPulse';
const BUS_IFACE = 'io.github.mintmotion.SensorPulse';

// Textos: el servicio los publica ya traducidos al idioma elegido (views.tr), para los 8
// idiomas. Esta tabla solo se usa hasta la primera foto (o con un servicio antiguo).
const TR = {
    es: {'Open window': 'Abrir ventana', 'Settings': 'Ajustes', 'Mute alarms for 1 hour': 'Silenciar alarmas 1 hora',
         'Waiting for data…': 'Esperando datos…', 'Rename a sensor…': 'Cambiar el nombre de un sensor…'},
    fr: {'Open window': 'Ouvrir la fenêtre', 'Settings': 'Paramètres', 'Mute alarms for 1 hour': 'Couper les alarmes 1 heure',
         'Waiting for data…': 'En attente de données…', 'Rename a sensor…': 'Renommer un capteur…'},
    it: {'Open window': 'Apri la finestra', 'Settings': 'Impostazioni', 'Mute alarms for 1 hour': 'Silenzia gli allarmi per 1 ora',
         'Waiting for data…': 'In attesa di dati…', 'Rename a sensor…': 'Rinomina un sensore…'},
    de: {'Open window': 'Fenster öffnen', 'Settings': 'Einstellungen', 'Mute alarms for 1 hour': 'Alarme 1 Stunde stummschalten',
         'Waiting for data…': 'Warte auf Daten…', 'Rename a sensor…': 'Sensor umbenennen…'},
    pt: {'Open window': 'Abrir janela', 'Settings': 'Definições', 'Mute alarms for 1 hour': 'Silenciar alarmes durante 1 hora',
         'Waiting for data…': 'A aguardar dados…', 'Rename a sensor…': 'Mudar o nome de um sensor…'},
    ja: {'Open window': 'ウィンドウを開く', 'Settings': '設定', 'Mute alarms for 1 hour': 'アラームを1時間ミュート',
         'Waiting for data…': 'データを待っています…', 'Rename a sensor…': 'センサーの名前を変更…'},
    zh: {'Open window': '打开窗口', 'Settings': '设置', 'Mute alarms for 1 hour': '静音警报 1 小时',
         'Waiting for data…': '正在等待数据…', 'Rename a sensor…': '重命名传感器…'},
};
// clave de views.tr para cada texto original
const TR_KEY = {'Open window': 'open', 'Settings': 'settings', 'Mute alarms for 1 hour': 'mute',
                'Waiting for data…': 'waiting', 'Rename a sensor…': 'rename', 'asleep': 'asleep',
                'normal': 'normal', 'high': 'high', 'critical': 'critical', 'slowing down': 'slowing down'};

function hexRGB(h, fallback) {
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(h || '');
    if (!m) return fallback || [0.55, 0.58, 0.62];
    return [parseInt(m[1], 16) / 255, parseInt(m[2], 16) / 255, parseInt(m[3], 16) / 255];
}

const Indicator = GObject.registerClass(
class SensorPulseIndicator extends PanelMenu.Button {
    _init(tr) {
        super._init(0.0, 'SensorPulse');
        // idioma: el elegido en Ajustes (el servicio lo publica en views.lang); al principio,
        // el del sistema
        this._tr = tr;
        this._lang = null;
        this._srvTr = null;      // textos ya traducidos por el servicio (views.tr)
        this._t = s => (this._srvTr && this._srvTr[TR_KEY[s]]) || (this._tr && this._tr[s]) || s;
        this._box = new St.BoxLayout({style_class: 'panel-status-menu-box'});
        this._icon = new St.Icon({icon_name: 'sensorpulse-symbolic', style_class: 'system-status-icon'});
        this._box.add_child(this._icon);
        this._labels = [];
        this._layoutKey = '';
        this.add_child(this._box);
        this._snap = null;
        this._blinkOn = true;
        this._blinkId = 0;

        this._summary = new PopupMenu.PopupMenuSection();
        this.menu.connect('open-state-changed', (m, open) => { if (open) this._buildSummary(); });
        this.menu.addMenuItem(this._summary);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._actions = [
            ['Open window', this.menu.addAction(this._t('Open window'), () => this._spawn(['sensorpulse', '--window']))],
            ['Settings', this.menu.addAction(this._t('Settings'), () => this._spawn(['sensorpulse', '--settings']))],
            ['Mute alarms for 1 hour', this.menu.addAction(this._t('Mute alarms for 1 hour'), () => this._call('Mute', new GLib.Variant('(i)', [60])))],
        ];
        // cambiar el nombre de un sensor directamente desde la barra
        this._renameMenu = new PopupMenu.PopupSubMenuMenuItem(this._t('Rename a sensor…'));
        this.menu.addMenuItem(this._renameMenu);
        this._actions.push(['Rename a sensor…', this._renameMenu]);

        this._sub = Gio.DBus.session.signal_subscribe(BUS_NAME, BUS_IFACE, 'ViewsUpdated', BUS_PATH, null,
            Gio.DBusSignalFlags.NONE, (c, s, p, i, sig, params) => {
                try {
                    this._update(JSON.parse(params.deepUnpack()[0]));
                } catch (e) {
                    logError(e, 'SensorPulse');
                }
            });
        // primera foto (si el servicio no está en marcha, D-Bus lo arranca)
        this._call('GetSnapshot', null, '(s)', res => {
            try {
                this._update(JSON.parse(res.deepUnpack()[0]));
            } catch (e) {
                this._setEmpty();
            }
        });
        this._setEmpty();
        this._stale = false;
        this._retryDelay = 3;
        // si el servicio desaparece o se atasca, los valores mostrados dejan de ser de fiar
        this._watch = Gio.bus_watch_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameWatcherFlags.NONE,
            () => this._fetch(), () => this._onVanished());
        this._staleId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 5, () => {
            // desde que LLEGÓ la última foto, con reloj monótono: con Date.now() un salto
            // atrás del reloj del sistema impedía detectar un servicio congelado
            const s = this._snap;
            if (s && this._rx && (GLib.get_monotonic_time() - this._rx) / 1e6 > (s.stale_after || 15))
                this._markStale();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _fetch() {
        // GetSnapshot también reactiva el servicio por D-Bus si no está en marcha
        this._call('GetSnapshot', null, '(s)', res => {
            try {
                this._update(JSON.parse(res.deepUnpack()[0]));
                this._retryDelay = 3;
            } catch (e) {
                this._scheduleRetry();
            }
        });
    }

    _onVanished() {
        this._markStale();
        // si ya funcionaba y desaparece, no se vuelve a arrancar por D-Bus: o systemd lo
        // reinicia (Restart=always) o el usuario lo ha parado a propósito. Al reaparecer,
        // bus_watch_name llama a _fetch(). Solo se insiste si nunca llegó a funcionar.
        if (!this._seen)
            this._scheduleRetry();
    }

    _scheduleRetry() {
        if (this._retryId || this._destroyed) return;
        // reintentos cada vez más espaciados (3, 6, 12... hasta 5 min), cancelados en destroy()
        const d = this._retryDelay;
        this._retryDelay = Math.min(300, d * 2);
        this._retryId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, d, () => {
            this._retryId = 0;
            this._fetch();
            return GLib.SOURCE_REMOVE;
        });
    }

    _markStale() {
        if (this._stale) return;
        this._stale = true;
        this._render();
    }

    _call(method, params, outSig, cb) {
        // la respuesta puede llegar después de disable(): se cancela en destroy() y,
        // por si acaso, no se toca nada si el indicador ya no existe
        if (!this._cancellable)
            this._cancellable = new Gio.Cancellable();
        // GetSnapshot arranca el servicio por D-Bus solo hasta que ha funcionado una vez
        const flags = this._seen ? Gio.DBusCallFlags.NO_AUTO_START : Gio.DBusCallFlags.NONE;
        Gio.DBus.session.call(BUS_NAME, BUS_PATH, BUS_IFACE, method, params,
            outSig ? new GLib.VariantType(outSig) : null, flags, 5000,
            this._cancellable,
            (conn, res) => {
                let r = null;
                try {
                    r = conn.call_finish(res);
                } catch (e) {
                    // servicio no disponible o llamada cancelada
                    if (!this._destroyed && method === 'GetSnapshot' && !this._seen) this._scheduleRetry();
                    return;
                }
                if (!this._destroyed && cb)
                    cb(r);
            });
    }

    _spawn(argv) {
        try {
            GLib.spawn_async(null, argv, null, GLib.SpawnFlags.SEARCH_PATH, null);
        } catch (e) {
            logError(e, 'SensorPulse');
        }
    }

    _setEmpty() {
        if (this._snap) return;
        this._summary.removeAll();
        this._summary.addMenuItem(new PopupMenu.PopupMenuItem(this._t('Waiting for data…'), {reactive: false}));
    }

    _update(snap) {
        // el servicio publica en snap.views lo que hay que mostrar (selección, nombres,
        // °C/°F, estado y color): sin leer ficheros en el hilo principal del Shell
        this._snap = snap;
        this._rx = GLib.get_monotonic_time();
        this._seen = true;
        this._stale = false;
        this._render();
    }

    _render() {
        const v = this._snap && this._snap.views;
        if (!v) return;
        if ((v.lang && v.lang !== this._lang) || (v.tr && !this._srvTr)) {
            this._lang = v.lang;
            this._tr = TR[v.lang] || null;       // «en» u otro sin tabla: los textos originales
            this._srvTr = v.tr || null;
            for (const [key, item] of this._actions) item.label.text = this._t(key);
        }
        this._accent = v.accent || '#22d3ee';
        const stale = this._stale;
        const shown = v.panel.slice(0, v.panel_count);
        const style = v.panel_style === 'gauge' ? 'gauge' : 'text';
        const names = v.panel_labels !== false;
        const key = `${style}:${names}:${shown.length}`;
        if (key !== this._layoutKey) {
            // aspecto o número de valores distinto: se rehacen los elementos de la barra
            for (const l of this._labels) {
                l._name.destroy();
                l.destroy();
            }
            this._labels = shown.map(() => style === 'gauge' ? this._makeGauge(names) : this._makeText(names));
            this._layoutKey = key;
        }
        shown.forEach((it, i) => {
            // sin datos recientes: «–» en gris, nunca el último valor en verde
            const l = this._labels[i];
            l._it = stale ? Object.assign({}, it, {short: '–', tiny: '–', frac: 0, color: '#8c949e', state: null}) : it;
            this._paint(l);
        });
        this._fillRename(v);
        this._icon.visible = shown.length === 0;
        this._setBlink(!stale && v.blink && shown.some(it => it.alarm),
                       shown.some(it => it.alarm && it.alarm_level === 'critical'));
        if (this.menu.isOpen)
            this._buildSummary();
    }

    _makeText(names) {
        // nombre delante del valor, como en el widget flotante («CPU 45°»)
        const name = new St.Label({y_align: Clutter.ActorAlign.CENTER,
                                   style: 'margin-left: 6px; margin-right: 3px;', opacity: 190});
        name.visible = names;
        const l = new St.Label({y_align: Clutter.ActorAlign.CENTER, style: 'margin-left: 4px;'});
        this._box.add_child(name);
        this._box.add_child(l);
        l._name = name;
        l._kind = 'text';
        return l;
    }

    _makeGauge(names) {
        // indicador tipo cuentakilómetros: arco de 240° con cairo y la cifra encima
        const size = Math.max(16, Math.round((Main.panel.height || 32) * 0.78));
        const g = new St.Widget({layout_manager: new Clutter.BinLayout(), width: size, height: size,
                                 y_align: Clutter.ActorAlign.CENTER, style: 'margin-left: 4px;'});
        const area = new St.DrawingArea({width: size, height: size});
        area.connect('repaint', a => this._paintArc(a, g));
        const lbl = new St.Label({x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER,
                                  x_expand: true, y_expand: true});
        g.add_child(area);
        g.add_child(lbl);
        Object.assign(g, {_area: area, _lbl: lbl, _size: size, _kind: 'gauge'});
        const name = new St.Label({y_align: Clutter.ActorAlign.CENTER, opacity: 190,
                                   style: 'margin-left: 2px; margin-right: 3px; font-size: 0.85em;'});
        name.visible = names;
        this._box.add_child(g);
        this._box.add_child(name);
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
        } catch (e) { /* gris claro */ }
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
        const it = l._it || {};
        l._name.set_text(it.label || '');
        if (l._kind === 'gauge') {
            const txt = it.tiny || it.short || '–';
            const fs = Math.max(7, Math.round(l._size * (txt.length <= 3 ? 0.36 : 0.29)));
            l._lbl.set_text(txt);
            l._lbl.set_style(`font-weight: bold; font-size: ${fs}px; font-feature-settings: "tnum";`);
            l._area.queue_repaint();
            return;
        }
        const sym = it.state && it.state !== 'normal' ? (it.symbol || '') : '';
        l.set_text(sym + (it.short || '–'));
        const css = it.color ? `color: ${it.color};` : '';
        l.set_style('font-weight: bold; font-feature-settings: "tnum";' + css);
    }

    _fillRename(v) {
        // la lista solo se rehace si cambian los sensores o sus nombres
        const list = (v.panel || []).concat(v.panel_extra || []);
        const key = list.map(it => it.id + '=' + it.label).join('|');
        if (key === this._renameKey) return;
        this._renameKey = key;
        this._renameMenu.menu.removeAll();
        for (const it of list)
            this._renameMenu.menu.addAction(it.label || it.id, () => this._spawn(['sensorpulse', '--rename', it.id]));
        this._renameMenu.visible = list.length > 0;
    }

    _buildSummary() {
        // el menú solo se rehace al abrirlo (o si está abierto y llega un dato)
        const v = this._snap && this._snap.views;
        this._summary.removeAll();
        if (!v || this._stale) {
            this._summary.addMenuItem(new PopupMenu.PopupMenuItem(this._t('Waiting for data…'), {reactive: false}));
            return;
        }
        if (v.muted_text)
            this._summary.addMenuItem(new PopupMenu.PopupMenuItem(v.muted_text, {reactive: false}));
        const words = {normal: this._t('normal'), hot: this._t('high'), critical: this._t('critical')};
        // los valores elegidos para el panel que no caben en la barra (o «solo al hacer clic»)
        const extra = v.panel_extra || [];
        const seen = new Set();
        for (const it of extra) {
            seen.add(it.id);
            const line = `${it.symbol ? it.symbol + ' ' : ''}${it.label}: ${it.short}${it.kind === 'fan' && it.value !== null ? ' RPM' : ''}`;
            this._summary.addMenuItem(new PopupMenu.PopupMenuItem(line, {reactive: false}));
        }
        if (extra.length)
            this._summary.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        for (const it of v.summary) {
            if (seen.has(it.id)) continue;
            let line = `${it.device_name}: ${it.short}${it.kind === 'fan' && it.value !== null ? ' RPM' : ''}`;
            if (it.asleep) line += ` (${this._t('asleep')})`;
            else if (it.state) line = `${it.symbol} ${line} · ${it.throttling ? this._t('slowing down') : words[it.state]}`;
            this._summary.addMenuItem(new PopupMenu.PopupMenuItem(line, {reactive: false}));
        }
    }

    _setBlink(on, crit = false) {
        // «respiración»: crítico 1 Hz, aviso 0,5 Hz (como el resto de vistas)
        const half = crit ? 500 : 1000;
        if (on && this._blinkId && this._blinkHalf !== half) {
            GLib.source_remove(this._blinkId);
            this._blinkId = 0;
        }
        if (on && !this._blinkId) {
            this._blinkHalf = half;
            this._blinkId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, half, () => {
                this._blinkOn = !this._blinkOn;
                this._box.opacity = this._blinkOn ? 255 : 90;
                return GLib.SOURCE_CONTINUE;
            });
        } else if (!on && this._blinkId) {
            GLib.source_remove(this._blinkId);
            this._blinkId = 0;
            this._box.opacity = 255;
        }
    }

    destroy() {
        this._destroyed = true;
        if (this._cancellable) {
            this._cancellable.cancel();
            this._cancellable = null;
        }
        this._setBlink(false);
        if (this._staleId) {
            GLib.source_remove(this._staleId);
            this._staleId = 0;
        }
        if (this._retryId) {
            GLib.source_remove(this._retryId);
            this._retryId = 0;
        }
        if (this._watch) {
            Gio.bus_unwatch_name(this._watch);
            this._watch = 0;
        }
        if (this._sub) {
            Gio.DBus.session.signal_unsubscribe(this._sub);
            this._sub = 0;
        }
        super.destroy();
    }
});

export default class SensorPulseExtension extends Extension {
    enable() {
        const langs = GLib.get_language_names();
        let tr = null;
        for (const l of langs) {
            const k = l.split(/[_.@]/)[0];
            if (TR[k]) { tr = TR[k]; break; }
            if (k === 'en' || k === 'C') break;
        }
        this._indicator = new Indicator(tr);
        // posición 0 de la zona derecha: a la IZQUIERDA de los iconos del sistema (red,
        // sonido, batería...), en el espacio libre, nunca entre ellos ni tras el menú del sistema
        Main.panel.addToStatusArea(this.uuid, this._indicator, 0, 'right');
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
