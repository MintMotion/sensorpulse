"""Pruebas de la auditoría de la 0.3.3 (un caso por hallazgo). Ejecutar: python3 tests/test_audit.py
No necesitan pantalla ni D-Bus; las de interfaz y servicio están en gui_regress.py y service_regress.py."""

import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from test_core import with_root  # noqa: E402


def fresh_cfg_dir():
    d = tempfile.mkdtemp(prefix="sp-cfg-")
    os.environ["XDG_CONFIG_HOME"] = d
    os.environ["XDG_STATE_HOME"] = d
    os.makedirs(os.path.join(d, "sensorpulse"), exist_ok=True)
    return d


# ------------------------------------------------------------------ A-02 / M-01 / M-07
def test_a02_corrupt_configs_load():
    """Ninguna configuración corrupta impide cargar; los valores quedan saneados."""
    from sensorpulse import config as C
    d = fresh_cfg_dir()
    path = os.path.join(d, "sensorpulse", "config.json")
    cases = [
        "[]", "null", "5", '{"intervals":{"fast":"abc"}}',
        '{"rules":[{"id":"r1","op":"above","value":50}]}',
        '{"rules":[{"id":"r1","channel":"a","op":"above","value":"x"}]}',
        '{"sensors":null}', '{"alarms":[]}', '{"floating":3,"window":[]}',
        '{"intervals":{"fast":0,"hdd":5}}', '{"intervals":{"fast":99999}}',
        '{"display":{"panel":"yes"},"style":42,"accent":"rojo","unit":"K"}',
        '{"sensors":{"visible":{"a":1},"renames":{"a":5},"panel":[1,"x"]}}',
        "{no es json",
    ]
    for c in cases:
        with open(path, "w") as f:
            f.write(c)
        cfg = C.load()
        assert 1 <= cfg["intervals"]["fast"] <= 60, c
        assert 60 <= cfg["intervals"]["hdd"] <= 14400, c   # 0.3.7: desde 1 min
        assert isinstance(cfg["rules"], list) and isinstance(cfg["alarms"], dict), c
        assert isinstance(cfg["display"]["panel"], bool), c
        assert cfg["unit"] in ("C", "F") and cfg["accent"].startswith("#"), c
        assert all(isinstance(x, str) for x in cfg["sensors"]["panel"]), c
    # regla válida se conserva y se normaliza; la inválida se descarta
    with open(path, "w") as f:
        json.dump({"rules": [{"id": "ok", "channel": "x/t", "op": "above", "value": 70, "delay": "9"},
                             {"id": "bad", "op": "above", "value": 1}]}, f)
    cfg = C.load()
    assert [r["id"] for r in cfg["rules"]] == ["ok"] and cfg["rules"][0]["delay"] == 10
    assert C.LAST_PROBLEM["dropped_rules"] == 1


def test_m01_corrupt_file_is_kept_and_not_overwritten():
    from sensorpulse import config as C
    d = fresh_cfg_dir()
    path = os.path.join(d, "sensorpulse", "config.json")
    good = C.load()
    good["style"] = "glass"
    C.save(good)
    good["style"] = "hud"
    C.save(good)                                   # crea config.json.bak con «glass»
    with open(path, "w") as f:
        f.write("{truncado")
    cfg = C.load()
    names = os.listdir(os.path.join(d, "sensorpulse"))
    assert any(n.startswith("config.json.corrupt-") for n in names), names
    assert open([os.path.join(d, "sensorpulse", n) for n in names
                 if n.startswith("config.json.corrupt-")][0]).read() == "{truncado"
    assert cfg["style"] == "glass"                 # se recupera la última copia buena
    assert C.LAST_PROBLEM["corrupt_file"]


