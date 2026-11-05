"""Reversible MCP integration fixture: real duct connectors, real native geometry.

Run with run_modify(dry_run=True). Temporary stubs are test endpoints, not
distributors or a completed residential circuit. No production flows are changed.
"""
from pyrevit.api import DB
from ARQT_MEP.routing import settings,service
from ARQT_MEP.routing.revit import extractor,proposals,network_model as nm
from ARQT_MEP import revit_read as rr

before=set(rr.eid(e.Id) for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType())
group=DB.TransactionGroup(doc,'Ensayo reversible de modelado HRV');group.Start()
try:
    hrv=next(e for e in DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance) if 'recuperador' in e.Symbol.FamilyName.casefold())
    rigid=next(t for t in DB.FilteredElementCollector(doc).OfClass(DB.Mechanical.DuctType) if str(t.Shape)=='Round')
    system=next(t for t in DB.FilteredElementCollector(doc).OfClass(DB.Mechanical.MechanicalSystemType) if str(t.SystemClassification)=='SupplyAir')
    level=doc.GetElement(hrv.LevelId)
    if level is None:level=next(iter(DB.FilteredElementCollector(doc).OfClass(DB.Level)))
    tx=DB.Transaction(doc,'Crear extremos ficticios de ensayo');tx.Start()
    a=DB.Mechanical.Duct.Create(doc,system.Id,rigid.Id,level.Id,proposals.xyz([100000,100000,10000]),proposals.xyz([101000,100000,10000]))
    b=DB.Mechanical.Duct.Create(doc,system.Id,rigid.Id,level.Id,proposals.xyz([104000,100000,10000]),proposals.xyz([105000,100000,10000]))
    for e in (a,b):e.get_Parameter(DB.BuiltInParameter.RBS_CURVE_DIAMETER_PARAM).Set(rr.internal(125))
    doc.Regenerate();tx.Commit()
    ca=max(a.ConnectorManager.Connectors,key=lambda c:c.Origin.X)
    cb=min(b.ConnectorManager.Connectors,key=lambda c:c.Origin.X)
    def port(e,c):
        p=c.Origin;n=c.CoordinateSystem.BasisZ
        return dict(id=e.UniqueId+':connector:'+str(c.Id),element_key=e.UniqueId,element_id=rr.eid(e.Id),
             connector_id=int(c.Id),xyz_mm=[rr.mm(p.X),rr.mm(p.Y),rr.mm(p.Z)],direction=[n.X,n.Y,n.Z],
             connected=False,provenance='REVIT_CONNECTOR',size={'shape':'Round','diameter_mm':125})
    pa,pb=port(a,ca),port(b,cb)
    config=settings.validate({'hrv_id':hrv.UniqueId,'connection_straight_mm':150,
            'constraints':{'allowed_regions':[{'id':'test','kind':'EXPLICIT','box_mm':[99000,99000,9000,106000,101000,11000]}]}})
    snapshot=extractor.analyze_mep_geometry(doc,config)
    catalog={'devices':[{'id':'fixture','connectors':[pa,pb]}],'terminals':[]}
    p=service.create_mep_route_proposal(snapshot,catalog,
       [{'port_id':pa['id'],'network_type':'SUPPLY','kind':'DISTRIBUTOR'}],
       [{'port_id':pb['id'],'network_type':'SUPPLY','airflow_lps':8}],config)
    p=proposals.validate_mep_routes(doc,snapshot,p)
    assert p['routes'][0]['native_status'] in ('VALID','VALID_WITH_WARNINGS'),p['routes'][0]
    p['routes'][0]['approval_status']='APPROVED';p=service.seal(p)
    modeled=nm.model(doc,p,True)
    assert len(modeled['created'])==1,modeled
    assert modeled['counts']['CONECTADO']==2,modeled
    duplicate=nm.model(doc,p,True)
    assert not duplicate['created'],duplicate
    duct=doc.GetElement(DB.ElementId(modeled['created'][0]['element_id']))
    data=proposals.metadata(duct)
    last=next(c for c in duct.ConnectorManager.Connectors if c.Id==data['end_ids']['destination'])
    tx=DB.Transaction(doc,'Simular desconexion');tx.Start();last.DisconnectFrom(cb);doc.Regenerate();tx.Commit()
    pending=nm.audit(doc,p)
    assert pending['counts']['CONDUCTO_SIN_CONECTAR']==1,pending
    assert pending['counts']['CONECTADO']==1,pending
    assert pending['counts'].get('TOMA_SIN_USAR',0)>0,pending
    tx=DB.Transaction(doc,'Simular conexion equivocada');tx.Start()
    origin=last.Origin
    other=DB.Mechanical.Duct.Create(doc,system.Id,rigid.Id,level.Id,origin,origin.Add(DB.XYZ(5,0,0)))
    other.get_Parameter(DB.BuiltInParameter.RBS_CURVE_DIAMETER_PARAM).Set(rr.internal(125));doc.Regenerate()
    wrong_target=min(other.ConnectorManager.Connectors,key=lambda c:c.Origin.DistanceTo(origin))
    last.ConnectTo(wrong_target);doc.Regenerate()
    assert tx.Commit()==DB.TransactionStatus.Committed
    wrong=nm.audit(doc,p)
    assert wrong['counts'].get('CONEXION_INCORRECTA',0)>=1,wrong
    tx=DB.Transaction(doc,'Ensayar principal rigida con codo');tx.Start()
    rigid_segments,joints,rigid_errors=nm._rigid(doc,{'points_mm':[[110000,100000,10000],[111000,100000,10000],[111000,101000,10000]]},config,system.Id,level.Id,125)
    assert len(rigid_segments)==2 and len(joints)==1
    tx.RollBack()
    result={'native_modeling':True,'created_count':len(modeled['created']),'both_ends_connected':True,
            'repeat_does_not_duplicate':True,'disconnected_endpoint_identified':True,
            'wrong_connection_identified':True,'unused_ports_separate':True,
            'rigid_segments_created':len(rigid_segments),'rigid_joint_errors':rigid_errors,
            'fixture':'temporary rigid stubs, flexible branch'}
finally:group.RollBack()
assert before==set(rr.eid(e.Id) for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType())
result['original_elements_preserved']=True
