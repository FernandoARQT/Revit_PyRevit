"""Review and explicitly remove only transport components within selected scope."""
import hashlib
import json
from pyrevit.api import DB
from System.Collections.Generic import List
from ... import revit_read as rr,ownership
from .. import settings
from ..systems.heat_recovery_topology import selected_equipment
from ..connector_labels import HRV_FUNCTIONS
from . import extractor,proposals
from .network_model import manager


def transport(element,boundary_ids=()):
    if rr.eid(element.Id) in boundary_ids:return False
    return isinstance(element,(DB.Mechanical.Duct,DB.Mechanical.FlexDuct)) or bool(
        element.Category and rr.eid(element.Category.Id) in (int(DB.BuiltInCategory.OST_DuctFitting),int(DB.BuiltInCategory.OST_DuctAccessory)))


def inspect(doc,configuration,destinations=None):
    configuration=settings.validate(configuration);destinations=destinations or []
    catalog=extractor.get_mep_connection_points(doc,configuration)
    hrv,selected=selected_equipment(catalog,configuration)
    boundary_ids={d['element_id'] for d in catalog['devices'] if d.get('is_configured_distributor') or 'recuperador' in d.get('family','').casefold()}
    boundary_ids.update(t['element_id'] for t in catalog['terminals'])
    networks={n for d,n in selected};scope=[]
    for port in hrv['connectors']:
        function=HRV_FUNCTIONS.get((port.get('classification'),port.get('flow_direction')))
        if function and function[0] in networks:scope.append(port)
    for d,n in selected:scope.extend(d['connectors'])
    terminal_ids={v['terminal_id'] for v in destinations if v.get('include',True) and v.get('terminal_id')}
    for t in catalog['terminals']:
        if t['id'] in terminal_ids:scope.extend(t['connectors'])
    allowed={(p['element_id'],p['connector_id']) for p in scope}
    seen=set();components=[]
    for port in scope:
        element=doc.GetElement(port['element_key']);cm=manager(element)
        source=next(c for c in cm.Connectors if c.Id==port['connector_id'])
        for reference in source.AllRefs:
            if reference.ConnectorType!=DB.ConnectorType.End or not transport(reference.Owner,boundary_ids):continue
            if rr.eid(reference.Owner.Id) in seen:continue
            queue=[reference.Owner];members={};external=set();blocked=[]
            while queue:
                e=queue.pop();eid=rr.eid(e.Id)
                if eid in seen:continue
                seen.add(eid);members[eid]=e
                if e.Pinned or e.GroupId!=DB.ElementId.InvalidElementId:blocked.append('Elemento fijado o agrupado: '+str(eid))
                for c in manager(e).Connectors:
                    if c.ConnectorType!=DB.ConnectorType.End:continue
                    for other in c.AllRefs:
                        if other.ConnectorType!=DB.ConnectorType.End or other.Owner.Id==e.Id:continue
                        if transport(other.Owner,boundary_ids):queue.append(other.Owner)
                        elif (rr.eid(other.Owner.Id),int(other.Id)) not in allowed:external.add((rr.eid(other.Owner.Id),int(other.Id)))
            if external:blocked.append('La red llega a tomas fuera de la selección; se conserva ese componente.')
            components.append({'element_ids':sorted(members),'outside_ports':[list(v) for v in sorted(external)],'blocked':sorted(blocked)})
    components.sort(key=lambda c:tuple(c['element_ids']))
    ids={i for component in components if not component['blocked'] for i in component['element_ids']}
    for eid in list(ids):
        for dependent_id in doc.GetElement(DB.ElementId(eid)).GetDependentElements(None):
            dependent=doc.GetElement(dependent_id)
            if dependent and dependent.Category:
                category=str(DB.BuiltInCategory(rr.eid(dependent.Category.Id)))
                if category in ('OST_DuctCurvesCenterLine','OST_FlexDuctCurvesCenterLine'):ids.add(rr.eid(dependent_id))
    for e in DB.FilteredElementCollector(doc).OfClass(DB.ExtensibleStorage.DataStorage):
        data=proposals.metadata(e)
        if data and data.get('kind')=='HRV_MODELED_NETWORK' and data.get('document_id')==ownership.document_id(doc):
            if data.get('members') and set(data['members']).issubset(ids):ids.add(rr.eid(e.Id))
    plan={'document_id':ownership.document_id(doc),'fingerprint':rr.fingerprint(doc),'configuration':configuration,
          'destinations':destinations,'components':components,'delete_ids':sorted(ids),'decision':'KEEP_UNLESS_EXPLICITLY_REMOVED'}
    plan['confirmation_token']=hashlib.sha256(json.dumps(plan,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return plan


def remove(doc,plan,confirmation_token,explicit=False):
    if not explicit:raise ValueError('Eliminar requiere explicit=True y el token de la revisión previa.')
    fresh=inspect(doc,plan['configuration'],plan.get('destinations'))
    if fresh['confirmation_token']!=confirmation_token or plan['confirmation_token']!=confirmation_token:
        raise ValueError('La revisión ya no corresponde al modelo actual; vuelva a consultar antes de eliminar.')
    if not fresh['delete_ids']:return {'deleted_ids':[],'blocked_components':[c for c in fresh['components'] if c['blocked']]}
    prior={rr.eid(e.Id):e.GetType().FullName for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType()}
    tx=DB.Transaction(doc,'ARQ-T · Eliminar conexiones HRV revisadas');tx.Start()
    try:
        ids=List[DB.ElementId]()
        for eid in fresh['delete_ids']:ids.Add(DB.ElementId(eid))
        removed={rr.eid(eid) for eid in doc.Delete(ids)}
        unexpected=[eid for eid in removed-set(fresh['delete_ids']) if prior.get(eid)!='Autodesk.Revit.DB.Mechanical.MechanicalSystem']
        if unexpected:raise ValueError('Revit eliminaría dependientes no incluidos en la revisión: '+str(unexpected))
        if tx.Commit()!=DB.TransactionStatus.Committed:raise ValueError('Revit no confirmó la eliminación.')
    except Exception:
        if tx.GetStatus()==DB.TransactionStatus.Started:tx.RollBack()
        raise
    return {'deleted_ids':sorted(removed),'recalculate_required':True,'blocked_components':[c for c in fresh['components'] if c['blocked']]}
