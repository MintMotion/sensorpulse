"""Temperatura de los discos: qué tipo es cada uno y cada cuánto se lee.

- NVMe y SSD SATA: leer la temperatura no afecta a su reposo ni a su vida útil, así que se
  leen a menudo (NVMe al ritmo normal, SSD SATA cada pocos segundos) y no hay nada que elegir.
- Discos duros: leer la temperatura puede reiniciar su temporizador de reposo. Un intervalo
  largo los deja descansar, pero puede ocultar un calentamiento: lo decide el usuario. Además,
  si un disco duro está templado se vigila cada 2 minutos hasta que se enfríe (opcional).

Lo usan Ajustes y el asistente.
"""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from ..i18n import _

HDD_PRESETS = (120, 300, 600, 900, 1800, 3600, 7200)


def media_text(media):
    return {"nvme": _("NVMe SSD"), "ssd": _("SATA SSD"), "hdd": _("Hard disk"),
            "unknown": _("Disk of unknown type (treated as a hard disk)")}.get(media, _("Disk"))


def every_text(secs):
    secs = int(round(secs or 0))
    if secs < 120:
        return _("every %d s") % secs
    if secs < 3600 or secs % 3600:
        return _("every %d min") % round(secs / 60.0)
    return _("every %d h") % (secs // 3600)


def disks(store):
    return [d for d in store.devices() if d.get("cls") == "disk"]


def has_hdd(store):
    return any(d.get("media") in ("hdd", "unknown") for d in disks(store))


def _label(text, dim=False, markup=False):
    l = Gtk.Label(xalign=0)
    if markup:
        l.set_markup(text)
    else:
        l.set_text(text)
    l.set_line_wrap(True)
    l.set_max_width_chars(70)
    if dim:
        l.get_style_context().add_class("dim-label")
    return l


def build(store, cfg, on_change, compact=False):
    """Caja con la lista de discos y, si hay discos duros, la elección del intervalo."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    found = disks(store)
    hdd_int = float(cfg["intervals"].get("hdd", 1800))
    for d in found:
        media = d.get("media") or ""
        name = d.get("model") or d.get("name") or ""
        if media in ("nvme", "ssd"):
            how = _("read %s: it does not affect its rest or its lifespan") % every_text(d.get("interval"))
        elif media in ("hdd", "unknown"):
            how = _("read %s (you decide below)") % every_text(hdd_int)
            if d.get("warm"):
                how += "  ·  " + _("warm: watched every 2 min until it cools down")
        else:
            how = every_text(d.get("interval"))
        box.pack_start(_label("•  <b>%s</b>  —  %s: %s" % (
            GLib.markup_escape_text(name), GLib.markup_escape_text(media_text(media)),
            GLib.markup_escape_text(how)), markup=True), False, False, 0)
    if not found:
        box.pack_start(_label(_("No disk with a temperature sensor was found."), dim=True),
                       False, False, 0)
    if not has_hdd(store) and found:
        return box
    if not found and compact:
        return box
    row = Gtk.Box(spacing=10)
    row.pack_start(Gtk.Label(label=_("Hard disk reading interval"), xalign=0), False, False, 0)
    combo = Gtk.ComboBoxText()
    names = {120: _("every 2 min (watch closely)"), 300: _("every 5 min"), 600: _("every 10 min"),
             900: _("every 15 min"), 1800: _("every 30 min (let them rest)"),
             3600: _("every hour"), 7200: _("every 2 hours")}
    for secs in HDD_PRESETS:
        combo.append(str(secs), names[secs])
    cur = int(round(hdd_int))
    if not combo.set_active_id(str(cur)):
        combo.append(str(cur), every_text(cur))
        combo.set_active_id(str(cur))

    def _changed(w):
        cfg["intervals"]["hdd"] = int(w.get_active_id())
        on_change()
    combo.connect("changed", _changed)
    row.pack_start(combo, False, False, 0)
    box.pack_start(row, False, False, 4)
    box.pack_start(_label(_("A long interval lets the disk rest (spin down), but a disk could "
                            "heat up between two readings without the alarm going off. A short "
                            "interval watches it closely, but on many disks each reading stops "
                            "them from going to sleep."), dim=True), False, False, 0)
    ad = Gtk.CheckButton(label=_("Watch a hard disk every 2 minutes while it is warm"))
    ad.set_active(bool(cfg.get("hdd_adaptive", True)))

    def _ad(w):
        cfg["hdd_adaptive"] = w.get_active()
        on_change()
    ad.connect("toggled", _ad)
    box.pack_start(ad, False, False, 0)
    box.pack_start(_label(_("A warm disk is working, so reading it more often does not take "
                            "away its rest. With the disk service (UDisks2), a disk that is "
                            "asleep is not woken up to read it."), dim=True), False, False, 0)
    return box
