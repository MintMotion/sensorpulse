"""Pruebas de interfaz de la 0.3.7. Ejecutar dentro de dbus-run-session + xvfb-run con el
servicio en marcha, SENSORPULSE_SYSROOT, XDG_CONFIG_HOME y XDG_STATE_HOME temporales."""

import json
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")   # el programa usa GTK 3; sin esto, si hay GTK 4 instalado se carga ese
from gi.repository import GLib, Gtk  # noqa: E402

errors, results = [], []


def check(name, cond, info=""):
    results.append(("OK " if cond else "FALLO ") + name + ("" if cond else "  " + str(info)))
    if not cond:
        errors.append(name)


def cfg_on_disk():
    with open(os.path.join(os.environ["XDG_CONFIG_HOME"], "sensorpulse", "config.json")) as f:
        return json.load(f)


from sensorpulse.app import App  # noqa: E402

app = App()
steps = []


def step(fn):
    steps.append(fn)


def pump(secs):
    end = time.monotonic() + secs
    ctx = GLib.MainContext.default()
    while time.monotonic() < end:
        ctx.iteration(False)
        time.sleep(0.01)


# ---------------------------------------------------------------- 2.2 «En una ventana»
def t_window_switch():
    app.open_settings(page="general")
    app.settings._display_changed("window", True)
    pump(0.5)
    check("2.2 «En una ventana» activado abre la ventana", app.window is not None and app.window.get_visible())
    app.settings._display_changed("window", False)
    pump(0.5)
    check("2.2 «En una ventana» desactivado la cierra sola", app.window is None)
    check("2.2 se guarda", cfg_on_disk()["display"]["window"] is False)
    # con la ventana activada, cambiar OTRO ajuste no la abre ni la pone delante
    app.store.cfg["display"]["window"] = True
    app.store.save_cfg()
    app.settings.save()
    pump(0.3)
    check("2.2 otro ajuste no saca la ventana", app.window is None)
    app.store.cfg["display"]["window"] = False
    app.store.save_cfg()


# ---------------------------------------------------------------- 2.2 barra / clic
def t_settings_on_click_column():
    s = app.settings
    s.show_page("sensors")
    rows = [ch for dev in s.sstore for ch in dev.iterchildren()]
    target = next(r for r in rows if not r[2])            # uno que no está en el panel
    path = target.path.to_string()
    s._toggled(None, path, 9)                             # «Al hacer clic»
    cfg = cfg_on_disk()
    check("2.2 «Al hacer clic» guardado", target[4] in cfg["sensors"]["panel_menu"], cfg["sensors"])
    s._toggled(None, path, 2)                             # ahora «Panel»: excluye «Al hacer clic»
    cfg = cfg_on_disk()
    check("2.2 «Panel» y «Al hacer clic» se excluyen",
          target[4] in cfg["sensors"]["panel"] and target[4] not in cfg["sensors"]["panel_menu"])


# ---------------------------------------------------------------- 2.4 nombres
def t_rename_dialog():
    cid = app.store.devices()[0]["channels"][0]["id"]
    app.rename_sensor(cid)
    pump(0.3)
    dlg = [w for w in app.get_windows() if w.__class__.__name__ == "RenameDialog"]
    check("2.4 diálogo de nombre abierto (sensorpulse --rename)", len(dlg) == 1)
    if dlg:
        dlg[0].entry.set_text("Placa base")
        dlg[0].response(Gtk.ResponseType.OK)
        pump(2.5)
        check("2.4 nombre guardado", cfg_on_disk()["sensors"]["renames"].get(cid) == "Placa base")
        snap = app.store.client.snapshot()
        labels = [it["label"] for it in snap["views"]["panel"] + snap["views"]["floating"]]
        check("2.4 el servicio lo publica a las vistas", "Placa base" in labels, labels)


