"""NVIDIA vía NVML (libnvidia-ml.so.1) con ctypes, de forma perezosa.

Reglas para no gastar batería:
- Antes de inicializar NVML se comprueba power/runtime_status de cada GPU NVIDIA:
  leer ese fichero NO despierta la tarjeta.
- Si todas duermen, NVML no se inicializa; si estaba inicializado, se cierra
  (mantener la librería abierta impediría que la GPU entre en reposo).
- Nunca se ejecuta nvidia-smi.
"""

import ctypes
import glob
import os

from . import sysfs
from .model import Channel, Device, GPU, TEMP, POWER, PERCENT
from ..i18n import _

NVML_SUCCESS = 0
NVML_TEMPERATURE_GPU = 0
NVML_TEMPERATURE_THRESHOLD_SHUTDOWN = 0
NVML_TEMPERATURE_THRESHOLD_SLOWDOWN = 1


# Causas de bajada de reloj por temperatura (nvml.h / nvidia-ml-py)
CLOCKS_EVENT_SW_THERMAL = 0x20
CLOCKS_EVENT_HW_THERMAL = 0x40
FAN_SPEED_INFO_V1 = 0x100000C   # NVML_STRUCT_VERSION(FanSpeedInfo, 1)


class _FanSpeedInfo(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint), ("fan", ctypes.c_uint), ("speed", ctypes.c_uint)]


