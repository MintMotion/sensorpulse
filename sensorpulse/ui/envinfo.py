"""Detección del entorno: escritorio, sesión, compositor y capacidades de la bandeja."""

import glob
import os

import gi
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk

from ..core import sysfs
from ..i18n import _


def desktop():
    d = (os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "").lower()
    for key, name in (("cinnamon", "cinnamon"), ("x-cinnamon", "cinnamon"),
                      # Budgie publica «Budgie:GNOME»: debe ir antes que «gnome»
                      ("budgie", "budgie"), ("pantheon", "other"), ("deepin", "other"),
                      ("cosmic", "other"), ("gnome", "gnome"),
                      ("unity", "gnome"), ("kde", "kde"), ("plasma", "kde"), ("xfce", "xfce"),
                      ("mate", "mate"), ("lxqt", "lxqt"),
                      ("sway", "wlroots"), ("hyprland", "wlroots")):
        if key in d:
            return name
    return "other"


def is_wayland():
    return bool(os.environ.get("WAYLAND_DISPLAY")) or \
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def composited():
    s = Gdk.Screen.get_default()
    return bool(s and s.is_composited() and s.get_rgba_visual() is not None)


def gnome_has_appindicator():
    """¿Hay extensión AppIndicator instalada? (Ubuntu la trae activada de serie)."""
    paths = glob.glob("/usr/share/gnome-shell/extensions/*appindicator*") + \
        glob.glob(os.path.expanduser("~/.local/share/gnome-shell/extensions/*appindicator*"))
    return bool(paths)


GNOME_UUID = "sensorpulse@mintmotion.github.io"


def gnome_ext_installed():
    return any(os.path.isdir(os.path.join(b, GNOME_UUID)) for b in (
        "/usr/share/gnome-shell/extensions",
        os.path.expanduser("~/.local/share/gnome-shell/extensions")))


def gnome_shell_version():
    """Versión mayor de GNOME Shell en marcha (p. ej. 46) o None si no se sabe."""
    v = None
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        r = bus.call_sync("org.gnome.Shell", "/org/gnome/Shell", "org.freedesktop.DBus.Properties",
                          "Get", GLib.Variant("(ss)", ("org.gnome.Shell", "ShellVersion")),
                          GLib.VariantType("(v)"), Gio.DBusCallFlags.NO_AUTO_START, 1500, None)
        v = r.unpack()[0]
    except Exception:
        v = None
    if not v:
        import shutil
        import subprocess
        if shutil.which("gnome-shell"):
            try:
                v = subprocess.run(["gnome-shell", "--version"], capture_output=True, text=True,
                                   timeout=5).stdout
            except (OSError, subprocess.SubprocessError):
                v = None
    import re
    m = re.search(r"(\d+)(?:\.\d+)?", v or "")
    return int(m.group(1)) if m else None


