# ARQ-T MEP V2.1
## CORRECCIÓN PROFUNDA DEL PLANIFICADOR GLOBAL EXISTENTE

### 0. OBJETIVO Y ALCANCE

Necesito una intervención técnica profunda sobre el motor MEP V2 que ya está desarrollado.

**No debes introducir nuevas familias de algoritmos ni volver a diseñar la arquitectura desde cero.**

El objetivo es corregir las deficiencias de implementación e integración de los sistemas existentes para obtener un planificador más estable, eficiente, determinista cuando corresponda y capaz de aprovechar correctamente todas las soluciones que descubre.

La implementación dispone actualmente de:

- Grafo topológico de regiones y portales.
- Análisis de conectividad.
- Asignación conjunta de corredores y tomas.
- A* tridimensional.
- Mapa incremental con validación diferida.
- IRIS/GCS aproximado.
- RRT-Connect.
- Optimización combinatoria.
- Negociación cooperativa de rutas.
- Cálculo paralelo.
- Validación geométrica.
- Persistencia de propuestas.
- Integración con Revit y MCP.

Estos componentes deben conservarse.

La campaña de pruebas ha obtenido hasta siete de nueve recorridos centrales válidos.

Los terminales que permanecen pendientes en el mejor resultado final son:

- `3366660`
- `3366668`

El terminal `3366668` tuvo anteriormente una trayectoria central individual validada, por lo que su desaparición del mejor conjunto merece una investigación específica.

**Objetivo prioritario: conseguir que la planificación global aproveche todas las rutas válidas descubiertas, explore alternativas realmente nuevas y maximice el número de conexiones simultáneamente compatibles.**

No buscar mejoras aparentes mediante relajación de las restricciones físicas.

---

# 1. CONGELAR Y CARACTERIZAR LA IMPLEMENTACIÓN ACTUAL

Antes de modificar código:

1. Inspecciona el estado real del repositorio.
2. Comprueba qué cambios de la V2 están integrados y cuáles permanecen incompletos.
3. Conserva una referencia del código actual mediante Git.
4. Localiza los archivos de las campañas anteriores.
5. Comprueba las configuraciones utilizadas en los ensayos.
6. Identifica los snapshots geométricos y sus revisiones.
7. Comprueba qué resultados corresponden a ejecuciones nuevas y cuáles a continuaciones.

Utiliza especialmente:

```text
docs/MEP_V2_IMPLEMENTACION.md
tests/artifacts/routing-v2-final-campaign.json
tools/benchmark_routing_v2.py
tools/benchmark_routing_v2_campaign.py
```

No presupongas que un resultado anterior puede combinarse con el actual si no coinciden sus condiciones de validez.

Antes de reutilizar candidatos, deben comprobarse:

- Identidad del documento.
- Revisión geométrica.
- Configuración efectiva.
- Diámetros y envolventes.
- Conectores.
- Clasificación de obstáculos.
- Pasos disponibles.
- Aprobaciones.
- Restricciones y rutas bloqueadas.

El resultado del diagnóstico inicial debe permitir reproducir cualquier ensayo.

---

# 2. CORREGIR EL MECANISMO DE CONSERVACIÓN DE LA MEJOR SOLUCIÓN GLOBAL

**Prioridad P0.**

Este es el cambio que implementaría primero.

## 2.1. Problema actual

`multi_route.py` conserva rutas anteriores y mantiene una variable `best`.

Sin embargo, el proceso de reconstrucción de soluciones anteriores y la búsqueda de nuevos conjuntos no constituyen todavía un mecanismo suficientemente explícito de conservación de la mejor combinación global certificada.

Además, el conjunto anterior puede reconstruirse aceptando rutas secuencialmente.

Cuando existen conflictos entre candidatos, el resultado puede depender del orden de reconstrucción.

No quiero que el mejor conjunto de rutas se pierda, degrade o reconstruya incorrectamente por este motivo.

## 2.2. Implementar GlobalIncumbent

Crea un módulo:

```text
routing/core/global_incumbent.py
```

Su responsabilidad será conservar la mejor combinación global de rutas compatible con las condiciones actuales.

Implementa una estructura equivalente a:

```python
class GlobalIncumbent:
    def __init__(self, context_signature):
        ...

    def consider(self, candidate_network, validator):
        ...

    def best(self):
        ...

    def invalidate(self, changed_dependencies):
        ...

    def export_state(self):
        ...

    def restore_state(self, state, validator):
        ...
```

El código definitivo puede adaptarse al estilo existente.

### Información almacenada

La solución global deberá guardar:

- Identificadores de todas las rutas.
- Geometría de las rutas resueltas.
- Tomas utilizadas.
- Corredores.
- Portales.
- Radios efectivos.
- Resultados de validación.
- Interferencias comprobadas.
- Penetraciones condicionadas.
- Coste global.
- Revisión del documento.
- Firma de las restricciones.
- Origen de cada candidato.
- Instante lógico de descubrimiento.

Los resultados deberán poder serializarse sin referencias a objetos vivos de Revit.

## 2.3. Validación de la combinación completa

Antes de sustituir la mejor solución:

1. Comprueba cada ruta individualmente.
2. Comprueba conflictos entre todas las rutas seleccionadas.
3. Comprueba exclusividad de conectores.
4. Comprueba que las redes SUPPLY y EXTRACT siguen separadas.
5. Comprueba restricciones absolutas.
6. Comprueba penetraciones condicionadas.
7. Calcula el resultado global.
8. Compara con la mejor solución existente.

No aceptar un conjunto porque sus rutas sean válidas individualmente si no pueden coexistir.

## 2.4. Comparación lexicográfica

Establece esta prioridad:

**Primero:** cero incumplimientos de restricciones absolutas.

**Segundo:** maximizar las rutas centrales geométricamente válidas y simultáneamente compatibles.

**Tercero:** minimizar las intervenciones constructivas condicionadas.

**Cuarto:** minimizar conflictos pendientes y costes constructivos.

**Quinto:** minimizar longitud, giros y costes secundarios.

Las rutas condicionadas deberán identificarse separadamente. No contabilices una penetración estructural propuesta como autorizada.

Una nueva solución de seis rutas nunca debe sustituir a otra de siete rutas igualmente válidas porque tenga una longitud total inferior.

## 2.5. Monotonicidad del mejor resultado

Dentro de una misma búsqueda continuada, con geometría y restricciones idénticas, el número de rutas del mejor conjunto compatible no debe disminuir.

La exploración puede probar soluciones peores.

El resultado conservado no debe empeorar.

Si una validación posterior demuestra que la solución anterior era incorrecta, puede invalidarse, pero deberá registrarse explícitamente la causa.

Distingue:

- Solución nueva.
- Mejor solución global.
- Solución invalidada.
- Solución sustituida por otra mejor.
- Solución conservada.

Este comportamiento debe verificarse mediante pruebas específicas.

---

# 3. REVISAR LA RECONSTRUCCIÓN DE SOLUCIONES AL CONTINUAR UNA SESIÓN

Inspecciona:

```text
multi_route.py
service.py
global_planner.py
portfolio.py
native_feedback.py
```

Actualmente se reutilizan propuestas anteriores, rutas y bancos de candidatos.

Quiero garantizar que esa reutilización es correcta.

## 3.1. No reconstruir soluciones mediante aceptación arbitraria

Si una propuesta anterior contiene siete rutas compatibles, restaurar exactamente ese conjunto antes de iniciar nuevas optimizaciones.

No reconstruirlo mediante una aceptación secuencial que pueda descartar rutas por un orden diferente.

Primero validar la solución completa.

Si el conjunto sigue siendo válido, conservarlo como incumbent inicial.

Si falla, identificar qué relaciones han dejado de ser válidas.

Solo entonces reconstruir el mejor subconjunto compatible mediante el selector conjunto.

## 3.2. Continuación entre ejecuciones

Al continuar una búsqueda deben conservarse:

- Mejor conjunto global.
- Candidatos individuales.
- Corredores descubiertos.
- Tomas evaluadas.
- Aristas estáticas verificadas.
- Celdas convexas reutilizables.
- Caminos rechazados y motivos.
- Información relevante de exploración.

No repetir por defecto la preparación completa si las condiciones son idénticas.

## 3.3. Validación de cambios

Si cambia un conector o un elemento geométrico, invalida únicamente los resultados realmente dependientes de ese cambio cuando pueda determinarse con seguridad.

Si no puede garantizarse una invalidación parcial correcta, reconstruye la información afectada.

Nunca reutilices una validación geométrica correspondiente a otro diámetro o conjunto de obstáculos.

---

# 4. REESTRUCTURAR EL BANCO DE CANDIDATOS

**Prioridad P0.**

Este es otro punto fundamental.

Actualmente `portfolio.py` y los procesos de selección utilizan límites de candidatos que pueden favorecer las primeras soluciones encontradas.

El problema no es únicamente cuántos candidatos existen.

El problema es qué diversidad representan.

## 4.1. Banco global estructurado

Implementa un banco de candidatos compartido:

```text
routing/core/candidate_repository.py
```

Cada candidato tendrá:

```python
{
    "route_id": ...,
    "source_port_id": ...,
    "destination_port_id": ...,
    "network_type": ...,
    "scenario": ...,
    "topology_signature": ...,
    "portal_signature": ...,
    "geometry_signature": ...,
    "search_method": ...,
    "geometry_status": ...,
    "approval_status": ...,
    "cost": ...,
    "points_mm": ...,
    "primitives": ...,
    "validation_context": ...
}
```

## 4.2. Firmas de diversidad

No considerar diferentes dos candidatos únicamente porque sus puntos tengan coordenadas distintas.

Define:

**Firma topológica:** secuencia de regiones y pasos.

**Firma constructiva:** muros y penetraciones atravesados.

**Firma de conexión:** toma del distribuidor y docking utilizado.

**Firma geométrica:** representación normalizada de la trayectoria.

Dos candidatos próximos geométricamente, pero que utilizan el mismo conector y la misma secuencia de pasos, pertenecen a una familia similar.

Dos candidatos que atraviesan muros diferentes deben conservarse como alternativas distintas, aunque tengan longitudes parecidas.

## 4.3. Eliminación de duplicados

Elimina candidatos verdaderamente equivalentes.

No elimines un candidato únicamente porque su longitud sea ligeramente mayor.

Puede ser necesario para evitar conflictos con otras rutas.

## 4.4. Política inicial de conservación

Propongo estos límites iniciales:

```text
Máximo preferente por ruta: 24 candidatos.

Máximo preferente por familia topológica: 3.

Reservar al menos una alternativa por toma y corredor admisible
cuando exista una solución geométrica validada.

Mantener candidatos que formen parte del GlobalIncumbent
independientemente del límite ordinario.
```

Los límites son de memoria y rendimiento, no de validez geométrica.

Si aparecen más de 24 alternativas relevantes, aplica una selección por diversidad y dominancia.

No conservar automáticamente los primeros 24.

No permitir que diez variaciones casi idénticas ocupen el espacio de otras alternativas constructivamente distintas.

## 4.5. Dominancia

Un candidato podrá dominar a otro cuando:

- Utilicen la misma toma.
- Pertenezcan a la misma familia topológica.
- Tengan condiciones de aprobación equivalentes.
- Uno tenga un coste menor.
- Su geometría no introduzca una ocupación espacial peor relevante para la coordinación.

La longitud inferior no basta por sí sola para establecer dominancia.

Antes de descartar un candidato por ocupación, verifica que su sustituto no introduce conflictos nuevos con otras rutas.

---

# 5. INTEGRAR CORRECTAMENTE TODOS LOS ALGORITMOS EXISTENTES

**Prioridad P1.**

No quiero que cada algoritmo sea una búsqueda independiente que devuelve un resultado aislado.

Todos deben publicar candidatos en el mismo repositorio.

## 5.1. Flujo de trabajo

El planificador global deberá seguir aproximadamente:

```text
GlobalPlanner
    |
    +-- TopologyGraph
    |       |
    |       +-- Regiones
    |       +-- Portales
    |       +-- Corredores
    |
    +-- CandidateRepository
    |       |
    |       +-- A*
    |       +-- Incremental Roadmap
    |       +-- IRIS/GCS
    |       +-- RRT-Connect
    |       +-- Reparación de conectores
    |
    +-- JointCoordinator
    |       |
    |       +-- Asignación de tomas
    |       +-- Selección de corredores
    |       +-- Compatibilidad entre rutas
    |       +-- Reorganización
    |
    +-- GlobalIncumbent
            |
            +-- Validación
            +-- Conservación
            +-- Continuación
```

La implementación puede mantener módulos existentes y añadir adaptadores.

No duplicar innecesariamente clases o datos.

## 5.2. Resultados compartidos

Cuando RRT-Connect encuentre una ruta:

- Incorporarla al repositorio.
- Conservar su secuencia de portales.
- Registrar el corredor utilizado.
- Permitir refinamiento geométrico.
- Permitir comparación con rutas de otros métodos.

Cuando A* encuentre una trayectoria distinta:

- Incorporarla igualmente.
- Registrar su diversidad topológica.
- No descartarla únicamente porque RRT ya encontró una solución más corta.

Cuando IRIS/GCS genere una región útil:

- Conservar la información reutilizable.
- Relacionarla con los corredores topológicos.
- Facilitar su empleo en búsquedas posteriores.

La validación geométrica debe ser común.

## 5.3. Evitar trabajo duplicado

Antes de lanzar un nuevo intento, consultar:

- ¿Se ha explorado ya este corredor?
- ¿Con esta toma?
- ¿Con este perfil?
- ¿Con estas restricciones?
- ¿Qué métodos se utilizaron?
- ¿Por qué fracasaron?
- ¿Qué información puede reutilizarse?

No repetir la misma combinación de parámetros si no ha cambiado ninguna condición relevante.

---

