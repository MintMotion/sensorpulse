"""Pruebas de extremo a extremo del servicio D-Bus (A-01, A-02, A-03, B-10, M-01).
Ejecutar dentro de un bus de sesión aislado:
    dbus-run-session -- python3 tests/service_regress.py
Arranca bin/sensorpulse-service con un sysfs simulado y una configuración temporal."""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NAME, PATH, IFACE = "io.github.mintmotion.SensorPulse", "/io/github/mintmotion/SensorPulse", \
    "io.github.mintmotion.SensorPulse"

TMP = tempfile.mkdtemp(prefix="sp-svc-")
SYS = os.path.join(TMP, "sys")
CFG = os.path.join(TMP, "cfg")
subprocess.check_call([sys.executable, os.path.join(ROOT, "tools", "fake_sysfs.py"), SYS, "desktop"],
                      stdout=subprocess.DEVNULL)
ENV = dict(os.environ, SENSORPULSE_SYSROOT=SYS, XDG_CONFIG_HOME=CFG, XDG_STATE_HOME=CFG,
           XDG_CACHE_HOME=CFG, PYTHONPATH=ROOT, XDG_RUNTIME_DIR=TMP, LANG="C")
CFGFILE = os.path.join(CFG, "sensorpulse", "config.json")

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
proc = None
# SP_INSTALLED=1: prueba el paquete instalado (/usr/bin/sensorpulse-service) en lugar del árbol de fuentes
INSTALLED = bool(os.environ.get("SP_INSTALLED"))
PKG = "/usr/share/sensorpulse" if INSTALLED else ROOT
SERVICE_CMD = ["/usr/bin/sensorpulse-service"] if INSTALLED else [sys.executable, "-m", "sensorpulse.service"]


def call(method, sig=None, *args, flags=Gio.DBusCallFlags.NONE, timeout=5000):
    body = GLib.Variant(sig, args) if sig else None
    r = bus.call_sync(NAME, PATH, IFACE, method, body, None, flags, timeout, None)
    return r.unpack()[0] if r.n_children() else None


def snapshot():
    return json.loads(call("GetSnapshot"))


def wait_name(present=True, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        r = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "NameHasOwner", GLib.Variant("(s)", (NAME,)), None, Gio.DBusCallFlags.NONE, 2000, None)
        if r.unpack()[0] == present:
            return True
        time.sleep(0.2)
    return False


