"""Operations dispatched by the installed MCP run_query/run_modify tools."""
import copy
import datetime
from . import service,settings

READ_OPERATIONS=('get_heat_recovery_equipment_assignments','plan_heat_recovery_topology','inspect_heat_recovery_previous','audit_heat_recovery_connections','analyze_mep_geometry','get_mep_system_profiles','get_mep_connection_points',
 'create_mep_route_proposal','get_mep_route_proposal','optimize_mep_routes','get_mep_route_alternatives',
 'set_mep_route_constraints','approve_mep_route','validate_mep_routes','select_mep_route_alternative')
WRITE_OPERATIONS=('prepare_terminal_adapters','set_heat_recovery_equipment_assignments','remove_heat_recovery_previous','model_heat_recovery_network','preview_mep_routes','hide_mep_routes','save_mep_route_proposal')


def dispatch(doc,operation,payload=None,allow_write=False,cancelled=None):
    payload=payload or {}
    from .design.operations import OPERATIONS as DESIGN_OPERATIONS,WRITE_OPERATIONS as DESIGN_WRITES
    if operation in DESIGN_OPERATIONS:
        if doc is None or doc.IsFamilyDocument:raise ValueError('Abra un documento de proyecto.')
        from .design.adapter import dispatch as design_dispatch
        if operation in DESIGN_WRITES and not allow_write:raise ValueError('Modelar conexiones requiere allow_write=True y confirmación explícita.')
        return design_dispatch(doc,operation,payload,allow_write=allow_write)
    if operation=='get_mep_system_profiles':return service.get_mep_system_profiles()
    if operation not in READ_OPERATIONS+WRITE_OPERATIONS:raise ValueError('Operación de trazado desconocida: '+operation)
    if operation in WRITE_OPERATIONS and not allow_write:raise ValueError('Esta operación requiere allow_write=True y una acción explícita.')
    if doc is None or doc.IsFamilyDocument:raise ValueError('Abra un documento de proyecto.')
    if operation=='prepare_terminal_adapters':
        from .revit.terminal_adapters import prepare
        return prepare(doc,settings.validate(payload.get('configuration',{})),payload.get('destinations',[]))
    if operation in ('get_heat_recovery_equipment_assignments','set_heat_recovery_equipment_assignments'):
        from .revit import equipment_selection
        if operation=='get_heat_recovery_equipment_assignments':return equipment_selection.read(doc)
        return equipment_selection.save(doc,payload.get('configuration',{}),payload.get('explicit',False))
    if operation in ('inspect_heat_recovery_previous','remove_heat_recovery_previous'):
        from .revit import previous_network
        if operation=='inspect_heat_recovery_previous':return previous_network.inspect(doc,payload.get('configuration',{}),payload.get('destinations'))
        return previous_network.remove(doc,payload['plan'],payload.get('confirmation_token'),payload.get('explicit',False))
    if operation in ('model_heat_recovery_network','audit_heat_recovery_connections'):
        from .revit import network_model
        if operation=='audit_heat_recovery_connections':return network_model.audit(doc,payload.get('proposal'))
        if not payload.get('proposal'):raise ValueError('Calcule y apruebe una propuesta primero.')
        return network_model.model(doc,payload['proposal'],payload.get('explicit',False),cancelled)
    from .revit import extractor,proposals
    if operation=='plan_heat_recovery_topology':
        from .systems.heat_recovery_topology import requests
        configuration=settings.validate(payload.get('configuration',{}))
        catalog=extractor.get_mep_connection_points(doc,configuration)
        routes,warnings,topology,overrides=requests(catalog,configuration,payload.get('destinations',[]))
        return {'requests':routes,'topology':topology,'warnings':warnings,'route_overrides':overrides}
    if operation=='get_mep_connection_points':return extractor.get_mep_connection_points(doc,settings.validate(payload.get('configuration',{})))
    if operation=='get_mep_route_proposal':return proposals.get_mep_route_proposal(doc,payload.get('proposal_id'))
    if operation=='hide_mep_routes':return proposals.hide_preview(doc,payload.get('proposal_id'),payload.get('explicit',False))
    proposal=payload.get('proposal')
    if proposal:service.check(proposal)
    configuration=settings.validate(payload.get('configuration',proposal['configuration'] if proposal else {}))
    snapshot=payload.get('snapshot')
    if operation in ('preview_mep_routes','save_mep_route_proposal'):
        if operation=='preview_mep_routes':return proposals.preview_mep_routes(doc,proposal,payload.get('explicit',False),payload.get('route_ids'),payload.get('alternative_index'))
        return proposals.save_mep_route_proposal(doc,proposal,payload.get('explicit',False))
    fresh=extractor.analyze_mep_geometry(doc,configuration,cancelled)
    if snapshot is not None and (snapshot['document_identity']!=fresh['document_identity'] or snapshot['geometry_revision']!=fresh['geometry_revision']):
        raise ValueError('OUTDATED: el snapshot no corresponde a la revisión actual.')
    snapshot=fresh
    if operation=='analyze_mep_geometry':
        return snapshot if payload.get('include_geometry',False) else {
            'document_identity':snapshot['document_identity'],'geometry_revision':snapshot['geometry_revision'],
            'regions':snapshot['regions'],'obstacles':[{k:v for k,v in o.items() if k!='meshes'} for o in snapshot['obstacles']],
            'warnings':snapshot['warnings'],'stats':snapshot['stats'],'units':'mm',
            'note':'Use include_geometry=True para recuperar el snapshot serializable completo.'}
    if operation in ('create_mep_route_proposal','optimize_mep_routes'):
        catalog=extractor.get_mep_connection_points(doc,configuration)
        if configuration['native_validation']:
            from .native_feedback import design
            return design(snapshot,catalog,payload.get('origins',proposal['origins'] if proposal else []),
                payload.get('destinations',proposal['destinations'] if proposal else []),configuration,proposal,
                lambda p:proposals.validate_mep_routes(doc,snapshot,p),cancelled,payload.get('route_ids'))
        result=service.create_mep_route_proposal(snapshot,catalog,payload.get('origins',proposal['origins'] if proposal else []),
                    payload.get('destinations',proposal['destinations'] if proposal else []),configuration,proposal,cancelled,route_ids=payload.get('route_ids'))
        if configuration['native_validation']:return proposals.validate_mep_routes(doc,snapshot,result)
        for r in result['routes']:r['collision_status']='NOT_EVALUATED'
        result['summary']=service.summarize(result['routes'])
        return service.seal(result)
    if proposal is None:raise ValueError('Esta operación requiere el JSON de una propuesta.')
    service.assert_current(snapshot,proposal)
    if operation=='validate_mep_routes':return proposals.validate_mep_routes(doc,snapshot,proposal)
    if operation=='set_mep_route_constraints':
        new_settings=settings.merge(configuration,{'constraints':payload['constraints']})
        new_snapshot=extractor.analyze_mep_geometry(doc,new_settings,cancelled)
        catalog=extractor.get_mep_connection_points(doc,new_settings)
        result=service.create_mep_route_proposal(new_snapshot,catalog,proposal['origins'],proposal['destinations'],new_settings,proposal,cancelled)
        return proposals.validate_mep_routes(doc,new_snapshot,result)
    if operation=='get_mep_route_alternatives':
        options=service.get_mep_route_alternatives(snapshot,proposal,payload['route_id'],cancelled)
        result=copy.deepcopy(proposal);route=next(r for r in result['routes'] if r['route_id']==payload['route_id'])
        checked=[]
        for option in options:
            test=copy.deepcopy(proposal);target=next(r for r in test['routes'] if r['route_id']==payload['route_id'])
            for key in ('reason','errors','attempts'):target.pop(key,None)
            target.update(option);target['collision_status']=option['status']
            validated=proposals.validate_mep_routes(doc,snapshot,service.seal(test))
            alternative=next(r for r in validated['routes'] if r['route_id']==payload['route_id'])
            alternative.pop('alternatives',None);checked.append(alternative)
        route['alternatives']=checked
        return service.seal(result)
    if operation=='select_mep_route_alternative':
        result=copy.deepcopy(proposal);route=next(r for r in result['routes'] if r['route_id']==payload['route_id'])
        options=route.get('alternatives',[]);index=payload['alternative_index']
        if type(index)!=int or not 0<=index<len(options):raise ValueError('Alternativa inexistente.')
        route.update(copy.deepcopy(options[index]));route['alternatives']=options;route['approval_status']='DRAFT'
        result['status']='DRAFT'
        return proposals.validate_mep_routes(doc,snapshot,service.seal(result))
    if operation=='approve_mep_route':
        if not payload.get('explicit'):raise ValueError('Aprobar requiere explicit=True tras revisar la ruta.')
        result=proposals.validate_mep_routes(doc,snapshot,proposal)
        route=next(r for r in result['routes'] if r['route_id']==payload['route_id'])
        if route['native_status'] in ('INVALID','NOT_EVALUATED'):raise ValueError('La ruta no supera la validación geométrica real.')
        pending=[p for p in route.get('passages',[]) if p['classification']=='CONDITIONAL' and p['id'] not in configuration['constraints']['approved_passages']]
        if pending:
            technical=payload.get('technical_approval',{})
            if not technical.get('reference') or not technical.get('responsible'):
                raise ValueError('Un paso condicionado necesita referencia de validación técnica y responsable; no se autoriza por caber geométricamente.')
            route['technical_approval']=dict(technical,passages=[p['id'] for p in pending])
            result['configuration']['constraints']['approved_passages']=sorted(set(configuration['constraints']['approved_passages'])|{p['id'] for p in pending})
            for p in pending:result['configuration']['constraints']['passage_approvals'][p['id']]=technical
            route['collision_status']='VALID_WITH_WARNINGS';route['native_status']='VALID_WITH_WARNINGS'
        route['approval_status']='APPROVED';route['approved_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        result['status']='APPROVED' if all(r['approval_status']=='APPROVED' for r in result['routes']) else 'DRAFT'
        result['summary']=service.summarize(result['routes'])
        return service.seal(result)
