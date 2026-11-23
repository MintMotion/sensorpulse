#!/usr/bin/env python3
"""Mide el coste de dibujo del widget flotante con el historial lleno (10 min = 300 puntos).

Ejecutar con el servicio en marcha (sysfs falso) y una pantalla (Xvfb):
  SENSORPULSE_SYSROOT=/tmp/fake ... python3 tools/bench_draw.py [estilo] [segundos]

Informa de: redibujados por segundo del flotante, milisegundos de CPU por redibujado y la
CPU estimada (porcentaje de un núcleo) que se va en dibujar. También mide el tiempo de CPU
real del proceso durante la prueba (time.process_time).
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gi.repository import GLib  # noqa: E402

from sensorpulse.app import App  # noqa: E402

STYLE = sys.argv[1] if len(sys.argv) > 1 else "glass"
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0

app = App()
stats = {"draws": 0, "cpu": 0.0}


def prefill():
    st = app.store
    from collections import deque
    import math
    now = time.time()
    for d in st.snapshot.get("devices", []):
        for c in d["channels"]:
            v = c.get("value")
            if v is None:
                continue
            q = deque(maxlen=300)
            for i in range(300):
                q.append((now - (300 - i) * 2, v * (1 + 0.08 * math.sin(i / 9.0))))
            st.local_hist[c["id"]] = q


def start():
    app.store.cfg["style"] = STYLE
    app.store.cfg["display"]["floating"] = True
    prefill()
    app.set_floating(True, save=False)
    fw = app.floating
    orig = fw.on_draw

    def wrapped(w, cr):
        t0 = time.process_time()
        r = orig(w, cr)
        stats["cpu"] += time.process_time() - t0
        stats["draws"] += 1
        return r
    fw.disconnect_by_func(orig) if hasattr(fw, "disconnect_by_func") else None
    fw.connect("draw", wrapped)
    fw.refresh()
    GLib.timeout_add(3000, begin)
    return False


def begin():
    stats["draws"], stats["cpu"] = 0, 0.0
    stats["t0"], stats["p0"] = time.monotonic(), time.process_time()
    GLib.timeout_add(int(SECS * 1000), finish)
    return False


def finish():
    el = time.monotonic() - stats["t0"]
    proc = time.process_time() - stats["p0"]
    d = stats["draws"]
    print("estilo=%s  redibujados/s=%.2f  ms/redibujado=%.2f  CPU dibujo=%.2f%% de un núcleo  "
          "CPU total proceso=%.2f%% de un núcleo" % (
              STYLE, d / el, 1000 * stats["cpu"] / max(1, d), 100 * stats["cpu"] / el, 100 * proc / el))
    sys.stdout.flush()
    app.quit()
    return False


GLib.timeout_add(2500, start)
app.hold()
app.run(["sensorpulse", "--background"])
