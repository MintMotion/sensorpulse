#!/usr/bin/env python3
"""Genera sensorpulse/po/{es,fr,it,de,pt,ja,zh}.po a partir de la plantilla .pot y de los
diccionarios tools/tr_*.py. Comprueba que no falte ninguna cadena y que los
marcadores de formato (%s, %(name)s...) coincidan."""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from tr_es import ES  # noqa: E402
from tr_fr import FR  # noqa: E402
from tr_it import IT  # noqa: E402
from tr_de import DE  # noqa: E402
from tr_pt import PT  # noqa: E402
from tr_ja import JA  # noqa: E402
from tr_zh import ZH  # noqa: E402

sys.path.insert(0, ROOT)
from sensorpulse.i18n import parse_po  # noqa: E402

POT = os.path.join(ROOT, "sensorpulse", "po", "sensorpulse.pot")
FMT = re.compile(r"%(\(\w+\))?[-#0 +]*\d*(\.\d+)?[sdif%]")


def msgids():
    ids, cur, field = [], None, None
    for line in open(POT, encoding="utf-8").read().splitlines() + [""]:
        if line.startswith("msgid "):
            cur, field = [line[6:].strip()[1:-1]], "id"
        elif line.startswith('"') and field == "id":
            cur.append(line.strip()[1:-1])
        elif line.startswith("msgstr"):
            s = "".join(cur)
            if s:
                ids.append(s.replace('\\"', '"').replace("\\\\", "\\"))
            field = None
    return ids


def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def main():
    ids = msgids()
    ok = True
    for code, table, _name in (("es", ES, "Español"), ("fr", FR, "Français"),
                              ("it", IT, "Italiano"), ("de", DE, "Deutsch"),
                              ("pt", PT, "Português"), ("ja", JA, "日本語"), ("zh", ZH, "中文")):
        missing = [m for m in ids if m not in table]
        extra = [k for k in table if k not in ids]
        bad = [m for m in ids if m in table and
               sorted(x.group(0) for x in FMT.finditer(m)) != sorted(x.group(0) for x in FMT.finditer(table[m]))]
        if missing or bad:
            ok = False
            print(code, "FALTAN:", missing, "FORMATO:", bad)
        if extra:
            print(code, "sobran (se ignoran):", extra)
        out = ['msgid ""', 'msgstr ""', '"Project-Id-Version: sensorpulse\\n"',
               '"Language: %s\\n"' % code, '"MIME-Version: 1.0\\n"',
               '"Content-Type: text/plain; charset=UTF-8\\n"',
               '"Content-Transfer-Encoding: 8bit\\n"', ""]
        for m in ids:
            if m in table:
                out += ['msgid "%s"' % esc(m), 'msgstr "%s"' % esc(table[m]), ""]
        path = os.path.join(ROOT, "sensorpulse", "po", code + ".po")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        # verificación de ida y vuelta con el mismo lector que usa el programa
        cat = parse_po(path)
        assert all(cat.get(m) == table[m] for m in ids if m in table), code
        print(code, len(cat), "cadenas OK")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