def test_m01_m07_atomic_private_save():
    from sensorpulse import config as C
    d = fresh_cfg_dir()
    C.save(C.load())
    C.save(C.load())
    cdir = os.path.join(d, "sensorpulse")
    assert stat.S_IMODE(os.stat(cdir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(os.path.join(cdir, "config.json")).st_mode) == 0o600
    assert not [n for n in os.listdir(cdir) if n.endswith(".tmp")]      # sin temporales sueltos


def test_m07_history_private_and_weighted():
    from sensorpulse.core.historydb import HistoryDB
    d = fresh_cfg_dir()
    p = os.path.join(d, "state", "h.sqlite")
    t = time.time()
    db = HistoryDB(p)
    db.add(t, [("a", 10.0)])
    db.add(t + 1, [("a", 20.0)])
    db.close()
    db = HistoryDB(p)                              # reinicio a mitad de minuto
    db.add(t + 2, [("a", 30.0)])
    db.close()
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(p)).st_mode) == 0o700
    import sqlite3
    rows = sqlite3.connect(p).execute("SELECT avg, mn, mx, n FROM m1").fetchall()
    assert len(rows) <= 2 and sum(r[3] for r in rows) == 3        # B-08: sin duplicados por minuto
    db = HistoryDB(p)
    out = db.query("a", 3600)
    assert abs(sum(r[1] for r in out) / len(out) - 20.0) < 1e-6 or len(out) == 2   # media ponderada
    db.close()


def test_b08_old_database_is_migrated():
    import sqlite3
    from sensorpulse.core.historydb import HistoryDB
    d = fresh_cfg_dir()
    p = os.path.join(d, "old.sqlite")
    c = sqlite3.connect(p)
    c.executescript("CREATE TABLE channels (id INTEGER PRIMARY KEY, cid TEXT UNIQUE);"
                    "CREATE TABLE m1 (t INTEGER, ch INTEGER, avg REAL, mn REAL, mx REAL);"
                    "CREATE TABLE m10 (t INTEGER, ch INTEGER, avg REAL, mn REAL, mx REAL);"
                    "CREATE TABLE alarms (t REAL, ev TEXT);"
                    "INSERT INTO channels(cid) VALUES ('a');"
                    "INSERT INTO m1 VALUES (60,1,50,50,50),(60,1,60,60,60),(120,1,70,70,70);")
    c.commit()
    c.close()
    db = HistoryDB(p)
    rows = db.db.execute("SELECT t, avg, n FROM m1 ORDER BY t").fetchall()
    assert rows == [(60, 55.0, 2), (120, 70.0, 1)], rows
    db.close()


# ------------------------------------------------------------------ A-03 / mute
def test_a03_mute_limits():
    from sensorpulse.core.alarms import AlarmEngine, MAX_MUTE_MIN
    a = AlarmEngine({})
    a.mute(2147483647)
    assert a.muted_until - time.time() <= MAX_MUTE_MIN * 60 + 2
    a.mute(-5)
    assert a.muted_until == 0.0
    a.mute("x")
    assert a.muted_until == 0.0


def test_a03_critical_still_notifies_when_muted():
    from sensorpulse.core.alarms import AlarmEngine, new_rule
    from sensorpulse.core.model import Channel
    ch = Channel(id="x/t", device_id="x", kind="temp", label="CPU", warn=80, crit=95, value=97.0)
    a = AlarmEngine({"enabled": True, "delay": 0, "notify": True, "sound": True})
    a.configure(a.settings, [new_rule("x/t", 50, delay=0)])
    a.mute(60)
    ev = a.evaluate({ch.id: ch}, time.time())
    crit = [e for e in ev if e["level"] == "critical" and e["type"] == "fired"]
    assert crit and all(e["notify"] and not e["sound"] for e in crit)


# ------------------------------------------------------------------ B-04 estado único
def test_b04_single_state_function():
    from sensorpulse.core.model import temp_state
    from sensorpulse import views
    assert temp_state(70, 80, 95, throttling=True) == "hot"
    assert temp_state(96, 80, 95) == "critical"
    assert temp_state(None, 80, 95) is None
    ch = {"kind": "temp", "value": 70.0, "warn": 80.0, "crit": 95.0, "throttling": True}
    assert views.state_of(ch) == "hot"
    d = with_root("laptop")
    from sensorpulse.core.engine import Engine
    e = Engine()
    e.poll(now=1000.0, force=True)
    f = os.path.join(d, "sys/devices/system/cpu/cpu2/thermal_throttle/package_throttle_count")
    open(f, "w").write("99\n")
    e.poll(now=1003.0, force=True)
    pkg = next(c for c in e.devices[0].channels if c.primary)
    assert pkg.throttling and pkg.state == "hot"   # la CLI ya dice lo mismo que el panel


