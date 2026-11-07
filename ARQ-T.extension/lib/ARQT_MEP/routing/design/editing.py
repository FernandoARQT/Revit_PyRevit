"""Schematic domain edits retain topology, physical ends and scoped decisions."""
import copy
import math
from . import model


def _fixed(p,xy):
    points=[q for path in p.selected_paths.values() for q in (path['points_mm'][0],path['points_mm'][-1])]
    return any(math.dist(xy,q[:2])<1e-4 for q in points)


def _update_vertex(p,cid,index,xy,changed):
    c=p.corridors[cid];old=c['reference_xy_mm'][index]
    if _fixed(p,old):raise ValueError('El extremo real está fijado. Modifique el distribuidor mediante su transformación virtual.')
    if c['constraint_type']=='LOCKED_WAYPOINT':raise ValueError('Corredor bloqueado.')
    boundary=index in (0,len(c['reference_xy_mm'])-1)
    for key,other in p.corridors.items():
        if key!=cid and not boundary:continue
        if other.get('source_manifold_id')!=c.get('source_manifold_id'):continue
        indices=[index] if key==cid else [i for i in (0,len(other['reference_xy_mm'])-1) if math.dist(other['reference_xy_mm'][i],old)<1e-4]
        if indices and other['constraint_type']=='LOCKED_WAYPOINT':raise ValueError('La unión pertenece a un corredor bloqueado.')
        for i in indices:
            other['reference_xy_mm'][i]=list(xy)
            if other.get('reference_xyz_mm') and len(other['reference_xyz_mm'])==len(other['reference_xy_mm']):other['reference_xyz_mm'][i][:2]=xy
            changed.add(key)


def _compile(p,changed):
    affected=set()
    for cid in changed:
        c=p.corridors.get(cid)
        if c:affected.update(c['reachable_terminal_ids']);c['validation_status']='EDITED_PENDING_RECHECK'
    for tid in affected:
        ids=p.corridor_assignments.get(tid,[])
        if not ids:continue
        points=[]
        for cid in ids:
            c=p.corridors[cid];xyz=c.get('reference_xyz_mm',[])
            if len(xyz)!=len(c['reference_xy_mm']):
                previous=p.selected_paths[tid]['points_mm'];z=sum(q[2] for q in previous)/len(previous)
                xyz=[[x,y,z] for x,y in c['reference_xy_mm']]
            if points and math.dist(points[-1],xyz[0])>1e-3:raise ValueError('La edición separa una unión del esquema.')
            points.extend(copy.deepcopy(xyz[1:] if points else xyz))
        previous=p.selected_paths[tid]
        p.constraints.setdefault('schematic_paths',{})[tid]={'points_mm':points,'portal_ids':previous['portal_ids'],
            'endpoint_id':previous['endpoint']['endpoint_id'],'source_port_hint':previous['source_port_hint'],
            'network_type':previous['network_type'],'manifold_id':previous['manifold_id'],
            'constraint_type':'LOCKED' if any(p.corridors[cid]['constraint_type']=='LOCKED_WAYPOINT' for cid in ids) else 'REQUIRED'}
        if tid in p.connectivity:p.connectivity[tid]['status']='EDITED_PENDING_RECHECK'
        if 'axis:'+tid in p.individual_routes:p.individual_routes['axis:'+tid]['validation_status']='NOT_EVALUATED'
    for cid in changed:
        c=p.corridors.get(cid)
        if c:model._points(c['reference_xy_mm'])
    p.validation={};p.design_stage='CORRIDORS';p.solver_state['stage']='EDITED_PENDING_RECHECK'
    return sorted(affected)


