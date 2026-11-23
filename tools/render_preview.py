#!/usr/bin/env python3
"""Genera PNG de vista previa de los estilos sin servicio ni pantalla (para pruebas y docs).

Uso: SENSORPULSE_SYSROOT=/tmp/fs_d python3 tools/render_preview.py SALIDA_DIR [hot]
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cairo  # noqa: E402

from sensorpulse.core.engine import Engine  # noqa: E402
from sensorpulse.ui.store import Store  # noqa: E402
from sensorpulse.ui.floating import Renderer, STYLES, SIZES  # noqa: E402
from sensorpulse.ui.theme import PALETTES, hex_to_rgb  # noqa: E402


def build_store(hot=False):
    e = Engine()
    e.poll(force=True)
    if hot:  # simular carga: CPU caliente y GPU crítica
        for c in e.channels.values():
            if c.kind == "temp" and c.primary and "cpu" in c.device_id.lower() or "k10temp" in c.id:
                c.value = 88.0
            if c.id.endswith("temp1") and "amdgpu" in c.id:
                c.value = 101.0
            if c.id.endswith("/busy"):
                c.value = 97.0
        for d in e.devices:
            from sensorpulse.core.engine import compute_state
            for c in d.channels:
                c.state = compute_state(c)
    t = time.time()
    s = Store()
    s.animate = False   # sin barrido inicial: valores finales
    snap = e.snapshot()
    snap["alarm_channels"] = [c.id for c in e.channels.values() if c.state == "critical"]
    snap["state"] = "critical" if snap["alarm_channels"] else snap["state"]
    s._anim_source = 1  # no arrancar el temporizador de GLib
    s._on_update(snap)
    # historia sintética para las mini-gráficas
    import math
    for cid, q in s.local_hist.items():
        q.clear()
        c = e.channels[cid]
        base = c.value or 0
        for i in range(120):
            q.append((t - 120 + i, base * (0.85 + 0.15 * math.sin(i / 9.0)) + (i / 120.0) * base * 0.1))
    s.pulse = 1.0
    return s


def main():
    out = sys.argv[1]
    hot = len(sys.argv) > 2 and sys.argv[2] == "hot"
    os.makedirs(out, exist_ok=True)
    store = build_store(hot)
    r = Renderer(store)
    for theme in ("dark", "light"):
        pal = PALETTES[theme]
        for style in STYLES:
            items = [(store.item(c, d), list(store.local_hist.get(c["id"], ())))
                     for c, d in store.featured("floating", 6)]
            w, h = r.size(style, len(items), SIZES["medium"])
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
            cr = cairo.Context(surf)
            # fondo de escritorio simulado para ver la transparencia
            g = cairo.LinearGradient(0, 0, w, h)
            if theme == "dark":
                g.add_color_stop_rgb(0, 0.05, 0.08, 0.16)
                g.add_color_stop_rgb(1, 0.18, 0.06, 0.22)
            else:
                g.add_color_stop_rgb(0, 0.80, 0.86, 0.93)
                g.add_color_stop_rgb(1, 0.93, 0.87, 0.80)
            cr.set_source(g)
            cr.paint()
            r.draw(cr, w, h, items, style, SIZES["medium"], pal, hex_to_rgb("#22d3ee"),
                   background=False, header=True, pulse=store.pulse)
            name = os.path.join(out, "%s_%s%s.png" % (style, theme, "_hot" if hot else ""))
            surf.write_to_png(name)
            print(name, w, h)


if __name__ == "__main__":
    main()
