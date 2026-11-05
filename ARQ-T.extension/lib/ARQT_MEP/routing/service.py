"""Shared pure proposal service, serializable and callable without Revit."""
import copy
import datetime
import hashlib
import json
import math
import time
from . import settings
from .systems.ventilation_radial import VentilationRadialSystem
from .core.spatial_model import build_spatial_model
from .core.multi_route import route_alternatives,compact_anchor
from .core import geometry as g
from .core.passages import generate_passages


def seal(proposal):
    proposal=dict(proposal);proposal.pop('content_hash',None)
    proposal['content_hash']=hashlib.sha256(json.dumps(proposal,sort_keys=True,allow_nan=False,ensure_ascii=False).encode()).hexdigest()
    return proposal


def check(proposal):
    if seal(proposal)['content_hash']!=proposal.get('content_hash'):raise ValueError('Propuesta alterada o incompleta.')
    return proposal


def get_mep_system_profiles():
    return [{'system_id':'ventilation_radial','name':'Ventilación radial con recuperador','available':True,
             'strategy':'RADIAL','defaults':copy.deepcopy(settings.DEFAULT),
             'capabilities':{'geometric_routing':True,'native_solid_validation':True,
                             'automatic_hydraulic_sizing':False,'parallel_duct_sizing':False,
                             'automatic_velocity_sizing':True,'automatic_transition_creation':True,
                             'automatic_distributor_placement':False,'native_connected_network_creation':True,
                             'native_creation_requires_approved_routes':True}}]


