# Validación de integración pendiente · Revit 2025

**Por habitación, revisión actual:** se ha sustituido el corte IFC que fallaba para la puerta 186796 por medios rectángulos calculados con Anchura del tipo y caras reales del muro. Cada suelo debe llegar al mismo plano medio, conservar todo el ancho de paso y ocupar media profundidad. Probar ambos suelos juntos y generados por separado; verificar encuentro exacto, límites que ya invaden el hueco, línea de ubicación del muro desplazada, FromRoom/ToRoom invertidos y conservación de curvas en habitaciones con arcos. La suite independiente usa GEOS y dobles; el modelo real sigue pendiente.

**Criterio confirmado:** el suelo continuo debe dejar huecos bajo los tabiques y unir habitaciones por puertas y separadores. Sustituye el criterio inicial de pavimentar también bajo tabiques. La revisión ha retirado todo relleno por franjas, huellas materiales y límites al centro.

Si la selección incluye regiones totalmente independientes por muros, se genera un suelo por grupo conectado. Probar el caso de la captura: conjunto principal de siete habitaciones y habitación 6 aislada, separados aproximadamente 40 mm; se deben preparar y crear ambos suelos. La confirmación debe indicar dos suelos y sus habitaciones. Si falla cualquiera, revertir ambos y conservar los suelos anteriores.

**Estado: validación completa pendiente.** El usuario ha generado un suelo con la estrategia anterior y ha confirmado que los recortes bajo tabiques no coincidían con lo que necesitaba. La unión actual se ha comprobado con GEOS y geometrías de referencia; sigue pendiente verificarla en su modelo. Usar una copia de prueba del proyecto, una planta con fase coherente y un tipo normal de pavimento.

El continuo utiliza `Room.GetBoundarySegments(Finish)` y rectángulos calculados con **Anchura del tipo de puerta** y caras reales del muro. Extrae el exterior y todos los anillos interiores de esa unión. No consulta sólidos de muro ni límites al centro para rellenar tabiques. Los separadores de habitación se eliminan como líneas internas de la unión y no necesitan puerta.

Las pruebas verifican puntos bajo tabiques sin pavimento y puntos en pasos de puerta pavimentados, incluso en una planta girada. Son geometrías de referencia; no son una extracción del RVT de la captura. La limpieza solo elimina residuos menores que la precisión/tolerancia documentada y muestra la superficie ajustada en la confirmación.

Repetir la selección real de ocho habitaciones. Comprobar que el porche queda completamente excluido, que se conservan TODOS los recortes bajo tabiques de baños/vestidor/despacho y que solo los pasos correspondientes a puertas añaden superficie. Inspeccionar tanto los entrantes exteriores como los anillos interiores del boceto. Revisar la posición del origen de las familias respecto al centro de Anchura.

Cerrar toda la ventana y abrirla de nuevo desde el botón para cargar esta revisión. Si hay desconexión, un paso no verificable o un encuentro no reparable, el error guarda un JSON con el cálculo que permite reproducirlo sin Revit.

| Caso | Comprobación dentro de Revit | Estado |
| --- | --- | --- |
| 1. Rectangular sin puertas | Un suelo; perímetro en acabado interior y área correcta. | Pendiente |
| 2. Dos habitaciones con puerta | Dos suelos; cada uno con toda la Anchura del tipo y media profundidad del tabique, sin hueco/solape. Repetir con ubicación desplazada y creando cada habitación en una ejecución distinta. | Pendiente |
| 3. Selección múltiple | N habitaciones seleccionadas → N suelos; ninguna otra. | Pendiente |
| 4. Continuo | Conservar los huecos y entrantes bajo TODOS los tabiques; pavimentar solo habitaciones y pasos. Crear un suelo por cada grupo conectado, incluidas habitaciones aisladas. | Pendiente |
| 5. Selección parcial | Excluir la habitación vecina, también con muros compartidos. Las regiones seleccionadas desconectadas deben generarse todas, sin unirlas artificialmente. | Pendiente |
| 6. Irregular y diagonal | Respetar lados y vértices exactos, incluido tabique que llega a cerramiento diagonal. El modo continuo debe avisar ante un arco; el modo por habitación debe conservarlo. | Pendiente |
| 7. Otro nivel | Conservar XY; comprobar `LevelId`, geometría y tabla de planificación. | Pendiente |
| 8. Desfases | +120, −120, 0 y +12,5 mm; verificar parámetro y cota superior respecto al nivel de destino. | Pendiente |
| 9. Puerta a no seleccionada o exterior | En Por habitación, llegar al centro del paso incluso sin habitación al otro lado. Probar solo FromRoom, solo ToRoom y límite original que ya ocupa todo el hueco; no pavimentar la mitad exterior. | Pendiente |
| 10. Fallo en un suelo | Forzar perfil incompatible o un fallo de creación después del primer suelo; no debe conservar ninguno de esa ejecución y debe mantener los anteriores. | Pendiente |
| 11. Separadores | Dos habitaciones contiguas separadas por línea: un suelo continuo sin puerta. Seleccionar solo una: excluir completamente la otra. | Pendiente |

Comprobar además: recarga y disponibilidad del botón; CPython 3.12.3 en el logger; confirmación antes de crear; cancelar sin cambios; aviso de duplicados al repetir; habitaciones excluidas por fase/superficie; puerta con FromRoom/ToRoom incompleto; Anchura de tipo ausente o inválida; modo por habitación cuando el límite ya entra en el hueco; cierre modal centrado sobre Revit; escalado de Windows al 100/150/200 % y monitor secundario; selección al ordenar/filtrar; tipos/materiales/espesores conservados; una única operación Deshacer tras el éxito.
