# Historial de cambios

## 0.3.8 — 23/11/2026
- **Applet de Cinnamon:** no se cargaba («Error importing applet.js», aviso de Cinnamon al
  iniciar sesión). Asignaba `this._panelHeight`, que en Cinnamon es una propiedad de solo lectura
  de la clase `Applet`. Ahora usa un nombre propio (`_spPanelHeight`).
- Las pruebas de JavaScript simulan esa propiedad de solo lectura como la tiene Cinnamon, así que
  ese error ya no puede pasar desapercibido.
- `tests/gui_037.py` pide GTK 3 antes de cargarlo; si estaba instalado GTK 4, fallaba al arrancar.

## 0.3.7 — 22/11/2026
Peticiones de mejora y una revisión de todo lo afectado. Detalle y pruebas de cada cambio:
docs/SensorPulse-0.3.7-cambios.md.

Panel
- El applet de Cinnamon se coloca **a la izquierda de los iconos del sistema y el reloj**, en el
  espacio libre (la 0.3.6 lo ponía a la derecha del todo). Si ya estaba allí, se mueve una vez;
  si el usuario lo coloca en otro sitio, se respeta. La extensión de GNOME, igual.
- Hasta **8 valores** en la barra (antes 3, y 3 por defecto en vez de 2). Los que no caben, y los
  marcados «Al hacer clic», salen al pulsar el panel.
- Aspecto del panel a elegir: **nombre y número** o **indicadores tipo cuentakilómetros**, con o
  sin nombres.
- Ajustes › «En una ventana» desactivado **cierra la ventana**; cambiar cualquier otro ajuste ya
  no la saca por encima de Ajustes.

Asistente, nombres y sonidos
- El asistente explica y deja elegir qué va al panel, qué solo al hacer clic y qué al widget del
  escritorio, y poner **nombre propio** a cada sensor. También en Ajustes y, con el botón
  derecho, desde el applet, la extensión, el desklet y el widget flotante.
- **15 sonidos** (antes 6), entre ellos una **alarma de central nuclear**, un ping de sonar,
  pitidos digitales y una campanilla suave; botón **▶** junto a cada selector (método D-Bus
  `PlaySound`, sin notificación). El asistente ofrece todas las opciones de sonido de Ajustes,
  y Ajustes gana el selector del sonido de los avisos.

Discos
- Se detecta el tipo de cada disco: **NVMe y SSD SATA** se leen a menudo (no afecta a su reposo
  ni a su vida útil); en los **discos duros** decide el usuario (de 2 min a 2 h) y, si están
  templados, se vigilan cada 2 minutos hasta que se enfríen. También en el asistente.

Consumo
- Widget flotante «Cristal» con la gráfica llena: **de 8,6 % a 0,4 % de un núcleo** en dibujar
  (proceso entero: de 9,1 % a 0,8 %). Fotograma guardado en caché, solo se repintan las
  hélices, minigráfica con un solo trazo y degradados en caché, hélices a 5 fps y la ventana
  principal solo redibuja las tarjetas que se mueven.

Idiomas
- **Italiano, alemán y portugués** (además de español, inglés, francés, japonés y chino), también
  en el applet, la extensión, el desklet, el lanzador, polkit y AppStream.

Otros
- Interruptores de Ajustes centrados (con una descripción de dos líneas salían estirados).
- Mantenedor del paquete: «SensorPulse», sin correo.

## 0.3.6 — 22/11/2026
Corrige 42 fallos encontrados en una revisión completa de la 0.3.5 (5 críticos, 20 importantes y
17 secundarios). Detalle, reproducción y comprobación de cada uno: docs/SensorPulse-0.3.6-cambios.md.

Críticos
- **C1/C2** Lecturas, retardos de alarma, repeticiones, pausas y silencio se miden con el reloj
  monótono. En la 0.3.5 un salto atrás del reloj (NTP, cambio manual) congelaba lecturas y
  alarmas tanto tiempo como el salto, mientras la ventana, el panel y los applets seguían
  mostrando el último valor como actual.
