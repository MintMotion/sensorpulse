"""«Detectar sensores»: activa con una sola contraseña los sensores de placa base y de
discos SATA que el sistema no carga solo (ver bin/sensorpulse-detect)."""

import json
import os
import shutil

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gio, GLib

from ..i18n import _, N_

HELPER = "/usr/libexec/sensorpulse/sensorpulse-detect"
NAMES = {
    "nct6775": N_("motherboard"), "nct6683": N_("motherboard"), "it87": N_("motherboard"),
    "f71882fg": N_("motherboard"), "w83627ehf": N_("motherboard"), "w83627hf": N_("motherboard"),
    "sch5627": N_("motherboard"), "sch5636": N_("motherboard"),
    "asus_ec_sensors": N_("motherboard"), "asus_wmi_sensors": N_("motherboard"),
    "drivetemp": N_("hard disks"),
}


def helper_path():
    """Solo el ayudante instalado por el paquete (propiedad de root). Nunca un script de una
    carpeta del usuario: pkexec lo ejecutaría como root y cualquiera con acceso a esa carpeta
    podría cambiarlo."""
    try:
        st = os.stat(HELPER)
    except OSError:
        return None
    if st.st_uid != 0 or st.st_mode & 0o022:
        return None
    return HELPER


def available():
    return helper_path() is not None and shutil.which("pkexec") is not None


def needs_detection(snapshot, sata_without_temp=False):
    """¿Faltan sensores de placa base o de discos?"""
    classes = {d.get("cls") for d in snapshot.get("devices", [])}
    drivers = {d.get("driver") for d in snapshot.get("devices", [])}
    no_board = not ({"board", "fans"} & classes) and "battery" not in classes
    return no_board or (sata_without_temp and "drivetemp" not in drivers)


def run(parent, store, done=None, settings_cb=None, laptop_ok=False):
    """Lanza el asistente (pide la contraseña una vez) y muestra el resultado."""
    before = {c["id"] for d in (store.snapshot.get("devices", []) if store else [])
              for c in d["channels"]}
    path = helper_path()
    if path is None or shutil.which("pkexec") is None:
        _message(parent, _("This option is not available on this system."))
        return
    try:
        proc = Gio.Subprocess.new(["pkexec", path] + (["--laptop-ok"] if laptop_ok else []),
                                  Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
    except GLib.Error as e:
        _message(parent, str(e))
        return
    if parent is not None:
        parent.set_sensitive(False)

    def _fin(p, res):
        if parent is not None:
            parent.set_sensitive(True)
        try:
            _ok, out, _err = p.communicate_utf8_finish(res)
        except GLib.Error:
            out = ""
        status = p.get_exit_status() if p.get_if_exited() else -1
        if status in (126, 127):   # contraseña cancelada o denegada
            return
        try:
            data = json.loads((out or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            data = {"error": "?"}
        new = []
        if store is not None and store.client is not None:
            try:
                store.client.rescan()
                snap = store.client.snapshot()
                store._on_update(snap)
                new = [(c, d) for d in snap.get("devices", []) for c in d["channels"]
                       if c["id"] not in before and c["kind"] != "status"]
            except Exception:
                pass
        if data.get("skipped") == "laptop" and not data.get("activated"):
            # el sondeo de chips Super I/O puede colgar algunos portátiles: solo si el usuario quiere
            if _ask(parent, _("This computer is a laptop. Laptops rarely have motherboard "
                              "sensors, and testing for them can freeze some models. "
                              "Try anyway?")):
                GLib.idle_add(lambda: (run(parent, store, done, settings_cb, laptop_ok=True), False)[1])
                return
        elif data.get("skipped") == "virtual" and not data.get("activated"):
            _message(parent, _("This computer does not expose physical sensors "
                               "(this is normal in virtual machines)."))
            if done:
                done(data)
            return
        if data.get("error"):
            _message(parent, _("The sensors could not be activated."))
        elif data.get("activated"):
            kinds = sorted({_(NAMES.get(m, m)) for m in data["activated"]})
            text = _("New sensors activated: %s.") % ", ".join(kinds) + "\n" + \
                _("They will stay active after restarting.")
            if new:
                _choose_new(parent, store, new, text)
            else:
                _message(parent, text)
        elif data.get("acpi_conflict"):
            # it87 tiene una opción para saltarse el conflicto, pero su documentación avisa
            # de que puede provocar reinicios inesperados: no se ofrece
            _message(parent, _("Your motherboard reserves its sensor chip for its own use, "
                               "so it cannot be activated safely."))
        elif any(r == "no_driver" for r in (data.get("results") or {}).values()) and \
                not any(r == "no_chip" for r in (data.get("results") or {}).values()):
            _message(parent, _("The system does not include drivers for these sensors "
                               "(this is normal in virtual machines and some cloud kernels)."))
        else:
            _message(parent, _("No new sensors were found. Everything your computer offers "
                               "is already being shown."))
        if done:
            done(data)

    proc.communicate_utf8_async(None, None, _fin)


def _choose_new(parent, store, new, text):
    """«He encontrado N sensores nuevos»: casillas para enseñarlos (F14/U9)."""
    dlg = Gtk.Dialog(title=_("New sensors"), transient_for=parent, modal=True)
    dlg.add_buttons(_("Close"), Gtk.ResponseType.CANCEL, _("Show selected"), Gtk.ResponseType.OK)
    area = dlg.get_content_area()
    area.set_spacing(6)
    area.set_border_width(14)
    lbl = Gtk.Label(label=text + "\n\n" + _("I have found %d new sensors. Which ones do you want "
                                              "to see?") % len(new), xalign=0)
    lbl.set_line_wrap(True)
    area.pack_start(lbl, False, False, 0)
    checks = []
    for c, d in new[:30]:
        val = c.get("value")
        unit = c.get("unit", "")
        txt = "%s · %s%s" % (d.get("name", ""), c.get("label", ""),
                             ("  (%g %s)" % (round(val, 1), unit)) if val is not None else "")
        chk = Gtk.CheckButton(label=txt)
        chk.set_active(c["kind"] in ("temp", "fan") and not c.get("advanced"))
        area.pack_start(chk, False, False, 0)
        checks.append((chk, c["id"]))
    dlg.show_all()
    if dlg.run() == Gtk.ResponseType.OK:
        for chk, cid in checks:
            if chk.get_active():
                store.cfg["sensors"]["visible"][cid] = True
        store.save_cfg()
    dlg.destroy()


def _message(parent, text):
    dlg = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.INFO,
                            buttons=Gtk.ButtonsType.OK, text=text)
    dlg.run()
    dlg.destroy()


def _ask(parent, text):
    dlg = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.WARNING,
                            buttons=Gtk.ButtonsType.NONE, text=text)
    dlg.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Try anyway"), Gtk.ResponseType.OK)
    ok = dlg.run() == Gtk.ResponseType.OK
    dlg.destroy()
    return ok
