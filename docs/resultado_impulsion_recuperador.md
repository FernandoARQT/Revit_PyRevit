# Impulsion del recuperador: correccion aplicada

Se corrigio la familia cargada
`fraenkische_profi-air_classic_plus_Distribuidor__5_Impulsion_Suministro`,
instancia 3229184: conector principal Entrante y cinco salidas Salientes.
Se conservaron su clasificacion de union, los seis conectores, geometria,
diametros, posiciones, conexiones y tapones de los cuatro ramales no utilizados.

El recuperador 3199617, conector 1, quedo como equipo base del sistema de
Suministro de aire 3200269. La rejilla 3353026 conserva su demanda de 5 L/s.

Prueba reversible: `7f238e2d46fa`.
Prueba de respuesta automatica de 5 a 6 L/s: `a897a2fbeb26`, revertida.
Aplicacion aprobada y confirmada: `0e228b4dcf6a`.
Verificacion posterior: `a2038906edda`.

El resultado verificado es 5 L/s en todos los tramos del recorrido conectado:
recuperador, transicion 3313709, flexible principal 3313706, distribuidor,
flexible de ramal 3313654, transicion 3353308 y rejilla 3353026.
No se escribio un caudal manual en el equipo ni en el distribuidor.

La extraccion se conservo en 9 L/s. No se realizo equilibrado entre redes.
Los conectores exteriores 2 y 4 del recuperador permanecen desconectados.
La API aun devuelve IsValid=false e IsWellConnected=false para ambos sistemas:
la transmision del caudal esta verificada, pero no acredita validacion integral
ni calculo completo de perdidas de carga.

Se utilizo `tools/reparar_impulsion_recuperador_temporal.py`. No se modificaron
botones. La familia se edito en una copia temporal, se recargo en el proyecto
y se cerro sin guardar el archivo RFA externo. El proyecto tampoco se guardo
desde MCP.
