# -*- coding: utf-8 -*-
"""Read-only family/project connector comparison. No reloads, saves or transactions."""
import json
import os
from pyrevit.api import DB
from pyrevit.revit.db import query

def eid(v):return int(v.Value)

def value(p):
    if p.StorageType==DB.StorageType.Integer:return p.AsInteger()
    if p.StorageType==DB.StorageType.Double:return p.AsDouble()
    if p.StorageType==DB.StorageType.String:return p.AsString()
    if p.StorageType==DB.StorageType.ElementId:return eid(p.AsElementId())

instances=[e for e in DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance) if 'distribuidor' in e.Symbol.FamilyName.casefold()]
families={eid(e.Symbol.Family.Id):e.Symbol.Family for e in instances}
report={'project':doc.Title,'read_only':True,'instances':[],'families':[]}
for e in instances:
    inv=e.GetTransform().Inverse;ports=[]
    for c in e.MEPModel.ConnectorManager.Connectors:
        if c.Domain!=DB.Domain.DomainHvac:continue
        p=inv.OfPoint(c.Origin)
        ports.append({'connector_id':int(c.Id),'system_classification':str(c.DuctSystemType),'direction':str(c.Direction),
                      'diameter_mm':c.Radius*2*304.8,'local_xyz_mm':[p.X*304.8,p.Y*304.8,p.Z*304.8],
                      'connected':bool(c.IsConnected),'flow_lps':DB.UnitUtils.ConvertFromInternalUnits(c.Flow,DB.UnitTypeId.LitersPerSecond)})
    report['instances'].append({'element_id':eid(e.Id),'family_id':eid(e.Symbol.Family.Id),'family':e.Symbol.FamilyName,'type':query.get_name(e.Symbol),'connectors':ports})

for family in families.values():
    existing=[d for d in app.Documents if d.IsFamilyDocument and (d.OwnerFamily.Name==family.Name or d.Title==family.Name)]
    sources=[];opened=None
    try:
        try:
            if existing:
                sources.append(('OPEN_EDITOR_MAY_DIFFER_FROM_PROJECT',existing[0]))
            else:raise ValueError('No matching family editor is open; loaded instance connectors are reported separately.')
        except Exception as exc:
            report['families'].append({'family':family.Name,'loaded_copy_unavailable':str(exc)})
        for d in existing:
            if not any(d.Equals(other) for label,other in sources):sources.append(('OPEN_EDITOR_MAY_DIFFER_FROM_PROJECT',d))
        for label,fd in sources:
            fm=fd.FamilyManager;ports=[]
            for c in DB.FilteredElementCollector(fd).OfClass(DB.ConnectorElement):
                if c.Domain!=DB.Domain.DomainHvac:continue
                parameters=[]
                for p in c.Parameters:
                    fp=fm.GetAssociatedFamilyParameter(p)
                    parameters.append({'name':p.Definition.Name,'id':eid(p.Id),'bip':str(DB.BuiltInParameter(eid(p.Id))) if eid(p.Id)<0 else None,
                                       'value':value(p),'formatted':p.AsValueString(),'is_read_only':bool(p.IsReadOnly),
                                       'association':{'name':fp.Definition.Name,'id':eid(fp.Id),'is_instance':bool(fp.IsInstance),'formula':fp.Formula} if fp else None})
                linked=c.GetLinkedConnectorElement();p=c.Origin
                ports.append({'element_id':eid(c.Id),'system_classification':str(c.SystemClassification),'is_primary':bool(c.IsPrimary),
                              'linked_connector_element_id':eid(linked.Id) if linked else None,
                              'diameter_mm':c.Radius*2*304.8,'local_xyz_mm':[p.X*304.8,p.Y*304.8,p.Z*304.8],'parameters':parameters})
            report['families'].append({'family':family.Name,'source':label,'modified_in_editor':bool(fd.IsModified),
                                       'connector_count':len(ports),'connectors':ports,
                                       'family_parameters':[{'name':p.Definition.Name,'id':eid(p.Id),'is_instance':bool(p.IsInstance),'formula':p.Formula} for p in fm.Parameters]})
    finally:
        if opened:opened.Close(False)

artifact=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','tests','artifacts','analisis_familia_distribuidor.json'))
with open(artifact,'w',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
result={'read_only':True,'report_file':artifact,'instances':report['instances'],'family_definitions':[]}
for f in report['families']:
    if 'connectors' not in f:result['family_definitions'].append(f);continue
    result['family_definitions'].append({'family':f['family'],'source':f['source'],'modified_in_editor':f['modified_in_editor'],
       'connector_count':f['connector_count'],'connectors':[dict({k:v for k,v in c.items() if k!='parameters'},
       relevant_parameters=[p for p in c['parameters'] if any(w in (p['bip'] or '') for w in ('SYSTEM','FLOW','DIRECTION')) or p['association']]) for c in f['connectors']]})
