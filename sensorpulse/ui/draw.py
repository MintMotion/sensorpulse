"""Motor gráfico (cairo): indicadores HUD, anillos, barras, minimal, cristal,
gráficas con color por tramos y hélice de ventilador animada."""

import math

import gi
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo

from .theme import FONT, FONT_NUM, FONT_MONO, value_color, GREY, RED

TAU = 2 * math.pi


# ------------------------------------------------------------------ utilidades

def rounded_rect(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def set_rgba(cr, c, a=None):
    if len(c) == 4 and a is None:
        cr.set_source_rgba(*c)
    else:
        cr.set_source_rgba(c[0], c[1], c[2], 1.0 if a is None else a)


def text(cr, s, x, y, size, color, weight=Pango.Weight.NORMAL, align="center",
         font=FONT, alpha=1.0, valign="middle", max_width=None, tabular=False):
    """Dibuja texto con Pango. Devuelve (ancho, alto)."""
    layout = PangoCairo.create_layout(cr)
    fd = Pango.FontDescription.from_string("%s %s" % (font.split(",")[0], 10))
    fd.set_family(font)
    fd.set_absolute_size(size * Pango.SCALE)
    fd.set_weight(weight)
    layout.set_font_description(fd)
    if tabular:
        try:
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_font_features_new("tnum=1"))
            layout.set_attributes(attrs)
        except Exception:
            pass
    layout.set_text(s, -1)
    if max_width:
        layout.set_width(int(max_width * Pango.SCALE))
        layout.set_ellipsize(Pango.EllipsizeMode.END)
    _ink, log = layout.get_pixel_extents()
    w, h = log.width, log.height
    if align == "center":
        px = x - w / 2
    elif align == "right":
        px = x - w
    else:
        px = x
    py = y - h / 2 if valign == "middle" else (y if valign == "top" else y - h)
    set_rgba(cr, color, alpha)
    cr.move_to(px, py)
    PangoCairo.show_layout(cr, layout)
    return w, h


def fmt(item):
    """Texto del valor y de la unidad listos para mostrar."""
    v = item.get("anim", item.get("value"))
    kind = item.get("kind")
    unit = item.get("unit", "")
    if item.get("asleep"):
        return "zZ", ""
    if v is None:
        return "–", unit
    if kind == "temp":
        return "%d" % round(v), unit
    if kind in ("fan",):
        return "%d" % round(v), "RPM"
    if kind == "percent":
        return "%d" % round(v), "%"
    if kind == "power":
        return ("%.0f" if v >= 10 else "%.1f") % v, "W"
    if kind == "freq":
        return ("%.1f" % (v / 1000.0), "GHz") if v >= 1000 else ("%d" % v, "MHz")
    if kind == "volt":
        return "%.2f" % v, "V"
    if kind == "status":
        return item.get("text") or "%d" % v, ""
    return "%.1f" % v, unit


SYMBOLS = {"normal": "✓", "hot": "⚠", "critical": "‼"}


def status_tag(item):
    """(símbolo, palabra, estado) para no depender solo del color. None si no aplica."""
    from ..i18n import _
    v = item.get("value")
    if item.get("kind") != "temp" or item.get("warn") is None or v is None or item.get("asleep"):
        return None
    if item.get("crit") is not None and v >= item["crit"]:
        st, word = "critical", _("CRITICAL")
    elif item.get("throttling"):
        st, word = "hot", _("SLOWING DOWN")
    elif v >= item["warn"]:
        st, word = "hot", _("HOT")
    else:
        st, word = "normal", _("NORMAL")
    return SYMBOLS[st], word, st


def _state_word(item):
    t = status_tag(item)
    return "%s %s" % (t[0], t[1]) if t else ""


def tagged_label(item):
    """Etiqueta con el símbolo de estado delante (✓ CPU, ⚠ GPU...)."""
    t = status_tag(item)
    return "%s %s" % (t[0], item.get("label", "")) if t else item.get("label", "")


def frac(item):
    v = item.get("anim", item.get("value"))
    if v is None:
        return 0.0
    lo, hi = item.get("vmin", 0.0), item.get("vmax", 100.0)
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def color_at(item, f, accent):
    """Color del indicador en la fracción f del recorrido."""
    lo, hi = item.get("vmin", 0.0), item.get("vmax", 100.0)
    v = lo + f * (hi - lo)
    return value_color(v, item.get("warn"), item.get("crit"), accent, item.get("cb", False))


def item_color(item, accent):
    if item.get("asleep") or item.get("value") is None:
        return GREY
    return value_color(item.get("anim", item.get("value")), item.get("warn"),
                       item.get("crit"), accent, item.get("cb", False))


# ------------------------------------------------------------------ capas en caché

_LAYERS = {}


