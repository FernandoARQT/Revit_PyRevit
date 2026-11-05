"""Build global constructive options before any movable route is reserved."""
import time
import copy
from .topology_graph import TopologyGraph
from .portal_graph import attach
from .connectivity_analysis import analyze
from ..settings import for_route,profile


def solve(space,requests,system_profile,configuration,existing,deadline,cancelled=None,target_route_ids=None,exploration_state=None,candidate_bank=None,planner_state=None):
    """Single pure orchestration boundary used by both WPF and MCP."""
    from .multi_route import optimize_multiple_routes
    from .candidate_repository import context_signature
    context=context_signature(space,requests,configuration);state=planner_state or {}
    space.planner_state=state if state.get('context')==context else {}
    reused_attempts=copy.deepcopy(space.planner_state.get('budget',{}).get('attempts',{}))
    if state and not space.planner_state:exploration_state=None;candidate_bank=None
    space.iris_cells=dict(space.planner_state.get('iris_cells',{}))
    started=time.monotonic();preparation=None
    preparation_reused=False
    if configuration['coordination'].get('topology_enabled',True):
        cached=space.planner_state.get('preparation')
        if cached and cached.get('complete'):
            preparation_reused=True
            preparation=copy.deepcopy(cached['report'])
            for r in requests:
                prior=cached['requests'].get(r['route_id'],{})
                for key in ('port_admission','topological_corridors'):r[key]=copy.deepcopy(prior.get(key,{} if key=='topological_corridors' else []))
        else:preparation=prepare(space,sorted(requests,key=lambda r:r['route_id']),configuration,
            min(deadline,started+max(0,deadline-started)*.15),cancelled)
        preparation_elapsed=time.monotonic()-started
        from .corridor_assignment import assign
        protected=set(configuration['constraints']['locked_routes'])
        if target_route_ids is not None:protected.update(r['route_id'] for r in requests if r['route_id'] not in target_route_ids)
        # Locked/preserved routes retain their source exactly. Their actual
        # reserved geometry remains authoritative in the joint selector.
        movable=[r for r in requests if r['route_id'] not in protected]
        preparation['assignment']=assign(space,movable,configuration,
            min(deadline,time.monotonic()+min(8,max(0,deadline-time.monotonic())*.04)),cancelled)
        preparation['current_preparation_seconds']=preparation_elapsed
        preparation['preparation_reused']=preparation_reused
    result=optimize_multiple_routes(space,requests,system_profile,configuration,existing,cancelled,
        target_route_ids=target_route_ids,absolute_deadline=deadline,exploration_state=exploration_state,candidate_bank=candidate_bank)
    result['global_plan']=preparation
    result['continuation']={'context_matches':bool(state) and state.get('context')==context,
        'reason':'NO_CHECKPOINT' if not state else 'CONTEXT_CHANGED' if not space.planner_state else 'MATCHED',
        'preparation_reused':preparation_reused,
        'repository_supplied':bool(candidate_bank),'exploration_supplied':bool(exploration_state),
        'route_history_reused':len(space.planner_state.get('route_history',{})),
        'attempts_reused':reused_attempts,
        'corridor_assignment':'REBUILT','native_geometry_cache':'PROCESS_LOCAL_NOT_SERIALIZED'}
    result.setdefault('planner_state',{})['iris_cells']=space.iris_cells
    result.setdefault('planner_state',{})['preparation']={'complete':bool(preparation) and all(
        p.get('status')!='PENDING_BUDGET' and p.get('connectivity',{}).get('graph_construction_completed',True)
        for d in preparation['routes'].values() for p in d['ports']) and all(p.get('conditioned_connectivity',{}).get('graph_construction_completed',True) for d in preparation['routes'].values() for p in d['ports']),
        'report':preparation,'requests':{r['route_id']:{k:r.get(k) for k in ('port_admission','topological_corridors')} for r in requests}}
    return result


def prepare(space,requests,configuration,deadline,cancelled=None):
    from .multi_route import compact_anchor,_stub
    from .connector_docking import analyze as docking_analysis
    started=time.monotonic();graphs={};diagnostics={}
    for request in requests:
        rid=request['route_id'];local=for_route(configuration,rid);p=profile(local)
        ports={q['id']:q for q in [request['source']]+request.get('source_alternatives',[]) if q}
        records=[];corridors={}
        for port in ports.values():
            record={'port_id':port['id'],'status':'PENDING_BUDGET'};records.append(record)
            if time.monotonic()>=deadline or (cancelled and cancelled()):continue
            record['docking']=docking_analysis(space,port,p,local)
            anchors=[compact_anchor(space,q,p,local) if request.get('compact_connections') else _stub(q,local['connection_straight_mm'])
                     for q in (port,request['destination'])]
            if not all(anchors) or not all(space.free(a[1],a[1],p.radius_mm) for a in anchors):
                record['status']='BULK_ANCHOR_BLOCKED_FITTING_ALTERNATIVES_PENDING';continue
            start,goal=anchors[0][1],anchors[1][1];key=(p.radius_mm,p.bend_radius_mm)
            if key not in graphs:
                graph=TopologyGraph(space,p.radius_mm,deadline,cancelled)
                attach(graph,start,goal,p,configuration,deadline=deadline,cancelled=cancelled)
                graphs[key]=graph
            result=analyze(graphs[key],start,goal,configuration['coordination'].get('topology_paths',8),deadline,cancelled)
            record.update(status=result['status'],connectivity=result)
            corridors[port['id']]=result['corridors']
            if configuration.get('structural_crossings')=='PROPOSAL_ONLY' and time.monotonic()<deadline:
                bkey=(p.radius_mm,p.bend_radius_mm,'B')
                if bkey not in graphs:
                    graph=TopologyGraph(space,p.radius_mm,deadline,cancelled)
                    attach(graph,start,goal,p,configuration,scenario='B',deadline=deadline,cancelled=cancelled)
                    graphs[bkey]=graph
                conditional=analyze(graphs[bkey],start,goal,configuration['coordination'].get('topology_paths',8),deadline,cancelled)
                record['conditioned_connectivity']=conditional
                corridors[port['id']]=result['corridors']+[dict(c,scenario='B') for c in conditional['corridors']]
        request['port_admission']=records;request['topological_corridors']=corridors
        diagnostics[rid]={'ports':records,'all_ports_accounted':len(records)==len(ports),
            'destination_docking':docking_analysis(space,request['destination'],p,local),
            'corridor_count':sum(len(v) for v in corridors.values())}
    return {'routes':diagnostics,'seconds':time.monotonic()-started,'profile_graphs':len(graphs),
            'scope':'Constructive candidates only; no automatic impossibility or geometric certification'}
