"""Prepare real adapters before routing; preserve terminal position and flow."""
import math
from pyrevit.api import DB
from ... import revit_read as rr
from ..core.terminal_adapter import FAMILY,TYPE
from ..core import geometry as g

KIND='TERMINAL_ADAPTER'

def ports(e):return [c for c in e.MEPModel.ConnectorManager.Connectors if c.Domain==DB.Domain.DomainHvac and c.ConnectorType==DB.ConnectorType.End]
def mm(p):return [rr.mm(p.X),rr.mm(p.Y),rr.mm(p.Z)]
def vector(p):return [p.X,p.Y,p.Z]
def descriptor(c):
    return {'id':c.Owner.UniqueId+':connector:'+str(c.Id),'element_key':c.Owner.UniqueId,'element_id':rr.eid(c.Owner.Id),
        'connector_id':int(c.Id),'xyz_mm':mm(c.Origin),'direction':vector(c.CoordinateSystem.BasisZ),
        'size':{'shape':'Round','diameter_mm':rr.mm(2*c.Radius)},'connected':bool(c.IsConnected),
        'classification':str(c.DuctSystemType),'flow_direction':str(c.Direction),'provenance':'REVIT_CONNECTOR',
        'connected_to_cap':any(isinstance(r.Owner,DB.FamilyInstance) and r.Owner.Symbol.FamilyName=='M_Remate redondo - DTL'
            and c.IsConnectedTo(r) for r in c.AllRefs if r.ConnectorType==DB.ConnectorType.End)}

def attached(terminal,port):
    return [r.Owner for r in port.AllRefs if r.Owner.Id!=terminal.Id and r.ConnectorType==DB.ConnectorType.End
        and port.IsConnectedTo(r) and isinstance(r.Owner,DB.FamilyInstance) and r.Owner.Symbol.FamilyName==FAMILY]

def record(terminal,port,adapter):
    ps=ports(adapter);lower=max(ps,key=lambda c:c.Radius)
    outlets=[c for c in ps if c.Id!=lower.Id]
    if len(outlets)!=2:raise ValueError('El adaptador debe tener exactamente dos salidas.')
    axis=port.CoordinateSystem.BasisZ
    if axis.DotProduct(terminal.GetTransform().BasisZ)<0:axis=axis.Negate()
    bound=adapter.get_BoundingBox(None)
    corners=[mm(DB.XYZ(x,y,z)) for x in (bound.Min.X,bound.Max.X) for y in (bound.Min.Y,bound.Max.Y) for z in (bound.Min.Z,bound.Max.Z)]
    return {'id':adapter.UniqueId,'element_id':rr.eid(adapter.Id),'type_id':rr.eid(adapter.GetTypeId()),'body_corners_mm':corners,
        'pivot_mm':mm(lower.Origin),'axis':vector(axis),'terminal_port':descriptor(port),
        'outlets':[descriptor(c) for c in sorted(outlets,key=lambda c:c.Id)]}

