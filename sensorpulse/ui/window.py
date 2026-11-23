"""Ventana principal: una tarjeta por dispositivo (el «vistazo») y vista de detalle
con gráfica grande, mín./máx. y acceso a las alarmas."""

import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango

from .. import APP_NAME
from .. import config as cfgmod
from ..i18n import _, N_
from ..views import muted_text
from . import draw as D
from .theme import PALETTES, hex_to_rgb, state_color
from .theme import resolve as theme_resolve

CARD_W, CARD_H = 260, 196

def css_for(theme):
    pal = PALETTES[theme]
    bg = "rgb(%d,%d,%d)" % tuple(int(x * 255) for x in pal["bg"])
    fg = "rgb(%d,%d,%d)" % tuple(int(x * 255) for x in pal["fg"])
    return ("""
    .sp-content { background-color: transparent; }
    .sp-bg { background-color: %s; }
    .sp-status { font-size: 9pt; opacity: 0.75; }
    .sp-content label, .sp-content button label { color: %s; }
    .sp-content button { background-image: none; background-color: rgba(127,127,127,0.12);
                         border-color: rgba(127,127,127,0.25); }
    .sp-content list, .sp-content row { background-color: transparent; }
    .sp-content row:selected { background-color: rgba(34,211,238,0.18); }
    .sp-content radiobutton:checked, .sp-content button:checked { background-color: rgba(34,211,238,0.30); }
    .sp-title { font-size: 15pt; font-weight: 600; }
    .sp-sub { opacity: 0.65; }
    .sp-flat { background: none; border: none; box-shadow: none; }
    """ % (bg, fg)).encode()


GAP = 14


