"""Stage 1: complete physical-endpoint paths and joint radial distribution."""
import copy
import itertools
import math
import time
from dataclasses import replace
from . import endpoints,schematic
from .navigation import Navigation
from .spatial import envelope
from .. import settings
from ..core import geometry as g
from ..core.spatial_model import build_spatial_model
from ..core.topology_graph import TopologyGraph
from ..core.portal_graph import attach
from ..core.passages import generate_passages
from ..core.multi_route import compact_anchor
from ..core.integer_master import solve


def selections(p):
    return [{'terminal_id':tid,'network_type':t['network_type'],**({'elbow_endpoint_id':t['elbow_endpoint_id']} if t.get('elbow_endpoint_id') else {})}
            for tid,t in p.terminals.items()]


def configuration(p,profile):
    cfg=copy.deepcopy(p.configuration);cfg['routing_strategy']='GLOBAL_RADIAL_V3'
    cfg.update(section={'shape':'CIRCULAR','nominal_diameter_mm':profile.nominal_diameter_mm,
        'outer_diameter_mm':profile.external_diameter_mm,'insulation_mm':profile.insulation_thickness_mm},
        clearance_mm=cfg.get('design_clearance_mm',cfg.get('clearance_mm',15)),bend_radius_mm=profile.minimum_bend_radius_mm)
    return settings.validate(cfg)


def _dock(space,port,profile,cfg):
    actual=dict(port,kind='TERMINAL_ADAPTER_OUTLET') if port.get('kind')=='ELBOW_OUTLET' else port
    anchor=compact_anchor(space,actual,profile,cfg)
    if anchor is None:return None
    a,b=anchor
    if space.forbidden_hit(a,b,profile.radius_mm) or space.collisions(a,b,profile.radius_mm,[port['element_key']]):return None
    return [list(a),list(b)]


def _reference(topological,source,destination,nav,portal_by_id,variant=0):
    nodes=topological['topology_nodes'];beam=[(0.,source,[],[])]
    for i,transition in enumerate(topological['edges']):
        region=nav.space.regions[nav.topology.parents[nodes[i]]].data['id']
        choices=[transition]
        if transition['kind']=='PORTAL':
            # Topology deliberately collapses stations on the same wall. A
            # failed central station must not eliminate every other admissible
            # opening between these exact regions.
            seen={};hosts=set(transition.get('host_ids',[]))
            for nxt,edge in nav.topology.edges[nodes[i]]:
                if nxt==nodes[i+1] and edge['kind']=='PORTAL' and set(edge.get('host_ids',[]))==hosts:
                    seen[edge['portal_id']]=edge
            choices=list(seen.values()) or choices
        expanded=[]
        for cost,current,segments,used in beam:
            ordered=sorted(choices,key=lambda e:g.distance(current,e['points_mm'][0])+g.distance(e['points_mm'][-1],destination))
            for edge in ordered[:16]:
                if nav.stopped():break
                boundary=edge['points_mm'];leg=nav.path(current,boundary[0],nodes[i],variant)
                if leg is None:continue
                parts=[(a,b,region,None) for a,b in zip(leg,leg[1:])];portals=list(used)
                if edge['kind']=='PORTAL':
                    portal=portal_by_id[edge['portal_id']];a,b=boundary
                    if nav.space.forbidden_hit(a,b,nav.radius) or nav.space.collisions(a,b,nav.radius,portal['element_ids']):continue
                    parts.append((a,b,region,portal));portals.append(portal['id'])
                distance=sum(g.distance(a,b) for a,b,_,_ in parts)
                expanded.append((cost+distance,boundary[-1],segments+parts,portals))
        expanded.sort(key=lambda v:v[0]+g.distance(v[1],destination));beam=expanded[:4]
        if not beam:return None
    region=nav.space.regions[nav.topology.parents[nodes[-1]]].data['id']
    completed=[]
    for cost,current,segments,used in beam:
        leg=nav.path(current,destination,nodes[-1],variant)
        if leg is not None:completed.append((cost+sum(g.distance(a,b) for a,b in zip(leg,leg[1:])),segments+[(a,b,region,None) for a,b in zip(leg,leg[1:])],used))
    if not completed:return None
    _,segments,used=min(completed,key=lambda v:v[0]);points=[list(source)];edges=[];oriented={}
    for a,b,region,portal in segments:
        if g.distance(a,b)<1e-4:continue
        eid=nav.register(a,b,region,portal);edges.append(eid);oriented[eid]=[list(a),list(b)];points.append(list(b))
    return points,edges,oriented,used