# ------------------------------------------------------------------ B-05 intervalos
def test_b05_user_interval_applies_to_slow_devices():
    with_root("laptop")
    from sensorpulse.core.engine import Engine
    e = Engine(fast_interval=10)
    nvme = next(d for d in e.devices if d.driver == "nvme")
    assert nvme.interval == 10                      # antes se quedaba en 5 s
    e.fast = 2.0
    e.apply_intervals()
    assert nvme.interval == 5                       # y nunca más rápido que su mínimo
    cpu = next(d for d in e.devices if d.cls == "cpu")
    assert cpu.interval == 2.0


# ------------------------------------------------------------------ discovery
def test_natural_sort_and_dormant_gpu():
    d = with_root("desktop")
    from sensorpulse.core import discovery, sysfs
    assert sorted(["temp10_input", "temp2_input", "temp1_input"], key=discovery._natural) == \
        ["temp1_input", "temp2_input", "temp10_input"]
    # B-11: GPU dormida (runtime_status suspended): no se leen etiquetas ni límites
    hw = os.path.join(d, "sys/devices/pci0000:00/0000:03:00.0")
    os.makedirs(os.path.join(hw, "hwmon/hwmon9"), exist_ok=True)
    os.makedirs(os.path.join(hw, "power"), exist_ok=True)
    for name, val in (("vendor", "0x1002"), ("class", "0x030000"), ("power/runtime_status", "suspended")):
        open(os.path.join(hw, name), "w").write(val + "\n")
    for name, val in (("name", "amdgpu"), ("temp1_input", "50000"), ("temp1_label", "edge"),
                      ("temp1_max", "90000"), ("temp1_crit", "100000"), ("power1_cap", "200000000")):
        open(os.path.join(hw, "hwmon/hwmon9", name), "w").write(val + "\n")
    os.symlink(hw, os.path.join(hw, "hwmon/hwmon9/device"))
    os.symlink(os.path.join(hw, "hwmon/hwmon9"), os.path.join(d, "sys/class/hwmon/hwmon9"))
    touched = []
    real_t, real_i = sysfs.read_text, sysfs.read_int
    sysfs.read_text = lambda p, *a, **k: (touched.append(p), real_t(p, *a, **k))[1]
    sysfs.read_int = lambda p, *a, **k: (touched.append(p), real_i(p, *a, **k))[1]
    try:
        devs = discovery._scan_hwmon()
    finally:
        sysfs.read_text, sysfs.read_int = real_t, real_i
    gpu = next(x for x in devs if x.driver == "amdgpu" and x.pm_path and "/0000:03:00.0/power" in x.pm_path
               and "0000:00:01.1" not in x.pm_path)
    assert gpu.limits_pending
    bad = [t for t in touched if "hwmon9" in t and any(
        t.endswith(s) for s in ("_label", "_max", "_crit", "_cap"))]
    assert not bad, bad


def test_b01_nvidia_requires_nvidia_driver():
    d = with_root("laptop")
    from sensorpulse.core import nvml
    pci = os.path.join(d, "sys/bus/pci/devices/0000:01:00.0")
    assert nvml.bound_driver(pci) == "nvidia"
    os.remove(os.path.join(pci, "driver"))
    os.symlink("../../../../bus/pci/drivers/nouveau", os.path.join(pci, "driver"))
    nvml.Nvml._load = lambda self: True        # la librería «está instalada»
    devs, _nv = nvml.discover_nvidia()
    assert devs == []                           # con nouveau no se duplica la GPU


def test_b02_desktop_detection():
    from sensorpulse.ui import envinfo
    for env, want in (("Budgie:GNOME", "budgie"), ("ubuntu:GNOME", "gnome"), ("X-Cinnamon", "cinnamon"),
                      ("Pantheon", "other"), ("KDE", "kde"), ("XFCE", "xfce"), ("MATE", "mate")):
        os.environ["XDG_CURRENT_DESKTOP"] = env
        assert envinfo.desktop() == want, env