# 6. REDISEÑAR EL GESTOR DE PRESUPUESTO

**Prioridad P0.**

Actualmente hay múltiples repartos de tiempo entre:

- Pasadas globales.
- Planificación inicial.
- Búsquedas individuales.
- IRIS.
- Roadmaps.
- A*.
- RRT.
- Coordinación.
- Refinamiento.

Quiero sustituir esta fragmentación por un planificador dinámico.

## 6.1. Crear SearchBudgetManager

Implementa:

```text
routing/core/search_budget.py
```

Responsabilidades:

- Controlar un único plazo global.
- Registrar consumo por motor.
- Asignar tiempo a nuevas tareas.
- Cancelar tareas de menor prioridad.
- Reasignar presupuestos no consumidos.
- Priorizar rutas pendientes.
- Limitar búsquedas redundantes.
- Reservar tiempo para seleccionar y validar candidatos.

No eliminar la cancelación cooperativa existente.

## 6.2. Modos

Conservar:

```python
ROUTING_MODES = {
    "benchmark": 120,
    "standard": 240,
    "deep": 600,
    "exhaustive": 1000
}
```

No modificar esos valores en esta fase.

## 6.3. Primera solución

Mientras existan conexiones sin ningún candidato viable, priorizar la búsqueda de una primera solución para ellas.

No dedicar recursos excesivos a mejorar una ruta que ya dispone de varias alternativas válidas.

## 6.4. Mejora global

Cuando exista un conjunto de siete rutas:

1. Guardarlo como incumbent.
2. Identificar las dos pendientes.
3. Buscar candidatos para ellas.
4. Identificar los conflictos que impiden incorporarlas.
5. Reoptimizar los grupos afectados.
6. Comparar cualquier nueva combinación con el incumbent.
7. Conservar la mejor.

No recalcular las nueve rutas desde cero en cada pasada.

## 6.5. Asignación adaptativa

Utiliza información del progreso real:

- Nuevos corredores descubiertos.
- Nuevas tomas evaluadas.
- Nuevos candidatos válidos.
- Candidatos que mejoran el conjunto.
- Repetición de fallos.
- Coste de preparación del algoritmo.
- Tiempo desde el último progreso.

Implementa una política de asignación basada en prioridad y rendimiento observado.

No es necesario incorporar aprendizaje automático.

Una estrategia determinista basada en puntuaciones será suficiente inicialmente.

## 6.6. Presupuestos por intento

Elimina límites internos que impidan utilizar el tiempo restante cuando una ruta sea difícil.

Un intento debe recibir un plazo explícito del gestor.

No volver a dividir recursivamente el tiempo disponible mediante porcentajes fijos sin comprobar el coste real.

Mantén un margen para consolidar, validar y guardar los resultados antes del plazo global.

---

# 7. MEJORAR LA CONTINUACIÓN DEL MAPA INCREMENTAL

Revisa:

```text
core/incremental_roadmap.py
parallel.py
parallel_worker.py
service.py
```

## 7.1. Problema a evitar

Ampliar el presupuesto de 120 a 600 segundos no debe producir únicamente una nueva ejecución que vuelve a crear nodos similares.

Debe aprovechar lo aprendido anteriormente.

## 7.2. Estado persistente

Mantén:

- Nodos geométricos.
- Aristas.
- Comprobaciones estáticas.
- Portales.
- Causas de rechazo.
- Regiones exploradas.
- Contadores de expansión.
- Semillas de muestreo.
- Información de continuidad.

## 7.3. Separar comprobaciones estáticas y dinámicas

Las comprobaciones estáticas dependen del edificio y del perfil.

Las comprobaciones dinámicas dependen de las reservas y de otras rutas.

No mezclar ambas en una misma caché sin distinguir su validez.

Cuando cambien las reservas, reutiliza la geometría estática y vuelve a verificar la ocupación dinámica.

## 7.4. Continuación real

La siguiente ejecución deberá:

1. Recuperar el estado anterior.
2. Validar su contexto.
3. Incorporar muestras nuevas.
4. Ampliar regiones insuficientemente exploradas.
5. Priorizar pasos y corredores sin soluciones.
6. Aprovechar las aristas ya comprobadas.
7. Conservar la mejor solución global.

No presentar simplemente la reutilización de una ruta como continuación de toda la búsqueda.

## 7.5. Límite de nodos

Revisa el límite actual de nodos incrementales.

Si se alcanza:

- Registrar la saturación.
- Identificar regiones poco exploradas.
- Conservar nodos críticos.
- Eliminar redundancia cuando sea seguro.
- Permitir una ampliación controlada en modos profundos.

No seguir ejecutando el método durante cientos de segundos si ya no puede incorporar información nueva.

---

