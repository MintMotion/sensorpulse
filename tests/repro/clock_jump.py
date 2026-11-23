"""C1/C2: salto atras del reloj de pared con el Service real (sysfs falso)."""
import os, sys, time, subprocess, tempfile, json
ROOT = sys.argv[1]
d = tempfile.mkdtemp()
subprocess.check_call([sys.executable, os.path.join(ROOT, "tools", "fake_sysfs.py"), d + "/sys", "desktop"], stdout=subprocess.DEVNULL)
os.environ.update(SENSORPULSE_SYSROOT=d + "/sys", XDG_CONFIG_HOME=d, XDG_STATE_HOME=d, XDG_CACHE_HOME=d)
sys.path.insert(0, ROOT)
real_time = time.time
offset = [0.0]
time.time = lambda: real_time() + offset[0]
from sensorpulse import service
svc = service.Service()
svc.notifier.notify = None
svc.tick(force=True)
cpu = next(c for c in svc.engine.channels.values() if c.kind == "temp" and c.crit)
def temp_file(c):
    return c.path
path = temp_file(cpu)
print("canal:", cpu.id, "crit", cpu.crit, "fichero", path)
def setv(v):
    with open(path, "w") as f: f.write("%d\n" % int(v * 1000))
setv(50)
for i in range(3):
    time.sleep(0.6); svc.tick()
print("antes del salto: valor", cpu.value)
offset[0] = -3600   # NTP corrige el reloj una hora hacia atras
setv(cpu.crit + 15)  # se calienta por encima del critico
t0 = real_time()
fired = []
orig = svc._handle_event
svc._handle_event = lambda ev: (fired.append(ev["type"] + ":" + ev["rule"]), orig(ev))
while real_time() - t0 < 25:
    time.sleep(0.5); svc.tick()
snap = json.loads(svc._snapshot_cache)
v = next(c["value"] for dd in snap["devices"] for c in dd["channels"] if c["id"] == cpu.id)
print("25 s despues del salto: valor publicado", v, "(real %.0f)" % (cpu.crit + 15))
print("snapshot.time avanza:", round(snap["time"] - (real_time() - 3600), 1), "s de desfase")
print("alarmas disparadas:", [f for f in fired if f.startswith("fired")])
