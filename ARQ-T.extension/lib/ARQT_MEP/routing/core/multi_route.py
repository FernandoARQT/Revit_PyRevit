"""Bounded A* planning, volume reservations, route reorder and rerouting."""
import copy
import itertools
import time
from dataclasses import replace
from . import geometry as g
from .astar_3d import find_route_3d,SparseGrid
from .route_geometry import simplify_route,validate_route_geometry,rejected_path
from ..settings import for_route,profile as make_profile
from . import cooperative


def _stub(endpoint,length):
    p=tuple(endpoint['xyz_mm']);d=endpoint.get('direction')
    return (p,g.add(p,g.mul(g.unit(d),length))) if d and length>0 else (p,p)


def compact_anchor(space,port,profile,settings):
    """Nearest free bulk-routing anchor. The physical connection is checked later."""
    origin=tuple(port['xyz_mm']);normal=port.get('direction')
    if not normal:return origin,origin
    step=settings['search']['local_refinement_mm']
    radius=profile.radius_mm
    maximum=max(settings['connection_straight_mm'],4*profile.radius_mm)
    minimum=0.
    if port.get('kind')=='TERMINAL_ADAPTER_OUTLET':
        # Lateral adapter docking needs space for a finite-radius flexible
        # turn plus its clearance; this is not a vertical ceiling offset.
        minimum=profile.bend_radius_mm+profile.radius_mm
        maximum=max(maximum,minimum+2*profile.bend_radius_mm)
    normal=g.unit(normal)
    distances={index*step for index in range(int(maximum/step)+1)}
    # Plane intersections avoid missing a thin usable interval between two
    # sample heights. No extra half-cell diagonal belongs to a centerline.
    for region in space.regions:
        low,high=0.,maximum
        for coefficients,nz,sign in ((region.lower_coefficients,region.lower_normal_z,1),
                                     (region.upper_coefficients,region.upper_normal_z,-1)):
            a,b,c=coefficients
            value=sign*(origin[2]-a*origin[0]-b*origin[1]-c)*nz
            slope=sign*(normal[2]-a*normal[0]-b*normal[1])*nz
            if abs(slope)<1e-10:
                if value<radius:high=-1;break
            elif slope>0:low=max(low,(radius-value)/slope)
            else:high=min(high,(radius-value)/slope)
        if high>=low:
            distances.update((low,min(high,low+.001),(low+high)/2,high))
    for distance in sorted(distances):
        if distance<minimum-1e-6:continue
        point=g.add(origin,g.mul(normal,distance))
        if space.free(point,point,radius):
            return origin,point
    return None


def plan_route(space,request,profile,settings,scenario='A',cancelled=None,diversity=(),deadline=None):
    if request.get('destination_alternatives'):
        choices=[request['destination']]+request['destination_alternatives'];offset=request.get('attempt_number',0)%len(choices)
        choices=choices[offset:]+choices[:offset];end=deadline if deadline is not None else time.monotonic()+settings['search']['max_seconds']
        last={'status':'NOT_EVALUATED','reason':'NO_ADAPTER_POSE_ROUTE'};attempts=[]
        for index,port in enumerate(choices):
            if time.monotonic()>=end or (cancelled and cancelled()):break
            variant=dict(request,destination=port);variant.pop('destination_alternatives',None)
            last=plan_route(space,variant,profile,settings,scenario,cancelled,diversity,
                time.monotonic()+max(0,end-time.monotonic())/max(1,len(choices)-index))
            attempts.append({'port':port['id'],'reason':last.get('reason'),'status':last['status']})
            if cooperative.usable(last):
                last.update(chosen_destination=copy.deepcopy(port),adapter_pose=copy.deepcopy(port['adapter_pose']))
                last['adapter_attempts']=attempts;return last
        last['adapter_attempts']=attempts;return last
    from .search_budget import SearchBudgetManager
    from .terminal_adapter import posed_space
    pose=request['destination'].get('adapter_pose') or request['source'].get('adapter_pose')
    space=posed_space(space,pose)
    local=dict(request)
    budget=SearchBudgetManager(deadline if deadline is not None else time.monotonic()+settings['search']['max_seconds'],cancelled,request.get('search_history'),clock=time.monotonic)
    local['_budget_manager']=budget
    result=_plan_route_impl(space,local,profile,settings,scenario,cancelled,diversity,deadline)
    result['search_history']=budget.export_state()
    result.setdefault('chosen_source',copy.deepcopy(request['source']))
    result.setdefault('source',copy.deepcopy(request['source']))
    result.setdefault('destination',copy.deepcopy(request['destination']))
    result.setdefault('chosen_destination',copy.deepcopy(request['destination']))
    if request['destination'].get('adapter_pose'):result['adapter_pose']=copy.deepcopy(request['destination']['adapter_pose'])
    # Standalone geometric searches need only endpoints. Preserve an explicit
    # network assignment when supplied; never invent one for geometric callers.
    if 'network_type' in request:result.setdefault('network_type',request['network_type'])
    from .candidate_repository import audit
    audit('DISCOVERED',request['route_id'],result,method=result.get('search_method'),status=result.get('status'),reason=result.get('reason'))
    return result


