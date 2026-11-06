"""Bounded navmesh, medial, perimeter and visibility corridor candidates.

All accepted edges are continuously checked against the existing SpatialModel.
The graph is deliberately incomplete; a missing path is never impossibility.
No per-terminal 3D search, reservations, MILP or native model edits occur here.
"""
import hashlib
import math
import time
import itertools
from dataclasses import dataclass,asdict
from shapely.geometry import Point,LineString,box
from shapely.ops import triangulate,unary_union
from .spatial import valid_height_intervals,envelope
from ..core.spatial_model import build_spatial_model
from ... import surface


@dataclass
class CorridorNode:
    node_id:str
    xyz_mm:list
    level_id:int
    kind:str


def _id(prefix,value):return prefix+hashlib.sha256(repr(value).encode()).hexdigest()[:16]


def _parts(geom):
    if geom.is_empty:return []
    if geom.geom_type=='Polygon':return [geom] if geom.area>1e-6 else []
    return [p for p in getattr(geom,'geoms',[]) if p.geom_type=='Polygon' and not p.is_empty and p.area>1e-6]


def _boundary_samples(polygon,spacing):
    result=[]
    for ring in [polygon.exterior]+list(polygon.interiors):
        if ring.is_empty or ring.length<1e-6:continue
        count=max(4,int(math.ceil(ring.length/spacing)))
        result.extend(list(ring.interpolate(i/count,normalized=True).coords)[0] for i in range(count))
    return result


def _seeds(polygon,spacing):
    """Complementary representations; candidate links are checked later."""
    result=[];links={}
    triangles=[p for p in triangulate(polygon) if polygon.covers(p)]
    for triangle in triangles:
        point=triangle.representative_point();result.append((list(point.coords)[0],'CELL'))
    for part in _parts(polygon.buffer(-spacing/2,join_style=2)):
        for ring in [part.exterior]+list(part.interiors):
            points=list(ring.coords);perimeter=[]
            for a,b in zip(points,points[1:]):
                n=max(1,int(math.ceil(math.dist(a,b)/spacing)))
                for i in range(n):
                    xy=tuple(a[k]+(b[k]-a[k])*i/n for k in (0,1));result.append((xy,'PERIMETER'));perimeter.append(xy)
            for a,b in zip(perimeter,perimeter[1:]+perimeter[:1]):
                links[tuple(sorted((tuple(round(v,3) for v in a),tuple(round(v,3) for v in b))))]='PERIMETER'
    samples=_boundary_samples(polygon,spacing)
    if len(samples)>=4:
        from ...runtime import scientific_context
        with scientific_context():
            from scipy.spatial import Voronoi,QhullError
        try:
            voronoi=Voronoi(samples)
            for a,b in voronoi.ridge_vertices:
                if a<0 or b<0:continue
                u,v=tuple(voronoi.vertices[a]),tuple(voronoi.vertices[b])
                if math.dist(u,v)<1e-3 or not polygon.covers(LineString([u,v])):continue
                n=max(1,int(math.ceil(math.dist(u,v)/spacing)))
                medial=[tuple(u[k]+(v[k]-u[k])*i/n for k in (0,1)) for i in range(n+1)]
                result.extend((xy,'MEDIAL') for xy in medial)
                for a,b in zip(medial,medial[1:]):
                    links[tuple(sorted((tuple(round(v,3) for v in a),tuple(round(v,3) for v in b))))]='MEDIAL'
        except QhullError:pass
    # Deterministic deduplication keeps a bounded geometric representation.
    unique={}
    for xy,kind in result:unique.setdefault(tuple(round(v,3) for v in xy),(xy,kind))
    return list(unique.values()),links


def _width(space,point,tangent,region_ids):
    """Cross-section through actual planes and conservative obstruction bounds."""
    x,y,z=point;normal=(-tangent[1],tangent[0]);span=100000.
    cut=LineString([(x+normal[0]*span,y+normal[1]*span),(x-normal[0]*span,y-normal[1]*span)])
    polygons=[r.polygon for r in space.regions if r.data.get('id') in region_ids and
              surface.height(r.lower,x,y)<=z<=surface.height(r.upper,x,y)]
    if not polygons:return 0
    allowed=unary_union(polygons)
    blockers=[box(o.box[0],o.box[1],o.box[3],o.box[4]) for o in space.obstacles if o.box[2]<=z<=o.box[5]]
    blockers.extend(box(b[0],b[1],b[3],b[4]) for b in space.forbidden if b[2]<=z<=b[5])
    if blockers:allowed=allowed.difference(unary_union(blockers))
    section=allowed.intersection(cut);pieces=list(section.geoms) if hasattr(section,'geoms') else [section]
    point2=Point(x,y)
    return max((line.length for line in pieces if line.distance(point2)<1e-5),default=0)


