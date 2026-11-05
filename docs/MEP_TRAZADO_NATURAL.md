# Trazado mediante rectas y curvas tangentes

Resoluciones por defecto: 50 mm general, 25 mm fina y 10 mm de último refinamiento ordinario. Si la búsqueda de 10 mm queda bloqueada a menos de 250 mm del destino, se permite un rescate a 5 mm dentro de una caja nominal de 500 mm (redondeada a la malla), con un máximo de 2 segundos y 10.000 expansiones, sin superar el plazo global restante. Conserva el prefijo encontrado y exige validar el recorrido completo, sus uniones y radios después. No explora toda la vivienda a 5 mm ni garantiza resolver un bloqueo distante. Los antiguos ajustes personales con el valor general por defecto de 100 mm se actualizan al cargar la herramienta; las propuestas ya calculadas conservan su configuración hasta un nuevo cálculo.

La separación libre entre dos conductos es de 15 mm con la configuración habitual, igual que frente a obstáculos y a la cara inferior del falso techo. El radio usado contra obstáculos incluye el radio exterior, el aislamiento aplicable y la holgura completa. Las reservas entre rutas conservan solo el radio físico del conducto reservado: la consulta del siguiente recorrido aporta la holgura una sola vez. La coordinación global y la inspección nativa tampoco suman dos veces esa holgura.

Se comprueban los tramos continuos entre todos los puntos de búsqueda, no solo sus vértices. Cada atajo de simplificación y cada curva resultante deben superar nuevamente la comprobación con el radio completo. Los puntos densos de comprobación no se convierten en controles de construcción del flexible; la geometría nativa final se inspecciona antes de conservarla.

La búsqueda puede utilizar celdas y muestreos densos, pero esos puntos no se entregan al flexible como controles de construcción.

- Los puntos duplicados y los vértices intermedios colineales se eliminan en cualquier dirección 3D.
- Los escalonamientos pueden sustituirse por diagonales libres. El atajo debe respetar los puntos protegidos y superar la comprobación de holguras y del radio de los giros resultantes.
- Cada recta de construcción tiene únicamente inicio y final.
- Cada curva conserva inicio y final de tangencia, centro, eje, radio y tangentes. La distancia de recorte se calcula con `R · tan(ángulo/2)`; se rechazan giros que no caben entre tramos.
- Para interpolar el flexible completo, Revit recibe los extremos de las rectas, las tangencias y un apoyo analítico intermedio por arco. Un arco geométrico no se confunde con una spline Revit de dos puntos sin información de curvatura.
- El muestreo de arcos a intervalos pequeños permanece exclusivamente como geometría técnica de comprobación. Se ha eliminado la densificación de controles cada 25 mm y el envío del muestreo de colisiones como controles nativos.
- Tras colocar transiciones, se recalculan las tangencias con los extremos reales del flexible. La previsualización usa rectas y arcos analíticos.
- La simplificación no autoriza colisiones ni acredita por sí sola que los ramales pendientes estén resueltos. El cuerpo nativo del flexible continúa sujeto a inspección completa.

Implementación: `routing/core/natural_path.py`, `route_geometry.py`, `multi_route.py`, `routing/revit/network_model.py` y `proposals.py`.

Pruebas: `tests/test_routing_natural_path.py`. Se comprueban rectas largas con cientos de puntos, diagonales 3D, tangencias de un giro de 90°, radios insuficientes, inversión de sentido y conservación de anclas ante ruido numérico.

Ensayos Revit revertidos: `14067b4acbca` (ramal recto, dos controles) y `def5e0d2837c` (dos principales curvas, cinco controles cada una, conexiones y geometría nativa verificadas). El ensayo de aproximación de rejilla girada no obtuvo una ruta en su presupuesto breve; no se presenta como validado.
