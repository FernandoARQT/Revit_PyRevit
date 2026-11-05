# 27. AMPLIACIÓN DEL MOTOR DE BÚSQUEDA: EXPLORACIÓN INCREMENTAL, LAZY ROADMAP Y DIVERSIDAD DE MÉTODOS

Esta ampliación complementa los requisitos anteriores. No sustituye la arquitectura jerárquica ni modifica las prioridades de seguridad, planificación topológica y coordinación global.

## 27.1. Búsqueda incremental y reanudable

El motor actual puede conservar soluciones y reutilizar determinados datos geométricos, pero necesito que también pueda aprovechar el trabajo de exploración realizado en búsquedas anteriores.

Implementa un mecanismo de búsqueda incremental que permita:

- Conservar nodos y aristas explorados.
- Reutilizar comprobaciones estáticas de colisiones.
- Mantener los candidatos descartados junto con sus causas.
- Ampliar progresivamente un mapa de caminos existente.
- Continuar la exploración cuando aumente el presupuesto de cálculo.
- Evitar repetir búsquedas equivalentes que ya han agotado su espacio de exploración.
- Reutilizar resultados entre distintas rutas del mismo edificio.

Distingue entre la información independiente de las rutas y aquella que depende de reservas, tomas ocupadas o condiciones particulares.

La información estática podrá compartirse entre rutas y trabajadores.

La información dinámica deberá revalidarse cuando cambie el escenario.

**No es obligatorio serializar todo el estado interno de A\* si ello supone una complejidad o un consumo de memoria desproporcionados.** Prioriza conservar el grafo explorado, los resultados de comprobación y las fronteras útiles. Implementa continuación exacta de la búsqueda únicamente donde aporte una mejora verificable.

La finalidad es que pasar de 120 a 600 segundos permita explorar progresivamente nuevas posibilidades, no repetir las mismas búsquedas con un límite temporal mayor.

## 27.2. Grafo de caminos incremental con validación diferida

Amplía el grafo topológico global con un mapa geométrico reutilizable inspirado en LazyPRM* y LazySP.

La implementación deberá:

1. Construir un conjunto inicial de nodos y conexiones potenciales dentro de los corredores topológicos.
2. Incorporar puntos relevantes de accesos, portales, cambios de cota y zonas de giro.
3. Permitir ampliar el mapa mediante muestreo adaptativo.
4. Priorizar caminos completos potencialmente viables antes de realizar todas las comprobaciones costosas.
5. Validar geométricamente las aristas que realmente intervienen en los candidatos seleccionados.
6. Marcar las aristas rechazadas y conservar el motivo del rechazo.
7. Recalcular caminos alternativos utilizando la información acumulada.
8. Compartir las aristas estáticas verificadas entre rutas compatibles.
9. Mantener la validación continua de la envolvente física como requisito final.

La comprobación diferida no debe utilizarse para aceptar conexiones sin verificar.

El mapa geométrico y el grafo constructivo deben estar relacionados, pero no confundirse:

- El grafo constructivo determina qué regiones y pasos pueden conectarse.
- El mapa geométrico busca trayectorias concretas dentro de esas posibilidades.

No introduzcas obligatoriamente OMPL como dependencia. Puedes implementar estas estrategias sobre los módulos existentes si resulta más eficiente y mantenible.

## 27.3. Estrategia complementaria RRT-Connect

Si después de las correcciones geométricas, la construcción del grafo y la integración de A*/IRIS/GCS persisten rutas sin resolver, incorpora experimentalmente RRT-Connect como estrategia de descubrimiento de candidatos.

Su ejecución debe ser independiente de las búsquedas basadas en grilla.

Debe:

- Operar sobre el volumen libre autorizado.
- Utilizar el grafo topológico para orientar el muestreo.
- Concentrar muestras en regiones de paso difíciles.
- Respetar los conectores y sus alternativas de docking.
- Generar trayectorias candidatas, nunca rutas automáticamente certificadas.
- Someter las trayectorias a la validación completa de curvatura, envolvente y obstáculos.
- Utilizar semillas aleatorias reproducibles.
- Compartir los candidatos encontrados con el planificador global.

Ejecuta una comparativa controlada.

**Mantén RRT-Connect únicamente si demuestra una mejora real** en conexiones resueltas, tiempo de descubrimiento o diversidad de corredores.

No incorporar BIT* en esta fase salvo que los resultados experimentales justifiquen expresamente la necesidad de un optimizador adicional por muestreo.

## 27.4. Diagnóstico específico de IRIS

Investiga el estado `IRIS_NO_CERTIFIED_CELL`.

Registra, como mínimo:

- Semilla utilizada para generar la región.
- Posición y perfil geométrico.
- Obstáculos que impiden expandirla.
- Restricciones activas.
- Estado de las optimizaciones numéricas.
- Número de regiones generadas y rechazadas.
- Solapes y conexiones entre regiones.
- Tiempo dedicado a generación y certificación.

Distingue entre:

- Ausencia real de volumen suficiente.
- Semilla mal situada.
- Aproximación convexa demasiado conservadora.
- Fallo de optimización numérica.
- Límite de tiempo.
- Límite de expansión.
- Falta de conexión entre regiones existentes.

No interpretar `IRIS_NO_CERTIFIED_CELL` como prueba de imposibilidad geométrica.

## 27.5. Pruebas de contribución algorítmica

Añade una campaña de ensayos con configuraciones comparables:

1. Grafo topológico + A*.
2. Grafo topológico + A* + IRIS/GCS.
3. Configuración anterior + mapa incremental con validación diferida.
4. Configuración anterior + coordinación global.
5. Configuración completa con RRT-Connect, únicamente si ha sido necesario implementarlo.

Para los métodos con componentes aleatorios, utiliza varias semillas fijas y documentadas.

Compara:

- Rutas resueltas.
- Candidatos topológicamente distintos.
- Aristas geométricas verificadas.
- Comprobaciones de colisión reutilizadas.
- Tiempo de preparación.
- Tiempo efectivo de búsqueda.
- Memoria utilizada.
- Porcentaje de trabajo repetido.
- Validez geométrica final.

No mantengas algoritmos adicionales cuyo coste no produzca beneficios verificables.

**Principio de implementación:** primero corregir el espacio geométrico y la conectividad; después reutilizar y ampliar inteligentemente la exploración; finalmente incorporar diversidad algorítmica cuando exista evidencia de que es necesaria.