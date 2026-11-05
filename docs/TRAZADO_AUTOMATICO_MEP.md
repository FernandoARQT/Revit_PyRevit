# Trazado automático MEP · primera versión radial

La actualización del motor IRIS/GCS, el selector binario y su estado de validación se describen en [MEP_IRIS_GCS_IMPLEMENTACION.md](MEP_IRIS_GCS_IMPLEMENTACION.md). Esa nota concreta el alcance de los nuevos algoritmos y distingue los resultados históricos de las comprobaciones pendientes.

Botón independiente en **ARQ-T → MEP → Trazado automático MEP**. Comparte todo el cálculo y la validación con el despachador del MCP existente. No sustituye ni cambia el botón Distribuir ventilación.

El comando empieza con un menú de sistemas. Actualmente solo está disponible **Recuperación de calor**. Calcula propuestas de recorridos y ofrece una acción separada **Modelar y conectar** para crear conductos reales a partir de rutas aprobadas con conectores BIM reales y compatibles. No dimensiona recuperadores ni calcula pérdidas de presión, acústica o CFD. Flow sigue siendo un dato de diseño de la rejilla, independiente del caudal de su conector.

## Modelado y comprobación de conexiones

Los módulos auxiliares `routing/system_menu.py` y `routing/revit/network_model.py` separan la selección de sistema y el modelado del núcleo geométrico. **Mostrar en Revit** sigue dibujando ejes; **Modelar y conectar** escribe conductos nativos. Debe seleccionarse el recuperador de referencia, calcular y aprobar las rutas y utilizar extremos de conectores reales. Las referencias manuales no se convierten en conexiones ficticias.

Para ramales se utiliza el tipo flexible existente configurado y el diámetro de la ruta, limitado a los tamaños configurados y con lectura posterior del valor real. Para una ruta principal cuyo origen se identifique como HRV se conserva el diámetro rígido de `main_connection`: se usa el único tipo circular disponible o `main_connection.type_id` cuando hay varios. Los cambios de dirección de la principal intentan crear codos; las uniones fallidas se registran. No se crean familias ni tipos, ni se desconectan redes existentes. Si un extremo exige una transición no verificada, la ruta queda sin modelar y se informa de la incompatibilidad. No se selecciona ni coloca automáticamente el distribuidor ni se calcula una nueva principal por el mero hecho de pulsar Modelar.

Cada ruta tiene su transacción. Un fallo de creación revierte esa ruta. Si el conducto existe y falla una conexión, se conserva con su diagnóstico. Un manifiesto propio guarda la ruta, los elementos y los conectores previstos y permite volver a comprobarlos incluso si se elimina un segmento. Repetir Modelar sobre rutas ya registradas solo comprueba el trabajo existente; no duplica elementos ni sustituye automáticamente conductos anteriores.

**Comprobar conexiones** consulta el estado real y distingue:

| Estado | Significado |
|---|---|
| TOMA_SIN_USAR | Toma libre que no es extremo de una ruta prevista/modelada; no constituye por sí sola un error |
| RUTA_SIN_MODELAR | Toma prevista en el diseño, pero no existe un conducto registrado para esa ruta |
| CONDUCTO_SIN_CONECTAR | Existía conducto modelado para ese extremo, pero falta su conexión, segmento o toma |
| CONEXION_INCORRECTA | El extremo está conectado a otra toma o la clasificación comprobada pertenece a otra red |
| CONECTADO | Conexión física con el extremo previsto, directa o mediante un accesorio intermedio |
| CONEXION_EXISTENTE_AJENA_A_PROPUESTA | Conexión existente fuera del trabajo registrado, que no se modifica |

El informe detalla ruta, ID de conducto, elemento de destino y número de toma. Comprueba también las uniones interiores de los segmentos rígidos. Una clasificación indefinida o bidireccional se señala para revisión funcional: continuidad física no demuestra por sí sola el uso correcto del sistema.