def edit(original,operation,payload,expected_revision):
    if type(expected_revision)!=int or original.revision!=expected_revision:raise ValueError('VERSION_CONFLICT')
    p=copy.deepcopy(original);before=model._snapshot(p);changed=set();affected=[]
    if operation in ('undo','redo'):
        source=p.history if operation=='undo' else p.future;target=p.future if operation=='undo' else p.history
        if not source:raise ValueError('No hay operaciones para '+operation)
        target.append(model._restore(p,source.pop()));p.validation={}
        p.solver_state['selected_paths']=copy.deepcopy(p.selected_paths)
    elif operation in ('move_axis_waypoint','add_axis_waypoint','delete_axis_waypoint','move_axis','set_axis_height','set_axis_lock'):
        rid=payload['route_id'];route=p.individual_routes.get(rid) or p.main_connections[rid];points=route.get('points_mm',[])
        if len(points)<2:raise ValueError('El eje no tiene geometría editable.')
        if p.constraints.get('axis_locks',{}).get(rid) and operation!='set_axis_lock':raise ValueError('Eje bloqueado.')
        index=payload.get('index',0)
        if operation=='set_axis_lock':
            if 'segment_index' in payload:
                i=payload['segment_index']
                if type(i)!=int or not 0<=i<len(points)-1:raise ValueError('Tramo de eje inválido.')
                records=p.constraints.setdefault('axis_locked_segments',{}).setdefault(rid,{})
                if payload.get('locked',True):records[str(i)]=copy.deepcopy(points[i:i+2])
                else:records.pop(str(i),None)
            else:p.constraints.setdefault('axis_locks',{})[rid]=bool(payload.get('locked',True))
        else:
            if operation!='move_axis' and (type(index)!=int or not 0<index<len(points)-1):raise ValueError('Los conectores reales de los extremos están fijados.')
            locked=[q for segment in p.constraints.get('axis_locked_segments',{}).get(rid,{}).values() for q in segment]
            indices=range(1,len(points)-1) if operation=='move_axis' else [index]
            if any(any(math.dist(points[i],q)<1e-4 for q in locked) for i in indices):raise ValueError('El punto pertenece a un tramo bloqueado.')
            if operation=='move_axis_waypoint':
                xy=model._xy(payload['point_mm']);points[index][:2]=xy
            elif operation=='set_axis_height':
                z=float(payload['z_mm'])
                if not math.isfinite(z):raise ValueError('Cota de eje inválida.')
                points[index][2]=z
            elif operation=='add_axis_waypoint':
                xy=model._xy(payload['point_mm']);points.insert(index,xy+[(points[index-1][2]+points[index][2])/2])
            elif operation=='delete_axis_waypoint':points.pop(index)
            else:
                dx,dy=model._xy(payload['delta_mm'])
                for q in points[1:-1]:q[0]+=dx;q[1]+=dy
            p.constraints.setdefault('axis_waypoints',{})[rid]=[{'waypoint_id':rid+':'+str(i),'xyz_mm':copy.deepcopy(q)} for i,q in enumerate(points[1:-1],1)]
        route['validation_status']='EDITED_PENDING_RECHECK';route['native_validation_status']='NOT_EVALUATED'
        p.validation={};p.design_stage='AXES';affected=[rid]
    elif operation in ('move_corridor','move_vertex','add_vertex','delete_vertex','delete_corridor','set_constraint','set_network'):
        cid=payload['corridor_id'];c=p.corridors[cid]
        if c.get('generated_by')!='GLOBAL_RADIAL_V3':
            # Existing manual guides retain the original editor contract.
            legacy=copy.deepcopy(original);legacy.configuration['routing_strategy']='LEGACY'
            result=model.edit(legacy,operation,payload,expected_revision);result.configuration=copy.deepcopy(original.configuration);return result
        if c['constraint_type']=='LOCKED_WAYPOINT' and operation!='set_constraint':raise ValueError('Corredor bloqueado.')
        points=c['reference_xy_mm'];index=payload.get('index',0)
        if operation=='move_vertex':
            if type(index)!=int or not 0<=index<len(points):raise ValueError('Índice de vértice inválido.')
            _update_vertex(p,cid,index,model._xy(payload['point_mm']),changed)
        elif operation=='move_corridor':
            dx,dy=model._xy(payload['delta_mm']);original_points=copy.deepcopy(points)
            for i,xy in enumerate(original_points):
                if not _fixed(p,xy):_update_vertex(p,cid,i,[xy[0]+dx,xy[1]+dy],changed)
        elif operation=='add_vertex':
            if type(index)!=int or not 0<index<len(points):raise ValueError('Inserte el punto dentro de un tramo.')
            xyz=c['reference_xyz_mm'];z=(xyz[index-1][2]+xyz[index][2])/2
            xy=model._xy(payload['point_mm']);points.insert(index,xy);xyz.insert(index,xy+[z]);changed.add(cid)
        elif operation=='delete_vertex':
            if type(index)!=int or not 0<index<len(points)-1:raise ValueError('No se elimina una conexión del esquema.')
            protected=[q for portal in p.corridor_graph.get('portals',[]) for q in portal['points_mm']]
            if any(math.dist(points[index],q[:2])<1e-4 for q in protected):raise ValueError('El punto representa un paso constructivo.')
            points.pop(index);c['reference_xyz_mm'].pop(index);changed.add(cid)
        elif operation=='set_network':raise ValueError('La red procede del distribuidor y del destino; no se cambia solo el color del corredor.')
        elif operation=='set_constraint':
            if payload['constraint_type'] not in model.CONSTRAINTS:raise ValueError('Restricción inválida.')
            c['constraint_type']=payload['constraint_type'];changed.add(cid)
        else:
            affected=list(c['reachable_terminal_ids']);del p.corridors[cid]
            for tid in affected:
                p.corridor_assignments.pop(tid,None);p.constraints.setdefault('schematic_paths',{}).pop(tid,None)
                p.selected_paths.pop(tid,None);p.connectivity[tid]['status']='SCHEMATIC_CONNECTION_REMOVED'
                for other in p.corridors.values():
                    if tid in other.get('reachable_terminal_ids',[]):other['validation_status']='EDITED_PENDING_RECHECK'
            p.validation={};p.design_stage='CORRIDORS'
        if changed:affected=_compile(p,changed)
    elif operation in ('move_manifold','rotate_manifold'):
        from .equipment import edit_frame
        network=payload['network_type'];edit_frame(p.equipment[network],operation,payload)
        affected=[tid for tid,t in p.terminals.items() if t['network_type']==network]
        for tid in affected:
            p.connectivity.setdefault(tid,{})['status']='EDITED_PENDING_RECHECK'
            if 'axis:'+tid in p.individual_routes:p.individual_routes['axis:'+tid]['validation_status']='NOT_EVALUATED'
        p.validation={};p.design_stage='CORRIDORS'
    elif operation=='move_junction':
        junction=p.schematic_junctions[payload['junction_id']];cid=junction['parent_corridor_id']
        _update_vertex(p,cid,len(p.corridors[cid]['reference_xy_mm'])-1,model._xy(payload['point_mm']),changed);affected=_compile(p,changed)
        junction['position_xyz_mm'][:2]=model._xy(payload['point_mm'])
    elif operation=='set_routing_constraint':
        value=payload['constraint_type']
        if value not in ('PREFERRED','REQUIRED','FORBIDDEN','LOCKED'):raise ValueError('Restricción desconocida.')
        tids=payload.get('terminal_ids',list(p.terminals))
        if not tids or set(tids)-set(p.terminals):raise ValueError('Ámbito de restricción inválido.')
        network=payload.get('network_type');tids=[t for t in tids if network is None or p.terminals[t]['network_type']==network]
        key=payload.get('constraint_id') or __import__('uuid').uuid4().hex
        p.constraints.setdefault('routing',{})[key]=dict(payload,terminal_ids=tids,constraint_type=value)
        affected=tids;p.validation={};p.design_stage='CORRIDORS'
        for tid in tids:p.connectivity.setdefault(tid,{})['status']='EDITED_PENDING_RECHECK'
    elif operation=='select_alternative':
        i=payload['index']
        if type(i)!=int or not 0<=i<len(p.alternatives):raise ValueError('Alternativa ausente.')
        alternative=p.alternatives[i];paths=alternative['selected_paths'];candidates={tid:[path] for tid,path in paths.items()}
        from .schematic import build
        built=build({tid:0 for tid in paths},candidates,p.corridor_graph['edges'],p.terminals)
        p.corridors=built['corridors'];p.branches=built['branches'];p.schematic_junctions=built['junctions'];p.corridor_assignments=built['assignments']
        p.selected_paths=copy.deepcopy(paths);p.solver_state['selected_paths']=copy.deepcopy(paths);p.validation={};p.design_stage='CORRIDORS'
        for tid,path in paths.items():
            if original.selected_paths.get(tid)!=path and 'axis:'+tid in p.individual_routes:p.individual_routes['axis:'+tid]['validation_status']='NOT_EVALUATED'
        for tid in p.connectivity:p.connectivity[tid]['status']='TOPOLOGICALLY_REACHABLE' if tid in paths else 'NOT_SELECTED'
        affected=list(p.terminals)
    else:raise ValueError('Operación V3 desconocida: '+operation)
    if operation not in ('undo','redo'):
        p.manual_edits.append({'operation':operation,'payload':copy.deepcopy(payload),'affected':affected})
        p.history.append(model._delta(before,p));p.history=p.history[-model.HISTORY_LIMIT:];p.future=[]
    p.revision+=1;p.updated_utc=model.now()
    return model.RoutingDesignProposal.from_dict(p.to_dict())