class Card(Gtk.DrawingArea):
    def __init__(self, win, dev_id, big=False):
        super().__init__()
        self.win = win
        self.dev_id = dev_id
        self.big = big
        if big:   # tarjeta principal de la rejilla «Bento»: 2 × 2
            self.set_size_request(CARD_W * 2 + GAP, CARD_H * 2 + GAP)
        else:
            self.set_size_request(CARD_W, CARD_H)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK |
                        Gdk.EventMask.LEAVE_NOTIFY_MASK | Gdk.EventMask.KEY_PRESS_MASK)
        self.set_can_focus(True)
        self.connect("key-press-event", self.on_key)
        acc = self.get_accessible()
        if acc is not None:
            gi.require_version("Atk", "1.0")
            from gi.repository import Atk
            acc.set_role(Atk.Role.PUSH_BUTTON)
        self.hover = False
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_click)
        self.connect("enter-notify-event", lambda *a: self._h(True))
        self.connect("leave-notify-event", lambda *a: self._h(False))
        self.set_tooltip_text(_("Click for details"))

    def _h(self, v):
        self.hover = v
        self.queue_draw()
        win = self.get_window()
        if win:
            win.set_cursor(Gdk.Cursor.new_from_name(self.get_display(), "pointer") if v else None)

    def on_key(self, _w, ev):
        if ev.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self.win.show_detail(self.dev_id)
            return True
        return False

    def update_accessible(self):
        """Texto para lectores de pantalla (Orca): «CPU: 54 °C, normal; …»."""
        acc = self.get_accessible()
        d = self.dev()
        if acc is None or d is None:
            return
        store = self.win.store
        parts = []
        for c in store.visible_channels(d)[:5]:
            it = store.item(c, d)
            val, unit = D.fmt(dict(it, anim=it["value"]))
            tag = D.status_tag(it)
            parts.append("%s %s %s%s" % (it["label"], val, unit, (", " + tag[1].lower()) if tag else ""))
        acc.set_name("%s: %s" % (d["name"], "; ".join(parts)))
        acc.set_description(_("Click for details"))

    def on_click(self, _w, ev):
        if ev.button == 1:
            self.win.show_detail(self.dev_id)
        return True

    def dev(self):
        for d in self.win.store.snapshot.get("devices", []):
            if d["id"] == self.dev_id:
                return d
        return None

    def on_draw(self, _w, cr):
        store = self.win.store
        cfg = store.cfg
        pal = PALETTES[theme_resolve(cfg)]
        accent = hex_to_rgb(cfg.get("accent"))
        d = self.dev()
        w, h = self.get_allocated_width(), self.get_allocated_height()
        if d is None:
            return False
        chans = store.visible_channels(d)
        prim = next((c for c in chans if c["primary"]), chans[0] if chans else None)
        alarm = any(c["id"] in store.snapshot.get("alarm_channels", []) for c in d["channels"])
        # tarjeta
        D.rounded_rect(cr, 1, 1, w - 2, h - 2, 16)
        cr.set_source_rgb(*(pal["card2"] if self.hover else pal["card"]))
        cr.fill_preserve()
        sc = state_color(d["state"], cfg.get("colorblind", False))
        if alarm and store.pulse > 0:
            cr.set_source_rgba(sc[0], sc[1], sc[2], 0.35 + 0.6 * store.pulse)
            cr.set_line_width(2.5)
        else:
            cr.set_source_rgba(1, 1, 1, 0.06) if theme_resolve(cfg) != "light" else \
                cr.set_source_rgba(0, 0, 0, 0.08)
            cr.set_line_width(1)
        cr.stroke()
        fl = [t for cid, t in store.flash.items() if any(c["id"] == cid for c in d["channels"])]
        if fl:
            k = 1.0 - min(1.0, time.monotonic() - max(fl))
            D.rounded_rect(cr, 1, 1, w - 2, h - 2, 16)
            cr.set_source_rgba(0.13, 0.77, 0.37, 0.8 * k)
            cr.set_line_width(3)
            cr.stroke()
        # cabecera
        D.text(cr, d["name"], 18, 24, 15, pal["fg"], Pango.Weight.SEMIBOLD, align="left",
               max_width=w - 70)
        sub = d.get("model") or ""
        if d.get("asleep") and d.get("hybrid"):
            sub = _("Resting · measured every 30 s to save battery")
        elif d.get("asleep"):
            sub = _("Resting (not woken up to save power)")
        elif any(c.get("throttling") for c in d["channels"]):
            sub = _("Reducing speed because of heat")
        elif d.get("hybrid"):
            sub = _("Measured every 30 s to save battery")
        D.text(cr, sub, 18, 44, 11, pal["dim"], align="left", max_width=w - 36)
        cr.set_source_rgb(*sc)
        cr.arc(w - 22, 24, 5, 0, 6.2832)
        cr.fill()
        if prim is None:
            return False
        it = store.item(prim, d)
        if self.big:
            self._draw_big(cr, w, h, d, chans, prim, it, pal, accent)
            return False
        # indicador principal (anillo) + valor
        tag = D.status_tag(it)
        D.draw_ring(cr, 58, 100, 34, dict(it, label=tag[1].capitalize() if tag else ""), pal,
                    accent, store.pulse)
        # secundarios
        y = 74
        for c in [c for c in chans if c is not prim][:4]:
            ci = store.item(c, d)
            val, unit = D.fmt(ci)
            col = D.item_color(ci, accent) if c["kind"] == "temp" else pal["fg"]
            st = D.status_tag(ci)
            sym = (st[0] + " ") if st and st[2] != "normal" else ""
            D.text(cr, ci["label"], 112, y, 11, pal["dim"], align="left", max_width=w - 190)
            D.text(cr, (sym + "%s %s" % (val, unit)).strip(), w - 18, y, 12, col,
                   Pango.Weight.SEMIBOLD, align="right", tabular=True)
            y += 19
        # mini-gráfica del principal
        pts = list(store.local_hist.get(prim["id"], ()))
        if len(pts) > 1:
            D.draw_sparkline(cr, 16, h - 44, w - 32, 30, pts, it, pal, accent, fill=True, line=1.6,
                             show_warn=False)
        else:
            D.text(cr, _("collecting data…"), w / 2, h - 28, 10, pal["dim"])
        return False


