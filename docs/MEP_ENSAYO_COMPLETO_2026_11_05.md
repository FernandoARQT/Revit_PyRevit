# Ensayo completo de instalación interior

Alcance solicitado: recuperación de calor, principales recuperador–distribuidor y ramales interiores siempre con `Flex - Redondo`. Los conductos rígidos al exterior quedan fuera del alcance.

## Correcciones

- Se sustituyó la creación rígida de principales interiores por flexible, conservando el dimensionado y las transiciones configuradas.
- Las configuraciones antiguas `RIGID_CIRCULAR` se migran a `FLEXIBLE_CIRCULAR` para estas principales interiores.
- Se tapan las tomas de distribuidores que no tengan una ruta realmente modelada. Una propuesta pendiente no reserva indefinidamente una toma abierta.
- Las tapas de distribuidor creadas por ARQ-T quedan identificadas y se pueden retirar durante la preparación para recalcular, sin eliminar tapas manuales.
- Se ensayaron controles de spline densificados y una representación por piezas flexibles. La representación por piezas no superó las comprobaciones nativas y se retiró del código.

## Resultado comprobado

Las dos principales interiores se crean como flexibles Ø125, con una transición por principal a la toma del distribuidor y ambas conexiones confirmadas. Las diez tomas de ramal sobrantes pueden cerrarse con tapas conectadas.

Ninguno de los siete ramales quedó modelado y validado en el ensayo completo. Una ruta que había pasado la comprobación central colisionó con tabiques al inspeccionar la geometría nativa del flexible y se revirtió. Se recalculó individualmente incorporando los conflictos detectados, sin superar la validación del cuerpo real. También se ensayó individualmente otra rejilla próxima al recuperador, que no superó la validación geométrica.

Los caudales medidos en ambos extremos de las principales fueron **0 L/s**, coherentes con la ausencia de terminales conectados. Esto **no verifica transmisión de caudal desde una rejilla hasta el recuperador**. No se han asignado caudales ficticios para presentar un resultado positivo.

Por tanto, la herramienta **todavía no está corroborada de extremo a extremo**, ni están verificadas todas las opciones de interfaz. Pasan 203 pruebas locales, y los ensayos de principales y conexiones cortas son positivos, pero queda pendiente la discrepancia entre los recorridos centrales y los cuerpos nativos de los ramales flexibles, además de las rutas todavía sin resolver.

## Evidencias y reproducción

- `tools/revit_complete_network_trial.py`: cálculo, aprobación, modelado, recálculo focalizado y lectura de caudales.
- `tools/revit_replay_complete_trial.py`: reasocia identificadores de adaptadores y reproduce un trazado calculado; permite conservar únicamente las principales verificadas.
- `tests/artifacts/complete-network-trial.json`: resultado completo y recálculo individual.
- `tests/artifacts/complete-network-proposal.json`: propuesta utilizada.
- Ensayos MCP: `4ffcd3732af9`, `5ece98910dd0`, `13180c64564d`, `219af76c0854`, `37ab436d1c2b`. Esos ensayos se revirtieron.

La operación posterior para conservar las dos principales está sometida a la aprobación propia de Revit, según la política MCP `ask`. No equivale a una instalación completa ni a una comprobación positiva del caudal de los ramales.

## Verificación posterior a conservar cambios

Se comprobó mediante API que las principales **3386280** y **3386288** existen como `FlexDuct`, tienen sus dos extremos físicamente conectados al recuperador y a sus transiciones, y que existen diez tapas identificadas de distribuidor. Ambos extremos de ambas principales leen **0 L/s**. La llamada que esperaba la aprobación agotó su tiempo de transporte, por lo que se verificaron los elementos directamente; no se dedujo la conservación del resultado de esa llamada.

El diagnóstico posterior encontró que la validación del recorrido central podía omitir la viabilidad de los giros al añadir el tramo final de conexión al adaptador. Ahora se reserva espacio lateral dependiente del radio de giro y de la envolvente, se exige una aproximación alineada a la salida lateral y se comprueba la constructibilidad de los giros del recorrido completo, incluso cuando la conexión nativa sigue pendiente. No se añade un desfase vertical fijo.

El ensayo individual `2f79efd7ebfe`, con estas comprobaciones, agotó 90 segundos sin obtener un recorrido certificado; no modeló ni conservó ningún ramal. Esto evita presentar como construible el candidato anterior, pero todavía no resuelve los ramales.
