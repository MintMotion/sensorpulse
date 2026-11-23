"""Motor de lectura: programa las lecturas por dispositivo, respeta el reposo
(runtime PM), calcula estados y guarda el histórico en memoria."""

import glob
import os
import sys
import time
from collections import deque

from . import sysfs
from .discovery import discover
from .nvml import discover_nvidia
from .model import (
    TEMP, FAN, STATUS, ST_SLEEP, ST_NORMAL,
    ST_NODATA, STATE_RANK, CPU, GPU, Channel, temp_state,
)
from ..i18n import _

SLOW_READ = 0.1          # s: una lectura más lenta se considera «lenta»
HYBRID_GPU_INTERVAL = 30.0  # s: dGPU de portátil híbrido (dejarla dormir)
THROTTLE_HOLD = 15.0     # s que se mantiene la marca de «bajando velocidad»
HIDDEN_TEMP_INTERVAL = 60.0  # s: temperaturas ocultas, solo para la alarma crítica
HDD_WARM_INTERVAL = 120.0   # s: disco duro templado (vigilancia más estrecha)
HDD_WARM_MARGIN = 8.0       # °C por debajo del aviso a partir de los que se considera templado
AMD_LEARN_LOAD = 60.0    # % de carga (núcleos más ocupados) para aprender la frecuencia sostenida
AMD_DROP = 0.75          # bajar de este factor de la frecuencia aprendida = frenando

HISTORY_SECONDS = 3600  # 1 h en memoria (el histórico largo va a SQLite en v0.5)


def _pci_runtime_pm(pm_path):
    """¿La GPU tiene reposo automático activado (power/control = auto)?"""
    if not pm_path:
        return False
    ctl = os.path.join(os.path.dirname(pm_path), "control")
    return (sysfs.read_text(ctl, "") or "").strip() == "auto"


class History:
    def __init__(self, seconds=HISTORY_SECONDS):
        self.seconds = seconds
        self.data = {}

    def add(self, cid, t, v):
        q = self.data.get(cid)
        if q is None:
            q = self.data[cid] = deque()
        q.append((t, v))
        limit = t - self.seconds
        while q and q[0][0] < limit:
            q.popleft()

    def get(self, cid, seconds=None):
        q = self.data.get(cid, ())
        if not seconds:
            return list(q)
        limit = time.time() - seconds
        return [(t, v) for (t, v) in q if t >= limit]


def compute_state(ch):
    if ch.value is None:
        return ST_NODATA
    if ch.kind != TEMP or ch.warn is None:
        return ST_NORMAL
    return temp_state(ch.value, ch.warn, ch.crit, bool(getattr(ch, "throttling", False)))


