"""Bounded joint allocation of distributor ports and local wall stations.

This is a constructive preference, never a geometric certificate. All admitted
stations are examined; a bounded beam retains alternatives across connections.
Station overlap is penalised rather than used as a proof of infeasibility.
"""
import copy
import time
from . import geometry as g
from ..settings import for_route,profile
from .astar_3d import passage_cost


def assign(space,requests,configuration,deadline,cancelled=None,beam_width=24):
    started=time.monotonic();portals=configuration['constraints']['passages']
    lookup={p['id']:p for p in portals};by_hosts={};evaluated=0;complete=True
    for portal in portals:
        by_hosts.setdefault(tuple(sorted(portal['element_ids'])),[]).append(portal)
    radii={r['route_id']:profile(for_route(configuration,r['route_id'])).radius_mm for r in requests}
    def stopped():return time.monotonic()>=deadline or bool(cancelled and cancelled())
    def overlap(candidate,used,radius):
        a,b=candidate['points_mm']
        return sum(bool(set(candidate['element_ids'])&set(other['element_ids'])) and
            g.segment_distance(a,b,*other['points_mm'])<radius+other_radius-1e-6
            for other,other_radius in used)
    def stations(corridor,source,destination,radius,used):
        nonlocal evaluated
        # A small internal beam avoids a Cartesian product over every wall.
        variants=[(0,0.,[],[],list(used),tuple(source['xyz_mm']))]
        for edge in corridor['edges']:
            if edge['kind']!='PORTAL':
                variants=[(n,c,es+[edge],ps,u,last) for n,c,es,ps,u,last in variants];continue
            original=lookup.get(edge['portal_id'])
            if original is None:return []
            candidates=by_hosts.get(tuple(sorted(original['element_ids'])),[])
            expanded=[];axis=g.unit(g.sub(*reversed(edge['points_mm'])))
            for portal in candidates:
                if stopped():break
                if portal['radius_mm']+1e-6<radius:continue
                if portal['id'] in configuration['constraints'].get('rejected_passages',[]):continue
                if portal['classification']=='CONDITIONAL' and corridor.get('scenario')!='B' and portal['id'] not in configuration['constraints'].get('approved_passages',[]):continue
                # Stay on this topological edge: unrelated stations on the same
                # host can lead to a different room or an unavailable region.
                a,b=portal['points_mm']
                if g.dot(g.sub(b,a),axis)<0:a,b=b,a
                if not any(r.contains(a,0) for r in space.regions) or not any(r.contains(b,0) for r in space.regions):continue
                for count,cost,edges,chosen,occupied,last in variants:
                    evaluated+=1
                    new_edge=dict(edge,portal_id=portal['id'],points_mm=[a,b],classification=portal['classification'])
                    distance=(g.distance(last,a)+g.distance(a,b))/1000+passage_cost(portal,configuration['weights'])
                    expanded.append((count+overlap(portal,occupied,radius),cost+distance,
                        edges+[new_edge],chosen+[portal],occupied+[(portal,radius)],tuple(b)))
            expanded.sort(key=lambda v:(v[0],v[1]+g.distance(v[-1],destination['xyz_mm'])/1000))
            variants=expanded[:4]
            if not variants:break
        return [(count,cost+g.distance(last,destination['xyz_mm'])/1000,
            dict(corridor,edges=edges,portal_ids=[p['id'] for p in chosen]),chosen)
            for count,cost,edges,chosen,occupied,last in variants]
    # Least flexible connections get first access; beam alternatives protect
    # the others instead of permanently reserving the first discovered route.
    order=sorted(requests,key=lambda r:(sum(len(cs) for cs in r.get('topological_corridors',{}).values()) or float('inf'),r['route_id']))
    # score=(unassigned, station overlaps, conditional interventions, length).
    beam=[((0,0,0,0.),{},set(),[])]
    for request_index,request in enumerate(order):
        if stopped():complete=False;break
        rid=request['route_id'];radius=radii[rid];expanded=[]
        ports={p['id']:p for p in [request['source']]+request.get('source_alternatives',[]) if p}
        # Generate station alternatives once, then compare them with every
        # global beam state. Repeating this geometry-independent work for
        # each state used to spend the entire allowance on the first routes.
        choices=[];seen=set()
        for port_id,corridors in request.get('topological_corridors',{}).items():
            if port_id not in ports:continue
            port=ports[port_id]
            for corridor in corridors:
                key=(port_id,tuple(corridor.get('topology_nodes',[])),tuple(corridor['portal_ids']),corridor.get('scenario','A'))
                if key in seen:continue
                seen.add(key)
                if stopped():complete=False;break
                for conflicts,length,variant,chosen in stations(corridor,port,request['destination'],radius,[]):
                    choices.append((port_id,length,variant,chosen))
        for score,selected,used_ports,used_stations in beam:
            for port_id,length,variant,chosen in choices:
                if stopped():complete=False;break
                if port_id in used_ports:continue
                conflicts=sum(overlap(p,used_stations,radius) for p in chosen)
                conditional=sum(p['classification']=='CONDITIONAL' for p in chosen)
                new_score=(score[0],score[1]+conflicts,score[2]+conditional,score[3]+length)
                expanded.append((new_score,dict(selected,**{rid:{'port_id':port_id,'corridor':variant,
                    'station_overlap_count':conflicts}}),used_ports|{port_id},used_stations+[(p,radius) for p in chosen]))
            expanded.append(((score[0]+1,score[1],score[2],score[3]),selected,used_ports,used_stations))
        expanded.sort(key=lambda state:state[0])
        beam=[];families=set();deferred=[]
        for state in expanded:
            family=tuple((rid,choice['port_id'],tuple(choice['corridor'].get('portal_ids',[]))) for rid,choice in sorted(state[1].items()))
            if family not in families:beam.append(state);families.add(family)
            else:deferred.append(state)
            if len(beam)>=beam_width:break
        if len(beam)<beam_width:beam.extend(deferred[:beam_width-len(beam)])
        if not complete:break
    best=min(beam,key=lambda state:state[0]);selected=best[1]
    for request in requests:
        chosen=selected.get(request['route_id'])
        if not chosen:continue
        original=request['source'];ports={p['id']:p for p in [original]+request.get('source_alternatives',[])}
        preferred=ports[chosen['port_id']]
        protected=set(configuration['constraints']['locked_routes'])
        if request['route_id'] not in protected:
            request['source']=preferred
            request['source_alternatives']=[p for p in ports.values() if p['id']!=preferred['id']]
        request['global_corridor_preference']=copy.deepcopy(chosen)
        values=request['topological_corridors'][chosen['port_id']]
        request['topological_corridors'][chosen['port_id']]=[chosen['corridor']]+values
    return {'selected':selected,'score':list(best[0]),'station_combinations_examined':evaluated,
        'beam_width':beam_width,'completed':complete,'seconds':time.monotonic()-started,
        'scope':'Joint constructive preferences; bounded beam, station occupancy only, no 3-D capacity certificate'}
