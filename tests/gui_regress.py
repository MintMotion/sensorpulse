"""Pruebas de regresión de la interfaz (fallos de la revisión de la 0.2.0).
Ejecutar dentro de dbus-run-session + xvfb-run con SENSORPULSE_SYSROOT, XDG_CONFIG_HOME y
XDG_STATE_HOME apuntando a carpetas temporales."""

import json
import os
import shutil
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gi.repository import GLib  # noqa: E402

errors = []
results = []


def check(name, cond, info=""):
    results.append(("OK " if cond else "FALLO ") + name + ("" if cond else "  " + str(info)))
    if not cond:
        errors.append(name)


def cfg_on_disk():
    with open(os.path.join(os.environ["XDG_CONFIG_HOME"], "sensorpulse", "config.json")) as f:
        return json.load(f)


from sensorpulse.app import App  # noqa: E402
from sensorpulse.ui import settings as S  # noqa: E402

app = App()
steps = []


def step(fn):
    steps.append(fn)


def run_steps():
    if not steps:
        app.quit_all()
        return False
    fn = steps.pop(0)
    try:
        fn()
    except Exception:
        errors.append(traceback.format_exc())
    return True


# 1) F1: varios cambios seguidos en Ajustes y una alarma nueva se guardan todos
def t_settings():
    app.open_settings(page="general")
    st = app.settings
    for k, v in (("unit", "F"), ("theme", "light"), ("colorblind", True)):
        st.cfg[k] = v
        st.save()                      # cada save llama a apply_display -> reload_cfg
    st.cfg["alarms"]["notify_hot"] = True
    st.save()
    from sensorpulse.core.alarms import new_rule
    st.cfg["rules"] = st.cfg.get("rules", []) + [new_rule("cpu@proc/load", 90)]
    st.save()
    disk = cfg_on_disk()
    check("F1 ajustes persisten", disk["unit"] == "F" and disk["theme"] == "light" and
          disk["colorblind"] and disk["alarms"]["notify_hot"] and len(disk["rules"]) == 1, disk)
    check("F1 mismo diccionario", app.settings.cfg is app.store.cfg)


# 4) F5: reglas en °F se guardan en °C
def t_fahrenheit():
    c = {"kind": "temp", "tjmax": 95.0}
    cfg = {"unit": "F"}
    check("F5 185 °F -> 85 °C", abs(S._to_celsius(cfg, c, 185.0) - 85.0) < 0.01)
    check("F5 se muestra en °F", abs(S._to_user(cfg, c, 85.0) - 185.0) < 0.01)
    check("U8 ventilador empieza en «baja de 300»", S._rule_default({"kind": "fan"}) == ("below", 300))


# 5) F6: varias agujas se mueven a la vez
def t_needles():
    from sensorpulse.ui.store import AnimatedValue
    st = app.store
    st.animate = True
    st.anims = {k: AnimatedValue(40.0) for k in ("a", "b", "c")}
    for a in st.anims.values():
        a.set(80.0)
    st._last_anim = time.monotonic() - 0.1
    st._anim_tick()
    vals = [a.current for a in st.anims.values()]
    check("F6 todas las agujas avanzan", all(v > 40.5 for v in vals), vals)


# 2) F2: desinstalar con el paquete ya borrado
def t_wipe():
    tmp = tempfile.mkdtemp()
    shutil.copytree(os.path.join(ROOT, "sensorpulse"), os.path.join(tmp, "sensorpulse"))
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from sensorpulse.ui import settings as S\n"
            "import shutil; shutil.rmtree(%r)\n"   # «purge» borra el programa
            "S.wipe_user_data(); print('WIPE-OK')\n") % (tmp, os.path.join(tmp, "sensorpulse"))
    import subprocess
    env = dict(os.environ)
    cfgdir = os.path.join(env["XDG_CONFIG_HOME"], "sensorpulse")
    state = os.path.join(env["XDG_STATE_HOME"], "sensorpulse")
    os.makedirs(state, exist_ok=True)
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    check("F2 borrado sin el programa", "WIPE-OK" in out.stdout and not os.path.exists(cfgdir)
          and not os.path.exists(state), out.stderr[-400:])
    os.makedirs(cfgdir, exist_ok=True)
    with open(os.path.join(cfgdir, "config.json"), "w") as f:
        json.dump(app.store.cfg, f)