# 8. CORREGIR LA INTEGRACIÓN DEL GRAFO TOPOLOGICO

Inspecciona:

```text
core/global_planner.py
core/topology_graph.py
core/portal_graph.py
core/connectivity_analysis.py
core/corridor_assignment.py
```

## 8.1. Reutilización del grafo

Actualmente se reutilizan grafos según el perfil geométrico.

Comprueba que el grafo compartido representa correctamente todas las regiones y portales necesarios para todas las conexiones del mismo perfil.

En particular, revisa que la construcción inicial de un grafo a partir de una conexión concreta no deje fuera información necesaria para otras conexiones posteriores.

La accesibilidad debe ser independiente del orden en que se procesan los terminales, salvo cuando exista una restricción dinámica explícita.

Crea una prueba que construya exactamente el mismo grafo procesando los terminales en órdenes diferentes.

Los resultados de conectividad deben ser equivalentes.

## 8.2. Grafos incompletos

Si la construcción del grafo termina por presupuesto:

- Registrar las regiones procesadas.
- Registrar los portales pendientes.
- Marcar el grafo como incompleto.
- Conservar el trabajo realizado.
- Permitir ampliarlo posteriormente.

No tratar una ausencia de conexión en un grafo incompleto como imposibilidad topológica.

## 8.3. Asignación conjunta

El sistema de asignación actual proporciona preferencias iniciales.

Mantén esa función.

Pero no conviertas las preferencias del beam search en restricciones definitivas.

La solución global debe poder reemplazarlas cuando encuentre una alternativa más favorable.

## 8.4. Diversidad de asignaciones

Revisa el ancho del beam utilizado para seleccionar tomas y estaciones de paso.

Conserva alternativas diferentes por:

- Toma.
- Corredor.
- Muro atravesado.
- Posición de penetración.
- Altura de paso.

No permitir que combinaciones muy parecidas ocupen todo el beam.

El cálculo previo de asignación no certifica la geometría tridimensional: debe continuar como orientación del optimizador detallado.

---

# 9. CORREGIR LA REASIGNACIÓN DE TOMAS SIN PERDER CORREDORES

Revisa:

```text
core/source_repair.py
core/portfolio.py
systems/heat_recovery_topology.py
```

El mecanismo actual intenta reutilizar un corredor válido cambiando la toma de origen.

La idea es correcta y merece conservarse.

Sin embargo, necesito comprobar que una reasignación actualiza coherentemente todos los metadatos de conexión.

## 9.1. Requisitos

Cuando se reasigna una toma:

1. Actualizar `source`.
2. Actualizar `chosen_source`.
3. Actualizar `source_id`.
4. Actualizar el docking de origen.
5. Actualizar los extremos geométricos.
6. Actualizar las transiciones requeridas.
7. Comprobar los diámetros.
8. Actualizar las reservas.
9. Invalidar cualquier validación nativa anterior.
10. Ejecutar nuevamente la validación geométrica.

No debe existir un candidato cuya geometría comience en una toma y cuyos metadatos hagan referencia a otra.

## 9.2. Reparación local

Si una trayectoria de RRT o A* permite alcanzar un terminal desde una toma, intenta reutilizar su corredor desde otras tomas compatibles mediante una conexión local.

No volver a calcular toda la ruta cuando únicamente cambia el tramo inicial.

La reparación debe comprobar la tangencia, el radio de giro, las transiciones y el volumen disponible.

---

# 10. MEJORAR LA COORDINACIÓN DE RUTAS CONFLICTIVAS

**Prioridad P1.**

El siguiente problema que resolvería es cómo reorganizar rutas cuando aparece una nueva conexión.

## 10.1. Construir un grafo de conflictos

Implementa:

```text
core/route_conflict_graph.py
```

Los nodos representarán candidatos de rutas.

Las aristas representarán incompatibilidades demostradas:

- Uso de la misma toma.
- Colisión geométrica.
- Ocupación incompatible de corredor.
- Penetraciones incompatibles.
- Restricciones de conexión.

Distingue conflictos definitivos de estimaciones preliminares de capacidad.

No excluir dos candidatos únicamente porque la estimación de capacidad de un corredor sea conservadora.

## 10.2. Resolver vecindarios

Cuando aparezca un candidato nuevo para una ruta pendiente:

1. Identifica qué rutas del incumbent interfiere.
2. Construye el subconjunto mínimo de rutas afectadas.
3. Conserva temporalmente las rutas ajenas al conflicto.
4. Recupera alternativas de las rutas afectadas.
5. Prueba reasignaciones de tomas.
6. Prueba corredores alternativos.
7. Ejecuta el selector combinatorio sobre el vecindario.
8. Valida el conjunto resultante.
9. Sustituye el incumbent si mejora.

