"""Icono del panel/bandeja con los valores y el color de estado.

Orden de preferencia:
1. XApp.StatusIcon (Cinnamon, MATE, Xfce en Mint/LMDE): admite texto junto al icono.
2. Ayatana AppIndicator (StatusNotifierItem): KDE, GNOME con extensión, Budgie, LXQt, Xfce.
3. Gtk.StatusIcon (bandeja antigua X11) como último recurso.

El icono se dibuja al vuelo (número + barra de color) para que se vea el valor
incluso en paneles que no muestran texto.
"""

import os
import stat
import tempfile

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from .. import APP_NAME
from ..views import muted_text, panel_split
from ..i18n import _
from . import draw as D
from .theme import state_color, value_color, hex_to_rgb


def _desktop():
    return (os.environ.get("XDG_CURRENT_DESKTOP", "") + ":" +
            os.environ.get("DESKTOP_SESSION", "")).lower()


_ICON_DIR = None


def _safe_dir(d):
    """¿Es d una carpeta real (no un enlace), mía y sin acceso para otros usuarios?"""
    try:
        st = os.lstat(d)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and not (st.st_mode & 0o077)


def _icon_dir():
    """Carpeta privada para los iconos de la bandeja. Nunca una ruta predecible en /tmp:
    otro usuario podría haber preparado la carpeta con enlaces a ficheros de la víctima."""
    global _ICON_DIR
    if _ICON_DIR and _safe_dir(_ICON_DIR):
        return _ICON_DIR
    run = os.environ.get("XDG_RUNTIME_DIR")
    if run and os.path.isdir(run):
        d = os.path.join(run, "sensorpulse-icons")
    else:
        d = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                         "sensorpulse", "icons")
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
    except OSError:
        pass
    if not _safe_dir(d):
        d = tempfile.mkdtemp(prefix="sensorpulse-icons-")   # 0700 y nombre aleatorio
    _ICON_DIR = d
    return d