def create_mep_route_proposal(snapshot,catalog,origins,destinations,configuration=None,existing=None,cancelled=None,route_ids=None):
    started=time.monotonic();configuration=settings.validate(configuration or {})
    native_sizes=catalog.get('configured_model',{}).get('flexible_duct',{}).get('sizing_diameters_mm')
    if native_sizes:
        configuration['flexible_duct']['available_diameters_mm']=list(native_sizes)
        configuration['flexible_duct']['diameter_source']='REVIT_ROUND_DUCT_SIZE_SETTINGS'
        configuration['main_connection']['automatic_diameter_change']=True
    topology=None
    requested_overrides=copy.deepcopy(configuration.get('route_overrides',{}))
    if 'equipment_assignments' in configuration:
        from .systems.heat_recovery_topology import requests as topology_requests
        requests,warnings,topology,overrides=topology_requests(catalog,configuration,destinations)
        for request in requests:
            rid=request['route_id']
            if route_ids is None or rid not in route_ids or rid not in requested_overrides:continue
            overrides[rid]=settings.merge(overrides.get(rid,{}),requested_overrides[rid])
            section=overrides[rid].get('section',{})
            diameter=section.get('nominal_diameter_mm')
            if diameter and native_sizes:
                if not any(abs(diameter-d)<.01 for d in native_sizes):
                    request['error']='DIAMETER_NOT_AVAILABLE';continue
                request['sizing']=dict(request.get('sizing') or {},diameter_mm=diameter,
                    velocity_mps=request['airflow_lps']/1000/(math.pi*(diameter/1000)**2/4),method='MANUAL_ROUTE_SECTION')
                request['required_transitions']=[{'endpoint':end,'from_mm':port['size']['diameter_mm'],'to_mm':diameter}
                    for end,port in (('source',request.get('source')),('destination',request['destination']))
                    if port and abs(port['size'].get('diameter_mm',diameter)-diameter)>.01]
        configuration=settings.merge(configuration,{'route_overrides':overrides})
        if any(r.get('compact_connections') for r in requests):
            warnings.append('Conexiones ajustadas al puerto, sin reserva fija de 300 mm. La geometría real de las transiciones y extremos se comprueba después de modelar; los conflictos se informan por ruta y elemento.')
    else:
        system=VentilationRadialSystem(catalog,configuration)
        requests,warnings=system.requests(origins,destinations)
    space=build_spatial_model(snapshot,configuration['constraints'])
    profile=settings.MEPSystemProfile(configuration['section'],configuration['clearance_mm'],configuration['bend_radius_mm'],configuration['weights'])
    absolute_deadline=started+configuration['search']['max_total_seconds']
    # Completed generated stations are geometry-derived data, not work to be
    # repeated with a shorter timeout on each continuation. Never cache a
    # partial enumeration as complete or reuse it across physical changes.
    from .core.candidate_repository import context_signature
    def passage_context(config,reqs):
        config=copy.deepcopy(config)
        config['constraints']['passages']=[p for p in config['constraints']['passages']
            if p.get('generated_by') not in ('WALL_CANDIDATES_V1','SLAB_CANDIDATES_V1')]
        return context_signature(space,reqs,config)
    previous_queries=(existing or {}).get('stats',{}).get('spatial_queries',{})
    coverage=previous_queries.get('passage_coverage',{})
    reuse_passages=bool(existing and coverage and all(v.get('complete') for v in coverage.values())
        and not previous_queries.get('passage_generation_incomplete')
        and existing.get('document_identity')==snapshot['document_identity']
        and existing.get('geometry_revision')==snapshot['geometry_revision']
        and passage_context(configuration,requests)==passage_context(existing['configuration'],existing['requests']))
    if reuse_passages:
        configuration['constraints']['passages']=copy.deepcopy(existing['configuration']['constraints']['passages'])
        space.stats['passage_coverage']=copy.deepcopy(coverage)
        space.stats['passage_generation_reused']=True
    else:
        configuration['constraints']['passages']=generate_passages(space,requests,configuration,
            deadline=min(absolute_deadline,started+configuration['search']['max_total_seconds']*.15),cancelled=cancelled)
        space.stats['passage_generation_reused']=False
    space.constraints=configuration['constraints']
    active=[r for r in requests if not r.get('error')]
    absolute_deadline=started+configuration['search']['max_total_seconds']
    if route_ids is not None:
        if not route_ids or set(route_ids)-{r['route_id'] for r in requests}:raise ValueError('Seleccione una ruta existente para recalcular.')
    existing_by_id={r['route_id']:r for r in (existing or {}).get('routes',[])}
    for request in active:
        rid=request['route_id'];prior=existing_by_id.get(rid)
        if not prior:continue
        if rid in configuration['constraints']['locked_routes'] or (route_ids is not None and rid not in route_ids):
            matching=next((p for p in [request['source']]+request.get('source_alternatives',[]) if p==prior.get('source')),None)
            if matching:
                ports={p['id']:p for p in [request['source']]+request.get('source_alternatives',[])}
                request['source']=copy.deepcopy(matching)
                request['source_alternatives']=[p for key,p in ports.items() if key!=matching['id']]
    from .core.global_planner import solve
    optimized=solve(space,active,profile,configuration,existing_by_id,absolute_deadline,cancelled,target_route_ids=route_ids,
        exploration_state=(existing or {}).get('exploration_state'),candidate_bank=(existing or {}).get('candidate_bank'),
        planner_state=(existing or {}).get('planner_state')) if active else {'routes':{},'iterations':[]}
    if optimized.get('ignored_locks'):warnings.append('Se han descartado bloqueos de rutas sin geometría: no existe un recorrido que conservar.')
    routes=[]
    for request in requests:
        local=settings.for_route(configuration,request['route_id'])
        core=optimized['routes'].get(request['route_id'],{'status':'NOT_EVALUATED','reason':request.get('error','NOT_PLANNED')})
        if core.get('chosen_destination'):
            from .core.terminal_adapter import allowed_destination
            if not allowed_destination(core['chosen_destination'],request):raise ValueError('Salida de adaptador no autorizada.')
            choices=[request['destination']]+request.get('destination_alternatives',[])
            request['destination']=copy.deepcopy(core['chosen_destination'])
            request['destination_alternatives']=[p for p in choices if p['id']!=request['destination']['id']]
        if core.get('chosen_source'):
            allowed=[request['source']]+request.get('source_alternatives',[])
            if core['chosen_source'] not in allowed:raise ValueError('Toma alternativa ajena al distribuidor o no equivalente.')
            request['source']=copy.deepcopy(core['chosen_source'])
            request['source_alternatives']=[copy.deepcopy(p) for p in {p['id']:p for p in allowed}.values() if p['id']!=request['source']['id']]
            if request.get('sizing'):
                diameter=request['sizing']['diameter_mm']
                request['required_transitions']=[{'endpoint':end,'from_mm':request[end]['size']['diameter_mm'],'to_mm':diameter}
                    for end in ('source','destination') if abs(request[end]['size']['diameter_mm']-diameter)>.01]
        if core.get('reason')=='NO_FREE_CONNECTION_ANCHOR' and request.get('compact_connections'):
            local_profile=settings.profile(local)
            anchors=[compact_anchor(space,request[end],local_profile,configuration) for end in ('source','destination')]
            core['connection_precheck']={'scope':'BULK_ANCHORS_ONLY','fittings_pending':True,
                'source_anchor_mm':anchors[0][1] if anchors[0] else None,
                'destination_anchor_mm':anchors[1][1] if anchors[1] else None}
            if all(anchors):core.update(status='NOT_EVALUATED',reason='NETWORK_CONFLICT_NOT_RESOLVED')
        route=dict(core,route_id=request['route_id'],network_type=request['network_type'],
                   design_diagnostics=((optimized.get('global_plan') or {}).get('routes') or {}).get(request['route_id']),
                   route_kind=request.get('route_kind','BRANCH'),source_id=request['source']['id'] if request['source'] else None,destination_id=request['destination']['id'],
                   source=request['source'],destination=request['destination'],airflow_lps=request['airflow_lps'],
                   sizing=request.get('sizing'),required_transitions=request.get('required_transitions',[]),
                   section=local['section'],clearance_mm=local['clearance_mm'],bend_radius_mm=local['bend_radius_mm'],
                   approval_status='DRAFT',collision_status=core['status'],structural_crossings=core.get('passages',[]),
                   xyz_points=core.get('points_mm',[]),units='mm',alternatives=[],native_status='NOT_EVALUATED')
        if route['collision_status'] in ('VALID','VALID_WITH_WARNINGS','CONDITIONAL'):
            from .core.natural_path import build as natural_path
            try:route['natural_path']=natural_path(route['points_mm'],route['bend_radius_mm'])
            except ValueError as exc:route['natural_path']={'status':'NOT_CONSTRUCTIBLE','reason':str(exc)}
            from .core.component_envelopes import describe
            route['components']=describe(route,configuration,catalog.get('configured_model',{}))
            route['component_geometry_complete']=bool(route['components']) and all(
                c['geometry_verification_status']=='CENTRAL_ENVELOPE_CHECKED' for c in route['components'])
            from .pressure import assess
            route['pressure_assessment']=assess(route,request,configuration)
        prior=existing_by_id.get(request['route_id'])
        if route_ids is not None and request['route_id'] not in route_ids and prior:
            unchanged=all(prior.get(k)==route.get(k) for k in ('source','destination','section','clearance_mm','bend_radius_mm','points_mm'))
            if unchanged and route['collision_status'] not in ('INVALID','NOT_EVALUATED'):route['approval_status']=prior.get('approval_status','DRAFT')
        from .core.routing_diagnostics import build as diagnose
        route['routing_diagnostics']=diagnose(route,request,optimized.get('global_plan'),bool(space.stats.get('passage_generation_incomplete')))
        routes.append(route)
    body={'version':1,'document_identity':snapshot['document_identity'],'geometry_revision':snapshot['geometry_revision'],
          'exploration_state':optimized.get('exploration_state',{}),
          'candidate_bank':optimized.get('candidate_bank',{}),
          'planner_state':optimized.get('planner_state',{}),
          'candidate_audit':optimized.get('candidate_audit',[]),
          'continuation':optimized.get('continuation',{}),
          'system_id':configuration['system_id'],'configuration':configuration,'origins':origins,'destinations':destinations,
          'requests':requests,'routes':routes,'topology':topology,'warnings':snapshot.get('warnings',[])+catalog.get('warnings',[])+warnings,
          'configured_model':copy.deepcopy(catalog.get('configured_model',{})),
          'capabilities':get_mep_system_profiles()[0]['capabilities'],
          'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'DRAFT',
          'stats':dict(snapshot.get('stats',{}),optimization=optimized.get('iterations'),total_seconds=time.monotonic()-started,
                       optimization_seconds=optimized.get('seconds',0),spatial_queries=space.stats,
                       parallel=optimized.get('parallel'),global_plan=optimized.get('global_plan'),
                       coordination=optimized.get('coordination',[]),portfolio=optimized.get('portfolio'),routing_objective=optimized.get('objective'),
                       automatic_iterations=len(optimized.get('iterations',[]))),
          'coordinate_system':snapshot.get('coordinate_system','mm fixture')}
    identity={'doc':body['document_identity'],'revision':body['geometry_revision'],'settings':configuration,'origins':origins,'destinations':destinations}
    body['proposal_id']=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
    body['summary']=summarize(routes)
    return seal(body)