class _Util(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _PciInfo(ctypes.Structure):
    _fields_ = [("busIdLegacy", ctypes.c_char * 16), ("domain", ctypes.c_uint),
                ("bus", ctypes.c_uint), ("device", ctypes.c_uint),
                ("pciDeviceId", ctypes.c_uint), ("pciSubSystemId", ctypes.c_uint),
                ("busId", ctypes.c_char * 32)]


def nvidia_pci_devices():
    """GPUs NVIDIA vistas en PCI (vendor 0x10de, clase 0x03xxxx)."""
    out = []
    for d in glob.glob(sysfs.p("sys/bus/pci/devices/*")):
        if (sysfs.read_text(os.path.join(d, "vendor"), "") or "").lower() != "0x10de":
            continue
        if not (sysfs.read_text(os.path.join(d, "class"), "") or "").startswith("0x03"):
            continue
        out.append(d)
    return sorted(out)


def bound_driver(pcidir):
    """Nombre del driver de kernel que controla este dispositivo PCI («nvidia», «nouveau»...)."""
    try:
        return os.path.basename(os.readlink(os.path.join(pcidir, "driver")))
    except OSError:
        return ""


def _pm_state(pcidir):
    return (sysfs.read_text(os.path.join(pcidir, "power", "runtime_status"), "active") or "active")


class Nvml:
    def __init__(self):
        self.lib = None
        self.ready = False
        self.handles = {}  # bdf -> handle
        self._tried = False

    def _load(self):
        if self.lib is not None or self._tried:
            return self.lib is not None
        self._tried = True
        try:
            self.lib = ctypes.CDLL("libnvidia-ml.so.1")
        except OSError:
            self.lib = None
        return self.lib is not None

    def available(self):
        return bool(nvidia_pci_devices()) and self._load()

    def init(self):
        if self.ready:
            return True
        if not self._load():
            return False
        fn = getattr(self.lib, "nvmlInit_v2", None) or getattr(self.lib, "nvmlInit", None)
        if fn is None or fn() != NVML_SUCCESS:
            return False
        self.ready = True
        self._map_handles()
        return True

    def shutdown(self):
        if self.ready and self.lib is not None:
            try:
                self.lib.nvmlShutdown()
            except Exception:
                pass
        self.ready = False
        self.handles = {}

    def _map_handles(self):
        count = ctypes.c_uint(0)
        fn = getattr(self.lib, "nvmlDeviceGetCount_v2", None) or self.lib.nvmlDeviceGetCount
        if fn(ctypes.byref(count)) != NVML_SUCCESS:
            return
        get = getattr(self.lib, "nvmlDeviceGetHandleByIndex_v2", None) or \
            self.lib.nvmlDeviceGetHandleByIndex
        for i in range(count.value):
            h = ctypes.c_void_p()
            if get(i, ctypes.byref(h)) != NVML_SUCCESS:
                continue
            pci = _PciInfo()
            fnp = getattr(self.lib, "nvmlDeviceGetPciInfo_v3", None) or \
                getattr(self.lib, "nvmlDeviceGetPciInfo_v2", None)
            bdf = str(i)
            if fnp is not None and fnp(h, ctypes.byref(pci)) == NVML_SUCCESS:
                bdf = pci.busId.decode(errors="ignore").lower()[-12:]  # 0000:01:00.0
            self.handles[bdf] = h

    def name(self, h):
        buf = ctypes.create_string_buffer(96)
        if self.lib.nvmlDeviceGetName(h, buf, 96) == NVML_SUCCESS:
            return buf.value.decode(errors="ignore")
        return ""

    def _uint(self, fname, h, *args):
        fn = getattr(self.lib, fname, None)
        if fn is None:
            return None
        v = ctypes.c_uint(0)
        if fn(h, *args, ctypes.byref(v)) == NVML_SUCCESS:
            return v.value
        return None

    def read(self, h):
        """Devuelve dict con temp, fan_pct, fan_rpm, power_w, load y thermal_slowdown."""
        out = {}
        out["temp"] = self._uint("nvmlDeviceGetTemperature", h, NVML_TEMPERATURE_GPU)
        out["fan_pct"] = self._uint("nvmlDeviceGetFanSpeed", h)
        p = self._uint("nvmlDeviceGetPowerUsage", h)
        out["power_w"] = p / 1000.0 if p is not None else None
        out["fan_rpm"] = self._fan_rpm(h)
        reasons = self._reasons(h)
        out["thermal_slowdown"] = bool(reasons & (CLOCKS_EVENT_SW_THERMAL | CLOCKS_EVENT_HW_THERMAL)) \
            if reasons is not None else False
        u = _Util()
        fn = getattr(self.lib, "nvmlDeviceGetUtilizationRates", None)
        if fn is not None and fn(h, ctypes.byref(u)) == NVML_SUCCESS:
            out["load"] = u.gpu
        return out

    def _fan_rpm(self, h):
        """RPM pretendidas del ventilador 0 (controladores 565 o posteriores)."""
        fn = getattr(self.lib, "nvmlDeviceGetFanSpeedRPM", None)
        if fn is None:
            return None
        info = _FanSpeedInfo(FAN_SPEED_INFO_V1, 0, 0)
        try:
            if fn(h, ctypes.byref(info)) == NVML_SUCCESS:
                return info.speed
        except Exception:
            pass
        return None

    def _reasons(self, h):
        for name in ("nvmlDeviceGetCurrentClocksEventReasons",
                     "nvmlDeviceGetCurrentClocksThrottleReasons"):
            fn = getattr(self.lib, name, None)
            if fn is None:
                continue
            v = ctypes.c_ulonglong(0)
            if fn(h, ctypes.byref(v)) == NVML_SUCCESS:
                return v.value
        return None

    def thresholds(self, h):
        slow = self._uint("nvmlDeviceGetTemperatureThreshold", h, NVML_TEMPERATURE_THRESHOLD_SLOWDOWN)
        shut = self._uint("nvmlDeviceGetTemperatureThreshold", h, NVML_TEMPERATURE_THRESHOLD_SHUTDOWN)
        return slow, shut


def discover_nvidia():
    """Crea dispositivos para GPUs NVIDIA con driver propietario.

    Si la GPU está dormida en el arranque, se crea igualmente (estado «en reposo»)
    sin despertarla; el nombre comercial se obtendrá cuando despierte.
    """
    devs = []
    pcis = nvidia_pci_devices()
    if not pcis:
        return devs, None
    nv = Nvml()
    if not nv._load():
        return devs, None  # nouveau o sin driver: lo cubre hwmon
    for pci in pcis:
        # solo si la GPU la lleva el driver propietario: con nouveau (aunque la librería NVML
        # siga instalada) la cubre hwmon y saldría duplicada, una vez «sin datos»
        if bound_driver(pci) != "nvidia":
            continue
        bdf = os.path.basename(pci).lower()
        dev_id = "nvidia@%s" % bdf
        dev = Device(id=dev_id, cls=GPU, name=_("GPU NVIDIA"), driver="nvidia",
                     pm_path=os.path.join(pci, "power", "runtime_status"))
        dev.channels = [
            Channel(id=dev_id + "/temp", device_id=dev_id, kind=TEMP, label=_("GPU"),
                    primary=True, warn=83.0, crit=93.0, vmin=20, vmax=105),
            Channel(id=dev_id + "/load", device_id=dev_id, kind=PERCENT, label=_("GPU load"),
                    vmin=0, vmax=100, auto_thresholds=False),
            Channel(id=dev_id + "/fan", device_id=dev_id, kind=PERCENT, label=_("GPU fan"),
                    vmin=0, vmax=100, auto_thresholds=False),
            Channel(id=dev_id + "/power", device_id=dev_id, kind=POWER, label=_("GPU power"),
                    vmin=0, vmax=450, auto_thresholds=False),
        ]
        devs.append(dev)
    return devs, nv
