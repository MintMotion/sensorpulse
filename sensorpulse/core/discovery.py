"""Descubrimiento de sensores: hwmon, thermal_zone, power_supply, CPU, RAM.

No necesita root. No lee valores de dispositivos dormidos (solo su estado PM).
"""

import glob
import hashlib
import os
import re

from . import sysfs
from .model import (
    Channel, Device, CPU, GPU, DISK, BOARD, FANS, MEMORY, BATTERY, OTHER,
    TEMP, FAN, VOLT, POWER, CURRENT, PERCENT, FREQ, STATUS,
)
from ..i18n import _


def _natural(name):
    """Orden natural: temp2 antes que temp10 (el orden alfabético los invierte)."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


# ---------------------------------------------------------------- clasificación

CPU_DRIVERS = {"coretemp", "k10temp", "k8temp", "zenpower", "cpu_thermal",
               "cpu-thermal", "soc_thermal", "fam15h_power", "via_cputemp"}
GPU_DRIVERS = {"amdgpu", "radeon", "nouveau", "i915", "xe"}
DISK_DRIVERS = {"nvme", "drivetemp"}
MEM_DRIVERS = {"spd5118", "jc42", "ee1004"}
# Controladores que hablan con el firmware (SMM/EC/WMI): cada lectura puede tardar
# y, en algunos Dell, provocar tirones de audio. Se leen con calma.
SLOW_DRIVERS = {"thinkpad", "dell_smm", "applesmc", "asus", "asus_ec_sensors",
                "asus_wmi_sensors", "hp"}
SLOW_INTERVAL = 10.0


def is_slow_driver(name):
    n = (name or "").lower()
    return n in SLOW_DRIVERS or n.startswith("dell") or n.endswith(("_wmi", "-wmi"))
BOARD_PREFIXES = ("nct", "it87", "it8", "w83", "f71", "asus", "acpitz", "pch_",
                  "dell", "thinkpad", "applesmc", "hp", "gigabyte", "sch5")

VENDORS = {"0x1002": "AMD", "0x10de": "NVIDIA", "0x8086": "Intel"}

# Valores por defecto prudentes (aviso, crítico) en °C cuando el hardware no los da.
DEFAULT_LIMITS = {
    CPU: (85.0, 100.0),   # CPU desconocida: crítico alto; la alarma real va por freno sostenido
    GPU: (85.0, 95.0),
    DISK: (60.0, 70.0),
    "hdd": (50.0, 60.0),
    BOARD: (70.0, 85.0),
    MEMORY: (75.0, 85.0),
    BATTERY: (45.0, 55.0),
    OTHER: (80.0, 95.0),
    "gpu_hotspot": (100.0, 110.0),
    "gpu_mem": (95.0, 105.0),
}

_TYPE_RE = re.compile(r"^(temp|fan|in|power|curr|energy|freq)(\d+)_(input|average)$")


def classify_driver(name):
    n = (name or "").lower()
    if n in CPU_DRIVERS:
        return CPU
    if n in GPU_DRIVERS:
        return GPU
    if n in DISK_DRIVERS:
        return DISK
    if n in MEM_DRIVERS:
        return MEMORY
    if n.startswith(BOARD_PREFIXES):
        return BOARD
    return OTHER


def nice_temp_label(driver, raw, n):
    """Traduce etiquetas técnicas a nombres comprensibles."""
    r = (raw or "").strip()
    rl = r.lower()
    d = driver.lower()
    if d == "coretemp":
        if rl.startswith("package id"):
            return _("CPU package"), True
        if rl.startswith("core"):
            return _("Core %s") % r.split()[-1], False
    if d in ("k10temp", "zenpower"):
        if rl == "tdie":
            return _("CPU die"), True
        if rl == "tctl":
            return _("CPU (control)"), True
        if rl.startswith("tccd"):
            return _("CCD %s") % rl[4:], False
    if d == "k8temp":
        return _("Core sensor %s") % n, n == "1"
    if d in ("cpu_thermal", "cpu-thermal", "soc_thermal", "via_cputemp"):
        return _("CPU"), True
    if d == "amdgpu":
        if rl == "edge":
            return _("GPU"), True
        if rl == "junction":
            return _("GPU hotspot"), False
        if rl == "mem":
            return _("GPU memory"), False
    if d in ("nouveau", "radeon"):
        return _("GPU"), n == "1"
    if d in ("i915", "xe"):
        if rl in ("pkg", "package", "") and n == "1":
            return _("GPU"), True
        if "vram" in rl or "mem" in rl:
            return _("GPU memory"), False
        return r or _("GPU sensor %s") % n, n == "1"
    if d == "nvme":
        if rl == "composite":
            return _("SSD"), True
        return _("SSD sensor %s") % rl.replace("sensor", "").strip() if rl.startswith("sensor") else (r or _("SSD")), False
    if d == "drivetemp":
        return _("Disk"), True
    if d in MEM_DRIVERS:
        return _("RAM"), True
    if d == "acpitz":
        return _("ACPI zone %s") % n, False
    return (r if r else _("Temperature %s") % n), False


def _model_for(devpath, driver):
    """Nombre comercial cuando se puede saber sin root."""
    for f in ("model", "../model", "device/model", "name_product"):
        t = sysfs.read_text(os.path.join(devpath, f))
        if t and len(t) < 80 and not t.startswith("0x"):
            return t.strip()
    return ""


def disk_media(driver, devpath):
    """Tipo de disco, sin despertarlo (solo sysfs):
    nvme     NVMe: la temperatura la da el controlador; leerla no afecta al reposo ni a la
             vida útil, así que se lee al ritmo normal.
    ssd      SATA sin partes móviles (queue/rotational = 0): igual, lecturas frecuentes.
    hdd      disco duro (rotational = 1): leer la temperatura reinicia el temporizador de
             reposo en muchos modelos; el intervalo lo decide el usuario.
    unknown  no se sabe: se trata como un disco duro (lo prudente)."""
    if driver == "nvme":
        return "nvme"
    if driver != "drivetemp":
        return ""
    for q in sorted(glob.glob(os.path.join(devpath, "block", "*", "queue", "rotational"))):
        v = (sysfs.read_text(q, "") or "").strip()
        if v == "0":
            return "ssd"
        if v == "1":
            return "hdd"
    return "unknown"


def _pci_vendor(devpath):
    v = sysfs.read_text(os.path.join(devpath, "vendor"))
    return VENDORS.get((v or "").lower(), "")


def _cpu_model():
    txt = sysfs.read_text(sysfs.p("proc/cpuinfo"), "") or ""
    for line in txt.splitlines():
        if line.lower().startswith(("model name", "hardware", "cpu model")):
            return line.split(":", 1)[1].strip()
    return ""


def _device_tree_model():
    return (sysfs.read_text(sysfs.p("proc/device-tree/model"), "") or "").strip("\x00 \n")


def cpu_tjmax(driver, model, crit_v):
    """Temperatura máxima de diseño (TjMax) de la CPU, solo con datos verificados:
    - Intel (coretemp): el propio driver la publica como tempN_crit.
    - AMD Ryzen (k10temp/zenpower): 95 °C; 89 °C en los X3D de la serie 7000.
    - Raspberry Pi: 85 °C (límite del firmware).
    Si no se conoce, devuelve None y se usan los valores prudentes por defecto."""
    d = (driver or "").lower()
    m = (model or "")
    if d == "coretemp":
        return crit_v
    if d in ("k10temp", "zenpower"):
        from .tjmax import amd_tjmax
        return amd_tjmax(m)[0]
    if d in ("cpu_thermal", "cpu-thermal") and "raspberry pi" in _device_tree_model().lower():
        return 85.0
    return None


def apply_tjmax(ch, tjmax):
    """Umbrales de CPU a partir de su TjMax: «alto» a TjMax-10 y «crítico» solo por encima
    del límite de diseño (llegar a TjMax en carga es normal en muchas CPU actuales)."""
    ch.tjmax = float(tjmax)
    ch.warn = round(tjmax - 10, 1)
    ch.crit = round(tjmax + 3, 1)
    ch.vmin = 20.0
    ch.vmax = max(100.0, round(tjmax + 10))


# id antiguo (con el índice nvmeN / la ruta SCSI) -> id estable; lo usa el servicio para migrar
ALIASES = {}


def _stable_id_text(devpath):
    for f in ("wwid", "serial", "vpd_pg80", "device/wwid", "device/serial"):
        try:
            with open(os.path.join(devpath, f), "rb") as fh:
                raw = fh.read().strip()
        except OSError:
            continue
        if raw:
            return raw
    return None


def _stable_disk_id(driver, devpath):
    """Id de disco que no cambia si cambia el orden de los NVMe o de los puertos SATA.
    Se usa un resumen del número de serie/WWID (no el número en claro)."""
    if driver not in ("nvme", "drivetemp"):
        return None
    raw = _stable_id_text(devpath)
    if not raw:
        return None
    return "%s@id-%s" % (driver, hashlib.sha1(raw).hexdigest()[:12])


def _limit_from(path, scale):
    v = sysfs.read_int(path)
    if v is None:
        return None
    val = v * scale
    if val <= 0 or val > 200:  # valores sin sentido (0, 255, 127000...)
        return None
    return val


def _apply_temp_limits(ch, cls, max_v, crit_v, extra=None):
    key = extra or cls
    dwarn, dcrit = DEFAULT_LIMITS.get(key, DEFAULT_LIMITS[OTHER])
    crit = crit_v if crit_v else (max_v + 10 if max_v and max_v < dcrit else dcrit)
    if max_v and max_v < crit:
        warn = max_v
    elif max_v and max_v >= crit:
        warn = crit - 10
    else:
        warn = min(dwarn, crit - 5)
    ch.warn, ch.crit = round(warn, 1), round(crit, 1)
    ch.vmin = 20.0
    ch.vmax = max(100.0, round(crit + 5))


# ---------------------------------------------------------------- hwmon

def _scan_hwmon(skip_drivers=()):
    devices = []
    used = set()
    for hw in sorted(glob.glob(sysfs.p("sys/class/hwmon/hwmon*")),
                     key=lambda x: int(re.sub(r"\D", "", os.path.basename(x)) or 0)):
        driver = sysfs.read_text(os.path.join(hw, "name"), "") or "hwmon"
        if driver in skip_drivers:
            continue
        devlink = os.path.join(hw, "device")
        devpath = os.path.realpath(devlink) if os.path.exists(devlink) else os.path.realpath(hw)
        cls = classify_driver(driver)
        dev_id = "%s@%s" % (driver, sysfs.rel(devpath))
        stable = _stable_disk_id(driver, devpath)
        if stable:
            ALIASES[dev_id] = stable   # para migrar la configuración de la 0.2.0
            dev_id = stable
        if dev_id in used:
            # dos discos con el mismo número de serie (clones, cajas USB que dan uno fijo) o
            # dos hwmon del mismo dispositivo: sin esto el segundo pisaba los canales del primero
            n = 2
            while "%s-%d" % (dev_id, n) in used:
                n += 1
            dev_id = "%s-%d" % (dev_id, n)
        used.add(dev_id)
        dev = Device(id=dev_id, cls=cls, name="", driver=driver)

        vendor = _pci_vendor(devpath) if cls == GPU else ""
        if cls == CPU:
            dev.name = _("CPU")
            dev.model = _cpu_model()
        elif cls == GPU:
            dev.name = (_("GPU %s") % vendor) if vendor else _("GPU")
            # iGPU frente a dGPU: vigilamos el estado PM de cualquier GPU PCI
            pm = os.path.join(devpath, "power", "runtime_status")
            if os.path.exists(pm):
                dev.pm_path = pm
        elif cls == DISK:
            dev.model = _model_for(devpath, driver)
            dev.media = disk_media(driver, devpath)
            dev.name = _("SSD") if dev.media in ("nvme", "ssd") else _("Disk")
            if driver == "drivetemp":
                dev.interval = 300.0  # no reiniciar el temporizador de reposo
            else:
                dev.interval = 5.0
            pm = os.path.join(devpath, "power", "runtime_status")
            if os.path.exists(pm):
                dev.pm_path = pm
        elif cls == MEMORY:
            dev.name = _("Memory")
        elif cls == BOARD:
            dl = driver.lower()
            if dl == "acpitz":
                dev.name = _("ACPI zone")
            elif dl.startswith(("thinkpad", "dell", "applesmc", "hp", "asus")) and not dl.startswith("asus_ec"):
                dev.name = _("System")
            else:
                dev.name = _("Motherboard")
        else:
            dev.name = driver

        # Dispositivo dormido (dGPU, disco NVMe): leer sus atributos (etiquetas, límites, tope de
        # potencia) puede despertarlo en kernels antiguos. Se crean los canales con valores por
        # defecto y se marca «límites pendientes»: se vuelve a descubrir cuando despierte.
        sleeping = bool(dev.pm_path) and driver != "drivetemp" and \
            (sysfs.read_text(dev.pm_path, "") or "").strip() == "suspended"
        dev.limits_pending = sleeping
        files = sorted(os.listdir(hw), key=_natural) if os.path.isdir(hw) else []
        for f in files:
            m = _TYPE_RE.match(f)
            if not m:
                continue
            prefix, n, suffix = m.groups()
            if prefix == "power" and suffix == "input" and "power%s_average" % n in files:
                continue  # preferimos average
            base = os.path.join(hw, "%s%s_" % (prefix, n))
            raw = "" if sleeping else (sysfs.read_text(base + "label", "") or "")
            cid = "%s/%s%s" % (dev_id, prefix, n)
            if prefix == "temp":
                label, primary = nice_temp_label(driver, raw, n)
                ch = Channel(id=cid, device_id=dev_id, kind=TEMP, label=label, raw_label=raw,
                             path=base + "input", scale=0.001, primary=primary)
                extra = None
                if cls == GPU and "hotspot" in label.lower():
                    extra = "gpu_hotspot"
                elif cls == GPU and "memory" in label.lower():
                    extra = "gpu_mem"
                elif driver == "drivetemp":
                    # un SSD SATA aguanta más calor que un disco duro (límites de disco genérico)
                    extra = None if dev.media == "ssd" else "hdd"
                if driver == "drivetemp" or sleeping:
                    # en drivetemp, leer temp*_max/_crit manda una orden SCT al disco igual que
                    # leer la temperatura (y lo despierta); en un dispositivo dormido, igual:
                    # límites por defecto, sin tocarlo
                    _apply_temp_limits(ch, cls, None, None, extra)
                else:
                    _apply_temp_limits(ch, cls, _limit_from(base + "max", 0.001),
                                       _limit_from(base + "crit", 0.001), extra)
                if driver == "acpitz":
                    ch.advanced = True
                    ch.note = "acpi"
            elif prefix == "fan":
                label = raw or (_("GPU fan") if cls == GPU else _("Fan %s") % n)
                ch = Channel(id=cid, device_id=dev_id, kind=FAN, label=label, raw_label=raw,
                             path=base + "input", scale=1.0, vmin=0, vmax=3000,
                             auto_thresholds=False)
            elif prefix == "in":
                ch = Channel(id=cid, device_id=dev_id, kind=VOLT, label=raw or "in%s" % n,
                             raw_label=raw, path=base + "input", scale=0.001,
                             vmin=0, vmax=15, advanced=True, auto_thresholds=False, note="advanced")
            elif prefix == "power":
                ch = Channel(id=cid, device_id=dev_id, kind=POWER,
                             label=(_("GPU power") if cls == GPU else (raw or _("Power"))),
                             raw_label=raw, path=base + suffix, scale=1e-6, vmin=0,
                             vmax=(0 if sleeping else (sysfs.read_int(base + "cap", 0) or 0)) * 1e-6 or 300,
                             advanced=(cls != GPU), auto_thresholds=False,
                             note="advanced" if cls != GPU else "")
            elif prefix == "curr":
                ch = Channel(id=cid, device_id=dev_id, kind=CURRENT, label=raw or "curr%s" % n,
                             raw_label=raw, path=base + "input", scale=0.001, vmin=0, vmax=50,
                             advanced=True, auto_thresholds=False, note="advanced")
            elif prefix == "freq":
                ch = Channel(id=cid, device_id=dev_id, kind=FREQ,
                             label=raw or _("Clock %s") % n, raw_label=raw,
                             path=base + "input", scale=1e-6, vmin=0, vmax=3000,
                             advanced=True, auto_thresholds=False, note="advanced")
            else:  # energy: contador, no útil como valor directo
                continue
            dev.channels.append(ch)

        # Extras de GPU AMD (uso), leídos con la misma guardia PM
        if driver == "amdgpu":
            busy = os.path.join(devpath, "gpu_busy_percent")
            if os.path.exists(busy):
                dev.channels.append(Channel(
                    id="%s/busy" % dev_id, device_id=dev_id, kind=PERCENT, label=_("GPU load"),
                    path=busy, scale=1.0, vmin=0, vmax=100, auto_thresholds=False))

        # AMD Zen/Zen+ con desfase (1600X-1800X +20, 2700X +10, Threadripper 1000/2000 +27):
        # k10temp publica Tctl (con desfase) y Tdie (real; solo existe cuando hay desfase).
        # La referencia es Tdie; Tctl queda como lectura avanzada, sin límites ni alarmas.
        offset_tctl = None
        if driver in ("k10temp", "zenpower"):
            tdie = next((c for c in dev.channels if c.kind == TEMP and c.raw_label.lower() == "tdie"), None)
            if tdie is not None:
                for c in dev.channels:
                    if c.kind != TEMP:
                        continue
                    c.primary = c is tdie
                    if c.raw_label.lower() == "tctl":
                        offset_tctl = c
                        c.advanced, c.note = True, "offset"
                        c.warn = c.crit = c.tjmax = None
                        c.auto_thresholds = False
        if cls == CPU:
            for c in dev.channels:
                if c.kind == TEMP and c is not offset_tctl:
                    crit_v = _limit_from(c.path.replace("_input", "_crit"), 0.001)
                    tj = cpu_tjmax(driver, dev.model, crit_v)
                    if tj:
                        apply_tjmax(c, tj)
        if is_slow_driver(driver):
            for c in dev.channels:
                c.slow = True
                c.interval = SLOW_INTERVAL
        if driver in ("k10temp", "zenpower"):
            # sin Tdie, Tctl es la temperatura de referencia de la CPU: nombre simple
            if not any(c.raw_label.lower() == "tdie" for c in dev.channels):
                for c in dev.channels:
                    if c.raw_label.lower() == "tctl":
                        c.label = _("CPU")
        if dev.channels:
            _ensure_primary(dev)
            if driver == "nvme":
                _inherit_limits(dev)
            devices.append(dev)
    return devices


def _inherit_limits(dev):
    """Los sensores secundarios de un NVMe usan los límites del principal."""
    prim = next((c for c in dev.channels if c.primary and c.kind == TEMP), None)
    if prim is None:
        return
    for c in dev.channels:
        if c is not prim and c.kind == TEMP:
            c.warn, c.crit, c.vmax = prim.warn, prim.crit, prim.vmax
            c.advanced = True
            c.note = "duplicate"


_KIND_ORDER = {TEMP: 0, PERCENT: 1, FAN: 2, FREQ: 3, POWER: 4, STATUS: 5, VOLT: 6, CURRENT: 7}


def _ensure_primary(dev):
    temps = [c for c in dev.channels if c.kind == TEMP]
    if any(c.primary for c in temps):
        # solo un principal: el primero marcado
        seen = False
        for c in temps:
            if c.primary and not seen:
                seen = True
            elif c.primary:
                c.primary = False
        return
    if temps:
        temps[0].primary = True
    elif dev.channels:
        dev.channels[0].primary = True


def sort_channels(dev):
    dev.channels.sort(key=lambda c: (not c.primary, _KIND_ORDER.get(c.kind, 9)))


# ---------------------------------------------------------------- thermal zones

def _scan_thermal(have_cpu):
    devices = []
    if have_cpu:
        return devices
    # orden numérico (thermal_zone2 antes que thermal_zone10, como los hwmon)
    for tz in sorted(glob.glob(sysfs.p("sys/class/thermal/thermal_zone*")),
                     key=lambda x: int(re.sub(r"\D", "", os.path.basename(x)) or 0)):
        typ = (sysfs.read_text(os.path.join(tz, "type"), "") or "").lower()
        if not os.path.exists(os.path.join(tz, "temp")):
            continue
        cls = CPU if any(k in typ for k in ("cpu", "soc", "x86_pkg", "bcm2835", "pkg")) else BOARD
        dev_id = "thermal@%s" % sysfs.rel(tz)
        dev = Device(id=dev_id, cls=cls, name=_("CPU") if cls == CPU else _("Thermal zone"),
                     driver="thermal:" + typ)
        if cls == CPU:
            dev.model = _cpu_model()
        ch = Channel(id=dev_id + "/temp", device_id=dev_id, kind=TEMP,
                     label=_("CPU") if cls == CPU else typ, raw_label=typ,
                     path=os.path.join(tz, "temp"), scale=0.001, primary=True)
        crit = None
        for tp in glob.glob(os.path.join(tz, "trip_point_*_type")):
            if sysfs.read_text(tp) == "critical":
                crit = _limit_from(tp.replace("_type", "_temp"), 0.001)
        _apply_temp_limits(ch, cls, None, crit)
        if cls == CPU and "raspberry pi" in _device_tree_model().lower():
            apply_tjmax(ch, 85.0)
        dev.channels.append(ch)
        devices.append(dev)
        if cls == CPU:
            have_cpu = True
    return devices


# ---------------------------------------------------------------- batería

def _scan_batteries():
    devices = []
    for ps in sorted(glob.glob(sysfs.p("sys/class/power_supply/*"))):
        if (sysfs.read_text(os.path.join(ps, "type"), "") or "").lower() != "battery":
            continue
        if (sysfs.read_text(os.path.join(ps, "scope"), "") or "").lower() == "device":
            continue  # ratones, teclados...
        name = os.path.basename(ps)
        dev_id = "battery@%s" % name
        dev = Device(id=dev_id, cls=BATTERY, name=_("Battery"), driver="power_supply",
                     interval=5.0)
        mf = sysfs.read_text(os.path.join(ps, "manufacturer"), "") or ""
        mo = sysfs.read_text(os.path.join(ps, "model_name"), "") or ""
        dev.model = (mf + " " + mo).strip()
        dev.channels.append(Channel(id=dev_id + "/capacity", device_id=dev_id, kind=PERCENT,
                                    label=_("Charge"), path=os.path.join(ps, "capacity"),
                                    primary=True, vmin=0, vmax=100, auto_thresholds=False))
        dev.channels.append(Channel(id=dev_id + "/power", device_id=dev_id, kind=POWER,
                                    label=_("Power draw"), path=ps, scale=1e-6, vmin=0, vmax=60,
                                    auto_thresholds=False))
        dev.channels.append(Channel(id=dev_id + "/health", device_id=dev_id, kind=PERCENT,
                                    label=_("Health"), path=ps, vmin=0, vmax=100,
                                    auto_thresholds=False))
        dev.channels.append(Channel(id=dev_id + "/status", device_id=dev_id, kind=STATUS,
                                    label=_("Status"), path=os.path.join(ps, "status"),
                                    auto_thresholds=False))
        if os.path.exists(os.path.join(ps, "cycle_count")):
            dev.channels.append(Channel(id=dev_id + "/cycles", device_id=dev_id, kind=STATUS,
                                        label=_("Charge cycles"),
                                        path=os.path.join(ps, "cycle_count"),
                                        advanced=True, auto_thresholds=False))
        devices.append(dev)
    return devices


# ---------------------------------------------------------------- CPU y RAM

def _add_cpu_load(devices):
    cpu = next((d for d in devices if d.cls == CPU), None)
    if cpu is None:
        cpu = Device(id="cpu@proc", cls=CPU, name=_("CPU"), driver="proc", model=_cpu_model())
        devices.insert(0, cpu)
    cpu.channels.append(Channel(id="cpu@proc/load", device_id=cpu.id, kind=PERCENT,
                                label=_("CPU load"), path=sysfs.p("proc/stat"),
                                vmin=0, vmax=100, auto_thresholds=False))
    cpu.channels.append(Channel(id="cpu@proc/coremax", device_id=cpu.id, kind=PERCENT,
                                label=_("Busiest cores"), path=sysfs.p("proc/stat"),
                                vmin=0, vmax=100, auto_thresholds=False, advanced=True,
                                note="advanced"))
    freqs = glob.glob(sysfs.p("sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"))
    if freqs:
        mx = sysfs.read_int(os.path.join(os.path.dirname(freqs[0]), "cpuinfo_max_freq"), 0) or 0
        cpu.channels.append(Channel(id="cpu@proc/freq", device_id=cpu.id, kind=FREQ,
                                    label=_("CPU clock"), path="", scale=0.001, vmin=0,
                                    vmax=(mx / 1000.0) or 5000, auto_thresholds=False))
    if not any(c.primary for c in cpu.channels):
        cpu.channels[0].primary = True


def _add_memory(devices):
    mem = next((d for d in devices if d.cls == MEMORY), None)
    if mem is None:
        mem = Device(id="memory@proc", cls=MEMORY, name=_("Memory"), driver="proc")
        devices.append(mem)
    has_temp = any(c.primary for c in mem.channels)
    mem.channels.append(Channel(id="memory@proc/used", device_id=mem.id, kind=PERCENT,
                                label=_("RAM in use"), path=sysfs.p("proc/meminfo"),
                                vmin=0, vmax=100, auto_thresholds=False,
                                primary=not has_temp))


def _split_fans(devices):
    """Los ventiladores de placa/portátil van a una tarjeta propia «Ventiladores»."""
    fans = Device(id="fans@all", cls=FANS, name=_("Fans"), driver="hwmon")
    for d in devices:
        if d.cls in (BOARD, OTHER, CPU):
            keep = []
            for c in d.channels:
                if c.kind == FAN:
                    fans.channels.append(c)
                else:
                    keep.append(c)
            d.channels = keep
    devices[:] = [d for d in devices if d.channels]
    if fans.channels:
        fans.channels[0].primary = True
        devices.append(fans)


def discover(skip_drivers=()):
    """Devuelve la lista de dispositivos encontrados, en orden de presentación.
    skip_drivers: controladores que no se tocan en absoluto (p. ej. drivetemp en la
    lectura directa de «sensorpulse status», para no despertar los discos)."""
    ALIASES.clear()
    devices = _scan_hwmon(skip_drivers)
    have_cpu = any(d.cls == CPU for d in devices)
    devices += _scan_thermal(have_cpu)
    devices += _scan_batteries()
    _add_cpu_load(devices)
    _add_memory(devices)
    _split_fans(devices)
    _number_duplicates(devices)
    for d in devices:
        sort_channels(d)
    from .model import DEVICE_ORDER
    devices.sort(key=lambda d: DEVICE_ORDER.index(d.cls) if d.cls in DEVICE_ORDER else 99)
    return devices


def _number_duplicates(devices):
    """«SSD», «SSD» → «SSD 1», «SSD 2» (o el modelo si se conoce)."""
    by_name = {}
    for d in devices:
        by_name.setdefault(d.name, []).append(d)
    for name, group in by_name.items():
        if len(group) > 1:
            for i, d in enumerate(group, 1):
                d.name = "%s %d" % (name, i)
