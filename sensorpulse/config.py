"""Configuración del usuario: ~/.config/sensorpulse/config.json"""

import copy
import json
import math
import os
import re
import sys
import tempfile
import time
import uuid

from . import PACKAGE

DEFAULTS = {
    "version": 1,
    "first_run_done": False,
    "seen_version": None,
    "display": {"panel": True, "window": False, "floating": False},
    "style": "cockpit",        # cockpit | hud | oled | rings | bars | minimal | glass
    "accent": "#22d3ee",
    "theme": "auto",           # auto (sigue al sistema) | dark | light
    "size": "medium",          # small | medium | large
    "unit": "C",               # C | F
    "autostart": True,
    # claves que escribe Ajustes (la 0.3.5 no las saneaba: "panel_count": "x" dejaba sin
    # datos el panel, la extensión de GNOME y el applet)
    "panel_count": 3,          # valores en la barra del panel (1-8; antes 1-3)
    "panel_style": "text",     # text (nombre y número) | gauge (indicadores tipo cuentakilómetros)
    "panel_labels": True,      # nombre junto a cada valor del panel
    "cinnamon_applet_placed": False,   # el applet ya se colocó a la izquierda de los iconos
    "colorblind": False,
    "animate_fans": True,
    "language": "auto",        # auto | es | en | fr | it | de | pt | ja | zh
    "alarms": {
        "enabled": True, "visual": True, "blink": True, "notify": True,
        "sound": False, "sound_name": "sensorpulse-critical", "repeat": False,
        "notify_hot": False, "hysteresis": 5, "delay": 10, "delay_warn": 30,
        "throttle": True, "idle_hot": True, "fan_stop": True, "warn_sound": False,
        "warn_sound_name": "sensorpulse-warning",
    },
    "rules": [],
    "sensors": {
        "visible": {},         # channel_id -> bool (si no está, se usa el valor por defecto)
        "renames": {},         # channel_id -> nombre
        "order": [],           # device_id en orden
        "panel": [],           # channel_ids en el panel (vacío = automático)
        "panel_menu": [],      # channel_ids que solo se ven al hacer clic en el panel
        "floating": [],        # channel_ids en el flotante (vacío = automático)
    },
    "floating": {"x": None, "y": None, "locked": False, "on_top": False,
                 "window_type": "auto",   # auto | dock | desktop | utility
                 "click_through": False, "opacity": 1.0, "monitor": None, "panel_bg": False},
    "window": {"width": 900, "height": 620, "layout": "bento", "aurora": False, "big": None},
    "intervals": {"fast": 2, "hdd": 1800, "ssd": 10},
    "hdd_adaptive": True,      # disco duro templado: leerlo cada 2 min hasta que se enfríe
    "disk_temps": True,        # False: no leer la temperatura de los discos SATA (que descansen)
}


def config_dir():
    # la especificación XDG manda ignorar las rutas relativas
    base = os.environ.get("XDG_CONFIG_HOME") or ""
    if not os.path.isabs(base):
        base = os.path.expanduser("~/.config")
    return os.path.join(base, PACKAGE)


def config_path():
    return os.path.join(config_dir(), "config.json")


# Aviso para la interfaz y el servicio: si al cargar hubo que apartar un config.json ilegible
# o descartar reglas inválidas, aquí queda el texto (se muestra una vez y se borra).
LAST_PROBLEM = {"corrupt_file": None, "dropped_rules": 0, "unreadable": None}

_ID_RE = re.compile(r"[A-Za-z0-9_.:@/+\-]{1,200}")
_SOUND_RE = re.compile(r"[A-Za-z0-9_-]{1,80}")
_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")

# límites de los valores numéricos: (mínimo, máximo)
_RANGES = {
    ("intervals", "fast"): (1, 60),
    ("intervals", "hdd"): (60, 14400),      # desde 1 min («vigilancia estrecha»)
    ("intervals", "ssd"): (2, 600),
    ("alarms", "hysteresis"): (0, 50),
    ("alarms", "delay"): (0, 3600),
    ("alarms", "delay_warn"): (0, 3600),
    ("floating", "opacity"): (0.1, 1.0),
    ("window", "width"): (300, 8000),
    ("window", "height"): (200, 8000),
    ("panel_count",): (1, 8),
}
_CHOICES = {
    ("style",): ("cockpit", "hud", "oled", "rings", "bars", "minimal", "glass"),
    ("theme",): ("auto", "dark", "light", "deep"),
    ("size",): ("small", "medium", "large"),
    ("unit",): ("C", "F"),
    ("window", "layout"): ("bento", "classic"),
    ("floating", "window_type"): ("auto", "dock", "desktop", "utility"),
    ("language",): ("auto", "es", "en", "fr", "it", "de", "pt", "ja", "zh"),
    ("panel_style",): ("text", "gauge"),
}
_INTS = {("panel_count",), ("intervals", "hdd"), ("intervals", "ssd"), ("alarms", "delay"), ("alarms", "delay_warn"),
         ("window", "width"), ("window", "height")}