def _draw_big_impl(self, cr, w, h, d, chans, prim, it, pal, accent):
    """Tarjeta grande: instrumento «Cabina», gráfica de los últimos minutos con la banda
    mínimo-máximo de la sesión y los demás valores."""
    store = self.win.store
    r = min(h * 0.30, w * 0.18)
    D.draw_cockpit(cr, 26 + r * 1.1, 70 + r * 1.15, r, it, pal, accent, store.pulse,
                   key="big:" + prim["id"])
    gx, gy = 26 + r * 2.4 + 20, 70
    gw, gh = w - gx - 22, h * 0.48
    pts = list(store.local_hist.get(prim["id"], ()))
    mm = store.session_minmax.get(prim["id"])
    if len(pts) > 1:
        if mm and prim["kind"] == "temp":
            conv = (lambda v: v * 9 / 5 + 32) if store.cfg.get("unit") == "F" else (lambda v: v)
            lo, hi = conv(mm[0]), conv(mm[1])
            vals = [v for _t, v in pts]
            vmin, vmax = min(vals + [lo]), max(vals + [hi])
            span = max(1.0, vmax - vmin)
            y1 = gy + gh - (hi - vmin) / span * gh
            y2 = gy + gh - (lo - vmin) / span * gh
            col = D.item_color(it, accent)
            cr.set_source_rgba(col[0], col[1], col[2], 0.08)
            cr.rectangle(gx, y1, gw, max(1, y2 - y1))
            cr.fill()
        if store.cfg.get("unit") == "F" and prim["kind"] == "temp":
            pts = [(t, v * 9 / 5 + 32) for t, v in pts]
        D.draw_sparkline(cr, gx, gy, gw, gh, pts, it, pal, accent, fill=True, line=2.0)
        if mm:
            lo = store.item(dict(prim, value=mm[0]), d)
            hi = store.item(dict(prim, value=mm[1]), d)
            D.text(cr, _("min %s · max %s") % (D.fmt(dict(lo, anim=lo["value"]))[0],
                                                 D.fmt(dict(hi, anim=hi["value"]))[0]),
                   gx, gy + gh + 14, 11, pal["dim"], align="left")
    else:
        D.text(cr, _("collecting data…"), gx + gw / 2, gy + gh / 2, 12, pal["dim"])
    y = gy + gh + 40
    for c in [c for c in chans if c is not prim][:6]:
        ci = store.item(c, d)
        val, unit = D.fmt(ci)
        col = D.item_color(ci, accent) if c["kind"] == "temp" else pal["fg"]
        D.text(cr, ci["label"], gx, y, 12, pal["dim"], align="left", max_width=gw * 0.6)
        D.text(cr, ("%s %s" % (val, unit)).strip(), gx + gw, y, 13, col, Pango.Weight.SEMIBOLD,
               align="right", tabular=True)
        y += 21
        if y > h - 16:
            break


Card._draw_big = _draw_big_impl


def export_rows(store, cid, seconds):
    """Filas del historial para exportar (hora local ISO, valor, mínimo, máximo; en °C)."""
    rows = []
    client = store.client
    data = []
    if client is not None:
        try:
            if seconds > 3600:
                data = client.history_range(cid, seconds)
            else:
                data = [[t, v, v, v] for t, v in client.history(cid, seconds)]
        except Exception:
            data = []
    for t, v, mn, mx in data:
        rows.append({"time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)),
                     "value": v, "min": mn, "max": mx})
    return rows