def prepare(doc,configuration,destinations):
    from . import proposals
    selected={v['terminal_id']:v for v in destinations if v.get('include',True) and v.get('terminal_id')}
    if not selected:return {'created':[],'reused':[],'preserved_existing_networks':[],'removed_owned_caps':[]}
    symbols=[s for s in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol) if s.FamilyName==FAMILY and rr.name(s)==TYPE]
    if len(symbols)!=1:raise ValueError('Cargue un único tipo '+FAMILY+' / '+TYPE)
    created=[];reused=[];preserved=[];removed_caps=[]
    tx=DB.Transaction(doc,'ARQ-T · Preparar codos de rejillas');tx.Start()
    try:
        symbol=symbols[0]
        if not symbol.IsActive:symbol.Activate();doc.Regenerate()
        for assignment in configuration.get('equipment_assignments',[]):
            if not assignment.get('include',True):continue
            device=doc.GetElement(DB.ElementId(int(assignment['element_id'])))
            if device is None:continue
            caps={r.Owner.UniqueId:r.Owner for c in ports(device) for r in c.AllRefs
                if r.ConnectorType==DB.ConnectorType.End and c.IsConnectedTo(r)
                and (proposals.metadata(r.Owner) or {}).get('kind')=='DISTRIBUTOR_CAP'
                and proposals.metadata(r.Owner).get('distributor_uid')==device.UniqueId}
            for cap in caps.values():removed_caps.append(rr.eid(cap.Id));doc.Delete(cap.Id)
            if caps:doc.Regenerate()
        for uid,value in selected.items():
            terminal=doc.GetElement(uid)
            if terminal is None:raise ValueError('Rejilla seleccionada ausente: '+uid)
            from ...family_rules import terminal_matches
            if not terminal_matches(terminal.Symbol.FamilyName,rr.name(terminal.Symbol),value['network_type']):raise ValueError('Familia de rejilla incompatible.')
            port=next((c for c in ports(terminal) if descriptor(c)['id']==value['port_id']),None)
            if port is None:raise ValueError('Toma de rejilla no verificable.')
            matches=attached(terminal,port)
            if len(matches)>1:raise ValueError('Más de un adaptador conectado a una rejilla.')
            if matches:
                existing=matches[0];lower=max(ports(existing),key=lambda c:c.Radius)
                outlets=[c for c in ports(existing) if c.Id!=lower.Id]
                # If a previously modeled branch has been deleted, remove
                # only our own remaining cap so the adapter can be replanned.
                if not any(c.IsConnected and not descriptor(c)['connected_to_cap'] for c in outlets):
                    caps={r.Owner.UniqueId:r.Owner for c in outlets for r in c.AllRefs
                        if r.ConnectorType==DB.ConnectorType.End and c.IsConnectedTo(r)
                        and (proposals.metadata(r.Owner) or {}).get('kind')=='TERMINAL_ADAPTER_CAP'
                        and proposals.metadata(r.Owner).get('adapter_uid')==existing.UniqueId}
                    for cap in caps.values():
                        removed_caps.append(rr.eid(cap.Id));doc.Delete(cap.Id)
                    if caps:doc.Regenerate()
                payload={'kind':KIND,'terminal_uid':uid,'network_type':value['network_type']}
                if proposals.metadata(matches[0])!=payload:proposals._tag(matches[0],payload)
                reused.append(rr.eid(matches[0].Id));continue
            if port.IsConnected:preserved.append(rr.eid(terminal.Id));continue
            before=terminal.GetTransform();flow=terminal.LookupParameter('Flow').AsDouble()
            adapter=doc.Create.NewFamilyInstance(port.Origin,symbol,doc.GetElement(terminal.LevelId),DB.Structure.StructuralType.NonStructural)
            doc.Regenerate()
            lower=max(ports(adapter),key=lambda c:c.Radius);lower_id=lower.Id
            if abs(lower.Radius-port.Radius)>rr.internal(.01):raise ValueError('Diámetro rejilla/adaptador incompatible; no se modifica el tipo.')
            axis=port.CoordinateSystem.BasisZ
            if axis.DotProduct(before.BasisZ)<0:axis=axis.Negate()
            current=lower.CoordinateSystem.BasisZ;wanted=axis.Negate();cross=current.CrossProduct(wanted)
            dot=max(-1.,min(1.,current.DotProduct(wanted)))
            if cross.GetLength()>1e-8:DB.ElementTransformUtils.RotateElement(doc,adapter.Id,DB.Line.CreateUnbound(lower.Origin,cross.Normalize()),math.acos(dot))
            elif dot<0:
                cross=current.CrossProduct(DB.XYZ.BasisX)
                if cross.GetLength()<1e-8:cross=current.CrossProduct(DB.XYZ.BasisY)
                DB.ElementTransformUtils.RotateElement(doc,adapter.Id,DB.Line.CreateUnbound(lower.Origin,cross.Normalize()),math.pi)
            doc.Regenerate();lower=next(c for c in ports(adapter) if c.Id==lower_id)
            DB.ElementTransformUtils.MoveElement(doc,adapter.Id,port.Origin-lower.Origin);doc.Regenerate()
            lower=next(c for c in ports(adapter) if c.Id==lower_id)
            centers=[]
            for assignment in configuration.get('equipment_assignments',[]):
                if assignment.get('network_type')!=value['network_type']:continue
                device=doc.GetElement(DB.ElementId(int(assignment['element_id'])))
                if device:
                    ps=ports(device)
                    centers.append(DB.XYZ(sum(p.Origin.X for p in ps)/len(ps),sum(p.Origin.Y for p in ps)/len(ps),sum(p.Origin.Z for p in ps)/len(ps)))
            if centers:
                target=min(centers,key=lambda c:c.DistanceTo(port.Origin))-port.Origin
                target=target-axis.Multiply(target.DotProduct(axis))
                ps=[c for c in ports(adapter) if c.Id!=lower_id]
                ref=(ps[0].Origin+ps[1].Origin).Multiply(.5)-lower.Origin
                ref=ref-axis.Multiply(ref.DotProduct(axis))
                if min(ref.GetLength(),target.GetLength())>1e-8:
                    angle=math.atan2(axis.DotProduct(ref.Normalize().CrossProduct(target.Normalize())),ref.Normalize().DotProduct(target.Normalize()))
                    DB.ElementTransformUtils.RotateElement(doc,adapter.Id,DB.Line.CreateUnbound(lower.Origin,axis),angle);doc.Regenerate()
            lower=next(c for c in ports(adapter) if c.Id==lower_id);lower.ConnectTo(port);doc.Regenerate()
            after=terminal.GetTransform()
            if any(a.DistanceTo(b)>1e-9 for a,b in zip((before.Origin,before.BasisX,before.BasisY,before.BasisZ),(after.Origin,after.BasisX,after.BasisY,after.BasisZ))):raise ValueError('La rejilla se ha desplazado; se revierte la preparación.')
            if not lower.IsConnectedTo(port) or abs(terminal.LookupParameter('Flow').AsDouble()-flow)>1e-9:raise ValueError('La unión o el caudal no se conserva.')
            proposals._tag(adapter,{'kind':KIND,'terminal_uid':uid,'network_type':value['network_type']})
            created.append(rr.eid(adapter.Id))
        if tx.Commit()!=DB.TransactionStatus.Committed:raise ValueError('Revit no ha confirmado los adaptadores.')
    except Exception:
        if tx.GetStatus()==DB.TransactionStatus.Started:tx.RollBack()
        raise
    return {'created':created,'reused':reused,'preserved_existing_networks':preserved,'removed_owned_caps':removed_caps}