_SOUNDS = {("alarms", "sound_name"), ("alarms", "warn_sound_name")}


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _coerce(default, value):
    """Devuelve value si tiene el tipo de default; si no, None (= usar el por defecto)."""
    if isinstance(default, bool):
        return value if isinstance(value, bool) else None
    if isinstance(default, (int, float)):
        return value if _is_num(value) else None
    if isinstance(default, str):
        return value if isinstance(value, str) else None
    if isinstance(default, list):
        return value if isinstance(value, list) else None
    return value   # default None u otros: se acepta tal cual


def _sanitize_rule(r):
    """Una regla manual válida o None. Normaliza los campos que usa el motor."""
    if not isinstance(r, dict):
        return None
    ch, op = r.get("channel"), r.get("op", "above")
    if not isinstance(ch, str) or not ch or op not in ("above", "below") or not _is_num(r.get("value")):
        return None
    rid = r.get("id")
    out = dict(r)
    out["id"] = rid if isinstance(rid, str) and _ID_RE.fullmatch(rid) else uuid.uuid4().hex[:10]
    out["op"] = op
    out["value"] = float(r["value"])
    d = r.get("delay", 10)
    out["delay"] = int(min(3600, max(0, d))) if _is_num(d) else 10
    h = r.get("hysteresis")
    out["hysteresis"] = float(min(50, max(0, h))) if _is_num(h) else None
    for k, dv in (("notify", True), ("sound", False), ("visual", True), ("repeat", False),
                  ("enabled", True)):
        out[k] = r[k] if isinstance(r.get(k), bool) else dv
    sn = r.get("sound_name")
    out["sound_name"] = sn if isinstance(sn, str) and _SOUND_RE.fullmatch(sn) else "alarm-clock-elapsed"
    if out.get("level") not in ("hot", "critical"):
        out.pop("level", None)
    return out


def sanitize(cfg, defaults=None, path=()):
    """Deja la configuración con los tipos y rangos esperados. Lo que no encaja vuelve al
    valor por defecto; las claves desconocidas se conservan (versiones futuras)."""
    top = defaults is None
    defaults = DEFAULTS if top else defaults
    if not isinstance(cfg, dict):
        return copy.deepcopy(defaults)
    out = {}
    for k, v in cfg.items():
        if k not in defaults:
            out[k] = v
    for k, dv in defaults.items():
        v = cfg.get(k, None)
        key = path + (k,)
        if isinstance(dv, dict):
            out[k] = sanitize(v if isinstance(v, dict) else {}, dv, key)
            continue
        v = _coerce(dv, v) if k in cfg else None
        if v is None and dv is not None:
            v = copy.deepcopy(dv)
        if key in _RANGES and _is_num(v):
            lo, hi = _RANGES[key]
            v = min(hi, max(lo, v))
        if key in _INTS and _is_num(v):
            v = int(round(v))
        if key in _CHOICES and v not in _CHOICES[key]:
            v = dv
        if key in _SOUNDS and not _SOUND_RE.fullmatch(str(v)):
            v = dv
        if k == "accent" and not _COLOR_RE.fullmatch(str(v)):
            v = dv
        out[k] = v
    if top:
        _sanitize_top(out)
    else:
        _sanitize_section(path, out, defaults)
    return out


def _sanitize_top(cfg):
    # «ya se aplicó la extensión»: su AUSENCIA tiene significado (ver app.py), así que no
    # va en DEFAULTS; si está, tiene que ser un booleano
    if "ext_applied" in cfg and not isinstance(cfg["ext_applied"], bool):
        del cfg["ext_applied"]
    good = [x for x in (_sanitize_rule(r) for r in cfg.get("rules", [])) if x is not None]
    LAST_PROBLEM["dropped_rules"] = len(cfg.get("rules", [])) - len(good)
    seen, uniq = set(), []
    for r in good:           # id repetido: el motor usa el id como clave
        if r["id"] in seen:
            r["id"] = uuid.uuid4().hex[:10]
        seen.add(r["id"])
        uniq.append(r)
    cfg["rules"] = uniq
    if LAST_PROBLEM["dropped_rules"]:
        print("sensorpulse: %d regla(s) no válidas descartadas" % LAST_PROBLEM["dropped_rules"],
              file=sys.stderr)


def _sanitize_section(path, sec, defaults):
    if path == ("sensors",):
        for k in ("visible", "renames"):
            d = sec.get(k)
            sec[k] = {a: (bool(b) if k == "visible" else str(b)) for a, b in d.items()
                      if isinstance(a, str)} if isinstance(d, dict) else {}
        for k in ("order", "panel", "panel_menu", "floating"):
            sec[k] = [a for a in sec.get(k, []) if isinstance(a, str)]
    elif path == ("floating",):
        for k in ("x", "y"):
            if not _is_num(sec.get(k)):
                sec[k] = None
        if not (sec.get("monitor") is None or _is_num(sec.get("monitor"))):
            sec["monitor"] = None
    elif path == ("window",):
        if not (sec.get("big") is None or isinstance(sec.get("big"), str)):
            sec["big"] = None


