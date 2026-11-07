"""Reviewable stage-3 plan bound to an exact proposal and geometry revision."""
import hashlib
import json
from . import schematic


def prepare(p):
    blockers=[];routes=list(p.individual_routes.values())+list(p.main_connections.values())
    if p.design_stage!='AXES':blockers.append({'code':'AXES_REQUIRED'})
    if len(p.selected_paths)!=len(p.terminals):blockers.append({'code':'SCHEMATIC_INCOMPLETE','pending_terminal_ids':sorted(set(p.terminals)-set(p.selected_paths))})
    if p.selected_paths:schematic.check(p)
    if not p.validation.get('coordination'):blockers.append({'code':'JOINT_VALIDATION_REQUIRED'})
    if set(p.main_connections)!={'main:SUPPLY','main:EXTRACT'}:blockers.append({'code':'MAIN_CONNECTIONS_REQUIRED'})
    expected={'axis:'+tid for tid in p.selected_paths}
    if set(p.individual_routes)!=expected:blockers.append({'code':'BRANCH_AXES_INCOMPLETE','missing_route_ids':sorted(expected-set(p.individual_routes))})
    native_catalog=p.catalog.get('configured_model',{})
    rigid_types=native_catalog.get('rigid_main_types',[])
    requested=p.configuration.get('main_connection',{}).get('type_id')
    if len([t for t in rigid_types if not requested or t['element_id']==requested])!=1:blockers.append({'code':'RIGID_MAIN_TYPE_REQUIRED'})
    for network,classification in (('SUPPLY','SupplyAir'),('EXTRACT','ExhaustAir')):
        requested_system=p.configuration.get('duct_system_type_ids',{}).get(network)
        choices=[t for t in native_catalog.get('mechanical_system_types',[]) if t['classification']==classification and (not requested_system or t['element_id']==requested_system)]
        if len(choices)!=1:blockers.append({'code':'NATIVE_SYSTEM_TYPE_REQUIRED','network_type':network})
    used=set();movements=[]
    for route in routes:
        rid=route['route_id']
        if route.get('profile',{}).get('insulation_thickness_mm',0)>0:blockers.append({'code':'NATIVE_INSULATION_IMPLEMENTATION_REQUIRED','route_id':rid})
        if route.get('validation_status')!='GEOMETRICALLY_VALID':blockers.append({'code':route.get('validation_status','AXIS_NOT_EVALUATED'),'route_id':rid});continue
        for key in ('source','destination'):
            port=route[key]
            if port.get('connected'):blockers.append({'code':'CONNECTOR_OCCUPIED','route_id':rid,'endpoint':key})
            if port['id'] in used:blockers.append({'code':'CONNECTOR_ASSIGNED_TWICE','route_id':rid,'endpoint':key})
            used.add(port['id'])
        for passage in route.get('passages',[]):
            if passage['classification']=='CONDITIONAL' and passage['id'] not in p.configuration['constraints']['approved_passages']:
                blockers.append({'code':'PASSAGE_APPROVAL_REQUIRED','route_id':rid,'portal_id':passage['id']})
    for network,equipment in p.equipment.items():
        if equipment.get('original_transform')!=equipment.get('proposed_transform'):
            device=next((d for d in p.catalog.get('devices',[]) if d['id']==equipment['id']),None)
            if not device or any(port.get('connected') for port in device['connectors']):blockers.append({'code':'VIRTUAL_MOVE_HAS_EXISTING_CONNECTIONS','network_type':network})
            movements.append({'element_id':equipment['element_id'],'network_type':network,
                'original_transform':equipment.get('original_transform'),'proposed_transform':equipment.get('proposed_transform')})
    payload={'proposal_id':p.proposal_id,'proposal_revision':p.revision,'document_identity':p.document_identity,
        'geometry_revision':p.geometry_revision,'routes':routes,'equipment_movements':movements,
        'configuration':p.configuration,'configuration_hash':hashlib.sha256(json.dumps(p.configuration,sort_keys=True,allow_nan=False).encode()).hexdigest()}
    token=hashlib.sha256(json.dumps(payload,sort_keys=True,allow_nan=False).encode()).hexdigest()
    return dict(payload,approval_token=token,ready=not blockers,blockers=blockers,
        summary={'flexible_branches':sum(r.get('route_kind','BRANCH')=='BRANCH' for r in routes),
            'rigid_mains':sum(r.get('route_kind')=='MAIN' for r in routes),'equipment_movements':len(movements),
            'planned_penetrations':len({q['id'] for r in routes for q in r.get('passages',[])}),
            'native_validation':'TEMPORARY_CREATION_AND_AUDIT_REQUIRED'},
        native_status='NOT_EVALUATED',hydraulic_status='NOT_EVALUATED')


def authorize(p,token,explicit):
    plan=prepare(p)
    if not explicit:raise ValueError('Modelar conexiones requiere confirmación explícita de la revisión.')
    if token!=plan['approval_token']:raise ValueError('STALE_APPROVAL: la propuesta ha cambiado.')
    if not plan['ready']:raise ValueError('MODEL_PLAN_BLOCKED: '+json.dumps(plan['blockers'],ensure_ascii=False))
    return plan
