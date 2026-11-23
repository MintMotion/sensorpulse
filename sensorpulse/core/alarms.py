"""Motor de alarmas.

Tres niveles:
- Informativo: solo color (lo hace la interfaz con warn/crit; aquí no genera eventos).
- Aviso (ST_HOT): notificación sin sonido.
- Crítico (ST_CRIT): notificación + sonido (si está activado) y repetición opcional.

Alarmas automáticas:
- Límite crítico de cada temperatura (umbral del hardware o de la familia de CPU).
- Aviso al llegar al umbral «alto» (opcional).
- La CPU/GPU baja su velocidad por calor (contadores del hardware).
- «Caliente sin trabajar»: temperatura alta con la CPU casi sin carga (refrigeración).
- Ventilador parado: un ventilador que giraba marca 0 RPM con el equipo caliente.

Y las reglas manuales del usuario. Cada regla tiene retardo (debe mantenerse X s)
e histéresis (no se repite hasta volver por debajo de umbral - histéresis).
"""

import time
import uuid

from .model import TEMP, FAN, ST_HOT, ST_CRIT, CPU, GPU

ABOVE = "above"
BELOW = "below"

CPU_LOAD_ID = "cpu@proc/load"
CPU_COREMAX_ID = "cpu@proc/coremax"
THROTTLE_CRIT_DELAY = 60   # s en el límite y frenando para la alarma crítica
WARN_COOLDOWN = 1800       # s: como mucho un aviso del mismo tipo cada 30 min
IDLE_LOAD = 15.0           # % de carga por debajo del cual la CPU está «sin trabajar»
IDLE_DELAY = 120           # s que debe mantenerse para avisar
IDLE_CRIT_LOAD = 30.0      # % para la versión crítica (en el límite y sin carga)
IDLE_CRIT_DELAY = 30
THROTTLE_DELAY = 30
FANSTOP_DELAY = 30
FANSTOP_CPU_TEMP = 60.0    # °C: con la CPU por debajo, un ventilador parado puede ser normal
FANSTOP_GPU_TEMP = 75.0    # °C: las GPU paran el ventilador a propósito en reposo
MAX_MUTE_MIN = 24 * 60     # el silencio no puede pasar de un día
NODATA_CLEAR = 60          # s sin dato del sensor para dar por cerrada una alarma activa


def rule_level(r):
    """Nivel de una regla, en un solo sitio: el guardado (automáticas y reglas manuales
    nuevas) o, en reglas manuales antiguas, crítico para «sube de» y aviso para «baja de»."""
    return r.get("level") or (ST_CRIT if r.get("op", ABOVE) == ABOVE else ST_HOT)


def idle_threshold(c, low_power=False):
    """Temperatura «demasiado alta para estar sin trabajar». En portátiles de bajo consumo
    sin ventilador detectado (suelen ir más calientes en reposo) el margen es mayor."""
    ref = c.tjmax or c.crit or 95.0
    if low_power:
        return max(75.0, ref - 15.0)
    return max(70.0, min(80.0, ref - 25.0))


def is_low_power(model):
    import re
    m = model or ""
    return bool(re.search(r"\d{4}[UY]\b|\bARM\b|Cortex|Apple|Raspberry", m, re.I))


def new_rule(channel, value, op=ABOVE, delay=10, hysteresis=None, notify=True,
             sound=True, visual=True, sound_name="alarm-clock-elapsed", repeat=False,
             enabled=True, level=ST_CRIT):
    return {
        "id": uuid.uuid4().hex[:10], "channel": channel, "op": op, "value": float(value),
        "delay": int(delay), "hysteresis": hysteresis, "notify": bool(notify),
        "sound": bool(sound), "visual": bool(visual), "sound_name": sound_name,
        "repeat": bool(repeat), "enabled": bool(enabled), "level": level,
    }


class _RuleState:
    __slots__ = ("since", "active", "last_sound", "peak", "started", "last_fired", "acked",
                 "repeats", "nodata_since")

    def __init__(self):
        self.since = None
        self.active = False
        self.last_sound = 0.0
        self.peak = None
        self.started = None
        self.last_fired = None     # None: nunca (con reloj monótono 0 sería «hace poco»)
        self.acked = False
        self.repeats = 0
        self.nodata_since = None


