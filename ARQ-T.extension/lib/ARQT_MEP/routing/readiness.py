"""Explain modeling blockers before any attempt to create Revit elements."""


def status_label(route):
    status=route.get('collision_status',route.get('status'))
    return {'NOT_EVALUATED':'Sin recorrido validado','INVALID':'Bloqueada',
            'CONDITIONAL':'Paso condicionado','VALID':'Validada','VALID_WITH_WARNINGS':'Validada con avisos'}.get(status,status or 'Pendiente')


def blocker(route):
    reason=route.get('reason')
    if reason in ('CONNECTION_SIZE_REQUIRES_UNMODELLED_TRANSITION','MAIN_TRANSITION_REQUIRED'):
        def diameter(port):
            return (port or {}).get('size',{}).get('diameter_mm','?')
        return 'Faltan transiciones compatibles: origen Ø{} mm → conducto Ø{} mm → destino Ø{} mm. El recorrido todavía no se ha evaluado geométricamente.'.format(
            diameter(route.get('source')),route.get('section',{}).get('nominal_diameter_mm','?'),diameter(route.get('destination')))
    reasons={
        'TOTAL_TIME_LIMIT':'Se agotó el tiempo de cálculo; esta ruta no llegó a evaluarse. No significa que exista una colisión.',
        'NO_ROUTE_GEOMETRY':'Esta ruta aún no tiene un recorrido calculado. Recalcule la selección para buscarlo.',
        'PRESERVED_ROUTE_OUTDATED':'Los conectores de esta ruta han cambiado; recalcúlela antes de aprobarla o modelarla.',
        'DIAMETER_NOT_AVAILABLE':'El diámetro seleccionado no está habilitado en los tamaños circulares de Revit.',
        'NO_FREE_CONNECTION_ANCHOR':'No se ha encontrado un arranque libre próximo al conector dentro del espacio permitido.',
        'NETWORK_CONFLICT_NOT_RESOLVED':'Los extremos tienen espacio de arranque, pero no se ha encontrado una combinación compatible con los otros recorridos. No demuestra que la conexión sea imposible.',
        'NO_COMPATIBLE_ROUTE_COMBINATION':'No se ha encontrado una combinación compatible de recorridos y tomas dentro de los límites de cálculo.',
        'CURVE_BELOW_NATIVE_TOLERANCE':'El recorrido contiene un tramo o giro demasiado pequeño para crearlo de forma fiable en Revit.',
        'TRANSITION_TYPE_NOT_CONFIGURED':'Asigne un tipo de transición a Flex - Redondo y recalcule.',
        'ROUND_CONNECTORS_REQUIRED':'La conexión automática requiere tomas circulares en ambos extremos.',
        'MISSING_DISTRIBUTOR_PORT':'No hay una toma libre de distribuidor para este terminal.',
        'MAIN_SPLITTER_REQUIRED':'Se necesita una derivación principal para alimentar varios distribuidores desde una única toma del recuperador.',
        'HRV_PORT_ALREADY_CONNECTED_KEEP_EXISTING':'La toma del recuperador ya está conectada y se ha elegido conservar la conexión previa.',
    }
    if reason in reasons:return reasons[reason]
    if reason=='NO_CERTIFIED_ROUTE_IN_SEARCH_LIMITS':
        attempts=route.get('attempts',[])
        if any(a.get('reason') in ('SEARCH_LIMIT','TOTAL_TIME_LIMIT','IRIS_GCS_TIME_LIMIT','IRIS_PREPARATION_TIME_LIMIT') for a in attempts):
            return 'La ruta se ha intentado, pero la búsqueda agotó sus límites antes de obtener un recorrido verificable. No acredita una colisión. Detalles por resolución disponibles en attempts.'
    if route.get('native_status') not in ('VALID','VALID_WITH_WARNINGS'):
        return 'Validación geométrica pendiente o no superada'+(': '+str(reason) if reason else '.')
    if route.get('approval_status')!='APPROVED':return 'La ruta está validada pero todavía no se ha aprobado.'
    return None


def preflight(proposal):
    ready=[];blocked=[]
    for route in proposal['routes']:
        message=blocker(route)
        if message:blocked.append({'route_id':route['route_id'],'reason':message})
        else:ready.append(route['route_id'])
    return {'ready_route_ids':ready,'failures':blocked}
