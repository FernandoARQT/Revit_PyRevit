"""Revit-facing proposal creation, freshness checks and explicit atomic commit."""
import copy
from . import service,proposal as model,proposal_store as store,revit_read as rr,ownership,surface
from shapely.geometry import mapping


def create(doc,room_uids,settings=None,scope=None,housing_id='Vivienda 1'):
    legacy=service.propose_ventilation_terminals(doc,room_uids,settings,scope)
    p=model.upgrade(legacy,housing_id,(settings or {}).get('zones',[]))
    for t in p['terminals']:
        if t.get('existing_id'):
            metadata=ownership.read(doc.GetElement(rr.DB.ElementId(t['existing_id']))) or {}
            t['lock_position']=bool(metadata.get('lock_position',t['lock_position']))
            t['lock_flow']=bool(metadata.get('lock_flow',t['lock_flow']))
            t['lock_full']=bool(metadata.get('locked',t['lock_full']))
    model.refresh(p);return store.create(model.seal(p))


def current(doc,p):
    model.check(p)
    if ownership.document_id(doc)!=p['document_id'] or rr.fingerprint(doc)!=p['fingerprint']:
        raise ValueError('OUTDATED: la geometría o los elementos Revit han cambiado; actualice el análisis antes de aprobar o aplicar.')


def approve(doc,proposal_id,expected_version,confirmed):
    p=store.get(ownership.document_id(doc),proposal_id);current(doc,p)
    return store.update(p['document_id'],proposal_id,expected_version,'approve_ventilation_proposal',{'confirmed':confirmed})


def refresh(doc,proposal_id,expected_version):
    document_id=ownership.document_id(doc);path=store._path(document_id,proposal_id)
    with store._lock(path):
        p=store.get(document_id,proposal_id)
        if p['version']!=expected_version:raise ValueError('VERSION_CONFLICT: la propuesta ha cambiado.')
        analysis=service.analyze_ventilation_rooms(doc,p['settings'])
        if not set(p['room_uids']).issubset({r['uid'] for r in analysis['rooms']}):
            raise ValueError('OUTDATED: faltan Rooms de la vivienda; revise la selección sin descartar el borrador guardado.')
        existing=ownership.inventory(doc,p['settings']['tolerance_mm']);by_id={e['id']:e for e in existing};issues=[]
        ids={t['existing_id'] for t in p['terminals'] if t.get('existing_id')}
        ids.update(a['id'] for a in p.get('legacy_actions',[]) if a.get('id') and a['action']=='eliminar propio obsoleto')
        missing={eid for eid in ids if doc.GetElement(rr.DB.ElementId(eid)) is None}
        model.detach_missing_terminals(p,missing)
        ids-=missing
        for eid in ids:
            item=by_id.get(eid)
            if item is None or not item['own']:issues.append('Terminal original ausente o ajeno: '+str(eid))
            elif any(t.get('existing_id')==eid and t.get('existing_uid') and t['existing_uid']!=item['uid'] for t in p['terminals']):
                issues.append('La identidad del terminal original ha cambiado: '+str(eid))
            elif item['manual_change']:issues.append('Cambio manual externo al editor en terminal '+str(eid)+'. No se sobrescribe.')
        p['analysis']=analysis;p['symbols']=rr.read_symbols(doc)
        model.synchronize_symbol_ids(p)
        p['obstacles']=[dict({k:v for k,v in o.items() if k!='polygon'},geometry=mapping(o['polygon'])) for o in rr.read_obstacles(doc,analysis['phase_id']) if o['id'] not in ids]
        for t in model.active(p):
            validation=model.validate_position(p,t,[t['x'],t['y']])
            if not validation['valid']:continue
            pose=validation['pose']
            changed=(abs(pose['z']-t['z'])>p['settings']['tolerance_mm'] or
                     surface.basis_error_degrees(surface.frame(pose),surface.frame(t))>.01 or pose['level_id']!=t['level_id'])
            if changed and (t['lock_position'] or t['lock_full']):
                issues.append('El techo de un terminal con posición bloqueada ha cambiado: '+t['terminal_id'])
            else:
                t.update(pose);t['alternatives']=[pose]+[a for a in t.get('alternatives',[]) if abs(a['x']-t['x'])+abs(a['y']-t['y'])>.1][:2]
        p['fingerprint']=rr.fingerprint(doc);p['document_issues']=issues;p['version']+=1;p['updated_utc']=model.now();p['status']='MANUALLY_MODIFIED';p['accepted_version']=None
        model.refresh(p);p=model.seal(p);store._write(path,p)
    return p


