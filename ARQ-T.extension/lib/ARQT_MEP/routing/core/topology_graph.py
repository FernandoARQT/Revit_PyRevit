"""Profile-dependent constructive region graph, never a route certificate.

Region nodes are covers, not proven obstacle-free connected components. Missing
paths therefore diagnose an incomplete representation, never impossibility.
"""
import heapq
import itertools
import time
from shapely.geometry import Point,LineString,mapping
from shapely.ops import unary_union
from . import geometry as g
from .spatial_model import Region


class TopologyGraph:
    def __init__(self,space,radius,deadline=float('inf'),cancelled=None):
        self.space=space;self.radius=radius;self.regions=[];self.parents=[]
        # One ceiling may cover several rooms separated by walls. Treating it
        # as one connected node would silently omit the necessary portals.
        walls=[(o,LineString([p[:2] for p in o.data['wall_axis_mm']]).buffer(o.data['wall_thickness_mm']/2,cap_style=2))
               for o in space.obstacles if o.data.get('wall_axis_mm') and o.data.get('wall_thickness_mm',0)>0]
        footprint=unary_union([poly for _,poly in walls]) if walls else None
        def add(parent,region,polygon,lower=None):
            parts=list(polygon.geoms) if hasattr(polygon,'geoms') else [polygon]
            for part in parts:
                if part.geom_type!='Polygon' or part.area<1e-6:continue
                self.regions.append(Region({'id':'topology:'+str(len(self.regions)), 'polygon':mapping(part),
                    'lower':lower or region.lower,'upper':region.upper}));self.parents.append(parent)
        construction_complete=True
        for parent,region in enumerate(space.regions):
            if time.monotonic()>=deadline or (cancelled and cancelled()):construction_complete=False;break
            add(parent,region,region.polygon.difference(footprint) if walls else region.polygon)
            for wall,poly in walls:
                patch=region.polygon.intersection(poly)
                if patch.is_empty:continue
                x0,y0,x1,y1=patch.bounds;a,b,c=region.lower_coefficients
                lower=max(wall.box[5],max(a*x+b*y+c for x in (x0,x1) for y in (y0,y1)))
                add(parent,region,patch,{'z':lower})
        self.edges={i:[] for i in range(len(self.regions))};self.complete=construction_complete
        for i,a in enumerate(self.regions):
            for j in range(i):
                if time.monotonic()>=deadline or (cancelled and cancelled()):self.complete=False;return
                b=self.regions[j]
                if a.polygon.distance(b.polygon)>1e-6:continue
                intersection=a.polygon.intersection(b.polygon)
                if intersection.is_empty:continue
                p=intersection.representative_point() if intersection.area>0 else intersection.centroid
                x,y=p.x,p.y
                lo=max(u*x+v*y+w+radius/r.lower_normal_z for r in (a,b) for u,v,w in [r.lower_coefficients])
                hi=min(u*x+v*y+w-radius/r.upper_normal_z for r in (a,b) for u,v,w in [r.upper_coefficients])
                witness=(x,y,(lo+hi)/2)
                if hi>=lo and space.free(witness,witness,radius):
                    self.add(i,j,{'kind':'CONTINUITY','points_mm':[witness],'cost':0.001})

    def regions_at(self,p):
        return [i for i,r in enumerate(self.regions) if r.contains(p,0)]

    def add(self,a,b,edge):
        self.edges[a].append((b,dict(edge)))
        reverse=dict(edge,points_mm=list(reversed(edge['points_mm'])))
        self.edges[b].append((a,reverse))

    def paths(self,start,goal,k=8,deadline=float('inf'),cancelled=None):
        goals=set(self.regions_at(goal));serial=itertools.count();queue=[];results=[];seen=set();expanded=0
        for node in self.regions_at(start):heapq.heappush(queue,(0,next(serial),[node],[]))
        while queue and len(results)<k:
            if time.monotonic()>=deadline or (cancelled and cancelled()):break
            cost,_,nodes,edges=heapq.heappop(queue);expanded+=1
            if expanded>20000:break
            if nodes[-1] in goals:
                signature=(tuple(nodes),tuple(e.get('portal_id') for e in edges))
                if signature not in seen:
                    seen.add(signature);results.append({'region_indices':list(dict.fromkeys(self.parents[i] for i in nodes)),
                        'topology_nodes':nodes,'edges':edges,'estimated_cost':cost,
                        'portal_ids':[e['portal_id'] for e in edges if e.get('portal_id')],
                        'status':'TOPOLOGICAL_CANDIDATE_NOT_CERTIFIED'})
                continue
            # Stations on one wall are geometric alternatives of the same
            # topological corridor. Collapse these before K-path enumeration.
            unique={}
            for nxt,edge in self.edges[nodes[-1]]:
                key=(nxt,edge['kind'],tuple(edge.get('host_ids',[])))
                if key not in unique or edge['cost']<unique[key][1]['cost']:unique[key]=(nxt,edge)
            for nxt,edge in unique.values():
                if nxt not in nodes:heapq.heappush(queue,(cost+edge['cost'],next(serial),nodes+[nxt],edges+[edge]))
        return results
