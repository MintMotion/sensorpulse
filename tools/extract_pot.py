#!/usr/bin/env python3
"""Extrae las cadenas traducibles del código (llamadas a _(), N_() y la tabla ngettext) con ast
y las compara con sensorpulse/po/sensorpulse.pot.
Uso: extract_pot.py          -> informa de diferencias
     extract_pot.py --write  -> reescribe el .pot con las cadenas del código"""

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POT = os.path.join(ROOT, "sensorpulse", "po", "sensorpulse.pot")
sys.path.insert(0, os.path.join(ROOT, "tools"))


def found():
    out = {}
    for base, _d, files in os.walk(os.path.join(ROOT, "sensorpulse")):
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(base, fn)
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
            for n in ast.walk(tree):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("_", "N_") \
                        and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                    out.setdefault(n.args[0].value, []).append("%s:%d" % (os.path.relpath(path, ROOT), n.lineno))
    return out


def pot_ids():
    from make_po import msgids
    return msgids()


def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def main():
    code = found()
    pot = set(pot_ids())
    new = sorted(set(code) - pot)
    old = sorted(pot - set(code))
    print("en el código y no en el .pot: %d" % len(new))
    for m in new:
        print("  +", repr(m), code[m][0])
    print("en el .pot y no usadas en el código: %d" % len(old))
    for m in old:
        print("  -", repr(m))
    if "--write" in sys.argv:
        lines = ['# SensorPulse translation template', 'msgid ""', 'msgstr ""',
                 '"Content-Type: text/plain; charset=UTF-8\\n"', ""]
        for m in sorted(code):
            lines += ["#: " + code[m][0], 'msgid "%s"' % esc(m), 'msgstr ""', ""]
        open(POT, "w", encoding="utf-8").write("\n".join(lines))
        print("escrito", POT)


if __name__ == "__main__":
    main()
