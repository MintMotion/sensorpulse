"""Estado de energía de los discos SATA sin despertarlos, con UDisks2.

`Drive.Ata.PmGetState` envía la orden ATA CHECK POWER MODE, pensada para no arrancar
el disco. Su acción de polkit (org.freedesktop.udisks2.ata-check-power) es «yes» para
la sesión activa: no pide contraseña. Se llama SIN permitir autorización interactiva
(ni la marca de D-Bus ni la opción auth.no_user_interaction de UDisks),
así que con la sesión bloqueada o inactiva nunca aparece una ventana de contraseña:
simplemente falla y se lee por intervalo, como antes.
"""

import glob
import os
import time

UDISKS = "org.freedesktop.UDisks2"
# CHECK POWER MODE (ATA/ACS): 0x00 reposo, 0x01 reposo (Standby_y, ACS-3), 0x40/0x41 caché
# NV con el disco parado; 0x80-0x83 inactivo (Idle, girando); 0xFF activo. La 0.3.5 solo
# reconocía 0x00 y leía (despertando) los discos que informan 0x01/0x40/0x41.
STANDBY_STATES = {0x00, 0x01, 0x40, 0x41}
DRIVE_RETRY = 600.0      # s antes de volver a buscar el Drive de UDisks si no se encontró


class DiskPower:
    def __init__(self):
        from gi.repository import Gio
        self.Gio = Gio
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.disabled_until = 0.0     # reloj monótono
        self._drive_of = {}           # dev.id -> (ruta o None, momento monótono)

    def _block_name(self, dev):
        # el hwmon de drivetemp cuelga del dispositivo SCSI; su disco está en .../block/sdX
        base = dev.pm_path and os.path.dirname(os.path.dirname(dev.pm_path))
        if not base:
            return None
        names = glob.glob(os.path.join(base, "block", "*"))
        return os.path.basename(names[0]) if names else None

    def _drive_path(self, dev):
        hit = self._drive_of.get(dev.id)
        if hit is not None:
            path, t = hit
            # una ruta encontrada se guarda; «no encontrado» solo un rato: al iniciar sesión
            # UDisks2 puede no estar listo todavía (la 0.3.5 lo recordaba para siempre y ese
            # disco ya no se comprobaba nunca: se leía despertándolo)
            if path is not None or time.monotonic() - t < DRIVE_RETRY:
                return path
        from gi.repository import GLib
        path = None
        name = self._block_name(dev)
        if name:
            try:
                r = self.bus.call_sync(UDISKS, "/org/freedesktop/UDisks2/block_devices/" + name,
                                       "org.freedesktop.DBus.Properties", "Get",
                                       GLib.Variant("(ss)", ("org.freedesktop.UDisks2.Block", "Drive")),
                                       GLib.VariantType("(v)"), self.Gio.DBusCallFlags.NONE, 2000, None)
                path = r.unpack()[0]
                if path in ("/", ""):
                    path = None
            except GLib.Error:
                path = None
        self._drive_of[dev.id] = (path, time.monotonic())
        return path

    def standby(self, dev):
        if time.monotonic() < self.disabled_until:
            return False
        from gi.repository import GLib
        path = self._drive_path(dev)
        if not path:
            return False
        try:
            r = self.bus.call_sync(UDISKS, path, "org.freedesktop.UDisks2.Drive.Ata", "PmGetState",
                                   # UDisks mira también esta opción del método para decidir
                                   # si puede pedir autorización: nunca
                                   GLib.Variant("(a{sv})", ({"auth.no_user_interaction":
                                                             GLib.Variant("b", True)},)),
                                   GLib.VariantType("(y)"),
                                   self.Gio.DBusCallFlags.NONE, 3000, None)
            return r.unpack()[0] in STANDBY_STATES
        except GLib.Error as e:
            if "NotAuthorized" in str(e) or "ServiceUnknown" in str(e):
                # sin permiso (sesión inactiva) o sin UDisks2: no reintentar en una hora
                self.disabled_until = time.monotonic() + 3600
            elif "UnknownObject" in str(e) or "UnknownMethod" in str(e):
                # el disco cambió (otro sdX tras reconectarlo): volver a buscar su Drive
                self._drive_of.pop(dev.id, None)
            return False
