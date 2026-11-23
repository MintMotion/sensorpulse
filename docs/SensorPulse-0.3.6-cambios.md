# SensorPulse 0.3.6: fallos encontrados y reparados

Revisión completa de la 0.3.5 (código Python, servicio, interfaz, extensión de GNOME, applet y
desklet de Cinnamon, ayudante root, empaquetado y traducciones). **42 fallos, todos reparados:
5 críticos, 20 importantes y 17 secundarios.**

Cada fallo tiene una prueba automática que **falla con el código de la 0.3.5 y pasa con la
0.3.6** (columna «Prueba»). Las pruebas se ejecutaron contra los dos árboles de fuentes:
`SP_ROOT=<0.3.5> python3 tests/test_036.py` da 30 fallos de 31 (la única que pasa es la
comprobación de formatos de las traducciones, que se añadió para no romperlas al cambiar
mensajes). En la 0.3.6 dan 0 fallos de 31.

Archivos: `sensorpulse_0.3.6_all.deb`, `sensorpulse-0.3.6-codigo.zip` y este documento.

---

## 1. Críticos

| ID | Fallo en la 0.3.5 | Reproducción con la 0.3.5 | Arreglo en la 0.3.6 | Prueba |
|---|---|---|---|---|
| **C1** | El calendario de lecturas usaba el reloj de pared (`time.time()`). Si el reloj del sistema saltaba hacia atrás (corrección de NTP, cambio de hora a mano, reanudar con la hora mal), **no se leía ningún sensor** durante el tiempo del salto. La foto no cambiaba, así que la ventana, el panel y los applets seguían enseñando la última temperatura como si fuera actual. | Servicio real con un sysfs falso: el reloj se atrasa 1 h y la CPU sube a 113 °C. A los 25 s el servicio sigue publicando 54,2 °C y no salta ninguna alarma (`tests/repro/clock_jump.py`). | Calendario del motor (`_next`, `_ch_next`, lentitud, freno térmico) y temporizador del servicio con reloj monótono. La hora de pared solo fecha los datos. | `test_c1_…` |
| **C2** | Los retardos de las alarmas, la histéresis temporal, las repeticiones del sonido, las pausas entre avisos, el «sin datos» y el silencio también usaban el reloj de pared. Con un salto atrás **una alarma crítica no saltaba** hasta recuperar el tiempo del salto, y el silencio se alargaba. Con un salto adelante, saltaban alarmas antes de su retardo. | Con 99 °C y un retardo de 1 s, tras atrasar el reloj 1 h la alarma no salta. Un silencio de 1 min con el reloj atrasado 1 h seguía activo a los 61 min. | `AlarmEngine.evaluate` mide en reloj monótono y fecha los eventos con la hora de pared. El fin del silencio también es monótono; `muted_until` se calcula al publicar con lo que queda. | `test_c2_alarm_delay_…`, `test_c2_mute_…` |
| **C3** | Las notificaciones usaban libnotify, cuyo `show()` **espera la respuesta** del demonio de notificaciones. Con un demonio colgado, el servicio entero se paraba ~25 s por cada aviso: no leía sensores, no evaluaba alarmas y no respondía por D-Bus. Varias alarmas seguidas podían superar el `WatchdogSec` y systemd lo mataba. La 0.3.5 lo reconocía como límite. | Demonio falso que nunca contesta: `GetSnapshot` tarda **24,73 s** tras un `TestAlarm` (0,00 s en la 0.3.6). | Notificaciones por D-Bus con llamadas asíncronas (`Notify`, `CloseNotification`, señales `ActionInvoked` y `NotificationClosed`). Se mantienen el reemplazo por regla, los botones y el cierre. Ya no se necesita `gir1.2-notify-0.7`. | `test_036_c3_…` y `test_036_notification_actions_work_async` (`service_regress.py`) |
| **C4** | Un `config.json` muy anidado (`[[[[…]]]]`) provocaba un `RecursionError` sin capturar **al importar `i18n`**. Se caían el servicio (en bucle con `Restart=always`), la interfaz y la CLI. | `import sensorpulse.i18n` → `RecursionError`. | `i18n._configured_language`, `config.load` y la lectura de `.bak` capturan `RecursionError`. Un idioma que no sea texto se ignora. | `test_c4_…` |
| **C5** | Ajustes escribe `panel_count`, `colorblind`, `animate_fans`, `language`, `ext_applied`, `floating.panel_bg` y `alarms.warn_sound_name`, pero `sanitize()` no las validaba. Con `"panel_count": "x"`, `views.build` lanzaba `ValueError`: **el panel, la extensión de GNOME y el applet se quedaban sin datos** y la bandeja fallaba. | `views.build(cfg)` → `ValueError: invalid literal for int()`. | Las claves pasan a `DEFAULTS` con tipo, rango u opciones (idioma, sonidos). `ext_applied` solo se admite como booleano, y `views.panel_count()` tolera cualquier valor. | `test_c5_…` |

