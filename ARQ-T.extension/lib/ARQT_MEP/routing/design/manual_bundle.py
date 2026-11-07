"""Manual corridors, ordered radial bundles and bounded local construction.

No building exploration, global optimiser, MILP, A*, RRT or IRIS is used.
The existing proposal, spatial checker and native execution contracts remain
the source of persistence, collision checks and final materialisation.
"""
import copy
import math
import time
import uuid
from dataclasses import asdict
from shapely.geometry import LineString,Point
from . import model,endpoints
from .spatial import valid_height_intervals
from .. import settings
from ..core import geometry as g
from ..core.natural_path import straight_vertices
from ..core.route_geometry import validate_route_geometry
from ..core.spatial_model import build_spatial_model
from ..core.terminal_adapter import rotate,posed_space,FAMILY


STRATEGY='MANUAL_BUNDLE'


def initialize(p):
    from ...family_rules import distributor_network
    from ..systems.heat_recovery_topology import distributor_ports
    p.configuration.setdefault('design_clearance_mm',p.configuration.get('clearance_mm',10))
    previous=p.equipment;records={}
    selected={str(v.get('element_id',v.get('element_uid'))) for v in p.configuration.get('equipment_assignments',[]) if v.get('include',True)}
    for device in p.catalog.get('devices',[]):
        network=distributor_network(device.get('family',''))
        if not network or not device.get('is_configured_distributor'):continue
        if selected and not {device['id'],str(device['element_id'])}.intersection(selected):continue
        main,branches=distributor_ports(device)
        old=next((v for v in previous.values() if v['id']==device['id']),{})
        frame=copy.deepcopy(device.get('transform'))
        records[device['id']]={'id':device['id'],'element_id':device['element_id'],'network_type':network,
            'main_port':copy.deepcopy(main),'available_ports':[{'endpoint':asdict(endpoints.endpoint(port,network)),
                'port':copy.deepcopy(port)} for port in branches if not port['connected']],
            'original_transform':frame,'proposed_transform':copy.deepcopy(old.get('proposed_transform',frame)),
            'body_box_mm':copy.deepcopy(device.get('body_box_mm')),'modification':copy.deepcopy(device.get('modification',{})),
            'existing_connections':copy.deepcopy(device.get('existing_connections',[]))}
        if old.get('original_transform') and old['original_transform']!=frame:
            records[device['id']]['proposed_transform']=copy.deepcopy(frame)
            records[device['id']]['validation_status']='REAL_MANIFOLD_CHANGED_REVIEW_REQUIRED'
    p.equipment=records
    p.endpoints=endpoints.elbow_destinations(p.catalog,[{'terminal_id':tid,'network_type':v['network_type']} for tid,v in p.terminals.items()])
    return p


def _clean(points):
    result=[]
    for point in points:
        xy=model._xy(point)
        if not result or math.dist(xy,result[-1])>=.001:result.append(xy)
    model._points(result)
    if len(result)>256:raise ValueError('El corredor admite como máximo 256 vértices.')
    if not LineString(result).is_simple:raise ValueError('El corredor no puede cruzarse consigo mismo.')
    return result


def assigned(p):
    result={}
    for cid,c in p.corridors.items():
        if c.get('generated_by')!=STRATEGY:continue
        if c.get('source_manifold_id') not in p.equipment:raise ValueError('MANIFOLD_NOT_FOUND: '+cid)
        network=p.equipment[c['source_manifold_id']]['network_type']
        if c['network_type']!=network:raise ValueError('CORRIDOR_NETWORK_MISMATCH: '+cid)
        for tid in c.get('reachable_terminal_ids',[]):
            if tid in result:raise ValueError('TERMINAL_ALREADY_ASSIGNED: '+tid)
            if tid not in p.terminals or p.terminals[tid]['network_type']!=network:raise ValueError('TERMINAL_NETWORK_MISMATCH: '+tid)
            if p.terminals[tid]['level_id']!=c['level_id']:raise ValueError('TERMINAL_LEVEL_MISMATCH: '+tid)
            result[tid]=cid
    return result


def _invalidate(p,cids):
    affected={tid for tid,cid in assigned(p).items() if cid in cids}
    affected.update(r.get('terminal_id') for r in p.individual_routes.values() if r.get('corridor_id') in cids)
    for tid in affected:
        p.selected_paths.pop(tid,None)
        if 'axis:'+str(tid) in p.individual_routes:p.individual_routes['axis:'+tid]['validation_status']='EDITED_PENDING_RECHECK'
    for cid in cids:
        if cid in p.corridors:
            c=p.corridors[cid];c['corridor_revision']=c.get('corridor_revision',1)+1;c['validation_status']='EDITED_PENDING_RECHECK'
    p.corridor_assignments={tid:[cid] for tid,cid in assigned(p).items()}
    p.validation={};p.design_stage='CORRIDORS';p.solver_state={'strategy':STRATEGY,'stage':'EDITED_PENDING_RECHECK'}


