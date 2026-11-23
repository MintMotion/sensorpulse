"""Widget flotante sobre el escritorio: fondo transparente, movible con el ratón,
bloqueable y con 5 estilos (HUD, anillos, barras, minimal, cristal).

- X11 (Cinnamon, Xfce, MATE, GNOME X11, KDE X11): ventana GTK RGBA sin bordes de tipo
  «dock» por debajo del resto: así no desaparece con «Mostrar escritorio» (probado en
  Muffin, xfwm4 y Marco) y queda por encima del fondo de escritorio de Nemo/Caja.
- Wayland con layer-shell (KDE, wlroots): GtkLayerShell en la capa inferior.
- GNOME Wayland: ventana normal (GNOME no ofrece layer-shell); se avisa en el asistente.
"""

import os

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib

from ..i18n import _
from ..views import muted_text
from . import draw as D
from .theme import PALETTES, hex_to_rgb, state_color

try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell
except (ValueError, ImportError):
    GtkLayerShell = None

SIZES = {"small": 0.75, "medium": 1.0, "large": 1.35}
STYLES = ["cockpit", "hud", "oled", "rings", "bars", "minimal", "glass"]


def style_names():
    return {"cockpit": _("Cockpit"), "hud": _("Futuristic HUD"), "oled": _("OLED minimal"),
            "rings": _("Rings"), "bars": _("Bars"), "minimal": _("Minimal"), "glass": _("Glass")}


def is_wayland():
    return bool(os.environ.get("WAYLAND_DISPLAY")) or \
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def layer_shell_ok():
    if GtkLayerShell is None or not is_wayland():
        return False
    try:
        return bool(GtkLayerShell.is_supported())
    except Exception:
        return False


