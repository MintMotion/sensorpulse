# Reproducciones de la revisión 0.3.6

Cada guion recibe la carpeta de las fuentes a probar (la 0.3.5 o la 0.3.6) y se ejecuta dentro de
`dbus-run-session`:

    dbus-run-session -- python3 tests/repro/clock_jump.py   /ruta/fuentes   # C1/C2: salto de reloj
    dbus-run-session -- python3 tests/repro/notif_block.py  /ruta/fuentes   # C3: demonio colgado
    dbus-run-session -- python3 tests/repro/restart_orig.py /ruta/fuentes   # I16: servicio congelado

`../vm-systemd/` tiene la prueba con systemd real (servicio de usuario en Ubuntu 24.04): `run.sh`
se ejecuta como root al arrancar la VM y `client.py` hace de interfaz conectada.
