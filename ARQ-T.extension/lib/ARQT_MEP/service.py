"""Shared READ / CALCULATE / PREVIEW service. No transactions or model edits."""
import copy
import hashlib
import json
from shapely.geometry import shape,Point,mapping
from . import airflow,config
from . import revit_read as rr
from . import ownership
from . import surface
from .spatial import optimize_terminal_positions,determine_terminal_count,polygon_from_rings


def digest(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,allow_nan=False).encode('utf8')).hexdigest()


def analyze_ventilation_rooms(doc,settings=None):
    settings=config.validate(copy.deepcopy(settings or config.DEFAULT))
    rooms,issues=rr.analyze_rooms(doc,settings.get('phase_id'))
    phases=sorted(set(r['phase_id'] for r in rooms))
    if len(phases)>1:
        return {'document':doc.Title,'document_id':ownership.document_id(doc),'rooms':[rr.public_room(r) for r in rooms],
                'issues':issues+['Elija una fase: hay habitaciones de varias fases.'],
                'phases':[{'id':rr.eid(p.Id),'name':rr.name(p)} for p in doc.Phases if rr.eid(p.Id) in phases]}
    if not rooms:
        raise ValueError('No hay habitaciones válidas en la fase seleccionada.')
    ceilings,cissues=rr.read_ceilings(doc,phases[0])
    edges,doors,dissues=rr.read_doors(doc,rooms,phases[0])
    for r in rooms:
        r['use']=airflow.room_use(r,settings['overrides'])
        r['excluded_exterior']=airflow.is_exterior(r['name'])
        r['doors']=doors[r['uid']]
        cs=rr.find_room_ceilings(r,ceilings,rooms,settings['overrides'].get(r['uid'],{}).get('ceiling_ids'))
        r['ceilings']=[dict({k:v for k,v in c.items() if k!='polygon'},geometry=mapping(c['polygon'])) for c in cs]
    frame={}
    try:
        view=doc.ActiveView;right=view.RightDirection;up=view.UpDirection;origin=view.Origin
        frame={'right':[right.X,right.Y],'up':[up.X,up.Y],'origin_mm':[rr.mm(origin.X),rr.mm(origin.Y)]}
    except Exception:pass
    return {'document':doc.Title,'document_id':ownership.document_id(doc),'phase_id':phases[0],
            'rooms':[rr.public_room(r) for r in rooms],'edges':edges,'issues':issues+cissues+dissues,
            'view_frame':frame,'kitchen_hints':rr.read_kitchen_hints(doc,rooms,phases[0]),
            'phases':[{'id':rr.eid(p.Id),'name':rr.name(p)} for p in doc.Phases if rr.eid(p.Id) in phases]}


def housing_rooms(analysis,room_uids):
    if not room_uids:
        raise ValueError('Defina explícitamente las Rooms que forman la vivienda completa, incluidas todas sus plantas.')
    all_rooms={r['uid']:r for r in analysis['rooms']}
    missing=set(room_uids)-set(all_rooms)
    if missing:
        raise ValueError('Rooms de vivienda ausentes en el documento/fase: '+str(sorted(missing)))
    rooms=[all_rooms[uid] for uid in sorted(set(room_uids)) if not airflow.is_exterior(all_rooms[uid]['name'])]
    if not rooms:raise ValueError('La selección no contiene habitaciones interiores; porches y terrazas están excluidos.')
    return rooms


def calculate_ventilation_flows(analysis,room_uids,settings):
    rooms=housing_rooms(analysis,room_uids)
    result=airflow.calculate_airflows(rooms,settings['overrides'],settings.get('main_bedroom_uid'),settings.get('zones',[]))
    active={r['uid'] for r in rooms if airflow.room_use(r,settings['overrides'])!='excluir'}
    result['warnings']+=airflow.transfer_issues([r for r in rooms if r['uid'] in active],analysis['edges'],result['rows'])
    result['warnings']+=['Propuesta geométrica preliminar, sin CFD. Reserva de conexión superior sin dimensiones de fabricante.',
                         'Las zonas seca/húmeda de locales mixtos y el mobiliario no identificado requieren revisión en planta.']
    room_map={r['uid']:r for r in rooms}
    for row in result['rows']:
        hint=determine_terminal_count(polygon_from_rings(room_map[row['room_uid']]['rings']),row['count'])
        row['count_review']=hint
        if hint['suggested_count']>row['count']:result['warnings'].append(row['name']+': '+hint['reason'])
    return result


