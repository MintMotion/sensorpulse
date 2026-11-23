"""Cliente D-Bus del servicio (usado por la interfaz y la CLI)."""

import json
import os
import re
import shutil
import subprocess
import sys

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from . import DBUS_NAME, DBUS_PATH, DBUS_IFACE, VERSION


def _service_cmd():
    here = os.path.dirname(os.path.abspath(__file__))
    return [sys.executable, "-m", "sensorpulse.service"], os.path.dirname(here)


def ensure_service(timeout_ms=4000):
    """Arranca el servicio si no está en marcha (systemd --user o proceso propio)."""
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    if _has_owner(bus):
        running = _service_version(bus)
        if running is not None and not _older(running, VERSION):
            # la misma versión, o una MÁS NUEVA (esta interfaz es la vieja: tras actualizar
            # se reinicia sola). La 0.3.5 paraba también el servicio nuevo cada vez.
            return True
        if running is None and not _responds(bus):
            # congelado: no contesta ni a GetVersion; pararlo no serviría (no atiende Quit)
            return True
        # servicio de una versión anterior (sigue vivo tras actualizar): reiniciarlo
        _stop_old(bus)
    started = False
    # 1) activación D-Bus (paquete instalado): el bus arranca el servicio vía systemd --user
    if os.path.exists("/usr/share/dbus-1/services/%s.service" % DBUS_NAME):
        try:
            bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "StartServiceByName", GLib.Variant("(su)", (DBUS_NAME, 0)),
                          GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, timeout_ms, None)
            started = True
        except GLib.Error:
            started = False
    # 2) systemd --user directamente
    if not started and shutil.which("systemctl") and \
            os.path.exists("/usr/lib/systemd/user/sensorpulse.service"):
        try:   # con un límite: un gestor de usuario atascado no debe congelar la interfaz
            r = subprocess.run(["systemctl", "--user", "start", "sensorpulse.service"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=max(1.0, timeout_ms / 1000.0))
            started = r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            started = False
    if not started:
        cmd, cwd = _service_cmd()
        env = dict(os.environ)
        env["PYTHONPATH"] = cwd + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    # esperar a que aparezca en el bus
    ctx = GLib.MainContext.default()
    waited = 0
    while waited < timeout_ms:
        if _has_owner(bus):
            return True
        GLib.usleep(100000)
        waited += 100
        while ctx.pending():
            ctx.iteration(False)
    return _has_owner(bus)


def _vtuple(v):
    out = []
    for part in str(v).split("."):
        m = re.match(r"\d+", part)
        out.append(int(m.group()) if m else 0)
    return tuple(out)


def _older(a, b):
    """¿La versión a es anterior a b?"""
    return _vtuple(a) < _vtuple(b)


def _responds(bus):
    """¿El dueño del nombre contesta (aunque sea con un error)? Distingue un servicio 0.1.0
    sin GetVersion de uno congelado."""
    try:
        bus.call_sync(DBUS_NAME, DBUS_PATH, "org.freedesktop.DBus.Peer", "Ping", None, None,
                      Gio.DBusCallFlags.NONE, 2000, None)
        return True
    except GLib.Error as e:
        return "Timeout" not in str(e) and "NoReply" not in str(e)


def _service_version(bus):
    try:
        r = bus.call_sync(DBUS_NAME, DBUS_PATH, DBUS_IFACE, "GetVersion", None,
                          GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 2000, None)
        return r.unpack()[0]
    except GLib.Error:
        return None   # 0.1.0 no tenía GetVersion


def _stop_old(bus):
    try:
        bus.call_sync(DBUS_NAME, DBUS_PATH, DBUS_IFACE, "Quit", None, None,
                      Gio.DBusCallFlags.NONE, 2000, None)
    except GLib.Error:
        pass
    for _i in range(30):   # esperar a que suelte el nombre
        if not _has_owner(bus):
            return
        GLib.usleep(100000)


def _has_owner(bus):
    try:
        r = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                          "org.freedesktop.DBus", "NameHasOwner",
                          GLib.Variant("(s)", (DBUS_NAME,)), GLib.VariantType("(b)"),
                          Gio.DBusCallFlags.NONE, 1000, None)
        return r.unpack()[0]
    except GLib.Error:
        return False


class Client:
    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._subs = []

    def call(self, method, params=None, out="(s)"):
        r = self.bus.call_sync(DBUS_NAME, DBUS_PATH, DBUS_IFACE, method, params,
                               GLib.VariantType(out) if out else None,
                               Gio.DBusCallFlags.NONE, 5000, None)
        return r.unpack() if r is not None and out else None

    def snapshot(self):
        return json.loads(self.call("GetSnapshot")[0])

    def history(self, channel, seconds=0):
        return json.loads(self.call("GetHistory", GLib.Variant("(si)", (channel, seconds)))[0])

    def history_range(self, channel, seconds):
        try:
            return json.loads(self.call("GetHistoryRange",
                                        GLib.Variant("(si)", (channel, seconds)))[0])
        except GLib.Error:
            return []

    def alarm_log(self):
        return json.loads(self.call("GetAlarmLog")[0])

    def reload(self):
        self.call("ReloadConfig", out=None)

    def rescan(self):
        self.call("Rescan", out=None)

    def mute(self, minutes):
        self.call("Mute", GLib.Variant("(i)", (minutes,)), out=None)

    def test_alarm(self, sound=""):
        self.call("TestAlarm", GLib.Variant("(s)", (sound,)), out=None)

    def play_sound(self, sound):
        """Solo suena (botón ▶ de los selectores). Con un servicio anterior a la 0.3.7, que
        no tiene PlaySound, se usa la alarma de prueba."""
        try:
            self.call("PlaySound", GLib.Variant("(s)", (sound,)), out=None)
        except GLib.Error as e:
            if "UnknownMethod" not in str(e):
                raise
            self.test_alarm(sound)

    def quit_service(self):
        self.call("Quit", out=None)

    def on_signal(self, name, cb):
        def _h(conn, sender, path, iface, sig, params):
            try:
                cb(json.loads(params.unpack()[0]))
            except Exception as e:
                print("signal error:", e, file=sys.stderr)
        sid = self.bus.signal_subscribe(DBUS_NAME, DBUS_IFACE, name, DBUS_PATH, None,
                                        Gio.DBusSignalFlags.NONE, _h)
        self._subs.append(sid)
        return sid
