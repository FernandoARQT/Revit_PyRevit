"""IRIS-style separating-plane/maximum-volume-ellipsoid inflation and GCS.

GCS discrete choices are searched explicitly (bounded simple cell paths), then
each path is optimized continuously. This is NOT the global GCS relaxation.
Obstacle convexification is conservative; curved ducts are checked separately.
"""
import heapq
import itertools
import math
import time
import warnings
from collections import OrderedDict
from shapely.geometry import Point,mapping
from shapely.ops import triangulate,unary_union
from ...runtime import scientific_context
from . import geometry as g
from .route_geometry import validate_route_geometry

_HULLS=OrderedDict()
_STATIC_OBSTACLES=OrderedDict()
_REGION_PARTS=OrderedDict()


class Deadline(Exception):pass


def _check(deadline,cancelled):
    if time.monotonic()>=deadline or (cancelled and cancelled()):raise Deadline()


def convex_parts(polygon,deadline=float('inf'),cancelled=None):
    _check(deadline,cancelled)
    if polygon.equals(polygon.convex_hull):return [polygon]
    triangles=[p for p in triangulate(polygon) if polygon.covers(p)]
    rings=[list(polygon.exterior.coords[:-1])]+[list(r.coords[:-1]) for r in polygon.interiors]
    edges=[(a,b) for ring in rings for a,b in zip(ring,ring[1:]+ring[:1])]
    vertices=[p for ring in rings for p in ring];stride=max(1,math.ceil(len(vertices)/64))
    samples=set(vertices[::stride])
    # Project boundary vertices onto nearby edges. These additional points
    # allow convex covers to overlap at an L-shaped corner instead of merely
    # meeting along a triangulation diagonal. Every hull stays in the polygon.
    for x,y in vertices[::stride]:
        _check(deadline,cancelled);projections=[]
        for (ax,ay),(bx,by) in edges:
            dx,dy=bx-ax,by-ay;length=dx*dx+dy*dy
            if not length:continue
            t=max(0,min(1,((x-ax)*dx+(y-ay)*dy)/length));p=(ax+t*dx,ay+t*dy)
            projections.append(((x-p[0])**2+(y-p[1])**2,p))
        samples.update(p for _,p in sorted(projections)[:6])
    parts=[]
    # Grow from EACH triangle without consuming its neighbors. The resulting
    # cover may overlap: a disjoint triangulation would create artificial
    # radius-sized gaps when each convex polygon is eroded for a duct.
    for seed in triangles:
        current=seed;centroid=seed.centroid
        candidates=sorted(samples,key=lambda p:((p[0]-centroid.x)**2+(p[1]-centroid.y)**2,p))
        for candidate in candidates:
            _check(deadline,cancelled);point=Point(candidate)
            if current.covers(point):continue
            hull=current.union(point).convex_hull
            if polygon.covers(hull):current=hull
        if not any(p.equals(current) for p in parts):parts.append(current)
    return parts


def _merged_regions(space,deadline,cancelled):
    """Remove imaginary room seams only for exactly identical bounding planes."""
    from .spatial_model import Region
    groups={}
    for region in space.regions:
        _check(deadline,cancelled)
        key=(tuple(region.lower_coefficients),tuple(region.upper_coefficients))
        groups.setdefault(key,[]).append(region)
    result=[]
    for regions in groups.values():
        _check(deadline,cancelled)
        union=unary_union([r.polygon for r in regions])
        polygons=[union] if union.geom_type=='Polygon' else list(union.geoms)
        for polygon in polygons:
            if polygon.geom_type!='Polygon' or polygon.is_empty:continue
            result.append(Region({'id':'IRIS_COMPATIBLE_PLENUM_UNION','polygon':mapping(polygon),
                'lower':regions[0].lower,'upper':regions[0].upper}))
    return result


def _box_vertices(bounds,np,padding=.001):
    # Pad even planar/linear bounds so Qhull always receives a 3-D body.
    bounds=list(bounds)
    for axis in range(3):
        bounds[axis]-=padding;bounds[axis+3]+=padding
    return np.array(g.box_mesh(bounds)[0],dtype=float)


