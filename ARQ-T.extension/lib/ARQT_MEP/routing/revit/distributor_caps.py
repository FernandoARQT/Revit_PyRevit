"""Close unused manifold ports with physically connected native end caps."""
import math
from pyrevit.api import DB
from ... import revit_read as rr
from ...family_rules import distributor_network


def create_cap(doc,source):
    """Place and physically join a round cap in the caller's transaction."""
    symbols=[s for s in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol)
        if s.FamilyName=='M_Remate redondo - DTL' and s.Category
        and rr.eid(s.Category.Id)==int(DB.BuiltInCategory.OST_DuctFitting)]
    if len(symbols)!=1:raise ValueError('Cargue un único tipo de tapa M_Remate redondo - DTL.')
    symbol=symbols[0]
    if not symbol.IsActive:symbol.Activate();doc.Regenerate()
    cap=doc.Create.NewFamilyInstance(source.Origin,symbol,DB.Structure.StructuralType.NonStructural)
    doc.Regenerate()
    def port():return next(c for c in cap.MEPModel.ConnectorManager.Connectors if c.ConnectorType==DB.ConnectorType.End)
    port().Radius=source.Radius;doc.Regenerate()
    current=port().CoordinateSystem.BasisZ.Normalize();target=source.CoordinateSystem.BasisZ.Negate().Normalize()
    axis=current.CrossProduct(target);dot=max(-1.,min(1.,current.DotProduct(target)))
    if axis.GetLength()>1e-8:
        DB.ElementTransformUtils.RotateElement(doc,cap.Id,DB.Line.CreateUnbound(port().Origin,axis.Normalize()),math.acos(dot))
    elif dot<0:
        axis=current.CrossProduct(DB.XYZ.BasisX)
        if axis.GetLength()<1e-8:axis=current.CrossProduct(DB.XYZ.BasisY)
        DB.ElementTransformUtils.RotateElement(doc,cap.Id,DB.Line.CreateUnbound(port().Origin,axis.Normalize()),math.pi)
    doc.Regenerate();DB.ElementTransformUtils.MoveElement(doc,cap.Id,source.Origin-port().Origin);doc.Regenerate()
    port().ConnectTo(source);doc.Regenerate()
    if not source.IsConnectedTo(port()):raise ValueError('Tapa sin conexión física.')
    return cap


def close_unused(doc, proposal):
    from .network_model import owned
    from . import proposals
    modeled_ids={data['route_id'] for _,data in owned(doc)}
    reserved = set()
    for route in proposal['routes']:
        if route['route_id'] not in modeled_ids:continue
        for key in ('source', 'destination'):
            port = route.get(key) or {}
            if port.get('kind') in ('DISTRIBUTOR', 'DISTRIBUTOR_MAIN'):
                reserved.add((port['element_id'], port['connector_id']))
    symbols = [s for s in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol)
               if s.FamilyName == 'M_Remate redondo - DTL'
               and s.Category and rr.eid(s.Category.Id) == int(DB.BuiltInCategory.OST_DuctFitting)]
    result = {'created': [], 'failures': []}
    for assignment in proposal['configuration'].get('equipment_assignments', []):
        if not assignment.get('include', True):
            continue
        value = assignment.get('element_uid')
        element = doc.GetElement(value) if value else doc.GetElement(DB.ElementId(int(assignment['element_id'])))
        if element is None or distributor_network(element.Symbol.FamilyName) != assignment['network_type']:
            result['failures'].append({'element': assignment, 'reason': 'Distribuidor ausente o de otra red.'})
            continue
        port_ids = [c.Id for c in element.MEPModel.ConnectorManager.Connectors
                    if c.Domain == DB.Domain.DomainHvac and c.ConnectorType == DB.ConnectorType.End
                    and not c.IsConnected and (rr.eid(element.Id), c.Id) not in reserved]
        for port_id in port_ids:
            tx = DB.Transaction(doc, 'ARQ-T · Añadir tapa a distribuidor')
            tx.Start()
            try:
                if len(symbols) != 1:
                    raise ValueError('Cargue un único tipo de tapa M_Remate redondo - DTL.')
                source = next(c for c in element.MEPModel.ConnectorManager.Connectors if c.Id == port_id)
                if source.Shape != DB.ConnectorProfileType.Round:
                    raise ValueError('La toma requiere una tapa no circular.')
                symbol = symbols[0]
                if not symbol.IsActive:
                    symbol.Activate()
                cap = doc.Create.NewFamilyInstance(source.Origin, symbol, DB.Structure.StructuralType.NonStructural)
                doc.Regenerate()
                def cap_port():
                    return next(c for c in cap.MEPModel.ConnectorManager.Connectors if c.ConnectorType == DB.ConnectorType.End)
                connector = cap_port()
                connector.Radius = source.Radius
                doc.Regenerate()
                connector = cap_port()
                direction = connector.CoordinateSystem.BasisZ.Normalize()
                target = source.CoordinateSystem.BasisZ.Negate().Normalize()
                axis = direction.CrossProduct(target)
                dot = max(-1., min(1., direction.DotProduct(target)))
                if axis.GetLength() > 1e-8:
                    DB.ElementTransformUtils.RotateElement(doc, cap.Id, DB.Line.CreateUnbound(connector.Origin, axis.Normalize()), math.acos(dot))
                elif dot < 0:
                    axis = direction.CrossProduct(DB.XYZ.BasisX)
                    if axis.GetLength() < 1e-8:
                        axis = direction.CrossProduct(DB.XYZ.BasisY)
                    DB.ElementTransformUtils.RotateElement(doc, cap.Id, DB.Line.CreateUnbound(connector.Origin, axis.Normalize()), math.pi)
                doc.Regenerate()
                DB.ElementTransformUtils.MoveElement(doc, cap.Id, source.Origin - cap_port().Origin)
                doc.Regenerate()
                cap_port().ConnectTo(source)
                doc.Regenerate()
                if not source.IsConnectedTo(cap_port()):
                    raise ValueError('La tapa no ha quedado conectada físicamente.')
                proposals._tag(cap,{'kind':'DISTRIBUTOR_CAP','distributor_uid':element.UniqueId})
                if tx.Commit() != DB.TransactionStatus.Committed:
                    raise ValueError('Revit no confirmó la tapa.')
                result['created'].append({'element_id': rr.eid(cap.Id), 'distributor_id': rr.eid(element.Id), 'connector_id': port_id})
            except Exception as exc:
                if tx.GetStatus() == DB.TransactionStatus.Started:
                    tx.RollBack()
                result['failures'].append({'distributor_id': rr.eid(element.Id), 'connector_id': port_id, 'reason': str(exc)})
    return result