## 2. Importantes

| ID | Fallo en la 0.3.5 | Reproducción / evidencia | Arreglo | Prueba |
|---|---|---|---|---|
| **I1** | `config.load()` trataba **cualquier** `OSError` (sin permiso, error de E/S, una carpeta) como fichero dañado y lo renombraba a `.corrupt-FECHA`, así que el usuario perdía sus ajustes. Además, `save()` sobrescribía un `config.json` que no se podía leer. | Con `config.json` como carpeta, se aparta como «corrupto». | Solo el contenido inválido se aparta. Ante un `OSError` se usa el `.bak` o los valores por defecto, `save()` no sobrescribe el fichero y se avisa con una notificación nueva traducida. | `test_i1_…` |
| **I2** | Para saber si los datos estaban caducados se restaba al reloj de pared la hora de la foto. Con un salto atrás, **un servicio congelado no se detectaba nunca**: la ventana, el panel y el flotante seguían en verde. Ocurría igual en la extensión de GNOME, el applet y el desklet (`Date.now()`). | Almacén con `stale_after=1`: tras atrasar el reloj y esperar 1,4 s no detecta nada. | Se cuenta el tiempo desde que **llegó** la última foto, con reloj monótono: `time.monotonic()` en Python y `GLib.get_monotonic_time()` en los tres JS. | `test_i2_…`, `test_js_…` |
| **I3** | Si el servicio desaparecía, la interfaz lo volvía a arrancar a los 3 s, y el applet y el desklet al instante, por activación D-Bus. Así peleaban con un `systemctl --user stop` hecho a propósito (también al desinstalar). | Revisión del código. En la VM con systemd real se compara la 0.3.5 con la 0.3.6 (§5). | Si el servicio iba bien y lo gestiona systemd (se mira su cgroup), no se rearranca: si se cae lo reinicia systemd con `Restart=always`, y si no lo hace es que se paró a propósito. Sin systemd se sigue rearrancando. La extensión, el applet y el desklet usan `NO_AUTO_START` cuando ya lo han visto funcionar. | `test_js_…`, VM (§5) |
| **I4** | Si el servicio no estaba listo en 4 s al abrir la interfaz (inicio de sesión lento), aparecía un error **modal** y la interfaz se quedaba **vacía para siempre**: sin cliente, sin suscripción y sin vigilancia del bus. | El almacén con `ensure_service` fallido no recibe datos cuando el servicio aparece. | `Store.connect()` crea siempre el cliente, vigila el nombre del bus y reintenta con pausas crecientes. El aviso ya no es modal. | `test_036_store_connects_…` |
| **I5** | NVIDIA: si NVML no tenía el *handle* de la tarjeta, `_read_nvidia` volvía sin borrar los valores. Se mostraba la última temperatura como actual. | Con una GPU sin *handle* se sigue mostrando 66 °C. | Los valores pasan a «sin dato». | `test_i6_nvidia_…` |
| **I6** | «Buscar sensores» (`rescan`) perdía las cabeceras de ventilador ocultas por estar a 0 RPM al arrancar (vuelven a salir vacías) y el intervalo de los sensores lentos. | Tras `rescan()`, `fan3` deja de estar oculto. | Se conservan `advanced`/`note="zero_rpm"` y `slow`/`interval`. | `test_i7_rescan_…` |
| **I7** | El historial SQLite guardaba en cada pasada **todos** los canales con valor, también los que no se habían leído (el disco SATA cada 30 min, los sensores lentos). Salían líneas planas falsas y medias con muestras repetidas. | Disco leído 1 vez → 3 muestras en el historial tras 4 pasadas. | El motor apunta qué ha leído en cada pasada (`last_read`). El historial en memoria y el SQLite solo guardan eso. | `test_i8_history_…` |
| **I8** | Con la base del historial dañada **al arrancar**, el servicio se quedaba sin historial para siempre. En marcha solo reconocía «malformed» (no «file is not a database»), dejaba los `-wal`/`-shm` viejos junto a la base nueva y pisaba copias `.corrupt` anteriores. | Fichero basura en `history.sqlite`: `svc.db is None`. | `historydb.open_db()` aparta la base dañada y crea otra. `quarantine()` mueve también `-wal` y `-shm` con fecha. `is_corruption()` reconoce los dos mensajes. | `test_i9_corrupt_…` |
| **I9** | Comprobar «No molestar» o la pantalla completa **bloqueaba el bucle** (`xprop` dos veces, `xfconf-query` y una llamada D-Bus síncrona). La llamada `Inhibited` sin `NO_AUTO_START` podía **arrancar** un demonio de notificaciones. La clave `show-banners` de GNOME se aplicaba también en Xfce, KDE o MATE, y aplazaba allí avisos sin motivo. | Con `XDG_CURRENT_DESKTOP=XFCE` y `show-banners=false` (GSettings en memoria), `_dnd()` devuelve `True`. | Comprobación asíncrona (`Gio.Subprocess`, con tiempo límite, y D-Bus con `NO_AUTO_START`), en caché 5 s. Los avisos no críticos esperan su resultado en orden. Cada clave solo vale en su escritorio. | `test_i9_gnome_dnd_…` |
| **I10** | Cualquier script llamado `sensorpulse` podía parar el servicio con `Quit`, porque solo se miraba el nombre en `cmdline`. La propia prueba de la 0.3.5 lo comprobaba como si fuera correcto. | Un script `/tmp/…/sensorpulse` para el servicio. | `trusted_caller()` exige el mismo UID, un intérprete Python real (`/proc/PID/exe`) y el programa de verdad: los ejecutables instalados o `-m sensorpulse.*` desde la carpeta del paquete. | `test_i11_trusted_caller`, `test_a03_…` |
| **I11** | UDisks2: si no encontraba el disco (UDisks aún sin arrancar al iniciar sesión), lo recordaba **para siempre**. Ese disco ya no se comprobaba y se leía despertándolo. Además solo reconocía `0x00` como reposo, no `0x01`, `0x40` ni `0x41` (ATA/ACS). | Sin reintento tras el primer fallo. Con el estado `0x01` se lee el disco. | El «no encontrado» caduca a los 10 min y un `UnknownObject` vuelve a buscar el disco. Se reconocen todos los estados de reposo. Reloj monótono. | `test_i12_udisks_…` |
| **I12** | Desklet: el latido de alarma crítica dependía del estado de la **temperatura**, no del nivel de la alarma (una regla crítica propia latía como aviso). El estilo «anillos» no mostraba la unidad en °F y los voltios salían sin decimales. | Revisión del código. | `alarm_level` como en el applet y la extensión, °F y `toFixed(2)` para voltios. | `test_js_…` |
| **I13** | `sata_disks_without_temp` contaba los discos y lectores **USB** (son `sd*` y no removibles), así que el asistente proponía «Detectar sensores» sin motivo. | Con un USB SanDisk: `['Ultra USB 3.0']`. | Solo los discos con vendedor `ATA` (lo mismo que comprueba el ayudante root). | `test_i14_usb_…` |
| **I14** | El menú del indicador Ayatana (GLib) no tenía «Silenciar alarmas 1 hora», que sí estaba en los menús GTK. | El menú solo tenía `window`, `toggle-floating`, `settings` y `quit`. | Nueva acción `app.mute-hour`, la misma en los dos menús. | `test_i15_…` |
| **I15** | `ensure_service` paraba el servicio si su versión era **distinta**, también si era más nueva. Una interfaz antigua tras actualizar paraba el servicio nuevo cada vez que se reconectaba. `systemctl --user start` no tenía tiempo límite. | Con un servicio 99.0.0 lo para. | Solo se para un servicio **más antiguo**, uno congelado se deja y `systemctl` tiene tiempo límite. | `test_i16_newer_…` |
| **I16** | «Reiniciar el servicio» no servía con el servicio **congelado**: solo le pedía `Quit`, que un proceso parado no atiende. Tardaba 12 s y lo dejaba igual. | SIGSTOP → `restart_service()` → 12,0 s y sigue sin responder (`tests/repro/restart_orig.py`). En la 0.3.6: 2,2 s y responde. | Con systemd, `systemctl --user restart`. Sin systemd, SIGTERM y después SIGKILL al dueño del nombre y nuevo arranque. | `test_036_restart_button_…` |
| **I17** | La extensión de GNOME no declaraba **GNOME 51** (publicado en septiembre de 2026; Ubuntu 26.10): GNOME no la cargaba. | `shell-version` terminaba en 50. Según la guía oficial de migración a 51, solo cambia que `disable()` no puede ser asíncrono, y el de la extensión es síncrono. | Se añade `"51"`. | `test_gnome_extension_…` |
| **I18** | Asistente: «Volver a lo recomendado» volvía a leer el disco y **perdía todo lo elegido** en las páginas anteriores, que aún no estaba guardado (dónde mostrar, estilo…). | Interfaz bajo Xvfb: el flotante y el estilo elegidos vuelven a los de antes. | `show_wizard(reload=False)`. | `gui_regress.py` X4 |
| **I19** | Al editar la regla de un sensor desconectado, el desplegable salía vacío y «Guardar» **descartaba los cambios sin decir nada**. | Interfaz bajo Xvfb. | Se añade el sensor como «no disponible» y la regla se guarda. | `gui_regress.py` X3 |
| **I20** | El registro de alarmas mostraba el valor en °C y **sin unidad** aunque se hubiera elegido °F. A cada alarma le ponía la duración de la **última** vez que se cerró esa regla. | Interfaz: «95 · 5 min» y «90 · 5 min» en vez de «203°F · 5 min» y «194°F · 1 min». | Cada `fired` se empareja con su `cleared`, y el valor se muestra en la unidad elegida y con unidad. | `gui_regress.py` X1 |