def generate(snapshot,profile,levels,constraints=None,anchors=(),spacing_mm=450,clearance_mm=10,
             max_nodes=1600,seconds=30,cancelled=None):
    """levels maps plenum region IDs to actual Revit level IDs."""
    if not math.isfinite(spacing_mm) or spacing_mm<100 or type(max_nodes)!=int or not 10<=max_nodes<=10000:
        raise ValueError('Resolución/límite del grafo no válido.')
    if not math.isfinite(seconds) or not 0<seconds<=1000:raise ValueError('Presupuesto no válido.')
    started=time.monotonic();deadline=started+seconds;space=build_spatial_model(snapshot,constraints)
    radius=envelope(profile,clearance_mm);nodes={};edges={};complete=True;reason=[];preferred={};batches=[]
    level_regions={level:{rid for rid,lid in levels.items() if lid==level} for level in set(levels.values())}
    def stopped():return time.monotonic()>=deadline or bool(cancelled and cancelled())
    def add_seed(xy,kind,level):
        if len(nodes)>=max_nodes:return
        intervals=valid_height_intervals(space,*xy,profile,clearance_mm,level_regions.get(level,set()))
        for lo,hi in intervals:
            for z in sorted({lo+(hi-lo)*.25,lo+(hi-lo)*.75}):
                if len(nodes)>=max_nodes:return
                point=[float(xy[0]),float(xy[1]),float(z)]
                if not space.free(point,point,radius):continue
                nid=_id('node:',(level,tuple(round(v,3) for v in point)))
                nodes.setdefault(nid,asdict(CorridorNode(nid,point,level,kind)))
    for region in space.regions:
        if stopped():complete=False;reason.append('BUDGET_OR_CANCELLED');break
        level=levels.get(region.data.get('id'))
        if level is None:continue
        safe=region.polygon.buffer(-radius)
        for polygon in _parts(safe):
            seeds,links=_seeds(polygon,spacing_mm);preferred.update(links)
            batches.append(iter([(xy,kind,level) for xy,kind in seeds]))
    for anchor in anchors:
        if stopped():complete=False;break
        add_seed(anchor['xyz_mm'][:2],'ANCHOR',anchor['level_id'])
    # Cover all rooms before spending the budget refining the first region.
    for batch in itertools.zip_longest(*batches):
        if stopped() or len(nodes)>=max_nodes:complete=False;reason.append('SEED_LIMIT');break
        for seed in batch:
            if seed is not None:add_seed(*seed)
    values=list(nodes.values())
    if values:
        from ...runtime import scientific_context
        with scientific_context():
            from scipy.spatial import cKDTree
        tree=cKDTree([n['xyz_mm'] for n in values]);pairs=set();by_xy={};link_neighbors={}
        for i,n in enumerate(values):by_xy.setdefault(tuple(round(v,3) for v in n['xyz_mm'][:2]),[]).append(i)
        for a,b in preferred:link_neighbors.setdefault(a,set()).add(b);link_neighbors.setdefault(b,set()).add(a)
        for i,node in enumerate(values):
            if stopped():complete=False;reason.append('EDGE_BUDGET');break
            # Local visibility avoids a dense all-pairs graph over the dwelling.
            _,neighbors=tree.query(node['xyz_mm'],k=min(14,len(values)))
            xy_key=tuple(round(v,3) for v in node['xyz_mm'][:2])
            explicit={j for xy in link_neighbors.get(xy_key,()) for j in by_xy.get(xy,())}
            neighbors=sorted(explicit)+[int(j) for j in __import__('numpy').atleast_1d(neighbors) if int(j) not in explicit]
            for j in neighbors:
                if stopped():complete=False;reason.append('EDGE_BUDGET');break
                j=int(j)
                if i==j or (min(i,j),max(i,j)) in pairs:continue
                pairs.add((min(i,j),max(i,j)));other=values[j]
                if node['level_id']!=other['level_id']:continue
                a,b=node['xyz_mm'],other['xyz_mm'];length=math.dist(a,b);xy_length=math.dist(a[:2],b[:2])
                if xy_length<1e-3 or length>spacing_mm*5:continue
                # Avoid the expensive recursive union proof for arbitrary
                # visibility pairs. Inter-region links require explicit portals.
                if not any(r.segment(a,b,radius) for r in space.regions):continue
                if not space.free(a,b,radius):continue
                region_ids=[r.data['id'] for r in space.regions if r.polygon.intersects(LineString([a[:2],b[:2]]))]
                tangent=((b[0]-a[0])/xy_length,(b[1]-a[1])/xy_length)
                count=max(2,int(math.ceil(length/(spacing_mm/2)))+1);widths=[];heights=[]
                for n in range(count):
                    s=n/(count-1);point=[a[k]+(b[k]-a[k])*s for k in (0,1,2)]
                    widths.append({'station_mm':s*length,'width_mm':_width(space,point,tangent,region_ids)})
                    heights.append({'station_mm':s*length,'axis_intervals_mm':valid_height_intervals(space,*point[:2],profile,clearance_mm,level_regions[node['level_id']])})
                eid=_id('corridor:',tuple(sorted((node['node_id'],other['node_id']))))
                xy_pair=tuple(sorted((xy_key,tuple(round(v,3) for v in other['xyz_mm'][:2]))))
                kind=preferred.get(xy_pair,'VISIBILITY')
                edges[eid]={'corridor_id':eid,'node_ids':[node['node_id'],other['node_id']],
                    'reference_xyz_mm':[a,b],'reference_xy_mm':[a[:2],b[:2]],'network_type':'SHARED','level_id':node['level_id'],
                    'region_sequence':region_ids,'portal_sequence':[],'width_profile':widths,'height_profile':heights,
                    'length_mm':length,'construction_cost':length/1000,'kind':kind,
                    'validation_status':'SPATIAL_CANDIDATE','generated_by':'V3_FREE_SPACE_GRAPH'}
    return {'nodes':nodes,'edges':edges,'profile_id':profile.profile_id,'geometry_revision':snapshot['geometry_revision'],
            'configuration':{'spacing_mm':spacing_mm,'clearance_mm':clearance_mm,'max_nodes':max_nodes},
            'stats':{'seconds':time.monotonic()-started,'nodes':len(nodes),'edges':len(edges),'enumeration_finished':complete,
                'limits':sorted(set(reason)),'cancelled':bool(cancelled and cancelled())},
            'representation_complete':False,'physical_infeasibility_proven':False,
            'scope':'Continuous capsule-checked candidates. Sampled width/height sections; bends, bundles and connections are not certified.'}
