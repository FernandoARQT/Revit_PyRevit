"""Single discipline-independent, direction-aware A* over a lazy rectilinear grid."""
import bisect
import heapq
import itertools
import math
import time
from . import geometry as g
from ... import surface


def direction(a,b):return tuple(round(v,7) for v in g.unit(g.sub(b,a)))


class SparseGrid:
    def __init__(self,space,start,goal,step,profile,passages=(),scenario='A',local_bounds=None):
        self.space=space;self.step=step;self.profile=profile;self.radius=profile.radius_mm
        # Nodes represent centerline samples, not fully empty voxel volumes.
        # Each edge is checked continuously at the complete physical radius.
        # Adding half the grid diagonal here wrongly rejects thin usable plenums.
        self.margin=self.radius;self.cache={};self.edges={};self.passages={}
        points=[tuple(start),tuple(goal)]
        self.portals=[]
        rejected=set(space.constraints.get('rejected_passages',[]))
        approved=set(space.constraints.get('approved_passages',[]))
        for p in passages:
            if p['id'] in rejected or p['radius_mm']<self.radius:continue
            owners=[o for o in space.obstacles if o.id in p['element_ids']]
            if len(owners)!=len(set(p['element_ids'])):continue
            if any(o.classification in ('UNKNOWN','FORBIDDEN') for o in owners):continue
            p=dict(p,crossing_multiplier=max([p.get('crossing_multiplier',1.0)]+[o.data.get('crossing_multiplier',1.0) for o in owners]))
            if any(o.classification=='CONDITIONAL' for o in owners) and p['classification']!='CONDITIONAL':continue
            if p['classification']=='CONDITIONAL' and not p.get('evaluable_zone'):continue
            if any(o.data.get('category')=='OST_Floors' for o in owners) and not p.get('evaluable_zone'):continue
            if p['classification']=='CONDITIONAL' and scenario=='A' and p['id'] not in approved:continue
            self.portals.append(p);points.extend(tuple(q) for q in p['points_mm'])
        region_boxes=[]
        for r in space.regions:
            x0,y0,x1,y1=r.polygon.bounds
            zs=[surface.height(boundary,x,y) for boundary in (r.lower,r.upper) for x in (x0,x1) for y in (y0,y1)]
            region_boxes.append((x0,y0,min(zs),x1,y1,max(zs)))
        domain=g.bounds(points+[b[:3] for b in region_boxes]+[b[3:] for b in region_boxes])
        if local_bounds is not None:
            domain=tuple(local_bounds)
            if not all(domain[i]<=p[i]<=domain[i+3] for p in (start,goal) for i in range(3)):
                raise ValueError('Los extremos del rescate deben estar dentro de su caja local.')
            self.portals=[p for p in self.portals if all(domain[i]<=q[i]<=domain[i+3] for q in p['points_mm'] for i in range(3))]
            points=[tuple(start),tuple(goal)]+[tuple(q) for p in self.portals for q in p['points_mm']]
        self.axes=[]
        for axis in range(3):
            lo=math.floor(domain[axis]/step)*step;hi=math.ceil(domain[axis+3]/step)*step
            if (hi-lo)/step>100000:raise ValueError('Dominio excesivo; limite el ámbito geométrico.')
            values={round(lo+j*step,6) for j in range(int(round((hi-lo)/step))+1)}
            values.update(round(p[axis],6) for p in points)
            self.axes.append(sorted(values))
        self.start=self.index(start);self.goal=self.index(goal)
        for p in self.portals:
            a,b=[self.index(q) for q in p['points_mm']]
            self.passages.setdefault(a,[]).append((b,p));self.passages.setdefault(b,[]).append((a,p))

    def index(self,p):return tuple(bisect.bisect_left(self.axes[i],round(p[i],6)) for i in range(3))
    def point(self,node):return tuple(self.axes[i][node[i]] for i in range(3))

    def node_free(self,node):
        if node not in self.cache:
            p=self.point(node);self.space.stats['cell_queries']+=1
            self.cache[node]=self.space.free(p,p,self.margin)
        return self.cache[node]

    def neighbors(self,node):
        p=self.point(node)
        moves=[]
        for axis in range(3):
            for sign in (-1,1):
                idx=node[axis]+sign
                if not 0<=idx<len(self.axes[axis]):continue
                nxt=list(node);nxt[axis]=idx;nxt=tuple(nxt)
                moves.append(nxt)
        # Follow an inclined ceiling without forcing alternating horizontal and
        # vertical 90-degree turns every few millimetres. Every long edge still
        # receives the full swept-volume collision check below.
        region=next((r for r in self.space.regions if r.contains(p,self.radius)),None)
        if region:
            for axis,slope in enumerate(region.lower_coefficients[:2]):
                if abs(slope)<.01:continue
                span=max(2,min(12,round(1/abs(slope))))
                for sign in (-1,1):
                    idx=node[axis]+sign*span
                    if not 0<=idx<len(self.axes[axis]):continue
                    target_z=p[2]+slope*(self.axes[axis][idx]-p[axis])
                    pos=bisect.bisect_left(self.axes[2],target_z)
                    for height in (pos-1,pos):
                        if not 0<=height<len(self.axes[2]):continue
                        nxt=list(node);nxt[axis]=idx;nxt[2]=height;moves.append(tuple(nxt))
        seen=set()
        for nxt in moves:
            if nxt in seen:continue
            seen.add(nxt)
            if not self.node_free(nxt):continue
            edge=tuple(sorted((node,nxt)))
            if edge not in self.edges:self.edges[edge]=self.space.free(p,self.point(nxt),self.radius)
            if self.edges[edge]:yield nxt,None
        for nxt,portal in self.passages.get(node,[]):
            q=self.point(nxt)
            if not self.node_free(nxt):continue
            if self.space.forbidden_hit(p,q,self.radius):continue
            if self.space.collisions(p,q,self.radius,portal['element_ids']):continue
            if any(g.segment_distance(p,q,a,b)<self.radius+r['radius_mm'] for r in self.space.reservations for a,b in zip(r['points'],r['points'][1:])):continue
            yield nxt,portal


