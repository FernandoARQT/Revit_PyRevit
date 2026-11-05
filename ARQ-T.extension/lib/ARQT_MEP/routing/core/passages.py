"""Explicit graph interventions, never free traversal through wall cells."""
import hashlib
import math
import time
from shapely.geometry import Point
from . import geometry as g
from ... import surface
from ..settings import effective_radius,for_route,profile


def generate_passages(space,requests,configuration,_radius=None,deadline=float('inf'),cancelled=None):
    explicit=[p for p in configuration['constraints']['passages'] if p.get('generated_by') not in ('WALL_CANDIDATES_V1','SLAB_CANDIDATES_V1')]
    if not requests:return explicit
    radii=sorted({effective_radius(for_route(configuration,r['route_id'])) for r in requests})
    if _radius is None and len(radii)>1:
        # A large main must not suppress feasible openings for smaller branches.
        result={p['id']:p for p in explicit}
        for radius in radii:
            for passage in generate_passages(space,requests,configuration,_radius=radius,deadline=deadline,cancelled=cancelled):result[passage['id']]=passage
        return list(result.values())
    radius=_radius if _radius is not None else radii[0]
    margin=radius+2
    result=list(explicit)
    anchors=[p['xyz_mm'] for r in requests for p in (r.get('source'),r.get('destination')) if p]
    anchors.extend(p['xyz_mm'] for r in requests for p in r.get('source_alternatives',[]))
    from .multi_route import compact_anchor
    for request in requests:
        local=profile(for_route(configuration,request['route_id']))
        for port in (request.get('source'),request.get('destination')):
            if port:
                anchor=compact_anchor(space,port,local,configuration)
                if anchor:anchors.append(anchor[1])
    for slab in space.obstacles:
        if cancelled and cancelled():raise InterruptedError('Generación de pasos cancelada.')
        if time.monotonic()>=deadline:
            space.stats['passage_generation_incomplete']=True;return result
        if slab.data.get('hard_forbidden') or slab.data.get('construction_identity')!='HOLLOW_CORE_SLAB':continue
        b=slab.box
        stations={(round(p[0],3),round(p[1],3)) for p in anchors
                  if b[0]+margin<=p[0]<=b[3]-margin and b[1]+margin<=p[1]<=b[4]-margin}
        stations.add(((b[0]+b[3])/2,(b[1]+b[4])/2))
        for x,y in sorted(stations):
            if time.monotonic()>=deadline:
                space.stats['passage_generation_incomplete']=True;return result
            ends=[(x,y,b[2]-margin-25),(x,y,b[5]+margin+25)]
            if not all(space.free(p,p,margin) for p in ends):continue
            if space.collisions(*ends,radius,[slab.id]):continue
            identity=slab.id+':'+str((x,y))+':radius:'+str(radius)
            result.append({'id':'slabpass:'+hashlib.sha256(identity.encode()).hexdigest()[:16],
                'equivalent_length_m':slab.data.get('equivalent_length_m',8.0),
                'construction_class':'HOLLOW_CORE_SLAB',
                'element_ids':[slab.id],'element_id':slab.data.get('element_id'),'points_mm':ends,
                'xyz_mm':[x,y,(b[2]+b[5])/2],'direction':[0,0,1],'thickness_mm':b[5]-b[2],
                'required_dimension_mm':2*radius,'radius_mm':radius,'classification':'CONDITIONAL',
                'crossing_multiplier':slab.data.get('crossing_multiplier',5.0),
                'intervention':'STRUCTURAL_PROPOSAL','approval_status':'PENDING_TECHNICAL_APPROVAL',
                'evaluable_zone':slab.id,'generated_by':'SLAB_CANDIDATES_V1'})
    walls=[o for o in space.obstacles if not o.data.get('hard_forbidden') and o.data.get('wall_axis_mm')
           and o.classification in ('PASSABLE','CONDITIONAL')]
    coverage={'barriers':len(walls),'visited':[],'admitted':[],'exhausted_without_candidate':[],
              'pending':[o.id for o in walls],'complete':False}
    space.stats.setdefault('passage_coverage',{})[str(radius)]=coverage
    active=[]
    try:
        # Cover every barrier once before refining a successful earlier wall.
        for wall in walls:
            _check(deadline,cancelled)
            iterator=_wall_candidates(space,wall,radius,anchors,deadline,cancelled)
            coverage['visited'].append(wall.id);coverage['pending'].remove(wall.id)
            try:
                result.append(next(iterator));active.append(iterator);coverage['admitted'].append(wall.id)
            except StopIteration:coverage['exhausted_without_candidate'].append(wall.id)
        while active:
            pending=[]
            for iterator in active:
                _check(deadline,cancelled)
                try:result.append(next(iterator));pending.append(iterator)
                except StopIteration:pass
            active=pending
        coverage['complete']=True
    except TimeoutError:space.stats['passage_generation_incomplete']=True
    return result