def _static_obstacles(space,np,ConvexHull,deadline,cancelled):
    result=[]
    for obstacle in space.obstacles:
        _check(deadline,cancelled)
        meshes=obstacle.data.get('meshes') or [{'vertices_mm':g.box_mesh(obstacle.box)[0]}]
        for mesh in meshes:
            _check(deadline,cancelled)
            vertices=mesh['vertices_mm'];key=(obstacle.id,tuple(tuple(v) for v in vertices))
            if key not in _HULLS:
                points=np.unique(np.array(vertices,dtype=float),axis=0)
                try:
                    hull=ConvexHull(points);points=points[hull.vertices]
                except (ValueError,RuntimeError):points=_box_vertices(obstacle.box,np)
                _HULLS[key]=points
                if len(_HULLS)>256:_HULLS.popitem(last=False)
            result.append(_HULLS[key])
    return result


def _obstacles(space,np,ConvexHull,deadline=float('inf'),cancelled=None):
    _check(deadline,cancelled);key=id(space.obstacles)
    # SpatialModel clones share immutable extracted solids. Retain the actual
    # list in the cache entry so Python cannot reuse its id for another scene.
    # New extraction/geometry revision creates a new list and misses the cache.
    entry=_STATIC_OBSTACLES.get(key)
    if entry is None or entry[0] is not space.obstacles:
        static=_static_obstacles(space,np,ConvexHull,deadline,cancelled)
        entry=(space.obstacles,static);_STATIC_OBSTACLES[key]=entry
        if len(_STATIC_OBSTACLES)>2:_STATIC_OBSTACLES.popitem(last=False)
    result=list(entry[1])
    # Restrictions and reservations are per attempt, never reused as static.
    for bounds in space.forbidden:
        _check(deadline,cancelled);result.append(_box_vertices(bounds,np))
    # A hull of endpoint cubes contains the whole reserved capsule, including
    # its end spheres. It is conservative but prevents carving cells through
    # previously protected routes. The new duct radius is added by inflate.
    for reservation in space.reservations:
        points=reservation['points'];radius=reservation['radius_mm']+.001
        pairs=list(zip(points,points[1:])) or [(p,p) for p in points]
        for a,b in pairs:
            _check(deadline,cancelled)
            vertices=[tuple(p[i]+signs[i]*radius for i in range(3))
                      for p in (a,b) for signs in itertools.product((-1,1),repeat=3)]
            vertices=np.unique(np.array(vertices,dtype=float),axis=0)
            hull=ConvexHull(vertices);result.append(vertices[hull.vertices])
    return result


def _region_parts(space,deadline,cancelled):
    _check(deadline,cancelled);key=id(space.regions);entry=_REGION_PARTS.get(key)
    if entry is None or entry[0] is not space.regions:
        pairs=[]
        for region in _merged_regions(space,deadline,cancelled):
            for polygon in convex_parts(region.polygon,deadline,cancelled):pairs.append((region,polygon))
        # Do not publish a partial cover when preparation is interrupted.
        entry=(space.regions,pairs);_REGION_PARTS[key]=entry
        if len(_REGION_PARTS)>2:_REGION_PARTS.popitem(last=False)
    return entry[1]


