"""Native connection using the curve type's configured transition fitting."""
from pyrevit.api import DB
from ... import revit_read as rr


def rigid_type_with_transition(doc,base,transition_type_id):
    """Use a dedicated type; never alter routing preferences of existing ducts."""
    group=DB.RoutingPreferenceRuleGroupType.Transitions
    if not transition_type_id or base.RoutingPreferenceManager.GetNumberOfRules(group):return base
    name='ARQT_MEP_{}_Transition_{}'.format(rr.eid(base.Id),transition_type_id)
    matches=[t for t in DB.FilteredElementCollector(doc).OfClass(DB.Mechanical.DuctType) if rr.name(t)==name]
    if len(matches)>1:raise ValueError('Tipo rígido de recuperación ambiguo.')
    clone=matches[0] if matches else base.Duplicate(name)
    pm=clone.RoutingPreferenceManager
    if not pm.GetNumberOfRules(group):
        pm.AddRule(group,DB.RoutingPreferenceRule(DB.ElementId(transition_type_id),'Transición configurada en Flex - Redondo'))
    return clone


def connect(doc,curve_port,target,expected_type_id=None,straight_mm=None):
    origin=target.Origin;radius=target.Radius
    if curve_port.Shape!=DB.ConnectorProfileType.Round or target.Shape!=DB.ConnectorProfileType.Round:
        raise ValueError('La transición automática requiere dos tomas circulares.')
    fitting=None
    if abs(curve_port.Radius-target.Radius)>rr.internal(.005):
        fitting=doc.Create.NewTransitionFitting(curve_port,target)
        doc.Regenerate()
        if expected_type_id and rr.eid(fitting.GetTypeId())!=expected_type_id:
            raise ValueError('Revit ha utilizado una transición distinta de la asignada a Flex - Redondo.')
        ports=list(fitting.MEPModel.ConnectorManager.Connectors)
        if len(ports)!=2 or not any(c.IsConnectedTo(target) for c in ports) or not any(c.IsConnectedTo(curve_port) for c in ports):
            raise ValueError('La transición no conecta físicamente ambos extremos.')
        if target.Origin.DistanceTo(origin)>rr.internal(.01) or abs(target.Radius-radius)>rr.internal(.005):
            raise ValueError('La transición intentó desplazar o redimensionar la toma del equipo.')
        # Native insertion attaches directly to the equipment port. No arbitrary
        # clearance length is imposed; inspect its resulting solids afterwards.
    else:
        curve_port.ConnectTo(target)
        doc.Regenerate()
        if not curve_port.IsConnectedTo(target):
            raise ValueError('Revit no confirma la conexión directa.')
    return fitting