La aprobación del eje no certifica la geometría finalmente interpolada por Revit para el flexible ni las dimensiones finales de sus accesorios. El informe mantiene esa limitación explícita; tampoco acredita pérdidas de carga, caudales propagados al conector o cumplimiento hidráulico. La envolvente real de lo modelado requiere revisión adicional.

## Uso

1. Colocar las rejillas con Distribuir ventilación, o disponer de terminales existentes con conectores HVAC y Flow de instancia verificable. Abrir el nuevo botón no modifica el modelo.
2. Elegir el recuperador de referencia. La lista superior solo incluye recuperadores. En Distribuidores, marcar **elementos completos** y asignar **SUPPLY** (impulsión) o **EXTRACT** (extracción). No se seleccionan tomas individuales. La entrada principal del distribuidor se identifica como su única toma circular de mayor diámetro; las demás son derivaciones. Una identificación ambigua se devuelve como error.
3. En Destinos, incluir las rejillas y su red. Flow es de solo consulta. Si hay varios conectores en una boca, seleccionar uno. El lector conserva las funciones de los terminales creados por ARQ-T y rechaza mezclarlas.
4. Configurar diámetro exterior, aislamiento, holgura, radio de giro y resolución. El perfil inicial es Ø125 mm, aislamiento 0, holgura 15 mm, envolvente radial 77,5 mm y radio provisional de codo 125 mm. Son valores editables de esta herramienta.
5. Analizar geometría y calcular recorridos. Revisar estados, longitud, codos, pasos, coste y diagnósticos. Sin espacio suficiente o sin conectores verificables, el motor devuelve un problema; no completa el recorrido con puntos inventados.
6. Seleccionar una ruta para consultar sus propiedades, comparar alternativas y aplicar una alternativa. Se devuelven hasta tres recorridos geométricamente diferentes cuando se encuentran. No se rellenan alternativas duplicadas.
7. Puede bloquear rutas, cambiar diámetro/holgura de una selección y forzar waypoints. Recalcular conserva la geometría bloqueada y vuelve a coordinar las demás rutas respetando sus envolventes. Cambiar el diámetro de una ruta bloqueada conserva su eje, pero vuelve a comprobar si cabe.
8. Mostrar en Revit dibuja los ejes en una vista 3D ortogonal existente. Naranja: impulsión; azul: extracción; violeta: condicionado; rojo: inválido/no evaluado; gris: alternativa solicitada por MCP. Seleccionar un eje permite volver a sus propiedades. Esc vuelve a la ventana y retira los ejes. Cerrar/cancelar no deja curvas temporales de esa sesión. Si no hay vista 3D, crear una y volver a mostrar.
9. Aprobar cada ruta revisada. Los pasos condicionados necesitan **responsable** y **referencia de validación técnica**. La herramienta registra esa declaración; no verifica un cálculo estructural ni autoriza una perforación por sí misma.
10. Guardar propuesta escribe un DataStorage propio con Extensible Storage. También puede guardar un borrador identificado como tal. Consultar propuestas permite recuperarlas. Guardar otra vez la misma propuesta actualiza su registro. Los ejes de vista previa no se guardan como conductos. El guardado del archivo RVT sigue siendo una operación habitual del usuario.

## Topología por equipos y conexiones anteriores

`systems/heat_recovery_topology.py` construye solicitudes **MAIN** recuperador → entrada principal de distribuidor y **BRANCH** salida de distribuidor → terminal, manteniendo los algoritmos geométricos existentes. Identifica las dos tomas interiores del recuperador por clasificación/sentido, separa redes, cuenta terminales por función, lee su Flow, asigna una toma libre distinta a cada terminal y comprueba capacidad. El caudal de una principal nueva suma las demandas nuevas asignadas a su distribuidor. La principal conserva Ø125; los ramales conservan el perfil de diseño seleccionado. No dimensiona diámetros por hidráulica ni inventa adaptadores entre diámetros distintos.

