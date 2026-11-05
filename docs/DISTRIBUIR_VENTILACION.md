# ARQ-T · Distribuir ventilación

Actualizado el 02/11/2026. Se modifica el botón existente **ARQ-T → MEP → Distribuir ventilación**. Revit 2025, pyRevit CPython 3.12 y Shapely 2.1.1. No se instala otro servidor MCP. El trazado de conductos sigue siendo una herramienta independiente.

## Uso

1. En **Habitaciones**, indique la vivienda y marque las habitaciones interiores que la forman; **Todas/Ninguna** facilita la selección. La casilla de pertenencia a vivienda es la única que se conserva. No se muestran porches/terrazas; vestidores y transferencias sí. Revise fase, ámbito, usos y principal automático.
2. Pulse **Calcular y distribuir**. En **Distribución** solo hay una fila con **+ Impulsión**, **+ Extracción**, **Eliminar**, **Ajustar plano** y **Más opciones**. El panel duplicado de propiedades y las casillas de aceptación/bloqueo se han eliminado. Los avisos están en un desplegable cerrado.
3. Edite con doble clic las celdas **Tipo**, **L/s**, **X** o **Y**. Tipo es un desplegable Impulsión/Extracción. Cada cambio se aplica al salir de la celda; no necesita un botón de guardar. Z es informativa y se recalcula con la cara inferior real del falso techo. Cambiar un eje conserva exactamente el otro, sin redondearlo al valor mostrado.
4. También puede seleccionar una rejilla en el plano y arrastrarla. Doble clic sobre ella abre su caudal en la tabla. Rueda: zoom; botón derecho/central: desplazar. Una posición inválida se rechaza. Seleccionar sin desplazar no cambia el borrador ni marca una posición como manual.
5. Para añadir, pulse **+ Impulsión/Extracción** y después el punto del plano. **Eliminar** o Supr elimina la provisional o marca la nativa propia para borrado posterior. Añadir redistribuye los caudales automáticos de su función. Una función no prevista requiere confirmación y parte de 8 L/s provisionales, editables en tabla. Un cambio de tipo o eliminación puede requerir corregir el reparto.
6. El diseño por estancia se edita en la tabla **Caudales**, separado del Flow individual. **Calcular y distribuir** incorpora ese diseño y distribuye las bocas automáticas conservando ajustes manuales. **Más opciones → Equilibrar caudales** muestra antes los ajustes y preserva caudales manuales/bloqueados; hay otra entrada explícita para incluir los manuales.
7. Las funciones secundarias están en **Más opciones**: cocina, bloqueos individuales, alternativas, reoptimización, deshacer/rehacer, recuperación e imagen. Los bloqueos se alternan con comandos Bloquear/Desbloquear, sin casillas. Cocina admite referencia/polígono/detección basada en elementos reales; Enter termina el polígono, Esc cancela. La zona detectada es una hipótesis funcional revisable.
8. Pulse **Aplicar en Revit**. Una confirmación conjunta muestra el número de rejillas, las eliminaciones propias y los avisos funcionales pendientes. No hay aceptación por filas ni casilla adicional de borrado. Solo tras esa confirmación se aprueba y aplica la versión actual, con transacción atómica y validación nativa. Las incidencias geométricas o de caudal impiden aplicar.
9. Cada edición se guarda automáticamente y la propuesta sigue disponible después de colocar. **Más opciones → Recuperar propuesta guardada** abre la más reciente de esa vivienda. Una vivienda/fase diferente conserva los borradores anteriores. Cambios externos del modelo requieren **Habitaciones → Actualizar**; los cambios manuales externos de nativos propios no se adoptan silenciosamente.

El botón abre una ventana no modal. Las operaciones que leen/escriben Revit se envían mediante `ExternalEvent`; los movimientos del ratón usan exclusivamente geometría cacheada. Reabrir el botón activa la ventana existente; después de cerrarla, abre los módulos actuales, evitando código anterior retenido por CPython. No se ejecuta una recarga de pyRevit desde MCP.

## Cálculo y criterio técnico

