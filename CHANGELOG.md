# Changelog

## 0.3.8 — 2026-11-23
- **Cinnamon applet:** it failed to load ("Error importing applet.js"). It assigned
  `this._panelHeight`, a read-only property of Cinnamon's `Applet` class; it now uses its own name.
- The JavaScript tests mock that read-only property like Cinnamon does.
- `tests/gui_037.py` requires GTK 3 before loading it.

## 0.3.7 — 2026-11-22
Requested improvements plus a review of everything they touch. Details and tests for each change:
docs/SensorPulse-0.3.7-cambios.md (Spanish).

Panel
- The Cinnamon applet is placed **to the left of the system icons and the clock**, in the free
  part of the panel (0.3.6 put it at the far right). If it is already there it is moved once;
  if the user moves it elsewhere, that is respected. Same for the GNOME extension.
- Up to **8 values** in the bar (was 3; default 3 instead of 2). Those that do not fit, and those
  marked "On click", are shown when the panel is clicked.
- Panel look: **name and number** or **odometer-style gauges**, with or without names.
- Settings › "In a window" switched off **closes the window**; changing any other setting no
  longer raises the window over Settings.

Assistant, names and sounds
- The assistant explains and lets you choose what goes to the panel, what only on click and what
  to the desktop widget, and give each sensor **a name of your own**. Also in Settings and, by
  right-clicking, from the applet, the extension, the desklet and the floating widget.
- **15 sounds** (was 6), including a **nuclear plant alarm**, a sonar ping, digital beeps and a
  soft chime; a **▶** button next to every sound selector (D-Bus method `PlaySound`, without a
  notification). The assistant offers every sound option in Settings, and Settings gains the
  warning-sound selector.

Disks
- Each disk's type is detected: **NVMe and SATA SSDs** are read often (no effect on rest or
  lifespan); for **hard disks** the user decides (2 min to 2 h) and warm ones are watched every
  2 minutes until they cool down. Also in the assistant.

CPU use
- Floating "Glass" widget with a full chart: **drawing goes from 8.6 % to 0.4 % of one core**
  (whole process: 9.1 % to 0.8 %). Cached frame, only the fan icons are repainted, single-stroke
  sparkline with cached gradients, fans at 5 fps, and the main window only redraws the cards
  that move.

Languages
- **Italian, German and Portuguese** (besides Spanish, English, French, Japanese and Chinese), also
  in the applet, extension, desklet, launcher, polkit and AppStream.

Other
- Settings switches centred (they were stretched next to two-line descriptions).
- Package maintainer: "SensorPulse", without an e-mail address.

## 0.3.6 — 2026-11-22
Fixes 42 bugs found in a full review of 0.3.5 (5 critical, 20 important, 17 minor). Details,
reproduction and verification of each one: docs/SensorPulse-0.3.6-cambios.md.

Critical
- **C1/C2** Readings, alarm delays, repeats, cool-downs and mute are timed with the monotonic
  clock. In 0.3.5 a backwards clock jump (NTP, manual change) froze all readings and alarms for
  as long as the jump, while the window, panel and applets kept showing the last value as current.
- **C3** Desktop notifications are sent asynchronously over D-Bus (libnotify removed): a hung
  notification daemon blocked the whole service for ~25 s per notification.
- **C4** A deeply nested `config.json` no longer crashes every SensorPulse process at start-up.
- **C5** All settings written by the Settings window are validated (`panel_count`, `colorblind`,
  `animate_fans`, `language`, `ext_applied`, `floating.panel_bg`, `alarms.warn_sound_name`).

Important
- An unreadable `config.json` (permissions, I/O error) is no longer moved away as "corrupt" nor
  overwritten; the window, panel, GNOME extension, Cinnamon applet and desklet detect a frozen
  service by the time since the last update arrived; a service stopped on purpose
  (`systemctl --user stop`) is no longer restarted by the UI or the desktop integrations; the
  UI connects by itself when the service appears late; "Restart the service" works on a frozen
  service; NVIDIA values are cleared when NVML loses the card; "Find sensors" keeps hidden
  empty fan headers; the history only stores values actually read; a damaged history database
  is replaced at start-up; "Do not disturb" checks never block and never start a notification
  daemon; only the real SensorPulse programs may stop the service over D-Bus; UDisks2 lookups
  are retried and all ATA standby states are recognised; the desklet uses the alarm level, shows
  °F and volts correctly; USB disks are not offered for SATA detection; the Ayatana (GLib) tray
  menu has "Mute alarms for 1 hour"; an old UI no longer stops a newer service; GNOME Shell 51
  is supported; the welcome assistant keeps earlier choices on "Back to recommended"; editing a
  rule of a disconnected sensor keeps it; the alarm log shows units and the right durations.