Los terminales con una conexión previa se cuentan como conservados y no se duplican. Una entrada principal ya conectada se conserva y se señala para comprobar su destino. Varias principales nuevas sobre una única toma del recuperador requieren un colector/derivación intermedio: se devuelve MAIN_SPLITTER_REQUIRED, sin reutilizar físicamente el mismo conector. En la revisión se distinguen las conexiones anteriores conservadas de las tomas sin usar. La elección SUPPLY/EXTRACT es una asignación lógica del diseñador, no cambia automáticamente la clasificación interna de los conectores de la familia.

**Conexiones previas…** revisa el transporte conectado a las tomas interiores del recuperador, los distribuidores seleccionados y los terminales incluidos. Ofrece conservar o eliminar la lista exacta revisada. La admisión/expulsión exterior no forma parte del ámbito interior. El recorrido se detiene en equipos y terminales: no elimina el recuperador, distribuidores ni rejillas. Se conservan componentes que llegan a tomas fuera de la selección y elementos fijados/agrupados. Los ejes dependientes se incluyen en la revisión. Dependientes inesperados causan reversión, no una eliminación ampliada silenciosamente. Tras eliminar se descarta el cálculo anterior y se exige recalcular. Esta es una eliminación explícita previa al rediseño, no una sustitución atómica de toda la instalación.

Guardar preferencias registra también la selección lógica de equipos en un DataStorage específico del proyecto, sin alterar familias ni sus parámetros. MCP puede leer o escribir las mismas asignaciones; el proyecto se vuelve a leer al abrir la ventana.

| Operación MCP adicional | Contrato |
|---|---|
| get_heat_recovery_equipment_assignments | READ; devuelve hrv_id y equipment_assignments guardados |
| set_heat_recovery_equipment_assignments | WRITE; configuration y explicit:true; asigna roles lógicos, no edita conectores de la familia |
| plan_heat_recovery_topology | READ; configuration y destinations; devuelve demandas, principales/ramales, preservados, capacidades y errores, sin ejecutar A* |
| inspect_heat_recovery_previous | READ; configuration y destinations; devuelve plan, IDs y confirmation_token |
| remove_heat_recovery_previous | WRITE; plan completo, confirmation_token y explicit:true; rechaza revisiones obsoletas |

`configuration.equipment_assignments` es una lista de objetos con `element_id` o `element_uid` y `network_type` SUPPLY/EXTRACT. `hrv_id` identifica el recuperador. `destinations` mantiene terminal_id, port_id y network_type de terminales reales. El cliente debe usar run_modify con dry_run:true antes de una eliminación real. La confirmación corresponde al conjunto revisado; cualquier cambio relevante del modelo invalida su token. Por defecto se conserva lo existente.

## Referencias manuales

La pestaña Referencias manuales admite listas JSON en **coordenadas internas del proyecto convertidas a milímetros**. Son puntos medidos y definidos explícitamente por el usuario. No se confunden con conectores de familias. Cada ramal necesita un origen distinto:

```json
[{"id":"distribuidor-S-1","xyz_mm":[1000,2000,2700],"network_type":"SUPPLY","kind":"DISTRIBUTOR","direction":[1,0,0]}]
```

```json
[{"id":"destino-S-1","xyz_mm":[4000,2000,2700],"network_type":"SUPPLY","airflow_lps":8}]
```

Las coordenadas anteriores solo ilustran el contrato; no pertenecen al proyecto actual. Si se indica una dirección, se comprueba el tramo recto de conexión (150 mm por defecto). Sin dirección, la referencia queda expresamente identificada como manual y no simula una orientación de conector.

## Arquitectura y geometría

La implementación se encuentra en `ARQ-T.extension/lib/ARQT_MEP/routing/`:

| Capa | Archivos | Responsabilidad |
|---|---|---|
| Geometría | `core/geometry.py`, `core/spatial_model.py`, `core/plenum_bridges.py`, `revit/extractor.py` | Sólidos triangulados, BVH, distancias, unión de volúmenes libres, lectura de Revit y vínculos |
| Motor universal | `core/astar_3d.py`, `core/corridors.py`, `core/multi_route.py`, `core/cooperative.py`, `core/portfolio.py`, `core/route_geometry.py`, `core/passages.py` | A*, corredores, selección conjunta de candidatos, coordinación, portales, radios y alternativas |
| Sistema | `systems/ventilation_radial.py`, `settings.py` | Demandas de Flow, asignación de puertos, redes y perfil de instalación |
| Integraciones | `service.py`, `mcp.py`, `gui.py`, `revit/proposals.py` | Contrato serializable, WPF, MCP, validación nativa, ejes y persistencia |

La búsqueda utiliza sólidos reales triangulados, con bounding boxes y BVH como filtro previo. Las cajas no sustituyen los sólidos en las comprobaciones resolubles. Cuando falta un sólido, la caja queda bloqueada conservadoramente; la validación nativa no declara comprobado ese volumen.

El volumen de cálculo se obtiene entre **cara inferior real del falso techo** y **cara inferior real de Floor/Roof** sobre él, conforme al criterio solicitado para esta herramienta. Incluye el espesor del falso techo; mantiene contornos, huecos y planos inclinados y comprueba los demás sólidos. No utiliza su descuelgue nominal como altura disponible.

Se comprueba la unión de los volúmenes de plenum: una envolvente puede quedar repartida entre dos regiones contiguas. En los límites se utiliza una comprobación conservadora por cilindros y bandas de altura, recortadas por los planos inferiores y superiores. Sobre huellas de muros compuestos se derivan regiones `WALL_GAP_AIR` únicamente entre falsos techos próximos (separación máxima 600 mm) con el mismo plano de cubierta; el límite inferior conserva la cota más restrictiva. Esto representa el aire que puede quedar sobre un muro cuya geometría termina más abajo. **No elimina ni ignora el muro, la cubierta u otros obstáculos**, ni habilita huecos sin muros conocidos entre zonas separadas.

El ámbito automático deriva de Rooms interiores; porches y terrazas se excluyen. Se leen obstáculos en un margen adicional de 500 mm, que no amplía el espacio permitido. Los vínculos, si se incluyen, utilizan GetTotalTransform tanto para mallas como para sólidos nativos. No se presupone una equivalencia entre fases de documentos diferentes.

Los patinillos, trasdosados y corredores especiales se habilitan mediante **volúmenes permitidos explícitos**, siempre sujetos a comprobación de los sólidos y dimensiones reales. No se deducen automáticamente como transitables por su nombre o por existir un hueco.

## Búsqueda y coordinación

