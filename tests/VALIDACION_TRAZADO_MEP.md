# Validación de Trazado automático MEP

## Topología por equipos y decisión sobre red anterior · 02/11/2026

Se introduce `systems/heat_recovery_topology.py` y selección de distribuidores completos. La lista HRV solo contiene recuperadores; las filas de distribuidores contienen ID/tipo, red, capacidad, tomas libres y diámetros. El usuario ya no marca orígenes por toma. MCP admite equipment_assignments, planificación de topología, persistencia lógica de roles y revisión/eliminación explícita de la red anterior. Los algoritmos de `core` no se cambian.

Suite `python -m unittest discover -s tests -p 'test_routing*.py'`: **61 pruebas correctas**, 36,739 s; ocho nuevas comprueban las dos etapas, caudales de principales, separación de redes, tomas únicas, capacidad, preservación de terminales conectados, entrada principal reservada, identificación de incompatibilidades y propuesta desde equipos sin orígenes individuales. Comprobación WPF independiente correcta.

Consulta nativa `6bc7e58c183f`: un recuperador y tres distribuidores de cinco derivaciones, ID 3229184, 3229778 y 3230469. En una asignación lógica de prueba no guardada, un distribuidor SUPPLY y dos EXTRACT produjeron **4 terminales de impulsión y 3 de extracción**; dos principales existentes quedaron conservadas y separadas en auditoría. La asignación de prueba no representa una decisión permanente del proyecto.

Ensayo nativo de interfaz `99fa9fb6e29c`: selección de dos distribuidores completos, sin columna PortId; cálculo desde la ventana sin error de origen HRV como distribuidor. Se generaron solicitudes de siete ramales y una principal nueva (la otra estaba conectada y se conservó). Todos quedaron sin resolver por incompatibilidades de sección, no por ausencia de asignación de origen. Captura `tests/artifacts/routing-equipment-topology.png`. La tabla se refinó después para mostrar el ID al principio y resumir diámetros iguales.

Ensayo reversible MCP `f99c391b47bf`: se guardaron/leyeron las asignaciones de equipos, se rechazó una confirmación inválida y otra obsoleta, y se eliminaron temporalmente los conductos 3230621/3230679 con sus ejes 3230622/3230680. Se verificó que recuperador, tres distribuidores y siete rejillas seguían existiendo. Resultado **rolled_back**: ninguna eliminación ni asignación de ese ensayo quedó conservada. La revisión final excluye todos los equipos aunque estén categorizados como accesorio/unión de conducto.

Discrepancias reales: terminales Ø80, derivaciones Ø90, entradas de distribuidores Ø160, principal configurada Ø125. No se equipara 93 con DN90 ni se crean transiciones supuestas. Se informa de esas incompatibilidades. Si se utilizan varios distribuidores de una red, falta definir el colector o derivación principal intermedio antes de reutilizar una única salida HRV. Las conexiones conservadas se mantienen físicamente, con revisión funcional de su destino; no se presentan como certificadas por el mero hecho de existir.

## Modelado y auditoría HRV · 02/11/2026

Se añaden módulos auxiliares `system_menu.py` y `revit/network_model.py`, sin cambiar los algoritmos del núcleo. El punto de entrada abre un menú cuya única opción implementada es Recuperación de calor. La ventana ofrece Modelar y conectar y Comprobar conexiones; MCP expone `model_heat_recovery_network` (write, explícito) y `audit_heat_recovery_connections` (read).

Ensayo nativo reversible **544059e5efdb**, 40,972 s: dos extremos de ensayo hechos con conductos rígidos temporales, región explícita de ensayo y recorrido calculado/validado por el motor existente. Se creó un flexible Ø125 del tipo existente y se conectaron ambos extremos; la auditoría devolvió dos conexiones correctas. Repetir Modelar no duplicó el conducto. Desconectar el extremo final dio un CONDUCTO_SIN_CONECTAR, conservando el extremo inicial CONECTADO. Conectar el final a otro conducto temporal situado en el mismo punto dio CONEXION_INCORRECTA. Las 26 tomas libres ajenas a esa ruta se mantuvieron como TOMA_SIN_USAR. La identidad de todos los ejemplares antes/después coincidió y el ensayo completo se revirtió.

