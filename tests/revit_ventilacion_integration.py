"""Run ONLY with pyRevit MCP run_modify(dry_run=True), in the project context.

The inner group rolls back even if assertions fail; the MCP dry run also rolls back.
"""
import copy
from pyrevit.api import DB
from ARQT_MEP import service,config,ownership,revit_write,revit_read
from ARQT_MEP.airflow import classify_room


def run(doc,test_main_room_uid=None,room_uids=None,scope=None):
    settings=copy.deepcopy(config.DEFAULT)
    analysis=service.analyze_ventilation_rooms(doc,settings)
    if 'phase_id' not in analysis:
        raise ValueError('La prueba requiere elegir explícitamente una fase única.')
    settings['phase_id']=analysis['phase_id']
    rooms=[r for r in analysis['rooms'] if room_uids is None or r['uid'] in room_uids]
    uids=[r['uid'] for r in rooms]
    before_ids={revit_read.eid(e.Id) for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType()}
    group=DB.TransactionGroup(doc,'ARQ-T | Ensayo ventilación revertido');group.Start()
    report={'no_main_required':True,'production_classification_saved':False}
    try:
        p=service.propose_ventilation_terminals(doc,uids,settings,scope)
        if not p['ready']:
            raise ValueError('Fixture no colocable: '+str(p['issues']))
        first=revit_write.create_ventilation_terminals(doc,p,approved=True)
        ids=[v['id'] for v in first['validated']]
        assert len(ids)==p['expected_count']
        assert all(v['valid'] for v in first['validated'])
        report['placement']=first
        second_proposal=service.propose_ventilation_terminals(doc,uids,settings,scope)
        assert second_proposal['ready'],second_proposal['issues']
        assert all(a['action']=='mantener' for a in second_proposal['actions'])
        original_versions={i:str(doc.GetElement(DB.ElementId(i)).VersionGuid) for i in ids}
        second=revit_write.create_ventilation_terminals(doc,second_proposal,approved=True)
        assert {v['id'] for v in second['validated']}==set(ids)
        assert all(str(doc.GetElement(DB.ElementId(i)).VersionGuid)==original_versions[i] for i in ids)
        report['idempotent']=True
        validation=revit_write.validate_ventilation_terminals(doc,uids)
        assert validation['valid'],validation['issues']
        assert validation['count']==len(ids)
        report['validation']=validation
        revit_write.set_terminal_lock(doc,ids[0],True,approved=True)
        locked=service.propose_ventilation_terminals(doc,uids,settings,scope)
        assert locked['ready']
        assert any(a['id']==ids[0] and a['action']=='mantener protegido' for a in locked['actions'])
        locked_version=str(doc.GetElement(DB.ElementId(ids[0])).VersionGuid)
        revit_write.create_ventilation_terminals(doc,locked,approved=True)
        assert str(doc.GetElement(DB.ElementId(ids[0])).VersionGuid)==locked_version
        assert doc.GetElement(DB.ElementId(ids[0])) is not None
        revit_write.set_terminal_lock(doc,ids[0],False,approved=True)
        report['locked_protected']=True
        t=DB.Transaction(doc,'Test fijación');t.Start();doc.GetElement(DB.ElementId(ids[0])).Pinned=True;t.Commit()
        pinned=service.propose_ventilation_terminals(doc,uids,settings,scope)
        assert pinned['ready']
        assert any(a['id']==ids[0] and a['action']=='mantener protegido' for a in pinned['actions'])
        t=DB.Transaction(doc,'Test desfijación');t.Start();doc.GetElement(DB.ElementId(ids[0])).Pinned=False;t.Commit()
        report['pinned_protected']=True
        cancel_settings=copy.deepcopy(settings)
        first_terminal=p['terminals'][0]
        role_key=first_terminal['room_uid']+':'+first_terminal['role']
        cancel_settings['overrides'][role_key]={'count':2}
        cancel_proposal=service.propose_ventilation_terminals(doc,uids,cancel_settings,scope)
        before_meta={e['id']:e['metadata'] for e in ownership.inventory(doc) if e['own']}
        calls=[0]
        def cancelled():
            calls[0]+=1
            return calls[0]>=2
        try:
            revit_write.create_ventilation_terminals(doc,cancel_proposal,True,cancelled=cancelled)
            raise AssertionError('La cancelación no se procesó.')
        except InterruptedError:
            pass
        after_meta={e['id']:e['metadata'] for e in ownership.inventory(doc) if e['own']}
        assert before_meta==after_meta
        report['cancelled_without_residue']=True
        t=DB.Transaction(doc,'Test inclinación manual');t.Start()
        e=doc.GetElement(DB.ElementId(ids[0]));pivot=e.Location.Point
        import math
        DB.ElementTransformUtils.RotateElement(doc,e.Id,DB.Line.CreateUnbound(pivot,DB.XYZ.BasisX),math.radians(5))
        t.Commit()
        tilted=service.propose_ventilation_terminals(doc,uids,settings,scope)
        assert not tilted['ready']
        report['manual_tilt_protected']=True
        t=DB.Transaction(doc,'Restaurar inclinación de prueba');t.Start()
        DB.ElementTransformUtils.RotateElement(doc,e.Id,DB.Line.CreateUnbound(pivot,DB.XYZ.BasisX),-math.radians(5))
        t.Commit()
        t=DB.Transaction(doc,'Test cambio manual');t.Start()
        DB.ElementTransformUtils.MoveElement(doc,DB.ElementId(ids[0]),DB.XYZ(revit_read.internal(100),0,0));t.Commit()
        changed=service.propose_ventilation_terminals(doc,uids,settings,scope)
        assert not changed['ready']
        report['manual_move_protected']=True
    finally:
        group.RollBack()
    after_ids={revit_read.eid(e.Id) for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType()}
    assert before_ids==after_ids
    report['all_model_changes_rolled_back']=True
    return report