- Grilla dispersa de seis movimientos ortogonales, generada bajo demanda. No se crea un array 3D de toda la vivienda.
- Resoluciones iniciales 100 / 25 / 10 mm. El refinamiento se intenta dentro del presupuesto disponible; agotar una resolución o presupuesto devuelve NOT_EVALUATED, no una prueba de imposibilidad geométrica.
- Estado A*: posición, dirección de llegada, longitud recta acumulada y longitud tangente del giro anterior para impedir que se solapen giros. Heurística euclídea multiplicada por el coste positivo de longitud; los otros costes son no negativos.
- Los nodos muestrean el eje del conducto. Se comprueba su radio efectivo y cada arista completa como una cápsula continua; la validación final incluye los arcos y su margen de discretización. Una celda con extremos libres no permite saltar un obstáculo entre ellos.
- Costes en metros y equivalentes de giro de 90°. Pesos iniciales: longitud 1, giro 0,4, proximidad 0,15, fuera de zona preferente 0,1, intervención no estructural 3, intervención condicionada 20. La banda adicional de proximidad es 50 mm. El desglose final utiliza longitud de los tramos y arcos y estima exposición por sus extremos; la búsqueda evalúa las transiciones de la grilla. Son índices de comparación, no euros ni pérdidas de presión.
- Coordinación del conjunto con reservas de envolventes. El orden inicial prioriza la menor altura libre en los extremos y las secciones mayores; la primera ruta calculada no tiene prioridad permanente. Si bloquea otra, se prueba la ruta pendiente sin los vecinos movibles, se identifican las interferencias y se reconstruyen las rutas desplazadas. El intercambio se acepta entero o se descarta entero: primero se reducen las conexiones sin resolver, después las rutas condicionadas y finalmente el coste total. Se conservan las rutas bloqueadas y, al recalcular una selección, las no seleccionadas.
- `coordination` permite activar esta negociación (`enabled`, por defecto `true`), reservar tiempo para ella (`initial_budget_fraction=0.35` para el primer trazado), limitar los intentos (`max_repair_trials=8`) y las rutas desplazadas por intento (`max_evictions=3`). También se comparan hasta tres órdenes. Las estadísticas y el panel de incidencias indican los intercambios aceptados. Es una búsqueda heurística acotada, sin garantía de óptimo global ni de completar todas las rutas.
- Se generan hasta cuatro candidatos por conexión (`max_candidates=4`) probando ambos sentidos de búsqueda y hasta tres tomas equivalentes del mismo distribuidor (`max_port_candidates=3`). No se cambian de distribuidor ni de red, no se usan tomas conectadas y se mantiene el tamaño de conexión, de modo que el caudal de la principal no cambia. Un selector conjunto con poda compara compatibilidad de envolventes y exclusividad de tomas (`max_assignment_nodes=50000`); conserva la solución anterior si no encuentra una mejor. Los conectores elegidos se serializan tanto en la ruta como en su solicitud para que el modelado use la misma toma.
- El presupuesto se reparte entre las pasadas automáticas y sus fases; la primera pasada no consume todo el tiempo. Se conserva un banco de candidatos entre pasadas, para combinar los recorridos que funcionaron en distintos órdenes sin tener que redescubrirlos. El límite de pasos del mapa se reparte entre muros, evitando que las numerosas variantes del primero excluyan los siguientes.
- El mapa de corredores propone conexiones visibles entre los extremos, pasos admitidos y niveles sobre los falsos techos, con aproximaciones rectas a los pasos. Comprueba aristas y arcos completos antes de aceptar un camino; después mantiene el refinamiento por grilla como alternativa. Por defecto `roadmap_enabled=true`, `roadmap_neighbors=20` y `max_roadmap_portals=80`. No confunde un fallo del mapa acotado con una imposibilidad física demostrada.
- Los arranques utilizan el radio efectivo sin añadir media diagonal de celda y prueban las intersecciones con los planos del plenum, además del muestreo, para no omitir intervalos estrechos de altura. Los pasos incluyen cotas de los arranques reales. Las transiciones y extremos siguen pendientes de comprobar sobre la geometría real al modelar.
- El primer giro puede comenzar tras la longitud tangente que exige su ángulo y radio (`R·tan(ángulo/2)`, `R` para 90°); entre dos giros se exige la suma de ambas longitudes tangentes. Adelantar un giro es una posibilidad geométrica, no una regla universal: se comprueba la envolvente completa y se compara el resultado del conjunto.
- Simplificación de duplicados, colineales y atajos ortogonales comprobados; fillets de radio finito y validación posterior. No se aceptan atajos que atraviesen sólidos.
- Se descartan segmentos y arcos de menos de 1 mm antes de enviarlos a Revit (en el proyecto de ensayo, `ShortCurveTolerance` es 0,7804 mm). Los barridos nativos adyacentes y tangentes comparten un disco de extremo: esa unión se certifica analíticamente para evitar booleanas degeneradas. Los sólidos de obstáculos, otras rutas y tramos no adyacentes mantienen las comprobaciones nativas de intersección.
- Caché de hasta dos conjuntos geométricos por contenido serializado. Cada cálculo tiene sus propias reservas y restricciones. Se vuelve a comprobar la revisión del modelo desde el MCP.
- Límite inicial: 100.000 estados por búsqueda, 20 s por intento, 120 s de coordinación, tres alternativas. Cancelación cooperativa; las llamadas nativas individuales a la API no se interrumpen a mitad.

## Obstáculos y pasos

