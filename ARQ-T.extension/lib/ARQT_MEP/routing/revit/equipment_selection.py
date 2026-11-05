"""Project-specific logical SUPPLY/EXTRACT assignments; no family parameters changed."""
from pyrevit.api import DB
from ... import ownership
from . import proposals,extractor
from .. import settings
from ..systems.heat_recovery_topology import selected_equipment

KIND='HRV_EQUIPMENT_SELECTION'

def records(doc):
    result=[]
    for e in DB.FilteredElementCollector(doc).OfClass(DB.ExtensibleStorage.DataStorage):
        data=proposals.metadata(e)
        if data and data.get('kind')==KIND and data.get('document_id')==ownership.document_id(doc):result.append((e,data))
    if len(result)>1:raise ValueError('Asignación de equipos duplicada; revise sus registros.')
    return result

def read(doc):
    found=records(doc)
    return found[0][1]['configuration'] if found else {}

def save(doc,configuration,explicit=False):
    if not explicit:raise ValueError('Guardar asignaciones requiere explicit=True.')
    configuration=settings.validate(configuration)
    catalog=extractor.get_mep_connection_points(doc,configuration)
    hrv,selected=selected_equipment(catalog,configuration)
    saved={'hrv_id':hrv['id'],'equipment_assignments':[{'element_id':d['element_id'],'network_type':n} for d,n in selected]}
    found=records(doc)
    tx=DB.Transaction(doc,'ARQ-T · Asignar redes a distribuidores');tx.Start()
    try:
        e=found[0][0] if found else DB.ExtensibleStorage.DataStorage.Create(doc)
        proposals._tag(e,{'kind':KIND,'document_id':ownership.document_id(doc),'configuration':saved})
        if tx.Commit()!=DB.TransactionStatus.Committed:raise ValueError('Asignación no confirmada por Revit.')
    except Exception:
        if tx.GetStatus()==DB.TransactionStatus.Started:tx.RollBack()
        raise
    return saved
