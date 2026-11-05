# ARQ-T MEP ROUTING ENGINE V2
## Especificación de implementación — Refactorización profunda del planificador automático de instalaciones

**Entorno:** Revit 2025 · pyRevit · CPython 3.12 · MCP · Shapely · SciPy/HiGHS

**Tipo de encargo:** Implementación integral, refactorización algorítmica y validación experimental.

**Prioridad:** Alta.

**Autoridad técnica:** Las decisiones principales de arquitectura, geometría, planificación y validación quedan establecidas en este documento. No se requiere solicitar decisiones menores de implementación.

---

# 1. MISIÓN Y RESULTADO ESPERADO

Debes transformar el motor actual de trazado MEP de ARQ-T en un sistema de planificación geométrica y constructiva global.

No se trata de añadir otro algoritmo al final de los existentes.

La modificación consiste en sustituir el proceso actual, basado principalmente en encontrar recorridos individuales y posteriormente coordinarlos, por una estrategia jerárquica que razone primero sobre el conjunto de la instalación.

La nueva arquitectura deberá:

1. Construir una representación fiable de todo el espacio realmente disponible.
2. Identificar la conectividad entre regiones, incluidos los pasos que requieren penetraciones en muros.
3. Analizar qué recorridos topológicos son posibles para todas las conexiones.
4. Evaluar todas las tomas compatibles de los distribuidores.
5. Generar alternativas de conexión físicamente construibles.
6. Planificar conjuntamente la ocupación de corredores y pasos.
7. Resolver las geometrías mediante algoritmos especializados.
8. Optimizar las trayectorias sin perder conexiones viables.
9. Verificar de forma independiente las restricciones físicas.
10. Modelar en Revit únicamente soluciones verificadas y autorizadas.

El algoritmo debe comportarse como un diseñador MEP experimentado: primero identifica por dónde conviene desarrollar la instalación, después distribuye las redes y finalmente ajusta cada conducto.

## Referencia de partida

El escenario disponible contiene nueve recorridos.

La última referencia validada registra:

- Cuatro recorridos centrales válidos.
- Cinco recorridos sin solución certificada.
- Presupuesto aproximado de 120 segundos.
- A* multirresolución, IRIS/GCS aproximado, corredores y coordinación conjunta ya implementados.
- Verificación geométrica nativa de los cuatro recorridos obtenidos.
- Conexiones físicas finales todavía no verificadas como red completa.

No afirmes que los otros cinco recorridos son imposibles. El sistema actual únicamente acredita que no ha conseguido encontrarlos y certificarlos con su estrategia y presupuesto.

El objetivo de esta refactorización es resolver el máximo número posible de conexiones, idealmente las nueve, sin incumplir ninguna restricción física.

---

# 2. DECISIÓN ARQUITECTÓNICA PRINCIPAL

Implementa una arquitectura jerárquica compuesta por seis niveles.

## Nivel 1. Geometría y espacio libre

Representación verificable del volumen disponible y de todos los obstáculos.

## Nivel 2. Topología constructiva

Grafo de regiones, corredores y pasos que conectan espacios.

## Nivel 3. Planificación global

Asignación de tomas, selección de corredores y estimación de capacidad para el conjunto.

## Nivel 4. Resolución geométrica

Generación de conductos individuales mediante A*, corredores, IRIS/GCS y optimización continua.

## Nivel 5. Coordinación y refinamiento

Resolución de conflictos, reasignación de posiciones y mejora conjunta.

## Nivel 6. Validación y modelado

Certificación geométrica, aprobación constructiva, creación nativa y auditoría.

**Regla de arquitectura:** los niveles inferiores deben reutilizar información de los superiores. No deben volver a descubrir independientemente la estructura del edificio en cada búsqueda.

Los algoritmos especializados compartirán un modelo geométrico, un grafo topológico y un sistema de candidatos común.

El sistema debe poder encontrar alternativas aunque alguno de sus motores específicos no esté disponible o agote su presupuesto.

---

# 3. PRESERVACIÓN DEL SISTEMA EXISTENTE

Trabaja sobre la implementación actual en:

`ARQT_MEP/routing/`

Inspecciona las dependencias reales antes de modificar módulos.

Conserva:

- Lectura de geometría y conectores mediante Revit API.
- Separación del cálculo geométrico respecto a las transacciones Revit.
- Interfaz WPF existente.
- Despachador MCP.
- Formato de propuestas, cuando sea compatible.
- Mecanismos de revisión y huella geométrica.
- ExtensibleStorage.
- Identificación de elementos y propiedad de las modificaciones.
- Validación nativa de sólidos.
- Auditoría de conexiones.
- Conservación de redes existentes.
- Cancelación cooperativa.
- Ejecución multiproceso.
- Capacidades operativas del trazado radial.

No modificar el botón independiente de distribución de terminales salvo para solucionar una dependencia demostrada.

No reorganizar todo el repositorio sin necesidad.

Mantén compatibilidad con propuestas anteriores mediante migración explícita de esquema cuando proceda.

Cada cambio debe incorporar pruebas de regresión.

---

# 4. CONTRATO GEOMÉTRICO OBLIGATORIO

## 4.1. Volumen autorizado

El trazado solamente puede desarrollarse dentro del volumen de instalaciones identificado entre:

**Límite inferior:** cara inferior real del falso techo.

**Límite superior:** cara inferior real del elemento constructivo superior correspondiente, incluyendo forjado o cubierta.

Los paquetes de falso techo suelen representar cámaras disponibles de 200–300 mm. No presupongas que la altura nominal equivale al espacio útil real: extráelo de la geometría.

