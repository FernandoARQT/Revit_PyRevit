"""Domain operations shared by the WPF controller and existing MCP dispatcher."""
from . import model,store

MUTATIONS=('add_corridor','move_corridor','move_vertex','add_vertex','delete_vertex',
           'delete_corridor','set_constraint','set_network','assign_terminal','undo','redo',
           'move_junction','set_routing_constraint','select_alternative','move_manifold','rotate_manifold',
           'move_axis_waypoint','add_axis_waypoint','delete_axis_waypoint','move_axis','set_axis_height','set_axis_lock',
           'assign_terminals','remove_terminal','set_corridor_distributor','set_branch_waypoint','set_elbow_rotation')
OPERATIONS=tuple('routing_design_'+op for op in MUTATIONS)+(
    'routing_design_create','routing_design_get','routing_design_list','routing_design_duplicate','routing_design_export',
    'routing_design_analyze','routing_design_generate_corridors','routing_design_calculate_axes',
    'routing_design_native_plan','routing_design_commit','routing_design_audit','routing_design_assert_current')

WRITE_OPERATIONS=('routing_design_commit',)


def dispatch(document_id,operation,payload=None):
    payload=payload or {};op=operation.removeprefix('routing_design_')
    if operation not in OPERATIONS:raise ValueError('Operación de diseño desconocida: '+operation)
    if op=='list':
        return [p.to_dict() for p in store.list_proposals(document_id,payload.get('source_proposal_id'))]
    pid=payload['proposal_id']
    if op=='generate_corridors':
        from .service import generate_corridors
        return generate_corridors(document_id,pid,payload['expected_revision'],payload.get('options')).to_dict()
    if op=='calculate_axes':
        from .service import calculate_axes
        return calculate_axes(document_id,pid,payload['expected_revision'],payload.get('options')).to_dict()
    if op=='get':return store.get(document_id,pid).to_dict()
    if op=='duplicate':return store.duplicate(document_id,pid,payload['expected_revision'],payload.get('name')).to_dict()
    if op=='export':
        from .preview import export
        return export(store.get(document_id,pid),payload.get('level_id'),payload.get('path'),payload.get('width',1600),payload.get('height',1100))
    if op not in MUTATIONS:raise ValueError('La creación requiere el adaptador del documento.')
    return store.update(document_id,pid,payload['expected_revision'],op,payload).to_dict()
