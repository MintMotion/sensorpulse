"""Prueba de humo de la interfaz: abre todas las vistas, páginas y estilos y falla si hay
excepciones. Ejecutar dentro de dbus-run-session + xvfb-run con SENSORPULSE_SYSROOT."""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gi.repository import GLib  # noqa: E402

errors = []


def hook(t, v, tb):
    errors.append("".join(traceback.format_exception(t, v, tb)))


sys.excepthook = hook

from sensorpulse.app import App  # noqa: E402
from sensorpulse.ui.floating import STYLES  # noqa: E402

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


step(lambda: app.apply_display())
step(lambda: app.show_window())
step(lambda: app.window.show_detail(app.store.devices()[1]["id"]))
for secs in (3600, 86400, 7 * 86400, 30 * 86400):
    step(lambda secs=secs: app.window.detail._on_range(type("B", (), {"get_active": lambda s: True})(), secs))
step(lambda: app.window.show_detail_for_channel(app.store.devices()[0]["channels"][0]["id"]))
step(lambda: app.window.cards and list(app.window.cards.values())[0].update_accessible())
step(lambda: app.window.show_overview())
for st in STYLES:
    step(lambda st=st: (app.store.cfg.__setitem__("style", st), app.set_floating(True),
                        app.floating.resize_to_content(), app.floating.queue_draw()))
for page in ("general", "sensors", "alarms", "about"):
    step(lambda p=page: app.open_settings(page=p))
step(lambda: app.settings.refresh_rules())
step(lambda: app.settings.refresh_log())
step(lambda: app.open_settings(page="about") or app.settings._copy_report())
step(lambda: __import__("sensorpulse.report", fromlist=["build"]).build(app.store.snapshot))
for i in range(5):
    step(lambda i=i: app.show_wizard(page=i))
step(lambda: app.wizard.next())
step(lambda: app.store.cfg.__setitem__("unit", "F"))
step(lambda: app.window and app.window.on_update())
step(lambda: app.wizard and app.wizard._limit_sentence())
step(lambda: app.store.cfg.__setitem__("theme", "light"))
step(lambda: app.window and (app.window.apply_theme(), app.window.on_update()))

os.environ.setdefault("SENSORPULSE_WIZARD_PAGE", "0")
cfgdir = os.path.join(os.environ["XDG_CONFIG_HOME"], "sensorpulse")
os.makedirs(cfgdir, exist_ok=True)
with open(os.path.join(cfgdir, "config.json"), "w") as f:
    f.write('{"first_run_done": true, "display": {"panel": true, "window": true, "floating": true}}')

GLib.timeout_add(3000, lambda: GLib.timeout_add(400, run_steps) and False)
app.run(["sensorpulse", "--background"])
if errors:
    print("ERRORES:\n" + "\n".join(errors))
    sys.exit(1)
print("GUI OK")