- **C3** Las notificaciones se envían por D-Bus de forma asíncrona (sin libnotify): un demonio de
  notificaciones colgado paraba todo el servicio ~25 s por cada aviso.
- **C4** Un `config.json` anidado a mucha profundidad ya no tumba todos los procesos al arrancar.
- **C5** Se validan todas las claves que escribe Ajustes (`panel_count`, `colorblind`,
  `animate_fans`, `language`, `ext_applied`, `floating.panel_bg`, `alarms.warn_sound_name`).

Importantes
- Un `config.json` ilegible (permisos, error de E/S) ya no se aparta como «dañado» ni se
  sobrescribe; la ventana, el panel, la extensión de GNOME, el applet y el desklet detectan un
  servicio congelado por el tiempo desde la última foto recibida; un servicio parado a propósito
  (`systemctl --user stop`) ya no lo vuelven a arrancar la interfaz ni las integraciones; la
  interfaz se conecta sola si el servicio aparece tarde; «Reiniciar el servicio» funciona con el
  servicio congelado; los valores de NVIDIA se borran si NVML pierde la tarjeta; «Buscar sensores»
  conserva ocultas las cabeceras de ventilador vacías; el historial solo guarda lo leído; una base
  de historial dañada se sustituye al arrancar; la comprobación de «No molestar» nunca bloquea ni
  arranca un demonio de notificaciones; solo los programas reales de SensorPulse pueden parar el
  servicio por D-Bus; UDisks2 se vuelve a consultar y se reconocen todos los estados de reposo ATA;
  el desklet usa el nivel de la alarma y muestra bien °F y voltios; los discos USB no se proponen
  para la detección SATA; el menú del indicador Ayatana (GLib) tiene «Silenciar 1 hora»; una
  interfaz antigua ya no para un servicio más nuevo; GNOME Shell 51; el asistente conserva lo
  elegido al «Volver a lo recomendado»; editar una regla de un sensor desconectado la conserva; el
  registro de alarmas muestra unidades y la duración correcta.

Secundarios
- Carga de CPU limitada a 0–100 %; escrituras del historial deshechas si fallan; puntos del
  historial largo centrados; copia de los ajustes más segura; rutas XDG relativas ignoradas;
  decimales para voltios y vatios en la CLI, avisos, gráficas y reglas; mensajes de la CLI
  traducidos; la extensión de GNOME solo se activa en versiones compatibles; rutas sysfs exactas;
  ids únicos para discos con el mismo número de serie; escrituras duraderas y con bloqueo en el
  ayudante root; paquete independiente de la umask; errores al guardar avisados; zonas térmicas
  en orden numérico; ficheros de traducción no UTF-8 ignorados.

## 0.3.5 — 21/11/2026
Corrige los 31 hallazgos de la auditoría de la 0.3.3 (A-01…A-03, M-01…M-07, B-01…B-14, P-01…P-07).

Fiabilidad
- **A-01** Una excepción en el bucle de lectura ya no detiene la vigilancia: el temporizador siempre se
  reprograma, cada evento de alarma está aislado, el error interno se avisa una vez y la unidad de
  systemd tiene vigilante (`WatchdogSec`) que solo se alimenta mientras las lecturas son recientes.
- **A-02** La configuración se valida campo a campo al cargarla (tipos, rangos, reglas). Las reglas
  inválidas se descartan con aviso; nada de lo que haya en `config.json` puede parar el servicio ni la interfaz.
- **B-05** El intervalo elegido también se aplica a NVMe y baterías; el siguiente ciclo se programa
  para el próximo vencimiento real en vez de despertar cada 0,5 s.
- **B-04** Una sola definición de «caliente/crítico» (el *throttling* cuenta como caliente en todas
  partes, también en `sensorpulse status`).
