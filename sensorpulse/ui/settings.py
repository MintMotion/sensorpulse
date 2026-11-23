"""Ajustes: una sola ventana con General, Sensores, Alarmas y Acerca de."""

import json
import os
import shutil
import subprocess
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Gio

from .. import APP_NAME, VERSION, AUTHOR, AUTHOR_EMAIL, LICENSE
from .. import config as cfgmod
from ..core.alarms import new_rule, rule_level, ABOVE, BELOW
from ..i18n import _
from .floating import style_names
from . import draw as D
from . import detect
from . import envinfo
from ..core import historydb   # cargado ya: tras «purge» el fichero desaparece (fallo F2)

from .sounds import picker as sound_picker


def _row(label, widget, sub=None):
    b = Gtk.Box(spacing=12)
    b.set_border_width(4)
    v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    l = Gtk.Label(label=label, xalign=0)
    v.pack_start(l, False, False, 0)
    if sub:
        s = Gtk.Label(label=sub, xalign=0)
        s.get_style_context().add_class("dim-label")
        s.set_line_wrap(True)
        s.set_max_width_chars(60)
        v.pack_start(s, False, False, 0)
    b.pack_start(v, True, True, 0)
    # centrado: con una descripción de dos líneas el interruptor salía estirado
    widget.set_valign(Gtk.Align.CENTER)
    b.pack_end(widget, False, False, 0)
    return b


def _is_temp(c):
    return bool(c) and c.get("kind") == "temp"


def _to_user(cfg, c, v):
    return v * 9 / 5 + 32 if _is_temp(c) and cfg.get("unit") == "F" else v


def _to_celsius(cfg, c, v):
    return (v - 32) * 5 / 9 if _is_temp(c) and cfg.get("unit") == "F" else v


def _rule_unit(cfg, c):
    if not c:
        return ""
    if c["kind"] == "temp":
        return "°F" if cfg.get("unit") == "F" else "°C"
    return {"fan": "RPM", "percent": "%", "power": "W"}.get(c["kind"], c.get("unit", ""))


def _rule_range(cfg, c):
    kind = c["kind"] if c else "temp"
    if kind == "temp":
        return (32, 300, 1) if cfg.get("unit") == "F" else (0, 150, 1)
    if kind == "fan":
        return 0, 10000, 50
    if kind == "percent":
        return 0, 100, 1
    if kind == "power":
        return 0, 1000, 0.5       # medio vatio: una regla de 5,5 W era imposible
    return 0, 1000, 1


def _rule_digits(c):
    return 1 if c and c["kind"] == "power" else 0


def _rule_default(c):
    """Valores iniciales sensatos según el sensor (en °C)."""
    if not c:
        return ABOVE, 80
    if c["kind"] == "temp":
        base = c.get("tjmax")
        return ABOVE, round((base - 5) if base else (c.get("warn") or 80))
    if c["kind"] == "fan":
        return BELOW, 300
    if c["kind"] == "percent":
        return ABOVE, 90
    return ABOVE, 100


def _section(text):
    l = Gtk.Label(xalign=0)
    l.set_markup("<b>%s</b>" % GLib.markup_escape_text(text))
    l.set_margin_top(10)
    return l


