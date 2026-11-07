"""Stage 2: physical takes, joint lanes and editable, checked tube axes."""
import copy
import itertools
import math
import time
import hashlib
import json
from dataclasses import asdict
from shapely.geometry import LineString,mapping,shape
from . import planner,schematic
from .. import settings
from ..core import geometry as g
from ..core.integer_master import solve
from ..core.route_geometry import validate_route_geometry,primitives
from ..core.natural_path import straight_vertices
from ..core.spatial_model import build_spatial_model
from ..core.cooperative import overlaps


def _offset_vertices(path,offsets):
    points=path['points_mm'];result=[]
    for index,point in enumerate(points):
        normals=[];distances=[]
        for edge_index in (index-1,index):
            if not 0<=edge_index<len(path['edge_ids']):continue
            a,b=path['oriented_edges'][path['edge_ids'][edge_index]];dx=b[0]-a[0];dy=b[1]-a[1];length=math.hypot(dx,dy)
            if length<1e-5:continue
            normals.append((-dy/length,dx/length));distances.append(offsets.get(path['edge_ids'][edge_index],0.))
        if not normals:result.append(list(point));continue
        normal=[sum(v[i] for v in normals) for i in (0,1)];length=math.hypot(*normal)
        if length<1e-6:normal=list(normals[0]);length=1
        normal=[v/length for v in normal];projection=max(.25,sum(a*b for a,b in zip(normal,normals[0])))
        # Retain the full incoming lane spacing at a departure. Averaging a
        # shared-lane offset with the single-branch zero offset halves the
        # separation exactly where tubes begin to split.
        shift=max(distances,key=abs)/projection
        result.append([point[0]+normal[0]*shift,point[1]+normal[1]*shift,point[2]])
    return result


def _lanes(p,profile,gap):
    edge_users={}
    for tid,path in p.selected_paths.items():
        for eid in path['edge_ids']:
            if p.corridor_graph['edges'][eid].get('connection_stub'):continue
            edge_users.setdefault(eid,[]).append(tid)
    # One stable rank maintains relative lateral order while routes share
    # an edge; no automatic vertical crossing is used to exchange lanes.
    endpoints={tid:path['endpoint']['origin_xyz_mm'] for tid,path in p.selected_paths.items()}
    order=sorted(endpoints,key=lambda t:(endpoints[t][0],endpoints[t][1],t));rank={t:i for i,t in enumerate(order)}
    offsets={t:{} for t in endpoints};bundles={};diameter=profile.external_diameter_mm+2*profile.insulation_thickness_mm
    for eid,users in edge_users.items():
        users=sorted(set(users),key=rank.get);width=len(users)*diameter+max(0,len(users)-1)*gap
        edge=p.corridor_graph['edges'][eid];available=edge['width_mm']-2*p.configuration['design_clearance_mm']
        positions={tid:-width/2+diameter/2+i*(diameter+gap) for i,tid in enumerate(users)}
        canonical=g.sub(edge['points_mm'][1],edge['points_mm'][0])
        for tid,value in positions.items():
            a,b=p.selected_paths[tid]['oriented_edges'][eid]
            offsets[tid][eid]=value*(1 if g.dot(g.sub(b,a),canonical)>=0 else -1)
        bid='bundle:'+eid;bundles[bid]={'bundle_id':bid,'edge_ids':[eid],'route_ids':['axis:'+t for t in users],
            'lane_order':['axis:'+t for t in users],'lane_offsets':{'axis:'+t:value for t,value in positions.items()},
            'required_width_mm':width,'available_width_mm':available,
            'validation_status':'CORRIDOR_CAPACITY_CONFLICT' if width>available+1e-4 else 'STRAIGHT_CAPACITY_ESTIMATED'}
    return offsets,bundles


