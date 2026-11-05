# Validación · editor de Distribuir ventilación

02/11/2026. Revit 2025 (25.0.2.419), pyRevit 7.0.0.26278+2233, CPython 3.12.3. Política actual: tabla 2.1, principal 8 L/s / secundarios 4 L/s, selección automática revisable; vestidores diferenciados. Los resultados del 01/11 con 8 L/s en todos los dormitorios son históricos y no describen la política vigente.

## Pruebas ejecutadas

| Comprobación | Resultado |
|---|---|
| `python -m unittest discover -s tests -p 'test_ventilacion*.py'` | 41 pruebas de regresión, correctas |
| `python -m unittest discover -s tests -p 'test_ventilation*.py'` | 44 pruebas de propuesta y transformaciones, correctas |
| `python -m unittest discover -s tests -p 'test_routing*.py'` | 42 pruebas de regresión del trazado independiente, correctas; no se modifica ese botón |
| `powershell -NoProfile -STA -ExecutionPolicy Bypass -File tests/check_ventilation_editor_ui.ps1` | WPF real en proceso aislado con puente CPython de pyRevit y geometría ficticia; arrastre, zoom/tamaño, añadir/eliminar, Flow, deshacer/rehacer, reequilibrado y sincronización correctos |
| Integración nativa `a9b7a09d8816` | Correcta, 45,5 s, `run_modify(dry_run=True)` y grupo interno revertido |
| Repetición ampliada `43fd141e8212` | Correcta, 48,5 s, todo el ensayo anterior más cambio real de cota de techo y protección de terminal bloqueado; reversión completa |
| Ventana no modal `82dce2892768`, `58482f79897b`, `98a070e7e2a9`, `15970f017b02` | Evento API ejecutado después de devolver el comando; 55 objetos iniciales en plano, edición MCP v1→v2 visible automáticamente, geometría/cocina/caudales v6 sincronizados, 114 objetos al final, sin errores |
| Capturas | PNG de propuesta y captura nativa `capture_view(mode:viewport)` abiertos y examinados realmente por el cliente |

## Simplificación de interfaz

Se sustituyen las tres filas de acciones y el panel de propiedades por una fila de herramientas, tabla editable y plano. No hay casillas de aceptación ni bloqueos en Distribución. Las operaciones secundarias están en Más opciones y los avisos se pliegan. Se conserva únicamente la casilla necesaria para indicar qué habitaciones forman la vivienda.

El harness WPF ejecutó edición real de las celdas de tipo (desplegable), Flow y X, conservación exacta del otro eje, doble clic en el plano para abrir el caudal, rechazo/reversión de valor negativo sin diálogo modal y conflicto de versión durante una edición sin sobrescribir el cambio externo. También conserva las comprobaciones de arrastre, altas/bajas, zoom, tamaño, historial y sincronización. Resultado: correcto, `artifacts/ventilation-editor-ui.json` e imagen actualizadas y examinadas.

Prueba nativa `fe4b7c2f7867`: siete rejillas colocadas y validadas desde la interfaz simplificada con una sola confirmación para toda la propuesta, incluidos los avisos funcionales; sin aceptación por filas. `run_modify(dry_run=True)`, resultado `rolled_back`, cero errores. Evidencia: `artifacts/ventilation-simple-native-ui.json`. La apertura real `2b0b27f5e233` conserva el borrador del usuario y muestra columnas ID, Habitación, Tipo, L/s, X, Y y Z sin modificar el modelo. Las evidencias anteriores de la antigua interfaz quedan como históricas.

Repetición final `901cd55b9dc1` con el botón combinado Calcular y distribuir: siete rejillas, una confirmación, geometría nativa comprobada, cero errores y reversión completa. La captura final de la ventana real está en `artifacts/ventilation-simple-current.png`; su informe confirma conservación de propuesta/versión y documento sin modificar.

La WPF aislada no sustituye la prueba nativa. La integración nativa coloca siete terminales y comprueba la cara visible inferior: pendientes opuestas de 9,0903°, error angular 0°, error máximo de plano aproximadamente 0,000522 mm. También prueba movimiento conservando IDs, alta de una octava boca, borrado nativo diferido, bloqueos independientes persistidos, cambios de tipo reales y rechazo de propuesta obsoleta después de un movimiento externo. Una segunda aplicación conserva IDs y `VersionGuid`, sin reescrituras innecesarias. El grupo se revierte y compara el conjunto de IDs antes/después; la consulta posterior confirma documento sin modificar y cero terminales de ensayo.

