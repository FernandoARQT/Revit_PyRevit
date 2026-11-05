# Cálculo MEP en varios procesadores

Estado: revisión de reutilización validada el 3 de noviembre de 2026. Pasan 138 pruebas; el ensayo del proyecto conserva cuatro de nueve rutas. Los resultados completos y sus límites se detallan al final.

## Reparto

`routing/parallel.py` gestiona procesos externos de CPython mediante un protocolo privado de mensajes JSON. `routing/parallel_worker.py` es su punto de entrada, sin botón ni acceso a Revit. Los procesos se reutilizan durante la optimización y se cierran al finalizar la sesión o cancelarla expresamente. Al agotar un lote se solicita cancelación cooperativa y se conservan los trabajadores; solo se sustituye a uno si sigue sin responder después de dos segundos de margen. Los archivos temporales contienen exclusivamente geometría y mensajes de esa sesión y se retiran al cerrar.

El límite es **máximo(1, procesadores lógicos disponibles − 2)**. Se consulta la afinidad del proceso cuando el sistema la facilita; en su defecto, `os.cpu_count()`. Solo se inician tantos trabajadores como trabajos tenga el lote, sin superar ese límite. Con menos de tres procesadores disponibles no es posible dejar dos libres y calcular a la vez; se usa uno. Es un límite de concurrencia, no una reserva exclusiva de núcleos del sistema operativo.

Se configuran OpenMP, OpenBLAS, MKL y NumExpr a un hilo por trabajador, y HiGHS a un hilo, para evitar paralelismo numérico anidado. La opción de hilos de HiGHS se transmite a través de SciPy. No se modifica la afinidad ni la configuración global de Revit.

Fases conectadas al motor externo:

- Búsquedas iniciales independientes de terminales pendientes.
- Generación simultánea de variantes de toma y sentido de búsqueda.
- IRIS/GCS, corredores y grilla dentro de cada búsqueda independiente.
- Refinamiento continuo de candidatos.
- Comprobación geométrica independiente de los candidatos frente a reservas protegidas.
- Comprobaciones de incompatibilidad entre parejas de candidatos, agrupadas en lotes cuando hay suficientes parejas para amortizar el transporte.
- Consulta de alternativas en ambos sentidos y escenario estructural explícito existente.

La negociación de reparaciones que depende de la ruta anterior, la selección binaria del conjunto, el acceso al documento, la validación nativa y el modelado permanecen coordinados. Las rutas calculadas en paralelo no se incorporan directamente como una red válida: primero se comprueba su compatibilidad y la exclusividad de las tomas. Bloqueos y rutas fuera de una selección recalculada conservan su protección.

## Ejecución y diagnóstico

`parallel.enabled` está activo por defecto. Puede desactivarse para comparar con el cálculo secuencial. Se busca un ejecutable Python externo; `ARQT_ROUTING_PYTHON` permite indicar su ruta. Necesita las dependencias geométricas del motor; SciPy sigue siendo opcional para los backends que lo admiten. No se intenta ejecutar Revit como intérprete Python.

Los mensajes incluyen un plazo de cálculo. La cola consume tiempo del presupuesto; no se concede el plazo íntegro a un trabajo que ha estado esperando. El padre recoge resultados completos y descarta trabajos sin respuesta válida. Los errores de proceso, dependencias, cola y cancelación se registran; no se convierten en geometría aprobada. Cuando queda presupuesto puede utilizarse la alternativa secuencial.

`proposal.stats.parallel` contiene procesadores detectados, límite de trabajadores, número de procesos iniciados a lo largo de la sesión, lotes, operaciones, procesos que respondieron, errores y tiempos. Los archivos de prueba independientes usan el modo secuencial salvo los ensayos de integración que activan expresamente los procesos externos.

## Próxima validación

Ejecutar `test_routing_parallel.py` y la batería del motor; comprobar limpieza y cancelación, errores de dependencias, varios procesos reales, límites de concurrencia y equivalencia geométrica. Repetir después el ensayo de las nueve rutas y la consulta nativa en Revit. El resultado previo era cuatro rutas verificadas; todavía no se acredita mejora de calidad o tiempo con esta implementación.

