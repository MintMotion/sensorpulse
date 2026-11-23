#!/bin/sh
# Construye dist/sensorpulse_<versión>_all.deb. Solo necesita dpkg-deb.
#   packaging/build-deb.sh            (MAINTAINER="..." para cambiar el mantenedor)
set -eu
# permisos del paquete independientes de quien lo construya (con umask 077 las carpetas
# salían 0700 y el programa no arrancaba para los usuarios normales)
umask 022
cd "$(dirname "$0")/.."

VERSION=$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' sensorpulse/__init__.py)
# Mantenedor: solo «SensorPulse», sin correo (decisión del proyecto, aunque Debian pida un
# correo en este campo; lintian lo marca como aviso y se acepta)
MAINTAINER=${MAINTAINER:-"SensorPulse"}
PKG="build/sensorpulse_${VERSION}_all"
# fechas reproducibles: las del último cambio del CHANGELOG (o SOURCE_DATE_EPOCH si se define)
if [ -z "${SOURCE_DATE_EPOCH:-}" ]; then
  REL=$(sed -n 's/^## [0-9][^ ]* — \([0-9-]*\).*/\1/p' CHANGELOG.md | head -n 1)
  SOURCE_DATE_EPOCH=$(date -u -d "${REL:-2026-01-01}" +%s)
fi
export SOURCE_DATE_EPOCH

rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" dist

# programa (Python puro: vale para cualquier Python 3 >= 3.8 del sistema)
mkdir -p "$PKG/usr/share/sensorpulse"
( cd . && find sensorpulse -name "*.py" -o -name "*.po" ) | while read -r f; do
  install -Dm644 "$f" "$PKG/usr/share/sensorpulse/$f"
done
install -Dm755 bin/sensorpulse         "$PKG/usr/bin/sensorpulse"
install -Dm755 bin/sensorpulse-service "$PKG/usr/bin/sensorpulse-service"

# «Detectar sensores»: ayudante root con su política de polkit (una sola contraseña)
install -Dm755 bin/sensorpulse-detect "$PKG/usr/libexec/sensorpulse/sensorpulse-detect"
install -Dm755 bin/sensorpulse-uninstall "$PKG/usr/libexec/sensorpulse/sensorpulse-uninstall"
install -Dm644 data/io.github.mintmotion.SensorPulse.policy \
  "$PKG/usr/share/polkit-1/actions/io.github.mintmotion.SensorPulse.policy"

# servicio de usuario: systemd --user + activación D-Bus (los applets lo arrancan solos)
install -Dm644 data/sensorpulse.service "$PKG/usr/lib/systemd/user/sensorpulse.service"
install -Dm644 data/io.github.mintmotion.SensorPulse.service \
  "$PKG/usr/share/dbus-1/services/io.github.mintmotion.SensorPulse.service"

# menú, arranque con el equipo (el usuario puede desactivarlo en Ajustes) e iconos
install -Dm644 data/sensorpulse.desktop "$PKG/usr/share/applications/sensorpulse.desktop"
install -Dm644 data/sensorpulse-autostart.desktop "$PKG/etc/xdg/autostart/sensorpulse.desktop"
install -Dm644 data/sensorpulse.svg "$PKG/usr/share/icons/hicolor/scalable/apps/sensorpulse.svg"
for n in 16 22 24 32 48 64 128 256; do
  install -Dm644 "data/icons/sensorpulse-$n.png" "$PKG/usr/share/icons/hicolor/${n}x${n}/apps/sensorpulse.png"
done
install -Dm644 data/sensorpulse.svg "$PKG/usr/share/pixmaps/sensorpulse.svg"
install -Dm644 data/sensorpulse-symbolic.svg "$PKG/usr/share/icons/hicolor/symbolic/apps/sensorpulse-symbolic.svg"
install -Dm644 data/io.github.mintmotion.SensorPulse.metainfo.xml \
  "$PKG/usr/share/metainfo/io.github.mintmotion.SensorPulse.metainfo.xml"

