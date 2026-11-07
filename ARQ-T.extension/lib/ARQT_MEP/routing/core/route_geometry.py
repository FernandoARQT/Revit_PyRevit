"""Validated simplification and finite-radius turn envelopes."""
import math
from . import geometry as g
from .astar_3d import direction,calculate_route_cost


def rejected_path(route,route_id,configuration):
    def key(points):
        values=tuple(tuple(round(v,3) for v in p) for p in points)
        return min(values,tuple(reversed(values)))
    points=route.get('routing_points_mm') or route.get('points_mm') or []
    return bool(points) and any(key(points)==key(old) for old in
        configuration['constraints'].get('rejected_paths',{}).get(route_id,[]))


def simplify_route(points,space,radius,preserve=(),bend_radius_mm=None):
    points=[tuple(p) for p in points];protected={tuple(p) for p in preserve}
    def is_protected(p):return any(g.distance(p,q)<=1e-4 for q in protected)
    clean=[]
    for p in points:
        if clean and g.distance(clean[-1],p)<1e-6:continue
        while len(clean)>1 and not is_protected(clean[-1]) and g.norm(g.cross(g.sub(clean[-1],clean[-2]),g.sub(p,clean[-1])))<1e-5 and g.dot(g.sub(clean[-1],clean[-2]),g.sub(p,clean[-1]))>=0:
            if not space.free(clean[-2],p,radius):break
            clean.pop()
        clean.append(p)
    changed=True
    while changed:
        changed=False
        for i in range(len(clean)-2):
            for j in range(len(clean)-1,i+1,-1):
                if any(is_protected(p) for p in clean[i+1:j]):continue
                delta=g.sub(clean[j],clean[i])
                if bend_radius_mm is None and sum(abs(v)>1e-6 for v in delta)!=1:continue
                if space.free(clean[i],clean[j],radius):
                    candidate=clean[:i+1]+clean[j:]
                    if bend_radius_mm is not None:
                        try:rounded=primitives(candidate,bend_radius_mm)
                        except ValueError:continue
                        if any(not space.free(a,b,radius+p.get('sagitta_mm',0))
                            for p in rounded for a,b in zip(p['points_mm'],p['points_mm'][1:])):continue
                    clean=candidate;changed=True;break
            if changed:break
    return clean


def primitives(points,bend_radius):
    pts=[tuple(p) for p in points];turns={};trims=[0.0]*len(pts)
    for i in range(1,len(pts)-1):
        incoming=direction(pts[i-1],pts[i]);outgoing=direction(pts[i],pts[i+1])
        angle=math.acos(max(-1,min(1,g.dot(incoming,outgoing))))
        if angle<1e-6:continue
        if angle>math.pi-1e-6:raise ValueError('Giro de 180° no construible.')
        if bend_radius<=0:raise ValueError('Radio de giro no definido para una ruta con codos.')
        trim=bend_radius*math.tan(angle/2);trims[i]=trim
        start=g.sub(pts[i],g.mul(incoming,trim));end=g.add(pts[i],g.mul(outgoing,trim))
        toward_center=g.unit(g.sub(outgoing,g.mul(incoming,g.dot(incoming,outgoing))))
        center=g.add(start,g.mul(toward_center,bend_radius));axis=g.unit(g.cross(incoming,outgoing))
        radial=g.sub(start,center);segments=max(2,math.ceil(angle/math.radians(5)))
        sampled=[]
        for j in range(segments+1):
            theta=angle*j/segments
            rotated=g.add(g.mul(radial,math.cos(theta)),g.mul(g.cross(axis,radial),math.sin(theta)))
            sampled.append(g.add(center,rotated))
        sampled[-1]=end
        turns[i]={'kind':'ARC','start':start,'end':end,'center':center,'axis':axis,'radius_mm':bend_radius,
                  'angle_rad':angle,'points_mm':sampled,'sagitta_mm':bend_radius*(1-math.cos(angle/segments/2))}
    for i,(a,b) in enumerate(zip(pts,pts[1:])):
        if trims[i]+trims[i+1]>g.distance(a,b)+1e-5:
            raise ValueError('Tramo insuficiente entre giros; aumente el espacio o revise el radio de curvatura.')
    result=[]
    for i in range(len(pts)-1):
        a=turns[i]['end'] if i in turns else pts[i]
        b=turns[i+1]['start'] if i+1 in turns else pts[i+1]
        if g.distance(a,b)>1e-5:result.append({'kind':'LINE','start':a,'end':b,'points_mm':[a,b]})
        if i+1 in turns:result.append(turns[i+1])
    return result