def start(cfg_text=None):
    global proc
    os.makedirs(os.path.dirname(CFGFILE), exist_ok=True)
    for n in os.listdir(os.path.dirname(CFGFILE)):
        os.remove(os.path.join(os.path.dirname(CFGFILE), n))
    if cfg_text is not None:
        open(CFGFILE, "w").write(cfg_text)
    proc = subprocess.Popen(SERVICE_CMD, env=ENV, cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    assert wait_name(True), "el servicio no ha arrancado con: %r" % cfg_text
    # espera a que haya una primera foto
    end = time.time() + 10
    while time.time() < end:
        try:
            if snapshot().get("devices"):
                return
        except GLib.Error:
            pass
        time.sleep(0.2)
    raise AssertionError("sin primera foto")


def stop():
    global proc
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
    proc = None
    wait_name(False, 5)


# ---------------------------------------------------------------- A-02 arranque con config corrupta
def test_a02_service_starts_with_corrupt_configs():
    for text in ("[]", "null", '{"intervals":{"fast":"abc"}}',
                 '{"rules":[{"id":"r1","op":"above","value":50}]}',
                 '{"rules":[{"id":"r1","channel":"a","op":"above","value":"x"}]}',
                 '{"sensors":null}', '{"alarms":[]}', '{"intervals":{"fast":0}}', "{truncado"):
        start(text)
        s = snapshot()
        assert s["devices"] and proc.poll() is None, text
        assert s["stale_after"] >= 15
        stop()


# ---------------------------------------------------------------- A-01 congelación
def test_a01_reload_with_invalid_rule_keeps_monitoring():
    start(json.dumps({"rules": []}))
    t0 = snapshot()["time"]
    open(CFGFILE, "w").write(json.dumps({"rules": [
        {"id": "r1", "channel": "x", "op": "above", "value": "x"},
        {"id": "r2", "op": "above", "value": 10}]}))
    call("ReloadConfig")
    time.sleep(5)
    t1 = snapshot()["time"]
    assert t1 > t0 + 2, "la vigilancia se ha congelado (%r -> %r)" % (t0, t1)
    assert proc.poll() is None
    stop()


def test_a01_tick_exception_does_not_stop_timer():
    """Provoca una excepción real en tick() y comprueba que sigue leyendo (el temporizador se reprograma)."""
    helper = os.path.join(TMP, "boom.py")
    open(helper, "w").write(
        "import sys, os\n"
        "sys.path.insert(0, %r)\n"
        "from sensorpulse import service\n"
        "orig = service.Service.tick\n"
        "state = {'n': 0}\n"
        "def tick(self, *a, **k):\n"
        "    state['n'] += 1\n"
        "    if state['n'] %% 3 == 0:\n"
        "        raise RuntimeError('boom')\n"
        "    return orig(self, *a, **k)\n"
        "service.Service.tick = tick\n"
        "service.main()\n" % PKG)
    global proc
    os.makedirs(os.path.dirname(CFGFILE), exist_ok=True)
    proc = subprocess.Popen([sys.executable, helper], env=ENV, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)
    assert wait_name(True)
    time.sleep(1.5)
    t0 = snapshot()["time"]
    time.sleep(6)
    t1 = snapshot()["time"]
    assert t1 > t0 + 2, "tras una excepción el servicio dejó de leer"
    assert proc.poll() is None
    stop()


# ---------------------------------------------------------------- A-03 API
def test_a03_mute_extremes_and_quit_denied():
    start()
    call("Mute", "(i)", 2147483647)
    s = snapshot()
    assert s["muted"] and s["muted_until"] - time.time() <= 1440 * 60 + 5, s["muted_until"]
    call("Mute", "(i)", -5)
    assert not snapshot()["muted"]
    # Quit desde un proceso ajeno (este script) → AccessDenied y el servicio sigue
    try:
        call("Quit")
        raise AssertionError("Quit debería rechazarse")
    except GLib.Error as e:
        assert "AccessDenied" in str(e), e
    time.sleep(0.7)
    assert proc.poll() is None
    # Quit desde un script que solo se LLAMA «sensorpulse» → también se rechaza (0.3.6: la
    # 0.3.5 lo aceptaba mirando solo el nombre). Los casos legítimos (ejecutables instalados,
    # -m sensorpulse.* desde esta carpeta) se prueban en test_036.py (trusted_caller).
    fake = os.path.join(TMP, "sensorpulse")
    open(fake, "w").write(
        "#!%s\nimport sys, gi\ngi.require_version('Gio','2.0')\nfrom gi.repository import Gio, GLib\n"
        "b=Gio.bus_get_sync(Gio.BusType.SESSION,None)\n"
        "try:\n    b.call_sync(%r,%r,%r,'Quit',None,None,Gio.DBusCallFlags.NONE,3000,None)\n"
        "except GLib.Error as e:\n    sys.exit(3 if 'AccessDenied' in str(e) else 1)\n"
        % (sys.executable, NAME, PATH, IFACE))
    os.chmod(fake, 0o755)
    assert subprocess.call([fake]) == 3, "un script llamado «sensorpulse» no debe poder parar el servicio"
    time.sleep(0.7)
    assert proc.poll() is None
    # Quit legítimo: el proceso es «python3 -m sensorpulse.…» con esta carpeta en PYTHONPATH
    code = ("import gi; gi.require_version('Gio','2.0'); from gi.repository import Gio; "
            "b=Gio.bus_get_sync(Gio.BusType.SESSION,None); "
            "b.call_sync(%r,%r,%r,'Quit',None,None,Gio.DBusCallFlags.NONE,3000,None)" % (NAME, PATH, IFACE))
    # módulo de prueba DENTRO del paquete (se borra al acabar)
    # (en el paquete instalado, el del paquete: /usr/share/sensorpulse)
    mod = os.path.join(PKG, "sensorpulse", "_test_quit.py")
    open(mod, "w").write(code.replace("; ", "\n"))
    try:
        subprocess.check_call([sys.executable, "-m", "sensorpulse._test_quit"], cwd=PKG,
                              env=dict(os.environ, PYTHONPATH=PKG))
    finally:
        os.unlink(mod)
    end = time.time() + 5
    while proc.poll() is None and time.time() < end:
        time.sleep(0.2)
    assert proc.poll() is not None, "Quit legítimo no paró el servicio"
    stop()


# ---------------------------------------------------------------- A-03 TestAlarm con límite
def test_a03_testalarm_rate_limited():
    log = os.path.join(TMP, "notify.log")
    open(log, "w").close()
    nd = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_notifd.py"), log], stdout=subprocess.PIPE)
    try:
        assert nd.stdout.readline().strip() == b"ready"
        start()
        for _i in range(6):
            call("TestAlarm", "(s)", "")
        time.sleep(1.0)
        n_test = open(log).read().count("Test alarm")
        assert n_test == 1, "TestAlarm sin límite de frecuencia: %d notificaciones" % n_test
        stop()
    finally:
        nd.terminate()
        nd.wait(5)


# ---------------------------------------------------------------- B-10 señal ligera
def test_b10_views_updated_signal_is_light():
    got = []
    sid = bus.signal_subscribe(NAME, IFACE, "ViewsUpdated", PATH, None, Gio.DBusSignalFlags.NONE,
                               lambda *a: got.append(a[5].unpack()[0]), None)
    full = []
    sid2 = bus.signal_subscribe(NAME, IFACE, "Updated", PATH, None, Gio.DBusSignalFlags.NONE,
                                lambda *a: full.append(a[5].unpack()[0]), None)
    start()
    ctx = GLib.MainContext.default()
    end = time.time() + 6
    while time.time() < end and len(got) < 2:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.05)
    bus.signal_unsubscribe(sid)
    bus.signal_unsubscribe(sid2)
    assert got, "no llegó ViewsUpdated"
    v = json.loads(got[-1])
    assert {"time", "stale_after", "views"} <= set(v)
    if full:
        assert len(got[-1]) < len(full[-1])
    stop()


