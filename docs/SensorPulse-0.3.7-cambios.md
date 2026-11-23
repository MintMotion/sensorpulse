# SensorPulse 0.3.7: cambios realizados

Todas las peticiones de la lista están implementadas, junto con la colocación del panel a la
izquierda de los iconos del sistema. De paso se corrigieron 4 fallos que salieron al revisar
el código afectado (§2).

Archivos: `sensorpulse_0.3.7_all.deb`, `sensorpulse-0.3.7-codigo.zip` y este documento.

**Pruebas.** `tests/test_037.py` tiene 20 pruebas (una por cambio) y `tests/gui_037.py` tiene 19
comprobaciones de interfaz. Ejecutadas contra el código de la 0.3.6, fallan 19 de las 20 pruebas
de `test_037.py`. La que pasa comprueba que la plantilla de traducción coincide con el código,
algo que ya se cumplía. Con la 0.3.7 no falla ninguna. Las pruebas de la 0.3.6 (`test_036`,
`test_core`, `test_audit`, `service_regress`, `gui_smoke` y `gui_regress`) siguen pasando. Solo
se adaptaron 4 comprobaciones que medían algo que ahora cambia a propósito (§5).

---

## 0. Resumen

| Petición | Estado | Dónde se comprueba |
|---|---|---|
| Barra de valores del panel a la izquierda de los iconos del sistema | Hecho | `test_cinnamon_applet_*`, prueba JS de la extensión |
| 2.1 Más sonidos y todos en el asistente | Hecho: 15 sonidos (antes 6) | `test_sounds_*`, `gui_037` |
| 2.1 Botón ▶ junto a cada selector | Hecho, en los 4 selectores | `test_037_playsound_*` (servicio) |
| 2.1 Alarma de central nuclear | Hecho (`sensorpulse-nuclear.wav`) | `test_sounds_generator_is_reproducible` |
| 2.2 «En una ventana» no se ocultaba | Corregido | `gui_037` 2.2 |
| 2.2 Más de 3 valores en el panel | Hecho: hasta 8 (3 por defecto) | `test_panel_bar_and_click_split` |
| 2.2 Elegir cuáles en la barra y cuáles al hacer clic | Hecho | `test_panel_bar_and_click_split`, `gui_037` |
| 2.2 Indicadores tipo cuentakilómetros en el panel | Hecho (applet, extensión e icono) | prueba JS (`tests/js`) |
| 2.3 El asistente explica y configura panel y widget | Hecho | `gui_037` 2.3 |
| 2.4 Nombres propios (asistente, Ajustes, panel, escritorio) | Hecho | `test_rename_set_name`, `gui_037` 2.4, prueba JS |
| 2.5 Italiano, alemán y portugués | Hecho: 386/386 cadenas en los 7 idiomas | `test_eight_languages_complete` |
| 2.6 Intervalo de los discos según su tipo | Hecho | `test_disk_*`, `test_warm_hdd_is_watched_closely` |
| 2.7 CPU del dibujado por debajo del 1 % de un núcleo | Hecho: de 8,6 % a 0,4 % | `gui_037` 2.7, §3 |
| 3 Mantenedor «SensorPulse», sin correo | Hecho | `test_maintainer_without_email_and_versions` |

---

## 1. Petición por petición

### Panel: la barra de valores salía a la derecha del todo

**Causa.** Al añadir el applet a Cinnamon, `_cinnamon_free_slot()` le daba la **última**
posición de la zona derecha (`max(posiciones) + 1`). Así acababa detrás del reloj, en la zona
de los iconos del sistema.

**Arreglo** (`ui/envinfo.py`):
- Applets nuevos: se les da la **primera** posición de la zona derecha (`min(posiciones) - 1`).
  Quedan justo a la izquierda de la bandeja, la red, el sonido y el reloj, en el espacio libre.
  En el código de Cinnamon (`appletManager.js`) se comprobó que la posición se lee con
  `parseInt` y que cada applet se coloca antes del primero que tenga un número mayor. Por eso
  un número negativo funciona y **no hay que mover ningún otro applet**.