FREE, PASSABLE, CONDITIONAL, FORBIDDEN y UNKNOWN son reglas explícitas. Muros: se consulta carácter estructural y MaterialClass; cuando no hay información suficiente quedan UNKNOWN y bloqueados. Vigas y pilares están prohibidos por defecto. Los elementos clasificados PASSABLE tampoco se atraviesan libremente: hacen falta conexiones especiales del grafo.

Se generan candidatos normales a **muros rectos PASSABLE**. Para muros CONDITIONAL solo se generan candidatos si su identificador aparece en `constraints.evaluable_elements`. Los forjados requieren un paso explícito con `evaluable_zone`; no se generan perforaciones automáticamente. La clasificación de un paso no puede rebajar la de su elemento. Un portal no puede ignorar un elemento UNKNOWN o FORBIDDEN.

Cada intervención registra elemento, XYZ, normal, espesor, dimensión necesaria, clasificación y estado. Se cobra una vez por portal. Los muros permanecen bloqueados fuera de esos pasos.

Escenario A excluye nuevos pasos estructurales. Escenario B puede proponerlos en ámbitos evaluables, conservando CONDITIONAL. B se ofrece como alternativa y no sustituye A automáticamente por tener menor coste. Los pasos existentes autorizados pueden introducirse expresamente con su referencia técnica.

Ejemplos de restricciones avanzadas:

```json
{
  "allowed_regions":[{"id":"patinillo-medido","kind":"SERVICE_SHAFT","box_mm":[100,200,0,500,600,6000]}],
  "forbidden_regions":[{"id":"reserva","box_mm":[800,800,2500,1200,1200,3000]}],
  "preferred_regions":[{"id":"pasillo","box_mm":[0,0,2400,5000,1000,3000]}],
  "evaluable_elements":["UniqueId del muro o linkUniqueId|elementUniqueId"],
  "passages":[],
  "rejected_passages":[],
  "approved_passages":[],
  "passage_approvals":{},
  "waypoints":{},
  "locked_routes":[]
}
```

Un paso manual necesita `id`, `element_ids`, dos `points_mm` medidos a ambos lados del elemento, `radius_mm` de la envolvente autorizada, `classification` y, para CONDITIONAL, `evaluable_zone`. Puede añadir `xyz_mm`, `direction`, `thickness_mm`, `required_dimension_mm`, `intervention` y `approval_status`. Nunca crea un hueco. Autorizarlo exige registrar responsable y referencia en `passage_approvals`, además de su ID en `approved_passages`.

## Validación y persistencia

La certificación final por defecto utiliza **sólidos Revit transitorios**: extrusiones para rectas, barridos para arcos y BooleanOperationsUtils.Intersect contra los sólidos originales, incluidos los vínculos transformados. Se comprueban holguras, aislamiento y envolventes de giro, interferencias entre propuestas y autointersecciones. Los pasos solo eximen al elemento correspondiente dentro del tramo autorizado. Una operación booleana fallida deja NOT_EVALUATED.

Los estados son VALID, VALID_WITH_WARNINGS, CONDITIONAL, INVALID y NOT_EVALUATED. Los radios genéricos de codo mantienen la advertencia de geometría provisional de fabricante. Una ruta condicionada no equivale a validada técnicamente. Si se desactiva la comprobación nativa en MCP, ninguna ruta queda certificada para aprobación.

Se registra identidad de documento, revisión de elementos relevantes (VersionGuid, Rooms, vínculos y puntos de conexión), configuración, orígenes, destinos, geometría, caudales, alternativas, condiciones y aprobación. Antes de aprobar, dibujar o guardar se consulta la revisión actual. Las propuestas recuperadas incompatibles se muestran OUTDATED. La firma SHA-256 detecta alteraciones accidentales del JSON; no sustituye una firma digital.

