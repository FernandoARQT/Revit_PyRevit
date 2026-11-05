"""Evidence-based route report; unmeasured values remain null or empty."""


def build(route,request,global_plan=None,passage_generation_incomplete=False):
    design=((global_plan or {}).get('routes') or {}).get(request['route_id'],{})
    records=design.get('ports',[]);attempts=route.get('attempts',[])
    ports={p['id']:p for p in [request.get('source')]+request.get('source_alternatives',[]) if p}
    reason=route.get('reason','');reasons=[reason]+[a.get('reason','') for a in attempts]
    state=route.get('collision_status',route.get('status','NOT_EVALUATED'))
    if state=='CONDITIONAL':final='CONDITIONAL_STRUCTURAL'
    elif state in ('VALID','VALID_WITH_WARNINGS'):final=state
    elif any('TIME' in r or 'BUDGET' in r or 'SEARCH_LIMIT' in r for r in reasons):final='TIME_BUDGET_EXHAUSTED'
    elif any('NETWORK' in r or 'COMPATIBLE_ROUTE_COMBINATION' in r for r in reasons):final='NETWORK_CONFLICT'
    elif any('CONNECTION_ANCHOR' in r or 'CONNECTOR_' in r for r in reasons):final='NO_COMPATIBLE_DOCKING_FOUND'
    elif passage_generation_incomplete:final='PORTAL_GENERATION_INCOMPLETE'
    elif any('BEND' in r or 'CURVE' in r for r in reasons):final='BEND_CONSTRAINT_FAILED'
    else:final='NO_GEOMETRIC_ROUTE_FOUND_IN_CURRENT_EXPLORATION'
    tested=[p['port_id'] for p in records if p.get('status')!='PENDING_BUDGET']
    connectivities=[p.get('connectivity',{}) for p in records]
    corridors=[c for p in records for c in p.get('connectivity',{}).get('corridors',[])]+[
        c for p in records for c in p.get('conditioned_connectivity',{}).get('corridors',[])]
    methods=[];times={}
    for attempt in attempts:
        method=attempt.get('method') or ('ASTAR_3D' if 'resolution_mm' in attempt else 'UNREPORTED_METHOD')
        if method not in methods:methods.append(method)
        if 'seconds' in attempt:times[method]=times.get(method,0)+attempt['seconds']
    if route.get('search_method') and route['search_method'] not in methods:methods.append(route['search_method'])
    common=set(corridors[0].get('portal_ids',[])) if corridors else set()
    for corridor in corridors[1:]:common.intersection_update(corridor.get('portal_ids',[]))
    errors=route.get('errors',[]);last=errors[-1] if errors else {}
    if not isinstance(last,dict):last={}
    return {'route_id':request['route_id'],'terminal_element_id':request['destination'].get('element_id'),
        'network_type':request.get('network_type'),'selected_distributor':(route.get('chosen_source') or request.get('source') or {}).get('element_id'),
        'compatible_ports':list(ports),'tested_ports':tested,'port_admission':records,
        'admissible_dockings':None,'docking_scope':'Bulk anchors tested; actual fitting verification remains independent',
        'source_region':sorted({i for c in connectivities for i in c.get('source_regions',[])}),
        'target_region':sorted({i for c in connectivities for i in c.get('destination_regions',[])}),
        'reachable_region_count':None,'topological_candidate_count':len(corridors),
        'mandatory_passages_in_current_candidates':sorted(common),'physical_mandatory_passages_proven':False,
        'structural_penetration_candidates':sorted({e['portal_id'] for c in corridors for e in c.get('edges',[])
            if e.get('classification')=='CONDITIONAL' and e.get('portal_id')}),
        'geometric_candidate_count':None,'tested_algorithms':methods,'time_by_algorithm_seconds':times,
        'expanded_nodes':route.get('expanded'),'last_blocking_element':last.get('element_id') or last.get('obstacle_id'),
        'last_blocking_xyz_mm':last.get('point_mm') or last.get('xyz_mm'),'best_partial_path':route.get('best_partial_path'),
        'final_status':final,'reason':reason,'attempts':attempts,
        'passage_generation_incomplete':passage_generation_incomplete,'physical_infeasibility_proven':False,
        'native_status':route.get('native_status','NOT_EVALUATED'),'native_connection_status':'NOT_VERIFIED'}
