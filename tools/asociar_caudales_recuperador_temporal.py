# -*- coding: utf-8 -*-
"""Asociacion nativa de los dos circuitos del recuperador; no fija caudales."""
from pyrevit.api import DB

if not doc.IsFamilyDocument or doc.Title != 'I-VE-Recuperador de calor':
    raise RuntimeError('Active la familia del recuperador.')
fm = doc.FamilyManager
parameters = {}
for fp in fm.Parameters:
    parameters[fp.Definition.Name] = fp
connectors = list(DB.FilteredElementCollector(doc).OfClass(DB.ConnectorElement))
assert len(connectors) == 4
plan = []
for ce in connectors:
    classification = ce.GetParameters('Clasificación de sistema')[0].AsInteger()
    direction = ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_DIRECTION_PARAM).AsInteger()
    assert classification in (1, 3) and direction in (1, 2)
    name = 'fan_flow_supply' if classification == 1 else 'fan_flow_exhaust'
    fp = parameters[name]
    assert fp.IsInstance and not fp.Formula
    flow = ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_PARAM)
    assert fm.CanElementParameterBeAssociated(flow)
    exterior = (classification == 1 and direction == 1) or (classification == 3 and direction == 2)
    plan.append((ce, flow, fp, 1 if exterior else 0))
t = DB.Transaction(doc, 'Asociar caudales independientes del recuperador')
t.Start()
try:
    for ce, flow, fp, configuration in plan:
        ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_CONFIGURATION_PARAM).Set(configuration)
        fm.AssociateElementParameterToFamilyParameter(flow, fp)
    doc.Regenerate()
    result = []
    for ce, flow, fp, configuration in plan:
        assert fm.GetAssociatedFamilyParameter(flow).Id == fp.Id
        result.append({'connector':ce.Id.Value, 'parameter':fp.Definition.Name,
                       'configuration':configuration})
    t.Commit()
except:
    t.RollBack()
    raise