def edit(original,operation,payload,expected_revision):
    if type(expected_revision)!=int or original.revision!=expected_revision:raise ValueError('VERSION_CONFLICT')
    p=copy.deepcopy(original);before=model._snapshot(p);cid=payload.get('corridor_id');changed=set()
    if operation in ('undo','redo'):
        source=p.history if operation=='undo' else p.future;target=p.future if operation=='undo' else p.history
        if not source:raise ValueError('No hay operaciones para '+operation)
        target.append(model._restore(p,source.pop()));p.validation={};p.design_stage='CORRIDORS'
        p.selected_paths={};p.main_connections={}
        for route in p.individual_routes.values():route['validation_status']='EDITED_PENDING_RECHECK'
    elif operation=='add_corridor':
        uid=payload['distributor_uid'];equipment=p.equipment.get(uid)
        if equipment is None:raise ValueError('Seleccione el distribuidor del corredor.')
        network=payload.get('network_type',equipment['network_type'])
        if network!=equipment['network_type']:raise ValueError('El distribuidor no pertenece a esa red.')
        cid=cid or uuid.uuid4().hex
        if cid in p.corridors:raise ValueError('Corredor duplicado.')
        c=model.Corridor(cid,_clean(payload['points_mm']),network,payload['level_id'],source_manifold_id=uid,
            generated_by=STRATEGY,entity_type='CORRIDOR',constraint_type='REQUIRED_WAYPOINT')
        p.corridors[cid]=asdict(c);changed.add(cid)
    elif operation in ('move_manifold','rotate_manifold'):
        from .equipment import edit_frame
        uid=payload.get('distributor_uid')
        if uid is None:
            choices=[k for k,v in p.equipment.items() if v['network_type']==payload.get('network_type')]
            if len(choices)!=1:raise ValueError('Seleccione un distribuidor concreto.')
            uid=choices[0]
        edit_frame(p.equipment[uid],operation,payload)
        changed={key for key,c in p.corridors.items() if c.get('source_manifold_id')==uid}
        p.main_connections={}
    elif operation in ('set_branch_waypoint','set_elbow_rotation'):
        tid=payload.get('terminal_id') or payload.get('route_id','').removeprefix('axis:')
        mapping=assigned(p)
        if tid not in mapping:raise ValueError('Seleccione un ramal asignado.')
        if operation=='set_branch_waypoint':
            records=p.constraints.setdefault('branch_waypoints',{})
            if payload.get('point_mm') is None:records.pop(tid,None)
            else:records[tid]=model._xy(payload['point_mm'])
        else:
            angle=payload.get('angle_degrees')
            if angle is None:p.constraints.setdefault('elbow_angles_degrees',{}).pop(tid,None)
            else:
                angle=float(angle)
                if not math.isfinite(angle):raise ValueError('Ángulo de codo no válido.')
                p.constraints.setdefault('elbow_angles_degrees',{})[tid]=angle
        changed.add(mapping[tid])
    else:
        if cid not in p.corridors:raise ValueError('Seleccione un corredor manual.')
        c=p.corridors[cid]
        if c.get('generated_by')!=STRATEGY:raise ValueError('Cree un estudio manual; no se convierten automáticamente los esquemas anteriores.')
        if c['constraint_type']=='LOCKED_WAYPOINT' and operation not in ('set_constraint','assign_terminals','assign_terminal','remove_terminal'):
            raise ValueError('Corredor bloqueado.')
        points=c['reference_xy_mm'];index=payload.get('index',0)
        if operation in ('assign_terminals','assign_terminal'):
            tids=payload.get('terminal_ids',[payload.get('terminal_id')]);mapping=assigned(p)
            for tid in tids:
                if tid not in p.terminals or p.terminals[tid]['network_type']!=c['network_type']:raise ValueError('TERMINAL_NETWORK_MISMATCH: '+str(tid))
                if p.terminals[tid]['level_id']!=c['level_id']:raise ValueError('El terminal no está en la planta del corredor.')
                if tid in mapping and mapping[tid]!=cid:raise ValueError('TERMINAL_ALREADY_ASSIGNED: retire antes su asignación al otro corredor.')
                if tid not in c['reachable_terminal_ids']:c['reachable_terminal_ids'].append(tid)
        elif operation=='remove_terminal':
            tid=payload['terminal_id']
            if tid in c['reachable_terminal_ids']:c['reachable_terminal_ids'].remove(tid)
            p.selected_paths.pop(tid,None);p.individual_routes.pop('axis:'+tid,None)
        elif operation=='set_corridor_distributor':
            equipment=p.equipment[payload['distributor_uid']]
            if c['reachable_terminal_ids'] and equipment['network_type']!=c['network_type']:raise ValueError('Retire los terminales antes de cambiar de red.')
            c.update(source_manifold_id=equipment['id'],network_type=equipment['network_type'])
        elif operation=='move_corridor':
            dx,dy=model._xy(payload['delta_mm']);c['reference_xy_mm']=[[x+dx,y+dy] for x,y in points]
        elif operation in ('move_vertex','add_vertex','delete_vertex'):
            if type(index)!=int or not 0<=index<len(points)+(operation=='add_vertex'):raise ValueError('Vértice inválido.')
            if operation=='move_vertex':points[index]=model._xy(payload['point_mm'])
            elif operation=='add_vertex':points.insert(index,model._xy(payload['point_mm']))
            else:points.pop(index)
            c['reference_xy_mm']=_clean(points)
        elif operation=='delete_corridor':
            for tid in c['reachable_terminal_ids']:
                p.selected_paths.pop(tid,None);p.individual_routes.pop('axis:'+tid,None)
            del p.corridors[cid]
        elif operation=='set_constraint':
            if payload['constraint_type'] not in model.CONSTRAINTS:raise ValueError('Restricción desconocida.')
            c['constraint_type']=payload['constraint_type']
        else:raise ValueError('La modalidad manual permite editar corredores y un único punto de paso por ramal.')
        changed.add(cid)
    if operation not in ('undo','redo'):
        _invalidate(p,changed)
        p.manual_edits.append({'operation':operation,'payload':copy.deepcopy(payload),'affected_corridors':sorted(changed)})
        p.history.append(model._delta(before,p));p.history=p.history[-model.HISTORY_LIMIT:];p.future=[]
    p.revision+=1;p.updated_utc=model.now()
    return model.RoutingDesignProposal.from_dict(p.to_dict())