# 8) F7: el flotante HUD con ventiladores girando gasta menos del 1 % de CPU
def t_cpu():
    app.store.cfg["style"] = "hud"
    app.store.cfg["animate_fans"] = True
    app.set_floating(True)
    app.floating.refresh()

    def ticks():
        with open("/proc/self/stat") as f:
            p = f.read().split(")")[1].split()
        return int(p[11]) + int(p[12])
    # dejar pasar el barrido inicial y medir 10 s con el bucle de GTK en reposo
    loop = GLib.MainLoop()
    GLib.timeout_add(3000, lambda: (loop.quit(), False)[1])
    loop.run()
    t0, c0 = time.monotonic(), ticks()
    loop = GLib.MainLoop()
    GLib.timeout_add(10000, lambda: (loop.quit(), False)[1])
    loop.run()
    used = (ticks() - c0) / os.sysconf("SC_CLK_TCK") / (time.monotonic() - t0) * 100
    check("F7 CPU < 1 %% (%.2f %%)" % used, used < 1.0, used)
    check("F7 sin hélices que dibujar", not app.store.fan_draw_ids)


# 5) Revisión 0.3.1 (fallos 4-5): el diálogo de reglas guarda el nivel elegido
def t_rule_level():
    from gi.repository import Gtk
    app.open_settings(page="alarms")
    st = app.settings
    before = len(st.cfg.get("rules", []))
    cid = app.store.devices()[0]["channels"][0]["id"]

    def answer():
        for w in Gtk.Window.list_toplevels():
            if isinstance(w, Gtk.Dialog) and w.get_visible() and w.get_title() == S._("Alarm"):
                combos = []

                def walk(x):
                    if isinstance(x, Gtk.ComboBoxText):
                        combos.append(x)
                    if isinstance(x, Gtk.Container):
                        for c in x.get_children():
                            walk(c)
                walk(w)
                lv = [c for c in combos if c.get_active_id() in ("hot", "critical")][0]
                check("nivel nuevo por defecto: crítico", lv.get_active_id() == "critical")
                lv.set_active_id("hot")
                w.response(Gtk.ResponseType.OK)
                return False
        return True
    GLib.timeout_add(500, answer)
    st.edit_rule(None, cid)
    rules = st.cfg.get("rules", [])
    check("regla guardada con nivel «aviso»", len(rules) == before + 1 and rules[-1].get("level") == "hot",
          rules[-1:])
    check("nivel en disco", cfg_on_disk()["rules"][-1].get("level") == "hot")


def _walk(x, out, kind):
    from gi.repository import Gtk
    if isinstance(x, kind):
        out.append(x)
    if isinstance(x, Gtk.Container):
        for c in x.get_children():
            _walk(c, out, kind)
    return out


def _find_channel(kind):
    for d in app.store.devices():
        for c in d["channels"]:
            if c["kind"] == kind:
                return c
    return None


# 0.3.6 X1: registro de alarmas con unidad (°F) y cada «fired» con SU «cleared»
def t_036_alarm_log():
    app.open_settings(page="alarms")
    st = app.settings
    st.cfg["unit"] = "F"
    c = _find_channel("temp")
    log = [{"type": "fired", "rule": "r", "channel": c["id"], "label": "CPU", "value": 90.0,
            "level": "critical", "time": 1000.0},
           {"type": "cleared", "rule": "r", "channel": c["id"], "label": "CPU", "duration": 60,
            "time": 1060.0},
           {"type": "fired", "rule": "r", "channel": c["id"], "label": "CPU", "value": 95.0,
            "level": "critical", "time": 2000.0},
           {"type": "cleared", "rule": "r", "channel": c["id"], "label": "CPU", "duration": 300,
            "time": 2300.0}]
    real = app.store.client.alarm_log
    app.store.client.alarm_log = lambda: log
    try:
        st.refresh_log()
    finally:
        app.store.client.alarm_log = real
    lines = st.log_label.get_text().splitlines()
    check("X1 registro en °F con unidad", "194°F" in lines[0] and "203°F" in lines[0] or
          ("203°F" in lines[0] and "194°F" in lines[1]), lines)
    check("X1 duración de cada alarma", "5 min" in lines[0] and "1 min" in lines[1], lines)
    st.cfg["unit"] = "C"
    st.save()


