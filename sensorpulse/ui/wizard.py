"""Asistente de primera ejecución (5 pasos): bienvenida, dónde mostrar, estilo,
qué vigilar y alarmas."""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango


from .. import APP_NAME
from .. import config as cfgmod
from ..i18n import _
from . import draw as D
from . import envinfo
from . import detect
from .floating import Renderer, STYLES, style_names
from .theme import PALETTES, hex_to_rgb
from .theme import resolve as theme_resolve


def _title(text):
    l = Gtk.Label(xalign=0)
    l.set_markup("<span size='x-large' weight='bold'>%s</span>" % GLib.markup_escape_text(text))
    return l


def _sub(text):
    l = Gtk.Label(label=text, xalign=0)
    l.set_line_wrap(True)
    l.set_max_width_chars(70)
    l.get_style_context().add_class("dim-label")
    return l


class Preview(Gtk.DrawingArea):
    """Vista previa en vivo de un estilo con los datos reales del equipo."""

    def __init__(self, store, style, n=3, scale=0.62, panel=False, fit=None):
        super().__init__()
        self.store, self.style, self.n, self.scale, self.panel = store, style, n, scale, panel
        self.renderer = Renderer(store)
        if panel:
            self.set_size_request(340, 46)
        elif fit:
            # escalar para caber en un recuadro fijo (galería homogénea)
            w1, h1 = self.renderer.size(style, n, 1.0, header=False)
            self.scale = min(fit[0] / float(w1), fit[1] / float(h1))
            self.set_size_request(*fit)
        else:
            w, h = self.renderer.size(style, n, scale, header=False)
            self.set_size_request(w, h)
        self.connect("destroy", lambda *a: (store.unsubscribe(self.queue_draw)))
        self.connect("draw", self.on_draw)
        store.subscribe_frame(self.queue_draw)
        store.subscribe(self.queue_draw)

    def on_draw(self, w, cr):
        import cairo
        cfg = self.store.cfg
        W, H = w.get_allocated_width(), w.get_allocated_height()
        g = cairo.LinearGradient(0, 0, W, H)
        g.add_color_stop_rgb(0, 0.06, 0.09, 0.18)
        g.add_color_stop_rgb(1, 0.20, 0.07, 0.24)
        D.rounded_rect(cr, 0, 0, W, H, 10)
        cr.set_source(g)
        cr.fill()
        if self.panel:
            self._draw_panel(cr, W, H)
            return False
        items = [(self.store.item(c, d), list(self.store.local_hist.get(c["id"], ())))
                 for c, d in self.store.featured("floating", self.n)]
        dark = dict(PALETTES["dark"])
        self.renderer.draw(cr, W, H, items, self.style, self.scale, dark,
                           hex_to_rgb(cfg.get("accent")), background=False, header=False,
                           pulse=self.store.pulse)
        return False

    def _draw_panel(self, cr, W, H):
        """Panel simulado con lo que se verá de verdad: cuántos valores, nombres o no, texto o
        indicadores, y los iconos del sistema y el reloj a la derecha (nuestros valores van
        a su izquierda, en el espacio libre)."""
        from ..views import panel_split
        cfg = self.store.cfg
        accent = hex_to_rgb(cfg.get("accent"))
        bar, _extra = panel_split(cfg, self.store.snapshot)
        ph = 30
        y0 = H - ph
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.rectangle(0, y0, W, ph)
        cr.fill()
        # iconos del sistema y reloj
        D.text(cr, "12:34", W - 10, y0 + ph / 2, 12, (1, 1, 1), align="right")
        sx = W - 56
        for _i in range(3):
            cr.set_source_rgba(1, 1, 1, 0.55)
            cr.arc(sx, y0 + ph / 2, 4.5, 0, 6.2832)
            cr.fill()
            sx -= 15
        right = sx - 4
        names = cfg.get("panel_labels", True)
        gauge = cfg.get("panel_style") == "gauge"
        parts = []
        for c, d in bar:
            it = self.store.item(c, d)
            v, u = D.fmt(dict(it, anim=it["value"]))
            parts.append((it, v + ("°" if it["kind"] == "temp" else (u if u == "%" else ""))))
        # si no cabe todo, se reduce (en un panel de verdad hay sitio de sobra)
        need = 0.0
        for it, val in parts:
            if names:
                need += D.text(cr, it["label"], 0, -50, 10, (0, 0, 0), max_width=70, alpha=0.0)[0] + 3
            need += (ph * 0.72 + 9) if gauge else \
                D.text(cr, val, 0, -50, 13, (0, 0, 0), Pango.Weight.BOLD, font=D.FONT_NUM, alpha=0.0)[0] + 9
        k = min(1.0, (right - 8) / max(1.0, need))
        cr.save()
        cr.translate(right, y0 + ph / 2)
        cr.scale(k, k)
        cr.translate(-right, -(y0 + ph / 2))
        # se dibuja de derecha a izquierda para que acabe junto a los iconos del sistema
        x = right
        for it, val in reversed(parts):
            col = D.item_color(it, accent)
            if names:
                nw, _h = D.text(cr, it["label"], x, y0 + ph / 2, 10, (0.8, 0.82, 0.86), align="right",
                                max_width=70, alpha=0.0)
                D.text(cr, it["label"], x, y0 + ph / 2, 10, (0.8, 0.82, 0.86), align="right", max_width=70)
                x -= nw + 3
            if gauge:
                r = ph * 0.36
                D.draw_panel_gauge(cr, x - r - 1, y0 + ph / 2 + 1, r,
                                   D.frac(dict(it, anim=it["value"])), col, val.replace("°", ""))
                x -= 2 * r + 9
            else:
                tw, _h = D.text(cr, val, x, y0 + ph / 2, 13, col, Pango.Weight.BOLD, align="right",
                                font=D.FONT_NUM)
                x -= tw + 9
        cr.restore()