class AlarmEngine:
    REPEAT_EVERY = 120.0

    def __init__(self, settings):
        self.settings = settings     # dict "alarms" de la configuración
        self.rules = []              # reglas manuales
        self.state = {}              # rule_id -> _RuleState
        self._last_rules = {}        # rule_id -> última definición evaluada
        self.muted_until = 0.0       # hora de pared (para mostrar «silenciado hasta…»)
        self._mute_end = 0.0         # fin real del silencio, en reloj monótono
        self._wall = None            # hora de pared de la evaluación en curso
        self.log = []                # historial reciente de alarmas (máx. 200)
        self.snoozed = {}            # tipo de aviso -> hasta cuándo no avisar («hoy no»)
        self._was_muted = False
        self._evaluated = set()
        self._nodata = set()

    def configure(self, settings, rules):
        self.settings = settings
        self.rules = list(rules or [])
        # los estados de reglas borradas que siguen activos se cierran en la próxima
        # evaluación (para avisar «cleared» y cerrar la notificación); los inactivos, fuera
        ids = {r["id"] for r in self.rules}
        for k in list(self.state):
            if ":" not in k and k not in ids and not self.state[k].active:
                del self.state[k]
                self._last_rules.pop(k, None)

    def mute(self, minutes):
        """Silencia sonido y avisos no críticos. Máximo 24 h: pasado ese tiempo (o con un valor
        absurdo recibido por D-Bus) las alarmas vuelven solas."""
        try:
            minutes = max(0, min(MAX_MUTE_MIN, int(minutes)))
        except (TypeError, ValueError):
            minutes = 0
        if minutes > 0:
            # el fin se mide con el reloj monótono: un salto del reloj del sistema no puede
            # alargar el silencio (ni acortarlo); muted_until es solo para mostrarlo
            self._mute_end = time.monotonic() + 60 * minutes
            self.muted_until = time.time() + 60 * minutes
        else:
            self._mute_end = 0.0
            self.muted_until = 0.0

    @property
    def muted(self):
        if self._mute_end and time.monotonic() >= self._mute_end:
            self._mute_end = 0.0
            self.muted_until = 0.0
        return self._mute_end > 0.0

    def mute_remaining(self):
        """Segundos de silencio que quedan (0 si no hay)."""
        return max(0.0, self._mute_end - time.monotonic()) if self.muted else 0.0

    def ack(self, rule_id):
        """«Entendido»: deja de repetir el sonido de esa alarma mientras dure."""
        st = self.state.get(rule_id)
        if st is not None:
            st.acked = True

    def snooze_kind(self, kind, until):
        """«No volver a avisar hoy» para un tipo de aviso (throttle, idle, fanstop)."""
        self.snoozed[kind] = until

    # ------------------------------------------------------------ reglas
    def _base(self, rid, c, level, delay, **kw):
        s = self.settings
        r = {"id": rid, "channel": c.id, "op": ABOVE, "value": c.crit, "delay": int(delay),
             "hysteresis": s.get("hysteresis", 5), "notify": s.get("notify", True),
             "sound": False, "visual": True, "sound_name": s.get("sound_name", "alarm-clock-elapsed"),
             "repeat": False, "enabled": True, "auto": True, "level": level, "kind": "threshold"}
        if level == ST_CRIT:
            r["sound"] = s.get("sound", False)
            r["repeat"] = s.get("repeat", False)
        else:
            r["sound"] = s.get("warn_sound", False)   # tono suave, desactivado por defecto
        r.update(kw)
        return r

    def _auto_rules(self, channels):
        s = self.settings
        if not s.get("enabled", True):
            return []
        out = []
        for c in channels.values():
            if c.kind != TEMP or c.crit is None:
                continue
            out.append(self._base("auto:crit:" + c.id, c, ST_CRIT, s.get("delay", 10)))
            if s.get("notify_hot", False) and c.warn is not None:
                out.append(self._base("auto:hot:" + c.id, c, ST_HOT, s.get("delay_warn", 30),
                                      value=c.warn))
        return out

    # ------------------------------------------------------------ evaluación
    def evaluate(self, channels, now=None, devices=None, fan_peak=None, mono=None):
        """channels: dict id -> Channel. Devuelve lista de eventos.
        now: hora de pared (fecha de los eventos). mono: reloj monótono para retardos,
        histéresis temporal, repeticiones y pausas: un salto del reloj del sistema no debe
        impedir ni adelantar una alarma (fallo C2 de la 0.3.5). Sin mono, now hace de línea
        de tiempo (pruebas con tiempo simulado)."""
        tl = mono if mono is not None else (now if now is not None else time.monotonic())
        self._wall = now or time.time()
        now = tl
        events = []
        default_h = float(self.settings.get("hysteresis", 5))
        self._evaluated = set()     # reglas evaluadas con dato en esta pasada
        self._nodata = set()        # reglas vigentes cuyo sensor no da dato ahora
        for r in self._auto_rules(channels) + [x for x in self.rules if x.get("enabled", True)]:
            c = channels.get(r["channel"])
            if c is None:
                continue
            v = c.value
            if v is None:
                self.state.setdefault(r["id"], _RuleState()).since = None
                self._nodata.add(r["id"])
                continue
            thr = float(r["value"])
            h = r.get("hysteresis")
            h = default_h if h is None else float(h)
            if r["op"] == ABOVE:
                cond, clear = v >= thr, v < thr - h
            else:
                cond, clear = v <= thr, v > thr + h
            self._step(r, c, v, cond, clear, now, events)
        if self.settings.get("enabled", True):
            self._smart(channels, devices or [], fan_peak, now, events)
        self._close_orphans(channels, now, events)
        # al acabar el silencio, volver a anunciar las alarmas críticas que siguen activas
        muted = self.muted
        if self._was_muted and not muted:
            for rid, st in self.state.items():
                r = self._last_rules.get(rid)
                c = channels.get(r["channel"]) if r else None
                if st.active and r and rule_level(r) == ST_CRIT and c is not None and c.value is not None:
                    ev = self._event("fired", r, c, c.value, now)
                    ev["reminder"] = True
                    events.append(ev)
        self._was_muted = muted
        for e in events:
            if e["type"] != "repeat":
                self.log.append(e)
        del self.log[:-200]
        return events

    def _close_orphans(self, channels, now, events):
        """Cierra las alarmas activas que ya no se evalúan: regla desactivada o borrada,
        alarmas apagadas, sensor desaparecido, o sensor sin dato durante NODATA_CLEAR s
        (GPU dormida, disco en reposo, lectura que falla)."""
        for rid, st in list(self.state.items()):
            if rid in self._evaluated:
                st.nodata_since = None
                continue
            if not st.active:
                continue
            if rid in self._nodata:
                if st.nodata_since is None:
                    st.nodata_since = now
                if now - st.nodata_since < NODATA_CLEAR:
                    continue
                reason = "nodata"
            else:
                reason = "disabled"
            st.active, st.since, st.nodata_since = False, None, None
            r = self._last_rules.get(rid)
            if r is None:
                continue
            ev = self._event("cleared", r, channels.get(r["channel"]), None, now, st.peak)
            ev["reason"] = reason
            ev["duration"] = now - (st.started or now)
            events.append(ev)
            if ":" not in rid and not any(x["id"] == rid for x in self.rules):
                del self.state[rid]
                self._last_rules.pop(rid, None)

    def _step(self, r, c, v, cond, clear, now, events):
        st = self.state.setdefault(r["id"], _RuleState())
        r = dict(r, _label=c.label)
        self._last_rules[r["id"]] = r
        self._evaluated.add(r["id"])
        if not st.active:
            if cond:
                if st.since is None:
                    st.since = now
                if now - st.since >= float(r.get("delay", 0)):
                    st.active = True
                    st.peak = v
                    st.started = now
                    st.last_sound = now
                    st.acked = False
                    st.repeats = 0
                    # avisos repetitivos (freno, sin trabajar, ventilador): como mucho 1 cada
                    # 30 min, y nada si el usuario pidió «hoy no»; los críticos, siempre
                    quiet = r.get("level") != ST_CRIT and (
                        (st.last_fired is not None and
                         now - st.last_fired < float(r.get("cooldown", 0))) or
                        self._wall < self.snoozed.get(r.get("kind"), 0))
                    st.last_fired = now if not quiet else st.last_fired
                    ev = self._event("fired", r, c, v, now)
                    if quiet:
                        ev["notify"] = ev["sound"] = False
                        ev["quiet"] = True
                    events.append(ev)
            else:
                st.since = None
        else:
            if r.get("op", ABOVE) == ABOVE:
                st.peak = max(st.peak if st.peak is not None else v, v)
            else:
                st.peak = min(st.peak if st.peak is not None else v, v)
            if clear:
                st.active = False
                st.since = None
                ev = self._event("cleared", r, c, v, now, st.peak)
                ev["duration"] = now - (st.started or now)
                events.append(ev)
            elif r.get("repeat") and r.get("sound") and not st.acked and \
                    now - st.last_sound >= self.REPEAT_EVERY:
                st.last_sound = now
                st.repeats += 1
                ev = self._event("repeat", r, c, v, now)
                ev["repeats"] = st.repeats
                events.append(ev)

    def _smart(self, channels, devices, fan_peak, now, events):
        s = self.settings
        cls_of = {d.id: d.cls for d in devices}
        # carga de los núcleos más ocupados (si no, la total)
        load_ch = channels.get(CPU_COREMAX_ID)
        if load_ch is None or load_ch.value is None:
            load_ch = channels.get(CPU_LOAD_ID)
        load = load_ch.value if load_ch is not None else None
        cpu_dev = next((d for d in devices if d.cls == CPU), None)
        has_fans = any(c.kind == FAN for c in channels.values())
        low_power = not has_fans and is_low_power(cpu_dev.model if cpu_dev else "")
        prim = {}
        for c in channels.values():
            if c.kind == TEMP and c.primary and cls_of.get(c.device_id) in (CPU, GPU):
                prim.setdefault(cls_of[c.device_id], []).append(c)
        cpu_t = prim.get(CPU, [None])[0]

        if s.get("throttle", True):
            for c in prim.get(CPU, []) + prim.get(GPU, []):
                r = self._base("auto:throttle:" + c.id, c, ST_HOT, THROTTLE_DELAY,
                               kind="throttle", value=c.value or 0, hysteresis=0,
                               cooldown=WARN_COOLDOWN)
                if c.value is None:
                    self._nodata.update((r["id"], "auto:throttlecrit:" + c.id))
                    continue
                self._step(r, c, c.value, c.throttling, not c.throttling, now, events)
                # crítico: en su límite durante 1 minuto Y bajando la velocidad: así se detecta
                # un disipador que falla con carga, sin saltar en un renderizado normal
                if c.tjmax:
                    r = self._base("auto:throttlecrit:" + c.id, c, ST_CRIT, THROTTLE_CRIT_DELAY,
                                   kind="throttlecrit", value=c.tjmax)
                    self._step(r, c, c.value, c.value >= c.tjmax - 1 and c.throttling,
                               c.value < c.tjmax - 6 or not c.throttling, now, events)

        if s.get("idle_hot", True) and cpu_t is not None and (cpu_t.value is None or load is None):
            self._nodata.update(("auto:idle:" + cpu_t.id, "auto:idlecrit:" + cpu_t.id))
        if s.get("idle_hot", True) and cpu_t is not None and cpu_t.value is not None \
                and load is not None:
            thr = idle_threshold(cpu_t, low_power)
            r = self._base("auto:idle:" + cpu_t.id, cpu_t, ST_HOT, IDLE_DELAY,
                           kind="idle", value=thr, load=load, cooldown=WARN_COOLDOWN)
            self._step(r, cpu_t, cpu_t.value, cpu_t.value >= thr and load < IDLE_LOAD,
                       cpu_t.value < thr - 5 or load >= IDLE_LOAD + 20, now, events)
            limit = cpu_t.tjmax or cpu_t.crit
            if limit:
                r = self._base("auto:idlecrit:" + cpu_t.id, cpu_t, ST_CRIT, IDLE_CRIT_DELAY,
                               kind="idle", value=limit, load=load)
                self._step(r, cpu_t, cpu_t.value,
                           cpu_t.value >= limit - 1 and load < IDLE_CRIT_LOAD,
                           cpu_t.value < limit - 6 or load >= IDLE_CRIT_LOAD + 20, now, events)

        if s.get("fan_stop", True) and fan_peak is not None:
            for c in channels.values():
                if c.kind == FAN and c.value is None:
                    self._nodata.add("auto:fanstop:" + c.id)
                if c.kind != FAN or c.value is None:
                    continue
                if fan_peak(c.id) <= 0:
                    continue  # nunca lo hemos visto girar: puede no estar conectado
                is_gpu = cls_of.get(c.device_id) == GPU
                ref = prim.get(GPU, [None])[0] if is_gpu else cpu_t
                if is_gpu:
                    ref = next((t for t in prim.get(GPU, []) if t.device_id == c.device_id), ref)
                if ref is None or ref.value is None:
                    self._nodata.add("auto:fanstop:" + c.id)
                    continue
                lim = FANSTOP_GPU_TEMP if is_gpu else FANSTOP_CPU_TEMP
                r = self._base("auto:fanstop:" + c.id, c, ST_HOT, FANSTOP_DELAY,
                               kind="fanstop", value=0, op=BELOW, ref=ref.label,
                               ref_value=ref.value, cooldown=WARN_COOLDOWN)
                self._step(r, c, c.value, c.value <= 0 and ref.value >= lim,
                           c.value > 0, now, events)

    def _event(self, typ, r, c, v, now, peak=None):
        return {
            "type": typ, "rule": r["id"], "auto": bool(r.get("auto")),
            "level": rule_level(r), "kind": r.get("kind", "threshold"),
            "channel": r["channel"], "label": c.label if c is not None else r.get("_label", ""),
            "value": v, "threshold": r["value"],
            "op": r.get("op", ABOVE), "peak": peak,
            "time": self._wall if self._wall is not None else time.time(),
            "load": r.get("load"), "ref": r.get("ref"), "ref_value": r.get("ref_value"),
            # en silencio no suena nada, pero una alarma CRÍTICA nueva sigue mostrando su
            # notificación: silenciar no debe poder ocultar que algo se está quemando
            "notify": r.get("notify", True) and (not self.muted or rule_level(r) == ST_CRIT),
            "sound": r.get("sound", False) and not self.muted,
            "sound_name": r.get("sound_name") or "alarm-clock-elapsed",
            "visual": r.get("visual", True),
        }

    def active_rules(self):
        """Reglas activas (para «Entendido» y para reemplazar notificaciones)."""
        return [rid for rid, st in self.state.items() if st.active]

    def active_channels(self):
        """Canales con alguna alarma activa que pide aviso visual (parpadeo)."""
        out = set()
        for rid, st in self.state.items():
            if not st.active:
                continue
            if rid.startswith("auto:"):
                out.add(rid.split(":", 2)[2])
            else:
                r = next((x for x in self.rules if x["id"] == rid), None)
                if r and r.get("visual", True):
                    out.add(r["channel"])
        return out

    def active_levels(self):
        """Canal -> nivel más alto de alarma activa."""
        out = {}
        for rid, st in self.state.items():
            if not st.active:
                continue
            if rid.startswith("auto:"):
                kind, cid = rid.split(":", 2)[1:]
                lvl = ST_CRIT if kind in ("crit", "idlecrit", "throttlecrit") else ST_HOT
            else:
                r = next((x for x in self.rules if x["id"] == rid), None)
                if not r:
                    continue
                cid, lvl = r["channel"], rule_level(r)
            if out.get(cid) != ST_CRIT:
                out[cid] = lvl
        return out
