"""Virtual instance frames transform ports and bodies without live Revit calls."""
import copy
import math
from ..core import geometry as g


def local(frame,point):
    delta=g.sub(point,frame['origin_mm'])
    return [g.dot(delta,frame['basis_'+axis]) for axis in ('x','y','z')]


def vector(frame,value):return [sum(value[j]*frame['basis_'+axis][i] for j,axis in enumerate(('x','y','z'))) for i in range(3)]
def point(frame,value):return list(g.add(frame['origin_mm'],vector(frame,value)))


def transform(p):
    result=copy.deepcopy(p)
    for previous in p.equipment.values():
        original=previous.get('original_transform');proposed=previous.get('proposed_transform')
        if not original or not proposed or original==proposed:continue
        device=next((d for d in result.catalog['devices'] if d['id']==previous['id']),None)
        if not device:raise ValueError('VIRTUAL_MANIFOLD_OUTDATED: distribuidor ausente.')
        if device.get('transform')!=original:raise ValueError('VIRTUAL_MANIFOLD_OUTDATED: el distribuidor real ha cambiado.')
        for port in device['connectors']:
            coordinates=local(original,port['xyz_mm']);direction=[g.dot(port['direction'],original['basis_'+axis]) for axis in ('x','y','z')]
            port['xyz_mm']=point(proposed,coordinates);port['direction']=vector(proposed,direction)
        device['transform']=copy.deepcopy(proposed)
        box=previous.get('body_box_mm')
        if box:
            corners=[point(proposed,local(original,q)) for q in __import__('itertools').product(*[(box[i],box[i+3]) for i in range(3)])]
            moved=g.bounds(corners);vertices,faces=g.box_mesh(moved)
            result.snapshot['obstacles']=[o for o in result.snapshot['obstacles'] if o['id']!=device['id']]
            result.snapshot['obstacles'].append({'id':device['id'],'element_id':device['element_id'],'element_uid':device['id'],
                'classification':'FORBIDDEN','box_mm':list(moved),'meshes':[{'vertices_mm':vertices,'faces':faces}]})
    return result


def edit_frame(manifold,operation,payload):
    frame=copy.deepcopy(manifold.get('proposed_transform'))
    if not frame:raise ValueError('Actualice el estudio para leer la transformación real del distribuidor.')
    if operation=='move_manifold':
        xy=payload['point_mm']
        if len(xy)!=2 or any(not math.isfinite(float(v)) for v in xy):raise ValueError('Posición virtual inválida.')
        frame['origin_mm'][:2]=[float(v) for v in xy]
    else:
        angle=math.radians(float(payload['angle_degrees']))
        if not math.isfinite(angle):raise ValueError('Ángulo virtual inválido.')
        for axis in ('x','y','z'):
            x,y,z=frame['basis_'+axis];frame['basis_'+axis]=[x*math.cos(angle)-y*math.sin(angle),x*math.sin(angle)+y*math.cos(angle),z]
    manifold['proposed_transform']=frame;manifold['validation_status']='VIRTUAL_MOVE_PENDING_RECHECK';manifold['main_connection_status']='NOT_EVALUATED'
