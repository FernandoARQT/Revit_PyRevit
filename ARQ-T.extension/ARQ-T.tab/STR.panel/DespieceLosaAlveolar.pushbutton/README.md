# ARQ-T · Despiece losa alveolar

Ubicación: **ARQ-T → Modelado → Despiece losa alveolar**. Revit 2025, pyRevit 7 y CPython. El algoritmo usa solo la biblioteca estándar de Python. La interfaz utiliza WPF/Python.NET disponibles en Revit; no requiere Shapely ni paquetes de optimización.

## Uso

1. Preseleccione un único suelo o pulse Seleccionar forjado.
2. Elija N-S o E-O, referido al norte de proyecto, sin aplicar el giro del norte real ni de la vista. Se actualizan longitud y anchura.
3. Revise los tres catálogos. Las columnas permiten editar anchura, categoría, penalización y activación. Cambiar categoría mueve la fila a su pestaña. Añadir crea una fila editable de 683 mm; si ya existe, cambie su anchura antes de confirmar. Eliminar afecta a la fila seleccionada de la pestaña actual.
4. Configure holguras mínima/nominal/máxima de ambos lados, mínimos de anchura, costes y tolerancias. Las condiciones de borde son notas persistentes; no implican un cálculo estructural de apoyos.
5. Calcule el top 3. Se muestran puntuación, cantidades, desglose, dimensiones mínimas, márgenes, superficie y compatibilidad de tolerancias. El esquema representa las placas a escala relativa, con colores por categoría y flecha longitudinal.
6. Seleccione una alternativa. Invertir disposición invierte el orden transversal sin recalcular la combinación ni cambiar las holguras.
7. **Generar placas sustituye el forjado original**. Ese botón confirma la sustitución; no hay una segunda confirmación. Todas las placas conservan exclusivamente tipo, nivel y desfase. Son suelos estructurales independientes. El espesor procede del tipo original. Se verifica todo antes de eliminar el original; cualquier error revierte la operación. Se puede deshacer en Revit.

No se generan piezas de ancho inventado, juntas adicionales, bandas in situ ni tipos nuevos. Las alternativas cercanas no admisibles se muestran con sobrante/déficit y no se pueden generar.

## Geometría admitida

Un bucle de croquis con cuatro líneas consecutivas, horizontal y rectangular, alineado con los ejes internos X/Y del proyecto. Tolerancia geométrica de alineación y dimensional: 0,01 mm. Los ejes son U=+Y/V=+X para N-S y U=+X/V=+Y para E-O. El perfil procede de `Floor.SketchId → Sketch.Profile`, nunca de su BoundingBox.

Se rechazan huecos, edición de forma habilitada, pendientes, curvas, giros, contornos en L y sólidos cuya superficie no coincide con el croquis (cortes/uniones). También se rechazan dependencias externas, por ejemplo familias anfitrionadas, cotas o etiquetas que podrían eliminarse al sustituir el suelo. No se aproximan contornos incompatibles. El original vuelve a validarse al generar y debe coincidir con la captura de cálculo, incluido VersionGuid.

Las placas se crean con `Floor.Create(..., True, None, 0.0)` y perfil rectangular nativo. Se comprueban croquis, área, cota superior, tipo, nivel y desfase; se conserva el original hasta completar estas verificaciones. Las dependencias automáticas internas del croquis se eliminan junto con él. No se copian otros parámetros de instancia.

## Optimización exacta

Programación dinámica por anchuras, con estados previous/used/current y tres candidatos únicos por suma. Activación cobrada únicamente al usar por primera vez una anchura diferente de 1200 mm. Se aplica la puntuación aditiva y los cinco desempates solicitados. La desviación cuadrática de holguras se añade al ranking final, nunca al podado intermedio. El último desempate usa el vector de cantidades para asegurar determinismo.

Se discretizan las sumas por el MCD del catálogo, conservando la anchura decimal medida del paño. Cada placa debe estar habilitada y superar el mínimo excepcional. El mínimo preferente es una preferencia informativa; no excluye medidas habilitadas entre ambos mínimos ni introduce una penalización oculta adicional.

Las holguras se calculan con la proyección del valor ideal sobre su intervalo factible. Cuando no hay solución se amplía acotadamente el límite hasta tres anchuras máximas más allá del rango admisible, para devolver también tres alternativas cercanas aunque el paño sea menor que una sola placa. Se limita a 500.000 estados discretos para evitar agotar la memoria; el comando informa y no altera el modelo si se supera ese límite.

Las tolerancias ±5/±25 mm son hipótesis preliminares editables. La comprobación evalúa los dos extremos de suma de anchuras frente a las holguras. No modifica la geometría ni bloquea una solución nominal válida. Siempre se indica que el montaje está pendiente de validación dimensional del fabricante y del hueco.

## Persistencia

Sección global `ARQT_DespieceLosaAlveolar_v1`, opción `settings_json`, JSON versión 1. Utiliza `pyrevit.script.get_config` y `script.save_config`. Se guardan catálogo completo, activación, categorías, penalizaciones, mínimos, márgenes, costes, tolerancias, orientación, inversión y notas de borde.

Guarda al terminar la edición de una celda, al confirmar, al perder el foco de los campos y al cerrar. Calcular no se ejecuta en cada pulsación de tecla. Cancelar el despiece conserva las preferencias confirmadas. Los cambios invalidan las alternativas. Si una edición es inválida se muestra el error y no sustituye la configuración guardada; al cerrar se puede descartar esa edición. Restaurar valores requiere confirmación.

Ante JSON corrupto o versión desconocida se pregunta antes de recuperar valores iniciales y se conserva el contenido anterior en `recovery_json`. Los errores de lectura/escritura se muestran; no se reemplazan silenciosamente los valores. Cerrar y abrir una ventana vuelve a leer la configuración global.

## Archivos y pruebas

`script.py` carga los módulos del propio botón desde sus rutas absolutas para evitar reutilizar módulos de otro comando. `ui.py/ui.xaml`: interfaz; `settings.py`: configuración; `optimizer.py/candidates.py`: cálculo puro; `geometry.py`: rectángulos y posiciones sin Revit; `revit_adapter.py`: acceso y transacciones; bundle e iconos.

Pruebas: `python -m unittest discover -s tests -p test_alveolar.py` desde la raíz del repositorio. Evidencias en `tests/VALIDACION_ALVEOLAR.md`, `tests/artifacts/alveolar-performance.json` y `tests/artifacts/alveolar-wpf.png`.

Para desinstalar, elimine solo este directorio y la entrada `DespieceLosaAlveolar` del layout de Modelado.panel/bundle.yaml; recargue pyRevit. La configuración de usuario se conserva.