La totalidad de la envolvente física de cada conducto deberá permanecer dentro de esos límites.

Quedan prohibidos:

- Conductos descolgados por debajo del falso techo.
- Trayectorias por encima del límite superior.
- Recorridos fuera de los volúmenes permitidos.
- Colisiones no autorizadas.
- Penetraciones en cubierta.

La verificación debe aplicarse a la geometría completa, no únicamente al eje del conducto.

## 4.2. Cubierta

Implementa un clasificador de cubierta con prioridad absoluta.

Incluye los elementos de cubierta modelados como `Roof`, `Floor` u otras categorías relevantes cuando su función constructiva esté acreditada.

No basta con detectar únicamente el nombre `Teja sobre forjado`.

Todos los sólidos identificados como cubierta, incluyendo sus elementos estructurales, serán `HARD_FORBIDDEN`.

Esta clasificación no se podrá modificar mediante:

- Overrides de usuario.
- Escenarios alternativos.
- Costes negativos o penalizaciones.
- Portales.
- Aprobaciones genéricas.
- Refinamiento geométrico.

Si existe incertidumbre sobre un elemento que puede constituir la cubierta, no lo reclasifiques automáticamente como atravesable.

Registra el elemento y exige identificación constructiva suficiente antes de validar la ruta.

## 4.3. Muros

Permite la generación de pasos por:

- Tabiques.
- Trasdosados.
- Muros de fábrica.
- Muros de hormigón, cuando resulte técnicamente admisible.

Un muro de hormigón no debe desaparecer del modelo de obstáculos. La solución se representa mediante un paso local identificado, con dimensiones y ubicación concretas.

Las penetraciones estructurales tendrán el estado:

`PROPOSED_STRUCTURAL_PENETRATION`

Podrán utilizarse para obtener recorridos de diseño condicionados.

No se permite considerarlas autorizadas ni abrir huecos automáticamente.

Cuando no se disponga de información suficiente sobre armaduras, esfuerzos, bordes o zonas singulares, conservar el requisito de revisión estructural.

Vigas, pilares y elementos estructurales especialmente sensibles no deben perforarse automáticamente mediante la regla general para muros.

---

# 5. CORRECCIÓN PRIORITARIA DE LA ENVOLVENTE GEOMÉTRICA

## 5.1. Modelo por componentes

Elimina el supuesto de que toda una ruta necesita la envolvente correspondiente al accesorio más grande.

La envolvente debe calcularse por componente:

```text
DuctSegment
Elbow
Transition
ConnectorAdapter
EndConnection
```

Para cada componente conserva:

```text
component_id
component_type
start_xyz_mm
end_xyz_mm
geometry
outer_dimensions_mm
insulation_mm
installation_clearance_mm
native_family_type_id
geometry_verification_status
```

La dimensión exterior del tubo no debe confundirse con su diámetro nominal.

Si un dato procede del fabricante, identifica su origen. Si no existe, no lo inventes.

## 5.2. Radio efectivo

Para un tramo circular sin aislamiento adicional:

`r_eff = D_exterior / 2 + holgura`

Con aislamiento:

`r_eff = D_exterior / 2 + espesor_aislamiento + holgura`

No sumar aislamiento nuevamente si el diámetro exterior ya lo incluye.

Mantén inicialmente:

`installation_clearance_mm = 15`

Este valor debe ser configurable por tipo de componente.

La holgura no es una tolerancia numérica.

## 5.3. Corrección de sizing.py

Localiza y elimina el comportamiento que aumenta la holgura de todo el recorrido mediante la diferencia entre el diámetro del tubo y el mayor diámetro de sus conectores.

Las transiciones solo reservan espacio en sus emplazamientos.

Un flexible Ø75 conectado mediante una transición Ø125 no se comportará geométricamente como un conducto Ø125 durante toda su longitud.

Corrige también el incremento global del radio de giro a partir del mayor diámetro de conexión.

El radio de giro se determinará según el tramo o accesorio concreto.

## 5.4. Curvas y accesorios

Para los tubos flexibles:

- Utiliza el radio de curvatura mínimo real cuando esté documentado.
- Conserva provisionalmente el valor configurado de 125 mm cuando falte información, identificándolo como supuesto de diseño.
- No declares certificada la geometría de fabricante mientras ese dato sea provisional.

Representa los giros mediante curvas continuas.

No valides una polilínea de esquinas agudas como sustituto de un flexible curvado.

Cuando se requiera un accesorio especial, verifica que existe un tipo compatible y que Revit puede instanciarlo.

---

# 6. CORRECCIÓN DE LA PRECISIÓN GEOMÉTRICA

Refactoriza las comprobaciones de `spatial_model.py` para separar tres conceptos.

## A. Dimensiones físicas

Diámetros, aislamiento, radios reales y holguras de montaje.

## B. Tolerancias geométricas

Errores numéricos, operaciones booleanas y aproximación de superficies.

## C. Resolución de búsqueda

Distancia entre nodos o puntos candidatos.

No permitas que una resolución de malla de 25 mm añada automáticamente 12,5 mm a la holgura física del conducto.

Establece como valor inicial de referencia una tolerancia de comparación geométrica de 0,5 mm, diferenciada de las tolerancias específicas de las operaciones nativas de Revit.

No emplees esa tolerancia para permitir atravesar obstáculos.

## 6.1. Segmentos y espacio libre

Sustituye, cuando sea posible, los muestreos conservadores por comprobaciones continuas de barridos geométricos.

Las cajas envolventes se utilizarán como filtro amplio, no como certificación final de colisiones.

Corrige la comprobación de volúmenes prohibidos si utiliza únicamente el solape de cajas del segmento.

