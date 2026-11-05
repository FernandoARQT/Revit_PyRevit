# ARQ-T | Automatización CAVITY

Botón independiente de Codex, para CPython 3.12 / pyRevit 7 / Revit 2025–2026.
Pestaña ARQ-T → Modelado → Automatización CAVITY. La carpeta raíz del repositorio ya figura en las rutas de extensiones de este equipo.

1. Seleccione un único suelo o pulse Seleccionar suelo en el diálogo.
2. Elija un tipo IGLU. Se muestran familia, tipo y dimensiones X/Y/altura en mm.
3. Introduzca separación y desfase desde la cara inferior. Con 0 mm, la base sólida de la pieza coincide con la cara inferior; un desfase positivo la eleva. Las piezas permanecen verticales, sin voltearse.
4. Calcule y revise el número de piezas, filas/columnas, superficies y márgenes.
5. Genere las instancias. Si hay piezas etiquetadas en el mismo suelo, elija sustituir, añadir o cancelar.

Cancelar o calcular no inicia transacciones. Generar usa una única transacción; un error revierte el conjunto y Revit permite deshacerlo. No corta ni une sólidos, no cambia parámetros del suelo y no crea grupos ni matrices asociativas. Se comprueban área y volumen del anfitrión antes de confirmar; se rechaza una familia que lo altere por corte automático. Las piezas manuales no llevan la marca de Extensible Storage de esta herramienta y no se eliminan. Sustituir elimina todas las matrices etiquetadas de ese suelo, incluidas las adicionales. Se rechaza eliminar piezas con elementos dependientes.

## Geometría y límites

Cada cálculo lee otra vez las caras del sólido actual del suelo seleccionado mediante get_Geometry, sin usar el contorno obtenido al resolver una referencia anfitriona. La referencia de cara se conserva únicamente para la inserción de familias. Antes de generar se comprueba que documento, suelo, cota y contorno siguen correspondiendo a la vista previa; si cambian, se solicita recalcular. Las piezas son independientes: editar el suelo después de generarlas no reajusta automáticamente una matriz ya creada. Para otro tamaño o planta, seleccione el suelo, calcule de nuevo y genere o sustituya su matriz.

Con origen de familia centrado en planta, el primer punto de inserción es borde mínimo + sobrante/2 + dimensión de pieza/2, por separado en X e Y. Los márgenes son distancias hasta el borde de la pieza, no hasta su centro de inserción. No se reutilizan posiciones de un suelo anterior.

Solo suelos horizontales con caras superior e inferior únicas y planas, contornos lineales o circulares. Las splines, elipses y suelos con edición de forma se rechazan expresamente. El eje local sigue la arista recta más larga. Se leen el perímetro y todos los huecos de la cara inferior nativa; la caja envolvente no determina la superficie válida. Para familias de plano de trabajo se conserva el anfitrión superior y se compensa su distancia a la cara inferior con el desfase, manteniendo la orientación vertical correcta. Los arcos se aproximan con flecha ≤0,1 mm y retranqueo conservador de 0,2 mm. No se rellenan huecos.

La huella es la envolvente rectangular de los sólidos visibles con volumen positivo, sin líneas ni vacíos. Se transforman las cajas de cada sólido al sistema local del tipo, excluyendo objetos sin volumen. Si el símbolo inactivo no tiene geometría disponible, se leen Length/Width/Height o sus nombres españoles; antes de generar se activa y se contrasta con la geometría real dentro de la transacción. Una discrepancia mayor que 0,5 mm cancela el conjunto. Se compensa el origen excéntrico en XYZ y se comprueba posición y orientación después de regenerar. No se deducen dimensiones del nombre del tipo. Los tipos IGLU actuales tienen 543 × 543 mm, no 500 × 500 mm.

Se admiten familias WorkPlaneBased sobre la referencia de cara, OneLevelBased y OneLevelBasedHosted sobre suelo. Otros sistemas aparecen como no compatibles. Los tipos sin dimensiones interpretables también se muestran como no compatibles. No se puede identificar semánticamente un sólido auxiliar de una familia arbitraria: si tiene volumen y es visible, amplía la envolvente conservadora. No se reduce la ocupación para ganar piezas.

Se calcula primero el número máximo de columnas y filas por las dimensiones locales del suelo y de la pieza: N = floor((dimensión del suelo + separación) / (dimensión de la pieza + separación)). La dimensión completa de la matriz es N × pieza + (N − 1) × separación. En cada eje, el sobrante es suelo − matriz y el inicio es borde mínimo + sobrante / 2. El primer centro de pieza se obtiene sumando media pieza a esa esquina inicial; el origen propio de la familia se compensa al insertar. Por ejemplo, sobrantes de 100 mm en X y 50 mm en Y producen márgenes de inicio de 50 mm y 25 mm. No hay desplazamientos posteriores ni recentrado de las piezas restantes. En irregulares se excluyen las piezas que invaden el exterior o los huecos sin mover la retícula original, y no se garantiza el máximo global. La vista previa muestra dimensiones del suelo, matriz completa y mitad de cada sobrante. En automático se comparan 0°/90°. Límite de 30.000 celdas candidatas. Cierre y abra el diálogo para cargar la actualización.

## Dependencias y mantenimiento

Antes de confirmar se miden los sólidos insertados en los ejes locales del suelo y se comprueba que sus centros coinciden con las posiciones calculadas desde la mitad de los sobrantes, con tolerancia de 0,1 mm. También se verifica la contención y se muestran los márgenes reales por parejas. Estas comprobaciones no desplazan las piezas.

Shapely 2.x y NumPy, ya disponibles en este equipo. Se busca el entorno del motor y luego Python312 local. No se descargan ni instalan paquetes automáticamente. WPF se carga directamente con XamlReader y Python.NET; no usa pyrevit.forms.WPFWindow.

Archivos: script.py (WPF/coordinación), window.xaml, cavity_grid.py (cálculo puro), cavity_geometry.py (lectura de Revit), cavity_revit.py (transacción y etiquetas), bundle.yaml e iconos. Para desinstalar elimine solo Cavity.pushbutton y su entrada Cavity del layout de Modelado.panel/bundle.yaml, y recargue pyRevit.

Pruebas geométricas: `python -m unittest discover -s tests -p test_cavity.py`. La validación interactiva final requiere abrir el botón, escoger suelo/tipo y revisar el resultado en el proyecto. La compatibilidad de Revit 2026 requiere una sesión de esa versión.
