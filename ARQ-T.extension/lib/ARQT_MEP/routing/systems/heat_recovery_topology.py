"""Equipment-level HRV → manifold → terminals requests; geometry engine unchanged."""
import copy
import math
from ..connector_labels import HRV_FUNCTIONS
from ..core import geometry as g
from ...family_rules import distributor_network, terminal_matches
from ..sizing import size_requests


def distributor_ports(device):
    ports=[p for p in device['connectors'] if p.get('size',{}).get('shape')=='Round']
    if len(ports)<2:raise ValueError('Distribuidor sin tomas circulares suficientes: '+str(device['element_id']))
    maximum=max(p['size']['diameter_mm'] for p in ports)
    mains=[p for p in ports if abs(p['size']['diameter_mm']-maximum)<.01]
    if len(mains)!=1:raise ValueError('Entrada principal del distribuidor ambigua: '+str(device['element_id']))
    branches=[p for p in ports if p['id']!=mains[0]['id']]
    return mains[0],branches


def selected_equipment(catalog,configuration):
    hrv=[d for d in catalog['devices'] if str(configuration.get('hrv_id')) in (d['id'],str(d['element_id']))]
    if len(hrv)!=1 or 'recuperador' not in hrv[0].get('family','').casefold():raise ValueError('Seleccione el recuperador de referencia.')
    by_id={str(d['element_id']):d for d in catalog['devices']}
    by_id.update({d['id']:d for d in catalog['devices']})
    selected=[];seen=set()
    for assignment in configuration.get('equipment_assignments',[]):
        if not assignment.get('include',True):continue
        device=by_id.get(str(assignment.get('element_id',assignment.get('element_uid'))))
        if not device or not device.get('is_configured_distributor'):raise ValueError('Distribuidor seleccionado ausente o no identificado.')
        network=assignment.get('network_type')
        if network not in ('SUPPLY','EXTRACT'):raise ValueError('Asigne SUPPLY o EXTRACT a cada distribuidor.')
        if distributor_network(device.get('family','')) != network:
            raise ValueError('Familia de distribuidor incompatible con '+network+': '+device.get('family',''))
        if device['id'] in seen:raise ValueError('Un distribuidor no puede pertenecer a dos redes.')
        seen.add(device['id']);selected.append((device,network))
    return hrv[0],selected