def prepare(p):
    result=copy.deepcopy(p);initialize(result);assigned(result)
    result.configuration['routing_strategy']=STRATEGY;result.alternatives=[];result.corridor_graph={};result.design_stage='CORRIDORS'
    return result


def project(vertices,xy):
    best=None;station=0.
    for index,(a,b) in enumerate(zip(vertices,vertices[1:])):
        length=math.dist(a,b);t=((xy[0]-a[0])*(b[0]-a[0])+(xy[1]-a[1])*(b[1]-a[1]))/(length*length)
        t=max(0,min(1,t));q=[a[k]+t*(b[k]-a[k]) for k in (0,1)];tangent=[(b[k]-a[k])/length for k in (0,1)]
        cross=tangent[0]*(xy[1]-q[1])-tangent[1]*(xy[0]-q[0]);candidate=(math.dist(q,xy),station+t*length,index,q,tangent,cross)
        if best is None or candidate[:2]<best[:2]:best=candidate
        station+=length
    return {'distance_mm':best[0],'station_mm':best[1],'segment':best[2],'point_mm':best[3],
        'tangent':best[4],'side':'LEFT' if best[5]>1e-5 else 'RIGHT' if best[5]<-1e-5 else 'CENTER'}


def offset(vertices,value):
    normals=[]
    for a,b in zip(vertices,vertices[1:]):
        length=math.dist(a,b);normals.append((-(b[1]-a[1])/length,(b[0]-a[0])/length))
    result=[]
    for i,q in enumerate(vertices):
        if i==0:n=normals[0];scale=value
        elif i==len(vertices)-1:n=normals[-1];scale=value
        else:
            n=[normals[i-1][k]+normals[i][k] for k in (0,1)];den=sum(n[k]*normals[i][k] for k in (0,1))
            if den<1e-6:raise ValueError('BUNDLE_OFFSET_INFEASIBLE')
            scale=value/den
        result.append([q[k]+n[k]*scale for k in (0,1)])
    if not LineString(result).is_simple:raise ValueError('BUNDLE_OFFSET_INFEASIBLE')
    return result


def _prefix(vertices,lane,station):
    result=[lane[0]];length=0.
    for i,(a,b) in enumerate(zip(vertices,vertices[1:])):
        segment=math.dist(a,b)
        if length+segment>=station:
            t=max(0,min(1,(station-length)/segment));q=[lane[i][k]+t*(lane[i+1][k]-lane[i][k]) for k in (0,1)]
            if math.dist(q,result[-1])>.001:result.append(q)
            return result
        result.append(lane[i+1]);length+=segment
    return result


def configuration(p,profile):
    cfg=copy.deepcopy(p.configuration);cfg['routing_strategy']=STRATEGY
    cfg.update(section={'shape':'CIRCULAR','nominal_diameter_mm':profile.nominal_diameter_mm,
        'outer_diameter_mm':profile.external_diameter_mm,'insulation_mm':profile.insulation_thickness_mm},
        clearance_mm=cfg.get('design_clearance_mm',cfg.get('clearance_mm',10)),bend_radius_mm=profile.minimum_bend_radius_mm)
    return settings.validate(cfg)