def legacy_for_commit(p):
    model.check(p);b={k:copy.deepcopy(p[k]) for k in ('document_id','fingerprint','room_uids','scope','settings','analysis','flows','symbols','warnings')}
    b.update(version=1,editor_proposal_id=p['proposal_id'],ready=p['ready'],issues=p['issues'],terminals=[],actions=[])
    active=model.active(p);wanted_ids={t.get('existing_id') for t in active if t.get('existing_id')}
    for t in active:
        q=copy.deepcopy(t);pose={k:copy.deepcopy(q[k]) for k in ('x','y','z','plane','ceiling_id','ceiling_uid','ceiling_surface_id','level_id','level_z','slope_deg','criteria','score') if k in q}
        q['alternatives']=[pose];q['editor_update']=True
        q['protected']=bool(q.get('protected') or q.get('lock_full'))
        b['terminals'].append(q)
        b['actions'].append({'key':q['key'],'action':'crear' if not q.get('existing_id') else 'actualizar propuesta','id':q.get('existing_id')})
    obsolete={a['id']:a for a in p.get('legacy_actions',[]) if a['action']=='eliminar propio obsoleto' and a['id'] not in wanted_ids}
    for t in p['terminals']:
        if t.get('status')=='PENDING_DELETE' and t.get('existing_id'):
            obsolete[t['existing_id']]={'key':t['key'],'id':t['existing_id'],'action':'eliminar propio obsoleto',
                                      'blocked':bool(t['lock_full'] or t['lock_position'] or t['lock_flow'])}
    b['actions'].extend(obsolete.values());b['expected_count']=len(active)
    b['proposal_id']=service.digest(b)
    return b


def commit(doc,proposal_id,expected_version,approved=False,delete_obsolete=False,cancelled=None):
    if not approved:raise ValueError('Aplicar requiere approved=True tras revisar la propuesta.')
    document_id=ownership.document_id(doc);path=store._path(document_id,proposal_id)
    with store._lock(path):
        p=store.get(document_id,proposal_id)
        if p['version']!=expected_version:raise ValueError('VERSION_CONFLICT: la propuesta ha cambiado.')
        current(doc,p)
        if p['status']!='APPROVED' or p['accepted_version']!=expected_version:raise ValueError('Apruebe la versión actual de la propuesta antes de aplicar.')
        model.refresh(p)
        if not p['ready']:raise ValueError('La propuesta no cumple las comprobaciones actuales.')
        from . import revit_write
        legacy=legacy_for_commit(p)
        result=revit_write.create_ventilation_terminals(doc,legacy,True,delete_obsolete=delete_obsolete,cancelled=cancelled)
        mapped={r['key']:r for r in result['validated']}
        p['terminals']=[t for t in p['terminals'] if t.get('status')!='PENDING_DELETE']
        for t in p['terminals']:
            actual=mapped[t['key']];e=doc.GetElement(rr.DB.ElementId(actual['id']))
            t.update(existing_id=actual['id'],existing_uid=e.UniqueId,existing_position=actual['position_mm'],existing_flow=actual['flow_l_s'],
                     existing_basis=rr.instance_basis(e),status='LOCKED' if t['lock_full'] else 'PLACED',accepted=True)
        p['fingerprint']=rr.fingerprint(doc);p['version']+=1;p['status']='PLACED';p['updated_utc']=model.now();p['accepted_version']=None
        p['legacy_actions']=[];p['history']=[];p['future']=[];p=model.seal(p)
        try:store._write(path,p)
        except Exception as exc:
            result['warnings'].append('Modelo aplicado; falló la actualización del archivo de propuesta. La revisión antigua impide repetir la escritura: '+str(exc))
        return {'proposal':p,'model_result':result}