# 0.3.6 X2/X3: reglas de potencia con decimales; regla de un sensor que no está se conserva
def t_036_rule_editor():
    from gi.repository import Gtk
    app.open_settings(page="alarms")
    st = app.settings
    seen = {}

    def answer(resp):
        for w in Gtk.Window.list_toplevels():
            if isinstance(w, Gtk.Dialog) and w.get_visible() and w.get_title() == S._("Alarm"):
                spins = _walk(w, [], Gtk.SpinButton)
                combos = _walk(w, [], Gtk.ComboBoxText)
                seen["digits"] = spins[0].get_digits()
                seen["delay_max"] = spins[1].get_range()[1]
                seen["sensor"] = combos[0].get_active_id()
                w.response(resp)
                return False
        return True
    p = _find_channel("power")
    GLib.timeout_add(500, answer, Gtk.ResponseType.CANCEL)
    st.edit_rule(None, p["id"])
    check("X2 regla de vatios con decimales", seen.get("digits") == 1, seen)
    check("X2 retardo hasta 3600 s", seen.get("delay_max") == 3600, seen)
    rule = {"id": "ghost1", "channel": "nvme@id-desconectado/temp1", "op": "above", "value": 70.0,
            "delay": 10, "notify": True, "sound": False, "visual": True, "repeat": False,
            "enabled": True, "level": "hot", "sound_name": "sensorpulse-critical"}
    st.cfg["rules"] = [rule]
    GLib.timeout_add(500, answer, Gtk.ResponseType.OK)
    st.edit_rule(rule)
    rules = st.cfg.get("rules", [])
    check("X3 editar regla de sensor ausente la conserva",
          seen.get("sensor") == rule["channel"] and any(r["id"] == "ghost1" for r in rules), (seen, rules))
    st.cfg["rules"] = []
    st.save()


# 0.3.6 X4: «Volver a lo recomendado» del asistente no pierde lo ya elegido
def t_036_wizard_recommended():
    app.show_wizard()
    w = app.wizard
    w.cfg["display"]["floating"] = True       # elegido en la página 2, aún sin guardar
    w.cfg["style"] = "bars"
    w._recommended()
    w2 = app.wizard
    check("X4 asistente conserva lo elegido", w2.cfg["display"]["floating"] is True and
          w2.cfg["style"] == "bars" and w2.cfg["sensors"]["visible"] == {},
          (w2.cfg["display"], w2.cfg["style"]))
    w2.cfg["display"]["floating"] = False
    w2.finish()


for fn in (t_settings, t_fahrenheit, t_needles, t_wipe, t_rule_level, t_036_alarm_log,
           t_036_rule_editor, t_036_wizard_recommended, t_cpu):
    step(fn)

cfgdir = os.path.join(os.environ["XDG_CONFIG_HOME"], "sensorpulse")
os.makedirs(cfgdir, exist_ok=True)
with open(os.path.join(cfgdir, "config.json"), "w") as f:
    f.write('{"first_run_done": true, "display": {"panel": true, "window": false, "floating": false}}')

# 8 s de calma para que se apaguen las animaciones de arranque antes de medir
GLib.timeout_add(9000, lambda: GLib.timeout_add(300, run_steps) and False)
app.run(["sensorpulse", "--background"])
print("\n".join(results))
if errors:
    print("ERRORES:\n" + "\n".join(errors))
    sys.exit(1)
print("REGRESIÓN OK")
