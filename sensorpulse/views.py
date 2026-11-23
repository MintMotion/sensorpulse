"""Lo que hay que mostrar en el panel y en el flotante, calculado en un solo sitio.

El servicio lo publica junto a la foto (snapshot["views"]) para que el icono de la
bandeja, el applet y el desklet de Cinnamon y la extensión de GNOME enseñen exactamente
lo mismo: sensores elegidos, nombres cambiados, °C/°F, estado, símbolo y color.
"""

import time

from . import config as cfgmod
from .i18n import _, language
from .core.model import temp_state

PREFS = ["cpu", "gpu", "disk", "battery", "fans", "memory"]
SYMBOLS = {"normal": "✓", "hot": "⚠", "critical": "‼"}

GREEN = (0.13, 0.77, 0.37)
YELLOW = (0.92, 0.70, 0.03)
ORANGE = (0.98, 0.45, 0.09)
RED = (0.94, 0.27, 0.27)
GREY = (0.55, 0.58, 0.62)
CB = {"ok": (0.0, 0.45, 0.70), "warn": (0.90, 0.62, 0.0), "crit": (0.80, 0.47, 0.65)}


def _mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def value_rgb(v, warn, crit, colorblind=False, accent=(0.13, 0.83, 0.93)):
    if v is None:
        return GREY
    if warn is None or crit is None:
        return accent
    g, y, o, r = (GREEN, YELLOW, ORANGE, RED) if not colorblind else \
        (CB["ok"], CB["warn"], CB["warn"], CB["crit"])
    start = warn - 15.0
    if v <= start:
        return g
    if v <= warn:
        return _mix(g, y, (v - start) / 15.0)
    if v <= crit:
        t = (v - warn) / max(1.0, crit - warn)
        return _mix(y, o, t * 2) if t < 0.5 else _mix(o, r, (t - 0.5) * 2)
    return r


def to_hex(rgb):
    return "#%02x%02x%02x" % tuple(int(max(0, min(1, x)) * 255) for x in rgb)


PANEL_MAX = 8        # valores como máximo en la barra (la 0.3.6 se quedaba en 3)
PANEL_LIMIT = 16     # elegidos para el panel en total (los que no caben salen al hacer clic)


def featured(cfg, snapshot, key, limit=6):
    chosen = cfg["sensors"].get(key) or []
    index = {c["id"]: (c, d) for d in snapshot.get("devices", []) for c in d["channels"]}
    if chosen:
        return [index[c] for c in chosen if c in index][:limit]
    out = []
    for cls in PREFS:
        for d in snapshot.get("devices", []):
            if d["cls"] != cls:
                continue
            prim = next((c for c in d["channels"] if c["primary"]), None)
            if prim and cfgmod.is_visible(cfg, prim, d["cls"]):
                out.append((prim, d))
                break
    return out[:limit]


def muted_text(snapshot):
    """«alarmas silenciadas hasta HH:MM» (cadena vacía si no hay silencio)."""
    until = snapshot.get("muted_until") or 0
    if not snapshot.get("muted") or until <= time.time():
        return ""
    return _("alarms muted until %s") % time.strftime("%H:%M", time.localtime(until))


def state_of(c):
    if c.get("kind") != "temp":
        return None
    return temp_state(c.get("value"), c.get("warn"), c.get("crit"), bool(c.get("throttling")))


def _tiny(kind, v, conv):
    """Texto muy corto para dentro de un indicador del panel («45°», «1,4k», «38W»)."""
    if v is None:
        return "–"
    if kind == "temp":
        return "%d°" % round(conv(v))
    if kind == "percent":
        return "%d%%" % round(v)
    if kind == "fan":
        return ("%.1fk" % (v / 1000.0)).replace(".0k", "k") if v >= 1000 else "%d" % round(v)
    if kind == "power":
        return "%dW" % round(v)
    return "%g" % round(v, 1)


def _frac(v, lo, hi):
    if v is None or lo is None or hi is None or hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (v - lo) / float(hi - lo)))


