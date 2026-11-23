"""Pruebas de los cambios de la 0.3.7 (ver docs/SensorPulse-0.3.7-cambios.md).
Ejecutar: python3 tests/test_037.py   (sin pantalla ni servicio)."""

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))


def fresh_env(profile="desktop"):
    d = tempfile.mkdtemp(prefix="sp037-")
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "fake_sysfs.py"), d + "/sys", profile],
                   check=True, stdout=subprocess.DEVNULL)
    os.environ["SENSORPULSE_SYSROOT"] = d + "/sys"
    os.environ["XDG_CONFIG_HOME"] = d + "/cfg"
    os.environ["XDG_STATE_HOME"] = d + "/state"
    os.makedirs(d + "/cfg/sensorpulse", exist_ok=True)
    from sensorpulse.core import sysfs
    importlib.reload(sysfs)
    from sensorpulse.core import discovery, engine
    importlib.reload(discovery)
    importlib.reload(engine)
    return d


def engine_snapshot():
    from sensorpulse.core.engine import Engine
    e = Engine(2, 1800)
    e.poll(force=True)
    return e, e.snapshot()


# ------------------------------------------------------------------ 2.2 panel
def test_panel_bar_and_click_split():
    fresh_env()
    from sensorpulse import config as cfgmod, views
    importlib.reload(cfgmod)
    importlib.reload(views)
    _e, snap = engine_snapshot()
    cfg = cfgmod.load()
    ids = [c["id"] for d in snap["devices"] for c in d["channels"] if c["kind"] == "temp"][:7]
    cfg["sensors"]["panel"] = ids[:5]
    cfg["sensors"]["panel_menu"] = [ids[6]]
    cfg["panel_count"] = 3
    bar, extra = views.panel_split(cfg, snap)
    assert [c["id"] for c, _d in bar] == ids[:3]
    # los que no caben en la barra y los «solo al hacer clic», en ese orden
    assert [c["id"] for c, _d in extra] == ids[3:5] + [ids[6]]
    v = views.build(cfg, snap)
    assert v["panel_count"] == 3 and len(v["panel"]) == 3 and len(v["panel_extra"]) == 3
    # más de 3 en la barra (la 0.3.6 tenía un máximo de 3)
    cfg["panel_count"] = 5
    assert len(views.build(cfg, snap)["panel"]) == 5
    cfg["panel_count"] = 99
    assert views.panel_count(cfg) == 8


def test_panel_items_for_gauges_and_renames():
    fresh_env()
    from sensorpulse import config as cfgmod, views
    importlib.reload(views)
    _e, snap = engine_snapshot()
    cfg = cfgmod.load()
    cpu = next(d for d in snap["devices"] if d["cls"] == "cpu")
    prim = next(c for c in cpu["channels"] if c["primary"])
    cfg["sensors"]["renames"][prim["id"]] = "Placa base"
    cfg["panel_style"] = "gauge"
    v = views.build(cfg, snap)
    it = next(x for x in v["panel"] if x["id"] == prim["id"])
    assert it["label"] == "Placa base" and it["renamed"]
    assert 0.0 < it["frac"] < 1.0 and it["tiny"].endswith("°")
    assert v["panel_style"] == "gauge" and v["panel_labels"] is True
    # el menú del panel enseña el nombre propio en vez del del dispositivo
    assert any(s["device_name"] == "Placa base" for s in v["summary"])
    fan = views.item(cfg, {"id": "f", "kind": "fan", "label": "F", "value": 1450.0, "vmin": 0,
                           "vmax": 3000}, {"id": "d"})
    assert fan["tiny"] == "1.4k" and abs(fan["frac"] - 1450 / 3000.0) < 0.01
    # textos del menú del applet/extensión/desklet ya traducidos por el servicio
    for k in ("open", "settings", "mute", "waiting", "rename"):
        assert v["tr"][k]