- Instalaciones que ya tenían el applet a la derecha del todo: `cinnamon_place_applet()` lo
  mueve una sola vez (clave `cinnamon_applet_placed`). Solo lo mueve si está detrás de todos los
  iconos del sistema. Si después el usuario lo coloca en otro sitio con el modo de edición del
  panel, se respeta.
- Extensión de GNOME: `addToStatusArea(uuid, indicador, 0, 'right')`, la primera posición de la
  zona derecha, a la izquierda de los indicadores del sistema.

**Limitación.** Si no hay applet ni extensión (Xfce, MATE, KDE…), se usa el icono de bandeja
(XApp/AppIndicator). Ese icono va siempre dentro del área de notificación, que es de la bandeja y
no de SensorPulse. En Linux Mint Cinnamon se usa el applet, que sí queda donde se pide.

### 2.1 Sonidos

- **Lista nueva** (`ui/sounds.py`): una sola lista con 15 sonidos para todos los selectores.
  - 6 propios, incluidos en el paquete: dos tonos, **central nuclear**, ping de sonar, pitidos
    digitales, campanilla suave y pitido suave.
  - 9 del tema freedesktop: despertador, aviso, campanilla, campana, timbre, mensaje, atención,
    tono de error e información.
- **Alarma de central nuclear** (`sensorpulse-nuclear.wav`, 3,4 s). Son tres «whoop» de klaxon
  que suben de 380 a 1150 Hz. Tienen timbre de bocina (armónicos impares 1, 3, 5 y 7), algo de
  vibrato y saturación, y eco de nave industrial. Los sonidos propios salen de
  `tools/make_sounds.py`: no necesita dependencias y genera siempre los mismos bytes, así el
  paquete sigue siendo reproducible.
- **Botón ▶ junto a cada selector**: alarma crítica, avisos, editor de cada alarma y asistente.
  Usa el método D-Bus nuevo `PlaySound`.
  - Solo reproduce el sonido, sin la notificación de prueba.
  - Admite como mucho uno cada 0,7 s, para que no sea un altavoz libre por D-Bus.
  - Si la interfaz habla con un servicio antiguo que no tiene `PlaySound`, usa la alarma de
    prueba de antes.
- **Asistente**: ahora ofrece lo mismo que Ajustes.
  - Activar el sonido de alarma, con su selector y ▶.
  - Repetir el sonido cada 2 minutos.
  - Sonido suave para los avisos, con su selector y ▶.
- **Ajustes** gana el selector del **sonido de los avisos**. La clave
  `alarms.warn_sound_name` ya existía, pero no había forma de elegirla. «Prueba de alarma» queda
  como un botón aparte.

### 2.2 Panel

- **«En una ventana» no se ocultaba.** Desactivar el interruptor solo lo guardaba.
  - Ahora `_display_changed()` cierra la ventana (`App.hide_window()`) y la abre al activarlo.
  - Fallo relacionado (§2): con «En una ventana» activado, **cualquier** cambio en Ajustes sacaba
    la ventana principal por delante. `Settings.save()` llama ahora a
    `apply_display(background=True)`.
- **Hasta 8 valores** en la barra.
  - El máximo pasa de 3 a 8 y el valor por defecto de 2 a 3 (`views.PANEL_MAX`).
  - Se pueden elegir hasta 16 sensores para el panel: los que no caben en la barra salen al hacer
    clic.
- **Barra o «Al hacer clic».**
  - En Ajustes › Sensores hay una columna nueva, «Al hacer clic», junto a «Panel». Las dos se
    excluyen: un sensor está en la barra o solo en la lista.
  - En el asistente, cada sensor tiene un desplegable: «—», «Panel» o «Al hacer clic».
  - `views.panel_split()` decide qué va en cada sitio: en la barra, los primeros elegidos hasta
    el máximo; en la lista, los que no caben y los marcados «Al hacer clic».
  - El applet, la extensión y el icono de bandeja muestran esa lista arriba del menú.