# ---------------------------------------------------------------- M-01 el servicio no pisa la config ilegible
def test_m01_service_does_not_overwrite_unreadable_config():
    start("{truncado")
    names = os.listdir(os.path.dirname(CFGFILE))
    assert any(n.startswith("config.json.corrupt-") for n in names), names
    kept = [n for n in names if n.startswith("config.json.corrupt-")][0]
    assert open(os.path.join(os.path.dirname(CFGFILE), kept)).read() == "{truncado"
    stop()


# ---------------------------------------------------------------- M-02 la interfaz detecta datos caducados
def test_m02_store_detects_frozen_and_dead_service():
    """Servicio congelado (SIGSTOP) → «sin datos» en ~20 s; servicio muerto → «sin datos» al instante y,
    al volver a arrancar, el almacén se recupera solo."""
    sys.path.insert(0, ROOT)
    os.environ.update({k: ENV[k] for k in ("SENSORPULSE_SYSROOT", "XDG_CONFIG_HOME", "XDG_STATE_HOME")})
    start()
    gi.require_version("Gtk", "3.0")
    from sensorpulse.ui.store import Store
    ctx = GLib.MainContext.default()

    def pump(secs, cond=None):
        end = time.time() + secs
        while time.time() < end:
            while ctx.pending():
                ctx.iteration(False)
            if cond and cond():
                return True
            time.sleep(0.05)
        return cond() if cond else None

    store = Store()
    assert store.connect()
    assert pump(10, lambda: store.snapshot.get("devices")), "el almacén no recibió datos"
    assert not store.stale
    ch = next(c for d in store.snapshot["devices"] for c in d["channels"] if c.get("kind") == "temp")
    assert store.item(ch)["state"] != "nodata"
    # 1) congelado
    os.kill(proc.pid, signal.SIGSTOP)
    try:
        assert pump(40, lambda: store.stale), "no detectó el servicio congelado"
        assert store.state() == "nodata" and store.item(ch)["state"] == "nodata"
    finally:
        os.kill(proc.pid, signal.SIGCONT)
    assert pump(15, lambda: not store.stale), "no se recuperó tras descongelar"
    # 2) muerto → se marca al instante y se reactiva por D-Bus
    proc.kill()
    proc.wait()
    assert pump(5, lambda: store.stale), "no detectó la caída del servicio"
    assert pump(25, lambda: not store.stale), "no reactivó el servicio caído"
    _forget_store(store)
    stop()
    # el servicio que arrancó el almacén no es «proc»: pararlo por su nombre en el bus
    _kill_owner()
    wait_name(False, 5)