def test_config_new_keys_sanitized():
    d = fresh_env()
    with open(d + "/cfg/sensorpulse/config.json", "w") as f:
        json.dump({"panel_count": 50, "panel_style": "rocket", "panel_labels": "no",
                   "language": "de", "intervals": {"hdd": 5, "ssd": 0},
                   "sensors": {"panel_menu": ["a", 3]}, "hdd_adaptive": 1}, f)
    from sensorpulse import config as cfgmod
    importlib.reload(cfgmod)
    cfg = cfgmod.load()
    assert cfg["panel_count"] == 8 and cfg["panel_style"] == "text" and cfg["panel_labels"] is True
    assert cfg["language"] == "de"
    assert cfg["intervals"]["hdd"] == 60 and cfg["intervals"]["ssd"] == 2
    assert cfg["sensors"]["panel_menu"] == ["a"] and cfg["hdd_adaptive"] is True
    for lang in ("it", "pt"):
        with open(d + "/cfg/sensorpulse/config.json", "w") as f:
            json.dump({"language": lang}, f)
        assert cfgmod.load()["language"] == lang


# ------------------------------------------------------------------ 2.2 sitio en el panel
class FakeSettings:
    def __init__(self, applets, panels=("1:0:bottom",)):
        self.v = {"enabled-applets": list(applets), "panels-enabled": list(panels)}

    def get_strv(self, k):
        return list(self.v[k])

    def set_strv(self, k, v):
        self.v[k] = list(v)


MINT = ["panel1:left:0:menu@cinnamon.org:0", "panel1:left:1:separator@cinnamon.org:1",
        "panel1:left:2:grouped-window-list@cinnamon.org:2", "panel1:right:0:systray@cinnamon.org:3",
        "panel1:right:1:xapp-status@cinnamon.org:4", "panel1:right:2:notifications@cinnamon.org:5",
        "panel1:right:3:printers@cinnamon.org:6", "panel1:right:4:removable-drives@cinnamon.org:7",
        "panel1:right:5:keyboard@cinnamon.org:8", "panel1:right:6:favorites@cinnamon.org:9",
        "panel1:right:7:network@cinnamon.org:10", "panel1:right:8:sound@cinnamon.org:11",
        "panel1:right:9:power@cinnamon.org:12", "panel1:right:10:calendar@cinnamon.org:13",
        "panel1:right:11:cornerbar@cinnamon.org:14"]


def _cinn(st):
    from sensorpulse.ui import envinfo
    importlib.reload(envinfo)
    envinfo._cinn_settings = lambda: st
    return envinfo


def _order(st, uuid):
    """Posición visual en la zona derecha (Cinnamon ordena por el número, de menor a mayor)."""
    right = [x.split(":") for x in st.v["enabled-applets"] if x.split(":")[1] == "right"]
    right.sort(key=lambda p: int(p[2]))
    return [p[3] for p in right].index(uuid)


def test_cinnamon_applet_goes_left_of_system_icons():
    st = FakeSettings(MINT)
    env = _cinn(st)
    assert env.cinnamon_set("applet", True)
    mine = [x for x in st.v["enabled-applets"] if env.CINN_APPLET in x][0]
    assert mine.split(":")[1] == "right"
    assert _order(st, env.CINN_APPLET) == 0, "debe quedar a la izquierda de la bandeja y el reloj"
    # sin tocar ningún otro applet
    assert [x for x in st.v["enabled-applets"] if env.CINN_APPLET not in x] == MINT


def test_cinnamon_applet_moved_once_from_old_place():
    # donde lo dejaba la 0.3.6: a la derecha del todo, tras el reloj
    st = FakeSettings(MINT + ["panel1:right:12:sensorpulse@mintmotion:15"])
    env = _cinn(st)
    assert env.cinnamon_place_applet()
    assert _order(st, env.CINN_APPLET) == 0
    # si el usuario lo coloca en otro sitio de la zona derecha, no se vuelve a mover
    st2 = FakeSettings(MINT + ["panel1:right:5:sensorpulse@mintmotion:15"])
    env = _cinn(st2)
    before = list(st2.v["enabled-applets"])
    env.cinnamon_place_applet()
    assert st2.v["enabled-applets"] == before