- **Aspecto del panel** (Ajustes › Panel y asistente): «Nombre y número», como antes, o
  «Indicadores». Además, «Mostrar los nombres en el panel» se puede desactivar para que quepan más
  valores.
  - Los indicadores son arcos de 240° tipo cuentakilómetros, como el estilo «Cabina» del widget.
    El arco lleva el color del valor y la cifra dentro.
  - Applet de Cinnamon y extensión de GNOME: `St.DrawingArea` con cairo y la cifra en una
    `St.Label` encima. Se dibuja solo al llegar un dato.
  - Icono de bandeja: el primer valor se dibuja como indicador en el propio icono.
  - El servicio publica para cada valor la fracción del recorrido (`frac`) y un texto corto
    (`tiny`: «45°», «1.4k», «38W»). Así las tres vistas dibujan lo mismo.

### 2.3 Asistente

- **Página «¿Dónde quieres ver tus sensores?»**
  - Debajo de «En el panel»: número de valores, aspecto y nombres.
  - Una vista previa del panel que cambia en vivo: los valores salen a la izquierda de los
    iconos del sistema y del reloj, como quedarán de verdad.
  - Un texto que explica la diferencia entre el panel, la lista al hacer clic y el widget del
    escritorio.
- **Página «¿Qué quieres vigilar y dónde?»** (antes «¿Qué quieres vigilar?»). Una fila por
  sensor:
  - «Mostrar».
  - **Nombre**, editable.
  - **Panel**: «—», «Panel» o «Al hacer clic».
  - **Widget**: casilla.
  - La explicación de cada columna va encima.

### 2.4 Nombres propios

- **Desde el asistente**: el campo «Nombre» de cada sensor. El nombre original queda como texto
  de ayuda y como «Nombre original: …» al pasar el ratón.
- **Desde Ajustes**: doble clic en el nombre, como antes.
- **Desde el panel**: «Cambiar el nombre de un sensor…» en el menú del applet de Cinnamon y en el
  de la extensión de GNOME.
- **Desde el escritorio**:
  - Widget flotante, botón derecho: «Cambiar el nombre de «CPU»…» para el indicador que está bajo
    el puntero, y un submenú con todos.
  - Desklet de Cinnamon, botón derecho: el mismo submenú.
- **Opción nueva `sensorpulse --rename ID`**, que usan el applet, la extensión y el desklet.
  - Abre un diálogo pequeño (`ui/rename.py`). Si el programa ya está abierto, lo abre ese mismo
    proceso.
  - Un nombre vacío o igual al original quita el cambio de nombre. El máximo es 40 caracteres.
- Con un nombre propio, la lista del menú del panel lo muestra en vez del nombre genérico del
  dispositivo. Por ejemplo, «Chipset: 41°» en vez de «Placa base: 41°».

### 2.5 Idiomas

- **Italiano, alemán y portugués**, con las 386 cadenas completas.
  - El portugués es el europeo. Al detectar el idioma, `pt_BR` también usa este portugués.
  - Las 64 cadenas nuevas de esta versión también están en español, francés, japonés y chino.
- Llegan a:
  - el programa (las `.po`);
  - el selector de idioma de Ajustes;
  - el lanzador (`.desktop`);
  - los mensajes de contraseña de polkit;
  - el resumen de AppStream.
- **Applet, extensión y desklet.** El servicio publica en `views.tr` sus textos ya traducidos.
  Así valen para los 8 idiomas sin repetir traducciones en JavaScript. Las tablas en JS solo se
  usan hasta que llega la primera foto.
- `make_po.py` comprueba que no falta ninguna cadena y que los marcadores (`%s`, `%(name)s`…)
  coinciden en los 7 idiomas.

Las traducciones las he hecho yo; conviene que una persona nativa revise italiano, alemán y
portugués.

### 2.6 Temperatura de los discos