Referencias del control de hilos: [HiGHS, opciones](https://ergo-code.github.io/HiGHS/dev/options/definitions/) y [SciPy, interfaz de opciones de HiGHS](https://github.com/scipy/scipy/issues/17250).

## Revisión de reutilización y presupuesto — pendiente de validar

El primer ensayo paralelo superó 135 pruebas y confirmó el límite de 14 trabajadores sobre 16 procesadores lógicos. Conservó cuatro rutas calculadas y cinco pendientes. Los vencimientos de lote provocaron 51 arranques acumulados y se asignaban intervalos decrecientes a los últimos trabajos de la cola. Ese resultado no acredita mejora de calidad ni tiempo frente al cálculo anterior.

La revisión siguiente incorpora:

- Registro de trabajos activos durante toda la sesión, no solo durante una llamada a `run`.
- Cancelación por archivo de control independiente para cada lote. Los bucles de búsqueda y refinamiento reciben esa señal; una operación individual puede tardar en devolver el control.
- Recogida de resultados antes de comprobar el vencimiento. Las respuestas tardías de un lote anterior se drenan y registran, pero nunca se asignan a índices del lote nuevo.
- Conservación de trabajadores y cachés al vencer un lote. Los trabajadores que ignoren la cancelación durante más de dos segundos se sustituyen individualmente cuando el coordinador vuelve a supervisarlos; los otros permanecen vivos. Nunca se supera el límite simultáneo configurado.
- Intervalo nominal por trabajo calculado una sola vez por lote y ajustable al alza según las tandas restantes. No se vuelve a dividir el tiempo restante por el número inicial de tandas en cada despacho.
- Inicio del intervalo de cálculo después de preparar el contexto en el trabajador, sujeto siempre al plazo final del lote y a un margen para devolver la respuesta.
- Reutilización de la geometría preparada entre lotes con las mismas restricciones, incorporando de nuevo las reservas vigentes. Una modificación de las restricciones reconstruye la base.
- Una toma focal por ruta y pasada, rotada entre pasadas. Las pendientes reciben ambos sentidos y las ya resueltas solo una variante; si se permite una única pasada se consideran todas las tomas admitidas. Los trabajos se intercalan por terminal, evitando que seis variantes de uno ocupen toda la primera tanda.
- Diagnóstico separado de espera/arranque, preparación y cálculo, presupuesto asignado, respuestas antiguas retiradas y sustituciones forzadas.

Se han escrito pruebas de reutilización entre lotes, cancelación con respuesta tardía, aislamiento de índices, presupuesto no decreciente de la cola y orden de trabajos por terminal. Se ejecutaron posteriormente con los resultados siguientes.

## Validación de la revisión — 3 de noviembre de 2026

- Pruebas específicas de procesos: 7 correctas. Batería `test_routing*.py`: 138 correctas, 57,633 s.
- Ensayo capturado de nueve rutas con presupuesto de 120 s: 117,531 s, cuatro válidas con advertencias y cinco pendientes (`NO_CERTIFIED_ROUTE_IN_SEARCH_LIMITS`). No demuestra que las cinco sean geométricamente imposibles.
- Hardware detectado: 16 procesadores lógicos, límite de 14 trabajadores. Se iniciaron 14 procesos acumulados, frente a los 51 del ensayo anterior; todos los lotes terminaron sus trabajos, sin sustituciones forzadas registradas.
- Variantes: lotes de 14 trabajos intercalados, frente a los 46 anteriores. La preparación reutilizada del contexto ronda 0,016–0,032 s en la mayoría de trabajos.
- Consulta nativa MCP `d9a3310a5e3e`: las cuatro rutas calculadas conservan `VALID_WITH_WARNINGS`, sin errores geométricos nativos; las otras cinco siguen `NOT_EVALUATED`. Documento sin modificaciones por esta consulta. No se han modelado conductos ni verificado conexiones físicas de accesorios.
- La batería y el ensayo se ejecutaron parcialmente a la vez: estos tiempos no son una comparación controlada de rendimiento. No se acredita aceleración total ni mejora del número de rutas resueltas.

La siguiente fase de código debe investigar las cinco búsquedas pendientes. Se cierra aquí la fase de validación para respetar la separación solicitada entre cambios con razonamiento alto y comprobaciones con light.