# ------------------------------------------------------------------ 2.6 discos
def test_disk_type_detection_and_intervals():
    fresh_env()
    e, snap = engine_snapshot()
    disks = {d["model"]: d for d in snap["devices"] if d["cls"] == "disk"}
    assert disks["WD_BLACK SN850X 1000GB"]["media"] == "nvme"
    assert disks["Samsung SSD 870 EVO 1TB"]["media"] == "ssd"
    assert disks["ST4000DM004-2CV1"]["media"] == "hdd"
    assert disks["Samsung SSD 870 EVO 1TB"]["interval"] == 10       # frecuente: no tiene reposo
    assert disks["ST4000DM004-2CV1"]["interval"] == 1800            # lo decide el usuario
    assert disks["WD_BLACK SN850X 1000GB"]["interval"] <= 5
    # un SSD SATA no usa los límites de disco duro (50/60 °C)
    ssd = next(c for c in disks["Samsung SSD 870 EVO 1TB"]["channels"] if c["kind"] == "temp")
    assert ssd["warn"] > 50
    # el usuario elige el intervalo del disco duro
    e.hdd = 300
    e.apply_intervals()
    assert next(d for d in e.devices if d.media == "hdd").interval == 300


def test_disk_unknown_type_is_treated_as_hdd():
    d = fresh_env()
    from sensorpulse.core import discovery
    for root, _dirs, files in os.walk(d + "/sys"):
        if "rotational" in files and "/sda/" in root + "/":
            os.unlink(os.path.join(root, "rotational"))
    importlib.reload(discovery)
    hdd = [x for x in discovery.discover() if x.model == "ST4000DM004-2CV1"][0]
    assert hdd.media == "unknown"
    from sensorpulse.core.engine import Engine
    e = Engine(2, 900)
    assert next(x for x in e.devices if x.model == "ST4000DM004-2CV1").interval == 900


def test_warm_hdd_is_watched_closely():
    d = fresh_env()
    from sensorpulse.core import engine as E
    e = E.Engine(2, 1800)
    e.poll(force=True, mono=1000.0)
    hdd = next(x for x in e.devices if x.media == "hdd")
    assert not hdd.warm and e._next[hdd.id] == 1000.0 + 1800
    # el disco se calienta: a 8 °C o menos del aviso se vuelve a leer a los 2 min
    path = next(c.path for c in hdd.channels if c.kind == "temp")
    real = os.path.join(d, "sys", path.lstrip("/")) if not os.path.exists(path) else path
    open(real, "w").write("45000\n")
    e.poll(force=True, mono=2000.0)
    assert hdd.warm and e._next[hdd.id] == 2000.0 + E.HDD_WARM_INTERVAL
    # opción desactivada: se respeta el intervalo elegido
    e.hdd_adaptive = False
    e.poll(force=True, mono=3000.0)
    assert not hdd.warm and e._next[hdd.id] == 3000.0 + 1800


# ------------------------------------------------------------------ 2.7 dibujado
def _surface(w=200, h=60):
    import cairo
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    return s, cairo.Context(s)


def test_sparkline_downsampled_keeps_peaks():
    from sensorpulse.ui import draw as D
    pts = [(i, 50.0) for i in range(3000)]
    pts[1234] = (1234, 99.0)
    pts[2222] = (2222, 1.0)
    out = D.downsample(pts, lambda t: t / 3000.0 * 100, 100)
    assert len(out) <= 100
    assert (1234, 99.0) in out and (2222, 1.0) in out


class CountingCtx:
    """Envuelve un contexto cairo y cuenta stroke() y fill()."""

    def __init__(self, cr):
        self._cr, self.strokes, self.fills = cr, 0, 0

    def stroke(self):
        self.strokes += 1
        self._cr.stroke()

    def fill(self):
        self.fills += 1
        self._cr.fill()

    def __getattr__(self, k):
        return getattr(self._cr, k)


