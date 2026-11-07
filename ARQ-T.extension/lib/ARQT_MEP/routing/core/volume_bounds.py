"""Conservative box containment in one certified region or bore."""
import itertools
from shapely.geometry import box
from . import geometry as g


def planar_boundary_covered(rings,triangles,tolerance_mm=.01):
    """Prove coverage of exact polygon loops, including holes, by mesh pads."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    if not rings or tolerance_mm<0:return False
    outer=max(range(len(rings)),key=lambda i:Polygon(rings[i]).area)
    polygon=Polygon(rings[outer],[ring for i,ring in enumerate(rings) if i!=outer])
    if not polygon.is_valid or polygon.is_empty:return False
    pieces=[Polygon(t) for t in triangles if Polygon(t).is_valid and Polygon(t).area>0]
    return bool(pieces) and unary_union(pieces).buffer(tolerance_mm).covers(polygon)


def contained(bounds,regions,clearance=0,bores=()):
    corners=list(itertools.product(*[(bounds[i],bounds[i+3]) for i in range(3)]))
    footprint=box(bounds[0],bounds[1],bounds[3],bounds[4])
    for region in regions:
        if not region.polygon.covers(footprint):continue
        if clearance and region.polygon.boundary.distance(footprint)<clearance-1e-6:continue
        a,b,c=region.lower_coefficients;u,v,w=region.upper_coefficients
        if all((z-a*x-b*y-c)*region.lower_normal_z>=clearance-1e-6 and
               (u*x+v*y+w-z)*region.upper_normal_z>=clearance-1e-6 for x,y,z in corners):return True
    for portal in bores:
        a,b=portal['points_mm'];length=g.distance(a,b)
        if length<1e-6 or portal['radius_mm']<=clearance:continue
        axis=g.unit(g.sub(b,a))
        if all(0<=g.dot(g.sub(q,a),axis)<=length and g.point_segment(a,b,q)<=portal['radius_mm']-clearance for q in corners):return True
    return False
