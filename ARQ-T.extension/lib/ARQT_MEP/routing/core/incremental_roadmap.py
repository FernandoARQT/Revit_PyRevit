"""Persistent geometric roadmap with complete-path lazy collision checking.

Only static segment results survive between requests. Reservations and turns
are checked on each invocation. Every returned candidate is independently
validated as a curved, finite-radius route before it can be used.
"""
import heapq
import itertools
import math
import random
import json
import hashlib
import time
from shapely.geometry import Point
from . import geometry as g
from .astar_3d import SparseGrid,passage_cost
from .route_geometry import validate_route_geometry,primitives


class Roadmap:
    def __init__(self):
        self.points=[];self.lookup={};self.static={};self.portals={};self.generation=0
        self.checked=0;self.reused=0;self.rejections=[]

    def add(self,p):
        p=tuple(round(float(v),5) for v in p)
        if p not in self.lookup:self.lookup[p]=len(self.points);self.points.append(p)
        return self.lookup[p]


def export_maps(space):
    return {key:{'points':gph.points,'static':[[a,b,value] for (a,b),value in gph.static.items()],
                 'portals':[[a,b,pid] for (a,b),pid in gph.portals.items()],'generation':gph.generation,
                 'rejections':gph.rejections[-1000:]}
            for key,gph in space.roadmap_cache.items()}


def restore_maps(space,records):
    for key,record in (records or {}).items():
        if key not in space.roadmap_cache:space.roadmap_cache[key]=Roadmap()
        graph=space.roadmap_cache[key];mapping={i:graph.add(p) for i,p in enumerate(record['points'])}
        for a,b,value in record['static']:
            pair=tuple(sorted((mapping[a],mapping[b])))
            # A blocked observation wins if duplicate reports disagree.
            graph.static[pair]=graph.static.get(pair,True) and bool(value)
        for a,b,pid in record['portals']:graph.portals[tuple(sorted((mapping[a],mapping[b])))]=pid
        known={(tuple(r['edge']),r['reason']) for r in graph.rejections}
        for rejection in record.get('rejections',[]):
            edge=[mapping[i] for i in rejection['edge']];entry=(tuple(edge),rejection['reason'])
            if entry not in known:graph.rejections.append(dict(rejection,edge=edge));known.add(entry)
        graph.rejections=graph.rejections[-1000:]
        graph.generation=max(graph.generation,record['generation'])
    while len(space.roadmap_cache)>6:space.roadmap_cache.popitem(last=False)


