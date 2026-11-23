"""Lista de sonidos y selector con botón ▶ para escucharlos antes de elegir.

La misma lista y el mismo selector en el asistente, en Ajustes (alarma y avisos) y en el
editor de cada alarma: el asistente ofrece todo lo que luego hay en Ajustes.
"""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from ..i18n import _, N_

# propios (sensorpulse/data/sounds, siempre disponibles) y del tema freedesktop
# (paquete sound-theme-freedesktop)
SOUNDS = [
    ("sensorpulse-critical", N_("Two-tone alarm")),
    ("sensorpulse-nuclear", N_("Nuclear plant alarm")),
    ("sensorpulse-sonar", N_("Sonar ping")),
    ("sensorpulse-beeps", N_("Digital beeps")),
    ("sensorpulse-chime", N_("Soft chime")),
    ("sensorpulse-warning", N_("Soft beep")),
    ("alarm-clock-elapsed", N_("Alarm clock")),
    ("dialog-warning", N_("Warning")),
    ("complete", N_("Chime")),
    ("bell", N_("Bell")),
    ("phone-incoming-call", N_("Ring")),
    ("message-new-instant", N_("Message")),
    ("window-attention", N_("Attention")),
    ("suspend-error", N_("Error tone")),
    ("dialog-information", N_("Information")),
]


def play(store, name):
    """Reproduce un sonido (lo hace el servicio, igual que en una alarma real)."""
    if store.client is None or not name:
        return
    try:
        store.client.play_sound(name)
    except GLib.Error:
        pass
    except Exception:
        pass


def picker(store, current, on_change, default="sensorpulse-critical"):
    """Desplegable con todos los sonidos y, a su lado, ▶ para escuchar el elegido.
    Devuelve (caja, desplegable)."""
    box = Gtk.Box(spacing=6)
    combo = Gtk.ComboBoxText()
    for k, n in SOUNDS:
        combo.append(k, _(n))
    if not combo.set_active_id(current or default):
        if current:          # un sonido del tema que no está en la lista: se ofrece igual
            combo.append(current, current)
            combo.set_active_id(current)
        else:
            combo.set_active_id(default)
    combo.connect("changed", lambda w: on_change(w.get_active_id()))
    btn = Gtk.Button(label="▶")      # carácter: se ve igual con cualquier tema de iconos
    btn.set_tooltip_text(_("Listen"))
    btn.get_accessible().set_name(_("Listen"))
    btn.connect("clicked", lambda *a: play(store, combo.get_active_id()))
    box.pack_start(combo, False, False, 0)
    box.pack_start(btn, False, False, 0)
    return box, combo
