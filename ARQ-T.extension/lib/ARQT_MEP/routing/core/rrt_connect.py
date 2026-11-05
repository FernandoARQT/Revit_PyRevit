"""Experimental reproducible bidirectional discovery; curved validation wins."""
import random
import time
from shapely.geometry import Point
from . import geometry as g
from .route_geometry import validate_route_geometry


def discover(space,start,goal,profile,configuration,deadline,cancelled=None,corridors=(),seed=0):
    rng=random.Random(seed);radius=profile.radius_mm;step=max(4*profile.bend_radius_mm,200)
    motions=0;total_nodes=0;trials=0;trees={}
    if not space.regions:return {'status':'NOT_EVALUATED','reason':'NO_AUTHORIZED_VOLUME'}
    if profile.max_slope is not None:return {'status':'NOT_EVALUATED','reason':'RRT_SLOPE_PROFILE_NOT_SUPPORTED'}
    def stopped():return time.monotonic()>=deadline or bool(cancelled and cancelled())
    def sample(regions,target):
        if rng.random()<.15:return target
        for _ in range(20):
            if stopped():return None
            region=rng.choice(regions);x0,y0,x1,y1=region.bounds
            x,y=rng.uniform(x0,x1),rng.uniform(y0,y1)
            if not region.polygon.covers(Point(x,y)):continue
            a,b,c=region.lower_coefficients;lo=a*x+b*y+c+radius/region.lower_normal_z
            a,b,c=region.upper_coefficients;hi=a*x+b*y+c-radius/region.upper_normal_z
            if hi>=lo:
                p=(x,y,rng.uniform(lo,hi))
                if space.free(p,p,radius):return p
        return None
    def leg(a,b,regions,leg_deadline):
        nonlocal motions,total_nodes
        if space.free(a,b,radius):return [tuple(a),tuple(b)]
        tree_key=(tuple(a),tuple(b),tuple(id(r) for r in regions))
        ta,tb=trees.setdefault(tree_key,(([tuple(a)],[-1],True),([tuple(b)],[-1],False)))
        def extend(tree,target):
            nonlocal motions,total_nodes
            points,parents,_=tree
            index=min(range(len(points)),key=lambda i:g.distance(points[i],target));p=points[index]
            length=g.distance(p,target)
            if length<.001:return index,True
            q=g.add(p,g.mul(g.sub(target,p),min(1,step/length)));motions+=1
            if not space.free(p,q,radius):return None,False
            points.append(q);parents.append(index);total_nodes+=1
            return len(points)-1,length<=step
        def path(tree,index):
            result=[]
            while index>=0:result.append(tree[0][index]);index=tree[1][index]
            return list(reversed(result))
        while not stopped() and time.monotonic()<leg_deadline:
            target=sample(regions,tb[0][0])
            if target is None:continue
            ia,_=extend(ta,target)
            if ia is not None:
                while not stopped() and time.monotonic()<leg_deadline:
                    ib,reached=extend(tb,ta[0][ia])
                    if ib is None:break
                    if reached:
                        left=path(ta,ia);right=path(tb,ib)
                        if not ta[2]:left,right=right,left
                        points=left+list(reversed(right))[1:]
                        # Visibility shortcutting still checks every full-radius
                        # segment. Curves are independently tested afterward.
                        clean=[points[0]];i=0
                        while i<len(points)-1:
                            if stopped():return None
                            j=len(points)-1
                            while j>i+1 and not stopped() and not space.free(points[i],points[j],radius):j-=1
                            clean.append(points[j]);i=j
                        return clean
            ta,tb=tb,ta
            trees[tree_key]=(ta,tb)
        return None
    portal_lookup={p['id']:p for p in configuration['constraints']['passages']}
    options=[{'edges':[],'region_indices':list(range(len(space.regions)))}]+list(corridors)
    while not stopped():
        corridor=options[trials%len(options)];trials+=1
        trial_deadline=min(deadline,time.monotonic()+max(0,deadline-time.monotonic())/min(len(options),3))
        regions=[space.regions[i] for i in corridor['region_indices']] or space.regions
        points=[tuple(start)];used=[];failed=False
        for edge in corridor['edges']:
            if edge['kind']!='PORTAL':continue
            a,b=edge['points_mm'];axis=g.unit(g.sub(b,a));arm=profile.bend_radius_mm
            entry=g.sub(a,g.mul(axis,arm));exit=g.add(b,g.mul(axis,arm))
            path=leg(points[-1],entry,regions,trial_deadline)
            if not path:failed=True;break
            points.extend(path[1:]+[tuple(a),tuple(b),exit]);used.append(portal_lookup[edge['portal_id']])
        if not failed:
            path=leg(points[-1],goal,regions,trial_deadline)
            if path:
                points.extend(path[1:]);route={'points_mm':points,'passages':used,'topology_nodes':corridor.get('topology_nodes',[]),
                    'corridor_portal_ids':corridor.get('portal_ids',[])}
                checked=validate_route_geometry(space,route,profile)
                if checked['status'] not in ('INVALID','NOT_EVALUATED'):
                    route.update(checked);route.update(search_method='EXPERIMENTAL_RRT_CONNECT',random_seed=seed,
                        tested_motions=motions,rrt_nodes=total_nodes,rrt_trials=trials)
                    return route
    return {'status':'NOT_EVALUATED','reason':'RRT_CONNECT_SEARCH_LIMIT','random_seed':seed,
            'tested_motions':motions,'rrt_nodes':total_nodes,'rrt_trials':trials}
