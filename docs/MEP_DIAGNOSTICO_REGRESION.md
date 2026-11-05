# Diagnóstico y corrección de coordinación global MEP

Fecha: 4 de noviembre de 2026. Evidencias en `tests/artifacts/routing-regression-diagnostic.json`, `routing-regression-recovered-network.json`, `routing-regression-checkpoint-report.json` y propuestas completas `routing-regression-checkpoint-1.json` y `routing-regression-checkpoint-2.json`. Reproducción: `tools/diagnose_routing_regression.py` y `tools/replay_routing_checkpoint.py`.

## A. Por qué se obtuvieron seis rutas

La geometría y el documento coinciden entre V2 y V2.1. Tras reconstruir explícitamente las alternativas desde el catálogo inmutable, coinciden también las firmas de contexto físico: perfiles, diámetros, posiciones, redes, holguras, radios, restricciones, pasos y bloqueos. La semilla es 0 en ambas campañas. No se han relajado restricciones.

Las ejecuciones no eran equivalentes: V2 empleó un proceso independiente con 1000 s y `max_candidates=4`; V2.1 empleó continuación 120+120+360+400 s y `max_candidates=24`. La diferencia en tomas serializadas es además un defecto, no un cambio autorizado del catálogo.

No hay una única causa demostrada para toda la diferencia de descubrimiento entre procesos. Sí hay defectos reproducidos: pérdida de alternativas al serializar el origen elegido; poda de geometrías distintas dentro de una misma familia de candidatos; repetición de índices de pasada; descarte de preparación por regenerar pasos completos con un plazo breve; y conflictos ya calculados por los trabajadores que el solver volvía a descubrir mediante cortes diferidos.

## B. La séptima ruta histórica

**El validador actual acepta individualmente las siete rutas históricas y acepta conjuntamente su combinación.** La ruta histórica exacta del terminal 3366670 no está en el banco final de V2.1. Eso demuestra su ausencia final, pero los registros anteriores no permiten afirmar que V2.1 la hubiera descubierto y descartado: las campañas fueron independientes y no existe una traza anterior por candidato.

La recuperación se ha hecho de forma explícita: primero se comprobaron documento, revisión, catálogo, posiciones y reglas; después se restauraron las alternativas omitidas; finalmente se revalidaron todos los candidatos. No se mezclaron bancos sin verificar su compatibilidad.

| Terminal o equipo de destino | V2 | V2.1 original | Candidato histórico y resultado recuperado |
|---|---|---|---|
| 3366660 | Pendiente | Pendiente | Sin ruta histórica resuelta; sigue pendiente |
| 3366666 | Válido | Válido | Histórico presente en el banco; válido |
| 3366670 | Válido | Pendiente | Histórico exacto ausente; recuperado y válido |
| 3366672 | Válido | Válido | Histórico exacto ausente; alternativas válidas |
| 3229184, distribuidor | Válido | Válido | Histórico presente; válido |
| 3366662 | Válido | Válido | Histórico exacto ausente; alternativas válidas |
| 3366664 | Válido | Válido | Histórico presente; válido |
| 3366668 | Pendiente | Pendiente | Sin ruta histórica resuelta; hay candidatos individuales conflictivos |
| 3230469, distribuidor | Válido | Válido | Histórico presente; válido |

## C–D. Defectos y cambios mínimos

1. `service.py` reemplazaba `source` sin conservar el origen anterior entre las alternativas. Ocurría al exportar la toma elegida y al recuperar una ruta protegida. Se conserva ahora el universo íntegro y único de tomas equivalentes. La propuesta antigua había perdido la toma 8 para 3366666 y la toma 10 para 3366672.
2. `CandidateRepository.bank()` podía excluir geometrías distintas por cuota de familia. La cuota establece ahora prioridad, sin eliminar alternativas por una supuesta dominancia espacial no demostrada. La deduplicación emplea coordenadas exactas y registra los duplicados; los contextos diferentes se rechazan con motivo.
3. El selector ya disponía de resultados de conflictos paralelos, pero el maestro entero iniciaba sus restricciones sin ellos. Se incorporan esos conflictos demostrados como cortes iniciales. Se mantiene el mismo solver y el mismo objetivo lexicográfico.
4. La rotación de tomas/direcciones reiniciaba su pasada al continuar. Se persiste `next_pass`. Se conserva también una solución explícita del checkpoint cuando mejora el estado interno restaurado.
5. La generación de pasos se repetía aunque la captura anterior fuera completa. En la prueba breve sustituyó 1339 pasos por enumeraciones parciales de 953 y 1065, invalidando el contexto. Ahora solo se reutiliza una enumeración completa con igualdad de contexto físico y documental; las parciales o incompatibles se recalculan.
6. El informe topológico reutilizado arrastraba tiempos de la etapa original. Se distingue el tiempo de preparación de la etapa actual y se registra qué preparación se ha reutilizado.

Se instrumentan descubrimiento, validación individual, admisión/rechazo del repositorio, consideración/rechazo del selector y prueba/aceptación global mediante identificadores de candidato. Los resultados paralelos incluyen trabajo, método y compatibilidad de contexto. La propuesta completa guarda `candidate_audit` y `continuation`. Los límites del solver se distinguen del plazo global. Estas trazas nuevas no reconstruyen retroactivamente decisiones que las campañas antiguas no registraron.

