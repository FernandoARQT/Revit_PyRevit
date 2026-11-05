"""Conservative air volumes over compound walls between adjacent ceilings.

Ceiling footprints stop at wall faces. The space above the wall is still air
when the real wall solid stops lower. Bridges only fill known wall footprints;
they never suppress the collision check against that wall or the roof.
"""
import itertools
from shapely.geometry import LineString,mapping
from shapely.ops import unary_union


def derive(regions,obstacles):
    ceilings=[r for r in regions if r.kind=='FALSE_CEILING']
    strips=[LineString([p[:2] for p in o.data['wall_axis_mm']]).buffer(o.data['wall_thickness_mm']/2+2,cap_style=2)
            for o in obstacles if o.data.get('wall_axis_mm') and o.data.get('wall_thickness_mm',0)>0]
    if len(ceilings)<2 or not strips:return []
    walls=unary_union(strips);result=[]
    for a,b in itertools.combinations(ceilings,2):
        gap=a.polygon.distance(b.polygon)
        if not .01<gap<=600:continue
        bridge=walls.intersection(a.polygon.buffer(gap+2)).intersection(b.polygon.buffer(gap+2))
        bridge=bridge.difference(a.polygon.union(b.polygon))
        parts=list(bridge.geoms) if hasattr(bridge,'geoms') else [bridge]
        for polygon in parts:
            if polygon.is_empty or polygon.geom_type!='Polygon' or polygon.area<1:continue
            if polygon.distance(a.polygon)>1e-4 or polygon.distance(b.polygon)>1e-4:continue
            x0,y0,x1,y1=polygon.bounds
            if all(abs(x-y)<1e-6 for x,y in zip(a.lower_coefficients,b.lower_coefficients)):
                lower=a.lower
            else:
                lower={'z':max(u*x+v*y+w for u,v,w in (a.lower_coefficients,b.lower_coefficients)
                                   for x in (x0,x1) for y in (y0,y1))}
            # The lower of both upper planes is a conservative roof bound.
            # Use a horizontal minimum only when the planes differ: it never
            # invents headroom between dissimilar ceiling/roof slopes.
            upper=a.upper if a.upper_coefficients==b.upper_coefficients else {
                'z':min(u*x+v*y+w for u,v,w in (a.upper_coefficients,b.upper_coefficients)
                        for x in (x0,x1) for y in (y0,y1))}
            result.append({'id':'wall-gap-air:'+str(len(result)),'kind':'WALL_GAP_AIR','polygon':mapping(polygon),
                           'lower':lower,'upper':upper,'generated_by':'ADJACENT_CEILING_WALL_FOOTPRINTS',
                           'ceiling_ids':[a.data.get('id'),b.data.get('id')]})
    return result
