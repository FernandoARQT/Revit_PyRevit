# ARQ-T · Suelos automáticos

**Trazado MEP:** nuevo botón independiente `ARQ-T → MEP → Trazado automático MEP`. Planifica y valida propuestas radiales 3D, compara alternativas y guarda recorridos sin crear conductos ni perforaciones. [Uso, arquitectura y MCP](docs/TRAZADO_AUTOMATICO_MEP.md) · [Pruebas y límites comprobados](tests/VALIDACION_TRAZADO_MEP.md).

**Ventilación:** `ARQ-T → MEP → Distribuir ventilación`. Calcula mínimos por uso de DB-HS 3 con principal automático revisable, equilibra por superficie y excluye porches/terrazas. El editor permite arrastrar, añadir/eliminar rejillas, editar caudales, delimitar cocina, bloquear, deshacer y reoptimizar. Comparte una propuesta persistente con MCP y actualiza las rejillas sobre techos horizontales/inclinados tras confirmación. [Uso y configuración](docs/DISTRIBUIR_VENTILACION.md) · [Pruebas reales y límites](tests/VALIDACION_VENTILACION.md).

También disponible: **ARQ-T → Modelado → Despiece losa alveolar**. Compara el top 3 de combinaciones mediante programación dinámica exacta y sustituye forjados rectangulares por placas nativas del mismo tipo, nivel y desfase. [Uso y límites](ARQ-T.extension/ARQ-T.tab/STR.panel/DespieceLosaAlveolar.pushbutton/README.md) · [Validación](tests/VALIDACION_ALVEOLAR.md).

Botón para **Revit 2025 + pyRevit + CPython**. Crea suelos arquitectónicos nativos y editables con tipos existentes. No modifica suelos anteriores ni elementos delimitadores.

**Ubicación:** `ARQ-T → Modelado → Suelos automáticos`.

## Instalación y uso

1. Mantén `ARQ-T.extension` en esta carpeta. Esta ruta ya está registrada en las extensiones de la instalación inspeccionada. En otro equipo, añade la carpeta que contiene la extensión en **pyRevit → Settings → Custom Extension Directories**.
2. Ejecuta **pyRevit → Reload**. Si no aparece la pestaña, reinicia Revit.
3. Abre una planta con nivel asociado. La tabla utiliza habitaciones locales delimitadas, con superficie positiva, del **nivel y fase de esa vista**. Las habitaciones inválidas quedan excluidas.
4. Escoge el modo y un tipo existente. Ninguna habitación está seleccionada inicialmente. Marca casillas, busca por nombre/número o selecciona todas; las cabeceras Número/Habitación permiten ordenar. Seleccionar/deseleccionar todas afecta al conjunto completo, incluso con un filtro activo.
5. Elige cualquier nivel de creación e introduce un desfase positivo, negativo o decimal en **mm**. La cota visible se actualiza automáticamente y utiliza la misma base de elevación del nivel de Revit. El código comprueba la posición geométrica usando `ProjectElevation`.
6. Pulsa **Generar suelos** y revisa la confirmación: configuración, habitaciones, número de suelos, superficie calculada y posibles solapes con suelos existentes en la misma cota. La opción predeterminada de esa confirmación es **No**.

**Continuo:** une **habitaciones seleccionadas + pasos de puertas interiores** en Shapely/GEOS y crea **un suelo por cada grupo conectado**. Las habitaciones aisladas por muros también se generan, como suelos independientes. Conserva los recortes bajo los tabiques. El criterio confirmado por el usuario sustituye el requisito inicial de cubrir también bajo ellos.

El cálculo parte de las **habitaciones seleccionadas y sus pasos de puerta**. Para cada puerta interior entre dos habitaciones seleccionadas, toma **Anchura del tipo de puerta** como ancho libre en unidades internas y mide la profundidad entre las caras reales del muro; el rectángulo se centra en el punto de inserción de la puerta. No usa el BoundingBox de la familia ni el corte IFC para este paso del continuo. Los **separadores de habitación tienen espesor cero**: la unión de dos contornos contiguos elimina su línea común, sin exigir una puerta. Una habitación no seleccionada al otro lado de un separador permanece excluida.