## Correcciones verificadas durante el ensayo

- `VersionGuid` no se actualizaba dentro del grupo de prueba: la huella incorpora ahora colocación, orientación, tipo y parámetros actuales de los terminales. Se repitió el ensayo completo y se verificó el rechazo `OUTDATED`.
- Los callbacks diferidos perdían la ruta de importación restaurada por MCP: se incorpora un contexto local de importación alrededor de callbacks, sin API en MouseMove.
- El dibujo discontinuo de los separadores virtuales intentaba `DoubleCollection([4,3])`, incompatible con el puente CPython/.NET de Revit. Se construye la colección vacía y se añaden doubles explícitos. El harness WPF incorpora separadores virtuales; el plano nativo se verificó después de abrir una ventana nueva con el código actual.
- Una intersección GEOS de cocina devolvía `covers=False` con área exterior cero. Se admite exclusivamente error numérico de borde inferior a 0,1 mm y área exterior de hasta 0,01 mm²; las zonas realmente exteriores siguen rechazándose.
- La ventana vieja conservaba funciones previas aunque MCP importara otra copia actual. El mismo botón activa una ventana existente y renueva únicamente los módulos del editor cuando se abre después de cerrarla. No se fuerza una recarga global de pyRevit desde MCP.
- Al actualizar el análisis se recomputan Z y orientación conservando XY. Se cambió realmente un techo 20 mm, se actualizó y aplicó la propuesta sin duplicados. Después se bloqueó la posición y se subió el techo otros 10 mm: se conservó la posición bloqueada y se impidió aplicar. Ambos cambios de techo se revirtieron.
- Se ejecutó el propio `script.py` del botón (`9979ba75b4fd`): abrió Vivienda 1, recuperó su borrador y dibujó 55 objetos de plano, sin alterar el modelo.
- Comprobación final `5a26c69ee2b5`: botón abierto con código actualizado, Vivienda 1 v2 conservada, 55 objetos de plano, geometría actual, cero terminales de ensayo y documento sin modificar.

## Cobertura de los 30 casos solicitados

| Casos | Evidencia ejecutada |
|---|---|
| 1–5: arrastre horizontal/inclinado, cambio de paño, fuera de Room, obstáculo | Pruebas de propuesta; arrastre WPF; movimiento y nueva cota en Revit |
| 6–10: alta, baja, Flow, diseño de estancia, reequilibrado | Pruebas de propuesta/WPF y alta-baja-Flow nativas con reversión |
| 11–14: bloqueos, reoptimizaciones y deshacer/rehacer | Pruebas independientes/WPF; almacenamiento de bloqueos leído de Extensible Storage nativo |
| 15–19: principal, vestidor, cocina, extracción y zona seca | Pruebas independientes y propuesta basada en la geometría real de Proyecto; no se modifica arquitectura |
| 20–24: mover/Flow por MCP, captura, acceso a imagen, sincronización WPF | Dispatcher real sobre el mismo borrador visible; capturas antes/después abiertas; actualización automática hasta v6 |
| 25–26: aplicar y reejecutar sin duplicados | Siete nativos validados, mismos IDs/versiones al repetir; prueba posterior de octava boca/borrado |
| 27–29: zoom, tamaño y coordenadas | WPF y transformaciones inversas con giros, origen lejano, pan y DPI; conservación de coordenadas |
| 30: mínimos normativos | Pruebas 0–4 dormitorios, principal/secundarios, usos combinados, zonas húmedas y equilibrio; validación nativa de Flow |

El cambio entre paños se prueba con geometría independiente; la muestra Revit prueba terminales en ambos paños inclinados y su movimiento, no un arrastre nativo atravesando la cumbrera. Las variaciones de DPI se comprueban numéricamente y mediante el uso de DIP, no trasladando Revit a varios monitores físicos. No se afirman ensayos físicos no ejecutados.

## Revisión visual real

La captura inicial mostró la extracción E02 en el espacio abierto sin delimitar cocina. Se consultó el elemento real 3221113, `ARQ_Cocina L`, categoría mobiliario y evidencia `KITCHEN_FURNITURE`. En un borrador de ensayo se definió una zona alrededor de su posición, recortada por el contorno de Room, y se reoptimizó únicamente esa estancia. La nueva captura muestra E02 dentro de cocina y la impulsión I01 en la zona seca; se ajustó I01 mediante XY estructuradas, no mediante píxeles. Validación: 34 L/s por red después de subir manualmente 1 L/s en una boca y reequilibrar; todos los mínimos y diseños satisfechos, Z recalculada en la pendiente. El cálculo inicial sin ese incremento era 33 L/s por red.