Si el vecindario no puede resolverse, amplíalo progresivamente.

No recalcular indiscriminadamente las nueve conexiones.

## 10.3. Estrategia de reparación

Prioridad inicial:

**Primera:** cambiar de toma.

**Segunda:** utilizar otro corredor ya descubierto.

**Tercera:** desplazar una ruta dentro del mismo corredor.

**Cuarta:** cambiar la posición de un paso.

**Quinta:** recalcular geométricamente las rutas afectadas.

Este orden es una heurística inicial. Puede alterarse cuando la evidencia geométrica indique que otra reparación es más prometedora.

## 10.4. Evitar bloqueos prematuros

Las rutas incluidas en el incumbent no deben quedar bloqueadas permanentemente por el mero hecho de estar resueltas.

Distingue:

- `USER_LOCKED`: bloqueada expresamente por el usuario.
- `PRESERVED_EXISTING`: conexión existente que no se puede modificar sin autorización.
- `INCUMBENT_MOVABLE`: solución válida del optimizador que puede reorganizarse.

Solo las dos primeras categorías deben actuar como restricciones de conservación.

---

# 11. REVISIÓN ESPECÍFICA DE RRT-CONNECT

RRT-Connect ha demostrado aportar soluciones adicionales.

No lo sustituiría.

Revisaría su integración y reproducibilidad.

## 11.1. Semillas

Implementa semillas deterministas derivadas de:

```text
Semilla global
+ identificador de ruta
+ toma
+ corredor
+ número lógico de intento
```

No depender del orden en que terminan los trabajadores.

No utilizar funciones de hash cuyo resultado pueda variar entre procesos.

## 11.2. Diversidad

Evita dedicar varios trabajadores a muestras equivalentes.

Distribuye las búsquedas entre:

- Corredores topológicos diferentes.
- Tomas diferentes.
- Semillas diferentes.
- Zonas de paso diferentes.

## 11.3. Resultados

Cuando encuentre una ruta válida:

- Incorporarla al banco global.
- Registrar el corredor.
- Registrar las penetraciones.
- Conservar la semilla.
- Registrar el estado de validación.
- Permitir su posterior refinamiento.

El candidato no debe desaparecer porque otra ejecución utilice una semilla distinta.

## 11.4. No aumentar su protagonismo sin evidencia

No dedicar automáticamente todo el presupuesto adicional a RRT.

Comparar su rendimiento marginal con los demás métodos.

---

# 12. REVISIÓN ESPECÍFICA DE IRIS/GCS

Conservar el algoritmo actual.

No implementar una nueva formulación matemática global en esta fase.

La prioridad es determinar cuándo aporta valor.

## 12.1. Analizar resultados

Utiliza los diagnósticos ya incorporados para clasificar:

- Semillas bloqueadas.
- Regiones insuficientes.
- Falta de cobertura del origen.
- Falta de cobertura del destino.
- Regiones convexas desconectadas.
- Fallos numéricos.
- Tiempo de preparación agotado.
- Trayectorias rechazadas posteriormente.

## 12.2. Reutilizar resultados

No reconstruir celdas convexas idénticas cuando los obstáculos estáticos y el perfil no han cambiado.

Reutilizar las celdas como información de corredores.

## 12.3. Presupuesto condicionado

No ejecutar IRIS/GCS en todos los intentos por obligación.

Priorizarlo cuando:

- Exista conectividad topológica.
- Haya regiones con espacio libre útil.
- El método pueda optimizar un corredor conocido.
- Haya tiempo suficiente para completar sus fases.

Si falla repetidamente por la misma causa, reducir su prioridad en esa familia de búsqueda.

No eliminarlo del motor.

---

# 13. COMPROBAR LA ESTABILIDAD DEL PARALELISMO

Inspecciona:

```text
parallel.py
parallel_worker.py
core/portfolio.py
core/multi_route.py
```

El orden de finalización de los trabajadores no debería determinar qué solución global se conserva.

## 13.1. Identificadores de trabajo

Cada tarea deberá disponer de:

```text
job_id
route_id
source_port_id
corridor_signature
scenario
algorithm
seed
attempt_number
```

Las respuestas deben asociarse mediante esos identificadores.

## 13.2. Recolección de resultados

No perder candidatos válidos porque:

- Terminen después que otros.
- Finalice una pasada.
- Se cierre una ventana de planificación.
- Haya respuestas tardías de una ejecución anterior.

Los resultados tardíos solo podrán incorporarse si pertenecen al contexto correcto y siguen siendo válidos.

## 13.3. Reproducibilidad

Crear pruebas que ejecuten el mismo conjunto de trabajos con órdenes de respuesta diferentes.