def item(cfg, c, d, alarms=()):
    """Un elemento listo para mostrar (sin depender de GTK)."""
    unit_f = cfg.get("unit") == "F" and c["kind"] == "temp"
    conv = (lambda x: x * 9 / 5 + 32 if x is not None else None) if unit_f else (lambda x: x)
    v = c.get("value")
    asleep = bool(d.get("asleep"))
    kind = c["kind"]
    unit = ("°F" if unit_f else "°C") if kind == "temp" else c.get("unit", "")
    if asleep:
        short = "zZ"
    elif v is None:
        short = "–"
    elif kind == "temp":
        short = "%d°" % round(conv(v))
    elif kind == "percent":
        short = "%d%%" % round(v)
    elif kind == "fan":
        short = "%d" % round(v)
    elif kind == "power":
        short = ("%.0fW" if v >= 10 else "%.1fW") % v
    elif kind == "status":
        short = c.get("text") or ""
    else:
        short = "%g" % round(v, 1)
    st = state_of(c) if not asleep else None
    rgb = value_rgb(v, c.get("warn"), c.get("crit"), bool(cfg.get("colorblind"))) \
        if kind == "temp" else None
    lo, hi = c.get("vmin"), c.get("vmax")
    if kind == "percent":
        lo, hi = 0.0, 100.0
    return {
        "id": c["id"], "device": d["id"], "kind": kind,
        "label": cfgmod.display_name(cfg, c), "short": short, "unit": unit,
        "value": None if v is None else round(conv(v), 1),
        "warn": conv(c.get("warn")) if kind == "temp" else c.get("warn"),
        "crit": conv(c.get("crit")) if kind == "temp" else c.get("crit"),
        "vmin": conv(c.get("vmin")) if kind == "temp" else c.get("vmin"),
        "vmax": conv(c.get("vmax")) if kind == "temp" else c.get("vmax"),
        "state": st, "symbol": SYMBOLS.get(st, ""), "throttling": bool(c.get("throttling")),
        "color": to_hex(rgb) if rgb else None, "asleep": asleep,
        "alarm": c["id"] in alarms,
        # indicador del panel: fracción del recorrido y texto corto
        "frac": 0.0 if asleep else round(_frac(v, lo, hi), 3),
        "tiny": "zZ" if asleep else _tiny(kind, v, conv),
        "renamed": c["id"] in (cfg["sensors"].get("renames") or {}),
    }


def panel_count(cfg):
    """Valores en la barra del panel (1-8). Tolera un valor que no sea número (no debe dejar
    sin datos el panel, la extensión ni el applet)."""
    try:
        n = int(cfg.get("panel_count", 3))
    except (TypeError, ValueError, OverflowError):
        n = 3
    return max(1, min(PANEL_MAX, n))


def panel_split(cfg, snapshot):
    """(en la barra, solo al hacer clic): pares (canal, dispositivo).
    En la barra: los primeros «panel_count» elegidos para el panel. Al hacer clic: los
    elegidos que no caben y los marcados como «solo al hacer clic»."""
    chosen = featured(cfg, snapshot, "panel", PANEL_LIMIT)
    n = panel_count(cfg)
    bar, extra = chosen[:n], chosen[n:]
    seen = {c["id"] for c, _d in chosen}
    index = {c["id"]: (c, d) for d in snapshot.get("devices", []) for c in d["channels"]}
    for cid in cfg["sensors"].get("panel_menu") or []:
        if cid in index and cid not in seen:
            extra.append(index[cid])
            seen.add(cid)
    return bar, extra[:PANEL_LIMIT]


def build(cfg, snapshot):
    alarms = set(snapshot.get("alarm_channels") or [])
    levels = snapshot.get("alarm_levels") or {}
    bar, extra = panel_split(cfg, snapshot)
    # «panel» mantiene el formato de la 0.3.6 (la barra = panel[:panel_count]) para que un
    # applet o una extensión aún sin recargar sigan funcionando tras actualizar
    panel = [item(cfg, c, d, alarms) for c, d in bar]
    panel_extra = [item(cfg, c, d, alarms) for c, d in extra]
    for it, (_c, d) in zip(panel_extra, extra):
        it["device_name"] = d.get("name", "")
    floating = [item(cfg, c, d, alarms) for c, d in featured(cfg, snapshot, "floating", 9)]
    summary = []
    for d in snapshot.get("devices", []):
        prim = next((c for c in d["channels"] if c["primary"]), None)
        if prim and cfgmod.is_visible(cfg, prim, d["cls"]):
            it = item(cfg, prim, d, alarms)
            # con nombre propio se muestra ese nombre («Placa base») en vez del del dispositivo
            it["device_name"] = it["label"] if it["renamed"] else d.get("name", "")
            summary.append(it)
    for it in panel + panel_extra + floating + summary:
        it["alarm_level"] = levels.get(it["id"]) if it["alarm"] else None
    style = cfg.get("panel_style", "text")
    return {
        "unit": "F" if cfg.get("unit") == "F" else "C",
        "colorblind": bool(cfg.get("colorblind")),
        "blink": cfg["alarms"].get("blink", True),
        "panel_count": len(panel),
        "panel": panel, "panel_extra": panel_extra,
        "panel_style": style if style in ("text", "gauge") else "text",
        "panel_labels": bool(cfg.get("panel_labels", True)),
        "accent": cfg.get("accent") or "#22d3ee",
        "floating": floating, "summary": summary,
        "muted_text": muted_text(snapshot),
        "lang": language(),
        "tr": panel_texts(),
    }


def panel_texts():
    """Textos de los menús del applet, la extensión y el desklet en el idioma elegido (así no
    hace falta repetir las traducciones en JavaScript para cada idioma nuevo)."""
    return {
        "open": _("Open window"), "settings": _("Settings"),
        "mute": _("Mute alarms for 1 hour"), "waiting": _("Waiting for data…"),
        "rename": _("Rename a sensor…"), "more": _("More values"),
        "asleep": _("asleep"), "normal": _("normal"), "high": _("high"),
        "critical": _("critical"), "slowing down": _("slowing down"),
    }
