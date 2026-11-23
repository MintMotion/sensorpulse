"""TjMax (temperatura máxima de funcionamiento) de los procesadores AMD.

Los valores proceden de las fichas oficiales de amd.com, consultadas el 19/11/2026
(campo «Max. Operating Temperature (Tjmax)»). Primero se buscan los modelos con
valor propio y después las reglas por familia que se cumplen sin excepción en los
datos verificados. Si existe el fichero tjmax_amd.csv (tabla completa por modelo),
sus valores verificados tienen prioridad.

Intel no está aquí: su TjMax lo publica el propio procesador (coretemp, tempN_crit).
"""

import csv
import os
import re

# ------------------------------------------------------------------ modelos concretos
# Excepciones y series mixtas (verificadas una a una en amd.com).
MODELS = {
    # Ryzen 2000 de sobremesa: el 2700X es la excepción (85 °C)
    "2700X": 85.0,
    # Threadripper 2990WX
    "2990WX": 68.0,
    # Ryzen 5000 de sobremesa: serie mixta
    "5500": 90.0, "5800X": 90.0, "5800XT": 90.0, "5900X": 90.0, "5900XT": 90.0,
    "5950X": 90.0, "5700X3D": 90.0, "5800X3D": 90.0,
    "5600X": 95.0, "5500F": 95.0, "5600F": 95.0, "5600G": 95.0, "5600GE": 95.0,
    "5600GT": 95.0, "5600T": 95.0, "5500GT": 95.0, "5700": 95.0, "5700G": 95.0,
    "5700GE": 95.0, "5300G": 95.0, "5800": 95.0, "5900": 95.0,
    # Ryzen 7000 de sobremesa con 3D V-Cache
    "7600X3D": 89.0, "7700X3D": 89.0, "7800X3D": 89.0, "7900X3D": 89.0, "7950X3D": 89.0,
    # Ryzen 7000 de portátil
    "7945HX3D": 89.0,
    "7730U": 95.0, "7735HS": 95.0, "7735U": 95.0, "7535HS": 95.0, "7535U": 95.0,
    "7640HS": 100.0, "7745HX": 100.0, "7840U": 100.0, "7845HX": 100.0, "7940HS": 100.0,
    "7945HX": 100.0,
}

# Modelos cuya ficha oficial no publica TjMax: no se inventa
NO_DATA = {"3100", "3300X"}

MOBILE_SUFFIX = re.compile(r"^(U|H|HS|HX|HX3D|H3D|C)$")   # C: Chromebook

_RE_AI = re.compile(r"Ryzen\s+AI\s+(?:Max\+?\s+)?(?:PRO\s+)?(?:\d+\s+)?(?:HX\s+)?(?:PRO\s+)?(\d{3})", re.I)
_RE_TR = re.compile(r"Threadripper\s+(?:PRO\s+)?(\d{4})(WX|X)?", re.I)
_RE_RYZEN = re.compile(r"Ryzen\s+(?:\d+\s+)?(?:PRO\s+)?(\d{3,4})([A-Z0-9]*)", re.I)
_RE_ATHLON = re.compile(r"Athlon", re.I)

_csv_cache = None


def _csv_paths():
    pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [os.path.join(pkg, "data", "tjmax_amd.csv"),
            os.path.join(os.path.dirname(pkg), "data", "tjmax_amd.csv"),   # árbol de fuentes
            "/usr/share/sensorpulse/tjmax_amd.csv"]


