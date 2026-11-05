# Actualización del motor conjunto MEP: IRIS y grafo de conjuntos convexos

Estado: cambios de código preparados; validación de esta revisión pendiente por petición del usuario. No se han ejecutado nuevas pruebas ni consultas a Revit después de la instrucción de detenerse antes de validar. Las pérdidas de presión quedan fuera de la prioridad de esta fase.

## Flujo implementado

1. Se intentan primero las conexiones directas y los pasos autorizados sencillos. Una solución sencilla comprobable no necesita construir todo el grafo.
2. `core/iris_gcs.py` construye un conjunto limitado de celdas convexas libres mediante planos separadores y expansión de un elipsoide inscrito. Se descuentan la sección exterior, el aislamiento y la holgura. Cada plano que separa un obstáculo se contrasta con todos los vértices de su envolvente convexa.
3. Solo se unen regiones de plenum cuyos planos inferior y superior coincidan exactamente. La unión conserva los huecos y las separaciones reales. Las coberturas convexas pueden solaparse para no crear cortes artificiales al dividir una planta no convexa. Los obstáculos originales siguen presentes.
4. Se construye un grafo de solapes y pasos admitidos. En cada solape se busca un punto con máxima distancia a sus límites. Para cada camino de celdas se optimiza después la posición de los puntos de paso en coordenadas locales expresadas en metros.
5. Se comparan tanto la trayectoria acortada como la que conserva los puntos interiores. Ambas deben superar la comprobación completa de tramos, arcos de radio finito, reservas y espacio permitido. Una trayectoria de puntos libre no basta para aceptar sus giros.
6. Si este backend no produce una solución verificable, continúan el mapa de corredores y la búsqueda multirresolución existente. Los motivos y límites de IRIS/GCS se conservan en `attempts` aunque finalmente se utilice otro motor.
7. `core/portfolio.py` combina alternativas de todas las conexiones, incluidos sentidos de búsqueda y tomas equivalentes del mismo distribuidor. `core/integer_master.py` utiliza un modelo binario con SciPy/HiGHS y añade exclusiones cuando la comprobación de envolventes descubre conflictos. Sin SciPy se mantiene la selección en Python.
8. Se reserva parte del tiempo para reparar vecindarios de rutas conflictivas. La primera selección conjunta no puede consumir todo ese presupuesto. Se conservan las rutas bloqueadas y las ajenas a una selección recalculada.
9. `native_feedback.py` puede volver a buscar únicamente las rutas con errores de validación nativa, excluyendo la geometría ya rechazada en ese intento. No convierte un fallo numérico o un tiempo agotado en una colisión demostrada. Este proceso de diseño y consulta no crea conductos.

## Alcance exacto de los algoritmos

La expansión sigue el esquema de IRIS: alternar separación de obstáculos y optimización del volumen del elipsoide. SciPy/SLSQP realiza las optimizaciones numéricas; no se afirma convergencia global. Las celdas usan envolventes convexas conservadoras de los obstáculos y de los conductos reservados, por lo que pueden descartar huecos que otro método encuentre.

El grafo contiene conjuntos convexos y optimiza continuamente cada camino discreto considerado. La implementación usa **enumeración acotada de caminos**, no la relajación convexa global de GCS ni el solver de Drake. No demuestra el óptimo global ni que una conexión sea imposible cuando agota sus candidatos. El modelo binario selecciona entre las alternativas generadas; tampoco representa todos los recorridos continuos posibles.

Los límites actuales son 24 celdas por intento, ocho caminos completos y 2.000 expansiones de caminos del grafo. `coordination.iris_gcs_enabled` permite desactivar esta fase; `coordination.max_iris_cells` admite de 2 a 64. Por defecto se asigna hasta el 30 % del tiempo restante de un intento a IRIS/GCS, y la mitad de ese intervalo a preparar e inflar celdas. Los límites son cooperativos: una llamada individual a una biblioteca puede terminar después de su plazo.

SciPy es necesario para IRIS/GCS, el refinamiento continuo y el selector HiGHS. La búsqueda A* y el selector en Python siguen disponibles sin esa dependencia. `scientific_context` permite localizar SciPy junto a la instalación de Shapely cuando MCP restaura `sys.path` entre comandos.

## Reglas conservadas

- El espacio permitido comienza por encima de la cara inferior real del falso techo y sigue sus planos inclinados.
- La cubierta «Teja sobre forjado» permanece prohibida. IRIS/GCS no habilita cruces adicionales.
- Los pasos estructurales mantienen su clasificación, ámbito y aprobación técnica. Se utiliza el mismo filtro de admisión de pasos que A*.
- Los conductos reservados participan en la separación de obstáculos. La comprobación final conserva también sus cápsulas originales.
- Las tomas alternativas pertenecen al mismo distribuidor, red y tamaño compatible. La toma elegida se conserva en los datos usados por el modelado.
- Las transiciones y conexiones físicas siguen necesitando comprobación sobre los accesorios reales. Encontrar un eje central no certifica una instalación completamente conectada.
- No se exige ni se inventa información de pérdidas de presión para encontrar recorridos. Un resultado sin datos de fabricante no equivale a una pérdida nula.

## Validación que queda pendiente

