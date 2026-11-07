"""Construction geometry and sparse native controls, separate from clash samples."""
import math
from . import geometry as g
from .route_geometry import primitives


def straight_vertices(points,tolerance_mm=.01):
    result=[]
    for point in points:
        point=tuple(point)
        if result and g.distance(result[-1],point)<=1e-6:continue
        while len(result)>1:
            a,b=result[-2:]
            if g.dot(g.sub(b,a),g.sub(point,b))<=0:break
            if g.point_segment(a,point,b)>tolerance_mm:break
            result.pop()
        result.append(point)
    return result


def build(points,bend_radius_mm):
    vertices=straight_vertices(points)
    if len(vertices)<2:raise ValueError('Trazado sin longitud construible.')
    pieces=primitives(vertices,bend_radius_mm);segments=[];controls=[]
    def append(p):
        if not controls or g.distance(controls[-1],p)>1e-6:controls.append(list(p))
    for piece in pieces:
        a,b=piece['start'],piece['end'];append(a)
        if piece['kind']=='LINE':
            segment={'kind':'LINE','start_mm':list(a),'end_mm':list(b),'points_mm':[list(a),list(b)]}
        else:
            center=piece['center'];axis=piece['axis'];v=g.sub(a,center);theta=piece['angle_rad']/2
            middle=g.add(center,g.add(g.mul(v,math.cos(theta)),g.mul(g.cross(axis,v),math.sin(theta))))
            # One support per arc for Revit's whole-flex spline. These are
            # interpolation supports, not cell points or Bézier handles.
            append(middle)
            segment={'kind':'ARC','start_mm':list(a),'end_mm':list(b),'center_mm':list(center),
                'radius_mm':piece['radius_mm'],'angle_rad':piece['angle_rad'],'axis':list(axis),
                'start_tangent':list(g.unit(g.cross(axis,g.sub(a,center)))),
                'end_tangent':list(g.unit(g.cross(axis,g.sub(b,center))))}
        append(b);segments.append(segment)
    return {'segments':segments,'vertices_mm':[list(p) for p in vertices],
        'native_controls_mm':controls,'native_control_count':len(controls),
        'scope':'Tangency geometry; native FlexDuct interpolation still requires body validation'}


def interpolation_supports(pieces,spacing_mm):
    """Uniform supports for one native flexible curve, preserving exact arcs."""
    if not math.isfinite(spacing_mm) or spacing_mm<=0:raise ValueError('Separación de apoyos inválida.')
    result=[]
    for piece in pieces:
        length=g.distance(piece['start'],piece['end']) if piece['kind']=='LINE' else piece['radius_mm']*piece['angle_rad']
        count=max(1,math.ceil(length/spacing_mm))
        for i in range(count+1):
            t=i/count
            if piece['kind']=='LINE':point=g.add(piece['start'],g.mul(g.sub(piece['end'],piece['start']),t))
            else:
                vector=g.sub(piece['start'],piece['center']);theta=piece['angle_rad']*t
                point=g.add(piece['center'],g.add(g.mul(vector,math.cos(theta)),g.mul(g.cross(piece['axis'],vector),math.sin(theta))))
            if not result or g.distance(result[-1],point)>1e-5:result.append(list(point))
    return result