# applet y desklet de Cinnamon (aparecen en Applets / Desklets de la configuración de Cinnamon)
for kind in applet desklet; do
  for d in data/cinnamon/$kind/*; do
    uuid=$(basename "$d")
    for f in "$d"/*; do
      install -Dm644 "$f" "$PKG/usr/share/cinnamon/${kind}s/$uuid/$(basename "$f")"
    done
  done
done

# extensión de GNOME Shell (45 o posterior)
for f in data/gnome-shell/sensorpulse@mintmotion.github.io/*; do
  install -Dm644 "$f" "$PKG/usr/share/gnome-shell/extensions/sensorpulse@mintmotion.github.io/$(basename "$f")"
done

# (la tipografía JetBrains Mono del estilo «Cabina» viene del paquete fonts-jetbrains-mono)

# sonidos propios y tabla de TjMax
for f in sensorpulse/data/sounds/*.wav; do
  install -Dm644 "$f" "$PKG/usr/share/sensorpulse/sensorpulse/data/sounds/$(basename "$f")"
done
[ -f data/tjmax_amd.csv ] && install -Dm644 data/tjmax_amd.csv "$PKG/usr/share/sensorpulse/tjmax_amd.csv"

# páginas de manual
for f in data/man/*.1; do
  mkdir -p "$PKG/usr/share/man/man1"
  # la versión sale de sensorpulse/__init__.py (VERSION), no se escribe a mano
  sed "s/@VERSION@/$VERSION/g" "$f" | gzip -9n > "$PKG/usr/share/man/man1/$(basename "$f").gz"
  chmod 644 "$PKG/usr/share/man/man1/$(basename "$f").gz"
done

# documentación
install -Dm644 README.md    "$PKG/usr/share/doc/sensorpulse/README.md"
install -Dm644 README.es.md "$PKG/usr/share/doc/sensorpulse/README.es.md"
install -Dm644 packaging/copyright "$PKG/usr/share/doc/sensorpulse/copyright"
gzip -9n -c CHANGELOG.md > "$PKG/usr/share/doc/sensorpulse/changelog.gz"
chmod 644 "$PKG/usr/share/doc/sensorpulse/changelog.gz"
# changelog en formato Debian
{
  printf 'sensorpulse (%s) unstable; urgency=medium\n\n  * See /usr/share/doc/sensorpulse/changelog.gz\n\n' "$VERSION"
  printf ' -- %s  %s\n' "$MAINTAINER" "$(date -R -d @${SOURCE_DATE_EPOCH:-$(date +%s)})"
} | gzip -9n > "$PKG/usr/share/doc/sensorpulse/changelog.Debian.gz"
chmod 644 "$PKG/usr/share/doc/sensorpulse/changelog.Debian.gz"

echo "/etc/xdg/autostart/sensorpulse.desktop" > "$PKG/DEBIAN/conffiles"

cat > "$PKG/DEBIAN/control" <<CONTROL
Package: sensorpulse
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.8), python3-gi, python3-gi-cairo, gir1.2-gtk-3.0
Recommends: gir1.2-ayatanaappindicator3-0.1, gir1.2-xapp-1.0, gir1.2-gsound-1.0, sound-theme-freedesktop, pkexec, polkitd, x11-utils, udisks2, lm-sensors, fonts-inter, fonts-jetbrains-mono
Suggests: gir1.2-gtklayershell-0.1, gnome-shell-extension-appindicator, fonts-noto-cjk
Installed-Size: $(du -sk --exclude=DEBIAN "$PKG" | cut -f1)
Maintainer: $MAINTAINER
Homepage: https://github.com/MintMotion/sensorpulse
Description: sensor monitor with visual and sound alarms
 SensorPulse watches CPU, GPU and disk temperatures, fan speeds and the
 battery. It can show them in the panel next to the clock, in a window with
 cards or floating on the desktop with a transparent background. Colors
 change as temperatures rise, and it warns with color, notifications and
 sound, using the limits of the hardware or alarms at the temperature you
 choose.
 .
 Alarms understand the processor: they use its design limit, warn when it
 slows down because of heat, when it gets hot while almost idle or when a fan
 stops. It keeps up to 30 days of history and includes a GNOME Shell
 extension, a Cinnamon applet and desklet, and an assistant that enables
 missing motherboard and disk sensors with a single password prompt.
 .
 It only reads sensors and does not need root to run. It never wakes up a
 sleeping dedicated graphics card. SSDs are read often, and hard disks at the
 interval you choose (30 minutes by default) so they can spin down.
CONTROL

# postinst: cachés del escritorio y bytecode de Python (arranque más rápido: sin compilar en
# cada inicio). py3compile/py3clean los trae el propio python3 (lo mismo que haría dh-python).
cat > "$PKG/DEBIAN/postinst" <<'SCRIPT'
#!/bin/sh
set -e
if command -v gtk-update-icon-cache >/dev/null 2>&1 && [ -f /usr/share/icons/hicolor/index.theme ]; then
  gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q /usr/share/applications || true
fi
case "$1" in
  configure)
    if command -v py3compile >/dev/null 2>&1; then
      py3compile -p sensorpulse /usr/share/sensorpulse || true
    fi
    ;;
esac
exit 0
SCRIPT

# postrm: al quitar (remove) o purgar, los módulos de sensores que activó «Detectar sensores»
# dejan de cargarse en cada arranque (el fichero lo creó este programa)
cat > "$PKG/DEBIAN/postrm" <<'SCRIPT'
#!/bin/sh
set -e
if command -v gtk-update-icon-cache >/dev/null 2>&1 && [ -f /usr/share/icons/hicolor/index.theme ]; then
  gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q /usr/share/applications || true
fi
case "$1" in
  remove|purge)
    rm -f /etc/modules-load.d/sensorpulse.conf /etc/modules-load.d/sensorpulse.conf.tmp \
      /etc/modules-load.d/.sensorpulse.*.tmp
    ;;
esac
exit 0
SCRIPT

# prerm: borrar el bytecode de Python (py3clean) y cualquier __pycache__ suelto que se cree
# si el programa se ejecuta como root
cat > "$PKG/DEBIAN/prerm" <<'SCRIPT'
#!/bin/sh
set -e
if command -v py3clean >/dev/null 2>&1; then
  py3clean -p sensorpulse || true
fi
if [ -d /usr/share/sensorpulse ]; then
  find /usr/share/sensorpulse -type d -name __pycache__ -prune -exec rm -rf {} + || true
fi
exit 0
SCRIPT
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm" "$PKG/DEBIAN/prerm"

# carpetas 0755 pase lo que pase (install -D las crea con la umask)
find "$PKG" -type d -exec chmod 755 {} +

# fechas de todos los ficheros = fecha de la versión (el .deb sale idéntico en cada construcción)
find "$PKG" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +

# sumas de verificación (debsums / lintian las esperan)
( cd "$PKG" && find usr etc -type f -print0 | sort -z | xargs -0 md5sum > DEBIAN/md5sums )
chmod 644 "$PKG/DEBIAN/md5sums"

dpkg-deb --build --root-owner-group -Zxz "$PKG" "dist/sensorpulse_${VERSION}_all.deb"
echo "Paquete listo: dist/sensorpulse_${VERSION}_all.deb"