def _resources(candidates,edge_records,equipment,profile,clearance):
    resources=[];gap=clearance;diameter=profile.external_diameter_mm+2*profile.insulation_thickness_mm
    for eid,edge in edge_records.items():
        if edge.get('connection_stub'):continue
        demands={(tid,i):diameter+gap for tid,paths in candidates.items() for i,p in enumerate(paths) if eid in p['edge_ids']}
        if demands:resources.append({'id':eid,'capacity':max(0,edge['width_mm']-2*clearance+gap),'demands':demands})
    for network,manifold in equipment.items():
        demands={(tid,i):1 for tid,paths in candidates.items() for i,p in enumerate(paths) if p['network_type']==network}
        resources.append({'id':'manifold:'+manifold['id'],'capacity':manifold['available_count'],'demands':demands})
        # Hall subset constraints establish that a distinct compatible port
        # assignment exists, without fixing physical takes before stage 2.
        groups={frozenset(p['compatible_source_ports']) for paths in candidates.values() for p in paths if p['network_type']==network}
        unions=set(groups)
        for size in range(2,min(len(groups),7)+1):
            for subset in itertools.combinations(groups,size):
                unions.add(frozenset().union(*subset))
                if len(unions)>=128:break
            if len(unions)>=128:break
        for union in unions:
            demand={(tid,i):1 for tid,paths in candidates.items() for i,p in enumerate(paths)
                if p['network_type']==network and set(p['compatible_source_ports'])<=union}
            if demand:resources.append({'id':'port-set:'+','.join(sorted(union)),'capacity':len(union),'demands':demand})
    return resources


def _manual_candidates(p,candidates,reports,destinations,roots,nav,portal_by_id,diagnostics):
    from ..core.route_geometry import passage_for
    invalid=set()
    for tid,decision in p.constraints.get('schematic_paths',{}).items():
        if tid not in candidates:continue
        item=destinations.get(decision['endpoint_id']);network=decision['network_type']
        previous=p.selected_paths.get(tid) or p.solver_state.get('selected_paths',{}).get(tid)
        candidate=None
        if item and previous and network in roots:
            ep,port,dock,compatible=item;points=copy.deepcopy(decision['points_mm']);points[-1]=list(ep['origin_xyz_mm'])
            source_port=next((v['port'] for v in p.equipment.get(network,{}).get('available_ports',[]) if v['port']['id']==decision['source_port_hint']['id']),decision['source_port_hint'])
            points[0]=list(source_port['xyz_mm']);edge_ids=[];oriented={};valid=True
            for index,(a,b) in enumerate(zip(points,points[1:])):
                if g.distance(a,b)<1e-4:continue
                portal=passage_for(a,b,[portal_by_id[k] for k in decision['portal_ids'] if k in portal_by_id],nav.radius)
                free=nav.space.free(a,b,nav.radius)
                if portal:
                    free=not nav.space.forbidden_hit(a,b,nav.radius) and not nav.space.collisions(a,b,nav.radius,portal['element_ids'])
                stub=False
                if not free and index in (0,len(points)-2) and g.distance(a,b)<max(600,4*nav.radius+nav.profile.minimum_bend_radius_mm):
                    owner=source_port['element_key'] if index==0 else port['element_key'];normal=source_port['direction'] if index==0 else g.mul(port['direction'],-1)
                    if g.dot(g.unit(g.sub(b,a)),g.unit(normal))>.999:
                        free=not nav.space.forbidden_hit(a,b,nav.radius) and not nav.space.collisions(a,b,nav.radius,[owner]);stub=free
                if not free:valid=False;break
                region=next((r.data['id'] for r in nav.space.regions if r.contains(g.mul(g.add(a,b),.5),0)),'explicit-portal')
                eid=nav.register(a,b,region,portal);nav.edges[eid]['connection_stub']=stub
                edge_ids.append(eid);oriented[eid]=[a,b]
            if valid and edge_ids:
                candidate=copy.deepcopy(previous);candidate.update(points_mm=points,edge_ids=edge_ids,oriented_edges=oriented,
                    endpoint=ep,destination=port,compatible_source_ports=compatible,source_port_hint=source_port,
                    manual_constraint=True,length_mm=sum(nav.edges[e]['length_mm'] for e in edge_ids))
                candidate['cost']={'total':candidate['length_mm']/1000}
        if decision.get('constraint_type') in ('REQUIRED','LOCKED'):
            candidates[tid]=[candidate] if candidate else []
        elif candidate:candidates[tid].insert(0,candidate)
        if candidate is None:
            invalid.add(tid);diagnostics[tid]['status']='MANUAL_SCHEMATIC_INVALID'
    return invalid