def gnome_ext_supported(version=None):
    """¿La extensión instalada declara esta versión de GNOME? (shell-version de metadata.json).
    Versión desconocida: se supone que sí (no bloquear por no poder preguntar)."""
    import json
    version = gnome_shell_version() if version is None else version
    if version is None:
        return True
    for b in ("/usr/share/gnome-shell/extensions",
              os.path.expanduser("~/.local/share/gnome-shell/extensions")):
        try:
            with open(os.path.join(b, GNOME_UUID, "metadata.json"), encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        return str(version) in [str(x).split(".")[0] for x in meta.get("shell-version", [])]
    return False


def gnome_ext_usable():
    """Instalada y compatible con este GNOME."""
    return gnome_ext_installed() and gnome_ext_supported()


def gnome_ext_set(enabled):
    """Activa o desactiva nuestra extensión de GNOME Shell (clave estándar de GNOME). No la
    activa en un GNOME que no figura en su metadata.json (la 0.3.5 la añadía igual y GNOME la
    marcaba como incompatible, sin panel y sin icono de bandeja de respaldo)."""
    if enabled and not gnome_ext_supported():
        return False
    try:
        from gi.repository import Gio
        src = Gio.SettingsSchemaSource.get_default()
        if src is None or src.lookup("org.gnome.shell", True) is None:
            return False
        st = Gio.Settings.new("org.gnome.shell")
        on = [x for x in st.get_strv("enabled-extensions") if x != GNOME_UUID]
        off = [x for x in st.get_strv("disabled-extensions") if x != GNOME_UUID]
        if enabled:
            on.append(GNOME_UUID)
        st.set_strv("enabled-extensions", on)
        st.set_strv("disabled-extensions", off)
        Gio.Settings.sync()
        return True
    except Exception:
        return False


def gnome_ext_active():
    """Pregunta a GNOME Shell si nuestra extensión está cargada y activa (estado 1).
    Una extensión instalada con el paquete no se carga hasta volver a iniciar sesión."""
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        # la interfaz org.gnome.Shell.Extensions la ofrece el propio Shell (org.gnome.Shell)
        r = bus.call_sync("org.gnome.Shell", "/org/gnome/Shell",
                          "org.gnome.Shell.Extensions", "GetExtensionInfo",
                          GLib.Variant("(s)", (GNOME_UUID,)), GLib.VariantType("(a{sv})"),
                          Gio.DBusCallFlags.NO_AUTO_START, 1500, None)
        info = r.unpack()[0]
        st = info.get("state")
        if st is None:
            return False
        try:
            src = Gio.SettingsSchemaSource.get_default()
            if src is not None and src.lookup("org.gnome.shell", True) is not None and \
                    Gio.Settings.new("org.gnome.shell").get_boolean("disable-user-extensions"):
                return False
        except Exception:
            pass
        return int(st) == 1
    except Exception:
        return False


def panel_by_extension():
    """En GNOME, si nuestra extensión está ACTIVA, el panel lo dibuja ella. Si está instalada
    pero aún no cargada (antes de volver a iniciar sesión), se usa el icono de bandeja."""
    return desktop() == "gnome" and gnome_ext_installed() and gnome_ext_active()


# ------------------------------------------------------------------ Cinnamon
CINN_APPLET = "sensorpulse@mintmotion"
CINN_DESKLET = "sensorpulse-gauges@mintmotion"


def cinnamon_has(kind):
    uuid = CINN_APPLET if kind == "applet" else CINN_DESKLET
    return any(os.path.isdir(os.path.join(b, uuid)) for b in (
        "/usr/share/cinnamon/%ss" % kind, os.path.expanduser("~/.local/share/cinnamon/%ss" % kind)))


def _cinn_settings():
    from gi.repository import Gio
    src = Gio.SettingsSchemaSource.get_default()
    if src is None or src.lookup("org.cinnamon", True) is None:
        return None
    return Gio.Settings.new("org.cinnamon")


def cinnamon_reload_xlets():
    """Tras actualizar el paquete, Cinnamon sigue ejecutando el applet y el desklet antiguos
    hasta que se recargan: se le pide que los recargue (método ReloadXlet de org.Cinnamon)."""
    if desktop() != "cinnamon":
        return False
    try:
        from gi.repository import Gio, GLib
        st = _cinn_settings()
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        done = False
        for key, uuid, xtype in (("enabled-applets", CINN_APPLET, "APPLET"),
                                 ("enabled-desklets", CINN_DESKLET, "DESKLET")):
            if st is None or not any(uuid in x for x in st.get_strv(key)):
                continue
            bus.call_sync("org.Cinnamon", "/org/Cinnamon", "org.Cinnamon", "ReloadXlet",
                          GLib.Variant("(ss)", (uuid, xtype)), None, Gio.DBusCallFlags.NONE,
                          3000, None)
            done = True
        return done
    except Exception:
        return False


def _cinnamon_panel(st):
    """Primer panel que existe de verdad (panels-enabled: «id:monitor:posición»). Con varios
    monitores o sin «panel1», un panel1 fijo dejaba el applet mal colocado o invisible."""
    try:
        ids = [int(x.split(":")[0]) for x in st.get_strv("panels-enabled")]
        if ids:
            return ids[0]
    except (ValueError, IndexError):
        pass
    return 1


def _right_zone(cur, panel, skip_uuid=None):
    """Posiciones ocupadas en la zona derecha de ese panel (sin contar skip_uuid)."""
    used = []
    for x in cur:
        p = x.split(":")
        if len(p) >= 4 and p[0] == "panel%d" % panel and p[1] == "right" and p[3] != skip_uuid:
            try:
                used.append(int(p[2]))
            except ValueError:
                pass
    return used


def _cinnamon_free_slot(st, cur):
    """Panel y posición para nuestro applet: la PRIMERA de la zona derecha, es decir, a la
    izquierda de la bandeja, la red, el sonido y el reloj, en el espacio libre del panel.
    La 0.3.6 usaba la última posición: el applet salía a la derecha del todo, tras el reloj,
    en el sitio de los iconos del sistema. Cinnamon ordena por número y admite negativos, así
    que no hay que mover ningún otro applet."""
    panel = _cinnamon_panel(st)
    used = _right_zone(cur, panel)
    return panel, (min(used) - 1 if used else 0)


def cinnamon_place_applet():
    """Coloca nuestro applet a la izquierda de los iconos del sistema si está en la zona
    derecha DETRÁS de todos ellos (donde lo dejaba la 0.3.6). Se hace una sola vez: si
    después el usuario lo mueve con el modo de edición del panel, se respeta."""
    try:
        st = _cinn_settings()
        if st is None:
            return False
        cur = list(st.get_strv("enabled-applets"))
        out, moved = [], False
        for x in cur:
            p = x.split(":")
            if len(p) >= 5 and p[3] == CINN_APPLET and p[1] == "right":
                try:
                    panel, pos = int(p[0].replace("panel", "")), int(p[2])
                except ValueError:
                    out.append(x)
                    continue
                others = _right_zone(cur, panel, skip_uuid=CINN_APPLET)
                if others and pos > max(others):
                    p[2] = str(min(others) - 1)
                    x = ":".join(p)
                    moved = True
            out.append(x)
        if moved:
            st.set_strv("enabled-applets", out)
            from gi.repository import Gio
            Gio.Settings.sync()
        return True
    except Exception:
        return False


def cinnamon_set(kind, enabled):
    """Añade o quita nuestro applet del panel (o el desklet del escritorio) de Cinnamon."""
    try:
        st = _cinn_settings()
        if st is None:
            return False
        key = "enabled-applets" if kind == "applet" else "enabled-desklets"
        uuid = CINN_APPLET if kind == "applet" else CINN_DESKLET
        cur = list(st.get_strv(key))
        present = [x for x in cur if (":%s:" % uuid) in (x + ":") or x.startswith(uuid + ":")]
        if enabled and not present:
            ids = []
            for x in cur:
                try:
                    ids.append(int(x.split(":")[-1] if kind == "applet" else x.split(":")[1]))
                except (ValueError, IndexError):
                    pass
            nid = max(ids + [0]) + 1
            if kind == "applet":
                panel, pos = _cinnamon_free_slot(st, cur)
                cur.append("panel%d:right:%d:%s:%d" % (panel, pos, uuid, nid))
            else:
                cur.append("%s:%d:80:120" % (uuid, nid))
            st.set_strv(key, cur)
        elif not enabled and present:
            st.set_strv(key, [x for x in cur if x not in present])
        from gi.repository import Gio
        Gio.Settings.sync()
        return True
    except Exception:
        return False


def panel_by_cinnamon_applet():
    return desktop() == "cinnamon" and cinnamon_has("applet")


def floating_by_desklet():
    """En Cinnamon Wayland, el desklet sustituye al widget GTK (que no puede quedarse detrás)."""
    return desktop() == "cinnamon" and is_wayland() and cinnamon_has("desklet")


def sata_disks_without_temp():
    """Discos SATA/SAS presentes pero sin el módulo drivetemp cargado."""
    have_drivetemp = any((sysfs.read_text(os.path.join(h, "name")) or "") == "drivetemp"
                         for h in glob.glob(sysfs.p("sys/class/hwmon/hwmon*")))
    if have_drivetemp:
        return []
    out = []
    for b in sorted(glob.glob(sysfs.p("sys/block/sd*"))):
        if sysfs.read_text(os.path.join(b, "removable"), "0") == "1":
            continue
        # solo discos ATA/SATA (los que sabe leer drivetemp; lo mismo que mira el ayudante):
        # un disco USB o una tarjeta en un lector USB también son «sd*» y no removibles
        vendor = sysfs.read_text(os.path.join(b, "device", "vendor"), "") or ""
        if not vendor.strip().upper().startswith("ATA"):
            continue
        model = sysfs.read_text(os.path.join(b, "device", "model"), "") or os.path.basename(b)
        out.append(model.strip())
    return out


def notes_for_display():
    """Avisos por modo de visualización según el entorno (para el asistente)."""
    de, wl = desktop(), is_wayland()
    notes = {"panel": "", "floating": "", "window": ""}
    if de == "gnome" and gnome_ext_usable():
        notes["panel"] = _("The values will appear in the top bar. The first time, you may need "
                           "to log out and back in.")
    elif de == "gnome" and not gnome_has_appindicator():
        notes["panel"] = _("GNOME needs the “AppIndicator” extension to show icons in the top bar "
                           "(Ubuntu already includes it).")
    if de == "gnome" and wl:
        notes["floating"] = _("On GNOME (Wayland) the floating widget is shown as a normal window: "
                              "GNOME does not let apps place widgets on the desktop.")
    elif not composited():
        notes["floating"] = _("Your desktop has transparency turned off; the widget will have a "
                              "solid background. You can enable the compositor in your desktop settings.")
    elif de == "cinnamon" and wl:
        notes["floating"] = _("On Cinnamon (Wayland) the widget may not stay behind your windows; "
                              "use “Always on top” or the X11 session if needed.")
    return notes
