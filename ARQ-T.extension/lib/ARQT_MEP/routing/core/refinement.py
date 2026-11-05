"""Continuous refinement inside certified spherical/convex trust regions."""
import copy
import time
from . import geometry as g
from .route_geometry import validate_route_geometry


def refine(space,route,profile,deadline,cancelled=None):
    try:
        from ...runtime import scientific_context
        with scientific_context():
            import numpy as np
            from scipy.optimize import minimize
    except ImportError:return route
    controls=route.get('routing_points_mm',route['points_mm'])
    protected={tuple(p) for passage in route.get('passages',[]) for p in passage['points_mm']}
    protected.update(tuple(p) for p in route.get('waypoints_mm',[]))
    movable=[];bounds=[];x0=[]
    for i,p in enumerate(controls[1:-1],1):
        if tuple(p) in protected:continue
        size=profile.bend_radius_mm
        while size>2 and not space.free(p,p,profile.radius_mm+size*3**.5):size/=2
        if size<=2:continue
        movable.append(i)
        for value in p:x0.append(value);bounds.append((value-size,value+size))
    if not movable:return route
    best=copy.deepcopy(route);best_cost=route.get('cost',{}).get('total',float('inf'))
    class Stop(Exception):pass
    def objective(values):
        nonlocal best,best_cost
        if time.monotonic()>=deadline or (cancelled and cancelled()):raise Stop()
        path=[tuple(p) for p in controls]
        for j,index in enumerate(movable):path[index]=tuple(values[j*3:j*3+3])
        candidate=copy.deepcopy(route)
        if route.get('connection_validation_pending'):
            candidate['routing_points_mm']=path
            first,last=route['points_mm'][0],route['points_mm'][-1]
            candidate['points_mm']=([first] if g.distance(first,path[0])>1e-6 else [])+path+(
                [last] if g.distance(last,path[-1])>1e-6 else [])
        else:candidate['points_mm']=path
        checked=validate_route_geometry(space,candidate,profile)
        if checked['status'] in ('INVALID','NOT_EVALUATED'):return 1e6+sum(g.distance(a,b) for a,b in zip(path,path[1:]))
        cost=checked['cost']['total']
        if cost<best_cost-1e-7:
            candidate.update(checked);candidate['continuous_refinement']='CERTIFIED_CONVEX_TRUST_REGIONS'
            best,best_cost=candidate,cost
        return cost
    try:minimize(objective,np.array(x0),bounds=bounds,method='Powell',options={'maxiter':8,'maxfev':100,'xtol':1.})
    except Stop:pass
    return best