def _apply_scoped_constraints(p,candidates):
    from shapely.geometry import Polygon,LineString
    for rule in p.constraints.get('routing',{}).values():
        polygon=Polygon(rule.get('geometry_xy_mm',[]))
        if polygon.is_empty or not polygon.is_valid:raise ValueError('Región de restricción inválida.')
        for tid in rule['terminal_ids']:
            admitted=[]
            for candidate in candidates.get(tid,[]):
                hit=LineString([v[:2] for v in candidate['points_mm']]).intersects(polygon)
                if rule['constraint_type']=='FORBIDDEN' and hit:continue
                if rule['constraint_type']=='REQUIRED' and not hit:continue
                if rule['constraint_type']=='PREFERRED' and hit:candidate['cost']['total']*=.85
                admitted.append(candidate)
            candidates[tid]=admitted


def calculate(p,profile,options=None,cancelled=None):
    options=options or {};started=time.monotonic();seconds=float(options.get('seconds',120));deadline=started+seconds
    if not math.isfinite(seconds) or not .1<=seconds<=1000:raise ValueError('Presupuesto de planificación inválido.')
    from .equipment import transform
    virtual=transform(p)
    cfg=configuration(p,profile);radius=envelope(profile,cfg['clearance_mm']);core_profile=settings.profile(cfg)
    space=build_spatial_model(virtual.snapshot,cfg['constraints']);reports=endpoints.elbow_destinations(virtual.catalog,selections(p))
    equipment=endpoints.manifolds(virtual.catalog,cfg);requests=[];destinations={};roots={};diagnostics={}
    for network,previous in p.equipment.items():
        if network in equipment and previous.get('proposed_transform'):
            equipment[network].update(original_transform=copy.deepcopy(previous['original_transform']),
                proposed_transform=copy.deepcopy(previous['proposed_transform']),main_connection_status='NOT_EVALUATED')
    for network,manifold in equipment.items():
        docks=[]
        for available in manifold['available_ports']:
            port=available['port'];dock=_dock(space,port,core_profile,cfg)
            if dock:docks.append((dock,port))
        if docks:
            # One common exit-region reference, not a final port assignment.
            targets=[e['origin_xyz_mm'] for r in reports.values() if r['network_type']==network for e in r['endpoints']]
            docks.sort(key=lambda pair:sum(g.distance(pair[0][-1],t) for t in targets))
            roots[network]=docks[0];manifold['schematic_exit_xyz_mm']=docks[0][0][-1]
    for tid,report in reports.items():
        diagnostic={'terminal_id':tid,'network_type':report['network_type'],'status':report['status'],
            'endpoint_count':len(report['endpoints']),'candidate_count':0,'physical_infeasibility_proven':False}
        diagnostics[tid]=diagnostic;network=report['network_type'];manifold=equipment.get(network)
        if not report['endpoints']:continue
        if not manifold or not manifold['available_count']:diagnostic['status']='MISSING_DISTRIBUTOR_PORT';continue
        if network not in roots:diagnostic['status']='MANIFOLD_EXIT_UNRESOLVED';continue
        ports=[v['port']['id'] for v in manifold['available_ports'] if abs(v['port']['size']['diameter_mm']-profile.nominal_diameter_mm)<.1]
        # A project envelope may differ from the nominal connector. Keep the
        # requirement explicit; transitions need a referenced native solution.
        if not ports:ports=[v['port']['id'] for v in manifold['available_ports']]
        source_dock,source_port=roots[network]
        for ep in report['endpoints']:
            port=dict(report['ports'][ep['endpoint_id']],kind='ELBOW_OUTLET');dock=_dock(space,port,core_profile,cfg)
            if not dock:continue
            destinations[ep['endpoint_id']]=(ep,port,dock,ports)
            requests.append({'route_id':tid+':'+ep['endpoint_id'],'source':source_port,'destination':port})
        diagnostic['status']='GEOMETRIC_APPROACH_PENDING' if any(e['endpoint_id'] in destinations for e in report['endpoints']) else 'ELBOW_APPROACH_INFEASIBLE'
    passage_deadline=min(deadline-.1,started+max(.1,seconds*.20))
    passages=generate_passages(space,requests,cfg,deadline=passage_deadline,cancelled=cancelled) if requests else []
    cfg['constraints']['passages']=passages;space.constraints=cfg['constraints'];portal_by_id={q['id']:q for q in passages}
    topology=TopologyGraph(space,radius,deadline=min(deadline-.1,started+seconds*.32),cancelled=cancelled)
    for scenario in ('A','B'):
        if requests:attach(topology,requests[0]['source']['xyz_mm'],requests[0]['destination']['xyz_mm'],core_profile,cfg,scenario,
            deadline=min(deadline-.1,started+seconds*.38),cancelled=cancelled)
    nav=Navigation(space,topology,radius,profile,cfg['clearance_mm'],started+seconds*.84,cancelled)
    candidates={tid:[] for tid in reports};topology_budget=False
    # Round-robin destinations ensures difficult rooms are attempted before
    # refining already connected rooms. At most eight paths per terminal.
    tasks=[]
    for tid,report in reports.items():
        network=report['network_type']
        if network not in roots:continue
        source_dock,source_port=roots[network]
        for ep in report['endpoints']:
            item=destinations.get(ep['endpoint_id'])
            if not item:continue
            _,port,dock,compatible=item
            paths=topology.paths(source_dock[-1],dock[-1],24,nav.deadline,cancelled)
            diagnostics[tid]['topological_paths_found']=diagnostics[tid].get('topological_paths_found',0)+len(paths)
            diagnostics[tid]['source_regions']=topology.regions_at(source_dock[-1]);diagnostics[tid]['destination_regions']=topology.regions_at(dock[-1])
            tasks.append((tid,ep,port,dock,compatible,paths))
    for attempt in range(72):
        tier=attempt//3;variant=attempt%3
        if attempt==24:
            nav.refinement_enabled=True
            nav.path_cache={k:v for k,v in nav.path_cache.items() if v is not None}
        for tid,ep,port,dock,compatible,paths in tasks:
            if nav.stopped():topology_budget=True;break
            if tier>=len(paths) or len(candidates[tid])>=8:continue
            if attempt>=24 and candidates[tid]:continue
            network=reports[tid]['network_type'];source_dock,source_port=roots[network]
            reference=_reference(paths[tier],source_dock[-1],dock[-1],nav,portal_by_id,variant)
            if reference is None:
                diagnostics[tid]['reference_attempts_without_witness']=diagnostics[tid].get('reference_attempts_without_witness',0)+1
                continue
            points,edge_ids,oriented,selected_portal_ids=reference
            # Endpoint approaches remain distinct from capsule-checked bulk
            # witnesses. They always retain the exact physical end positions.
            for begin,end,prepend,owner in ((source_dock[0],source_dock[-1],True,source_port['element_key']),
                                           (dock[-1],dock[0],False,port['element_key'])):
                if g.distance(begin,end)<1e-4:continue
                import hashlib
                eid='approach:'+hashlib.sha256(repr((begin,end,owner)).encode()).hexdigest()[:18]
                nav.edges[eid]={'edge_id':eid,'points_mm':[begin,end],'length_mm':g.distance(begin,end),
                    'region_id':'connector:'+owner,'portal_id':None,'width_mm':2*radius,'classification':'FREE','portal':None,'connection_stub':True}
                oriented[eid]=[begin,end]
                if prepend:points.insert(0,begin);edge_ids.insert(0,eid)
                else:points.append(end);edge_ids.append(eid)
            if not edge_ids:continue
            signature=tuple(edge_ids)
            if any(tuple(c['edge_ids'])==signature for c in candidates[tid]):continue
            used=[portal_by_id[k] for k in selected_portal_ids];conditional=any(v['classification']=='CONDITIONAL' and v['id'] not in cfg['constraints']['approved_passages'] for v in used)
            length=sum(nav.edges[e]['length_mm'] for e in edge_ids)
            candidate={'terminal_id':tid,'network_type':network,'manifold_id':equipment[network]['id'],
                'endpoint':ep,'destination':port,'source_port_hint':source_port,'compatible_source_ports':compatible,
                'edge_ids':edge_ids,'oriented_edges':oriented,'points_mm':points,'portal_ids':selected_portal_ids,
                'region_ids':[space.regions[i].data['id'] for i in paths[tier]['region_indices']],
                'status':'CONDITIONAL' if conditional else 'TOPOLOGICALLY_REACHABLE','approach_validation':'GEOMETRIC_APPROACH_PENDING',
                'length_mm':length,'cost':{'total':length/1000+sum(v.get('equivalent_length_m',0) for v in used)},
                'physical_axes_calculated':False,'reference_variant':variant}
            candidates[tid].append(candidate)
        if topology_budget:break
    invalid_manual=_manual_candidates(p,candidates,reports,destinations,roots,nav,portal_by_id,diagnostics)
    _apply_scoped_constraints(p,candidates)
    resources=_resources(candidates,nav.edges,equipment,profile,cfg['clearance_mm'])
    activation={eid:max(.001,e['length_mm']/1000) for eid,e in nav.edges.items() if not e.get('connection_stub')}
    options_found=[];excluded=[];solver_reports=[];seen=[]
    for attempt in range(6):
        if time.monotonic()>=deadline or cancelled and cancelled():break
        chosen,stats=solve(candidates,list(candidates),lambda *args:False,deadline,cancelled,
            resources=resources,activation_costs=activation,excluded_selections=excluded)
        solver_reports.append(stats)
        if chosen is None:raise RuntimeError('El optimizador HiGHS no está disponible o no produjo una solución.')
        if not chosen:break
        excluded.append(list(chosen.items()));coverage={e for tid,i in chosen.items() for e in candidates[tid][i]['edge_ids']}
        if seen and any(len(coverage.symmetric_difference(old))/max(1,len(coverage|old))<.20 for old in seen):continue
        seen.append(coverage);options_found.append({'selection':chosen,'selected_paths':{tid:candidates[tid][i] for tid,i in chosen.items()},
            'connected':len(chosen),'estimated_tube_length_mm':sum(candidates[tid][i]['length_mm'] for tid,i in chosen.items()),'solver':stats})
        if len(options_found)>=3:break
    selected=options_found[0]['selection'] if options_found else {}
    for tid,report in diagnostics.items():
        report['candidate_count']=len(candidates[tid])
        if tid in selected:report['status']='CONDITIONAL_PASSAGE_REQUIRED' if candidates[tid][selected[tid]]['status']=='CONDITIONAL' else 'TOPOLOGICALLY_REACHABLE'
        elif candidates[tid]:report['status']='CORRIDOR_CAPACITY_CONFLICT'
        elif report['status']=='GEOMETRIC_APPROACH_PENDING':report['status']='SEARCH_BUDGET_EXHAUSTED' if topology_budget else 'NO_CANDIDATE_FOUND'
    result=copy.deepcopy(p);result.configuration=cfg;result.equipment=equipment;result.endpoints=reports;result.connectivity=diagnostics
    built=schematic.build(selected,candidates,nav.edges,p.terminals)
    manual={k:c for k,c in p.corridors.items() if c.get('kind')=='MANUAL'}
    for cid,c in p.corridors.items():
        if invalid_manual.intersection(c.get('reachable_terminal_ids',[])):
            guide=copy.deepcopy(c);guide.update(kind='MANUAL',entity_type='MANUAL_GUIDE',generated_by='USER_SCHEMATIC_GUIDE',validation_status='MANUAL_SCHEMATIC_INVALID')
            manual[cid]=guide
    result.corridors=dict(built['corridors'],**manual);result.branches=built['branches'];result.schematic_junctions=built['junctions'];result.corridor_assignments=built['assignments']
    result.alternatives=options_found;result.design_stage='CORRIDORS'
    result.profiles={profile.profile_id:__import__('dataclasses').asdict(profile)}
    conditional=sum(d['status']=='CONDITIONAL_PASSAGE_REQUIRED' for d in diagnostics.values())
    result.validation={'stage':'CORRIDORS','schematic_status':'SCHEMATIC_INCOMPLETE' if len(selected)<len(reports) else 'SCHEMATIC_CONDITIONAL' if conditional else 'SCHEMATIC_COMPLETE',
        'identified_connectors':sum(bool(r['endpoints']) for r in reports.values()),'reachable_terminals':len(selected),
        'conditional_terminals':conditional,'pending_terminals':len(reports)-len(selected),'native_status':'NOT_EVALUATED'}
    result.selected_paths={tid:copy.deepcopy(candidates[tid][i]) for tid,i in selected.items()}
    result.solver_state={'strategy':'GLOBAL_RADIAL_V3','selected_paths':{tid:candidates[tid][i] for tid,i in selected.items()},
        'candidates':candidates,'solver_reports':solver_reports,'stage':'CORRIDORS','axes_calculated':False}
    result.corridor_graph={'edges':nav.edges,'nodes':{},'portals':passages,'internal_only':True,
        'topology_regions':[{'index':i,'parent_region_id':space.regions[topology.parents[i]].data['id'],
            'bounds':list(r.bounds),'neighbors':sorted({other for other,_ in topology.edges[i]})} for i,r in enumerate(topology.regions)],
        'passage_coverage':copy.deepcopy(space.stats.get('passage_coverage',{})),
        'reference_failures':nav.failures,
        'stats':{'seconds':time.monotonic()-started,'selected_terminals':len(selected),'terminals':len(reports),
            'candidate_paths':sum(len(v) for v in candidates.values()),'enumeration_finished':not topology_budget,
            'cancelled':bool(cancelled and cancelled()),'graph_construction_completed':topology.complete},
        'physical_infeasibility_proven':False}
    result.dependencies={cid:list(c['route_ids']) for cid,c in result.corridors.items() if c.get('generated_by')=='GLOBAL_RADIAL_V3'}
    schematic.check(result)
    from .model import RoutingDesignProposal
    return RoutingDesignProposal.from_dict(result.to_dict())