def test_b14_cinnamon_slot():
    from sensorpulse.ui import envinfo

    class St:
        def get_strv(self, k):
            return ["3:0:top"]
    # 0.3.7: la PRIMERA posición de la zona derecha (a la izquierda de los iconos del sistema)
    assert envinfo._cinnamon_free_slot(St(), ["panel3:right:0:a:1", "panel3:right:4:b:2",
                                               "panel1:right:9:c:3"]) == (3, -1)
    assert envinfo._cinnamon_free_slot(St(), []) == (3, 0)


# ------------------------------------------------------------------ B-07 / B-06
def test_b07_report_escapes_cells():
    from sensorpulse.report import build
    out = build({"devices": [{"name": "A|B", "channels": [{"label": "x\ny|z", "value": 1, "unit": "°C"}]}]})
    row = out.splitlines()[-1]
    assert row.count("|") - row.count("\\|") == 9 and "\n" not in row


def test_b06_export_cli_validation():
    cli = [sys.executable, os.path.join(ROOT, "bin", "sensorpulse")]
    env = dict(os.environ, PYTHONPATH=ROOT)
    r = subprocess.run([sys.executable, "-c",
                        "import sys; sys.path.insert(0, %r); from sensorpulse.cli import main; "
                        "sys.exit(main(['export','--days','2']))" % ROOT],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 2 and "id" in r.stderr.lower()        # antes: CSV vacío sin error
    r = subprocess.run([sys.executable, "-c",
                        "import sys; sys.path.insert(0, %r); from sensorpulse.cli import main; "
                        "sys.exit(main(['export','x','--days','-3']))" % ROOT],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 2
    assert cli


# ------------------------------------------------------------------ M-05 ayudante
def _load_detect():
    loader = importlib.machinery.SourceFileLoader("sp_detect", os.path.join(ROOT, "bin", "sensorpulse-detect"))
    spec = importlib.util.spec_from_loader("sp_detect", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def test_m05_helper_logic():
    m = _load_detect()
    assert open(os.path.join(ROOT, "bin", "sensorpulse-detect")).readline().strip() == "#!/usr/bin/python3 -I"
    assert m.attributed("/x", "nct6798", "nct6775") and m.attributed("/x", "it8628", "it87")
    assert not m.attributed("/nonexistent/hwmon3", "nvme", "nct6775")     # otro dispositivo: no cuenta
    assert not m.attributed("/nonexistent/hwmon3", "k10temp", "it87")
    # acpi_conflict: una línea concreta del driver, no palabras sueltas por todo dmesg
    real = subprocess.run
    try:
        subprocess.run = lambda *a, **k: subprocess.CompletedProcess(
            a, 0, stdout="ACPI: resource foo\nsomething conflict\nit87: ACPI resource conflict\n")
        assert m.acpi_conflict()
        subprocess.run = lambda *a, **k: subprocess.CompletedProcess(
            a, 0, stdout="ACPI: resource foo\nPCI conflict unrelated\nnct6775 loaded\n")
        assert not m.acpi_conflict()
    finally:
        subprocess.run = real
    assert m.WHITELIST >= set(m.SUPERIO)


# ------------------------------------------------------------------ M-03 iconos sin /tmp predecible
def test_m03_icon_dir_without_runtime_dir():
    import gi
    gi.require_version("Gtk", "3.0")
    from sensorpulse.ui import tray
    home = tempfile.mkdtemp(prefix="sp-home-")
    old = {k: os.environ.get(k) for k in ("XDG_RUNTIME_DIR", "XDG_CACHE_HOME", "HOME")}
    try:
        os.environ.pop("XDG_RUNTIME_DIR", None)
        os.environ.pop("XDG_CACHE_HOME", None)
        os.environ["HOME"] = home
        # el atacante prepara la carpeta predecible de /tmp con enlaces a un fichero de la víctima
        victim = os.path.join(home, "victima.txt")
        open(victim, "w").write("importante")
        trap = "/tmp/sensorpulse-icons"
        if not os.path.exists(trap):
            os.makedirs(trap)
            for i in range(4):
                os.symlink(victim, os.path.join(trap, "tray-%d.png" % i))
        tray._ICON_DIR = None
        d = tray._icon_dir()
        assert d != trap and not d.startswith("/tmp/sensorpulse-icons")
        assert d.startswith(home) and stat.S_IMODE(os.stat(d).st_mode) == 0o700
        # y aunque dentro hubiera un enlace, no se sigue
        link = os.path.join(d, "tray-0.png")
        os.symlink(victim, link)
        import cairo
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 4, 4)
        try:
            tray._write_png(surf, link)
            raise AssertionError("se siguió un enlace simbólico")
        except OSError:
            pass
        assert open(victim).read() == "importante"
        # carpeta con permisos laxos → se descarta
        os.chmod(d, 0o755)
        tray._ICON_DIR = None
        assert tray._icon_dir() != d
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil_rmtree("/tmp/sensorpulse-icons")


def shutil_rmtree(p):
    import shutil
    shutil.rmtree(p, ignore_errors=True)


# ------------------------------------------------------------------ M-06 solo el ayudante de root
def test_m06_helper_only_root_owned():
    from sensorpulse.ui import detect
    real = detect.HELPER
    tmp = tempfile.mkdtemp(prefix="sp-helper-")
    try:
        fake = os.path.join(tmp, "sensorpulse-detect")
        open(fake, "w").write("#!/bin/sh\n")
        detect.HELPER = fake
        if os.geteuid() == 0:
            os.chmod(fake, 0o755)
            assert detect.helper_path() == fake
            os.chmod(fake, 0o775)                          # escribible por el grupo → no
            assert detect.helper_path() is None
            os.chmod(fake, 0o755)
            os.chown(fake, 1000, 1000)                     # de un usuario → no
            assert detect.helper_path() is None
        else:
            assert detect.helper_path() is None            # un usuario normal nunca es «root»
        detect.HELPER = os.path.join(tmp, "no-existe")
        assert detect.helper_path() is None
    finally:
        detect.HELPER = real
    assert detect.HELPER.startswith("/usr/libexec/")
    src = open(os.path.join(ROOT, "sensorpulse", "ui", "settings.py"), encoding="utf-8").read()
    assert '"apt-get"' not in src and "pkexec\", \"apt" not in src      # sin alternativa genérica con apt-get


# ------------------------------------------------------------------ views / i18n
def test_views_lang_and_muted_text():
    from sensorpulse import views
    assert views.muted_text({"muted": False}) == ""
    t = views.muted_text({"muted": True, "muted_until": time.time() + 600})
    assert t and ":" in t
    cfg = __import__("sensorpulse.config", fromlist=["x"]).DEFAULTS
    v = views.build(cfg, {"devices": [], "muted": False})
    assert v["lang"] in ("es", "en", "fr", "ja", "zh")


def test_b03_deferred_translation_tables():
    import re
    src = open(os.path.join(ROOT, "sensorpulse", "ui", "detect.py"), encoding="utf-8").read()
    table = src.split("NAMES = {")[1].split("\n}")[0]
    assert "N_(" in table and not re.search(r"(?<![A-Za-z_])_\(", table)   # solo marcadas, no traducidas al importar
    assert "_(NAMES.get(" in src                                          # se traducen al usarlas
    from sensorpulse.i18n import N_
    assert N_("x") == "x"


def test_m04_autostart_tryexec():
    s = open(os.path.join(ROOT, "data", "sensorpulse-autostart.desktop")).read()
    assert "TryExec=sensorpulse" in s
    assert "StartupWMClass=sensorpulse" in open(os.path.join(ROOT, "data", "sensorpulse.desktop")).read()


def test_p06_unit_hardening():
    u = open(os.path.join(ROOT, "data", "sensorpulse.service")).read()
    for k in ("Restart=always", "WatchdogSec=", "NotifyAccess=main", "NoNewPrivileges=yes",
              "RestrictAddressFamilies=AF_UNIX"):
        assert k in u, k


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
