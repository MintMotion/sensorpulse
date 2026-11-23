"""Modelo de datos: dispositivos y canales."""

from dataclasses import dataclass, field
from typing import Optional

# Clases de dispositivo (orden en el que se muestran)
CPU = "cpu"
GPU = "gpu"
DISK = "disk"
BOARD = "board"
FANS = "fans"
MEMORY = "memory"
BATTERY = "battery"
OTHER = "other"

DEVICE_ORDER = [CPU, GPU, DISK, FANS, BATTERY, MEMORY, BOARD, OTHER]

# Tipos de canal
TEMP = "temp"
FAN = "fan"
VOLT = "volt"
POWER = "power"
CURRENT = "current"
ENERGY = "energy"
PERCENT = "percent"
FREQ = "freq"
STATUS = "status"

UNITS = {
    TEMP: "°C",
    FAN: "RPM",
    VOLT: "V",
    POWER: "W",
    CURRENT: "A",
    ENERGY: "J",
    PERCENT: "%",
    FREQ: "MHz",
    STATUS: "",
}

# Estados
ST_SLEEP = "sleep"
ST_NORMAL = "normal"
ST_HOT = "hot"
ST_CRIT = "critical"
ST_NODATA = "nodata"

def temp_state(value, warn, crit, throttling=False):
    """Estado de una temperatura (crítico / alto / normal), en UN solo sitio: lo usan el motor
    (órdenes de terminal, historial) y las vistas (panel, flotante, extensión, applet).
    None si no es una temperatura con umbrales o no hay dato."""
    if value is None or warn is None:
        return None
    if crit is not None and value >= crit:
        return ST_CRIT
    if throttling or value >= warn:
        return ST_HOT
    return ST_NORMAL


STATE_RANK = {ST_NODATA: 0, ST_SLEEP: 0, ST_NORMAL: 1, ST_HOT: 2, ST_CRIT: 3}


@dataclass
class Channel:
    id: str                 # estable: "<device_id>/<clave>"
    device_id: str
    kind: str               # TEMP, FAN...
    label: str              # nombre legible
    raw_label: str = ""     # etiqueta original (Tctl, Package id 0...)
    path: str = ""          # fichero sysfs (si aplica)
    scale: float = 1.0      # multiplicador para pasar a la unidad
    warn: Optional[float] = None
    crit: Optional[float] = None
    vmin: float = 0.0       # rango del indicador
    vmax: float = 100.0
    auto_thresholds: bool = True
    primary: bool = False   # valor principal del dispositivo
    advanced: bool = False  # se oculta por defecto (voltajes, etc.)
    value: Optional[float] = None
    state: str = ST_NODATA
    text: str = ""          # valor textual (estado de batería...)
    tjmax: Optional[float] = None   # límite de diseño (CPU) si se conoce
    throttling: bool = False        # el hardware está bajando la velocidad por calor
    interval: float = 0.0           # intervalo propio (0 = el del dispositivo)
    slow: bool = False              # habla con el firmware (EC/SMM): leer con calma
    note: str = ""                  # por qué está oculto por defecto (advanced, duplicate...)

    def to_dict(self):
        return {
            "id": self.id, "device": self.device_id, "kind": self.kind,
            "label": self.label, "raw_label": self.raw_label,
            "unit": UNITS.get(self.kind, ""), "warn": self.warn, "crit": self.crit,
            "vmin": self.vmin, "vmax": self.vmax, "auto": self.auto_thresholds,
            "primary": self.primary, "advanced": self.advanced,
            "value": self.value, "state": self.state, "text": self.text,
            "tjmax": self.tjmax, "throttling": self.throttling, "note": self.note,
        }


@dataclass
class Device:
    id: str                 # estable (ruta real del dispositivo + nombre)
    cls: str                # CPU, GPU...
    name: str               # nombre legible ("CPU", "GPU NVIDIA"...)
    driver: str = ""        # nombre hwmon (k10temp...)
    model: str = ""         # modelo comercial si se conoce
    interval: float = 2.0   # segundos entre lecturas
    base_interval: float = -1.0   # mínimo propio del dispositivo (NVMe, batería: 5 s); -1 = sin fijar
    pm_path: str = ""       # power/runtime_status a vigilar (dGPU, discos)
    asleep: bool = False
    hybrid: bool = False    # GPU con reposo automático (portátil híbrido)
    limits_pending: bool = False   # descubierto dormido: límites sin leer (hay que releerlos al despertar)
    media: str = ""         # discos: nvme | ssd | hdd | unknown (decide cada cuánto se leen)
    warm: bool = False      # disco duro templado: se vigila más a menudo hasta que se enfríe
    channels: list = field(default_factory=list)
    state: str = ST_NODATA

    def to_dict(self):
        return {
            "id": self.id, "cls": self.cls, "name": self.name,
            "driver": self.driver, "model": self.model, "asleep": self.asleep,
            "hybrid": self.hybrid, "interval": self.interval, "media": self.media,
            "warm": self.warm,
            "state": self.state, "channels": [c.to_dict() for c in self.channels],
        }