El ensayo también creó dos segmentos rígidos Ø125 con cambio de dirección. **No pudo insertar el codo con el tipo disponible**: devolvió un error de unión. Por tanto no se acredita una principal completa con codos compatibles en este proyecto. El modelador conserva ese diagnóstico en lugar de declarar conexión correcta. Las tomas de distribuidores de Ø90 y entradas Ø160 siguen requiriendo transiciones verificadas frente a los tamaños de proyecto; una incompatibilidad bloquea la ruta antes de modelar.

Interfaz real **d6e388d969a7**: el menú devolvió ventilation_radial al pulsar Recuperación de calor; los botones Modelar y conectar / Comprobar conexiones estaban cargados; la auditoría desde el botón mostró 26 tomas sin usar y 3 conexiones existentes ajenas a la propuesta. No se modificó el modelo. La comprobación WPF independiente `check_routing_ui.ps1` finalizó correctamente.

Pruebas independientes del trazador: 53 correctas, 36,705 s. Tras actualizar el contrato de capacidades, las 29 pruebas de contratos pasaron en 11,514 s. Se mantiene explícito que modelar no dimensiona hidráulicamente ni certifica la geometría final interpolada del flexible o las dimensiones finales de los accesorios. Este ensayo utiliza extremos ficticios de prueba; no demuestra el circuito completo de la vivienda.

Los primeros ensayos detectaron dos errores de interoperabilidad (constructor List con lista Python y namespace de DataStorage); se corrigieron antes del ensayo satisfactorio. Un intento de simular una conexión errónea contra una toma ya ocupada fue rechazado por Revit; se sustituyó por un extremo independiente coincidente para comprobar la clasificación de conexión incorrecta. Todas estas pruebas se revirtieron.

Fecha: 1 de noviembre de 2026. Revit 2025, build 25.0.2.419; pyRevit 7.0.0.26278+2233; CPython 3.12.3.

## Pruebas independientes

`python -m unittest discover -s tests -p 'test_routing*.py'`

42 pruebas ejecutadas correctamente (35,105 s en la última batería completa). Cubren mallas y distancias 3D, sección circular y rectangular conservadora, rectas, obstáculos, plenums de 200/300 mm, planos inclinados/cambio de altura, Ø125 con holgura, cavidad de 140 mm insuficiente, vigas y pilares, pasos de tabique y condicionados, cobro único por portal, waypoints, simplificación sin atajos inválidos, radios de giro, cinco ramales, cruces a igual/distinta cota, bloqueo, cambio de diámetro, reordenación/rip-up, alternativas por corredores diferentes, cancelación, presupuesto, desconocidos, separación de redes, un puerto por ramal, Flow del catálogo de instancia, conector ausente, propuesta obsoleta, integridad del JSON, determinismo, pasos contra zonas prohibidas, registro técnico de autorizaciones e invalidación de caché por contenido.

Los escenarios independientes son fixtures declarados, no sustituyen geometría Revit ni certifican el circuito del proyecto.

## Interfaz independiente

`powershell -NoProfile -STA -ExecutionPolicy Bypass -File tests/check_routing_ui.ps1`

Ejecutado con CPython/Python.NET de pyRevit en host .NET independiente. Abre WPF, comprueba tablas vacías, calcula un ramal con datos sintéticos expresos, selecciona su fila y muestra propiedades. Imagen revisada: `tests/artifacts/routing-ui.png`; medidas y resultados: `routing-ui.json`.

## Revit y MCP realmente ejecutados

Documento: Proyecto. Sesión MCP 770104. Las pruebas de creación se ejecutaron mediante **run_modify(dry_run=True)** y además dentro de un TransactionGroup revertido.