def passage_cost(p,weights):
    if 'equivalent_length_m' in p:
        value=float(p['equivalent_length_m'])
        if not math.isfinite(value) or value<0:raise ValueError('Coste constructivo equivalente inválido.')
        return value*weights['length']
    return (weights['conditional'] if p['classification']=='CONDITIONAL' else weights['crossing'])*p.get('crossing_multiplier',1.0)


def calculate_route_cost(points,weights,passages=(),preferred_penalty=0):
    length=sum(g.distance(a,b) for a,b in zip(points,points[1:]))/1000
    turns=[]
    for a,b,c in zip(points,points[1:],points[2:]):
        u=direction(a,b);v=direction(b,c)
        angle=math.acos(max(-1,min(1,g.dot(u,v))))
        if angle>1e-6:turns.append(angle/(math.pi/2))
    components={'length':length*weights['length'],'bends':sum(turns)*weights['bend'],
                'passages':sum(passage_cost(p,weights) for p in passages),
                'nonpreferred':preferred_penalty*weights['nonpreferred']}
    return {'total':sum(components.values()),'components':components,'units':'relative cost; length in m; turn in 90 degree equivalents'}


def find_route_3d(space,start,goal,profile,search,step,passages=(),scenario='A',cancelled=None,diversity=()):
    started=time.monotonic();grid=SparseGrid(space,start,goal,step,profile,passages,scenario,search.get('local_bounds_mm'))
    if not grid.node_free(grid.start) or not grid.node_free(grid.goal):
        return {'status':'NOT_EVALUATED','reason':'ENDPOINT_CLEARANCE_AT_RESOLUTION','resolution_mm':step,'expanded':0}
    required=2*profile.bend_radius_mm
    start_heading=search.get('start_direction')
    start_heading=tuple(round(v,7) for v in g.unit(start_heading)) if start_heading else None
    start_state=(grid.start,start_heading,float(search.get('start_straight_mm',0)),0)
    serial=itertools.count();queue=[(g.distance(start,goal)/1000*profile.weights['length'],0,next(serial),start_state)]
    costs={start_state:0};parents={};expanded=0;goal_state=None;closest=start_state
    def incomplete(reason):
        nodes=[];used=[];state=closest
        while state!=start_state:
            nodes.append(grid.point(state[0]));state,portal=parents[state]
            if portal:used.append(portal)
        nodes.append(grid.point(grid.start));nodes.reverse();used.reverse()
        return {'status':'NOT_EVALUATED','reason':reason,'expanded':expanded,'resolution_mm':step,
                'partial_points_mm':nodes,'partial_passages':used}
    while queue:
        _,cost,_,state=heapq.heappop(queue)
        if cost>costs.get(state,float('inf'))+1e-9:continue
        expanded+=1
        if g.distance(grid.point(state[0]),goal)<g.distance(grid.point(closest[0]),goal):closest=state
        if expanded==1 or expanded%16==0:
            if cancelled and cancelled():return {'status':'NOT_EVALUATED','reason':'CANCELLED','expanded':expanded}
            if expanded>=search['max_nodes'] or time.monotonic()-started>=search['max_seconds']:
                return incomplete('SEARCH_LIMIT')
        node,arrival,straight,previous_trim=state
        if node==grid.goal and straight+1e-5>=previous_trim:
            wanted=search.get('end_direction')
            if wanted and arrival:
                angle=math.acos(max(-1,min(1,g.dot(arrival,g.unit(wanted)))))
                if angle>math.pi-1e-6:continue
                trim=profile.bend_radius_mm*math.tan(angle/2) if angle>1e-6 else 0
                if straight+1e-5<previous_trim+trim or search.get('end_straight_mm',0)+1e-5<trim:continue
            goal_state=state;break
        a=grid.point(node)
        for nxt,portal in grid.neighbors(node):
            b=grid.point(nxt);travel=g.distance(a,b);heading=direction(a,b)
            angle=math.acos(max(-1,min(1,g.dot(arrival,heading)))) if arrival else 0
            if angle>math.pi-1e-6:continue
            trim=profile.bend_radius_mm*math.tan(angle/2) if angle>1e-6 else 0
            if angle>1e-6 and straight+1e-5<previous_trim+trim:continue
            if profile.max_slope is not None:
                horizontal=math.hypot(b[0]-a[0],b[1]-a[1])
                if abs(b[2]-a[2])>profile.max_slope*horizontal+1e-6:continue
            next_trim=trim if angle>1e-6 else previous_trim
            run=min(max(required,2*next_trim),travel+(straight if angle<1e-6 else 0))
            state2=(nxt,heading,round(run,3),round(next_trim,6))
            extra=travel/1000*(profile.weights['length']+profile.weights['nonpreferred']*space.preferred_penalty(b))
            if profile.weights.get('proximity',0) and space.collisions(b,b,profile.radius_mm+50):
                extra+=travel/1000*profile.weights['proximity']
            extra+=angle/(math.pi/2)*profile.weights['bend']
            if portal:extra+=passage_cost(portal,profile.weights)
            if diversity:
                if any(g.point_segment(c,d,b)<profile.radius_mm*3 for path in diversity for c,d in zip(path,path[1:])):
                    extra+=travel/1000
            candidate=cost+extra
            if candidate+1e-9>=costs.get(state2,float('inf')):continue
            costs[state2]=candidate;parents[state2]=(state,portal)
            heuristic=g.distance(b,goal)/1000*profile.weights['length']
            heapq.heappush(queue,(candidate+heuristic,candidate,next(serial),state2))
    if goal_state is None:return incomplete('NO_ROUTE_AT_RESOLUTION')
    nodes=[];used=[];state=goal_state
    while state!=start_state:
        nodes.append(grid.point(state[0]));state,portal=parents[state]
        if portal:used.append(portal)
    nodes.append(grid.point(grid.start));nodes.reverse();used.reverse()
    unique={p['id']:p for p in used}
    return {'status':'CONDITIONAL' if any(p['classification']=='CONDITIONAL' and p['id'] not in space.constraints.get('approved_passages',[]) for p in unique.values()) else 'VALID_WITH_WARNINGS',
            'points_mm':nodes,'passages':list(unique.values()),'cost':costs[goal_state],'expanded':expanded,
            'resolution_mm':step,'seconds':time.monotonic()-started,'cached_cells':len(grid.cache)}
