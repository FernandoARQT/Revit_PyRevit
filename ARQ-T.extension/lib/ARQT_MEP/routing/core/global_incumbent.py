"""Atomic retention of fully validated compatible networks."""
import copy
from .cooperative import usable,overlaps
from .route_geometry import validate_route_geometry,rejected_path
from ..settings import for_route,profile


def validate_network(space,requests,configuration,network):
    by_id={r['route_id']:r for r in requests};checked={};issues=[];ports={};radii={}
    for rid,route in sorted(network.items()):
        if not usable(route):continue
        request=by_id.get(rid)
        if request is None:issues.append({'route_id':rid,'reason':'UNKNOWN_ROUTE'});continue
        source=route.get('chosen_source') or route.get('source') or request['source']
        allowed=[request['source']]+request.get('source_alternatives',[])
        from .terminal_adapter import destination,allowed_destination
        target=destination(route,request)
        from .geometry import distance
        if (source not in allowed or rejected_path(route,rid,configuration) or
            distance(route['points_mm'][0],source['xyz_mm'])>.01 or
            not allowed_destination(target,request) or distance(route['points_mm'][-1],target['xyz_mm'])>.01 or
            route.get('network_type',request['network_type'])!=request['network_type']):
            issues.append({'route_id':rid,'reason':'ENDPOINT_NETWORK_OR_REJECTED_PATH'});continue
        local=profile(for_route(configuration,rid));report=validate_route_geometry(space,route,local)
        if report['status'] in ('INVALID','NOT_EVALUATED'):
            issues.append({'route_id':rid,'reason':'INDIVIDUAL_VALIDATION','detail':report});continue
        item=copy.deepcopy(route);item.update(report,chosen_source=copy.deepcopy(source))
        checked[rid]=item;radii[rid]=local.radius_mm
        for port in (source,target):
            key=port.get('physical_id',port['id'])
            if key in ports:issues.append({'route_ids':[ports[key],rid],'reason':'CONNECTOR_EXCLUSIVITY','port_id':key})
            ports[key]=rid
    keys=sorted(checked)
    for rid in configuration['constraints']['locked_routes']:
        if rid in by_id and rid not in checked:issues.append({'route_id':rid,'reason':'USER_LOCKED_ROUTE_MISSING'})
    for i,rid in enumerate(keys):
        for other in keys[i+1:]:
            if overlaps(checked[rid],radii[rid],checked[other],radii[other]):
                issues.append({'route_ids':[rid,other],'reason':'GEOMETRIC_INTERFERENCE'})
    return {'valid':not issues,'routes':checked,'issues':issues,'effective_radii':radii,
        'used_ports':ports,'pair_checks':len(keys)*(len(keys)-1)//2}


class GlobalIncumbent:
    def __init__(self,context_signature):
        self.context=context_signature;self.network={};self.validation={};self.events=[];self.clock=0

    @staticmethod
    def score(network):
        values=[r for r in network.values() if usable(r)]
        return (-len(values),sum(r.get('status')=='CONDITIONAL' for r in values),
            sum(r.get('cost',{}).get('passages',0) for r in values),
            sum(r.get('cost',{}).get('total',0) for r in values))

    def consider(self,candidate_network,validator):
        from .candidate_repository import audit
        for rid,route in candidate_network.items():audit('GLOBAL_COMBINATION_TESTED',rid,route)
        self.clock+=1;report=validator(candidate_network)
        accepted=report['valid'] and (not self.network or self.score(report['routes'])<self.score(self.network))
        self.events.append({'clock':self.clock,'event':'REPLACED' if accepted and self.network else 'NEW' if accepted else 'PRESERVED',
            'issues':report.get('issues',[]),'score':list(self.score(report.get('routes',{})))})
        if accepted:self.network=copy.deepcopy(report['routes']);self.validation=copy.deepcopy(report)
        for rid,route in candidate_network.items():
            if accepted and rid in report['routes']:audit('GLOBAL_COMBINATION_ACCEPTED',rid,route)
            else:audit('SELECTOR_REJECTED',rid,route,reason='GLOBAL_VALIDATION' if not report['valid'] else 'INCUMBENT_NOT_IMPROVED',issues=report.get('issues',[]))
        return accepted

    def best(self):return copy.deepcopy(self.network)

    def invalidate(self,changed_dependencies):
        # A global geometric change cannot safely be narrowed without a proven
        # dependency map. Record conservative invalidation instead of guessing.
        self.events.append({'clock':self.clock,'event':'INVALIDATED','dependencies':changed_dependencies})
        self.network={};self.validation={}

    def export_state(self):
        return {'context':self.context,'network':self.best(),'validation':self.validation,'events':self.events,'clock':self.clock}

    def restore_state(self,state,validator):
        if state.get('context')!=self.context:
            self.invalidate(['CONTEXT_CHANGED']);return False
        self.clock=state.get('clock',0);self.events=copy.deepcopy(state.get('events',[]))
        return self.consider(state.get('network',{}),validator)
