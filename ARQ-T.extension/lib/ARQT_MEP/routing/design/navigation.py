"""Lazy capsule-checked reference paths inside existing topology regions.

These are schematic witnesses, not individual tube axes. Existing region,
portal and collision machinery remains authoritative. No RRT/IRIS is run.
"""
import hashlib
import heapq
import itertools
import math
import time
from shapely.geometry import Point
from .graph import _seeds,_parts
from .spatial import valid_height_intervals
from ..core import geometry as g


class Navigation:
    def __init__(self,space,topology,radius,profile,clearance,deadline,cancelled=None):
        self.space=space;self.topology=topology;self.radius=radius;self.profile=profile;self.clearance=clearance
        self.deadline=deadline;self.cancelled=cancelled;self.regions={};self.checks={};self.nodes={};self.edges={};self.path_cache={};self.refined=set();self.failures=[]
        self.refinement_enabled=False

    def stopped(self):return time.monotonic()>=self.deadline or bool(self.cancelled and self.cancelled())

    def _points(self,index):
        if index in self.regions:return self.regions[index]
        region=self.topology.regions[index];parent=self.space.regions[self.topology.parents[index]].data['id'];points=[]
        for polygon in _parts(region.polygon.buffer(-self.radius-.01)):
            samples,_=_seeds(polygon,500)
            for xy,_ in samples[:180]:
                if self.stopped():break
                for lo,hi in valid_height_intervals(self.space,*xy,self.profile,self.clearance,{parent}):
                    for fraction in (.25,.75):
                        p=(xy[0],xy[1],lo+(hi-lo)*fraction)
                        if region.contains(p,self.radius) and self.space.free(p,p,self.radius):points.append(p)
        self.regions[index]=list(dict.fromkeys(tuple(round(v,5) for v in p) for p in points))
        return self.regions[index]

    def segment(self,a,b,index):
        key=(index,tuple(sorted((tuple(a),tuple(b)))))
        if key not in self.checks:
            region=self.topology.regions[index]
            # A continuity witness can sit exactly on the shared boundary of
            # two covers. Requiring its whole capsule inside each cover
            # separately falsely disconnects a proven free union. Keep the
            # centreline in this topology cell and prove the complete capsule
            # against the actual spatial union and obstacles.
            self.checks[key]=region.segment(a,b,0) and self.space.free(a,b,self.radius)
        return self.checks[key]

    def path(self,start,goal,index,variant=0):
        key=(tuple(start),tuple(goal),index,variant,index in self.refined)
        if key in self.path_cache:return self.path_cache[key]
        result=self._path(start,goal,index,variant)
        if not self.stopped():self.path_cache[key]=result
        return result

    def _path(self,start,goal,index,variant=0):
        start=tuple(start);goal=tuple(goal)
        if math.dist(start,goal)<1e-4:return [start]
        if variant:
            points=self._points(index)
            if points:
                region=self.topology.regions[index];center=region.polygon.representative_point()
                if variant==1:
                    hub=min(points,key=lambda p:math.hypot(p[0]-center.x,p[1]-center.y)+abs(p[2]-(start[2]+goal[2])/2)*.2)
                else:
                    # A canonical perimeter station is shared across requests,
                    # providing a materially different grouping candidate.
                    hub=min(points,key=lambda p:(p[0],abs(p[1]-center.y),p[2]))
                if min(math.dist(hub,start),math.dist(hub,goal))>1:
                    a=self.path(start,hub,index);b=self.path(hub,goal,index)
                    if a and b:return a+b[1:]
        if self.segment(start,goal,index):return [start,goal]
        points=list(dict.fromkeys([start,goal]+self._points(index)));serial=itertools.count();queue=[(math.dist(start,goal),next(serial),0)]
        distance={0:0.};parents={};neighbors={};closed=set()
        while queue and not self.stopped():
            _,_,i=heapq.heappop(queue)
            if i in closed:continue
            closed.add(i)
            if i==1:
                path=[i]
                while path[-1] in parents:path.append(parents[path[-1]])
                result=[points[j] for j in reversed(path)]
                # Greedy compression only across continuously checked segments.
                result=self.simplify(result,index)
                return result
            if i not in neighbors:
                ranked=sorted((math.dist(points[i],p),j) for j,p in enumerate(points) if j!=i)
                neighbors[i]=list(dict.fromkeys([1]+[j for _,j in ranked[:24]]))
            for j in neighbors[i]:
                if self.stopped():break
                if j in closed or not self.segment(points[i],points[j],index):continue
                cost=distance[i]+math.dist(points[i],points[j])
                if cost>=distance.get(j,float('inf')):continue
                distance[j]=cost;parents[j]=i;heapq.heappush(queue,(cost+math.dist(points[j],goal),next(serial),j))
        if self.refinement_enabled and not self.stopped() and index not in self.refined:
            self.refined.add(index);region=self.topology.regions[index];parent=self.space.regions[self.topology.parents[index]].data['id'];extra=[]
            samples=[(p[0],p[1]) for p in self._points(index)]
            steps=max(2,int(math.dist(start,goal)/250))
            samples.extend((start[0]+(goal[0]-start[0])*i/steps,start[1]+(goal[1]-start[1])*i/steps) for i in range(1,steps))
            for x,y in dict.fromkeys(samples):
                if self.stopped():break
                for lo,hi in valid_height_intervals(self.space,x,y,self.profile,self.clearance,{parent}):
                    for z in {lo+(hi-lo)*.05,(lo+hi)/2,lo+(hi-lo)*.95,min(hi,max(lo,start[2])),min(hi,max(lo,goal[2]))}:
                        point=(x,y,z)
                        if region.contains(point,0) and self.space.free(point,point,self.radius):extra.append(point)
            self.regions[index]=list(dict.fromkeys(self._points(index)+extra))
            return self.path(start,goal,index,variant)
        if not self.stopped() and len(self.failures)<24:self.failures.append({'region_index':index,'start':start,'goal':goal,'sample_nodes':len(points)})
        return None

    def simplify(self,points,index):
        result=[points[0]];i=0
        while i<len(points)-1:
            j=len(points)-1
            while j>i+1 and not self.segment(points[i],points[j],index):j-=1
            result.append(points[j]);i=j
        return result

    def register(self,a,b,region_id,portal=None):
        # Spatial edge identity is network independent for joint occupation.
        signature=(tuple(sorted((tuple(round(v,3) for v in a),tuple(round(v,3) for v in b)))),portal['id'] if portal else None)
        eid='selected-edge:'+hashlib.sha256(repr(signature).encode()).hexdigest()[:20]
        if eid not in self.edges:
            width=self.section_width(a,b,portal)
            self.edges[eid]={'edge_id':eid,'points_mm':[list(a),list(b)],'length_mm':math.dist(a,b),
                'region_id':region_id,'portal_id':portal['id'] if portal else None,'width_mm':width,
                'classification':portal['classification'] if portal else 'FREE','portal':portal,
                'capacity_status':'PRELIMINARY_STRAIGHT_SECTION_ONLY'}
        return eid

    def section_width(self,a,b,portal=None):
        delta=g.sub(b,a);horizontal=math.hypot(delta[0],delta[1])
        if horizontal<1e-5:return 2*self.radius
        normal=(-delta[1]/horizontal,delta[0]/horizontal,0);ignore=portal['element_ids'] if portal else ()
        widths=[]
        # Bounded straight-section estimate. Bends and actual lane geometry
        # remain pending in stage 2; a wide sample is never a certificate.
        for t in ((0.,1.) if portal else (.15,.5,.85)):
            if self.stopped():break
            center=g.add(a,g.mul(delta,t));extents=[]
            for sign in (-1,1):
                low=0.;high=1800.
                for _ in range(9):
                    middle=(low+high)/2;point=g.add(center,g.mul(normal,sign*middle))
                    if self.space.free(center,point,self.radius,ignore):low=middle
                    else:high=middle
                extents.append(low)
            widths.append(sum(extents)+2*self.radius)
        return min(widths,default=2*self.radius)