**Detección del tipo** (`core/discovery.py: disk_media()`). Solo mira sysfs y nunca despierta
el disco.

| Tipo | Cómo se reconoce | Intervalo | Por qué |
|---|---|---|---|
| NVMe | controlador `nvme` | el normal (≥ 5 s) | la temperatura la da el controlador; no afecta al reposo ni a la vida útil |
| SSD SATA | `queue/rotational = 0` | 10 s (`intervals.ssd`) | sin partes móviles ni temporizador de giro |
| Disco duro | `queue/rotational = 1` | **lo elige el usuario** (2 min a 2 h; 30 min por defecto) | en muchos modelos, cada lectura reinicia el temporizador de reposo |
| Desconocido | sin `rotational` | como un disco duro | lo prudente |

- **Disco duro templado.** Si la última lectura está a 8 °C o menos del aviso, se vuelve a leer
  cada 2 minutos hasta que se enfríe (`hdd_adaptive`, activado por defecto).
  - Así un intervalo largo no esconde un calentamiento que ya ha empezado.
  - Un disco tan templado está trabajando, así que leerlo no le quita reposo.
  - Con UDisks2, un disco dormido sigue sin despertarse.
- **Interfaz** (`ui/disks.py`):
  - En Ajustes › Discos se ve cada disco con su tipo y cada cuánto se lee.
  - Si hay discos duros, también:
    - el selector del intervalo, con texto: «cada 2 min (vigilancia estrecha)» … «cada 30 min
      (que descansen)»;
    - una explicación del compromiso entre descanso y vigilancia;
    - la casilla del disco templado.
  - Lo mismo sale en el asistente, en la página de alarmas, si hay algún disco duro.
- Un SSD SATA ya no usa los límites de disco duro (aviso a 50 °C y crítico a 60 °C), sino los de
  disco genérico.
- `sensorpulse status --json` y la foto del servicio incluyen `media` y `warm` por disco.

### 2.7 Consumo de recursos del dibujado

**Reproducción.** Medí la 0.3.6 con `tools/bench_draw.py`: widget flotante «Cristal», la gráfica
con 10 minutos de historial (300 puntos) y ventiladores girando. Gastaba **8,6 % de un núcleo
en dibujar**, con 8,65 redibujados por segundo de 10 ms cada uno; el proceso entero, 9,1 %. Es lo
mismo que se midió en un equipo real (≈ 8 % de un núcleo). La causa era la que señalaba el perfil:
- la hélice de los ventiladores mantenía la animación a 8 fotogramas por segundo;
- cada fotograma redibujaba el widget entero, con `draw_sparkline` haciendo un `stroke()` por
  segmento (299 por gráfica).

**Arreglos, por orden de impacto:**
1. **Ritmo de redibujado** (`ui/floating.py`, `ui/store.py`, `ui/window.py`):
   - el widget solo se redibuja entero cuando cambia algo que se ve: dato nuevo, ajustes, ratón
     encima, latido de una alarma o cifra rodando;
   - si lo único que se mueve son las hélices, se invalida solo su recuadro
     (`queue_draw_area`);
   - las hélices van a 5 fps. Antes iban a 8 fps y además giraban más de un aspa por fotograma
     (1,8 rad a 1450 RPM), así que parecían quietas o girando hacia atrás. Ahora la velocidad es
     proporcional a las RPM, pero se ve girar;
   - la ventana principal solo redibuja las tarjetas que tienen algo en movimiento. Antes las
     redibujaba todas en cada fotograma.
2. **Fotograma en caché.** El widget completo (fondo de cristal, tarjetas y minigráficas) se
   guarda en una superficie cairo. Solo se rehace cuando cambia su clave: versión de los datos,
   tamaño, estilo, tema, acento, ratón encima, bloqueo, «sin datos», latido o silencio.