def cached_layer(cr, key, cx, cy, half, painter):
    """Dibuja una vez en una superficie lo que no cambia (esfera, marcas, zonas) y en cada
    fotograma solo la copia. Respeta la escala HiDPI de la superficie de destino."""
    import cairo
    size = int(math.ceil(half * 2)) + 2
    try:
        sx, _sy = cr.get_target().get_device_scale()
    except Exception:
        sx = 1.0
    k = key + (size, sx)
    surf = _LAYERS.get(k)
    if surf is None:
        if len(_LAYERS) > 64:
            _LAYERS.clear()
        try:
            surf = cr.get_target().create_similar(cairo.CONTENT_COLOR_ALPHA, size, size)
        except Exception:
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        c2 = cairo.Context(surf)
        painter(c2, size / 2.0, size / 2.0)
        _LAYERS[k] = surf
    cr.save()
    cr.set_source_surface(surf, cx - size / 2.0, cy - size / 2.0)
    cr.paint()
    cr.restore()


# ------------------------------------------------------------------ cifras que ruedan

_ODO = {}


def odometer_busy():
    """¿Hay alguna cifra rodando ahora mismo? (el flotante no puede usar su copia en caché)."""
    import time as _t
    now = _t.monotonic()
    return any(st[0] != st[1] and st[2] and now - st[2] < 0.25 for st in _ODO.values())


def odometer(cr, key, val, x, y, size, color, weight, font):
    """Número centrado; cuando cambia, el nuevo entra rodando desde abajo (250 ms)."""
    import time as _t
    now = _t.monotonic()
    st = _ODO.get(key)
    if st is None and len(_ODO) > 256:
        _ODO.clear()      # vistas previas del asistente que ya no existen
    if st is None or st[1] != val:
        st = (st[1] if st else val, val, now if st else 0.0)
        _ODO[key] = st
    old, new, t0 = st
    k = (now - t0) / 0.25
    if old == new or k >= 1 or t0 == 0.0:
        return text(cr, new, x, y, size, color, weight, font=font, tabular=True)
    k = 1 - (1 - k) ** 3
    w, h = text(cr, new, x, y, size, color, weight, font=font, tabular=True, alpha=0.0)
    cr.save()
    cr.rectangle(x - w / 2 - 4, y - h / 2, w + 8, h)
    cr.clip()
    text(cr, old, x, y - h * k, size, color, weight, font=font, tabular=True, alpha=1 - k)
    text(cr, new, x, y + h * (1 - k), size, color, weight, font=font, tabular=True, alpha=k)
    cr.restore()
    return w, h


# ------------------------------------------------------------------ HUD futurista

