"""Read-only physical connector descriptors with instance-local identities."""
from pyrevit.api import DB
from ... import revit_read as rr


def descriptor(connector):
    owner=connector.Owner;p=connector.Origin;n=connector.CoordinateSystem.BasisZ
    size={'shape':str(connector.Shape)}
    if size['shape']=='Round':size['diameter_mm']=rr.mm(2*connector.Radius)
    elif size['shape'] in ('Rectangular','Oval'):size.update(width_mm=rr.mm(connector.Width),height_mm=rr.mm(connector.Height))
    refs=[]
    for other in connector.AllRefs:
        if other.Owner.Id==owner.Id or other.ConnectorType!=DB.ConnectorType.End or not connector.IsConnectedTo(other):continue
        refs.append({'owner_unique_id':other.Owner.UniqueId,'owner_element_id':rr.eid(other.Owner.Id),
            'connector_id':int(other.Id),'family':getattr(getattr(other.Owner,'Symbol',None),'FamilyName','')})
    data={'id':owner.UniqueId+':connector:'+str(connector.Id),'element_key':owner.UniqueId,'element_id':rr.eid(owner.Id),
        'connector_id':int(connector.Id),'xyz_mm':[rr.mm(p.X),rr.mm(p.Y),rr.mm(p.Z)],'direction':[n.X,n.Y,n.Z],
        'size':size,'domain':str(connector.Domain),'connector_type':str(connector.ConnectorType),
        'classification':str(connector.DuctSystemType),'flow_direction':str(connector.Direction),
        'connected':bool(connector.IsConnected),'provenance':'REVIT_CONNECTOR','physical_refs':refs}
    if isinstance(owner,DB.FamilyInstance):
        inverse=owner.GetTransform().Inverse;local=inverse.OfPoint(p);normal=inverse.OfVector(n)
        data.update(local_origin_mm=[rr.mm(local.X),rr.mm(local.Y),rr.mm(local.Z)],local_direction=[normal.X,normal.Y,normal.Z])
    return data