def inflate(space,region,polygon,seed,radius,obstacles,deadline,cancelled=None,iterations=3,diagnostics=None):
    """Return A*x<=b in mm, with a numerically contained inscribed ellipsoid.

    Each obstacle separator is verified against ALL its convex-hull vertices.
    Safety of the polytope does not depend on optimizer convergence.
    """
    diagnostics=diagnostics if diagnostics is not None else {}
    diagnostics.update(seed_mm=list(seed),radius_mm=radius,region_id=region.data.get('id'),
                       status='STARTED',optimizer_states=[])
    def failed(reason,vertices=None):
        diagnostics.update(status=reason)
        if vertices is not None:
            bounds=g.bounds(vertices.tolist())
            diagnostics['blocking_convex_bounds_mm']=bounds
            diagnostics['possible_blocking_elements']=[o.id for o in space.obstacles if g.overlaps(o.box,bounds)]
        return best
    with scientific_context():
        import numpy as np
        from scipy.spatial import ConvexHull
        from scipy.optimize import minimize
    _check(deadline,cancelled)
    origin=np.array(seed,dtype=float);poly=ConvexHull(np.array(polygon.exterior.coords[:-1]))
    base_a=[];base_b=[]
    for nx,ny,offset in poly.equations:
        normal=np.array([nx,ny,0.]);base_a.append(normal);base_b.append((-offset-radius-normal@origin)/1000)
    for coefficients,sign in ((region.lower_coefficients,1),(region.upper_coefficients,-1)):
        a,b,c=coefficients;normal=sign*np.array([a,b,-1.]);length=np.linalg.norm(normal)
        base_a.append(normal/length);base_b.append((-sign*c-radius*length-normal@origin)/length/1000)
    base_a=np.array(base_a);base_b=np.array(base_b)
    center=np.zeros(3);C=np.eye(3)*.001;best=None;volume=0
    def unpack(values):
        mat=np.zeros((3,3));mat[np.tril_indices(3)]=values[3:]
        return values[:3],mat
    for _ in range(iterations):
        _check(deadline,cancelled);aa=list(base_a);bb=list(base_b)
        inverse=np.linalg.inv(C);metric=inverse.T@inverse
        metric/=float(np.max(np.linalg.eigvalsh(metric)))
        ordered=sorted(obstacles,key=lambda v:float(np.min(np.linalg.norm((v-origin)/1000-center,axis=1))))
        for vertices in ordered:
            _check(deadline,cancelled)
            points=(vertices-origin)/1000
            # Existing separators may already exclude this entire obstacle.
            if any(np.min(points@a)>b+radius/1000-1e-8 for a,b in zip(aa,bb)):continue
            hull=ConvexHull(points);H=hull.equations[:,:3];h=-hull.equations[:,3]
            nearest=points[np.argmin(np.linalg.norm(points-center,axis=1))]
            def objective(x):
                _check(deadline,cancelled);d=x-center;return float(d@metric@d)
            projection=minimize(objective,nearest,jac=lambda x:2*metric@(x-center),
                constraints={'type':'ineq','fun':lambda x:h-H@x,'jac':lambda x:-H},method='SLSQP',
                options={'maxiter':30,'ftol':1e-8})
            diagnostics['optimizer_states'].append({'phase':'OBSTACLE_PROJECTION','success':bool(projection.success),'message':str(projection.message)})
            if not np.all(np.isfinite(projection.x)):return failed('PROJECTION_NUMERICAL_FAILURE',vertices)
            normal=metric@(projection.x-center);length=np.linalg.norm(normal)
            if length<1e-10:return failed('SEED_OR_CENTER_INSIDE_CONVEX_OBSTACLE',vertices)
            normal/=length;support=float(np.min(points@normal))-radius/1000-1e-6
            if float(normal@center)>=support:return failed('NO_CLEARANCE_FROM_CONVEX_OBSTACLE',vertices)
            aa.append(normal);bb.append(support)
        A=np.array(aa);b=np.array(bb);slack=b-A@center
        if np.min(slack)<=0:return failed('NO_POSITIVE_POLYTOPE_SLACK')
        # Verified separators already define a safe cell. A failed optional
        # ellipsoid maximization must not discard a thin but certified region.
        best={'A':A.tolist(),'b':(b*1000+A@origin).tolist(),'center':(center*1000+origin).tolist(),
              'ellipsoid_C_mm':(np.eye(3)*float(np.min(slack))*.2*1000).tolist(),'iterations':_}
        # Lower triangular positive-diagonal C parameterizes an ellipsoid;
        # log(det C) is concave and all support-function constraints are convex.
        initial=np.r_[center,(np.eye(3)*min(float(np.min(slack))*.25,.01))[np.tril_indices(3)]]
        diagonal=[3,5,8]
        bounds=[(None,None)]*9
        for i in diagonal:bounds[i]=(1e-7,None)
        def objective(values):
            _check(deadline,cancelled);return -float(np.log(values[diagonal]).sum())
        def constraint(values):
            c,M=unpack(values);return b-A@c-np.linalg.norm(A@M,axis=1)
        try:
            answer=minimize(objective,initial,bounds=bounds,constraints={'type':'ineq','fun':constraint},
                method='SLSQP',options={'maxiter':60,'ftol':1e-7})
        except Deadline:
            return best or {'A':A.tolist(),'b':(b*1000+A@origin).tolist(),'center':(center*1000+origin).tolist(),
                            'ellipsoid_C_mm':(np.eye(3)*float(np.min(slack))*.2*1000).tolist(),'iterations':0}
        diagnostics['optimizer_states'].append({'phase':'ELLIPSOID','success':bool(answer.success),'message':str(answer.message)})
        if not np.all(np.isfinite(answer.x)) or np.min(constraint(answer.x))<-1e-7:return failed('ELLIPSOID_NUMERICAL_FAILURE')
        center,C=unpack(answer.x);new_volume=float(np.linalg.det(C))
        best={'A':A.tolist(),'b':(b*1000+A@origin).tolist(),'center':(center*1000+origin).tolist(),
              'ellipsoid_C_mm':(C*1000).tolist(),'iterations':_+1}
        if new_volume<=volume*1.01:break
        volume=new_volume
    diagnostics['status']='CELL_GENERATED' if best else 'NO_CELL'
    return best