Revit 2025 TemporaryGraphicsManager/pyRevit tmpgfx ofrece controles de imagen en pantalla, no una colección genérica de ejes 3D seleccionables. Se utiliza por ello el respaldo de **ModelCurves propias**, con SketchPlane por segmento o arco y metadatos de propiedad. El cliente MCP debe llamar a `hide_mep_routes` en un `finally`. La interfaz lo hace al volver o cerrar. Un cierre abrupto del proceso puede dejar ejes; Ocultar ejes solo retira los elementos propios identificados. Si existen dependientes ajenos, la limpieza se revierte y avisa.

## MCP existente

No se instala otro servidor ni se inventan herramientas remotas. Las operaciones se invocan a través de `mcp__pyrevit.run_query` o `run_modify` importando el despachador:

```python
from ARQT_MEP.routing.mcp import dispatch
result = dispatch(doc, inputs['operation'], inputs.get('payload', {}),
                  allow_write=inputs.get('allow_write', False))
```

Usar `workspace` con la ruta absoluta de `ARQ-T.extension/lib`. También existe el punto de entrada `tools/routing_mcp.py`.

| Operación | Efecto |
|---|---|
| analyze_mep_geometry | READ; resumen por defecto, `include_geometry:true` para snapshot completo |
| get_mep_system_profiles | READ; solo ventilation_radial disponible |
| get_mep_connection_points | READ; conectores reales, Flow y advertencias |
| create_mep_route_proposal | CALCULATE + VALIDATE; origins, destinations, configuration |
| optimize_mep_routes | CALCULATE + VALIDATE; admite propuesta anterior y bloqueos |
| get_mep_route_alternatives | CALCULATE + VALIDATE; proposal, route_id |
| select_mep_route_alternative | CALCULATE + VALIDATE; proposal, route_id, alternative_index base 0 |
| set_mep_route_constraints | CALCULATE + VALIDATE; proposal, constraints |
| validate_mep_routes | VALIDATE; proposal |
| approve_mep_route | Estado en JSON; proposal, route_id, explicit:true y technical_approval cuando proceda |
| get_mep_route_proposal | READ; proposal_id opcional para listar |
| preview_mep_routes | PREVIEW: escritura reversible de ejes; proposal, explicit:true |
| hide_mep_routes | WRITE de limpieza; proposal_id opcional, explicit:true |
| save_mep_route_proposal | WRITE de DataStorage; proposal, explicit:true |

Las tres últimas requieren `allow_write=True` y `run_modify`; probar primero con `dry_run=True`. Aprobar solo cambia el JSON devuelto hasta guardar. No se aceptan catálogos proporcionados externamente como sustituto de los conectores del proyecto; se vuelven a leer. Un snapshot recibido se compara con la revisión actual y se utiliza la extracción fresca.

## Configuración y ampliación

Valores corporativos: `routing/config/ventilation_radial.csv`, UTF-8 y delimitador punto y coma, leídos y validados al importar. Las columnas son `key` y `value_json`: una clave de configuración y su valor JSON, que conserva listas, objetos, booleanos y unidades explícitas. El JSON anterior se conserva como base de compatibilidad; el CSV contiene todos sus valores originales y las nuevas especificaciones. `settings.read_configuration` es el límite del proveedor: una futura lectura de Excel debe devolver el mismo diccionario; el núcleo no depende del formato del archivo.

Preferencias personales: sección independiente `ARQT_AutomaticMEPRouting_v1` de pyRevit. Las preferencias excluyen restricciones espaciales, IDs, recuperador, fases y sobrescrituras de rutas para evitar transferir esos datos a otra vivienda. También excluyen las nuevas identificaciones, flexibles, distribuidores y conexión principal para que una preferencia antigua no oculte cambios del CSV. Cada propuesta guarda su configuración de proyecto; recuperarla no modifica los valores corporativos. Las sobrescrituras por ruta admiten section, clearance_mm, bend_radius_mm y weights.

