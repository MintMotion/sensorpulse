// Ejecuta el applet y el desklet de Cinnamon y la extensión de GNOME con objetos simulados
// (sin Cinnamon ni GNOME Shell) para detectar errores de JavaScript en sus caminos de código:
// modo texto e indicadores, menú, «sin datos», cambio de nombre. Uso:
//   node tests/js/run_xlets.js VIEWS.json
"use strict";
const fs = require("fs");
const path = require("path");
const ROOT = path.resolve(__dirname, "..", "..");
const snap = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const calls = [];
let repaints = 0;

// objeto que acepta cualquier llamada y propiedad (devuelve otro igual)
function stub(name) {
    const fn = function () { return stub(name + "()"); };
    return new Proxy(fn, {
        get(t, k) {
            if (k === Symbol.toPrimitive) return () => 0;
            if (k === "then") return undefined;
            if (!(k in t)) t[k] = stub(name + "." + String(k));
            return t[k];
        },
        construct() { return stub("new " + name); },
    });
}

class Actor {
    constructor(props) { Object.assign(this, props || {}); this.children = []; this.opacity = 255; this.visible = true; this._text = (props && props.text) || ""; }
    add(c) { this.children.push(c); }
    add_child(c) { this.children.push(c); }
    add_actor(c) { this.children.push(c); }
    destroy_all_children() { this.children = []; }
    destroy() { this.destroyed = true; }
    set_text(t) { if (typeof t !== "string") throw new Error("set_text sin texto: " + t); this._text = t; this.text = t; }
    get_text() { return this._text; }
    set_style(s) { if (typeof s !== "string") throw new Error("estilo"); this.style = s; }
    connect(sig, cb) { (this._sig = this._sig || {})[sig] = cb; return 1; }
    set_width() {} set_height() {}
}
class DrawingArea extends Actor {
    queue_repaint() {
        repaints++;
        if (this._sig && this._sig.repaint) this._sig.repaint(this);
    }
    get_context() { return cairoCtx(); }
    get_surface_size() { return [28, 28]; }
    get_theme_node() { return { get_foreground_color: () => ({ red: 230, green: 230, blue: 230, alpha: 255 }) }; }
}
function cairoCtx() {
    const ops = ["setLineCap", "setLineWidth", "setSourceRGBA", "setSourceRGB", "arc", "stroke", "fill",
                 "moveTo", "lineTo", "newPath", "save", "restore", "translate", "rectangle", "paint",
                 "setOperator", "closePath", "fillPreserve", "clip", "setSource", "rotate", "scale",
                 "setDash", "curveTo", "lineToRel", "arcNegative", "strokePreserve", "newSubPath"];
    const c = {};
    for (const o of ops) c[o] = (...a) => { for (const x of a) if (typeof x === "number" && !isFinite(x)) throw new Error("cairo." + o + " con NaN"); };
    c.$dispose = () => {};
    return c;
}
class MenuItem extends Actor {
    constructor(text, params) { super(); this.label = new Actor({ text }); this.menu = new Menu(); }
}
class Menu {
    constructor() { this.items = []; this.isOpen = false; }
    addMenuItem(i) { this.items.push(i); }
    addAction(t, cb) { const i = new MenuItem(t); i.cb = cb; this.items.push(i); return i; }
    removeAll() { this.items = []; }
    toggle() { this.isOpen = !this.isOpen; }
    connect() {}
}
const St = { BoxLayout: Actor, Label: Actor, Widget: Actor, Icon: Actor, DrawingArea,
             IconType: { FULLCOLOR: 0, SYMBOLIC: 1 }, Side: { LEFT: 0 } };