def t_wizard_sensors_page():
    app.show_wizard(page=3)
    w = app.wizard
    check("2.3 asistente: fila por sensor con panel y widget", len(w._rows) > 5)
    cid, pan, wid = w._rows[2]
    pan.set_active_id("menu")
    check("2.3 «Al hacer clic» desde el asistente", cid in app.store.cfg["sensors"]["panel_menu"])
    wid.set_active(not wid.get_active())
    check("2.3 widget desde el asistente", (cid in app.store.cfg["sensors"]["floating"]) == wid.get_active())
    # nombre propio desde el asistente
    grid_entries = []

    def walk(x):
        if isinstance(x, Gtk.Entry) and not isinstance(x, Gtk.SpinButton):
            grid_entries.append(x)
        if isinstance(x, Gtk.Container):
            for c in x.get_children():
                walk(c)
    walk(w.pages[3])
    first = w._rows[0][0]
    orig = app.store.find(first)[0]["label"]
    next(e for e in grid_entries if e.get_placeholder_text() == orig).set_text("Mi CPU")
    check("2.4 nombre desde el asistente", app.store.cfg["sensors"]["renames"].get(first) == "Mi CPU")
    w.idx = 1
    w.update_nav()
    check("2.3 opciones del panel en el asistente", getattr(w, "_panel_pv", None) is not None)
    w.idx = 4
    w.update_nav()
    pickers = []

    def walk2(x):
        if isinstance(x, Gtk.ComboBoxText):
            m = x.get_model()
            ids = [m.get_value(r.iter, 1) for r in m]
            if "sensorpulse-nuclear" in ids:
                pickers.append(x)
        if isinstance(x, Gtk.Container):
            for c in x.get_children():
                walk2(c)
    walk2(w.pages[4])
    check("2.1 asistente: selector de sonido de alarma y de avisos", len(pickers) == 2, len(pickers))
    w.destroy()
    app.wizard = None


# ---------------------------------------------------------------- 2.7 flotante
def t_floating_cache_and_cpu():
    from collections import deque
    import math
    st = app.store
    st.cfg["style"] = "glass"
    st.cfg["sensors"]["floating"] = []
    now = time.time()
    for d in st.snapshot.get("devices", []):
        for c in d["channels"]:
            if c.get("value") is not None:
                q = deque(maxlen=300)
                for i in range(300):
                    q.append((now - (300 - i) * 2, c["value"] * (1 + 0.08 * math.sin(i / 9.0))))
                st.local_hist[c["id"]] = q
    app.set_floating(True, save=False)
    fw = app.floating
    fw.refresh()
    pump(1.0)
    check("2.7 hélices en el flotante (estilo Cristal)", bool(st.fan_draw_ids))
    full, part = [0], [0]
    real_qd, real_qda = fw.queue_draw, fw.queue_draw_area
    fw.queue_draw = lambda: (full.__setitem__(0, full[0] + 1), real_qd())
    fw.queue_draw_area = lambda *a: (part.__setitem__(0, part[0] + 1), real_qda(*a))
    fw.on_draw(fw, __import__("cairo").Context(__import__("cairo").ImageSurface(0, 10, 10)))
    for _i in range(20):
        fw.on_frame()
    check("2.7 un fotograma solo de hélices no redibuja todo", full[0] == 0 and part[0] > 0, (full, part))
    fw.queue_draw, fw.queue_draw_area = real_qd, real_qda
    # coste de dibujo con el historial lleno: objetivo < 1 % de un núcleo
    spent = [0.0]
    orig = fw.on_draw

    def timed(w, cr):
        t0 = time.process_time()
        r = orig(w, cr)
        spent[0] += time.process_time() - t0
        return r
    fw.disconnect_by_func(orig)
    fw.connect("draw", timed)
    t0 = time.monotonic()
    pump(12)
    pct = 100 * spent[0] / (time.monotonic() - t0)
    check("2.7 dibujo del flotante < 1 %% de un núcleo (%.2f %%)" % pct, pct < 1.0, pct)
    # clic derecho: cambiar el nombre del indicador que está bajo el puntero
    n = len(fw.items())
    w, h = fw.get_size()
    x, y, cw, ch = fw.renderer.cell(0, n, "glass", fw.scale(), True, w, h)
    check("2.4 flotante: se sabe qué sensor está bajo el puntero",
          fw.renderer.hit(x + cw / 2, y + ch / 2, n, "glass", fw.scale(), w, h) == 0)


for fn in (t_window_switch, t_settings_on_click_column, t_rename_dialog, t_wizard_sensors_page,
           t_floating_cache_and_cpu):
    step(fn)


def run_steps():
    if not steps:
        app.quit_all()
        return False
    fn = steps.pop(0)
    try:
        fn()
    except Exception:
        errors.append(fn.__name__)
        results.append("FALLO %s\n%s" % (fn.__name__, traceback.format_exc()))
    return True


cfgdir = os.path.join(os.environ["XDG_CONFIG_HOME"], "sensorpulse")
os.makedirs(cfgdir, exist_ok=True)
with open(os.path.join(cfgdir, "config.json"), "w") as f:
    f.write('{"first_run_done": true, "display": {"panel": true, "window": false, "floating": false}}')
GLib.timeout_add(3000, lambda: GLib.timeout_add(300, run_steps) and False)
app.run(["sensorpulse", "--background"])
print("\n".join(results))
if errors:
    print("FALLOS: %s" % errors)
    sys.exit(1)
print("GUI 0.3.7 OK")