def _plan_route_impl(space,request,profile,settings,scenario='A',cancelled=None,diversity=(),deadline=None):
    settings=for_route(settings,request['route_id'])
    profile=replace(profile,section=settings['section'],clearance_mm=settings['clearance_mm'],
                    bend_radius_mm=settings['bend_radius_mm'],weights=settings['weights'])
    if request.get('search_from_destination'):
        reverse=dict(request,source=request['destination'],destination=request['source'],
                     waypoints_mm=list(reversed(request.get('waypoints_mm',[]))),search_from_destination=False)
        reverse['topological_corridors']={request['destination']['id']:[dict(c,
            region_indices=list(reversed(c['region_indices'])),topology_nodes=list(reversed(c.get('topology_nodes',[]))),
            edges=[dict(e,points_mm=list(reversed(e['points_mm']))) for e in reversed(c['edges'])],
            portal_ids=list(reversed(c['portal_ids'])))
            for c in request.get('topological_corridors',{}).get(request['source']['id'],[])]}
        route=plan_route(space,reverse,profile,settings,scenario,cancelled,diversity,deadline)
        if len(route.get('points_mm',[]))>=2:
            route['points_mm'].reverse()
            if 'routing_points_mm' in route:route['routing_points_mm'].reverse()
            if 'connection_anchors_mm' in route:
                anchors=route['connection_anchors_mm'];route['connection_anchors_mm']={'source':anchors['destination'],'destination':anchors['source']}
            route.update(validate_route_geometry(space,route,profile));route['search_direction']='DESTINATION_TO_SOURCE'
        route.update(chosen_source=copy.deepcopy(request['source']),source=copy.deepcopy(request['source']),destination=copy.deepcopy(request['destination']),chosen_destination=copy.deepcopy(request['destination']))
        return route
    started=time.monotonic();attempts=[];search=settings['search'];source=request['source'];destination=request['destination']
    from .search_budget import SearchBudgetManager,attempt_seed
    budget=request['_budget_manager']
    methods_left=5
    length=settings['connection_straight_mm']
    compact=request.get('compact_connections',False)
    if g.distance(source['xyz_mm'],destination['xyz_mm'])<1e-6:
        return {'status':'INVALID','reason':'COINCIDENT_ENDPOINTS'}
    if compact:
        anchors=[compact_anchor(space,p,profile,settings) for p in (source,destination)]
        if any(a is None for a in anchors):
            return {'status':'INVALID','reason':'NO_FREE_CONNECTION_ANCHOR','port_id':(source if anchors[0] is None else destination)['id']}
        (start,start_search),(end,end_search)=anchors
    else:
        start,start_search=_stub(source,length);end,end_search=_stub(destination,length)
    stubs=[]
    for port,a,b in ((source,start,start_search),(destination,end,end_search)):
        if g.distance(a,b)<1e-6:continue
        ignore=[port['element_key']] if port.get('element_key') else []
        if not compact and not space.stub_free(a,b,profile.radius_mm,ignore):
            blocking=space.collisions(a,b,profile.radius_mm,ignore)
            return {'status':'INVALID','reason':'CONNECTOR_OUTSIDE_FREE_SPACE_OR_CONNECTION_BLOCKED','port_id':port['id'],
                    'connection_check':{'straight_length_mm':length,'envelope_radius_mm':profile.radius_mm,
                        'blocking_elements':[{'id':o.id,'element_id':o.data.get('element_id'),'name':o.data.get('name'),
                                              'hard_forbidden':bool(o.data.get('hard_forbidden'))}
                                             for o in space.obstacles if o.id in blocking],
                        'message':'Compruebe la envolvente y el tramo recto de conexión dentro del volumen sobre la cara inferior del falso techo.'}}
        stubs.append({'points_mm':[a,b],'ignore_owner_ids':ignore,'native_check_pending':compact,
            'adapter_endpoint':port.get('kind')=='TERMINAL_ADAPTER_OUTLET'})
    waypoints=list(request.get('waypoints_mm',[]))
    for label,port,anchor in (('source',source,start_search),('destination',destination,end_search)):
        if port.get('kind')!='TERMINAL_ADAPTER_OUTLET':continue
        guard=g.add(anchor,g.mul(g.unit(port['direction']),profile.bend_radius_mm))
        if not space.free(guard,guard,profile.radius_mm):
            return {'status':'INVALID','reason':'ADAPTER_BEND_APPROACH_BLOCKED','port_id':port['id']}
        if label=='source':waypoints.insert(0,guard)
        else:waypoints.append(guard)
    docking_pending=False
    if request.get('oriented_docking') and source.get('direction') and destination.get('direction'):
        arm=max(profile.bend_radius_mm,profile.min_curve_length_mm*2)
        guards=[g.add(start_search,g.mul(g.unit(source['direction']),arm)),
                g.add(end_search,g.mul(g.unit(destination['direction']),arm))]
        # R is the trim of a 90-degree turn, not a universal connector stub.
        # An optional guard above a sloping ceiling must not invalidate the
        # whole route. Physical connector/fitting alignment remains pending.
        if all(space.free(p,p,profile.radius_mm) for p in guards):waypoints=[guards[0]]+waypoints+[guards[1]]
        else:docking_pending=True
    for p in waypoints:
        if not space.free(p,p,profile.radius_mm):return {'status':'INVALID','reason':'WAYPOINT_BLOCKED','point_mm':p}
    def repeated(path):
        def key(points):
            values=tuple(tuple(round(v,3) for v in p) for p in points)
            return min(values,tuple(reversed(values)))
        rejected=settings['constraints'].get('rejected_paths',{}).get(request['route_id'],[])
        if any(key(path)==key(old) for old in rejected):return True
        return any(all(min(g.point_segment(a,b,p) for a,b in zip(old,old[1:]))<profile.radius_mm*2
                       for p in path) and all(min(g.point_segment(a,b,p) for a,b in zip(path,path[1:]))<profile.radius_mm*2
                       for p in old) for old in diversity if len(old)>=2)
    if waypoints:
        # Required approach points need not force a staircase search. Test
        # their direct connecting legs and finite-radius turns first.
        path=[start_search]+[tuple(p) for p in waypoints]+[end_search]
        controls=([start] if g.distance(start,start_search)>1e-6 else [])+path+([end] if g.distance(end,end_search)>1e-6 else [])
        candidate={'points_mm':controls,'passages':[],'endpoint_stubs':stubs,'waypoints_mm':request.get('waypoints_mm',[])}
        if compact:candidate.update(connection_validation_pending=True,routing_points_mm=path,
            connection_anchors_mm={'source':start_search,'destination':end_search})
        checked=validate_route_geometry(space,candidate,profile)
        if checked['status'] not in ('INVALID','NOT_EVALUATED') and not repeated(path):
            candidate.update(checked,search_method='EXACT_WAYPOINT_CORRIDOR',expanded=0,seconds=time.monotonic()-started,attempts=[])
            return candidate
    # A short free main must not wait behind hundreds of portal trials.
    if not waypoints and not repeated([start_search,end_search]):
        path=[start_search,end_search]
        controls=([start] if g.distance(start,start_search)>1e-6 else [])+path+([end] if g.distance(end,end_search)>1e-6 else [])
        direct={'points_mm':controls,'passages':[],'endpoint_stubs':stubs,'waypoints_mm':[]}
        if compact:direct.update(connection_validation_pending=True,routing_points_mm=path,
            connection_anchors_mm={'source':start_search,'destination':end_search})
        checked=validate_route_geometry(space,direct,profile)
        if checked['status'] not in ('INVALID','NOT_EVALUATED'):
            direct.update(checked);direct.update(search_method='EXACT_DIRECT_CORRIDOR',expanded=0,seconds=time.monotonic()-started,attempts=[])
            return direct
    # Try already-authorized portals directly before expanding thousands of
    # cells on either side of a wall. Admission uses exactly the grid's rules.
    if not waypoints and settings['constraints']['passages']:
        # This shortcut tests ONE passage, not a general multi-wall route.
        # Bound it separately so hundreds of impossible shortcuts cannot
        # consume the grid/roadmap allowance before either starts searching.
        shortcut_started=time.monotonic();tested_portals=0
        shortcut_deadline=shortcut_started+min(.25,max(0,(deadline-shortcut_started) if deadline is not None else search['max_seconds'])*.1)
        admission=SparseGrid(space,start_search,end_search,search['coarse_resolution_mm'],profile,settings['constraints']['passages'],scenario)
        portals=sorted(admission.portals,key=lambda p:min(g.distance(start_search,a)+g.distance(b,end_search) for a,b in (p['points_mm'],list(reversed(p['points_mm'])))))
        for portal in portals:
            if time.monotonic()>=shortcut_deadline:break
            tested_portals+=1
            if cancelled and cancelled():return {'status':'NOT_EVALUATED','reason':'CANCELLED'}
            for a,b in (portal['points_mm'],list(reversed(portal['points_mm']))):
                if not space.free(start_search,a,profile.radius_mm) or not space.free(b,end_search,profile.radius_mm):continue
                if space.forbidden_hit(a,b,profile.radius_mm) or space.collisions(a,b,profile.radius_mm,portal['element_ids']):continue
                path=[]
                for point in (start_search,tuple(a),tuple(b),end_search):
                    if not path or g.distance(path[-1],point)>1e-6:path.append(point)
                if repeated(path):continue
                controls=([start] if g.distance(start,start_search)>1e-6 else [])+path+([end] if g.distance(end,end_search)>1e-6 else [])
                candidate={'points_mm':controls,'passages':[portal],'endpoint_stubs':stubs,'waypoints_mm':[]}
                if compact:candidate.update(connection_validation_pending=True,routing_points_mm=path,connection_anchors_mm={'source':start_search,'destination':end_search})
                checked=validate_route_geometry(space,candidate,profile)
                if checked['status']!='INVALID':
                    candidate.update(checked);candidate.update(search_method='EXACT_AUTHORIZED_PASSAGE',expanded=0,seconds=time.monotonic()-started,attempts=[])
                    return candidate
        attempts.append({'method':'EXACT_PASSAGE_SHORTCUT','tested_portals':tested_portals,
                         'seconds':time.monotonic()-shortcut_started,
                         'reason':'SHORTCUT_BUDGET' if time.monotonic()>=shortcut_deadline else 'NO_SINGLE_PASSAGE_SOLUTION'})
    # Exact swept-volume checks can certify simple corridors without building a
    # fine grid across the whole dwelling. Failed candidates still go to A*.
    if compact and not waypoints:
        simple_paths=[[start_search,end_search]]
        for axes in itertools.permutations(range(3)):
            current=list(start_search);path=[tuple(current)]
            for axis in axes:
                current[axis]=end_search[axis]
                if tuple(current)!=path[-1]:path.append(tuple(current))
            simple_paths.append(path)
        checked=set()
        for path in simple_paths:
            key=tuple(path)
            if key in checked:continue
            checked.add(key)
            if repeated(path):continue
            if cancelled and cancelled():return {'status':'NOT_EVALUATED','reason':'CANCELLED'}
            if deadline is not None and time.monotonic()>=deadline:break
            if not all(space.free(a,b,profile.radius_mm) for a,b in zip(path,path[1:])):continue
            controls=([start] if g.distance(start,start_search)>1e-6 else [])+path+([end] if g.distance(end,end_search)>1e-6 else [])
            candidate={'points_mm':controls,'routing_points_mm':path,'passages':[],
                       'connection_validation_pending':True,'endpoint_stubs':stubs,'waypoints_mm':[],
                       'connection_anchors_mm':{'source':start_search,'destination':end_search}}
            validation=validate_route_geometry(space,candidate,profile)
            if validation['status']!='INVALID':
                candidate.update(validation);candidate.update(expanded=0,search_method='EXACT_SIMPLE_CORRIDOR',
                    seconds=time.monotonic()-started,attempts=[])
                return candidate
    if not waypoints:
        portal_by_id={p['id']:p for p in settings['constraints']['passages']}
        for corridor in request.get('topological_corridors',{}).get(source['id'],[]):
            if scenario=='A' and corridor.get('scenario')=='B':continue
            if deadline is not None and time.monotonic()>=deadline:break
            controls=[start_search];used=[]
            for edge in corridor['edges']:
                if edge['kind']!='PORTAL':continue
                a,b=edge['points_mm'];axis=g.unit(g.sub(b,a));arm=profile.bend_radius_mm
                controls.extend([g.sub(a,g.mul(axis,arm)),a,b,g.add(b,g.mul(axis,arm))])
                used.append(portal_by_id[edge['portal_id']])
            controls.append(end_search)
            candidate={'points_mm':controls,'passages':used,'endpoint_stubs':stubs,'waypoints_mm':[]}
            if compact:candidate.update(connection_validation_pending=True,routing_points_mm=controls,
                connection_anchors_mm={'source':start_search,'destination':end_search},
                points_mm=([start] if g.distance(start,start_search)>1e-6 else [])+controls+([end] if g.distance(end,end_search)>1e-6 else []))
            checked=validate_route_geometry(space,candidate,profile)
            if checked['status'] not in ('INVALID','NOT_EVALUATED') and not repeated(controls):
                candidate.update(checked);candidate.update(search_method='TOPOLOGICAL_PORTAL_CHAIN',seconds=time.monotonic()-started,attempts=attempts)
                return candidate
    if settings['coordination'].get('rrt_connect_enabled',False) and not waypoints:
        from .rrt_connect import discover
        remaining=deadline-time.monotonic() if deadline is not None else search['max_seconds']
        corridors=[c for c in request.get('topological_corridors',{}).get(source['id'],[]) if scenario=='B' or c.get('scenario')!='B']
        method_started=time.monotonic()
        leg=discover(space,start_search,end_search,profile,settings,budget.allocate('RRT',pending=methods_left),
                     cancelled,corridors,attempt_seed(settings.get('random_seed',0),request['route_id'],source['id'],[c.get('portal_ids') for c in corridors],request.get('attempt_number',0)))
        attempts.append({k:v for k,v in leg.items() if k in ('status','reason','random_seed','tested_motions','rrt_nodes','rrt_trials')})
        attempts[-1]['method']='EXPERIMENTAL_RRT_CONNECT'
        attempts[-1]['seconds']=time.monotonic()-method_started
        budget.record('RRT',attempts[-1]['seconds'],'points_mm' in leg,leg.get('reason'));methods_left=max(1,methods_left-1)
        if 'points_mm' in leg and not repeated(leg['points_mm']):
            path=leg['points_mm'];controls=([start] if g.distance(start,start_search)>1e-6 else [])+path+([end] if g.distance(end,end_search)>1e-6 else [])
            candidate=dict(leg,points_mm=controls,endpoint_stubs=stubs,waypoints_mm=[])
            if compact:candidate.update(connection_validation_pending=True,routing_points_mm=path,
                connection_anchors_mm={'source':start_search,'destination':end_search})
            checked=validate_route_geometry(space,candidate,profile)
            if checked['status'] not in ('INVALID','NOT_EVALUATED'):
                candidate.update(checked);candidate.update(seconds=time.monotonic()-started,attempts=attempts)
                return candidate
    if settings['coordination'].get('incremental_enabled',True) and not waypoints:
        from .incremental_roadmap import search as lazy_search
        remaining=deadline-time.monotonic() if deadline is not None else search['max_seconds']
        corridors=request.get('topological_corridors',{}).get(source['id'],[])
        method_started=time.monotonic()
        leg=lazy_search(space,start_search,end_search,profile,settings,scenario,
                       budget.allocate('LAZY',pending=methods_left),cancelled,corridors)
        attempts.append({k:v for k,v in leg.items() if k in ('reason','status','roadmap_nodes','added_nodes','candidate_paths_tested','static_edges_checked','static_edges_reused')})
        attempts[-1]['method']='INCREMENTAL_LAZY_ROADMAP'
        attempts[-1]['seconds']=time.monotonic()-method_started
        budget.record('LAZY',attempts[-1]['seconds'],'points_mm' in leg,leg.get('reason'));methods_left=max(1,methods_left-1)
        if 'points_mm' in leg and not repeated(leg['points_mm']):
            path=leg['points_mm'];controls=([start] if g.distance(start,start_search)>1e-6 else [])+path+([end] if g.distance(end,end_search)>1e-6 else [])
            candidate=dict(leg,points_mm=controls,endpoint_stubs=stubs,waypoints_mm=[])
            if compact:candidate.update(connection_validation_pending=True,routing_points_mm=path,
                connection_anchors_mm={'source':start_search,'destination':end_search})
            checked=validate_route_geometry(space,candidate,profile)
            if checked['status'] not in ('INVALID','NOT_EVALUATED'):
                candidate.update(checked);candidate.update(seconds=time.monotonic()-started,attempts=attempts)
                return candidate
    if settings['coordination'].get('iris_gcs_enabled',True) and not waypoints:
        from .iris_gcs import find_gcs
        remaining=deadline-time.monotonic() if deadline is not None else search['max_seconds']
        # Do not initialize a convex backend in a subsecond slot and starve
        # the roadmap/grid. Larger focused attempts can use it productively.
        minimum=settings['coordination'].get('iris_min_seconds',3.)
        method_started=time.monotonic()
        leg=(find_gcs(space,start_search,end_search,profile,settings,scenario,cancelled,
                     budget.allocate('IRIS',pending=methods_left)) if budget.allocate('IRIS',pending=methods_left)-time.monotonic()>=minimum else
             {'status':'NOT_EVALUATED','reason':'IRIS_DEFERRED_SHORT_BUDGET'})
        attempt={key:leg[key] for key in ('status','reason','iris_cells','gcs_paths_tested','gcs_nodes_expanded','geometry_rejections','detail','iris_diagnostics') if key in leg}
        attempt['method']='IRIS_GCS_EXPLICIT_PATH_SEARCH';attempt['seconds']=time.monotonic()-method_started;attempts.append(attempt)
        budget.record('IRIS',attempt['seconds'],'points_mm' in leg,leg.get('reason'));methods_left=max(1,methods_left-1)
        if 'points_mm' in leg and not repeated(leg['points_mm']):
            path=leg['points_mm'];controls=([start] if g.distance(start,start_search)>1e-6 else [])+path+([end] if g.distance(end,end_search)>1e-6 else [])
            candidate=dict(leg,points_mm=controls,endpoint_stubs=stubs,waypoints_mm=[])
            if compact:candidate.update(connection_validation_pending=True,routing_points_mm=path,
                connection_anchors_mm={'source':start_search,'destination':end_search})
            checked=validate_route_geometry(space,candidate,profile)
            if checked['status'] not in ('INVALID','NOT_EVALUATED'):
                candidate.update(checked);candidate.update(seconds=time.monotonic()-started,attempts=attempts)
                return candidate
            attempt.update(status=checked['status'],reason=checked.get('reason','FINAL_CONNECTION_GEOMETRY'),errors=checked.get('errors',[]))
    if settings['coordination']['roadmap_enabled'] and not waypoints:
        from .corridors import find_corridor
        remaining=deadline-time.monotonic() if deadline is not None else search['max_seconds']
        corridor_deadline=budget.allocate('VISIBILITY',pending=methods_left)
        method_started=time.monotonic()
        leg=find_corridor(space,start_search,end_search,profile,settings,scenario,cancelled,diversity,corridor_deadline)
        if 'points_mm' in leg:
            path=leg['points_mm']
            controls=([start] if g.distance(start,start_search)>1e-6 else [])+path+([end] if g.distance(end,end_search)>1e-6 else [])
            candidate=dict(leg,points_mm=controls,endpoint_stubs=stubs,waypoints_mm=[])
            if compact:candidate.update(connection_validation_pending=True,routing_points_mm=path,
                connection_anchors_mm={'source':start_search,'destination':end_search})
            checked=validate_route_geometry(space,candidate,profile)
            if checked['status'] not in ('INVALID','NOT_EVALUATED') and not repeated(path):
                candidate.update(checked);candidate.update(seconds=time.monotonic()-started,attempts=attempts)
                return candidate
        attempts.append(dict(method='VISIBILITY_ROADMAP',**{key:leg[key] for key in (
            'status','reason','expanded','roadmap_nodes','tested_edges','preparation_seconds','search_seconds') if key in leg}))
        attempts[-1]['seconds']=time.monotonic()-method_started
        budget.record('VISIBILITY',attempts[-1]['seconds'],'points_mm' in leg,leg.get('reason'));methods_left=max(1,methods_left-1)
    best=None
    resolutions=list(dict.fromkeys(search[k] for k in ('coarse_resolution_mm','fine_resolution_mm','local_refinement_mm')))
    for resolution_index,resolution in enumerate(resolutions):
        resolution_started=time.monotonic()
        points=[start_search];passages=[];expanded=0;failed=None
        legs=[start_search]+[tuple(p) for p in waypoints]+[end_search]
        for a,b in zip(legs,legs[1:]):
            if g.distance(a,b)<1e-6:continue
            remaining=deadline-time.monotonic() if deadline is not None else search['max_seconds']
            if remaining<=0:failed={'reason':'TOTAL_TIME_LIMIT'};break
            share=remaining/max(1,(len(resolutions)-resolution_index)*(len(legs)-1)) if deadline is not None else remaining
            leg_search=dict(search,max_seconds=share if deadline is not None else min(search['max_seconds'],share))
            if resolution_index==len(resolutions)-1:
                # Keep a bounded slot available if the 10 mm search stalls
                # near its destination instead of spending the whole deadline.
                leg_search['max_seconds']=max(.001,leg_search['max_seconds']-min(search['rescue_seconds'],leg_search['max_seconds']*.25))
            leg=find_route_3d(space,a,b,profile,leg_search,resolution,settings['constraints']['passages'],scenario,cancelled,diversity)
            if 'points_mm' not in leg and resolution_index==len(resolutions)-1 and leg.get('reason')!='CANCELLED':
                from .local_rescue import rescue_tail
                local_deadline=deadline if deadline is not None else time.monotonic()+search['rescue_seconds']
                leg=rescue_tail(space,leg,b,profile,search,settings['constraints']['passages'],scenario,cancelled,local_deadline,diversity)
                if leg.get('local_rescue'):attempts.append(leg['local_rescue'])
            expanded+=leg.get('expanded',0)
            if 'points_mm' not in leg:failed=leg;break
            points.extend(leg['points_mm'][1:]);passages.extend(leg['passages'])
        if failed:
            attempts.append({'method':'ASTAR_3D','resolution_mm':resolution,'reason':failed['reason'],'expanded':expanded,
                'seconds':time.monotonic()-resolution_started})
            if failed['reason'] in ('CANCELLED','TOTAL_TIME_LIMIT'):break
            continue
        routing_points=list(points)
        if g.distance(start,start_search)>1e-6:points.insert(0,start)
        if g.distance(end,end_search)>1e-6:points.append(end)
        preserve=[p for passage in passages for p in passage['points_mm']]+[start_search,end_search]+waypoints
        points=simplify_route(points,space,profile.radius_mm,preserve,profile.bend_radius_mm)
        if compact:
            first=next((i for i,p in enumerate(points) if g.distance(p,start_search)<.001),None)
            last=next((i for i,p in enumerate(points) if g.distance(p,end_search)<.001),None)
            if first is None or last is None:
                attempts.append({'resolution_mm':resolution,'reason':'CONNECTION_ANCHOR_LOST_IN_SIMPLIFICATION'})
                continue
            routing_points=points[first:last+1]
        candidate={'points_mm':points,'passages':list({p['id']:p for p in passages}.values()),'endpoint_stubs':stubs,'waypoints_mm':waypoints}
        if request.get('oriented_docking'):candidate['connection_frame_check']=(
            'OPTIONAL_APPROACH_BLOCKED; FITTING_GEOMETRY_PENDING' if docking_pending else 'TANGENT_APPROACH; FITTING_GEOMETRY_PENDING')
        if compact:
            candidate.update(connection_validation_pending=True,routing_points_mm=routing_points,
                             connection_anchors_mm={'source':start_search,'destination':end_search})
        validation=validate_route_geometry(space,candidate,profile)
        candidate.update(validation);candidate['expanded']=expanded;candidate['resolution_mm']=resolution
        candidate['seconds']=time.monotonic()-started;candidate['attempts']=attempts
        if validation['status']!='INVALID' and not repeated(routing_points):best=candidate;break
        attempts.append({'resolution_mm':resolution,'reason':'FINAL_GEOMETRY','errors':validation['errors']})
    return best or {'status':'NOT_EVALUATED','reason':'NO_CERTIFIED_ROUTE_IN_SEARCH_LIMITS','attempts':attempts,'seconds':time.monotonic()-started}


