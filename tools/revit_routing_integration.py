"""Native integration fixture: run_modify(dry_run=True); every change also grouped and rolled back."""
import copy
from pyrevit.api import DB
from ARQT_MEP.routing import settings,mcp
from ARQT_MEP.routing.revit import extractor,proposals
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP import surface,revit_read as rr


def run(doc,test_terminals=False):
    before={rr.eid(e.Id) for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType()}
    configuration=settings.validate({'search':{'max_seconds':5,'max_total_seconds':20}})
    snapshot=extractor.analyze_mep_geometry(doc,configuration);space=build_spatial_model(snapshot)
    fixture=None
    for region in space.regions:
        p=region.polygon.representative_point();x,y=p.x,p.y
        z=(surface.height(region.lower,x,y)+surface.height(region.upper,x,y))/2
        for dx,dy in ((500,0),(0,500),(-500,0),(0,-500)):
            a=[x,y,z];b=[x+dx,y+dy,z]
            if space.free(a,b,77.5):fixture={'start':a,'end':b,'derived_from_region':region.data['id']};break
        if fixture:break
    if fixture is None:raise ValueError('No se encontró una envolvente libre real para el ensayo.')
    group=DB.TransactionGroup(doc,'ARQ-T | Ensayo trazado MEP revertido');group.Start()
    report={'geometry':snapshot['stats'],'manual_design_fixture':fixture,'native_bim_distributors_tested':False}
    try:
        payload={'configuration':configuration,'snapshot':snapshot,
            'origins':[{'id':'TEST-MANUAL-DISTRIBUTOR','xyz_mm':fixture['start'],'network_type':'SUPPLY','kind':'DISTRIBUTOR'}],
            'destinations':[{'id':'TEST-MANUAL-TERMINAL','xyz_mm':fixture['end'],'network_type':'SUPPLY','airflow_lps':8}]}
        proposal=mcp.dispatch(doc,'create_mep_route_proposal',payload)
        assert proposal['summary']['valid']==1,proposal['routes']
        assert proposal['routes'][0]['native_status']=='VALID_WITH_WARNINGS'
        report['calculation']=proposal['summary'];report['native_swept_validation']=proposal['routes'][0]['native_checks']
        proposal=mcp.dispatch(doc,'approve_mep_route',{'proposal':proposal,'route_id':proposal['routes'][0]['route_id'],'explicit':True})
        assert proposal['status']=='APPROVED'
        preview=mcp.dispatch(doc,'preview_mep_routes',{'proposal':proposal,'explicit':True},allow_write=True)
        assert preview['preview_ids']
        old_ids=preview['preview_ids'];preview=mcp.dispatch(doc,'preview_mep_routes',{'proposal':proposal,'explicit':True},allow_write=True)
        assert all(doc.GetElement(DB.ElementId(i)) is None for i in old_ids)
        assert len(preview['preview_ids'])==len(old_ids)
        saved=mcp.dispatch(doc,'save_mep_route_proposal',{'proposal':proposal,'explicit':True},allow_write=True)
        again=mcp.dispatch(doc,'save_mep_route_proposal',{'proposal':proposal,'explicit':True},allow_write=True)
        assert saved['element_id']==again['element_id']
        recovered=mcp.dispatch(doc,'get_mep_route_proposal',{'proposal_id':proposal['proposal_id']})
        assert len(recovered)==1 and recovered[0]['status']=='APPROVED'
        removed=mcp.dispatch(doc,'hide_mep_routes',{'proposal_id':proposal['proposal_id'],'explicit':True},allow_write=True)
        assert all(doc.GetElement(DB.ElementId(i)) is None for i in preview['preview_ids'])
        assert not any((proposals.metadata(e) or {}).get('kind') in ('ROUTE_PREVIEW','ROUTE_SKETCHPLANE') for e in DB.FilteredElementCollector(doc).OfClass(DB.CurveElement))
        report.update(preview_curves=len(old_ids),preview_replaced_without_duplicates=True,
            preview_cleanup=True,storage_upsert_id=saved['element_id'],storage_recovery=True,approval=True)
        if test_terminals:
            from ARQT_MEP import service as terminals_service,config,revit_write
            c=copy.deepcopy(config.DEFAULT);analysis=terminals_service.analyze_ventilation_rooms(doc,c)
            c['phase_id']=analysis['phase_id'];uids=[r['uid'] for r in analysis['rooms']]
            terminal_proposal=terminals_service.propose_ventilation_terminals(doc,uids,c)
            assert terminal_proposal['ready'],terminal_proposal['issues']
            placed=revit_write.create_ventilation_terminals(doc,terminal_proposal,approved=True)
            catalog=extractor.get_mep_connection_points(doc,configuration)
            actual={t['element_id']:t for t in catalog['terminals']}
            assert placed['validated']
            for row in placed['validated']:
                assert row['id'] in actual and actual[row['id']]['airflow_lps']>0
                assert actual[row['id']]['connectors']
            report['native_terminal_flow_reading']=[{'id':i,'flow_lps':t['airflow_lps'],'connectors':len(t['connectors'])} for i,t in actual.items()]
            try:mcp.dispatch(doc,'approve_mep_route',{'proposal':proposal,'route_id':proposal['routes'][0]['route_id'],'explicit':True})
            except ValueError as exc:
                assert 'OUTDATED' in str(exc);report['changed_model_blocks_approval']=True
            else:raise AssertionError('La propuesta antigua no fue invalidada.')
    finally:group.RollBack()
    after={rr.eid(e.Id) for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType()}
    assert before==after
    report['all_model_changes_rolled_back']=True
    return report
