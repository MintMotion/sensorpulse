"""Colores, estados y tipografía comunes a todas las vistas."""

FONT = "Inter, Cantarell, Ubuntu, 'Noto Sans', 'DejaVu Sans', sans-serif"
FONT_NUM = "Inter Display, Inter, Cantarell, Ubuntu, 'DejaVu Sans', sans-serif"
# cifras técnicas del estilo «Cabina»: JetBrains Mono si está instalada (paquete fonts-jetbrains-mono);
# si no, Pango usa las siguientes de la lista
FONT_MONO = "JetBrains Mono, Inter, 'DejaVu Sans Mono', monospace"

GREEN = (0.13, 0.77, 0.37)
YELLOW = (0.92, 0.70, 0.03)
ORANGE = (0.98, 0.45, 0.09)
RED = (0.94, 0.27, 0.27)
GREY = (0.55, 0.58, 0.62)

PALETTES = {
    "dark": {
        "bg": (0.07, 0.09, 0.12), "card": (0.11, 0.13, 0.17), "card2": (0.14, 0.17, 0.22),
        "fg": (0.93, 0.95, 0.97), "dim": (0.58, 0.63, 0.70), "track": (1, 1, 1, 0.08),
        "grid": (1, 1, 1, 0.06),
    },
    "deep": {   # «Oscuro profundo»: casi negro, ideal para pantallas OLED
        "bg": (0.015, 0.017, 0.022), "card": (0.055, 0.06, 0.075), "card2": (0.08, 0.09, 0.11),
        "fg": (0.93, 0.95, 0.97), "dim": (0.52, 0.56, 0.62), "track": (1, 1, 1, 0.07),
        "grid": (1, 1, 1, 0.05),
    },
    "light": {
        "bg": (0.95, 0.96, 0.97), "card": (1, 1, 1), "card2": (0.97, 0.98, 0.99),
        "fg": (0.09, 0.11, 0.15), "dim": (0.40, 0.45, 0.52), "track": (0, 0, 0, 0.08),
        "grid": (0, 0, 0, 0.06),
    },
}

# Paleta alternativa para daltonismo (azul → naranja → magenta)
CB = {"ok": (0.0, 0.45, 0.70), "warn": (0.90, 0.62, 0.0), "crit": (0.80, 0.47, 0.65)}

STATE_ICON = {"normal": "●", "hot": "▲", "critical": "■", "sleep": "☾", "nodata": "–"}


def hex_to_rgb(h, default=(0.13, 0.83, 0.93)):
    try:
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except (ValueError, AttributeError):
        return default


def mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def value_color(v, warn, crit, accent=None, colorblind=False):
    """Color continuo según la cercanía al umbral: verde → amarillo → naranja → rojo.

    Si el sensor no tiene umbrales (ventiladores, %, etc.) devuelve el color de acento.
    """
    if v is None:
        return GREY
    if warn is None or crit is None:
        return accent or GREEN
    g, y, o, r = (GREEN, YELLOW, ORANGE, RED) if not colorblind else \
        (CB["ok"], CB["warn"], CB["warn"], CB["crit"])
    start = warn - 15.0
    if v <= start:
        return g
    if v <= warn:
        return mix(g, y, (v - start) / 15.0)
    if v <= crit:
        span = max(1.0, crit - warn)
        t = (v - warn) / span
        return mix(y, o, t * 2) if t < 0.5 else mix(o, r, (t - 0.5) * 2)
    return r


def state_color(state, colorblind=False):
    if colorblind:
        return {"normal": CB["ok"], "hot": CB["warn"], "critical": CB["crit"]}.get(state, GREY)
    return {"normal": GREEN, "hot": ORANGE, "critical": RED}.get(state, GREY)


def _portal_color_scheme():
    """1 = oscuro, 2 = claro, 0/None = sin preferencia (portal org.freedesktop.appearance)."""
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        for method, sig in (("ReadOne", "(v)"), ("Read", "(v)")):
            try:
                r = bus.call_sync("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                                  "org.freedesktop.portal.Settings", method,
                                  GLib.Variant("(ss)", ("org.freedesktop.appearance", "color-scheme")),
                                  GLib.VariantType(sig), Gio.DBusCallFlags.NONE, 800, None)
                v = r.unpack()[0]
                while hasattr(v, "unpack"):
                    v = v.unpack()
                return int(v)
            except Exception:
                continue
    except Exception:
        pass
    return None


_sys_cache = {}


def system_theme():
    """«dark» o «light» según el escritorio (portal y, si no, el tema GTK)."""
    import time as _t
    hit = _sys_cache.get("v")
    if hit and _t.monotonic() - hit[0] < 10:
        return hit[1]
    res = None
    cs = _portal_color_scheme()
    if cs == 1:
        res = "dark"
    elif cs == 2:
        res = "light"
    if res is None:
        try:
            from gi.repository import Gtk
            st = Gtk.Settings.get_default()
            name = (st.get_property("gtk-theme-name") or "").lower()
            dark = st.get_property("gtk-application-prefer-dark-theme") or "dark" in name
            res = "dark" if dark else "light"
        except Exception:
            res = "dark"
    _sys_cache["v"] = (_t.monotonic(), res)
    return res


def resolve(cfg):
    t = cfg.get("theme", "auto")
    return t if t in ("dark", "light", "deep") else system_theme()