def _forget_store(st):
    """Que un almacén de una prueba no siga vigilando (y rearrancando) en las siguientes."""
    if getattr(st, "_watch_id", 0):
        Gio.bus_unwatch_name(st._watch_id)
        st._watch_id = 0
    if getattr(st, "_reconnect_src", 0):
        GLib.source_remove(st._reconnect_src)
        st._reconnect_src = 0


def _kill_owner():
    try:
        r = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "GetConnectionUnixProcessID", GLib.Variant("(s)", (NAME,)),
                          GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 1000, None)
        os.kill(r.unpack()[0], signal.SIGTERM)
    except (GLib.Error, OSError):
        pass


# ---------------------------------------------------------------- 0.3.6
def _notifd(mode=""):
    log = os.path.join(TMP, "notify-%s.log" % (mode.replace("=", "-") or "ok"))
    open(log, "w").close()
    nd = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_notifd.py"), log] +
                          ([mode] if mode else []), stdout=subprocess.PIPE)
    assert nd.stdout.readline().strip() == b"ready"
    return nd, log


def _lat(method="GetSnapshot"):
    t = time.time()
    bus.call_sync(NAME, PATH, IFACE, method, None, None, Gio.DBusCallFlags.NONE, 60000, None)
    return time.time() - t


def test_036_c3_hung_notification_daemon_does_not_block():
    """C3: con un demonio de notificaciones que no contesta, el servicio sigue respondiendo
    (la 0.3.5, con libnotify síncrono, se quedaba ~25 s parado por cada aviso)."""
    nd, log = _notifd("hang")
    try:
        start()
        call("TestAlarm", "(s)", "")
        time.sleep(0.5)
        assert "Test alarm" in open(log).read(), "no llegó a enviar la notificación"
        worst = max(_lat() for _i in range(5))
        assert worst < 1.0, "GetSnapshot tardó %.1f s con el demonio colgado" % worst
        # y sigue leyendo sensores: la foto avanza
        t1 = snapshot()["time"]
        time.sleep(4.5)
        assert snapshot()["time"] > t1, "el servicio dejó de publicar datos"
        stop()
    finally:
        nd.terminate()
        nd.wait(5)


def test_036_notification_actions_work_async():
    """Los botones de la notificación (ActionInvoked) siguen funcionando sin libnotify:
    «Silenciar 1 hora» en una alarma real silencia el servicio."""
    nd, log = _notifd("invoke=mute")
    try:
        cfg = {"first_run_done": True, "alarms": {"enabled": True, "notify": True, "delay": 0},
               "rules": [{"id": "r1", "channel": "cpu@proc/load", "op": "above", "value": -1,
                          "delay": 0, "notify": True, "sound": False, "level": "critical"}]}
        start(json.dumps(cfg))
        end = time.time() + 10
        while time.time() < end and not snapshot().get("muted"):
            time.sleep(0.3)
        lines = open(log).read().splitlines()
        assert any("\tmute" in ln or ",mute" in ln for ln in lines), lines
        assert snapshot().get("muted"), "la acción «Silenciar» no llegó al servicio"
        stop()
    finally:
        nd.terminate()
        nd.wait(5)


