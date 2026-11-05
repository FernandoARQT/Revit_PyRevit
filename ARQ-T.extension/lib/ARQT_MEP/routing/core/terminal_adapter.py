"""Discrete physical outlet poses around the actual grille connector axis."""
import copy
import json
import math
from . import geometry as g

FAMILY='Fraenkische_profi-air_classic_Codo rejilla'
TYPE='profi-air classic outlet 90° plus - 2xNW 90 / DN 125 - short'
ANGLES=(0,-15,15,-30,30,-45,45)

def rotate(vector,axis,radians):
    n=g.unit(axis);c=math.cos(radians);s=math.sin(radians)
    return g.add(g.add(g.mul(vector,c),g.mul(g.cross(n,vector),s)),g.mul(n,g.dot(n,vector)*(1-c)))

def variants(adapter,source,network):
    pivot=adapter['pivot_mm'];axis=list(g.unit(adapter['axis']));result=[]
    middle=g.mul(g.add(adapter['outlets'][0]['xyz_mm'],adapter['outlets'][1]['xyz_mm']),.5)
    ref=g.sub(middle,pivot);ref=g.sub(ref,g.mul(axis,g.dot(ref,axis)))
    target=g.sub(source['xyz_mm'],pivot);target=g.sub(target,g.mul(axis,g.dot(target,axis)))
    if g.norm(ref)<1e-7 or g.norm(target)<1e-7:raise ValueError('Orientación del adaptador indeterminada respecto del distribuidor.')
    ref=g.unit(ref);target=g.unit(target)
    base=math.atan2(g.dot(axis,g.cross(ref,target)),g.dot(ref,target))
    for angle in ANGLES:
        radians=base+math.radians(angle)
        pose={'element_id':adapter['element_id'],'element_key':adapter['id'],'terminal_port':adapter['terminal_port'],
            'pivot_mm':pivot,'axis':axis,'rotation_rad':radians,'offset_deg':angle,'type_id':adapter['type_id'],
            'captured_outlets':copy.deepcopy(adapter['outlets']),
            'geometry_validation':'PENDING_NATIVE_BODY_CHECK'}
        if adapter.get('body_corners_mm'):
            corners=[g.add(pivot,rotate(g.sub(p,pivot),axis,radians)) for p in adapter['body_corners_mm']]
            pose['body_box_mm']=list(g.bounds(corners))
        for port in adapter['outlets']:
            if port['connected']:continue
            item=copy.deepcopy(port)
            item.update(xyz_mm=list(g.add(pivot,rotate(g.sub(port['xyz_mm'],pivot),axis,radians))),
                direction=list(rotate(port['direction'],axis,radians)),kind='TERMINAL_ADAPTER_OUTLET',network_type=network,
                adapter_pose=copy.deepcopy(pose),physical_id=port['id'],id=port['id']+':pose:'+str(angle))
            result.append(item)
    return result

def destination(route,request):
    return route.get('chosen_destination') or route.get('destination') or request['destination']

def allowed_destination(port,request):
    # Worker/checkpoint transport preserves JSON values, not Python's tuple
    # type. Check the entire descriptor after normalizing sequence types;
    # matching just the id would accept altered coordinates or rotations.
    for expected in [request['destination']]+request.get('destination_alternatives',[]):
        if port.get('id')!=expected.get('id'):continue
        return json.loads(json.dumps(port,allow_nan=False))==json.loads(json.dumps(expected,allow_nan=False))
    return False


def posed_space(space,pose):
    """Replace the adapter's captured obstacle with its proposed body bound.

    A rotated outlet must not use anchors computed against the old body.
    Other equipment and all forbidden building elements remain obstacles.
    """
    if not pose or not pose.get('body_box_mm'):return space
    token=(pose['element_key'],tuple(pose['body_box_mm']))
    if getattr(space,'terminal_pose_token',None)==token:return space
    from collections import OrderedDict
    from .spatial_model import Obstacle
    box=pose['body_box_mm'];vertices,faces=g.box_mesh(box)
    body={'id':pose['element_key'],'element_id':pose['element_id'],'box_mm':box,
        'classification':'FORBIDDEN','meshes':[{'vertices_mm':vertices,'faces':faces}]}
    model=space.clone()
    model.obstacles=[o for o in space.obstacles if o.id!=pose['element_key'] or o.data.get('hard_forbidden')]
    model.obstacles.append(Obstacle(body));model.tree=g.BVH(model.obstacles,lambda o:o.box)
    model.collision_cache=OrderedDict();model.roadmap_cache=OrderedDict()
    model.data=dict(space.data,obstacles=[o.data for o in model.obstacles])
    model.geometry_signature=str(getattr(space,'geometry_signature',''))+repr(token)
    model.terminal_pose_token=token
    return model