def _assign(p,profile,cfg,offsets,deadline,cancelled):
    admitted={};target_guides={}
    for tid,path in p.selected_paths.items():
        guide=_offset_vertices(path,offsets[tid]);target_guides[tid]=guide
        source_equipment=p.equipment[path['network_type']];choices=[]
        for available in source_equipment['available_ports']:
            port=available['port']
            locked=p.individual_routes.get('axis:'+tid) if p.constraints.get('axis_locks',{}).get('axis:'+tid) else None
            if locked and port['id']!=locked.get('source',{}).get('id'):continue
            if port['connected'] or port['id'] not in path['compatible_source_ports']:continue
            if abs(port['size']['diameter_mm']-path['endpoint']['nominal_diameter_mm'])>.1:continue
            target=guide[min(1,len(guide)-1)];normal=g.unit(port['direction']);delta=g.sub(target,port['xyz_mm'])
            orientation=1-g.dot(normal,g.unit(delta)) if g.norm(delta)>1e-5 else 0
            previous=p.individual_routes.get('axis:'+tid,{})
            keep=previous.get('source',{}).get('id')==port['id'] and previous.get('primitives')
            choices.append({'status':'VALID','source':copy.deepcopy(port),'guide':guide,'lane_offsets':offsets[tid],
                'cost':{'total':g.distance(port['xyz_mm'],target)/1000+orientation*2+(0 if keep or not previous.get('primitives') else 20)}})
        admitted[tid]=choices
    resources=[]
    for pid in {c['source']['id'] for choices in admitted.values() for c in choices}:
        resources.append({'capacity':1,'demands':{(tid,i):1 for tid,cs in admitted.items() for i,c in enumerate(cs) if c['source']['id']==pid}})
    def crossing(a,ia,b,ib):
        ca,cb=admitted[a][ia],admitted[b][ib]
        if p.selected_paths[a]['manifold_id']!=p.selected_paths[b]['manifold_id']:return False
        # Pair-dependent fan-out ordering uses separate lane-entry targets.
        pa,pb=ca['source']['xyz_mm'],cb['source']['xyz_mm'];qa=ca['guide'][min(1,len(ca['guide'])-1)];qb=cb['guide'][min(1,len(cb['guide'])-1)]
        if math.dist(qa,qb)<1:return False
        return abs(pa[2]-pb[2])<profile.external_diameter_mm and LineString([pa[:2],qa[:2]]).crosses(LineString([pb[:2],qb[:2]]))
    chosen,report=solve(admitted,list(admitted),crossing,deadline,cancelled,resources=resources)
    return {tid:admitted[tid][i] for tid,i in (chosen or {}).items()},report


def _band_space(p,path,profile):
    data=copy.deepcopy(p.snapshot)
    from shapely.geometry import MultiPoint,Point
    from shapely.ops import unary_union
    bands=[]
    for eid in path['edge_ids']:
        edge=p.corridor_graph['edges'][eid];a,b=path['oriented_edges'][eid]
        if math.dist(a[:2],b[:2])<1e-5:continue
        half=max(profile.external_diameter_mm,edge.get('width_mm',400)/2)
        bands.append(LineString([a[:2],b[:2]]).buffer(half,cap_style=2))
    if not bands:bands=[LineString([q[:2] for q in path['points_mm']]).buffer(max(200,profile.external_diameter_mm),cap_style=2)]
    # A bottleneck applies to its own section, never to the whole route.
    # Junction manoeuvres remain local; the original 3D space/obstacles and
    # exact authorised bores still decide every full-radius motion.
    bands.extend(Point(*q[:2]).buffer(profile.minimum_bend_radius_mm*1.8+profile.external_diameter_mm) for q in path['points_mm'][1:-1])
    corridor=unary_union(bands)
    # Physical takes occupy different positions from the common schematic
    # origin. Reserve the real distributor exit region for the fan-out;
    # clipping every take to the single reference line makes all but one
    # source unreachable and prevents finite-radius return turns.
    manifold=p.equipment[path['network_type']]
    exits=[port['port']['xyz_mm'][:2] for port in manifold['available_ports']]
    if exits:corridor=corridor.union(MultiPoint(exits).convex_hull.buffer(profile.minimum_bend_radius_mm*3+profile.external_diameter_mm))
    corridor=corridor.union(Point(*path['endpoint']['origin_xyz_mm'][:2]).buffer(profile.minimum_bend_radius_mm*2+profile.external_diameter_mm))
    regions=[]
    for region in data['regions']:
        if 'polygon' in region:polygon=shape(region['polygon'])
        else:
            from shapely.geometry import box
            b=region['box_mm'];polygon=box(b[0],b[1],b[3],b[4])
        clipped=polygon.intersection(corridor)
        if clipped.is_empty:continue
        parts=list(clipped.geoms) if hasattr(clipped,'geoms') else [clipped]
        for part in parts:
            if part.geom_type=='Polygon':
                record=dict(region,polygon=mapping(part));record.pop('box_mm',None);regions.append(record)
    data['regions']=regions
    return build_spatial_model(data,p.configuration['constraints'])


def _check(space,points,source,destination,profile,passages,cfg):
    arm=max(profile.minimum_bend_radius_mm*2,cfg['connection_straight_mm'],profile.external_diameter_mm*1.5)
    stubs=[{'points_mm':[source['xyz_mm'],list(g.add(source['xyz_mm'],g.mul(g.unit(source['direction']),arm)))],
            'ignore_owner_ids':[source['element_key']]},
           {'points_mm':[destination['xyz_mm'],list(g.add(destination['xyz_mm'],g.mul(g.unit(destination['direction']),arm)))],
            'ignore_owner_ids':[destination['element_key']]}]
    candidate={'points_mm':[list(q) for q in points],'endpoint_stubs':stubs,'passages':passages,'split_passages':True}
    candidate.update(validate_route_geometry(space,candidate,settings.profile(cfg)))
    if candidate['status']!='INVALID':
        pieces=candidate.get('primitives',[])
        def tangent(piece,at_end):
            if piece['kind']=='LINE':return g.unit(g.sub(piece['end'],piece['start']))
            point=piece['end'] if at_end else piece['start'];return g.unit(g.cross(piece['axis'],g.sub(point,piece['center'])))
        if not pieces or g.dot(tangent(pieces[0],False),g.unit(source['direction']))<.99999 or g.dot(tangent(pieces[-1],True),g.mul(g.unit(destination['direction']),-1))<.99999:
            candidate.update(status='INVALID',reason='CONNECTOR_APPROACH_CONFLICT',errors=['CONNECTOR_TANGENCY_MISMATCH'])
    return candidate


