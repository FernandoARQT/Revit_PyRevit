# -*- coding: utf-8 -*-
"""Carga variante H12 autorizada y retira solo sus tapones superiores obsoletos."""
from pyrevit.api import DB
import revit_document_utils as utils

assert not doc.IsFamilyDocument and doc.Title=='Proyecto'
instance=doc.GetElement(DB.ElementId(3230469))
inverse=instance.GetTransform().Inverse
caps=set()
kept_links=[]
for c in instance.MEPModel.ConnectorManager.Connectors:
    if c.Domain!=DB.Domain.DomainHvac or c.ConnectorType!=DB.ConnectorType.End:continue
    is_top=inverse.OfVector(c.CoordinateSystem.BasisZ).Normalize().DotProduct(DB.XYZ.BasisZ)>0.99
    for ref in c.AllRefs:
        if ref.Owner.Id==instance.Id or ref.ConnectorType!=DB.ConnectorType.End or not c.IsConnectedTo(ref):continue
        if is_top:
            assert isinstance(ref.Owner,DB.FamilyInstance) and ref.Owner.MEPModel.PartType==DB.PartType.Cap, 'Toma superior conectada a un ramal real; no se modifica'
            caps.add(int(ref.Owner.Id.Value))
        else:
            kept_links.append((int(c.Id),int(ref.Owner.Id.Value),int(ref.Id)))
assert len(caps)==5, 'La situacion difiere del estado verificado'
fd=next(d for d in app.Documents if d.IsFamilyDocument and '5-fold' in d.Title)
assert DB.FilteredElementCollector(fd).OfClass(DB.ConnectorElement).GetElementCount()==6
t=DB.Transaction(doc,'Retirar cinco tapones de tomas superiores eliminadas')
t.Start()
for ident in caps:
    removed=doc.Delete(DB.ElementId(ident))
    assert not any(x.Value in [3199617,3228892,3230469] for x in removed)
t.Commit()
operation=utils.load_open_family(uiapp,doc,str(fd.GetHashCode()))
instance=doc.GetElement(DB.ElementId(3230469))
connectors=list(instance.MEPModel.ConnectorManager.Connectors)
assert len(connectors)==6
assert not any(instance.GetTransform().Inverse.OfVector(c.CoordinateSystem.BasisZ).Normalize().DotProduct(DB.XYZ.BasisZ)>0.99 for c in connectors)
for port,owner,other_port in kept_links:
    c=next(c for c in connectors if c.Id==port)
    other=doc.GetElement(DB.ElementId(owner))
    cm=other.ConnectorManager if isinstance(other,DB.MEPCurve) else other.MEPModel.ConnectorManager
    ref=next(c for c in cm.Connectors if c.Id==other_port)
    assert c.IsConnectedTo(ref), 'Se ha alterado una conexion horizontal; revertir'
expected=doc.GetElement(DB.ElementId(3228892)).LookupParameter('Flow').AsDouble()
hrv=doc.GetElement(DB.ElementId(3199617))
inlet=next(c for c in hrv.MEPModel.ConnectorManager.Connectors if c.Id==3)
assert abs(inlet.Flow-expected)<1e-8, 'El caudal no llega al recuperador; revertir'
remaining_caps=[]
for c in connectors:
    for ref in c.AllRefs:
        if ref.Owner.Id!=instance.Id and ref.ConnectorType==DB.ConnectorType.End and c.IsConnectedTo(ref) and isinstance(ref.Owner,DB.FamilyInstance) and ref.Owner.MEPModel.PartType==DB.PartType.Cap:
            remaining_caps.append(int(ref.Owner.Id.Value))
assert len(remaining_caps)==4
result={'removed_top_caps':sorted(caps),'remaining_horizontal_caps':sorted(remaining_caps),
        'connector_count':len(connectors),'horizontal_connections_preserved':True,
        'hrv_flow_lps':DB.UnitUtils.ConvertFromInternalUnits(inlet.Flow,DB.UnitTypeId.LitersPerSecond),
        'family_editor_preserved':fd.IsValidObject,'saved':False}
