"""Executable V3 stages over immutable snapshots, with optimistic publication."""
import copy
import dataclasses
from . import model,store
from ..core.spatial_model import build_spatial_model


def region_levels(p):
    rooms=[r for r in p.background['analysis']['rooms'] if r['uid'] in p.background['room_uids']]
    ceiling_levels={}
    for room in rooms:
        for c in room.get('ceilings',[]):
            for key in (c['id'],c.get('uid')):
                if key is not None:ceiling_levels.setdefault(key,set()).add(room['level_id'])
    levels={}
    for region in p.snapshot.get('regions',[]):
        element=region.get('lower_element')
        choices=ceiling_levels.get(element,set())
        if not choices and isinstance(element,str):choices=ceiling_levels.get(element.split(':')[0],set())
        if 'level_id' in region:choices={region['level_id']}
        if len(choices)==1:levels[region['id']]=next(iter(choices))
    # Reuse the core's real air bridges, without inferring floors across levels.
    space=build_spatial_model(p.snapshot,p.configuration.get('constraints',{}))
    for region in space.regions:
        ids=region.data.get('ceiling_ids',[])
        if ids and all(i in levels for i in ids) and len({levels[i] for i in ids})==1:levels[region.data['id']]=levels[ids[0]]
    return levels


def calculate_graph(p,options=None,cancelled=None):
    """Worker-only computation. No store writes or live Revit access."""
    if not p.snapshot or not p.profiles:raise ValueError('Analice el edificio y defina un perfil de conducto antes de generar corredores.')
    profile=model.DuctProfile(**next(iter(p.profiles.values())))
    if p.configuration.get('routing_strategy')=='MANUAL_BUNDLE':
        from .manual_bundle import calculate,prepare
        return calculate(p,profile,options,cancelled) if (options or {}).get('task')=='AXES' else prepare(p)
    if (options or {}).get('task')=='AXES':
        from .axes import calculate
        return calculate(p,profile,options,cancelled)
    if p.configuration.get('routing_strategy')=='GLOBAL_RADIAL_V3':
        from .planner import calculate
        result=calculate(p,profile,options,cancelled)
        if cancelled and cancelled() and p.solver_state.get('selected_paths'):
            old=copy.deepcopy(p);old.corridor_graph['last_attempt']={'cancelled':True};return old
        return result
    from .graph import generate
    levels=region_levels(p)
    if not levels:raise ValueError('No hay cámaras con una planta identificada en el ámbito.')
    anchors=[{'xyz_mm':t['position_mm'],'level_id':t['level_id']} for t in p.terminals.values()]
    opts=options or {}
    graph=generate(p.snapshot,profile,levels,p.configuration.get('constraints'),anchors,
        spacing_mm=opts.get('spacing_mm',450),clearance_mm=p.configuration['design_clearance_mm'],
        max_nodes=opts.get('max_nodes',1600),seconds=opts.get('seconds',30),cancelled=cancelled)
    graph['stats']['unmapped_regions']=[r['id'] for r in p.snapshot['regions'] if r['id'] not in levels]
    result=copy.deepcopy(p);result.corridor_graph=graph
    if graph['stats'].get('cancelled') and p.corridor_graph:
        result=copy.deepcopy(p)
        result.corridor_graph['last_attempt']=graph['stats']
        return result
    # Generated candidates never replace a manually edited geometric decision.
    manual={k:c for k,c in result.corridors.items() if c.get('kind','MANUAL')=='MANUAL'}
    from .display import corridors as display_corridors
    generated=display_corridors(graph)
    result.corridors=dict(generated,**manual)
    for cid in manual:graph['edges'].pop(cid,None)
    result.corridor_assignments={t:ids for t,ids in p.corridor_assignments.items() if all(i in result.corridors for i in ids)}
    for cid in set(p.corridors)-set(manual):model.invalidate(result,cid)
    result.validation={};result.solver_state={}
    return result


def generate_corridors(document_id,proposal_id,expected_revision,options=None,cancelled=None):
    p=store.get(document_id,proposal_id)
    if type(expected_revision)!=int or p.revision!=expected_revision:raise ValueError('VERSION_CONFLICT')
    lease=model.context(p,p.configuration)
    from .process import calculate
    result=calculate(p,options or {},cancelled)
    return store.publish(document_id,proposal_id,lease,p.configuration,result)


def calculate_axes(document_id,proposal_id,expected_revision,options=None,cancelled=None):
    return generate_corridors(document_id,proposal_id,expected_revision,dict(options or {},task='AXES'),cancelled)
