"""Servicio de usuario: lee sensores, evalúa alarmas, avisa (notificación y sonido)
y publica los datos por D-Bus de sesión. Sin root."""

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from . import APP_NAME, DBUS_NAME, DBUS_PATH, DBUS_IFACE, VERSION
from . import config as cfgmod
from .core.engine import Engine
from .core.alarms import AlarmEngine, CPU_LOAD_ID
from .core.model import TEMP, FAN, PERCENT, POWER, FREQ, ST_CRIT
from . import i18n
from .i18n import _

HISTORY_KINDS = (TEMP, FAN, PERCENT, POWER, FREQ)

MAX_MUTE_MIN = 24 * 60        # el silencio no puede durar más de un día
TEST_ALARM_EVERY = 5.0        # s entre dos «Probar alarma»
PREVIEW_EVERY = 0.7           # s entre dos «▶» de un selector de sonido
WATCHDOG_STALE = 30.0         # s sin una lectura buena para dejar de avisar a systemd


def sd_notify(msg):
    """Aviso a systemd (WATCHDOG=1, READY=1...) sin depender de python3-systemd."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sk:
            sk.connect(addr)
            sk.sendall(msg.encode())
    except OSError:
        pass


def _clamp_minutes(m):
    try:
        m = int(m)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_MUTE_MIN, m))

_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def trusted_caller(argv, exe, cwd, pythonpath="", root=None):
    """¿Esta línea de órdenes es un programa de SensorPulse? exe: nombre del ejecutable real
    (/proc/PID/exe). Se aceptan los ejecutables instalados y, desde las fuentes, este mismo
    paquete con -m (cwd o PYTHONPATH apuntando aquí). No basta con LLAMARSE sensorpulse."""
    root = root or _PKG_ROOT
    if not re.fullmatch(r"python3(\.\d+)?", exe or "") or not argv:
        return False
    args = list(argv[1:])
    while args and args[0].startswith("-") and args[0] not in ("-m", "-c"):
        args = args[1:]          # opciones del intérprete (-I, -u, -X ...)
    if not args or args[0] == "-c":
        return False
    if args[0] == "-m":
        if len(args) < 2 or not (args[1] == "sensorpulse" or args[1].startswith("sensorpulse.")):
            return False
        paths = [os.path.realpath(cwd)] + [os.path.realpath(x) for x in pythonpath.split(":") if x]
        return root in paths
    script = args[0] if os.path.isabs(args[0]) else os.path.join(cwd, args[0])
    script = os.path.realpath(script)
    allowed = {os.path.realpath(x) for x in ("/usr/bin/sensorpulse", "/usr/bin/sensorpulse-service")}
    allowed.update(os.path.join(root, "bin", n) for n in ("sensorpulse", "sensorpulse-service"))
    return script in allowed


IFACE_XML = """
<node>
  <interface name="%s">
    <method name="GetSnapshot"><arg type="s" direction="out"/></method>
    <method name="GetHistory">
      <arg name="channel" type="s" direction="in"/>
      <arg name="seconds" type="i" direction="in"/>
      <arg type="s" direction="out"/>
    </method>
    <method name="GetHistoryRange">
      <arg name="channel" type="s" direction="in"/>
      <arg name="seconds" type="i" direction="in"/>
      <arg type="s" direction="out"/>
    </method>
    <method name="GetAlarmLog"><arg type="s" direction="out"/></method>
    <method name="GetVersion"><arg type="s" direction="out"/></method>
    <method name="GetAllValues"><arg type="s" direction="out"/></method>
    <method name="Ack"><arg name="rule" type="s" direction="in"/></method>
    <method name="ReloadConfig"/>
    <method name="Rescan"/>
    <method name="Mute"><arg name="minutes" type="i" direction="in"/></method>
    <method name="TestAlarm"><arg name="sound" type="s" direction="in"/></method>
    <method name="PlaySound"><arg name="sound" type="s" direction="in"/></method>
    <method name="Quit"/>
    <signal name="Updated"><arg type="s"/></signal>
    <signal name="ViewsUpdated"><arg type="s"/></signal>
    <signal name="Alarm"><arg type="s"/></signal>
  </interface>