- **B-08** Historial: sin filas duplicadas tras reinicios (índice único y upsert ponderado); las bases
  antiguas se migran; las medias se ponderan por número de muestras.

Seguridad y privacidad
- **A-03** `Mute` limitado a 24 h y las alarmas críticas avisan aunque haya silencio; `Quit` solo lo
  acepta un proceso de SensorPulse; `TestAlarm` con límite de frecuencia; todas las vistas muestran
  «alarmas silenciadas hasta HH:MM»; la unidad usa `Restart=always` y `NoNewPrivileges`,
  `LockPersonality`, `RestrictRealtime`, `RestrictAddressFamilies`, `UMask=0077`.
- **M-01** Un `config.json` ilegible se conserva como `config.json.corrupt-<fecha>`, se usa la última
  copia buena `.bak`, se avisa al usuario y el guardado es atómico (temporal único, `fsync`).
- **M-03** Los iconos de la bandeja nunca se escriben en `/tmp`: carpeta privada, `O_NOFOLLOW`, 0600.
- **M-06** «Detectar sensores» y «Desinstalar» solo usan el ayudante de `/usr/libexec` (de root); se
  quita la alternativa `pkexec apt-get`; el ayudante se ejecuta con `python3 -I`.
- **M-07** Historial, configuración y caché privados (0700/0600).

Interfaz y escritorios
- **M-02** Bandeja, flotante, ventana, extensión de GNOME, applet y desklet de Cinnamon muestran «sin
  datos» si las lecturas están caducadas o el servicio ha caído, y se reconectan (reactivándolo)
  vigilando el nombre en el bus.
- **B-02** Se reconocen Budgie, Pantheon, Deepin y COSMIC; la extensión de GNOME solo se activa cuando
  el usuario cambia la opción, no en cada arranque.
- **B-03** Idioma: las tablas se traducen al usarlas; la extensión y el applet siguen el idioma
  elegido en Ajustes; al cambiarlo, la interfaz se reinicia.
- **B-10** Señal ligera `ViewsUpdated` para los paneles; el applet de Cinnamon parpadea con la opacidad.
- **B-12** `StartupWMClass` y nombre de programa para que Wayland asocie la ventana con el lanzador.
- **B-13** El reinicio propio funciona con `python3 -m sensorpulse.app`.
- **B-14** El applet de Cinnamon va al primer panel existente y a la siguiente posición libre.

Hardware
- **M-05** «Detectar sensores»: se omite en máquinas virtuales; en portátiles pregunta antes; solo da
  éxito si aparece un hwmon nuevo cuyo nombre corresponde al módulo; detección precisa de conflicto
  ACPI; los módulos cargados al arrancar se eliminan con el paquete (`remove` y `purge`).
- **B-01** Los dispositivos NVML solo se crean si la GPU usa el driver `nvidia` (sin duplicado con nouveau).
- **B-11** No se tocan las GPU dormidas en el descubrimiento; nuevo ajuste «Leer la temperatura de
  los discos duros» para quien no quiera despertar discos nunca.

Otros
- **B-06** `sensorpulse export` usa argparse y valida el id del sensor y los días.
- **B-07** Las celdas del informe Markdown escapan `|` y saltos de línea.
- **B-09** Se quita la ruta de sonidos que no existía; los reproductores se recogen (sin zombis).
- **M-04** El autostart del sistema lleva `TryExec`: se ignora cuando el programa ya no está.

Empaquetado
- **P-01** Mantenedor real y changelog Debian válido. **P-02** `md5sums`, `py3compile`/`py3clean`.
  **P-03** Ya no se incluyen las fuentes (`Recommends: fonts-jetbrains-mono`). **P-04** copyright DEP-5.
  **P-05** `Recommends: pkexec, polkitd`. **P-07** `Installed-Size`, `set -eu`, fechas reproducibles.

Pruebas: `tests/test_audit.py` (un caso por hallazgo) y `tests/service_regress.py` (servicio y D-Bus).

