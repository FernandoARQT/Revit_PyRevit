# Validación · Despiece losa alveolar · 25/10/2026

## Estado de las tareas

1. Catálogo y persistencia: implementados. Guardado/lectura con API nativa comprobado en Revit, run 3335aa43dd9a. JSON idéntico al recargar, 19 medidas predeterminadas. Prueba unitaria de cambio de categoría/penalización/activación y detección de configuración corrupta sin sobrescribirla.
2. K-best exacto: implementado previous/used/current. Comparado con enumeración independiente en el ejemplo de 8755 mm y 70 casos aleatorios pequeños. El orden es determinista y se cuenta la activación una sola vez.
3. Geometría y sustitución: implementadas y probadas en Revit 2025 con forjados rectangulares temporales del mismo tipo existente en el proyecto, no con originales del usuario. Tipo 3073787, nivel 2309996, desfase +125 mm.
4. WPF: carga real, DataTable con tres vistas editables, 12 campos, generación desactivada inicialmente, selección de alternativa y dibujo comprobados dentro de Revit. PNG renderizado desde WPF e inspeccionado visualmente.
5. Integración en Modelado: bundle e iconos creados. Recarga fe29790cbfe4 y comprobación a1e9b28b1cdd: pestaña ARQ-T visible y RibbonButton Despiece losa alveolar, ID CustomCtrl_%CustomCtrl_%ARQ-T%Modelado%DespieceLosaAlveolar. Las 19 filas persistidas siguen accesibles después de recargar pyRevit.

## Integración Revit

- Revit 2025 25.0.2.419; pyRevit 7.0.0.26278; CPython 3.12.3.
- Run 864c2237cbcf: ocho placas, mismo tipo/nivel/desfase, original de prueba eliminado después de validar; fallo provocado tras la primera placa conserva original y número total de suelos. Todo revertido por dry_run.
- Run d335fbf169ed: repetición final en N-S y E-O, ocho placas por orientación, sustitución y rollback provocado correctos, desfase conservado de 125 mm. Todo revertido. Los avisos de solape pertenecen a fixtures temporales superpuestos de ambas orientaciones, no a placas vecinas del mismo despiece.
- El proyecto tiene norte real girado 1,08610397935 rad (~62,23°). Las pruebas usan X/Y internos sin aplicar ese giro.
- Protección adicional: se rechazan dependencias externas del original. Se verificó que Revit crea una dependencia automática interna `Element` sin nombre ni categoría para el suelo estructural; se admite junto con Sketch/SketchPlane/ModelLine.
- WPF: runs 0c6999e5779d y 69fe84a6de12. Se dibujaron 18 controles de preview para la solución de ocho placas. No se mostró una ventana modal al usuario para automatizar clics.

## Resultados del ejemplo

W=8755 mm; márgenes por lado 20/25/30 mm. Top 3 con el catálogo completo:

1. 5×1200 + 3×900: puntuación 17.
2. 6×1200 + 900 + 600: puntuación 21, un elemento de nivel 2.
3. 6×1200 + 2×750: puntuación 21, dos elementos de nivel 2.

Todas suman 8700 mm; márgenes 27,5/27,5 mm. El segundo puesto precede al tercero por el desempate de cantidad de nivel 2, no por orden preescrito.

## Rendimiento

Mediciones locales sin trazador: ~0,02 / 0,06 / 0,14 / 0,23 s para 5/10/20/30 m con catálogo completo; ~0,45–0,51 s para 10 m y MCD=1 al añadir 683 mm. Con tracemalloc, picos Python de ~0,56 / 0,72 / 1,80 / 2,90 MB y ~6,54 MB para MCD=1. No incluyen memoria de Revit ni de WPF. Datos exactos en artifacts/alveolar-performance.json.

## Límites de comprobación

Pendiente prueba manual del usuario: seleccionar suelo desde la ventana, editar todas las celdas/categorías y cerrar/abrir; comprobar persistencia después de cerrar y arrancar Revit; pulsar Generar sobre un suelo real y deshacer manualmente. Se comprobó guardado nativo y nueva lectura sin reiniciar Revit. No se certifica montaje ni se hacen cálculos estructurales.
