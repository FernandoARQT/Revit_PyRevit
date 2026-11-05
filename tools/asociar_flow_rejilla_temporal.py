# -*- coding: utf-8 -*-
"""Ejecutar mediante MCP con la familia de rejillas activa. No guarda ni recarga."""
from pyrevit.api import DB
from Autodesk.Revit.DB.Mechanical import DuctFlowConfigurationType

assert doc.IsFamilyDocument and 'Rejillas de ventilaci' in doc.Title
ce = doc.GetElement(DB.ElementId(2667590))
fm = doc.FamilyManager
fp = fm.get_Parameter('Flow')
assert fp is not None and fp.IsInstance
assert fp.Definition.GetDataType() == DB.SpecTypeId.AirFlow
p = ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_PARAM)
assert fm.CanElementParameterBeAssociated(p)
t = DB.Transaction(doc, 'Asociar caudal de conector a Flow de instancia')
t.Start()
ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_CONFIGURATION_PARAM).Set(int(DuctFlowConfigurationType.Preset))
fm.AssociateElementParameterToFamilyParameter(p, fp)
doc.Regenerate()
t.Commit()
associated = fm.GetAssociatedFamilyParameter(p)
assert associated is not None and associated.Id == fp.Id
result = {'connector':2667590, 'flow_configuration':'Preset',
          'parameter':associated.Definition.Name, 'instance':associated.IsInstance,
          'association_verified':True, 'saved':False, 'reloaded':False}