def search(space,start,goal,profile,settings,scenario='A',deadline=None,cancelled=None,corridors=()):
    deadline=deadline or time.monotonic()+settings['search']['max_seconds'];started=time.monotonic()
    def stopped():return time.monotonic()>=deadline or bool(cancelled and cancelled())
    radius=profile.radius_mm;bend=profile.bend_radius_mm
    key=hashlib.sha256(json.dumps([getattr(space,'geometry_signature',space.data),radius,bend,profile.max_slope,
        scenario,space.constraints],sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()
    cache=space.roadmap_cache
    if key not in cache:
        cache[key]=Roadmap()
        while len(cache)>6:cache.popitem(last=False)
    graph=cache[key];before=graph.checked;reused=graph.reused;initial_nodes=len(graph.points)
    source=graph.add(start);target=graph.add(goal)
    admitted=SparseGrid(space,start,goal,settings['search']['coarse_resolution_mm'],profile,
                        settings['constraints']['passages'],scenario).portals
    portal_by_id={p['id']:p for p in admitted}
    preferred={pid for c in corridors for pid in c['portal_ids']}
    # Keep building connectivity, adding relevant passages first. Wider
    # subsequent calls can enlarge this same graph without losing checked edges.
    selected=sorted(admitted,key=lambda p:(p['id'] not in preferred,
        min(g.distance(start,a)+g.distance(b,goal) for a,b in (p['points_mm'],list(reversed(p['points_mm']))))))
    by_host={}
    for portal in selected:by_host.setdefault(tuple(portal['element_ids']),[]).append(portal)
    selected=[]
    limit=settings['coordination']['max_roadmap_portals']*(1+min(graph.generation,3))
    for tier in range(max(map(len,by_host.values()),default=0)):
        for group in by_host.values():
            if tier<len(group):selected.append(group[tier])
        if len(selected)>=limit:break
    if preferred and graph.generation==0:selected=[p for p in selected if p['id'] in preferred]
    for portal in selected:
        if stopped():break
        a,b=portal['points_mm'];ia,ib=graph.add(a),graph.add(b)
        graph.portals[tuple(sorted((ia,ib)))]=portal['id']
        axis=g.unit(g.sub(b,a))
        for point,sign in ((a,-1),(b,1)):
            for length in (bend,2*bend):graph.add(g.add(point,g.mul(axis,sign*length)))
    sampling_generation=min(graph.generation,3)
    # Bound memory, not the random sequence: a fourth continuation must get
    # new samples rather than replay generation three forever.
    rng=random.Random(settings.get('random_seed',0)+graph.generation)
    sample_limit=settings['coordination'].get('max_incremental_nodes',4096)
    if settings.get('routing_mode') in ('deep','exhaustive'):sample_limit=min(20000,sample_limit*2)
    space.stats['incremental_saturation']={'limit':sample_limit,'nodes':len(graph.points),'saturated':len(graph.points)>=sample_limit,'generation':graph.generation}
    for region in space.regions:
        if stopped():break
        interior=region.polygon.buffer(-radius-.01)
        if interior.is_empty:continue
        polygons=list(interior.geoms) if hasattr(interior,'geoms') else [interior]
        for polygon in polygons:
            center=polygon.representative_point();xy=[(center.x,center.y)]
            xy+=list(polygon.exterior.coords[:-1])[::max(1,len(polygon.exterior.coords)//10)]
            x0,y0,x1,y1=polygon.bounds
            for _ in range(4*(sampling_generation+1)):
                x,y=rng.uniform(x0,x1),rng.uniform(y0,y1)
                if polygon.covers(Point(x,y)):xy.append((x,y))
            for x,y in xy:
                if len(graph.points)>=sample_limit:break
                a,b,c=region.lower_coefficients;lo=a*x+b*y+c+radius/region.lower_normal_z+.01
                a,b,c=region.upper_coefficients;hi=a*x+b*y+c-radius/region.upper_normal_z-.01
                if hi>=lo:
                    for z in (lo,(lo+hi)/2,hi):graph.add((x,y,z))
    graph.generation+=1
    static_space=space.clone();static_space.reservations=[]
    neighbor_cache={};banned_turns=set();dynamic_blocked=set();counter=itertools.count();trials=0
    def neighbors(i):
        if i not in neighbor_cache:
            nearest=sorted((g.distance(graph.points[i],p),j) for j,p in enumerate(graph.points) if j!=i)
            indices={source,target}|{j for _,j in nearest[:settings['coordination']['roadmap_neighbors']]}
            indices.update(b if a==i else a for a,b in graph.portals if i in (a,b))
            neighbor_cache[i]=indices-{i}
        return neighbor_cache[i]
    def checked_edge(i,j):
        pair=tuple(sorted((i,j)));a,b=graph.points[i],graph.points[j]
        pid=graph.portals.get(pair);portal=portal_by_id.get(pid)
        if pid and not portal:return False
        if pair not in graph.static:
            free=(not static_space.forbidden_hit(a,b,radius) and not static_space.collisions(a,b,radius,portal['element_ids'])
                  and static_space.free(a,a,radius) and static_space.free(b,b,radius)) if portal else static_space.free(a,b,radius)
            graph.static[pair]=free;graph.checked+=1
            if not free:
                graph.rejections.append({'edge':[i,j],'reason':'STATIC_ENVELOPE_BLOCKED'})
                if len(graph.rejections)>1000:del graph.rejections[:-1000]
        else:graph.reused+=1
        if not graph.static[pair]:return False
        return not any(g.segment_distance(a,b,u,v)<radius+r['radius_mm']-1e-6 for r in space.reservations
                       for u,v in zip(r['points'],r['points'][1:]))
    while not stopped():
        initial=(source,None,0.,0.);queue=[(g.distance(start,goal),0,next(counter),initial)];costs={initial:0};parents={};found=None
        while queue and not stopped():
            _,cost,_,state=heapq.heappop(queue)
            if cost>costs.get(state,float('inf'))+1e-6:continue
            i,previous,run,trim=state;a=graph.points[i]
            if i==target and run>=trim-1e-5:found=state;break
            for j in neighbors(i):
                pair=tuple(sorted((i,j)))
                if pair in dynamic_blocked or graph.static.get(pair) is False or (previous,i,j) in banned_turns:continue
                b=graph.points[j];travel=g.distance(a,b)
                if travel<.001:continue
                if profile.max_slope is not None and abs(b[2]-a[2])>profile.max_slope*math.hypot(b[0]-a[0],b[1]-a[1])+1e-6:continue
                angle=0 if previous is None else math.acos(max(-1,min(1,g.dot(g.unit(g.sub(a,graph.points[previous])),g.unit(g.sub(b,a))))))
                if angle>math.pi-1e-6:continue
                required=bend*math.tan(angle/2)
                if run+1e-5<trim+required:continue
                newrun=travel+(run if angle<1e-6 else 0)
                nxt=(j,i if angle>1e-6 or previous is None else previous,round(newrun,3),round(required if angle>1e-6 else trim,6))
                portal=portal_by_id.get(graph.portals.get(pair));extra=passage_cost(portal,profile.weights)*1000 if portal else 0
                value=cost+travel+angle*bend+extra
                if value<costs.get(nxt,float('inf')):
                    costs[nxt]=value;parents[nxt]=state
                    heapq.heappush(queue,(value+g.distance(b,goal),value,next(counter),nxt))
        if found is None:break
        indices=[];state=found
        while state!=initial:indices.append(state[0]);state=parents[state]
        indices.append(source);indices.reverse();trials+=1
        failed=False
        for i,j in zip(indices,indices[1:]):
            if stopped():failed=True;break
            if not checked_edge(i,j):dynamic_blocked.add(tuple(sorted((i,j))));failed=True;break
        if failed:continue
        used=[portal_by_id[graph.portals[tuple(sorted((i,j)))]] for i,j in zip(indices,indices[1:])
              if tuple(sorted((i,j))) in graph.portals and graph.portals[tuple(sorted((i,j)))] in portal_by_id]
        route={'points_mm':[graph.points[i] for i in indices],'passages':used}
        report=validate_route_geometry(space,route,profile)
        if report['status'] not in ('INVALID','NOT_EVALUATED'):
            route.update(report);route.update(search_method='INCREMENTAL_LAZY_ROADMAP',roadmap_generation=graph.generation,
                roadmap_nodes=len(graph.points),static_edges_checked=graph.checked-before,static_edges_reused=graph.reused-reused)
            return route
        # Find precisely the colliding turn, rather than discarding a safe
        # shared straight edge. A final validation failure is never accepted.
        changed=False
        for a,i,b in zip(indices,indices[1:],indices[2:]):
            try:
                pieces=primitives([graph.points[a],graph.points[i],graph.points[b]],bend)
                bad=any(not space.free(u,v,radius+p.get('sagitta_mm',0)) for p in pieces if p['kind']=='ARC'
                        for u,v in zip(p['points_mm'],p['points_mm'][1:]))
            except ValueError:bad=True
            if bad:banned_turns.add((a,i,b));changed=True;break
        if not changed:break
    return {'status':'NOT_EVALUATED','reason':'LAZY_ROADMAP_TIME_LIMIT' if stopped() else 'LAZY_ROADMAP_FRONTIER_EXHAUSTED',
        'roadmap_nodes':len(graph.points),'added_nodes':len(graph.points)-initial_nodes,'candidate_paths_tested':trials,
        'static_edges_checked':graph.checked-before,'static_edges_reused':graph.reused-reused,'seconds':time.monotonic()-started}