def _lift(space,xy,profile,gap,passages,spacing=200):
    from ... import surface
    points=[];samples=[]
    for a,b in zip(xy,xy[1:]):
        count=max(1,math.ceil(math.dist(a,b)/spacing))
        samples.extend([[a[k]+(b[k]-a[k])*i/count for k in (0,1)] for i in range(count)])
    samples.append(list(xy[-1]))
    for x,y in samples:
        bands=valid_height_intervals(space,x,y,profile,gap);options=[]
        for region in space.regions:
            if not region.polygon.covers(Point(x,y)):continue
            lower=surface.height(region.lower,x,y);upper=surface.height(region.upper,x,y);middle=(lower+upper)/2
            for lo,hi in bands:
                z=max(lo,min(hi,middle));options.append((abs(z-middle),abs(z-points[-1][2]) if points else 0,z,middle,lower,upper))
        if options:
            _,_,z,middle,lower,upper=min(options)
            record={'xy_mm':[x,y],'z_lower_mm':lower,'z_upper_mm':upper,'z_mid_mm':middle,'axis_z_mm':z,'deviation_mm':z-middle}
        else:
            found=None
            for portal in passages:
                a,b=portal['points_mm'];length=math.dist(a[:2],b[:2])
                if length<1e-5 or portal['radius_mm']<profile.external_diameter_mm/2+profile.insulation_thickness_mm+gap:continue
                t=((x-a[0])*(b[0]-a[0])+(y-a[1])*(b[1]-a[1]))/(length*length)
                if not 0<=t<=1:continue
                q=[a[k]+t*(b[k]-a[k]) for k in range(3)]
                if math.dist([x,y],q[:2])<.1:found=q;break
            if found is None:
                hits=set(space.collisions((x,y,0),(x,y,20000),0))
                wall_hit=any(obstacle.id in hits and obstacle.data.get('wall_axis_mm') for obstacle in space.obstacles)
                reason='CORRIDOR_PASSAGE_REQUIRED' if wall_hit else 'INSUFFICIENT_PLENUM_HEIGHT'
                raise ValueError(reason+' @ '+repr([x,y]))
            z=found[2];record={'xy_mm':[x,y],'axis_z_mm':z,'passage':True,'deviation_mm':None}
        points.append([x,y,z]);record['sample_index']=len(points)-1
        if len(points)==1:height_records=[record]
        else:height_records.append(record)
    return straight_vertices(points),height_records


def _pose_options(p,tid,approach,ports):
    terminal=next(t for t in p.catalog['terminals'] if t['id']==tid);adapter=terminal.get('terminal_adapter')
    options=[(copy.deepcopy(port),None) for port in ports]
    if not adapter:return options
    device=next((d for d in p.catalog['devices'] if d['id']==adapter['id']),{})
    modification=device.get('modification',{})
    requested=p.constraints.get('elbow_angles_degrees',{}).get(tid)
    allowed=p.configuration.get('manual_bundle',{}).get('elbow_rotation_angles_degrees',{}).get(device.get('family',FAMILY),[0,-15,15,-30,30,-45,45,90,-90,180] if device.get('family',FAMILY)==FAMILY else [0])
    if requested is not None:
        if not any(abs(requested-v)<1e-6 for v in allowed):raise ValueError('ELBOW_ROTATION_UNAVAILABLE')
        angles=[requested]
    else:angles=[v for v in allowed if v]
    if modification.get('pinned') or modification.get('grouped') or modification.get('hosted'):
        if requested not in (None,0):raise ValueError('ELBOW_ROTATION_UNAVAILABLE')
        return options
    if any(port.get('connected') for port in adapter['outlets']):return options
    pivot=adapter['pivot_mm'];axis=adapter['axis']
    rotated=[]
    for degrees in angles:
        radians=math.radians(degrees)
        corners=[g.add(pivot,rotate(g.sub(q,pivot),axis,radians)) for q in adapter.get('body_corners_mm',[])]
        pose={'element_id':adapter['element_id'],'element_key':adapter['id'],'type_id':adapter['type_id'],
            'terminal_uid':tid,'terminal_port':adapter['terminal_port'],'pivot_mm':pivot,'axis':axis,
            'rotation_rad':radians,'original_rotation':0.,'proposed_rotation':radians,
            'captured_outlets':copy.deepcopy(adapter['outlets']),'body_box_mm':list(g.bounds(corners)) if corners else None}
        for port in ports:
            target=copy.deepcopy(port);target['xyz_mm']=list(g.add(pivot,rotate(g.sub(port['xyz_mm'],pivot),axis,radians)))
            target['direction']=list(rotate(port['direction'],axis,radians));target['adapter_pose']=pose
            rotated.append((target,pose))
    rotated.sort(key=lambda item:g.distance(item[0]['xyz_mm'],approach)+200*(1-g.dot(g.unit(item[0]['direction']),g.unit(g.sub(approach,item[0]['xyz_mm'])))))
    return rotated if requested not in (None,0) else options+rotated


def _links(start,goal,incoming,outgoing,radius,waypoint=None):
    # Bounded analytic constructions; no route search is invoked.
    if waypoint is not None:return [[start,list(g.add(start,g.mul(g.unit(incoming),radius))),waypoint,
        list(g.sub(goal,g.mul(g.unit(outgoing),radius))),goal]]
    from ..core.oriented_path import seeds
    direct=[start,list(g.add(start,g.mul(g.unit(incoming),radius))),list(g.sub(goal,g.mul(g.unit(outgoing),radius))),goal]
    return [direct]+seeds(start,goal,incoming,outgoing,radius)