| Comprobación | Resultado ejecutado |
|---|---|
| Lectura de sólidos anfitriones | 138 obstáculos, 26.334 triángulos |
| Plenums sobre Rooms interiores | 9 regiones después de limitar el espacio permitido al interior |
| Falsos techos inclinados | Caras reales leídas con normales oblicuas y alturas variables |
| Recuperador | ID 3199617; cuatro conectores HVAC reales, clasificación, dirección y ocupación leídas |
| Sólidos transitorios de rectas y codos | Extrusión y CreateSweptGeometry ejecutadas con volúmenes positivos; consulta 0538d47f3785 |
| Propuesta derivada de corredor real | 0,5 m, Ø125 + 15 mm holgura, una recta; sin interferencias nativas |
| Validación real | BooleanOperationsUtils.Intersect contra sólidos originales; un tramo, cero errores |
| Aprobación | Propuesta pasó a APPROVED después de validación |
| ModelCurves y SketchPlane | Un eje creado; segunda vista previa sustituyó al primero sin duplicarlo |
| Limpieza | Curvas y planos propios eliminados |
| Persistencia | DataStorage creado, actualizado con el mismo ElementId y recuperado como APPROVED |
| Flow de rejillas | Siete instancias creadas temporalmente por la herramienta anterior; leídos 10, 15,6451, 9,2627, 8, 9,0921, 8 y 8 L/s; un conector HVAC por instancia |
| Revisión obsoleta | La colocación temporal de terminales cambió la revisión y bloqueó aprobar la propuesta anterior con OUTDATED |
| Reversión | Igualdad del conjunto completo de ElementIds antes/después del TransactionGroup; MCP devolvió rolled_back |

Ensayo principal: run **b0813263d8bb**, 73,93 s con lectura, creación, comprobaciones repetidas y reversión. Esta duración corresponde al ensayo completo, no al tiempo aislado de A*. El código reproducible está en `tools/revit_routing_integration.py`.

Repetido con la versión final: run **a949083b9dbe**, 43,943 s, resultado `rolled_back`. Volvieron a pasar cálculo, comprobación nativa, aprobación, sustitución de vista previa, limpieza, guardado/recuperación sin duplicados, lectura de las siete rejillas temporales y rechazo por revisión obsoleta. Igualdad del conjunto completo de ElementIds tras revertir.

Ventana comprobada dentro de Revit mediante run **29d70f1dbc35**: cuatro filas de conectores reales del recuperador, cero destinos existentes, nueve regiones de plenum y propuesta inicial vacía. Se ejecutó Analizar geometría y se cerró la ventana automáticamente, sin modificar el documento.

El **2 de noviembre de 2026**, el usuario confirmó Revit operativo y el nuevo botón visible. Se recuperó la conexión MCP con la sesión 49252 y el mismo documento Proyecto. Consulta **617efcb95b50**: panel MEP con Distribuir ventilación y Trazado automático MEP, ambos visibles y habilitados, con sus ayudas cargadas; documento sin modificaciones. Consulta **c723a5198465**: exactamente un comando registrado para TrazadoAutomaticoMEP.pushbutton. No fue necesaria otra recarga.

Los orígenes/destinos de ese ensayo son **referencias manuales de prueba derivadas geométricamente del modelo**; no se presentan como distribuidores o rejillas existentes. No queda guardada ninguna propuesta del ensayo.

## Comprobaciones pendientes

- Circuito completo con distribuidores BIM y rejillas definitivas: el proyecto actual no proporciona ese conjunto.
- Geometría de fabricante de accesorios, reductores y codos; validación constructiva de pasos.
- Caso real con vínculos desplazados/rotados: el adaptador implementa transformación, pero el ensayo descrito no demuestra ese escenario.

## Actualización de configuración del recuperador · 02/11/2026

Integración parcial dentro del paquete existente, sin scripts independientes nuevos, familias nuevas ni duplicación de tipos. El CSV semicolon conserva todos los valores originales del JSON y añade identificación constructiva, flexible, distribuidores y conexión rígida principal. Se consume desde `settings`, el extractor, el catálogo MCP y las propuestas; los diagnósticos se muestran en la interfaz. Los algoritmos de `routing/core` no se han modificado.

Pruebas independientes: `python -m unittest discover -s tests -p 'test_routing*.py'`: **49 pruebas correctas**, 36,122 s. Incluyen conservación de todos los parámetros originales, coincidencias parciales con acentos/mayúsculas, cambios de reglas desde configuración, rechazo de tamaños inválidos y claves CSV duplicadas, precedencia corporativa y declaración de capacidades reales.

Consulta integrada MCP **9955a74d1cb1**: lectura del CSV correcto, incluidos «Diámetro» y «FRÄNKISCHE»; identificación de 29 tipos constructivos cargados. Geometría extraída: 138 obstáculos, 26.333 triángulos, 9 regiones; 21 obstáculos STRUCTURE/CONDITIONAL, 32 PASSABLE/PASSABLE y 8 FALSE_CEILING como sólidos ocupados que delimitan el espacio libre. El falso techo conserva su volumen real; no se convierte en espacio transitable su espesor. Recuperador ID **3199617**, cuatro conectores HVAC Ø125 mm; las conexiones interiores de impulsión y extracción están libres. **Cero terminales colocados**: en este estado no es posible comprobar caudales de terminales existentes ni un circuito completo.

