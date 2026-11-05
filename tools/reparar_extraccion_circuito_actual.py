# -*- coding: utf-8 -*-
"""Correcciones auxiliares MCP por etapas. No guarda ni modifica botones."""
from pyrevit.api import DB
from Autodesk.Revit.DB.Mechanical import DuctSystemType


def repair_distributor_editor(fd):
    assert fd.IsFamilyDocument and '5-fold' in fd.Title
    before = []
    t = DB.Transaction(fd, 'Corregir conectores pasivos del distribuidor')
    t.Start()
    for ce in DB.FilteredElementCollector(fd).OfClass(DB.ConnectorElement):
        p = ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_CONNECTOR_SYSTEM_CLASSIFICATION_PARAM)
        before.append({'id':int(ce.Id.Value), 'classification':p.AsInteger()})
        p.Set(int(DuctSystemType.Fitting))
    fm = fd.FamilyManager
    fm.Set(fm.get_Parameter('TH12'), 1)
    fm.Set(fm.get_Parameter('TV12'), 0)
    fd.Regenerate()
    t.Commit()
    return {'before':before, 'connectors_after':[
        {'id':int(c.Id.Value),'class':str(c.SystemClassification)}
        for c in DB.FilteredElementCollector(fd).OfClass(DB.ConnectorElement)],
        'TH12':fm.CurrentType.AsInteger(fm.get_Parameter('TH12')),
        'TV12':fm.CurrentType.AsInteger(fm.get_Parameter('TV12'))}


def terminal_editor(fd):
    ce = fd.GetElement(DB.ElementId(2667590))
    fm = fd.FamilyManager
    fp = fm.get_Parameter('Flow')
    assert fp.IsInstance and fp.Definition.GetDataType() == DB.SpecTypeId.AirFlow
    ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_CONFIGURATION_PARAM).Set(1)
    fm.AssociateElementParameterToFamilyParameter(
        ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_PARAM), fp)
    return {'associated':True,'preset':True}


def hrv_editor(fd):
    ce = fd.GetElement(DB.ElementId(2693880))
    ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_CONNECTOR_SYSTEM_CLASSIFICATION_PARAM).Set(int(DuctSystemType.ExhaustAir))
    return {'extraction_inlet_class':str(ce.SystemClassification)}


CIRCUIT_IDS = [3199617,3230469,3228892,3232297,3235338,3235353,3235484,3235492]


def circuit_snapshot(project):
    rows = []
    for ident in CIRCUIT_IDS:
        el = project.GetElement(DB.ElementId(ident))
        assert el is not None
        cm = el.ConnectorManager if isinstance(el, DB.MEPCurve) else el.MEPModel.ConnectorManager
        for c in cm.Connectors:
            if c.Domain != DB.Domain.DomainHvac or c.ConnectorType != DB.ConnectorType.End: continue
            links = sorted([(int(r.Owner.Id.Value),int(r.Id)) for r in c.AllRefs
                if r.Owner.Id != el.Id and r.ConnectorType == DB.ConnectorType.End and c.IsConnectedTo(r)])
            rows.append({'element':ident,'port':int(c.Id),'links':links,
                'flow':DB.UnitUtils.ConvertFromInternalUnits(c.Flow,DB.UnitTypeId.LitersPerSecond),
                'system':str(c.DuctSystemType),'direction':str(c.Direction)})
    return sorted(rows,key=lambda r:(r['element'],r['port']))


def repair_project():
    import revit_document_utils as u
    assert not doc.IsFamilyDocument
    before = circuit_snapshot(doc)
    fd = next(d for d in app.Documents if d.IsFamilyDocument and '5-fold' in d.Title)
    u.load_open_family(uiapp,doc,str(fd.GetHashCode()))
    u.edit_and_reload_family(uiapp,doc,3228892,terminal_editor)
    u.edit_and_reload_family(uiapp,doc,3199617,hrv_editor)
    t = DB.Transaction(doc,'Unificar circuito de aire viciado')
    t.Start()
    for ident in [3232297,3235484]:
        p = doc.GetElement(DB.ElementId(ident)).get_Parameter(DB.BuiltInParameter.RBS_DUCT_SYSTEM_TYPE_PARAM)
        p.Set(DB.ElementId(95163))
    doc.Regenerate()
    h = doc.GetElement(DB.ElementId(3199617))
    inlet = next(c for c in h.MEPModel.ConnectorManager.Connectors if c.Id == 3)
    system = inlet.MEPSystem
    if system is not None and system.BaseEquipment is None:
        cs = DB.ConnectorSet()
        cs.Insert(inlet)
        system.Remove(cs)
        doc.Regenerate()
        inlet = next(c for c in h.MEPModel.ConnectorManager.Connectors if c.Id == 3)
        system.BaseEquipmentConnector = inlet
    doc.Regenerate()
    t.Commit()
    after = circuit_snapshot(doc)
    assert [(r['element'],r['port'],r['links']) for r in before] == [(r['element'],r['port'],r['links']) for r in after], 'Las conexiones han cambiado; revertir'
    return {'before':before,'after':after,'editor_preserved':fd.IsValidObject}


if globals().get('inputs',{}).get('stage') == 'distributor_editor':
    result = repair_distributor_editor(doc)
elif globals().get('inputs',{}).get('stage') == 'project':
    result = repair_project()