def summarize(routes):
    return {'route_count':len(routes),'valid':sum(r.get('collision_status') in ('VALID','VALID_WITH_WARNINGS') for r in routes),
            'conditional':sum(r.get('collision_status')=='CONDITIONAL' for r in routes),
            'unresolved':sum(r.get('collision_status') in ('INVALID','NOT_EVALUATED') for r in routes),
            'total_length_m':sum(r.get('length_m',0) for r in routes),'bends':sum(r.get('bends',0) for r in routes),
            'structural_crossings':sum(sum(p['classification']=='CONDITIONAL' for p in r.get('passages',[])) for r in routes),
            'cost':sum(r.get('cost',{}).get('total',0) for r in routes)}


def get_mep_route_alternatives(snapshot,proposal,route_id,cancelled=None):
    check(proposal);configuration=proposal['configuration'];space=build_spatial_model(snapshot,configuration['constraints'])
    profile=settings.MEPSystemProfile(configuration['section'],configuration['clearance_mm'],configuration['bend_radius_mm'],configuration['weights'])
    for r in proposal['routes']:
        if r['route_id']!=route_id and r.get('collision_status') not in ('INVALID','NOT_EVALUATED'):
            space.reserve(r['route_id'],r.get('sampled_points_mm',r.get('points_mm',[])),settings.profile(settings.for_route(configuration,r['route_id'])).radius_mm)
    request=next(r for r in proposal['requests'] if r['route_id']==route_id)
    current=next(r for r in proposal['routes'] if r['route_id']==route_id)
    if request.get('error'):return []
    return route_alternatives(space,request,profile,configuration,current,cancelled)


def assert_current(snapshot,proposal):
    check(proposal)
    if snapshot['document_identity']!=proposal['document_identity'] or snapshot['geometry_revision']!=proposal['geometry_revision']:
        raise ValueError('OUTDATED: la geometría o los puntos de conexión han cambiado; vuelva a analizar.')


def set_mep_route_constraints(snapshot,catalog,proposal,constraints,cancelled=None):
    assert_current(snapshot,proposal)
    configuration=settings.merge(proposal['configuration'],{'constraints':constraints})
    return create_mep_route_proposal(snapshot,catalog,proposal['origins'],proposal['destinations'],configuration,proposal,cancelled)
