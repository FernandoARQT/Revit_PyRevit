"""Height bands from actual plenum planes and conservative local mesh cuts."""
import math
from shapely.geometry import Point,box
from ... import surface
from ..core import geometry as g


def envelope(profile,clearance_mm=0):
    if not math.isfinite(clearance_mm) or clearance_mm<0:raise ValueError('Holgura no válida.')
    return profile.external_diameter_mm/2+profile.insulation_thickness_mm+clearance_mm


def merge_intervals(values):
    result=[]
    for lo,hi in sorted(values):
        if lo>hi:continue
        if result and lo<=result[-1][1]+1e-7:result[-1][1]=max(hi,result[-1][1])
        else:result.append([lo,hi])
    return result


def _clip(triangle,axis,bound,greater):
    if not triangle:return []
    result=[]
    for a,b in zip(triangle,triangle[1:]+triangle[:1]):
        ia=(a[axis]>=bound) if greater else (a[axis]<=bound)
        ib=(b[axis]>=bound) if greater else (b[axis]<=bound)
        if ia:result.append(a)
        if ia!=ib:
            t=(bound-a[axis])/(b[axis]-a[axis])
            result.append(tuple(a[k]+t*(b[k]-a[k]) for k in (0,1,2)))
    return result


def _mesh_cuts(mesh,x,y,radius):
    """Project local surface pieces, plus solid interiors, onto the Z axis.

    Clipping to a square containing the circular footprint is conservative.
    Thin solids are retained; a high/low corner of a sloping roof elsewhere
    in the building cannot consume this entire vertical band.
    """
    eps=1e-5;r=radius+eps;cuts=[];hits=[]
    query=(x-r,y-r,mesh.box[2]-r,x+r,y+r,mesh.box[5]+r)
    origin=(x,y,mesh.box[2]-1)
    for triangle in mesh.bvh.query(query):
        polygon=list(triangle)
        for axis,bound,greater in ((0,x-r,True),(0,x+r,False),(1,y-r,True),(1,y+r,False)):
            polygon=_clip(polygon,axis,bound,greater)
            if not polygon:break
        if polygon:cuts.append((min(p[2] for p in polygon)-r,max(p[2] for p in polygon)+r))
        t=g.ray_triangle(origin,(0,0,1),*triangle)
        if t is not None and t>=0:hits.append(origin[2]+t)
    distinct=[]
    for z in sorted(hits):
        if not distinct or z-distinct[-1]>eps:distinct.append(z)
    for lo,hi in zip(distinct,distinct[1:]):
        if hi-lo>eps and mesh.inside((x,y,(lo+hi)/2)):cuts.append((lo-r,hi+r))
    return cuts


def valid_height_intervals(space,x,y,profile,clearance_mm=0,region_ids=None):
    radius=envelope(profile,clearance_mm);point=Point(x,y);intervals=[]
    key=(x,y,radius,None if region_ids is None else tuple(sorted(region_ids)))
    if not hasattr(space,'height_band_cache'):space.height_band_cache={}
    cached=space.height_band_cache.get(key)
    if cached is not None:return [list(v) for v in cached]
    for region in space.regions:
        if region_ids is not None and region.data.get('id') not in region_ids:continue
        if not region.polygon.covers(point) or region.boundary.distance(point)<radius-1e-6:continue
        lo=surface.height(region.lower,x,y)+radius/abs(region.lower_normal_z)
        hi=surface.height(region.upper,x,y)-radius/abs(region.upper_normal_z)
        if lo<=hi:intervals.append([lo,hi])
    intervals=merge_intervals(intervals)
    cuts=[]
    if intervals:
        a=(x,y,min(i[0] for i in intervals));b=(x,y,max(i[1] for i in intervals))
        for obstacle in space.candidates(a,b,radius):
            bounds=obstacle.box
            if box(bounds[0],bounds[1],bounds[3],bounds[4]).distance(point)>radius:continue
            if obstacle.meshes:
                for mesh in obstacle.meshes:cuts.extend(_mesh_cuts(mesh,x,y,radius))
            else:cuts.append((bounds[2]-radius,bounds[5]+radius))
    for b in space.forbidden:
        if box(b[0],b[1],b[3],b[4]).distance(point)<=radius:cuts.append((b[2]-radius,b[5]+radius))
    for cut in merge_intervals(cuts):
        remaining=[]
        for lo,hi in intervals:
            if cut[1]<lo or cut[0]>hi:remaining.append([lo,hi]);continue
            if cut[0]>lo:remaining.append([lo,cut[0]])
            if cut[1]<hi:remaining.append([cut[1],hi])
        intervals=remaining
    result=[[a+1e-4,b-1e-4] for a,b in intervals if b-a>2e-4]
    if len(space.height_band_cache)>20000:space.height_band_cache.clear()
    space.height_band_cache[key]=result
    return [list(v) for v in result]
