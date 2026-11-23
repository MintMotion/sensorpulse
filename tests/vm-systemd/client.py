"""Cliente como la interfaz (Store) del paquete instalado: conecta y deja correr el bucle."""
import sys
import time
sys.path.insert(0, "/usr/share/sensorpulse")
from gi.repository import GLib  # noqa: E402
from sensorpulse.ui.store import Store  # noqa: E402
st = Store()
print("CLIENT connect=%s" % st.connect(), flush=True)
ctx = GLib.MainContext.default()
end = time.time() + float(sys.argv[1])
marks = [float(x) for x in sys.argv[2:]]
t0 = time.time()
while time.time() < end:
    while ctx.pending():
        ctx.iteration(False)
    if marks and time.time() - t0 >= marks[0]:
        marks.pop(0)
        print("CLIENT t=%.0f stale=%s" % (time.time() - t0, st.stale), flush=True)
    time.sleep(0.05)
print("CLIENT end stale=%s devices=%d" % (st.stale, len(st.snapshot.get("devices", []))), flush=True)
