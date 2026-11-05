"""Lazy visibility roadmap through plenum layers and admitted wall passages.

Nodes only suggest corridors. Every edge, bend and final route still receives
the same continuous full-envelope checks as the fine-grid planner.
"""
import heapq
import itertools
import math
import time
from shapely.geometry import Point
from . import geometry as g
from .astar_3d import SparseGrid,direction,passage_cost
from .route_geometry import primitives


def find_corridor(space,start,goal,profile,settings,scenario='A',cancelled=None,diversity=(),deadline=None):
    started=time.monotonic()
    options=settings['coordination'];radius=profile.radius_mm;bend=profile.bend_radius_mm
    if g.distance(start,goal)<1e-5:return {'status':'NOT_EVALUATED','reason':'COINCIDENT_ENDPOINTS'}
    points=[tuple(start),tuple(goal)];lookup={p:i for i,p in enumerate(points)};links={};hubs=set()
    def add(p):
        p=tuple(round(v,5) for v in p)
        if p not in lookup:lookup[p]=len(points);points.append(p)
        return lookup[p]
    # Reuse exactly the admission policy of the grid, including rejected,
    # conditional, structural and hard-forbidden checks.
    admission=SparseGrid(space,start,goal,settings['search']['coarse_resolution_mm'],profile,
                         settings['constraints']['passages'],scenario)
    portals=sorted(admission.portals,key=lambda p:min(g.distance(start,a)+g.distance(b,goal)
                   for a,b in (p['points_mm'],list(reversed(p['points_mm'])))))
    # A cluster of elevations/stations on the first wall must not consume the
    # whole roadmap before the second wall gets a single candidate.
    groups={}
    for portal in portals:groups.setdefault(tuple(sorted(portal['element_ids'])),[]).append(portal)
    groups=list(groups.values());selected=[]
    for tier in range(max(map(len,groups),default=0)):
        for group in groups:
            if tier<len(group):selected.append(group[tier])
        if len(selected)>=options['max_roadmap_portals']:break
    for portal in selected[:options['max_roadmap_portals']]:
        if (cancelled and cancelled()) or (deadline is not None and time.monotonic()>=deadline):break
        a,b=[tuple(p) for p in portal['points_mm']]
        # Nodes are suggestions, never certified corridors. Defer expensive
        # free-space queries to the edges actually visited by the search.
        ia,ib=add(a),add(b);links[ia,ib]=portal;links[ib,ia]=portal
        axis=g.unit(g.sub(b,a))
        for p,sign in ((a,-1),(b,1)):
            # Straight approach points keep bend trims outside the wall.
            for length in (bend,2*bend):
                q=g.add(p,g.mul(axis,sign*length))
                add(q)
    for region in space.regions:
        if (cancelled and cancelled()) or (deadline is not None and time.monotonic()>=deadline):break
        interior=region.polygon.buffer(-radius-2)
        if interior.is_empty:continue
        parts=list(interior.geoms) if hasattr(interior,'geoms') else [interior]
        for polygon in parts:
            center=polygon.representative_point();xy=[(center.x,center.y)]
            # Project the endpoints into each room as well as sampling its
            # interior corners. These levels follow inclined ceilings.
            for p in (start,goal):
                if polygon.covers(Point(p[:2])):xy.append(p[:2])
            corners=list(polygon.exterior.coords)[:-1]
            stride=max(1,math.ceil(len(corners)/12))
            xy.extend(corners[::stride])
            for x,y in xy:
                a,b,c=region.lower_coefficients;lo=a*x+b*y+c+(radius+2)/region.lower_normal_z
                a,b,c=region.upper_coefficients;hi=a*x+b*y+c-(radius+2)/region.upper_normal_z
                if hi<lo:continue
                for z in (lo,(lo+hi)/2,hi):
                    p=(x,y,z)
                    index=add(p)
                    if (x,y)==xy[0]:hubs.add(index)
    if not all(space.free(p,p,radius) for p in (start,goal)):
        return {'status':'NOT_EVALUATED','reason':'ROADMAP_ENDPOINT_BLOCKED'}
    edges={};neighbor_cache={};turn_cache={};serial=itertools.count()
    def neighbors(node):
        if node not in neighbor_cache:
            ranked=sorted((g.distance(points[node],p),i) for i,p in enumerate(points) if i!=node)
            # Endpoint visibility is always tried; portal mates are mandatory.
            indexes={0,1}|hubs|{i for _,i in ranked[:options['roadmap_neighbors']]}
            indexes.update(b for a,b in links if a==node)
            neighbor_cache[node]=[i for i in sorted(indexes) if i!=node]
        for nxt in neighbor_cache[node]:
            # A sequence of blocked edges yields no neighbors, so the caller
            # cannot enforce its deadline until this generator yields again.
            if (cancelled and cancelled()) or (deadline is not None and time.monotonic()>=deadline):return
            pair=tuple(sorted((node,nxt)));portal=links.get((node,nxt))
            if pair not in edges:
                a,b=points[node],points[nxt]
                if portal:
                    free=(not space.forbidden_hit(a,b,radius) and not space.collisions(a,b,radius,portal['element_ids'])
                          and space.free(a,a,radius) and space.free(b,b,radius)
                          and not any(g.segment_distance(a,b,u,v)<radius+r['radius_mm']-1e-6
                              for r in space.reservations for u,v in zip(r['points'],r['points'][1:])))
                else:free=space.free(a,b,radius)
                edges[pair]=free
            if edges[pair]:yield nxt,portal
    initial=(0,None,0.,0.)
    preparation_seconds=time.monotonic()-started
    queue=[(g.distance(start,goal)/1000*profile.weights['length'],0,next(serial),initial)]
    costs={initial:0};parents={};expanded=0;found=None
    while queue:
        if expanded%8==0 and ((cancelled and cancelled()) or (deadline is not None and time.monotonic()>=deadline)):break
        _,cost,_,state=heapq.heappop(queue)
        if cost>costs.get(state,float('inf'))+1e-9:continue
        node,arrival,straight,previous_trim=state;expanded+=1
        if expanded>settings['search']['max_nodes']:break
        if node==1 and straight+1e-5>=previous_trim:found=state;break
        a=points[node]
        for nxt,portal in neighbors(node):
            if deadline is not None and time.monotonic()>=deadline:break
            b=points[nxt];travel=g.distance(a,b)
            if travel<1e-4:continue
            heading=direction(a,b)
            angle=math.acos(max(-1,min(1,g.dot(arrival,heading)))) if arrival else 0
            if angle>math.pi-1e-6:continue
            trim=bend*math.tan(angle/2) if angle>1e-6 else 0
            if straight+1e-5<previous_trim+trim:continue
            if profile.max_slope is not None and abs(b[2]-a[2])>profile.max_slope*math.hypot(b[0]-a[0],b[1]-a[1])+1e-6:continue
            if trim>0:
                # Reject a colliding arc during search, rather than repeatedly
                # returning a polyline whose rounded corner is not constructible.
                key=(node,arrival,heading)
                if key not in turn_cache:
                    approach=g.sub(a,g.mul(arrival,trim));departure=g.add(a,g.mul(heading,trim))
                    try:
                        arcs=[p for p in primitives([approach,a,departure],bend) if p['kind']=='ARC']
                        turn_cache[key]=all(space.free(u,v,radius+p['sagitta_mm']) for p in arcs
                                              for u,v in zip(p['points_mm'],p['points_mm'][1:]))
                    except ValueError:turn_cache[key]=False
                if not turn_cache[key]:continue
            next_trim=trim if angle>1e-6 else previous_trim
            # Roadmap edges may turn at any angle, so retain the full straight
            # run (unlike the bounded orthogonal grid state).
            run=travel+(straight if angle<1e-6 else 0)
            next_state=(nxt,heading,round(run,3),round(next_trim,6))
            extra=travel/1000*(profile.weights['length']+profile.weights['nonpreferred']*space.preferred_penalty(b))
            extra+=angle/(math.pi/2)*profile.weights['bend']
            if portal:extra+=passage_cost(portal,profile.weights)
            if diversity and any(g.point_segment(u,v,b)<radius*3 for path in diversity for u,v in zip(path,path[1:])):extra+=travel/1000*2
            new=cost+extra
            if new+1e-9>=costs.get(next_state,float('inf')):continue
            costs[next_state]=new;parents[next_state]=(state,portal)
            heapq.heappush(queue,(new+g.distance(b,goal)/1000*profile.weights['length'],new,next(serial),next_state))
    diagnostics={'expanded':expanded,'roadmap_nodes':len(points),'tested_edges':len(edges),
                 'preparation_seconds':preparation_seconds,'search_seconds':time.monotonic()-started-preparation_seconds}
    if found is None:return dict(diagnostics,status='NOT_EVALUATED',reason='ROADMAP_SEARCH_LIMIT')
    path=[];used=[];state=found
    while state!=initial:
        path.append(points[state[0]]);state,portal=parents[state]
        if portal:used.append(portal)
    path.append(tuple(start));path.reverse();used.reverse()
    return dict(diagnostics,points_mm=path,passages=list({p['id']:p for p in used}.values()),
                search_method='PLENUM_VISIBILITY_ROADMAP')
