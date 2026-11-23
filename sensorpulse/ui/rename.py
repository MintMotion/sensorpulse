"""Diálogo para poner un nombre propio a un sensor («diode 0» → «Placa base»,
«Ventilador 2» → «Ventilador de extracción»).

Lo usan el applet de Cinnamon, la extensión de GNOME y el desklet (con «sensorpulse --rename
ID»), el widget flotante (menú del botón derecho) y Ajustes. El nombre se guarda en
sensors.renames y el servicio lo publica a todas las vistas.
"""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from .. import APP_NAME
from .. import config as cfgmod
from ..i18n import _

MAX_LEN = 40


def set_name(store, cid, text):
    """Guarda (o quita, con texto vacío) el nombre propio de un canal."""
    text = " ".join((text or "").split())[:MAX_LEN]
    ren = store.cfg["sensors"]["renames"]
    c, _d = store.find(cid)
    if not text or (c is not None and text == c.get("label")):
        ren.pop(cid, None)
    else:
        ren[cid] = text
    store.save_cfg()
    for cb in list(store.listeners):
        cb()


class RenameDialog(Gtk.Dialog):
    def __init__(self, app, store, cid, parent=None):
        super().__init__(title=_("Rename sensor"), transient_for=parent, modal=parent is not None)
        self.set_icon_name("sensorpulse")
        self.set_default_size(380, -1)
        self.store, self.cid = store, cid
        self.set_wmclass("sensorpulse", APP_NAME)
        c, d = store.find(cid)
        orig = c["label"] if c else cid
        dev = (d.get("name") if d else "") or ""
        self.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Save"), Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)
        area = self.get_content_area()
        area.set_spacing(8)
        area.set_border_width(14)
        head = Gtk.Label(xalign=0)
        head.set_markup("<b>%s</b>" % GLib.markup_escape_text(
            ("%s · %s" % (dev, orig)) if dev and dev != orig else orig))
        area.pack_start(head, False, False, 0)
        hint = Gtk.Label(label=_("Give it a name of your own, for example «Motherboard» or «Exhaust "
                                 "fan». Leave it empty to use the original name."), xalign=0)
        hint.set_line_wrap(True)
        hint.set_max_width_chars(48)
        hint.get_style_context().add_class("dim-label")
        area.pack_start(hint, False, False, 0)
        self.entry = Gtk.Entry()
        self.entry.set_max_length(MAX_LEN)
        self.entry.set_text(cfgmod.display_name(store.cfg, c) if c else
                            store.cfg["sensors"]["renames"].get(cid, ""))
        self.entry.set_placeholder_text(orig)
        self.entry.set_activates_default(True)
        area.pack_start(self.entry, False, False, 0)
        self.connect("response", self._response)
        self.show_all()
        self.entry.grab_focus()

    def _response(self, _dlg, resp):
        if resp == Gtk.ResponseType.OK:
            set_name(self.store, self.cid, self.entry.get_text())
        self.destroy()