def apply_pose(doc,port):
    pose=port['adapter_pose'];adapter=doc.GetElement(pose['element_key']);terminal=doc.GetElement(pose['terminal_port']['element_key'])
    if adapter is None or terminal is None:raise ValueError('Adaptador o rejilla ausente.')
    if rr.eid(adapter.GetTypeId())!=pose['type_id']:raise ValueError('El tipo del adaptador ha cambiado.')
    check_capture(adapter,pose)
    original=terminal.GetTransform();p=DB.XYZ(*[rr.internal(v) for v in pose['pivot_mm']]);axis=DB.XYZ(*pose['axis'])
    for c in ports(adapter):
        if c.IsConnected and c.Id!=max(ports(adapter),key=lambda x:x.Radius).Id:raise ValueError('No se gira un adaptador con salidas ya conectadas.')
    lower=max(ports(adapter),key=lambda x:x.Radius)
    terminal_port=next(c for c in ports(terminal) if c.Id==pose['terminal_port']['connector_id'])
    if not lower.IsConnectedTo(terminal_port):raise ValueError('El adaptador ya no está unido a su rejilla.')
    lower.DisconnectFrom(terminal_port)
    DB.ElementTransformUtils.RotateElement(doc,adapter.Id,DB.Line.CreateUnbound(p,axis),pose['rotation_rad']);doc.Regenerate()
    lower=max(ports(adapter),key=lambda x:x.Radius);lower.ConnectTo(terminal_port);doc.Regenerate()
    actual=terminal.GetTransform()
    if any(a.DistanceTo(b)>1e-9 for a,b in zip((original.Origin,original.BasisX,original.BasisY,original.BasisZ),(actual.Origin,actual.BasisX,actual.BasisY,actual.BasisZ))):raise ValueError('El giro intenta mover la rejilla.')
    c=next(c for c in ports(adapter) if c.Id==port['connector_id'])
    expected=DB.XYZ(*[rr.internal(v) for v in port['xyz_mm']])
    if c.Origin.DistanceTo(expected)>rr.internal(.1):raise ValueError('El adaptador no coincide con la pose aprobada; recalcule.')
    return c


