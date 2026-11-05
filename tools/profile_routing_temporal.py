"""Offline profiling of a captured read-only routing scene; no Revit imports."""
import sys,json,time,cProfile,pstats
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'ARQ-T.extension/lib'))
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core.multi_route import plan_route
from ARQT_MEP.routing.settings import profile,for_route,validate
snapshot=json.loads((root/'tests/artifacts/routing-spatial-diagnostic.json').read_text(encoding='utf8'))
proposal=json.loads((root/'tests/artifacts/routing-budget-probe-v3.json').read_text(encoding='utf8'))
config=validate(proposal['configuration'])
request=proposal['requests'][0]
space=build_spatial_model(snapshot,config['constraints'])
if '--network' in sys.argv:
    from ARQT_MEP.routing import service
    catalog=json.loads((root/'tests/artifacts/routing-current-catalog.json').read_text(encoding='utf8'))
    snapshot=json.loads((root/'tests/artifacts/routing-current-snapshot.json').read_text(encoding='utf8'))
    seconds=next((float(arg.split('=',1)[1]) for arg in sys.argv if arg.startswith('--seconds=')),60)
    config['search'].update(max_total_seconds=seconds,max_seconds=20,max_iterations=3)
    result=service.create_mep_route_proposal(snapshot,catalog,proposal['origins'],proposal['destinations'],config)
    (root/'tests/artifacts/routing-network-coordination.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf8')
    print(json.dumps({'summary':result['summary'],'seconds':result['stats']['optimization_seconds'],
        'phases':[{'iteration':phase['iteration'],'unresolved':phase['unresolved'],
            'candidates':(phase.get('portfolio') or {}).get('candidate_count'),
            'port_changes':(phase.get('portfolio') or {}).get('changed_ports'),
            'assignment_nodes':(phase.get('portfolio') or {}).get('assignment_nodes')} for phase in result['stats']['optimization']],
        'routes':[{'destination':r['destination']['element_id'],'source':r['source']['connector_id'],
            'method':r.get('search_method'),'status':r['status'],'reason':r.get('reason')} for r in result['routes']]},ensure_ascii=False),flush=True)
    sys.exit(0)
if '--selection' in sys.argv:
    from ARQT_MEP.routing.core.multi_route import optimize_multiple_routes
    existing={r['route_id']:r for r in proposal['routes']}
    selected=proposal['requests'][0]['route_id']
    config['search'].update(max_total_seconds=2,max_seconds=1,max_iterations=1)
    config['constraints']['locked_routes']=list(existing)
    result=optimize_multiple_routes(space,proposal['requests'],profile(config),config,existing,target_route_ids=[selected])
    print(json.dumps({'returned_routes':len(result['routes']),'ignored_empty_locks':len(result['ignored_locks']),
                      'selected_status':result['routes'][selected]['status'],
                      'preserved_valid':sum(r['status']=='VALID_WITH_WARNINGS' for k,r in result['routes'].items() if k!=selected)},ensure_ascii=False))
    sys.exit(0)
if '--all' in sys.argv or '--cooperative' in sys.argv:
    from ARQT_MEP.routing.core.passages import generate_passages
    config['constraints']['passages']=generate_passages(space,proposal['requests'],config)
    space.constraints=config['constraints']
if '--cooperative' in sys.argv:
    import copy
    from ARQT_MEP.routing.core.multi_route import optimize_multiple_routes
    from ARQT_MEP.routing.settings import validate
    for enabled in (False,True):
        local=validate(copy.deepcopy(config))
        local['search'].update(max_total_seconds=15,max_seconds=5,max_iterations=1)
        local['coordination']['enabled']=enabled
        computed=optimize_multiple_routes(space,proposal['requests'],profile(local),local)
        print(json.dumps({'coordination_enabled':enabled,'seconds':computed['seconds'],
            'usable':sum(r['status'] in ('VALID','VALID_WITH_WARNINGS','CONDITIONAL') for r in computed['routes'].values()),
            'exchanges':computed['coordination'],'routes':[{'id':rid,'status':r['status'],'reason':r.get('reason')}
                for rid,r in computed['routes'].items()]},ensure_ascii=False),flush=True)
    sys.exit(0)
if '--inspect' in sys.argv:
    from ARQT_MEP.routing.core.multi_route import compact_anchor
    for req in proposal['requests']:
        p=profile(for_route(config,req['route_id']))
        anchors=[compact_anchor(space,port,p,config) for port in (req['source'],req['destination'])]
        ids=space.collisions(anchors[0][1],anchors[1][1],p.radius_mm) if all(anchors) else []
        print(json.dumps({'destination':req['destination']['element_id'],'anchors':[a[1] if a else None for a in anchors],
            'direct_collisions':[{'id':o.data.get('element_id'),'name':o.data.get('name'),'classification':o.classification,
                 'portals':sum(o.id in v['element_ids'] for v in config['constraints']['passages'])}
                for o in space.obstacles if o.id in ids]},ensure_ascii=False))
    sys.exit(0)
if '--all' in sys.argv:
    from ARQT_MEP.routing.core.multi_route import optimize_multiple_routes
    from ARQT_MEP.routing import service
    config['search'].update(max_total_seconds=60,max_seconds=20,max_iterations=1)
    computed=optimize_multiple_routes(space,proposal['requests'],profile(config),config)
    proposal['configuration']=config
    for route in proposal['routes']:
        core=computed['routes'].get(route['route_id'],{'status':'NOT_EVALUATED','reason':'TOTAL_TIME_LIMIT'})
        for key in ('reason','attempts','native_checks','primitives','sampled_points_mm','points_mm','routing_points_mm'):
            route.pop(key,None)
        route.update(core);route.update(collision_status=core['status'],native_status='NOT_EVALUATED',approval_status='DRAFT')
    proposal['stats']['optimization_seconds']=computed['seconds']
    proposal['summary']=service.summarize(proposal['routes'])
    proposal=service.seal(proposal)
    (root/'tests/artifacts/routing-budget-probe-final.json').write_text(json.dumps(proposal,ensure_ascii=False),encoding='utf8')
    print(json.dumps({'seconds':computed['seconds'],'summary':proposal['summary'],'routes':[
        {'destination':r['destination']['element_id'],'status':r['status'],'reason':r.get('reason'),'expanded':r.get('expanded')}
        for r in proposal['routes']]},ensure_ascii=False,indent=2))
    sys.exit(0)
profiler=cProfile.Profile();profiler.enable()
route=plan_route(space,request,profile(for_route(config,request['route_id'])),config,deadline=time.monotonic()+3)
profiler.disable()
print(route.get('attempts',route.get('status')))
pstats.Stats(profiler).sort_stats('tottime').print_stats(16)
