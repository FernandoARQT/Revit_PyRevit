"""MCP-only repair of loaded extract terminal connector; no button changes."""
from pyrevit.api import DB
import revit_document_utils as utils

def editor(fd):
    connectors=list(DB.FilteredElementCollector(fd).OfClass(DB.ConnectorElement))
    if len(connectors)!=1:raise ValueError('La rejilla debe tener un único conector.')
    fm=fd.FamilyManager;fp=fm.get_Parameter('Flow')
    if fp is None or not fp.IsInstance or fp.Definition.GetDataType()!=DB.SpecTypeId.AirFlow:
        raise ValueError('Flow de instancia no verificado.')
    ce=connectors[0];before={}
    for key,bip,value in (
        ('classification',DB.BuiltInParameter.RBS_DUCT_CONNECTOR_SYSTEM_CLASSIFICATION_PARAM,3),
        ('direction',DB.BuiltInParameter.RBS_DUCT_FLOW_DIRECTION_PARAM,2),
        ('configuration',DB.BuiltInParameter.RBS_DUCT_FLOW_CONFIGURATION_PARAM,1)):
        p=ce.get_Parameter(bip)
        if p is None:raise ValueError('Parámetro de conector ausente: '+key)
        before[key]=p.AsInteger()
        if p.AsInteger()!=value:p.Set(value)
    flow=ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_FLOW_PARAM)
    if fm.GetAssociatedFamilyParameter(flow)!=fp:fm.AssociateElementParameterToFamilyParameter(flow,fp)
    return {'before':before,'after':{'classification':'ExhaustAir','direction':'Out','configuration':'Preset','associated_to':'Flow'}}

def run(uiapp,doc):
    terminal=doc.GetElement(DB.ElementId(3378775))
    if terminal is None or terminal.Symbol.FamilyName!='Fraenkische_rejilla 125_Extraccion':
        raise ValueError('Instancia de extracción prevista ausente.')
    fam=terminal.Symbol.Family;symbol_id=terminal.GetTypeId()
    instances=[e for e in DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance) if e.Symbol.Family.Id==fam.Id]
    def frame(e):
        tr=e.GetTransform()
        return [[p.X,p.Y,p.Z] for p in (tr.Origin,tr.BasisX,tr.BasisY,tr.BasisZ)]
    frames={e.Id.Value:frame(e) for e in instances}
    flow=terminal.LookupParameter('Flow').AsDouble()
    operation=utils.edit_and_reload_family(uiapp,doc,3378775,editor)
    from unir_codo_rejilla_temporal import run as reconnect
    connection=reconnect(doc)
    terminal=doc.GetElement(DB.ElementId(3378775))
    if terminal.GetTypeId()!=symbol_id or abs(terminal.LookupParameter('Flow').AsDouble()-flow)>1e-9:
        raise ValueError('Ha cambiado el tipo o caudal de la instancia.')
    for ident,prior in frames.items():
        if frame(doc.GetElement(DB.ElementId(ident)))!=prior:raise ValueError('Ha cambiado la geometría de una rejilla.')
    flows=[]
    for ident in (3378775,3379549,3379661):
        e=doc.GetElement(DB.ElementId(ident));cm=e.ConnectorManager if isinstance(e,DB.MEPCurve) else e.MEPModel.ConnectorManager
        ports=[{'id':int(c.Id),'class':str(c.DuctSystemType),'direction':str(c.Direction),
            'flow_lps':DB.UnitUtils.ConvertFromInternalUnits(c.Flow,DB.UnitTypeId.LitersPerSecond),'connected':c.IsConnected}
            for c in cm.Connectors if c.Domain==DB.Domain.DomainHvac]
        flows.append({'element':ident,'ports':ports})
    if any(abs(p['flow_lps']-DB.UnitUtils.ConvertFromInternalUnits(flow,DB.UnitTypeId.LitersPerSecond))>.001
           for p in flows[-1]['ports']):raise ValueError('El caudal no se transmite a ambos extremos del tubo.')
    return {'family':operation,'instance_count':len(instances),'connection':connection,'geometry_preserved':True,'flows':flows}