def check_capture(adapter,pose):
    current={c.Id:c for c in ports(adapter)}
    for captured in pose['captured_outlets']:
        actual=current.get(captured['connector_id'])
        if actual is None or g.distance(mm(actual.Origin),captured['xyz_mm'])>.1:
            raise ValueError('El adaptador ha girado desde el cálculo; recalcule.')


def close_unused(doc,adapter,selected_id):
    """Inside the route transaction: cap the other outlet, never the grille."""
    from .distributor_caps import create_cap
    from . import proposals
    lower=max(ports(adapter),key=lambda c:c.Radius);result=[]
    for c in ports(adapter):
        if c.Id in (lower.Id,selected_id) or c.IsConnected:continue
        cap=create_cap(doc,c)
        proposals._tag(cap,{'kind':'TERMINAL_ADAPTER_CAP','adapter_uid':adapter.UniqueId})
        result.append(cap)
    return result


def mount_scope(doc,adapter,clearance_mm):
    """Only the verified DN125 collar crossing the ceiling interface."""
    from . import proposals
    data=proposals.metadata(adapter)
    if not data or data.get('kind')!=KIND:return None
    terminal=doc.GetElement(data['terminal_uid'])
    if terminal is None:raise ValueError('Rejilla del adaptador ausente.')
    lower=max(ports(adapter),key=lambda c:c.Radius)
    matches=[c for c in ports(terminal) if lower.IsConnectedTo(c)]
    if len(matches)!=1:raise ValueError('Unión adaptador-rejilla sin verificar.')
    p=mm(lower.Origin);axis=vector(lower.CoordinateSystem.BasisZ)
    radius=rr.mm(lower.Radius)+clearance_mm
    bound=terminal.get_BoundingBox(None)
    axial=[g.dot(g.sub(mm(DB.XYZ(x,y,z)),p),axis)
        for x in (bound.Min.X,bound.Max.X) for y in (bound.Min.Y,bound.Max.Y) for z in (bound.Min.Z,bound.Max.Z)]
    # The connected grille has an actual neck above its connector plane.
    # Cover that neck and the two mating clearances, only inside the DN125
    # cylinder; do not exempt the lateral adapter body or any roof.
    return proposals.swept_solid({'kind':'LINE','start':g.add(p,g.mul(axis,min(axial)-2*clearance_mm)),
        'end':g.add(p,g.mul(axis,max(axial)+2*clearance_mm))},radius)


def posed_solids(doc,pose):
    adapter=doc.GetElement(pose['element_key'])
    if adapter is None or rr.eid(adapter.GetTypeId())!=pose['type_id']:raise ValueError('Adaptador cambiado; recalcule.')
    check_capture(adapter,pose)
    lower=max(ports(adapter),key=lambda c:c.Radius)
    if g.distance(mm(lower.Origin),pose['pivot_mm'])>.1:raise ValueError('El adaptador se ha movido; recalcule.')
    pivot=DB.XYZ(*[rr.internal(v) for v in pose['pivot_mm']]);axis=DB.XYZ(*pose['axis'])
    frame=DB.Transform.CreateRotationAtPoint(axis,pose['rotation_rad'],pivot)
    return adapter,[DB.SolidUtils.CreateTransformed(s,frame) for s in rr.solids(adapter)]