Las reglas `model_identification.construction` comparan parcialmente el nombre de tipo, sin distinguir mayúsculas, acentos o separadores: Paquete techo identifica falso techo; MU-Hormigon, estructura; Trasdosado y Tabique, elementos atravesables. Las reglas explícitas del usuario y el carácter estructural mantienen prioridad. PASSABLE sigue requiriendo un paso geométricamente validado; STRUCTURE conserva CONDITIONAL y no autoriza perforaciones. No se han cambiado A*, coordinación, generación de pasos ni validación con sólidos.

`get_mep_connection_points` consume la configuración para localizar el tipo flexible existente, enumerar los tipos cargados de distribuidores por familia o tipo y devolver sus parámetros dimensionales. Devuelve `configured_model`, tamaños solicitados que no aparecen en el listado circular y advertencias. Los conectores solo se certifican sobre instancias; el catálogo de tipos no inventa sus puertos. En instancias de distribuidores se leen diámetros, posición y orientación y se advierte de la diferencia respecto de la conexión principal configurada. Los caudales se leen de parámetros de instancia tipados AirFlow: se mantiene la prioridad del Flow de diseño existente y, en su ausencia, se exige un único candidato para evitar elegir arbitrariamente entre parámetros distintos.

Las propuestas conservan ese catálogo, sus advertencias y las capacidades del perfil. La interfaz muestra los diagnósticos al abrir y actualizar. Las claves `selection`, `parallel_branches` y `placement` registran los requisitos solicitados; **no implementan** dimensionado hidráulico, ramales paralelos ni colocación automática. `get_mep_system_profiles.capabilities` declara expresamente esas funciones ausentes. La sección geométrica original sigue siendo editable; no debe confundirse con un diámetro calculado por pérdidas de carga. La integración completa de la red permanece pendiente.

El núcleo recibe milímetros, geometría serializada, regiones, perfil geométrico, restricciones, portales y solicitudes de conexión. Para otro sistema se añade una estrategia que construya solicitudes y compruebe reglas de sistema, manteniendo A*, colisiones y coordinación. Actualmente solo se implementa RADIAL. La sección rectangular usa una envolvente circular circunscrita conservadora; no se afirma optimización de su orientación ni accesorios rectangulares.

Requisitos de ejecución: Revit 2025, pyRevit con CPython 3.12/Python.NET, Shapely y NumPy compatibles. No se necesitan bibliotecas nuevas para el motor A*. Pillow solo reconstruye iconos de desarrollo; no se usa al pulsar el botón.

## Límites comprobados

- El proyecto inspeccionado dispone de recuperador y tipos cargados de distribuidores de 5 y 10 tomas, pero no de distribuidores ni rejillas definitivas colocados. Se han probado conectores de distribuidores en instancias temporales revertidas; no se ha probado una red completa conectada.
- Solo se infieren automáticamente plenums con caras de límite planas, incluidas las inclinadas. Techos curvos y otros espacios necesitan ámbitos medidos explícitos. Los obstáculos mantienen sus sólidos triangulados/nativos.
- Los corredores deben estar incluidos en el ámbito permitido; la herramienta no interpreta automáticamente todos los patinillos o trasdosados por su nombre.
- Pasos automáticos limitados a muros rectos. Los demás requieren definición explícita. No se comprueba estructura, resistencia al fuego ni compatibilidad constructiva de una perforación.
- Movimientos ortogonales y radios finitos pueden no encontrar recorridos oblicuos posibles en huecos inclinados estrechos. NOT_EVALUATED conserva esa incertidumbre.
- Las diferencias de sección entre conector y ramal se rechazan como transición no modelada. No se supone que un reductor cabe.
- Sin correspondencia de fases vinculadas se incluyen sus fases conservadoramente. Vínculos excluidos o descargados reducen el alcance de la validación.
- La coordinación es heurística y acotada; no garantiza encontrar toda solución ni el óptimo global. Las limitaciones de búsqueda no se presentan como imposibilidad demostrada.
- La vista previa modifica temporalmente el documento mediante curvas; no es una superposición de GPU sin elementos. Se limpia al finalizar normalmente.

Véase [validación ejecutada y resultados](../tests/VALIDACION_TRAZADO_MEP.md).
