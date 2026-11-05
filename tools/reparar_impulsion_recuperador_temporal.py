# -*- coding: utf-8 -*-
"""Correccion de impulsion en etapas. No cambia botones, rutas ni caudales de extraccion."""
from pyrevit.api import DB
import revit_document_utils as utils


def ports(element):
    cm=element.ConnectorManager if isinstance(element,DB.MEPCurve) else element.MEPModel.ConnectorManager
    return [c for c in cm.Connectors if c.Domain==DB.Domain.DomainHvac and c.ConnectorType==DB.ConnectorType.End]


def snapshot(project):
    queue=[3199617,3229184,3353026]
    visited=set()
    rows=[]
    while queue:
        ident=queue.pop(0)
        if ident in visited:continue
        visited.add(ident)
        assert len(visited)<=100
        element=project.GetElement(DB.ElementId(ident))
        assert element is not None
        for c in ports(element):
            links=[]
            for ref in c.AllRefs:
                if ref.Owner.Id!=element.Id and ref.ConnectorType==DB.ConnectorType.End and c.IsConnectedTo(ref):
                    links.append((int(ref.Owner.Id.Value),int(ref.Id)))
                    queue.append(int(ref.Owner.Id.Value))
            rows.append({'element':ident,'port':int(c.Id),'links':sorted(links),
                         'flow':c.Flow,'origin':c.Origin,'radius':c.Radius})
    return sorted(rows,key=lambda row:(row['element'],row['port']))


def correct_directions(fd):
    connectors=list(DB.FilteredElementCollector(fd).OfClass(DB.ConnectorElement))
    assert len(connectors)==6 and sum(1 for c in connectors if c.IsPrimary)==1
    changes=[]
    for ce in connectors:
        parameter=ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_DIRECTION_PARAM)
        desired=1 if ce.IsPrimary else 2
        if parameter.AsInteger()!=desired:
            changes.append({'id':int(ce.Id.Value),'from':parameter.AsInteger(),'to':desired})
            parameter.Set(desired)
    return changes


assert not doc.IsFamilyDocument and doc.Title=='Proyecto'
before=snapshot(doc)
stage=inputs['stage']
equipment=doc.GetElement(DB.ElementId(3199617))
extraction_before=next(c for c in ports(equipment) if c.Id==3).Flow
terminal=doc.GetElement(DB.ElementId(3353026))
design_before=terminal.LookupParameter('Flow').AsDouble()
target=next(c for c in ports(terminal)).MEPSystem
assert target is not None and doc.GetElement(target.GetTypeId()).SystemClassification==DB.MEPSystemClassification.SupplyAir
family_changes=[]
if stage in ['family','combined']:
    operation=utils.edit_and_reload_family(uiapp,doc,3229184,correct_directions)
    family_changes=operation['details']
if stage in ['system','combined']:
    t=DB.Transaction(doc,'Asignar recuperador como equipo base de impulsion')
    t.Start()
    outlet=next(c for c in ports(equipment) if c.Id==1)
    if target.BaseEquipment is None:
        if outlet.MEPSystem is not None:
            cs=DB.ConnectorSet()
            cs.Insert(outlet)
            outlet.MEPSystem.Remove(cs)
            doc.Regenerate()
            outlet=next(c for c in ports(equipment) if c.Id==1)
        target.BaseEquipmentConnector=outlet
    else:
        assert target.BaseEquipment.Id==equipment.Id
    doc.Regenerate()
    t.Commit()
after=snapshot(doc)
assert [(r['element'],r['port'],r['links']) for r in before]==[(r['element'],r['port'],r['links']) for r in after], 'Cambio de conexiones; revertir'
for previous,current in zip(before,after):
    assert previous['origin'].DistanceTo(current['origin'])<1e-7 and abs(previous['radius']-current['radius'])<1e-8, 'Cambio geometrico; revertir'
assert abs(next(c for c in ports(equipment) if c.Id==3).Flow-extraction_before)<1e-8
assert abs(terminal.LookupParameter('Flow').AsDouble()-design_before)<1e-8
flows=[]
for ident in [3199617,3229184,3353026]:
    for c in ports(doc.GetElement(DB.ElementId(ident))):
        flows.append({'element':ident,'port':int(c.Id),'direction':str(c.Direction),'lps':DB.UnitUtils.ConvertFromInternalUnits(c.Flow,DB.UnitTypeId.LitersPerSecond)})
outlet=next(c for c in ports(equipment) if c.Id==1)
if inputs.get('require_flow'):
    assert abs(outlet.Flow-design_before)<1e-8, 'La impulsion no transmite el caudal completo; revertir'
result={'stage':stage,'family_changes':family_changes,'connections_and_geometry_preserved':True,
        'extraction_unchanged':True,'base':int(target.BaseEquipment.Id.Value) if target.BaseEquipment else None,
        'system':int(target.Id.Value),'flows':flows}
if inputs.get('test_delta_lps'):
    delta=DB.UnitUtils.ConvertToInternalUnits(float(inputs['test_delta_lps']),DB.UnitTypeId.LitersPerSecond)
    t=DB.Transaction(doc,'Prueba reversible de respuesta de caudal de impulsion')
    t.Start()
    terminal.LookupParameter('Flow').Set(design_before+delta)
    doc.Regenerate()
    t.Commit()
    response=next(c for c in ports(equipment) if c.Id==1).Flow
    assert abs(response-design_before-delta)<1e-8
    assert abs(next(c for c in ports(equipment) if c.Id==3).Flow-extraction_before)<1e-8
    result['test_response_lps']=DB.UnitUtils.ConvertFromInternalUnits(response,DB.UnitTypeId.LitersPerSecond)