def _reserved_points(route):
    return [p for piece in route.get('primitives',[]) for p in piece['points_mm']]


def optimize_multiple_routes(space,requests,profile,settings,existing=None,cancelled=None,target_route_ids=None,absolute_deadline=None,exploration_state=None,candidate_bank=None):
    from ..parallel import Session
    from .candidate_repository import audit_session
    with audit_session() as events,Session(space,settings,exploration_state) as workers:
        result=_optimize_multiple_routes(space,requests,profile,settings,existing,cancelled,target_route_ids,absolute_deadline,candidate_bank)
        workers._collect([], -1, {}) if workers.inflight else None
        for rid,items in workers.late_candidates.items():result.setdefault('candidate_bank',{}).setdefault(rid,[]).extend(items)
        result['parallel']=workers.report
        from .incremental_roadmap import export_maps
        result['exploration_state']=export_maps(space)
        result['candidate_audit']=events
        return result


def _optimize_multiple_routes(space,requests,profile,settings,existing=None,cancelled=None,target_route_ids=None,absolute_deadline=None,candidate_bank=None):
    started=time.monotonic();existing=existing or {};locked=set(settings['constraints']['locked_routes'])
    deadline=min(started+settings['search']['max_total_seconds'],absolute_deadline or float('inf'))
    available=max(0,deadline-started)
    by_id={r['route_id']:r for r in requests};iterations=[]
    ignored_locks=sorted(rid for rid in locked if len((existing.get(rid) or {}).get('points_mm') or [])<2)
    locked-=set(ignored_locks)
    if target_route_ids is not None:locked-=set(target_route_ids)
    preserve=set(by_id)-set(target_route_ids) if target_route_ids is not None else set()
    for rid in locked:
        if rid in by_id and rid not in existing:raise ValueError('Ruta bloqueada sin geometría recuperable: '+rid)
    # Do not spend the shared budget on an unreserved preliminary solve whose
    # results would then be discarded and solved a second time.
    difficult=sorted(requests,key=lambda r:(sum(len(v) for v in r.get('topological_corridors',{}).values()) or float('inf'),cooperative.priority(space,r,settings)))
    orders=[difficult,list(reversed(difficult))]
    if len(difficult)>1:orders.append(difficult[1:]+difficult[:1])
    best={};best_score=(float('inf'),)*4;best_space=space;best_coordination=[];best_portfolio=None;candidate_bank=copy.deepcopy(candidate_bank or {})
    for request in requests:
        candidate_bank[request['route_id']]=[c for c in candidate_bank.get(request['route_id'],[]) if not rejected_path(c,request['route_id'],settings)]
        for candidate in candidate_bank.get(request['route_id'],[]):
            candidate.setdefault('chosen_source',copy.deepcopy(candidate.get('source') or request['source']))
    from .global_incumbent import GlobalIncumbent,validate_network
    from .candidate_repository import CandidateRepository,context_signature
    from .search_budget import SearchBudgetManager
    context=context_signature(space,requests,settings);state=getattr(space,'planner_state',{})
    incumbent=GlobalIncumbent(context);repository=CandidateRepository(context)
    # Protected failures remain visible, but cannot constrain the certified
    # network as though they still had usable geometry.
    protected_routes={};protected_space=space.clone()
    for rid in sorted((locked|preserve)&set(by_id)):
        route=copy.deepcopy(existing.get(rid,{'status':'NOT_EVALUATED','reason':'NOT_PREVIOUSLY_CALCULATED'}))
        req=by_id[rid];source=route.get('chosen_source') or route.get('source')
        moved=(source is not None and source not in [req['source']]+req.get('source_alternatives',[])) or (
            route.get('destination') is not None and route['destination']!=req['destination'])
        points=route.get('points_mm') or []
        if len(points)>=2:
            moved=moved or g.distance(points[0],(source or req['source'])['xyz_mm'])>.01 or g.distance(points[-1],req['destination']['xyz_mm'])>.01
        if moved:route.update(status='NOT_EVALUATED',reason='PRESERVED_ROUTE_OUTDATED')
        elif len(points)<2:route['status']='NOT_EVALUATED'
        else:
            local_profile=make_profile(for_route(settings,rid))
            route.update(validate_route_geometry(protected_space,route,local_profile))
            if cooperative.usable(route):protected_space.reserve(rid,_reserved_points(route),local_profile.radius_mm)
        protected_routes[rid]=route
    diagnostics={rid:copy.deepcopy(route) for rid,route in protected_routes.items() if not cooperative.usable(route)}
    validation_settings=copy.deepcopy(settings)
    validation_settings['constraints']['locked_routes']=sorted(rid for rid in locked if cooperative.usable(protected_routes.get(rid,{})))
    def validator(network):
        report=validate_network(space,requests,validation_settings,network)
        for rid in locked|preserve:
            prior=protected_routes.get(rid,{})
            if cooperative.usable(prior) and (rid not in report['routes'] or any(
                report['routes'][rid].get(k)!=prior.get(k) for k in ('points_mm',))):
                report['valid']=False;report['issues'].append({'route_id':rid,'reason':'PROTECTED_ROUTE_CHANGED'})
        return report
    manager=SearchBudgetManager(deadline,cancelled,state.get('budget'),clock=time.monotonic)
    histories=copy.deepcopy(state.get('route_history',{}))
    repository.restore_state(state.get('repository',{}))
    for rid,items in candidate_bank.items():
        for item in items:repository.publish(rid,item)
    restored=incumbent.restore_state(state['incumbent'],validator) if state.get('incumbent') else incumbent.consider(existing,validator)
    if state.get('incumbent'):
        # An explicitly supplied, revalidated checkpoint network may be newer
        # than its embedded planner state (for example after candidate repair).
        restored=incumbent.consider(existing,validator) or restored
    if not restored and existing:
        # Reconstruct jointly only after the atomic previous set failed.
        from .portfolio import select_candidates
        pools={r['route_id']:[existing[r['route_id']]] for r in requests if r['route_id'] in existing}
        repaired,_=select_candidates(space,requests,pools,settings,{},set(),manager.allocate('RESTORE',pending=max(2,len(requests))),cancelled)
        incumbent.consider(repaired,validator)
    best=incumbent.best()
    for rid,item in best.items():repository.publish(rid,item,protect=True)
    candidate_bank=repository.bank()
    if best:best_score=cooperative.score(best,by_id)
    pass_offset=state.get('next_pass',0)
    for iteration,order in enumerate(orders[:int(settings['search']['max_iterations'])]):
        phase_started=time.monotonic()
        conditional=any(p['classification']=='CONDITIONAL' for p in settings['constraints']['passages'])
        scenario='B' if settings.get('structural_crossings')=='PROPOSAL_ONLY' and conditional and (iteration%2 or settings['search']['max_iterations']==1) else 'A'
        if cancelled and cancelled():break
        if iteration and time.monotonic()-started>settings['search']['max_total_seconds']:break
        pass_count=min(len(orders),int(settings['search']['max_iterations']))
        pass_deadline=manager.allocate('PASS:'+str(iteration),pending=max(1,pass_count-iteration),consolidation=True)
        working=protected_space.clone();routes=copy.deepcopy(protected_routes)
        pending=[r for r in order if r['route_id'] not in locked|preserve]
        coordinated=settings.get('coordination',{}).get('enabled',True) and (len(pending)>1 or any(len(r.get('source_alternatives',[]))>1 for r in pending))
        planning_deadline=pass_deadline
        if coordinated:
            planning_deadline=manager.allocate('DISCOVERY',pending=2,deadline=pass_deadline)
        # Keep earlier certified routes as candidates. Subsequent passes spend
        # their planning allowance on the unresolved connections; the global
        # selector can still replace any movable route afterwards.
        if best:
            for request in pending:
                rid=request['route_id'];prior=best.get(rid,{})
                if not cooperative.usable(prior):continue
                local_profile=make_profile(for_route(settings,rid))
                checked=validate_route_geometry(working,prior,local_profile)
                if checked['status'] in ('INVALID','NOT_EVALUATED'):continue
                routes[rid]=copy.deepcopy(prior);routes[rid].update(checked)
                working.reserve(rid,_reserved_points(routes[rid]),local_profile.radius_mm)
            pending=[r for r in pending if r['route_id'] not in routes]
        from ..parallel import current
        workers=current();parallel_routes=None
        if coordinated and workers:
            jobs=[{'operation':'plan','route_id':r['route_id'],'request':dict(r,attempt_number=manager.next_attempt(r['route_id']),search_history=histories.get(r['route_id'])),'scenario':scenario} for r in pending]
            parallel_routes=workers.run(working,settings,jobs,planning_deadline,cancelled)
        for request_index,request in enumerate(pending):
            rid=request['route_id']
            if rid in locked:continue
            if cancelled and cancelled():break
            ready=parallel_routes[request_index] if parallel_routes is not None else None
            if time.monotonic()>=planning_deadline and ready is None:
                routes[rid]={'status':'NOT_EVALUATED','reason':'TOTAL_TIME_LIMIT'};continue
            remaining=max(0,planning_deadline-time.monotonic())
            route_deadline=manager.allocate('ROUTE:'+rid,pending=max(1,len(pending)-request_index),
                consolidation=not coordinated,deadline=planning_deadline)
            route=ready
            if route is None:route=plan_route(working,dict(request,attempt_number=manager.next_attempt(rid),search_history=histories.get(rid)),profile,settings,scenario=scenario,cancelled=cancelled,deadline=route_deadline)
            if route.get('search_history'):histories[rid]=route['search_history']
            manager.record('ROUTE:'+rid,route.get('seconds',0),cooperative.usable(route),route.get('reason'))
            if cooperative.usable(route):
                # Independently generated routes can overlap one another.
                # Bank them for the joint selector, then certify compatibility
                # before accepting them into the provisional reserved set.
                discovered=copy.deepcopy(route);discovered.setdefault('chosen_source',copy.deepcopy(request['source']))
                candidate_bank.setdefault(rid,[]).append(discovered)
                checked=validate_route_geometry(working,route,make_profile(for_route(settings,rid)))
                if checked['status'] in ('INVALID','NOT_EVALUATED'):
                    route={'status':'NOT_EVALUATED','reason':'NETWORK_CONFLICT_NOT_RESOLVED'}
                else:route.update(checked)
            routes[rid]=route
            if route['status'] not in ('INVALID','NOT_EVALUATED'):working.reserve(rid,_reserved_points(route),make_profile(for_route(settings,rid)).radius_mm)
        for rid,items in (workers.late_candidates.items() if workers else []):
            candidate_bank.setdefault(rid,[]).extend(items)
        if workers:workers.late_candidates.clear()
        for rid,item in routes.items():
            if not cooperative.usable(item):diagnostics[rid]=copy.deepcopy(item)
        negotiations=[];portfolio_report=None
        if coordinated and time.monotonic()<pass_deadline:
            repair_deadline=time.monotonic()+(pass_deadline-time.monotonic())*.25
            routes,negotiations=cooperative.negotiate(space,routes,requests,settings,locked|preserve,plan_route,repair_deadline,cancelled)
            if time.monotonic()<pass_deadline and settings['coordination']['max_candidates']>1:
                from .portfolio import coordinate
                # Keep successful discoveries from every order. A later pass
                # may complete the set by combining routes found earlier.
                for rid,route in routes.items():
                    if cooperative.usable(route):
                        stored=candidate_bank.setdefault(rid,[])
                        if not any(old.get('chosen_source')==route.get('chosen_source') and old['points_mm']==route['points_mm'] for old in stored):stored.append(copy.deepcopy(route))
                if cooperative.score(best,by_id)<cooperative.score(routes,by_id):routes=copy.deepcopy(best)
                routes,portfolio_report=coordinate(space,routes,requests,settings,locked|preserve,plan_route,
                    time.monotonic()+(pass_deadline-time.monotonic())*.9,cancelled,candidate_bank,pass_index=pass_offset+iteration)
            working=space.clone()
            for rid,route in routes.items():
                if cooperative.usable(route):working.reserve(rid,_reserved_points(route),make_profile(for_route(settings,rid)).radius_mm)
        from ..pressure import assess
        for rid,route in routes.items():
            if cooperative.usable(route):
                route['section']=for_route(settings,rid)['section']
                route['pressure_assessment']=assess(route,by_id[rid],settings)
        score=cooperative.score(routes,by_id);missing=score[0];total=score[3]
        iterations.append({'iteration':iteration,'scenario':scenario,'order':[r['route_id'] for r in order],'unresolved':missing,'cost':total,'rip_up':iteration>0,
                           'coordination':negotiations,'portfolio':portfolio_report,
                           'accepted_exchanges':sum(e['accepted'] for e in negotiations)})
        for rid,items in candidate_bank.items():
            for item in items:repository.publish(rid,item)
        for rid,item in routes.items():repository.publish(rid,item)
        for rid,item in routes.items():
            if not cooperative.usable(item):diagnostics[rid]=copy.deepcopy(item)
        improved=incumbent.consider(routes,validator)
        manager.record('DISCOVERY',time.monotonic()-phase_started,improved)
        if improved:
            best=incumbent.best();best_score=cooperative.score(best,by_id)
            best_space=working;best_coordination=negotiations;best_portfolio=portfolio_report
        repository.protected.clear()
        for rid,item in incumbent.best().items():repository.publish(rid,item,protect=True)
        candidate_bank=repository.bank()
        if missing==0 and iteration>=1:break
    result_routes=incumbent.best()
    for rid in by_id:
        if rid not in result_routes:
            result_routes[rid]=copy.deepcopy(diagnostics.get(rid,{'status':'NOT_EVALUATED',
                'reason':'CANCELLED' if cancelled and cancelled() else 'TOTAL_TIME_LIMIT' if time.monotonic()>=deadline else 'NETWORK_CONFLICT_NOT_RESOLVED'}))
    return {'routes':result_routes,'candidate_bank':repository.bank(),'planner_state':{'context':context,'next_pass':pass_offset+len(iterations),'incumbent':incumbent.export_state(),'repository':repository.export_state(),'budget':manager.export_state(),'route_history':histories},'iterations':iterations,'seconds':time.monotonic()-started,'preliminary_count':0,
            'reserved_count':len(best_space.reservations),'ignored_locks':ignored_locks,'coordination':best_coordination,'portfolio':best_portfolio,
            'objective':'complete connections, then fewer conditional crossings, then total network cost'}