Implementa intersección efectiva entre el volumen barrido y la región prohibida.

## 6.2. Regiones contiguas

Permite que la envolvente de un conducto ocupe simultáneamente varias regiones cuya unión forma un espacio libre continuo.

Evita exigir que toda la envolvente pertenezca a una única región.

Para superficies inclinadas, aplica coherentemente distancias tridimensionales y distancias respecto al plano.

## 6.3. Modelo de falso techo

Revisa `plenum_bridges.py`.

No exijas igualdad exacta de coeficientes de planos para reconocer continuidad geométrica.

Utiliza comprobación de solape, distancia y volumen disponible con tolerancias definidas.

Una unión entre regiones no debe crear espacio ficticio dentro de un elemento sólido.

El espacio situado sobre la coronación de un muro puede ser libre si los sólidos lo demuestran.

---

# 7. NUEVO GENERADOR DE PASOS

Refactoriza `core/passages.py` e incorpora un generador de candidatos basado en conectividad.

## 7.1. Cobertura completa de barreras

Elimina el retorno global que detiene la generación al alcanzar 500 candidatos sin haber evaluado todos los muros.

El nuevo proceso tendrá dos etapas.

**Primera etapa — cobertura topológica**

Para cada barrera que separa regiones relevantes, intenta identificar al menos un paso geométricamente admisible por perfil de conducto.

**Segunda etapa — refinamiento adaptativo**

Genera posiciones y alturas adicionales cuando:

- El paso inicial no admite curvas.
- Existe congestión.
- La asignación global requiere otra posición.
- Un candidato produce conflicto con otra ruta.
- La optimización identifica una alternativa prometedora.

El número de pasos por muro no será fijo.

No limitar arbitrariamente a siete estaciones.

## 7.2. Portales constructivos

Representa cada paso mediante:

```text
portal_id
source_region
target_region
host_element_id
host_element_uid
construction_class
entry_xyz_mm
exit_xyz_mm
penetration_axis
available_cross_section
maximum_admissible_profile
required_intervention
approval_required
approval_status
estimated_cost
```

La validez del paso depende de la sección que debe atravesarlo.

Un paso Ø75 no debe permitir automáticamente un conducto Ø125.

Los portales deberán comprobar la envolvente completa desde la aproximación de entrada hasta la salida.

## 7.3. Hormigón

Genera penetraciones propuestas sin exigir que el usuario haya añadido previamente cada muro a `evaluable_elements`.

Esa lista podrá seguir utilizándose para restringir ámbitos expresamente prohibidos o determinados contextos técnicos, pero no para eliminar automáticamente de la búsqueda todas las alternativas de hormigón.

Distingue claramente:

- Candidato geométricamente viable.
- Candidato condicionado a revisión.
- Candidato aprobado técnicamente.
- Penetración existente y verificada.
- Candidato prohibido.

La cubierta nunca genera portales.

## 7.4. Coste constructivo inicial

Establece costes de referencia expresados en metros equivalentes de recorrido:

| Intervención | Penalización inicial |
|---|---:|
| Paso libre existente | 0 |
| Tabique ligero | 1 m equivalente |
| Muro de fábrica | 2 m equivalentes |
| Muro de hormigón | 4 m equivalentes |
| Cubierta | Prohibido |

Son pesos iniciales de optimización, no costes económicos ni justificaciones estructurales.

Una penetración en hormigón puede ser preferible a un rodeo excesivo.

Estos pesos se documentarán y se someterán a pruebas de sensibilidad.

---

# 8. NUEVO GRAFO TOPOLOGICO GLOBAL

Implementa:

```text
core/topology_graph.py
core/portal_graph.py
core/connectivity_analysis.py
```

Integra estos módulos con el modelo espacial existente.

## 8.1. Nodos

Representarán componentes conectados de espacio libre y zonas estratégicas del volumen autorizado.

No es necesario que cada región corresponda exactamente a una habitación de Revit.

La subdivisión debe responder a la geometría útil del conducto.

## 8.2. Aristas

Representarán:

- Continuidad de espacio libre.
- Conexiones entre regiones.
- Portales abiertos.
- Pasos por tabiques.
- Penetraciones propuestas.
- Transiciones geométricas entre cotas.

Cada arista tendrá restricciones de tamaño, orientación, altura y capacidad.

## 8.3. Grafo dependiente del perfil

La conectividad no será universal para todos los conductos.

Construye o consulta grafos filtrados según:

- Diámetro exterior.
- Aislamiento.
- Holgura.
- Radio mínimo de curvatura.
- Restricciones de conexión.

Reutiliza geometría compartida, sin duplicar innecesariamente todos los datos.

## 8.4. Análisis de alcanzabilidad

Antes de ejecutar búsquedas costosas:

1. Identifica la región de origen.
2. Identifica la región de destino.
3. Comprueba conectividad topológica.
4. Encuentra corredores alternativos.
5. Identifica pasos indispensables.
6. Detecta posibles cuellos de botella.

Genera un resultado diagnóstico por terminal.

Si la búsqueda topológica no encuentra una conexión, no afirmes automáticamente que el trazado es imposible: determina si la representación del espacio o los portales están incompletos.

## 8.5. Caminos topológicos alternativos

Utiliza una búsqueda de K caminos de bajo coste y diversidad topológica.

Valor inicial:

`K = 8`

Los caminos deben diferenciarse prioritariamente por regiones y portales utilizados, no por variaciones pequeñas de coordenadas.

Si las ocho alternativas fracasan, permite ampliación adaptativa.

Conserva los corredores prometedores para la fase geométrica.

---