const Clutter = { ActorAlign: { CENTER: 1, FILL: 0 }, BinLayout: class {} };
const GLib = {
    get_monotonic_time: () => Date.now() * 1000, get_language_names: () => ["es_ES.UTF-8", "es", "C"],
    VariantType: class {}, Variant: class {}, timeout_add: () => 7, timeout_add_seconds: () => 8,
    source_remove: () => {}, spawn_async: (...a) => calls.push(["spawn", a[1]]),
    PRIORITY_DEFAULT: 0, SOURCE_CONTINUE: true, SOURCE_REMOVE: false, SpawnFlags: { SEARCH_PATH: 1 },
};
const Gio = {
    DBus: { session: { signal_subscribe: () => 1, signal_unsubscribe: () => {}, call: () => {} } },
    DBusSignalFlags: { NONE: 0 }, DBusCallFlags: { NONE: 0, NO_AUTO_START: 1 },
    bus_watch_name: () => 2, bus_unwatch_name: () => {}, BusType: { SESSION: 0 },
    BusNameWatcherFlags: { NONE: 0 }, Cancellable: class { cancel() {} },
};
const PopupMenu = {
    PopupMenuManager: class { addMenu() {} }, PopupMenuItem: MenuItem, PopupIconMenuItem: MenuItem,
    PopupSeparatorMenuItem: MenuItem, PopupSubMenuMenuItem: MenuItem, PopupMenuSection: Menu,
};
const Util = { spawn: a => calls.push(["spawn", a]), spawnCommandLine: c => calls.push(["cmd", c]) };
const Mainloop = { timeout_add: () => 5, timeout_add_seconds: () => 6, source_remove: () => {} };
const Cairo = { LineCap: { ROUND: 1, BUTT: 0 }, Operator: { SOURCE: 1, OVER: 2 }, LinearGradient: class { addColorStopRGBA() {} addColorStopRGB() {} },
                RadialGradient: class { addColorStopRGBA() {} } };
global.global = { logError: e => { throw e; } };
global.logError = e => { throw e; };

function check(cond, msg) { if (!cond) { console.error("FALLO: " + msg); process.exitCode = 1; } else console.log("ok  " + msg); }

// ------------------------------------------------------------------ applet de Cinnamon
{
    // Igual que Cinnamon (js/ui/applet.js): _panelHeight y _scaleMode son getters de solo lectura;
    // asignarlos en modo estricto lanza TypeError y Cinnamon descarta el applet.
    class Applet {
        constructor() { this.actor = new Actor(); this.panel = { height: 32 }; }
        get _panelHeight() { return this.panel.height; }
        get _scaleMode() { return true; }
        setAllowedLayout() {} set_applet_tooltip(t) { this.tip = t; }
    }
    const imports = { ui: { applet: { Applet, AllowedLayout: { BOTH: 0 }, AppletPopupMenu: Menu }, popupMenu: PopupMenu },
                      gi: { St, Clutter, Gio, GLib }, misc: { util: Util }, mainloop: Mainloop, cairo: Cairo };
    const src = fs.readFileSync(path.join(ROOT, "data/cinnamon/applet/sensorpulse@mintmotion/applet.js"), "utf8");
    const main = new Function("imports", src + "\nreturn main;")(imports);
    const a = main({}, 0, 30, 1);
    for (const style of ["text", "gauge"]) {
        for (const labels of [true, false]) {
            const s = JSON.parse(JSON.stringify(snap));
            s.views.panel_style = style; s.views.panel_labels = labels;
            a._update(s);
            check(a.labels.length === s.views.panel_count, `applet ${style}/${labels}: ${a.labels.length} valores en la barra`);
            if (style === "gauge") check(a.labels.every(l => l._lbl.get_text().length > 0), "applet: cifra dentro del indicador");
            else check(a.labels.every(l => l.get_text().length > 0 && l._name.visible === labels), "applet: texto y nombre");
            a._buildMenu();
            const texts = a.menu.items.map(i => i.label && i.label.get_text());
            check(texts.some(t => t === s.views.tr.rename), "applet: submenú «cambiar nombre»");
            const sub = a.menu.items.find(i => i.label && i.label.get_text() === s.views.tr.rename);
            sub.menu.items[0]._sig.activate();
            check(calls.length && calls[calls.length - 1][1][1] === "--rename", "applet: abre sensorpulse --rename");
            for (const ex of s.views.panel_extra)
                check(texts.some(t => t && t.indexOf(ex.label) >= 0), "applet: valor extra en el menú: " + ex.label);
        }
    }
    a._markStale();
    check(a.labels.every(l => (l._lbl ? l._lbl.get_text() : l.get_text()) === "–"), "applet: «–» sin datos");
    check(repaints > 0, "applet: indicadores dibujados (" + repaints + ")");
}