## 0.3.4 — 21/11/2026
Revisión de compatibilidad y robustez para Debian, Ubuntu, Mint y derivadas.

- Compatible con Python 3.8 y 3.9: se quita `zip(strict=True)`, lo único que exigía Python 3.10
  (el paquete pasa a depender de `python3 (>= 3.8)`).
- Abrir y cerrar la ventana principal ya no deja suscriptores huérfanos en el almacén de datos
  (el consumo crecía con cada apertura).
- Ajustes incluye los interruptores «Bloquear posición» y «Dejar pasar los clics cuando está bloqueado». Con ambos
  activos, el widget flotante ignoraba todos los clics y no se podía desbloquear desde la interfaz.
- Los sensores se ordenan de forma natural (temp2 antes que temp10) en chips con 10 o más canales.
- Budgie se detecta bien (publica `Budgie:GNOME` y se trataba como GNOME).
- Paquete: campo `Maintainer` válido, `md5sums` incluido y datos de control sin avisos de lintian.

## 0.3.3 — 19/11/2026
Corrige los fallos de la revisión de la 0.3.1.

**Graves**
- Ryzen con temperatura de control desplazada (1600X-1800X, 2700X, Threadripper 1000/2000): la
  referencia es la temperatura real (Tdie). Antes se usaba Tctl (hasta 27 °C más) y saltaba una
  alarma crítica falsa con el equipo en reposo.
- Las alarmas activas se cierran al desactivar la regla o las alarmas, al borrar la regla y
  cuando el sensor deja de dar dato durante un minuto. Antes se quedaban en rojo para siempre.
- Buscar sensores y `sensorpulse status` ya no leen los límites de los discos SATA (en
  drivetemp, esa lectura despierta el disco).

**Alarmas**
- Las reglas propias tienen nivel (aviso o crítica) y se trata igual en todas partes: color del
  panel, sonido elegido, aplazamiento en pantalla completa y recordatorio al quitar el silencio.
- Los mensajes de «baja de» y «vuelve a la normalidad» usan °F si está elegido y llevan unidad.
- El idioma elegido en Ajustes se aplica también a las notificaciones y nombres de sensores.
- «Reiniciar servicio» reconecta la ventana aunque se abriera con el servicio caído.

**TjMax**
- Las reglas por familia ya nunca dan un valor por encima del oficial (antes, 35 modelos mal).
- Claves de la tabla limpias de texto comercial («6th Gen», «APU», «Surface Edition»…).

**Otros**
- Historial: la gráfica incluye el minuto en curso; corregido un posible id de sensor erróneo.
- Salud de la batería sin mezclar µWh y µAh. UDisks sin posibilidad de pedir contraseña.
- Applet, desklet y extensión: reintentos que se cancelan al quitarlos y ritmo de parpadeo
  según el nivel. Páginas de manual con la versión correcta.

## 0.3.2 — 19/11/2026
- El panel muestra el nombre delante de cada valor, como el widget flotante («CPU 54°  GPU 47°»):
  applet de Cinnamon, extensión de GNOME e icono de la bandeja.
- Tras actualizar el paquete, el programa se reinicia solo con la versión nueva y pide a Cinnamon
  que recargue el applet y el desklet. Antes seguían funcionando los antiguos hasta cerrar sesión,
  con textos a medio traducir y ajustes (como el estilo del widget) que no se aplicaban.

## 0.3.1 — 19/11/2026
- Tabla completa de TjMax de AMD (747 modelos, 661 con valor oficial) sacada del catálogo de
  especificaciones de amd.com y comprobada contra 250 fichas individuales: tiene prioridad sobre
  las reglas por familia (p. ej. Ryzen 5 5600 y Ryzen 7 5700X: 90 °C, no 95).
- Los modelos cuya ficha oficial no publica TjMax se quedan sin valor en lugar de recibir uno
  por familia.
