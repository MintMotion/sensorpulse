"""Aplicación GTK: une servicio, bandeja, flotante, ventana, asistente y ajustes.

sensorpulse               abre la ventana (o el asistente la primera vez)
sensorpulse --background  arranque con el equipo: solo panel/flotante
sensorpulse --settings    abre los ajustes
sensorpulse --wizard      repite el asistente
sensorpulse --detail ID   abre la ventana en el detalle del sensor ID
sensorpulse status|report órdenes de terminal (ver cli.py)
"""

import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gio, GLib

from . import APP_ID, APP_NAME
from .i18n import _
from .ui.store import Store


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID + ".App",
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.store = None
        self.window = None
        self.floating = None
        self.tray = None
        self.settings = None
        self.wizard = None
        self._held = False
        for name, opt_help in (("background", "Start in the background (panel/floating only)"),
                               ("settings", "Open settings"), ("wizard", "Run the welcome assistant"),
                               ("window", "Open the main window")):
            self.add_main_option(name, 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, opt_help, None)
        self.add_main_option("detail", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Open the detail of a sensor", "ID")
        self.add_main_option("rename", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Rename a sensor (from the panel or the desktop widget)", "ID")

    # ------------------------------------------------------------ arranque
    def do_startup(self):
        Gtk.Application.do_startup(self)
        GLib.set_application_name(APP_NAME)
        for name, cb in (("window", lambda *a: self.show_window()),
                         ("settings", lambda *a: self.open_settings()),
                         ("toggle-floating", lambda *a: self.set_floating(not self.floating_on())),
                         ("wizard", lambda *a: self.show_wizard()),
                         ("mute-hour", lambda *a: self.mute_hour()),
                         ("quit", lambda *a: self.quit_all())):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)
        self.store = Store()
        self._config_notice()
        self._watch_updates()
        if not self.store.connect():
            # no modal: la interfaz sigue arrancando y se conecta sola si el servicio aparece
            # más tarde (inicio de sesión lento)
            dlg = Gtk.MessageDialog(message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.CLOSE,
                                    text=_("The sensor service could not be started."))
            dlg.connect("response", lambda d, _r: d.destroy())
            dlg.show()

    def mute_hour(self):
        """Silenciar 1 hora (menú de la bandeja, GTK o Gio). Sin servicio no hace nada."""
        try:
            if self.store.client is not None:
                self.store.client.mute(60)
        except GLib.Error as e:
            print("sensorpulse: no se pudo silenciar: %s" % e.message, file=sys.stderr)

    def _config_notice(self):
        """Si config.json estaba dañado, Store() lo apartó: se avisa en vez de perderlo en silencio."""
        from . import config as cfgmod
        p = cfgmod.LAST_PROBLEM
        if p.get("unreadable"):
            n = Gio.Notification.new(_("Settings could not be read"))
            n.set_body(_("%s cannot be read (check its permissions). The last good copy or the "
                         "defaults are being used, and the file will not be overwritten.")
                       % p["unreadable"])
            self.send_notification("config-unreadable", n)
        if p.get("corrupt_file"):
            n = Gio.Notification.new(_("Settings could not be read"))
            n.set_body(_("The settings file was damaged. It was saved as %s and the "
                         "last good copy or the defaults are being used.") % p["corrupt_file"])
            self.send_notification("config-problem", n)
            p["corrupt_file"] = None

    # ------------------------------------------------------------ actualizaciones
    def _watch_updates(self):
        """Al actualizar el paquete, este proceso sigue con el código y las traducciones
        antiguos (y Cinnamon con el applet antiguo): se vigila la versión instalada y,
        si cambia, se recargan el applet/desklet y el programa se reinicia solo."""
        import os
        from . import VERSION
        cfg = self.store.cfg
        if cfg.get("seen_version") != VERSION:
            from .ui import envinfo
            envinfo.cinnamon_reload_xlets()
            if cfg.get("seen_version") and "ext_applied" not in cfg:
                # actualización desde una versión que la reactivaba en cada arranque: se respeta
                # lo que el usuario tenga ahora
                cfg["ext_applied"] = bool(cfg["display"].get("panel"))
            cfg["seen_version"] = VERSION
            try:
                from . import config as cfgmod
                disk = cfgmod.load()
                disk["seen_version"] = VERSION
                if "ext_applied" in cfg:
                    disk["ext_applied"] = cfg["ext_applied"]
                cfgmod.save(disk)
            except Exception:
                pass
        path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "__init__.py")
        self._upd_timer = 0
        try:
            self._upd_mon = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.NONE, None)
            self._upd_mon.connect("changed", lambda *a: self._update_changed(path))
        except Exception:
            self._upd_mon = None

    def _update_changed(self, path):
        # dpkg sustituye los ficheros uno a uno: esperar a que termine
        if self._upd_timer:
            GLib.source_remove(self._upd_timer)
        self._upd_timer = GLib.timeout_add_seconds(8, lambda: self._maybe_restart(path))

    def _maybe_restart(self, path):
        import re
        from . import VERSION
        self._upd_timer = 0
        try:
            with open(path, encoding="utf-8") as f:
                m = re.search(r'^VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
        except OSError:
            return False          # desinstalado: no hacer nada
        if not m or m.group(1) == VERSION:
            return False
        args = ["--window"] if (self.window is not None and self.window.get_visible()) else ["--background"]
        self.restart_ui(args)
        return False

    def restart_ui(self, args):
        """Vuelve a lanzar este mismo programa (tras actualizarse o al cambiar de idioma)."""
        import os
        script = os.path.abspath(sys.argv[0])
        if os.path.basename(script) == "app.py" and (__package__ or "").startswith("sensorpulse"):
            # arrancado con «python3 -m sensorpulse.app»: un script suelto no podría importar
            # el paquete (importaciones relativas)
            cmd = [sys.executable, "-m", "sensorpulse.app"] + args
        else:
            exe = sys.argv[0] if os.path.isabs(sys.argv[0]) else "/usr/bin/sensorpulse"
            cmd = [sys.executable, exe] + args
        os.execv(sys.executable, cmd)

    def do_command_line(self, cmdline):
        opts = cmdline.get_options_dict().end().unpack()
        first = not self.store.cfg.get("first_run_done")
        if first:
            import os
            self.show_wizard(page=int(os.environ.get("SENSORPULSE_WIZARD_PAGE", "0")))
        elif opts.get("rename"):
            self.apply_display(background=True)
            self.rename_sensor(opts["rename"])
        elif opts.get("detail"):
            self.apply_display(background=True)
            self.show_window()
            self.window.show_detail_for_channel(opts["detail"])
        elif opts.get("settings"):
            self.open_settings()
        elif opts.get("wizard"):
            self.show_wizard()
        elif opts.get("background"):
            self.apply_display(background=True)
        else:
            self.apply_display(background=True)
            self.show_window()
        return 0

    def do_activate(self):
        self.show_window()

    # ------------------------------------------------------------ vistas
    def floating_on(self):
        from .ui import envinfo
        if envinfo.floating_by_desklet():
            return bool(self.store.cfg["display"].get("floating"))
        return bool(self.floating and self.floating.get_visible())

    def apply_display(self, background=False):
        self.store.reload_cfg()
        disp = self.store.cfg["display"]
        # panel (en GNOME con nuestra extensión, el panel es la extensión)
        from .ui import envinfo
        by_ext = envinfo.panel_by_extension()
        by_applet = envinfo.panel_by_cinnamon_applet()
        cfg = self.store.cfg
        if envinfo.desktop() == "gnome" and envinfo.gnome_ext_installed():
            # Activar/desactivar la extensión solo cuando cambia la opción «En el panel»:
            # si el usuario la desactiva a mano en «Extensiones», no se vuelve a activar sola.
            # Solo se apunta como hecho si se pudo (GNOME no compatible: se reintenta tras
            # actualizar, y mientras tanto se usa el icono de bandeja)
            want = bool(disp.get("panel"))
            if cfg.get("ext_applied") != want and envinfo.gnome_ext_set(want):
                cfg["ext_applied"] = want
                self.store.save_cfg()
        if by_applet:
            envinfo.cinnamon_set("applet", bool(disp.get("panel")))
            if disp.get("panel") and not cfg.get("cinnamon_applet_placed"):
                # una sola vez: llevarlo a la izquierda de los iconos del sistema si la 0.3.6
                # lo dejó a la derecha del todo
                if envinfo.cinnamon_place_applet():
                    cfg["cinnamon_applet_placed"] = True
                    self.store.save_cfg()
        if by_ext or by_applet:
            if self.tray is not None:
                self._hide_tray()
        elif disp.get("panel") and self.tray is None:
            from .ui.tray import Tray
            self.tray = Tray(self, self.store)
        elif not disp.get("panel") and self.tray is not None:
            self._hide_tray()
        # flotante
        self.set_floating(bool(disp.get("floating")), save=False)
        if self.window is not None:
            self.window.apply_theme()
            self.window.rebuild()
        if self.floating is not None:
            self.floating.apply_settings()
        if not background and disp.get("window"):
            self.show_window()
        # mantener viva la aplicación mientras haya panel o flotante
        need = bool((disp.get("panel") and not (by_ext or by_applet)) or
                    (disp.get("floating") and not envinfo.floating_by_desklet()))
        if need and not self._held:
            self.hold()
            self._held = True
        elif not need and self._held:
            self.release()
            self._held = False

    def _hide_tray(self):
        t = self.tray
        self.tray = None
        try:
            if t.kind == "xapp":
                t.icon.set_visible(False)
            elif t.kind == "ayatana":
                from gi.repository import AyatanaAppIndicator3 as AI
                t.icon.set_status(AI.IndicatorStatus.PASSIVE)
            elif t.kind == "gtk":
                t.icon.set_visible(False)
        except Exception:
            pass
        self.store.unsubscribe(t.update)

    def set_floating(self, on, save=True):
        from .ui import envinfo
        if envinfo.floating_by_desklet():
            envinfo.cinnamon_set("desklet", on)
            on_gtk = False
        else:
            on_gtk = on
        self._set_floating_gtk(on_gtk)
        if save:
            self.store.cfg["display"]["floating"] = on
            self.store.save_cfg()

    def _set_floating_gtk(self, on):
        if on:
            if self.floating is None:
                from .ui.floating import FloatingWidget
                self.floating = FloatingWidget(self, self.store)
            self.floating.show_all()
            self.floating.update_input_shape()
        elif self.floating is not None:
            self.floating.hide()
            self.store.fan_draw_ids = set()

    def show_window(self):
        if self.window is None:
            from .ui.window import MainWindow
            self.window = MainWindow(self, self.store)
            self.window.connect("destroy", self._window_closed)
        self.window.show_all()
        self.window.present()
        import os
        if os.environ.get("SENSORPULSE_OPEN_DETAIL"):  # solo para pruebas automáticas
            def _d():
                devs = self.store.devices()
                if devs:
                    self.window.show_detail(devs[int(os.environ["SENSORPULSE_OPEN_DETAIL"])]["id"])
                return False
            GLib.timeout_add(3000, _d)

    def _window_closed(self, *_a):
        self.window = None

    def hide_window(self):
        """Ajustes › «En una ventana» desactivado: la ventana se cierra sola (la 0.3.6 la
        dejaba abierta y había que cerrarla con la X)."""
        if self.window is not None:
            w = self.window
            self.window = None
            w.destroy()

    def rename_sensor(self, cid, parent=None):
        """Diálogo pequeño para poner un nombre propio a un sensor («diode 0» → «Placa base»).
        Lo abren el applet de Cinnamon, la extensión de GNOME, el desklet (sensorpulse
        --rename ID), el widget flotante y Ajustes."""
        from .ui.rename import RenameDialog
        if self.store.find(cid)[0] is None and not self.store.snapshot.get("devices") and \
                getattr(self, "_rename_wait", 0) < 4:
            # recién arrancado: esperar a la primera foto del servicio (sin ventanas abiertas
            # la aplicación terminaría: se mantiene viva mientras tanto)
            self._rename_wait = getattr(self, "_rename_wait", 0) + 1
            self.hold()

            def _again():
                self.release()
                self.rename_sensor(cid, parent)
                return False
            GLib.timeout_add(1500, _again)
            return
        self._rename_wait = 0
        dlg = RenameDialog(self, self.store, cid, parent)
        self.add_window(dlg)
        dlg.present()

    def open_settings(self, page=None, channel=None):
        if self.settings is None:
            from .ui.settings import Settings
            self.settings = Settings(self, self.store)
            self.add_window(self.settings)
            self.settings.connect("destroy", lambda *a: setattr(self, "settings", None))
        self.settings.show_all()
        self.settings.present()
        self.settings.show_page(page, channel)

    def show_wizard(self, page=0, reload=True):
        """reload=False: rehacer el asistente con lo elegido hasta ahora (aún sin guardar)."""
        from .ui.wizard import Wizard
        if self.wizard is not None:
            self.wizard.destroy()
        if reload:
            self.store.reload_cfg()
        self.wizard = Wizard(self, self.store, self._wizard_done)
        self.add_window(self.wizard)
        self.wizard.idx = page
        self.wizard.show_all()
        self.wizard.update_nav()
        self.wizard.present()

    def _wizard_done(self):
        from .ui.settings import cfgmod_autostart
        cfgmod_autostart(self.store.cfg.get("autostart", True))
        w = self.wizard
        self.wizard = None
        self.apply_display()
        if not any(self.store.cfg["display"].values()):
            self.show_window()
        if w is not None:
            GLib.idle_add(w.destroy)

    def quit_all(self):
        self.store.save_cfg()
        self.quit()


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("status", "report", "export"):
        from .cli import main as cli
        return cli(sys.argv[1:])
    # El nombre del programa es lo que el escritorio compara con sensorpulse.desktop
    # (app_id en Wayland, WM_CLASS en X11); así el icono y el lanzador coinciden
    GLib.set_prgname("sensorpulse")
    app = App()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
