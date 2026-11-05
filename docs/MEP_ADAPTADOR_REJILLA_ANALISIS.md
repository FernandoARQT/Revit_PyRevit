# Adaptador de rejilla: inspección e integración de la orientación

**Documento histórico de análisis.** La implementación posterior, las familias DN125 actuales y las pruebas están descritas en [MEP_ADAPTADOR_REJILLA_IMPLEMENTACION.md](MEP_ADAPTADOR_REJILLA_IMPLEMENTACION.md).

Consulta de Revit: 5 de noviembre de 2026. No se han modificado elementos del proyecto ni se ha implementado todavía la colocación automática.

## Pieza inspeccionada

- Instancia 3368717; tipo 3368037.
- Familia `Fraenkische_profi-air_classic_Codo rejilla`.
- Tipo `profi-air classic outlet 90° plus - 2xNW 90 / DN 125 - short`.
- Conector inferior 1: Ø125, normal (0,0,-1).
- Salidas 2 y 3: Ø90, ambas con normal (1,0,0). Sus centros están separados 108 mm. No son dos ejes divergentes según la API.
- Desde el eje inferior hasta el punto medio de las salidas: desplazamiento lateral 132 mm y elevación 115,5 mm.
- Centro de boca inferior: (55284,159464; 61108,905199; 4475) mm.
- Rejilla coincidente: instancia 3366664, tipo `Rejilla extracción`; su conector 3 es Ø80, normal (0,0,1).
- Ambos conectores coincidentes figuran libres: hay alineación posicional, pero ninguna conexión física confirmada. La clasificación actual leída en esta rejilla y en las bocas del adaptador es `UndefinedSystemType`.

Los parámetros D=125 y D1=90 pertenecen al tipo. No se deben modificar globalmente para resolver una instancia sin revisar antes la compatibilidad del producto.

Las dos salidas parecen una V en la geometría, pero la orientación API de ambos conectores es paralela. La referencia estable de simetría es el vector desde el eje de la rejilla al punto medio de las salidas, proyectado sobre el plano perpendicular al eje. Si en otro tipo las direcciones divergen, también se puede verificar su bisectriz; no se debe deducir la referencia exclusivamente de dos normales casi opuestas o paralelas al eje.

## Definición de las siete posiciones

El pivote será el origen del conector de la rejilla y el eje su normal exterior real, no Z global. Se alinea primero la boca inferior del adaptador con esa normal, en sentido opuesto. Se conserva el asiento observado en la pieza de referencia.

Se proyecta el vector rejilla→centro del distribuidor sobre el plano normal a ese eje. La posición 0° alinea la referencia de simetría del adaptador con esa proyección. Después se generan 0°, −15°, +15°, −30°, +30°, −45° y +45° alrededor del mismo eje. Los límites son respecto de la orientación base hacia el distribuidor, no respecto de los ejes globales del modelo.

En la pieza horizontal inspeccionada, la orientación base hacia el centro del distribuidor de extracción 3230469 resulta aproximadamente −1,847° respecto a +X. Los giros globales correspondientes son −46,847°, −31,847°, −16,847°, −1,847°, 13,153°, 28,153° y 43,153°. Esta cifra es ilustrativa: debe recalcularse para cada rejilla y distribuidor.

Cada pose transforma el cuerpo completo y las dos posiciones y normales de salida. Con un único ramal hay hasta 14 alternativas de conexión: siete poses por dos bocas. No se deben presentar como 14 soluciones ya válidas. Si hacen falta dos ramales, deben compartir una única pose del adaptador y tomas distintas; no pueden seleccionarse dos giros diferentes para la misma pieza.

Si la proyección hacia el distribuidor es nula o casi nula, no hay una dirección base inequívoca. Se debe registrar ese caso y mantener una referencia geométrica estable o solicitar una referencia, sin producir un vector nulo.

## Punto de incorporación en el motor

1. `revit/extractor.py`: identificar el tipo y capturar conectores, geometría sólida, transformación y asociación con la rejilla. Distinguir adaptador ya colocado de plantilla para nuevas piezas. Una coincidencia espacial no demuestra conexión.
2. Construcción de solicitudes, especialmente `systems/heat_recovery_topology.py`: mantener la identidad de la rejilla y su caudal como destino lógico. Añadir un grupo de poses del adaptador y sus posibles puertos físicos; el flexible debe terminar en una salida lateral Ø90, no en la boca vertical de la rejilla.
3. Preparación global y `core/connector_docking.py`: evaluar las poses antes de construir anclas y corredores. La salida ya es lateral; aplicar a la boca vertical el prechequeo de giro del flexible produciría una necesidad de altura ficticia.
4. `core/multi_route.py` y `portfolio.py`: generar candidatos con identidad que incluya pose, boca del adaptador y toma del distribuidor. Reservar el cuerpo real del adaptador y su tapa junto con el flexible. Rechazar poses que invadan cubierta, losa, otros adaptadores o conductos. Para varias rutas del mismo adaptador, imponer coherencia de pose y exclusividad de boca en el selector.
5. `candidate_repository.py`, contexto y cachés: incluir geometría y tipo del adaptador, eje, ángulo, boca, diámetro y asiento en las identidades. Una rotación cambia los extremos y exige nueva validación; no basta con actualizar un ángulo después de aprobar la ruta.
6. `revit/network_model.py`: dentro de la transacción, crear o reutilizar el adaptador, aplicar exactamente la transformación elegida y verificar los conectores reales regenerados. La resolución de conectores y la comprobación de familia deben distinguir la rejilla lógica de la unión física. El código actual espera que el destino físico sea una rejilla y rechazaría directamente este adaptador.
7. Auditoría de conexiones: comprobar rejilla→adaptador→flexible→distribuidor, registrar todas las piezas y tapar las bocas no usadas cuando corresponda. La conexión de un único ramal no justifica dejar la otra boca abierta.

La orientación 0° será la preferida en igualdad de condiciones, pero la prioridad sigue siendo conectar el mayor número de terminales sin interferencias. La desviación angular y longitud son criterios secundarios. No se selecciona primero la pose más corta de cada rejilla para congelarla después: las siete poses permanecen disponibles para la coordinación conjunta.

## Pendiente técnico antes de modelar

Hay que resolver y validar la unión inferior Ø125 del adaptador con la toma Ø80 de la rejilla. No se cambiará silenciosamente el diámetro de una familia ni se inventará una transición de longitud cero. Si se utiliza una transición real, su longitud y volumen deben incorporarse al asiento y a la comprobación de espacio. Tampoco está verificada aún la propagación de caudal a través de esta familia de tres conectores ni su configuración para la red de extracción.

Esta inspección deja definida la variable y sus puntos de integración. Todavía no constituye implementación ni prueba de modelado automático. Las evidencias originales se conservan en `tests/artifacts/terminal-adapter-inspection.json` y `terminal-adapter-orientation.json`.