class Wizard(Gtk.Window):
    def __init__(self, app, store, on_done):
        super().__init__(title=_("Welcome to %s") % APP_NAME)
        self.app, self.store, self.on_done = app, store, on_done
        self.cfg = store.cfg
        self.set_default_size(820, 600)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name("sensorpulse")
        self.pages = []
        self._scan = []
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.pack_start(self.stack, True, True, 0)
        nav = Gtk.Box(spacing=8)
        nav.set_border_width(14)
        self.dots = Gtk.Label()
        nav.pack_start(self.dots, False, False, 0)
        self.next_btn = Gtk.Button(label=_("Next"))
        self.next_btn.get_style_context().add_class("suggested-action")
        self.next_btn.connect("clicked", self.next)
        self.back_btn = Gtk.Button(label=_("Back"))
        self.back_btn.connect("clicked", self.back)
        nav.pack_end(self.next_btn, False, False, 0)
        nav.pack_end(self.back_btn, False, False, 0)
        outer.pack_end(nav, False, False, 0)
        outer.pack_end(Gtk.Separator(), False, False, 0)
        self.add(outer)
        self.notes = envinfo.notes_for_display()
        if not self.cfg.get("first_run_done") and \
                any(d["cls"] == "battery" for d in store.snapshot.get("devices", [])):
            self.cfg["style"] = "oled"   # en portátiles, el estilo que menos gasta
        for builder in (self.page_welcome, self.page_where, self.page_style, self.page_sensors,
                        self.page_alarms, self.page_ready):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            box.set_border_width(28)
            builder(box)
            sw = Gtk.ScrolledWindow()
            sw.add(box)
            self.stack.add_named(sw, str(len(self.pages)))
            self.pages.append(sw)
        self.idx = 0
        self.connect("delete-event", self._on_close)
        self.update_nav()
        GLib.timeout_add(250, self._scan_step)

    # ------------------------------------------------------------ navegación
    def update_nav(self):
        self.stack.set_visible_child_name(str(self.idx))
        self.back_btn.set_sensitive(self.idx > 0)
        last = self.idx == len(self.pages) - 1
        self.next_btn.set_label(_("Finish") if last else (_("Start") if self.idx == 0 else _("Next")))
        self.dots.set_markup("  ".join("<b>●</b>" if i == self.idx else "○"
                                       for i in range(len(self.pages))))

    def next(self, *_a):
        if self.idx == len(self.pages) - 1:
            self.finish()
            return
        self.idx += 1
        # saltar la página de estilo si solo se eligió el panel
        if self.idx == 2 and not (self.cfg["display"]["floating"] or self.cfg["display"]["window"]):
            self.idx += 1
        self.update_nav()
        if self.idx == len(self.pages) - 1 and getattr(self, "_ready", None):
            self._ready.sweep()

    def back(self, *_a):
        self.idx -= 1
        if self.idx == 2 and not (self.cfg["display"]["floating"] or self.cfg["display"]["window"]):
            self.idx -= 1
        self.idx = max(0, self.idx)
        self.update_nav()

    def _on_close(self, *_a):
        # cerrar sin terminar: se guarda lo recomendado para no volver a molestar
        self.finish()
        return False

    def finish(self):
        self.cfg["first_run_done"] = True
        self.store.save_cfg()
        self.hide()
        self.on_done()

    # ------------------------------------------------------------ páginas
    def page_welcome(self, box):
        box.pack_start(_title(_("Welcome")), False, False, 0)
        box.pack_start(_sub(_("Let's get your sensor monitor ready in one minute. "
                              "Nothing needs to be configured by hand.")), False, False, 0)
        box.pack_start(Gtk.Label(label=_("This is what I found on your computer:"), xalign=0),
                       False, False, 6)
        grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        found = False
        for d in self.store.devices():
            kinds = sorted({c["kind"] for c in d["channels"] if not c.get("advanced")})
            names = {"temp": _("temperature"), "fan": _("fans"), "percent": _("usage"),
                     "power": _("power"), "freq": _("clock")}
            desc = ", ".join(names[k] for k in kinds if k in names)
            if not desc:
                continue
            found = True
            l = Gtk.Label(xalign=0)
            l.set_markup("<b>%s</b>%s  —  %s" % (
                GLib.markup_escape_text(d["name"]),
                ("  <small>(%s)</small>" % GLib.markup_escape_text(d["model"])) if d.get("model") else "",
                GLib.markup_escape_text(desc)))
            row = Gtk.Box(spacing=8)
            mark = CheckMark()
            row.pack_start(mark, False, False, 0)
            row.pack_start(l, False, False, 0)
            rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                               transition_duration=180)
            rev.add(row)
            self._scan.append((rev, mark))
            grid.pack_start(rev, False, False, 0)
        if not found:
            grid.pack_start(_sub(_("This computer does not expose physical sensors "
                                   "(this is normal in virtual machines).")), False, False, 0)
        sata = envinfo.sata_disks_without_temp()
        if sata:
            l = Gtk.Label(xalign=0)
            l.set_markup("○  <b>%s</b>  —  %s" % (
                GLib.markup_escape_text(", ".join(sata)),
                GLib.markup_escape_text(_("disk temperature can be enabled later (optional)"))))
            grid.pack_start(l, False, False, 0)
        if not any(c["kind"] == "fan" for d in self.store.devices() for c in d["channels"]):
            grid.pack_start(_sub(_("No fan speeds found: many laptops do not publish them for Linux. "
                                   "Temperatures will still be monitored.")), False, False, 4)
        box.pack_start(grid, False, False, 0)
        if detect.available() and detect.needs_detection(self.store.snapshot, bool(sata)):
            hb = Gtk.Box(spacing=10)
            b = Gtk.Button(label=_("Detect sensors…"))
            b.connect("clicked", lambda *a: detect.run(self, self.store,
                                                       lambda d: self.app.show_wizard(page=0)))
            hb.pack_start(b, False, False, 0)
            hb.pack_start(_sub(_("Activates the motherboard and disk sensors that are not loaded. "
                                 "Your password will be asked once.")), True, True, 0)
            box.pack_start(hb, False, False, 6)
        box.pack_start(Gtk.Separator(), False, False, 6)
        rec = Gtk.Button(label=_("Use recommended settings and finish"))
        rec.set_halign(Gtk.Align.START)
        rec.connect("clicked", self._use_recommended)
        box.pack_start(rec, False, False, 0)
        box.pack_start(_sub(_("Shows the temperatures in the panel and warns you only when it matters. "
                              "Or press “Start” to choose everything yourself.")), False, False, 0)

    def _scan_step(self):
        """«He encontrado esto»: los dispositivos aparecen uno a uno (60 ms)."""
        anim = self.store.animate
        for i, (rev, mark) in enumerate(self._scan):
            if anim:
                GLib.timeout_add(60 * i, lambda r=rev, m=mark: (r.set_reveal_child(True),
                                                                m.start(), False)[2])
            else:
                rev.set_reveal_child(True)
                mark.progress = 1.0
        return False

    def _use_recommended(self, *_a):
        d = cfgmod.DEFAULTS
        import copy
        self.cfg["display"] = copy.deepcopy(d["display"])
        self.cfg["alarms"] = copy.deepcopy(d["alarms"])
        self.cfg["sensors"]["visible"] = {}
        self.cfg["autostart"] = True
        self.finish()

    def page_where(self, box):
        box.pack_start(_title(_("Where do you want to see your sensors?")), False, False, 0)
        box.pack_start(_sub(_("You can choose more than one. You can change this at any time.")),
                       False, False, 0)
        disp = self.cfg["display"]
        opts = (
            ("panel", _("In the panel"), _("Next to the clock and the other icons"), True),
            ("window", _("In a window"), _("Cards with all your sensors, always at hand"), False),
            ("floating", _("Floating on the desktop"),
             _("Transparent; place it anywhere with the mouse"), False),
        )
        for key, title, sub, _p in opts:
            frame = Gtk.Box(spacing=16)
            frame.set_border_width(6)
            chk = Gtk.CheckButton()
            chk.set_active(bool(disp.get(key)))
            chk.connect("toggled", lambda w, k=key: disp.__setitem__(k, w.get_active()))
            frame.pack_start(chk, False, False, 0)
            txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            t = Gtk.Label(xalign=0)
            t.set_markup("<b>%s</b>" % GLib.markup_escape_text(title))
            txt.pack_start(t, False, False, 0)
            txt.pack_start(_sub(sub), False, False, 0)
            if self.notes.get(key):
                n = _sub("ℹ " + self.notes[key])
                txt.pack_start(n, False, False, 0)
            frame.pack_start(txt, True, True, 0)
            if key == "panel":
                pv = Preview(self.store, "minimal", panel=True)
            elif key == "window":
                pv = Preview(self.store, "glass", n=2, scale=0.5)
            else:
                pv = Preview(self.store, "hud", n=2, scale=0.45)
            frame.pack_end(pv, False, False, 0)
            ev = Gtk.EventBox()
            ev.add(frame)
            ev.connect("button-press-event", lambda w, e, c=chk: c.set_active(not c.get_active()))
            box.pack_start(ev, False, False, 4)
            if key == "panel":
                self._panel_pv = pv
                box.pack_start(self._panel_options(), False, False, 0)
            box.pack_start(Gtk.Separator(), False, False, 0)
        box.pack_start(_sub(_("The panel shows a few values next to the clock, and clicking it "
                              "shows the rest. The desktop widget shows larger gauges. On the "
                              "next pages you choose which sensors go to each place and you "
                              "can give them names of your own.")), False, False, 4)

    def _panel_options(self):
        """Opciones del panel en el propio asistente (antes solo estaban en Ajustes)."""
        cfg = self.cfg
        row = Gtk.Box(spacing=8)
        row.set_margin_start(44)
        row.pack_start(Gtk.Label(label=_("Values in the panel")), False, False, 0)
        pc = Gtk.SpinButton.new_with_range(1, 8, 1)
        pc.set_value(int(cfg.get("panel_count", 3)))
        pc.connect("value-changed", lambda w: (cfg.__setitem__("panel_count", int(w.get_value())),
                                               self._panel_pv.queue_draw()))
        row.pack_start(pc, False, False, 0)
        row.pack_start(Gtk.Label(label="   " + _("Look")), False, False, 0)
        ps = Gtk.ComboBoxText()
        ps.append("text", _("Name and number"))
        ps.append("gauge", _("Gauges (like the desktop widget)"))
        ps.set_active_id(cfg.get("panel_style", "text"))
        ps.connect("changed", lambda w: (cfg.__setitem__("panel_style", w.get_active_id()),
                                         self._panel_pv.queue_draw()))
        row.pack_start(ps, False, False, 0)
        nm = Gtk.CheckButton(label=_("Names"))
        nm.set_active(bool(cfg.get("panel_labels", True)))
        nm.connect("toggled", lambda w: (cfg.__setitem__("panel_labels", w.get_active()),
                                         self._panel_pv.queue_draw()))
        row.pack_start(nm, False, False, 0)
        return row

    def page_style(self, box):
        box.pack_start(_title(_("Choose a style")), False, False, 0)
        box.pack_start(_sub(_("Live preview with your real data. Values move smoothly and change "
                              "color as temperatures rise.")), False, False, 0)
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3,
                           min_children_per_line=3, homogeneous=True,
                           column_spacing=14, row_spacing=14)
        group = None
        names = style_names()
        for st in STYLES:
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            pv = Preview(self.store, st, n=3, fit=(236, 130))
            rb = Gtk.RadioButton.new_with_label_from_widget(group, names[st])
            group = rb
            rb.set_active(self.cfg.get("style") == st)
            rb.connect("toggled", lambda w, s=st: w.get_active() and self.cfg.__setitem__("style", s))
            ev = Gtk.EventBox()
            ev.add(pv)
            ev.connect("button-press-event", lambda w, e, r=rb: r.set_active(True))
            vb.pack_start(ev, False, False, 0)
            vb.pack_start(rb, False, False, 0)
            flow.add(vb)
        box.pack_start(flow, False, False, 0)
        row = Gtk.Box(spacing=10)
        row.pack_start(Gtk.Label(label=_("Accent color")), False, False, 0)
        cb = Gtk.ColorButton()
        rgba = Gdk.RGBA()
        rgba.parse(self.cfg.get("accent", "#22d3ee"))
        cb.set_rgba(rgba)
        cb.connect("color-set", self._accent)
        row.pack_start(cb, False, False, 0)
        row.pack_start(Gtk.Label(label="   " + _("Size")), False, False, 0)
        size = Gtk.ComboBoxText()
        for k, v in (("small", _("Small")), ("medium", _("Medium")), ("large", _("Large"))):
            size.append(k, v)
        size.set_active_id(self.cfg.get("size", "medium"))
        size.connect("changed", lambda w: self.cfg.__setitem__("size", w.get_active_id()))
        row.pack_start(size, False, False, 0)
        box.pack_start(row, False, False, 8)

    def _accent(self, btn):
        c = btn.get_rgba()
        self.cfg["accent"] = "#%02x%02x%02x" % (int(c.red * 255), int(c.green * 255), int(c.blue * 255))
        self.store.listeners and [cb() for cb in list(self.store.listeners)]

    def page_sensors(self, box):
        box.pack_start(_title(_("What do you want to monitor, and where?")), False, False, 0)
        box.pack_start(_sub(_("The recommended sensors are already selected. Panel: next to the "
                              "clock. On click: only in the list that opens when you click the "
                              "panel. Widget: the floating desktop widget. Type a name of your "
                              "own if you like (for example «Motherboard» instead of «diode 0»).")),
                       False, False, 0)
        from ..views import PANEL_LIMIT
        s = self.cfg["sensors"]
        vis = s["visible"]
        panel_sel = s.get("panel") or [c["id"] for c, _d in self.store.featured("panel", PANEL_LIMIT)]
        float_sel = s.get("floating") or [c["id"] for c, _d in self.store.featured("floating", 9)]
        menu_sel = set(s.get("panel_menu") or [])
        self._rows = []
        for d in self.store.devices():
            chans = [c for c in d["channels"] if c["kind"] != "status"]
            if not chans:
                continue
            exp = Gtk.Expander()
            exp.set_label_widget(Gtk.Label(label=d["name"] + ("  ·  " + d["model"] if d.get("model") else "")))
            exp.set_expanded(d["cls"] in ("cpu", "gpu", "disk"))
            grid = Gtk.Grid(column_spacing=10, row_spacing=3)
            grid.set_margin_start(24)
            for col, head in enumerate((_("Show"), _("Name"), _("Panel"), _("Widget"))):
                h = Gtk.Label(xalign=0)
                h.set_markup("<small>%s</small>" % GLib.markup_escape_text(head))
                h.get_style_context().add_class("dim-label")
                grid.attach(h, col, 0, 1, 1)
            for i, c in enumerate(chans, start=1):
                chk = Gtk.CheckButton()
                chk.set_active(cfgmod.is_visible(self.cfg, c, d["cls"]))
                chk.connect("toggled", lambda w, cid=c["id"]: vis.__setitem__(cid, w.get_active()))
                grid.attach(chk, 0, i, 1, 1)
                ent = Gtk.Entry()
                ent.set_width_chars(22)
                ent.set_max_length(40)
                ent.set_text(cfgmod.display_name(self.cfg, c))
                ent.set_placeholder_text(c["label"])
                ent.set_tooltip_text(_("Original name: %s") % c["label"])
                ent.connect("changed", self._renamed, c)
                grid.attach(ent, 1, i, 1, 1)
                pan = Gtk.ComboBoxText()
                pan.append("", "—")
                pan.append("bar", _("Panel"))
                pan.append("menu", _("On click"))
                pan.set_active_id("bar" if c["id"] in panel_sel else
                                  "menu" if c["id"] in menu_sel else "")
                pan.connect("changed", lambda *_a: self._places_changed())
                grid.attach(pan, 2, i, 1, 1)
                wid = Gtk.CheckButton()
                wid.set_active(c["id"] in float_sel)
                wid.set_halign(Gtk.Align.CENTER)
                wid.connect("toggled", lambda *_a: self._places_changed())
                grid.attach(wid, 3, i, 1, 1)
                self._rows.append((c["id"], pan, wid))
            exp.add(grid)
            box.pack_start(exp, False, False, 0)
        rec = Gtk.Button(label=_("Back to recommended"))
        rec.set_halign(Gtk.Align.START)
        rec.connect("clicked", self._recommended)
        box.pack_start(rec, False, False, 8)

    def _renamed(self, ent, c):
        text = " ".join(ent.get_text().split())
        ren = self.cfg["sensors"]["renames"]
        if not text or text == c["label"]:
            ren.pop(c["id"], None)
        else:
            ren[c["id"]] = text

    def _places_changed(self):
        """Panel (barra / al hacer clic) y widget, en el orden en que aparecen los sensores."""
        s = self.cfg["sensors"]
        s["panel"] = [cid for cid, pan, _w in self._rows if pan.get_active_id() == "bar"]
        s["panel_menu"] = [cid for cid, pan, _w in self._rows if pan.get_active_id() == "menu"]
        s["floating"] = [cid for cid, _p, wid in self._rows if wid.get_active()]
        if getattr(self, "_panel_pv", None) is not None:
            self._panel_pv.queue_draw()

    def _recommended(self, *_a):
        s = self.cfg["sensors"]
        s["visible"], s["panel"], s["panel_menu"], s["floating"], s["renames"] = {}, [], [], [], {}
        # sin releer el disco: la 0.3.5 lo releía y se perdía lo elegido en las páginas
        # anteriores (dónde verlo, estilo, tamaño...), que aún no estaba guardado
        self.app.show_wizard(page=3, reload=False)

    def page_alarms(self, box):
        a = self.cfg["alarms"]
        box.pack_start(_title(_("Alarms")), False, False, 0)
        box.pack_start(_sub(_("Recommended limits are read from your hardware. You can add your own "
                              "alarms (for example, at 75 °C) later in Settings.")), False, False, 0)

        def chk(label, key, parent=a):
            c = Gtk.CheckButton(label=label)
            c.set_active(bool(parent.get(key)))
            c.connect("toggled", lambda w: parent.__setitem__(key, w.get_active()))
            box.pack_start(c, False, False, 2)
            return c

        gauge = NormalZone(self.store)
        if gauge.ok:
            box.pack_start(gauge, False, False, 4)
        sentence = self._limit_sentence()
        if sentence:
            l = Gtk.Label(xalign=0)
            l.set_line_wrap(True)
            l.set_max_width_chars(70)
            l.set_markup("ℹ  " + GLib.markup_escape_text(sentence))
            box.pack_start(l, False, False, 4)
        chk(_("Warn me if something gets too hot"), "enabled")
        chk(_("Visual alarm (color and blinking)"), "blink")
        chk(_("Desktop notification"), "notify")
        # sonidos: las mismas opciones que luego hay en Ajustes, con ▶ para escucharlos
        from .sounds import picker
        row = Gtk.Box(spacing=10)
        s = Gtk.CheckButton(label=_("Sound alarm"))
        s.set_active(bool(a.get("sound")))
        s.connect("toggled", lambda w: a.__setitem__("sound", w.get_active()))
        row.pack_start(s, False, False, 0)
        pk, _c = picker(self.store, a.get("sound_name"), lambda k: a.__setitem__("sound_name", k))
        row.pack_start(pk, False, False, 0)
        box.pack_start(row, False, False, 2)
        rp = chk(_("Repeat the sound every 2 minutes while the alarm lasts"), "repeat")
        rp.set_margin_start(24)
        row = Gtk.Box(spacing=10)
        ws = Gtk.CheckButton(label=_("Soft sound for warnings"))
        ws.set_active(bool(a.get("warn_sound")))
        ws.connect("toggled", lambda w: a.__setitem__("warn_sound", w.get_active()))
        row.pack_start(ws, False, False, 0)
        wk, _c = picker(self.store, a.get("warn_sound_name"),
                        lambda k: a.__setitem__("warn_sound_name", k), default="sensorpulse-warning")
        row.pack_start(wk, False, False, 0)
        box.pack_start(row, False, False, 2)
        from . import disks as disks_ui
        if disks_ui.has_hdd(self.store):
            box.pack_start(Gtk.Separator(), False, False, 8)
            t = Gtk.Label(xalign=0)
            t.set_markup("<b>%s</b>" % GLib.markup_escape_text(_("Disk temperature")))
            box.pack_start(t, False, False, 0)
            box.pack_start(disks_ui.build(self.store, self.cfg, lambda: None, compact=True),
                           False, False, 0)
        box.pack_start(Gtk.Separator(), False, False, 8)
        chk(_("Start with the computer"), "autostart", self.cfg)

    def _limit_sentence(self):
        """Una frase con el límite calculado para su procesador."""
        for d in self.store.devices():
            if d["cls"] != "cpu":
                continue
            prim = next((c for c in d["channels"] if c["primary"] and c["kind"] == "temp"), None)
            if prim is None:
                return ""
            it = self.store.item(prim, d)
            model = d.get("model") or d["name"]
            unit = it.get("unit", "°C")
            if it.get("tjmax") is not None:
                return _("Your %(model)s is designed to work up to %(tj).0f%(unit)s under load. "
                         "You will be warned if it goes beyond that, if it slows down because of heat "
                         "or if it gets hot while doing almost nothing.") % {
                    "model": model, "tj": it["tjmax"], "unit": unit}
            if it.get("crit") is not None:
                return _("Your %(model)s will raise an alarm at %(crit).0f%(unit)s.") % {
                    "model": model, "crit": it["crit"], "unit": unit}
        return ""


    def page_ready(self, box):
        box.pack_start(_title(_("All set")), False, False, 0)
        box.pack_start(_sub(_("Your sensors are being watched. You will only be warned when it "
                              "matters.")), False, False, 0)
        self._ready = SweepGauges(self.store)
        box.pack_start(self._ready, True, True, 12)