# 9. INTEGRACIÓN REAL DE IRIS/GCS

El análisis convexo debe formar parte del sistema de planificación, no ser únicamente un motor alternativo que se prueba después del fracaso de otro.

## 9.1. Papel de IRIS

Utiliza regiones convexas para obtener subconjuntos del espacio libre útiles para:

- Identificar corredores amplios.
- Generar posiciones seguras de paso.
- Determinar zonas de continuidad.
- Facilitar la optimización continua.
- Localizar regiones donde existe mayor libertad geométrica.

Mantén la representación original de los obstáculos como autoridad final.

Una aproximación convexa conservadora puede no cubrir todo el espacio realmente disponible. No conviertas la ausencia de una celda convexa en demostración de inexistencia de ruta.

## 9.2. Construcción de regiones

Prioriza la generación de regiones convexas en:

- Corredores topológicos candidatos.
- Conexiones entre espacios.
- Zonas de giro.
- Pasos estrechos.
- Entornos de distribuidores.
- Aproximaciones a terminales.

No consumas tiempo generando celdas en todo el edificio si ya existe un conjunto limitado de corredores relevantes.

## 9.3. Relaciones entre regiones

Construye relaciones de solape y continuidad entre regiones convexas.

Las aristas deben tener suficiente información para comprobar que existe un paso geométrico real entre ambas.

No basta con una proximidad entre centros o cajas.

## 9.4. Optimización continua

Sobre cada secuencia de regiones y portales:

- Optimiza posiciones de paso.
- Minimiza longitud y curvatura.
- Considera obstáculos y reservas.
- Mantiene las condiciones de tangencia.
- Respeta radios mínimos.
- Valida el barrido completo.

## 9.5. Alcance GCS

La implementación actual utiliza enumeración limitada de caminos y optimización continua.

No la denomines optimización global garantizada.

Mantén inicialmente la implementación existente, pero reorganízala para aprovechar los corredores topológicos y compartir regiones entre rutas.

No incorpores una dependencia grande adicional, como otro framework completo de optimización, sin demostrar primero que la arquitectura híbrida existente resulta insuficiente.

**Decisión:** IRIS/GCS será un refinador y generador de corredores geométricos dentro de la planificación global, no la única fuente de conectividad.

---

# 10. REFACTORIZACIÓN DE A*

Mantén A* como algoritmo de búsqueda geométrica, pero deja de utilizarlo como responsable de descubrir por sí solo toda la estrategia de instalaciones.

## 10.1. Dominio de búsqueda

Utiliza los corredores seleccionados por el grafo topológico.

Amplía el dominio cuando un corredor no permite construir una geometría válida.

## 10.2. Resolución

Mantén como punto de partida:

```text
Resolución gruesa: 100 mm
Resolución fina: 25 mm
Refinamiento local: 10 mm
```

No construyas obligatoriamente una grilla fina en toda la vivienda.

Concentra el refinamiento en:

- Portales.
- Obstáculos próximos.
- Cambios de dirección.
- Encuentros de falsos techos.
- Conexiones y transiciones.

## 10.3. Direcciones

Amplía las alternativas respecto a la grilla ortogonal actual.

Implementa conectividad *any-angle* o búsqueda híbrida que permita segmentos oblicuos cuando sean geométricamente válidos.

Las aristas se comprobarán con la envolvente física completa.

No se permite atravesar sólidos entre nodos.

## 10.4. Estado de curvatura

Conserva la información de dirección y requisitos de giro.

Corrige los estados que puedan descartar caminos válidos por imponer antes de tiempo tramos rectos o radios provisionales.

El cumplimiento del radio de curvatura debe verificarse sobre la trayectoria final.

---

# 11. CONEXIONES ORIENTADAS Y DOCKING

Implementa:

`core/connector_docking.py`

La generación del arranque no dependerá exclusivamente de prolongar linealmente la dirección del conector.

Para cada conector genera alternativas geométricamente admisibles:

- Conexión directa.
- Tramo recto y giro.
- Codo compatible.
- Transición local.
- Aproximación en diferentes direcciones.
- Ajuste de cota dentro del volumen disponible.

No inventar piezas de fabricante.

El sistema debe consultar las familias y tipos cargados o los datos geométricos conocidos.

Cada alternativa tendrá:

```text
dock_id
connector_id
connector_origin
connector_direction
components
entry_point
entry_tangent
required_dimensions
clearance_envelope
native_compatibility
validation_status
```

Una toma cuyo arranque recto esté bloqueado no se considerará inutilizable hasta comprobar alternativas compatibles.

## 11.1. Restricción vertical

Todos los componentes de la conexión deberán permanecer dentro del volumen autorizado.

Si un conector de terminal se encuentra físicamente por debajo del límite inferior y exige que un conducto descienda por debajo del falso techo, registra una incompatibilidad de alojamiento o de familia.

No ocultar ese problema ampliando artificialmente el espacio permitido.

## 11.2. Comprobación nativa

La geometría de docking podrá planificarse en el motor independiente, pero su validez definitiva deberá contrastarse con Revit.

---

# 12. ASIGNACIÓN COMPLETA DE TOMAS

Modifica:

- `systems/heat_recovery_topology.py`
- `core/portfolio.py`
- `core/integer_master.py`
- `core/multi_route.py`

## 12.1. Topología de redes

Conserva estrictamente:

**SUPPLY:** recuperador → distribuidor de impulsión → terminales de impulsión.

**EXTRACT:** terminales de extracción → distribuidor de extracción → recuperador.

El sentido del flujo debe coincidir con los conectores reales.

Las redes no se mezclan.

Habitualmente existirá un distribuidor por red.