def _build_cells(space,start,goal,profile,admitted,limit,np,ConvexHull,deadline,cancelled,diagnostics=None):
    diagnostics=diagnostics if diagnostics is not None else {}
    started=time.monotonic();diagnostics.update(seeds_admitted=0,seed_attempts=[],generated=0,time_limit=False,
        generation_limit=False,scope='Conservative convex obstacles; failure is not proof of physical infeasibility')
    cells=[];seeds=[];seen=set()
    obstacles=_obstacles(space,np,ConvexHull,deadline,cancelled)
    endpoints=[start,goal]+[p for portal in admitted for p in portal['points_mm']]
    try:
        for region,polygon in _region_parts(space,deadline,cancelled):
            _check(deadline,cancelled)
            center=polygon.representative_point();xy=[(center.x,center.y)]
            # Boundary-biased seeds explore both sides of obstacles even
            # when a region's representative point lies inside a solid.
            xy.extend((.8*x+.2*center.x,.8*y+.2*center.y)
                      for x,y in polygon.exterior.coords[:-1])
            points=list(endpoints)
            for x,y in xy:
                lo=sum(v*w for v,w in zip(region.lower_coefficients,(x,y,1)))
                hi=sum(v*w for v,w in zip(region.upper_coefficients,(x,y,1)))
                points.append((x,y,(lo+hi)/2))
            for point in points:
                _check(deadline,cancelled)
                if polygon.covers(Point(point[:2])) and region.contains(point,profile.radius_mm):
                    priority=0 if tuple(point) in (tuple(start),tuple(goal)) else 1
                    seeds.append((priority,g.distance(start,point)+g.distance(point,goal),region,polygon,point))
        seeds.sort(key=lambda v:v[:2])
        diagnostics['seeds_admitted']=len(seeds)
        for _,_,region,polygon,point in seeds:
            _check(deadline,cancelled)
            if len(cells)>=limit:diagnostics['generation_limit']=True;break
            if any(np.max(np.array(cell['A'])@point-np.array(cell['b']))<=-1 for cell in cells):continue
            key=tuple(point)
            if key in seen:continue
            seen.add(key)
            if not space.free(point,point,profile.radius_mm):
                diagnostics['seed_attempts'].append({'seed_mm':list(point),'radius_mm':profile.radius_mm,
                    'status':'SEED_BLOCKED_IN_ORIGINAL_GEOMETRY','blocking_elements':space.collisions(point,point,profile.radius_mm)})
                continue
            cell_deadline=min(deadline,time.monotonic()+max(.01,(deadline-time.monotonic())*.25))
            record={};attempt_started=time.monotonic()
            try:cell=inflate(space,region,polygon,point,profile.radius_mm,obstacles,cell_deadline,cancelled,iterations=2,diagnostics=record)
            except Deadline:record['status']='CELL_TIME_LIMIT';cell=None
            record.update(seconds=time.monotonic()-attempt_started,cell_returned=bool(cell))
            diagnostics['seed_attempts'].append(record)
            if cell:cells.append(cell)
    except Deadline:diagnostics['time_limit']=True
    diagnostics.update(generated=len(cells),generation_seconds=time.monotonic()-started)
    return cells