## 3. Secundarios

| ID | Fallo en la 0.3.5 | Arreglo | Prueba |
|---|---|---|---|
| **S1** | La carga de CPU no estaba acotada: con contadores que bajan (una CPU desconectada) salía **−5,3 %** o más del 100 %, y una línea rara de `/proc/stat` lanzaba `ValueError`. | Acotada entre 0 y 100 %, con lecturas incompletas ignoradas. | `test_m1_…` |
| **S2** | Historial: un error a mitad de la escritura (disco lleno) dejaba la transacción abierta y el reintento sumaba otra vez las filas ya actualizadas (n=2 con una sola muestra). | `rollback()` y los acumuladores se vacían solo si la escritura va bien. | `test_m2_…` |
| **S3** | La gráfica de historial largo movía los puntos 30 s fijos, aunque cada punto agrupe de 1 min a 1 h. | `bucket_step()` y el punto en el centro de su grupo. | Revisión del código |
| **S4** | `.bak` creado con `copyfile` y `chmod` después (0644 durante un instante), cambio de nombre sin `fsync` de la carpeta y `XDG_CONFIG_HOME`/`XDG_STATE_HOME` relativos aceptados (la especificación manda ignorarlos). | `.bak` escrito ya con 0600, `fsync` de la carpeta y rutas relativas ignoradas. | `test_m4_…` |
| **S5** | CLI: `%.0f` para todo («1 V» para 1,23 V, «5 W» para 5,4 W), y los mensajes de `export` solo en español. | Decimales por tipo y mensajes traducidos a los 4 idiomas. | `test_m5_…` |
| **S6** | Avisos: «GPU has reached **6 W** (limit **6 W**)» para 5,8 W con un límite de 5,5 W. | Números con los decimales que hacen falta. Plantillas y `.po` actualizados. | `test_m6_…` |
| **S7** | Gráfica de detalle: eje con `%d` («1 1 1 1 1» para voltios), lectura accesible con `%.0f` y margen mínimo de 5 para todo (voltios y vatios planos). | Decimales según el margen y margen mínimo por tipo. | Revisión del código + `gui_smoke` |
| **S8** | `gnome_ext_set(True)` activaba la extensión en un GNOME que no figura en su `metadata.json`. Además, `ext_applied` se apuntaba como hecho aunque fallara. | Se comprueba la versión (`ShellVersion` por D-Bus o `gnome-shell --version`) y solo se apunta si ha funcionado. | Revisión del código |
| **S9** | Las versiones del applet y del desklet seguían en 0.1.0, y la de la extensión en 0.3.5. | Todas en 0.3.6, con una prueba que exige que coincidan. | `test_xlet_versions_…` |
| **S10** | `sysfs.rel()` comparaba sin separador: `/tmp/x/sys2/a` quedaba como `/2/a`. | Comparación con separador. | `test_m11_…` |
| **S11** | Dos discos con el mismo número de serie (clones, cajas USB) tenían el **mismo id**: el segundo pisaba los canales del primero. | Sufijo `-2`, `-3`… | `test_m12_…` |
| **S12** | El ayudante root escribía `/etc/modules-load.d/sensorpulse.conf` sin `fsync` (tras un corte podía quedar vacío) y con un `.tmp` fijo que compartían dos ejecuciones a la vez. | Temporal único, `fsync` del fichero y de la carpeta, y bloqueo `flock`. `postrm` borra también los temporales. | `test_detect_helper_…` |
| **S13** | `build-deb.sh` **fallaba** con una umask restrictiva (077 o 027): `control directory has bad permissions 700`. | `umask 022` y carpetas a 0755. Con umask 077 sale el mismo paquete, byte a byte, que con 022. | Construcción comparada |
| **S14** | Guardar los ajustes con un error de disco (lleno, solo lectura) lanzaba una excepción sin capturar en cada cambio de Ajustes, sin aviso. | Se captura y se avisa una vez con un mensaje traducido. | `test_s14_…` |
| **S15** | Reglas de potencia sin decimales (incremento de 1 y 0 decimales) y retardo máximo de 600 s en el editor, aunque la configuración admite 3600: al editar una regla de más de 600 s se recortaba. | Medio vatio, un decimal y hasta 3600 s. | `gui_regress.py` X2 |
| **S16** | Las zonas térmicas salían en orden de texto (`thermal_zone10` antes que `thermal_zone2`). | Orden numérico, como los hwmon. | Revisión del código |
| **S17** | Un `.po` que no esté en UTF-8 lanzaba `UnicodeDecodeError` al importar `i18n`. | Se ignora el fichero. | `test_s17_…` |

