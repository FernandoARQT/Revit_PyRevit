# Validación de CAVITY · 25/10/2026

## Causa comprobada del módulo aparentemente cortado en X

La caja de sección de {3D} está activa y su límite izquierdo mundial X=50400,237254 mm entra unos 824 mm dentro del suelo, cuyo extremo izquierdo está aproximadamente en X=49575,97 mm. Su límite derecho X=61126,005392 mm también recorta el suelo real, cuyo extremo llega a X=61130,934221 mm. El límite visible de la vista no es el límite del suelo utilizado para modular.

Run c564be2eb21b, rolled_back: matriz completa de 252 piezas, 21 × 12; márgenes sólidos izquierda/derecha 75,981165 mm e inferior/superior 64,982855 mm. Con esa misma matriz completa, la caja de sección oculta 12 piezas y corta visualmente otras 12. Esta es evidencia directa de una columna aparentemente parcial aunque sus instancias sean completas. No se cambia la fórmula ni se desplazan las piezas para compensar un recorte de vista. Se añaden avisos en vista previa y resultado que cuantifican piezas ocultas/cortadas. La caja de sección no se modifica automáticamente.

## Prueba completa tras volver a Proyecto

Run 2903b42e4b2c, dry_run, decisión rolled_back. Se ejecutó la colocación completa y sustitución sobre dos suelos diferentes, usando lectura nueva del sólido actual en cada caso. No se conservaron cambios.

| Suelo | Tamaño local mm | Matriz | Margen X mm | Margen Y mm | Primer centro desde bordes X/Y mm | Base m |
|---|---|---|---|---|---|---|
| 3073971 | 11554,962329 × 6645,965709 | 21 × 12 = 252 | 75,981165 | 64,982855 | 347,481165 / 336,482855 | 1,50 |
| 3073975 | 10755,189409 × 2511,977856 | 19 × 4 = 76 | 219,094704 | 169,988928 | 490,594704 / 441,488928 | 2,11 |

Los márgenes de los sólidos insertados coinciden con los teóricos por parejas, con diferencias numéricas inferiores a 0,000001 mm. Los primeros centros añaden media pieza (271,50 mm) al margen de inicio en cada eje. La generación comprobó posición, orientación y conservación del área/volumen del anfitrión antes de finalizar. Queda pendiente la confirmación visual del usuario al ejecutar el botón actualizado.

## Lectura actual y cambio entre ejecuciones

- El documento activo pasó al editor de familia; se leyó el proyecto abierto sin modificarlo. Run a4aa4f0dd507: suelo 3073971 ahora mide 11554,962329 × 6645,965709 mm, área 76,787726 m². Nuevo cálculo: 21 × 12 = 252 piezas de 543 mm, inicio 75,981165 / 64,982855 mm. Se calcularon también otros dos suelos con tamaños/contornos diferentes en la misma consulta.
- En esta consulta, referencia de cara y cara del sólido coinciden en área. La diferencia respecto a las consultas anteriores de 52,83 m² NO demuestra por sí sola un fallo de GetGeometryObjectFromReference; el modelo pudo cambiar entre consultas. Queda corregida la atribución causal de la comunicación anterior.
- Se cambia la fuente de contorno a caras del sólido actual y se añade control de documento/suelo/contorno antes de generar. Un cambio posterior al cálculo obliga a recalcular. Las matrices independientes ya creadas no se actualizan automáticamente al editar su suelo.
- Añadida regresión con tres ejecuciones sucesivas sobre tamaños, posiciones y piezas distintas, verificando primer centro = borde + mitad de sobrante + media pieza.

## Auditoría de la matriz existente

Sin modificar elementos, runs 1b271694f4f2 y 0712f884cd3f: suelo 3073971, tipo Cavity. Matriz existente de 171 piezas H14, 19 columnas y 9 filas. Selección activa de 108 piezas, solo 12 columnas por 9 filas. Los límites de la selección parcial están desplazados respecto al suelo, pero los límites de la matriz completa no.

