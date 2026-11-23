"""Estado compartido de la interfaz: última foto del servicio, animaciones,
selección de sensores y avisos a las vistas."""

import math
import sys
import time

from gi.repository import Gio, GLib

from .. import DBUS_NAME
from .. import config as cfgmod
from ..i18n import _
from ..client import Client, ensure_service


def _merge_inplace(dst, src):
    """Copia src en dst conservando los mismos objetos dict (también los anidados), porque
    las vistas guardan referencias a ellos (p. ej. cfg["alarms"])."""
    for k in list(dst):
        if k not in src:
            del dst[k]
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge_inplace(dst[k], v)
        else:
            dst[k] = v


def animations_enabled():
    """Respeta «reducir animaciones» del escritorio (gtk-enable-animations)."""
    try:
        from gi.repository import Gtk
        st = Gtk.Settings.get_default()
        return bool(st.get_property("gtk-enable-animations")) if st is not None else True
    except Exception:
        return True


class AnimatedValue:
    """Aguja con física de muelle amortiguado (x'' = -k(x - objetivo) - c·x'):
    llega en ≈0,4 s con un rebote muy ligero, como un instrumento real."""

    __slots__ = ("current", "target", "vel")
    K = 120.0
    C = 18.0

    def __init__(self, v=None, start=None):
        self.target = v
        self.current = start if (start is not None and v is not None) else v
        self.vel = 0.0

    def set(self, v):
        self.target = v
        if self.current is None or v is None:
            self.current = v
            self.vel = 0.0

    def snap(self):
        self.current = self.target
        self.vel = 0.0

    def step(self, dt):
        if self.current is None or self.target is None:
            return False
        d = self.target - self.current
        if abs(d) < 0.05 and abs(self.vel) < 0.05:
            self.current = self.target
            self.vel = 0.0
            return False
        dt = min(dt, 0.05)   # estable aunque se pierda un fotograma
        acc = self.K * d - self.C * self.vel
        self.vel += acc * dt
        self.current += self.vel * dt
        return True


def _owner_pid(bus):
    try:
        r = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "GetConnectionUnixProcessID", GLib.Variant("(s)", (DBUS_NAME,)),
                          GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 1000, None)
        return r.unpack()[0]
    except GLib.Error:
        return None


def _owner_in_systemd_unit(bus, owner=None, pid=None):
    """¿El proceso dueño del nombre corre dentro de la unidad sensorpulse.service?"""
    try:
        if pid is None:
            r = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                              "GetConnectionUnixProcessID", GLib.Variant("(s)", (owner or DBUS_NAME,)),
                              GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 1000, None)
            pid = r.unpack()[0]
        with open("/proc/%d/cgroup" % pid) as f:
            return any(line.rstrip().endswith("/sensorpulse.service") for line in f)
    except (GLib.Error, OSError, ValueError):
        return False


def _warn(text):
    try:
        from gi.repository import Gtk
        dlg = Gtk.MessageDialog(message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.OK,
                                text=text)
        dlg.connect("response", lambda d, _r: d.destroy())
        dlg.show()
    except Exception:
        pass