def _merge(base, over):
    """Mezcla la configuración guardada sobre los valores por defecto. Ignora lo que no
    tenga el tipo esperado: un config.json editado a mano o dañado no debe impedir el arranque."""
    if not isinstance(over, dict):
        return base
    for k, v in over.items():
        cur = base.get(k)
        if isinstance(cur, dict):
            if isinstance(v, dict):
                _merge(cur, v)
        elif isinstance(cur, list) and not isinstance(v, list):
            continue
        else:
            base[k] = v
    return base


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_bak(path):
    try:
        raw = _read_json(path + ".bak")
        return raw if isinstance(raw, dict) else None
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):
        return None


def _stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def load():
    """Lee la configuración. Un archivo ilegible NUNCA se sobrescribe: se aparta como
    config.json.corrupt-FECHA y se usa la última copia buena (.bak) o los valores por defecto."""
    path = config_path()
    raw = None
    LAST_PROBLEM["unreadable"] = None
    try:
        raw = _read_json(path)
        if not isinstance(raw, dict):
            raise ValueError("la raíz no es un objeto")
    except FileNotFoundError:
        raw = None
    except OSError as e:
        # sin permiso, error de E/S, es una carpeta...: el fichero NO está dañado y no se
        # aparta (la 0.3.5 lo renombraba como «corrupto» y el usuario perdía sus ajustes).
        # Se usa la copia buena o los valores por defecto, y save() no lo sobrescribe.
        LAST_PROBLEM["unreadable"] = path
        print("sensorpulse: no se puede leer %s (%s)" % (path, e.strerror or e), file=sys.stderr)
        raw = _read_bak(path)
    except (ValueError, UnicodeDecodeError, RecursionError):
        # RecursionError: JSON anidado miles de niveles (antes tumbaba el programa)
        raw = None
        try:
            dest = "%s.corrupt-%s" % (path, _stamp())
            os.replace(path, dest)
            LAST_PROBLEM["corrupt_file"] = dest
            print("sensorpulse: configuración ilegible, guardada en %s" % dest, file=sys.stderr)
        except OSError:
            LAST_PROBLEM["corrupt_file"] = path
        raw = _read_bak(path)
    cfg = copy.deepcopy(DEFAULTS)
    if raw:
        _merge(cfg, raw)
    return sanitize(cfg)


def _private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def save(cfg):
    """Escritura atómica: fichero temporal único (la interfaz y el servicio pueden guardar a
    la vez), fsync, permisos 0600 y copia .bak de la última versión que se pudo leer."""
    d = config_dir()
    _private_dir(d)
    path = config_path()
    # un config.json que existe pero no se puede leer (permisos, E/S) no se pisa: tiene los
    # ajustes del usuario. El llamador recibe el OSError y avisa.
    try:
        current = _read_json(path)
    except FileNotFoundError:
        current = None
    except OSError:
        raise
    except (ValueError, UnicodeDecodeError, RecursionError):
        current = None
    fd, tmp = tempfile.mkstemp(prefix="config.json.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        if isinstance(current, dict):
            # copia .bak: se crea ya con 0600 (copyfile la dejaba un instante con 0644)
            bfd, btmp = tempfile.mkstemp(prefix="config.json.", suffix=".bak.tmp", dir=d)
            try:
                with os.fdopen(bfd, "w", encoding="utf-8") as f:
                    json.dump(current, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(btmp, path + ".bak")
            except OSError:
                try:
                    os.unlink(btmp)
                except OSError:
                    pass
        os.replace(tmp, path)
        try:   # que el cambio de nombre sobreviva a un corte de luz
            dfd = os.open(d, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def to_display(value, unit, kind):
    """Convierte °C a °F si el usuario lo pide."""
    if value is None:
        return None
    if kind == "temp" and unit == "F":
        return value * 9 / 5 + 32
    return value


DEFAULT_VISIBLE_CLASSES = {"cpu", "gpu", "disk", "fans", "battery", "memory"}


def is_visible(cfg, ch, dev_cls):
    """¿Se muestra este canal? Respeta la elección del usuario; si no la hay, usa lo recomendado."""
    v = cfg["sensors"]["visible"].get(ch["id"])
    if v is not None:
        return bool(v)
    if ch.get("advanced"):
        return False
    return dev_cls in DEFAULT_VISIBLE_CLASSES


def display_name(cfg, ch):
    return cfg["sensors"]["renames"].get(ch["id"]) or ch["label"]
