"""Informe anónimo del equipo, listo para pegar en un foro o en una incidencia.

No incluye nombre del equipo, usuario, números de serie, direcciones MAC ni IP.
"""

import glob
import os
import platform

from . import APP_NAME, VERSION
from .core import sysfs

PCI_VENDORS = {"0x1002": "AMD", "0x10de": "NVIDIA", "0x8086": "Intel"}


def _os_name():
    for f in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("PRETTY_NAME="):
                        return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
    return "?"


def _gpus():
    out = []
    for d in sorted(glob.glob(sysfs.p("sys/bus/pci/devices/*"))):
        if not (sysfs.read_text(os.path.join(d, "class"), "") or "").startswith("0x03"):
            continue
        ven = (sysfs.read_text(os.path.join(d, "vendor"), "") or "").lower()
        dev = sysfs.read_text(os.path.join(d, "device"), "") or ""
        drv = os.path.basename(os.path.realpath(os.path.join(d, "driver"))) \
            if os.path.exists(os.path.join(d, "driver")) else "-"
        ctl = (sysfs.read_text(os.path.join(d, "power", "control"), "") or "").strip()
        out.append("%s [%s:%s] driver=%s pm=%s" % (PCI_VENDORS.get(ven, ven), ven[2:],
                                                    dev[2:], drv, ctl or "-"))
    return out


def _hwmon_drivers():
    names = []
    for hw in sorted(glob.glob(sysfs.p("sys/class/hwmon/hwmon*"))):
        names.append(sysfs.read_text(os.path.join(hw, "name"), "") or "?")
    return names


def _fmt(v):
    if v is None:
        return "—"
    return ("%.1f" % v).rstrip("0").rstrip(".")


def _cell(v):
    """Texto de una celda de la tabla Markdown: lo que viene del hardware puede traer «|»
    o saltos de línea que romperían la tabla."""
    return str(v if v is not None else "").replace("\\", "\\\\").replace("|", "\\|") \
        .replace("\r", " ").replace("\n", " ")


def build(snapshot, extra_notes=None):
    """snapshot: dict como el que publica el servicio."""
    from .core.discovery import _cpu_model
    lines = ["### %s %s — informe del equipo / hardware report" % (APP_NAME, VERSION), ""]
    lines.append("- OS: %s" % _os_name())
    lines.append("- Kernel: %s (%s)" % (platform.release(), platform.machine()))
    lines.append("- Desktop: %s / %s" % (os.environ.get("XDG_CURRENT_DESKTOP", "?"),
                                         os.environ.get("XDG_SESSION_TYPE", "?")))
    lines.append("- CPU: %s" % (_cpu_model() or "?"))
    for g in _gpus():
        lines.append("- GPU: %s" % g)
    lines.append("- hwmon: %s" % (", ".join(_hwmon_drivers()) or "—"))
    thr = bool(glob.glob(sysfs.p("sys/devices/system/cpu/cpu0/thermal_throttle")))
    lines.append("- thermal_throttle: %s" % ("yes" if thr else "no"))
    for n in extra_notes or []:
        lines.append("- %s" % n)
    lines.append("")
    lines.append("| Device | Driver | Sensor | Raw | Value | Warn | Crit | TjMax |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for d in snapshot.get("devices", []):
        name = d.get("name", "")
        if d.get("model"):
            name += " (%s)" % d["model"]
        for c in d.get("channels", []):
            val = c.get("text") or (_fmt(c.get("value")) + " " + (c.get("unit") or "")).strip()
            if d.get("asleep"):
                val = "asleep"
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % tuple(_cell(x) for x in (
                name, d.get("driver", ""), c.get("label", ""), c.get("raw_label", "") or "",
                val, _fmt(c.get("warn")), _fmt(c.get("crit")), _fmt(c.get("tjmax")))))
            name = ""
    return "\n".join(lines) + "\n"