class Store:
    def __init__(self):
        self.cfg = cfgmod.load()
        self.snapshot = {"devices": [], "state": "nodata", "alarm_channels": []}
        self.anims = {}
        self.listeners = []
        self.frame_listeners = []
        self.client = None
        self.connected = False
        self._anim_source = None
        self._anim_period = None
        self._last_anim = time.monotonic()
        self.pulse = 0.0
        self.fan_angle = {}
        self.session_minmax = {}  # channel_id -> [min, max] desde que se abrió (o se reinició)
        self.local_hist = {}   # channel_id -> deque[(t, v)] (últimos ~10 min, para mini-gráficas)
        self.flash = {}             # canal -> momento en que volvió a la normalidad
        self.fan_draw_ids = set()   # ventiladores cuya hélice se está dibujando en alguna vista
        self.shown_ids = set()      # canales visibles en alguna vista (para el parpadeo)
        self.animate = animations_enabled()
        self.stale = False          # la última foto es demasiado antigua: no fiarse del valor
        self._stale_src = GLib.timeout_add_seconds(5, self._check_stale)
        self._watch_id = 0
        self._reconnect_src = 0
        self._last_rx = None        # momento (monótono) de la última foto recibida
        self._seen_owner = False    # el servicio llegó a estar en marcha en esta sesión
        self._managed = False       # lo gestiona systemd --user (se reinicia solo si cae)
        self._start_tries = 0
        self._save_warned = False

    # ------------------------------------------------------------ conexión
    def connect(self):
        """Conecta con el servicio (lo arranca si hace falta). Aunque no arranque a tiempo
        (inicio de sesión lento) se queda vigilando el bus: en cuanto aparezca, la interfaz
        recibe datos. La 0.3.5 se quedaba vacía para siempre en ese caso."""
        ok = False
        try:
            ok = ensure_service()
        except Exception:
            ok = False
        if self.client is None:
            self.client = Client()
            self.client.on_signal("Updated", self._on_update)
        if ok:
            try:
                self._on_update(self.client.snapshot())
            except Exception:
                pass
        self.connected = ok
        self._watch_service()
        return ok

    # ------------------------------------------------------------ datos caducados
    def _watch_service(self):
        """Vigila el nombre D-Bus del servicio: si desaparece, los valores en pantalla ya no
        son de fiar y se intenta volver a arrancarlo."""
        if self._watch_id:
            return
        try:
            self._watch_id = Gio.bus_watch_name(Gio.BusType.SESSION, DBUS_NAME,
                                                Gio.BusNameWatcherFlags.NONE,
                                                self._service_appeared, self._service_vanished)
        except Exception:
            self._watch_id = 0

    def _service_appeared(self, conn, name, owner):
        self._seen_owner = True
        self._start_tries = 0
        self._managed = _owner_in_systemd_unit(conn, owner)
        if self._reconnect_src:
            GLib.source_remove(self._reconnect_src)
            self._reconnect_src = 0
        if self.client is not None:
            try:
                self._on_update(self.client.snapshot())
                self.connected = True
            except Exception:
                pass

    def _service_vanished(self, conn, name):
        self._set_stale(True)
        if self._seen_owner and self._managed:
            # systemd --user lo vigila (Restart=always): si cayó, lo vuelve a arrancar él; si
            # no lo hace es que alguien lo ha parado a propósito (systemctl --user stop,
            # desinstalar). La 0.3.5 lo volvía a arrancar a los 3 s, peleando con el usuario.
            return
        if not self._reconnect_src:
            delay = 3 if self._seen_owner else min(30, 5 * (self._start_tries + 1))
            self._reconnect_src = GLib.timeout_add_seconds(delay, self._reconnect)

    def _reconnect(self):
        self._reconnect_src = 0
        self._start_tries += 1
        try:
            ensure_service(timeout_ms=3000)
        except Exception:
            pass
        # si sigue sin aparecer y nunca llegó a funcionar, se reintenta (con pausas mayores)
        if not self._seen_owner and self._start_tries < 12 and not self._reconnect_src:
            self._reconnect_src = GLib.timeout_add_seconds(min(30, 5 * (self._start_tries + 1)),
                                                           self._reconnect)
        return False

    def _set_stale(self, stale):
        if stale != self.stale:
            self.stale = stale
            for cb in list(self.listeners):
                cb()

    def _check_stale(self):
        # se mide desde que LLEGÓ la última foto (reloj monótono): con el reloj de pared,
        # tras un salto atrás del reloj del sistema un servicio congelado no se detectaba
        if self._last_rx is not None:
            try:
                limit = float(self.snapshot.get("stale_after") or 15.0)
            except (TypeError, ValueError):
                limit = 15.0
            self._set_stale(time.monotonic() - self._last_rx > limit)
        return True

    def state(self):
        """Estado global para colorear iconos: «sin datos» si la foto está caducada."""
        return "nodata" if self.stale else self.snapshot.get("state", "nodata")

    def _on_update(self, snap):
        before = set(self.snapshot.get("alarm_channels") or [])
        after = set(snap.get("alarm_channels") or [])
        now = time.monotonic()
        for cid in before - after:
            self.flash[cid] = now   # destello verde breve al volver a la normalidad
        self.snapshot = snap
        self._last_rx = now
        self.stale = False
        if not self.animate:
            self.flash = {}
        for d in snap.get("devices", []):
            for c in d["channels"]:
                a = self.anims.get(c["id"])
                if a is None:
                    # al aparecer, la aguja «barre» desde el mínimo (como un coche al arrancar)
                    start = c.get("vmin") if self.animate and c["kind"] in ("temp", "percent", "fan") else None
                    a = self.anims[c["id"]] = AnimatedValue(c["value"], start)
                a.set(c["value"])
                if not self.animate:
                    a.snap()
                if c["value"] is not None and c["kind"] != "status":
                    mm = self.session_minmax.get(c["id"])
                    if mm is None:
                        self.session_minmax[c["id"]] = [c["value"], c["value"]]
                    else:
                        mm[0] = min(mm[0], c["value"])
                        mm[1] = max(mm[1], c["value"])
                    q = self.local_hist.get(c["id"])
                    if q is None:
                        from collections import deque
                        q = self.local_hist[c["id"]] = deque(maxlen=300)
                    t = snap.get("time", time.time())
                    if not q or q[-1][0] != t:
                        q.append((t, c["value"]))
        shown = set()
        for d in snap.get("devices", []):
            for c in d["channels"]:
                if cfgmod.is_visible(self.cfg, c, d["cls"]):
                    shown.add(c["id"])
        for key in ("panel", "floating"):
            shown.update(c["id"] for c, _d in self.featured(key, 9))
        self.shown_ids = shown
        self._ensure_anim()
        for cb in list(self.listeners):
            cb()

    def subscribe(self, cb):
        self.listeners.append(cb)

    def unsubscribe(self, cb):
        if cb in self.listeners:
            self.listeners.remove(cb)
        if cb in self.frame_listeners:
            self.frame_listeners.remove(cb)

    def subscribe_frame(self, cb):
        """cb() en cada fotograma de animación (solo mientras algo se mueve)."""
        self.frame_listeners.append(cb)

    # ------------------------------------------------------------ animación
    FAST_MS = 33     # agujas en movimiento o alarma parpadeando
    SLOW_MS = 200    # solo ventiladores girando: 5 fotogramas por segundo bastan (0.3.7)
    FAN_MAX_SPEED = 2.4   # rad/s de la hélice a 3000 RPM o más

    def _ensure_anim(self, period=None):
        period = period or self.FAST_MS
        if self._anim_source is not None:
            if self._anim_period == period:
                return
            GLib.source_remove(self._anim_source)
        else:
            self._last_anim = time.monotonic()
        self._anim_period = period
        self._anim_source = GLib.timeout_add(period, self._anim_tick)

    def _anim_tick(self):
        now = time.monotonic()
        dt = now - self._last_anim
        self._last_anim = now
        # lista, no generador: any() se pararía en la primera aguja (fallo F6)
        moving = any([a.step(dt) for a in list(self.anims.values())])
        if self.flash and self.animate:
            self.flash = {k: t for k, t in self.flash.items() if now - t < 1.0}
            moving = moving or bool(self.flash)
        alarm = self.blinking()
        fans = self.has_spinning_fans()
        if alarm:
            # «respiración»: crítico ≈1 Hz, aviso ≈0,5 Hz (sin parpadeos rápidos)
            crit = "critical" in (self.snapshot.get("alarm_levels") or {}).values()
            period = 1.0 if crit else 2.0
            self.pulse = 0.5 + 0.5 * math.sin(now * 2 * math.pi / period)
        else:
            self.pulse = 0.0
        if fans:
            for cid, rpm in fans.items():
                # proporcional a las RPM pero lento: a 8 fps la 0.3.6 giraba más de un aspa por
                # fotograma (1450 RPM ≈ 1,8 rad) y parecía quieta o hacia atrás
                speed = self.FAN_MAX_SPEED * min(rpm, 3000) / 3000.0
                self.fan_angle[cid] = (self.fan_angle.get(cid, 0.0) + min(dt, 0.5) * speed) % (2 * math.pi)
        for cb in list(self.frame_listeners):
            cb()
        if not moving and not alarm and not fans:
            self._anim_source = None
            return False
        want = self.FAST_MS if (moving or alarm) else self.SLOW_MS
        if want != self._anim_period:
            self._anim_period = want
            self._anim_source = GLib.timeout_add(want, self._anim_tick)
            return False
        return True

    def animating_ids(self):
        """Canales que cambian de un fotograma a otro: agujas en movimiento, alarmas que
        laten, destellos de «vuelta a la normalidad» y hélices que giran."""
        out = {cid for cid, a in self.anims.items()
               if a.current is not None and a.target is not None and
               (abs(a.target - a.current) >= 0.05 or abs(a.vel) >= 0.05)}
        if self.blinking():
            out.update(self.snapshot.get("alarm_channels") or [])
        out.update(self.flash)
        out.update(self.has_spinning_fans())
        return out

    def blinking(self):
        """¿Hay que animar el aviso? Solo si la alarma es de un sensor que se ve."""
        if not self.animate or not self.cfg["alarms"].get("blink", True):
            return False
        active = self.snapshot.get("alarm_channels") or []
        return any(cid in self.shown_ids for cid in active)

    def has_spinning_fans(self):
        """Solo los ventiladores cuya hélice se dibuja de verdad en alguna vista (F7)."""
        if not self.animate or not self.cfg.get("animate_fans", True) or not self.fan_draw_ids:
            return {}
        out = {}
        for d in self.snapshot.get("devices", []):
            for c in d["channels"]:
                if c["kind"] == "fan" and c["value"] and c["id"] in self.fan_draw_ids:
                    out[c["id"]] = c["value"]
        return out

    # ------------------------------------------------------------ consultas
    def reload_cfg(self):
        # recargar EN EL MISMO diccionario: Ajustes y el asistente guardan una referencia
        # a store.cfg; si se sustituyera, sus cambios se perderían (fallo F1 de la 0.2.0)
        _merge_inplace(self.cfg, cfgmod.load())

    def save_cfg(self):
        try:
            cfgmod.save(self.cfg)
        except OSError as e:
            # disco lleno, carpeta de solo lectura o config.json ilegible (que no se pisa)
            print("sensorpulse: no se pudieron guardar los ajustes: %s" % e, file=sys.stderr)
            if not self._save_warned:
                self._save_warned = True
                _warn(_("The settings could not be saved (%s).") % (e.strerror or e))
            return
        if self.client is not None:
            try:
                self.client.reload()
            except Exception:
                pass

    def restart_service(self):
        """Reinicia el servicio si dejó de responder (botón de la barra de estado). Un servicio
        CONGELADO no atiende «Quit» (la 0.3.5 esperaba ~10 s y lo dejaba igual): con systemd
        se usa «systemctl --user restart»; sin él, se termina el proceso dueño del nombre."""
        import os
        import signal
        import subprocess
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        pid = _owner_pid(bus)
        restarted = False
        if pid and _owner_in_systemd_unit(bus, None, pid):
            try:
                restarted = subprocess.run(["systemctl", "--user", "restart", "sensorpulse.service"],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           timeout=20).returncode == 0
            except (OSError, subprocess.SubprocessError):
                restarted = False
        if not restarted and pid:
            try:
                if self.client is not None:
                    self.client.quit_service()
            except Exception:
                pass
            for sig, wait in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 1.0)):
                if _owner_pid(bus) != pid:
                    break
                try:
                    os.kill(pid, sig)
                except OSError:
                    break
                end = time.monotonic() + wait
                while time.monotonic() < end and _owner_pid(bus) == pid:
                    time.sleep(0.1)
        if not ensure_service():
            return False
        if self.client is None:
            # la interfaz se abrió con el servicio caído: no había cliente ni suscripción
            self.client = Client()
            self.client.on_signal("Updated", self._on_update)
        self.connected = True
        try:
            self._on_update(self.client.snapshot())
        except Exception:
            pass
        return True

    def devices(self):
        devs = self.snapshot.get("devices", [])
        order = self.cfg["sensors"].get("order") or []
        if order:
            devs = sorted(devs, key=lambda d: order.index(d["id"]) if d["id"] in order else 999)
        return devs

    def item(self, ch, dev=None):
        """Diccionario listo para dibujar."""
        a = self.anims.get(ch["id"])
        unit_f = self.cfg.get("unit") == "F" and ch["kind"] == "temp"
        conv = (lambda v: v * 9 / 5 + 32 if v is not None else None) if unit_f else (lambda v: v)
        it = dict(ch)
        it["label"] = cfgmod.display_name(self.cfg, ch)
        if self.stale:
            # el servicio no responde: nada de mostrar en verde una temperatura que ya no existe
            it["anim"] = it["value"] = None
            it["state"] = "nodata"
        else:
            it["anim"] = conv(a.current if a else ch["value"])
            it["value"] = conv(ch["value"])
        if ch["kind"] == "temp":
            it["warn"], it["crit"] = conv(ch["warn"]), conv(ch["crit"])
            it["vmin"], it["vmax"] = conv(ch["vmin"]), conv(ch["vmax"])
            it["unit"] = "°F" if unit_f else "°C"
        it["asleep"] = bool(dev and dev.get("asleep"))
        it["alarm"] = ch["id"] in self.snapshot.get("alarm_channels", []) and self.animate
        it["alarm_level"] = (self.snapshot.get("alarm_levels") or {}).get(ch["id"])
        if ch.get("tjmax") is not None:
            it["tjmax"] = conv(ch["tjmax"])
        it["cb"] = self.cfg.get("colorblind", False)
        return it

    def visible_channels(self, dev):
        return [c for c in dev["channels"] if cfgmod.is_visible(self.cfg, c, dev["cls"])]

    def featured(self, key, limit=6):
        """Canales para el panel/flotante: elegidos por el usuario o automáticos."""
        chosen = self.cfg["sensors"].get(key) or []
        index = {c["id"]: (c, d) for d in self.snapshot.get("devices", []) for c in d["channels"]}
        if chosen:
            return [index[c] for c in chosen if c in index][:limit]
        out = []
        prefs = ["cpu", "gpu", "disk", "battery", "fans", "memory"]
        for cls in prefs:
            for d in self.snapshot.get("devices", []):
                if d["cls"] != cls:
                    continue
                prim = next((c for c in d["channels"] if c["primary"]), None)
                if prim and cfgmod.is_visible(self.cfg, prim, d["cls"]):
                    out.append((prim, d))
                    break
        return out[:limit]

    def find(self, cid):
        for d in self.snapshot.get("devices", []):
            for c in d["channels"]:
                if c["id"] == cid:
                    return c, d
        return None, None