def _self_collision(pieces,radius,minimum_bend):
    # A bounded-curvature local neighbourhood belongs to the same continuous
    # tube. Compare nonlocal portions using exact arc stations and inflated
    # chord capsules, including long lines whose closest point is internal.
    segments=[];station=0.
    for piece in pieces:
        points=piece['points_mm'];length=g.distance(piece['start'],piece['end']) if piece['kind']=='LINE' else piece['radius_mm']*piece['angle_rad']
        for i,(a,b) in enumerate(zip(points,points[1:])):
            segments.append((a,b,station+length*i/(len(points)-1),length/(len(points)-1),piece.get('sagitta_mm',0.)))
        station+=length
    for i,(a,b,sa,la,ea) in enumerate(segments):
        for c,d,sb,lb,eb in segments[:max(0,i-1)]:
            if g.segment_distance(a,b,c,d)>=2*radius+ea+eb:continue
            u=g.sub(b,a);v=g.sub(d,c);w=g.sub(a,c)
            aa=g.dot(u,u);bb=g.dot(u,v);cc=g.dot(v,v);dd=g.dot(u,w);ee=g.dot(v,w)
            denominator=aa*cc-bb*bb
            t=max(0,min(1,(bb*ee-cc*dd)/denominator)) if denominator>1e-12 else 0.
            q=(bb*t+ee)/cc if cc>1e-12 else 0.
            if q<0:q=0.;t=max(0,min(1,-dd/aa)) if aa>1e-12 else 0.
            elif q>1:q=1.;t=max(0,min(1,(bb-dd)/aa)) if aa>1e-12 else 0.
            if abs((sa+t*la)-(sb+q*lb))>math.pi*minimum_bend:return True
    return False


def _build_route(p,c,tid,source,destination,pose,profile,cfg,space,lane,station,lateral,base_radius,passages,deadline,cancelled):
    radius=profile.minimum_bend_radius_mm*1.2;arm=max(radius,cfg['connection_straight_mm']);begin=list(g.add(source['xyz_mm'],g.mul(g.unit(source['direction']),arm)))
    end=list(g.add(destination['xyz_mm'],g.mul(g.unit(destination['direction']),arm)))
    prefix=_prefix(c['reference_xy_mm'],lane,station)
    if len(prefix)<2:raise ValueError('BUNDLE_OFFSET_INFEASIBLE: ramificación demasiado próxima al origen.')
    trunk,heights=_lift(space,prefix,profile,cfg['clearance_mm'],passages,max(100,radius))
    incoming=g.unit(g.sub(trunk[1],trunk[0]));outgoing=g.unit(g.sub(trunk[-1],trunk[-2]))
    waypoint=p.constraints.get('branch_waypoints',{}).get(tid)
    manual=_lift(space,[waypoint,waypoint],profile,cfg['clearance_mm'],passages)[0][0] if waypoint else None
    stubs=[{'points_mm':[port['xyz_mm'],list(g.add(port['xyz_mm'],g.mul(g.unit(port['direction']),arm*2)))],
        'ignore_owner_ids':[port['element_key']]} for port in (source,destination)]
    corner_radii={}
    for index in range(1,len(c['reference_xy_mm'])-1):
        a,b,d=c['reference_xy_mm'][index-1:index+2];turn=(b[0]-a[0])*(d[1]-b[1])-(b[1]-a[1])*(d[0]-b[0])
        corner_radii[tuple(round(v,3) for v in lane[index])]=base_radius-(lateral if turn>0 else -lateral)
    local=posed_space(space,pose)
    last_failure='ELBOW_APPROACH_INFEASIBLE'
    for entrance in _links(begin,trunk[0],source['direction'],incoming,radius):
        for tail in _links(trunk[-1],end,outgoing,g.mul(destination['direction'],-1),radius,manual):
            if cancelled and cancelled():raise InterruptedError('Cálculo cancelado.')
            if time.monotonic()>=deadline:raise TimeoutError('LOCAL_CONNECTION_BUDGET_EXHAUSTED')
            points=straight_vertices([source['xyz_mm']]+entrance+list(trunk[1:-1])+tail+[destination['xyz_mm']])
            radii=[max(profile.minimum_bend_radius_mm,corner_radii.get(tuple(round(v,3) for v in q[:2]),radius)) for q in points]
            candidate={'points_mm':[list(q) for q in points],'corner_radii_mm':radii,'endpoint_stubs':stubs,
                'passages':passages,'split_passages':True,'adapter_pose':pose}
            checked=validate_route_geometry(local,candidate,settings.profile(cfg));candidate.update(checked)
            if checked['status']=='INVALID':continue
            pieces=checked['primitives']
            own_radius=profile.external_diameter_mm/2+profile.insulation_thickness_mm+cfg['clearance_mm']/2+cfg.get('native_axis_deviation_mm',5.)
            if _self_collision(pieces,own_radius,profile.minimum_bend_radius_mm):last_failure='BUNDLE_OFFSET_INFEASIBLE';continue
            def tangent(piece,end=False):
                return g.unit(g.sub(piece['end'],piece['start'])) if piece['kind']=='LINE' else g.unit(g.cross(piece['axis'],g.sub(piece['end'] if end else piece['start'],piece['center'])))
            if not pieces or g.dot(tangent(pieces[0]),g.unit(source['direction']))<.99999 or g.dot(tangent(pieces[-1],True),g.mul(g.unit(destination['direction']),-1))<.99999:continue
            candidate.update(height_profile=heights,source=copy.deepcopy(source),destination=copy.deepcopy(destination),
                route_id='axis:'+tid,terminal_id=tid,terminal_uid=tid,elbow_uid=destination['element_key'],
                distributor_uid=c['source_manifold_id'],source_manifold_id=c['source_manifold_id'],
                source_connector_id=source['id'],destination_connector_id=destination['id'],
                corridor_id=c['corridor_id'],corridor_ids=[c['corridor_id']],network_type=c['network_type'],route_kind='BRANCH',
                lane_offset_mm=lateral,branch_position_mm=station,level_id=c['level_id'],profile=asdict(profile),section=cfg['section'],
                clearance_mm=cfg['clearance_mm'],bend_radius_mm=profile.minimum_bend_radius_mm,construction_bend_radius_mm=radius,
                input_proposal_revision=p.revision,input_corridor_revision=c.get('corridor_revision',1),geometry_revision=p.geometry_revision,
                validation_status='GEOMETRICALLY_VALID' if checked['status'] in ('VALID','VALID_WITH_WARNINGS') else checked['status'],
                native_validation_status='NOT_EVALUATED',connection_status='NOT_MODELED',elbow_pose=pose)
            return candidate
    raise ValueError(last_failure+': revise la salida del haz o añada un punto de paso.')


