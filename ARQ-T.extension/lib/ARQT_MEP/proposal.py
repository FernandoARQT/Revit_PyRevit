"""Versioned VentilationProposal: cached geometry edits, flows, zones and history.

Pure data operations in internal-project millimetres. Never imports Autodesk or
opens transactions. All clients use edit() and the same optimistic version.
"""
import copy
import datetime
import hashlib
import json
import math
import uuid
from dataclasses import dataclass,asdict
from shapely.geometry import Point,Polygon,shape,mapping
from shapely.ops import unary_union
from . import airflow,surface
from .spatial import polygon_from_rings,optimize_terminal_positions

ROLES=('impulsion','extraccion')
MUTATIONS=('move_ventilation_terminal','add_ventilation_terminal','delete_ventilation_terminal',
 'set_terminal_flow','set_room_design_flow','change_terminal_type','rebalance_ventilation',
 'define_ventilation_zone','delete_ventilation_zone','set_proposal_terminal_lock',
 'reoptimize_ventilation','select_ventilation_alternative','set_main_bedroom',
 'undo_ventilation','redo_ventilation','approve_ventilation_proposal','accept_ventilation_terminals','set_ventilation_configuration','confirm_ventilation_warnings')


def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()


def seal(data):
    result=dict(data);result.pop('content_hash',None)
    result['content_hash']=hashlib.sha256(json.dumps(result,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()
    return result


def check(data):
    if data.get('kind')!='VentilationProposal' or seal(data)['content_hash']!=data.get('content_hash'):
        raise ValueError('Propuesta alterada, incompleta o no compatible.')
    return data


def active(p):return [t for t in p['terminals'] if t.get('status')!='PENDING_DELETE']


def synchronize_symbol_ids(p):
    """Refresh cached ids together with the live symbol catalog before review."""
    for terminal in active(p):
        terminal['symbol_id']=p['symbols'][terminal['role']]['id']
        for alternative in terminal.get('alternatives',[]):
            if 'symbol_id' in alternative:
                alternative['symbol_id']=terminal['symbol_id']
def detach_missing_terminals(p,missing_ids):
    """Recreate deleted originals, preserving draft design; caller proves absence in Revit."""
    missing=set(missing_ids);recovered=[]
    for t in p['terminals']:
        if t.get('existing_id') not in missing:continue
        if t.get('status')=='PENDING_DELETE':continue
        recovered.append(t['terminal_id'])
        for key in ('existing_id','existing_uid','existing_position','existing_flow','existing_basis'):
            t[key]=None
        t.pop('preserve',None);t['protected']=False;t['accepted']=False
        t['status']='LOCKED' if t.get('lock_full') else 'PROPOSED'
    deleted=[t for t in p['terminals'] if t.get('status')=='PENDING_DELETE' and t.get('existing_id') in missing]
    p['terminals']=[t for t in p['terminals'] if t not in deleted]
    removed_actions=any(a.get('id') in missing for a in p.get('legacy_actions',[]))
    for key in ('legacy_actions','actions'):
        if key in p:p[key]=[a for a in p[key] if a.get('id') not in missing]
    if recovered or deleted or removed_actions:
        # Undo must not restore bindings to elements deleted outside this draft.
        p['history']=[];p['future']=[];p['accepted_version']=None
    return recovered


def terminal(p,tid):return next(t for t in p['terminals'] if t['terminal_id']==tid)
def room(p,uid):return next(r for r in p['analysis']['rooms'] if r['uid']==uid)
def row(p,uid,role):return next((r for r in p['flows']['rows'] if r['room_uid']==uid and r['role']==role),None)


@dataclass
class VentilationZone:
    zone_id:str
    room_uid:str
    use:str
    polygon_mm:list|None=None
    reference_mm:list|None=None
    source:str='MANUAL'
    confirmed:bool=True

    def to_dict(self):return asdict(self)


def upgrade(legacy,housing_id='Vivienda 1',zones=None):
    p=copy.deepcopy(legacy);p.pop('proposal_id',None)
    p.update(kind='VentilationProposal',schema_version=2,version=1,proposal_id='vent_'+uuid.uuid4().hex,
             housing_id=housing_id,status='PROPOSED',created_utc=now(),updated_utc=now(),zones=copy.deepcopy(zones or []),
             history=[],future=[],accepted_version=None)
    selected=[r['uid'] for r in p['analysis']['rooms'] if r['uid'] in p['room_uids']]
    if p['scope']['kind']=='level':selected=[r['uid'] for r in p['analysis']['rooms'] if r['uid'] in p['room_uids'] and r['level_id']==p['scope']['level_id']]
    elif p['scope']['kind']=='rooms':selected=p['scope']['room_uids']
    p['placement_room_uids']=sorted(selected)
    p['document_issues']=(['Terminales ajenos en el ámbito: '+str(p.get('manual_terminals'))] if p.get('manual_terminals') else [])+['Elemento propio protegido pendiente de eliminar.' for a in p['actions'] if a.get('blocked')]
    p['legacy_actions']=copy.deepcopy(p['actions'])
    for index,t in enumerate(p['terminals']):
        t.update(terminal_id=t['key'],status='LOCKED' if t.get('protected') else 'PROPOSED',position_manual=False,
            flow_mode='CAUDAL_AUTOMATICO',lock_position=bool(t.get('protected')),lock_flow=bool(t.get('protected')),
            lock_full=bool(t.get('protected')),accepted=False,warnings_confirmed=[],validation_status='VALIDO',
            existing_uid=t.get('existing_uid'),display_id=('I' if t['role']=='impulsion' else 'E')+str(index+1).zfill(2))
    p['main_bedroom_uid']=p['flows'].get('main_bedroom',{}).get('uid')
    refresh(p)
    return seal(p)


def _positive(value,label):
    try:value=float(value)
    except (TypeError,ValueError):raise ValueError(label+' debe ser numérico.')
    if not math.isfinite(value) or value<=0:raise ValueError(label+' debe ser positivo y finito.')
    return value


def _zone_geometry(p,uid,use):
    regions=[];poly=polygon_from_rings(room(p,uid)['rings'])
    for z in p['zones']:
        if z['room_uid']!=uid or z['use']!=use:continue
        if z.get('polygon_mm'):regions.append(Polygon(z['polygon_mm']))
        elif z.get('reference_mm'):regions.append(Point(z['reference_mm']).buffer(1000).intersection(poly))
    return unary_union(regions) if regions else None


def design_warnings(p,t,xy):
    r=room(p,t['room_uid']);use=airflow.room_use(r,p['settings']['overrides']);warnings=[]
    kitchen=_zone_geometry(p,r['uid'],'COCINA')
    if t['role']=='extraccion' and use not in ('cocina','bano','aseo','mixto','dormitorio_mixto') and kitchen is None:
        warnings.append('Extracción en local seco o de transferencia: confirme su función o defina zona de cocina.')
    if kitchen is not None:
        inside=kitchen.covers(Point(xy))
        if t['role']=='extraccion' and not inside:warnings.append('Extracción fuera de la zona de cocina definida.')
        if t['role']=='impulsion' and inside:warnings.append('Impulsión dentro de la zona de cocina; priorice salón-comedor.')
    if use in ('mixto','dormitorio_mixto') and kitchen is None:
        warnings.append('Local mixto sin zona de cocina delimitada: revise la posición y confirme la función.')
    return warnings


def validate_position(p,t,xy):
    if len(xy)!=2 or not all(math.isfinite(float(v)) for v in xy):
        return {'valid':False,'status':'NO_VALIDO','errors':['XY reales finitos requeridos.'],'warnings':[]}
    x,y=map(float,xy);r=room(p,t['room_uid']);poly=polygon_from_rings(r['rings']);errors=[]
    if not poly.covers(Point(x,y)):return {'valid':False,'status':'NO_VALIDO','errors':['Fuera de la habitación asociada.'],'warnings':[]}
    info=p['symbols'][t['role']]
    if info.get('placement','OneLevelBased')!='OneLevelBased':
        return {'valid':False,'status':'NO_VALIDO','errors':['Familia con alojamiento no compatible con este editor.'],'warnings':[]}
    clear=max(p['settings']['wall_clearance_mm'],100.01 if t['role']=='extraccion' else 0)
    surfaces=[]
    for c in r['ceilings']:
        candidate=dict(c,polygon=shape(c['geometry']));footprint,z0,z1=surface.envelope(candidate,x,y,info['dimensions'])
        if not candidate['polygon'].buffer(-clear-.1).covers(footprint):continue
        if not poly.buffer(-clear-.1).covers(footprint):continue
        surfaces.append((surface.height(candidate,x,y),candidate,footprint,z0,z1))
    if not surfaces:return {'valid':False,'status':'NO_VALIDO','errors':['Sin paño inferior apto o demasiado cerca de borde/pared.'],'warnings':[]}
    z,c,footprint,z0,z1=min(surfaces,key=lambda item:(item[0],str(surface.key(item[1]))))
    extra=float(p['settings'].get('connection_space_mm',100));normal=surface.plane(c)['normal']
    connection,cz0,cz1=surface.envelope(c,x,y,(info['dimensions'][0],info['dimensions'][1],info['dimensions'][2]+extra))
    for obstacle in p.get('obstacles',[]):
        if obstacle['id']==c['id'] or obstacle['id']==t.get('existing_id'):continue
        geometry=shape(obstacle['geometry'])
        if obstacle['z0']<cz1-1e-6 and obstacle['z1']>cz0+1e-6 and connection.intersects(geometry):
            errors.append('Interferencia o reserva superior ocupada por elemento {}.'.format(obstacle['id']))
    for other in active(p):
        if other['terminal_id']==t['terminal_id'] or other['room_uid']!=t['room_uid']:continue
        if math.hypot(x-other['x'],y-other['y'])<p['settings']['terminal_spacing_mm']-.001:
            errors.append('Separación insuficiente a '+other['terminal_id'])
    warnings=design_warnings(p,t,(x,y))
    pose={'x':x,'y':y,'z':z,'position_mm':[x,y,z],'plane':surface.plane(c),'basis':surface.frame(c),
          'slope_deg':surface.slope_degrees(c),'ceiling_id':c['id'],'ceiling_uid':c['uid'],
          'ceiling_surface_id':surface.key(c),'level_id':c['level_id'],'level_z':c['level_z'],
          'score':0.0,'criteria':{'wall_mm':Point(x,y).distance(poly.boundary),
          'door_mm':min((math.hypot(x-a,y-b) for a,b in r.get('doors',[])),default=1000)}}
    return {'valid':not errors,'status':'NO_VALIDO' if errors else 'VALIDO_CON_ADVERTENCIAS' if warnings else 'VALIDO',
            'errors':errors,'warnings':warnings,'pose':pose}


def _pose(p,t,xy,confirmed=False):
    result=validate_position(p,t,xy)
    if not result['valid']:raise ValueError('; '.join(result['errors']))
    acknowledged=result['warnings']==t.get('warnings_confirmed',[])
    if result['warnings'] and not confirmed and not acknowledged:raise ValueError('CONFIRM_WARNINGS: '+'; '.join(result['warnings']))
    t.update(result['pose']);t['validation_status']=result['status'];t['warnings_confirmed']=result['warnings'] if confirmed or acknowledged else []
    t['alternatives']=[copy.deepcopy(result['pose'])]+[a for a in t.get('alternatives',[]) if math.hypot(a['x']-t['x'],a['y']-t['y'])>.1][:2]


def _ensure_row(p,uid,role,flow):
    existing=row(p,uid,role)
    if existing:return existing
    if flow is None:raise ValueError('Indique explícitamente el caudal de diseño para esta nueva función.')
    r=room(p,uid);flow=_positive(flow,'Caudal')
    result=dict(key=uid+':'+role,room_uid=uid,name=r['name'],number=r['number'],level_id=r['level_id'],
        use=airflow.room_use(r,p['settings']['overrides']),role=role,minimum=0,requested=flow,design=flow,count=0,
        area_m2=r['area_m2'],flow_each=flow,minimum_basis='Función añadida manualmente; requiere revisión')
    p['flows']['rows'].append(result);return result


def _redistribute(p,r,include_manual=False):
    ts=[t for t in active(p) if t['room_uid']==r['room_uid'] and t['role']==r['role']]
    free=[t for t in ts if not t['lock_flow'] and not t['lock_full'] and (include_manual or t['flow_mode']=='CAUDAL_AUTOMATICO')]
    fixed=[t for t in ts if t not in free];remainder=r['design']-sum(t['flow'] for t in fixed)
    if not free:
        if abs(remainder)>1e-6:raise ValueError('No hay caudales libres para redistribuir '+r['name'])
        return
    if remainder<=0:raise ValueError('Los caudales fijos ocupan o exceden el diseño de '+r['name'])
    for t in free:t['flow']=remainder/len(free)


def refresh(p):
    errors=list(p.get('document_issues',[]));warnings=[];sums={role:0.0 for role in ROLES};placement=set(p['placement_room_uids'])
    for t in active(p):
        result=validate_position(p,t,[t['x'],t['y']]);t['validation_status']=result['status']
        if t.get('reoptimization_issue'):warnings.append(t['terminal_id']+': '+t['reoptimization_issue'])
        t['position_mm']=[t['x'],t['y'],t['z']];t['flow_m3h']=t['flow']*3.6
        t['flow_state']='CAUDAL_BLOQUEADO' if t['lock_flow'] or t['lock_full'] else t['flow_mode']
        errors.extend(t['terminal_id']+': '+e for e in result['errors'])
        if result['valid']:
            pose=result['pose']
            if abs(pose['z']-t['z'])>p['settings']['tolerance_mm'] or surface.basis_error_degrees(surface.frame(pose),surface.frame(t))>.01 or pose['level_id']!=t['level_id']:
                errors.append('Pose de techo desactualizada; actualice el análisis: '+t['terminal_id'])
        warnings.extend(t['terminal_id']+': '+w for w in result['warnings'])
        if result['warnings'] and result['warnings']!=t.get('warnings_confirmed',[]):errors.append('Advertencias de función sin confirmar: '+t['terminal_id'])
        if not math.isfinite(t['flow']) or t['flow']<=0:errors.append('Caudal no positivo: '+t['terminal_id'])
    room_results=[]
    for r in p['flows']['rows']:
        ts=[t for t in active(p) if t['room_uid']==r['room_uid'] and t['role']==r['role']]
        assigned=sum(t['flow'] for t in ts);r['assigned']=assigned;r['difference']=assigned-r['design'];r['count']=len(ts) if r['room_uid'] in placement else r['count']
        if r['room_uid'] in placement:
            sums[r['role']]+=assigned
            if not ts and r['minimum']>0:errors.append('Estancia sin bocas requeridas: '+r['name']+' / '+r['role'])
            if assigned<r['minimum']-1e-6:errors.append('Caudal inferior al mínimo CTE: '+r['name']+' / '+r['role'])
            if abs(r['difference'])>1e-6:errors.append('Suma de terminales distinta del diseño: '+r['name']+' / '+r['role'])
        else:sums[r['role']]+=r['design']
        room_results.append({k:r[k] for k in ('key','room_uid','role','minimum','design','assigned','difference','count')})
    difference=sums['impulsion']-sums['extraccion']
    if abs(difference)>1e-6:errors.append('Circuito desequilibrado: {:.6f} L/s.'.format(difference))
    if sums['extraccion']<p['flows']['wet_total_minimum']-1e-6:errors.append('Extracción total inferior al mínimo CTE.')
    if set(p['room_uids'])-placement:warnings.append('Ámbito parcial: fuera del ámbito se conserva el diseño, sin certificar terminales reales.')
    p['validation']={'valid':not errors,'status':'NO_VALIDO' if errors else 'VALIDO_CON_ADVERTENCIAS' if warnings else 'VALIDO',
                     'errors':list(dict.fromkeys(errors)),'warnings':list(dict.fromkeys(warnings)),
                     'rooms':room_results,'supply_lps':sums['impulsion'],'extract_lps':sums['extraccion'],'difference_lps':difference,
                     'geometry_basis':'cached Revit geometry; native family solids checked at commit'}
    p['ready']=not errors;p['expected_count']=len(active(p));p['issues']=p['validation']['errors']
    return p['validation']


def rebalance_plan(p,include_manual=False):
    working=copy.deepcopy(p);ts=active(working);rows=[r for r in working['flows']['rows'] if r['room_uid'] in working['placement_room_uids']]
    def eligible(t):return not t['lock_flow'] and not t['lock_full'] and (include_manual or t['flow_mode']=='CAUDAL_AUTOMATICO')
    for r in rows:
        group=[t for t in ts if t['room_uid']==r['room_uid'] and t['role']==r['role']]
        fixed=sum(t['flow'] for t in group if not eligible(t));free=sum(eligible(t) for t in group)
        r['design']=max(r['minimum'],r['design'],fixed+(0.001*free))
        if not free and abs(sum(t['flow'] for t in group)-r['design'])>1e-6:
            return {'compatible':False,'issues':['Caudales manuales/bloqueados incompatibles con diseño en '+r['name']],'base_version':p['version']}
    totals={role:sum(r['design'] for r in working['flows']['rows'] if r['role']==role) for role in ROLES}
    target=max(*totals.values(),working['flows']['wet_total_minimum'])
    for role in ROLES:
        group=[r for r in rows if r['role']==role and any(eligible(t) and t['room_uid']==r['room_uid'] and t['role']==role for t in ts)]
        extra=target-totals[role]
        if extra>1e-6 and not group:return {'compatible':False,'issues':['No hay caudales libres en '+role],'base_version':p['version']}
        area=sum(r['area_m2'] for r in group)
        for r in group:r['design']+=extra*r['area_m2']/area
    try:
        for r in rows:_redistribute(working,r,include_manual)
    except ValueError as exc:return {'compatible':False,'issues':[str(exc)],'base_version':p['version']}
    changes=[{'terminal_id':t['terminal_id'],'before':terminal(p,t['terminal_id'])['flow'],'after':t['flow']} for t in active(working) if abs(t['flow']-terminal(p,t['terminal_id'])['flow'])>1e-9]
    return {'compatible':True,'base_version':p['version'],'changes':changes,
            'room_designs':{r['key']:r['design'] for r in rows},'target_lps':target,'include_manual':include_manual}


def _snapshot(p):return copy.deepcopy({k:v for k,v in p.items() if k not in ('history','future','content_hash')})


def _sync_requirements(p):
    rooms=[r for r in p['analysis']['rooms'] if r['uid'] in p['room_uids']]
    new=airflow.calculate_airflows(rooms,p['settings']['overrides'],p['main_bedroom_uid'],p['zones'])
    keys={r['key'] for r in new['rows']}
    for r in p['flows']['rows']:
        if r['key'] not in keys:r['minimum']=0;r['minimum_basis']='Función manual: revisar uso'
    for r in new['rows']:
        current=row(p,r['room_uid'],r['role'])
        if current:current['minimum']=r['minimum'];current['minimum_basis']=r['minimum_basis']
        else:p['flows']['rows'].append(r)
    p['flows']['wet_total_minimum']=new['wet_total_minimum'];p['flows']['main_bedroom']=new['main_bedroom']


def edit(proposal,operation,payload,expected_version):
    check(proposal)
    if expected_version!=proposal['version']:raise ValueError('VERSION_CONFLICT: recargue la propuesta antes de editar.')
    if operation not in MUTATIONS:raise ValueError('Operación de propuesta desconocida: '+operation)
    p=copy.deepcopy(proposal);before=_snapshot(p);p['status']='MANUALLY_MODIFIED';p['accepted_version']=None
    if operation in ('undo_ventilation','redo_ventilation'):
        source='history' if operation=='undo_ventilation' else 'future';target='future' if source=='history' else 'history'
        if not p[source]:raise ValueError('No hay operaciones para '+source)
        snapshot=p[source].pop();history=p['history'];future=p['future'];p[target].append(before)
        p=snapshot;p['history']=history;p['future']=future
    elif operation=='approve_ventilation_proposal':
        validation=refresh(p)
        if not validation['valid']:raise ValueError('Propuesta no válida: '+'; '.join(validation['errors']))
        if not payload.get('confirmed'):raise ValueError('Aprobar requiere confirmed=True tras revisar la propuesta.')
        p['status']='APPROVED';p['accepted_version']=expected_version+1
        for t in active(p):t['accepted']=True;t['status']='LOCKED' if t['lock_full'] else 'APPROVED'
    else:
        if operation=='set_ventilation_configuration':
            from .config import validate
            settings=validate(copy.deepcopy(payload['settings']))
            if settings.get('phase_id')!=p['settings'].get('phase_id'):raise ValueError('Un cambio de fase requiere nuevo análisis y propuesta de ese ámbito.')
            p['settings']=settings;p['main_bedroom_uid']=settings.get('main_bedroom_uid',p['main_bedroom_uid']);_sync_requirements(p)
        elif operation=='confirm_ventilation_warnings':
            if not payload.get('confirmed'):raise ValueError('Revise los avisos y confirme explícitamente.')
            for tid in payload['terminal_ids']:
                t=terminal(p,tid);validation=validate_position(p,t,[t['x'],t['y']])
                if not validation['valid']:raise ValueError('La posición no es válida; no se puede confirmar.')
                t['warnings_confirmed']=validation['warnings']
        elif operation=='add_ventilation_terminal':
            role=payload['role'];uid=payload['room_uid']
            if role not in ROLES or uid not in p['placement_room_uids']:raise ValueError('Tipo o habitación fuera del ámbito de la propuesta.')
            tid='manual_'+uuid.uuid4().hex
            t=dict(terminal_id=tid,key=tid,room_uid=uid,room_name=room(p,uid)['name'],role=role,flow=1.0,
                   symbol_id=p['symbols'][role]['id'],existing_id=None,existing_position=None,existing_flow=None,existing_basis=None,
                   lock_position=False,lock_flow=False,lock_full=False,flow_mode='CAUDAL_AUTOMATICO',position_manual=True,
                   accepted=False,status='MANUALLY_MODIFIED',warnings_confirmed=[],alternatives=[])
            r=_ensure_row(p,uid,role,payload.get('flow_lps'));p['terminals'].append(t)
            _pose(p,t,payload['position_mm'],payload.get('confirm_warnings',False))
            if payload.get('flow_lps') is not None:t['flow']=_positive(payload['flow_lps'],'Flow');t['flow_mode']='CAUDAL_MANUAL'
            else:_redistribute(p,r)
        elif operation=='define_ventilation_zone':
            z=VentilationZone(payload.get('zone_id') or 'zone_'+uuid.uuid4().hex,payload['room_uid'],payload.get('use','COCINA'),
                    payload.get('polygon_mm'),payload.get('reference_mm'),payload.get('source','MANUAL'),payload.get('confirmed',True)).to_dict()
            if z['room_uid'] not in p['room_uids'] or z['use'] not in ('COCINA','SALON_COMEDOR'):raise ValueError('Zona funcional o habitación no válida.')
            poly=polygon_from_rings(room(p,z['room_uid'])['rings'])
            if z['polygon_mm']:
                geometry=Polygon(z['polygon_mm'])
                # GEOS may report covers=False on a computed intersection even
                # with zero outside area; allow only submillimetric roundoff.
                if not geometry.is_valid or geometry.area<1 or geometry.difference(poly).area>.01 or not poly.buffer(.1).covers(geometry):raise ValueError('Polígono de zona inválido o fuera de Room.')
            elif z['reference_mm']:
                if len(z['reference_mm'])!=2 or not all(math.isfinite(float(v)) for v in z['reference_mm']) or not poly.covers(Point(z['reference_mm'])):raise ValueError('Referencia de cocina fuera de Room.')
            else:raise ValueError('La zona necesita polígono o punto de referencia medido.')
            p['zones']=[v for v in p['zones'] if v['zone_id']!=z['zone_id']]+[z];_sync_requirements(p)
        elif operation=='delete_ventilation_zone':
            p['zones']=[z for z in p['zones'] if z['zone_id']!=payload['zone_id']];_sync_requirements(p)
        elif operation=='set_main_bedroom':
            p['main_bedroom_uid']=payload['room_uid'];p['settings']['main_bedroom_uid']=payload['room_uid']
            p['settings'].setdefault('main_bedrooms',{}).setdefault(p['document_id'],{})[p['housing_id']]=payload['room_uid'];_sync_requirements(p)
        elif operation=='set_room_design_flow':
            r=row(p,payload['room_uid'],payload['role'])
            if r is None:raise ValueError('Función de estancia no definida.')
            value=_positive(payload['flow_lps'],'Diseño de estancia')
            if value<r['minimum']:raise ValueError('Caudal de diseño inferior al mínimo CTE.')
            r['design']=value;r['requested']=value
            if payload.get('redistribute'):_redistribute(p,r,payload.get('include_manual',False))
        elif operation=='rebalance_ventilation':
            if not payload.get('confirmed'):raise ValueError('Revise el plan y confirme el reequilibrado.')
            plan=rebalance_plan(p,payload.get('include_manual',False))
            if not plan['compatible']:raise ValueError('; '.join(plan['issues']))
            for change in plan['changes']:terminal(p,change['terminal_id'])['flow']=change['after']
            for r in p['flows']['rows']:
                if r['key'] in plan['room_designs']:r['design']=plan['room_designs'][r['key']]
        elif operation=='reoptimize_ventilation':
            uid=payload.get('room_uid')
            for t in active(p):
                if uid and t['room_uid']!=uid:continue
                if t['lock_position'] or t['lock_full'] or t['position_manual'] and not payload.get('include_manual_positions',False):continue
                r=room(p,t['room_uid']);poly=polygon_from_rings(r['rings']);cs=[dict(c,polygon=shape(c['geometry'])) for c in r['ceilings']]
                obstacles=[dict(o,polygon=shape(o['geometry'])) for o in p.get('obstacles',[]) if o['id']!=t.get('existing_id')]
                others=[(q['x'],q['y']) for q in active(p) if q['terminal_id']!=t['terminal_id'] and q['room_uid']==t['room_uid']]
                kitchen=_zone_geometry(p,t['room_uid'],'COCINA')
                preferred=(kitchen if t['role']=='extraccion' else poly.difference(kitchen)) if kitchen is not None else None
                candidates=optimize_terminal_positions(poly,cs,1,p['symbols'][t['role']]['dimensions'],p['settings'],r.get('doors',[]),obstacles,others,t['role'],preferred=preferred)
                valid=[]
                for option in candidates[0]['alternatives']:
                    validation=validate_position(p,t,[option['x'],option['y']])
                    if validation['valid'] and not validation['warnings']:
                        valid.append(validation['pose'])
                if valid:
                    _pose(p,t,[valid[0]['x'],valid[0]['y']],True);t['alternatives']=valid[:3];t.pop('reoptimization_issue',None)
                else:t['reoptimization_issue']='No se halló una alternativa válida; se conserva la posición para revisión.'
        elif operation=='accept_ventilation_terminals':
            ids=set(payload.get('terminal_ids',[t['terminal_id'] for t in active(p)]))
            for t in active(p):
                if t['terminal_id'] in ids:t['accepted']=bool(payload.get('accepted',True))
        else:
            t=terminal(p,payload['terminal_id'])
            if t.get('status')=='PENDING_DELETE':raise ValueError('Terminal pendiente de eliminar: deshaga esa operación antes de editarlo.')
            if operation=='set_proposal_terminal_lock':
                for key in ('lock_position','lock_flow','lock_full'):
                    if key in payload:t[key]=bool(payload[key])
                if t['lock_full']:t['status']='LOCKED'
            else:
                if t['lock_full']:raise ValueError('Terminal completamente bloqueado.')
                if operation in ('move_ventilation_terminal','select_ventilation_alternative'):
                    if t['lock_position']:raise ValueError('Posición bloqueada.')
                    xy=payload['position_mm'] if operation=='move_ventilation_terminal' else [t['alternatives'][payload['alternative_index']][a] for a in ('x','y')]
                    _pose(p,t,xy,payload.get('confirm_warnings',False));t['position_manual']=True
                elif operation=='set_terminal_flow':
                    if t['lock_flow']:raise ValueError('Caudal bloqueado.')
                    t['flow']=_positive(payload['flow_lps'],'Flow');t['flow_mode']='CAUDAL_MANUAL'
                elif operation=='delete_ventilation_terminal':
                    if t['lock_position'] or t['lock_flow']:raise ValueError('Desbloquee el terminal antes de eliminarlo.')
                    if t.get('existing_id'):t['status']='PENDING_DELETE'
                    else:p['terminals'].remove(t)
                elif operation=='change_terminal_type':
                    role=payload['role']
                    if role not in ROLES:raise ValueError('Tipo de terminal no compatible.')
                    if t['lock_position'] or t['lock_flow']:raise ValueError('El cambio de tipo afecta posición y caudal; desbloquee ambos primero.')
                    t['role']=role;t['symbol_id']=p['symbols'][role]['id'];_ensure_row(p,t['room_uid'],role,t['flow'])
                    _pose(p,t,[t['x'],t['y']],payload.get('confirm_warnings',False))
                t['accepted']=False
                if t.get('status')!='PENDING_DELETE':t['status']='MANUALLY_MODIFIED'
        p['history'].append(before);p['history']=p['history'][-30:];p['future']=[]
    p['version']=expected_version+1;p['updated_utc']=now();refresh(p)
    return seal(p)
