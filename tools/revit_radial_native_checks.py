"""Mandatory native negative/movement tests; the group ALWAYS rolls back."""
from dataclasses import asdict
from pyrevit.api import DB
from System.Collections.Generic import List
from ARQT_MEP.routing.revit import extractor,proposals
from ARQT_MEP.routing.design import endpoints,native
from ARQT_MEP.routing import settings
from ARQT_MEP import revit_read as rr


def run_real_axis(doc,capture):
    """Exercise one captured real axis; always remove the temporary network."""
    from ARQT_MEP.routing.revit.geometry_conflicts import inspect
    before=rr.fingerprint(doc)
    route=next(r for r in capture['individual_routes'].values() if r['validation_status']=='GEOMETRICALLY_VALID')
    group=DB.TransactionGroup(doc,'ARQ-T · Eje real reversible');group.Start();tx=None
    result={'route_id':route['route_id'],'scope':'Single captured route; not full installation certification.'}
    try:
        tx=DB.Transaction(doc,'ARQ-T · Conexión temporal');tx.Start()
        catalog=extractor.get_mep_connection_points(doc,capture['configuration'])
        protected=native._terminal_state(catalog,capture['terminals'])
        record=native._create(doc,route,capture['configuration'])
        native._assert_terminal_state(protected,native._terminal_state(extractor.get_mep_connection_points(doc,capture['configuration']),capture['terminals']))
        conflicts=inspect(doc,record['ducts']+record['fittings'],capture['snapshot'],
            [route['source']['element_id'],route['destination']['element_id']],capture['configuration']['constraints'],
            route['passages'],route['clearance_mm'],record['connection_envelopes'])
        result.update(connected=True,curve_checks=record['curve_checks'],native_body_verified=not conflicts,conflicts=conflicts,terminals_preserved=True)
    except Exception as exc:result.update(connected=False,error=str(exc))
    finally:
        if tx and tx.GetStatus()==DB.TransactionStatus.Started:tx.RollBack()
        if group.GetStatus()==DB.TransactionStatus.Started:group.RollBack()
    assert before==rr.fingerprint(doc)
    result.update(fingerprint_unchanged=True,always_rolled_back=True)
    return result


def run(doc):
    before=rr.fingerprint(doc);cfg=settings.load();catalog=extractor.get_mep_connection_points(doc,cfg)
    selections=[{'terminal_id':t['id'],'network_type':t['suggested_network_type']} for t in catalog['terminals']]
    reports=endpoints.elbow_destinations(catalog,selections)
    tid=next(t for t,r in reports.items() if r['endpoints']);old=reports[tid]['endpoints'][0];report=reports[tid]
    group=DB.TransactionGroup(doc,'ARQ-T · Ensayos reversibles V3');group.Start();tx=None
    try:
        tx=DB.Transaction(doc,'ARQ-T · Conectores y curva temporal');tx.Start()
        ids=List[DB.ElementId]();ids.Add(doc.GetElement(tid).Id);ids.Add(DB.ElementId(report['elbow_element_id']))
        DB.ElementTransformUtils.MoveElements(doc,ids,DB.XYZ(rr.internal(.5),0,0));doc.Regenerate()
        updated=extractor.get_mep_connection_points(doc,cfg);fresh_report=endpoints.elbow_destinations(updated,selections)[tid]
        fresh=endpoints.resolve(old,[p for d in updated['devices'] if d['id']==old['owner_unique_id'] for p in d['connectors']])
        assert abs(fresh.origin_xyz_mm[0]-old['origin_xyz_mm'][0]-.5)<.01
        assert fresh.endpoint_id==old['endpoint_id'] and fresh_report['endpoints']
        flex=next(t for t in DB.FilteredElementCollector(doc).OfClass(DB.Mechanical.FlexDuctType) if rr.name(t)=='Flex - Redondo')
        system=next(t for t in DB.FilteredElementCollector(doc).OfClass(DB.Mechanical.MechanicalSystemType) if str(t.SystemClassification)=='SupplyAir')
        level=doc.GetElement(tid).LevelId
        if level==DB.ElementId.InvalidElementId:raise ValueError('Nivel del ensayo no identificado.')
        points=List[DB.XYZ]();origin=list(fresh.origin_xyz_mm);start=[origin[0],origin[1]+2000,origin[2]];end=[start[0]+2000,start[1],start[2]]
        points.Add(proposals.xyz(start));points.Add(proposals.xyz(end))
        duct=DB.Mechanical.FlexDuct.Create(doc,system.Id,flex.Id,level,DB.XYZ.BasisX,DB.XYZ.BasisX,points)
        duct.get_Parameter(DB.BuiltInParameter.RBS_CURVE_DIAMETER_PARAM).Set(rr.internal(90));doc.Regenerate()
        parallel_points=List[DB.XYZ]()
        for q in (start,end):parallel_points.Add(proposals.xyz([q[0],q[1]+200,q[2]]))
        parallel=DB.Mechanical.FlexDuct.Create(doc,system.Id,flex.Id,level,DB.XYZ.BasisX,DB.XYZ.BasisX,parallel_points)
        parallel.get_Parameter(DB.BuiltInParameter.RBS_CURVE_DIAMETER_PARAM).Set(rr.internal(90));doc.Regenerate()
        bodies=[{'ducts':[element],'fittings':[]} for element in (duct,parallel)]
        routes=[{'route_id':rid,'clearance_mm':10} for rid in ('test-a','test-b')]
        joint=native._joint_bodies(bodies,routes);assert joint['verified']
        DB.ElementTransformUtils.MoveElement(doc,parallel.Id,DB.XYZ(0,rr.internal(-200),0));doc.Regenerate()
        try:native._joint_bodies(bodies,routes)
        except ValueError:collision_rejected=True
        else:raise AssertionError('Native body overlap was accepted.')
        ports=list(duct.ConnectorManager.Connectors)
        source=next(d for d in updated['devices'] if d.get('is_configured_distributor'))['connectors'][0]
        destination=fresh_report['ports'][fresh_report['endpoints'][0]['endpoint_id']]
        manifest=DB.ExtensibleStorage.DataStorage.Create(doc)
        proposals._tag(manifest,{'kind':native.KIND,'proposal_id':'NATIVE_NEGATIVE_TEST','route_id':'unconnected-test',
            'source':source,'destination':destination,'end_element_ids':{'source':rr.eid(duct.Id),'destination':rr.eid(duct.Id)},
            'end_ids':{'source':int(ports[0].Id),'destination':int(ports[1].Id)},'members':[rr.eid(duct.Id)]})
        audit=native.audit(doc,'NATIVE_NEGATIVE_TEST');assert not audit['native_connections_valid'] and audit['failures']
        assessment=native._curve_check(duct,{'primitives':[{'points_mm':[start,end]}],'bend_radius_mm':150},5)
        assert assessment['verified'],assessment
        result={'physical_endpoint_refresh':True,'signature_survives_move':True,'native_curve_assessment':assessment,
            'disconnected_curve_rejected':True,'joint_bodies':joint,'native_body_collision_rejected':collision_rejected,
            'audit':audit,'scope':'Connector, actual body pair and straight native curve tests; not full installation certification.'}
        tx.Commit();group.RollBack()
        assert rr.fingerprint(doc)==before
        result.update(fingerprint_unchanged=True,always_rolled_back=True)
        return result
    finally:
        if tx and tx.GetStatus()==DB.TransactionStatus.Started:tx.RollBack()
        if group.GetStatus()==DB.TransactionStatus.Started:group.RollBack()