def draw_hud(cr, cx, cy, r, item, pal, accent, pulse=0.0):
    """Indicador tipo velocímetro: 270°, marcas, aguja con inercia y brillo."""
    a0 = math.radians(135)
    sweep = math.radians(270)
    thick = r * 0.11
    f = frac(item)
    col = item_color(item, accent)

    # halo de fondo
    grad_r = r * 1.05
    import cairo
    rg = cairo.RadialGradient(cx, cy, r * 0.2, cx, cy, grad_r)
    rg.add_color_stop_rgba(0, col[0], col[1], col[2], 0.10)
    rg.add_color_stop_rgba(1, col[0], col[1], col[2], 0.0)
    cr.set_source(rg)
    cr.new_path()
    cr.arc(cx, cy, grad_r, 0, TAU)
    cr.fill()

    # pista, zonas y marcas apagadas: capa estática en caché (se dibuja una vez)
    lo, hi = item.get("vmin", 0.0), item.get("vmax", 100.0)
    key = ("hud", round(r, 1), pal["track"], pal["dim"], lo, hi, item.get("warn"), item.get("crit"))

    def _static(c2, ox, oy):
        c2.set_line_cap(1)
        c2.set_line_width(thick)
        set_rgba(c2, pal["track"])
        c2.new_path()
        c2.arc(ox, oy, r, a0, a0 + sweep)
        c2.stroke()
        if item.get("warn") is not None and hi > lo:
            fw = max(0, min(1, (item["warn"] - lo) / (hi - lo)))
            fc = max(0, min(1, ((item.get("crit") or hi) - lo) / (hi - lo)))
            c2.set_line_width(max(1.5, r * 0.025))
            c2.set_line_cap(0)
            set_rgba(c2, (0.98, 0.45, 0.09), 0.75)
            c2.new_path()
            c2.arc(ox, oy, r + thick * 0.95, a0 + sweep * fw, a0 + sweep * fc)
            c2.stroke()
            set_rgba(c2, RED, 0.9)
            c2.new_path()
            c2.arc(ox, oy, r + thick * 0.95, a0 + sweep * fc, a0 + sweep)
            c2.stroke()
        for i in range(28):
            t = i / 27.0
            ang = a0 + sweep * t
            major = i % 3 == 0
            r1 = r - thick * (1.3 if major else 1.0)
            r2 = r - thick * (2.2 if major else 1.6)
            c2.set_line_width(max(1.0, r * (0.018 if major else 0.01)))
            set_rgba(c2, pal["dim"], 0.35)
            c2.move_to(ox + r1 * math.cos(ang), oy + r1 * math.sin(ang))
            c2.line_to(ox + r2 * math.cos(ang), oy + r2 * math.sin(ang))
            c2.stroke()

    cached_layer(cr, key, cx, cy, r * 1.3, _static)

    # marcas encendidas (solo las que tocan: las apagadas están en la capa en caché)
    for i in range(28):
        t = i / 27.0
        if t > f + 1e-6:
            break
        ang = a0 + sweep * t
        major = i % 3 == 0
        r1 = r - thick * (1.3 if major else 1.0)
        r2 = r - thick * (2.2 if major else 1.6)
        cr.set_line_width(max(1.0, r * (0.018 if major else 0.01)))
        set_rgba(cr, color_at(item, t, accent), 0.9)
        cr.move_to(cx + r1 * math.cos(ang), cy + r1 * math.sin(ang))
        cr.line_to(cx + r2 * math.cos(ang), cy + r2 * math.sin(ang))
        cr.stroke()

    # arco de valor con degradado de calor (por segmentos) + brillo
    if f > 0.001:
        steps = max(2, int(28 * f))
        for _glow, width, alpha in ((True, thick * 2.2, 0.18), (False, thick, 1.0)):
            cr.set_line_width(width)
            cr.set_line_cap(0)
            for s in range(steps):
                t0 = f * s / steps
                t1 = f * (s + 1) / steps + 0.002
                c = color_at(item, (t0 + t1) / 2, accent)
                set_rgba(cr, c, alpha)
                cr.new_path()
                cr.arc(cx, cy, r, a0 + sweep * t0, a0 + sweep * min(t1, f))
                cr.stroke()
        # punta redondeada
        ang = a0 + sweep * f
        set_rgba(cr, col)
        cr.new_path()
        cr.arc(cx + r * math.cos(ang), cy + r * math.sin(ang), thick / 2, 0, TAU)
        cr.fill()

    # aguja corta (marcador) por dentro del arco, con brillo
    ang = a0 + sweep * f
    r_in, r_out = r - thick * 3.2, r - thick * 0.9
    for w, al in ((r * 0.075, 0.18), (r * 0.03, 0.95)):
        cr.set_line_width(w)
        cr.set_line_cap(1)
        set_rgba(cr, col, al)
        cr.new_path()
        cr.move_to(cx + r_in * math.cos(ang), cy + r_in * math.sin(ang))
        cr.line_to(cx + r_out * math.cos(ang), cy + r_out * math.sin(ang))
        cr.stroke()

    # textos
    val, unit = fmt(item)
    w, _h = text(cr, val, cx, cy + r * 0.02, r * 0.46, pal["fg"], Pango.Weight.SEMIBOLD,
                 font=FONT_NUM, tabular=True)
    if unit:
        text(cr, unit, cx + w / 2 + 2, cy - r * 0.10, r * 0.15, pal["dim"], align="left")
    text(cr, item.get("label", ""), cx, cy + r * 0.42, r * 0.15, pal["dim"],
         Pango.Weight.MEDIUM, max_width=r * 1.3)
    st = item.get("value")
    if item.get("warn") is not None and st is not None and not item.get("asleep"):
        tag = _state_word(item)
        text(cr, tag, cx, cy + r * 0.80, r * 0.13, col, Pango.Weight.SEMIBOLD)

    # pulso de alarma
    if item.get("alarm") and pulse > 0:
        cr.set_line_width(max(2.0, r * 0.05))
        set_rgba(cr, RED, 0.25 + 0.6 * pulse)
        cr.new_path()
        cr.arc(cx, cy, r + thick * 1.8, 0, TAU)
        cr.stroke()


# ------------------------------------------------------------------ anillos

def draw_ring(cr, cx, cy, r, item, pal, accent, pulse=0.0):
    thick = max(3.0, r * 0.12)
    f = frac(item)
    col = item_color(item, accent)
    cr.set_line_width(thick)
    cr.set_line_cap(1)
    set_rgba(cr, pal["track"])
    cr.new_path()
    cr.arc(cx, cy, r, 0, TAU)
    cr.stroke()
    if f > 0.001:
        start = -math.pi / 2
        set_rgba(cr, col, 0.18)
        cr.set_line_width(thick * 2.2)
        cr.new_path()
        cr.arc(cx, cy, r, start, start + TAU * f)
        cr.stroke()
        set_rgba(cr, col)
        cr.set_line_width(thick)
        cr.new_path()
        cr.arc(cx, cy, r, start, start + TAU * f)
        cr.stroke()
    val, unit = fmt(item)
    text(cr, val + (unit if unit in ("°C", "°F", "%") else ""), cx, cy - r * 0.06, r * 0.46,
         pal["fg"], Pango.Weight.SEMIBOLD, font=FONT_NUM, tabular=True)
    if unit and unit not in ("°C", "°F", "%"):
        text(cr, unit, cx, cy + r * 0.34, r * 0.18, pal["dim"])
    text(cr, tagged_label(item), cx, cy + r + thick + r * 0.28, r * 0.24, pal["dim"],
         Pango.Weight.MEDIUM, max_width=r * 2.6)
    if item.get("alarm") and pulse > 0:
        set_rgba(cr, RED, 0.2 + 0.6 * pulse)
        cr.set_line_width(max(2.0, thick * 0.5))
        cr.new_path()
        cr.arc(cx, cy, r + thick * 1.4, 0, TAU)
        cr.stroke()