El mejor resultado global debe ser equivalente cuando se dispone del mismo conjunto de candidatos.

No confundir reproducibilidad del selector con reproducibilidad temporal de toda una búsqueda paralela.

---

# 14. DIAGNÓSTICO ESPECÍFICO DE LOS DOS TERMINALES PENDIENTES

No repetir inicialmente una campaña general de 1.000 segundos.

Primero necesito conocer exactamente por qué no conseguimos completar el conjunto.

## 14.1. Terminal 3366668

Existe un resultado histórico en el que RRT encontró una trayectoria central válida para este terminal.

Localiza ese candidato.

Comprueba que el snapshot y las restricciones son compatibles con el ensayo actual.

Si no son compatibles, registra las diferencias y evita comparaciones inválidas.

Si son compatibles:

1. Revalida individualmente la geometría.
2. Comprueba la toma seleccionada.
3. Identifica su corredor.
4. Comprueba sus penetraciones.
5. Compara con las siete rutas del incumbent.
6. Construye el grafo de conflictos.
7. Determina qué rutas habría que modificar.
8. Busca alternativas de las rutas conflictivas.
9. Intenta obtener una combinación de ocho rutas.

Si la combinación no se consigue, identifica exactamente qué incompatibilidades lo impiden.

No basta con responder `NETWORK_CONFLICT`.

## 14.2. Terminal 3366660

Ejecuta una búsqueda individual profunda.

Sin reservas de otras rutas modificables.

Mantén las restricciones absolutas y las redes existentes.

Comprueba:

- Todas las tomas compatibles.
- Docking.
- Conectividad topológica.
- Corredores alternativos.
- Pasos.
- Radios de curvatura.
- IRIS.
- Mapa incremental.
- A*.
- RRT-Connect.

Registra el mejor candidato parcial.

Si aparece una ruta individual válida, incorpórala al banco y estudia su compatibilidad global.

Si no aparece, identifica la fase que impide progresar.

---

# 15. CAMPAÑA DE PRUEBAS CORRECTA

La comparación anterior contiene ejecuciones independientes de diferentes presupuestos.

No necesito que todas ellas den resultados necesariamente crecientes.

Pero quiero una prueba específica de continuación.

## 15.1. Prueba incremental

Ejecuta una única búsqueda lógica en etapas:

```text
Etapa A: presupuesto acumulado 120 s.

Etapa B: ampliar el presupuesto acumulado a 240 s.

Etapa C: ampliar el presupuesto acumulado a 600 s.

Etapa D: ampliar el presupuesto acumulado a 1000 s.
```

Conserva los resultados válidos de cada etapa.

La solución incumbent no puede empeorar si no cambian las condiciones.

## 15.2. Prueba independiente

Ejecuta también búsquedas nuevas con diferentes semillas.

Objetivo: comparar diversidad y estabilidad.

No exigir resultados idénticos si existen diferencias de muestreo o planificación.

## 15.3. Prueba del selector

Construye un caso sintético con:

- Nueve conexiones.
- Varias alternativas por conexión.
- Tomas excluyentes.
- Conflictos geométricos entre candidatos.
- Una combinación válida conocida de nueve rutas.
- Varias combinaciones subóptimas de siete y ocho.

Verifica que el selector puede recuperar la combinación completa.

Después cambia el orden de los candidatos y de las respuestas.

El resultado no debe degradarse por el orden de entrada.

## 15.4. Prueba de conservación

Construye un escenario:

1. Se descubren siete rutas válidas.
2. Se incorpora un candidato nuevo incompatible.
3. Se ejecuta negociación.
4. La negociación no encuentra mejora.
5. El mejor conjunto debe seguir conteniendo las siete originales.

Añade otro escenario donde la negociación sí encuentra una solución de ocho rutas.

La solución de ocho debe sustituir correctamente a la de siete.

## 15.5. Prueba de invalidación

Modificar artificialmente una condición relevante:

- Diámetro.
- Geometría.
- Conector.
- Obstáculo.
- Restricción.
- Aprobación de paso.

Comprobar que se invalida la información dependiente.

No conservar rutas antiguas como válidas bajo condiciones diferentes.

---

# 16. VALIDACIÓN NATIVA Y SEGURIDAD

Las modificaciones anteriores afectan principalmente al planificador.

No vuelvas a desarrollar todo el modelador Revit.

Pero asegura que la integración mantiene las validaciones existentes.

Conservar:

- Prohibición absoluta de atravesar cubierta.
- Prohibición de descuelgue bajo falso techo.
- Holguras físicas.
- Validación de radios.
- Restricciones constructivas.
- Separación de impulsión y extracción.
- Uso exclusivo de conectores.
- Protección de elementos existentes.
- Reversión de transacciones fallidas.