Código cargado inspeccionado mediante __file__ y solve.__code__.co_names: corresponde al archivo actual, con cálculo explícito de sobrante/2. Límites de sólidos: márgenes 219,094316 mm a izquierda y derecha, 12,886214 mm en ambas direcciones de Y. Una segunda comprobación recorre las mallas trianguladas de los sólidos, sin usar solid_bounds, y confirma el centrado con diferencias de unas milésimas de mm por teselado.

El suelo mide aproximadamente 10755,189 × 4912,772 mm en sus ejes locales; sus aristas se desvían menos de 1 mm de un rectángulo exacto. La familia mide 543 × 543 × 140 mm. Suelo entre cotas 1,50 y 2,00 m; las piezas parten de 1,50 m y alcanzan 1,64 m, por lo que quedan ocultas bajo la cara superior en una vista sólida desde arriba.

Diagnóstico: no se detecta descentramiento de la matriz completa analizada. La selección parcial puede aparentarlo; la vista sólida puede ocultar las piezas. Estas son explicaciones posibles de la percepción, no una confirmación de lo que ve el usuario. No se modifica otra vez el algoritmo sin evidencia de un fallo. Diagrama reconstruido: tests/artifacts/cavity-diagnostico-centrado.png. No es una captura de pantalla. La captura directa estaba tapada por otra aplicación; una consulta visual posterior quedó bloqueada por el aviso modal de guardado de Revit.

## Criterio definitivo: mitad del sobrante por eje

- Sustituido el cálculo anterior por columnas/filas máximas, extensión completa y origen en borde mínimo + sobrante/2. Eliminados búsqueda de fases, módulo de fase, recentrado tras recortar celdas y desplazamiento del conjunto después de insertar. La comprobación geométrica solo verifica posiciones y contención.
- Siete pruebas locales pasan. Caso explícito: suelo de 10100 × 8050 mm, piezas de 500 × 500 mm, matriz 20 × 16 = 10000 × 8000 mm, sobrantes 100/50 mm, inicio 50/25 mm respecto al borde, también con coordenadas de suelo distintas de cero. Se comprueba además que la separación se incluye en las dimensiones completas.
- Run a5ab1a2cd964, rolled_back: suelo 3073971 de 10755,188633 × 4912,772428 mm, pieza 543 × 543 mm, matriz 19 × 9 de 10317 × 4887 mm. Sobrantes 438,188633 / 25,772428 mm; inicio 219,094316 / 12,886214 mm. Los márgenes reales de los sólidos coinciden por parejas con esos valores. Prueba de sustitución revertida sin cambios conservados.
- Las notas anteriores sobre recentrar piezas son históricas y quedan sustituidas por este criterio.

## Corrección de centrado y cara inferior

- Segunda comprobación sobre el suelo de la pieza seleccionada: suelo 3073971. Se miden las envolventes de los sólidos insertados en los ejes locales, incluyendo transformaciones anidadas. Se corrige el centro del conjunto en X e Y y se verifica diferencia de márgenes ≤0,1 mm antes de confirmar. Si el contorno no lo permite, se revierte la operación.
- Run 7926925a6108: 171 piezas; márgenes sólidos izquierda/derecha 219,094316 mm, inferior/superior 12,886214 mm; base a 1,50 m. Prueba revertida. La matriz existente ya tenía sus centros de inserción equilibrados por parejas; esta prueba refuerza la comprobación sobre sólidos reales y no acredita un gran desplazamiento de la matriz anterior. Los avisos de coincidencias de la prueba se debían a superponer temporalmente las piezas a la matriz existente; no se conservaron duplicados.