def _scope_rooms(rooms,scope):
    kind=scope.get('kind','all')
    if kind=='all':
        return {r['uid'] for r in rooms}
    if kind=='level':
        result={r['uid'] for r in rooms if r['level_id']==int(scope['level_id'])}
    elif kind=='rooms':
        result=set(scope.get('room_uids',[]))
        if not result.issubset({r['uid'] for r in rooms}):
            raise ValueError('El ámbito contiene Rooms ajenas a esta vivienda.')
    else:
        raise ValueError('Ámbito desconocido.')
    if not result:
        raise ValueError('Ámbito de colocación vacío.')
    return result


def propose_ventilation_terminals(doc,room_uids,settings=None,scope=None):
    settings=config.validate(copy.deepcopy(settings or config.DEFAULT));scope=scope or {'kind':'all'}
    analysis=analyze_ventilation_rooms(doc,settings)
    rooms=housing_rooms(analysis,room_uids)
    flows=calculate_ventilation_flows(analysis,room_uids,settings)
    excluded={r['uid'] for r in analysis['rooms'] if airflow.is_exterior(r['name'])}
    effective_scope=dict(scope)
    if scope.get('kind')=='rooms':effective_scope['room_uids']=[uid for uid in scope.get('room_uids',[]) if uid not in excluded]
    selected=_scope_rooms(rooms,effective_scope)
    symbols=rr.read_symbols(doc)
    existing=ownership.inventory(doc,settings['tolerance_mm'])
    own_ids={e['id'] for e in existing if e['own'] and e['metadata']['room_uid'] in selected}
    obstacles=[o for o in rr.read_obstacles(doc,analysis['phase_id']) if o['id'] not in own_ids]
    proposals=[];issues=list(analysis['issues']);actions=[];occupied_by_room={}
    room_dict={r['uid']:r for r in rooms}
    for row in flows['rows']:
        if row['room_uid'] not in selected:
            continue
        room=room_dict[row['room_uid']]
        from .spatial import polygon_from_rings
        poly=polygon_from_rings(room['rings'])
        ceilings=[dict(c,polygon=shape(c['geometry'])) for c in room['ceilings']]
        relevant=[o for o in obstacles if o['polygon'].intersects(poly)]
        occupied=occupied_by_room.setdefault(row['room_uid'],[])
        try:
            if not ceilings:
                raise ValueError('Sin cara inferior plana de falso techo apta')
            positions=optimize_terminal_positions(poly,ceilings,row['count'],symbols[row['role']]['dimensions'],
                    settings,room['doors'],relevant,occupied,row['role'],[o for o in relevant if o['furniture']])
            for index,p in enumerate(positions):
                key=row['key']+':'+str(index)
                matches=[e for e in existing if e['own'] and e['metadata'].get('key')==key]
                if len(matches)>1:
                    raise ValueError('Metadatos duplicados para '+key)
                current=matches[0] if matches else None
                if current and current['position']:
                    import math
                    basis=current.get('basis',[[1,0,0],[0,1,0],[0,0,1]])
                    x,y,z=surface.visible_point(current['position'],basis,symbols[row['role']]['visible_z'])
                    z-=current['metadata'].get('target',{}).get('installation_offset_mm',0)
                    clear=max(settings['wall_clearance_mm'],100.01 if row['role']=='extraccion' else 0)
                    valid_ceiling=None
                    for c in ceilings:
                        if abs(surface.height(c,x,y)-z)>settings['tolerance_mm'] or c['level_id']!=current['level_id']:continue
                        if surface.basis_error_degrees(basis,surface.frame(c))>0.01:continue
                        footprint,z0,z1=surface.envelope(c,x,y,symbols[row['role']]['dimensions'])
                        if c['polygon'].buffer(-clear-0.1).covers(footprint):valid_ceiling=c;break
                    free=valid_ceiling is not None
                    if free:
                        free=poly.buffer(-clear-0.1).covers(footprint) and not any(
                            b['id']!=valid_ceiling['id'] and b['z0']<z1 and b['z1']>z0 and
                            footprint.intersects(b['polygon']) for b in relevant)
                    others=occupied+[(q['x'],q['y']) for j,q in enumerate(positions) if j!=index]
                    free=free and all(math.hypot(x-q[0],y-q[1])>=settings['terminal_spacing_mm'] for q in others)
                    if free:
                        retained={'x':x,'y':y,'z':surface.height(valid_ceiling,x,y),'ceiling_id':valid_ceiling['id'],
                                  'level_id':valid_ceiling['level_id'],'score':0.0,
                                  'plane':surface.plane(valid_ceiling),'ceiling_surface_id':surface.key(valid_ceiling),
                                  'slope_deg':surface.slope_degrees(valid_ceiling),
                                  'criteria':{'wall_mm':Point(x,y).distance(poly.boundary),
                                   'door_mm':min((math.hypot(x-d[0],y-d[1]) for d in room['doors']),default=1000),
                                   'retained_existing':True}}
                        alternatives=[retained]+[a for a in p['alternatives'] if math.hypot(x-a['x'],y-a['y'])>0.1]
                        p.update(retained);p['alternatives']=alternatives[:3];p['existing_valid']=True
                for alt in p['alternatives']:
                    alt['ceiling_uid']=next(c['uid'] for c in ceilings if c['id']==alt['ceiling_id'])
                    alt['level_z']=next(c['level_z'] for c in ceilings if c['id']==alt['ceiling_id'])
                p['ceiling_uid']=p['alternatives'][0]['ceiling_uid'];p['level_z']=p['alternatives'][0]['level_z']
                p.update({'key':key,'room_uid':row['room_uid'],'room_name':room['name'],'role':row['role'],
                          'flow':row['flow_each'],'symbol_id':symbols[row['role']]['id'],'existing_id':current['id'] if current else None,
                          'existing_position':current['position'] if current else None,'existing_flow':current['flow'] if current else None,
                          'existing_basis':current.get('basis',[[1,0,0],[0,1,0],[0,0,1]]) if current else None})
                action='crear'
                if current:
                    insertion=surface.installation_insertion(p,symbols[row['role']]['visible_z'])
                    same=all(abs(a-b)<=settings['tolerance_mm'] for a,b in zip(current['position'],insertion)) and abs(current['position'][2]-insertion[2])<=.001 and surface.basis_error_degrees(p['existing_basis'],surface.frame(p))<=0.01
                    action='mantener' if same and abs(current['flow']-p['flow'])<1e-6 else 'actualizar' if same else 'reubicar'
                    protected=current['pinned'] or current['metadata'].get('locked') or current['manual_change']
                    if protected:
                        if current['manual_change'] or action!='mantener':
                            raise ValueError('Terminal {} bloqueado o modificado manualmente; no se alterará.'.format(current['id']))
                        p['protected']=True
                        action='mantener protegido'
                    if same:
                        p['existing_valid']=True
                proposals.append(p);actions.append({'key':key,'action':action,'id':p['existing_id']})
                occupied.append((p['x'],p['y']))
        except ValueError as exc:
            issues.append('{} ({}): {}'.format(room['name'],room['number'],exc))
    expected=sum(r['count'] for r in flows['rows'] if r['room_uid'] in selected)
    for p in proposals:
        p['alternatives']=[a for a in p['alternatives'] if all(q is p or q['room_uid']!=p['room_uid'] or
            ((a['x']-q['x'])**2+(a['y']-q['y'])**2)**0.5>=settings['terminal_spacing_mm']-0.001 for q in proposals)]
    wanted={p['key'] for p in proposals}
    obsolete=[e for e in existing if e['own'] and e['metadata']['room_uid'] in selected and e['metadata'].get('key') not in wanted]
    for e in obsolete:
        actions.append({'key':e['metadata'].get('key'),'action':'eliminar propio obsoleto','id':e['id'],
                        'blocked':e['pinned'] or e['manual_change'] or bool(e['metadata'].get('locked'))})
    manual=[e['id'] for e in existing if not e['own'] and e['position'] and any(
        polygon_from_rings(r['rings']).covers(Point(e['position'][:2])) and
        any(abs(e['position'][2]-surface.height(c,e['position'][0],e['position'][1]))<1000 for c in r['ceilings']) for r in rooms if r['uid'] in selected)]
    if manual:
        issues.append('Terminales manuales detectados en el ámbito: {}. No se reutilizan ni alteran; revise el diseño existente.'.format(manual))
    existing_uids={e['id']:e['uid'] for e in existing}
    for p in proposals:p['existing_uid']=existing_uids.get(p.get('existing_id'))
    payload={'version':1,'document_id':ownership.document_id(doc),'fingerprint':rr.fingerprint(doc),
             'room_uids':sorted(r['uid'] for r in rooms),'scope':scope,'settings':settings,'analysis':analysis,'flows':flows,
             'excluded_exterior_uids':sorted(excluded.intersection(room_uids)),
             'symbols':symbols,'terminals':proposals,'actions':actions,'issues':issues,'manual_terminals':manual,
             'ready':len(proposals)==expected and expected>0 and not manual and not any(a.get('blocked') for a in actions),
             'expected_count':expected,'warnings':flows['warnings'],
             'obstacles':[dict({k:v for k,v in o.items() if k!='polygon'},geometry=mapping(o['polygon'])) for o in obstacles]}
    payload['proposal_id']=digest(payload)
    return payload


def get_ventilation_proposal(proposal):
    if proposal.get('kind')=='VentilationProposal':
        from .proposal import check
        return check(proposal)
    body=dict(proposal);identifier=body.pop('proposal_id',None)
    if identifier!=digest(body):
        raise ValueError('Propuesta alterada o incompleta; vuelva a optimizar.')
    return proposal