Minor
- CPU load clamped to 0–100 %; history writes are rolled back on error; long-history points
  centred; safer settings backup; relative XDG paths ignored; decimals for volts and watts in the
  CLI, notifications, charts and rules; translated CLI messages; GNOME extension only enabled on
  supported versions; exact sysfs paths; unique ids for disks with the same serial; durable,
  locked writes in the root helper; package build independent of the umask; save errors reported;
  thermal zones in numeric order; non-UTF-8 translation files ignored.

## 0.3.5 — 2026-11-21
Fixes all 31 findings of the 0.3.3 audit (A-01…A-03, M-01…M-07, B-01…B-14, P-01…P-07).

Reliability
- **A-01** An exception in the read loop no longer stops monitoring: the timer always reschedules,
  each alarm event is isolated, an internal error is reported once, and the systemd unit has a
  watchdog (`WatchdogSec`) fed only while readings are recent.
- **A-02** The configuration is validated field by field on load (types, ranges, alarm rules).
  Invalid rules are dropped with a notice; nothing in `config.json` can stop the service or the UI.
- **B-05** The refresh interval chosen by the user also applies to NVMe and batteries; the next
  tick is scheduled for the next real deadline instead of waking every 0.5 s.
- **B-04** One shared definition of "hot/critical" (throttling counts as hot everywhere, including
  `sensorpulse status`).
- **B-08** History: no duplicate rows after restarts (unique index and weighted upsert); old
  databases are migrated; averages are weighted by number of samples.

Security and privacy
- **A-03** `Mute` is limited to 24 h and critical alarms still notify while muted; `Quit` is only
  accepted from SensorPulse's own processes; `TestAlarm` is rate limited; every view shows
  "alarms muted until HH:MM"; the unit uses `Restart=always` plus `NoNewPrivileges`,
  `LockPersonality`, `RestrictRealtime`, `RestrictAddressFamilies`, `UMask=0077`.
- **M-01** An unreadable `config.json` is kept as `config.json.corrupt-<date>`, the last good
  `.bak` is used, the user is notified, and saves are atomic (unique temp file, `fsync`).
- **M-03** Tray icons are never written to `/tmp`: private runtime/cache directory, `O_NOFOLLOW`, 0600.
- **M-06** "Detect sensors" and "Uninstall" only use the root-owned helper in `/usr/libexec`; the
  `pkexec apt-get` fallback is gone; the helper runs with `python3 -I`.
- **M-07** History, configuration and cache are private (0700/0600).

Interface and desktops
- **M-02** Tray, floating widget, window, GNOME extension, Cinnamon applet and desklet show "no
  data" when readings are stale or the service is gone, and reconnect (waking the service) by
  watching the bus name.
- **B-02** Budgie, Pantheon, Deepin and COSMIC are recognised; the GNOME extension is only enabled
  when the user changes the option, not on every start.
- **B-03** Language: tables are translated when used; the extension and applet follow the language
  chosen in Settings; changing it restarts the interface.
- **B-10** A lightweight `ViewsUpdated` signal for panels; the Cinnamon applet blinks using opacity.
- **B-12** `StartupWMClass` and program name so Wayland associates the window with the launcher.
- **B-13** The self-restart works when started as `python3 -m sensorpulse.app`.
- **B-14** The Cinnamon applet goes to the first existing panel and the next free position.

Hardware
- **M-05** "Detect sensors": skipped on virtual machines; on laptops it asks first; success needs a
  new hwmon whose name belongs to the module; precise ACPI-conflict detection; modules loaded at
  boot are removed with the package (`remove` as well as `purge`).
- **B-01** NVML devices are only created when the GPU is bound to the `nvidia` driver (no duplicate with nouveau).
- **B-11** Sleeping GPUs are not touched during discovery; new switch "Read hard disk
  temperatures" for users who must never wake a disk.