- La búsqueda respeta los límites del nombre (3200G no se confunde con 3200GE) y entiende las
  cadenas antiguas con «(tm)» (FX, Athlon).
- Los Athlon antiguos (II, X2, 6xx) ya no reciben los 95 °C de los Athlon modernos.

## 0.3.0 — 19/11/2026
Corrige los fallos de la revisión de la 0.2.0 y añade mejoras de uso y visuales.

**Fallos graves corregidos**
- Ajustes ya no pierde los cambios a partir del segundo (tampoco las alarmas manuales).
- Desinstalar borra de verdad los datos del usuario y no deja la ventana bloqueada; espera
  hasta 2 minutos si apt está ocupado, avisa si falla y usa su propia petición de contraseña.
- Cambiar ajustes, silenciar o buscar sensores ya no lee los discos SATA ni los sensores lentos.
- TjMax de cada procesador AMD según las fichas oficiales de amd.com (portátiles Ryzen 95-105 °C,
  Ryzen 5000 de sobremesa 90/95 °C, X3D 7000 89 °C…): sin falsas alarmas críticas en portátiles.

**Alarmas**
- Crítico por freno sostenido: en su límite durante 1 minuto y bajando la velocidad (Intel por
  contadores; AMD aprendiendo la frecuencia que el equipo sostiene con carga).
- «Caliente sin trabajar» mira los núcleos más ocupados: un juego de un solo hilo ya no lo dispara.
- Avisos de freno, «sin trabajar» y ventilador: como mucho uno cada 30 min, con «Hoy no más».
- «Entendido» detiene la repetición; el volumen sube poco a poco; las notificaciones de una
  misma alarma se reemplazan en lugar de apilarse.
- Al acabar el silencio se vuelven a anunciar las alarmas críticas activas.
- Respeta el «No molestar» de GNOME, Cinnamon, Xfce y KDE, y resume lo aplazado al terminar.
- Alarmas manuales en °F: se guardan en °C y se editan en tu unidad, con valores iniciales
  según el sensor (ventilador: «baja de 300 RPM»).
- Las temperaturas ocultas se siguen vigilando despacio (cada minuto) para la alarma crítica.
- Sonidos propios: aviso suave (opcional) y alarma de dos tonos.

**Vistas**
- Una sola lógica para todas las vistas: el applet y el desklet de Cinnamon y la extensión de
  GNOME respetan la selección, los nombres, °F y la paleta para daltonismo.
- Cinnamon: el panel usa el applet propio; en Wayland, el flotante es el desklet.
- GNOME: si la extensión aún no está cargada (antes de volver a iniciar sesión), se usa el icono.
- Estilos nuevos «Cabina» (por defecto en sobremesa) y «OLED mínimo» (por defecto en portátiles);
  «Cristal» con tinte, grano y borde especular.
- Ventana «Bento» con una tarjeta grande, fondo «Aurora» opcional, tema «Oscuro profundo»,
  barra de estado («Último dato hace 2 s», reconexión, aviso de actualización).
- Agujas con física de muelle y barrido al aparecer; cifras que ruedan; alarmas que «respiran»;
  destello verde al volver a la normalidad. Se respeta «reducir animaciones».
- Asistente con escaneo animado, zona «Esto es normal con carga» y pantalla final «Todo listo».
- Gestor de sensores con el valor actual y el motivo por el que cada sensor está oculto.
- Tras «Detectar sensores», elegir cuáles de los sensores nuevos mostrar.

**Rendimiento**
- Todas las agujas se mueven a la vez; la animación solo corre si algo visible se mueve
  (flotante HUD con ventiladores: 0,3-0,4 % de CPU medido, antes 3,8 %).
- Applet y desklet sin temporizadores permanentes; extensión de GNOME sin lecturas de ficheros.

