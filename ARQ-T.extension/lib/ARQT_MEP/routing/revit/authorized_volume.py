"""Native body minus a conservative authorised volume, including local bores.

All solids are transient. A failed boolean never authorises geometry. Region
joins add only volumes already contained in the original union.
"""
import math
import time
from System.Collections.Generic import List
from pyrevit.api import DB
from shapely.ops import triangulate,unary_union
from shapely.geometry import mapping
try:
    from shapely import constrained_delaunay_triangles
except ImportError:
    constrained_delaunay_triangles=None
from ... import revit_read as rr
from ..core import geometry as g
from ..core.spatial_model import Region
from ..core.iris_gcs import _merged_regions
from ..core.polygon_cover import native_subset
from .proposals import xyz,swept_solid,_solid_box


def box_solid(bounds):
    x0,y0,z0,x1,y1,z1=bounds
    points=[xyz(p) for p in ((x0,y0,z0),(x1,y0,z0),(x1,y1,z0),(x0,y1,z0))]
    loop=DB.CurveLoop()
    for a,b in zip(points,points[1:]+points[:1]):loop.Append(DB.Line.CreateBound(a,b))
    loops=List[DB.CurveLoop]();loops.Add(loop)
    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(loops,DB.XYZ.BasisZ,rr.internal(z1-z0))


def oriented_clearance_bound(solid,clearance):
    """Rigidly oriented bound encloses the solid plus its full clearance.

    Choosing a face normal only improves tightness. Containment follows from
    the transformed native bounding box, irrespective of that choice.
    """
    faces=[f for f in solid.Faces if isinstance(f,DB.PlanarFace)]
    if not faces:return box_solid(g.expand(_solid_box(solid),clearance))
    normal=max(faces,key=lambda f:f.Area).FaceNormal.Normalize()
    ref=DB.XYZ.BasisX if abs(normal.X)<.8 else DB.XYZ.BasisY
    x=ref.CrossProduct(normal).Normalize();y=normal.CrossProduct(x).Normalize()
    frame=DB.Transform.Identity;frame.Origin=solid.ComputeCentroid()
    frame.BasisX=x;frame.BasisY=y;frame.BasisZ=normal
    local=DB.SolidUtils.CreateTransformed(solid,frame.Inverse)
    bound=box_solid(g.expand(_solid_box(local),clearance))
    return DB.SolidUtils.CreateTransformed(bound,frame)


def permitted(portal,constraints):
    if portal['classification']=='PASSABLE':return True
    record=constraints.get('passage_approvals',{}).get(portal['id'],{})
    return portal['id'] in constraints.get('approved_passages',[]) and bool(record.get('responsible') and record.get('reference'))