## 4. Lo que se revisó y no era un fallo

- `this.actor.add()` en el applet de Cinnamon: Cinnamon añade `add()` a `St.BoxLayout` en
  `js/ui/environment.js` (`_patchContainerClass`), así que funciona.
- Unidad de systemd, activación D-Bus, autoarranque, política de polkit, metainfo y `.desktop`:
  sin errores (`desktop-file-validate`, `appstreamcli validate`, `xmllint`).
- `vermin`: el código sigue siendo compatible con Python 3.8 o posterior.
- El aviso `PyGIWarning` que sale al importar `sensorpulse.ui.store` en un proceso sin GTK 3
  cargado no afecta al programa: `app.py` fija GTK 3.0 antes de importar el almacén.
- El «primer aviso silencioso» que habría aparecido al pasar los avisos repetitivos al reloj
  monótono (`last_fired = 0`) se evitó antes de que existiera: ahora es `None` (nunca). No es un
  fallo de la 0.3.5.

## 5. Cómo se ha comprobado

**Pruebas del proyecto** (todas pasan en la 0.3.6):
- `test_core.py` 14/14 y `test_audit.py` 22/22. `test_core` se ha adaptado en un punto: el fin
  del silencio ahora se simula con los dos relojes.
- `test_036.py` 31/31 (nuevo; 30 fallan en la 0.3.5).
- `service_regress.py` 12/12. Hay 4 pruebas nuevas, y `test_a03` se ha cambiado: el script falso
  ahora se rechaza y un `-m sensorpulse.*` legítimo se acepta.