Se ha preparado `tests/test_routing_iris_gcs.py` para comprobar separación de obstáculos y reservas, elipsoides contenidos, huecos, esquinas, continuidad entre regiones compatibles, planos inclinados, barreras prohibidas, cancelación y ausencia de SciPy. Se debe ejecutar junto con las pruebas existentes y repetir después el cálculo con la geometría capturada del proyecto y la validación nativa de Revit, sin conservar cambios en el modelo.

La referencia anterior del proyecto era cuatro de nueve recorridos centrales verificados y cinco pendientes. No constituye un resultado del código recién añadido. Hasta completar la próxima fase no se acredita mejora en número de rutas, tiempo de cálculo ni conexiones reales.

Secuencia prevista: pruebas independientes, ensayo con la geometría capturada, comparación del conjunto de nueve rutas y consulta nativa de los resultados. Si aparecen fallos, se corrige el código antes de declarar concluida la validación.

## Segunda revisión tras el ensayo de cuatro rutas

Los registros del ensayo mostraban intentos inversos rechazados por `WAYPOINT_BLOCKED`, fases de corredores con cero estados explorados y fracciones de tiempo demasiado pequeñas para iniciar IRIS. La causa de cada conexión pendiente aún requiere comprobación; esos registros sí justifican corregir la planificación del presupuesto y la aproximación opcional.

Cambios preparados, todavía sin validar:

- La aproximación de longitud R deja de convertirse en un waypoint obligatorio si queda bloqueada. R corresponde al recorte de un giro de 90°, y no a todos los ángulos. Los waypoints impuestos por el usuario siguen siendo obligatorios y la conexión física sigue pendiente.
- Cada ruta genera hasta dos variantes por pasada, intercalando búsqueda inversa y directa de una misma toma y rotando las tomas entre pasadas. Se evita dividir su tiempo en seis o más inicializaciones. La búsqueda sigue siendo acotada y puede no recorrer todas las variantes.
- Las pasadas siguientes reutilizan los recorridos certificados anteriores y dedican la búsqueda inicial a los pendientes. La negociación y selección conjunta mantienen la posibilidad de sustituir rutas movibles.
- IRIS se aplaza cuando su fracción de tiempo es menor que `coordination.iris_min_seconds` (3 s por defecto). El aplazamiento se registra y deja continuar los motores existentes.
- Se reutilizan las envolventes estáticas y las coberturas convexas entre clones de un mismo SpatialModel. Se conservan referencias a las listas de geometría para distinguir escenas; se retienen como máximo dos escenas. Restricciones y reservas de conductos se incorporan de nuevo en cada intento. Una nueva extracción con otras listas invalida esa reutilización.
- Los diagnósticos del selector conservan los intentos de cada candidato para que un motivo general no oculte qué fase agotó su tiempo.

Se han añadido casos de prueba para reservas nuevas con caché estática, aplazamiento por tiempo y distinción entre aproximación opcional y waypoint del usuario. Esta revisión debe comprobarse en la siguiente fase; el resultado de cuatro rutas pertenece al código anterior.

## Tercera revisión: admisión de tomas y mapa con comprobación diferida

La segunda revisión obtuvo 128/129 pruebas, mantuvo cuatro rutas verificadas en Revit y dejó cinco pendientes. Se detectó una regresión: los dos intentos podían gastarse en la misma toma bloqueada antes de probar una alternativa. Los registros también mostraban mapas con cero estados explorados y búsquedas de grilla sin tiempo después de las fases previas.

Correcciones preparadas, aún sin ejecutar:

- Se comprueba el arranque de cada toma antes de limitar los intentos. Las tomas sin arranque libre quedan diagnosticadas como `NO_FREE_BULK_START`; no consumen las variantes de búsqueda. La admisión usa el perfil y las reservas vigentes.
- Cada toma admitida recibe una oportunidad antes de repetir la misma en sentido contrario. El orden de tomas y el sentido inicial rotan entre pasadas. Se realizan hasta `max(2, número de tomas admitidas)` variantes, limitadas además por el tiempo y el banco de candidatos. Se conservan las restricciones del escenario A; las alternativas estructurales explícitas siguen utilizando el escenario B existente.
- La prueba de atajos de un solo portal dispone de un máximo de 0,25 s o del 10 % del tiempo restante, el menor. Esta fase no resuelve varios muros y ya no debe consumir todo el intento buscando un atajo inexistente. Una comprobación individual no es interrumpible.
- La preparación del mapa propone puntos sin certificar cada uno por adelantado. Las aristas visitadas comprueban su cápsula completa; los pasos comprueban además ambos extremos, obstáculos y reservas. Los arcos mantienen su comprobación durante la búsqueda y el recorrido final conserva la validación completa.
- El generador de vecinos comprueba también el plazo y la cancelación entre aristas bloqueadas, incluso cuando no produce ningún vecino.
- Los diagnósticos del mapa separan segundos de preparación y búsqueda y cuentan puntos, aristas comprobadas y estados explorados. El intento registra asimismo cuántos atajos de un solo paso se probaron.

Se mantienen los ensayos de seguridad anteriores y se añaden casos de prioridad entre tomas distintas y consultas diferidas del mapa. No se acredita ninguna mejora del proyecto hasta ejecutar la siguiente validación.
