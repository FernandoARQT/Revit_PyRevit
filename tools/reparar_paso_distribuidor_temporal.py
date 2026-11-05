# -*- coding: utf-8 -*-
"""Solo cambia clasificacion de conectores. No cambia tipos ni geometria."""
from pyrevit.api import DB
from Autodesk.Revit.DB.Mechanical import DuctSystemType

assert doc.IsFamilyDocument and '5-fold' in doc.Title
fm = doc.FamilyManager
type_state = {}
for ft in fm.Types:
    type_state[str(ft.Name)] = (ft.AsInteger(fm.get_Parameter('TH12')),ft.AsInteger(fm.get_Parameter('TV12')))
changed = []
t = DB.Transaction(doc,'Restablecer conectores pasivos del distribuidor')
t.Start()
for ce in DB.FilteredElementCollector(doc).OfClass(DB.ConnectorElement):
    p = ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_CONNECTOR_SYSTEM_CLASSIFICATION_PARAM)
    if p.AsInteger() != int(DuctSystemType.Fitting):
        changed.append(int(ce.Id.Value))
        p.Set(int(DuctSystemType.Fitting))
doc.Regenerate()
t.Commit()
for ft in fm.Types:
    assert type_state[str(ft.Name)] == (ft.AsInteger(fm.get_Parameter('TH12')),ft.AsInteger(fm.get_Parameter('TV12')))
result = {'changed_connectors':changed,'H12_V12_preserved':True,'connectors':[
    {'id':int(ce.Id.Value),'classification':str(ce.SystemClassification)}
    for ce in DB.FilteredElementCollector(doc).OfClass(DB.ConnectorElement)]}
