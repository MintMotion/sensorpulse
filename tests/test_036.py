"""Pruebas de los fallos corregidos en la 0.3.6 (un caso por hallazgo; ver docs/SensorPulse-0.3.6-cambios.md).
Ejecutar: python3 tests/test_036.py [nombre...]
Con SP_ROOT=/ruta/a/otra/version se ejecutan contra otro árbol de fuentes (así se comprobó
cuáles fallaban en la 0.3.5). No necesitan pantalla; las que usan D-Bus se saltan sin bus."""

import importlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.environ.get("SP_ROOT") or os.path.dirname(HERE))
sys.path.insert(0, ROOT)

import gi  # noqa: E402
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

REAL_TIME = time.time


class Skip(Exception):
    pass


def fresh_env(kind="desktop"):
    d = tempfile.mkdtemp(prefix="sp036-")
    subprocess.check_call([sys.executable, os.path.join(HERE, "..", "tools", "fake_sysfs.py")
                           if not os.environ.get("SP_ROOT") else
                           os.path.join(ROOT, "tools", "fake_sysfs.py"), d + "/sys", kind],
                          stdout=subprocess.DEVNULL)
    os.environ.update(SENSORPULSE_SYSROOT=d + "/sys", XDG_CONFIG_HOME=d + "/cfg",
                      XDG_STATE_HOME=d + "/state", XDG_CACHE_HOME=d + "/cache")
    os.makedirs(d + "/cfg/sensorpulse", exist_ok=True)
    from sensorpulse.core import sysfs
    importlib.reload(sysfs)
    for m in ("discovery", "nvml", "engine", "alarms", "udisks", "historydb"):
        mod = sys.modules.get("sensorpulse.core." + m)
        if mod:
            importlib.reload(mod)
    for m in ("sensorpulse.config", "sensorpulse.views"):
        if m in sys.modules:
            importlib.reload(sys.modules[m])
    return d


class Clock:
    """Desplaza time.time (el reloj de pared) sin tocar time.monotonic."""

    def __init__(self):
        self.offset = 0.0

    def __enter__(self):
        time.time = lambda: REAL_TIME() + self.offset
        return self

    def __exit__(self, *a):
        time.time = REAL_TIME


def hwmon_file(eng, pred):
    c = next(c for c in eng.channels.values() if pred(c))
    return c, c.path


def write_milli(path, v):
    with open(path, "w") as f:
        f.write("%d\n" % int(v * 1000))


# ------------------------------------------------------------------ C1 calendario de lecturas
def test_c1_engine_keeps_reading_after_clock_jumps_back():
    fresh_env()
    from sensorpulse.core.engine import Engine
    with Clock() as clk:
        e = Engine(fast_interval=1)
        e.poll(force=True)
        c, path = hwmon_file(e, lambda c: c.kind == "temp" and c.crit and "k10temp" in c.id)
        clk.offset = -3600          # NTP retrasa el reloj una hora
        write_milli(path, 91)
        end = REAL_TIME() + 4
        while REAL_TIME() < end and c.value != 91.0:
            time.sleep(0.25)
            e.poll()
    assert c.value == 91.0, "lecturas congeladas tras el salto de reloj (valor %s)" % c.value


# ------------------------------------------------------------------ C2 retardo de alarma
def test_c2_alarm_delay_survives_clock_jump():
    fresh_env()
    from sensorpulse.core.model import Channel
    from sensorpulse.core.alarms import AlarmEngine
    ch = Channel(id="x/t", device_id="x", kind="temp", label="CPU", warn=80, crit=95, value=99.0)
    a = AlarmEngine({"enabled": True, "delay": 1, "notify": True})
    with Clock() as clk:
        assert a.evaluate({ch.id: ch}) == []
        clk.offset = -3600
        time.sleep(1.3)
        ev = a.evaluate({ch.id: ch})
    assert [e["type"] for e in ev] == ["fired"], "la alarma crítica no saltó tras el salto: %r" % ev