class AuthorizedVolume:
    def __init__(self,space,clearance_mm=0,passages=()):
        self.space=space;self.clearance=clearance_mm;self.cache={};self.bores=[]
        self.regions=_merged_regions(space,float('inf'),None)
        extra=[]
        for i,a in enumerate(self.regions):
            for b in self.regions[:i]:
                if a.polygon.distance(b.polygon)>1e-6:continue
                shared=a.polygon.intersection(b.polygon)
                if shared.is_empty:continue
                patch=a.polygon.union(b.polygon).intersection(shared.buffer(100))
                if patch.is_empty:continue
                x0,y0,x1,y1=patch.bounds
                lower=max(u*x+v*y+w for r in (a,b) for u,v,w in [r.lower_coefficients] for x in (x0,x1) for y in (y0,y1))
                upper=min(u*x+v*y+w for r in (a,b) for u,v,w in [r.upper_coefficients] for x in (x0,x1) for y in (y0,y1))
                if upper>lower:extra.append(Region({'polygon':mapping(patch),'lower':{'z':lower},'upper':{'z':upper}}))
        self.regions.extend(extra)
        for p in passages:
            owners=[o for o in space.obstacles if o.id in p['element_ids']]
            if len(owners)!=len(set(p['element_ids'])) or any(o.data.get('hard_forbidden') or o.classification in ('FORBIDDEN','UNKNOWN') for o in owners):continue
            if any(o.classification=='CONDITIONAL' for o in owners) and p['classification']!='CONDITIONAL':continue
            if permitted(p,space.constraints) and p['radius_mm']>clearance_mm:
                self.bores.append((p,swept_solid({'kind':'LINE','start':p['points_mm'][0],'end':p['points_mm'][1]},p['radius_mm']-clearance_mm)))

    def _domain_bore(self,portal):
        """Only a local wall bore with proven common bounding planes adds air.

        Unbounded explicit tunnels, roofs and uncertain hosts never extend the
        authorized domain. Other legitimate cases remain unverified.
        """
        owners=[o for o in self.space.obstacles if o.id in portal['element_ids']]
        if len(owners)!=1 or not owners[0].data.get('wall_axis_mm'):return False
        a,b=portal['points_mm'];radius=portal['radius_mm']
        if g.distance(a,b)>owners[0].data.get('wall_thickness_mm',0)+2*radius+60:return False
        if not all(self.space.allowed(q,q,radius) for q in (a,b)):return False
        left=[r for r in self.space.regions if r.contains(a,0)];right=[r for r in self.space.regions if r.contains(b,0)]
        def planes_fit(region):
            u,v,w=region.lower_coefficients;x,y,z=region.upper_coefficients
            return all((q[2]-u*q[0]-v*q[1]-w)*region.lower_normal_z>=radius-1e-6 and
                       (x*q[0]+y*q[1]+z-q[2])*region.upper_normal_z>=radius-1e-6 for q in (a,b))
        return any(planes_fit(r) and planes_fit(s) for r in left for s in right)

    def _solids(self,index):
        if index in self.cache:return self.cache[index]
        region=self.regions[index];r=self.clearance;errors=[];solids=[]
        polygon=region.polygon.buffer(-r/math.cos(math.pi/64)) if r else region.polygon
        parts=list(polygon.geoms) if hasattr(polygon,'geoms') else [polygon]
        for part in parts:
            if part.is_empty or part.geom_type!='Polygon':continue
            # A whole parallel-plane prism needs far fewer native boolean
            # operations than a triangulation and avoids artificial slivers.
            low=region.lower_coefficients;high=region.upper_coefficients
            heights=[(high[0]-low[0])*x+(high[1]-low[1])*y+high[2]-low[2]-r/region.lower_normal_z-r/region.upper_normal_z
                     for x,y in list(part.exterior.coords)[:-1]]
            if heights and min(heights)>0 and max(heights)-min(heights)<1e-4:
                try:
                    native= native_subset(part,1.0)
                    loops=List[DB.CurveLoop]()
                    for ring in [native.exterior]+list(native.interiors):
                        points=[xyz((x,y,low[0]*x+low[1]*y+low[2]+r/region.lower_normal_z)) for x,y in list(ring.coords)[:-1]]
                        loop=DB.CurveLoop()
                        for p,q in zip(points,points[1:]+points[:1]):loop.Append(DB.Line.CreateBound(p,q))
                        loops.Add(loop)
                    solids.append(DB.GeometryCreationUtilities.CreateExtrusionGeometry(loops,DB.XYZ.BasisZ,rr.internal(min(heights))))
                    continue
                except Exception as exc:errors.append('PARALLEL_PRISM_FALLBACK: '+str(exc))
            candidates=(list(constrained_delaunay_triangles(part).geoms)
                        if constrained_delaunay_triangles is not None else triangulate(part))
            triangles=[t for t in candidates if part.covers(t)]
            if not triangles or unary_union(triangles).symmetric_difference(part).area>1e-6:
                errors.append('INCOMPLETE_DOMAIN_TRIANGULATION')
            for triangle in triangles:
                try:
                    loops=List[DB.CurveLoop]()
                    for coefficients,sign,nz in ((region.lower_coefficients,1,region.lower_normal_z),
                                                  (region.upper_coefficients,-1,region.upper_normal_z)):
                        a,b,c=coefficients
                        vertices=[xyz((x,y,a*x+b*y+c+sign*r/nz)) for x,y in list(triangle.exterior.coords)[:-1]]
                        loop=DB.CurveLoop()
                        for p,q in zip(vertices,vertices[1:]+vertices[:1]):loop.Append(DB.Line.CreateBound(p,q))
                        loops.Add(loop)
                    # Inverted/empty headroom must not create a loft through
                    # the opposite bounding plane.
                    if any(sum(v*w for v,w in zip(region.upper_coefficients,(x,y,1)))-r/region.upper_normal_z<=
                           sum(v*w for v,w in zip(region.lower_coefficients,(x,y,1)))+r/region.lower_normal_z
                           for x,y in list(triangle.exterior.coords)[:-1]):continue
                    solids.append(DB.GeometryCreationUtilities.CreateLoftGeometry(loops,
                        DB.SolidOptions(DB.ElementId.InvalidElementId,DB.ElementId.InvalidElementId)))
                except Exception as exc:errors.append(str(exc))
        self.cache[index]=(solids,errors);return solids,errors

    def _partitioned_contains(self,solid,volumes,deadline):
        from ..core.volume_bounds import contained
        queue=[(solid,0)];cells=0
        portals=[p for p,_ in self.bores if self._domain_bore(p)]
        while queue and cells<4096 and time.monotonic()<deadline:
            piece,depth=queue.pop();cells+=1;bounds=_solid_box(piece)
            if contained(bounds,self.regions,self.clearance,portals):continue
            residual=piece
            for volume in volumes:
                if not g.overlaps(bounds,_solid_box(volume)):continue
                try:
                    residual=DB.BooleanOperationsUtils.ExecuteBooleanOperation(residual,volume,DB.BooleanOperationsType.Difference)
                    if residual.Volume<=1e-9:break
                except Exception:continue
            if residual.Volume<=1e-9:continue
            sizes=[bounds[i+3]-bounds[i] for i in range(3)];axis=max(range(3),key=sizes.__getitem__)
            if depth>=18 or sizes[axis]<2:return {'verified':False,'cells':cells,'reason':'NATIVE_LOCAL_CONTAINMENT_UNRESOLVED'}
            middle=(bounds[axis]+bounds[axis+3])/2;children=[]
            try:
                for lower in (True,False):
                    clip=list(bounds);clip[axis+3 if lower else axis]=middle
                    child=DB.BooleanOperationsUtils.ExecuteBooleanOperation(piece,box_solid(g.expand(clip,1.)),DB.BooleanOperationsType.Intersect)
                    if child.Volume>1e-9:children.append(child)
            except Exception:return {'verified':False,'cells':cells,'reason':'NATIVE_SUBDIVISION_BOOLEAN_FAILED'}
            if sum(q.Volume for q in children)<piece.Volume-1e-9:return {'verified':False,'cells':cells,'reason':'NATIVE_SUBDIVISION_VOLUME_NOT_PRESERVED',
                'parent_volume_mm3':piece.Volume*304.8**3,'child_volume_mm3':sum(q.Volume for q in children)*304.8**3,'bounds_mm':bounds}
            queue.extend((child,depth+1) for child in children)
        return {'verified':not queue,'cells':cells,'reason':'NATIVE_PARTITIONED_BOX_AND_BOOLEAN_PROOF' if not queue else 'NATIVE_PARTITION_BUDGET_EXHAUSTED'}

    def contains(self,solid):
        residual=solid;errors=[]
        volumes=[b for p,b in self.bores if self._domain_bore(p)]
        query=_solid_box(solid)
        for i,region in enumerate(self.regions):
            x0,y0,x1,y1=region.bounds
            if x0>query[3] or x1<query[0] or y0>query[4] or y1<query[1]:continue
            pieces,failed=self._solids(i);volumes.extend(pieces);errors.extend(failed)
        for allowed in volumes:
            try:
                if not g.overlaps(query,_solid_box(allowed)):continue
                residual=DB.BooleanOperationsUtils.ExecuteBooleanOperation(residual,allowed,DB.BooleanOperationsType.Difference)
                if residual.Volume<=1e-9:return {'verified':True,'method':'NATIVE_BODY_MINUS_AUTHORIZED_UNION','clearance_mm':self.clearance}
            except Exception as exc:errors.append(str(exc))
        local=self._partitioned_contains(solid,volumes,time.monotonic()+8.)
        if local['verified']:return dict(local,method='NATIVE_PARTITIONED_BODY_IN_AUTHORIZED_UNION',clearance_mm=self.clearance)
        return {'verified':False,'method':'NATIVE_BODY_MINUS_AUTHORIZED_UNION','clearance_mm':self.clearance,'partitioned_check':local,
                'remaining_volume_mm3':residual.Volume*304.8**3,'remaining_box_mm':_solid_box(residual),'error_count':len(errors),'errors':list(dict.fromkeys(errors))}
