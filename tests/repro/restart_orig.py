import os, sys, time, signal, subprocess, tempfile
ROOT = sys.argv[1]
d = tempfile.mkdtemp()
subprocess.check_call([sys.executable, os.path.join(ROOT, "tools", "fake_sysfs.py"), d + "/sys", "desktop"], stdout=subprocess.DEVNULL)
os.environ.update(SENSORPULSE_SYSROOT=d + "/sys", XDG_CONFIG_HOME=d, XDG_STATE_HOME=d, PYTHONPATH=ROOT)
sys.path.insert(0, ROOT)
from gi.repository import Gio, GLib
from sensorpulse.ui.store import Store
p = subprocess.Popen([sys.executable, "-m", "sensorpulse.service"], cwd=ROOT)
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
N = ("io.github.mintmotion.SensorPulse", "/io/github/mintmotion/SensorPulse", "io.github.mintmotion.SensorPulse")
for i in range(50):
    try:
        bus.call_sync(*N, "GetVersion", None, None, 0, 500, None); break
    except GLib.Error: time.sleep(0.2)
os.kill(p.pid, signal.SIGSTOP)
st = Store(); st.client = None
t = time.time(); r = st.restart_service(); print("restart_service() -> %s en %.1f s" % (r, time.time() - t))
try:
    bus.call_sync(*N, "GetSnapshot", None, None, 0, 3000, None); print("el servicio responde")
except GLib.Error as e:
    print("el servicio sigue sin responder:", e.message)
os.kill(p.pid, signal.SIGKILL)
