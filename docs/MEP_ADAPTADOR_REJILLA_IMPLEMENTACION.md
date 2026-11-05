# Codo de rejilla en el trazado MEP

Implementación: 5 de noviembre de 2026.

Familia exacta: `Fraenkische_profi-air_classic_Codo rejilla`.
Tipo exacto: `profi-air classic outlet 90° plus - 2xNW 90 / DN 125 - short`.

## Funcionamiento

1. **Calcular recorridos** coloca o reutiliza un adaptador conectado físicamente a cada rejilla seleccionada. No desplaza la rejilla ni altera su parámetro de instancia `Flow`. Las conexiones previas con otras piezas se conservan.
2. Orienta la bisectriz de las dos salidas hacia el distribuidor, proyectada en el plano perpendicular al eje de la rejilla. También funciona con ese eje inclinado.
3. Genera las orientaciones 0°, −15°, +15°, −30°, +30°, −45° y +45°. Para cada una ofrece las dos salidas laterales: hasta 14 alternativas por rejilla.
4. La rejilla conserva la identidad y el caudal de diseño. El extremo físico del recorrido es el conector 2 o 3 del codo. El diámetro se calcula por caudal y tamaños habilitados; si difiere del Ø90 de esa salida, se conserva la necesidad de transición real.
5. Cada pose sustituye el obstáculo de la posición anterior del codo por su envolvente girada durante la búsqueda y la validación. Se vacían las cachés geométricas dependientes de esa posición. El selector conjunto tiene disponibles las distintas poses y bocas.
6. **Modelar y conectar** aplica la pose elegida, comprueba el conector regenerado, crea el flexible y tapa la otra salida. Giro, flexible, conexiones y tapa se confirman conjuntamente; un fallo revierte ese ramal.

La geometría real del adaptador se comprueba también mediante sólidos Revit transformados, sin mover el modelo durante esa comprobación. El cuello DN125 de montaje tiene una excepción local de contacto con su propia rejilla; no se elimina la comprobación de cubiertas, otros obstáculos ni del cuerpo lateral del adaptador.

La auditoría añade las uniones rejilla–adaptador y tapa–adaptador a los dos extremos del conducto. Un adaptador con un ramal existente no origina otro ramal por tener libre su segunda salida. Si se ha eliminado el ramal, la preparación puede retirar una tapa propia de ARQ-T para recalcular; no elimina tapas manuales ni redes existentes.

## MCP

Operación de escritura explícita previa al cálculo:

```python
dispatch(doc, 'prepare_terminal_adapters', {
    'configuration': configuration,
    'destinations': destinations,
}, allow_write=True)
```

Los destinos mantienen `terminal_id`, `port_id` de la rejilla, `network_type` e `include`. Después se vuelve a extraer la geometría y el catálogo. Las operaciones de cálculo de solo lectura no colocan piezas implícitamente. Las referencias manuales de diseño siguen funcionando sin adaptadores reales.

## Verificación realizada

- Prueba nativa de preparación de las siete rejillas actuales: siete adaptadores, sin duplicados al repetir.
- 98 comprobaciones de salida y giro: siete rejillas × siete orientaciones × dos bocas. Se mantienen posición, orientación y caudal de las rejillas.
- Modelado nativo de un ramal corto de suministro, y de otro usando la segunda boca con un giro de +15°: flexible Ø90, ambas conexiones, unión inferior y tapa verificadas; sin conflictos geométricos en esos ensayos.
- Modelado nativo de un ramal corto de extracción: conexiones y tapa verificadas, con `ExhaustAir`, sentido `In` en el distribuidor y `Out` en la boca lateral del adaptador.
- Todas las pruebas de modelo se revirtieron. Los distribuidores próximos usados en los ensayos eran copias temporales para aislar la conexión física de la búsqueda de recorridos largos.

La prueba conjunta final con búsqueda paralela y 90 segundos de presupuesto resolvió y validó nativamente tres rutas: las dos principales y un ramal de extracción hasta la salida 3 del adaptador, con giro de −15°. Quedaron seis ramales sin resolver: cinco por límites de búsqueda o coordinación y uno sin ancla libre cerca de la cubierta. Esto no acredita que todos los recorridos de la vivienda estén resueltos. Se mantienen los bloqueos geométricos y los diagnósticos correspondientes.

La prueba conjunta detectó también la conversión de tuplas a listas al intercambiar candidatos entre procesos. Los descriptores de las poses son ahora compatibles con JSON y la autorización compara todos sus valores normalizados, sin limitarse al identificador de conector.

Pruebas locales: `tests/test_routing_terminal_adapter.py`. Ensayo nativo siempre revertido: `tools/revit_terminal_adapter_integration.py` (contiene identificadores del proyecto usados exclusivamente como fixture). Evidencias de conexiones: `tests/artifacts/terminal-adapter-verified.json`; propuesta de cálculo conjunto: `tests/artifacts/routing-adapter-final-test.json`.