</node>
""" % DBUS_IFACE

SOUND_DIRS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sounds"),
]


def _reap(proc, res):
    try:
        proc.wait_finish(res)
    except GLib.Error:
        pass


class Notifier:
    """Notificaciones del escritorio + sonido. Todo opcional y tolerante a fallos.

    Las notificaciones se envían por D-Bus con llamadas ASÍNCRONAS (org.freedesktop.Notifications).
    La 0.3.5 usaba libnotify, cuyo show() espera la respuesta del demonio: un demonio de
    notificaciones colgado dejaba el servicio parado ~25 s por cada aviso (sin lecturas, sin
    alarmas, sin responder por D-Bus y con riesgo de que systemd lo matara). Fallo C3."""

    NAME = "org.freedesktop.Notifications"
    PATH = "/org/freedesktop/Notifications"
    TIMEOUT_MS = 5000

    def __init__(self):
        self.gsound = None
        try:
            gi.require_version("GSound", "1.0")
            from gi.repository import GSound
            ctx = GSound.Context()
            ctx.init()
            self.gsound = ctx
        except Exception:
            self.gsound = None
        self.bus = None
        self.enabled = True
        self._subs = []
        self._caps = None          # None = aún no se sabe (se pregunta una vez, sin esperar)
        self._by_key = {}          # clave -> id de la notificación en el servidor
        self._key_of = {}          # id -> clave
        self._acts = {}            # id -> {acción: función}
        self._inflight = {}        # clave -> petición pendiente mientras se espera el id
        self._errors = 0

    # ------------------------------------------------------------ conexión
    def _bus(self):
        if self.bus is None:
            try:
                self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            except GLib.Error:
                self.bus = None
                return None
            for sig, cb in (("ActionInvoked", self._on_action), ("NotificationClosed", self._on_closed)):
                self._subs.append(self.bus.signal_subscribe(
                    self.NAME, self.NAME, sig, self.PATH, None, Gio.DBusSignalFlags.NONE, cb))
            self.bus.call(self.NAME, self.PATH, self.NAME, "GetCapabilities", None,
                          GLib.VariantType("(as)"), Gio.DBusCallFlags.NO_AUTO_START,
                          self.TIMEOUT_MS, None, self._caps_done, None)
        return self.bus

    def _caps_done(self, bus, res, _data):
        try:
            self._caps = list(bus.call_finish(res).unpack()[0])
        except GLib.Error:
            self._caps = None

    def _has_actions(self):
        return self._caps is None or "actions" in self._caps

    # ------------------------------------------------------------ mostrar
    def show(self, title, body, urgent=False, actions=None, key=None):
        """actions: lista de (clave, texto, función). key: si ya hay una notificación con esa
        clave (misma regla), se reemplaza en lugar de apilar otra. Nunca bloquea."""
        if not self.enabled:
            return
        req = (title, body, urgent, list(actions or []))
        if key and key in self._inflight:
            # aún no se sabe el id de la anterior: se envía al llegar, reemplazándola
            self._inflight[key] = req
            return
        self._send(key, req)

    def _send(self, key, req):
        bus = self._bus()
        if bus is None:
            return
        title, body, urgent, actions = req
        acts = []
        if actions and self._has_actions():
            for k, label, _fn in actions:
                acts += [k, label]
        hints = {"desktop-entry": GLib.Variant("s", "sensorpulse"),
                 "urgency": GLib.Variant("y", 2 if urgent else 1)}
        replaces = self._by_key.get(key, 0) if key else 0
        if key:
            self._inflight[key] = None
        try:
            bus.call(self.NAME, self.PATH, self.NAME, "Notify",
                     GLib.Variant("(susssasa{sv}i)", (APP_NAME, replaces, "sensorpulse", title,
                                                      body, acts, hints, -1)),
                     GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, self.TIMEOUT_MS, None,
                     self._sent, (key, actions))
        except Exception:
            self._inflight.pop(key, None)

    def _sent(self, bus, res, data):
        key, actions = data
        try:
            (nid,) = bus.call_finish(res).unpack()
        except GLib.Error as e:
            nid = 0
            self._errors += 1
            if self._errors == 1:
                print("sensorpulse: no se pudo mostrar una notificación: %s" % e.message,
                      file=sys.stderr)
        if nid:
            if key:
                old = self._by_key.get(key)
                if old and old != nid:
                    self._forget(old)
                self._by_key[key] = nid
                self._key_of[nid] = key
            self._acts[nid] = {k: fn for k, _label, fn in actions}
            while len(self._acts) > 50:            # notificaciones viejas sin cerrar
                self._forget(next(iter(self._acts)))
        if key:
            pending = self._inflight.pop(key, None)
            if pending == "close":
                self.close_key(key)
            elif pending is not None:
                self._send(key, pending)

    def _forget(self, nid):
        self._acts.pop(nid, None)
        k = self._key_of.pop(nid, None)
        if k and self._by_key.get(k) == nid:
            del self._by_key[k]

    def _on_action(self, _conn, _sender, _path, _iface, _sig, params):
        nid, action = params.unpack()
        fn = self._acts.get(nid, {}).get(action)
        if fn is not None:
            try:
                fn()
            except Exception:
                traceback.print_exc()

    def _on_closed(self, _conn, _sender, _path, _iface, _sig, params):
        nid = params.unpack()[0]
        self._forget(nid)

    def close_key(self, key):
        if key in self._inflight:
            self._inflight[key] = "close"      # se cierra en cuanto se sepa su id
            return
        nid = self._by_key.pop(key, None)
        if nid and self.bus is not None:
            self._key_of.pop(nid, None)
            self._acts.pop(nid, None)
            self.bus.call(self.NAME, self.PATH, self.NAME, "CloseNotification",
                          GLib.Variant("(u)", (nid,)), None, Gio.DBusCallFlags.NO_AUTO_START,
                          self.TIMEOUT_MS, None, None, None)

    def _sound_file(self, name):
        for d in SOUND_DIRS:
            for ext in (".oga", ".ogg", ".wav"):
                f = os.path.join(d, name + ext)
                if os.path.exists(f):
                    return f
        return None

    def play(self, name, step=None):
        """step: repetición nº (0 = primera). El volumen sube poco a poco si se repite:
        −12 dB, −6 dB y después volumen normal."""
        # solo nombres de sonido del tema (sin rutas: el nombre puede venir por D-Bus)
        if not name or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            name = "sensorpulse-critical"
        f = self._sound_file(name)
        db = 0.0 if step is None else (-12.0, -6.0, 0.0)[min(step, 2)]
        if self.gsound is not None:
            try:
                attrs = {"media.role": "alarm", "canberra.volume": "%.1f" % db}
                if f:
                    attrs["media.filename"] = f
                else:
                    attrs["event.id"] = name
                self.gsound.play_simple(attrs, None)
                return
            except Exception:
                pass
        # alternativa: reproductor de línea de órdenes
        target = f or "/usr/share/sounds/freedesktop/stereo/%s.oga" % name
        self._play_file(target, db)

    def _play_file(self, target, db=0.0):
        vol = int(65536 * (10 ** (db / 20.0)))
        for player in ("paplay", "pw-play", "aplay"):
            if shutil.which(player) and os.path.exists(target):
                cmd = [player, target]
                if player == "paplay":
                    cmd = [player, "--volume=%d" % vol, target]
                elif player == "pw-play":
                    cmd = [player, "--volume=%.2f" % (vol / 65536.0), target]
                try:
                    # Gio.Subprocess + wait_async: el proceso se recoge al terminar (con Popen sin
                    # wait quedaban procesos zombi hasta la siguiente llamada)
                    p = Gio.Subprocess.new(cmd, Gio.SubprocessFlags.STDOUT_SILENCE |
                                           Gio.SubprocessFlags.STDERR_SILENCE)
                    p.wait_async(None, _reap)
                except GLib.Error:
                    continue
                return


class Service:
    def __init__(self):
        self.cfg = cfgmod.load()
        self.engine = Engine(self.cfg["intervals"].get("fast", 2),
                             self.cfg["intervals"].get("hdd", 1800),
                             skip_drivers=self._skip_drivers(),
                             ssd_interval=self.cfg["intervals"].get("ssd", 10),
                             hdd_adaptive=self.cfg.get("hdd_adaptive", True))
        self.alarms = AlarmEngine(self.cfg["alarms"])
        self.alarms.configure(self.cfg["alarms"], self.cfg.get("rules", []))
        self.notifier = Notifier()
        self.db = None
        try:
            from .core.historydb import open_db
            self.db = open_db()     # una base dañada se aparta y se empieza otra
        except Exception as e:
            print("sensorpulse: sin historial largo (%s)" % e, file=sys.stderr)
        self._supp_t = None        # momento (monótono) de la última comprobación de «ocupado»
        self._supp_v = False       # pantalla completa o «No molestar»
        self._supp_busy = False    # comprobación en curso (asíncrona)
        self._supp_wait = []       # avisos no críticos esperando a saber si hay que aplazarlos
        self._deferred = []
        self._db_error = False
        self._migrate_ids()
        self.update_wanted()
        self.conn = None
        self.reg_id = None
        self.loop = GLib.MainLoop()
        self._snapshot_cache = "{}"
        self._tick_errors = 0
        self._tick_error_notified = False
        self._last_good_tick = time.monotonic()
        self._last_test = 0.0
        self._last_preview = 0.0

    def _skip_drivers(self):
        """Sin UDisks2 no se sabe si un disco SATA duerme: el usuario puede pedir no leerlos."""
        return () if self.cfg.get("disk_temps", True) else ("drivetemp",)

    # ------------------------------------------------------------ D-Bus
    def _on_bus(self, conn, name):
        self.conn = conn
        info = Gio.DBusNodeInfo.new_for_xml(IFACE_XML).interfaces[0]
        self.reg_id = conn.register_object(DBUS_PATH, info, self._on_call, None, None)

    def _on_name_lost(self, conn, name):
        # GDBus también llama aquí si aún no hay conexión: solo salimos si de verdad
        # hay otra instancia dueña del nombre.
        owner = None
        if conn is not None:
            try:
                r = conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                   "org.freedesktop.DBus", "GetNameOwner",
                                   GLib.Variant("(s)", (DBUS_NAME,)), GLib.VariantType("(s)"),
                                   Gio.DBusCallFlags.NONE, 1000, None)
                owner = r.unpack()[0]
            except GLib.Error:
                owner = None
        if owner and conn is not None and owner != conn.get_unique_name():
            print("%s: ya hay un servicio en marcha" % APP_NAME, file=sys.stderr)
            GLib.idle_add(self.loop.quit)

    def _sender_is_ours(self, sender):
        """¿La llamada viene de un proceso de SensorPulse (interfaz, CLI)? Se comprueba con el
        PID y el UID que da el bus. La 0.3.5 solo miraba el NOMBRE del programa en cmdline
        (cualquier script llamado «sensorpulse» pasaba). Ahora: mismo usuario, intérprete
        Python y el programa REAL (los ejecutables instalados, o este mismo paquete cargado con
        -m desde su carpeta). Un proceso del mismo usuario siempre podría matar el servicio con
        kill: esto solo evita que un script cualquiera lo pare por D-Bus por error o a propósito."""
        if self.conn is None or not sender:
            return False

        def ask(method, sig):
            r = self.conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                    "org.freedesktop.DBus", method, GLib.Variant("(s)", (sender,)),
                                    GLib.VariantType(sig), Gio.DBusCallFlags.NONE, 1000, None)
            return r.unpack()[0]
        try:
            if ask("GetConnectionUnixUser", "(u)") != os.getuid():
                return False
            pid = ask("GetConnectionUnixProcessID", "(u)")
            with open("/proc/%d/cmdline" % pid, "rb") as f:
                argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
            exe = os.path.basename(os.path.realpath("/proc/%d/exe" % pid))
            cwd = os.path.realpath("/proc/%d/cwd" % pid)
        except (GLib.Error, OSError, ValueError):
            return False
        try:
            with open("/proc/%d/environ" % pid, "rb") as f:
                env = dict(x.split(b"=", 1) for x in f.read().split(b"\0") if b"=" in x)
            pypath = env.get(b"PYTHONPATH", b"").decode("utf-8", "replace")
        except (OSError, ValueError):
            pypath = ""
        return trusted_caller(argv, exe, cwd, pypath)

    def _ret(self, inv, sig=None, *vals):
        inv.return_value(GLib.Variant(sig, vals) if sig else None)

    def _on_call(self, conn, sender, path, iface, method, params, inv):
        try:
            if method == "GetSnapshot":
                self._ret(inv, "(s)", self._snapshot_cache)
            elif method == "GetHistory":
                cid, secs = params.unpack()
                data = self.engine.history.get(cid, secs or None)
                self._ret(inv, "(s)", json.dumps(data))
            elif method == "GetHistoryRange":
                cid, secs = params.unpack()
                data = self.db.query(cid, secs) if self.db is not None else []
                self._ret(inv, "(s)", json.dumps(data))
            elif method == "GetAlarmLog":
                log = self.db.alarms(100) if self.db is not None else self.alarms.log[-100:]
                self._ret(inv, "(s)", json.dumps(log))
            elif method == "GetVersion":
                self._ret(inv, "(s)", VERSION)
            elif method == "GetAllValues":
                # lectura puntual de todo (también lo oculto) para el gestor de sensores;
                # nunca discos SATA ni controladores lentos ni dispositivos dormidos
                self._ret(inv, "(s)", json.dumps(self.engine.read_once()))
            elif method == "Ack":
                (rid,) = params.unpack()
                self.alarms.ack(rid)
                self._ret(inv)
            elif method == "ReloadConfig":
                self.reload()
                self._ret(inv)
            elif method == "Rescan":
                self.engine.rescan()
                self.update_wanted()
                self.tick(publish=True)
                self._ret(inv)
            elif method == "Mute":
                (mins,) = params.unpack()
                self.alarms.mute(_clamp_minutes(mins))
                self.tick(publish=True)
                self._ret(inv)
            elif method == "TestAlarm":
                (snd,) = params.unpack()
                now = time.monotonic()
                if now - self._last_test >= TEST_ALARM_EVERY:   # sin límite sería un altavoz libre
                    self._last_test = now
                    self.notifier.show(_("Test alarm"), _("This is how an alarm will look and sound."),
                                       urgent=True)
                    self.notifier.play(snd or self.cfg["alarms"].get("sound_name"))
                self._ret(inv)
            elif method == "PlaySound":
                # botón ▶ junto a cada selector de sonido: solo el sonido, sin notificación
                (snd,) = params.unpack()
                now = time.monotonic()
                if now - self._last_preview >= PREVIEW_EVERY:
                    self._last_preview = now
                    self.notifier.play(snd or self.cfg["alarms"].get("sound_name"))
                self._ret(inv)
            elif method == "Quit":
                if not self._sender_is_ours(sender):
                    inv.return_dbus_error("org.freedesktop.DBus.Error.AccessDenied",
                                          "Quit solo lo acepta desde SensorPulse")
                    return
                self._ret(inv)
                GLib.idle_add(self.loop.quit)
            else:
                inv.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
        except Exception as e:  # nunca tumbar el servicio por una llamada
            inv.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(e))

    def emit(self, signal_name, payload):
        if self.conn is None:
            return
        try:
            self.conn.emit_signal(None, DBUS_PATH, DBUS_IFACE, signal_name,
                                  GLib.Variant("(s)", (payload,)))
        except GLib.Error:
            pass

    # ------------------------------------------------------------ lógica
    def reload(self):
        self.cfg = cfgmod.load()
        self._startup_notices()
        # idioma cambiado en Ajustes: notificaciones, botones y nombres de sensores
        # (los nombres se ponen al descubrir: se vuelve a descubrir, sin leer discos)
        old_lang = i18n.language()
        if i18n.set_language(self.cfg.get("language")) != old_lang:
            self.engine.rescan()
        self.alarms.configure(self.cfg["alarms"], self.cfg.get("rules", []))
        if tuple(self.engine.skip_drivers) != self._skip_drivers():
            self.engine.skip_drivers = self._skip_drivers()
            self.engine.rescan()
        self.engine.fast = float(self.cfg["intervals"].get("fast", 2))
        self.engine.hdd = float(self.cfg["intervals"].get("hdd", 1800))
        self.engine.ssd = float(self.cfg["intervals"].get("ssd", 10))
        self.engine.hdd_adaptive = bool(self.cfg.get("hdd_adaptive", True))
        self.engine.apply_intervals()
        self.update_wanted()
        self.tick(publish=True)

    def _migrate_ids(self):
        """0.2.0 identificaba los discos por su posición (nvme0, puerto SATA); ahora por un
        resumen de su número de serie. Se traduce la configuración guardada."""
        from .core.discovery import ALIASES
        if not ALIASES:
            return
        def tr(x):
            for old, new in ALIASES.items():
                if x == old or x.startswith(old + "/"):
                    return new + x[len(old):]
            return x
        cfg = self.cfg
        s = cfg["sensors"]
        before = json.dumps(cfg, sort_keys=True)
        for k in ("visible", "renames"):
            s[k] = {tr(a): b for a, b in s.get(k, {}).items()}
        for k in ("order", "panel", "floating"):
            s[k] = [tr(a) for a in s.get(k, [])]
        for r in cfg.get("rules", []):
            r["channel"] = tr(r["channel"])
        if json.dumps(cfg, sort_keys=True) != before:
            cfgmod.save(cfg)

    def update_wanted(self):
        """Solo se leen los sensores visibles en algún sitio o usados por alarmas."""
        cfg = self.cfg
        # siempre: carga total, núcleos más ocupados y frecuencia (alarmas inteligentes)
        wanted = {CPU_LOAD_ID, "cpu@proc/coremax", "cpu@proc/freq"}
        for d in self.engine.devices:
            for c in d.channels:
                if cfgmod.is_visible(cfg, {"id": c.id, "advanced": c.advanced}, d.cls):
                    wanted.add(c.id)
        wanted.update(r["channel"] for r in cfg.get("rules", []))
        wanted.update(cfg["sensors"].get("panel", []))
        wanted.update(cfg["sensors"].get("floating", []))
        self.engine.set_wanted(wanted)
        self._nch = len(self.engine.channels)

    def tick(self, force=False, publish=False):
        """force: leer todo el hardware (solo al arrancar). publish: volver a evaluar y
        publicar sin forzar lecturas (cambios de ajustes, silenciar, buscar sensores)."""
        now = time.time()
        mono = time.monotonic()
        changed = self.engine.poll(now=now, force=force, mono=mono)
        if self.engine.rescan_needed:
            self.engine.rescan_needed = False
            self.engine.rescan()
            self.update_wanted()
        if len(self.engine.channels) != self._nch:   # p. ej. aparece el RPM de una NVIDIA
            self.update_wanted()
        events = []
        if changed or publish or force:   # evaluar solo con datos nuevos (no cada 0,5 s)
            try:
                events = self.alarms.evaluate(self.engine.channels, now, self.engine.devices,
                                              self.engine.fan_peak, mono=mono)
            except Exception:
                # un fallo en las alarmas no debe dejar sin publicar los datos
                self._report_error("alarmas")
        for ev in events:
            try:
                self._handle_event(ev)
            except Exception:
                self._report_error("evento")
        self._check_suppression_end()
        if self.db is not None and changed:
            wanted = self.engine.wanted
            read = self.engine.last_read
            try:
                # solo lo leído en esta pasada (antes se repetía el último valor de los
                # sensores lentos y del disco en cada minuto: línea plana falsa)
                self.db.add(now, [(c.id, c.value) for c in self.engine.channels.values()
                                  if c.kind in HISTORY_KINDS and c.value is not None
                                  and c.id in read and (wanted is None or c.id in wanted)])
            except Exception as e:
                self._history_failed(e)
        if changed or events or force or publish:
            snap = self.engine.snapshot()
            snap["alarm_channels"] = sorted(self.alarms.active_channels())
            snap["alarm_levels"] = self.alarms.active_levels()
            snap["muted"] = self.alarms.muted
            # hora de fin calculada ahora con lo que queda: correcta aunque el reloj salte
            snap["muted_until"] = (now + self.alarms.mute_remaining()) if snap["muted"] else 0.0
            # pasado este tiempo sin foto nueva, las vistas dejan de mostrar el último valor
            snap["stale_after"] = max(15.0, 3 * self.engine.fast + 5)
            try:
                from . import views
                snap["views"] = views.build(self.cfg, snap)
            except Exception as e:
                print("sensorpulse: vistas: %s" % e, file=sys.stderr)
            self._snapshot_cache = json.dumps(snap)
            self.emit("Updated", self._snapshot_cache)
            # señal ligera para extensión, applet y desklet: solo lo que muestran (la foto
            # completa pesa ~17 KB y estos clientes la procesan en el hilo del escritorio)
            if "views" in snap:
                self.emit("ViewsUpdated", json.dumps({"time": snap["time"],
                                                      "stale_after": snap["stale_after"],
                                                      "views": snap["views"]}))
        return True

    def _report_error(self, where):
        """Una excepción interna se registra y se avisa UNA vez; la vigilancia continúa."""
        self._tick_errors += 1
        print("sensorpulse: error interno (%s):\n%s" % (where, traceback.format_exc()),
              file=sys.stderr)
        if not self._tick_error_notified:
            self._tick_error_notified = True
            self.notifier.show("SensorPulse", _("Internal error; monitoring continues."))

    def _next_delay(self):
        """Segundos hasta el próximo vencimiento real de una lectura (entre 0,25 y 2 s):
        no se despierta cada 0,5 s para no hacer nada."""
        try:
            due = self.engine.next_due()
            if due is None:
                return 2.0
            # el calendario del motor va en reloj monótono (un salto del reloj del sistema
            # no puede dejar el temporizador esperando una hora)
            return max(0.25, min(2.0, due - time.monotonic()))
        except (ValueError, AttributeError, TypeError):
            return 2.0

    def _safe_tick(self):
        """Lo que programa el temporizador. Una excepción no puede detener la vigilancia
        (PyGObject elimina el temporizador que lanza una excepción, y el servicio seguiría
        vivo respondiendo con datos viejos): se registra y el siguiente tick se programa SIEMPRE."""
        try:
            self.tick()
            self._last_good_tick = time.monotonic()
        except Exception:
            self._report_error("lectura")
        finally:
            self._tick_src = GLib.timeout_add(int(self._next_delay() * 1000), self._safe_tick)
        return False

    def _watchdog(self):
        """Latido para systemd (WatchdogSec): solo mientras haya lecturas recientes. Si la
        vigilancia se atasca, systemd reinicia el servicio en lugar de dejarlo congelado."""
        if time.monotonic() - self._last_good_tick < WATCHDOG_STALE:
            sd_notify("WATCHDOG=1")
        return True

    # ------------------------------------------------------------ avisos
    def _history_failed(self, e):
        """Disco lleno o base de datos dañada: avisar una sola vez y apartar la base dañada."""
        import sqlite3
        if not self._db_error:
            self._db_error = True
            print("sensorpulse: error de historial: %s" % e, file=sys.stderr)
            self.notifier.show(_("History is not being saved"),
                               _("The sensor history could not be written (%s). Alarms keep "
                                 "working.") % e)
        from .core.historydb import HistoryDB, is_corruption, quarantine
        if isinstance(e, sqlite3.DatabaseError) and is_corruption(e):
            try:
                path = self.db.path
                self.db.close()
                quarantine(path)       # con -wal y -shm, sin pisar copias anteriores
                self.db = HistoryDB(path)
                self._db_error = False
            except Exception:
                self.db = None

    # «Ocupado»: pantalla completa (juego, vídeo) o «No molestar». Se comprueba SIN bloquear
    # el bucle (la 0.3.5 lanzaba xprop/xfconf-query y una llamada D-Bus síncronas en él) y la
    # consulta D-Bus no arranca un demonio de notificaciones que no esté en marcha.
    SUPPRESS_TTL = 5.0

    def _suppress_fresh(self):
        return self._supp_t is not None and time.monotonic() - self._supp_t < self.SUPPRESS_TTL

    def _suppress(self):
        """Último valor conocido (puede tener hasta SUPPRESS_TTL s)."""
        return self._supp_v

    def _dnd_settings(self):
        """«No molestar» en la configuración de GNOME o Cinnamon: lectura local (dconf), rápida.
        Cada clave solo vale en su escritorio (la 0.3.5 aplicaba la de GNOME también en Xfce,
        KDE o MATE si el esquema estaba instalado)."""
        de = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
        try:
            src = Gio.SettingsSchemaSource.get_default()
        except Exception:
            return False
        if src is None:
            return False
        if "cinnamon" in de:
            checks = (("org.cinnamon.desktop.notifications", "display-notifications"),)
        elif any(w in de for w in ("gnome", "ubuntu", "unity", "pop", "budgie", "zorin")):
            checks = (("org.gnome.desktop.notifications", "show-banners"),)
        else:
            checks = ()
        for schema, key in checks:
            try:
                sc = src.lookup(schema, True)
                if sc is not None and sc.has_key(key) and not Gio.Settings.new(schema).get_boolean(key):
                    return True
            except Exception:
                pass
        return False

    def _refresh_suppress(self, cb=None):
        """Comprueba de forma asíncrona si el usuario está ocupado; luego llama a cb()."""
        if cb is not None:
            self._supp_cbs = getattr(self, "_supp_cbs", [])
            self._supp_cbs.append(cb)
        if self._supp_busy:
            return
        self._supp_busy = True
        state = {"v": self._dnd_settings(), "left": 1}
        de = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()

        def done(v=False):
            state["v"] = state["v"] or bool(v)
            state["left"] -= 1
            if state["left"] == 0:
                self._supp_v = state["v"]
                self._supp_t = time.monotonic()
                self._supp_busy = False
                cbs, self._supp_cbs = getattr(self, "_supp_cbs", []), []
                for f in cbs:
                    try:
                        f()
                    except Exception:
                        self._report_error("avisos")

        if "xfce" in de and shutil.which("xfconf-query"):
            state["left"] += 1
            self._run_async(["xfconf-query", "-c", "xfce4-notifyd", "-p", "/do-not-disturb"],
                            lambda out: done((out or "").strip() == "true"))
        if self.conn is not None:
            state["left"] += 1

            def _inh(conn, res, _d):
                try:
                    done(bool(conn.call_finish(res).unpack()[0]))
                except GLib.Error:
                    done(False)
            self.conn.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                           "org.freedesktop.DBus.Properties", "Get",
                           GLib.Variant("(ss)", ("org.freedesktop.Notifications", "Inhibited")),
                           GLib.VariantType("(v)"), Gio.DBusCallFlags.NO_AUTO_START, 500, None,
                           _inh, None)
        if os.environ.get("DISPLAY") and shutil.which("xprop"):
            state["left"] += 1

            def _root(out):
                out = out or ""
                wid = out.strip().split()[-1] if "#" in out else ""
                if not wid or wid == "0x0" or not re.fullmatch(r"0x[0-9a-fA-F]+", wid):
                    done(False)
                    return
                self._run_async(["xprop", "-id", wid, "_NET_WM_STATE"],
                                lambda st: done("_NET_WM_STATE_FULLSCREEN" in (st or "")))
            self._run_async(["xprop", "-root", "_NET_ACTIVE_WINDOW"], _root)
        done(False)   # la parte local (dconf) ya está

    def _run_async(self, cmd, cb, timeout_s=1.0):
        """Ejecuta cmd sin esperar; cb(salida o None). Se mata si tarda más de timeout_s."""
        try:
            proc = Gio.Subprocess.new(cmd, Gio.SubprocessFlags.STDOUT_PIPE |
                                      Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            cb(None)
            return
        fired = []

        def finish(out):
            if not fired:
                fired.append(1)
                cb(out)

        def _kill():
            if not fired:
                proc.force_exit()
                finish(None)
            return False
        tid = GLib.timeout_add(int(timeout_s * 1000), _kill)

        def _done(p, res):
            GLib.source_remove(tid) if not fired else None
            try:
                _ok, out, _err = p.communicate_utf8_finish(res)
            except GLib.Error:
                out = None
            finish(out)
        proc.communicate_utf8_async(None, None, _done)

    def _check_suppression_end(self):
        """Al salir de la pantalla completa o de «No molestar», resumir lo que se aplazó."""
        if not self._deferred:
            return
        if not self._suppress_fresh():
            self._refresh_suppress()      # se mira de nuevo en el próximo tick
            return
        if self._suppress():
            return
        items = self._deferred[-3:]
        self._deferred = []
        lines = []
        for ev in items:
            if ev.get("value") is None:
                continue
            if self._is_temp(ev["channel"]):
                val, unit = self._temp(ev["value"])
            else:
                val, unit = ev["value"], self._unit(ev["channel"])
            lines.append("%s %s: %s%s" % ("‼" if ev["level"] == ST_CRIT else "⚠", ev["label"],
                                          self._num(val, ev["channel"]), unit))
        if lines:
            self.notifier.show(_("While you were busy"), "\n".join(lines), key="summary")

    def _temp(self, v):
        if self.cfg.get("unit") == "F" and v is not None:
            return v * 9 / 5 + 32, "°F"
        return v, "°C"

    def _open_detail(self, cid):
        exe = shutil.which("sensorpulse")
        cmd = [exe] if exe else [sys.executable, "-m", "sensorpulse.app"]
        try:
            subprocess.Popen(cmd + ["--detail", cid], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass

    def _actions(self, ev):
        acts = [("mute", _("Mute 1 hour"), lambda: (self.alarms.mute(60), self.tick(publish=True))),
                ("details", _("Details"), lambda cid=ev["channel"]: self._open_detail(cid))]
        if ev["level"] == ST_CRIT and ev.get("sound") and self.cfg["alarms"].get("repeat"):
            acts.insert(0, ("ack", _("Got it"), lambda rid=ev["rule"]: self.alarms.ack(rid)))
        if ev.get("kind") in ("throttle", "idle", "fanstop") and ev["level"] != ST_CRIT:
            def _today(kind=ev["kind"]):
                lt = time.localtime()
                midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + 1, 0, 0, 0, 0, 0, -1))
                self.alarms.snooze_kind(kind, midnight)
            acts.append(("today", _("Not again today"), _today))
        return acts

    def _message(self, ev):
        kind = ev.get("kind", "threshold")
        crit = ev["level"] == ST_CRIT
        val, unit = self._temp(ev["value"])
        if kind == "throttlecrit":
            return (_("Cooling problem"),
                    _("%(name)s is at its limit (%(value).0f%(unit)s) and has to slow down a lot to "
                      "protect itself. Make sure the air vents are not blocked and the fans are "
                      "working.") % {"name": ev["label"], "value": val, "unit": unit})
        if kind == "throttle":
            return (_("Slowing down because of heat"),
                    _("%(name)s is reducing its speed to protect itself from heat "
                      "(%(value).0f%(unit)s).") % {"name": ev["label"], "value": val, "unit": unit})
        if kind == "idle":
            title = _("Cooling problem") if crit else _("Too hot for the workload")
            return (title,
                    _("%(name)s is at %(value).0f%(unit)s while the computer is almost idle "
                      "(%(load).0f%% load). Make sure the air vents are not blocked.") % {
                        "name": ev["label"], "value": val, "unit": unit,
                        "load": ev.get("load") or 0})
        if kind == "fanstop":
            rv, unit = self._temp(ev.get("ref_value"))
            return (_("Fan stopped"),
                    _("%(name)s has stopped while %(ref)s is at %(value).0f%(unit)s.") % {
                        "name": ev["label"], "ref": ev.get("ref") or "", "value": rv or 0,
                        "unit": unit})
        if self._is_temp(ev["channel"]):
            lim, u = self._temp(ev["threshold"])
        else:
            val, lim, u = ev["value"], ev["threshold"], self._unit(ev["channel"])
        # con decimales cuando hacen falta (una regla de 5,5 W decía «límite 6 W»)
        args = {"name": ev["label"], "value": self._num(val, ev["channel"]), "unit": u,
                "limit": self._num(lim, ev["channel"])}
        if ev["op"] == "above":
            body = _("%(name)s has reached %(value)s%(unit)s (limit %(limit)s%(unit)s).") % args
        else:
            body = _("%(name)s has dropped to %(value)s%(unit)s (limit %(limit)s%(unit)s).") % args
        return (_("Temperature alarm") if crit else _("Warning"), body)

    def _unit(self, cid):
        from .core.model import UNITS
        c = self.engine.channels.get(cid)
        u = UNITS.get(c.kind, "") if c is not None else ""
        return u if u in ("%",) else (" " + u if u else "")

    def _num(self, v, cid):
        """Número para un aviso: sin decimales salvo donde importan (vatios < 10, voltios)."""
        if v is None:
            return "–"
        c = self.engine.channels.get(cid)
        kind = c.kind if c is not None else None
        if kind == "volt":
            return "%.2f" % v
        if kind == POWER and abs(v) < 10:
            return ("%.1f" % v).rstrip("0").rstrip(".")
        return "%.0f" % v

    def _is_temp(self, cid):
        c = self.engine.channels.get(cid)
        return c is not None and c.kind == TEMP

    def _handle_event(self, ev):
        needs = ev["type"] == "fired" and ev["level"] != ST_CRIT and (ev["notify"] or ev["sound"])
        if self._supp_wait or (needs and not self._suppress_fresh()):
            # hay que saber si el usuario está ocupado: se comprueba sin bloquear y el aviso
            # sale al terminar (en orden: un «cleared» no puede adelantarse a su «fired»)
            self._supp_wait.append(ev)
            if len(self._supp_wait) == 1:
                self._refresh_suppress(self._flush_waiting)
            return
        self._dispatch(ev)

    def _flush_waiting(self):
        waiting, self._supp_wait = self._supp_wait, []
        for ev in waiting:
            try:
                self._dispatch(ev)
            except Exception:
                self._report_error("evento")

    def _dispatch(self, ev):
        crit = ev["level"] == ST_CRIT
        key = "rule:" + ev["rule"]
        if ev["type"] == "fired" and not crit and (ev["notify"] or ev["sound"]) and self._suppress():
            # pantalla completa o «No molestar»: los avisos no críticos se apuntan y se
            # resumen al terminar; las alarmas críticas interrumpen siempre
            ev["notify"] = False
            ev["sound"] = False
            ev["deferred"] = True
            self._deferred.append(ev)
        self.emit("Alarm", json.dumps(ev))
        if self.db is not None and ev["type"] in ("fired", "cleared"):
            try:
                self.db.add_alarm(ev)
            except Exception as e:
                self._history_failed(e)
        if ev["type"] == "fired":
            if ev["notify"]:
                title, body = self._message(ev)
                self.notifier.show(title, body, urgent=crit, actions=self._actions(ev), key=key)
            if ev["sound"]:
                self.notifier.play(self._sound_for(ev), step=0)
        elif ev["type"] == "repeat" and ev["sound"]:
            self.notifier.play(self._sound_for(ev), step=ev.get("repeats", 1))
        elif ev["type"] == "cleared":
            if ev["notify"] and not ev["auto"] and ev["value"] is not None:
                if self._is_temp(ev["channel"]):
                    val, unit = self._temp(ev["value"])
                else:
                    val, unit = ev["value"], self._unit(ev["channel"])
                self.notifier.show(_("Back to normal"),
                                   _("%(name)s is back to %(value)s%(unit)s.") % {
                                       "name": ev["label"], "value": self._num(val, ev["channel"]),
                                       "unit": unit}, key=key)
            else:
                # cerrada sin dato (sensor dormido) o por desactivar la regla: quitar el aviso
                self.notifier.close_key(key)

    def _sound_for(self, ev):
        """Reglas manuales: el sonido elegido en la regla. Automáticas: el sonido de alarma
        para las críticas y un tono suave para los avisos."""
        if not ev.get("auto"):
            return ev.get("sound_name") or self.cfg["alarms"].get("sound_name") or "sensorpulse-critical"
        if ev["level"] == ST_CRIT:
            return ev.get("sound_name") or self.cfg["alarms"].get("sound_name") or "sensorpulse-critical"
        return self.cfg["alarms"].get("warn_sound_name") or "sensorpulse-warning"

    def run(self):
        Gio.bus_own_name(Gio.BusType.SESSION, DBUS_NAME, Gio.BusNameOwnerFlags.NONE,
                         self._on_bus, None, self._on_name_lost)
        try:
            self.tick(force=True)
        except Exception:
            self._report_error("arranque")
        self._last_good_tick = time.monotonic()
        self._startup_notices()
        self._tick_src = GLib.timeout_add(500, self._safe_tick)
        GLib.timeout_add_seconds(10, self._watchdog)
        sd_notify("READY=1")
        for s in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, s, self._quit)
        self.loop.run()
        if self.db is not None:
            self.db.close()
        self.engine.fds.close()
        if self.engine.nvml is not None:
            self.engine.nvml.shutdown()

    def _startup_notices(self):
        """Avisos de arranque: configuración ilegible apartada o reglas descartadas."""
        p = cfgmod.LAST_PROBLEM
        if p.get("unreadable"):
            self.notifier.show(_("Settings could not be read"),
                               _("%s cannot be read (check its permissions). The last good copy "
                                 "or the defaults are being used, and the file will not be "
                                 "overwritten.") % p["unreadable"], key="config-unreadable")
            p["unreadable"] = None
        if p.get("corrupt_file"):
            self.notifier.show(_("Settings could not be read"),
                               _("The settings file was damaged. It was saved as %s and the "
                                 "last good copy or the defaults are being used.") % p["corrupt_file"])
            p["corrupt_file"] = None
        if p.get("dropped_rules"):
            self.notifier.show(_("Some alarm rules were ignored"),
                               _("%d custom alarm rule(s) were invalid and have been ignored.")
                               % p["dropped_rules"])
            p["dropped_rules"] = 0

    def _quit(self):
        self.loop.quit()
        return False


def main():
    # Arrancado por activación D-Bus sin systemd: el bus nos da DBUS_STARTER_ADDRESS
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS") and os.environ.get("DBUS_STARTER_ADDRESS"):
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = os.environ["DBUS_STARTER_ADDRESS"]
    Service().run()


if __name__ == "__main__":
    main()