def requests(catalog,configuration,destinations):
    hrv,selected=selected_equipment(catalog,configuration)
    roles={'SUPPLY':[],'EXTRACT':[]}
    for p in hrv['connectors']:
        function=HRV_FUNCTIONS.get((p.get('classification'),p.get('flow_direction')))
        if function and function[0] in roles:roles[function[0]].append(p)
    terminals={t['id']:t for t in catalog['terminals']}
    demands={'SUPPLY':[],'EXTRACT':[]};seen=set()
    for value in destinations:
        if not value.get('include',True):continue
        terminal=terminals.get(value.get('terminal_id'))
        if not terminal:raise ValueError('La topología automática requiere terminales Revit reales.')
        if terminal['id'] in seen:raise ValueError('Un terminal solo se cuenta una vez.')
        seen.add(terminal['id'])
        network=value.get('network_type')
        if network not in demands:raise ValueError('Asigne una red a cada terminal.')
        if not terminal_matches(terminal.get('family',''),terminal.get('name',''),network):
            raise ValueError('Familia/tipo de rejilla incompatible con '+network+': '+terminal.get('family','')+' / '+terminal.get('name',''))
        if not terminal.get('network_assignment_requires_review',True) and terminal.get('suggested_network_type')!=network:
            raise ValueError('La función del terminal no coincide con la red seleccionada.')
        ports=[p for p in terminal['connectors'] if p['id']==value.get('port_id')]
        if len(ports)!=1:raise ValueError('Seleccione un conector real e inequívoco del terminal.')
        flow=terminal.get('airflow_lps')
        if flow is None or not math.isfinite(float(flow)) or flow<=0:raise ValueError('Caudal de diseño no verificable para '+terminal['id'])
        demands[network].append((terminal,ports[0],float(flow)))
    result=[];preserved=[];warnings=[];summary={};overrides={};allocations={}
    for network in ('SUPPLY','EXTRACT'):
        devices=[d for d,n in selected if n==network]
        current=demands[network];available=[];used_devices={};assigned_flows={}
        for d in devices:
            main,branches=distributor_ports(d)
            for port in branches:
                if not port['connected']:available.append((d,main,port))
        free_demands=[]
        for terminal,port,flow in current:
            adapter=terminal.get('terminal_adapter')
            # A free second outlet does not imply a second demand when the
            # first outlet already serves this terminal.
            needs_adapter_branch=adapter and not any(p['connected'] and not p.get('connected_to_cap') for p in adapter['outlets'])
            if port['connected'] and not needs_adapter_branch:
                preserved.append({'network_type':network,'kind':'TERMINAL','element_id':terminal['element_id'],'port':port,'airflow_lps':flow})
            else:free_demands.append((terminal,port,flow))
        summary[network]={'terminals':len(current),'new_branches':len(free_demands),'preserved_terminals':len(current)-len(free_demands),
                          'free_distributor_branches':len(available),'selected_distributors':len(devices)}
        if len(free_demands)>len(available):warnings.append('{}: faltan {} tomas de distribuidor para los terminales incluidos.'.format(network,len(free_demands)-len(available)))
        for terminal,target,flow in sorted(free_demands,key=lambda v:v[0]['id']):
            rid='route:'+target['id']
            destination=dict(target,kind='TERMINAL',network_type=network)
            req={'route_id':rid,'network_type':network,'destination':destination,'airflow_lps':flow,
                 'source':None,'route_kind':'BRANCH','room_uid':terminal.get('room_uid'),
                 'waypoints_mm':configuration['constraints']['waypoints'].get(rid,[])}
            if not available:req['error']='MISSING_DISTRIBUTOR_PORT'
            else:
                d,main,port=min(available,key=lambda v:(g.distance(v[2]['xyz_mm'],target['xyz_mm']),v[2]['id']))
                available.remove((d,main,port));used_devices[d['id']]=(d,main)
                assigned_flows[d['id']]=assigned_flows.get(d['id'],0)+flow
                allocations[target['id']]=d['element_id']
                req['source']=dict(port,kind='DISTRIBUTOR',network_type=network)
                adapter=terminal.get('terminal_adapter')
                if adapter:
                    from ..core.terminal_adapter import variants
                    center=[sum(p['xyz_mm'][axis] for p in d['connectors'])/len(d['connectors']) for axis in range(3)]
                    choices=variants(adapter,{'xyz_mm':center},network)
                    if not choices:req['error']='TERMINAL_ADAPTER_OUTLETS_OCCUPIED'
                    elif any(p.get('connected_to_cap') for p in adapter['outlets']):
                        req['error']='TERMINAL_ADAPTER_MANUAL_CAP_REQUIRES_REVIEW'
                    else:
                        req['terminal_port']=dict(target)
                        req['destination']=choices[0];req['destination_alternatives']=choices[1:]
                        target=choices[0]
                elif catalog.get('terminal_adapters_required'):
                    req['error']='MISSING_TERMINAL_ADAPTER'
                # Only interchangeable unconnected branches of this same
                # manifold: changing devices would require resizing its main.
                _,all_branches=distributor_ports(d)
                req['source_alternatives']=[dict(p,kind='DISTRIBUTOR',network_type=network)
                    for p in all_branches if not p['connected'] and (p['size']==port['size'] or
                        catalog.get('configured_model',{}).get('flexible_duct',{}).get('transition_type_id'))]
                section=configuration.get('route_overrides',{}).get(rid,{}).get('section',configuration['section'])
                nominal=section.get('nominal_diameter_mm')
                if any(p.get('size',{}).get('shape')!='Round' or abs(p['size']['diameter_mm']-nominal)>.01 for p in (port,target)):
                    req.setdefault('error','CONNECTION_SIZE_REQUIRES_UNMODELLED_TRANSITION')
            result.append(req)
        if not used_devices:continue
        if len(roles[network])!=1:
            detail='; '.join('toma {}: {} / {}'.format(p.get('connector_id','?'),p.get('classification','?'),p.get('flow_direction','?')) for p in hrv['connectors'])
            raise ValueError('Recuperador {}: se esperaban 1 toma interior {} y se identificaron {}. Conectores leídos: {}'.format(hrv['element_id'],network,len(roles[network]),detail))
        source=roles[network][0]
        if len(used_devices)>1:
            warnings.append(network+': varios distribuidores requieren una derivación/colector principal intermedio; no se reutiliza la misma toma del recuperador.')
        for uid,(d,main) in sorted(used_devices.items()):
            if main['connected']:
                preserved.append({'network_type':network,'kind':'MAIN','element_id':d['element_id'],'port':main})
                warnings.append('Principal existente del distribuidor {} conservada; comprobar que pertenece al recuperador y a la red asignados.'.format(d['element_id']))
                continue
            rid='main:'+source['id']+':'+main['id']
            diameter=configuration['main_connection']['diameter_mm']
            overrides[rid]={'section':{'shape':'CIRCULAR','nominal_diameter_mm':diameter,'outer_diameter_mm':diameter,'insulation_mm':0}}
            request={'route_id':rid,'route_kind':'MAIN','network_type':network,'source':dict(source,kind='HRV',network_type=network),
                     'destination':dict(main,kind='DISTRIBUTOR_MAIN',network_type=network),
                     'airflow_lps':assigned_flows[uid],'waypoints_mm':configuration['constraints']['waypoints'].get(rid,[])}
            if source['connected']:request['error']='HRV_PORT_ALREADY_CONNECTED_KEEP_EXISTING'
            elif len(used_devices)>1:request['error']='MAIN_SPLITTER_REQUIRED'
            elif abs(main['size']['diameter_mm']-diameter)>.01:request['error']='MAIN_TRANSITION_REQUIRED'
            result.append(request)
    size_requests(result,catalog,configuration,overrides)
    return result,warnings,{'counts':summary,'preserved_connections':preserved,'terminal_distributors':allocations},overrides
