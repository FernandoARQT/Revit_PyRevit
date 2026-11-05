# -*- coding: utf-8 -*-
"""Auxiliar MCP. Corrige la copia cargada; preserva los editores abiertos.
Ejecutar con run_modify, primero dry_run=True. No guarda archivos Revit.
"""
from Autodesk.Revit import DB
from Autodesk.Revit.DB.Mechanical import DuctFlowConfigurationType
from pyrevit.revit.db.create import FamilyLoaderOptionsHandler

ids = [3199617, 3238608, 3238611, 3230469, 3254688, 3254692, 3228892]
def snapshot():
    rows = []
    for ident in ids:
        el = doc.GetElement(DB.ElementId(ident))
        cm = el.ConnectorManager if isinstance(el, DB.MEPCurve) else el.MEPModel.ConnectorManager
        for c in cm.Connectors:
            if c.Domain != DB.Domain.DomainHvac: continue
            links = sorted([(int(r.Owner.Id.Value), int(r.Id)) for r in c.AllRefs
                if r.Owner.Id != el.Id and r.ConnectorType == DB.ConnectorType.End and c.IsConnectedTo(r)])
            rows.append({'element':ident, 'port':int(c.Id), 'system':str(c.DuctSystemType),
                'flow_lps': DB.UnitUtils.ConvertFromInternalUnits(c.Flow, DB.UnitTypeId.LitersPerSecond),
                'links':links})
    return rows

before = snapshot()
fd = next(d for d in app.Documents if d.IsFamilyDocument and 'Rejillas de ventilaci' in d.Title)
fg = DB.TransactionGroup(fd, 'Prueba temporal asociacion Flow')
fg.Start()
try:
    ft = DB.Transaction(fd, 'Asociar demanda de rejilla')
    ft.Start()
    ce = fd.GetElement(DB.ElementId(2667590))
    fp = fd.FamilyManager.get_Parameter('Flow')
    if not fp.IsInstance: raise Exception('Flow no es de instancia')
    ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_CONFIGURATION_PARAM).Set(int(DuctFlowConfigurationType.Preset))
    fd.FamilyManager.AssociateElementParameterToFamilyParameter(
        ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_PARAM), fp)
    ft.Commit()
    fd.LoadFamily(doc, FamilyLoaderOptionsHandler())
finally:
    fg.RollBack()

t = DB.Transaction(doc, 'Corregir flexible a aire viciado')
t.Start()
doc.GetElement(DB.ElementId(3238608)).get_Parameter(DB.BuiltInParameter.RBS_DUCT_SYSTEM_TYPE_PARAM).Set(DB.ElementId(95163))
doc.Regenerate()
t.Commit()
after = snapshot()
assert [(r['element'],r['port'],r['links']) for r in before] == [(r['element'],r['port'],r['links']) for r in after], 'Han cambiado conexiones; revertir'
result = {'before':before, 'after':after, 'family_editor_preserved':True}