- Retícula centrada por defecto; no se buscan fases de borde para aumentar el número. Recentra las piezas retenidas solo si sus huellas completas siguen contenidas.
- Lectura del perímetro y elevación de la cara inferior. Instancias verticales con base sólida a esa cota + desfase; se compensa la distancia al anfitrión superior en las familias de plano de trabajo.
- Run 0bd8a87a296f, dry_run, rolled_back: 64 piezas, cara inferior a 1,50 m y superior a 2,00 m. Las bases geométricas de las 64 piezas quedaron a 1,50 m. Márgenes izquierda/derecha 122,160893 mm; inferior/superior 176,094747 mm. Área y volumen del suelo verificados sin cambios.
- Cinco pruebas unitarias pasan, incluida regresión de centrado sobre un contorno casi rectangular con aristas ligeramente desviadas.
- Los resultados de la implementación anterior que figuran más abajo se conservan como historial; el cálculo por defecto actual prioriza centrado sobre búsqueda de máximo global.

## Comprobado

- Revit 2025 25.0.2.419, pyRevit 7.0.0.26278, CPython 3.12.3.
- Proyecto activo: Vivienda unifamiliar en Robledo de Chavela, Proyecto.rvt.
- Seis tipos IGLU cargados, sistema WorkPlaneBased. Los parámetros y sólidos confirman 543 × 543 mm; alturas 140–250 mm.
- Carga directa de PresentationFramework y creación de una Window mediante XamlReader en Revit.
- Inserción con la referencia original de GetTopFaces; la propiedad Face.Reference puede ser nula y no se utiliza.
- Desfase editable mediante INSTANCE_FREE_HOST_OFFSET_PARAM.
- Consulta de tres suelos reales: 171, 1292 y 64 piezas para huella de 543 mm (IDs 3073971, 3074264, 3192469). La primera implementación sin vectorizar tardó 230 s para los tres; fue sustituida por cálculo vectorizado.
- Prueba completa posterior con los módulos finales: 64 instancias H14 en suelo 3192469, desfase +100 mm, 64 etiquetas verificadas; posición, orientación, área y volumen comprobados por el módulo de generación. Cálculo + creación ~1,05 s. Run MCP 749b2a8b8212, dry_run, decisión rolled_back. Ninguna pieza de esta prueba se ha conservado.
- Cuatro pruebas unitarias locales: ejemplo rectangular de 320 piezas y márgenes 150/100 mm; contornos cóncavos, diagonales y con hueco; cambios de dimensiones y separación; entradas inválidas/suelo demasiado pequeño. Pasan con NumPy/Shapely reales.
- Sintaxis de los módulos comprobada mediante compile.
- WPF final cargado desde window.xaml y enlace de evento comprobado; filtro ISelectionFilter creado con Python.NET.
- Recarga realizada. La cinta contiene el RibbonButton «Automatización CAVITY», ID CustomCtrl_%CustomCtrl_%ARQ-T%Modelado%Cavity, pestaña ARQ-T visible. Se verificó directamente con AdWindows porque find_all_commands de pyRevit falla en CPython con RuntimeType no invocable.
- Sustitución probada: 64 piezas iniciales, 64 sustitutas y 64 etiquetadas finales. Run a2c8e25b271f, rolled_back. Se corrigió la comprobación de dependencias para excluir la propia instancia, que Revit incluye en GetDependentElements(None). Eje local del suelo probado: 90,002394°.

## Pendiente

- Interacción completa con el diálogo WPF y selección desde Revit por el usuario.
- Prueba en Revit 2026 y familias diferentes de las IGLU presentes.
- Prueba de deshacer manualmente desde Revit y comparación visual de un suelo girado.

Un aviso modal de Revit, «Proyecto no guardado recientemente», bloqueó temporalmente las solicitudes con revit_busy. El usuario lo atendió y las comprobaciones continuaron. No se ha guardado el proyecto automáticamente.

## Límite del encargo

La búsqueda irregular no certifica siempre el máximo global. Se conserva la huella completa y se exploran fases y refinamientos. El contorno real se comprueba mediante covers sobre cada rectángulo de ocupación; no se colocan piezas parciales.