class DetailView(Gtk.Box):
    RANGES = [(600, N_("10 min")), (3600, N_("1 hour")), (86400, N_("24 hours")),
              (7 * 86400, N_("7 days")), (30 * 86400, N_("30 days"))]

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.win = win
        self.dev_id = None
        self.range = 600
        self.hist = {}
        self._fetch_src = None
        self.set_border_width(16)
        top = Gtk.Box(spacing=8)
        back = Gtk.Button.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON)
        back.set_tooltip_text(_("Back"))
        back.connect("clicked", lambda *a: win.show_overview())
        top.pack_start(back, False, False, 0)
        self.title = Gtk.Label(xalign=0)
        self.title.get_style_context().add_class("sp-title")
        top.pack_start(self.title, True, True, 0)
        self.range_btns = []
        group = None
        for secs, label in [(s, _(n)) for s, n in reversed(self.RANGES)]:
            b = Gtk.RadioButton.new_with_label_from_widget(group, label)
            b.set_mode(False)
            group = b
            b.connect("toggled", self._on_range, secs)
            top.pack_end(b, False, False, 0)
        exp = Gtk.Button(label=_("Export…"))
        exp.set_tooltip_text(_("Save the history of this sensor as CSV or JSON"))
        exp.connect("clicked", self._export)
        top.pack_end(exp, False, False, 0)
        reset = Gtk.Button(label=_("Reset min/max"))
        reset.connect("clicked", self._reset)
        top.pack_end(reset, False, False, 6)
        self.pack_start(top, False, False, 0)
        self.chart = Gtk.DrawingArea()
        self.chart.set_size_request(-1, 260)
        self.chart.connect("draw", self.on_chart)
        self.pack_start(self.chart, True, True, 0)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-selected", lambda *a: self.chart.queue_draw())
        sw = Gtk.ScrolledWindow()
        sw.set_min_content_height(180)
        sw.add(self.listbox)
        self.pack_start(sw, True, True, 0)
        alarms = Gtk.Button(label=_("Alarms for this sensor…"))
        alarms.connect("clicked", self._alarms)
        self.pack_start(alarms, False, False, 0)
        self.rows = {}

    def _on_range(self, btn, secs):
        if btn.get_active():
            self.range = secs
            self.fetch()

    def _export(self, *_a):
        import csv
        import json as _json
        cid = self.selected()
        if not cid:
            return
        c, d = self.win.store.find(cid)
        dlg = Gtk.FileChooserDialog(title=_("Export history"), transient_for=self.win,
                                    action=Gtk.FileChooserAction.SAVE)
        dlg.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Save"), Gtk.ResponseType.OK)
        dlg.set_do_overwrite_confirmation(True)
        safe = "".join(ch if ch.isalnum() else "-" for ch in (c["label"] if c else "sensor"))
        dlg.set_current_name("sensorpulse-%s-%s.csv" % (safe, time.strftime("%Y%m%d")))
        for name, pat in (("CSV", "*.csv"), ("JSON", "*.json")):
            flt = Gtk.FileFilter()
            flt.set_name(name)
            flt.add_pattern(pat)
            dlg.add_filter(flt)
        if dlg.run() == Gtk.ResponseType.OK:
            path = dlg.get_filename()
            rows = export_rows(self.win.store, cid, self.range)
            try:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    if path.lower().endswith(".json"):
                        _json.dump({"sensor": c["label"] if c else cid, "unit": (c or {}).get("unit"),
                                    "rows": rows}, f, ensure_ascii=False, indent=1)
                    else:
                        w = csv.writer(f)
                        w.writerow(["time", "value", "min", "max"])
                        for r in rows:
                            w.writerow([r["time"], r["value"], r["min"], r["max"]])
            except OSError as e:
                m = Gtk.MessageDialog(transient_for=self.win, modal=True,
                                      message_type=Gtk.MessageType.ERROR,
                                      buttons=Gtk.ButtonsType.OK, text=str(e))
                m.run()
                m.destroy()
        dlg.destroy()

    def _reset(self, *_a):
        for cid in list(self.rows):
            self.win.store.session_minmax.pop(cid, None)
        self.update()

    def _alarms(self, *_a):
        cid = self.selected()
        self.win.get_application().open_settings(page="alarms", channel=cid)

    def show_device(self, dev_id):
        self.dev_id = dev_id
        for r in self.listbox.get_children():
            self.listbox.remove(r)
        self.rows = {}
        d = self._dev()
        if d is None:
            return
        self.title.set_text(d["name"] + ("  ·  " + d["model"] if d.get("model") else ""))
        for c in d["channels"]:
            if c["kind"] == "status" and not c.get("text"):
                continue
            row = Gtk.ListBoxRow()
            box = Gtk.Box(spacing=12)
            box.set_border_width(6)
            name = Gtk.Label(label=cfgmod.display_name(self.win.store.cfg, c), xalign=0)
            val = Gtk.Label(xalign=1)
            mm = Gtk.Label(xalign=1)
            mm.get_style_context().add_class("sp-sub")
            box.pack_start(name, True, True, 0)
            box.pack_start(mm, False, False, 0)
            box.pack_start(val, False, False, 0)
            row.add(box)
            row.cid = c["id"]
            self.listbox.add(row)
            self.rows[c["id"]] = (row, val, mm)
        self.listbox.show_all()
        first = self.listbox.get_row_at_index(0)
        if first:
            self.listbox.select_row(first)
        self.fetch()
        self.update()

    def _dev(self):
        for d in self.win.store.snapshot.get("devices", []):
            if d["id"] == self.dev_id:
                return d
        return None

    def selected(self):
        r = self.listbox.get_selected_row()
        return getattr(r, "cid", None) if r else None

    def fetch(self):
        client = self.win.store.client
        if client is None:
            return
        for cid in self.rows:
            try:
                if self.range > 3600:   # historial largo (SQLite del servicio)
                    # t es el inicio de cada grupo: el punto va en su centro (antes +30 s
                    # fijos, que solo valían para grupos de 1 minuto)
                    from ..core.historydb import bucket_step
                    half = bucket_step(self.range) / 2.0
                    self.hist[cid] = [(r[0] + half, r[1]) for r in client.history_range(cid, self.range)]
                else:
                    self.hist[cid] = client.history(cid, self.range)
            except Exception:
                self.hist[cid] = list(self.win.store.local_hist.get(cid, ()))
        self.chart.queue_draw()

    def update(self):
        d = self._dev()
        if d is None:
            return
        store = self.win.store
        for c in d["channels"]:
            if c["id"] not in self.rows:
                continue
            _row, val, mm = self.rows[c["id"]]
            it = store.item(c, d)
            v, unit = D.fmt(dict(it, anim=it["value"]))
            if c["kind"] == "status":
                v, unit = c.get("text", ""), ""
            val.set_markup("<b>%s</b> %s" % (GLib.markup_escape_text(v), unit))
            m = store.session_minmax.get(c["id"])
            if m and c["kind"] not in ("status",):
                lo = store.item(dict(c, value=m[0]), d)
                hi = store.item(dict(c, value=m[1]), d)
                mm.set_text(_("min %s · max %s") % (D.fmt(dict(lo, anim=lo["value"]))[0],
                                                     D.fmt(dict(hi, anim=hi["value"]))[0]))
            # añadir el punto nuevo al histórico mostrado
            if c["value"] is not None and c["id"] in self.hist:
                t = store.snapshot.get("time", time.time())
                h = self.hist[c["id"]]
                if not h or h[-1][0] != t:
                    h.append((t, c["value"]))
        self.chart.queue_draw()

    def on_chart(self, w, cr):
        store = self.win.store
        cfg = store.cfg
        pal = PALETTES[theme_resolve(cfg)]
        accent = hex_to_rgb(cfg.get("accent"))
        W, H = w.get_allocated_width(), w.get_allocated_height()
        D.rounded_rect(cr, 0, 0, W, H, 14)
        cr.set_source_rgb(*pal["card"])
        cr.fill()
        cid = self.selected()
        c, d = store.find(cid) if cid else (None, None)
        if c is None:
            return False
        it = store.item(c, d)
        pts = self.hist.get(cid) or list(store.local_hist.get(cid, ()))
        now = time.time()
        if cfg.get("unit") == "F" and c["kind"] == "temp":
            pts = [(t, v * 9 / 5 + 32) for t, v in pts]
        # rejilla
        cr.set_source_rgba(*pal["grid"])
        cr.set_line_width(1)
        for i in range(1, 5):
            y = 20 + (H - 50) * i / 5
            cr.move_to(50, y)
            cr.line_to(W - 16, y)
        cr.stroke()
        res = D.draw_sparkline(cr, 50, 20, W - 66, H - 50, pts, it, pal, accent, fill=True,
                               line=2.2, t0=now - self.range, t1=now)
        if res:
            lo, hi = res
            # decimales según el margen (un gráfico de 1,1-1,4 V decía «1 1 1 1 1 1»)
            dec = 0 if hi - lo >= 10 else (1 if hi - lo >= 1 else 2)
            for i in range(0, 6):
                v = lo + (hi - lo) * (5 - i) / 5
                D.text(cr, "%.*f" % (dec, v), 42, 20 + (H - 50) * i / 5, 10, pal["dim"],
                       align="right")
        if self.range >= 2 * 86400:
            ago = _("−%d days") % (self.range // 86400)
        elif self.range >= 86400:
            ago = _("−%d h") % (self.range // 3600)
        else:
            ago = _("−%d min") % (self.range // 60)
        D.text(cr, ago, 50, H - 14, 10, pal["dim"], align="left")
        D.text(cr, _("now"), W - 16, H - 14, 10, pal["dim"], align="right")
        if len(pts) < 2:
            D.text(cr, _("collecting data…"), W / 2, H / 2, 13, pal["dim"])
        acc = w.get_accessible()
        if acc is not None and pts:
            vals = [v for _t, v in pts if v is not None]
            if vals:
                f = (lambda x: "%.2f" % x) if c["kind"] == "volt" else \
                    (lambda x: ("%.1f" % x) if abs(x) < 10 and c["kind"] == "power" else "%.0f" % x)
                acc.set_name(_("Chart of %(name)s: min %(min)s, max %(max)s, now %(now)s") % {
                    "name": it["label"], "min": f(min(vals)), "max": f(max(vals)), "now": f(vals[-1])})
        return False


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app, store):
        super().__init__(application=app, title=APP_NAME)
        self.store = store
        cfg = store.cfg
        self.set_default_size(cfg["window"].get("width", 900), cfg["window"].get("height", 620))
        self.set_icon_name("sensorpulse")
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), self.css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.apply_theme()

        hb = Gtk.HeaderBar(show_close_button=True)
        self.set_titlebar(hb)
        self.status = Gtk.Label()
        hb.set_custom_title(self.status)
        gear = Gtk.Button.new_from_icon_name("emblem-system-symbolic", Gtk.IconSize.BUTTON)
        gear.set_tooltip_text(_("Settings"))
        gear.connect("clicked", lambda *a: app.open_settings())
        hb.pack_end(gear)
        self.mute_btn = Gtk.MenuButton()
        self.mute_btn.set_tooltip_text(_("Mute alarms"))
        self.mute_btn.add(Gtk.Image.new_from_icon_name("audio-volume-muted-symbolic",
                                                      Gtk.IconSize.BUTTON))
        mm = Gtk.Menu()
        for mins, label in ((15, _("Mute for 15 minutes")), (60, _("Mute for 1 hour")),
                            (24 * 60, _("Mute until tomorrow")), (0, _("Unmute"))):
            it = Gtk.MenuItem(label=label)
            it.connect("activate", lambda w, m=mins: self.store.client and self.store.client.mute(m))
            mm.append(it)
        mm.show_all()
        self.mute_btn.set_popup(mm)
        hb.pack_end(self.mute_btn)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        # rejilla propia: «Bento» (tarjeta principal grande) o clásica
        self.flow = Gtk.Grid(column_spacing=GAP, row_spacing=GAP)
        self.flow.set_border_width(18)
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_halign(Gtk.Align.CENTER)
        self._cols = 0
        sw = Gtk.ScrolledWindow()
        sw.add(self.flow)
        sw.connect("size-allocate", self._on_alloc)
        self.stack.add_named(sw, "overview")
        self.detail = DetailView(self)
        self.stack.add_named(self.detail, "detail")
        self.stack.get_style_context().add_class("sp-content")
        # fondo pintado (plano o «Aurora») debajo de todo
        self.bg = Gtk.DrawingArea()
        self.bg.connect("draw", self._draw_bg)
        overlay = Gtk.Overlay()
        overlay.add(self.bg)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.pack_start(self.stack, True, True, 0)
        # barra de estado: «Último dato hace 2 s» / «Reconectando…» / actualización
        sb = Gtk.Box(spacing=8)
        sb.set_border_width(4)
        sb.set_margin_start(14)
        sb.set_margin_end(14)
        self.status_lbl = Gtk.Label(xalign=0)
        self.status_lbl.get_style_context().add_class("sp-status")
        sb.pack_start(self.status_lbl, True, True, 0)
        self.status_btn = Gtk.Button()
        self.status_btn.set_no_show_all(True)
        self.status_btn.connect("clicked", self._status_action)
        sb.pack_end(self.status_btn, False, False, 0)
        root.pack_end(sb, False, False, 0)
        root.get_style_context().add_class("sp-content")
        overlay.add_overlay(root)
        self.add(overlay)
        self._status_mode = None
        self._aurora_col = None
        self._status_src = GLib.timeout_add_seconds(1, self._tick_status)
        self.connect("destroy", self._on_destroy)
        self.cards = {}
        self.connect("configure-event", self._on_conf)
        self.connect("delete-event", self._on_delete)
        store.subscribe(self.on_update)
        store.subscribe_frame(self.on_frame)
        self.on_update()

    def _on_destroy(self, *_a):
        # sin esto cada apertura/cierre dejaba suscriptores huérfanos en el store
        GLib.source_remove(self._status_src)
        self.store.unsubscribe(self.on_update)
        self.store.unsubscribe(self.on_frame)

    def apply_theme(self):
        self.css.load_from_data(css_for(theme_resolve(self.store.cfg)))
        settings = Gtk.Settings.get_default()
        chosen = self.store.cfg.get("theme", "auto")
        if settings is not None and chosen in ("dark", "light", "deep"):
            # solo si el usuario fuerza un tema; en «auto» la ventana sigue al sistema
            settings.set_property("gtk-application-prefer-dark-theme", chosen != "light")

    def _on_conf(self, *_a):
        w, h = self.get_size()
        self.store.cfg["window"].update({"width": w, "height": h})
        return False

    def _on_delete(self, *_a):
        self.store.save_cfg()
        return False

    def _on_alloc(self, sw, alloc):
        cols = max(1, (alloc.width - 36 + GAP) // (CARD_W + GAP))
        if cols != self._cols:
            self._cols = cols
            GLib.idle_add(lambda: (self.rebuild(), False)[1])

    def _big_id(self, devs):
        win = self.store.cfg["window"]
        if win.get("layout", "bento") != "bento" or max(1, self._cols) < 2:
            return None
        ids = [d["id"] for d in devs]
        if win.get("big") in ids:
            return win["big"]
        cpu = next((d["id"] for d in devs if d["cls"] == "cpu"), None)
        return cpu or (ids[0] if ids else None)

    def rebuild(self):
        for ch in self.flow.get_children():
            self.flow.remove(ch)
        self.cards = {}
        devs = [d for d in self.store.devices() if self.store.visible_channels(d)]
        cols = max(1, self._cols or 3)
        big = self._big_id(devs)
        taken = set()
        pos = 0

        def next_free():
            nonlocal pos
            while (pos // cols, pos % cols) in taken:
                pos += 1
            cell = (pos // cols, pos % cols)
            taken.add(cell)
            return cell

        if big:
            c = Card(self, big, big=True)
            self.cards[big] = c
            self.flow.attach(c, 0, 0, 2, 2)
            taken.update({(0, 0), (0, 1), (1, 0), (1, 1)})
        for d in devs:
            if d["id"] == big:
                continue
            c = Card(self, d["id"])
            self.cards[d["id"]] = c
            row, col = next_free()
            self.flow.attach(c, col, row, 1, 1)
        add = Gtk.Button(label="＋  " + _("Add or hide sensors"))
        add.set_size_request(CARD_W, 48)
        add.set_valign(Gtk.Align.CENTER)
        add.connect("clicked", lambda *a: self.get_application().open_settings(page="sensors"))
        row, col = next_free()
        self.flow.attach(add, col, row, 1, 1)
        self.flow.show_all()

    # ------------------------------------------------------------ fondo y barra de estado
    def _draw_bg(self, w, cr):
        import cairo
        cfg = self.store.cfg
        pal = PALETTES[theme_resolve(cfg)]
        W, H = w.get_allocated_width(), w.get_allocated_height()
        cr.set_source_rgb(*pal["bg"])
        cr.paint()
        if not cfg["window"].get("aurora"):
            return False
        # «Aurora»: degradado radial muy suave cuyo tono sigue el estado global
        target = state_color(self.store.snapshot.get("state", "normal"), cfg.get("colorblind"))
        cur = self._aurora_col or target
        cur = tuple(cur[i] + (target[i] - cur[i]) * 0.25 for i in range(3))
        self._aurora_col = cur
        a = 0.16 if theme_resolve(cfg) != "light" else 0.10
        for (fx, fy, rr, aa) in ((0.15, 0.10, 0.75, a), (0.9, 0.95, 0.65, a * 0.7)):
            g = cairo.RadialGradient(W * fx, H * fy, 0, W * fx, H * fy, max(W, H) * rr)
            g.add_color_stop_rgba(0, cur[0], cur[1], cur[2], aa)
            g.add_color_stop_rgba(1, cur[0], cur[1], cur[2], 0)
            cr.set_source(g)
            cr.paint()
        return False

    def _tick_status(self):
        if not self.get_mapped():
            return True
        if self.store.cfg["window"].get("aurora"):
            self.bg.queue_draw()   # como mucho una vez por segundo
        age = time.time() - float(self.store.snapshot.get("time") or 0)
        if self._update_available():
            self._set_status(_("SensorPulse has been updated."), _("Restart SensorPulse"), "restart")
        elif self.store.snapshot.get("time") and age > float(self.store.snapshot.get("stale_after") or 12):
            self._set_status(_("Reconnecting to the sensor service…"), _("Restart the service"),
                             "service")
        elif self.store.snapshot.get("time"):
            self._set_status(_("Last reading %d s ago") % max(0, round(age)), None, None)
        return True

    def _set_status(self, text, button, mode):
        self.status_lbl.set_text(text)
        self._status_mode = mode
        if button:
            self.status_btn.set_label(button)
            self.status_btn.show()
        else:
            self.status_btn.hide()

    def _update_available(self):
        """¿Se ha instalado otra versión mientras la ventana estaba abierta?"""
        now = time.time()
        if now - getattr(self, "_upd_t", 0) < 60:
            return getattr(self, "_upd", False)
        self._upd_t = now
        import os
        from .. import VERSION
        f = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "__init__.py")
        try:
            with open(f, encoding="utf-8") as fh:
                txt = fh.read()
            self._upd = ('VERSION = "%s"' % VERSION) not in txt
        except OSError:
            self._upd = False
        return self._upd

    def _status_action(self, *_a):
        if self._status_mode == "restart":
            import os
            import sys
            self.store.save_cfg()
            exe = "/usr/bin/sensorpulse" if os.path.exists("/usr/bin/sensorpulse") else sys.argv[0]
            os.execv(sys.executable, [sys.executable, exe, "--window"])
        elif self._status_mode == "service":
            self.store.restart_service()

    def on_update(self):
        snap = self.store.snapshot
        ids = [d["id"] for d in self.store.devices() if self.store.visible_channels(d)]
        if set(ids) != set(self.cards):
            self.rebuild()
        st = self.store.state()
        txt = {"normal": _("All good"), "hot": _("Something is hot"), "critical": _("Attention!"),
               "sleep": _("Resting")}.get(st, _("Waiting for data…"))
        col = state_color(st, self.store.cfg.get("colorblind", False))
        hexc = "#%02x%02x%02x" % tuple(int(x * 255) for x in col)
        extra = ("  ·  " + muted_text(snap)) if (muted_text(snap) and not self.store.stale) else ""
        self.status.set_markup('<span foreground="%s">●</span>  <b>%s</b>%s' %
                               (hexc, GLib.markup_escape_text(txt), GLib.markup_escape_text(extra)))
        for c in self.cards.values():
            c.queue_draw()
            c.update_accessible()
        if self.stack.get_visible_child_name() == "detail":
            self.detail.update()

    def on_frame(self):
        """Solo las tarjetas con algo que se mueve (aguja, latido, destello o hélice): en la
        0.3.6 se redibujaban todas en cada fotograma."""
        if self.stack.get_visible_child_name() != "overview":
            return
        live = self.store.animating_ids()
        for dev_id, c in self.cards.items():
            d = next((x for x in self.store.snapshot.get("devices", []) if x["id"] == dev_id), None)
            if d is None or any(ch["id"] in live for ch in d["channels"]):
                c.queue_draw()

    def show_detail(self, dev_id):
        self.detail.show_device(dev_id)
        self.stack.set_visible_child_name("detail")

    def show_detail_for_channel(self, cid):
        def _go():
            c, d = self.store.find(cid)
            if d is None:
                return False
            self.show_detail(d["id"])
            for row in self.detail.listbox.get_children():
                if getattr(row, "cid", None) == cid:
                    self.detail.listbox.select_row(row)
            return False
        if self.store.find(cid)[1] is not None:
            _go()
        else:   # recién arrancado: esperar a la primera foto del servicio
            GLib.timeout_add(1500, _go)

    def show_overview(self):
        self.stack.set_visible_child_name("overview")