## E. Comprobaciones

- Suite completa: **191 pruebas correctas**, 56,738 s, sin fallos ni errores.
- Las siete rutas históricas superan validación individual y conjunta sobre la captura usada por V2.1.
- Selección con 69 candidatos revalidados: pasa de seis a siete. Antes de reutilizar cortes, el replay requirió 136 llamadas al solver; después, 3, con 1134 exclusiones iniciales. La última reproducción tardó 8,5 s, incluyendo validación, procesos y decisiones. No se presenta como comparación controlada de velocidad global.
- El solver certifica siete conexiones como óptimo **para ese banco suministrado**, sin afirmar imposibilidad física de ocho o nueve.
- Reparación local de 3366670: grupo de cinco rutas, externas protegidas, resultado conjunto válido de siete. Grupos de cuatro para 3366660 y 3366668: no añaden conexiones con las alternativas existentes.
- Dos continuaciones de 20 s de presupuesto desde el checkpoint recuperado: siete rutas en ambas, contexto coincidente, banco y mapas suministrados, preparación reutilizada, nueve historiales recuperados, contador de pasadas 3→6. Tiempos de pared próximos a 22 s incluyen validación/restauración y cierre de trabajadores; no equivalen a aumentar el presupuesto de búsqueda.
- Pruebas de regresión: conservación de tomas al cambiar origen, conservación de siete geometrías de la misma familia con cuota preferente inferior, rechazo de contexto incompatible, reutilización de pasos completos e invalidación al cambiar holgura, y nueve conexiones preferidas frente a alternativas baratas limitadas a seis tomas. Se comprueban órdenes inversos y opciones condicionadas.

## F–G. Mejor resultado y conexiones restantes

Mejor conjunto recuperado: **7/9 recorridos centrales**, 30,618 m, 20 giros, sin cruces estructurales en esta combinación. Recupera 3366670. No se ha ejecutado otra campaña exhaustiva ni acreditado una mejora en descubrimiento desde cero respecto de V2.

3366660 y 3366668 siguen pendientes en la combinación elegida. El JSON de diagnóstico contiene, por candidato válido, rutas bloqueantes, terminales/equipos, tomas, pasos y segmentos testigo con coordenadas y distancia entre ejes. Los segmentos describen interferencias de envolventes; no se inventa un punto único de intersección cuando el conflicto es de holgura. No existen conductos modelados por esta prueba a los que asignar un ElementId de conducto.

Para 3366660 aparecen interferencias con los recorridos a 3366672, 3366662 y 3366666, además de exclusividad de toma en algunas alternativas. Para 3366670 aparecen conflictos con 3366662, 3366666, 3366672 y la principal a 3229184; las alternativas históricas permiten resolverlos conjuntamente. Para 3366668 hay interferencias con 3366672, 3366662 y 3366664 y exclusividad en ciertas tomas. El motivo genérico anterior `NO_CERTIFIED_ROUTE_IN_SEARCH_LIMITS` no significaba ausencia de todos los candidatos: el banco contiene alternativas individualmente válidas.

El diagnóstico y la reproducción son geométricos, sin cambios en Revit. No certifican transiciones reales, conexiones físicas, pérdidas de presión ni una red modelada completa.

## H. Presupuesto y continuación

La campaña anterior consumió unos 967,89 s de pared dentro de los cuatro procesos de búsqueda. Sus lotes paralelos registran 545,45 s de operaciones de planificación, 6,38 s de validación y 11,48 s de conflictos. Son tiempos de lotes en el proceso coordinador, no sumas de CPU de todos los trabajadores. Se recibieron 259 respuestas de planificación y se hicieron 364 validaciones de candidatos entre pasadas. Los bancos crecieron 12→20→49→62; los contadores globales de intentos crecieron 17→29→42→51.

Los artefactos anteriores no permiten repartir de forma exhaustiva y sin solapamiento cada segundo entre A*, RRT, IRIS/GCS, roadmap, negociación, solver y espera. Tampoco permiten identificar todos los trabajos repetidos por semilla y corredor. El tiempo restante de esos totales no debe etiquetarse arbitrariamente como desperdicio. Los intentos guardados y los lotes sirven de evidencia parcial, y las nuevas métricas de selección y trazas permiten un análisis posterior más preciso.

La continuación anterior recuperaba resultados y parte del estado; no era correcto describirla como reutilización completa. Se reconstruye la asignación de corredores; las cachés de consultas geométricas nativas siguen siendo locales al proceso y no se serializan. Los mapas incrementales, celdas IRIS, candidatos, historiales y contadores sí se transportan cuando coincide el contexto. La reutilización completa de consultas geométricas entre procesos no se ha añadido ni se afirma.

Conclusión técnica: se han demostrado y corregido pérdidas de opciones y trabajo repetido, y se ha acreditado la recuperación y conservación de siete conexiones. El aprovechamiento global del tiempo aún no puede calificarse de óptimo. Falta demostrar una mejora de búsqueda sin candidatos históricos; los datos disponibles no justifican prometer nueve rutas a la primera.