def passage_for(a,b,passages,radius):
    if g.distance(a,b)<1e-6:return None
    for p in passages:
        c,d=p['points_mm'];axis=g.unit(g.sub(d,c));length=g.distance(c,d)
        if p['radius_mm']<radius:continue
        if abs(g.dot(direction(a,b),axis))<.99999:continue
        if all(-1e-4<=g.dot(g.sub(q,c),axis)<=length+1e-4 and g.point_segment(c,d,q)<.1 for q in (a,b)):
            return p
    return None


def split_passage_line(a,b,passages,radius):
    length=g.distance(a,b)
    if length<1e-6:return [(a,b)]
    axis=g.unit(g.sub(b,a));stations={0.,length}
    for portal in passages:
        if portal['radius_mm']<radius:continue
        c,d=portal['points_mm']
        if abs(g.dot(axis,g.unit(g.sub(d,c))))<.99999:continue
        if max(g.point_segment(a,b,q) for q in (c,d))>.1:continue
        stations.update(max(0,min(length,g.dot(g.sub(q,a),axis))) for q in (c,d))
    distinct=[]
    for t in sorted(stations):
        if not distinct or t-distinct[-1]>1e-5:distinct.append(t)
    points=[g.add(a,g.mul(axis,t)) for t in distinct]
    return list(zip(points,points[1:]))


def tangent_joint(a,b):
    """Adjacent native sweeps share one end disk, without volume overlap.

    This only certifies their immediate joint, not non-adjacent self-collisions.
    Finite-radius profiles must have bend radius >= swept radius as validated
    by settings; all obstacle and other-route checks remain necessary.
    """
    if g.distance(a['end'],b['start'])>1e-4:return False
    def tangent(piece,end):
        if piece['kind']=='LINE':return g.unit(g.sub(piece['end'],piece['start']))
        point=piece['end'] if end else piece['start']
        return g.unit(g.cross(piece['axis'],g.sub(point,piece['center'])))
    return g.dot(tangent(a,True),tangent(b,False))>1-1e-7


