# -*- coding: utf-8 -*-
"""Cierre de tomas libres con tapones nativos. Verificar en dry_run antes de aplicar."""
from pyrevit.api import DB
from pyrevit.revit.db.create import load_family_with_result
import math

assert inputs.get('diagnostic_only') is True or inputs.get('apply_verified') is True
assert not doc.IsFamilyDocument
existing_links=[]
for ident in [3199617,3228892,3230469]:
    element=doc.GetElement(DB.ElementId(ident))
    for c in element.MEPModel.ConnectorManager.Connectors:
        for ref in c.AllRefs:
            if ref.Owner.Id!=element.Id and ref.ConnectorType==DB.ConnectorType.End and c.IsConnectedTo(ref):
                existing_links.append((ident,int(c.Id),int(ref.Owner.Id.Value),int(ref.Id)))
path=r'C:\ProgramData\Autodesk\RVT 2024\Libraries\Spanish_INTL\Conducto\Uniones\Redondo\Tapones\M_Remate redondo - DTL.rfa'
t=DB.Transaction(doc,'Cerrar tomas libres del distribuidor con tapones nativos')
t.Start()
loaded,symbols=load_family_with_result(path,doc=doc)
assert symbols
symbol=symbols[0]
if not symbol.IsActive:symbol.Activate()
doc.Regenerate()
instance=doc.GetElement(DB.ElementId(3230469))
free=[c for c in instance.MEPModel.ConnectorManager.Connectors if c.ConnectorType==DB.ConnectorType.End and not c.IsConnected]
caps=[]
for source in free:
    cap=doc.Create.NewFamilyInstance(source.Origin,symbol,DB.Structure.StructuralType.NonStructural)
    doc.Regenerate()
    connector=next(c for c in cap.MEPModel.ConnectorManager.Connectors if c.ConnectorType==DB.ConnectorType.End)
    connector.Radius=source.Radius
    doc.Regenerate()
    connector=next(c for c in cap.MEPModel.ConnectorManager.Connectors if c.ConnectorType==DB.ConnectorType.End)
    direction=connector.CoordinateSystem.BasisZ.Normalize()
    target=source.CoordinateSystem.BasisZ.Negate().Normalize()
    axis=direction.CrossProduct(target)
    dot=max(-1.0,min(1.0,direction.DotProduct(target)))
    if axis.GetLength()>1e-8:
        DB.ElementTransformUtils.RotateElement(doc,cap.Id,DB.Line.CreateUnbound(connector.Origin,axis.Normalize()),math.acos(dot))
    elif dot<0:
        perpendicular=direction.CrossProduct(DB.XYZ.BasisX)
        if perpendicular.GetLength()<1e-8:perpendicular=direction.CrossProduct(DB.XYZ.BasisY)
        DB.ElementTransformUtils.RotateElement(doc,cap.Id,DB.Line.CreateUnbound(connector.Origin,perpendicular.Normalize()),math.pi)
    doc.Regenerate()
    connector=next(c for c in cap.MEPModel.ConnectorManager.Connectors if c.ConnectorType==DB.ConnectorType.End)
    DB.ElementTransformUtils.MoveElement(doc,cap.Id,source.Origin-connector.Origin)
    doc.Regenerate()
    connector=next(c for c in cap.MEPModel.ConnectorManager.Connectors if c.ConnectorType==DB.ConnectorType.End)
    connector.ConnectTo(source)
    caps.append(int(cap.Id.Value))
doc.Regenerate()
baseline=next(c for c in doc.GetElement(DB.ElementId(3199617)).MEPModel.ConnectorManager.Connectors if c.Id==3).Flow
if inputs.get('test_delta_lps'):
    p=doc.GetElement(DB.ElementId(3228892)).LookupParameter('Flow')
    p.Set(p.AsDouble()+DB.UnitUtils.ConvertToInternalUnits(float(inputs['test_delta_lps']),DB.UnitTypeId.LitersPerSecond))
    doc.Regenerate()
t.Commit()
expected=doc.GetElement(DB.ElementId(3228892)).LookupParameter('Flow').AsDouble()
actual=next(c for c in doc.GetElement(DB.ElementId(3199617)).MEPModel.ConnectorManager.Connectors if c.Id==3).Flow
assert abs(actual-expected)<1e-8, 'El caudal no llega integro al recuperador; revertir'
for owner,port,other,other_port in existing_links:
    c=next(c for c in doc.GetElement(DB.ElementId(owner)).MEPModel.ConnectorManager.Connectors if c.Id==port)
    other_element=doc.GetElement(DB.ElementId(other))
    other_manager=other_element.ConnectorManager if isinstance(other_element,DB.MEPCurve) else other_element.MEPModel.ConnectorManager
    ref=next(c for c in other_manager.Connectors if c.Id==other_port)
    assert c.IsConnectedTo(ref), 'Una conexion original ha cambiado; revertir'
result={'caps':caps,'baseline_hrv_lps':DB.UnitUtils.ConvertFromInternalUnits(baseline,DB.UnitTypeId.LitersPerSecond),'ports':[]}
system=next(c for c in doc.GetElement(DB.ElementId(3199617)).MEPModel.ConnectorManager.Connectors if c.Id==3).MEPSystem
result['system']={'valid':system.IsValid,'well_connected':system.IsWellConnected,'base':int(system.BaseEquipment.Id.Value),'flow_lps':DB.UnitUtils.ConvertFromInternalUnits(system.GetFlow(),DB.UnitTypeId.LitersPerSecond)}
result['existing_connections_preserved']=True
for ident in [3199617,3230469,3228892]:
    e=doc.GetElement(DB.ElementId(ident))
    for c in e.MEPModel.ConnectorManager.Connectors:
        result['ports'].append({'element':ident,'port':int(c.Id),'flow':DB.UnitUtils.ConvertFromInternalUnits(c.Flow,DB.UnitTypeId.LitersPerSecond)})