class Renderer:
    """Calcula el tamaño y dibuja un conjunto de elementos con el estilo pedido.
    Lo usan el flotante, las vistas previas del asistente y la ventana."""

    def __init__(self, store):
        self.store = store

    def metrics(self, style, n, scale):
        n = max(1, n)
        if style == "cockpit":
            r = 62 * scale
            cols = min(n, 3)
            rows = (n + cols - 1) // cols
            return cols, rows, r * 2.5, r * 2.45, r
        if style == "oled":
            r = 42 * scale
            cols = min(n, 4)
            rows = (n + cols - 1) // cols
            return cols, rows, r * 2.7, r * 2.9, r
        if style == "hud":
            r = 64 * scale
            cols = min(n, 3)
            rows = (n + cols - 1) // cols
            return cols, rows, r * 2.75, r * 2.55, r
        if style == "rings":
            r = 38 * scale
            cols = min(n, 4)
            rows = (n + cols - 1) // cols
            return cols, rows, r * 2.9, r * 3.0, r
        if style == "bars":
            return 1, n, 300 * scale, 46 * scale, 0
        if style == "minimal":
            cols = min(n, 3)
            rows = (n + cols - 1) // cols
            return cols, rows, 150 * scale, 76 * scale, 0
        # glass
        cols = min(n, 2)
        rows = (n + cols - 1) // cols
        return cols, rows, 210 * scale, 116 * scale, 0

    def size(self, style, n, scale, header=True):
        cols, rows, cw, ch, _r = self.metrics(style, n, scale)
        pad = 14 * scale
        head = (26 * scale) if header else 0
        return int(cols * cw + pad * 2), int(rows * ch + pad * 2 + head)

    def cell(self, i, n, style, scale, header=True, w=None, h=None):
        """(x, y, ancho, alto) de la celda i, en coordenadas del widget."""
        cols, rows, cw, ch, _r = self.metrics(style, n, scale)
        pad = 14 * scale
        need_w, need_h = self.size(style, n, scale, header)
        ox = max(0, ((w or need_w) - need_w) / 2.0)
        oy = max(0, ((h or need_h) - need_h) / 2.0)
        top = pad + (26 * scale if header else 0)
        return ox + pad + (i % cols) * cw, oy + top + (i // cols) * ch, cw, ch

    def hit(self, x, y, n, style, scale, w=None, h=None, header=True):
        """Índice del elemento bajo el puntero (o None)."""
        for i in range(n):
            cx, cy, cw, ch = self.cell(i, n, style, scale, header, w, h)
            if cx <= x < cx + cw and cy <= y < cy + ch:
                return i
        return None

    def fan_spots(self, w, h, items, style, scale, header=True):
        """Dónde va la hélice de cada ventilador: [(id, cx, cy, radio)]."""
        if style not in ("minimal", "glass"):
            return []
        out = []
        n = len(items)
        for i, (it, _pts) in enumerate(items):
            if it.get("kind") == "fan" and it.get("value"):
                x, y, cw, ch = self.cell(i, n, style, scale, header, w, h)
                out.append((it["id"], x + cw - 26 * scale, y + ch * 0.62, 11 * scale))
        return out

    def draw_fans(self, cr, w, h, items, style, scale, pal, header=True):
        for fid, cx, cy, r in self.fan_spots(w, h, items, style, scale, header):
            D.draw_fan(cr, cx, cy, r, self.store.fan_angle.get(fid, 0.0), pal["dim"], 0.8)

    def draw(self, cr, w, h, items, style, scale, pal, accent, background=False, header=True,
             pulse=0.0, fans=True):
        n = len(items)
        cols, rows, cw, ch, r = self.metrics(style, n, scale)
        pad = 14 * scale
        need_w, need_h = self.size(style, n, scale, header)
        ox, oy = max(0, (w - need_w) / 2.0), max(0, (h - need_h) / 2.0)
        cr.save()
        cr.translate(ox, oy)
        if background:
            D.rounded_rect(cr, 0.5, 0.5, w - 1, h - 1, 18 * scale)
            bg = pal["bg"]
            if style == "oled":
                bg = (0, 0, 0)
            cr.set_source_rgba(bg[0], bg[1], bg[2], 0.92 if style == "oled" else 0.55)
            cr.fill_preserve()
            cr.set_source_rgba(1, 1, 1, 0.08)
            cr.set_line_width(1)
            cr.stroke()
        top = pad
        if header:
            st = self.store.state()
            col = state_color(st, self.store.cfg.get("colorblind", False))
            txt = {"normal": _("All good"), "hot": _("Something is hot"),
                   "critical": _("Attention!"), "sleep": _("Resting")}.get(st, _("Waiting for data…"))
            if not self.store.stale and muted_text(self.store.snapshot):
                txt += "  ·  " + muted_text(self.store.snapshot)
            cr.set_source_rgba(col[0], col[1], col[2], 1)
            cr.arc(pad + 5 * scale, top + 9 * scale, 4.5 * scale, 0, 6.2832)
            cr.fill()
            D.text(cr, txt, pad + 16 * scale, top + 9 * scale, 12 * scale, pal["dim"],
                   align="left")
            top += 26 * scale
        for i, (it, pts) in enumerate(items):
            cx_i, cy_i = i % cols, i // cols
            x = pad + cx_i * cw
            y = top + cy_i * ch
            if style == "cockpit":
                D.draw_cockpit(cr, x + cw / 2, y + ch * 0.47, r, it, pal, accent, pulse,
                               key="%s:%s" % (id(self), it.get("id")))
            elif style == "oled":
                D.draw_oled(cr, x + cw / 2, y + r * 1.15, r, it, pal, accent, pulse)
            elif style == "hud":
                D.draw_hud(cr, x + cw / 2, y + ch * 0.46, r, it, pal, accent, pulse)
            elif style == "rings":
                D.draw_ring(cr, x + cw / 2, y + r * 1.15, r, it, pal, accent, pulse)
            elif style == "bars":
                D.draw_bar(cr, x, y + 4 * scale, cw, ch - 10 * scale, it, pal, accent, pulse)
            elif style == "minimal":
                D.draw_minimal(cr, x + 4 * scale, y, cw - 10 * scale, ch, it, pal, accent, pulse)
            else:
                D.draw_glass(cr, x + 4 * scale, y + 4 * scale, cw - 8 * scale, ch - 8 * scale,
                             it, pal, accent, pts, pulse)
        cr.restore()
        if fans:
            self.draw_fans(cr, w, h, items, style, scale, pal, header)


class FloatingWidget(Gtk.Window):
    def __init__(self, app, store):
        super().__init__(application=app, title=_("Sensors"))
        self.store = store
        self.renderer = Renderer(store)
        self.set_decorated(False)
        self.set_app_paintable(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_resizable(False)
        self.set_accept_focus(False)
        self.stick()
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        self.transparent = visual is not None and screen.is_composited()
        if visual is not None:
            self.set_visual(visual)
        self.layer = False
        if layer_shell_ok():
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.BOTTOM)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
            try:   # KDE: con este espacio de nombres no se oculta con Meta+D
                GtkLayerShell.set_namespace(self, "desktop")
            except Exception:
                pass
            self.layer = True
        else:
            wt = self.store.cfg["floating"].get("window_type", "auto")
            self.wtype = {"utility": Gdk.WindowTypeHint.UTILITY,
                          "desktop": Gdk.WindowTypeHint.DESKTOP}.get(wt, Gdk.WindowTypeHint.DOCK)
            self.set_type_hint(self.wtype)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK |
                        Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_press)
        self.connect("button-release-event", self.on_release)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("configure-event", self.on_configure)
        self.connect("enter-notify-event", lambda *a: self._hover(True))
        self.connect("leave-notify-event", lambda *a: self._hover(False))
        self._drag = None
        self._hovered = False
        self._save_src = None
        self._ver = 0                # sube con cada dato nuevo (clave de la copia en caché)
        self._cache = None           # fotograma completo ya dibujado (sin las hélices)
        self._cache_key = None
        store.subscribe(self.refresh)
        store.subscribe_frame(self.on_frame)
        self.connect("destroy", lambda *a: (store.unsubscribe(self.refresh),
                                            store.unsubscribe(self.on_frame)))
        self.apply_settings()

    # ------------------------------------------------------------ ajustes
    def fcfg(self):
        return self.store.cfg["floating"]

    def apply_settings(self):
        f = self.fcfg()
        if not self.layer:
            self.set_keep_above(bool(f.get("on_top")))
            self.set_keep_below(not f.get("on_top"))
        else:
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP if f.get("on_top")
                                    else GtkLayerShell.Layer.BOTTOM)
        self.set_opacity(float(f.get("opacity", 1.0)))
        self.resize_to_content()
        self.place()
        self.update_input_shape()

    def place(self):
        f = self.fcfg()
        x, y = f.get("x"), f.get("y")
        if x is None or y is None:
            geo = self.get_display().get_monitor(0).get_workarea() if self.get_display() else None
            w, _h = self.get_size()
            x = (geo.x + geo.width - w - 40) if geo else 100
            y = (geo.y + 60) if geo else 100
        # no dejarlo fuera de la pantalla (p. ej. tras quitar un monitor)
        disp = self.get_display()
        if disp is not None and disp.get_n_monitors() > 0:
            w, h = self.get_size()
            xs, ys, xe, ye = 10 ** 9, 10 ** 9, -10 ** 9, -10 ** 9
            for i in range(disp.get_n_monitors()):
                g = disp.get_monitor(i).get_geometry()
                xs, ys = min(xs, g.x), min(ys, g.y)
                xe, ye = max(xe, g.x + g.width), max(ye, g.y + g.height)
            x = max(xs, min(int(x), xe - min(w, xe - xs)))
            y = max(ys, min(int(y), ye - min(h, ye - ys)))
        if self.layer:
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, int(x))
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, int(y))
        else:
            self.move(int(x), int(y))

    def items(self):
        """En caché: se recalcula al llegar datos, no en cada fotograma (R3)."""
        if getattr(self, "_items", None) is None:
            self._items = self._build_items()
        return self._items

    def _build_items(self):
        out = []
        for ch, dev in self.store.featured("floating", limit=9):
            it = self.store.item(ch, dev)
            pts = list(self.store.local_hist.get(ch["id"], ()))
            out.append((it, pts))
        return out

    def scale(self):
        return SIZES.get(self.store.cfg.get("size", "medium"), 1.0)

    def resize_to_content(self):
        n = max(1, len(self.items()))
        w, h = self.renderer.size(self.store.cfg.get("style", "cockpit"), n, self.scale())
        self.set_size_request(w, h)
        self.resize(w, h)

    def refresh(self):
        self._items = self._build_items()
        self._ver += 1
        style = self.store.cfg.get("style", "cockpit")
        if self.get_visible() and style in ("minimal", "glass"):
            self.store.fan_draw_ids = {it["id"] for it, _p in self._items if it.get("kind") == "fan"}
        else:
            self.store.fan_draw_ids = set()
        acc = self.get_accessible()
        if acc is not None:
            parts = []
            for it, _p in self.items():
                v, u = D.fmt(dict(it, anim=it["value"]))
                tag = D.status_tag(it)
                parts.append("%s %s %s%s" % (it["label"], v, u, (", " + tag[1].lower()) if tag else ""))
            acc.set_name("; ".join(parts))
        n = len(self.items())
        w, h = self.renderer.size(self.store.cfg.get("style", "cockpit"), max(1, n), self.scale())
        cw, ch = self.get_size()
        if (cw, ch) != (w, h):
            self.resize_to_content()
        self.queue_draw()

    def update_input_shape(self):
        f = self.fcfg()
        win = self.get_window()
        if win is None:
            return
        if f.get("locked") and f.get("click_through"):
            import cairo
            self.input_shape_combine_region(cairo.Region())
        else:
            self.input_shape_combine_region(None)

    # ------------------------------------------------------------ dibujo
    # Consumo (0.3.7): en la 0.3.6 cada fotograma de animación (8 por segundo solo por las
    # hélices de los ventiladores) volvía a dibujar el widget entero, con sus minigráficas:
    # ~8,6 % de un núcleo con el estilo «Cristal». Ahora el fotograma completo se guarda en
    # una superficie y solo se rehace cuando cambia algo que se ve (dato nuevo, ajustes,
    # ratón encima, latido de una alarma, cifra rodando). Las hélices se dibujan encima y,
    # si es lo único que se mueve, solo se invalida su recuadro.
    def _pal(self):
        # sobre el fondo de escritorio, no sobre ventanas
        return PALETTES["light" if self.store.cfg.get("theme") == "light" else "dark"]

    def _content_key(self):
        cfg = self.store.cfg
        if D.odometer_busy():
            return None
        alarm = any(it.get("alarm") for it, _p in self.items())
        return (self._ver, self.get_size(), cfg.get("style"), cfg.get("size"), cfg.get("theme"),
                cfg.get("accent"), cfg.get("colorblind"), self._hovered,
                bool(cfg["floating"].get("panel_bg", False)), bool(self.fcfg().get("locked")),
                self.store.stale, round(self.store.pulse, 2) if alarm else 0.0,
                muted_text(self.store.snapshot))

    def _paint_content(self, cr, w, h):
        cfg = self.store.cfg
        pal = self._pal()
        if not self.transparent:
            D.rounded_rect(cr, 0, 0, w, h, 0)
            cr.set_source_rgb(*pal["bg"])
            cr.fill()
        self.renderer.draw(cr, w, h, self.items(), cfg.get("style", "cockpit"), self.scale(), pal,
                           hex_to_rgb(cfg.get("accent")), background=self._hovered or
                           cfg["floating"].get("panel_bg", False), header=True,
                           pulse=self.store.pulse, fans=False)
        if self._hovered:
            locked = self.fcfg().get("locked")
            D.text(cr, ("🔒 " if locked else "✥ ") + (_("Locked") if locked else
                   _("Drag to move · right-click for options")), w / 2, h - 10, 10, pal["dim"])

    def on_draw(self, _w, cr):
        import cairo
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        w, h = self.get_size()
        key = self._content_key()
        if key is None or key != self._cache_key or self._cache is None:
            try:
                surf = cr.get_target().create_similar(cairo.CONTENT_COLOR_ALPHA, w, h)
            except Exception:
                surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
            self._paint_content(cairo.Context(surf), w, h)
            self._cache, self._cache_key = surf, key
        cr.set_source_surface(self._cache, 0, 0)
        cr.paint()
        cfg = self.store.cfg
        self.renderer.draw_fans(cr, w, h, self.items(), cfg.get("style", "cockpit"), self.scale(),
                                self._pal())
        return False

    def on_frame(self):
        """Un fotograma de animación: si el contenido no ha cambiado, solo se repintan las
        hélices (o nada)."""
        if not self.get_visible():
            return
        key = self._content_key()
        if key is None or key != self._cache_key:
            self.queue_draw()
            return
        if not self.store.fan_draw_ids:
            return
        w, h = self.get_size()
        cfg = self.store.cfg
        for _fid, cx, cy, r in self.renderer.fan_spots(w, h, self.items(), cfg.get("style"),
                                                        self.scale()):
            self.queue_draw_area(int(cx - r) - 2, int(cy - r) - 2, int(2 * r) + 5, int(2 * r) + 5)

    def _hover(self, v):
        self._hovered = v
        self.queue_draw()

    # ------------------------------------------------------------ ratón
    def on_press(self, _w, ev):
        if ev.button == 3:
            self.popup_menu(ev)
            return True
        if ev.button == 1 and ev.type == Gdk.EventType._2BUTTON_PRESS:
            self.get_application().activate_action("window", None)
            return True
        if ev.button == 1 and not self.fcfg().get("locked"):
            if self.layer:
                self._drag = (ev.x_root, ev.y_root, ev.x, ev.y)
            elif getattr(self, "wtype", None) == Gdk.WindowTypeHint.UTILITY:
                self.begin_move_drag(ev.button, int(ev.x_root), int(ev.y_root), ev.time)
            else:
                # los gestores no mueven ventanas «dock»/«desktop»: lo hacemos nosotros
                self._drag = ("x11", ev.x, ev.y)
            return True
        return False

    def on_motion(self, _w, ev):
        if self._drag and self._drag[0] == "x11":
            self.move(int(ev.x_root - self._drag[1]), int(ev.y_root - self._drag[2]))
            return True
        if self._drag and self.layer:
            f = self.fcfg()
            dx, dy = ev.x - self._drag[2], ev.y - self._drag[3]
            f["x"] = max(0, int((f.get("x") or 0) + dx))
            f["y"] = max(0, int((f.get("y") or 0) + dy))
            self.place()
        return False

    def on_release(self, _w, ev):
        if self._drag:
            self._drag = None
            self._schedule_save()
        return False

    def on_configure(self, _w, ev):
        if not self.layer:
            f = self.fcfg()
            x, y = self.get_position()
            if (f.get("x"), f.get("y")) != (x, y):
                f["x"], f["y"] = x, y
                self._schedule_save()
        return False

    def _schedule_save(self):
        if self._save_src:
            GLib.source_remove(self._save_src)
        self._save_src = GLib.timeout_add(800, self._save)

    def _save(self):
        self._save_src = None
        self.store.save_cfg()
        return False

    # ------------------------------------------------------------ menú
    def popup_menu(self, ev):
        m = Gtk.Menu()
        f = self.fcfg()

        def check(label, key):
            it = Gtk.CheckMenuItem(label=label)
            it.set_active(bool(f.get(key)))
            it.connect("toggled", lambda w: self._toggle(key, w.get_active()))
            m.append(it)

        check(_("Lock position"), "locked")
        check(_("Always on top"), "on_top")
        ct = Gtk.CheckMenuItem(label=_("Click-through when locked"))
        ct.set_active(bool(f.get("click_through")))
        ct.connect("toggled", lambda w: self._toggle("click_through", w.get_active()))
        m.append(ct)
        m.append(Gtk.SeparatorMenuItem())
        st = Gtk.MenuItem(label=_("Style"))
        sm = Gtk.Menu()
        group = None
        for key, name in style_names().items():
            it = Gtk.RadioMenuItem.new_with_label_from_widget(group, name)
            group = it
            it.set_active(self.store.cfg.get("style") == key)
            it.connect("toggled", lambda w, k=key: w.get_active() and self._set("style", k))
            sm.append(it)
        st.set_submenu(sm)
        m.append(st)
        sz = Gtk.MenuItem(label=_("Size"))
        szm = Gtk.Menu()
        group = None
        for key, name in (("small", _("Small")), ("medium", _("Medium")), ("large", _("Large"))):
            it = Gtk.RadioMenuItem.new_with_label_from_widget(group, name)
            group = it
            it.set_active(self.store.cfg.get("size") == key)
            it.connect("toggled", lambda w, k=key: w.get_active() and self._set("size", k))
            szm.append(it)
        sz.set_submenu(szm)
        m.append(sz)
        m.append(Gtk.SeparatorMenuItem())
        # nombre propio para un sensor: el que está bajo el puntero y, en un submenú, todos
        items = self.items()
        w, h = self.get_size()
        hit = self.renderer.hit(ev.x, ev.y, len(items), self.store.cfg.get("style", "cockpit"),
                                self.scale(), w, h)
        app = self.get_application()
        if hit is not None:
            it = items[hit][0]
            rn = Gtk.MenuItem(label=_("Rename «%s»…") % it.get("label", ""))
            rn.connect("activate", lambda _w, cid=it["id"]: app.rename_sensor(cid))
            m.append(rn)
        if items:
            sub = Gtk.MenuItem(label=_("Rename a sensor…"))
            subm = Gtk.Menu()
            for it, _p in items:
                mi = Gtk.MenuItem(label=it.get("label", it["id"]))
                mi.connect("activate", lambda _w, cid=it["id"]: app.rename_sensor(cid))
                subm.append(mi)
            sub.set_submenu(subm)
            m.append(sub)
            m.append(Gtk.SeparatorMenuItem())
        for label, action in ((_("Open window"), "window"), (_("Settings"), "settings")):
            it = Gtk.MenuItem(label=label)
            it.connect("activate", lambda w, a=action: self.get_application().activate_action(a, None))
            m.append(it)
        hide = Gtk.MenuItem(label=_("Hide floating widget"))
        hide.connect("activate", lambda w: self.get_application().set_floating(False))
        m.append(hide)
        m.show_all()
        m.popup_at_pointer(ev)

    def _toggle(self, key, val):
        self.fcfg()[key] = val
        self.store.save_cfg()
        self.apply_settings()

    def _set(self, key, val):
        self.store.cfg[key] = val
        self.store.save_cfg()
        self.resize_to_content()
        self.queue_draw()
