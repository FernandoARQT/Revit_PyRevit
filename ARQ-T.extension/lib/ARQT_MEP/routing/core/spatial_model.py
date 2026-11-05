"""Lazy occupied space from serialized solids and permitted volumes."""
import math
import time
import hashlib
import json
from collections import OrderedDict
from shapely.geometry import Point,LineString,MultiPoint,shape,box
from shapely.prepared import prep
from shapely.ops import unary_union
from . import geometry as g
from ... import surface

_CACHE=OrderedDict()


def _half_plane(polygon,coefficients,limit,bounds):
    """Clip XY by a*x+b*y+c <= limit; preserve polygon holes via intersection."""
    a,b,c=coefficients
    if abs(a)+abs(b)<1e-12:return polygon if c<=limit+1e-7 else polygon.difference(polygon)
    x0,y0,x1,y1=bounds;corners=[(x0,y0),(x1,y0),(x1,y1),(x0,y1)];clipped=[]
    for p,q in zip(corners,corners[1:]+corners[:1]):
        u=a*p[0]+b*p[1]+c-limit;v=a*q[0]+b*q[1]+c-limit
        if u<=0:clipped.append(p)
        if (u<=0)!=(v<=0):
            t=u/(u-v);clipped.append((p[0]+t*(q[0]-p[0]),p[1]+t*(q[1]-p[1])))
    if len(clipped)<3:return polygon.difference(polygon)
    from shapely.geometry import Polygon
    return polygon.intersection(Polygon(clipped))


class Region:
    def __init__(self,data):
        self.data=data;self.kind=data.get('kind','EXPLICIT')
        if 'box_mm' in data:
            b=data['box_mm'];self.polygon=box(b[0],b[1],b[3],b[4])
            self.lower={'z':b[2]};self.upper={'z':b[5]}
        else:
            self.polygon=shape(data['polygon']);self.lower=data['lower'];self.upper=data['upper']
        self.bounds=self.polygon.bounds;self.boundary=self.polygon.boundary;self.prepared=prep(self.polygon)
        self.lower_coefficients=surface.coefficients(self.lower);self.upper_coefficients=surface.coefficients(self.upper)
        self.lower_normal_z=surface.plane(self.lower)['normal'][2];self.upper_normal_z=surface.plane(self.upper)['normal'][2]

    def contains(self,p,r=0):
        x,y,z=p
        x0,y0,x1,y1=self.bounds
        if x-r<x0-1e-6 or y-r<y0-1e-6 or x+r>x1+1e-6 or y+r>y1+1e-6:return False
        a,b,c=self.lower_coefficients;lower=(z-(a*x+b*y+c))*self.lower_normal_z
        a,b,c=self.upper_coefficients;upper=((a*x+b*y+c)-z)*self.upper_normal_z
        if min(lower,upper)<r-1e-6:return False
        point=Point(x,y)
        return self.prepared.covers(point) and (not r or self.boundary.distance(point)>=r-1e-6)

    def segment(self,a,b,r):
        line=LineString([a[:2],b[:2]]) if a[:2]!=b[:2] else Point(*a[:2])
        return self.contains(a,r) and self.contains(b,r) and self.polygon.covers(line) and self.boundary.distance(line)>=r-1e-6

    def polyhedron(self,vertices):
        from shapely.geometry import MultiPoint
        if not self.polygon.covers(MultiPoint([p[:2] for p in vertices]).convex_hull):return False
        return all(p[2]>=surface.height(self.lower,*p[:2])-1e-6 and p[2]<=surface.height(self.upper,*p[:2])+1e-6 for p in vertices)


class Obstacle:
    def __init__(self,data):
        self.data=data;self.id=data['id'];self.classification=data.get('classification','UNKNOWN')
        self.meshes=[g.TriangleSolid(m['vertices_mm'],m['faces']) for m in data.get('meshes',[])]
        self.box=tuple(data['box_mm'])

    def hit(self,a,b,r):
        if not g.overlaps(self.box,g.expand(g.bounds([a,b]),r)):return False
        return any(m.capsule(a,b,r) for m in self.meshes) if self.meshes else True


