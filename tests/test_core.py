"""Pruebas del núcleo con árboles sysfs falsos. Ejecutar: python3 -m pytest tests/ (o python3 tests/test_core.py)"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


def make_tree(kind):
    d = tempfile.mkdtemp(prefix="sp-%s-" % kind)
    subprocess.check_call([sys.executable, os.path.join(ROOT, "tools", "fake_sysfs.py"), d, kind],
                          stdout=subprocess.DEVNULL)
    return d


def with_root(kind):
    d = make_tree(kind)
    os.environ["SENSORPULSE_SYSROOT"] = d
    import importlib
    from sensorpulse.core import sysfs
    importlib.reload(sysfs)
    for m in ("discovery", "nvml", "engine", "alarms"):
        mod = sys.modules.get("sensorpulse.core." + m)
        if mod:
            importlib.reload(mod)
    return d


def test_desktop_discovery():
    d = with_root("desktop")
    from sensorpulse.core.engine import Engine
    e = Engine()
    e.poll(force=True)
    classes = [x.cls for x in e.devices]
    assert classes[0] == "cpu" and "gpu" in classes and "fans" in classes
    cpu = e.devices[0]
    prim = next(c for c in cpu.channels if c.primary)
    assert prim.label == "CPU" and prim.value == 54.2  # Tctl sin Tdie → «CPU»
    gpu = next(x for x in e.devices if x.cls == "gpu")
    hot = next(c for c in gpu.channels if c.raw_label == "junction")
    assert hot.crit == 110.0 and hot.warn == 100.0
    nvme = next(x for x in e.devices if x.driver == "nvme")
    assert nvme.model.startswith("WD_BLACK") and nvme.interval == 5.0
    hdd = next(x for x in e.devices if x.driver == "drivetemp")
    assert hdd.interval == 1800.0  # no impedir que el disco se apague
    tj = next(c for c in cpu.channels if c.primary)
    assert tj.tjmax == 95.0 and tj.warn == 85.0 and tj.crit == 98.0  # Ryzen 7000
    fans = next(x for x in e.devices if x.cls == "fans")
    f3 = next(c for c in fans.channels if c.id.endswith("fan3"))
    assert f3.advanced  # cabecera sin ventilador: oculta por defecto
    shutil.rmtree(d)


def test_sleeping_gpu_not_read():
    d = with_root("desktop")
    from sensorpulse.core.engine import Engine
    gpu_dev = os.path.join(d, "sys/devices/pci0000:00/0000:00:01.1/0000:03:00.0")
    with open(os.path.join(gpu_dev, "power/runtime_status"), "w") as f:
        f.write("suspended\n")
    # si se leyera, daría error: lo convertimos en directorio ilegible
    e = Engine()
    e.poll(force=True)
    gpu = next(x for x in e.devices if x.cls == "gpu")
    assert gpu.asleep and gpu.state == "sleep"
    assert all(c.value is None for c in gpu.channels)
    for c in gpu.channels:
        assert c.path not in e.fds._fds  # nunca se abrió ningún fichero de la GPU dormida
    shutil.rmtree(d)


def test_laptop_battery():
    d = with_root("laptop")
    from sensorpulse.core.engine import Engine
    e = Engine()
    e.poll(force=True)
    bat = next(x for x in e.devices if x.cls == "battery")
    vals = {c.id.split("/")[-1]: c for c in bat.channels}
    assert vals["capacity"].value == 86 and vals["health"].value == 92.0
    assert vals["power"].value == 9.3 and vals["status"].text == "Discharging"
    cpu = e.devices[0]
    assert next(c for c in cpu.channels if c.primary).label == "CPU package"
    shutil.rmtree(d)


def test_alarm_delay_hysteresis_mute():
    with_root("desktop")
    from sensorpulse.core.model import Channel
    from sensorpulse.core.alarms import AlarmEngine, new_rule
    ch = Channel(id="x/temp1", device_id="x", kind="temp", label="CPU", warn=80, crit=95)
    chans = {ch.id: ch}
    a = AlarmEngine({"enabled": True, "hysteresis": 5, "delay": 0, "notify": True, "sound": True})
    r = new_rule("x/temp1", 75, delay=10, hysteresis=3)
    a.configure(a.settings, [r])
    t = 1000.0
    ch.value = 76
    assert a.evaluate(chans, t) == []                 # aún dentro del retardo
    ev = a.evaluate(chans, t + 11)
    assert [e["type"] for e in ev] == ["fired"] and ev[0]["sound"]
    ch.value = 74
    assert a.evaluate(chans, t + 12) == []            # histéresis: 74 > 75-3
    ch.value = 71
    assert [e["type"] for e in a.evaluate(chans, t + 13)] == ["cleared"]
    # alarma automática al llegar al crítico
    ch.value = 96
    ev = a.evaluate(chans, t + 20)
    assert any(e["rule"].startswith("auto:crit") for e in ev)
    assert "x/temp1" in a.active_channels()
    a.mute(10)
    ch.value = 50
    a.evaluate(chans, t + 30)
    ch.value = 97
    ev = a.evaluate(chans, time.time() + 1)
    # silenciadas: nada de sonido; la notificación solo se conserva en las críticas
    assert ev and all(not e["sound"] and e["notify"] == (e["level"] == "critical") for e in ev)


def test_laptop_throttle_slow_drivers():
    d = with_root("laptop")
    from sensorpulse.core.engine import Engine
    e = Engine()
    e.poll(now=1000.0, force=True)
    cpu = e.devices[0]
    pkg = next(c for c in cpu.channels if c.primary)
    assert pkg.tjmax == 100.0 and pkg.crit == 103.0     # Intel: TjMax del propio driver
    assert not pkg.throttling
    f = os.path.join(d, "sys/devices/system/cpu/cpu2/thermal_throttle/package_throttle_count")
    with open(f, "w") as fh:
        fh.write("9\n")
    e.poll(now=1003.0, force=True)
    assert pkg.throttling                               # el contador ha subido
    e.poll(now=1030.0, force=True)
    assert not pkg.throttling                           # ya no sube
    tp = [c for c in e.channels.values() if c.device_id.startswith("thinkpad")]
    assert tp and all(c.slow and c.interval == 10.0 for c in tp)
    # canal no visible: el ventilador no se lee; la temperatura oculta sí, despacio (alarma)
    e.set_wanted({pkg.id})
    e.poll(now=1040.0, force=True)
    assert all(c.value is None for c in tp if c.kind == "fan")
    assert all(c.value is not None for c in tp if c.kind == "temp")
    shutil.rmtree(d)


def test_smart_alarms():
    with_root("desktop")
    from sensorpulse.core.model import Channel, Device
    from sensorpulse.core.alarms import AlarmEngine
    cpu = Device(id="k10", cls="cpu", name="CPU")
    t = Channel(id="k10/temp1", device_id="k10", kind="temp", label="CPU", primary=True,
                warn=85, crit=98, tjmax=95)
    load = Channel(id="cpu@proc/load", device_id="k10", kind="percent", label="load")
    fans = Device(id="fans@all", cls="fans", name="Fans")
    fan = Channel(id="fans@all/fan1", device_id="fans@all", kind="fan", label="Fan 1")
    cpu.channels = [t, load]
    fans.channels = [fan]
    chans = {c.id: c for c in (t, load, fan)}
    peaks = {"fans@all/fan1": 1200}
    a = AlarmEngine({"enabled": True, "hysteresis": 5, "delay": 10, "notify": True,
                     "sound": True})
    ev = lambda now: a.evaluate(chans, now, [cpu, fans], lambda cid: peaks.get(cid, 0))
    # renderizando a 95 °C con 100 % de carga: normal en un Ryzen 7000, sin alarmas
    t.value, load.value, fan.value = 95.0, 100.0, 1500
    assert ev(0) == [] and ev(200) == []
    # 80 °C sin carga durante 2 min: aviso «caliente sin trabajar» (sin sonido)
    t.value, load.value = 80.0, 5.0
    assert ev(300) == []
    out = ev(421)
    assert [(e["kind"], e["level"], e["sound"]) for e in out] == [("idle", "hot", False)]
    # en el límite y sin carga: crítico con sonido
    t.value = 95.0
    ev(430)
    out = ev(461)
    assert any(e["kind"] == "idle" and e["level"] == "critical" and e["sound"] for e in out)
    # ventilador que giraba y se para con la CPU caliente
    fan.value = 0
    ev(470)
    out = ev(501)
    assert any(e["kind"] == "fanstop" for e in out)
    # bajada de velocidad por calor
    t.throttling = True
    ev(510)
    out = ev(541)
    assert any(e["kind"] == "throttle" for e in out)
    lv = a.active_levels()
    assert lv["k10/temp1"] == "critical" and lv["fans@all/fan1"] == "hot"


def test_history_db():
    import sensorpulse.core.historydb as H
    d = tempfile.mkdtemp()
    db = H.HistoryDB(os.path.join(d, "h.sqlite"))
    t0 = 1_700_000_000
    for k in range(0, 7200, 2):
        db.add(t0 + k, [("a", 50.0 + (k // 600))])
    db.add_alarm({"time": t0, "kind": "idle"})
    db.close()
    db = H.HistoryDB(os.path.join(d, "h.sqlite"))
    real = H.time.time
    H.time.time = lambda: t0 + 7200
    try:
        q = db.query("a", 3600)
        assert 55 <= len(q) <= 61 and q[-1][3] == 61.0
        assert db.alarms()[0]["kind"] == "idle"
    finally:
        H.time.time = real
    shutil.rmtree(d)


def test_report_is_anonymous():
    with_root("laptop")
    from sensorpulse.core.engine import Engine
    from sensorpulse.report import build
    import socket
    e = Engine()
    e.poll(force=True)
    txt = build(e.snapshot())
    assert "coretemp" in txt and "thermal_throttle: yes" in txt
    host = socket.gethostname()
    assert len(host) < 4 or host not in txt
    assert os.environ.get("USER", "neo-nobody") not in txt


def test_tjmax_table():
    """F4: portátiles Ryzen 100-105 °C, Ryzen 5000 de sobremesa 90/95 °C, X3D 7000 89 °C."""
    from sensorpulse.core.tjmax import amd_tjmax
    cases = {
        "AMD Ryzen 7 7840HS w/ Radeon 780M Graphics": 100.0,
        "AMD Ryzen 7 5800H with Radeon Graphics": 105.0,
        "AMD Ryzen AI 9 HX 370 w/ Radeon 890M": 100.0,
        "AMD Ryzen 9 5950X 16-Core Processor": 90.0,
        "AMD Ryzen 5 5600X 6-Core Processor": 95.0,
        "AMD Ryzen 7 7800X3D 8-Core Processor": 89.0,
        "AMD Ryzen 7 9850X3D 8-Core Processor": 95.0,
        "AMD Ryzen 7 7735HS with Radeon Graphics": 95.0,
        "AMD Ryzen 7 2700X Eight-Core Processor": 85.0,
    }
    for model, tj in cases.items():
        assert amd_tjmax(model)[0] == tj, model
    assert amd_tjmax("AMD Ryzen 3 3100 4-Core Processor")[0] is None   # sin dato oficial


def test_tjmax_official_csv():
    """Tabla oficial de amd.com: prioridad sobre las reglas, límites de palabra y sin inventar."""
    from sensorpulse.core.tjmax import amd_tjmax
    cases = {
        "AMD Ryzen 5 5600 6-Core Processor": 90.0,          # la regla de familia daría 95
        "AMD Ryzen 7 5700X 8-Core Processor": 90.0,
        "AMD Ryzen 7 PRO 2700X Eight-Core Processor": 95.0, # el 2700X normal es 85
        "AMD Ryzen 3 3200GE w/ Radeon Vega Graphics": 95.0,
        "AMD FX(tm)-8350 Eight-Core Processor": 61.0,
        "AMD Ryzen 9 PRO 6950HS w/ Radeon 680M Graphics": 94.99,
        "AMD Ryzen Threadripper PRO 3995WX 64-Cores": 90.0,
    }
    for model, tj in cases.items():
        v, origin = amd_tjmax(model)
        assert (v, origin) == (tj, "tabla"), model
    # Athlon antiguo sin fila en la tabla: no se le aplica la regla de los Athlon Zen
    assert amd_tjmax("AMD Athlon(tm) II X2 250 Processor")[0] is None


def test_no_forced_disk_reads():
    """F3: buscar sensores (y volver a evaluar) no debe volver a leer el disco SATA."""
    with_root("desktop")
    from sensorpulse.core.engine import Engine
    e = Engine()
    hdd = next(x for x in e.devices if x.driver == "drivetemp")
    path = hdd.channels[0].path
    reads = []
    orig = e.fds.read_int

    def spy(p, *a):
        if p == path:
            reads.append(p)
        return orig(p, *a)
    e.fds.read_int = spy
    e.poll(now=1000.0, force=True)
    assert len(reads) == 1
    e.rescan()
    e.fds.read_int = spy
    for t in (1001.0, 1003.0, 1010.0, 1500.0):
        e.poll(now=t)
    assert len(reads) == 1, reads   # siguiente lectura: dentro de 30 min


def test_single_thread_not_idle():
    """F8: un hilo al 100 % en una CPU de 32 hilos no es «sin trabajar»."""
    with_root("desktop")
    from sensorpulse.core.engine import Engine
    from sensorpulse.core.model import Channel, Device
    from sensorpulse.core.alarms import AlarmEngine
    e = Engine()

    def stat(busy, tot):
        # contadores acumulados: el núcleo 5 pasa «busy» ticks ocupado en el intervalo
        lines = ["cpu  %d 0 0 %d 0 0 0 0" % (busy, 32 * tot - busy)]
        for i in range(32):
            b = busy if i == 5 else 0
            lines.append("cpu%d %d 0 0 %d 0 0 0 0" % (i, b, tot - b))
        return lines
    e._core_loads(stat(0, 1000))
    e._core_loads(stat(1000, 2000))
    assert e.core_top >= 50.0          # el núcleo ocupado cuenta
    cpu = Device(id="k10", cls="cpu", name="CPU", model="AMD Ryzen 9 7950X")
    t = Channel(id="k10/t", device_id="k10", kind="temp", label="CPU", primary=True,
                warn=85, crit=98, tjmax=95, value=82.0)
    load = Channel(id="cpu@proc/load", device_id="k10", kind="percent", label="l", value=3.1)
    cm = Channel(id="cpu@proc/coremax", device_id="k10", kind="percent", label="c",
                 value=e.core_top)
    cpu.channels = [t, load, cm]
    a = AlarmEngine({"enabled": True, "delay": 10})
    chans = {c.id: c for c in cpu.channels}
    ev = []
    for k in range(0, 400, 2):
        ev += a.evaluate(chans, 1000.0 + k, [cpu], lambda cid: 0)
    assert not any(x["kind"] == "idle" for x in ev)


def test_throttle_critical_and_cooldown():
    """X1 y F12: crítico solo en el límite Y frenando; avisos de freno como mucho cada 30 min."""
    with_root("desktop")
    from sensorpulse.core.model import Channel, Device
    from sensorpulse.core.alarms import AlarmEngine
    cpu = Device(id="c", cls="cpu", name="CPU")
    t = Channel(id="c/t", device_id="c", kind="temp", label="CPU", primary=True,
                warn=90, crit=103, tjmax=100, value=100.0)
    cm = Channel(id="cpu@proc/coremax", device_id="c", kind="percent", label="c", value=100.0)
    cpu.channels = [t, cm]
    chans = {c.id: c for c in cpu.channels}
    a = AlarmEngine({"enabled": True, "delay": 10, "sound": True, "notify": True})
    ev = lambda now: a.evaluate(chans, now, [cpu], lambda cid: 0)
    # a 100 °C con carga pero sin frenar: nada (renderizado normal en el límite)
    assert ev(0) == [] and ev(120) == []
    t.throttling = True
    ev(130)
    out = ev(161) + ev(200)
    assert any(e["kind"] == "throttle" for e in out)
    assert any(e["kind"] == "throttlecrit" and e["sound"] for e in out)
    # deja de frenar y vuelve a frenar a los 5 min: el aviso no críticos se calla (30 min)
    t.throttling = False
    t.value = 80.0
    ev(210)
    t.throttling = True
    t.value = 95.0
    ev(500)
    out = ev(531)
    thr = [e for e in out if e["kind"] == "throttle" and e["type"] == "fired"]
    assert thr and not thr[0]["notify"]


def test_mute_end_reminder():
    """F13: al acabar el silencio se vuelve a anunciar la alarma crítica activa."""
    with_root("desktop")
    from sensorpulse.core.model import Channel
    from sensorpulse.core.alarms import AlarmEngine
    import sensorpulse.core.alarms as A
    ch = Channel(id="x/t", device_id="x", kind="temp", label="CPU", warn=80, crit=95, value=97.0)
    chans = {ch.id: ch}
    a = AlarmEngine({"enabled": True, "delay": 0, "notify": True, "sound": True})
    # 0.3.6: el silencio se mide con el reloj monótono (un salto del reloj de pared no lo
    # alarga ni lo acorta): se simulan los dos relojes
    real, real_m = A.time.time, A.time.monotonic
    try:
        A.time.time = lambda: 1000.0
        A.time.monotonic = lambda: 50.0
        a.mute(10)
        a.evaluate(chans, 1000.0)
        A.time.time = lambda: 1000.0 + 601
        A.time.monotonic = lambda: 50.0 + 601
        out = a.evaluate(chans, 1601.0)
    finally:
        A.time.time, A.time.monotonic = real, real_m
    assert any(e.get("reminder") and e["notify"] for e in out)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)


def test_tctl_offset_tdie_primary():
    """Fallo 1 (revisión 0.3.1): con Tctl desplazado (TR 1950X +27 °C) la referencia es Tdie
    y en reposo no salta ninguna alarma."""
    d = with_root("tr1950x")
    from sensorpulse.core.engine import Engine
    from sensorpulse.core.alarms import AlarmEngine
    e = Engine()
    e.poll(force=True)
    cpu = e.devices[0]
    prim = next(c for c in cpu.channels if c.primary and c.kind == "temp")
    tctl = next(c for c in cpu.channels if c.raw_label == "Tctl")
    assert prim.raw_label == "Tdie" and prim.value == 40.0 and prim.tjmax == 68.0
    assert tctl.advanced and not tctl.primary and tctl.crit is None and tctl.tjmax is None
    chans = {c.id: c for dv in e.devices for c in dv.channels}
    a = AlarmEngine({"enabled": True, "hysteresis": 5, "delay": 10, "notify": True, "sound": True})
    for c in chans.values():
        if c.kind == "percent" and c.id.startswith("cpu@proc"):
            c.value = 2.0
    evs = []
    for t in range(0, 300, 2):
        evs += a.evaluate(chans, t, e.devices, lambda cid: 0)
    assert evs == [], evs
    shutil.rmtree(d)


def test_discovery_does_not_touch_sata_disk():
    """Fallo 3 (revisión 0.3.1): descubrir sensores no lee temp*_max/_crit de drivetemp
    (cada lectura es una orden SCT que despierta el disco) y «status» sin servicio ni lo mira."""
    d = with_root("desktop")
    from sensorpulse.core import discovery, sysfs
    hw = [h for h in os.listdir(os.path.join(d, "sys/class/hwmon"))
          if open(os.path.join(d, "sys/class/hwmon", h, "name")).read().strip() == "drivetemp"][0]
    touched = []
    orig_int, orig_txt = sysfs.read_int, sysfs.read_text

    def spy_int(p, *a, **k):
        if "/%s/" % hw in p or p.endswith("/" + hw):
            touched.append(p)
        return orig_int(p, *a, **k)
    sysfs.read_int = spy_int
    try:
        devs = discovery.discover()
        assert any(x.driver == "drivetemp" for x in devs)
        assert not [p for p in touched if re.search(r"temp\d+_(max|crit|input)$", p)], touched
        devs = discovery.discover(skip_drivers=("drivetemp",))
        assert not any(x.driver == "drivetemp" for x in devs)
    finally:
        sysfs.read_int, sysfs.read_text = orig_int, orig_txt
    shutil.rmtree(d)


def test_tjmax_rules_never_above_official():
    """Fallo 6 (revisión 0.3.1): sin la tabla, ninguna regla por familia puede dar un TjMax
    mayor que el oficial (el aviso y la alarma llegarían tarde)."""
    import csv
    import sensorpulse.core.tjmax as T
    saved = T._csv_cache
    T._csv_cache = {}
    try:
        with open(os.path.join(ROOT, "data", "tjmax_amd.csv"), encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r["estado"].startswith("verificado")]
        above = []
        for r in rows:
            v, _o = T.amd_tjmax("AMD " + r["cadena_proc_cpuinfo"])
            if v is not None and v > float(r["tjmax_c"]) + 0.5:
                above.append((r["cadena_proc_cpuinfo"], v, r["tjmax_c"]))
        assert above == [], above
        assert T.amd_tjmax("AMD Ryzen 7 PRO 2700X Eight-Core Processor")[0] == 95.0
        assert T.amd_tjmax("AMD Ryzen 5 3500C with Radeon Vega Mobile Gfx")[0] == 105.0
    finally:
        T._csv_cache = saved


def test_tjmax_csv_data():
    """Fallo 12 (revisión 0.3.1): claves sin texto comercial que no sale en /proc/cpuinfo,
    sin claves repetidas con valores distintos y valores en un rango razonable."""
    import csv
    import re as _re
    with open(os.path.join(ROOT, "data", "tjmax_amd.csv"), encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    seen = {}
    for r in rows:
        k = r["cadena_proc_cpuinfo"]
        assert not _re.search(r"\bAMD\b|\bAPU\b|\bGen\b|Edition|Processor|\(|™|®", k), k
        if r["estado"].startswith("verificado"):
            v = float(r["tjmax_c"])
            assert 55 <= v <= 110, (k, v)
            assert seen.setdefault(k.lower(), v) == v, (k, v, seen[k.lower()])
    from sensorpulse.core.tjmax import amd_tjmax
    assert amd_tjmax("AMD Ryzen 9 9950X3D2 16-Core Processor") == (95.0, "tabla")
    assert amd_tjmax("AMD PRO A10-8750B R7, 12 Compute Cores 4C+8G") == (71.3, "tabla")


def test_alarms_close_when_not_evaluated():
    """Fallo 2 (revisión 0.3.1): una alarma activa se cierra al desactivar su regla, al
    apagar las alarmas o cuando el sensor deja de dar dato (pasado un minuto)."""
    from sensorpulse.core.model import Channel
    from sensorpulse.core.alarms import AlarmEngine, new_rule
    t = Channel(id="x/temp1", device_id="x", kind="temp", label="X", warn=70, crit=90)
    chans = {t.id: t}
    base = {"enabled": True, "hysteresis": 5, "delay": 0, "notify": True, "sound": True}
    # 1) regla manual desactivada
    r = new_rule("x/temp1", 80, delay=0)
    a = AlarmEngine(dict(base))
    a.configure(dict(base), [r])
    t.value = 85.0
    assert [e["type"] for e in a.evaluate(chans, 0)] == ["fired"]
    a.configure(dict(base), [dict(r, enabled=False)])
    out = a.evaluate(chans, 2)
    assert [(e["type"], e.get("reason")) for e in out] == [("cleared", "disabled")]
    assert a.active_levels() == {}
    # 2) alarmas apagadas con la automática crítica activa
    a = AlarmEngine(dict(base))
    t.value = 95.0
    assert [e["rule"] for e in a.evaluate(chans, 0)] == ["auto:crit:x/temp1"]
    a.configure(dict(base, enabled=False), [])
    out = a.evaluate(chans, 2)
    assert [(e["type"], e.get("reason")) for e in out] == [("cleared", "disabled")]
    assert a.active_levels() == {}
    # 3) el sensor deja de dar dato: sigue activa un minuto y después se cierra
    a = AlarmEngine(dict(base))
    a.evaluate(chans, 0)
    t.value = None
    assert a.evaluate(chans, 2) == [] and a.active_levels() == {"x/temp1": "critical"}
    assert a.evaluate(chans, 30) == []
    out = a.evaluate(chans, 63)
    assert [(e["type"], e.get("reason"), e["value"]) for e in out] == [("cleared", "nodata", None)]
    assert a.active_levels() == {}


def test_manual_rule_level():
    """Fallos 4 y 5 (revisión 0.3.1): el nivel de una regla manual es el mismo en el evento,
    en el panel y en el recordatorio al quitar el silencio; y se puede elegir."""
    from sensorpulse.core.model import Channel
    from sensorpulse.core.alarms import AlarmEngine, rule_level, new_rule, BELOW
    f = Channel(id="f/fan1", device_id="f", kind="fan", label="Fan")
    t = Channel(id="x/temp1", device_id="x", kind="temp", label="X")
    chans = {f.id: f, t.id: t}
    old_below = {"id": "r1", "channel": "f/fan1", "op": BELOW, "value": 300.0, "delay": 0,
                 "notify": True, "sound": True, "visual": True, "enabled": True}
    old_above = {"id": "r2", "channel": "x/temp1", "op": "above", "value": 85.0, "delay": 0,
                 "notify": True, "sound": True, "visual": True, "enabled": True}
    assert rule_level(old_below) == "hot" and rule_level(old_above) == "critical"
    assert new_rule("f/fan1", 300, op=BELOW)["level"] == "critical"
    s = {"enabled": True, "hysteresis": 5, "delay": 0}
    a = AlarmEngine(s)
    a.configure(s, [old_below, old_above])
    f.value, t.value = 0.0, 90.0
    a.mute(60)
    out = a.evaluate(chans, time.time())
    assert {e["rule"]: e["level"] for e in out} == {"r1": "hot", "r2": "critical"}
    assert a.active_levels() == {"f/fan1": "hot", "x/temp1": "critical"}
    a.mute(0)
    out = a.evaluate(chans, time.time() + 1)
    # al acabar el silencio se recuerda la manual crítica (antes solo las automáticas)
    assert [(e["rule"], e.get("reminder")) for e in out] == [("r2", True)]