def _mains(p,profile,space,cfg):
    from ..connector_labels import HRV_FUNCTIONS
    results={};used=set();by_network={}
    for c in p.corridors.values():
        if c.get('reachable_terminal_ids'):by_network.setdefault(c['network_type'],set()).add(c['source_manifold_id'])
    if not p.configuration.get('manual_bundle',{}).get('include_mains',True):return results
    devices=[d for d in p.catalog['devices'] if str(p.configuration.get('hrv_id')) in (d['id'],str(d['element_id']))]
    main=p.configuration.get('main_connection',{});diameter=main.get('diameter_mm',125)
    for network,uids in by_network.items():
        for uid in sorted(uids):
            rid='main:'+uid;failed={'route_id':rid,'route_kind':'MAIN','network_type':network,'points_mm':[],
                'validation_status':'MAIN_CONNECTOR_UNRESOLVED','status':'NOT_EVALUATED'};results[rid]=failed
            if len(devices)!=1:continue
            ports=[port for port in devices[0]['connectors'] if (HRV_FUNCTIONS.get((port.get('classification'),port.get('flow_direction'))) or [None])[0]==network]
            if len(ports)!=1:continue
            source=ports[0];destination=p.equipment[uid]['main_port']
            if source['id'] in used or source['connected'] or destination['connected']:failed['validation_status']='MAIN_EXISTING_CONNECTION_REVIEW_REQUIRED';continue
            used.add(source['id'])
            if not main.get('outer_diameter_mm') or not main.get('bend_radius_mm'):failed['validation_status']='MAIN_PROFILE_REQUIRED';continue
            declared=model.DuctProfile('manual-main',network,diameter,None,main['outer_diameter_mm'],main.get('insulation_mm',0),main['bend_radius_mm'])
            local=configuration(p,declared);radius=declared.minimum_bend_radius_mm
            points=straight_vertices([source['xyz_mm'],g.add(source['xyz_mm'],g.mul(source['direction'],radius)),
                g.add(destination['xyz_mm'],g.mul(destination['direction'],radius)),destination['xyz_mm']])
            route={'points_mm':[list(q) for q in points],'passages':[],'endpoint_stubs':[{'points_mm':[port['xyz_mm'],list(g.add(port['xyz_mm'],g.mul(port['direction'],2*radius)))],
                'ignore_owner_ids':[port['element_key']]} for port in (source,destination)]}
            route.update(validate_route_geometry(space,route,settings.profile(local)))
            route.update(route_id=rid,route_kind='MAIN',network_type=network,source=source,destination=destination,profile=asdict(declared),
                section=local['section'],clearance_mm=local['clearance_mm'],bend_radius_mm=radius,construction_kind='RIGID_CIRCULAR',
                level_id=next(c['level_id'] for c in p.corridors.values() if c['source_manifold_id']==uid),
                validation_status='GEOMETRICALLY_VALID' if route['status'] in ('VALID','VALID_WITH_WARNINGS') else route['status'])
            results[rid]=route
    return results


