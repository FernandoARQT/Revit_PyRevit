# ARQ-T MEP V2 — implementación y evidencia

Especificaciones de referencia: `MEP_V2_ESPECIFICACION.md` y `MEP_V2_AMPLIACION.md`.
Estado: motor integrado con regresiones y comprobaciones nativas. La red física completa todavía no está certificada.

## Resultado acreditado antes de la campaña final

| Ensayo | Tiempo real | Recorridos centrales válidos | Verificación nativa |
|---|---:|---:|---|
| Referencia anterior | aprox. 120 s | 4/9 | Sí, referencia conservada |
| RRT, semilla 0, arquitectura intermedia | presupuesto 120 s | 5/9 | Sí, consulta `d18021b139f9` |
| Motor integrado, arquitectura intermedia | 581,047 s | 7/9 | Sí, consulta `8903013b98fe` |
| Primera asignación de estaciones, antes de la última revisión | 233,531 s | 7/9 | Sí, consulta `9de67bcd583c` |

Los siete del último ensayo son los terminales 3366666, 3366670, 3366672, 3366662 y 3366664 y las dos principales hacia 3229184 y 3230469. Siguen sin solución conjunta certificada 3366660 y 3366668. No se declara que sean imposibles.

La validación nativa anterior comprueba sólidos barridos del recorrido central, interferencias y compatibilidad entre trayectorias. Los extremos y accesorios reales requieren otra validación después de crearlos provisionalmente. `VALID_WITH_WARNINGS` no acredita una red físicamente conectada completa.

## Arquitectura implementada

| Nivel | Módulos principales | Comportamiento |
|---|---|---|
| Espacio y envolventes | `spatial_model`, `plenum_bridges`, `component_envelopes`, `construction_policy` | Unión continua de regiones, planos inclinados, dimensiones exteriores, holguras y cubierta infranqueable |
| Conectividad | `topology_graph`, `portal_graph`, `connectivity_analysis` | Subdivisión por huellas de muros, pasos locales y varios corredores por perfil |
| Planificación global | `global_planner`, `corridor_assignment`, `corridor_capacity` | Evaluación de tomas, asignación conjunta de fuentes y estaciones de paso antes de reservar rutas |
| Búsqueda | A* direccional, `iris_gcs`, `incremental_roadmap`, `rrt_connect` | Motores complementarios sobre el mismo espacio y política de pasos |
| Coordinación | `cooperative`, `portfolio`, `integer_master`, `source_repair` | Candidatos compartidos, exclusiones de tomas y solapes, reparación de fuentes y conservación de soluciones |
| Validación y Revit | `routing_diagnostics`, `native_feedback`, `revit/proposals`, `revit/network_model` | Diagnóstico por fase, reintentos acotados, comprobación nativa y rollback por ruta |

Los nodos topológicos son coberturas geométricas, no una descomposición exacta demostrada de todo el espacio libre. La ausencia de un camino en este grafo no demuestra desconexión física. La asignación de corredores es una preferencia constructiva de un beam acotado, no una certificación de capacidad tridimensional.

## Cambios de geometría y política constructiva

- Separados tramo, curva, transición y conexión. Un accesorio mayor deja de inflar toda la ruta. Las dimensiones exteriores desconocidas permanecen sin certificar.
- Eliminada la inflación por media diagonal de celda: los nodos representan posiciones del eje y las aristas se verifican con toda la envolvente.
- Comprobación continua de regiones contiguas y planos ligeramente diferentes, sin inventar altura libre ni aceptar huecos entre volúmenes.
- Las regiones prohibidas se contrastan con volúmenes barridos, no sólo con el solape de cajas de sus ejes.
- Categoría de cubierta y evidencia constructiva nominal prevalecen sobre overrides. La losa alveolar de cubierta permanece prohibida.
- Muros estructurales generan propuestas condicionadas. Sólo una aprobación identificada con responsable y referencia puede autorizar su paso. No se crean perforaciones.
- Generación de pasos por barrera y perfil en dos etapas; cobertura registrada, refinamiento y plazos. Eliminados los límites globales de 500 pasos, siete estaciones por muro y veinte posiciones de losa.
- Costes iniciales por clase de material acreditada: tabique ligero 1 m equivalente, fábrica 2 m, hormigón 4 m, losa alveolar 8 m. Son pesos de búsqueda, no autorizaciones ni precios. Cuando la clase no está acreditada se conserva el coste genérico y se deja explícita la falta de clasificación.

## Planificación conjunta e incremental

Se examinan todas las tomas compatibles presentes en el catálogo; no se recortan a tres antes de comprobar su arranque. Las no evaluadas por tiempo quedan como `PENDING_BUDGET`.

La asignación conjunta reparte tomas y estaciones de cruce con exclusividad de fuentes y penalización de solapes de sus envolventes. En el ensayo específico de la captura real ha asignado las nueve conexiones, sin solapes entre estaciones elegidas, en 4,984 s. La validación 3D posterior sigue siendo obligatoria. El empaquetador rectangular de `corridor_capacity` proporciona una estimación adicional; todavía no constituye un asignador general de carriles tridimensionales.

El roadmap incremental conserva nodos, aristas estáticas verificadas y rechazadas con su causa. Sus claves incluyen geometría, perfil, escenario y restricciones. Reservas y giros se revalidan en cada búsqueda. Los estados se serializan en JSON y se comparten entre trabajadores; un resultado estático bloqueado prevalece si dos informes discrepan. La memoria está acotada. Las continuaciones obtienen muestras nuevas después de la tercera generación, sin repetir indefinidamente la misma semilla.

