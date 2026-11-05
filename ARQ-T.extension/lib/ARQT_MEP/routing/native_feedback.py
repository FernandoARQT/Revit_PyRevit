"""Bounded read-only design/validation feedback; no model transactions."""
import copy
import time
from . import service,settings


def design(snapshot,catalog,origins,destinations,configuration,existing,validator,cancelled=None,route_ids=None):
    started=time.monotonic();total=configuration['search']['max_total_seconds'];deadline=started+total
    local=copy.deepcopy(configuration);local['search']['max_total_seconds']=max(.1,total*.7)
    local['constraints'].pop('rejected_paths',None)
    proposal=service.create_mep_route_proposal(snapshot,catalog,origins,destinations,local,existing,cancelled,route_ids)
    validation_started=time.monotonic();result=validator(proposal);validation_seconds=time.monotonic()-validation_started;events=[]
    def score(proposal):
        summary=proposal['summary']
        return (summary.get('unresolved',len([r for r in proposal['routes'] if r.get('native_status') not in ('VALID','VALID_WITH_WARNINGS','CONDITIONAL')])),
                summary.get('conditional',0),summary.get('cost',0))
    for iteration in range(2):
        if time.monotonic()>=deadline or (cancelled and cancelled()):break
        failed=[r for r in result['routes'] if len(r.get('points_mm',[]))>=2
            and r.get('native_status') not in ('VALID','VALID_WITH_WARNINGS')
            and r.get('native_checks',{}).get('errors')]
        unresolved=[r for r in result['routes'] if r.get('collision_status',r.get('status')) in ('NOT_EVALUATED','INVALID')]
        candidates={r['route_id']:r for r in failed+unresolved}
        retry_routes=[r for r in candidates.values() if r['route_id'] not in configuration['constraints']['locked_routes']
                and (route_ids is None or r['route_id'] in route_ids)]
        if not retry_routes:break
        allowance=deadline-time.monotonic()-max(1,validation_seconds*1.2)
        if allowance<1:break
        retry=copy.deepcopy(result['configuration']);retry['search']['max_total_seconds']=allowance
        banned=retry['constraints'].setdefault('rejected_paths',{})
        for route in failed:banned.setdefault(route['route_id'],[]).append(route.get('routing_points_mm',route['points_mm']))
        ids=[r['route_id'] for r in retry_routes]
        candidate=service.create_mep_route_proposal(snapshot,catalog,origins,destinations,retry,result,cancelled,ids)
        validation_started=time.monotonic();candidate=validator(candidate);validation_seconds=time.monotonic()-validation_started
        before=score(result);after=score(candidate)
        events.append({'iteration':iteration,'route_ids':ids,'accepted':after<before,
                       'scope':'Resume unresolved exploration and exclude native-unverified candidates; no collision inferred from a kernel failure',
                       'search_allowance_seconds':allowance,'validation_seconds':validation_seconds})
        if after<before:result=candidate
        else:break
    result['configuration']['search']['max_total_seconds']=total
    result['stats']['native_feedback']=events;result['stats']['design_validation_seconds']=time.monotonic()-started
    return service.seal(result)