Si existen varios distribuidores del mismo sistema, se requerirá una red principal con las derivaciones necesarias. No conectar dos distribuidores a la misma toma física sin una solución de colector válida.

## 12.2. Evaluar todas las tomas

No limitar la admisión inicial a tres tomas.

Para cada terminal examina todas las tomas libres físicamente compatibles de su red.

La admisión inicial será económica:

- Comprobar tamaño.
- Comprobar función.
- Comprobar disponibilidad.
- Generar docking.
- Comprobar accesibilidad topológica.
- Estimar coste y dificultad.

Solo después selecciona las mejores alternativas para cálculo detallado.

Si las alternativas seleccionadas fracasan, amplía a las restantes.

Todas las tomas admisibles deberán quedar clasificadas como evaluadas, descartadas por causa concreta o pendientes por presupuesto.

## 12.3. No fijar asignaciones anticipadamente

La proximidad geométrica será únicamente una heurística.

La asignación final debe resolverse junto con el corredor y la geometría.

Utiliza el optimizador combinatorio existente para seleccionar:

```text
Terminal
Distribuidor
Toma
Docking
Corredor
Trayectoria
```

Respeta la exclusividad de conectores y todas las reservas.

---

# 13. PLANIFICACIÓN MULTIRRUTA GLOBAL

Implementa:

`core/global_planner.py`

Este será el orquestador principal.

## 13.1. Planificación inicial

Construye candidatos topológicos para todos los terminales antes de reservar rutas definitivas.

Calcula:

- Cantidad de opciones por terminal.
- Pasos obligatorios.
- Corredores compartidos.
- Capacidad libre.
- Diámetros.
- Complejidad de conexiones.

Prioriza los terminales con menor flexibilidad topológica.

## 13.2. Capacidad de corredores

Implementa:

`core/corridor_capacity.py`

No basta con conocer si un corredor es transitable.

Es necesario estimar si puede alojar conjuntamente varios conductos.

Utiliza las secciones reales y la separación geométrica exigida.

En cámaras de 200–300 mm, presta especial atención a la colocación lateral y a las diferencias de cota.

No presupongas que los tubos se pueden apilar libremente.

Las estimaciones de capacidad son filtros iniciales; la geometría final debe comprobarse en tres dimensiones.

## 13.3. Ocupación conjunta

Permite asignar franjas y posiciones aproximadas a cada conducto antes del refinamiento.

No fijes definitivamente el eje de un conducto hasta comprobar las necesidades de los demás.

## 13.4. Resolución de conflictos

Conserva las estrategias existentes de *rip-up and reroute*, pero aplícalas a grupos de conflicto identificados.

Cuando una ruta bloquee otra:

1. Identifica el corredor conflictivo.
2. Comprueba si hay capacidad para reorganizar ambas.
3. Examina alternativas topológicas.
4. Reasigna tomas si procede.
5. Vuelve a optimizar el conjunto afectado.
6. Acepta el intercambio solo si mejora la solución global.

No permitir que una ruta calculada primero conserve prioridad permanente sobre otra más restringida.

---

# 14. FUNCIÓN OBJETIVO GLOBAL

Implementa una comparación lexicográfica de soluciones.

## Nivel obligatorio

Cero incumplimientos de restricciones absolutas.

No permitir:

- Cubierta atravesada.
- Conducto descolgado.
- Colisión prohibida.
- Uso duplicado de una toma.
- Mezcla de redes.
- Geometría fuera del dominio autorizado.

## Primer objetivo de optimización

**Maximizar el número de conexiones geométricamente viables.**

Una solución de nueve rutas válidas debe preferirse a otra de ocho rutas más cortas.

Las rutas condicionadas a aprobación estructural deberán contabilizarse separadamente de las ya autorizadas.

## Segundo objetivo

Resolver la compatibilidad de todas las rutas y reducir intervenciones difíciles.

## Tercer objetivo

Minimizar el coste conjunto:

```text
Coste =
longitud equivalente
+ giros
+ penetraciones
+ congestión
+ penalización por proximidad
+ penalización por complejidad de conexión
```

Los valores numéricos de estos costes no representan pérdidas de carga ni euros.

## Cuarto objetivo

Optimizar las características aeráulicas cuando existan datos verificables.

No utilizar pérdidas de presión desconocidas como cero.

No sacrificar la viabilidad de una conexión para obtener pequeñas mejoras de longitud.

---

# 15. REDISEÑO DEL PRESUPUESTO DE CÁLCULO

Amplía los límites de tiempo y elimina fragmentaciones perjudiciales.

## 15.1. Modos

Implementa:

```yaml
routing_modes:
  benchmark:
    total_seconds: 120
  standard:
    total_seconds: 240
  deep:
    total_seconds: 600
  exhaustive:
    total_seconds: 1000
```

El modo predeterminado de uso será `standard`.

El usuario podrá seleccionar `deep` y `exhaustive` desde interfaz y MCP.

Los tiempos serán máximos, no tiempos de ejecución obligatorios.

## 15.2. Presupuesto dinámico

El planificador debe disponer de un plazo global basado en tiempo monotónico.

Las operaciones deberán recibir el tiempo restante real.

Elimina el comportamiento por el cual un límite fijo de 20 segundos en A* impide utilizar el presupuesto disponible en búsquedas difíciles.

No asignes permanentemente un número pequeño de segundos a cada ruta.

## 15.3. Política inicial

Organiza el proceso en:

1. Preparación geométrica y topológica.
2. Descubrimiento de soluciones factibles.
3. Coordinación global.
4. Refinamiento.
5. Validación.

