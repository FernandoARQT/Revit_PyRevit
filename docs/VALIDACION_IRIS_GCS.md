# Validación IRIS/GCS — 03/11/2026

126 casos ejecutados: 125 superaron la primera ejecución; el restante falló porque su malla de prueba omitía `faces`. Corregido únicamente ese dato de ensayo, se repitieron los 11 casos IRIS/GCS y todos pasaron. No se repitió la batería completa después de esa corrección del fixture.

Ensayo de la geometría capturada: nueve rutas, presupuesto 120 s, tiempo de optimización 116,953 s, tres pasadas y hasta 12 candidatos comparados. Resultado: cuatro recorridos centrales válidos y cinco pendientes. No mejora el número de rutas de la referencia anterior.

Consulta nativa MCP `538d817f5dd0`: revisión del proyecto coincidente; los cuatro recorridos superan los sólidos nativos sin errores. Destinos: 3366666, 3229184, 3366664 y 3230469. Las transiciones y extremos siguen pendientes de comprobar tras modelar. No se crearon ni modificaron elementos.

Pendientes por compatibilidad de red: terminales 3366660 y 3366670. Pendientes por búsqueda acotada sin solución certificada: 3366672, 3366662 y 3366668. Estos estados no demuestran imposibilidad geométrica.

La integración supera los casos independientes, pero aún no acredita la mejora sustancial buscada en este proyecto. Se requiere diagnóstico y una siguiente fase de cambios de código antes de dar por logrado ese objetivo.