class CheckMark(Gtk.DrawingArea):
    """Marca ✓ que se dibuja como un trazo al aparecer."""

    def __init__(self):
        super().__init__()
        self.set_size_request(18, 18)
        self.progress = 0.0
        self.connect("draw", self._draw)

    def start(self):
        import time
        t0 = time.monotonic()

        def _tick():
            self.progress = min(1.0, (time.monotonic() - t0) / 0.25)
            self.queue_draw()
            return self.progress < 1.0
        GLib.timeout_add(16, _tick)

    def _draw(self, w, cr):
        pts = [(3, 9.5), (7.5, 14), (15, 4.5)]
        segs = [(pts[0], pts[1]), (pts[1], pts[2])]
        lens = [((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in segs]
        total = sum(lens) * self.progress
        cr.set_source_rgb(0.13, 0.77, 0.37)
        cr.set_line_width(2.4)
        cr.set_line_cap(1)
        cr.set_line_join(1)
        cr.move_to(*pts[0])
        for (a, b), ln in zip(segs, lens):
            if total <= 0:
                break
            k = min(1.0, total / ln)
            cr.line_to(a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)
            total -= ln
        cr.stroke()
        return False


class NormalZone(Gtk.DrawingArea):
    """La frase del límite, hecha visual: zona «normal con carga» en ámbar suave."""

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.it = None
        for d in store.devices():
            if d["cls"] == "cpu":
                prim = next((c for c in d["channels"] if c["primary"] and c["kind"] == "temp"), None)
                if prim and prim.get("tjmax"):
                    self.it = store.item(prim, d)
        self.ok = self.it is not None
        self.set_size_request(-1, 74)
        self.connect("draw", self._draw)

    def _draw(self, w, cr):
        it = self.it
        W = w.get_allocated_width()
        x0, x1, y = 20, W - 20, 30
        lo, hi = it["vmin"], it["vmax"]
        tj, warn = it["tjmax"], it["warn"]

        def X(v):
            return x0 + (x1 - x0) * max(0.0, min(1.0, (v - lo) / float(hi - lo)))
        for a, b, col, al in ((lo, warn, (0.13, 0.77, 0.37), 0.55), (warn, tj, (0.98, 0.62, 0.15), 0.75),
                              (tj, hi, (0.94, 0.27, 0.27), 0.75)):
            cr.set_source_rgba(col[0], col[1], col[2], al)
            D.rounded_rect(cr, X(a), y, max(1, X(b) - X(a) - 2), 10, 4)
            cr.fill()
        pal = PALETTES[theme_resolve(self.store.cfg)]
        unit = it.get("unit", "°C")
        D.text(cr, _("This is normal under load"), (X(warn) + X(tj)) / 2, y - 12, 11,
               (0.85, 0.55, 0.10), Pango.Weight.SEMIBOLD)
        for v in (lo, warn, tj, hi):
            D.text(cr, "%d%s" % (round(v), unit), X(v), y + 24, 10, pal["dim"])
        if it.get("value") is not None:
            cx = X(it["value"])
            cr.set_source_rgb(*pal["fg"])
            cr.move_to(cx, y - 3)
            cr.line_to(cx - 5, y - 9)
            cr.line_to(cx + 5, y - 9)
            cr.close_path()
            cr.fill()
        return False


class SweepGauges(Gtk.DrawingArea):
    """Pantalla final: las agujas barren de 0 al máximo y vuelven (1,2 s), como un coche."""

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.set_size_request(-1, 220)
        self.t0 = None
        self.connect("draw", self._draw)

    def sweep(self):
        import time
        if not self.store.animate:
            self.t0 = None
            self.queue_draw()
            return
        self.t0 = time.monotonic()

        def _tick():
            self.queue_draw()
            return self.t0 is not None and time.monotonic() - self.t0 < 1.25
        GLib.timeout_add(16, _tick)

    def _draw(self, w, cr):
        import time
        W, H = w.get_allocated_width(), w.get_allocated_height()
        pal = dict(PALETTES["dark"])
        items = [self.store.item(c, d) for c, d in self.store.featured("floating", 3)]
        if not items:
            return False
        p = None
        if self.t0 is not None:
            p = (time.monotonic() - self.t0) / 1.2
            if p >= 1:
                p = None

        def ease(t):
            return 1 - (1 - t) ** 3
        r = min(H * 0.38, W / (2.6 * len(items)))
        for i, it in enumerate(items):
            cx = W * (i + 0.5) / len(items)
            it = dict(it)
            if p is not None and it.get("vmax") is not None:
                lo, hi = it["vmin"], it["vmax"]
                v = it.get("value") if it.get("value") is not None else lo
                val = lo + (hi - lo) * ease(p / 0.5) if p < 0.5 else hi + (v - hi) * ease((p - 0.5) / 0.5)
                it["anim"] = it["value"] = val
            D.draw_cockpit(cr, cx, H * 0.52, r, it, pal, hex_to_rgb(self.store.cfg.get("accent")),
                           0.0, key="ready:%d" % i)
        return False
