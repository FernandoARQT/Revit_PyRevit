# -*- coding: utf-8 -*-
"""Correccion logica del recuperador; no edita familias ni guarda documentos."""
from pyrevit.api import DB


def ports(el):
    cm = el.ConnectorManager if isinstance(el, DB.MEPCurve) else el.MEPModel.ConnectorManager
    return [c for c in cm.Connectors if c.Domain == DB.Domain.DomainHvac and c.ConnectorType == DB.ConnectorType.End]


def snapshot():
    queue = [3199617,3228892,3230469]
    seen = set()
    rows = []
    while queue:
        ident = queue.pop(0)
        if ident in seen: continue
        seen.add(ident)
        assert len(seen) <= 40
        el = doc.GetElement(DB.ElementId(ident))
        for c in ports(el):
            refs = []
            for ref in c.AllRefs:
                if ref.Owner.Id != el.Id and ref.ConnectorType == DB.ConnectorType.End and c.IsConnectedTo(ref):
                    refs.append((int(ref.Owner.Id.Value),int(ref.Id)))
                    queue.append(int(ref.Owner.Id.Value))
            rows.append({'element':ident,'port':int(c.Id),'links':sorted(refs),
                'system_id':int(c.MEPSystem.Id.Value) if c.MEPSystem else None,
                'flow_lps':DB.UnitUtils.ConvertFromInternalUnits(c.Flow,DB.UnitTypeId.LitersPerSecond)})
    return sorted(rows,key=lambda x:(x['element'],x['port']))


assert not doc.IsFamilyDocument and doc.Title == 'Proyecto'
before = snapshot()
equipment = doc.GetElement(DB.ElementId(3199617))
inlet = next(c for c in ports(equipment) if c.Id == 3)
terminal = doc.GetElement(DB.ElementId(3228892))
target = next(c for c in ports(terminal)).MEPSystem
assert target is not None
assert doc.GetElement(target.GetTypeId()).SystemClassification == DB.MEPSystemClassification.ExhaustAir
old = inlet.MEPSystem
t = DB.Transaction(doc,'Asignar recuperador como base de la extraccion existente')
t.Start()
if old is not None and old.Id != target.Id:
    connectors = DB.ConnectorSet()
    connectors.Insert(inlet)
    old.Remove(connectors)
    doc.Regenerate()
    inlet = next(c for c in ports(equipment) if c.Id == 3)
if target.BaseEquipment is None:
    if inlet.MEPSystem is not None:
        connectors = DB.ConnectorSet()
        connectors.Insert(inlet)
        inlet.MEPSystem.Remove(connectors)
        doc.Regenerate()
        inlet = next(c for c in ports(equipment) if c.Id == 3)
    target.BaseEquipmentConnector = inlet
doc.Regenerate()
t.Commit()
after = snapshot()
assert [(x['element'],x['port'],x['links']) for x in before] == [(x['element'],x['port'],x['links']) for x in after], 'Cambio de conexiones fisicas; revertir'
assert target.BaseEquipment is not None and target.BaseEquipment.Id == equipment.Id
result = {'system_id':int(target.Id.Value),'base_equipment':int(target.BaseEquipment.Id.Value),
          'connections_preserved':True,'before':before,'after':after}