3. **`draw_sparkline`**:
   - un solo `stroke()` y un solo `fill()`;
   - como mucho un punto por píxel de ancho. Se guardan el mínimo y el máximo de cada columna de
     2 px, así no se pierden los picos;
   - degradados en caché. El color por tramos se mantiene con **un único degradado vertical**:
     el color solo depende del valor, el valor solo de la altura, y las paradas del degradado
     están donde cambia la mezcla de `value_color()`. Una prueba compara píxel a píxel el
     degradado con `value_color()`;
   - el fondo `draw_glass` también usa degradados en caché.

**Resultado** con la misma prueba (`tools/bench_draw.py`, 30 s, historial lleno):

| Estilo | 0.3.6, dibujo | 0.3.7, dibujo | 0.3.6, proceso | 0.3.7, proceso |
|---|---|---|---|---|
| Cristal | 8,61 % | **0,42 %** | 9,09 % | **0,81 %** |
| Minimal | 1,18 % | 0,16 % | 1,71 % | 0,54 % |
| Cabina | 0,33 % | 0,14 % | 0,45 % | 0,23 % |
| HUD | — | 0,20 % | — | 0,30 % |

(porcentaje de **un** núcleo; con 12 hilos, el 0,81 % de un núcleo es un 0,07 % de la CPU total)

La imagen es la misma que en la 0.3.6: se comparó el render de «Cristal» antes y después.

**Medida larga.** Interfaz con el panel y el widget flotante «Cristal», servicio
con sensores simulados, 75 minutos. La CPU se midió con `pidstat -p PID 5 12` (12 muestras de 5 s)
al principio y al final; la memoria, cada 5 min.

| | Interfaz | Servicio |
|---|---|---|
| CPU al principio (2 min, gráfica a medio llenar) | 0,68 % de un núcleo | 0,13 % |
| CPU al final (75 min, gráfica llena) | **0,75 % de un núcleo** | **0,15 %** |
| PSS a los 3 min / 60 min / 70 min | 74 827 / 75 027 / 75 027 kB | 24 580 / 26 956 / 26 960 kB |
| RSS a los 3 min / 40 min / 70 min | 103 080 / 103 280 / 103 280 kB | — |

- **Sin fugas.**
  - La interfaz deja de crecer al llenarse su gráfica de 10 minutos: el RSS queda fijo desde el
    minuto 40 y el PSS desde el 45.
  - El servicio crece durante la primera hora y se queda fijo a partir del minuto 60. Es lo
    esperado: guarda en memoria la última hora de lecturas (`HISTORY_SECONDS = 3600`); lo
    anterior va a SQLite.
  - Lo confirma `tracemalloc` en la interfaz. Entre los minutos 3 y 13, la memoria de Python
    solo crece 0,3 MB (de 7,75 a 8,06 MB), y todo es la gráfica de 10 minutos llenándose
    (`deque(maxlen=300)` por sensor, `store.py:270`), que tiene tope.
- **Misma memoria que la 0.3.6.** Las dos versiones juntas, en las mismas condiciones y a los
  5 min: interfaz 101 272 kB frente a 101 352 kB de RSS; servicio 31 428 kB frente a 31 480 kB.
- **La RAM publicada es PSS.**
  - El PSS reparte la memoria compartida (GTK, Pango, Python…) entre los procesos que la usan.
    Por eso depende de lo que haya abierto. En los minutos 35 a 40 de esta medida bajó de
    75 a 52,7 MB solo porque arrancó otro proceso de prueba con las mismas bibliotecas.
  - En el contenedor de pruebas, sin escritorio, la interfaz sale con unos 75 MB de PSS. En el
    escritorio real medido, que comparte esas bibliotecas con otros programas, eran 46 MB.
  - Para ver si hay una fuga sirve más el RSS de un mismo proceso a lo largo del tiempo, que
    aquí no cambia.

### 3. Mantenedor del paquete (decisión que se mantiene)

- `packaging/build-deb.sh`: `Maintainer: SensorPulse`, sin correo. Se mantiene aunque Debian
  pida un correo; lintian lo marcará como aviso. También el `changelog.Debian`.
