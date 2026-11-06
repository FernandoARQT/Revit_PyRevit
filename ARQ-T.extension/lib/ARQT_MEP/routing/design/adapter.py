"""Revit boundary; draft edits remain entirely outside the RVT."""
from . import model,store,operations


def dispatch(doc,operation,payload=None):
    from ... import ownership,revit_read,service,config
    from .. import settings
    from ..revit import extractor,equipment_selection
    payload=payload or {};document_id=ownership.document_id(doc)
    if operation=='routing_design_create':
        if payload.get('source_proposal_id'):
            from ... import proposal_store as terminals,proposal_adapter
            background=terminals.get(document_id,payload['source_proposal_id'])
            if type(payload.get('source_version'))!=int or background['version']!=payload['source_version']:raise ValueError('VERSION_CONFLICT: la distribución ha cambiado.')
            proposal_adapter.current(doc,background)
            return store.create(model.create(background,payload.get('name','Esquema de conductos'))).to_dict()
        from .background import from_catalog
        configuration=settings.validate(payload.get('configuration',equipment_selection.read(doc)))
        analysis_settings=dict(config.DEFAULT)
        if configuration.get('phase_id') is not None:analysis_settings['phase_id']=configuration['phase_id']
        analysis=service.analyze_ventilation_rooms(doc,analysis_settings)
        view=doc.ActiveView
        if view is None or str(view.ViewType) not in ('FloorPlan','CeilingPlan','EngineeringPlan','AreaPlan'):analysis['view_frame']={}
        catalog=extractor.get_mep_connection_points(doc,configuration)
        background=from_catalog(analysis,catalog,payload.get('destinations',[]),document_id,revit_read.fingerprint(doc),payload.get('room_uids'))
        p=model.create(background,payload.get('name','Estudio '+str(len(store.entries(document_id))+1)));p.configuration=configuration;p.catalog=catalog
        if payload.get('analyze'):
            from .spatial import envelope
            from dataclasses import asdict
            profile=model.DuctProfile(**payload['duct_profile']);clearance=float(payload.get('clearance_mm',10));envelope(profile,clearance)
            configuration['design_clearance_mm']=clearance
            snapshot=extractor.analyze_mep_geometry(doc,configuration)
            p.snapshot=snapshot;p.geometry_revision=snapshot['geometry_revision'];p.configuration=configuration
            p.profiles={profile.profile_id:asdict(profile)}
        return store.create(p).to_dict()
    if operation=='routing_design_analyze':
        p=store.get(document_id,payload['proposal_id'])
        if p.revision!=payload['expected_revision']:raise ValueError('VERSION_CONFLICT')
        if revit_read.fingerprint(doc)!=p.background['fingerprint']:raise ValueError('OUTDATED: el modelo ha cambiado; cree o actualice el contexto del trazador.')
        profile=model.DuctProfile(**payload['duct_profile'])
        configuration=settings.validate(payload.get('configuration',p.configuration or equipment_selection.read(doc)))
        clearance=float(payload.get('clearance_mm',10))
        from .spatial import envelope
        envelope(profile,clearance);configuration['design_clearance_mm']=clearance
        snapshot=extractor.analyze_mep_geometry(doc,configuration)
        catalog=extractor.get_mep_connection_points(doc,configuration)
        return store.capture(document_id,p.proposal_id,payload['expected_revision'],snapshot,catalog,configuration,profile).to_dict()
    return operations.dispatch(document_id,operation,payload)