La geometría central validada no equivale a instalación físicamente conectada.

No contabilizar las verificaciones de sólidos centrales como éxito de modelado nativo.

Si una ruta falla al generar el flexible o sus transiciones, conservar esa información para impedir repetir exactamente la misma geometría rechazada.

Las pruebas sobre Revit deben ser reversibles, sin conservar modificaciones no autorizadas.

---

# 17. ORDEN DE IMPLEMENTACIÓN

Trabaja en este orden.

### Hito 1 — Conservación y selección

Implementar:

- `GlobalIncumbent`.
- Validación de conjuntos.
- Restauración correcta de resultados.
- Comparación lexicográfica.
- Pruebas de monotonicidad.

Este hito es obligatorio antes de seguir cambiando el reparto de tiempo.

### Hito 2 — Banco de candidatos

Implementar:

- Firmas topológicas.
- Diversidad geométrica.
- Dominancia.
- Conservación de alternativas.
- Reutilización correcta entre pasadas.

### Hito 3 — Gestor de presupuesto

Implementar:

- Plazo global.
- Asignación dinámica.
- Prioridad de pendientes.
- Control de redundancia.
- Conservación de resultados tardíos válidos.

### Hito 4 — Integración algorítmica

Corregir:

- Intercambio de candidatos.
- Grafo topológico compartido.
- Continuación incremental.
- RRT.
- IRIS/GCS.
- Reparación de tomas.
- Coordinación de corredores.

No introducir algoritmos nuevos.

### Hito 5 — Diagnóstico de 3366660 y 3366668

Ejecutar pruebas individuales y de integración.

Identificar la causa concreta de cada fallo.

### Hito 6 — Benchmark y regresión

Comparar con la implementación anterior.

Ejecutar campañas controladas.

Verificar que no se han introducido regresiones.

---

# 18. RESULTADOS QUE NECESITO

Entrega un informe con estos apartados:

**A. Defectos identificados**

Qué problema concreto se ha encontrado en el código.

**B. Corrección implementada**

Archivo, función y comportamiento modificado.

**C. Justificación**

Por qué esa modificación puede mejorar el resultado.

**D. Pruebas**

Qué pruebas se han realizado y con qué resultados.

**E. Mejor solución global**

Número de rutas compatibles, tomas utilizadas y estado de validación.

**F. Rutas pendientes**

Motivos específicos de fallo.

**G. Rendimiento**

Tiempo por algoritmo, candidatos útiles, candidatos repetidos y trabajo reutilizado.

**H. Comparativa**

Resultados antes y después con las mismas condiciones.

**I. Limitaciones restantes**

Qué problemas no se han conseguido resolver y qué evidencia los caracteriza.

---

# 19. CRITERIOS DE ACEPTACIÓN

Consideraré satisfactoria la revisión si:

1. El sistema conserva correctamente su mejor solución global.
2. Las búsquedas continuadas aprovechan el trabajo previo.
3. Las alternativas de rutas no se pierden por límites arbitrarios o por orden de llegada.
4. El reparto del presupuesto favorece las conexiones pendientes.
5. Los algoritmos comparten candidatos y resultados.
6. La coordinación puede reorganizar rutas sin destruir una solución mejor.
7. Existe un diagnóstico técnico concreto de los terminales 3366660 y 3366668.
8. No se han relajado restricciones físicas.
9. Todas las pruebas anteriores siguen pasando.
10. El rendimiento y los resultados se comparan mediante ensayos reproducibles.

El objetivo deseable es alcanzar ocho o nueve rutas simultáneamente compatibles.

No es obligatorio conseguirlo si la geometría real o las restricciones lo impiden.

Lo obligatorio es demostrar que el motor aprovecha correctamente las posibilidades disponibles.

---

# 20. INSTRUCCIÓN FINAL

Quiero que implementes las modificaciones, no que redactes otra propuesta teórica.

Antes de cambiar cada componente, comprueba cómo funciona realmente en el repositorio actual.

No dupliques funcionalidades que ya estén correctamente implementadas.

No introduzcas nuevas dependencias de optimización sin necesidad.

No modifiques la interfaz ni las herramientas independientes salvo cuando sea necesario para integrar cambios del planificador.

Realiza las modificaciones en hitos comprobables, manteniendo una versión estable del sistema.

Ejecuta las pruebas correspondientes después de cada hito.

No utilices únicamente la cantidad de pruebas superadas como evidencia de éxito. La referencia principal sigue siendo el escenario real de nueve conexiones.

**La prioridad de esta revisión no es aumentar la complejidad del motor. Es conseguir que toda la complejidad que ya hemos desarrollado trabaje correctamente, de manera conjunta y con resultados estables.**