El banco de candidatos y el mejor conjunto compatible se conservan. Una continuación no elimina rutas válidas anteriores. Los caminos rechazados por la validación nativa quedan excluidos tanto de la búsqueda como del banco y del conjunto inicial. La búsqueda inversa conserva también la orientación inversa de los corredores topológicos.

RRT-Connect es una estrategia experimental reproducible de descubrimiento, sometida a la misma validación final. Produjo una mejora intermedia de 4 a 5 conexiones y aporta rutas al conjunto de siete. No se incorpora BIT* ni OMPL. IRIS/GCS registra semillas, obstáculos aproximantes, restricciones, respuestas numéricas, celdas y límites; un fallo de certificación no se interpreta como imposibilidad.

## Presupuesto y paralelismo

Modos WPF/MCP: benchmark 120 s, estándar 240 s, profundo 600 s y exhaustivo 1.000 s. Se conservan los presupuestos personalizados explícitos.

Preparación de pasos, topología, asignación y resolución comparten un plazo absoluto. El feedback nativo reserva tiempo para validar y aprovecha el resto para continuar las rutas sin candidato, además de reintentar geometrías rechazadas.

El cálculo independiente usa procesos CPython externos, sin objetos Revit. Esta máquina tiene 16 procesadores lógicos; el máximo observado es 14 trabajadores. Revit API y transacciones permanecen en su hilo autorizado. La campaña de comparación ejecuta los casos secuencialmente para no multiplicar pools simultáneos.

## Validación nativa y seguridad del modelado

- Las uniones fallidas, autointersecciones no adyacentes, colisiones o comprobaciones incompletas revierten la transacción de la ruta antes de crear su manifiesto de propiedad.
- Se verifican conductos, codos internos y transiciones; las rutas posteriores ven los elementos reales creados antes.
- Corregidos puntos de interpolación del flexible después de que las transiciones desplacen sus extremos. El defecto anterior podía dejar un elemento válido sin sólidos nativos.
- Los ámbitos de contacto con equipos se calculan tras la regeneración final. Son locales a cada conexión; nunca se ignora un equipo completo ni una cubierta.
- El cuerpo nativo se contrasta con una unión autorizada erosionada por la holgura. Para planos paralelos se usan prismas completos; en otros casos, lofts de triangulación restringida. Una reducción de vértices cortos sólo se utiliza tras demostrar que el polígono resultante es un subconjunto del original.
- Las holguras contra obstáculos usan envolventes orientadas conservadoras. Su solape se informa como holgura no certificada; sólo una intersección de sólidos reales se denomina colisión física confirmada. Las envolventes pueden rechazar casos que realmente caben.
- Los pasos locales sólo excluyen su cilindro y su anfitrión permitido. No eliminan muros completos ni autorizan una perforación de cubierta.

`tools/revit_v2_native_fixture.py` ejecuta un ensayo con un `TransactionGroup` propio que siempre revierte, además de `run_modify(dry_run=True)`. Las tapas quedan fuera de ese ensayo para aislar el flexible. El último ensayo (`e3e4495f3378`) confirma geometría de flexible y transiciones, pero no certifica toda su holgura: quedan operaciones nativas fallidas en dos tabiques, una parte del volumen disponible no demostrada y dos posibles conflictos de holgura conservadores. Se revierte la ruta completa. No se ha certificado ni conservado una red nueva.

## Pruebas y reproducción

Regresión final de este bloque: `python -m unittest discover -s tests -p 'test_routing*.py'`: **175 pruebas correctas, 66,702 s**. Compilación correcta.

Consulta nativa `cca75a58bff8`: seis comprobaciones correctas de cuerpo completo, holgura, hueco conservado, planos inclinados y envolvente orientada. No crea elementos. WPF nativo comprobado previamente: ComboBox y modos disponibles (`d077e42ee503`).

Herramientas:

```powershell
python tools/benchmark_routing_v2_campaign.py --ablations
python tools/benchmark_routing_v2.py --seconds 240 --snapshot tests/artifacts/routing-v2-fresh-snapshot.json --output tests/artifacts/ensayo.json
python tools/benchmark_routing_v2.py --seconds 400 --resume tests/artifacts/routing-v2-600-full.json --output tests/artifacts/continuacion.json
```

El último ejemplo usa la captura antigua predeterminada para corresponder al checkpoint antiguo. Las continuaciones son tiempo adicional, no ensayos en frío del mismo presupuesto total.

Cada ensayo final registra captura, hash del motor, semilla, versión de Python, procesadores, métodos, estados por ruta y tiempos. La campaña final y las ablaciones se guardan en `tests/artifacts/routing-v2-final-campaign.json`. Los primeros ensayos intermedios de 120/240 s que heredaron accidentalmente una iteración no son comparaciones equivalentes y quedan excluidos de conclusiones de rendimiento.

## Límites que siguen abiertos

1. Certificar todas las holguras y accesorios de la red física con las geometrías nativas complejas del proyecto.
2. Obtener una combinación válida para los dos terminales restantes o demostrar un conflicto constructivo concreto sin confundir falta de exploración con imposibilidad.
3. El docking actual diagnostica geometría local y conserva el requisito de verificar accesorios reales; no promete todas las variantes de fabricante.
4. La estimación y asignación de pasos no sustituye una distribución general de carriles 3D ni una comprobación estructural.
5. La campaña final y las contribuciones por método deben evaluarse por sus resultados; no se presume una mejora uniforme al aumentar el tiempo.

El documento de proyecto se mantiene sin cambios conservados durante el desarrollo. La familia del recuperador ya estaba abierta con modificaciones previas; no se ha guardado ni alterado.