# ------------------------------------------------------------------ barras

def draw_bar(cr, x, y, w, h, item, pal, accent, pulse=0.0):
    f = frac(item)
    val, unit = fmt(item)
    lh = h * 0.42
    text(cr, tagged_label(item), x, y + lh / 2, lh * 0.72, pal["dim"], Pango.Weight.MEDIUM,
         align="left", max_width=w * 0.6)
    text(cr, "%s %s" % (val, unit), x + w, y + lh / 2, lh * 0.85, pal["fg"],
         Pango.Weight.SEMIBOLD, align="right", font=FONT_NUM, tabular=True)
    by, bh = y + lh + h * 0.12, h * 0.34
    rounded_rect(cr, x, by, w, bh, bh / 2)
    set_rgba(cr, pal["track"])
    cr.fill()
    if f > 0.001:
        import cairo
        lg = cairo.LinearGradient(x, 0, x + w, 0)
        for s in range(0, 11):
            t = s / 10.0
            c = color_at(item, t, accent)
            lg.add_color_stop_rgb(t, *c)
        cr.save()
        rounded_rect(cr, x, by, max(bh, w * f), bh, bh / 2)
        cr.clip()
        cr.set_source(lg)
        cr.paint()
        cr.restore()
    if item.get("warn") is not None:
        lo, hi = item.get("vmin", 0.0), item.get("vmax", 100.0)
        if hi > lo:
            fw = max(0, min(1, (item["warn"] - lo) / (hi - lo)))
            set_rgba(cr, pal["fg"], 0.55)
            cr.set_line_width(1.5)
            cr.move_to(x + w * fw, by - 2)
            cr.line_to(x + w * fw, by + bh + 2)
            cr.stroke()
    if item.get("alarm") and pulse > 0:
        rounded_rect(cr, x - 3, by - 3, w + 6, bh + 6, bh)
        set_rgba(cr, RED, 0.2 + 0.7 * pulse)
        cr.set_line_width(2)
        cr.stroke()


# ------------------------------------------------------------------ minimal

def draw_minimal(cr, x, y, w, h, item, pal, accent, pulse=0.0):
    col = item_color(item, accent)
    val, unit = fmt(item)
    alpha = 1.0 if not (item.get("alarm") and pulse > 0) else 0.45 + 0.55 * (1 - pulse)
    dot = h * 0.10
    set_rgba(cr, col, alpha)
    cr.new_path()
    cr.arc(x + dot, y + h * 0.28, dot, 0, TAU)
    cr.fill()
    text(cr, tagged_label(item).upper(), x + dot * 3, y + h * 0.28, h * 0.17, pal["dim"],
         Pango.Weight.SEMIBOLD, align="left", max_width=w - dot * 3)
    vw, _ = text(cr, val, x, y + h * 0.68, h * 0.50, col, Pango.Weight.BOLD, align="left",
                 font=FONT_NUM, alpha=alpha, tabular=True)
    if unit:
        text(cr, unit, x + vw + 3, y + h * 0.60, h * 0.20, pal["dim"], align="left")


# ------------------------------------------------------------------ cristal

