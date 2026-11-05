# Circuito de extraccion: resultado verificado

Proyecto: `Proyecto`. Recuperador 3199617, rejilla 3228892, distribuidor 3230469.

El recuperador se reasigno al sistema de Aire viciado 3230681 como equipo base.
La clasificacion de sus conectores por si sola no habia corregido esa pertenencia.

Las pruebas independientes descartaron las direcciones y el tipo Multipuerto como
solucion suficiente. Reducir la copia a dos conectores permitio pasar el caudal,
incluso manteniendo Multipuerto. Conservar once conectores y cerrar las nueve tomas
libres mediante remates nativos tambien permitio el paso completo, sin editar la
definicion de familia ni reducir su numero de tomas.

Se aplicaron nueve instancias `M_Remate redondo - DTL`, ajustadas al diametro de
las tomas y conectadas fisicamente. Run confirmado: `a971ffa0267d`.
Las conexiones anteriores quedaron conservadas.

Resultado: 9.136468194531448 L/s en la rejilla, entrada y salida del distribuidor,
y conector 3 del recuperador. El sistema mantiene el mismo caudal total.

Prueba de respuesta nativa: cambiar temporalmente el Flow de la rejilla en +1 L/s
produjo 10.136468194531448 L/s en el recuperador. Run `bfa00fa2487e`, revertido.
No se escribio un caudal manual en el recuperador ni en el distribuidor.

Limitacion pendiente: Revit sigue devolviendo `IsValid=false` e
`IsWellConnected=false` para el sistema. La propagacion del caudal esta verificada;
esto no acredita una validacion completa de la red ni de sus perdidas de carga.

Al utilizar una toma libre en el futuro, hay que retirar su tapon antes de conectar
el nuevo ramal. El script auxiliar utilizado es
`tools/probar_tapones_distribuidor_temporal.py`; no se modificaron botones ARQ-T
ni se guardo el proyecto desde MCP.

## Especializacion posterior autorizada: solo H12

El usuario autorizo expresamente dejar esta familia solo como H12. Se eliminaron
los cinco conectores superiores 3293355 a 3293359 y se fijaron TH12=true y
TV12=false mediante formulas. Quedaron el principal y cinco tomas horizontales,
con posiciones y diametros conservados. Run confirmado: `168a573e33d7`.

La familia especializada ya aparece cargada en Proyecto con seis conectores.
Los cinco tapones de las tomas superiores quedaron desconectados tras esa carga;
la limpieza auxiliar `tools/retirar_tapones_superiores_h12_temporal.py` retira
exclusivamente esos elementos. La prueba `435d3674327c` confirma que se conservan
los cuatro tapones horizontales y 9.136468194531448 L/s en el recuperador.

Esta familia queda especializada en H12; no conserva el cambio funcional a V12.
