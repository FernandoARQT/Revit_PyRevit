# -*- coding: utf-8 -*-
"""Retira solo los cinco remates obsoletos comprobados tras cargar H12."""
from pyrevit.api import DB

assert not doc.IsFamilyDocument
obsolete=[3311908,3311913,3311915,3311917,3311919]
protected=[3199617,3228892,3230469,3311911,3311921,3311923,3311925]
distributor=doc.GetElement(DB.ElementId(3230469))
assert distributor.MEPModel.ConnectorManager.Connectors.Size==6
inverse=distributor.GetTransform().Inverse
for ident in obsolete:
    cap=doc.GetElement(DB.ElementId(ident))
    assert cap is not None and isinstance(cap,DB.FamilyInstance) and cap.MEPModel.PartType==DB.PartType.Cap
    for c in cap.MEPModel.ConnectorManager.Connectors:
        assert not c.IsConnected, 'Tapon conectado; no se elimina'
        assert abs(inverse.OfPoint(c.Origin).Z*304.8-225.0)<0.1, 'Posicion inesperada'
t=DB.Transaction(doc,'Retirar cinco tapones superiores obsoletos de H12')
t.Start()
deleted=[]
for ident in obsolete:
    deleted.extend(int(i.Value) for i in doc.Delete(DB.ElementId(ident)))
assert not set(deleted).intersection(protected)
doc.Regenerate()
t.Commit()
terminal=doc.GetElement(DB.ElementId(3228892))
expected=terminal.LookupParameter('Flow').AsDouble()
inlet=next(c for c in doc.GetElement(DB.ElementId(3199617)).MEPModel.ConnectorManager.Connectors if c.Id==3)
assert abs(inlet.Flow-expected)<1e-8
for ident in protected:assert doc.GetElement(DB.ElementId(ident)) is not None
result={'removed_obsolete_caps':obsolete,'horizontal_caps_preserved':4,'distributor_connectors':6,
        'hrv_flow_lps':DB.UnitUtils.ConvertFromInternalUnits(inlet.Flow,DB.UnitTypeId.LitersPerSecond),'saved':False}
