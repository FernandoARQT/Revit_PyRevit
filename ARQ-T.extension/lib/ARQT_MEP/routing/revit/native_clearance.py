"""Tight conservative clearance proof for native planar polyhedral obstacles.

The original solid covers its interior; triangle bounds cover its complete
boundary plus clearance. Curved or incompletely triangulated faces remain
unverified. No sampled native surface is accepted as a distance certificate.
"""
import time
from pyrevit.api import DB
from ... import revit_read as rr
from ..core import geometry as g
from .proposals import _solid_box
from .authorized_volume import box_solid


def certify(body,obstacle,margin,cutters=(),deadline=None):
    deadline=deadline or time.monotonic()+3.;triangles=[];cells=0
    def overlap(a,b):
        result=DB.BooleanOperationsUtils.ExecuteBooleanOperation(a,b,DB.BooleanOperationsType.Intersect)
        for cutter in cutters:
            if result.Volume<=1e-9:break
            if g.overlaps(_solid_box(result),_solid_box(cutter)):
                result=DB.BooleanOperationsUtils.ExecuteBooleanOperation(result,cutter,DB.BooleanOperationsType.Difference)
        return result.Volume>1e-9
    try:
        if overlap(body,obstacle):return {'verified':False,'reason':'ACTUAL_PHYSICAL_INTERSECTION'}
        if margin<=0:return {'verified':True,'method':'ACTUAL_NATIVE_SOLID_INTERSECTION'}
        for face in obstacle.Faces:
            if not isinstance(face,DB.PlanarFace) or any(not isinstance(edge.AsCurve(),DB.Line) for loop in face.EdgeLoops for edge in loop):
                return {'verified':False,'reason':'NONPOLYHEDRAL_OBSTACLE'}
            mesh=face.Triangulate();pieces=[];padding=.01
            origin=face.Origin;u=face.XVector.Normalize();v=face.YVector.Normalize()
            def project(point):
                delta=point-origin
                return (rr.mm(delta.DotProduct(u)),rr.mm(delta.DotProduct(v)))
            rings=[[project(curve.GetEndPoint(0)) for curve in loop] for loop in face.GetEdgesAsCurveLoops()]
            projected=[]
            for i in range(mesh.NumTriangles):
                triangle=mesh.get_Triangle(i)
                native=[triangle.get_Vertex(j) for j in range(3)]
                pieces.append([(rr.mm(q.X),rr.mm(q.Y),rr.mm(q.Z)) for q in native]);projected.append([project(q) for q in native])
            from ..core.volume_bounds import planar_boundary_covered
            if not planar_boundary_covered(rings,projected,padding):return {'verified':False,'reason':'EXACT_PLANAR_BOUNDARY_NOT_COVERED'}
            triangles.extend(pieces)
        if not triangles:return {'verified':False,'reason':'NATIVE_BOUNDARY_UNAVAILABLE'}
        query=_solid_box(body);queue=triangles
        while queue and cells<8192 and time.monotonic()<deadline:
            triangle=queue.pop();bounds=g.expand(g.bounds(triangle),margin+padding);cells+=1
            if not g.overlaps(query,bounds) or not overlap(body,box_solid(bounds)):continue
            edges=[(g.distance(triangle[i],triangle[j]),i,j) for i,j in ((0,1),(1,2),(2,0))]
            length,i,j=max(edges)
            if length<2:return {'verified':False,'reason':'LOCAL_CONSERVATIVE_CLEARANCE_UNRESOLVED','cells':cells}
            k=3-i-j;mid=g.mul(g.add(triangle[i],triangle[j]),.5)
            queue.extend(([triangle[i],mid,triangle[k]],[mid,triangle[j],triangle[k]]))
        return {'verified':not queue,'method':'EXACT_PLANAR_NATIVE_BOUNDARY_WITH_SUBDIVIDED_CLEARANCE_BOUNDS',
            'cells':cells,'reason':None if not queue else 'NATIVE_CLEARANCE_BUDGET_EXHAUSTED'}
    except Exception as exc:return {'verified':False,'reason':'NATIVE_CLEARANCE_BOOLEAN_UNRESOLVED','detail':str(exc),'cells':cells}