Da prioridad al descubrimiento de rutas faltantes sobre la mejora marginal de las existentes.

Como orientación de planificación inicial:

- Hasta 15 % para construcción y análisis topológico.
- Hasta 45 % para descubrimiento geométrico.
- Hasta 30 % para coordinación global.
- Hasta 10 % para refinamiento.

Estas proporciones no serán reservas rígidas.

El tiempo sobrante de cualquier etapa debe poder reasignarse.

Si el modelo topológico ya está preparado y validado, reutilízalo.

Si existen cinco rutas pendientes, el presupuesto restante debe favorecerlas frente a volver a optimizar repetidamente las cuatro ya válidas.

## 15.4. Aumento progresivo de profundidad

Para cada ruta difícil:

1. Búsqueda rápida en corredores prioritarios.
2. Ampliación de candidatos topológicos.
3. Evaluación de tomas alternativas.
4. Búsqueda con resolución adaptativa.
5. Alternativas de penetraciones.
6. Coordinación multirruta.
7. Búsqueda exhaustiva dentro del tiempo restante.

No repetir exactamente el mismo intento con parámetros equivalentes.

## 15.5. Terminación anticipada

Si todas las rutas quedan resueltas y el sistema no identifica mejoras relevantes pendientes, puede finalizar.

No consumir 1.000 segundos por obligación.

---

# 16. REUTILIZACIÓN Y RENDIMIENTO

El cálculo geométrico se ejecutará preferentemente fuera del contexto de transacciones Revit.

La API de Revit permanecerá en el hilo y contexto válido correspondiente.

Reutiliza los procesos CPython existentes.

Implementa cachés con dependencias identificables para:

- Geometría estática.
- Mallas y BVH.
- Regiones libres.
- Grafo topológico.
- Portales.
- Celdas convexas.
- Corredores.
- Resultados de docking.
- Consultas de colisión.
- Candidatos geométricos.

La caché debe distinguir entre:

**Datos estáticos:** edificio, superficies, obstáculos y conectores.

**Datos dinámicos:** rutas reservadas, tomas ocupadas, restricciones y aprobaciones.

No reutilizar como libre una trayectoria que ahora está ocupada.

No repetir una descomposición convexa idéntica por cada terminal si puede compartirse.

Evalúa el paralelismo mediante tiempo real de cálculo y consumo de memoria. No presupongas que utilizar el máximo número de procesos es siempre óptimo.

---

# 17. VALIDACIÓN GEOMÉTRICA ESTRICTA

Implementa una validación independiente de los algoritmos que generan las rutas.

El planificador no debe ser juez de su propio resultado.

## 17.1. Validación continua

Comprueba:

- Envolvente física completa.
- Pertenencia al volumen autorizado.
- Límite inferior del falso techo.
- Límite superior.
- Cubierta.
- Obstáculos.
- Otras rutas.
- Radios de curvatura.
- Continuidad geométrica.
- Compatibilidad de conexiones.
- Portales y penetraciones.

Las aproximaciones mediante cápsulas o mallas pueden utilizarse como filtros, pero la certificación final debe ser suficientemente precisa para evitar falsos positivos.

## 17.2. Portales condicionados

Un paso a través de un muro de hormigón no debe provocar un rechazo automático del diseño.

Debe producir un estado condicionado a revisión técnica.

En la comprobación de colisiones, permite excluir únicamente la porción del muro ocupada por una penetración identificada y autorizada para el escenario de diseño.

No ignores el sólido completo del muro.

No ignores otros obstáculos que coincidan con ese paso.

La cubierta nunca podrá excluirse.

## 17.3. Geometría nativa

Revisa `revit/proposals.py` y `revit/geometry_conflicts.py`.

La validación nativa deberá comprobar el volumen de los elementos resultantes, no solamente que los puntos muestreados estén por encima del falso techo.

Si un elemento queda fuera de las regiones autorizadas, debe registrarse el incumplimiento aunque no exista una región inferior localizada en ese punto.

No autorices rutas únicamente porque no haya podido resolverse una comprobación de sólidos.

Cuando falte geometría o falle una operación booleana, registra un estado no verificado.

---

# 18. MODELADO NATIVO: TRANSACCIONES SEGURAS

Refactoriza `revit/network_model.py`.

Hay que impedir que un resultado parcial termine registrado como una instalación correctamente conectada.

## 18.1. Validación antes del commit

Para cada ruta, comprueba:

- Existencia de ambos conectores.
- Disponibilidad.
- Compatibilidad de red.
- Diámetros.
- Familias.
- Accesorios.
- Geometría creada.
- Conexiones de ambos extremos.
- Uniones interiores.
- Ausencia de colisiones prohibidas.
- Estado de las penetraciones constructivas.

## 18.2. Fallos de conexión

No confirmar la transacción como éxito si:

- Una transición obligatoria falla.
- Un extremo queda desconectado.
- Una unión necesaria no se crea.
- La geometría real no puede verificarse.
- Se infringe un límite absoluto.

Utiliza transacciones o grupos de transacciones que permitan revertir la operación correspondiente de forma coherente.

Si se permite conservar un resultado parcial para diagnóstico, deberá ser una operación explícita y no quedar marcado como ruta final válida.

## 18.3. Flexibles

Revisa que los puntos enviados a `FlexDuct.Create` representen una geometría compatible con el radio de curvatura y con la familia real.

No conviertas automáticamente cada punto muestreado de una curva en una sucesión de codos rígidos.

Para conductos principales rígidos, genera tramos y accesorios nativos adecuados a la geometría.

## 18.4. Tapas

Modifica `distributor_caps.py`.