Ensayo nativo reversible MCP **c797c503565a**, IronPython, 1,941 s. Se crearon instancias temporales dentro de transacciones revertidas:

| Elemento | Resultado verificado |
|---|---|
| Flex - Redondo, tipo **112209** | El mismo tipo para los tres diámetros, sin duplicarlo |
| Diámetro de instancia | `RBS_CURVE_DIAMETER_PARAM`, editable; valores reales 75, 93 y 125 mm; dos conectores por flexible |
| Distribuidor 5 tomas, tipo **3228110** | 6 conectores HVAC: 5 de Ø90 y 1 de Ø160 mm |
| Distribuidor 10 tomas, tipo **3226668** | 11 conectores HVAC: 10 de Ø90 y 1 de Ø160 mm |
| Orientación local de prueba | Salidas +Z y entrada −X, con colocación sin giro; se debe transformar al orientar cada instancia |
| Reversión | Igualdad de ElementIds de ejemplares antes/después; MCP `no_changes`, 0 añadidos, 0 modificados, 0 eliminados |

Los tipos de distribuidores tienen familias `Fr%c3%a4nkische_profi-air_classic_plus_Distribuidor__5-fold_RFA_2025_LevelBased` y `Fr%c3%a4nkische_profi-air_classic_plus_Distribuidor__10-fold_RFA_2025_LevelBased`; tipos `profi-air classic plus 90° manifold, 5-fold` y `profi-air classic plus 90° manifold, 10-fold`. El catálogo devuelve sus parámetros de longitud sin confundirlos con una envolvente de instalación verificada: BL=325, BH=225 y BW=300/600 mm en los tipos consultados. La envolvente real incluye conectores y espacio de montaje, y requiere geometría de cada instancia.

93 mm **no aparece en el listado circular de tamaños del proyecto**, pero la asignación por API y su lectura posterior sí han funcionado; se conserva como tamaño solicitado y no se sustituye por 90. No se ha cambiado el listado global ni los parámetros de los tipos. La posibilidad de asignarlo no demuestra equivalencia comercial ni compatibilidad física.

Contraste de fabricante: [profi-air classic pipe](https://www.fraenkische.com/en/product/profi-air-classic-pipe-grey-in-coils) presenta NW90 con dimensiones de 90 mm; [manifold plus](https://www.fraenkische.com/en/product/profi-air-classic-manifold-plus?context=profi-air-pipe-system) presenta entrada DN160 y derivaciones NW63–90. Estas fichas no justifican equiparar el diámetro Revit 93 con DN90; `commercial_size_mapping` queda vacío. La principal rígida Ø125 se conserva como requisito, pero necesita una transición compatible hacia Ø160. No se ha creado una conexión ficticia ni alterado el diámetro del distribuidor.

**Pendiente, no implementado:** dimensionado por pérdida de carga y velocidad, distribución hidráulica en conductos paralelos, selección/colocación optimizada de distribuidores, creación de conductos y accesorios y continuidad MEP. El diseñador existente ofrece búsqueda y validación geométrica, sin esas funciones hidráulicas o de escritura de red. Los nuevos parámetros no las crean por sí mismos. Faltan además correspondencia comercial verificada, curvas/datos del producto y accesorios compatibles. No se declara completado el resultado esperado del circuito automático.

Incidencia de sesión: la recarga iniciada mediante MCP el 1 de noviembre quedó sin devolver control (run 864839b0df2b). La conexión y el registro del botón quedaron comprobados el 2 de noviembre, según las consultas indicadas arriba. La incidencia no deja pendiente la verificación de la cinta.

## Archivos

Creados: paquete `lib/ARQT_MEP/routing/` (core, systems, revit, service, MCP, WPF, settings y JSON), bundle `TrazadoAutomaticoMEP.pushbutton` (script, YAML, XAML, SVG y PNG 512 claros/oscuros), `tools/routing_mcp.py`, generador de icono, ensayos nativos e independientes, manual y este registro.

Modificados: `MEP.panel/bundle.yaml` para añadir el botón; `lib/arqt_ribbon_assets.py` para aplicar sus recursos; README para enlazar el manual. No se modifica el código de Distribuir ventilación.