def test_sparkline_single_stroke():
    from sensorpulse.ui import draw as D
    pts = [(i, 40 + 30 * ((i % 50) / 50.0)) for i in range(300)]
    item = {"kind": "temp", "warn": 70.0, "crit": 90.0, "value": 55.0}
    _s, cr = _surface()
    c = CountingCtx(cr)
    D.draw_sparkline(c, 0, 0, 200, 60, pts, item, {}, (0.1, 0.8, 0.9), fill=True, show_warn=False)
    assert c.strokes == 1 and c.fills == 1, (c.strokes, c.fills)   # 0.3.6: 299 stroke()


def test_heat_gradient_matches_value_colors():
    import cairo
    from sensorpulse.ui import draw as D
    from sensorpulse.ui.theme import value_color
    lo, hi, warn, crit = 20.0, 100.0, 70.0, 90.0
    s, cr = _surface(4, 81)
    cr.set_source(D.heat_gradient(0, 80, lo, hi, warn, crit, (0, 0, 1)))
    cr.paint()
    s.flush()
    data = s.get_data()
    stride = s.get_stride()
    for v in (25.0, 60.0, 72.0, 80.0, 88.0, 95.0):
        y = int(round(80 - (v - lo) / (hi - lo) * 80))
        b, g, r = data[y * stride], data[y * stride + 1], data[y * stride + 2]
        want = value_color(v, warn, crit, (0, 0, 1))
        assert all(abs(a / 255.0 - w) < 0.05 for a, w in zip((r, g, b), want)), (v, (r, g, b), want)
    # degradados en caché: no se crean en cada llamada
    assert D.heat_gradient(0, 80, lo, hi, warn, crit, (0, 0, 1)) is \
        D.heat_gradient(0, 80, lo, hi, warn, crit, (0, 0, 1))
    assert isinstance(D.heat_gradient(0, 80, lo, hi, None, None, (0, 0, 1)), cairo.LinearGradient)


# ------------------------------------------------------------------ 2.1 sonidos
def test_sounds_exist_and_are_valid():
    import wave
    from sensorpulse.ui.sounds import SOUNDS
    from sensorpulse import config as cfgmod
    names = [k for k, _n in SOUNDS]
    assert "sensorpulse-nuclear" in names and len(names) >= 12 and len(set(names)) == len(names)
    for k in names:
        assert cfgmod._SOUND_RE.fullmatch(k), k
        if k.startswith("sensorpulse-"):
            p = os.path.join(ROOT, "sensorpulse", "data", "sounds", k + ".wav")
            with wave.open(p) as w:
                assert w.getnframes() / float(w.getframerate()) > 0.2, k
    from sensorpulse.service import Notifier
    assert Notifier._sound_file(None, "sensorpulse-nuclear").endswith("sensorpulse-nuclear.wav")


def test_service_has_playsound():
    from sensorpulse import service
    assert '<method name="PlaySound">' in service.INTROSPECTION if hasattr(service, "INTROSPECTION") \
        else "PlaySound" in open(os.path.join(ROOT, "sensorpulse", "service.py")).read()
    assert service.PREVIEW_EVERY < service.TEST_ALARM_EVERY


def test_sounds_generator_is_reproducible():
    d = tempfile.mkdtemp(prefix="sp037snd-")
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "make_sounds.py"), d], check=True,
                   stdout=subprocess.DEVNULL)
    for f in os.listdir(d):
        a = open(os.path.join(d, f), "rb").read()
        b = open(os.path.join(ROOT, "sensorpulse", "data", "sounds", f), "rb").read()
        assert a == b, f
    shutil.rmtree(d)


# ------------------------------------------------------------------ 2.4 nombres
def test_rename_set_name():
    fresh_env()
    from sensorpulse.ui import rename

    class St:
        cfg = {"sensors": {"renames": {}}}
        listeners = []
        saved = 0

        def find(self, cid):
            return ({"id": cid, "label": "diode 0"}, {"name": "Placa"})

        def save_cfg(self):
            St.saved += 1
    st = St()
    rename.set_name(st, "x", "  Placa   base ")
    assert st.cfg["sensors"]["renames"]["x"] == "Placa base"
    rename.set_name(st, "x", "diode 0")             # igual que el original: se quita
    assert "x" not in st.cfg["sensors"]["renames"]
    rename.set_name(st, "x", "a" * 99)
    assert len(st.cfg["sensors"]["renames"]["x"]) == rename.MAX_LEN
    rename.set_name(st, "x", "")
    assert "x" not in st.cfg["sensors"]["renames"] and St.saved == 4


