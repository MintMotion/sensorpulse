"""C3: con el demonio colgado, ¿cuánto tarda el servicio en responder GetSnapshot tras un aviso?"""
import os, sys, time, subprocess, tempfile
from gi.repository import Gio, GLib
ROOT = sys.argv[1]
d = tempfile.mkdtemp()
subprocess.check_call([sys.executable, os.path.join(ROOT, "tools", "fake_sysfs.py"), d + "/sys", "desktop"], stdout=subprocess.DEVNULL)
env = dict(os.environ, SENSORPULSE_SYSROOT=d + "/sys", XDG_CONFIG_HOME=d, XDG_STATE_HOME=d, PYTHONPATH=ROOT)
nd = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "hung_notifd.py")])
time.sleep(1)
svc = subprocess.Popen(sys.argv[2:] if len(sys.argv) > 2 else [sys.executable, "-m", "sensorpulse.service"], env=env, cwd=ROOT)
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
N = ("io.github.mintmotion.SensorPulse", "/io/github/mintmotion/SensorPulse", "io.github.mintmotion.SensorPulse")
for i in range(100):
    try:
        bus.call_sync(*N, "GetVersion", None, None, 0, 1000, None); break
    except GLib.Error:
        time.sleep(0.2)
def lat():
    t = time.time()
    try:
        bus.call_sync(*N, "GetSnapshot", None, None, 0, 60000, None)
    except GLib.Error as e:
        return "error %s" % e
    return "%.2f s" % (time.time() - t)
print("GetSnapshot normal:", lat())
bus.call(*N, "TestAlarm", GLib.Variant("(s)", ("",)), None, 0, 60000, None, None, None)
time.sleep(0.3)
print("GetSnapshot tras un aviso con el demonio colgado:", lat())
svc.terminate(); nd.terminate()