- `gui_smoke.py` y `gui_regress.py` bajo Xvfb, con 6 comprobaciones nuevas.

**Distribuciones** (en cada una: instalar la 0.3.5, actualizar a la 0.3.6, ejecutar la CLI
instalada, todas las pruebas con su Python, el servicio instalado por activación D-Bus, la
interfaz bajo Xvfb y la purga):

| Distribución | Python | Resultado |
|---|---|---|
| Ubuntu 22.04 | 3.10 | todo OK (*) |
| Debian 12 | 3.11 | todo OK |
| Debian 13 | 3.13 | todo OK |
| Linux Mint 22 | 3.12 | todo OK |

(*) En Ubuntu 22.04 falló al principio una prueba **del propio arnés**. Con el servicio
instalado, el `-m sensorpulse.*` legítimo se lanzaba desde las fuentes y no desde
`/usr/share/sensorpulse`, y el servicio lo rechazaba, que es lo correcto. Se corrigió la prueba
y se repitió con el paquete definitivo.

**Paquete definitivo en las cuatro distribuciones**: instalar sobre la 0.3.5,
`service_regress.py` con el servicio instalado (12/12), `test_036.py` (31/31) y purga. Todo OK.

**systemd real** (Ubuntu 24.04 en QEMU, servicio de usuario, primero la 0.3.5 y luego la 0.3.6):
| Prueba | 0.3.5 | 0.3.6 |
|---|---|---|
| Activación por D-Bus (`SystemdService=`) | OK | OK |
| `Restart=always` tras `kill -9` | rearranca (NRestarts=1) | rearranca (NRestarts=1) |
| `WatchdogSec=90` con el servicio congelado (SIGSTOP) | systemd lo reinicia a los 94 s («Watchdog timeout») | igual, a los 94 s |
| `systemctl --user stop` con un cliente de la interfaz conectado | **a los 25 s vuelve a estar en marcha** (lo rearranca la interfaz: I3) | **sigue parado**; la interfaz lo muestra «sin datos» |
| `systemctl --user start` después | — | la interfaz vuelve a recibir datos sola |