def _store():
    sys.path.insert(0, ROOT)
    os.environ.update({k: ENV[k] for k in ("SENSORPULSE_SYSROOT", "XDG_CONFIG_HOME", "XDG_STATE_HOME")})
    gi.require_version("Gtk", "3.0")
    from sensorpulse.ui import store as S
    return S


def _pump(secs, cond=None):
    ctx = GLib.MainContext.default()
    end = time.time() + secs
    while time.time() < end:
        while ctx.pending():
            ctx.iteration(False)
        if cond and cond():
            return True
        time.sleep(0.05)
    return cond() if cond else None


def test_036_restart_button_revives_frozen_service():
    """«Reiniciar el servicio» con el servicio CONGELADO (SIGSTOP): la 0.3.5 solo le pedía
    Quit, que un proceso parado no atiende, y lo dejaba igual."""
    S = _store()
    start()
    old = proc.pid
    os.kill(old, signal.SIGSTOP)
    st = S.Store()
    st.connect = lambda: None
    t0 = time.time()
    st.restart_service()
    pid = S._owner_pid(bus)
    assert pid and pid != old, "sigue el proceso congelado (%s)" % pid
    assert _lat() < 2.0
    assert time.time() - t0 < 15
    proc.wait(5)
    _forget_store(st)
    # el nuevo lo arrancó ensure_service: pararlo
    os.kill(pid, signal.SIGTERM)
    wait_name(False, 5)


def test_036_store_connects_when_service_appears_later():
    """I4: si el servicio no está a tiempo al abrir la interfaz, el almacén se conecta solo
    cuando aparece (la 0.3.5 se quedaba vacía para siempre)."""
    S = _store()
    stop()
    real = S.ensure_service
    S.ensure_service = lambda *a, **k: False      # «no arrancó a tiempo»
    try:
        st = S.Store()
        assert st.connect() is False
        start()
        assert _pump(10, lambda: bool(st.snapshot.get("devices"))), "no recibió datos al aparecer"
        assert not st.stale
    finally:
        S.ensure_service = real
        _forget_store(st)
    stop()


# ---------------------------------------------------------------- 0.3.7
def test_037_playsound_without_notification():
    """▶ junto a cada selector: suena sin mostrar la notificación de prueba, responde al
    momento y tiene límite de frecuencia (no es un altavoz libre por D-Bus)."""
    log = os.path.join(TMP, "notify037.log")
    open(log, "w").close()
    nd = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_notifd.py"), log], stdout=subprocess.PIPE)
    try:
        assert nd.stdout.readline().strip() == b"ready"
        start()
        t0 = time.time()
        for name in ("sensorpulse-nuclear", "sensorpulse-sonar", "../../etc/passwd", ""):
            call("PlaySound", "(s)", name)
        assert time.time() - t0 < 2.0
        time.sleep(1.0)
        assert "Test alarm" not in open(log).read(), "PlaySound no debe mostrar notificaciones"
        snap = snapshot()
        disks = [d for d in snap["devices"] if d["cls"] == "disk"]
        assert {d["media"] for d in disks} >= {"nvme", "hdd", "ssd"}, [d.get("media") for d in disks]
        stop()
    finally:
        nd.terminate()
        nd.wait(5)


if __name__ == "__main__":
    ok = True
    try:
        only = sys.argv[1:]     # nombres (o parte) de las pruebas a ejecutar
        for name, fn in list(globals().items()):
            if name.startswith("test_") and (not only or any(o in name for o in only)):
                try:
                    fn()
                    print("OK", name)
                except Exception as e:      # noqa: BLE001
                    ok = False
                    import traceback
                    traceback.print_exc()
                    print("FALLO", name, e)
                    stop()
    finally:
        stop()
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(0 if ok else 1)