class SpatialModel:
    def __init__(self,data,constraints=None):
        started=time.monotonic()
        self.data=data;self.constraints=constraints or {}
        self.regions=[Region(r) for r in data.get('regions',[])+self.constraints.get('allowed_regions',[])]
        self._continuous_regions=None
        self.obstacles=[Obstacle(o) for o in data.get('obstacles',[])]
        self.collision_cache=OrderedDict()
        self.roadmap_cache=OrderedDict()
        self.union_cache=OrderedDict()
        self.tree=g.BVH(self.obstacles,lambda o:o.box) if self.obstacles else None
        self.forbidden=[tuple(z['box_mm']) for z in self.constraints.get('forbidden_regions',[])]
        self.preferred=[Region(z) for z in self.constraints.get('preferred_regions',[])]
        self.reservations=[];self.stats={'collision_queries':0,'cell_queries':0,'spatial_build_seconds':time.monotonic()-started}

    def candidates(self,a,b,r):
        return self.tree.query(g.expand(g.bounds([a,b]),r)) if self.tree else ()

    def collisions(self,a,b,r,ignore=()):
        self.stats['collision_queries']+=1
        a,b=tuple(a),tuple(b)
        if b<a:a,b=b,a
        key=(a,b,r,tuple(sorted(ignore)))
        cached=self.collision_cache.get(key)
        if cached is not None:return list(cached)
        result=[o.id for o in self.candidates(a,b,r) if (o.id not in ignore or o.data.get('hard_forbidden')) and o.hit(a,b,r)]
        self.collision_cache[key]=tuple(result)
        if len(self.collision_cache)>50000:self.collision_cache.popitem(last=False)
        return result

    def allowed(self,a,b,r):
        if self.forbidden_hit(a,b,r):return False
        if a==b:return self.union_contains(a,r)
        if any(region.segment(a,b,r) for region in self.regions):return True
        if self._continuous_regions is None:
            groups={}
            for region in self.regions:
                key=(tuple(region.lower_coefficients),tuple(region.upper_coefficients))
                groups.setdefault(key,[]).append(region)
            self._continuous_regions=[]
            from shapely.geometry import mapping
            for regions in groups.values():
                poly=unary_union([v.polygon for v in regions])
                self._continuous_regions.append(Region({'polygon':mapping(poly),
                    'lower':regions[0].lower,'upper':regions[0].upper}))
        if any(region.segment(a,b,r) for region in self._continuous_regions):return True
        # Continuous circumscribed prism proof over the UNION. Subdivision
        # tightens its vertical range; search resolution never enlarges r.
        def covered(u,v,depth=0):
            xy=LineString([u[:2],v[:2]]).buffer(max(r/math.cos(math.pi/64),1e-7),quad_segs=16)
            if self._union_prism(xy,min(u[2],v[2])-r,max(u[2],v[2])+r):return True
            if depth>=10 or g.distance(u,v)<=.5:return False
            mid=g.mul(g.add(u,v),.5)
            if not self.union_contains(mid,r):return False
            return covered(u,mid,depth+1) and covered(mid,v,depth+1)
        if not self.union_contains(a,r) or not self.union_contains(b,r):return False
        return covered(a,b)

    def _union_prism(self,footprint,lower,upper):
        bounds=footprint.bounds;parts=[]
        for region in self.regions:
            polygon=region.polygon.intersection(footprint)
            if polygon.is_empty:continue
            polygon=_half_plane(polygon,region.lower_coefficients,lower,bounds)
            polygon=_half_plane(polygon,tuple(-v for v in region.upper_coefficients),-upper,bounds)
            if not polygon.is_empty:parts.append(polygon)
        return bool(parts) and unary_union(parts).covers(footprint)

    def union_contains(self,p,r):
        if any(region.contains(p,r) for region in self.regions):return True
        key=tuple(p)+(r,)
        if key in self.union_cache:return self.union_cache[key]
        x,y,z=p
        # A circumscribed disk and full vertical band form a conservative
        # cylinder around the sphere. Combining clipped footprints proves the
        # whole cylinder is inside the UNION, including inclined boundaries.
        radius=r/math.cos(math.pi/64) if r else 1e-7
        bounds=(x-radius-1,y-radius-1,x+radius+1,y+radius+1);patch=box(*bounds);parts=[]
        for region in self.regions:
            x0,y0,x1,y1=region.bounds
            if x0>bounds[2] or x1<bounds[0] or y0>bounds[3] or y1<bounds[1]:continue
            polygon=region.polygon.intersection(patch)
            if polygon.is_empty:continue
            polygon=_half_plane(polygon,region.lower_coefficients,z-r,bounds)
            polygon=_half_plane(polygon,tuple(-v for v in region.upper_coefficients),-z-r,bounds)
            if not polygon.is_empty:parts.append(polygon)
        result=bool(parts) and unary_union(parts).covers(Point(x,y).buffer(radius,quad_segs=16))
        self.union_cache[key]=result
        if len(self.union_cache)>20000:self.union_cache.popitem(last=False)
        return result

    def forbidden_hit(self,a,b,r):
        for zone in self.forbidden:
            if not g.overlaps(zone,g.expand(g.bounds([a,b]),r)):continue
            vertices,faces=g.box_mesh(zone)
            if g.TriangleSolid(vertices,faces).capsule(a,b,r):return True
        return False

    def polyhedron_allowed(self,vertices):
        """Conservative whole-body proof, including unlocated/outside geometry.

        A convex hull projection and full Z band enclose the entire body.
        Failing this proof never authorizes geometry based on sampled vertices.
        """
        if not vertices:return False
        if any(region.polyhedron(vertices) for region in self.regions):return True
        return self._union_prism(MultiPoint([p[:2] for p in vertices]).convex_hull,
                                 min(p[2] for p in vertices),max(p[2] for p in vertices))

    def free(self,a,b,r,ignore=(),reservation_ignore=None):
        if not self.allowed(a,b,r) or self.collisions(a,b,r,ignore):return False
        for path in self.reservations:
            if path['id']==reservation_ignore:continue
            if any(g.segment_distance(a,b,c,d)<r+path['radius_mm']-1e-6 for c,d in zip(path['points'],path['points'][1:])):return False
        return True

    def stub_free(self,a,b,r,ignore=()):
        vertices,faces=g.cylinder_mesh(a,b,r)
        if not any(region.polyhedron(vertices) for region in self.regions):
            if not self._union_prism(MultiPoint([p[:2] for p in vertices]).convex_hull,
                                     min(p[2] for p in vertices),max(p[2] for p in vertices)):return False
        query=g.bounds(vertices);swept=None
        for o in self.tree.query(query) if self.tree else ():
            if (o.id in ignore and not o.data.get('hard_forbidden')) or not g.overlaps(o.box,query):continue
            if not o.meshes:return False
            swept=swept or g.TriangleSolid(vertices,faces)
            if any(m.intersects(swept) for m in o.meshes):return False
        swept=swept or g.TriangleSolid(vertices,faces)
        if any(g.overlaps(z,query) and swept.intersects(g.TriangleSolid(*g.box_mesh(z))) for z in self.forbidden):return False
        return not any(g.segment_distance(a,b,c,d)<r+path['radius_mm']-1e-6 for path in self.reservations for c,d in zip(path['points'],path['points'][1:]))

    def reserve(self,route_id,points,radius):
        # Query envelopes already include their clearance. Keeping it on the
        # reserved tube as well would turn a 15 mm gap into 30 mm.
        physical_radius=float(radius)-getattr(radius,'clearance_mm',0)
        self.reservations.append({'id':route_id,'points':[tuple(p) for p in points],'radius_mm':physical_radius})

    def clone(self):
        clone=object.__new__(SpatialModel);clone.__dict__=dict(self.__dict__)
        clone.reservations=list(self.reservations)
        return clone

    def preferred_penalty(self,p):
        return 0 if not self.preferred or any(r.contains(p) for r in self.preferred) else 1