def find_gcs(space,start,goal,profile,settings,scenario='A',cancelled=None,deadline=None):
    deadline=deadline or time.monotonic()+settings['search']['max_seconds']
    try:
        with scientific_context():
            import numpy as np
            from scipy.spatial import ConvexHull
            from scipy.optimize import linprog,minimize
    except ImportError:return {'status':'NOT_EVALUATED','reason':'IRIS_GCS_SCIPY_REQUIRED'}
    try:_check(deadline,cancelled)
    except Deadline:return {'status':'NOT_EVALUATED','reason':'CANCELLED' if cancelled and cancelled() else 'IRIS_GCS_TIME_LIMIT'}
    from .astar_3d import SparseGrid
    admitted=SparseGrid(space,start,goal,settings['search']['coarse_resolution_mm'],profile,settings['constraints']['passages'],scenario).portals
    limit=settings['coordination'].get('max_iris_cells',24)
    inflation_deadline=time.monotonic()+max(0,deadline-time.monotonic())*.5
    try:
        diagnostics={}
        from .candidate_repository import signature
        cache=getattr(space,'iris_cells',None)
        if cache is None:space.iris_cells=cache={}
        key=signature([getattr(space,'geometry_signature',space.data),profile.radius_mm,profile.bend_radius_mm,
            space.constraints,space.reservations,start,goal,scenario,limit])
        if key in cache:
            cells=cache[key];diagnostics['reused_cells']=len(cells)
        else:
            cells=_build_cells(space,start,goal,profile,admitted,limit,np,ConvexHull,inflation_deadline,cancelled,diagnostics)
            if cells:cache[key]=cells
            while len(cache)>32:cache.pop(next(iter(cache)))
    except Deadline:return {'status':'NOT_EVALUATED','reason':'CANCELLED' if cancelled and cancelled() else 'IRIS_PREPARATION_TIME_LIMIT'}
    except (ValueError,RuntimeError,np.linalg.LinAlgError) as exc:
        return {'status':'NOT_EVALUATED','reason':'IRIS_NUMERICAL_FAILURE','detail':str(exc)}
    if not cells:return {'status':'NOT_EVALUATED','reason':'IRIS_NO_CERTIFIED_CELL','iris_diagnostics':diagnostics}
    A=[np.array(c['A']) for c in cells];b=[np.array(c['b']) for c in cells]
    def contains(i,p):return np.max(A[i]@p-b[i])<=1e-5
    starts=[i for i in range(len(cells)) if contains(i,start)];goals={i for i in range(len(cells)) if contains(i,goal)}
    if not starts or not goals:
        return {'status':'NOT_EVALUATED','reason':'IRIS_ENDPOINT_NOT_COVERED','iris_cells':len(cells),
                'source_covered':bool(starts),'destination_covered':bool(goals),'iris_diagnostics':diagnostics}
    edges={i:[] for i in range(len(cells))};overlaps={};best=None;trials=0;expanded=0;timed_out=False;rejections=[]
    try:
        for i in range(len(cells)):
            for j in range(i):
                _check(deadline,cancelled)
                matrix=np.vstack([A[i],A[j]]);upper=np.r_[b[i],b[j]]
                # Maximum-clearance overlap knot, rather than an arbitrary LP
                # vertex that typically leaves no space to round a corner.
                padded=np.column_stack([matrix,np.linalg.norm(matrix,axis=1)])
                with warnings.catch_warnings():
                    warnings.filterwarnings('ignore',message='Unrecognized options detected.*threads.*')
                    answer=linprog([0,0,0,-1],A_ub=padded,b_ub=upper,bounds=[(None,None)]*3+[(0,None)],method='highs',
                        options={'time_limit':max(.001,deadline-time.monotonic()),'threads':1})
                if answer.success and np.all(np.isfinite(answer.x)) and np.max(padded@answer.x-upper)<=1e-5:
                    overlaps[i,j]=overlaps[j,i]=(matrix,upper,answer.x[:3])
                    edges[i].append((j,None));edges[j].append((i,None))
        for portal in admitted:
            _check(deadline,cancelled)
            pa,pb=portal['points_mm']
            for i in range(len(cells)):
                if not contains(i,pa):continue
                for j in range(len(cells)):
                    if i!=j and contains(j,pb):
                        edges[i].append((j,(portal,pa,pb)));edges[j].append((i,(portal,pb,pa)))
        queue=[];serial=itertools.count()
        for i in starts:heapq.heappush(queue,(0,next(serial),[i],[]))
        while queue and trials<8 and expanded<2000:
            _check(deadline,cancelled);_,_,path,transitions=heapq.heappop(queue);expanded+=1;i=path[-1]
            if i in goals:
                trials+=1;knots=[np.array(start)];constraints=[];passages=[]
                for left,right,portal in transitions:
                    if portal:
                        p,pa,pb=portal;knots.extend([np.array(pa),np.array(pb)]);passages.append(p)
                        constraints.extend([(len(knots)-2,None,np.array(pa)),(len(knots)-1,None,np.array(pb))])
                    else:
                        matrix,upper,x=overlaps[left,right];knots.append(x)
                        constraints.append((len(knots)-1,matrix,upper))
                # Optimize in local metres, not large Revit world-mm values.
                origin=np.array(start,dtype=float)
                knots.append(np.array(goal));initial=((np.array(knots[1:-1])-origin)/1000).ravel() if len(knots)>2 else np.array([])
                def unpack(x):return [np.array(start)]+list(x.reshape((-1,3))*1000+origin)+[np.array(goal)]
                def objective(x):
                    _check(deadline,cancelled);p=unpack(x);return sum(np.linalg.norm(v-u) for u,v in zip(p,p[1:]))/1000
                cons=[]
                for index,matrix,upper in constraints:
                    if matrix is None:cons.append({'type':'eq','fun':lambda x,k=index,p=(upper-origin)/1000:x.reshape((-1,3))[k-1]-p})
                    else:cons.append({'type':'ineq','fun':lambda x,k=index,M=matrix,h=(upper-matrix@origin)/1000:h-M@x.reshape((-1,3))[k-1]})
                answer=minimize(objective,initial,method='SLSQP',constraints=cons,options={'maxiter':60,'ftol':1e-8}) if len(initial) else None
                solution=answer.x if answer is not None else initial
                trajectories=[('INTERIOR_KNOTS',initial)]
                if not np.all(np.isfinite(solution)) or any(
                    (np.max(np.abs(c['fun'](solution)))>1e-7 if c['type']=='eq' else np.min(c['fun'](solution))<-1e-7) for c in cons):
                    rejections.append('CONTINUOUS_CELL_CONSTRAINTS_NOT_SATISFIED')
                else:trajectories.insert(0,('LENGTH_OPTIMIZED',solution))
                # Shortest and maximum-clearance trajectories are distinct
                # candidates: shortening must not erase a constructible turn.
                for mode,values in trajectories:
                    _check(deadline,cancelled);points=unpack(values);clean=[]
                    for p in points:
                        if not clean or g.distance(clean[-1],p)>1e-4:clean.append(tuple(float(v) for v in p))
                    route={'points_mm':clean,'passages':passages}
                    checked=validate_route_geometry(space,route,profile)
                    if checked['status'] not in ('INVALID','NOT_EVALUATED'):
                        route.update(checked);route.update(search_method='IRIS_GCS_EXPLICIT_PATH_SEARCH',iris_cells=len(cells),
                            gcs_paths_tested=trials,gcs_trajectory=mode)
                        if best is None or route['cost']['total']<best['cost']['total']:best=route
                    else:rejections.append(checked.get('reason','FINITE_RADIUS_GEOMETRY_REJECTED'))
            else:
                for j,portal in edges[i]:
                    if j in path:continue
                    cost=sum(g.distance(cells[a]['center'],cells[z]['center']) for a,z in zip(path+[j],(path+[j])[1:]))
                    heapq.heappush(queue,(cost,next(serial),path+[j],transitions+[(i,j,portal)]))
        if best:return best
    except Deadline:
        timed_out=True
        if best:return best
    except (ValueError,RuntimeError,np.linalg.LinAlgError) as exc:
        if best:return best
        return {'status':'NOT_EVALUATED','reason':'GCS_NUMERICAL_FAILURE','detail':str(exc),'iris_cells':len(cells)}
    return {'status':'NOT_EVALUATED','reason':('CANCELLED' if cancelled and cancelled() else
            'IRIS_GCS_TIME_LIMIT' if timed_out else 'GCS_BOUNDED_SEARCH_EXHAUSTED'),
            'iris_cells':len(cells),'gcs_paths_tested':trials,'gcs_nodes_expanded':expanded,'geometry_rejections':rejections}
