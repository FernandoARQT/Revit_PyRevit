"""Callable from existing MCP run_query/run_modify; does not start a server."""
from . import service,config

READ_OPERATIONS=('analyze_ventilation_rooms','calculate_ventilation_flows',
                 'propose_ventilation_terminals','get_ventilation_proposal','validate_ventilation_terminals')
WRITE_OPERATIONS=('place_ventilation_terminals','update_ventilation_terminals','set_terminal_lock')
EDITOR_READ_OPERATIONS=('get_ventilation_terminals','get_terminal_details','get_ventilation_validation',
 'list_ventilation_proposals','create_ventilation_proposal','get_rebalance_plan','export_ventilation_preview','refresh_ventilation_proposal')


def dispatch(doc,operation,payload=None,allow_write=False):
    # Additive routing bridge; terminal-distribution operations keep their
    # original dispatch body and contracts below.
    if operation.startswith(('routing_design_','get_mep_','analyze_mep_','create_mep_','optimize_mep_',
            'set_mep_','approve_mep_','validate_mep_','select_mep_','preview_mep_','hide_mep_','save_mep_',
            'get_heat_recovery_','set_heat_recovery_','plan_heat_recovery_','inspect_heat_recovery_',
            'audit_heat_recovery_','remove_heat_recovery_','model_heat_recovery_','prepare_terminal_adapters')):
        from .routing.mcp import dispatch as routing_dispatch
        return routing_dispatch(doc,operation,payload,allow_write=allow_write)
    payload=payload or {}
    if doc is None or doc.IsFamilyDocument:
        raise ValueError('Abra un documento de proyecto.')
    from . import proposal as model,proposal_store as store,ownership
    if operation in model.MUTATIONS or operation in EDITOR_READ_OPERATIONS or operation=='commit_ventilation_proposal' or operation=='get_ventilation_proposal' and payload.get('proposal_id'):
        from . import proposal_adapter as adapter
        document_id=ownership.document_id(doc)
        if operation=='create_ventilation_proposal':return adapter.create(doc,payload['room_uids'],payload.get('settings'),payload.get('scope'),payload.get('housing_id','Vivienda 1'))
        if operation=='list_ventilation_proposals':return store.list_proposals(document_id,payload.get('housing_id'))
        pid=payload['proposal_id'];p=store.get(document_id,pid)
        if operation=='get_ventilation_proposal':return p
        if operation=='refresh_ventilation_proposal':return adapter.refresh(doc,pid,payload['expected_version'])
        if operation=='get_ventilation_terminals':return model.active(p)
        if operation=='get_terminal_details':
            t=model.terminal(p,payload['terminal_id']);return dict(t,room_flow=model.row(p,t['room_uid'],t['role']),proposal_version=p['version'])
        if operation=='get_rebalance_plan':return model.rebalance_plan(p,payload.get('include_manual',False))
        if operation=='get_ventilation_validation':
            validation=model.refresh(p)
            try:adapter.current(doc,p)
            except ValueError as exc:validation=dict(validation,valid=False,status='NO_VALIDO',outdated=True,errors=validation['errors']+[str(exc)])
            return validation
        if operation=='export_ventilation_preview':
            from .preview_image import export
            return export(p,payload.get('level_id'),payload.get('path'),payload.get('width',1600),payload.get('height',1100))
        if operation=='approve_ventilation_proposal':return adapter.approve(doc,pid,payload['expected_version'],payload.get('confirmed',False))
        if operation=='commit_ventilation_proposal':
            if not allow_write:raise ValueError('COMMIT requiere allow_write=True y run_modify.')
            return adapter.commit(doc,pid,payload['expected_version'],payload.get('approved',False),payload.get('delete_obsolete',False))
        return store.update(document_id,pid,payload['expected_version'],operation,payload)
    if operation in WRITE_OPERATIONS:
        if not allow_write:
            raise ValueError('Las operaciones de escritura requieren allow_write=True y run_modify.')
        from . import revit_write
        if operation=='set_terminal_lock':
            return revit_write.set_terminal_lock(doc,payload['element_id'],payload['locked'],payload.get('approved',False))
        return revit_write.create_ventilation_terminals(doc,payload['proposal'],payload.get('approved',False),
                payload.get('choices'),payload.get('delete_obsolete',False))
    if operation not in READ_OPERATIONS:
        raise ValueError('Operación desconocida: '+str(operation))
    if operation=='get_ventilation_proposal':
        return service.get_ventilation_proposal(payload['proposal'])
    if operation=='validate_ventilation_terminals':
        from .revit_write import validate_ventilation_terminals
        return validate_ventilation_terminals(doc,payload.get('room_uids'),payload.get('tolerance_mm',1))
    settings=config.validate(payload.get('settings',config.DEFAULT))
    if operation=='analyze_ventilation_rooms':
        return service.analyze_ventilation_rooms(doc,settings)
    if operation=='calculate_ventilation_flows':
        analysis=service.analyze_ventilation_rooms(doc,settings)
        return service.calculate_ventilation_flows(analysis,payload['room_uids'],settings)
    return service.propose_ventilation_terminals(doc,payload['room_uids'],settings,payload.get('scope'))
