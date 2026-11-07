"""Conservative cubic centreline bounds using Bernstein convex hulls."""
from . import geometry as g


def split(points):
    a,b,c,d=points;ab=g.mul(g.add(a,b),.5);bc=g.mul(g.add(b,c),.5);cd=g.mul(g.add(c,d),.5)
    abc=g.mul(g.add(ab,bc),.5);bcd=g.mul(g.add(bc,cd),.5);middle=g.mul(g.add(abc,bcd),.5)
    return [a,ab,abc,middle],[middle,bcd,cd,d]


def radius_lower_bound(points):
    velocity=[g.mul(g.sub(b,a),3) for a,b in zip(points,points[1:])]
    chord=g.sub(points[-1],points[0])
    if g.norm(chord)<1e-10:return 0.
    normal=g.unit(chord);minimum=min(g.dot(v,normal) for v in velocity)
    if minimum<=0:return 0.
    acceleration=[g.mul(g.sub(b,a),2) for a,b in zip(velocity,velocity[1:])]
    maximum=max(map(g.norm,acceleration))
    # |v x a| / |v|^3 <= max|a| / min|v|^2. The derivative
    # Bernstein hull supplies a positive lower projection for the speed.
    return minimum**2/maximum if maximum>1e-12 else float('inf')


def certify(points,minimum_radius_mm,reference_segments,tolerance_mm,max_depth=12,max_cells=12000):
    pending=[(points,0)];cells=0;minimum=float('inf')
    while pending and cells<max_cells:
        control,depth=pending.pop();cells+=1;radius=radius_lower_bound(control)
        # A capsule around ONE straight reference segment is convex. If it
        # contains all four controls, it contains the entire cubic hull.
        contained=any(max(g.point_segment(a,b,q) for q in control)<=tolerance_mm for a,b in reference_segments)
        if radius+1e-4>=minimum_radius_mm and contained:minimum=min(minimum,radius);continue
        left,right=split(control);middle=left[-1]
        if min((g.point_segment(a,b,middle) for a,b in reference_segments),default=float('inf'))>tolerance_mm+1e-4:
            return {'verified':False,'reason':'NATIVE_AXIS_DEVIATION','cells':cells}
        if depth>=max_depth:return {'verified':False,'reason':'NATIVE_CURVATURE_OR_DISTANCE_BOUND_UNRESOLVED','cells':cells}
        pending.extend(((left,depth+1),(right,depth+1)))
    if pending:return {'verified':False,'reason':'NATIVE_CURVE_BOUND_BUDGET','cells':cells}
    return {'verified':True,'radius_lower_bound_mm':None if minimum==float('inf') else minimum,'cells':cells,
        'method':'CUBIC_BERNSTEIN_DERIVATIVE_AND_CAPSULE_HULL_BOUNDS'}