La región automática alrededor del mueble es una hipótesis funcional del ensayo, no una zona arquitectónica exacta ni un diseño aprobado. El borrador de ensayo y la ventana se eliminaron/cerraron al terminar. Las imágenes y sus metadatos se conservan como evidencias.

## Archivos de evidencia

- `artifacts/ventilation-editor-native-integration.json`: resultado nativo completo, decisión `rolled_back`.
- `artifacts/ventilation-editor-native-refresh.json`: repetición completa y pruebas adicionales de cambio de techo/bloqueo, decisión `rolled_back`.
- `artifacts/ventilation-editor-native-modeless.json`: callback API, sincronización v6, cero errores y documento sin modificar.
- `artifacts/ventilation-editor-native-visual.json`: elemento real de cocina, zona, XYZ, Flow, validación y leyenda.
- `artifacts/ventilation-editor-native-before.png`, `ventilation-editor-native-after.png`, `ventilation-editor-native-ui.png`: capturas del borrador real abiertas por el cliente.
- `artifacts/ventilation-editor-ui.json`, `ventilation-editor-ui.png`, `ventilation-editor-plan.png`: prueba WPF aislada, expresamente ficticia.
- La captura nativa de planta también se recibió como imagen MCP y quedó en `%APPDATA%/pyRevit/agent/captures/20261102-110750-e361c4 - Plano de planta - 11 NSA_0,15.png`.

Los JSON/imágenes `ventilacion-...` anteriores corresponden al desarrollo previo. Mantienen valor histórico para geometría/pendientes, no para la nueva política de caudales ni la edición interactiva.

## Repetición y límites

Ejecute la integración nativa desde el MCP existente con `workspace=ARQ-T.extension/lib`, motor CPython, cargando de forma fresca `tools/revit_ventilation_editor_integration.py` y llamando `run(doc, artifacts)` dentro de `run_modify(dry_run=True)`. Necesita un proyecto con Rooms, techos aptos y los dos tipos compatibles. El script usa almacén temporal aislado, revierte su grupo y compara IDs. La reversión del modelo no revierte archivos: no use un borrador de producción para ensayos COMMIT.

La ventana no modal se comprueba en dos llamadas con `tests/revit_ventilation_editor_modeless.py`: cargue su módulo de forma fresca antes de `open_fixture`, espere al evento API y consulte `state`. No reutilice una copia del helper que conserve módulos de versiones anteriores. El ensayo no debe cerrar una ventana del usuario; la fixture exige que no exista ninguna antes de abrir la suya. Cierre y elimine únicamente el borrador con nombre `ENSAYO MODELESS MCP`.

Quedan fuera de la muestra nativa otras viviendas, varias plantas y monitores con DPI distintos; tienen cobertura independiente en el motor, pero requieren muestras reales adicionales. Otras formas de alojamiento y cambios de nivel nativo se rechazan. Flow no está asociado al conector HVAC. Faltan capacidad/acústica/pérdidas/difusión de fabricante, dimensionado de conductos y verificación efectiva de transferencias. No se considera esta comprobación una aprobación integral del diseño ni del CTE.
# Recuperación de originales eliminados · 02/11/2026

Incidencia: el borrador conservaba referencias a 3222271–3222277, ausentes del proyecto. La actualización los trataba como conflicto y bloqueaba su recreación. Se distinguen ahora los elementos realmente inexistentes de los existentes ajenos o modificados. Solo para los inexistentes se retiran las referencias guardadas, conservando posición, tipo, caudal y bloqueos del borrador. Las eliminaciones ya ejecutadas no reaparecen. El historial de deshacer se reinicia al reconciliar estas bajas para no restaurar referencias inexistentes.

Colocar actualiza el borrador contra Revit antes de validarlo. Consulta MCP `7e26b14b3610`: siete IDs ausentes, inventario vacío. Recuperación `a5e66e00f866`: borrador versión 16, siete acciones crear, cero errores. Ensayo reversible `579c223dfc8d`: siete terminales creados y validados, cero eliminaciones, alineación correcta con techos inclinados, desfase de instalación −1 mm; resultado `rolled_back`. No se conservaron los elementos de prueba.

Pruebas independientes: `python -m unittest discover -s tests -p 'test_ventilation_editor.py'`: 47 correctas; incluye conservación del diseño y bloqueos, ausencia de cambios en referencias existentes y retirada de eliminaciones ya realizadas. Compilación de los tres módulos modificados correcta.

