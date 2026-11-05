"""Persistent ownership, kept separate from editable project parameters."""
import json
import math
from System import Guid, String
from pyrevit.api import DB
from .revit_read import eid, mm
from .revit_read import instance_basis
from .surface import basis_error_degrees

SCHEMA_GUID='329f0e36-b871-4b67-b924-ea6d2f5bd5e4'


def schema(create=False):
    found=DB.ExtensibleStorage.Schema.Lookup(Guid(SCHEMA_GUID))
    if found or not create:
        return found
    builder=DB.ExtensibleStorage.SchemaBuilder(Guid(SCHEMA_GUID))
    builder.SetSchemaName('ARQT_Ventilation_v1')
    builder.SetReadAccessLevel(DB.ExtensibleStorage.AccessLevel.Public)
    builder.SetWriteAccessLevel(DB.ExtensibleStorage.AccessLevel.Public)
    builder.AddSimpleField('Payload',String)
    return builder.Finish()


def read(element):
    s=schema()
    if s is None:
        return None
    entity=element.GetEntity(s)
    if not entity.IsValid():
        return None
    raw=entity.Get[String](s.GetField('Payload'))
    data=json.loads(raw)
    if data.get('version')!=1:
        raise ValueError('Metadatos ARQ-T de versión no compatible en {}'.format(eid(element.Id)))
    return data


def write(element,data):
    s=schema(True)
    entity=DB.ExtensibleStorage.Entity(s)
    entity.Set[String](s.GetField('Payload'),json.dumps(dict(data,version=1),ensure_ascii=False,allow_nan=False))
    element.SetEntity(entity)


def document_id(doc):
    return doc.ProjectInformation.UniqueId


def inventory(doc,tolerance_mm=1):
    output=[]
    for e in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_DuctTerminal).WhereElementIsNotElementType():
        metadata=read(e)
        location=e.Location
        point=location.Point if isinstance(location,DB.LocationPoint) else None
        position=[mm(point.X),mm(point.Y),mm(point.Z)] if point else None
        rotation=float(location.Rotation) if isinstance(location,DB.LocationPoint) else None
        basis=instance_basis(e) if isinstance(e,DB.FamilyInstance) else None
        own=bool(metadata and metadata.get('document_id')==document_id(doc))
        p=e.LookupParameter('Flow')
        flow=(DB.UnitUtils.ConvertFromInternalUnits(p.AsDouble(),DB.UnitTypeId.LitersPerSecond)
              if p and p.StorageType==DB.StorageType.Double and p.Definition.GetDataType()==DB.SpecTypeId.AirFlow else None)
        moved=False
        if own:
            saved=metadata.get('position')
            moved=(position is None or saved is None or math.dist(position,saved)>tolerance_mm or
                   abs(rotation-metadata.get('rotation',0))>1e-7 or
                   basis_error_degrees(basis,metadata.get('basis',[[1,0,0],[0,1,0],[0,0,1]]))>0.01 or
                   metadata.get('symbol_id')!=eid(e.GetTypeId()) or
                   metadata.get('target',{}).get('level_id')!=eid(e.LevelId) or
                   flow is None or abs(flow-metadata.get('flow',-1))>1e-6)
        output.append({'id':eid(e.Id),'uid':e.UniqueId,'own':own,'metadata':metadata,'pinned':bool(e.Pinned),
                       'position':position,'rotation':rotation,'basis':basis,'flow':flow,'manual_change':moved,
                       'symbol_id':eid(e.GetTypeId()),'level_id':eid(e.LevelId)})
    return output
