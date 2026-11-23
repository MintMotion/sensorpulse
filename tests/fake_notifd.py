"""Demonio de notificaciones falso para las pruebas del servicio: apunta cada Notify en un fichero.
Uso: fake_notifd.py FICHERO_LOG [MODO]   (imprime «ready» cuando ya tiene el nombre en el bus)
MODO: «hang» = nunca contesta a Notify (demonio colgado); «invoke=ACCIÓN» = pulsa esa acción
en cuanto llega una notificación que la ofrece (señal ActionInvoked).
Cada línea del registro: resumen<TAB>replaces_id<TAB>acciones separadas por comas."""

import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

LOG = sys.argv[1]
MODE = sys.argv[2] if len(sys.argv) > 2 else ""
HELD = []
XML = """<node><interface name='org.freedesktop.Notifications'>
  <method name='Notify'><arg type='s' direction='in'/><arg type='u' direction='in'/><arg type='s' direction='in'/>
  <arg type='s' direction='in'/><arg type='s' direction='in'/><arg type='as' direction='in'/>
  <arg type='a{sv}' direction='in'/><arg type='i' direction='in'/><arg type='u' direction='out'/></method>
  <method name='GetCapabilities'><arg type='as' direction='out'/></method>
  <method name='CloseNotification'><arg type='u' direction='in'/></method>
  <method name='GetServerInformation'><arg type='s' direction='out'/><arg type='s' direction='out'/>
  <arg type='s' direction='out'/><arg type='s' direction='out'/></method>
  <signal name='ActionInvoked'><arg type='u'/><arg type='s'/></signal>
  <signal name='NotificationClosed'><arg type='u'/><arg type='u'/></signal></interface></node>"""
COUNT = [0]


def handler(conn, sender, path, iface, method, params, inv):
    if method == "Notify":
        COUNT[0] += 1
        p = params.unpack()
        acts = p[5]
        with open(LOG, "a") as f:
            f.write("%s\t%d\t%s\n" % (p[3], p[1], ",".join(acts[0::2])))
        if MODE == "hang":
            HELD.append(inv)        # sin respuesta: como un demonio atascado
            return
        nid = p[1] or COUNT[0]
        inv.return_value(GLib.Variant("(u)", (nid,)))
        if MODE.startswith("invoke=") and MODE[7:] in acts[0::2]:
            key = MODE[7:]
            GLib.timeout_add(200, lambda: (conn.emit_signal(
                None, "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
                "ActionInvoked", GLib.Variant("(us)", (nid, key))), False)[1])
    elif method == "GetCapabilities":
        inv.return_value(GLib.Variant("(as)", (["actions", "body"],)))
    elif method == "GetServerInformation":
        inv.return_value(GLib.Variant("(ssss)", ("fake", "x", "1", "1.2")))
    else:
        inv.return_value(None)


def acquired(conn, name):
    conn.register_object("/org/freedesktop/Notifications", Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0],
                         handler, None, None)
    print("ready", flush=True)


Gio.bus_own_name(Gio.BusType.SESSION, "org.freedesktop.Notifications", Gio.BusNameOwnerFlags.NONE,
                 acquired, None, None)
GLib.MainLoop().run()