Other
- **B-06** `sensorpulse export` uses argparse and validates the sensor id and days.
- **B-07** Markdown report cells escape `|` and line breaks.
- **B-09** Removed the dead sound path; sound players are reaped (no zombies).
- **M-04** The system autostart entry has `TryExec`, so it is ignored once the program is removed.

Packaging
- **P-01** Real maintainer and valid Debian changelog. **P-02** `md5sums`, `py3compile`/`py3clean`.
  **P-03** Fonts are no longer bundled (`Recommends: fonts-jetbrains-mono`). **P-04** DEP-5
  copyright. **P-05** `Recommends: pkexec, polkitd`. **P-07** `Installed-Size`, `set -eu`,
  reproducible timestamps.

Tests: `tests/test_audit.py` (one case per finding) and `tests/service_regress.py` (service and D-Bus).

## 0.3.4 — 2026-11-21
Compatibility and robustness review for Debian, Ubuntu, Mint and derivatives.

- Python 3.8 and 3.9 support: removed `zip(strict=True)`, the only construct that required
  Python 3.10 (the package now depends on `python3 (>= 3.8)`).
- Opening and closing the main window no longer leaves listeners behind in the data store
  (memory and CPU use grew with every open/close).
- Settings has switches for "Lock position" and "Click-through when locked". With both on, the
  floating widget ignored every click and could not be unlocked from the interface.
- Sensors are sorted naturally (temp2 before temp10) on chips with 10 or more channels.
- Budgie is detected correctly (it reports `Budgie:GNOME` and was treated as GNOME).
- Package: valid `Maintainer` field, `md5sums` included, lintian-clean control data.

## 0.3.3 — 2026-11-19
Fixes the issues found in the 0.3.1 review.

- Ryzen with an offset control temperature (1600X-1800X, 2700X, Threadripper 1000/2000): the
  real temperature (Tdie) is the reference; Tctl caused a false critical alarm while idle.
- Active alarms are closed when their rule or alarms are disabled, the rule is deleted or the
  sensor gives no data for a minute.
- Scanning for sensors and `sensorpulse status` no longer read SATA disk limits (drivetemp
  wakes the disk for that).
- Custom rules have a level (warning or critical) used consistently everywhere.
- "Drops below" and "back to normal" messages use °F when chosen and include the unit.
- The language chosen in Settings also applies to notifications and sensor names.
- "Restart service" reconnects the window even if it opened with the service down.
- Per-family TjMax rules never exceed the official value; table keys without marketing text.
- History includes the current minute; battery health no longer mixes µWh and µAh; UDisks
  never asks for a password; applet, desklet and extension cancel retries when removed.

## 0.3.2 — 2026-11-19
- The panel shows the name before each value, like the floating widget ("CPU 54°  GPU 47°"):
  Cinnamon applet, GNOME extension and tray icon.
- After a package upgrade the program restarts itself with the new version and asks Cinnamon to
  reload the applet and desklet; before, the old ones kept running until logging out.

## 0.3.1 — 2026-11-19
- Full AMD TjMax table (747 models, 661 with an official value) taken from the amd.com
  specifications catalogue and checked against 250 individual product pages; it takes
  priority over the per-family rules (e.g. Ryzen 5 5600 and Ryzen 7 5700X: 90 °C, not 95).
- Models whose official page publishes no TjMax get no value instead of a family guess.
- Matching respects model-name boundaries (3200G vs 3200GE) and old "(tm)" strings (FX, Athlon).
- Old Athlons (II, X2, 6xx) no longer get the 95 °C of the modern Athlons.

## 0.3.0 — 2026-11-19
Fixes the issues found in the 0.2.0 review and adds usability and visual improvements.

- Settings no longer lose changes after the first one (custom alarms included).
- Uninstall really deletes user data, waits for a busy apt, reports errors and has its own
  password prompt.
- Changing settings, muting or searching for sensors no longer reads SATA disks or slow sensors.
- Per-model AMD TjMax from amd.com (Ryzen laptops 95-105 °C, Ryzen 5000 desktop 90/95 °C, 7000 X3D
  89 °C…): no false critical alarms on laptops.