No colocar tapas definitivas mientras existan rutas pendientes que puedan utilizar esas tomas.

Cierra únicamente puertos definitivamente libres cuando se haya solicitado finalizar la configuración de la red.

No se debe alterar una conexión previamente existente sin autorización.

---

# 19. DIAGNÓSTICO OBLIGATORIO DE TODAS LAS RUTAS

Implementa:

`core/routing_diagnostics.py`

Genera un informe estructurado por ruta.

Cada resultado deberá identificar:

```text
route_id
terminal_element_id
network_type
selected_distributor
compatible_ports
tested_ports
admissible_dockings
source_region
target_region
reachable_region_count
topological_candidate_count
mandatory_passages
structural_penetration_candidates
geometric_candidate_count
tested_algorithms
time_by_algorithm
expanded_nodes
last_blocking_element
last_blocking_xyz_mm
best_partial_path
final_status
```

Distingue:

```text
VALID
VALID_WITH_WARNINGS
CONDITIONAL_STRUCTURAL
TOPOLOGY_DISCONNECTED
PORTAL_GENERATION_INCOMPLETE
NO_COMPATIBLE_DOCKING
NO_GEOMETRIC_ROUTE
BEND_CONSTRAINT_FAILED
NETWORK_CONFLICT
TIME_BUDGET_EXHAUSTED
NUMERICAL_VALIDATION_FAILED
NATIVE_GEOMETRY_UNVERIFIED
NATIVE_CONNECTION_FAILED
```

Los estados podrán ajustarse para conservar compatibilidad con el esquema existente.

**No agrupar todos los fallos bajo `NO_CERTIFIED_ROUTE_IN_SEARCH_LIMITS`.**

Ese estado podrá mantenerse como resumen, pero siempre acompañado de una causa específica o de la fase que no llegó a evaluarse.

Los informes deben identificar qué motor intentó cada alternativa, cuánto tiempo consumió y por qué fue descartada.

---

# 20. PRUEBAS DE DIAGNÓSTICO ANTES DE LA COMPARATIVA

Ejecuta primero el escenario de nueve conexiones en condiciones controladas.

## Prueba A — Rutas individuales

Calcula cada ruta por separado sin las reservas de las demás.

Objetivo: determinar si el fallo procede de la geometría o de la coordinación.

## Prueba B — Conectividad topológica

Comprueba las nueve conexiones mediante el grafo de regiones y portales, sin exigir todavía curvas completas.

Objetivo: encontrar regiones desconectadas o pasos ausentes.

## Prueba C — Tomas

Comprueba todas las tomas disponibles de los distribuidores correspondientes.

Objetivo: verificar que ninguna solución queda excluida por asignación anticipada.

## Prueba D — Accesorios

Prueba las alternativas de docking con las familias reales.

Objetivo: separar los problemas de arranque de los problemas del corredor.

## Prueba E — Envolventes

Compara la situación original con la nueva representación localizada de transiciones y codos.

Objetivo: identificar rutas descartadas por sobredimensionamiento artificial.

## Prueba F — Hormigón

Permite penetraciones propuestas y evalúa su efecto en la conectividad.

No perforar elementos durante estas pruebas.

## Prueba G — Coordinación

Calcula las nueve rutas conjuntamente y compara con las soluciones individuales.

Objetivo: localizar conflictos de ocupación y comprobar la eficacia de la planificación global.

---

# 21. COMPARACIÓN DE PRESUPUESTOS

Realiza ensayos con:

```text
120 s
180 s
240 s
600 s
1000 s
```

Todos deben utilizar:

- Misma geometría.
- Mismas restricciones.
- Misma versión del código para la comparación interna.
- Mismo hardware.
- Cachés documentadas.
- Condiciones de paralelismo registradas.

Distingue los tiempos de:

- Preparación de geometría.
- Construcción de grafo.
- Generación de candidatos.
- Resolución geométrica.
- Coordinación.
- Refinamiento.
- Validación.

El objetivo de ampliar el presupuesto no es hacer más iteraciones arbitrarias.

Es permitir explorar corredores, conectores y combinaciones que antes quedaban fuera de los límites.

Si el resultado permanece estancado, identifica dónde se produce ese estancamiento.

---

# 22. PRUEBAS UNITARIAS, DE INTEGRACIÓN Y DE REGRESIÓN

Mantén las existentes y añade pruebas para:

1. Continuidad entre falsos techos de habitaciones adyacentes.
2. Dos falsos techos con pequeñas diferencias numéricas entre planos.
3. Planos inclinados.
4. Espacios estrechos.
5. Conductos de diferentes diámetros.
6. Transiciones cuyo volumen es mayor que el del conducto.
7. Codos próximos a conectores.
8. Conectores con salida recta bloqueada.
9. Diez tomas en un distribuidor.
10. Toma viable situada fuera de las tres primeras.
11. Penetraciones en hormigón.
12. Prohibición absoluta de cubierta.
13. Ausencia de descuelgues.
14. Múltiples conductos compartiendo corredor.
15. Reorganización de rutas válidas que bloquean otra.
16. Pasos situados en muros que antes no se alcanzaban por el límite de 500 candidatos.
17. Colisiones entre curvas reales.
18. Pérdida de conectividad por reducción de diámetro libre.
19. Modelos vinculados y transformaciones.
20. Geometría ausente o degenerada.
21. Errores de transición y conexión Revit.
22. Rollback de transacciones fallidas.
23. Persistencia de propuestas y aprobación.
24. Cancelación cooperativa y plazos.
25. Cachés válidas e invalidadas.
26. Funcionamiento de la interfaz y del MCP.

Para cada prueba registra el resultado esperado y el obtenido.