def _norm(s):
    s = re.sub(r"\((?:tm|r)\)|[\u2122\u00ae\u200b\u00a0]", " ", s or "", flags=re.I)
    s = re.sub(r"\bAMD\b", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _load_csv():
    """Tabla completa por modelo (si está instalada).

    Devuelve {clave: valor}; valor None = la ficha oficial no publica TjMax (no se inventa)."""
    global _csv_cache
    if _csv_cache is not None:
        return _csv_cache
    _csv_cache = {}
    for p in _csv_paths():
        try:
            with open(p, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    estado = (row.get("estado") or "")
                    key = _norm(row.get("cadena_proc_cpuinfo") or row.get("modelo_oficial"))
                    if not key:
                        continue
                    if estado.startswith("verificado"):
                        try:
                            _csv_cache[key] = float(row.get("tjmax_c"))
                        except (TypeError, ValueError):
                            pass
                    elif estado.startswith("sin dato") and key not in _csv_cache:
                        _csv_cache[key] = None
            break
        except OSError:
            continue
    return _csv_cache


def _csv_lookup(model):
    """Clave más larga de la tabla contenida en el modelo, respetando límites de palabra
    (que «ryzen 3 3200g» no case con «ryzen 3 3200ge»)."""
    low = _norm(model)
    best = None
    for key, val in _load_csv().items():
        if len(key) <= (len(best[0]) if best else 0):
            continue
        if re.search(r"(?<![\w+])" + re.escape(key) + r"(?![\w+])", low):
            best = (key, val)
    return best


def amd_tjmax(model):
    """TjMax en °C de una CPU AMD a partir de su nombre (/proc/cpuinfo) o None si no se sabe.

    Devuelve (valor, origen) con origen «modelo», «familia» o «tabla»."""
    m = re.sub(r"\((?:tm|r)\)", "", model or "", flags=re.I).replace("™", "")
    low = m.lower()
    # 1) tabla oficial por modelo, si está instalada
    best = _csv_lookup(m)
    if best:
        return (best[1], "tabla") if best[1] is not None else (None, "")
    # 2) Threadripper
    t = _RE_TR.search(m)
    if t:
        num = t.group(1) + (t.group(2) or "")
        pro = bool(re.search(r"Threadripper\s+PRO", m, re.I))
        if num.upper() in MODELS and not pro:
            return MODELS[num.upper()], "modelo"
        if pro and t.group(1)[0] == "3":     # Threadripper PRO 3000 WX: 90 °C
            return 90.0, "familia"
        if t.group(1)[0] in "3579":   # 3000, 5000 WX, 7000, 9000
            return 95.0, "familia"
        return None, ""
    # 3) Ryzen AI (portátil salvo la serie 400 de sobremesa)
    a = _RE_AI.search(m)
    if a:
        serie = a.group(1)[0]
        if serie == "3" or "max" in low:
            return 100.0, "familia"
        if serie == "4":
            # serie AI 400: sobremesa (sufijo G/GE) 95 °C; portátil 100 °C
            if re.search(r"\d{3}GE?\b", m):
                return 95.0, "familia"
            return 100.0, "familia"
        return None, ""
    if _RE_ATHLON.search(m):
        # la regla verificada (95 °C) solo vale para los Athlon de sobremesa de la era Zen
        # (200GE, 300GE, 3000G, Gold/Silver de sobremesa); los antiguos (Athlon II, X2, 6xx…)
        # tienen valores de 61-75 °C y los de portátil no siguen regla: sin tabla, no se sabe
        if re.search(r"Athlon\s+(?:PRO\s+|Gold\s+|Silver\s+)?(?:[23]\d\dGE?|3000G)\b", m, re.I):
            return 95.0, "familia"
        return None, ""
    r = _RE_RYZEN.search(m)
    if not r:
        return None, ""
    num, suf = r.group(1), r.group(2).upper()
    full = num + suf
    pro = bool(re.search(r"\bPRO\b", m, re.I))
    # MODELS son modelos de consumo: el PRO de mismo número puede tener otro valor (2700X 85 °C,
    # PRO 2700X 95 °C), así que a los PRO solo se les aplican las reglas de su familia
    if full in MODELS and not pro:
        return MODELS[full], "modelo"
    if full in NO_DATA:
        return None, ""
    mobile = bool(MOBILE_SUFFIX.match(suf))
    if len(num) == 3:   # serie 200 (portátil): 100 °C
        return (100.0, "familia") if num[0] == "2" else (None, "")
    serie = num[0]
    if mobile:
        if suf == "C":                           # Chromebook: 3000 105 °C, 5000 95 °C
            return (105.0 if serie == "3" else 95.0), "familia"
        if pro and suf == "U" and (serie == "3" or (serie == "5" and num.endswith("75"))):
            return 95.0, "familia"               # PRO 3000 de portátil y PRO 5x75U
        if num in ("3250",):
            return 95.0, "familia"               # Dali
        if serie == "5" and num[2] == "2" and suf == "U":
            return 95.0, "familia"               # Barcelo 5x25U
        if serie in "345":
            return 105.0, "familia"
        if serie == "6":
            return 95.0, "familia"
        if serie == "7":
            gen = num[2]   # 7x30/7x35: Zen 3/3+; 7x40/7x45: Zen 4
            if gen == "3":
                return 95.0, "familia"
            if gen == "4":
                return 100.0, "familia"
            return None, ""           # 7x20 (Mendocino) y otros: AMD no publica el dato
        if serie in "89":
            return 100.0, "familia"
        return None, ""
    # sobremesa
    if serie in "13489":
        return 95.0, "familia"
    if serie == "7":
        return (89.0, "familia") if "X3D" in suf else (95.0, "familia")
    if serie == "5":
        # APU (G/GE) y PRO: 95 °C; el resto es una serie mixta 90/95: el valor bajo
        # (con el alto, el aviso llegaría tarde)
        if pro or suf in ("G", "GE"):
            return 95.0, "familia"
        return 90.0, "familia"
    if serie == "2":
        return 95.0, "familia"
    return None, ""
