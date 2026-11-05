"""Bounded negotiation of complete route sets; no Revit or model writes."""
import copy
import time
from . import geometry as g
from .route_geometry import validate_route_geometry
from ..settings import for_route,profile as make_profile


def usable(route):
    return route.get('status',route.get('collision_status')) in ('VALID','VALID_WITH_WARNINGS','CONDITIONAL') and len(route.get('points_mm') or [])>=2


def points(route):
    return [p for piece in route.get('primitives',[]) for p in piece['points_mm']]


def score(routes,ids):
    """Completion dominates all savings; conditional crossings remain explicit."""
    return (sum(not usable(routes.get(rid,{})) for rid in ids),
            sum(routes.get(rid,{}).get('status')=='CONDITIONAL' for rid in ids),
            sum(routes.get(rid,{}).get('cost',{}).get('passages',0) for rid in ids),
            sum(routes.get(rid,{}).get('cost',{}).get('total',0) for rid in ids))


def priority(space,request,configuration):
    profile=make_profile(for_route(configuration,request['route_id']))
    spare=[]
    for port in (request['source'],request['destination']):
        x,y,z=port['xyz_mm'];local=[]
        for region in space.regions:
            x0,y0,x1,y1=region.bounds
            if not (x0<=x<=x1 and y0<=y<=y1):continue
            a,b,c=region.lower_coefficients;lower=a*x+b*y+c
            a,b,c=region.upper_coefficients;upper=a*x+b*y+c
            local.append((max(lower-z,0,z-upper),(upper-lower)*min(region.lower_normal_z,region.upper_normal_z)-2*profile.radius_mm))
        if local:spare.append(min(local)[1])
    return (min(spare) if spare else float('inf'),-profile.radius_mm,
            -g.distance(request['source']['xyz_mm'],request['destination']['xyz_mm']),request['route_id'])


def overlaps(a,ra,b,rb):
    # Apply the larger requested surface gap once, including adapters.
    shared=min(getattr(ra,'clearance_mm',0),getattr(rb,'clearance_mm',0))
    ra=float(ra)-shared/2
    rb=float(rb)-shared/2
    for own,other,radius in ((a,b,rb),(b,a,ra)):
        box=own.get('adapter_pose',{}).get('body_box_mm')
        if box:
            body=g.TriangleSolid(*g.box_mesh(g.expand(box,ra if own is a else rb)))
            path=points(other)
            if any(body.capsule(p,q,radius) for p,q in zip(path,path[1:])):return True
            other_box=other.get('adapter_pose',{}).get('body_box_mm')
            if other_box and g.overlaps(g.expand(box,ra),g.expand(other_box,rb)):return True
    aa,bb=points(a),points(b)
    if len(aa)<2 or len(bb)<2:return False
    ra+=max((p.get('sagitta_mm',0) for p in a.get('primitives',[])),default=0)
    rb+=max((p.get('sagitta_mm',0) for p in b.get('primitives',[])),default=0)
    if not g.overlaps(g.expand(g.bounds(aa),ra),g.expand(g.bounds(bb),rb)):return False
    return any(g.segment_distance(p,q,u,v)<ra+rb-1e-6
               for p,q in zip(aa,aa[1:]) for u,v in zip(bb,bb[1:]))


def negotiate(space,routes,requests,configuration,protected,planner,deadline,cancelled=None):
    """Give a blocked route its corridor, then reroute its blockers atomically."""
    by_id={r['route_id']:r for r in requests};protected=set(protected)
    result=copy.deepcopy(routes);events=[]
    options=configuration.get('coordination',{})
    max_trials=options.get('max_repair_trials',6);max_evictions=options.get('max_evictions',3)
    failed=[r for r in requests if r['route_id'] not in protected and not usable(result.get(r['route_id'],{}))]
    failed.sort(key=lambda r:priority(space,r,configuration))
    radii={rid:make_profile(for_route(configuration,rid)).radius_mm for rid in by_id}

    def occupied(records,omit=()):
        working=space.clone()
        for rid,route in records.items():
            if rid not in omit and usable(route):working.reserve(rid,points(route),radii[rid])
        return working

    for index,request in enumerate(failed[:max_trials]):
        if (cancelled and cancelled()) or time.monotonic()>=deadline:break
        rid=request['route_id'];remaining=max(1,min(len(failed),max_trials)-index)
        trial_deadline=min(deadline,time.monotonic()+(deadline-time.monotonic())/remaining)
        event={'route_id':rid,'accepted':False,'evicted':[]}
        fixed={key:value for key,value in result.items() if key in protected}
        # Keep half the trial time to rebuild displaced routes.
        candidate=planner(occupied(fixed),request,make_profile(for_route(configuration,rid)),configuration,
                          cancelled=cancelled,deadline=time.monotonic()+(trial_deadline-time.monotonic())/2)
        if not usable(candidate):
            event['reason']='NO_ROUTE_WITHOUT_MOVABLE_NEIGHBOURS';events.append(event);continue
        blockers=[key for key,value in result.items() if key!=rid and usable(value)
                  and overlaps(candidate,radii[rid],value,radii[key])]
        event['evicted']=sorted(blockers)
        if set(blockers)&protected or len(blockers)>max_evictions:
            event['reason']='PROTECTED_ROUTE_OR_EVICTION_LIMIT';events.append(event);continue
        trial=copy.deepcopy(result);trial[rid]=candidate
        working=occupied(result,omit=set(blockers)|{rid})
        checked=validate_route_geometry(working,candidate,make_profile(for_route(configuration,rid)))
        if checked['status'] in ('INVALID','NOT_EVALUATED'):
            event['reason']='CANDIDATE_NOT_COMPATIBLE';events.append(event);continue
        trial[rid].update(checked);working.reserve(rid,points(trial[rid]),radii[rid])
        complete=True
        for position,key in enumerate(sorted(blockers,key=lambda key:priority(space,by_id[key],configuration))):
            if (cancelled and cancelled()) or time.monotonic()>=trial_deadline:complete=False;break
            share=(trial_deadline-time.monotonic())/max(1,len(blockers)-position)
            replacement=planner(working,by_id[key],make_profile(for_route(configuration,key)),configuration,
                                cancelled=cancelled,deadline=time.monotonic()+share)
            if not usable(replacement):complete=False;break
            checked=validate_route_geometry(working,replacement,make_profile(for_route(configuration,key)))
            if checked['status'] in ('INVALID','NOT_EVALUATED'):complete=False;break
            replacement.update(checked);trial[key]=replacement;working.reserve(key,points(replacement),radii[key])
        before=score(result,by_id);after=score(trial,by_id)
        if complete and not (cancelled and cancelled()) and time.monotonic()<trial_deadline and after<before:
            result=trial;event.update(accepted=True,before=list(before),after=list(after),reason='BETTER_COMPLETE_SET')
        else:event['reason']='NO_GLOBAL_IMPROVEMENT'
        events.append(event)
    return result,events