def _write_png(surface, path):
    """Escribe el PNG sin seguir enlaces simbólicos y con permisos 0600."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        surface.write_to_png(f)


class Tray:
    def __init__(self, app, store):
        self.app = app
        self.store = store
        self.kind = None
        self.icon = None
        self._last_key = None
        self._counter = 0
        self.menu = self._build_menu()
        d = _desktop()
        prefer_xapp = any(k in d for k in ("cinnamon", "mate", "xfce"))
        if prefer_xapp and self._try_xapp():
            pass
        elif self._try_ayatana():
            pass
        else:
            self._try_gtk()
        store.subscribe(self.update)
        self.update()

    # ------------------------------------------------------------ backends
    def _try_xapp(self):
        try:
            gi.require_version("XApp", "1.0")
            from gi.repository import XApp
        except (ValueError, ImportError):
            return False
        ic = XApp.StatusIcon()
        ic.set_name("sensorpulse")
        ic.set_primary_menu(None)
        ic.set_secondary_menu(self.menu)
        ic.connect("activate", lambda *a: self.app.activate_action("window", None))
        self.icon, self.kind = ic, "xapp"
        return True

    def _try_ayatana(self):
        for ns, ver in (("AyatanaAppIndicatorGlib", "2.0"), ("AyatanaAppIndicator3", "0.1")):
            try:
                gi.require_version(ns, ver)
                mod = __import__("gi.repository", fromlist=[ns])
                AI = getattr(mod, ns)
            except (ValueError, ImportError, AttributeError):
                continue
            if ns == "AyatanaAppIndicator3":
                ind = AI.Indicator.new("sensorpulse", "sensorpulse",
                                       AI.IndicatorCategory.HARDWARE)
                ind.set_status(AI.IndicatorStatus.ACTIVE)
                ind.set_menu(self.menu)
                ind.set_title(APP_NAME)
            else:
                # API GLib (sin GTK): menú como Gio.Menu con acciones de la aplicación
                ind = AI.Indicator.new("sensorpulse", "sensorpulse",
                                       AI.IndicatorCategory.HARDWARE)
                ind.set_status(AI.IndicatorStatus.ACTIVE)
                ind.set_title(APP_NAME)
                try:
                    ind.set_menu(self._gio_menu())
                    ind.set_actions(self.app, "app")
                except Exception:
                    pass
            self.icon, self.kind = ind, "ayatana"
            return True
        return False

    def _try_gtk(self):
        try:
            ic = Gtk.StatusIcon()
        except Exception:
            return False
        ic.connect("activate", lambda *a: self.app.activate_action("window", None))
        ic.connect("popup-menu", lambda i, b, t: self.menu.popup(None, None, None, None, b, t))
        self.icon, self.kind = ic, "gtk"
        return True

    # ------------------------------------------------------------ menú
    def _build_menu(self):
        m = Gtk.Menu()
        self.summary_items = []
        self.summary_sep = Gtk.SeparatorMenuItem()
        for label, act in ((_("Open window"), "window"), (_("Show/hide floating widget"), "toggle-floating"),
                           (_("Settings"), "settings")):
            it = Gtk.MenuItem(label=label)
            it.connect("activate", lambda w, a=act: self.app.activate_action(a, None))
            m.append(it)
        mute = Gtk.MenuItem(label=_("Mute alarms for 1 hour"))
        mute.connect("activate", lambda *a: self.app.activate_action("mute-hour", None))
        m.append(mute)
        m.append(Gtk.SeparatorMenuItem())
        q = Gtk.MenuItem(label=_("Quit"))
        q.connect("activate", lambda *a: self.app.activate_action("quit", None))
        m.append(q)
        m.show_all()
        return m

    def _gio_menu(self):
        from gi.repository import Gio
        menu = Gio.Menu()
        menu.append(_("Open window"), "app.window")
        menu.append(_("Show/hide floating widget"), "app.toggle-floating")
        menu.append(_("Settings"), "app.settings")
        # el mismo «Silenciar» que el menú GTK (faltaba en el indicador Ayatana GLib)
        menu.append(_("Mute alarms for 1 hour"), "app.mute-hour")
        menu.append(_("Quit"), "app.quit")
        return menu

    def _refresh_summary(self, rows):
        for it in self.summary_items:
            self.menu.remove(it)
        if self.summary_sep.get_parent():
            self.menu.remove(self.summary_sep)
        self.summary_items = []
        for i, txt in enumerate(rows):
            it = Gtk.MenuItem(label=txt)
            it.set_sensitive(False)
            self.menu.insert(it, i)
            self.summary_items.append(it)
        if rows:
            self.menu.insert(self.summary_sep, len(rows))
        self.menu.show_all()

    # ------------------------------------------------------------ actualización
    def update(self):
        if self.icon is None:
            return
        store = self.store
        cfg = store.cfg
        bar, extra = panel_split(cfg, store.snapshot)
        shown = [store.item(c, d) for c, d in bar]
        items = shown + [store.item(c, d) for c, d in extra]
        gauge = cfg.get("panel_style") == "gauge"
        names = cfg.get("panel_labels", True)
        parts, rows = [], []
        for it in items:
            v, u = D.fmt(dict(it, anim=it["value"]))
            tag = D.status_tag(it)
            rows.append("%s%s: %s %s%s" % ((tag[0] + " ") if tag else "", it["label"], v, u,
                                           (" · " + tag[1].lower()) if tag else ""))
        for i, it in enumerate(shown):
            v, u = D.fmt(dict(it, anim=it["value"]))
            tag = D.status_tag(it)
            sym = tag[0] if tag and tag[2] != "normal" else ""
            if gauge and i == 0:
                # con indicadores, el primer valor ya va dibujado en el icono
                if names:
                    parts.append(it["label"])
                continue
            # nombre delante del valor, como en el widget flotante («CPU 45°»)
            val = "%s%s%s" % (sym, v, "°" if it["kind"] == "temp" else (u if u == "%" else ""))
            parts.append("%s %s" % (it["label"], val) if names else val)
        label = "  ".join(parts)
        st = store.state()
        key = (label, st, muted_text(store.snapshot), store.stale, gauge,
               shown[0]["value"] if shown else None)
        if key != self._last_key:
            self._last_key = key
            path = self._render_icon(shown[0] if shown else None, st, gauge)
            tip = APP_NAME + "\n" + "\n".join(rows)
            if store.stale:
                tip += "\n" + _("Waiting for data…")
            elif muted_text(store.snapshot):
                tip += "\n" + muted_text(store.snapshot)
            if self.kind == "xapp":
                self.icon.set_icon_name(path)
                self.icon.set_label(label)
                self.icon.set_tooltip_text(tip)
            elif self.kind == "ayatana":
                self.icon.set_icon_full(path, tip)
                try:
                    self.icon.set_label(label, label)
                except Exception:
                    pass
            elif self.kind == "gtk":
                self.icon.set_from_file(path)
                self.icon.set_tooltip_text(tip)
            self._refresh_summary(rows)

    def _render_icon(self, it, state, gauge=False):
        """Icono 64×64: número grande y barra inferior con el color de estado, o (gauge=True)
        un indicador circular tipo cuentakilómetros con el número dentro."""
        import cairo
        S = 64
        # nítido en pantallas HiDPI: se dibuja a la escala real del monitor
        try:
            from gi.repository import Gdk
            mon = Gdk.Display.get_default().get_monitor(0)
            k = max(1, int(mon.get_scale_factor()))
        except Exception:
            k = 1
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, S * k, S * k)
        cr = cairo.Context(surf)
        cr.scale(k, k)
        accent = hex_to_rgb(self.store.cfg.get("accent"))
        col = state_color(state) if state in ("normal", "hot", "critical") else (0.6, 0.6, 0.6)
        if it is not None and gauge:
            v, _u = D.fmt(dict(it, anim=it["value"]))
            c = value_color(it.get("value"), it.get("warn"), it.get("crit"), accent)
            D.draw_panel_gauge(cr, S / 2, S / 2 + 3, S * 0.42, D.frac(dict(it, anim=it["value"])),
                               c, v + ("°" if it["kind"] == "temp" else ""))
        elif it is not None:
            v, _u = D.fmt(dict(it, anim=it["value"]))
            c = value_color(it.get("value"), it.get("warn"), it.get("crit"), accent)
            size = 40 if len(v) <= 2 else 30
            # sombra para que se lea en paneles claros y oscuros
            D.text(cr, v, S / 2 + 1.5, S / 2 - 4.5, size, (0, 0, 0), D.Pango.Weight.BOLD,
                   font=D.FONT_NUM, alpha=0.55)
            D.text(cr, v, S / 2, S / 2 - 6, size, c, D.Pango.Weight.BOLD, font=D.FONT_NUM)
            # contorno suave para paneles claros
            D.rounded_rect(cr, 6, S - 14, S - 12, 9, 4)
            cr.set_source_rgb(*c)
            cr.fill()
        else:
            cr.set_source_rgb(*col)
            cr.arc(S / 2, S / 2, S / 4, 0, 6.2832)
            cr.fill()
        # nombre único: algunos paneles cachean por ruta
        self._counter = (self._counter + 1) % 4
        path = os.path.join(_icon_dir(), "tray-%d.png" % self._counter)
        _write_png(surf, path)
        return path