def validate_route_geometry(space,route,profile):
    from .terminal_adapter import posed_space
    space=posed_space(space,route.get('adapter_pose'))
    errors=[];samples=[];radius=profile.radius_mm;passages=route.get('passages',[])
    controls=route.get('points_mm') or []
    if len(controls)<2:
        return {'status':'NOT_EVALUATED','reason':'NO_ROUTE_GEOMETRY','errors':['NO_ROUTE_GEOMETRY'],'primitives':[]}
    if route.get('connection_validation_pending'):
        if any(s.get('adapter_endpoint') for s in route.get('endpoint_stubs',[])):
            try:primitives(controls,profile.bend_radius_mm)
            except ValueError as exc:
                return {'status':'INVALID','reason':'ADAPTER_CONNECTION_BEND_NOT_CONSTRUCTIBLE','errors':[str(exc)],'primitives':[]}
        controls=route.get('routing_points_mm',controls)
    if len(controls)<2 or all(g.distance(controls[0],p)<1e-6 for p in controls):
        return {'status':'INVALID','errors':['COINCIDENT_ENDPOINTS'],'primitives':[]}
    try:pieces=primitives(controls,profile.bend_radius_mm)
    except ValueError as exc:return {'status':'INVALID','errors':[str(exc)],'primitives':[]}
    too_short=[i for i,p in enumerate(pieces) if (g.distance(p['start'],p['end']) if p['kind']=='LINE'
               else p['radius_mm']*p['angle_rad'])<profile.min_curve_length_mm]
    if too_short:return {'status':'INVALID','reason':'CURVE_BELOW_NATIVE_TOLERANCE',
        'errors':[{'segment':i,'reason':'CURVE_BELOW_NATIVE_TOLERANCE','minimum_mm':profile.min_curve_length_mm} for i in too_short],
        'primitives':[]}
    for index,piece in enumerate(pieces):
        segments=split_passage_line(piece['start'],piece['end'],passages,radius) if route.get('split_passages') and piece['kind']=='LINE' else list(zip(piece['points_mm'],piece['points_mm'][1:]))
        for a,b in segments:
            samples.extend([a,b]);extra=piece.get('sagitta_mm',0)
            portal=passage_for(a,b,passages,radius)
            endpoint_ignore=[]
            for stub in route.get('endpoint_stubs',[]):
                if g.point_segment(*stub['points_mm'],a)<.01 and g.point_segment(*stub['points_mm'],b)<.01:
                    endpoint_ignore=stub.get('ignore_owner_ids',[])
            if portal:
                if space.forbidden_hit(a,b,radius+extra):errors.append({'segment':index,'reason':'PASSAGE_THROUGH_FORBIDDEN_REGION'})
                if space.collisions(a,b,radius+extra,portal['element_ids']):errors.append({'segment':index,'reason':'PASSAGE_COLLISION'})
            elif endpoint_ignore:
                if not space.stub_free(a,b,radius+extra,endpoint_ignore):errors.append({'segment':index,'reason':'CONNECTION_ENVELOPE'})
            elif not space.free(a,b,radius+extra):errors.append({'segment':index,'reason':'VOLUME_COLLISION_OR_OUTSIDE_ALLOWED_SPACE'})
    for p in route.get('waypoints_mm',[]):
        if not any(g.point_segment(a,b,p)<.5 for a,b in zip(samples,samples[1:]) if g.distance(a,b)>1e-6):
            errors.append({'reason':'WAYPOINT_NOT_ON_FINAL_PATH','point_mm':p})
    conditional=any(p['classification']=='CONDITIONAL' and p['id'] not in space.constraints.get('approved_passages',[]) for p in passages)
    status='INVALID' if errors else 'CONDITIONAL' if conditional else 'VALID_WITH_WARNINGS'
    length=sum(g.distance(p['start'],p['end']) if p['kind']=='LINE' else p['radius_mm']*p['angle_rad'] for p in pieces)
    horizontal=sum(math.hypot(b[0]-a[0],b[1]-a[1]) for p in pieces for a,b in zip(p['points_mm'],p['points_mm'][1:]))
    nonpreferred=0;proximity=0
    for piece in pieces:
        for a,b in zip(piece['points_mm'],piece['points_mm'][1:]):
            metres=g.distance(a,b)/1000
            nonpreferred+=metres*(space.preferred_penalty(a)+space.preferred_penalty(b))/2
            if profile.weights.get('proximity',0):
                proximity+=metres*(bool(space.collisions(a,a,radius+50))+bool(space.collisions(b,b,radius+50)))/2
    cost=calculate_route_cost(controls,profile.weights,passages,nonpreferred)
    cost['components']['length']=length/1000*profile.weights['length']
    cost['components']['proximity']=proximity*profile.weights.get('proximity',0)
    cost['total']=sum(cost['components'].values())
    if route.get('connection_validation_pending'):
        original=route['points_mm']
        samples=[tuple(original[0])]+samples+[tuple(original[-1])]
        length+=g.distance(original[0],controls[0])+g.distance(controls[-1],original[-1])
    return {'status':status,'errors':errors,'primitives':pieces,'length_m':length/1000,'horizontal_length_m':horizontal/1000,
            'elevation_change_mm':controls[-1][2]-controls[0][2],
            'bends':sum(p['kind']=='ARC' for p in pieces),
            'cost':cost,'sampled_points_mm':samples,
            'warnings':['Envolvente de codos provisional, pendiente de geometría de fabricante.']+(['Extremos y transiciones pendientes de comprobación sobre geometría real al modelar.'] if route.get('connection_validation_pending') else [])}