def _check(deadline,cancelled):
    if cancelled and cancelled():raise InterruptedError('Generación de pasos cancelada.')
    if time.monotonic()>=deadline:raise TimeoutError()


def _wall_candidates(space,wall,radius,anchors,deadline,cancelled):
    data=wall.data;a,b=data['wall_axis_mm'];margin=radius+2;length=g.distance(a,b)
    if length<2*margin:return
    tangent=g.unit(g.sub(b,a));normal=(-tangent[1],tangent[0],0)
    count=max(2,math.ceil(length/max(2*margin,500)))
    stations={length*j/count for j in range(1,count)}
    stations.update(max(margin,min(length-margin,g.dot(g.sub(p,a),tangent))) for p in anchors)
    half=data['wall_thickness_mm']/2+margin+25
    for station in sorted(stations,key=lambda v:(abs(v-length/2),v)):
        _check(deadline,cancelled);center=g.add(a,g.mul(tangent,station));zs=set()
        for region in space.regions:
            if region.polygon.distance(Point(center[:2]))>half+margin:continue
            lower=surface.height(region.lower,*center[:2]);upper=surface.height(region.upper,*center[:2])
            zs.update((round((lower+upper)/2,3),round(lower+margin+2,3),round(upper-margin-2,3)))
        zs.update(round(p[2],6) for p in anchors if g.point_segment(a,b,p)<2000)
        for z in sorted(zs):
            _check(deadline,cancelled);center=(center[0],center[1],z)
            ends=[g.add(center,g.mul(normal,sign*half)) for sign in (-1,1)]
            if not all(space.free(p,p,margin) for p in ends):continue
            if space.collisions(*ends,radius,[wall.id]):continue
            identity=wall.id+':'+str([round(v,2) for v in center])+':radius:'+str(radius)
            cost={k:data[k] for k in ('equivalent_length_m','construction_class','cost_basis') if k in data}
            yield dict(cost,**{'id':'wallpass:'+hashlib.sha256(identity.encode()).hexdigest()[:16],
                'element_ids':[wall.id],'element_id':data.get('element_id'),'points_mm':ends,
                'xyz_mm':center,'direction':normal,'thickness_mm':data['wall_thickness_mm'],
                'required_dimension_mm':2*radius,'radius_mm':radius,'classification':wall.classification,
                'intervention':'NEW_NONSTRUCTURAL' if wall.classification=='PASSABLE' else 'STRUCTURAL_PROPOSAL',
                'design_state':'GEOMETRIC_CANDIDATE' if wall.classification=='PASSABLE' else 'PROPOSED_STRUCTURAL_PENETRATION',
                'approval_status':'CONSTRUCTION_REVIEW' if wall.classification=='PASSABLE' else 'PENDING_TECHNICAL_APPROVAL',
                'evaluable_zone':wall.id if wall.classification=='CONDITIONAL' else None,'generated_by':'WALL_CANDIDATES_V1'})