def route_alternatives(space,request,profile,settings,preferred=None,cancelled=None):
    from ..parallel import Session,current
    if current() is not None:return _route_alternatives(space,request,profile,settings,preferred,cancelled)
    with Session(space,settings):return _route_alternatives(space,request,profile,settings,preferred,cancelled)


def _route_alternatives(space,request,profile,settings,preferred=None,cancelled=None):
    result=[]
    deadline=time.monotonic()+settings['search']['max_total_seconds']
    if preferred and preferred.get('status') not in ('INVALID','NOT_EVALUATED'):result.append(preferred)
    from ..parallel import current
    scenarios=('A','A','B');workers=current()
    jobs=[{'operation':'plan','route_id':request['route_id'],
           'request':dict(request,search_from_destination=index==1),'scenario':scenario,
           'diversity':[r['points_mm'] for r in result],'refine':True} for index,scenario in enumerate(scenarios)]
    replies=workers.run(space,settings,jobs,deadline,cancelled) if workers else None
    for index,scenario in enumerate(scenarios):
        if len(result)>=settings['search']['max_alternatives']:break
        candidate=replies[index] if replies is not None else None
        if candidate is None:
            if time.monotonic()>=deadline:continue
            candidate=plan_route(space,request,profile,settings,scenario,cancelled,[r['points_mm'] for r in result],deadline)
        if candidate.get('status') in ('INVALID','NOT_EVALUATED'):continue
        points=candidate['points_mm']
        different=all(any(min(g.point_segment(a,b,p) for a,b in zip(other['points_mm'],other['points_mm'][1:]))>max(profile.radius_mm*2,settings['search']['fine_resolution_mm']*2) for p in points) for other in result)
        if different or not result:result.append(candidate)
    return result