# ------------------------------------------------------------------ 2.5 idiomas
def test_eight_languages_complete():
    from sensorpulse import i18n
    from make_po import msgids
    ids = msgids()
    assert set(i18n.LANGS) == {"es", "en", "fr", "it", "de", "pt", "ja", "zh"}
    for lang in ("es", "fr", "it", "de", "pt", "ja", "zh"):
        cat = i18n.parse_po(os.path.join(ROOT, "sensorpulse", "po", lang + ".po"))
        missing = [m for m in ids if m not in cat]
        assert not missing, (lang, missing[:5])
        i18n.set_language(lang)
        assert i18n._("Settings") != "Settings", lang
    i18n.set_language("en")
    os.environ["LANGUAGE"] = "pt_BR"
    assert i18n.system_language() == "pt"
    del os.environ["LANGUAGE"]


def test_pot_matches_code():
    out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "extract_pot.py")],
                         capture_output=True, text=True).stdout
    assert "en el código y no en el .pot: 0" in out and "no usadas en el código: 0" in out, out


def test_desktop_and_policy_translated():
    desk = open(os.path.join(ROOT, "data", "sensorpulse.desktop"), encoding="utf-8").read()
    pol = open(os.path.join(ROOT, "data", "io.github.mintmotion.SensorPulse.policy"), encoding="utf-8").read()
    for lang in ("es", "fr", "it", "de", "pt", "ja", "zh"):
        assert "Comment[%s]=" % lang in desk, lang
        assert pol.count('xml:lang="%s"' % lang) == 4, lang


# ------------------------------------------------------------------ 3 y empaquetado
def test_maintainer_without_email_and_versions():
    sh = open(os.path.join(ROOT, "packaging", "build-deb.sh"), encoding="utf-8").read()
    m = re.search(r'^MAINTAINER=\$\{MAINTAINER:-"([^"]*)"\}', sh, re.M)
    assert m and m.group(1) == "SensorPulse"
    from sensorpulse import VERSION
    for p in ("data/cinnamon/applet/sensorpulse@mintmotion/metadata.json",
              "data/cinnamon/desklet/sensorpulse-gauges@mintmotion/metadata.json"):
        assert json.load(open(os.path.join(ROOT, p)))["version"] == VERSION
    g = json.load(open(os.path.join(ROOT, "data/gnome-shell/sensorpulse@mintmotion.github.io/metadata.json")))
    assert g["version-name"] == VERSION


def test_xlets_run_with_node():
    """El applet, el desklet y la extensión se ejecutan con objetos simulados (tests/js)."""
    node = shutil.which("node")
    if not node:
        print("   (sin node: se omite)")
        return
    fresh_env()
    from sensorpulse import config as cfgmod, views
    importlib.reload(views)
    _e, snap = engine_snapshot()
    cfg = cfgmod.load()
    cfg["panel_count"] = 2
    snap["views"] = views.build(cfg, snap)
    p = tempfile.mktemp(suffix=".json")
    json.dump(snap, open(p, "w"))
    r = subprocess.run([node, os.path.join(ROOT, "tests", "js", "run_xlets.js"), p],
                       capture_output=True, text=True)
    os.unlink(p)
    assert r.returncode == 0 and "FALLO" not in r.stdout, r.stdout + r.stderr


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("OK", name)
            except Exception as e:     # noqa: BLE001
                fails += 1
                import traceback
                traceback.print_exc()
                print("FALLO", name, type(e).__name__, e)
    print("fallos: %d" % fails)
    sys.exit(1 if fails else 0)