def _constructive_legs(p,path,choice,profile,cfg,space,first,last,deadline,cancelled,guard_factor=.25):
    """Solve short oriented legs between protected straight wall approaches."""
    source=choice['source'];destination=path['destination'];radius=settings.profile(cfg).radius_mm
    portals={v['id']:v for v in p.corridor_graph['portals']};crossings=[]
    for eid in path['edge_ids']:
        edge=p.corridor_graph['edges'][eid];pid=edge.get('portal_id')
        if not pid:continue
        portal=copy.deepcopy(portals[pid]);a,b=copy.deepcopy(path['oriented_edges'][eid]);normal=g.unit(g.sub(b,a))
        lateral=(-normal[1],normal[0],0);value=choice.get('lane_offsets',{}).get(eid,0.)
        shift=g.mul(lateral,value);a=list(g.add(a,shift));b=list(g.add(b,shift))
        if abs(value)>1e-5:
            import hashlib
            original=portal['id'];portal.update(id=original+':lane:'+hashlib.sha256(repr((a,b)).encode()).hexdigest()[:10],parent_portal_id=original,points_mm=[a,b],
                approval_status='PENDING_TECHNICAL_APPROVAL' if portal['classification']=='CONDITIONAL' else 'CONSTRUCTION_REVIEW')
        else:portal['points_mm']=[a,b]
        if space.collisions(a,b,radius,portal['element_ids']) or space.forbidden_hit(a,b,radius):return None,{'reason':'LANE_PASSAGE_BLOCKED','portal_id':pid}
        # A horizontal bore can be usable only at its two ends while a
        # tangent approach hits a sloping ceiling. Follow candidate bounding
        # plane slopes, retaining the same wall station and lateral lane.
        pitches={0.}
        for region in space.regions:
            if any(region.polygon.distance(__import__('shapely.geometry',fromlist=['Point']).Point(*q[:2]))<radius for q in (a,b)):
                for plane in (region.lower_coefficients,region.upper_coefficients):pitches.add(round(plane[0]*normal[0]+plane[1]*normal[1],8))
        fitted=None;mid=g.mul(g.add(a,b),.5);half=g.distance(a,b)/2
        # Begin at the certified bore collars. Extra mandatory lead would
        # push a turn into nearby equipment; tangent-circle/roadmap seeds
        # supply their own finite straight approach before this collar.
        for factor in dict.fromkeys((guard_factor,.25,.5,0.,.75,1.15)):
            for pitch,vertical in itertools.product(sorted(pitches,key=abs),(0.,-25.,25.,-50.,50.)):
                axis=g.unit((normal[0],normal[1],pitch));aa=list(g.sub(mid,g.mul(axis,half)));bb=list(g.add(mid,g.mul(axis,half)))
                aa[2]+=vertical;bb[2]+=vertical
                before=list(g.sub(aa,g.mul(axis,cfg['bend_radius_mm']*factor)));after=list(g.add(bb,g.mul(axis,cfg['bend_radius_mm']*factor)))
                if not space.free(before,before,radius) or not space.free(after,after,radius):continue
                if space.collisions(aa,bb,radius,portal['element_ids']) or space.forbidden_hit(aa,bb,radius):continue
                fitted=(aa,bb,before,after);break
            if fitted:break
        if not fitted:return None,{'reason':'PORTAL_BEND_APPROACH_CONFLICT','portal_id':pid}
        a,b,before,after=fitted
        if max(g.distance(q,v) for q,v in zip((a,b),portal['points_mm']))>.1:
            import hashlib
            original=portal.get('parent_portal_id',pid)
            portal.update(id=original+':axis:'+hashlib.sha256(repr((a,b)).encode()).hexdigest()[:10],parent_portal_id=original,
                approval_status='PENDING_TECHNICAL_APPROVAL' if portal['classification']=='CONDITIONAL' else 'CONSTRUCTION_REVIEW')
        portal['points_mm']=[a,b]
        crossings.append((before,after,portal))
    endpoints_to_connect=[(source['xyz_mm'],first)]
    for before,after,portal in crossings:endpoints_to_connect.append((before,after))
    endpoints_to_connect.append((last,destination['xyz_mm']))
    band=_band_space(p,path,profile);band.reservations=list(space.reservations);all_points=[source['xyz_mm']];attempts=[]
    used=[portal for _,_,portal in crossings]
    portal_indices=[j for j,eid in enumerate(path['edge_ids']) if p.corridor_graph['edges'][eid].get('portal_id')]
    for i in range(len(endpoints_to_connect)-1):
        if time.monotonic()>=deadline or cancelled and cancelled():return None,{'reason':'SEARCH_BUDGET_EXHAUSTED','legs':attempts}
        previous,start=endpoints_to_connect[i];goal,following=endpoints_to_connect[i+1]
        adjacent=[crossings[j][2] for j in (i-1,i) if 0<=j<len(crossings)]
        stubs=_check(space,[source['xyz_mm'],first,last,destination['xyz_mm']],source,destination,profile,used,cfg)['endpoint_stubs']
        direct={'points_mm':straight_vertices([previous,start,goal,following]),'passages':adjacent,'endpoint_stubs':stubs,'split_passages':True}
        checked=validate_route_geometry(space,direct,settings.profile(cfg))
        controls=[start,goal];reference=[]
        if checked['status']=='INVALID' and choice.get('guide'):
            lo=portal_indices[i-1]+2 if i else 1;hi=portal_indices[i] if i<len(portal_indices) else len(choice['guide'])-1
            reference=[start]+copy.deepcopy(choice['guide'][lo:hi])+[goal]
            # Keep the selected spatial organisation as a seed; shorten only
            # continuously free spans within this one approved portal leg.
            compressed=[start];j=0
            while j<len(reference)-1:
                k=len(reference)-1
                while k>j+1 and not space.free(reference[j],reference[k],radius):k-=1
                compressed.append(reference[k]);j=k
            for seed in (compressed,reference):
                proposed=straight_vertices([previous]+seed+[following])
                checked=validate_route_geometry(space,dict(direct,points_mm=proposed),settings.profile(cfg))
                if checked['status']!='INVALID':controls=seed;break
        if checked['status']=='INVALID':
            from ..core.oriented_path import seeds
            for padding in (1.,1.1,1.5):
                if time.monotonic()>=deadline or cancelled and cancelled():break
                for seed in seeds(start,goal,g.sub(start,previous),g.sub(following,goal),cfg['bend_radius_mm']*padding):
                    if time.monotonic()>=deadline or cancelled and cancelled():break
                    proposed=straight_vertices([previous]+seed+[following])
                    candidate=dict(direct,points_mm=proposed)
                    checked=validate_route_geometry(space,candidate,settings.profile(cfg))
                    if checked['status']!='INVALID':controls=seed;break
                if checked['status']!='INVALID':break
        if checked['status']=='INVALID':
            from ..core.incremental_roadmap import search
            local=copy.deepcopy(cfg);local['constraints']['passages']=adjacent
            local['search'].update(approach_source_mm=previous,approach_destination_mm=following,endpoint_stubs=stubs,split_passages=True,seed_points_mm=reference)
            allowance=max(.1,deadline-time.monotonic())/max(1,len(endpoints_to_connect)-1-i)
            found=search(band,start,goal,settings.profile(cfg),local,'B',min(deadline,time.monotonic()+allowance),cancelled)
            attempts.append({'leg':i,'algorithm':'ORIENTED_LOCAL_ROADMAP','status':found['status'],'reason':found.get('reason')})
            if found.get('status') not in ('VALID','VALID_WITH_WARNINGS','CONDITIONAL'):return None,{'reason':found.get('reason','LOCAL_LEG_UNRESOLVED'),'legs':attempts}
            controls=found['points_mm'][1:-1]
        else:attempts.append({'leg':i,'algorithm':'DIRECT_ORIENTED_LEG','status':checked['status']})
        for q in controls+[following]:
            if not all_points or g.distance(q,all_points[-1])>1e-4:all_points.append(list(q))
    result=_check(space,straight_vertices(all_points),source,destination,profile,used,cfg)
    return result,{'reason':'ORIENTED_PORTAL_AND_CONNECTOR_APPROACHES','guard_factor':guard_factor,'legs':attempts,'status':result['status'],'errors':result.get('errors',[])[:3]}


