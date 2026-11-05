# -*- coding: utf-8 -*-
"""Especializa la familia abierta como H12. No guarda ni recarga documentos."""
from pyrevit.api import DB

assert doc.IsFamilyDocument and '5-fold' in doc.Title
fm=doc.FamilyManager
assert fm.CurrentType.AsInteger(fm.get_Parameter('TH12'))==1
connectors=list(DB.FilteredElementCollector(doc).OfClass(DB.ConnectorElement))
assert len(connectors)==11
superior=[c for c in connectors if not c.IsPrimary and c.Direction.Normalize().DotProduct(DB.XYZ.BasisZ)>0.99]
assert len(superior)==5
superior_ids=set(int(c.Id.Value) for c in superior)
kept=[c for c in connectors if c not in superior]
before=[(int(c.Id.Value),c.Origin,c.Radius,c.Direction) for c in kept]
removed=[]
t=DB.Transaction(doc,'Corregir familia H12 eliminando conectores superiores inactivos')
t.Start()
for ce in superior:
    deleted=doc.Delete(ce.Id)
    removed.extend(int(ident.Value) for ident in deleted)
assert set(removed)==superior_ids, 'unexpected deletion'
fm.SetFormula(fm.get_Parameter('TH12'),'1 = 1')
fm.SetFormula(fm.get_Parameter('TV12'),'1 = 0')
doc.Regenerate()
after=list(DB.FilteredElementCollector(doc).OfClass(DB.ConnectorElement))
assert len(after)==6
for ident,origin,radius,direction in before:
    ce=doc.GetElement(DB.ElementId(ident))
    assert ce is not None and ce.Origin.DistanceTo(origin)<1e-8 and abs(ce.Radius-radius)<1e-8 and ce.Direction.IsAlmostEqualTo(direction)
assert sum(1 for c in after if c.IsPrimary)==1
t.Commit()
result={'removed_connectors':removed,'remaining_connectors':len(after),'positions_and_diameters_preserved':True,
        'variant':'H12 only','TH12':fm.CurrentType.AsInteger(fm.get_Parameter('TH12')),'TV12':fm.CurrentType.AsInteger(fm.get_Parameter('TV12')),
        'saved':False,'loaded_in_project':False}