No aceptar una prueba que simplemente compruebe que una función devuelve un objeto, sin verificar la geometría o el comportamiento buscado.

---

# 23. CRITERIOS CUANTITATIVOS DE ACEPTACIÓN

## A. Cobertura de tomas

El 100 % de las tomas compatibles debe recibir una evaluación de admisibilidad.

Las que no lleguen a búsqueda detallada por presupuesto deben quedar identificadas.

## B. Cobertura topológica

Todas las conexiones deben tener diagnóstico de alcanzabilidad.

No aceptar pérdidas silenciosas de regiones o portales.

## C. Seguridad geométrica

Cero violaciones de límites absolutos.

Cero rutas declaradas válidas con geometría no verificada.

## D. Resultados

La referencia es cuatro de nueve.

El objetivo funcional es llegar a nueve de nueve cuando exista solución física y constructiva viable.

No existe permiso para forzar ese resultado relajando requisitos.

## E. Rendimiento

Demostrar que el incremento del presupuesto de cálculo produce más exploración útil y no únicamente más intentos repetidos.

## F. Calidad de arquitectura

Los algoritmos deben compartir geometría, topología, candidatos y resultados.

No crear otros motores independientes que dupliquen trabajo sin aportar mejoras demostradas.

## G. Modelado

Una ruta solo se marcará como nativamente conectada si todas las conexiones necesarias han sido verificadas.

Una penetración estructural propuesta no equivale a una perforación ejecutada o autorizada.

---

# 24. ORDEN OBLIGATORIO DE IMPLEMENTACIÓN

## Hito 1 — Base geométrica fiable

Corregir:

- Envolventes.
- Tolerancias.
- Continuidad de falsos techos.
- Límites absolutos.
- Clasificación de cubierta.
- Generación de penetraciones.

Ejecutar pruebas geométricas.

## Hito 2 — Conectividad global

Implementar:

- Grafo topológico.
- Portales.
- Alcanzabilidad.
- Corredores alternativos.
- Diagnóstico de regiones.

Ejecutar las nueve pruebas topológicas.

## Hito 3 — Conexiones y tomas

Implementar:

- Docking orientado.
- Comprobación de todas las tomas.
- Asignación global.
- Compatibilidad de accesorios.

## Hito 4 — Optimización integrada

Reorganizar:

- A*.
- Corredores.
- IRIS/GCS.
- Refinamiento.
- MILP/HiGHS.
- Coordinación multirruta.

Todos los motores deben compartir candidatos y límites de tiempo.

## Hito 5 — Escalabilidad

Implementar:

- Modos de presupuesto.
- Planificador dinámico.
- Cachés.
- Paralelismo.
- Cancelación.

## Hito 6 — Revit y validación

Revisar:

- Geometría nativa.
- Conexiones.
- Transacciones.
- Tapas.
- Auditoría.
- MCP e interfaz.

## Hito 7 — Comparativa final

Ejecutar el escenario completo.

Comparar contra la referencia de cuatro de nueve.

Documentar mejoras y problemas pendientes.

Cada hito debe quedar integrado y probado antes de avanzar al siguiente. No sustituir pruebas por declaraciones de que la implementación está preparada.

---

# 25. ENTREGABLES

Al finalizar necesito:

**1. Código implementado.** No únicamente propuestas o pseudocódigo.

**2. Arquitectura V2 documentada.** Responsabilidad de cada módulo e interfaces entre capas.

**3. Registro de defectos corregidos.** Relación entre cada defecto original y su corrección.

**4. Nuevas pruebas.** Resultados reales de las baterías automatizadas.

**5. Informe de las nueve rutas.** Diagnóstico individual y resultado final.

**6. Comparación de presupuestos.** 120, 180, 240, 600 y 1.000 segundos cuando resulte posible ejecutar cada escenario.

**7. Comparativa entre métodos.** Evaluación de la contribución de topología, A*, IRIS/GCS, optimización conjunta y refinamiento.

**8. Verificación Revit.** Diferenciar propuestas, validación geométrica y conexiones físicas realmente creadas.

**9. Registro de pendientes.** Para cada problema no resuelto, causa identificada, pruebas que la sustentan y siguiente corrección técnica necesaria.

**10. Instrucciones operativas.** Cómo utilizar y diagnosticar el nuevo motor desde pyRevit y MCP.

---

# 26. INSTRUCCIÓN FINAL DE EJECUCIÓN

Tienes autorización para tomar decisiones menores de implementación que sean necesarias para completar esta arquitectura.

No necesito que me preguntes:

- Cómo denominar clases auxiliares.
- Cómo organizar funciones privadas.
- Cómo distribuir archivos pequeños.
- Cómo estructurar pruebas internas.
- Qué heurística secundaria es más conveniente cuando puede compararse mediante benchmarks.

Sí debes respetar todas las restricciones físicas y la compatibilidad con el sistema existente.

No debes inventar geometría de fabricante, aprobar penetraciones estructurales ni ejecutar modificaciones destructivas sin la autorización correspondiente.

**Trabaja en el código, ejecuta pruebas, identifica fallos y continúa corrigiéndolos dentro del encargo.**

No finalices al conseguir que pasen las pruebas unitarias si la situación real de nueve rutas continúa estancada y no se ha diagnosticado la causa.

Tampoco declares mejoras sin comparar la geometría y las condiciones del ensayo.

La finalidad es construir un sistema productivo capaz de diseñar instalaciones completas con un comportamiento comparable al razonamiento de un técnico MEP experimentado.

**La arquitectura debe razonar sobre la instalación global, no simplemente buscar nueve caminos aislados.**