def _route(p,tid,path,choice,profile,cfg,space,deadline,cancelled):
    source=choice['source'];destination=path['destination'];guide=copy.deepcopy(choice['guide']);arm=max(cfg['bend_radius_mm'],cfg['connection_straight_mm'])
    from .spatial import valid_height_intervals
    protected=[q for portal in p.corridor_graph['portals'] if portal['id'] in path['portal_ids'] for q in portal['points_mm']]
    total=sum(g.distance(a,b) for a,b in zip(guide,guide[1:]));station=0.
    for i,q in enumerate(guide):
        if i:station+=g.distance(guide[i-1],q)
        if i in (0,len(guide)-1) or any(g.distance(q,v)<1e-4 for v in protected):continue
        # Stage 1 already supplied a continuously free 3D witness. Preserve
        # its height through roof ridges and ceiling steps; a linear
        # endpoint interpolation can cut below an intermediate ceiling even
        # when every endpoint separately has an admissible height.
        preferred=q[2]
        bands=valid_height_intervals(space,*q[:2],profile,cfg['clearance_mm'])
        if bands:q[2]=min((min(hi,max(lo,preferred)) for lo,hi in bands),key=lambda z:abs(z-preferred))
    first=list(g.add(source['xyz_mm'],g.mul(g.unit(source['direction']),arm)))
    end_arm=max(arm,cfg['bend_radius_mm']+settings.profile(cfg).radius_mm)
    last=list(g.add(destination['xyz_mm'],g.mul(g.unit(destination['direction']),end_arm)))
    interior=[q for q in guide[1:-1] if min(g.distance(q,source['xyz_mm']),g.distance(q,destination['xyz_mm']))>arm*1.2]
    manual=p.constraints.get('axis_waypoints',{}).get(path.get('route_id','axis:'+tid))
    if manual is not None:interior=[v['xyz_mm'] for v in manual]
    points=straight_vertices([source['xyz_mm'],first]+interior+[last,destination['xyz_mm']])
    used=[q for q in p.corridor_graph['portals'] if q['id'] in path['portal_ids']]
    result=_check(space,points,source,destination,profile,used,cfg);attempts=[{'algorithm':'DIRECT_GEOMETRY','reason':'APPROVED_SCHEMATIC_AND_LANES','status':result['status']}]
    if result['status']=='INVALID' and not manual and time.monotonic()<deadline:
        phase_deadline=min(deadline,time.monotonic()+max(1.,(deadline-time.monotonic())*.65))
        factors=(.25,0.,1.15) if path['portal_ids'] else (.25,)
        for i,factor in enumerate(factors):
            if time.monotonic()>=phase_deadline or cancelled and cancelled():break
            local_deadline=time.monotonic()+(phase_deadline-time.monotonic())/(len(factors)-i)
            constructive,record=_constructive_legs(p,path,dict(choice,guide=guide),profile,cfg,space,first,last,local_deadline,cancelled,factor)
            attempts.append(dict(record,algorithm='CONSTRUCTIVE_LOCAL_LEGS',guard_factor=factor))
            if constructive and constructive['status']!='INVALID':result=constructive;break
    if result['status']=='INVALID' and not manual:
        # A take can face away from the approved corridor. Joining a fixed
        # connector stub directly to that corridor creates a near-180-degree
        # vertex, whose trim is unbounded. Try finite-radius return fan-outs
        # before asking a search engine to repair the connector organisation.
        u=g.unit(source['direction']);target=points[min(2,len(points)-1)];delta=g.sub(target,source['xyz_mm'])
        if g.dot(u,g.unit(delta))<-.25:
            side=g.sub(delta,g.mul(u,g.dot(delta,u)))
            if g.norm(side)<1e-5:side=g.cross(u,(0,0,1))
            if g.norm(side)>1e-5:
                side=g.unit(side);tested=0
                for sign,lead,span in itertools.product((1,-1),(1.15,2.),(2.7,3.5,4.5)):
                    if time.monotonic()>=deadline or cancelled and cancelled():break
                    a=g.add(source['xyz_mm'],g.mul(u,lead*cfg['bend_radius_mm']))
                    b=g.add(a,g.mul(side,sign*span*cfg['bend_radius_mm']))
                    controls=straight_vertices([source['xyz_mm'],a,b]+points[2:]);candidate=_check(space,controls,source,destination,profile,used,cfg);tested+=1
                    if candidate['status']!='INVALID':result=candidate;break
                attempts.append({'algorithm':'DIRECT_RETURN_FANOUT','reason':'SOURCE_NORMAL_OPPOSES_CORRIDOR','tested':tested,'status':result['status']})
    band=None
    if result['status']=='INVALID' and time.monotonic()<deadline and not manual:
        band=_band_space(p,path,profile);from ..core.astar_3d import find_route_3d
        local=dict(cfg['search'],max_seconds=min(2.,max(.05,deadline-time.monotonic())),max_nodes=min(cfg['search']['max_nodes'],25000),
            local_bounds_mm=g.expand(g.bounds(path['points_mm']+[first,last]),max(300,arm)),
            start_direction=source['direction'],start_straight_mm=arm,end_direction=g.mul(destination['direction'],-1),end_straight_mm=end_arm)
        candidate=find_route_3d(band,first,last,settings.profile(cfg),local,cfg['search']['fine_resolution_mm'],used,'B',cancelled)
        if candidate.get('points_mm'):
            tested=_check(space,straight_vertices([source['xyz_mm']]+candidate['points_mm']+[destination['xyz_mm']]),source,destination,profile,used,cfg)
            if tested['status']!='INVALID':result=tested
        attempts.append({'algorithm':'LOCAL_ASTAR','reason':'LOCAL_GEOMETRIC_PROBLEM_IN_APPROVED_CORRIDOR','status':result['status'],
            'search_status':candidate.get('status'),'search_reason':candidate.get('reason'),'expanded':candidate.get('expanded')})
    if result['status']=='INVALID' and deadline-time.monotonic()>2 and not manual:
        from ..core.incremental_roadmap import search
        band=band or _band_space(p,path,profile)
        oriented=copy.deepcopy(cfg);oriented['constraints']['passages']=used
        oriented['search'].update(approach_source_mm=source['xyz_mm'],approach_destination_mm=destination['xyz_mm'],endpoint_stubs=result.get('endpoint_stubs',[]),seed_points_mm=guide)
        remaining=deadline-time.monotonic();roadmap_deadline=time.monotonic()+remaining*(.55 if remaining>4 else 1.)
        candidate=search(band,first,last,settings.profile(cfg),oriented,'B',roadmap_deadline,cancelled)
        if candidate.get('points_mm'):
            tested=_check(space,straight_vertices([source['xyz_mm']]+candidate['points_mm']+[destination['xyz_mm']]),source,destination,profile,used,cfg)
            if tested['status']!='INVALID':result=tested
        attempts.append({'algorithm':'INCREMENTAL_ROADMAP','reason':'LOCAL_GRID_DID_NOT_RESOLVE_APPROVED_CORRIDOR','status':result['status'],
            'search_status':candidate.get('status'),'search_reason':candidate.get('reason'),'roadmap_nodes':candidate.get('roadmap_nodes')})
    if result['status']=='INVALID' and not manual and deadline-time.monotonic()>1.5:
        band=band or _band_space(p,path,profile);local=copy.deepcopy(cfg);local['constraints']['passages']=used
        sloping=any(abs(v)>1e-6 for r in band.regions for plane in (r.lower_coefficients,r.upper_coefficients) for v in plane[:2])
        if sloping:
            from ..core.iris_gcs import find_gcs
            candidate=find_gcs(band,first,last,settings.profile(cfg),local,'B',cancelled,deadline)
            engine='IRIS_GCS';reason='SLOPING_HEIGHT_BANDS_WITH_UNRESOLVED_LOCAL_GEOMETRY'
        else:
            from ..core.rrt_connect import discover
            corridor={'edges':[{'kind':'PORTAL','points_mm':path['oriented_edges'][eid],'portal_id':p.corridor_graph['edges'][eid]['portal_id']}
                for eid in path['edge_ids'] if p.corridor_graph['edges'][eid].get('portal_id')],
                'region_indices':list(range(len(band.regions))),'portal_ids':path['portal_ids']}
            candidate=discover(band,first,last,settings.profile(cfg),local,deadline,cancelled,[corridor],cfg.get('random_seed',0))
            engine='RRT_CONNECT';reason='NONCONVEX_LOCAL_FREE_SPACE_WITH_UNRESOLVED_GEOMETRY'
        if candidate.get('points_mm'):
            tested=_check(space,straight_vertices([source['xyz_mm']]+candidate['points_mm']+[destination['xyz_mm']]),source,destination,profile,used,cfg)
            if tested['status']!='INVALID':result=tested
        attempts.append({'algorithm':engine,'reason':reason,'status':result['status'],'search_status':candidate.get('status'),'search_reason':candidate.get('reason')})
    result.update(route_id=path.get('route_id','axis:'+tid),route_kind=path.get('route_kind','BRANCH'),terminal_id=tid,network_type=path['network_type'],source_manifold_id=path['manifold_id'],
        source=source,destination=destination,destination_endpoint=path['endpoint'],profile=asdict(profile),
        section=cfg['section'],clearance_mm=cfg['clearance_mm'],bend_radius_mm=profile.minimum_bend_radius_mm,construction_bend_radius_mm=cfg['bend_radius_mm'],
        corridor_ids=p.corridor_assignments.get(tid,[]),level_id=path.get('level_id',p.terminals.get(tid,{}).get('level_id')),search_attempts=attempts,
        constraint_type='PREFERRED_WAYPOINT',validation_status='GEOMETRICALLY_VALID' if result['status'] in ('VALID','VALID_WITH_WARNINGS') else result['status'],
        native_validation_status='NOT_EVALUATED',connection_status='NOT_MODELED')
    return result