def build_spatial_model(serialized,constraints=None):
    geometry={'regions':serialized.get('regions',[]),'obstacles':serialized.get('obstacles',[])}
    key=hashlib.sha256(json.dumps(geometry,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()
    cached=key in _CACHE
    if not cached:
        _CACHE[key]=SpatialModel(serialized)
        while len(_CACHE)>2:_CACHE.popitem(last=False)
    _CACHE.move_to_end(key)
    model=_CACHE[key].clone();model.data=serialized;model.constraints=constraints or {};model.geometry_signature=key
    model.regions=[Region(r) for r in serialized.get('regions',[])+model.constraints.get('allowed_regions',[])]
    from .plenum_bridges import derive
    bridges=derive(model.regions,model.obstacles)
    model.regions.extend(Region(r) for r in bridges)
    model._continuous_regions=None
    model.union_cache=OrderedDict()
    model.forbidden=[tuple(z['box_mm']) for z in model.constraints.get('forbidden_regions',[])]
    model.preferred=[Region(z) for z in model.constraints.get('preferred_regions',[])]
    model.stats=dict(model.stats,collision_queries=0,cell_queries=0,geometry_cache_hit=cached,wall_gap_air_regions=len(bridges),
                     spatial_build_seconds=0 if cached else model.stats['spatial_build_seconds'])
    return model


def inflate_obstacles(model,profile):
    """An exact radius query is used instead of materializing inflated solids."""
    return {'model':model,'effective_radius_mm':profile.radius_mm,'partial_cells':'centerline samples; each edge checked as a continuous full-radius capsule'}