def test_c2_mute_not_extended_by_clock_jump():
    fresh_env()
    from sensorpulse.core.alarms import AlarmEngine
    a = AlarmEngine({"enabled": True})
    with Clock() as clk:
        a.mute(1)
        clk.offset = -3600          # una hora atrás: el silencio NO debe durar 61 minutos
        orig = time.monotonic
        try:
            time.monotonic = lambda: orig() + 61
            muted = a.muted
        finally:
            time.monotonic = orig
    # en la 0.3.5 el silencio dependía de time.time: con el reloj atrasado seguía 1 h más
    assert not muted, "el silencio se alargó por un salto del reloj"


# ------------------------------------------------------------------ C4 / C5 configuración
def test_c4_deeply_nested_config_does_not_crash_anything():
    d = fresh_env()
    with open(d + "/cfg/sensorpulse/config.json", "w") as f:
        f.write("[" * 100000 + "]" * 100000)
    env = dict(os.environ, PYTHONPATH=ROOT)
    for mod in ("sensorpulse.i18n", "sensorpulse.config"):
        r = subprocess.run([sys.executable, "-c", "import %s as m; getattr(m, 'load', lambda: 0)()" % mod],
                           env=env, cwd=ROOT, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, "%s se cae: %s" % (mod, r.stderr.strip().splitlines()[-1:])


def test_c5_settings_keys_are_sanitized():
    d = fresh_env()
    with open(d + "/cfg/sensorpulse/config.json", "w") as f:
        json.dump({"panel_count": "x", "colorblind": "no", "animate_fans": 3, "language": [1],
                   "ext_applied": "sí", "floating": {"panel_bg": "y"},
                   "alarms": {"warn_sound_name": "../../etc/x"}}, f)
    from sensorpulse import config as cfgmod, views
    importlib.reload(cfgmod)
    importlib.reload(views)
    cfg = cfgmod.load()
    v = views.build(cfg, {"devices": [], "time": time.time()})   # 0.3.5: ValueError
    # 0.3.7: hasta 8 valores (3 por defecto); views publica cuántos hay de verdad en la barra
    assert views.panel_count(cfg) == 3 and v["panel_count"] == 0
    assert cfg.get("colorblind") in (False, None) and cfg.get("animate_fans") in (True, None)
    assert cfg.get("language") in ("auto", None)
    assert "ext_applied" not in cfg or isinstance(cfg["ext_applied"], bool)
    assert cfg["floating"].get("panel_bg") in (False, None)
    assert cfg["alarms"].get("warn_sound_name") in ("sensorpulse-warning", None)
    assert cfg.get("colorblind") is False, "colorblind sin sanear: %r" % cfg.get("colorblind")


# ------------------------------------------------------------------ I1 config ilegible
def test_i1_unreadable_config_is_not_moved_or_overwritten():
    d = fresh_env()
    path = d + "/cfg/sensorpulse/config.json"
    os.mkdir(path)                  # EISDIR: un OSError que NO es un fichero dañado
    with open(os.path.join(path, "keep"), "w") as f:
        f.write("x")
    from sensorpulse import config as cfgmod
    importlib.reload(cfgmod)
    cfgmod.load()
    assert os.path.isdir(path), "se apartó como «corrupto» algo que no estaba dañado"
    try:
        cfgmod.save(cfgmod.load())
    except OSError:
        pass
    assert os.path.isdir(path) and os.path.exists(os.path.join(path, "keep"))


# ------------------------------------------------------------------ I2 almacén caducado
def test_i2_store_detects_stale_even_if_clock_jumps_back():
    fresh_env()
    gi.require_version("Gtk", "3.0")
    from sensorpulse.ui import store as S
    importlib.reload(S)
    st = S.Store()
    GLib.source_remove(st._stale_src)
    with Clock() as clk:
        st._on_update({"devices": [], "time": time.time(), "stale_after": 1.0})
        clk.offset = -3600
        time.sleep(1.4)
        st._check_stale()
    assert st.stale, "servicio congelado sin detectar tras un salto atrás del reloj"


# ------------------------------------------------------------------ I6 NVIDIA sin handle
def test_i6_nvidia_without_handle_clears_values():
    fresh_env()
    from sensorpulse.core.engine import Engine
    from sensorpulse.core.model import Channel, Device
    e = Engine()
    dev = Device(id="nvidia@0000:01:00.0", cls="gpu", name="GPU", driver="nvidia")
    t = Channel(id=dev.id + "/temp", device_id=dev.id, kind="temp", label="GPU", value=66.0)
    dev.channels = [t]

    class NV:
        handles = {"0000:02:00.0": 1, "0000:03:00.0": 2}   # dos tarjetas, ninguna es esta

        def init(self):
            return True
    e.nvml = NV()
    e._read_nvidia(dev)
    assert t.value is None, "muestra %s °C de una GPU que no responde" % t.value


# ------------------------------------------------------------------ I7 rescan
def test_i7_rescan_keeps_learned_zero_rpm_flag():
    fresh_env()
    from sensorpulse.core.engine import Engine
    e = Engine()
    e.poll(force=True)
    zero = [c.id for c in e.channels.values() if c.note == "zero_rpm"]
    assert zero, "el árbol de prueba debería tener un ventilador a 0 RPM"
    e.rescan()
    lost = [cid for cid in zero if not e.channels[cid].advanced]
    assert not lost, "tras «Buscar sensores» reaparecen cabeceras sin ventilador: %s" % lost


# ------------------------------------------------------------------ I8 historial
def test_i8_history_db_only_stores_values_read_now():
    fresh_env()
    from sensorpulse import service
    importlib.reload(service)
    svc = service.Service()
    svc.notifier.show = lambda *a, **k: None
    svc.tick(force=True)
    disk = next(c.id for c in svc.engine.channels.values() if "drivetemp" in c.id and c.kind == "temp")
    for _i in range(4):
        time.sleep(1.05)
        svc.tick()
    acc = svc.db._m1.get(disk)
    n = acc.n if acc else 0
    assert n == 1, "el disco se leyó 1 vez y el historial tiene %d muestras suyas" % n


def test_i9_corrupt_history_db_at_start_is_replaced():
    d = fresh_env()
    os.makedirs(d + "/state/sensorpulse", exist_ok=True)
    with open(d + "/state/sensorpulse/history.sqlite", "wb") as f:
        f.write(b"esto no es una base de datos SQLite" * 100)
    from sensorpulse import service
    importlib.reload(service)
    svc = service.Service()
    assert svc.db is not None, "sin historial para siempre por una base dañada"
    assert any(n.startswith("history.sqlite.corrupt") for n in os.listdir(d + "/state/sensorpulse"))


# ------------------------------------------------------------------ I11 remitente de Quit
def test_i11_trusted_caller():
    from sensorpulse import service
    if not hasattr(service, "trusted_caller"):
        raise AssertionError("no hay comprobación del programa real (solo el nombre)")
    tc = service.trusted_caller
    root = "/usr/share/sensorpulse"
    assert tc(["/usr/bin/python3", "/usr/bin/sensorpulse", "--background"], "python3.12", "/", "", root)
    assert tc(["python3", "-I", "/usr/bin/sensorpulse"], "python3.11", "/home/u", "", root)
    assert tc(["python3", "-m", "sensorpulse.app"], "python3.12", root, "", root)
    assert tc(["python3", "-m", "sensorpulse.app"], "python3.12", "/tmp", root, root)
    assert not tc(["python3", "/tmp/sensorpulse"], "python3.12", "/tmp", "", root)
    assert not tc(["/home/u/bin/sensorpulse"], "bash", "/", "", root)
    assert not tc(["python3", "-m", "sensorpulse.app"], "python3.12", "/tmp", "/tmp", root)
    assert not tc(["python3", "-c", "import sensorpulse"], "python3.12", root, "", root)
    assert not tc(["python3", "./sensorpulse"], "python3.12", "/tmp", "", root)


# ------------------------------------------------------------------ I12 UDisks
def test_i12_udisks_standby_states_and_retry():
    fresh_env()
    from sensorpulse.core import udisks as U
    importlib.reload(U)
    calls = {"get": 0}
    state = [0x01]

    class Bus:
        def call_sync(self, name, path, iface, method, params, rtype, flags, timeout, canc):
            if method == "Get":
                calls["get"] += 1
                if calls["get"] == 1:
                    raise GLib.Error("org.freedesktop.DBus.Error.ServiceUnknown")
                return GLib.Variant("(v)", (GLib.Variant("o", "/org/freedesktop/UDisks2/drives/X"),))
            return GLib.Variant("(y)", (state[0],))

    dp = U.DiskPower.__new__(U.DiskPower)
    dp.Gio, dp.bus, dp.disabled_until, dp._drive_of = Gio, Bus(), 0.0, {}

    class Dev:
        id = "drivetemp@x"
        pm_path = "/nonexistent/a/b"
    dp._block_name = lambda dev: "sda"
    assert dp.standby(Dev()) is False          # UDisks aún no está: sin ruta
    real_m, real_t = time.monotonic, time.time
    try:
        time.monotonic = lambda: real_m() + 3600
        time.time = lambda: real_t() + 3600
        st = dp.standby(Dev())
    finally:
        time.monotonic, time.time = real_m, real_t
    assert calls["get"] == 2, "no volvió a buscar el disco en UDisks (caché de «no encontrado» eterna)"
    assert st is True, "estado 0x01 (Standby_y) no reconocido como reposo"


# ------------------------------------------------------------------ I14 discos USB
def test_i14_usb_disk_is_not_reported_as_sata():
    fresh_env()
    root = os.environ["SENSORPULSE_SYSROOT"]
    for f in os.listdir(os.path.join(root, "sys/class/hwmon")):
        p = os.path.join(root, "sys/class/hwmon", f, "name")
        if open(p).read().strip() == "drivetemp":
            open(p, "w").write("otro\n")
    b = os.path.join(root, "sys/block/sdb")
    os.makedirs(b + "/device")
    open(b + "/removable", "w").write("0\n")
    open(b + "/device/model", "w").write("Ultra USB 3.0\n")
    open(b + "/device/vendor", "w").write("SanDisk \n")
    gi.require_version("Gdk", "3.0")
    from sensorpulse.ui import envinfo
    importlib.reload(envinfo)
    assert envinfo.sata_disks_without_temp() == [], envinfo.sata_disks_without_temp()


# ------------------------------------------------------------------ I15 menú Ayatana
def test_i15_gio_tray_menu_has_mute():
    gi.require_version("Gtk", "3.0")
    from sensorpulse.ui import tray
    m = tray.Tray._gio_menu(object())
    acts = [m.get_item_attribute_value(i, "action", None).unpack() for i in range(m.get_n_items())]
    assert any("mute" in a for a in acts), acts


# ------------------------------------------------------------------ I16 versión del servicio
def test_i16_newer_service_is_not_stopped():
    from sensorpulse import client as C
    importlib.reload(C)
    stopped = []
    saved = (C._has_owner, C._service_version, C._stop_old, Gio.bus_get_sync)
    try:
        C._has_owner = lambda bus: True
        C._service_version = lambda bus: "99.0.0"
        C._stop_old = lambda bus: stopped.append(1)
        Gio.bus_get_sync = lambda *a: None
        C.ensure_service(timeout_ms=200)
    finally:
        C._has_owner, C._service_version, C._stop_old, Gio.bus_get_sync = saved
    assert not stopped, "una interfaz antigua para el servicio NUEVO"


# ------------------------------------------------------------------ M1 carga de CPU
def test_m1_cpu_load_is_clamped():
    fresh_env()
    from sensorpulse.core.engine import Engine
    from sensorpulse.core import sysfs
    e = Engine()
    stat_p = sysfs.p("proc/stat")
    lines = open(stat_p).read().splitlines()

    def put(idle, total_extra):
        vals = [100, 0, 100, idle, 0, 0, 0, 0]
        vals[0] += total_extra
        open(stat_p, "w").write("cpu  " + " ".join(map(str, vals)) + "\n" + "\n".join(lines[1:]) + "\n")
    put(1000, 0)
    e._cpu_load()
    put(5000, -200)          # el reposo sube más que el total (CPU desconectada / contadores)
    v = e._cpu_load()
    assert v is not None and 0.0 <= v <= 100.0, "carga imposible: %s %%" % v


# ------------------------------------------------------------------ M2 transacción a medias
def test_m2_history_insert_failure_does_not_duplicate():
    d = fresh_env()
    from sensorpulse.core import historydb as H
    importlib.reload(H)
    h = H.HistoryDB(d + "/h.sqlite")
    t0 = 1_000_020
    h.add(t0, [("a", 10.0), ("b", 20.0)])
    real = h.db

    class Proxy:
        fail = True

        def __getattr__(self, k):
            return getattr(real, k)

        def execute(self, sql, *a):
            if self.fail and sql.startswith("INSERT INTO m1") and a and a[0][1] == h._ch("b"):
                Proxy.fail = False
                raise sqlite3.OperationalError("database or disk is full")
            return real.execute(sql, *a)
    h.db = Proxy()
    try:
        h.add(t0 + 60, [("a", 10.0)])
    except sqlite3.Error:
        pass
    h.add(t0 + 61, [("a", 10.0)])
    h.add(t0 + 125, [("a", 10.0)])
    h.db = real
    n = real.execute("SELECT n FROM m1 WHERE ch=? AND t=?", (h._ch("a"), (t0 // 60) * 60)).fetchone()
    assert n and n[0] == 1, "muestras duplicadas tras un fallo a mitad de escritura: n=%s" % (n,)


# ------------------------------------------------------------------ M4 XDG relativo
def test_m4_relative_xdg_dirs_are_ignored():
    fresh_env()
    from sensorpulse import config as cfgmod
    from sensorpulse.core import historydb as H
    os.environ["XDG_CONFIG_HOME"] = "relativo"
    os.environ["XDG_STATE_HOME"] = "relativo"
    try:
        assert os.path.isabs(cfgmod.config_dir()), cfgmod.config_dir()
        assert os.path.isabs(H.state_dir()), H.state_dir()
    finally:
        fresh_env()


# ------------------------------------------------------------------ M5 CLI
def test_m5_cli_status_keeps_decimals():
    fresh_env()
    from sensorpulse import cli
    snap = {"devices": [{"name": "Placa", "cls": "cpu", "channels": [
        {"id": "b/in0", "kind": "volt", "label": "Vcore", "value": 1.234, "unit": "V",
         "state": "normal", "advanced": False},
        {"id": "b/p", "kind": "power", "label": "Paquete", "value": 5.4, "unit": "W",
         "state": "normal", "advanced": False}]}]}
    out = cli._status_text(snap)
    assert "1.23 V" in out and "5.4 W" in out, out


# ------------------------------------------------------------------ M6 avisos con decimales
def test_m6_power_alarm_message_keeps_decimals():
    fresh_env()
    from sensorpulse import service
    from sensorpulse.core.model import Channel
    importlib.reload(service)
    svc = service.Service()
    svc.engine.channels["g/p"] = Channel(id="g/p", device_id="g", kind="power", label="GPU", value=5.8)
    _t, body = svc._message({"kind": "threshold", "level": "hot", "value": 5.8, "threshold": 5.5,
                             "label": "GPU", "channel": "g/p", "op": "above"})
    assert "5.5" in body and "5.8" in body, body


# ------------------------------------------------------------------ M11 / M12
def test_m11_sysfs_rel_needs_separator():
    from sensorpulse.core import sysfs
    old = sysfs.SYSROOT
    try:
        sysfs.SYSROOT = "/tmp/x/sys"
        assert sysfs.rel("/tmp/x/sys2/a") == "/tmp/x/sys2/a", sysfs.rel("/tmp/x/sys2/a")
        assert sysfs.rel("/tmp/x/sys/a") == "/a"
    finally:
        sysfs.SYSROOT = old


def test_m12_two_disks_with_same_serial_keep_both():
    fresh_env()
    root = os.environ["SENSORPULSE_SYSROOT"]
    import shutil
    base = os.path.join(root, "sys/class/hwmon")
    src = next(os.path.join(base, f) for f in sorted(os.listdir(base))
               if open(os.path.join(base, f, "name")).read().strip() == "drivetemp" and
               "target1:" in os.path.realpath(os.path.join(base, f, "device")))
    dev = os.path.realpath(os.path.join(src, "device"))
    # (target9: el sysfs falso de la 0.3.7 ya trae un SSD SATA en target2)
    dev2 = dev.replace("target1:0:0/1:0:0:0", "target9:0:0/9:0:0:0").replace("ata2/host1", "ata9/host8")
    shutil.copytree(dev, dev2, symlinks=True)
    for p in (dev, dev2):
        open(os.path.join(p, "wwid"), "w").write("t10.ATA     MISMO-SERIE\n")
    h2 = os.path.join(base, "hwmon9")
    shutil.copytree(src, h2, symlinks=True)
    os.unlink(os.path.join(h2, "device"))
    os.symlink(dev2, os.path.join(h2, "device"))
    from sensorpulse.core import discovery
    importlib.reload(discovery)
    devs = [d for d in discovery.discover() if d.driver == "drivetemp"]
    ids = [d.id for d in devs]
    assert len(ids) == 3 and len(set(ids)) == 3, ids


# ------------------------------------------------------------------ integraciones
def test_gnome_extension_declares_gnome_51():
    meta = json.load(open(os.path.join(ROOT, "data/gnome-shell/sensorpulse@mintmotion.github.io/metadata.json")))
    assert "51" in meta["shell-version"], meta["shell-version"]


def test_xlet_versions_match_program():
    from sensorpulse import VERSION
    for p in ("data/cinnamon/applet/sensorpulse@mintmotion/metadata.json",
              "data/cinnamon/desklet/sensorpulse-gauges@mintmotion/metadata.json"):
        assert json.load(open(os.path.join(ROOT, p)))["version"] == VERSION, p
    meta = json.load(open(os.path.join(ROOT, "data/gnome-shell/sensorpulse@mintmotion.github.io/metadata.json")))
    assert meta["version-name"] == VERSION


def test_js_uses_monotonic_receipt_time_and_alarm_level():
    for p in ("data/gnome-shell/sensorpulse@mintmotion.github.io/extension.js",
              "data/cinnamon/applet/sensorpulse@mintmotion/applet.js",
              "data/cinnamon/desklet/sensorpulse-gauges@mintmotion/desklet.js"):
        js = open(os.path.join(ROOT, p)).read()
        assert "Date.now() / 1000 -" not in js, "%s: caducidad con el reloj de pared" % p
        assert "NO_AUTO_START" in js, "%s: rearranca un servicio parado a propósito" % p
    dk = open(os.path.join(ROOT, "data/cinnamon/desklet/sensorpulse-gauges@mintmotion/desklet.js")).read()
    assert 'it.state === "critical"' not in dk and '"°F"' in dk


def test_translations_keep_format_markers():
    import re
    from sensorpulse.i18n import parse_po
    spec = re.compile(r"%(?:\(\w+\))?[-#0 +]*\d*(?:\.\d+)?[sdifgGeEx]")
    for lang in ("es", "fr", "it", "de", "pt", "ja", "zh"):
        cat = parse_po(os.path.join(ROOT, "sensorpulse/po/%s.po" % lang))
        for k, v in cat.items():
            a = spec.findall(k.replace("%%", ""))
            b = spec.findall(v.replace("%%", ""))
            assert sorted(a) == sorted(b), (lang, k, v)


def test_detect_helper_writes_atomically():
    import importlib.machinery
    import importlib.util
    path = os.path.join(ROOT, "bin", "sensorpulse-detect")
    loader = importlib.machinery.SourceFileLoader("sp_detect", path)
    spec = importlib.util.spec_from_loader("sp_detect", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    if not hasattr(m, "_write_conf"):
        raise AssertionError("sin escritura atómica con fsync ni temporal único")
    d = tempfile.mkdtemp()
    m.CONF = os.path.join(d, "sensorpulse.conf")
    m._write_conf(["drivetemp", "nct6775"])
    assert open(m.CONF).read().splitlines()[-2:] == ["drivetemp", "nct6775"]
    assert os.listdir(d) == ["sensorpulse.conf"]
    assert stat.S_IMODE(os.stat(m.CONF).st_mode) == 0o644


# ------------------------------------------------------------------ I9 «No molestar»
def test_i9_gnome_dnd_key_ignored_on_other_desktops():
    """En Xfce/KDE/MATE no vale la clave de GNOME aunque su esquema esté instalado."""
    src = Gio.SettingsSchemaSource.get_default()
    if src is None or src.lookup("org.gnome.desktop.notifications", True) is None:
        raise Skip("sin el esquema de GNOME")
    code = ("import os, sys; sys.path.insert(0, %r)\n"
            "from gi.repository import Gio\n"
            "Gio.Settings.new('org.gnome.desktop.notifications').set_boolean('show-banners', False)\n"
            "from sensorpulse import service\n"
            "s = service.Service.__new__(service.Service); s.conn = None\n"
            "f = getattr(s, '_dnd_settings', None) or getattr(s, '_dnd')\n"
            "s._dnd_cache = (0.0, False)\n"
            "print('DND', f())\n" % ROOT)
    env = dict(os.environ, GSETTINGS_BACKEND="memory", XDG_CURRENT_DESKTOP="XFCE", PYTHONPATH=ROOT)
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                         timeout=60).stdout
    assert "DND False" in out, "en Xfce se aplazan avisos por la clave de GNOME: %r" % out


# ------------------------------------------------------------------ S14 / S17
def test_s14_store_save_error_is_handled():
    d = fresh_env()
    gi.require_version("Gtk", "3.0")
    from sensorpulse.ui import store as S
    importlib.reload(S)
    st = S.Store()
    GLib.source_remove(st._stale_src)
    os.mkdir(d + "/cfg/sensorpulse/config.json")      # no se puede escribir encima
    S._warn = lambda text: None
    st.save_cfg()           # 0.3.5: IsADirectoryError sin capturar en cada cambio de Ajustes


def test_s17_non_utf8_po_is_ignored():
    from sensorpulse import i18n
    p = os.path.join(tempfile.mkdtemp(), "es.po")
    open(p, "wb").write(b'msgid "Hello"\nmsgstr "Hola \xe1rbol"\n')
    assert i18n.parse_po(p) == {}


if __name__ == "__main__":
    only = sys.argv[1:]
    bad = 0
    for name, fn in list(globals().items()):
        if not name.startswith("test_") or (only and not any(o in name for o in only)):
            continue
        try:
            fn()
            print("OK", name)
        except Skip as e:
            print("SALTADA", name, e)
        except Exception as e:    # noqa: BLE001
            bad += 1
            print("FALLO", name, "-", type(e).__name__, str(e)[:160])
        finally:
            time.time = REAL_TIME
    print("fallos:", bad)
    sys.exit(1 if bad else 0)