def calculate(original,profile,options=None,cancelled=None):
    from .equipment import transform
    p=prepare(original);mapping=assigned(p)
    if not mapping:raise ValueError('Dibuje un corredor y asígnele al menos una rejilla.')
    working=transform(p);initialize(working)
    # Preserve the real/proposed frames for the native plan; only the working
    # connector descriptors and obstacle bounds are transformed for geometry.
    cfg=configuration(p,profile)
    approvals=cfg['constraints'].get('passage_approvals',{})
    cfg['constraints']['approved_passages']=[key for key in cfg['constraints'].get('approved_passages',[])
        if approvals.get(key,{}).get('responsible') and approvals.get(key,{}).get('reference')]
    space=build_spatial_model(working.snapshot,cfg['constraints'])
    started=time.monotonic();deadline=started+float((options or {}).get('seconds',30));gap=cfg['clearance_mm']
    passages=[copy.deepcopy(v) for v in cfg['constraints'].get('passages',[]) if v.get('classification')=='PASSABLE' or v.get('id') in cfg['constraints'].get('approved_passages',[])]
    routes={};used_ports=set();p.selected_paths={};p.elbow_rotations={};p.bundles={};p.branches={};p.connectivity={}
    admitted=p.catalog.get('configured_model',{}).get('flexible_duct',{}).get('listed_diameters_mm',[])
    if admitted and not any(abs(d-profile.nominal_diameter_mm)<.01 for d in admitted):raise ValueError('PROJECT_DIAMETER_NOT_AVAILABLE')
    diameter=profile.external_diameter_mm+2*profile.insulation_thickness_mm
    for cid,c in p.corridors.items():
        tids=c.get('reachable_terminal_ids',[])
        if not tids:continue
        equipment=working.equipment.get(c['source_manifold_id']);destinations={};projections={}
        for tid in tids:
            report=p.endpoints[tid]
            if not report['endpoints']:
                p.connectivity[tid]={'status':report['status']};routes['axis:'+tid]={'route_id':'axis:'+tid,'terminal_id':tid,'network_type':c['network_type'],
                    'corridor_id':cid,'points_mm':[],'status':'NOT_EVALUATED','validation_status':report['status']};continue
            ports=[report['ports'][endpoint['endpoint_id']] for endpoint in report['endpoints']]
            ports.sort(key=lambda port:project(c['reference_xy_mm'],port['xyz_mm'][:2])['distance_mm'])
            destinations[tid]=ports;projections[tid]=project(c['reference_xy_mm'],ports[0]['xyz_mm'][:2])
        right=sorted((t for t in destinations if projections[t]['side']!='LEFT'),key=lambda t:(projections[t]['station_mm'],t))
        left=sorted((t for t in destinations if projections[t]['side']=='LEFT'),key=lambda t:(projections[t]['station_mm'],t),reverse=True)
        order=right+left;offsets={tid:(i-(len(order)-1)/2)*(diameter+gap) for i,tid in enumerate(order)}
        base_radius=profile.minimum_bend_radius_mm*1.2+max(map(abs,offsets.values()),default=0)
        width=len(order)*diameter+max(0,len(order)-1)*gap
        c['preliminary_capacity']={'required_width_mm':width,'status':'PENDING_FULL_SWEEP'}
        origin=c['reference_xy_mm'][0];a,b=c['reference_xy_mm'][:2];length=math.dist(a,b);normal=(-(b[1]-a[1])/length,(b[0]-a[0])/length)
        ports=[v['port'] for v in equipment.get('available_ports',[]) if v['port']['id'] not in used_ports and not v['port']['connected'] and abs(v['port']['size'].get('diameter_mm',0)-profile.nominal_diameter_mm)<.1]
        ports.sort(key=lambda port:(sum((port['xyz_mm'][k]-origin[k])*normal[k] for k in (0,1)),port['xyz_mm'][2],port['connector_id']))
        from ..core.cooperative import overlaps
        best={};best_score=(-1,-1,float('-inf'));best_ports=[];selections=[]
        for start in range(max(1,len(ports)-len(order)+1)):
            selection=ports[start:start+len(order)];selections.append(selection)
            for i in range(len(selection)-1):
                swapped=list(selection);swapped[i],swapped[i+1]=swapped[i+1],swapped[i];selections.append(swapped)
        for selection in selections[:8]:
            trial={}
            for i,tid in enumerate(order):
                failure={'route_id':'axis:'+tid,'terminal_id':tid,'network_type':c['network_type'],'corridor_id':cid,'points_mm':[],
                    'status':'NOT_EVALUATED','validation_status':'MANIFOLD_PORT_ASSIGNMENT_FAILED','lane_index':i,'incident_xy_mm':projections[tid]['point_mm']}
                trial[tid]=failure
                if i>=len(selection):continue
                if cancelled and cancelled():raise InterruptedError('Cálculo cancelado; se conserva el borrador anterior.')
                if time.monotonic()>=deadline:failure['validation_status']='LOCAL_CONNECTION_BUDGET_EXHAUSTED';continue
                source=selection[i]
                if math.dist(source['xyz_mm'][:2],origin)>max(1000,width+3*base_radius):failure['validation_status']='CORRIDOR_START_REMOTE';continue
                station=max(0,projections[tid]['station_mm']-base_radius)
                try:
                    lane=offset(c['reference_xy_mm'],offsets[tid])
                    local_deadline=min(deadline,time.monotonic()+max(.1,(deadline-time.monotonic())/max(1,len(order)-i)))
                    for destination,pose in _pose_options(p,tid,projections[tid]['point_mm']+[source['xyz_mm'][2]],destinations[tid]):
                        try:
                            route=_build_route(p,c,tid,source,destination,pose,profile,cfg,space,lane,station,offsets[tid],base_radius,passages,local_deadline,cancelled)
                            route['lane_index']=i;route['departure_side']=projections[tid]['side'];trial[tid]=route;break
                        except TimeoutError as exc:failure.update(validation_status='LOCAL_CONNECTION_BUDGET_EXHAUSTED',detail=str(exc));break
                        except ValueError as exc:failure['validation_status']=str(exc).split(':')[0].split(' @ ')[0];failure['detail']=str(exc)
                except ValueError as exc:failure['validation_status']=str(exc).split(':')[0].split(' @ ')[0];failure['detail']=str(exc)
            viable=[r for r in trial.values() if r.get('validation_status')=='GEOMETRICALLY_VALID'];conflicted=set()
            for i,a in enumerate(viable):
                for b in viable[:i]:
                    if overlaps(a,settings.profile(cfg).radius_mm,b,settings.profile(cfg).radius_mm):conflicted.update((a['route_id'],b['route_id']))
            score=(len(viable)-len(conflicted),len(viable),-sum(r.get('length_m',0) for r in trial.values()))
            if score>best_score:best_score=score;best=trial;best_ports=selection
            if score[0]==len(order) or time.monotonic()>=deadline:break
        used_ports.update(port['id'] for port in best_ports)
        for tid,route in best.items():
            routes['axis:'+tid]=route;p.connectivity[tid]={'status':route['validation_status'],'corridor_id':cid}
            if route.get('destination'):
                ep=asdict(endpoints.endpoint(route['destination'],c['network_type'],tid))
                p.selected_paths[tid]={'terminal_id':tid,'endpoint':ep,'network_type':c['network_type'],'manifold_id':c['source_manifold_id'],'points_mm':route['points_mm']}
                if route.get('elbow_pose'):p.elbow_rotations[tid]=route['elbow_pose']
            p.branches['branch:'+tid]={'terminal_id':tid,'corridor_id':cid,'station_mm':route.get('branch_position_mm'),
                'side':projections[tid]['side'],'lane_index':route.get('lane_index')}
        p.bundles[cid]={'bundle_id':cid,'corridor_ids':[cid],'route_ids':['axis:'+tid for tid in order],
            'lane_order':['axis:'+tid for tid in order],'lane_offsets':{'axis:'+tid:offsets[tid] for tid in order},'required_width_mm':width}
        c['validation_status']='SCHEMATIC_READY' if all(routes.get('axis:'+tid,{}).get('destination') for tid in tids) else 'SCHEMATIC_PENDING'
    p.main_connections=_mains(working,profile,space,cfg)
    from ..core.cooperative import overlaps
    all_routes=list(routes.values())+list(p.main_connections.values());collisions=[];xy_crossings=[]
    for i,a in enumerate(all_routes):
        if not a.get('primitives'):continue
        for b in all_routes[:i]:
            if not b.get('primitives'):continue
            ra=settings.effective_radius({'section':a['section'],'clearance_mm':a['clearance_mm']});rb=settings.effective_radius({'section':b['section'],'clearance_mm':b['clearance_mm']})
            physical=overlaps(a,ra,b,rb)
            aa=[q[:2] for piece in a['primitives'] for q in piece['points_mm']];bb=[q[:2] for piece in b['primitives'] for q in piece['points_mm']]
            cross=LineString(aa).intersection(LineString(bb))
            if not cross.is_empty:xy_crossings.append({'route_ids':[a['route_id'],b['route_id']],'kind':'GEOMETRIC_COLLISION' if physical else 'XY_INTERSECTION','geometry':cross.wkt})
            if physical:
                collisions.append([a['route_id'],b['route_id']])
                for route in (a,b):route.update(validation_status='VERTICAL_CROSSING_REVIEW_REQUIRED',status='INVALID')
    p.individual_routes=routes;p.alternatives=[];p.corridor_graph={};p.design_stage='AXES'
    p.configuration['routing_strategy']=STRATEGY
    p.validation={'stage':'AXES','geometry_revision':p.geometry_revision,'input_proposal_revision':original.revision,
        'valid_route_ids':[rid for rid,r in routes.items() if r['validation_status']=='GEOMETRICALLY_VALID'],
        'invalid_route_ids':[rid for rid,r in routes.items() if r['validation_status']!='GEOMETRICALLY_VALID'],
        'coordination':{'method':'ORDERED_MANUAL_LANES_AND_PAIRWISE_SWEEPS','collisions':collisions},'xy_intersections':xy_crossings,
        'native_verified':False,'seconds':time.monotonic()-started}
    p.solver_state={'strategy':STRATEGY,'stage':'AXES','axes_calculated':True}
    return model.RoutingDesignProposal.from_dict(p.to_dict())