// ------------------------------------------------------------------ desklet de Cinnamon
{
    class Desklet { constructor() { this.actor = new Actor(); this._menu = new Menu(); this.metadata = {}; }
                    setContent(c) { this.content = c; } setHeader() {} }
    const DEF = { style: "hud", size: 1.0, count: 3 };
    class Settings { constructor(o) { this.o = o; } bind(k, prop) { this.o[prop] = DEF[k]; } }
    const PangoCairo = new Proxy({}, { get: () => () => ({ set_font_description() {}, set_text() {}, get_pixel_extents: () => [{}, { width: 10, height: 10 }],
                                                            set_attributes() {}, set_width() {}, set_ellipsize() {}, get_pixel_size: () => [10, 10] }) });
    const Pango = stub("Pango");
    const imports = { ui: { desklet: { Desklet }, settings: { DeskletSettings: Settings, BindingDirection: { IN: 0 } }, popupMenu: PopupMenu },
                      gi: { St, Clutter, Gio, GLib, Pango, PangoCairo }, misc: { util: Util }, mainloop: Mainloop, cairo: Cairo };
    const src = fs.readFileSync(path.join(ROOT, "data/cinnamon/desklet/sensorpulse-gauges@mintmotion/desklet.js"), "utf8");
    const main = new Function("imports", src + "\nreturn main;")(imports);
    const d = main({}, 1);
    for (const st of ["hud", "rings", "bars"]) {
        d.style = st;
        d._update(JSON.parse(JSON.stringify(snap)));
        d._animate();
        check(d.items.length === Math.min(3, snap.views.floating.length), "desklet " + st + ": dibujado");
    }
    const sub = d._menu.items.find(i => i.label && i.label.get_text() === snap.views.tr.rename);
    check(sub && sub.menu.items.length === d.items.length, "desklet: submenú de cambio de nombre");
    sub.menu.items[0]._sig.activate();
    check(calls[calls.length - 1][1][1] === "--rename", "desklet: abre sensorpulse --rename");
}

// ------------------------------------------------------------------ extensión de GNOME
{
    let src = fs.readFileSync(path.join(ROOT, "data/gnome-shell/sensorpulse@mintmotion.github.io/extension.js"), "utf8");
    class Button extends Actor { _init() { this.menu = new Menu(); } add_child(c) { this.children.push(c); } destroy() {} }
    const added = [];
    const mods = {
        "gi://GObject": { registerClass: c => c }, "gi://St": St, "gi://Gio": Gio, "gi://GLib": GLib,
        "gi://Clutter": Clutter, "cairo": Cairo,
        "resource:///org/gnome/shell/extensions/extension.js": { Extension: class { constructor() { this.uuid = "x"; } } },
        "resource:///org/gnome/shell/ui/main.js": { panel: { height: 32, addToStatusArea: (...a) => added.push(a) } },
        "resource:///org/gnome/shell/ui/panelMenu.js": { Button },
        "resource:///org/gnome/shell/ui/popupMenu.js": PopupMenu,
    };
    src = src.replace(/^import \* as (\w+) from '([^']+)';$/mg, "const $1 = MODS['$2'];")
             .replace(/^import \{(\w+)\} from '([^']+)';$/mg, "const {$1} = MODS['$2'];")
             .replace(/^import (\w+) from '([^']+)';$/mg, "const $1 = MODS['$2'];")
             .replace("export default class", "return class");
    const Ext = new Function("MODS", src)(mods);
    const e = new Ext();
    e.enable();
    check(added.length && added[0][2] === 0 && added[0][3] === "right", "extensión: a la izquierda de los iconos del sistema (posición 0, zona derecha)");
    const ind = e._indicator;
    ind._init(null);
    for (const style of ["text", "gauge"]) {
        const s = JSON.parse(JSON.stringify(snap));
        s.views.panel_style = style;
        ind._update(s);
        check(ind._labels.length === s.views.panel_count, `extensión ${style}: ${ind._labels.length} valores`);
        ind._buildSummary();
        check(ind._renameMenu.menu.items.length === s.views.panel.length + s.views.panel_extra.length, "extensión: submenú de nombres");
    }
    ind._markStale();
    check(ind._labels.every(l => (l._lbl ? l._lbl.get_text() : l.get_text()) === "–"), "extensión: «–» sin datos");
}
