"""Native MCP editor integration; run_modify(dry_run=True), also grouped rollback."""
import copy
import os
import tempfile
from pyrevit.api import DB
from ARQT_MEP import service,config,proposal as model,proposal_store as store,proposal_adapter as adapter,ownership,revit_read as rr
from ARQT_MEP.integration import dispatch
from ARQT_MEP.spatial import optimize_terminal_positions,polygon_from_rings
from shapely.geometry import shape


def run(doc,artifact_folder):
    before={rr.eid(e.Id) for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType()};prior_root=store.ROOT
    report={'native':True,'prior_model_modified':doc.IsModified}
    group=DB.TransactionGroup(doc,'ARQ-T | Ensayo editor ventilación revertido');group.Start()
    try:
        with tempfile.TemporaryDirectory(dir=artifact_folder,prefix='editor-native-store-') as temporary:
            store.ROOT=temporary;s=copy.deepcopy(config.DEFAULT);a=service.analyze_ventilation_rooms(doc,s)
            p=adapter.create(doc,[r['uid'] for r in a['rooms']],s,housing_id='ENSAYO MCP EDITOR')
            def edit(op,**payload):
                nonlocal p
                p=dispatch(doc,op,dict(payload,proposal_id=p['proposal_id'],expected_version=p['version']));return p
            def commit(delete=False):
                nonlocal p
                edit('approve_ventilation_proposal',confirmed=True)
                result=dispatch(doc,'commit_ventilation_proposal',{'proposal_id':p['proposal_id'],'expected_version':p['version'],'approved':True,'delete_obsolete':delete},allow_write=True)
                p=result['proposal'];return result['model_result']
            for t in list(p['terminals']):
                if model.design_warnings(p,t,[t['x'],t['y']]):edit('move_ventilation_terminal',terminal_id=t['terminal_id'],position_mm=[t['x'],t['y']],confirm_warnings=True)
            sloped=next(t for t in p['terminals'] if t['slope_deg']>.1);tid=sloped['terminal_id'];old_z=sloped['z'];xy=None
            for dy in (25,-25,40,-40):
                candidate=[sloped['x'],sloped['y']+dy]
                if model.validate_position(p,sloped,candidate)['valid']:xy=candidate;break
            assert xy is not None
            edit('move_ventilation_terminal',terminal_id=tid,position_mm=xy,confirm_warnings=True)
            assert abs(model.terminal(p,tid)['z']-old_z)>.01
            report['slope_move_recomputes_z']=True
            edit('set_terminal_flow',terminal_id=tid,flow_lps=model.terminal(p,tid)['flow']+1)
            assert not dispatch(doc,'get_ventilation_validation',{'proposal_id':p['proposal_id']})['valid']
            edit('rebalance_ventilation',confirmed=True);first=commit();ids={v['id'] for v in first['validated']}
            assert all(v['valid'] and v['orientation_error_deg']<.01 for v in first['validated'])
            assert all(v['installation_offset_mm']==-1 and abs(v['visible_z_mm']-(v['ceiling_z_mm']-1))<.001 for v in first['validated'])
            report['vertical_installation_offset_minus_one_mm']=True
            report['first_commit']=first
            versions={i:str(doc.GetElement(DB.ElementId(i)).VersionGuid) for i in ids};second=commit()
            assert ids=={v['id'] for v in second['validated']}
            assert versions=={i:str(doc.GetElement(DB.ElementId(i)).VersionGuid) for i in ids}
            report['rerun_without_duplicates_or_rewrites']=True
            t=model.terminal(p,tid);move=[t['x'],t['y']-15]
            if not model.validate_position(p,t,move)['valid']:move=[t['x']+15,t['y']]
            edit('move_ventilation_terminal',terminal_id=tid,position_mm=move,confirm_warnings=True);moved=commit()
            assert {v['id'] for v in moved['validated']}==ids;report['move_existing_preserves_ids']=True
            bedroom=next(r for r in p['analysis']['rooms'] if r['use']=='dormitorio')
            poly=polygon_from_rings(bedroom['rings']);cs=[dict(c,polygon=shape(c['geometry'])) for c in bedroom['ceilings']]
            obstacles=[dict(o,polygon=shape(o['geometry'])) for o in p['obstacles']]
            occupied=[(t['x'],t['y']) for t in model.active(p) if t['room_uid']==bedroom['uid']]
            candidate=optimize_terminal_positions(poly,cs,1,p['symbols']['impulsion']['dimensions'],p['settings'],bedroom['doors'],obstacles,occupied)[0]
            edit('add_ventilation_terminal',room_uid=bedroom['uid'],role='impulsion',position_mm=[candidate['x'],candidate['y']])
            new=next(t for t in p['terminals'] if t['terminal_id'].startswith('manual_'));new_tid=new['terminal_id'];added=commit()
            assert len(added['validated'])==len(ids)+1;new_id=model.terminal(p,new_tid)['existing_id']
            edit('delete_ventilation_terminal',terminal_id=new_tid)
            assert doc.GetElement(DB.ElementId(new_id)) is not None
            edit('rebalance_ventilation',confirmed=True);deleted=commit(True)
            assert new_id in deleted['deleted_ids'] and doc.GetElement(DB.ElementId(new_id)) is None
            report['staged_add_and_delete_verified']=True
            target=next(t for t in p['terminals'] if t['room_uid']==bedroom['uid']);lock_tid=target['terminal_id']
            edit('set_proposal_terminal_lock',terminal_id=lock_tid,lock_position=True,lock_flow=True);commit()
            metadata=ownership.read(doc.GetElement(DB.ElementId(model.terminal(p,lock_tid)['existing_id'])))
            assert metadata['lock_position'] and metadata['lock_flow'];report['independent_locks_persist']=True
            edit('set_proposal_terminal_lock',terminal_id=lock_tid,lock_position=False,lock_flow=False)
            mixed=next(r for r in p['analysis']['rooms'] if r['use']=='mixto');pair=[t['terminal_id'] for t in p['terminals'] if t['room_uid']==mixed['uid']]
            assert len(pair)==2
            for key in pair:
                role='extraccion' if model.terminal(p,key)['role']=='impulsion' else 'impulsion'
                edit('change_terminal_type',terminal_id=key,role=role,confirm_warnings=True)
                edit('set_terminal_flow',terminal_id=key,flow_lps=model.row(p,mixed['uid'],role)['design'])
            changed=commit()
            for key in pair:
                t=model.terminal(p,key);e=doc.GetElement(DB.ElementId(t['existing_id']))
                assert rr.eid(e.GetTypeId())==p['symbols'][t['role']]['id']
                assert ownership.read(e)['role']==t['role']
            report['type_change_on_existing_family_verified']=True
            t=model.terminal(p,tid);e=doc.GetElement(DB.ElementId(t['existing_id']))
            tx=DB.Transaction(doc,'Ensayo modificación externa de terminal propio');tx.Start();DB.ElementTransformUtils.MoveElement(doc,e.Id,DB.XYZ(rr.internal(10),0,0));tx.Commit()
            try:edit('approve_ventilation_proposal',confirmed=True)
            except ValueError as exc:assert 'OUTDATED' in str(exc);report['outdated_commit_blocked']=True
            else:raise AssertionError('No se detectó la revisión obsoleta.')
            tx=DB.Transaction(doc,'Restaurar movimiento externo de ensayo');tx.Start();DB.ElementTransformUtils.MoveElement(doc,e.Id,DB.XYZ(rr.internal(-10),0,0));tx.Commit()
            target=model.terminal(p,lock_tid);ceiling=doc.GetElement(DB.ElementId(target['ceiling_id']))
            offset=ceiling.get_Parameter(DB.BuiltInParameter.CEILING_HEIGHTABOVELEVEL_PARAM)
            assert offset is not None and not offset.IsReadOnly
            old_z=target['z'];old_xy=[target['x'],target['y']]
            tx=DB.Transaction(doc,'Ensayo cambio de altura de falso techo');tx.Start();offset.Set(offset.AsDouble()+rr.internal(20));tx.Commit()
            p=dispatch(doc,'refresh_ventilation_proposal',{'proposal_id':p['proposal_id'],'expected_version':p['version']})
            target=model.terminal(p,lock_tid);assert abs(target['z']-old_z-20)<.01
            assert [target['x'],target['y']]==old_xy
            commit();report['refresh_recomputes_height_preserving_xy']=True
            edit('set_proposal_terminal_lock',terminal_id=lock_tid,lock_position=True)
            old_z=model.terminal(p,lock_tid)['z']
            tx=DB.Transaction(doc,'Ensayo techo de terminal bloqueado');tx.Start();offset.Set(offset.AsDouble()+rr.internal(10));tx.Commit()
            p=dispatch(doc,'refresh_ventilation_proposal',{'proposal_id':p['proposal_id'],'expected_version':p['version']})
            assert model.terminal(p,lock_tid)['z']==old_z and not p['ready']
            assert any('bloqueada' in issue for issue in p['document_issues'])
            report['changed_ceiling_with_locked_position_blocks_commit']=True
    finally:store.ROOT=prior_root;group.RollBack()
    after={rr.eid(e.Id) for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType()}
    assert before==after;report['all_model_changes_rolled_back']=True
    return report