Se extraen **el anillo exterior y todos los anillos interiores** de la unión para crear el suelo nativo. Los tabiques que llegan al cerramiento producen entrantes del contorno; los grupos de tabiques completamente rodeados producen anillos interiores. Ambos se conservan. La continuidad de la superficie no implica que deban rellenarse esos huecos.

Se ha retirado la lógica que añadía franjas de tabique o rellenaba huecos mediante huellas materiales y límites al centro. Cada paso de puerta debe alcanzar ambos contornos de habitación con anchura positiva. Un paso interior no resuelto detiene la ejecución con el ID, aunque exista otra conexión entre las habitaciones. Las puertas hacia el porche excluido no se incorporan y se comprueba que el resultado no invade habitaciones no seleccionadas.

La unión final se realiza en GEOS, sin booleanas 3D ni ensanchamientos. Se aplica una malla numérica de **0,00003048 mm** para eliminar separaciones de redondeo y pequeños quiebros. Los microhuecos, agujas y fragmentos que colapsan en esa malla se descartan si la superficie modificada queda dentro del presupuesto numérico del perímetro. Véase la [API de precisión de Shapely](https://shapely.readthedocs.io/en/2.1.1/reference/shapely.set_precision.html).

Antes de crear aristas nativas se eliminan pequeños quiebros por debajo de `ShortCurveTolerance`. Se descartan huecos interiores cuya anchura mínima, medida con el rectángulo orientado, es inferior a esa tolerancia, incluidas las rendijas alargadas y triangulares. Se conservan patios y ranuras con anchura representable. Tras simplificar con GEOS, cada arista residual se intenta resolver mediante la intersección de sus apoyos vecinos o eliminando uno de sus extremos. Cada cambio debe conservar la validez y los huecos restantes, limitar el desplazamiento a la tolerancia y superar una comprobación de superficie. La superficie ajustada figura en la confirmación y en el logger. Si no puede repararse, se guarda también el diagnóstico del perfil final para reproducir el fallo. Esta limpieza no conecta habitaciones separadas por un hueco físico.

Se valida y reconstruye un perfil nativo por componente conectado. La confirmación muestra el **número real de suelos**, las habitaciones de cada grupo independiente, la **superficie añadida en pasos de puerta** y el **número y superficie de huecos conservados** del conjunto. Una desconexión real no es un error ni provoca puentes artificiales. Todos los suelos se crean dentro de la misma operación atómica: un fallo revierte el conjunto. Si un paso no puede verificarse o un perfil no puede limpiarse, se guarda un JSON local en `%TEMP%/ARQ-T/SuelosAutomaticos/continuo-*.json` para reproducirlo sin Revit.

**Por habitación:** crea exactamente un suelo por habitación elegida. En cada puerta, los suelos toman **la Anchura completa del tipo y la mitad de la profundidad entre las caras reales del tabique**, encontrándose en el mismo plano medio. Se recorta la mitad opuesta si el límite original ya invadía el hueco. Si solo se selecciona una habitación, su pavimento llega a ese mismo centro. **También las puertas de entrada sin habitación al otro lado llegan al centro del paso**, tanto si la habitación existente está en FromRoom como en ToRoom; la mitad exterior queda sin pavimentar.

## Puertas y geometría

Los contornos proceden de `Room.GetBoundarySegments(Finish)`: se utiliza la huella plana de acabado de cada habitación, no la extracción de una cara 3D con `SpatialElementGeometryCalculator`. El modo continuo utiliza segmentos rectos y operaciones 2D; el modo por habitación conserva las curvas nativas y sus operaciones de sólido. Los arcos no se teselan. No se crean habitaciones temporales ni se cambia la delimitación.

Para medir la profundidad del paso se consultan las caras laterales reales mediante `HostObjectUtils.GetSideFaces`. No se extraen sólidos de muro ni cortes IFC. El punto de encuentro depende de las caras, no de la línea de ubicación del muro.

Las puertas utilizan `FromRoom`/`ToRoom` por fase, complementados con comprobaciones espaciales en ambas caras. En los dos modos, el ancho procede del parámetro de tipo **Anchura**; se presupone que el origen de la familia está centrado en el paso. En modo por habitación se usa un origen fijo de precisión para que ejecuciones independientes compartan la misma línea central. Los perfiles rectos se unen y recortan en GEOS; cuando una habitación contiene arcos, se conservan sus curvas mediante operaciones nativas con los mismos medios pasos rectangulares. No se utiliza la caja de la familia ni la extracción IFC.

Se validan perfiles, huecos, superficie y conectividad. `Floor.Create` utiliza el tipo y nivel elegidos; el desfase se aplica una vez mediante `FLOOR_HEIGHTABOVELEVEL_PARAM`. Se comprueba tipo, nivel, desfase y cota superior. Los errores revierten toda la ejecución; los avisos aparecen al terminar. El logger de pyRevit registra los detalles técnicos.

## Archivos y dependencias

En `ARQ-T.extension/ARQ-T.tab/ARQ.panel/SuelosAutomaticos.pushbutton/`:

El script carga sus dos módulos directamente desde los archivos de este botón en cada ejecución. Esto evita reutilizar módulos antiguos de la sesión CPython tras corregir el código. Las rutas cargadas quedan registradas en el logger.

Tras actualizar los archivos, **cerrar toda la ventana de Suelos automáticos y volver a abrirla desde el botón**. Volver a pulsar Generar en una ventana ya abierta conserva el código anterior. No es necesario reiniciar Revit.

- `script.py`: interfaz Windows Forms y coordinación; encabezado `#! python3`.
- `arqt_geometry.py`: contornos de habitación, pasos de puerta y perfiles exteriores/interiores.
- `arqt_floors.py`: datos del proyecto, posibles duplicados y creación atómica.
- `bundle.yaml`, `icon.png`, `icon.dark.png`, `icon.ico`: botón y recursos. Los PNG tienen 96 × 96 px; el ICO contiene 16/24/32/48/64/128/256 px.

Requiere Revit 2025, pyRevit con CPython/Python.NET y **Shapely y NumPy en ambos modos**. No requiere `RevitAPIIFC`. En este equipo ya están disponibles: **Shapely 2.1.1, NumPy 2.3.2 y GEOS 3.13.1**. Se ha comprobado su carga y los escenarios de planta con CPython 3.12.3 y con Python.NET de pyRevit en un host independiente; véase `tests/artifacts/shapely-result.json`.

El cargador busca primero en el entorno actual, después en `lib` junto al botón y finalmente en `%LOCALAPPDATA%/Programs/Python/Python312/Lib/site-packages` para CPython 3.12. La ruta externa se añade solo durante la importación. **No instala paquetes automáticamente.** En otro equipo, con Python 3.12 x64, instalar junto al botón: `py -3.12 -m pip install --target "<ruta del botón>/lib" shapely==2.1.1 numpy==2.3.2`. [Documentación oficial de Shapely](https://shapely.readthedocs.io/en/2.1.1/).

No requiere WPF, procesos auxiliares ni ensamblados C# adicionales. `tools/build_icons.py` usa Pillow únicamente para reconstruir los iconos.

En este equipo se ha inspeccionado pyRevit **7.0.0.26278+2233**, con motor **CPY3123 / Python 3.12.3**, y Revit **25.0.2.419**. El backend instalado de `pyrevit.forms.WPFWindow` lanza `PyRevitCPythonNotSupported`; por eso se utiliza Windows Forms directamente. Véase también la [documentación del backend CPython de pyRevit](https://docs.pyrevitlabs.io/reference/pyrevit/forms/_cpy/) y las [utilidades geométricas de Revit 2025](https://help.autodesk.com/cloudhelp/2025/ENU/Revit-API/files/Revit_API_Developers_Guide/Revit_Geometric_Elements/Geometry/Revit_API_Revit_API_Developers_Guide_Revit_Geometric_Elements_Geometry_Geometry_Utility_Classes_html.html).

## Pruebas y límites conocidos

Han pasado **64 pruebas con CPython 3.12.3 de pyRevit**, con GEOS real y dobles de la API. La suite verifica medios pasos con centro real desplazado respecto a la línea de ubicación, ausencia de hueco/solape, una sola habitación seleccionada, ejecuciones separadas, límites que ya invaden el hueco, orden FromRoom/ToRoom invertido y puertas exteriores con solo FromRoom o solo ToRoom. También cubre el continuo por grupos, tabiques excluidos, separadores, porche excluido, microaristas, desfases y reversión ante fallos.

Ejecutar: `python -m unittest discover -s tests -p 'test_*.py' -v`. La lectura de geometría real y la creación de suelos siguen pendientes de validación en el modelo del usuario; las pruebas independientes usan geometrías de referencia y dobles en las llamadas a Revit.

Comprobar el backend con el motor incluido: `"C:/Program Files/pyRevit-Master/bin/cengines/CPY3123/python.exe" tests/check_shapely.py`. Para comprobarlo además alojado mediante Python.NET: `powershell -NoProfile -ExecutionPolicy Bypass -File tests/check_winforms.ps1 -TestScript check_shapely.py`. Estas comprobaciones no requieren ni modifican un modelo de Revit.

La interfaz se ha construido y renderizado con **el CPython y Python.NET de pyRevit**, en un host independiente .NET Framework: eventos, selección, filtro, ordenación, modos y cotas. Ejecutar: `powershell -NoProfile -ExecutionPolicy Bypass -File tests/check_winforms.ps1`. Imagen y resultado: `tests/artifacts/`. Esta prueba usa datos ficticios; **no valida el host .NET 8 de Revit ni su geometría**.

**No se ha completado la validación de creación en Revit.** Las pruebas del usuario han permitido localizar errores de integración; las comprobaciones independientes no sustituyen la validación en su modelo. Los casos están en [tests/VALIDACION_REVIT.md](tests/VALIDACION_REVIT.md).

Límites de esta primera versión:

- Vista de planta, habitaciones locales del modelo principal y una fase; no procesa habitaciones/puertas de vínculos ni opciones de diseño. Los contornos locales pueden apoyarse en vínculos, pero las puertas se resuelven solo en anfitriones locales.
- El modo continuo requiere perímetros de segmentos rectos y muros verticales. Si hay arcos, no los aproxima: muestra una advertencia. En modo por habitación se conservan los arcos; una puerta sobre muro curvo, inclinado o con caras no paralelas necesita revisión manual.
- Los espacios bajo tabiques, pilares y cámaras se conservan tal como resultan de la unión de habitaciones y pasos; sus anillos aparecen en el perfil y en el recuento de huecos.
- Los dos modos requieren Anchura de tipo y puertas sobre muros rectos con caras medibles. Un medio paso separado de su habitación, bases diferentes o relaciones espaciales ambiguas detienen la ejecución con el ID.
- Encuentros constructivos complejos, pilares dentro de un tabique y muros con perfiles/variaciones especiales requieren comprobación en Revit; los fallos de la booleana nativa se notifican y no se corrigen modificando el modelo.
- La detección de duplicados compara solapes en planta a la misma cota nominal para caras superiores planas. No es una auditoría general de interferencias ni detecta de forma completa suelos inclinados/deformados.
- El escalado utiliza Windows Forms y el DPI que Revit proporciona; el cuerpo tiene desplazamiento y las acciones permanecen visibles. Falta comprobarlo dentro de Revit con distintos DPI/monitores.
