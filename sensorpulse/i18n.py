"""Traducciones. Igual que en SysCheckup: se leen los .po directamente (sin compilar a .mo).

Textos fuente en inglés. Idiomas: es, en, fr, it, de, pt, ja, zh. Se detecta el idioma del sistema
(LANGUAGE, LC_ALL, LC_MESSAGES, LANG); si no es uno de los soportados, inglés.
El usuario puede fijarlo en Ajustes (clave "language" de la configuración).
"""

import json
import os
import re

LANGS = ("es", "en", "fr", "it", "de", "pt", "ja", "zh")
_CAT = {}
_LANG = ["en"]


def po_dirs():
    here = os.path.dirname(os.path.realpath(__file__))
    return [os.path.join(here, "po"), "/usr/share/sensorpulse/po"]


def parse_po(path):
    """Lector mínimo de .po: msgid/msgstr con cadenas de varias líneas."""
    cat, key, cur, field = {}, None, [], None

    def unq(t):
        esc = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
        return re.sub(r'\\(.)', lambda m: esc.get(m.group(1), m.group(1)), t.strip()[1:-1])

    def flush():
        if key is not None and field == "msgstr":
            val = "".join(cur)
            if key and val:
                cat[key] = val
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, ValueError):     # ValueError: fichero que no es UTF-8
        return {}
    for line in lines + [""]:
        line = line.strip()
        if line.startswith("msgid "):
            flush()
            key, cur, field = None, [unq(line[6:])], "msgid"
        elif line.startswith("msgstr "):
            key, cur, field = "".join(cur), [unq(line[7:])], "msgstr"
        elif line.startswith('"') and field:
            cur.append(unq(line))
        elif not line or line.startswith("#"):
            flush()
            if not line.startswith("#"):
                key, cur, field = None, [], None
    flush()
    return cat


def system_language():
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        for v in os.environ.get(var, "").split(":"):
            code = v.split("_")[0].split(".")[0].lower()
            if code in LANGS:
                return code
            if code and code not in ("c", "posix"):
                return "en"
    return "en"


def _configured_language():
    base = os.environ.get("XDG_CONFIG_HOME") or ""
    if not os.path.isabs(base):          # XDG: las rutas relativas se ignoran
        base = os.path.expanduser("~/.config")
    try:
        with open(os.path.join(base, "sensorpulse", "config.json"), encoding="utf-8") as f:
            code = json.load(f).get("language") or ""
    except (OSError, ValueError, AttributeError, RecursionError):
        # RecursionError: un config.json anidado miles de niveles tumbaba TODOS los procesos
        # al importar este módulo (servicio en bucle de reinicios, interfaz y CLI)
        return ""
    return code if isinstance(code, str) else ""


def set_language(code=None):
    if not isinstance(code, str):
        code = None
    code = code or _configured_language() or system_language()
    if code == "auto":
        code = system_language()
    code = code if code in LANGS else "en"
    _LANG[0] = code
    _CAT.clear()
    if code != "en":
        for d in po_dirs():
            cat = parse_po(os.path.join(d, code + ".po"))
            if cat:
                _CAT.update(cat)
                break
    return code


def language():
    return _LANG[0]


def _(s):
    return _CAT.get(s, s) if s else s


def N_(s):
    """Marca una cadena para traducir SIN traducirla ahora (tablas a nivel de módulo, que se
    evalúan al importar, antes de que se elija el idioma). Se traduce al usarla: _(texto)."""
    return s


set_language()
