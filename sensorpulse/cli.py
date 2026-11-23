"""Órdenes de terminal (sin interfaz gráfica).

sensorpulse status          valores actuales en texto
sensorpulse status --json   valores actuales en JSON (para Conky, scripts...)
sensorpulse report          informe anónimo del equipo (Markdown)
sensorpulse export ID [--days N] [--json]   historial de un sensor (CSV por defecto)
"""

import json
import sys
import time


def _snapshot():
    """Del servicio (arrancándolo si hace falta). Si no hay sesión D-Bus, una lectura
    directa que NO toca los discos SATA: un Conky que llame cada 5 s no debe impedir
    que el disco se apague."""
    try:
        from .client import Client, ensure_service
        if ensure_service(timeout_ms=4000):
            snap = Client().snapshot()
            if snap.get("devices"):
                return snap
    except Exception:
        pass
    from . import config as cfgmod
    from .core.engine import Engine
    cfg = cfgmod.load()
    eng = Engine(cfg["intervals"].get("fast", 2), cfg["intervals"].get("hdd", 1800),
                 skip_drivers=("drivetemp",))
    eng.poll(force=True)
    time.sleep(0.5)
    eng.poll(force=True)   # la carga de CPU necesita dos lecturas
    snap = eng.snapshot()
    if eng.nvml is not None:
        eng.nvml.shutdown()
    return snap


def _num(v, kind):
    """Decimales donde importan (la 0.3.5 escribía «1 V» para 1,23 V y «5 W» para 5,4 W)."""
    if kind == "volt":
        return "%.2f" % v
    if kind in ("power", "current") and abs(v) < 10:
        return "%.1f" % v
    return "%.0f" % v


def _status_text(snap):
    from . import config as cfgmod
    from .i18n import _
    cfg = cfgmod.load()
    words = {"normal": _("Normal"), "hot": _("High"), "critical": _("Critical"),
             "sleep": _("asleep"), "nodata": "—"}
    out = []
    for d in snap.get("devices", []):
        chans = [c for c in d.get("channels", []) if cfgmod.is_visible(cfg, c, d.get("cls"))]
        if not chans:
            continue   # no imprimir cabeceras vacías
        out.append(d.get("name", ""))
        for c in chans:
            v = c.get("value")
            if c.get("text"):
                val = c["text"]
            elif v is None:
                val = "—"
            else:
                vv = cfgmod.to_display(v, cfg.get("unit", "C"), c.get("kind"))
                unit = c.get("unit", "")
                if c.get("kind") == "temp" and cfg.get("unit") == "F":
                    unit = "°F"
                val = "%s%s" % (_num(vv, c.get("kind")),
                                 unit if unit in ("°C", "°F", "%") else " " + unit)
            st = words.get(c.get("state"), "")
            out.append("  %-22s %10s  %s" % (cfgmod.display_name(cfg, c), val,
                                              st if c.get("kind") == "temp" else ""))
    return "\n".join(out)


def _export(args):
    import argparse
    import csv
    import math
    from .i18n import _
    # mensajes traducidos como el resto del programa (antes, siempre en español)
    ap = argparse.ArgumentParser(prog="sensorpulse export",
                                 description=_("History of a sensor (CSV by default)."))
    ap.add_argument("id", help=_("sensor identifier (see “sensorpulse status --json”)"))
    ap.add_argument("--days", type=float, default=1.0, help=_("days back (default 1)"))
    ap.add_argument("--json", action="store_true", help=_("JSON output instead of CSV"))
    ns = ap.parse_args(args)
    if not math.isfinite(ns.days) or not 0 < ns.days <= 3650:
        ap.error(_("--days must be between 0 and 3650"))
    from .client import Client, ensure_service
    if not ensure_service():
        print("sensorpulse: " + _("the service is not available"), file=sys.stderr)
        return 1
    cl = Client()
    known = {c.get("id") for d in cl.snapshot().get("devices", []) for c in d.get("channels", [])}
    if ns.id not in known:
        print("sensorpulse: " + _("there is no sensor “%s”") % ns.id, file=sys.stderr)
        return 3
    data = cl.history_range(ns.id, int(ns.days * 86400))
    rows = [{"time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)), "value": v,
             "min": mn, "max": mx} for t, v, mn, mx in data]
    if ns.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        w = csv.writer(sys.stdout)
        w.writerow(["time", "value", "min", "max"])
        for r in rows:
            w.writerow([r["time"], r["value"], r["min"], r["max"]])
    return 0


def main(argv):
    from .i18n import set_language
    set_language()
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        snap = _snapshot()
        if "--json" in argv:
            print(json.dumps(snap, ensure_ascii=False, indent=1))
        else:
            print(_status_text(snap))
        return 0
    if cmd == "export":
        return _export(argv[1:])
    if cmd == "report":
        from .report import build
        print(build(_snapshot()))
        return 0
    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