**Funciones**
- Exportar el historial de un sensor a CSV o JSON (ventana y `sensorpulse export`).
- Estado de los discos SATA con UDisks2 sin despertarlos (sin pedir contraseña nunca).
- Ids estables de discos (no cambian si cambia el orden de los NVMe); la configuración se migra sola.
- «Detectar sensores» distingue «falta el controlador» de «no hay chip».
- Historial: índices, aviso único si no se puede escribir y recuperación si la base se daña.

**Paquete**
- Descripción en inglés, páginas de manual, changelog Debian, metainfo con capturas y notas,
  sin nombres de otros programas, sin caché de Python al desinstalar.

## 0.2.0 — 19/11/2026
- Alarmas que entienden el procesador: temperatura máxima de diseño por familia (Intel desde el
  propio sensor, Ryzen 95 °C, X3D serie 7000 89 °C, Raspberry Pi 85 °C); un Ryzen a 95 °C con
  carga ya no salta como crítico.
- Avisos nuevos: CPU (Intel) o NVIDIA bajando la velocidad por calor, «caliente sin trabajar»
  (y crítico si llega al límite sin carga) y ventilador parado.
- Tres niveles de alarma (informativo, aviso, crítico); el sonido queda para lo crítico.
  Durante juegos o vídeos a pantalla completa solo interrumpen las críticas (X11).
- Notificaciones con botones «Silenciar 1 hora» y «Detalles».
- Registro de alarmas guardado e historial de hasta 30 días (SQLite), con vistas de 24 horas,
  7 días y 30 días.
- «Detectar sensores»: activa con una sola contraseña los sensores de placa base y de discos SATA.
- Extensión de GNOME Shell (45 o posterior).
- Asistente: botón «Usar lo recomendado y terminar» y frase con el límite calculado para tu CPU.
- Informe del equipo anónimo para foros y órdenes `sensorpulse status [--json]` y `sensorpulse report`.
- Símbolo y palabra de estado junto al color; textos para el lector de pantalla Orca.
- La ventana sigue el tema claro u oscuro del sistema.
- Correcciones:
  - el desklet dibuja el texto con PangoCairo (japonés y chino ya no salen como cuadrados);
  - NVIDIA en portátiles híbridos: se cierra NVML tras cada lectura y se lee cada 30 s;
  - NVIDIA: velocidad del ventilador en RPM con controladores 565 o posteriores;
  - discos SATA cada 30 minutos (ajustable) en lugar de 5;
  - portátiles Dell/ThinkPad/HP/ASUS/Mac: lectura cada 10 s, más lenta si una lectura tarda;
  - solo se leen los sensores visibles o usados por alarmas;
  - la animación de ventiladores ya no va a 30 fotogramas por segundo permanentes;
  - el widget flotante ya no desaparece con «Mostrar escritorio» (ventana «dock» en X11);
  - las notificaciones respetan °F;
  - desinstalar: pide la contraseña antes de borrar nada, usa «purge» y no deja rastro
    (configuración, historial, applet/desklet/extensión activados ni caché de Python);
  - tras actualizar, la ventana reinicia el servicio antiguo automáticamente.

## 0.1.0 — 18/11/2026
Primera versión.
- Servicio en segundo plano (sin root) que lee hwmon, zonas térmicas, baterías, NVIDIA (NVML), CPU y RAM.
- Nunca despierta una gráfica dedicada en reposo; los discos SATA se leen solo cada 5 minutos.
- Alarmas automáticas con los límites del hardware y alarmas propias (por encima/debajo, retardo,
  histéresis): visuales (color y parpadeo), notificación y sonido; silencio de 15 min, 1 h o hasta mañana.
- Icono en el panel (XApp, Ayatana o bandeja clásica), ventana con tarjetas y gráficas de detalle,
  widget flotante transparente con 5 estilos (HUD futurista, Anillos, Barras, Minimal, Cristal).
- Asistente de bienvenida de 5 pasos, ajustes y gestor de sensores (mostrar/ocultar, renombrar, panel, flotante).
- Applet y desklet de Cinnamon.
- Español, inglés, francés, japonés y chino.
