# -*- coding: utf-8 -*-
"""Variantes de conectores en copia temporal; usar primero dry_run MCP."""
from pyrevit.api import DB
import revit_document_utils as utils


def snapshot(project):
    data=[]
    for ident in [3199617,3228892,3230469]:
        e=project.GetElement(DB.ElementId(ident))
        for c in e.MEPModel.ConnectorManager.Connectors:
            if c.Domain != DB.Domain.DomainHvac: continue
            links=[]
            for r in c.AllRefs:
                if r.Owner.Id!=e.Id and r.ConnectorType==DB.ConnectorType.End and c.IsConnectedTo(r):
                    links.append((int(r.Owner.Id.Value),int(r.Id)))
            data.append({'element':ident,'port':int(c.Id),'links':sorted(links),
                'flow':DB.UnitUtils.ConvertFromInternalUnits(c.Flow,DB.UnitTypeId.LitersPerSecond)})
    return sorted(data,key=lambda x:(x['element'],x['port']))


def editor(fd):
    connectors=list(DB.FilteredElementCollector(fd).OfClass(DB.ConnectorElement))
    main=next(c for c in connectors if c.IsPrimary)
    mode=inputs['mode']
    for ce in connectors:
        cls=29 if mode.startswith('global') else 28
        ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_CONNECTOR_SYSTEM_CLASSIFICATION_PARAM).Set(cls)
        if mode=='fitting_bidirectional':
            ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_DIRECTION_PARAM).Set(0)
        if cls==29:
            ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_CONFIGURATION_PARAM).Set(0)
            ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_DIRECTION_PARAM).Set(0)
    if mode.endswith('linked'):
        for ce in connectors:
            if ce.Id!=main.Id: ce.SetLinkedConnectorElement(main)
    if mode=='global_pair':
        instance=doc.GetElement(DB.ElementId(3230469))
        branch=next(c for c in instance.MEPModel.ConnectorManager.Connectors if c.Id==8)
        point=instance.GetTransform().Inverse.OfPoint(branch.Origin)
        selected=min((ce for ce in connectors if ce.Id!=main.Id),key=lambda ce:ce.Origin.DistanceTo(point))
        assert selected.Origin.DistanceTo(point)<0.001
        selected.SetLinkedConnectorElement(main)
    if mode=='global_sum':
        fm=fd.FamilyManager
        names=[]
        for index,ce in enumerate(c for c in connectors if c.Id!=main.Id):
            name='ARQT_Caudal_Ramal_{0:02d}'.format(index+1)
            fp=fm.get_Parameter(name)
            if fp is None:fp=fm.AddParameter(name,DB.GroupTypeId.Mechanical,DB.SpecTypeId.AirFlow,True)
            fm.AssociateElementParameterToFamilyParameter(ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_PARAM),fp)
            names.append(name)
        total=fm.get_Parameter('ARQT_Caudal_Total')
        if total is None:total=fm.AddParameter('ARQT_Caudal_Total',DB.GroupTypeId.Mechanical,DB.SpecTypeId.AirFlow,True)
        fm.SetFormula(total,' + '.join(names))
        main.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_CONFIGURATION_PARAM).Set(1)
        fm.AssociateElementParameterToFamilyParameter(main.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_PARAM),total)
    if mode=='global_transfer':
        fm=fd.FamilyManager
        total=fm.get_Parameter('ARQT_Caudal_Transferido')
        if total is None:total=fm.AddParameter('ARQT_Caudal_Transferido',DB.GroupTypeId.Mechanical,DB.SpecTypeId.AirFlow,True)
        main.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_CONFIGURATION_PARAM).Set(1)
        fm.AssociateElementParameterToFamilyParameter(main.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_PARAM),total)
    if mode in ['fitting_two','fitting_two_multi']:
        instance=doc.GetElement(DB.ElementId(3230469))
        branch=next(c for c in instance.MEPModel.ConnectorManager.Connectors if c.Id==8)
        point=instance.GetTransform().Inverse.OfPoint(branch.Origin)
        selected=min((ce for ce in connectors if ce.Id!=main.Id),key=lambda ce:ce.Origin.DistanceTo(point))
        assert selected.Origin.DistanceTo(point)<0.001
        for ce in connectors:
            if ce.Id!=main.Id and ce.Id!=selected.Id:fd.Delete(ce.Id)
        if mode=='fitting_two':
            fd.OwnerFamily.get_Parameter(DB.BuiltInParameter.FAMILY_CONTENT_PART_TYPE).Set(int(DB.PartType.Transition))
        connectors=list(DB.FilteredElementCollector(fd).OfClass(DB.ConnectorElement))
    if mode=='fitting_all_transition':
        fd.OwnerFamily.get_Parameter(DB.BuiltInParameter.FAMILY_CONTENT_PART_TYPE).Set(int(DB.PartType.Transition))
    if mode=='fitting_active_face':
        instance=doc.GetElement(DB.ElementId(3230469))
        branch=next(c for c in instance.MEPModel.ConnectorManager.Connectors if c.Id==8)
        point=instance.GetTransform().Inverse.OfPoint(branch.Origin)
        selected=min((ce for ce in connectors if ce.Id!=main.Id),key=lambda ce:ce.Origin.DistanceTo(point))
        assert selected.Origin.DistanceTo(point)<0.001
        normal=selected.Direction.Normalize()
        for ce in connectors:
            if ce.Id!=main.Id and ce.Direction.Normalize().DotProduct(normal)<0.99:fd.Delete(ce.Id)
        connectors=list(DB.FilteredElementCollector(fd).OfClass(DB.ConnectorElement))
    return [{'id':int(ce.Id.Value),'linked':int(ce.GetLinkedConnectorElement().Id.Value) if ce.GetLinkedConnectorElement() else None}
            for ce in connectors]


assert not doc.IsFamilyDocument
before=snapshot(doc)
operation=utils.edit_and_reload_family(uiapp,doc,3230469,editor)
if inputs['mode']=='global_transfer':
    instance=doc.GetElement(DB.ElementId(3230469))
    terminal=doc.GetElement(DB.ElementId(3228892))
    value=terminal.LookupParameter('Flow').AsDouble()
    transaction=DB.Transaction(doc,'Prueba transferencia derivada del terminal conectado')
    transaction.Start()
    instance.LookupParameter('ARQT_Caudal_Transferido').Set(value)
    doc.Regenerate()
    transaction.Commit()
after=snapshot(doc)
assert sorted((x['element'],x['port'],link) for x in before for link in x['links'])==sorted((x['element'],x['port'],link) for x in after for link in x['links'])
result={'mode':inputs['mode'],'links':operation['details'],'connections_preserved':True,'flows':after}
if inputs['mode']=='global_sum':
    result['parameters']=[{'name':p.Definition.Name,'value':p.AsDouble()} for p in doc.GetElement(DB.ElementId(3230469)).Parameters if p.Definition.Name.startswith('ARQT_Caudal')]
