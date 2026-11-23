#!/bin/bash
# SensorPulse en Ubuntu 24.04 con systemd real (servicio de usuario): 0.3.5 y luego 0.3.6.
L=/mnt/rtcmd/sp.log
exec >> $L 2>&1
systemctl mask --runtime --now serial-getty@ttyS0.service >/dev/null 2>&1
say() { echo "$*"; echo "$*" > /dev/ttyS0 2>/dev/null || true; sync; }
trap 'journalctl -b --no-pager > /mnt/rtcmd/journal.txt 2>&1; runuser -u ubuntu -- env XDG_RUNTIME_DIR=/run/user/1000 journalctl --user -b --no-pager > /mnt/rtcmd/journal-user.txt 2>&1; sync' EXIT
say "SP-START $(date) $(systemctl --version | head -1)"
id ubuntu >/dev/null 2>&1 || useradd -m -u 1000 -s /bin/bash ubuntu
loginctl enable-linger ubuntu
for i in $(seq 1 120); do [ -S /run/user/1000/bus ] && break; sleep 1; done
say "bus de usuario: $(ls -la /run/user/1000/bus 2>&1)"
U="runuser -u ubuntu -- env XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus"
show() { $U systemctl --user show sensorpulse.service -p ActiveState -p SubState -p MainPID -p NRestarts -p Result | tr '\n' ' '; }
pid() { $U systemctl --user show sensorpulse.service -p MainPID --value; }
for V in 0.3.5 0.3.6; do
  say "=================== $V"
  dpkg --force-depends -i /mnt/rtcmd/sensorpulse_${V}_all.deb >/tmp/dpkg.log 2>&1; say "dpkg rc=$?"
  $U systemctl --user daemon-reload
  $U systemctl --user stop sensorpulse.service 2>/dev/null; sleep 2
  say "1) activación por D-Bus: $($U busctl --user call io.github.mintmotion.SensorPulse /io/github/mintmotion/SensorPulse io.github.mintmotion.SensorPulse GetVersion 2>&1)"
  sleep 3; say "   $(show)"
  P=$(pid); kill -9 $P; sleep 8
  say "2) Restart=always tras kill -9 de $P: $(show)"
  P=$(pid); T0=$(date +%s); kill -STOP $P
  for i in $(seq 1 40); do sleep 5; [ "$(pid)" != "$P" ] && [ "$(pid)" != "0" ] && break; done
  say "3) watchdog tras SIGSTOP de $P: nuevo PID $(pid) a los $(( $(date +%s) - T0 )) s · $(show)"
  say "   journal: $($U journalctl --user -u sensorpulse --since "-3min" --no-pager 2>/dev/null | grep -i -m2 'watchdog' | tr '\n' ' ')"
  $U python3 /mnt/rtcmd/client.py 75 8 30 70 > /tmp/client-$V.log 2>&1 &
  CL=$!
  sleep 10
  $U systemctl --user stop sensorpulse.service
  say "4) stop a propósito con un cliente de la interfaz conectado: $(show)"
  sleep 25
  say "   25 s después: $(show)  (debe seguir parado)"
  $U systemctl --user start sensorpulse.service; sleep 6
  say "   start manual: $(show)"
  wait $CL
  say "   cliente: $(tr '\n' ' ' < /tmp/client-$V.log)"
done
say "SP-END $(date)"