class Engine:
    def __init__(self, fast_interval=2.0, hdd_interval=1800, skip_drivers=(), ssd_interval=10,
                 hdd_adaptive=True):
        self.skip_drivers = tuple(skip_drivers)
        self.fast = float(fast_interval)
        self.hdd = float(hdd_interval)
        self.ssd = float(ssd_interval)      # SSD SATA: no tiene reposo que proteger
        self.hdd_adaptive = bool(hdd_adaptive)
        self.wanted = None          # ids de canal a leer (None = todos)
        self._ch_next = {}
        self._thr_files = []
        self._thr_prev = None
        self._thr_last = 0.0
        self._cpu_prev_cores = None
        self.core_top = None        # carga media de los 2 núcleos más ocupados
        self._amd_ref = 0.0         # frecuencia máxima sostenida aprendida con carga
        self._amd_samples = 0
        self.udisks = None          # estado de energía de discos sin despertarlos (UDisks2)
        self.devices = []
        self.channels = {}
        self.history = History()
        self.fds = sysfs.FdCache()
        self.nvml = None
        self._next = {}
        self._cpu_prev = None
        self._fan_peak = {}
        self._first = True
        self.rescan_needed = False
        self.last_read = set()
        self.rescan()

    # ------------------------------------------------------------ descubrimiento
    def rescan(self):
        """Vuelve a buscar sensores. Conserva el calendario de lecturas de los dispositivos
        que ya existían: un «Buscar sensores» no debe leer el disco SATA (fallo F3)."""
        self.fds.close()
        if self.nvml is not None:
            self.nvml.shutdown()   # no dejar una NVML abierta al sustituirla (F19)
        devs = discover(self.skip_drivers)
        nv_devs, nv = discover_nvidia()
        if nv_devs:
            # insertar las NVIDIA tras la última GPU/CPU
            idx = max([i for i, d in enumerate(devs) if d.cls in (CPU, GPU)] or [0]) + 1
            devs[idx:idx] = nv_devs
        self.nvml = nv
        # conservar la última lectura de los sensores que ya existían: los que no toca leer
        # todavía (disco SATA cada 30 min) no deben quedarse en blanco tras el rescan
        old = getattr(self, "channels", {}) or {}
        for d in devs:
            for c in d.channels:
                prev = old.get(c.id)
                if prev is None:
                    continue
                if c.value is None:
                    c.value = prev.value
                # lo aprendido en marcha no se pierde al volver a buscar: cabeceras de
                # ventilador sin nada conectado (ocultas al arrancar) y sensores lentos
                if prev.note == "zero_rpm" and not c.note:
                    c.advanced, c.note = True, "zero_rpm"
                if prev.slow:
                    c.slow, c.interval = True, max(c.interval, prev.interval)
        self.devices = devs
        self.channels = {c.id: c for d in devs for c in d.channels}
        self.apply_intervals()
        ids = {d.id for d in devs}
        self._next = {k: v for k, v in self._next.items() if k in ids}
        self._thr_files = sorted(
            glob.glob(sysfs.p("sys/devices/system/cpu/cpu[0-9]*/thermal_throttle/core_throttle_count")) +
            glob.glob(sysfs.p("sys/devices/system/cpu/cpu[0-9]*/thermal_throttle/package_throttle_count")))
        self._thr_prev = None

    def apply_intervals(self):
        for d in self.devices:
            if d.driver == "drivetemp":
                # SSD SATA: leerlo a menudo no afecta a su reposo ni a su vida útil. Disco duro
                # (o desconocido): lo que elija el usuario (30 min por defecto)
                d.interval = max(self.fast, self.ssd) if d.media == "ssd" else self.hdd
            elif d.driver == "nvidia":
                d.hybrid = _pci_runtime_pm(d.pm_path)
                d.interval = max(self.fast, HYBRID_GPU_INTERVAL) if d.hybrid else self.fast
            else:
                if d.base_interval < 0:     # primera vez: lo que fijó el descubrimiento
                    d.base_interval = d.interval if d.interval >= 5 else 0.0
                # el intervalo elegido por el usuario manda, salvo en los sensores lentos
                # por naturaleza (nunca más rápido que su mínimo)
                d.interval = max(self.fast, d.base_interval)

    def set_wanted(self, ids):
        """Solo se leen estos canales (los visibles y los que usan las alarmas)."""
        self.wanted = set(ids) if ids is not None else None

    # ------------------------------------------------------------ lecturas
    def _asleep(self, dev):
        if not dev.pm_path:
            return False
        st = sysfs.read_text(dev.pm_path, "active") or "active"
        return st.strip() == "suspended"

    def poll(self, now=None, force=False, mono=None):
        """Lee los dispositivos cuyo intervalo ha vencido. Devuelve True si algo cambió.
        now: hora de pared (solo para fechar los datos). mono: reloj monótono para el
        calendario de lecturas: si el reloj del sistema salta (NTP, cambio de hora, reanudar)
        las lecturas no se congelan ni se disparan (fallo C1 de la 0.3.5). Sin mono se usa
        now como línea de tiempo (pruebas con tiempo simulado)."""
        tl = mono if mono is not None else (now if now is not None else time.monotonic())
        now = now or time.time()
        changed = False
        any_nvidia_awake = False
        self.last_read = set()     # canales leídos de verdad en esta pasada
        for dev in self.devices:
            due = self._next.get(dev.id, 0)
            if not force and tl < due:
                if dev.driver == "nvidia" and not dev.asleep:
                    any_nvidia_awake = True
                continue
            self._next[dev.id] = tl + dev.interval
            asleep = self._asleep(dev)
            dev.asleep = asleep
            if dev.limits_pending and not asleep:
                # se descubrió dormido (sin leer límites): ahora que está despierto, releerlos
                dev.limits_pending = False
                self.rescan_needed = True
            if asleep:
                for c in dev.channels:
                    c.value = None
                    c.state = ST_SLEEP
                dev.state = ST_SLEEP
                changed = True
                continue
            if dev.driver == "nvidia":
                any_nvidia_awake = True
                self._read_nvidia(dev)
                self.last_read.update(c.id for c in dev.channels if c.value is not None)
                if dev.hybrid and self.nvml is not None:
                    # portátil híbrido: no dejar NVML abierto entre lecturas
                    self.nvml.shutdown()
            else:
                if dev.driver == "drivetemp" and self._disk_standby(dev):
                    # el disco está en reposo (UDisks2): no leerlo para no despertarlo
                    dev.asleep = True
                    for c in dev.channels:
                        c.value = None
                        c.state = ST_SLEEP
                    dev.state = ST_SLEEP
                    changed = True
                    continue
                for c in dev.channels:
                    interval = c.interval
                    if self.wanted is not None and c.id not in self.wanted:
                        if c.kind == TEMP and c.crit is not None:
                            # oculta: se sigue vigilando, despacio, solo para la alarma crítica
                            interval = max(interval, HIDDEN_TEMP_INTERVAL)
                        else:
                            c.value = None
                            continue
                    if interval > dev.interval and not force:
                        if tl < self._ch_next.get(c.id, 0):
                            continue
                        self._ch_next[c.id] = tl + interval
                    t0 = time.monotonic()
                    self._read_channel(dev, c)
                    dt = time.monotonic() - t0
                    self.last_read.add(c.id)
                    if dt > SLOW_READ:
                        self._slow_down(c, dev, dt, tl)
            if dev.driver == "drivetemp" and dev.media != "ssd":
                self._hdd_warmth(dev, tl)
            if dev.cls == CPU:
                self._update_throttle(dev, tl)
            worst = ST_NODATA
            for c in dev.channels:
                c.state = compute_state(c)
                # solo lo leído ahora: un canal que no tocaba leer (lento, disco cada 30 min)
                # no debe aparecer en el historial como una línea plana de valores repetidos
                if c.value is not None and c.kind != STATUS and c.id in self.last_read:
                    self.history.add(c.id, now, c.value)
                if STATE_RANK.get(c.state, 0) > STATE_RANK.get(worst, 0):
                    worst = c.state
            dev.state = worst
            changed = True
        if self._first:
            # cabeceras de ventilador sin nada conectado (0 RPM al arrancar): ocultas por defecto
            gpu_ids = {d.id for d in self.devices if d.cls == GPU}
            for c in self.channels.values():
                # solo cabeceras de placa base: las gráficas paran el ventilador a propósito
                if c.kind == FAN and c.value == 0 and c.device_id not in gpu_ids:
                    c.advanced = True
                    c.note = "zero_rpm"
            self._first = False
        # cerrar NVML si todas las NVIDIA duermen (para que puedan seguir durmiendo)
        if self.nvml is not None and self.nvml.ready and not any_nvidia_awake:
            self.nvml.shutdown()
        return changed

    def _hdd_warmth(self, dev, tl):
        """Un intervalo largo deja descansar al disco duro, pero puede ocultar que se está
        calentando. Si la última lectura está cerca del aviso (a 8 °C o menos), se vuelve a
        leer cada HDD_WARM_INTERVAL hasta que se enfríe: un disco tan templado está
        trabajando, así que leerlo no le quita reposo."""
        prim = next((c for c in dev.channels if c.kind == TEMP and c.value is not None), None)
        dev.warm = bool(self.hdd_adaptive and prim is not None and prim.warn is not None and
                        prim.value >= prim.warn - HDD_WARM_MARGIN)
        if dev.warm and dev.interval > HDD_WARM_INTERVAL:
            self._next[dev.id] = tl + HDD_WARM_INTERVAL

    def _slow_down(self, c, dev, dt, now):
        old = max(c.interval, dev.interval)
        c.interval = min(old * 2, 60.0)
        c.slow = True
        self._ch_next[c.id] = now + c.interval
        print("sensorpulse: %s tarda %.2f s en leerse; ahora cada %.0f s"
              % (c.id, dt, c.interval), file=sys.stderr)

    def _update_throttle(self, dev, now):
        """Intel: los contadores thermal_throttle suben cuando la CPU baja su velocidad por calor.
        AMD (sin contadores): se aprende la frecuencia que el equipo sostiene con carga y se
        considera «frenando» si, en el límite de temperatura, cae muy por debajo."""
        if not self._thr_files:
            self._amd_throttle(dev, now)
            return
        total = 0
        for f in self._thr_files:
            v = self.fds.read_int(f)
            if v:
                total += v
        if self._thr_prev is not None and total > self._thr_prev:
            self._thr_last = now
        self._thr_prev = total
        thr = (now - self._thr_last) < THROTTLE_HOLD and self._thr_last > 0
        for c in dev.channels:
            if c.kind == TEMP:
                c.throttling = thr

    def _amd_throttle(self, dev, now):
        temp = next((c for c in dev.channels if c.kind == TEMP and c.primary), None)
        freq = self.channels.get("cpu@proc/freq")
        if temp is None or not temp.tjmax or temp.value is None or freq is None \
                or freq.value is None or self.core_top is None:
            return
        tj = temp.tjmax
        if self.core_top >= AMD_LEARN_LOAD and temp.value < tj - 5:
            self._amd_ref = max(self._amd_ref * 0.9995, freq.value)
            self._amd_samples += 1
        learned = self._amd_samples >= 30 and self._amd_ref > 0
        thr = learned and temp.value >= tj - 1 and self.core_top >= AMD_LEARN_LOAD \
            and freq.value < AMD_DROP * self._amd_ref
        for c in dev.channels:
            if c.kind == TEMP:
                c.throttling = bool(thr)

    def _disk_standby(self, dev):
        """True si UDisks2 dice que el disco está en reposo (sin despertarlo)."""
        try:
            if self.udisks is None:
                from .udisks import DiskPower
                self.udisks = DiskPower()
            return self.udisks.standby(dev)
        except Exception:
            return False

    def _read_channel(self, dev, c):
        cid = c.id
        if cid == "cpu@proc/coremax":
            c.value = self.core_top
            return
        if cid.endswith("/load") and c.device_id and "proc/stat" in c.path:
            c.value = self._cpu_load()
            return
        if cid == "cpu@proc/freq":
            c.value = self._cpu_freq()
            return
        if cid == "memory@proc/used":
            c.value = self._mem_used()
            return
        if dev.cls == "battery":
            self._read_battery(c)
            return
        raw = self.fds.read_int(c.path) if c.path else None
        if raw is None:
            c.value = None
            return
        v = raw * c.scale
        if c.kind == TEMP and (v < -40 or v > 200):
            v = None  # lectura imposible
        c.value = round(v, 1) if v is not None else None
        if c.kind == FAN and c.value is not None:
            peak = max(self._fan_peak.get(cid, 0), c.value)
            self._fan_peak[cid] = peak
            c.vmax = max(2000, round(peak * 1.2, -2))

    def _read_nvidia(self, dev):
        nv = self.nvml
        if nv is None or not nv.init():
            for c in dev.channels:
                c.value = None
            return
        bdf = dev.id.split("@", 1)[1]
        h = nv.handles.get(bdf) or (list(nv.handles.values())[0] if len(nv.handles) == 1 else None)
        if h is None:
            # NVML no da esta tarjeta (p. ej. tras reiniciar el controlador): sin dato, no el
            # último valor como si fuera actual
            for c in dev.channels:
                c.value = None
            return
        if not dev.model:
            dev.model = nv.name(h)
            slow, shut = nv.thresholds(h)
            t = dev.channels[0]
            if slow:
                t.crit = float(slow)
                t.warn = float(slow - 8)
                t.vmax = max(100.0, float(shut or slow + 10))
        r = nv.read(h)
        if r.get("fan_rpm") is not None and not any(c.id.endswith("/rpm") for c in dev.channels):
            ch = Channel(id=dev.id + "/rpm", device_id=dev.id, kind=FAN, label=_("GPU fan"),
                         vmin=0, vmax=3000, auto_thresholds=False)
            dev.channels.insert(1, ch)
            self.channels[ch.id] = ch
            for c in dev.channels:
                if c.id.endswith("/fan"):
                    c.advanced = True   # con RPM, el porcentaje pasa a avanzado
        vals = {"/temp": r.get("temp"), "/load": r.get("load"), "/rpm": r.get("fan_rpm"),
                "/fan": r.get("fan_pct"), "/power": r.get("power_w")}
        for c in dev.channels:
            for suf, v in vals.items():
                if c.id.endswith(suf):
                    c.value = round(float(v), 1) if v is not None else None
            if c.kind == TEMP:
                c.throttling = bool(r.get("thermal_slowdown"))
            if c.kind == FAN and c.value is not None:
                peak = max(self._fan_peak.get(c.id, 0), c.value)
                self._fan_peak[c.id] = peak
                c.vmax = max(2000, round(peak * 1.2, -2))

    def read_once(self):
        """Valor actual de todos los canales, también los ocultos (gestor de sensores).
        No toca discos SATA, controladores lentos ni dispositivos dormidos."""
        out = {}
        for dev in self.devices:
            if dev.asleep or dev.driver in ("drivetemp", "nvidia") or self._asleep(dev):
                continue
            for c in dev.channels:
                if c.value is not None:
                    out[c.id] = c.value
                    continue
                if c.slow or not c.path or dev.cls == "battery":
                    continue
                raw = self.fds.read_int(c.path)
                if raw is not None:
                    out[c.id] = round(raw * c.scale, 1)
        return out

    def next_due(self):
        """Próximo vencimiento (en la línea de tiempo de poll) o None."""
        return min(self._next.values()) if self._next else None

    def fan_peak(self, cid):
        return self._fan_peak.get(cid, 0)

    # ------------------------------------------------------------ CPU / RAM / batería
    def _cpu_load(self):
        txt = sysfs.read_text(sysfs.p("proc/stat"), "") or ""
        lines = txt.splitlines()
        self._core_loads(lines)
        line = lines[0] if lines else ""
        parts = line.split()
        if not parts or parts[0] != "cpu":
            return None
        try:
            vals = [int(x) for x in parts[1:]]
        except ValueError:
            return None
        if len(vals) < 4:
            return None
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals[:8])
        prev = self._cpu_prev
        self._cpu_prev = (idle, total)
        if prev is None or total <= prev[1]:
            return 0.0    # primera lectura, o contadores que bajan (CPU desconectada)
        busy = 1 - (idle - prev[0]) / float(total - prev[1])
        return round(100.0 * max(0.0, min(1.0, busy)), 1)

    def _core_loads(self, lines):
        """Carga de cada núcleo: media de los 2 más ocupados (un juego o una compilación de un
        solo hilo dan poca carga total pero calientan su núcleo)."""
        cur = {}
        for ln in lines[1:]:
            if not ln.startswith("cpu"):
                break
            p = ln.split()
            try:
                vals = [int(x) for x in p[1:9]]
            except ValueError:
                continue
            if len(vals) < 5:
                continue
            cur[p[0]] = (vals[3] + vals[4], sum(vals))
        prev = self._cpu_prev_cores
        self._cpu_prev_cores = cur
        if not prev or not cur:
            return
        loads = []
        for k, (idle, total) in cur.items():
            if k in prev and total > prev[k][1]:
                busy = 1 - (idle - prev[k][0]) / float(total - prev[k][1])
                loads.append(100.0 * max(0.0, min(1.0, busy)))
        if loads:
            loads.sort(reverse=True)
            top = loads[:2]
            self.core_top = round(sum(top) / len(top), 1)

    def _cpu_freq(self):
        vals = []
        for f in glob.glob(sysfs.p("sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq")):
            v = self.fds.read_int(f)
            if v:
                vals.append(v)
        return round(max(vals) / 1000.0) if vals else None

    def _mem_used(self):
        info = {}
        for line in (sysfs.read_text(sysfs.p("proc/meminfo"), "") or "").splitlines():
            k, _, rest = line.partition(":")
            try:
                info[k] = int(rest.split()[0])
            except (ValueError, IndexError):
                pass
        total = info.get("MemTotal")
        avail = info.get("MemAvailable")
        if not total or avail is None:
            return None
        return round(100.0 * (total - avail) / total, 1)

    def _read_battery(self, c):
        ps = c.path if os.path.isdir(c.path) else os.path.dirname(c.path)
        if c.id.endswith("/capacity"):
            c.value = sysfs.read_int(os.path.join(ps, "capacity"))
        elif c.id.endswith("/power"):
            pn = sysfs.read_int(os.path.join(ps, "power_now"))
            if pn is None:
                cur = sysfs.read_int(os.path.join(ps, "current_now"))
                vol = sysfs.read_int(os.path.join(ps, "voltage_now"))
                pn = cur * vol / 1e6 if cur is not None and vol is not None else None
            c.value = round(abs(pn) * 1e-6, 1) if pn is not None else None
        elif c.id.endswith("/health"):
            # la pareja completa energy_* (µWh) o charge_* (µAh), nunca una de cada
            full = design = None
            for pre in ("energy", "charge"):
                f = sysfs.read_int(os.path.join(ps, pre + "_full"))
                d = sysfs.read_int(os.path.join(ps, pre + "_full_design"))
                if f and d:
                    full, design = f, d
                    break
            c.value = round(100.0 * full / design, 1) if full and design else None
        elif c.id.endswith("/status"):
            c.text = sysfs.read_text(os.path.join(ps, "status"), "") or ""
            c.value = 0.0
        elif c.id.endswith("/cycles"):
            v = sysfs.read_int(os.path.join(ps, "cycle_count"))
            c.value = float(v) if v is not None else None
            c.text = str(v) if v is not None else ""

    # ------------------------------------------------------------ salida
    def snapshot(self):
        worst = ST_NODATA
        for d in self.devices:
            if STATE_RANK.get(d.state, 0) > STATE_RANK.get(worst, 0):
                worst = d.state
        return {"time": time.time(), "state": worst, "api": 2,
                "devices": [d.to_dict() for d in self.devices]}
