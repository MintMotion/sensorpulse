"""Demonio de notificaciones que acepta el nombre pero nunca contesta a Notify."""
from gi.repository import Gio, GLib
XML = """<node><interface name="org.freedesktop.Notifications">
<method name="Notify"><arg type="s" direction="in"/><arg type="u" direction="in"/><arg type="s" direction="in"/>
<arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="as" direction="in"/><arg type="a{sv}" direction="in"/>
<arg type="i" direction="in"/><arg type="u" direction="out"/></method>
<method name="GetCapabilities"><arg type="as" direction="out"/></method>
<method name="CloseNotification"><arg type="u" direction="in"/></method>
<method name="GetServerInformation"><arg type="s" direction="out"/><arg type="s" direction="out"/><arg type="s" direction="out"/><arg type="s" direction="out"/></method>
<signal name="NotificationClosed"><arg type="u"/><arg type="u"/></signal>
<signal name="ActionInvoked"><arg type="u"/><arg type="s"/></signal>
</interface></node>"""
held = []
def call(conn, sender, path, iface, method, params, inv):
    if method == "GetCapabilities":
        inv.return_value(GLib.Variant("(as)", (["actions", "body"],)))
    elif method == "GetServerInformation":
        inv.return_value(GLib.Variant("(ssss)", ("hung", "x", "1", "1.2")))
    else:
        held.append(inv)   # nunca responde
        print("retenido", method, flush=True)
def on_bus(conn, name):
    info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
    conn.register_object("/org/freedesktop/Notifications", info, call, None, None)
Gio.bus_own_name(Gio.BusType.SESSION, "org.freedesktop.Notifications", 0, on_bus, None, None)
GLib.MainLoop().run()
