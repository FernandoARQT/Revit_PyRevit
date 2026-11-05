# MEP V2.1 — cambios de código, validación pendiente

La primera fase dejó las pruebas pendientes por instrucción del usuario. Tras su autorización se ejecutó la suite `test_routing*.py`: 176 pruebas contabilizadas y 20 errores, con un módulo sin importar por un `IndentationError`. Esa cifra no representa la suite completa. Los resultados históricos de V2 no acreditan este código modificado.

## Correcciones posteriores a la primera ejecución

- Reparado el bloque de cancelación en `parallel.py`: solo marca las tareas del lote detenido; la verificación de respuestas permanece en el colector.
- `plan_route` y la recuperación de candidatos tardíos conservan `network_type` cuando existe, sin exigirlo ni inventarlo en consultas exclusivamente geométricas.
- Los trabajos de transporte sin una ruta individual reciben igualmente un identificador de trabajo; se mantiene el rechazo de respuestas con identidad incorrecta.
- Actualizado el trabajador simulado de las pruebas al protocolo con `job_id`. Añadidas comprobaciones de respuestas tardías, contextos incompatibles y metadatos geométricos.

Estas correcciones solo se han revisado mediante lectura del código. No se han vuelto a ejecutar pruebas, compilaciones, benchmarks ni comprobaciones en Revit durante esta fase de corrección. Queda pendiente repetir la suite y, después de resolver sus resultados, continuar la validación del proyecto.

La segunda ejecución autorizada contabilizó 184 pruebas: 181 correctas, un fallo de reparto de presupuesto y dos errores por rutas pendientes ausentes del resultado. La corrección posterior unifica el reloj del planificador y del gestor de presupuesto, reparte el tiempo dentro del plazo de cada fase y conserva los diagnósticos de las rutas pendientes junto al conjunto certificado. Las rutas protegidas obsoletas se identifican antes de seleccionar el conjunto y dejan de bloquear la aceptación de las rutas recalculadas. Se han añadido casos para el presupuesto por fase y para un recálculo correcto que convive con una ruta protegida obsoleta. Esta última corrección aún no se ha ejecutado ni validado.

## Referencia y diagnóstico

Se conserva `V2_1-baseline/routing-before.zip` y su SHA256. Git no está instalado en las ubicaciones habituales ni disponible en PATH; no se ha creado un commit ni se presenta el archivo como sustituto equivalente de Git.

La campaña V2 final corresponde a procesos independientes: 6/5/5/6/7 rutas para presupuestos 120/180/240/600/1000 s. Esa serie no debe interpretarse como una continuación que pierde rutas. La deficiencia concreta de implementación era la reconstrucción secuencial, la retención por orden de llegada, los recortes de candidatos y el descarte de respuestas entre pasadas.

## Cambios y justificación

| Componente | Cambio |
|---|---|
| `core/global_incumbent.py` | Conservación atómica; revalidación individual y de pares, exclusividad de extremos, redes, restricciones y caminos rechazados; eventos de conservación, sustitución e invalidación; exportación/restauración por contexto |
| `core/candidate_repository.py` | Identidad topológica, constructiva, de conexión y geométrica; deduplicación determinista; límite preferente 24, tres por familia, cobertura de alternativas y protección del conjunto global. No hay descarte por longitud como supuesta dominancia espacial |
| `core/search_budget.py` | Plazo absoluto, reserva de consolidación, contadores lógicos, asignación por rendimiento observado y semillas SHA256 estables |
| `core/route_conflict_graph.py` | Exclusiones demostradas por conector y envolvente; vecindarios ampliables. Las estimaciones de capacidad no crean exclusiones |
| `core/multi_route.py` | Restauración completa antes de reconstruir; selector conjunto si la restauración falla; historial por ruta; repositorio y conjunto global entre pasadas; prioridad a pendientes; metadatos de resultados normalizados; persistencia de estado |
| `core/portfolio.py` | Eliminados cortes por primeros candidatos; reducción por diversidad después de recoger; conservación de alternativas; diagnóstico de conflictos y reasignaciones; orden de intentos reproducible |
| `core/global_planner.py`, `service.py` | Firma efectiva de geometría, perfiles, conectores, restricciones y aprobaciones; restauración de preparación completa; invalidación conservadora cuando no puede acreditarse una reutilización parcial segura |
| `core/source_repair.py` | Actualiza origen, ID, docking, extremos y transiciones; vuelve a validar y anula aprobación y validación nativa previas |
| `core/incremental_roadmap.py` | Conserva cachés estáticas separadas de reservas; registra saturación y permite ampliación acotada en profundo/exhaustivo |
| `core/iris_gcs.py` | Caché serializable de celdas ligada también a restricciones y reservas exactas; evita reutilizar celdas bajo una ocupación diferente |
| `parallel.py`, `parallel_worker.py` | IDs de trabajos verificados, contexto explícito, respuestas tardías conservadas para revalidación posterior y transporte de mapas/celdas |
| `core/corridor_assignment.py` | Retención de familias diferentes en el beam; las preferencias continúan siendo reemplazables |

No se añaden familias de algoritmos ni dependencias. Se mantienen los modos 120/240/600/1000 s y las comprobaciones físicas del modelador.

## Instrumentos preparados, sin ejecutar

- `tests/test_routing_v21.py`: conservación 7→6→8, combinación inválida, cambio de contexto, exclusividad, diversidad, semillas y selector de nueve rutas con entradas invertidas.
- `tools/diagnose_routing_v21.py`: comprueba compatibilidad estricta del histórico 3366668, revalidación individual y grafo de conflictos; búsqueda individual del 3366660 sin reservas de rutas movibles. Requiere rutas de entrada explícitas y genera un informe; no se ha ejecutado.
- `tools/benchmark_routing_v2_campaign.py --staged`: prepara continuación con incrementos 120+120+360+400 s, presupuestos acumulados 120/240/600/1000 s. Las ablaciones independientes se ejecutan separadamente.

## Estado de resultados y límites

El mejor resultado **histórico V2** sigue siendo siete recorridos centrales nativos, con 3366660 y 3366668 pendientes. No hay resultado V2.1 medido ni mejora de rendimiento acreditada. Tampoco existe una nueva certificación de red física conectada.

La invalidación parcial de geometría se aplica de forma conservadora: si no existe evidencia suficiente para aislar dependencias se descarta el estado afectado completo. La preparación topológica incompleta conserva diagnósticos, pero puede requerir reconstrucción; no se declara desconexión física. La política de presupuesto aún requiere medir su rendimiento y ajustar sus prioridades con los ensayos pendientes.

No se ha modificado la interfaz, las familias, los elementos ni los parámetros del proyecto Revit durante esta fase.