def _mains(p,space,deadline,cancelled):
    from .model import DuctProfile
    from .endpoints import endpoint
    from ..connector_labels import HRV_FUNCTIONS
    devices=[d for d in p.catalog['devices'] if str(p.configuration.get('hrv_id')) in (d['id'],str(d['element_id']))]
    result={}
    for network,manifold in p.equipment.items():
        rid='main:'+network;failure={'route_id':rid,'route_kind':'MAIN','network_type':network,'points_mm':[],'status':'NOT_EVALUATED','validation_status':'HRV_CONNECTOR_UNRESOLVED'}
        result[rid]=failure
        if len(devices)!=1:continue
        ports=[port for port in devices[0]['connectors'] if (HRV_FUNCTIONS.get((port.get('classification'),port.get('flow_direction'))) or [None])[0]==network]
        if len(ports)!=1:continue
        source=ports[0];destination=manifold['main_port']
        if source['connected'] or destination['connected']:failure['validation_status']='MAIN_CONNECTOR_OCCUPIED';continue
        specification=p.configuration.get('main_profile')
        if specification:profile=DuctProfile(**dict(specification,network_type=network))
        else:
            main=p.configuration.get('main_connection',{});diameter=main.get('diameter_mm',125)
            if not main.get('outer_diameter_mm') or not main.get('bend_radius_mm'):
                failure['validation_status']='MAIN_PROFILE_REQUIRED';continue
            # Explicit project envelope, never labelled a verified product.
            profile=DuctProfile('project-main-envelope',network,diameter,None,
                main['outer_diameter_mm'],main.get('insulation_mm',0),main['bend_radius_mm'],None,False)
        cfg=planner.configuration(p,profile);cfg['main_connection']=dict(cfg.get('main_connection',{}),kind='RIGID_CIRCULAR')
        points=[source['xyz_mm'],destination['xyz_mm']];level=next(iter(p.terminals.values()))['level_id']
        path={'route_id':rid,'route_kind':'MAIN','points_mm':points,'edge_ids':[],'portal_ids':[],
            'oriented_edges':{},'endpoint':asdict(endpoint(destination,network)),'destination':destination,
            'network_type':network,'manifold_id':manifold['id'],'level_id':level}
        choice={'source':source,'guide':points}
        result[rid]=_route(p,rid,path,choice,profile,cfg,space,deadline,cancelled)
        result[rid]['construction_kind']='RIGID_CIRCULAR'
    return result