def draw_glass(cr, x, y, w, h, item, pal, accent, points=None, pulse=0.0):
    import cairo
    col = item_color(item, accent)
    dark = pal["bg"][0] < 0.5
    st = status_tag(item)
    rad = h * 0.14
    # relleno translúcido tintado por el estado del sensor
    rounded_rect(cr, x, y, w, h, rad)
    tint = col if st and st[2] != "normal" else (1, 1, 1)
    def _tint():
        lg = cairo.LinearGradient(x, y, x, y + h)
        lg.add_color_stop_rgba(0, tint[0], tint[1], tint[2], 0.11 if dark else 0.55)
        lg.add_color_stop_rgba(1, tint[0], tint[1], tint[2], 0.04 if dark else 0.40)
        return lg
    gk = (round(x, 1), round(y, 1), round(w, 1), round(h, 1), dark)
    cr.set_source(_cached(("gtint",) + gk + (tuple(tint),), _tint))
    cr.fill_preserve()
    cr.save()
    cr.clip_preserve()
    cr.set_source(grain_pattern())
    cr.paint_with_alpha(0.6 if dark else 0.25)
    # sombra interior suave abajo
    def _shadow():
        sg = cairo.LinearGradient(x, y + h * 0.6, x, y + h)
        sg.add_color_stop_rgba(0, 0, 0, 0, 0)
        sg.add_color_stop_rgba(1, 0, 0, 0, 0.18 if dark else 0.06)
        return sg
    cr.set_source(_cached(("gshadow",) + gk, _shadow))
    cr.paint()
    cr.restore()
    # borde especular: luz arriba a la izquierda
    if item.get("alarm") and pulse > 0:
        set_rgba(cr, col, 0.3 + 0.6 * pulse)
    else:
        def _edge():
            bg = cairo.LinearGradient(x, y, x + w, y + h)
            bg.add_color_stop_rgba(0, 1, 1, 1, 0.45 if dark else 0.9)
            bg.add_color_stop_rgba(0.5, 1, 1, 1, 0.08)
            bg.add_color_stop_rgba(1, 1, 1, 1, 0.18 if dark else 0.5)
            return bg
        cr.set_source(_cached(("gedge",) + gk, _edge))
    cr.set_line_width(1.2)
    cr.new_path()
    rounded_rect(cr, x + 0.6, y + 0.6, w - 1.2, h - 1.2, rad)
    cr.stroke()
    pad = h * 0.12
    text(cr, tagged_label(item), x + pad, y + pad + h * 0.08, h * 0.14, pal["dim"],
         Pango.Weight.MEDIUM, align="left", max_width=w * 0.6)
    val, unit = fmt(item)
    vw, _ = text(cr, val, x + pad, y + h * 0.50, h * 0.32, pal["fg"], Pango.Weight.SEMIBOLD,
                 align="left", font=FONT_NUM, tabular=True)
    text(cr, unit, x + pad + vw + 3, y + h * 0.45, h * 0.14, pal["dim"], align="left")
    set_rgba(cr, col)
    cr.new_path()
    cr.arc(x + w - pad - h * 0.05, y + pad + h * 0.08, h * 0.05, 0, TAU)
    cr.fill()
    if points:
        draw_sparkline(cr, x + pad, y + h * 0.68, w - 2 * pad, h * 0.22, points, item, pal,
                       accent, fill=True, line=1.5)


# ------------------------------------------------------------------ gráficas

_GRADS = {}


def _cached(key, make):
    """Degradados reutilizables: crearlos en cada fotograma costaba tanto como dibujar."""
    g = _GRADS.get(key)
    if g is None:
        if len(_GRADS) > 128:
            _GRADS.clear()
        g = _GRADS[key] = make()
    return g


def heat_gradient(y, h, lo, hi, warn, crit, accent, cb=False, alpha=1.0):
    """Degradado VERTICAL con el color de cada valor (verde → amarillo → naranja → rojo).
    En la gráfica el color solo depende del valor, y el valor solo de la altura: con un
    único degradado vertical, un solo trazo sale con el mismo color por tramos que antes
    pedía un stroke() por cada segmento (0.3.6: ~300 trazos por gráfica y fotograma)."""
    import cairo

    def make():
        g = cairo.LinearGradient(0, y + h, 0, y)          # abajo = lo, arriba = hi
        if warn is None or crit is None or hi <= lo:
            c = accent or (0.13, 0.77, 0.37)
            g.add_color_stop_rgba(0, c[0], c[1], c[2], alpha)
            g.add_color_stop_rgba(1, c[0], c[1], c[2], alpha)
            return g
        # los puntos donde cambia la mezcla de value_color(): entre ellos es lineal en RGB,
        # igual que la interpolación del degradado
        stops = sorted({lo, hi} | {v for v in (warn - 15.0, warn, (warn + crit) / 2.0, crit)
                                   if lo < v < hi})
        for v in stops:
            c = value_color(v, warn, crit, accent, cb)
            g.add_color_stop_rgba((v - lo) / (hi - lo), c[0], c[1], c[2], alpha)
        return g
    return _cached(("heat", round(y, 1), round(h, 1), round(lo, 2), round(hi, 2), warn, crit,
                    tuple(accent or ()), cb, alpha), make)