Estas pruebas se hicieron con el paquete anterior a la última reconstrucción. El paquete
definitivo solo cambia en `changelog.gz` (y su línea de `md5sums`), comprobado con
`diff -r` de los dos paquetes desempaquetados.

**Paquete**: `lintian` sin avisos y construcción reproducible (mismo .deb byte a byte, también
con umask 077). La purga no deja ficheros del paquete ni `/etc/modules-load.d/sensorpulse.conf`.

## 6. Límites (lo que no se ha podido probar aquí)

- La extensión de GNOME, el applet y el desklet no se han ejecutado dentro de GNOME Shell ni de
  Cinnamon. Los cambios de JS pasan `node --check` y una prueba que exige el reloj monótono,
  `NO_AUTO_START` y `alarm_level`. La compatibilidad con GNOME 51 se basa en la guía oficial de
  migración, no en una prueba.
- La parte asíncrona de «No molestar» o pantalla completa con `xprop` y `xfconf-query` se ha
  comprobado por revisión del código y con la prueba de GNOME frente a Xfce, no en un escritorio
  Xfce real.
- UDisks2, NVML y el ayudante root se han probado con dobles (sin discos SATA ni NVIDIA reales).
- No hay Python 3.8 ni 3.9 para probar: se ha comprobado con `vermin`.
- `appstreamcli` sin red no comprueba que existan las URL de las capturas.