- El correo sigue en «Acerca de» y en `copyright`, como autor del programa: eso no es el campo
  de mantenedor.

---

## 2. Fallos encontrados de paso y corregidos

| ID | Fallo en la 0.3.6 | Arreglo |
|---|---|---|
| **P1** | Con «En una ventana» activado, **cualquier** cambio en Ajustes sacaba la ventana principal por delante de Ajustes (`save()` → `apply_display()` sin `background`). | `apply_display(background=True)`. La ventana solo la abre y la cierra su interruptor. |
| **P2** | Las hélices a 8 fps giraban más de un aspa por fotograma: parecían quietas o hacia atrás (aliasing). | Velocidad limitada (2,4 rad/s a 3000 RPM) y 5 fps. |
| **P3** | En Ajustes, un interruptor junto a una descripción de dos líneas salía estirado verticalmente. | Centrado vertical en `_row()`. |
| **P4** | La ventana principal redibujaba todas las tarjetas en cada fotograma de animación, aunque solo se moviera una aguja. | Solo las tarjetas con algo en movimiento (`Store.animating_ids()`). |

---

## 3. Compatibilidad al actualizar

- **El applet, el desklet y la extensión aún sin recargar** siguen funcionando con el servicio
  nuevo. `views.panel` mantiene su formato: la barra sigue siendo `panel[:panel_count]`. Además,
  la interfaz pide a Cinnamon que recargue el applet y el desklet al detectar la versión nueva,
  como ya hacía.
- **Un servicio antiguo con la interfaz nueva**: si falta `PlaySound`, se usa la alarma de
  prueba. Si faltan `panel_style`, `tr` o `panel_extra`, se usan los valores por defecto.
- **Configuración**: las claves nuevas (`panel_style`, `panel_labels`, `sensors.panel_menu`,
  `intervals.ssd`, `hdd_adaptive` y `cinnamon_applet_placed`) se validan con tipo y rango. El
  intervalo de los discos duros admite ahora desde 1 min (antes 5).
- **Paquete**: se probó a instalar la 0.3.6, actualizar a la 0.3.7, ejecutar
  `/usr/bin/sensorpulse status` (en alemán) y purgar. Los ficheros instalados son exactamente los
  del código. El `.deb` sale idéntico byte a byte en dos construcciones, también con umask 077.

---

## 4. Lo que no se ha podido comprobar aquí

Las pruebas se hicieron en Ubuntu 24.04 con Python 3.12, sensores simulados (`tools/fake_sysfs.py`)
y Xvfb. Faltan tres comprobaciones, y conviene hacerlas en un Linux Mint real:

- **Applet de Cinnamon y extensión de GNOME en un escritorio real.** Su código se ejecutó en Node
  con objetos simulados de St, Clutter, Gio y cairo (`tests/js/run_xlets.js`). Así se cubrieron
  todos los caminos: texto, indicadores, con y sin nombres, menú, cambio de nombre y «sin datos».
  También se comprobó la posición en el panel con la lista de applets de un Mint por defecto.
  Pero no se ha visto cómo dibuja Cinnamon los indicadores: tamaño de la cifra según el tema y
  la altura del panel.
- **Que suenen los sonidos**: en el contenedor no hay tarjeta de sonido. Se comprobó que los WAV
  son válidos, que el servicio encuentra el fichero y que `PlaySound` responde, no enseña
  notificación y tiene límite de frecuencia.
- **Discos reales**: el tipo se lee de `queue/rotational`. Se probó con NVMe, SSD SATA, disco
  duro y disco sin ese dato en un sysfs simulado.

Medida a repetir en su equipo, como pedía:
`pidstat -p $(pgrep -f /usr/bin/sensorpulse) 5 12` con el flotante activo.

---

## 5. Pruebas