def downsample(pts, x_of, max_points):
    """Como mucho max_points puntos: por cada columna de 2 píxeles se guardan el mínimo y el
    máximo (en su orden), así no se pierden los picos y no hay más puntos que píxeles."""
    if len(pts) <= max_points:
        return pts
    out, col, bucket = [], None, []

    def flush():
        if not bucket:
            return
        if len(bucket) <= 2:
            out.extend(bucket)
            return
        a = min(bucket, key=lambda p: p[1])
        b = max(bucket, key=lambda p: p[1])
        out.extend(sorted({a, b}, key=lambda p: p[0]))
    for p in pts:
        c = int(x_of(p[0]) // 2)
        if c != col:
            flush()
            bucket, col = [], c
        bucket.append(p)
    flush()
    return out


def draw_sparkline(cr, x, y, w, h, points, item, pal, accent, fill=True, line=2.0,
                   t0=None, t1=None, show_warn=True, vmin=None, vmax=None):
    """Gráfica con el color de cada tramo según su valor y zona roja sobre el aviso.
    Un solo trazo y un solo relleno, como mucho un punto por píxel de ancho y degradados en
    caché (en la 0.3.6 se llevaba el ~80 % de cada redibujado del widget «Cristal»)."""
    pts = [(t, v) for t, v in points if v is not None]
    if len(pts) < 2:
        return
    t0 = pts[0][0] if t0 is None else t0
    t1 = pts[-1][0] if t1 is None else t1
    if t1 <= t0:
        return
    vals = [v for _, v in pts]
    lo = min(vals) if vmin is None else vmin
    hi = max(vals) if vmax is None else vmax
    warn, crit = item.get("warn"), item.get("crit")
    if vmin is None:
        lo = min(lo, (warn or lo) - 20) if warn is not None else lo
        # margen mínimo según lo que se mide: con 5 fijos, un voltaje de 1,1-1,4 V o un
        # consumo de pocos vatios salían como una línea plana
        min_span = {"temp": 5.0, "percent": 5.0, "fan": 100.0, "freq": 100.0, "volt": 0.05,
                    "power": 1.0, "current": 0.1}.get(item.get("kind"), 1.0)
        span = max(hi - lo, min_span)
        lo -= span * 0.1
        hi += span * 0.1
    if hi <= lo:
        hi = lo + 1

    def X(t):
        return x + (t - t0) / (t1 - t0) * w

    def Y(v):
        return y + h - (v - lo) / (hi - lo) * h

    # zona de aviso
    if show_warn and warn is not None and lo < warn < hi:
        set_rgba(cr, RED, 0.08)
        cr.rectangle(x, y, w, Y(warn) - y)
        cr.fill()
        cr.set_dash([4, 4])
        cr.set_line_width(1)
        set_rgba(cr, (0.98, 0.45, 0.09), 0.6)
        cr.move_to(x, Y(warn))
        cr.line_to(x + w, Y(warn))
        cr.stroke()
        cr.set_dash([])
    if show_warn and crit is not None and lo < crit < hi:
        cr.set_dash([2, 4])
        cr.set_line_width(1)
        set_rgba(cr, RED, 0.7)
        cr.move_to(x, Y(crit))
        cr.line_to(x + w, Y(crit))
        cr.stroke()
        cr.set_dash([])

    pts = downsample(pts, X, max(2, int(w)))
    col_now = item_color(item, accent)
    cr.new_path()
    cr.move_to(X(pts[0][0]), Y(pts[0][1]))
    for t, v in pts[1:]:
        cr.line_to(X(t), Y(v))
    if fill:
        import cairo
        cr.save()
        cr.line_to(X(pts[-1][0]), y + h)
        cr.line_to(X(pts[0][0]), y + h)
        cr.close_path()

        def _fill():
            lg = cairo.LinearGradient(0, y, 0, y + h)
            lg.add_color_stop_rgba(0, col_now[0], col_now[1], col_now[2], 0.28)
            lg.add_color_stop_rgba(1, col_now[0], col_now[1], col_now[2], 0.0)
            return lg
        cr.set_source(_cached(("fill", round(y, 1), round(h, 1), col_now), _fill))
        cr.fill()
        cr.restore()
        cr.new_path()
        cr.move_to(X(pts[0][0]), Y(pts[0][1]))
        for t, v in pts[1:]:
            cr.line_to(X(t), Y(v))
    cr.set_line_width(line)
    cr.set_line_cap(1)
    cr.set_line_join(1)
    cr.set_source(heat_gradient(y, h, lo, hi, warn, crit, accent, item.get("cb", False)))
    cr.stroke()
    return lo, hi


# ------------------------------------------------------------------ ventilador

def draw_fan(cr, cx, cy, r, angle, color, alpha=1.0):
    """Hélice de 5 aspas que gira según las RPM."""
    cr.save()
    cr.translate(cx, cy)
    cr.rotate(angle)
    set_rgba(cr, color, alpha)
    for i in range(5):
        cr.save()
        cr.rotate(i * TAU / 5)
        cr.move_to(0, 0)
        cr.curve_to(r * 0.25, -r * 0.15, r * 0.9, -r * 0.35, r * 0.95, r * 0.05)
        cr.curve_to(r * 0.6, r * 0.25, r * 0.2, r * 0.15, 0, 0)
        cr.fill()
        cr.restore()
    set_rgba(cr, color, alpha)
    cr.arc(0, 0, r * 0.18, 0, TAU)
    cr.fill()
    cr.restore()


# ------------------------------------------------------------------ Cabina (instrumento de coche)

def draw_cockpit(cr, cx, cy, r, item, pal, accent, pulse=0.0, key=None):
    """Evolución del HUD: bisel, segmentos LED que se encienden en orden, zona roja que
    «respira» en aviso, aguja fina con brillo y cifras que ruedan."""
    a0 = math.radians(150)
    sweep = math.radians(240)
    n = 36
    # la esfera es siempre oscura (bisel propio): textos claros en cualquier tema
    pal = dict(pal, fg=(0.93, 0.95, 0.97), dim=(0.60, 0.64, 0.70))
    f = frac(item)
    col = item_color(item, accent)
    lo, hi = item.get("vmin", 0.0), item.get("vmax", 100.0)
    warn, crit = item.get("warn"), item.get("crit")
    fw = max(0, min(1, (warn - lo) / (hi - lo))) if warn is not None and hi > lo else 2
    fc = max(0, min(1, ((crit or hi) - lo) / (hi - lo))) if warn is not None and hi > lo else 2
    seg_in, seg_out = r * 0.74, r * 0.92

    def _static(c2, ox, oy):
        import cairo
        # bisel
        g = cairo.RadialGradient(ox, oy, r * 0.2, ox, oy, r * 1.08)
        g.add_color_stop_rgba(0, 0.10, 0.12, 0.16, 0.55)
        g.add_color_stop_rgba(0.85, 0.05, 0.06, 0.08, 0.55)
        g.add_color_stop_rgba(1, 0.0, 0.0, 0.0, 0.0)
        c2.set_source(g)
        c2.arc(ox, oy, r * 1.06, 0, TAU)
        c2.fill()
        c2.set_line_width(max(1.0, r * 0.02))
        lg = cairo.LinearGradient(ox - r, oy - r, ox + r, oy + r)
        lg.add_color_stop_rgba(0, 1, 1, 1, 0.22)
        lg.add_color_stop_rgba(1, 1, 1, 1, 0.03)
        c2.set_source(lg)
        c2.arc(ox, oy, r * 1.0, 0, TAU)
        c2.stroke()
        # segmentos LED apagados (los de la zona roja, rojo oscuro)
        c2.set_line_cap(0)
        for i in range(n):
            t = (i + 0.5) / n
            ang = a0 + sweep * t
            c2.set_line_width(max(1.5, r * 0.055))
            if t >= fc:
                set_rgba(c2, (0.35, 0.06, 0.06), 0.9)
            elif t >= fw:
                set_rgba(c2, (0.32, 0.18, 0.04), 0.8)
            else:
                set_rgba(c2, pal["dim"], 0.18)
            c2.move_to(ox + seg_in * math.cos(ang), oy + seg_in * math.sin(ang))
            c2.line_to(ox + seg_out * math.cos(ang), oy + seg_out * math.sin(ang))
            c2.stroke()
        # escala: mínimo y máximo
        for t, v in ((0.0, lo), (1.0, hi)):
            ang = a0 + sweep * t
            text(c2, "%d" % round(v), ox + r * 0.60 * math.cos(ang), oy + r * 0.60 * math.sin(ang),
                 r * 0.11, pal["dim"], tabular=True)

    cached_layer(cr, ("cockpit", round(r, 1), pal["dim"], lo, hi, warn, crit), cx, cy, r * 1.1, _static)

    # zona roja que respira cuando hay aviso o alarma
    st = status_tag(item)
    if st and st[2] != "normal" and fc <= 1:
        breath = 0.35 + 0.45 * (pulse if pulse > 0 else 0.6)
        cr.set_line_width(max(1.5, r * 0.04))
        set_rgba(cr, RED, breath)
        cr.new_path()
        cr.arc(cx, cy, r * 0.98, a0 + sweep * fc, a0 + sweep)
        cr.stroke()

    # LED encendidos en orden, con brillo (2 trazos de alfa decreciente)
    cr.set_line_cap(0)
    lit = int(f * n + 1e-6)
    for _glow, wmul, alpha in ((True, 0.13, 0.20), (False, 0.055, 1.0)):
        cr.set_line_width(max(1.5, r * wmul))
        for i in range(lit):
            t = (i + 0.5) / n
            ang = a0 + sweep * t
            set_rgba(cr, color_at(item, t, accent), alpha)
            cr.move_to(cx + seg_in * math.cos(ang), cy + seg_in * math.sin(ang))
            cr.line_to(cx + seg_out * math.cos(ang), cy + seg_out * math.sin(ang))
            cr.stroke()

    # aguja: un trazo corto por fuera de la cifra (no la tapa), con brillo
    ang = a0 + sweep * f
    for w, al in ((r * 0.07, 0.16), (r * 0.024, 0.95)):
        cr.set_line_width(w)
        cr.set_line_cap(1)
        set_rgba(cr, col, al)
        cr.move_to(cx + r * 0.50 * math.cos(ang), cy + r * 0.50 * math.sin(ang))
        cr.line_to(cx + r * 0.88 * math.cos(ang), cy + r * 0.88 * math.sin(ang))
        cr.stroke()

    # cifra en el centro (rueda al cambiar), unidad, nombre y estado debajo
    val, unit = fmt(dict(item, anim=item.get("value")))
    vw, _vh = odometer(cr, key or item.get("id", ""), val, cx, cy - r * 0.02, r * 0.36, pal["fg"],
                       Pango.Weight.BOLD, FONT_MONO)
    if unit:
        text(cr, unit, cx + vw / 2 + 2, cy - r * 0.14, r * 0.12, pal["dim"], align="left")
    text(cr, item.get("label", ""), cx, cy + r * 0.30, r * 0.13, pal["dim"], Pango.Weight.MEDIUM,
         max_width=r * 0.95)
    if st:
        text(cr, "%s %s" % (st[0], st[1]), cx, cy + r * 0.62, r * 0.11, col, Pango.Weight.SEMIBOLD)
    if item.get("alarm") and pulse > 0:
        cr.set_line_width(max(2.0, r * 0.04))
        set_rgba(cr, RED, 0.2 + 0.55 * pulse)
        cr.new_path()
        cr.arc(cx, cy, r * 1.08, 0, TAU)
        cr.stroke()


# ------------------------------------------------------------------ OLED mínimo

def draw_oled(cr, cx, cy, r, item, pal, accent, pulse=0.0):
    """Negro puro, trazo fino, cifra grande. Color solo cuando hay algo que contar."""
    st = status_tag(item)
    f = frac(item)
    white = (0.93, 0.95, 0.97)
    col = item_color(item, accent) if (st and st[2] != "normal") else white
    cr.set_line_width(max(1.0, r * 0.035))
    cr.set_line_cap(1)
    set_rgba(cr, white, 0.12)
    cr.new_path()
    cr.arc(cx, cy, r, -math.pi / 2, -math.pi / 2 + TAU)
    cr.stroke()
    if f > 0.001:
        set_rgba(cr, col, 0.95)
        cr.new_path()
        cr.arc(cx, cy, r, -math.pi / 2, -math.pi / 2 + TAU * f)
        cr.stroke()
    val, unit = fmt(item)
    text(cr, val, cx, cy - r * 0.04, r * 0.62, col if col != white else white, Pango.Weight.LIGHT,
         font=FONT_NUM, tabular=True)
    small = unit if unit not in ("°C", "°F") else unit
    text(cr, (small + "  " + item.get("label", "")).strip(), cx, cy + r * 0.48, r * 0.15,
         (0.6, 0.62, 0.66), max_width=r * 1.7)
    if st and st[2] != "normal":
        text(cr, "%s %s" % (st[0], st[1]), cx, cy + r + r * 0.25, r * 0.15, col,
             Pango.Weight.SEMIBOLD)
    if item.get("alarm") and pulse > 0:
        set_rgba(cr, RED, 0.15 + 0.5 * pulse)
        cr.set_line_width(max(1.0, r * 0.03))
        cr.new_path()
        cr.arc(cx, cy, r * 1.12, 0, TAU)
        cr.stroke()


# ------------------------------------------------------------------ indicador del panel

def draw_panel_gauge(cr, cx, cy, r, f, col, label, track=(0.55, 0.57, 0.6), dark_text=None):
    """Indicador pequeño tipo cuentakilómetros (240°) para el panel: pista tenue, arco con el
    color del valor y la cifra dentro. Lo usan el icono de bandeja y la vista previa del
    asistente; el applet de Cinnamon y la extensión de GNOME dibujan lo mismo en JS."""
    a0 = math.radians(150)
    sweep = math.radians(240)
    lw = max(2.0, r * 0.22)
    cr.set_line_cap(1)
    cr.set_line_width(lw)
    # pista gris medio: se ve sobre paneles claros y oscuros
    set_rgba(cr, track, 0.45)
    cr.new_path()
    cr.arc(cx, cy, r, a0, a0 + sweep)
    cr.stroke()
    if f > 0.001:
        set_rgba(cr, col)
        cr.new_path()
        cr.arc(cx, cy, r, a0, a0 + sweep * max(0.0, min(1.0, f)))
        cr.stroke()
    size = r * (0.95 if len(label) <= 3 else 0.75)
    if dark_text is None:
        # sombra para que la cifra blanca se lea también en paneles claros
        sh = max(1.0, r * 0.05)
        text(cr, label, cx + sh, cy + sh, size, (0, 0, 0), Pango.Weight.BOLD, font=FONT_NUM,
             tabular=True, alpha=0.6)
    text(cr, label, cx, cy, size, dark_text or (1, 1, 1), Pango.Weight.BOLD, font=FONT_NUM,
         tabular=True)
    cr.new_path()


# ------------------------------------------------------------------ grano (cristal)

_GRAIN = None


def grain_pattern():
    """Textura de grano finísima (evita las bandas de color en los degradados)."""
    global _GRAIN
    if _GRAIN is None:
        import cairo
        import random
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 64, 64)
        c = cairo.Context(surf)
        rnd = random.Random(7)
        for _i in range(700):
            c.set_source_rgba(1, 1, 1, rnd.uniform(0.02, 0.06))
            c.rectangle(rnd.randrange(64), rnd.randrange(64), 1, 1)
            c.fill()
        pat = cairo.SurfacePattern(surf)
        pat.set_extend(cairo.EXTEND_REPEAT)
        _GRAIN = pat
    return _GRAIN