def _input_signature(p,path,choice,profile,cfg):
    rid=path.get('route_id','axis:'+path['terminal_id'])
    decision={key:choice.get(key) for key in ('source','guide','lane_offsets')}
    return hashlib.sha256(json.dumps([p.geometry_revision,path,decision,asdict(profile),cfg,
        p.constraints.get('axis_waypoints',{}).get(rid),p.constraints.get('axis_locked_segments',{}).get(rid),
        p.equipment],sort_keys=True,allow_nan=False).encode()).hexdigest()


def calculate(p,profile,options=None,cancelled=None):
    if not p.selected_paths:raise ValueError('Seleccione primero una propuesta con conexiones completas.')
    if any(r.get('status')=='EDITED_PENDING_RECHECK' for r in p.connectivity.values()):raise ValueError('Recalcule los corredores editados antes de calcular ejes.')
    schematic.check(p);options=options or {};started=time.monotonic();deadline=started+float(options.get('seconds',120))
    from .equipment import transform
    working=transform(p)
    cfg=planner.configuration(p,profile)
    # Live Hermite tests show overshoot at line/arc curvature changes. This
    # construction allowance is explicit; native certification still checks
    # the manufacturer's minimum, and can reject the generated spline.
    cfg['bend_radius_mm']=profile.minimum_bend_radius_mm*1.2
    space=build_spatial_model(working.snapshot,cfg['constraints'])
    offsets,bundles=_lanes(p,profile,cfg['clearance_mm']);assigned,assignment=_assign(p,profile,cfg,offsets,started+min(5,(deadline-started)*.15),cancelled)
    # Keep a bounded final slice for mains, conflict separation and repair.
    routing_deadline=deadline-min(5.,max(.5,(deadline-started)*.12))
    routes={};valid=[];reused=[]
    conflicts={rid for bundle in bundles.values() if bundle['validation_status']=='CORRIDOR_CAPACITY_CONFLICT' for rid in bundle['route_ids']}
    for index,(tid,path) in enumerate(p.selected_paths.items()):
        rid='axis:'+tid
        preserved=p.individual_routes.get(rid)
        signature=_input_signature(p,path,assigned[tid],profile,cfg) if tid in assigned else None
        locked=p.constraints.get('axis_locks',{}).get(rid)
        compatible=preserved and signature and preserved.get('input_signature')==signature and preserved.get('primitives')
        if preserved and (locked or compatible) and len(preserved.get('points_mm',[]))>=2:
            route=copy.deepcopy(preserved)
            if compatible:route['status']=route.get('independent_status',route['status'])
            if locked and not compatible:
                route.update(_check(space,route['points_mm'],route['source'],route['destination'],profile,route['passages'],cfg))
                if tid not in assigned or route['source']!=assigned[tid]['source'] or route['destination']!=path['destination']:
                    route.update(status='INVALID',reason='LOCKED_AXIS_REAL_ENDPOINT_CHANGED',errors=['LOCKED_AXIS_REAL_ENDPOINT_CHANGED'])
                route.update(profile=asdict(profile),section=cfg['section'],clearance_mm=cfg['clearance_mm'],bend_radius_mm=profile.minimum_bend_radius_mm)
            route['validation_status']='GEOMETRICALLY_VALID' if route['status'] in ('VALID','VALID_WITH_WARNINGS') else route['status']
            if compatible:reused.append(rid)
        elif rid in conflicts:route={'route_id':rid,'terminal_id':tid,'network_type':path['network_type'],'validation_status':'CORRIDOR_CAPACITY_CONFLICT','status':'INVALID','points_mm':[]}
        elif tid not in assigned:route={'route_id':rid,'terminal_id':tid,'network_type':path['network_type'],'validation_status':'SOURCE_PORT_ASSIGNMENT_CONFLICT','status':'INVALID','points_mm':[]}
        elif time.monotonic()>=routing_deadline or cancelled and cancelled():route={'route_id':rid,'terminal_id':tid,'network_type':path['network_type'],'validation_status':'SEARCH_BUDGET_EXHAUSTED','status':'NOT_EVALUATED','points_mm':[]}
        else:
            remaining=max(.1,routing_deadline-time.monotonic())/max(1,len(p.selected_paths)-index)
            route=_route(working,tid,path,assigned[tid],profile,cfg,space,min(routing_deadline,time.monotonic()+remaining),cancelled)
        route['input_signature']=signature
        route['independent_status']=route['status']
        if route.get('validation_status')=='GEOMETRICALLY_VALID':
            for segment in p.constraints.get('axis_locked_segments',{}).get(rid,{}).values():
                samples=[q for piece in route.get('primitives',[]) for q in piece['points_mm']]
                if any(not any(g.point_segment(a,b,q)<.1 for a,b in zip(samples,samples[1:])) for q in segment):
                    route.update(validation_status='LOCKED_SEGMENT_CONFLICT',status='INVALID')
        routes[rid]=route
        if route.get('validation_status')=='GEOMETRICALLY_VALID':valid.append(rid)
    # Independent joint check; a valid isolated route cannot hide a clash with
    # the other network. Retain the best non-conflicting set explicitly.
    mains=_mains(working,space,min(deadline-.2,time.monotonic()+2.),cancelled);all_routes=dict(routes,**mains)
    valid.extend(rid for rid,r in mains.items() if r.get('validation_status')=='GEOMETRICALLY_VALID')
    admitted={rid:[all_routes[rid]] for rid in valid}
    radii={rid:settings.effective_radius({'section':all_routes[rid]['section'],'clearance_mm':all_routes[rid]['clearance_mm']}) for rid in valid}
    locked=[rid for rid in valid if p.constraints.get('axis_locks',{}).get(rid)]
    chosen,coordination=solve(admitted,list(admitted),lambda a,ia,b,ib:overlaps(all_routes[a],radii[a],all_routes[b],radii[b]),
        min(deadline,time.monotonic()+2.),cancelled,resources=[{'capacity':-1,'demands':{(rid,0):-1}} for rid in locked])
    repairs=[]
    if chosen is not None:
        # Rebuild only an excluded unlocked branch against the accepted set.
        # Never discard a valid route to replace it with an unverified trial.
        for rid in [v for v in valid if v not in chosen and v.startswith('axis:') and v not in locked]:
            if deadline-time.monotonic()<.5 or cancelled and cancelled():break
            tid=all_routes[rid]['terminal_id'];reserved=space.clone()
            for other in chosen:
                r=all_routes[other];reserved.reserve(other,[q for piece in r['primitives'] for q in piece['points_mm']],radii[other])
            candidate=_route(working,tid,p.selected_paths[tid],assigned[tid],profile,cfg,reserved,deadline-.1,cancelled)
            accepted=candidate.get('validation_status')=='GEOMETRICALLY_VALID' and all(not overlaps(candidate,radii[rid],all_routes[other],radii[other]) for other in chosen)
            repairs.append({'route_id':rid,'accepted':accepted,'reason':'REPAIR_AGAINST_ACCEPTED_NATIVE_ENVELOPES'})
            if accepted:
                candidate['input_signature']=routes[rid].get('input_signature');routes[rid]=candidate;all_routes[rid]=candidate;chosen[rid]=0
        for rid in valid:
            if rid not in chosen:all_routes[rid].update(validation_status='JOINT_COLLISION_CONFLICT',status='INVALID')
    elif valid:
        for rid in valid:all_routes[rid].update(validation_status='JOINT_CHECK_PENDING',status='NOT_EVALUATED')
    result=copy.deepcopy(p);result.bundles=bundles;result.individual_routes=routes;result.main_connections=mains;result.design_stage='AXES'
    result.validation={'stage':'AXES','geometry_revision':p.geometry_revision,'valid_route_ids':[rid for rid,r in routes.items() if r.get('validation_status')=='GEOMETRICALLY_VALID'],
        'invalid_route_ids':[rid for rid,r in routes.items() if r.get('validation_status')!='GEOMETRICALLY_VALID'],
        'native_verified':False,'assignment':assignment,'coordination':coordination,'joint_repairs':repairs,'reused_route_ids':reused,'seconds':time.monotonic()-started}
    result.solver_state['stage']='AXES';result.solver_state['axes_calculated']=True
    from .model import RoutingDesignProposal
    return RoutingDesignProposal.from_dict(result.to_dict())