| Batería | Resultado |
|---|---|
| `tests/test_037.py` (nueva, 20 pruebas) | 20/20. Con el código de la 0.3.6: 19 fallan |
| `tests/gui_037.py` (nueva, 19 comprobaciones bajo Xvfb) | todas OK; dibujo del flotante «Cristal» 0,44 % de un núcleo |
| `tests/js/run_xlets.js` (nueva: applet, desklet y extensión en Node) | todo OK |
| `tests/service_regress.py` (+1 prueba: `PlaySound`) | 13/13 |
| `tests/test_036.py`, `test_core.py`, `test_audit.py` | 0 fallos |
| `tests/gui_smoke.py`, `tests/gui_regress.py` | OK |
| `pyflakes`, `vermin` (Python ≥ 3.8), `node --check`, `xmllint` | sin avisos |

Comprobaciones adaptadas porque el comportamiento cambia a propósito:
- `test_036`: `panel_count` por defecto pasa de 2 a 3.
- `test_036`: la prueba de dos discos con el mismo número de serie usa otra ruta, porque el
  sysfs simulado ya trae un SSD SATA en `target2`.
- `test_036`: la comprobación de formatos recorre los 7 idiomas.
- `test_audit`: el intervalo mínimo de los discos duros pasa de 300 a 60 s, y la posición del
  applet pasa a ser la primera de la zona derecha.

---

## 6. Ficheros

**Nuevos**
- `sensorpulse/ui/sounds.py`: lista de sonidos y selector con ▶.
- `sensorpulse/ui/disks.py`: discos por tipo e intervalo.
- `sensorpulse/ui/rename.py`: diálogo de nombre propio.
- `sensorpulse/data/sounds/`: `sensorpulse-nuclear`, `-sonar`, `-beeps` y `-chime`, en WAV.
- `sensorpulse/po/`: `it.po`, `de.po` y `pt.po`.
- `tools/`: `make_sounds.py`, `bench_draw.py`, `tr_it.py`, `tr_de.py` y `tr_pt.py`.
- `tests/`: `test_037.py`, `gui_037.py` y `js/run_xlets.js`.

**Cambiados**
- Programa:
  - `views.py`: reparto entre barra y clic, datos de los indicadores y textos para JS.
  - `config.py`: claves nuevas, 8 valores e idiomas.
  - `app.py`: `--rename`, ocultar la ventana y mover el applet.
  - `client.py` y `service.py`: `PlaySound`.
  - `i18n.py`.
  - `core/model.py`, `core/discovery.py` y `core/engine.py`: tipo de disco, intervalos y disco
    templado.
  - `ui/draw.py`: minigráfica, degradados en caché e indicador del panel.
  - `ui/floating.py`: caché del fotograma, hélices y cambio de nombre.
  - `ui/store.py`: hélices y `animating_ids`.
  - `ui/window.py`: redibujado selectivo.
  - `ui/tray.py`: barra o clic e indicador.
  - `ui/envinfo.py`: posición del applet.
  - `ui/settings.py`: sección Panel, columna «Al hacer clic», sonidos, discos e idiomas.
  - `ui/wizard.py`: páginas nuevas.
- Integraciones:
  - `data/cinnamon/applet/…/applet.js`;
  - `data/cinnamon/desklet/…/desklet.js`;
  - `data/gnome-shell/…/extension.js`;
  - sus `metadata.json` (versión 0.3.7).
- Datos:
  - `sensorpulse.desktop`;
  - `io.github.mintmotion.SensorPulse.policy`;
  - `io.github.mintmotion.SensorPulse.metainfo.xml`;
  - `man/sensorpulse.1` (`--rename`).
- Empaquetado y documentación: `packaging/build-deb.sh`, `README.md`, `README.es.md`,
  `CHANGELOG.md` y `CHANGELOG.es.md`.
- Pruebas y herramientas: `tools/fake_sysfs.py` (SSD SATA y datos de bloque), `tools/make_po.py`,
  `tools/tr_es/fr/ja/zh.py`, `tests/test_036.py`, `tests/test_audit.py` y
  `tests/service_regress.py`.