class Settings(Gtk.Window):
    def __init__(self, app, store):
        super().__init__(title=_("%s settings") % APP_NAME)
        self.app, self.store = app, store
        self.cfg = store.cfg
        self.set_default_size(780, 600)
        self.set_icon_name("sensorpulse")
        box = Gtk.Box()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        side = Gtk.StackSidebar(stack=self.stack)
        side.set_size_request(170, -1)
        box.pack_start(side, False, False, 0)
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)
        box.pack_start(self.stack, True, True, 0)
        self.add(box)
        for name, title, builder in (("general", _("General"), self.page_general),
                                     ("sensors", _("Sensors"), self.page_sensors),
                                     ("alarms", _("Alarms"), self.page_alarms),
                                     ("about", _("About"), self.page_about)):
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            v.set_border_width(18)
            builder(v)
            sw = Gtk.ScrolledWindow()
            sw.add(v)
            self.stack.add_titled(sw, name, title)
        self.connect("delete-event", lambda *a: self.save() or False)

    def _language_changed(self, combo):
        code = combo.get_active_id()
        if code == (self.cfg.get("language") or "auto"):
            return
        self.cfg["language"] = code
        self.save()           # el servicio cambia de idioma en caliente (avisos, nombres)
        # la interfaz se construye con el idioma ya elegido: se reinicia para no mezclar idiomas
        GLib.timeout_add(300, lambda: (self.app.restart_ui(["--settings"]), False)[1])

    def save(self):
        self.store.save_cfg()
        # background=True: cambiar cualquier ajuste no debe sacar la ventana principal por
        # encima de Ajustes (la 0.3.6 la presentaba en cada cambio si «En una ventana» estaba
        # activado); el interruptor de la ventana la abre o la cierra él mismo
        self.app.apply_display(background=True)
        cfgmod_autostart(self.cfg.get("autostart", True))

    def show_page(self, page, channel=None):
        if page:
            self.stack.set_visible_child_name(page)
        if page == "alarms" and channel:
            self.edit_rule(None, channel)

    # ------------------------------------------------------------ general
    def page_general(self, v):
        disp = self.cfg["display"]
        v.pack_start(_section(_("Where to show the sensors")), False, False, 0)
        for key, label in (("panel", _("In the panel")), ("window", _("In a window")),
                           ("floating", _("Floating on the desktop"))):
            sw = Gtk.Switch(active=bool(disp.get(key)))
            sw.connect("notify::active", lambda w, p, k=key: self._display_changed(k, w.get_active()))
            v.pack_start(_row(label, sw), False, False, 0)
        # Con «bloqueado + atravesable» el widget no recibe clics y el menú contextual
        # deja de ser accesible: estos interruptores son la salida para desbloquearlo.
        fl = self.cfg["floating"]
        for key, label in (("locked", _("Lock position")),
                           ("click_through", _("Click-through when locked"))):
            sw = Gtk.Switch(active=bool(fl.get(key)))
            sw.connect("notify::active", lambda w, p, k=key: (fl.__setitem__(k, w.get_active()),
                                                             self.save()))
            v.pack_start(_row(label, sw), False, False, 0)
        self._panel_section(v)
        v.pack_start(_section(_("Appearance")), False, False, 0)
        style = Gtk.ComboBoxText()
        for k, n in style_names().items():
            style.append(k, n)
        style.set_active_id(self.cfg.get("style", "cockpit"))
        style.connect("changed", lambda w: (self.cfg.__setitem__("style", w.get_active_id()),
                                            self.save()))
        v.pack_start(_row(_("Floating widget style"), style), False, False, 0)
        size = Gtk.ComboBoxText()
        for k, n in (("small", _("Small")), ("medium", _("Medium")), ("large", _("Large"))):
            size.append(k, n)
        size.set_active_id(self.cfg.get("size", "medium"))
        size.connect("changed", lambda w: (self.cfg.__setitem__("size", w.get_active_id()), self.save()))
        v.pack_start(_row(_("Size"), size), False, False, 0)
        theme = Gtk.ComboBoxText()
        theme.append("auto", _("Same as the system"))
        theme.append("dark", _("Dark"))
        theme.append("deep", _("Deep dark"))
        theme.append("light", _("Light"))
        theme.set_active_id(self.cfg.get("theme", "auto"))
        theme.connect("changed", lambda w: (self.cfg.__setitem__("theme", w.get_active_id()), self.save()))
        v.pack_start(_row(_("Theme"), theme), False, False, 0)
        win = self.cfg["window"]
        lay = Gtk.ComboBoxText()
        lay.append("bento", _("Bento (one large card)"))
        lay.append("classic", _("Classic (all the same size)"))
        lay.set_active_id(win.get("layout", "bento"))
        lay.connect("changed", lambda w: (win.__setitem__("layout", w.get_active_id()), self.save()))
        v.pack_start(_row(_("Window layout"), lay), False, False, 0)
        big = Gtk.ComboBoxText()
        big.append("", _("Automatic (CPU)"))
        for d in self.store.devices():
            big.append(d["id"], d["name"])
        big.set_active_id(win.get("big") or "")
        big.connect("changed", lambda w: (win.__setitem__("big", w.get_active_id() or None),
                                          self.save()))
        v.pack_start(_row(_("Large card"), big), False, False, 0)
        au_sw = Gtk.Switch(active=bool(win.get("aurora")))
        au_sw.connect("notify::active", lambda w, p: (win.__setitem__("aurora", w.get_active()),
                                                      self.save()))
        v.pack_start(_row(_("Aurora background"), au_sw,
                          _("A very soft glow whose color follows the state of your computer.")),
                     False, False, 0)
        cb = Gtk.ColorButton()
        rgba = Gdk.RGBA()
        rgba.parse(self.cfg.get("accent", "#22d3ee"))
        cb.set_rgba(rgba)
        cb.connect("color-set", self._accent)
        v.pack_start(_row(_("Accent color"), cb), False, False, 0)
        unit = Gtk.ComboBoxText()
        unit.append("C", "°C")
        unit.append("F", "°F")
        unit.set_active_id(self.cfg.get("unit", "C"))
        unit.connect("changed", lambda w: (self.cfg.__setitem__("unit", w.get_active_id()), self.save()))
        v.pack_start(_row(_("Temperature unit"), unit), False, False, 0)
        cbsw = Gtk.Switch(active=bool(self.cfg.get("colorblind")))
        cbsw.connect("notify::active", lambda w, p: (self.cfg.__setitem__("colorblind", w.get_active()),
                                                     self.save()))
        v.pack_start(_row(_("Color-blind friendly colors"), cbsw), False, False, 0)
        fan = Gtk.Switch(active=bool(self.cfg.get("animate_fans", True)))
        fan.connect("notify::active", lambda w, p: (self.cfg.__setitem__("animate_fans", w.get_active()),
                                                    self.save()))
        v.pack_start(_row(_("Animate fan icons"), fan), False, False, 0)
        lang = Gtk.ComboBoxText()
        for k, n in (("auto", _("Automatic")), ("es", "Español"), ("en", "English"),
                     ("fr", "Français"), ("it", "Italiano"), ("de", "Deutsch"),
                     ("pt", "Português"), ("ja", "日本語"), ("zh", "中文")):
            lang.append(k, n)
        lang.set_active_id(self.cfg.get("language") or "auto")
        lang.connect("changed", self._language_changed)
        v.pack_start(_row(_("Language"), lang, _("The program restarts to apply the new language.")),
                     False, False, 0)
        v.pack_start(_section(_("System")), False, False, 0)
        au = Gtk.Switch(active=bool(self.cfg.get("autostart", True)))
        au.connect("notify::active", lambda w, p: (self.cfg.__setitem__("autostart", w.get_active()),
                                                   self.save()))
        v.pack_start(_row(_("Start with the computer"), au,
                          _("Needed for alarms to work when the window is closed.")), False, False, 0)
        iv = Gtk.SpinButton.new_with_range(1, 10, 1)
        iv.set_value(float(self.cfg["intervals"].get("fast", 2)))
        iv.connect("value-changed", lambda w: (self.cfg["intervals"].__setitem__("fast", int(w.get_value())),
                                               self.save()))
        v.pack_start(_row(_("Refresh every (seconds)"), iv), False, False, 0)
        v.pack_start(_section(_("Disks")), False, False, 0)
        dsw = Gtk.Switch(active=bool(self.cfg.get("disk_temps", True)))
        dsw.connect("notify::active", lambda w, p: (self.cfg.__setitem__("disk_temps", w.get_active()),
                                                    self.save()))
        v.pack_start(_row(_("Read hard disk temperatures"), dsw,
                          _("Turn this off if your disks must never be woken up: without the "
                            "disk service (UDisks2) the program cannot tell whether a disk is asleep.")),
                     False, False, 0)
        from . import disks as disks_ui
        db = disks_ui.build(self.store, self.cfg, self.save)
        db.set_border_width(4)
        v.pack_start(db, False, False, 0)
        v.pack_start(_section(_("Assistant")), False, False, 0)
        again = Gtk.Button(label=_("Run the welcome assistant again"))
        again.connect("clicked", lambda *a: self.app.show_wizard())
        v.pack_start(again, False, False, 10)
        un = Gtk.Button(label=_("Uninstall %s…") % APP_NAME)
        un.get_style_context().add_class("destructive-action")
        un.connect("clicked", self.uninstall)
        v.pack_start(un, False, False, 0)

    def _panel_section(self, v):
        """Panel: cuántos valores caben en la barra, su aspecto y los nombres."""
        v.pack_start(_section(_("Panel")), False, False, 0)
        pc = Gtk.SpinButton.new_with_range(1, 8, 1)
        pc.set_value(int(self.cfg.get("panel_count", 3)))
        pc.connect("value-changed", lambda w: (self.cfg.__setitem__("panel_count", int(w.get_value())),
                                               self.save()))
        v.pack_start(_row(_("Values shown in the panel"), pc,
                          _("If you choose more sensors for the panel, the rest appear when you "
                            "click it. Choose which ones in «Sensors».")), False, False, 0)
        ps = Gtk.ComboBoxText()
        ps.append("text", _("Name and number"))
        ps.append("gauge", _("Gauges (like the desktop widget)"))
        ps.set_active_id(self.cfg.get("panel_style", "text"))
        ps.connect("changed", lambda w: (self.cfg.__setitem__("panel_style", w.get_active_id()),
                                         self.save()))
        v.pack_start(_row(_("Panel look"), ps), False, False, 0)
        nm = Gtk.Switch(active=bool(self.cfg.get("panel_labels", True)))
        nm.connect("notify::active", lambda w, p: (self.cfg.__setitem__("panel_labels", w.get_active()),
                                                   self.save()))
        v.pack_start(_row(_("Show the names in the panel"), nm,
                          _("Turn it off to fit more values in less space.")), False, False, 0)

    def _display_changed(self, key, on):
        self.cfg["display"][key] = on
        self.save()
        if key == "window":
            # al activarlo se abre (apply_display) y al desactivarlo se cierra: la 0.3.6 la
            # dejaba abierta y había que cerrarla con la X
            if on:
                self.app.show_window()
            else:
                self.app.hide_window()
            self.present()

    def _accent(self, btn):
        c = btn.get_rgba()
        self.cfg["accent"] = "#%02x%02x%02x" % (int(c.red * 255), int(c.green * 255), int(c.blue * 255))
        self.save()

    # ------------------------------------------------------------ sensores
    def page_sensors(self, v):
        v.pack_start(Gtk.Label(label=_("Choose what is shown and where. Double-click a name to rename it."),
                               xalign=0), False, False, 4)
        expl = Gtk.Label(label=_("Panel: next to the clock. On click: only in the list that opens "
                                 "when you click the panel. Floating: the desktop widget."), xalign=0)
        expl.set_line_wrap(True)
        expl.set_max_width_chars(90)
        expl.get_style_context().add_class("dim-label")
        v.pack_start(expl, False, False, 0)
        # columnas: nombre, visible, panel, flotante, id, editable, es_dispositivo, valor, nota,
        # solo al hacer clic
        self.sstore = Gtk.TreeStore(str, bool, bool, bool, str, bool, bool, str, str, bool)
        try:   # lectura puntual (también de lo oculto, sin discos SATA ni sensores lentos)
            values = json.loads(self.store.client.call("GetAllValues")[0]) if self.store.client else {}
        except Exception:
            values = {}
        notes = {"advanced": _("advanced reading"), "duplicate": _("duplicate of the main sensor"),
                 "offset": _("control value with offset (not the real temperature)"),
                 "acpi": _("generic firmware zone"),
                 "zero_rpm": _("0 RPM at start: maybe nothing connected")}
        vis = self.cfg["sensors"]
        from ..views import PANEL_LIMIT
        panel_auto = [c["id"] for c, d in self.store.featured("panel", PANEL_LIMIT)]
        float_auto = [c["id"] for c, d in self.store.featured("floating", 9)]
        panel_sel = vis.get("panel") or panel_auto
        float_sel = vis.get("floating") or float_auto
        menu_sel = set(vis.get("panel_menu") or [])
        for d in self.store.devices():
            it = self.sstore.append(None, [d["name"], True, False, False, d["id"], False, True,
                                           "", "", False])
            for c in d["channels"]:
                if c["kind"] == "status":
                    continue
                v_ = c.get("value")
                if v_ is None:
                    v_ = values.get(c["id"])
                if d.get("asleep"):
                    vtxt = _("asleep")
                elif v_ is None:
                    vtxt = "—"
                else:
                    item = self.store.item(dict(c, value=v_), d)
                    vtxt = " ".join(D.fmt(dict(item, anim=item["value"]))).strip()
                note = notes.get(c.get("note"), "")
                if not note and not cfgmod.is_visible(self.cfg, c, d["cls"]) and \
                        c["id"] not in self.cfg["sensors"]["visible"]:
                    note = _("hidden by default")
                self.sstore.append(it, [cfgmod.display_name(self.cfg, c),
                                        cfgmod.is_visible(self.cfg, c, d["cls"]),
                                        c["id"] in panel_sel, c["id"] in float_sel, c["id"], True,
                                        False, vtxt, note,
                                        c["id"] in menu_sel and c["id"] not in panel_sel])
        tv = Gtk.TreeView(model=self.sstore)
        r = Gtk.CellRendererText()
        r.connect("edited", self._renamed)
        col = Gtk.TreeViewColumn(_("Sensor"), r, text=0, editable=5)
        col.set_expand(True)
        tv.append_column(col)
        rv = Gtk.CellRendererText(xalign=1.0)
        tv.append_column(Gtk.TreeViewColumn(_("Now"), rv, text=7))
        for idx, title in ((1, _("Show")), (2, _("Panel")), (9, _("On click")), (3, _("Floating"))):
            t = Gtk.CellRendererToggle()
            t.connect("toggled", self._toggled, idx)
            c = Gtk.TreeViewColumn(title, t, active=idx)
            c.add_attribute(t, "visible", 5)
            tv.append_column(c)
        rn = Gtk.CellRendererText()
        rn.set_property("foreground", "#8a919c")
        rn.set_property("ellipsize", 3)   # PANGO_ELLIPSIZE_END
        cn = Gtk.TreeViewColumn(_("Note"), rn, text=8)
        cn.set_min_width(150)
        tv.append_column(cn)
        tv.expand_all()
        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        sw.add(tv)
        v.pack_start(sw, True, True, 0)
        hb = Gtk.Box(spacing=8)
        rec = Gtk.Button(label=_("Back to recommended"))
        rec.connect("clicked", self._sensors_default)
        hb.pack_start(rec, False, False, 0)
        rescan = Gtk.Button(label=_("Search for new sensors"))
        rescan.connect("clicked", lambda *a: self.store.client and self.store.client.rescan())
        hb.pack_start(rescan, False, False, 0)
        if detect.available():
            det = Gtk.Button(label=_("Detect sensors…"))
            det.set_tooltip_text(_("Activates the motherboard and disk sensors that are not loaded. "
                                   "Your password will be asked once."))
            det.connect("clicked", lambda *a: detect.run(self, self.store,
                                                         lambda d: self._reopen("sensors"),
                                                         settings_cb=self._show_new))
            hb.pack_start(det, False, False, 0)
        v.pack_start(hb, False, False, 6)

    def _toggled(self, rend, path, idx):
        it = self.sstore.get_iter(path)
        val = not self.sstore[it][idx]
        self.sstore[it][idx] = val
        cid = self.sstore[it][4]
        s = self.cfg["sensors"]
        if idx == 1:
            s["visible"][cid] = val
        else:
            # «Panel» y «Al hacer clic» se excluyen: un sensor está en la barra o solo en la lista
            if val and idx in (2, 9):
                self.sstore[it][11 - idx] = False
            # solo las listas de la columna tocada (el flotante no pasa de automático a fijo
            # por tocar el panel, ni al revés)
            cols = (("floating", 3),) if idx == 3 else (("panel", 2), ("panel_menu", 9))
            for key, col in cols:
                sel = []
                for dev in self.sstore:
                    for ch in dev.iterchildren():
                        if ch[col]:
                            sel.append(ch[4])
                s[key] = sel
        self.save()

    def _renamed(self, rend, path, text):
        it = self.sstore.get_iter(path)
        cid = self.sstore[it][4]
        text = text.strip()
        if text:
            self.cfg["sensors"]["renames"][cid] = text
        else:
            self.cfg["sensors"]["renames"].pop(cid, None)
        c, _d = self.store.find(cid)
        self.sstore[it][0] = text or (c["label"] if c else cid)
        self.save()

    def _show_new(self, ids):
        """Tras «Detectar sensores»: hacer visibles los sensores nuevos que el usuario marque."""
        for cid in ids:
            self.cfg["sensors"]["visible"][cid] = True
        self.save()

    def _reopen(self, page):
        self.destroy()
        GLib.timeout_add(1200, lambda: (self.app.open_settings(page=page), False)[1])

    def _sensors_default(self, *_a):
        s = self.cfg["sensors"]
        s["visible"], s["panel"], s["floating"], s["panel_menu"] = {}, [], [], []
        self.save()
        self.destroy()
        self.app.open_settings(page="sensors")

    # ------------------------------------------------------------ alarmas
    def page_alarms(self, v):
        a = self.cfg["alarms"]
        v.pack_start(_section(_("Automatic alarms")), False, False, 0)
        v.pack_start(Gtk.Label(label=_("Use the limits recommended by your hardware."), xalign=0),
                     False, False, 0)

        def sw(label, key, sub=None):
            s = Gtk.Switch(active=bool(a.get(key)))
            s.connect("notify::active", lambda w, p: (a.__setitem__(key, w.get_active()), self.save()))
            v.pack_start(_row(label, s, sub), False, False, 0)

        sw(_("Automatic alarms"), "enabled")
        sw(_("Warn when the CPU or GPU slows down because of heat"), "throttle")
        sw(_("Warn when it gets hot while almost idle"), "idle_hot",
           _("A sign that the air vents may be blocked or the cooling is not working well."))
        sw(_("Warn when a fan stops"), "fan_stop")
        sw(_("Also warn when something is hot (not only critical)"), "notify_hot")
        sw(_("Blink when an alarm is active"), "blink")
        sw(_("Desktop notifications"), "notify")
        sw(_("Sound"), "sound")
        sw(_("Repeat the sound every 2 minutes while the alarm lasts"), "repeat",
           _("The volume rises little by little. «Got it» in the notification stops it."))
        hb, _snd = sound_picker(self.store, a.get("sound_name"),
                                lambda k: (a.__setitem__("sound_name", k), self.save()))
        v.pack_start(_row(_("Alarm sound"), hb, _("Critical alarms. ▶ plays it.")), False, False, 0)
        sw(_("Soft sound for warnings"), "warn_sound")
        wb, _wsnd = sound_picker(self.store, a.get("warn_sound_name"),
                                 lambda k: (a.__setitem__("warn_sound_name", k), self.save()),
                                 default="sensorpulse-warning")
        v.pack_start(_row(_("Warning sound"), wb), False, False, 0)
        test = Gtk.Button(label=_("Test alarm"))
        test.set_tooltip_text(_("Shows a test notification and plays the alarm sound."))
        test.set_halign(Gtk.Align.START)
        test.connect("clicked", lambda *x: self.store.client and self.store.client.test_alarm(
            a.get("sound_name", "")))
        v.pack_start(test, False, False, 4)
        delay = Gtk.SpinButton.new_with_range(0, 120, 5)
        delay.set_value(float(a.get("delay", 10)))
        delay.connect("value-changed", lambda w: (a.__setitem__("delay", int(w.get_value())), self.save()))
        v.pack_start(_row(_("Wait before alarming (seconds)"), delay,
                          _("Avoids false alarms from short peaks.")), False, False, 0)
        hy = Gtk.SpinButton.new_with_range(1, 20, 1)
        hy.set_value(float(a.get("hysteresis", 5)))
        hy.connect("value-changed", lambda w: (a.__setitem__("hysteresis", int(w.get_value())), self.save()))
        v.pack_start(_row(_("Degrees it must drop before alarming again"), hy), False, False, 0)

        v.pack_start(_section(_("My alarms")), False, False, 0)
        self.rules_box = Gtk.ListBox()
        self.rules_box.set_selection_mode(Gtk.SelectionMode.NONE)
        v.pack_start(self.rules_box, False, False, 0)
        add = Gtk.Button(label="＋  " + _("New alarm"))
        add.set_halign(Gtk.Align.START)
        add.connect("clicked", lambda *x: self.edit_rule(None))
        v.pack_start(add, False, False, 6)
        self.refresh_rules()
        v.pack_start(Gtk.Label(label=_("Warnings only show a notification; critical alarms also "
                                       "play the sound. During games or full-screen videos only "
                                       "critical alarms interrupt."), xalign=0, wrap=True,
                               max_width_chars=70), False, False, 6)
        v.pack_start(_section(_("Recent alarms")), False, False, 0)
        self.log_label = Gtk.Label(xalign=0)
        self.log_label.set_selectable(True)
        v.pack_start(self.log_label, False, False, 0)
        self.refresh_log()

    def refresh_rules(self):
        for ch in self.rules_box.get_children():
            self.rules_box.remove(ch)
        rules = self.cfg.get("rules", [])
        if not rules:
            l = Gtk.Label(label=_("You have no alarms of your own yet."), xalign=0)
            l.get_style_context().add_class("dim-label")
            self.rules_box.add(l)
        for r in rules:
            c, _d = self.store.find(r["channel"])
            name = cfgmod.display_name(self.cfg, c) if c else r["channel"]
            unit, shown = _rule_unit(self.cfg, c), _to_user(self.cfg, c, r["value"])
            op = _("above") if r["op"] == ABOVE else _("below")
            how = [x for x, k in ((_("color"), "visual"), (_("notification"), "notify"),
                                  (_("sound"), "sound")) if r.get(k)]
            hb = Gtk.Box(spacing=8)
            hb.set_border_width(4)
            sw = Gtk.Switch(active=r.get("enabled", True))
            sw.set_valign(Gtk.Align.CENTER)
            sw.connect("notify::active", lambda w, p, rr=r: (rr.__setitem__("enabled", w.get_active()),
                                                            self.save()))
            hb.pack_start(sw, False, False, 0)
            lab = Gtk.Label(xalign=0)
            lab.set_markup("<b>%s</b> %s %g %s\n<small>%s</small>" % (
                GLib.markup_escape_text(name), op, round(shown, 1), unit,
                GLib.markup_escape_text(", ".join(how))))
            hb.pack_start(lab, True, True, 0)
            ed = Gtk.Button.new_from_icon_name("document-edit-symbolic", Gtk.IconSize.BUTTON)
            ed.connect("clicked", lambda w, rr=r: self.edit_rule(rr))
            hb.pack_end(ed, False, False, 0)
            de = Gtk.Button.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.BUTTON)
            de.connect("clicked", lambda w, rr=r: self._delete_rule(rr))
            hb.pack_end(de, False, False, 0)
            self.rules_box.add(hb)
        self.rules_box.show_all()

    def _delete_rule(self, r):
        self.cfg["rules"] = [x for x in self.cfg["rules"] if x["id"] != r["id"]]
        self.save()
        self.refresh_rules()

    def refresh_log(self):
        try:
            log = self.store.client.alarm_log() if self.store.client else []
        except Exception:
            log = []
        lines = []
        kinds = {"throttle": _("slowing down because of heat"), "idle": _("hot while idle"),
                 "fanstop": _("fan stopped")}
        # cada «fired» con SU «cleared» (el primero de la misma regla que llega después):
        # antes se usaba el último «cleared» de la regla para todas sus alarmas anteriores
        ends, open_ = {}, {}
        for i, e in enumerate(log):
            if e.get("type") == "fired":
                open_[e.get("rule")] = i
            elif e.get("type") == "cleared" and e.get("rule") in open_:
                ends[open_.pop(e.get("rule"))] = e
        for i in range(len(log) - 1, max(-1, len(log) - 41), -1):
            e = log[i]
            if e.get("type") != "fired" or len(lines) >= 15:
                continue
            what = kinds.get(e.get("kind")) or self._log_value(e)
            sym = "‼" if e.get("level") == "critical" else "⚠"
            end = ends.get(i)
            dur = ""
            if end and end.get("duration"):
                dur = "  ·  " + _("%d min") % max(1, round(end["duration"] / 60))
            lines.append("%s %s  ·  %s  ·  %s%s" % (
                sym, time.strftime("%d/%m %H:%M", time.localtime(e["time"])), e["label"], what, dur))
        self.log_label.set_text("\n".join(lines) or _("No alarms so far."))

    def _log_value(self, e):
        """Valor de una alarma del registro con su unidad (en °F si se eligió °F; antes se
        mostraba el número en °C y sin unidad)."""
        v = e.get("value")
        if v is None:
            return "–"
        c, _d = self.store.find(e.get("channel")) if e.get("channel") else (None, None)
        if c is not None:
            unit = _rule_unit(self.cfg, c)
            shown = _to_user(self.cfg, c, v)
            if c["kind"] == "power":
                return "%.1f %s" % (shown, unit)
            return ("%.0f%s" if unit in ("°C", "°F", "%") else "%.0f %s") % (shown, unit)
        return "%.0f" % v

    def edit_rule(self, rule, channel=None):
        """Las reglas se guardan SIEMPRE en °C; se muestran y editan en la unidad elegida."""
        dlg = Gtk.Dialog(title=_("Alarm"), transient_for=self, modal=True)
        dlg.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Save"), Gtk.ResponseType.OK)
        area = dlg.get_content_area()
        area.set_spacing(8)
        area.set_border_width(14)
        r = dict(rule) if rule else new_rule(channel or "", 80)
        combo = Gtk.ComboBoxText()
        for d in self.store.devices():
            for c in d["channels"]:
                if c["kind"] in ("temp", "fan", "percent", "power"):
                    combo.append(c["id"], "%s · %s" % (d["name"], cfgmod.display_name(self.cfg, c)))
        if r["channel"]:
            if not combo.set_active_id(r["channel"]):
                # el sensor de la regla no está ahora (disco desconectado...): se ofrece igual
                # para que «Guardar» no descarte la regla en silencio
                combo.append(r["channel"], r["channel"] + " · " + _("not available"))
                combo.set_active_id(r["channel"])
        else:
            combo.set_active(0)
        area.pack_start(_row(_("Sensor"), combo), False, False, 0)
        op = Gtk.ComboBoxText()
        op.append(ABOVE, _("goes above"))
        op.append(BELOW, _("drops below"))
        op.set_active_id(r["op"])
        val = Gtk.SpinButton.new_with_range(-50, 20000, 1)
        unit_lbl = Gtk.Label()
        hb = Gtk.Box(spacing=6)
        hb.pack_start(op, False, False, 0)
        hb.pack_start(val, False, False, 0)
        hb.pack_start(unit_lbl, False, False, 0)
        area.pack_start(_row(_("When it"), hb), False, False, 0)

        def _sensor_changed(*_a, first=False):
            c, _d = self.store.find(combo.get_active_id())
            lo, hi, step = _rule_range(self.cfg, c)
            val.set_digits(_rule_digits(c))
            val.set_range(lo, hi)
            val.set_increments(step, step * 10)
            unit_lbl.set_text(_rule_unit(self.cfg, c))
            if first and rule:
                val.set_value(_to_user(self.cfg, c, r["value"]))
            else:
                op_id, default = _rule_default(c)
                op.set_active_id(op_id)
                val.set_value(_to_user(self.cfg, c, default))

        combo.connect("changed", _sensor_changed)
        _sensor_changed(first=True)
        delay = Gtk.SpinButton.new_with_range(0, 3600, 5)   # el máximo que admite la configuración
        delay.set_value(r.get("delay", 10))
        area.pack_start(_row(_("For at least (seconds)"), delay), False, False, 0)
        # nivel: decide el color del panel, el sonido y si se aplaza en pantalla completa
        level = Gtk.ComboBoxText()
        level.append("hot", _("Warning (it waits if you are busy)"))
        level.append("critical", _("Critical (always interrupts)"))
        level.set_active_id(rule_level(r))
        area.pack_start(_row(_("Level"), level), False, False, 0)
        checks = {}
        for key, label in (("visual", _("Color and blinking")), ("notify", _("Notification")),
                           ("sound", _("Sound")), ("repeat", _("Repeat the sound"))):
            c = Gtk.CheckButton(label=label)
            c.set_active(bool(r.get(key)))
            area.pack_start(c, False, False, 0)
            checks[key] = c
        sbox, snd = sound_picker(self.store, r.get("sound_name") or "sensorpulse-critical",
                                 lambda k: None)
        area.pack_start(_row(_("Sound"), sbox), False, False, 0)
        dlg.show_all()
        if dlg.run() == Gtk.ResponseType.OK and combo.get_active_id():
            c, _d = self.store.find(combo.get_active_id())
            r.update({"channel": combo.get_active_id(), "op": op.get_active_id(),
                      "value": round(_to_celsius(self.cfg, c, float(val.get_value())), 2),
                      "delay": int(delay.get_value()), "sound_name": snd.get_active_id(),
                      "level": level.get_active_id()})
            for k, c2 in checks.items():
                r[k] = c2.get_active()
            rules = [x for x in self.cfg.get("rules", []) if x["id"] != r["id"]]
            rules.append(r)
            self.cfg["rules"] = rules
            self.save()
            self.refresh_rules()
        dlg.destroy()

    # ------------------------------------------------------------ acerca de
    def page_about(self, v):
        t = Gtk.Label()
        t.set_markup("<span size='xx-large' weight='bold'>%s</span>\n%s %s" % (
            APP_NAME, _("Version"), VERSION))
        v.pack_start(t, False, False, 10)
        v.pack_start(Gtk.Label(label=_("Sensor monitor for Debian, Ubuntu, Linux Mint and derivatives.")),
                     False, False, 0)
        v.pack_start(Gtk.Label(label="%s · %s" % (AUTHOR, AUTHOR_EMAIL)), False, False, 6)
        v.pack_start(Gtk.Label(label=_("License: %s · Free and open source software") % LICENSE),
                     False, False, 0)
        v.pack_start(Gtk.Label(label=_("It only reads sensors: it never changes fans or hardware settings.")),
                     False, False, 10)
        rep = Gtk.Button(label=_("Copy hardware report"))
        rep.set_halign(Gtk.Align.CENTER)
        rep.set_tooltip_text(_("Anonymous summary of your sensors to paste in a forum or a bug report. "
                               "It does not include your name, computer name or serial numbers."))
        rep.connect("clicked", self._copy_report)
        v.pack_start(rep, False, False, 6)
        self.rep_label = Gtk.Label()
        v.pack_start(self.rep_label, False, False, 0)

    def _copy_report(self, *_a):
        from ..report import build
        txt = build(self.store.snapshot)
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(txt, -1)
        self.rep_label.set_text(_("Copied to the clipboard."))

    # ------------------------------------------------------------ desinstalar
    def uninstall(self, *_a):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.NONE,
                                text=_("Uninstall %s?") % APP_NAME)
        dlg.format_secondary_text(_("The program will be removed. Your password will be requested."))
        wipe_box = Gtk.CheckButton(label=_("Also delete my settings and alarms"))
        wipe_box.set_active(True)
        dlg.get_message_area().pack_start(wipe_box, False, False, 0)
        dlg.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Uninstall"), Gtk.ResponseType.OK)
        dlg.show_all()
        resp = dlg.run()
        wipe = wipe_box.get_active()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK or not shutil.which("pkexec"):
            return
        # 1) contraseña y desinstalación completa («purge» también borra los ficheros de
        #    configuración del sistema); 2) solo si ha ido bien, borrar los datos del usuario
        if not _root_owned(UNINSTALL_HELPER):
            # sin el ayudante del paquete no se ofrece una alternativa con la acción genérica
            # de polkit (pediría una contraseña genérica para ejecutar apt-get como root)
            _error(self, _("%s could not be uninstalled. Another program may be "
                           "installing updates: try again in a few minutes.") % APP_NAME)
            return
        self.set_sensitive(False)
        try:
            proc = Gio.Subprocess.new(["pkexec", UNINSTALL_HELPER],
                                      Gio.SubprocessFlags.STDOUT_SILENCE |
                                      Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            self.set_sensitive(True)
            return

        def _done(p, res):
            try:
                p.wait_finish(res)
            except GLib.Error:
                pass
            ok = p.get_if_exited() and p.get_exit_status() == 0
            if not ok:
                self.set_sensitive(True)
                code = p.get_exit_status() if p.get_if_exited() else -1
                if code not in (126, 127):   # 126/127: contraseña cancelada
                    _error(self, _("%s could not be uninstalled. Another program may be "
                                   "installing updates: try again in a few minutes.") % APP_NAME)
                return   # no se borra nada
            try:
                try:
                    # el servicio se reinicia solo (Restart=always): se para con systemd
                    if shutil.which("systemctl"):
                        subprocess.run(["systemctl", "--user", "stop", "sensorpulse.service"],
                                       timeout=10, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
                    if self.store.client:
                        self.store.client.quit_service()
                except Exception:
                    pass
                if wipe:
                    wipe_user_data()
            finally:
                self.app.quit()

        proc.wait_async(None, _done)


def wipe_user_data():
    """Borra todo lo que el programa guarda en la cuenta del usuario. No importa nada:
    se llama después de desinstalar, cuando los ficheros del programa ya no existen."""
    gnome_ext_set = envinfo.gnome_ext_set
    shutil.rmtree(cfgmod.config_dir(), ignore_errors=True)
    shutil.rmtree(historydb.state_dir(), ignore_errors=True)
    rt = os.environ.get("XDG_RUNTIME_DIR")
    if rt:
        shutil.rmtree(os.path.join(rt, "sensorpulse-icons"), ignore_errors=True)
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    shutil.rmtree(os.path.join(cache, "sensorpulse"), ignore_errors=True)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    for p in (os.path.join(base, "autostart", "sensorpulse.desktop"),):
        if os.path.exists(p):
            os.remove(p)
    for spice in ("sensorpulse@mintmotion", "sensorpulse-gauges@mintmotion"):
        shutil.rmtree(os.path.join(base, "cinnamon", "spices", spice), ignore_errors=True)
    # quitar el applet/desklet de Cinnamon y la extensión de GNOME de las listas activas
    try:
        src = Gio.SettingsSchemaSource.get_default()
        if src is not None and src.lookup("org.cinnamon", True) is not None:
            st = Gio.Settings.new("org.cinnamon")
            for key in ("enabled-applets", "enabled-desklets"):
                cur = st.get_strv(key)
                new = [x for x in cur if "@mintmotion" not in x or "sensorpulse" not in x]
                if new != cur:
                    st.set_strv(key, new)
        gnome_ext_set(False)
        Gio.Settings.sync()
    except Exception:
        pass


UNINSTALL_HELPER = "/usr/libexec/sensorpulse/sensorpulse-uninstall"


def _root_owned(path):
    """¿Existe el fichero, es de root y nadie más puede escribirlo?"""
    try:
        st = os.stat(path)
    except OSError:
        return False
    return st.st_uid == 0 and not st.st_mode & 0o022


def _error(parent, text):
    dlg = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.ERROR,
                            buttons=Gtk.ButtonsType.OK, text=text)
    dlg.run()
    dlg.destroy()


SYSTEM_AUTOSTART = "/etc/xdg/autostart/sensorpulse.desktop"


def cfgmod_autostart(enabled):
    """Arranque con el equipo.

    Instalado como paquete existe /etc/xdg/autostart/sensorpulse.desktop: para desactivarlo
    se crea ~/.config/autostart/sensorpulse.desktop con Hidden=true (estándar XDG); para
    activarlo se borra esa anulación. Ejecutado desde el código fuente se crea un .desktop propio.
    """
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    path = os.path.join(base, "autostart", "sensorpulse.desktop")
    if os.path.exists(SYSTEM_AUTOSTART):
        if enabled:
            if os.path.exists(path):
                os.remove(path)
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("[Desktop Entry]\nType=Application\nName=%s\nExec=sensorpulse --background\n"
                        "Hidden=true\n" % APP_NAME)
        return
    if enabled:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("[Desktop Entry]\nType=Application\nName=%s\nExec=sensorpulse --background\n"
                    "Icon=sensorpulse\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n"
                    "X-GNOME-Autostart-Delay=5\n" % APP_NAME)
    elif os.path.exists(path):
        os.remove(path)