Fuente: [DB-HS oficial, HS 3, tabla 2.1](https://www.codigotecnico.org/pdf/Documentos/HS/DBHS.pdf), documento de 14/06/2022, comprobado el 02/11/2026.

| Dormitorios de la vivienda | Principal | Secundario | Estar/comedor | Extracción total mínima | Mínimo por local húmedo |
|---|---:|---:|---:|---:|---:|
| 0–1 | 8 L/s, si existe | — | 6 L/s | 12 L/s | 6 L/s |
| 2 | 8 L/s | 4 L/s | 8 L/s | 24 L/s | 7 L/s |
| 3 o más | 8 L/s | 4 L/s | 10 L/s | 33 L/s | 8 L/s |

La prioridad automática de principal es: nombre principal/Hab. Ppal, habitación sin número, habitación 1 y finalmente mayor superficie. Los empates se resuelven de forma estable. La selección manual se guarda por documento y vivienda. «Hab. Ppal Vestidor» se reconoce como vestidor antes de buscar dormitorios. Los despachos y otros usos secos análogos requieren clasificación; los usos secos combinados toman el mayor mínimo aplicable. Un local mixto tiene funciones seca y húmeda diferenciadas, sin modificar la Room.

La superficie reparte incrementos de equilibrado por encima de mínimos y solicitudes de diseño; no se inventa un coeficiente general L/s·m². El objetivo inicial es el máximo entre sumas de impulsión/extracción y mínimo global húmedo. Editar Flow no modifica automáticamente los demás terminales. Se mantienen separados mínimo CTE, diseño de estancia, suma de bocas y diferencias. El reequilibrado explícito busca una solución compatible con caudales fijos y bloqueos, o informa que no existe con esas restricciones. La campana de cocción es un sistema independiente; no se incluye en estas sumas ni se dimensiona aquí.

El reparto proporcional es un criterio del programa, no un reparto obligatorio del CTE. En una Room mixta se utiliza actualmente su superficie completa como peso de cada función, también de extracción; una zona de cocina delimitada orienta las posiciones, pero no sustituye automáticamente ese peso por el área de cocina. Por eso puede concentrarse más incremento en esa extracción. El usuario puede ajustar el reparto conservando mínimos locales, mínimo global y equilibrio.

Las configuraciones anteriores conservan los caudales solicitados por el usuario; migrar la política de mínimos no reduce esos valores de diseño automáticamente.

## Geometría, familias y Flow

Se leen contornos reales a cara de acabado, huecos, puertas y separadores virtuales de Room. Las curvas se subdividen con tolerancia de 0,05 mm. Los separadores virtuales se dibujan discontinuos y enlazan el grafo de transferencia sin crear huecos arquitectónicos. Este grafo no acredita las secciones efectivas de paso.

Se usan las caras inferiores planas reales de Ceilings horizontales/inclinados, con origen, normal, nivel y distintos paños. La altura se evalúa por la ecuación del plano en cada XY. La inserción se corrige a lo largo de la normal según el plano visible de la familia, y la instancia se orienta en 3D; no se confunde la cota de inserción con la cara visible inferior. Se comprueban Room, bordes, separación entre bocas, obstáculos y reserva superior provisional de conexión. El arrastre emplea caché; la aplicación final verifica la geometría sólida nativa, la coincidencia de planos, nivel, desfase, orientación, tipo y lectura de Flow. Tolerancia angular: 0,01°; geométrica inicial: 1 mm.

Por indicación del usuario, el desfase final de instalación se reduce **0,0010 m (1 mm vertical)** respecto a esa inserción calculada. Se mantienen XY y orientación, también sobre techos inclinados. Se asigna la cota absoluta corregida, sin restar de nuevo al desfase de una instancia ya instalada. La cara visible queda 1 mm por debajo de la cara de referencia en Z. La validación compara contra ese plano desplazado y los metadatos registran `installation_offset_mm=-1`; al reutilizar se recupera la cara de referencia original. Los elementos antiguos que requieren bajar se actualizan solo al aplicar la propuesta; una posición bloqueada impide esa corrección hasta desbloquearla.

Familia compatible comprobada, categoría `OST_DuctTerminal`, alojamiento `OneLevelBased`:

- `Rejillas de ventilación (Rejilla de admisión techo)`.
- Tipos: `Rejilla impulsión` y `Rejilla extracción`.
- Parámetro **de instancia `Flow`**, Double, dato Air Flow, editable. Conversión explícita a L/s con `UnitUtils`; lectura posterior tras `Regenerate`. No se modifican tipos ni familias. Otras formas de alojamiento se rechazan hasta implementar y verificar su adaptador.

**Flow almacena diseño y no está asociado al conector HVAC de estas familias.** No transmite automáticamente el caudal al futuro conducto. La reserva superior inicial es de 100 mm y requiere contrastar las dimensiones de accesorios reales.

El descarte por envolventes es conservador y puede rechazar posiciones físicamente posibles. Una booleana no resuelta o un vínculo próximo bloquea la aplicación. Un muestreo discreto puede no encontrar una posición posible entre muestras; el arrastre permite posiciones continuas sujetas a las mismas comprobaciones. Reoptimizar sin alternativa conserva la posición e informa de la incidencia.

## Persistencia y concurrencia

`VentilationProposal`, esquema 2, contiene documento, vivienda, fase, ámbito, Rooms, contornos, planos de techo, tipos, bocas con identificador estable, caudales, zonas, bloqueos, aprobación y versión. Coordenadas en milímetros del sistema interno del proyecto, no coordenadas compartidas/topográficas. El marco de la vista y su inversa convierten a WPF DIP, con una sola inversión de Y. Zoom, tamaño y DPI no alteran datos del modelo.

WPF y MCP comparten JSON en `ARQ-T.extension/data/ventilation/<hash-documento>/<proposal_id>.json`. Cada edición exige `expected_version`, comprueba integridad y escribe mediante sustitución atómica con bloqueo de archivo. Una versión antigua produce `VERSION_CONFLICT`. La interfaz consulta cambios de archivo cada 800 ms sin releer la geometría Revit; no interrumpe un arrastre ni sobrescribe un campo con foco. Conserva hasta 30 pasos de historial. Después de aplicar a Revit se vacía el historial para no deshacer nativos mediante la edición de un borrador.

El directorio debe permitir escritura. Para otro ordenador se necesitan extensión, dependencias y los borradores si quiere continuar las mismas propuestas. El bloqueo protege clientes sobre el mismo sistema de archivos; no garantiza exclusión distribuida entre copias independientes sincronizadas por OneDrive. No edite simultáneamente la misma propuesta desde dos ordenadores con copias locales.

Los nativos propios conservan Extensible Storage `329f0e36-b871-4b67-b924-ea6d2f5bd5e4`, con documento, Room, función, posición, orientación, Flow, identidad de propuesta/terminal y bloqueos independientes. No se crean parámetros compartidos corporativos. Se preservan familias ajenas, elementos fijados y cambios manuales externos. Se valida la huella del documento antes de aprobar/aplicar, incluyendo versiones y colocación/Flow actuales: `VersionGuid` por sí solo puede retrasarse dentro de grupos de transacciones. Actualizar el análisis recomputa Z y orientación sobre la geometría nueva conservando XY; un techo cambiado bajo una posición bloqueada impide aplicar hasta revisar el bloqueo.

## Operaciones mediante el MCP instalado

Use las herramientas existentes `run_query` para análisis y edición del borrador, y `run_modify` para COMMIT autorizado. `workspace` debe ser la ruta absoluta de `ARQ-T.extension/lib`, motor `cpython`:

```python
from ARQT_MEP.integration import dispatch
result = dispatch(doc, inputs['operation'], inputs.get('payload', {}),
                  allow_write=inputs.get('allow_write', False))
```

El mismo script está en `tools/ventilation_mcp.py`. Pase los datos por `inputs`, sin interpolarlos como código. `settings` parte del diccionario completo `config.DEFAULT`.

| Grupo | Operaciones |
|---|---|
| Análisis/cálculo | `analyze_ventilation_rooms`, `calculate_ventilation_flows` |
| Propuesta persistente | `create_ventilation_proposal`, `list_ventilation_proposals`, `get_ventilation_proposal`, `refresh_ventilation_proposal` |
| Consulta | `get_ventilation_terminals`, `get_terminal_details`, `get_ventilation_validation`, `get_rebalance_plan` |
| Geometría/tipo | `move_ventilation_terminal`, `add_ventilation_terminal`, `delete_ventilation_terminal`, `change_terminal_type`, `select_ventilation_alternative` |
| Caudal | `set_terminal_flow`, `set_room_design_flow`, `rebalance_ventilation` |
| Zonas/criterios | `define_ventilation_zone`, `delete_ventilation_zone`, `set_main_bedroom`, `set_ventilation_configuration` |
| Edición | `set_proposal_terminal_lock`, `reoptimize_ventilation`, `undo_ventilation`, `redo_ventilation`, `accept_ventilation_terminals`, `confirm_ventilation_warnings` |
| Revisión/aplicación | `approve_ventilation_proposal`, `commit_ventilation_proposal`, `export_ventilation_preview` |

Se conservan también las operaciones heredadas `propose_ventilation_terminals`, `place_ventilation_terminals`, `update_ventilation_terminals`, `validate_ventilation_terminals` y `set_terminal_lock` para clientes anteriores. El editor nuevo utiliza la propuesta persistente.

Creación: payload `{"room_uids":["UID de cada Room de la vivienda completa"],"housing_id":"Vivienda A","scope":{"kind":"all"}}`. Puede pasar configuración completa. Los ámbitos alternativos son `{"kind":"level","level_id":123}` o `{"kind":"rooms","room_uids":["..."]}`. En ámbito parcial, el balance usa el diseño fuera del ámbito y lo advierte; no certifica la existencia de terminales reales allí.

Edición de ejemplo, usando la versión devuelta por la operación anterior:

```json
{
  "operation": "move_ventilation_terminal",
  "payload": {
    "proposal_id": "vent_...",
    "expected_version": 3,
    "terminal_id": "identificador devuelto por get_ventilation_terminals",
    "position_mm": [57671.6, 56314.4]
  }
}
```

Campos principales: Flow/diseño `flow_lps`; nueva boca `room_uid`, `role` (`impulsion`/`extraccion`), `position_mm`; cambio de tipo `terminal_id`, `role`; zona `room_uid`, `use` (`COCINA`/`SALON_COMEDOR`), `polygon_mm` o `reference_mm`, opcional `zone_id` para actualizarla; bloqueos `lock_position`, `lock_flow`, `lock_full`; reoptimizador opcional `room_uid`, `include_manual_positions`; redistribución opcional `redistribute`, `include_manual`. Una advertencia funcional pendiente requiere `confirm_warnings:true` después de revisarla. Las alternativas usan `alternative_index` desde cero y se validan de nuevo antes de aceptarlas.

`get_rebalance_plan` devuelve `base_version`, compatibilidad y cambios antes/después sin modificar la propuesta; para aplicarlos use `rebalance_ventilation`, esa versión y `confirmed:true`. Para aprobar, `approve_ventilation_proposal` exige `confirmed:true` y geometría actual. COMMIT requiere la nueva versión aprobada, `approved:true`, `allow_write:true` y `run_modify`; las eliminaciones requieren además `delete_obsolete:true`. Un cliente no puede saltarse la revisión usando una versión antigua. Ejecute ensayos con `dry_run:true` y un almacén de prueba aislado: la reversión de Revit no revierte archivos JSON.

## Capturas y revisión visual comprobadas

`export_ventilation_preview` produce un PNG limpio con Rooms, puertas, contornos, techos, zonas, colores, etiquetas y caudales; devuelve ruta accesible, versión, planta y leyenda con identificadores completos y XYZ reales. Admite `level_id`, ruta absoluta `path`, ancho/alto. Por defecto guarda en `data/ventilation/previews`. Se genera con WPF, sin exportar el documento Revit. El cliente debe abrir el archivo con su herramienta de imagen; recibir una ruta por sí solo no prueba capacidad visual.

El servidor ya dispone de `capture_view`: se ha reutilizado `mode:viewport` para comprobar la planta Revit, sin crear otro mecanismo de capturas. En esta entrega se abrieron y examinaron realmente tanto esa imagen como los PNG del editor. Se comparó una propuesta antes/después, se consultó el mueble de cocina real y se trasladó la extracción mediante operaciones estructuradas. La imagen complementa la geometría; no se asignan coordenadas basándose únicamente en píxeles.

## Archivos y validación

- Motor conservado y ampliado: `airflow.py`, `config.py`, `service.py`, `revit_read.py`, `revit_write.py`, `integration.py` y fachada `gui.py`.
- Modelo nuevo compartido: `proposal.py`, `proposal_store.py`, `proposal_adapter.py`.
- Editor/transformaciones: `editor_gui.py`, `plan_canvas.py`, `plan_transform.py`, `external_event.py`, `runtime.py`, `preview_image.py`.
- Mismo botón: `script.py`, `window.xaml`, `bundle.yaml`. `.gitignore` excluye los borradores del modelo; README enlaza este flujo.
- Pruebas: `test_ventilation_editor.py`, harness WPF `preview_ventilation_editor.py`/`check_ventilation_editor_ui.ps1`, integración nativa `tools/revit_ventilation_editor_integration.py` y prueba de ventana persistente `tests/revit_ventilation_editor_modeless.py`. Se actualizan las pruebas anteriores para la política vigente.

Consulte los resultados, cobertura de los 30 casos solicitados y evidencias en [VALIDACION_VENTILACION.md](../tests/VALIDACION_VENTILACION.md).

## Límites

Admite techos inferiores planos horizontales/inclinados y Rooms, puertas y techos locales de una fase. No resuelve superficies curvas, habitaciones vinculadas ni opciones de diseño. El cambio de nivel de un terminal nativo existente se rechaza de forma explícita; requiere una estrategia de realojamiento específica. La detección de cocina es orientativa y revisable. No acredita por sí sola cumplimiento integral del CTE: faltan fabricante, acústica, pérdidas, difusión, sección de transferencias y dimensionado de conductos/equipos. Flow sigue sin asociación al conector HVAC. La muestra real actual es una vivienda con siete bocas y pendientes opuestas; varias plantas y otras viviendas se cubren con pruebas independientes, pendientes de muestras nativas adicionales.