- Critical CPU alarm by sustained throttling; "hot while idle" uses the busiest cores; warnings at
  most every 30 min with "Not again today"; "Got it" stops repeats; rising volume; notifications
  replaced instead of stacked; reminder when mute ends; Do Not Disturb respected (GNOME,
  Cinnamon, Xfce, KDE) with a summary afterwards; custom alarms stored in °C; hidden temperatures
  still watched slowly for critical alarms; own warning and two-tone sounds.
- One display logic for every view (Cinnamon applet and desklet, GNOME extension); Cinnamon uses
  its applet and, on Wayland, the desklet; GNOME falls back to the tray icon until the extension
  is loaded.
- New Cockpit and OLED minimal styles; improved Glass; Bento window, Aurora background, Deep dark
  theme and status bar; spring needles, start-up sweep, rolling digits, breathing alarms, green
  flash on recovery; reduced-motion respected; animated assistant with a final screen.
- Sensor manager shows current values and why a sensor is hidden; choose which new sensors to show
  after "Detect sensors".
- Performance: floating HUD with fans measured at 0.3-0.4 % CPU (was 3.8 %).
- Export history to CSV/JSON; SATA power state via UDisks2 without waking disks; stable disk IDs
  with automatic migration; better sensor detection messages; more robust history.
- Packaging: English description, man pages, Debian changelog, richer metainfo.

## 0.2.0 — 2026-11-19
- Alarms that understand the processor: design limit per family (Intel from its own sensor,
  Ryzen 95 °C, 7000-series X3D 89 °C, Raspberry Pi 85 °C); a Ryzen at 95 °C under load no
  longer triggers a critical alarm.
- New warnings: CPU (Intel) or NVIDIA slowing down because of heat, "hot while idle" (critical
  if it reaches the limit with no load) and stopped fan.
- Three alarm levels (informative, warning, critical); sound is kept for critical alarms.
  During games or full-screen videos only critical alarms interrupt (X11).
- Notifications with "Mute 1 hour" and "Details" buttons.
- Persistent alarm log and up to 30 days of history (SQLite), with 24-hour, 7-day and 30-day views.
- "Detect sensors": enables motherboard and SATA disk sensors with a single password prompt.
- GNOME Shell extension (45 or later).
- Assistant: "Use recommended settings and finish" button and a sentence with the limit for your CPU.
- Anonymous hardware report for forums and `sensorpulse status [--json]` / `sensorpulse report`.
- State symbol and word next to the color; screen reader (Orca) texts.
- The window follows the system light or dark theme.
- Fixes:
  - the desklet draws text with PangoCairo (Japanese and Chinese no longer show as boxes);
  - NVIDIA on hybrid laptops: NVML is closed after each reading, read every 30 s;
  - NVIDIA: fan speed in RPM with driver 565 or later;
  - SATA disks every 30 minutes (adjustable) instead of 5;
  - Dell/ThinkPad/HP/ASUS/Mac laptops: read every 10 s, slower if a read is slow;
  - only sensors that are shown or used by alarms are read;
  - the fan animation no longer runs at 30 frames per second all the time;
  - the floating widget no longer disappears with "Show desktop" (dock window on X11);
  - notifications respect °F;
  - uninstall: asks for the password before deleting anything, uses "purge" and leaves no
    trace (settings, history, enabled applet/desklet/extension or Python cache);
  - after an update, the window restarts the old service automatically.

## 0.1.0 — 2026-11-18
First version.
- Background service (no root) that reads hwmon, thermal zones, batteries, NVIDIA (NVML), CPU and RAM.
- Never wakes up a sleeping dedicated GPU; SATA disks are only read every 5 minutes.
- Automatic alarms from hardware limits and custom alarms (above/below, delay, hysteresis),
  visual (color + blinking), desktop notification and sound; mute for 15 min / 1 h / until tomorrow.
- Panel icon (XApp, Ayatana or legacy tray), main window with cards and detailed charts,
  transparent floating widget with 5 styles (Futuristic HUD, Rings, Bars, Minimal, Glass).
- 5-step welcome assistant, settings, sensor manager (show/hide, rename, panel, floating).
- Cinnamon applet and desklet.
- Spanish, English, French, Japanese and Chinese.
