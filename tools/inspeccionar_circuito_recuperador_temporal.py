# -*- coding: utf-8 -*-
"""Read-only MCP circuit trace. No button imports, transactions or model writes.

Traverses each HRV port separately, stops at terminals and other equipment,
filters physical connector references, and reports design/connector flows apart.
"""
import json
import os
from collections import deque
from pyrevit.api import DB
from pyrevit.revit.db import query


def eid(value):return int(value.Value)

def connectors(element):
    manager=element.ConnectorManager if isinstance(element,DB.MEPCurve) else (
        element.MEPModel.ConnectorManager if getattr(element,'MEPModel',None) else None)
    return [c for c in manager.Connectors if c.Domain==DB.Domain.DomainHvac and c.ConnectorType==DB.ConnectorType.End] if manager else []

def key(connector):return (eid(connector.Owner.Id),int(connector.Id))

def physical_refs(connector):
    return [c for c in connector.AllRefs if c.Owner.Id!=connector.Owner.Id
            and c.Domain==DB.Domain.DomainHvac and c.ConnectorType==DB.ConnectorType.End
            and connector.IsConnectedTo(c)]

def airflow(value):return DB.UnitUtils.ConvertFromInternalUnits(value,DB.UnitTypeId.LitersPerSecond)

def port_info(c):
    row={'element_id':eid(c.Owner.Id),'connector_id':int(c.Id),'classification':str(c.DuctSystemType),
         'direction':str(c.Direction),'connected':bool(c.IsConnected),
         'diameter_mm':c.Radius*2*304.8 if c.Shape==DB.ConnectorProfileType.Round else None,
         'physical_links':[list(key(p)) for p in physical_refs(c)]}
    for name in ('Flow','AssignedFlow','AssignedFlowConfiguration'):
        try:
            value=getattr(c,name)
            row[name+'_lps' if name!='AssignedFlowConfiguration' else name]=airflow(value) if name!='AssignedFlowConfiguration' else str(value)
        except Exception as exc:row[name+'_unavailable']=str(exc)
    return row

def flow_parameters(element):
    rows=[]
    for scope,owner in [('INSTANCE',element),('TYPE',doc.GetElement(element.GetTypeId()))]:
        if owner is None:continue
        for p in owner.Parameters:
            if p.StorageType!=DB.StorageType.Double or p.Definition.GetDataType()!=DB.SpecTypeId.AirFlow:continue
            pid=eid(p.Id)
            rows.append({'scope':scope,'name':p.Definition.Name,'parameter_id':pid,
                         'built_in':str(DB.BuiltInParameter(pid)) if pid<0 else None,
                         'value_lps':airflow(p.AsDouble()) if p.HasValue else None,
                         'formatted':p.AsValueString(),'is_read_only':bool(p.IsReadOnly),
                         'guid':str(p.GUID) if p.IsShared else None})
    return rows

def element_info(e):
    return {'element_id':eid(e.Id),'name':query.get_name(e),
            'family':e.Symbol.FamilyName if isinstance(e,DB.FamilyInstance) else None,
            'type':query.get_name(doc.GetElement(e.GetTypeId())),
            'category':str(DB.BuiltInCategory(eid(e.Category.Id))) if e.Category else None,
            'class':e.GetType().FullName,'connectors':[port_info(c) for c in connectors(e)],
            'flow_parameters':flow_parameters(e)}

def trace(hrv,start):
    queue=deque((c,[eid(hrv.Id),eid(c.Owner.Id)]) for c in physical_refs(start))
    seen=set();elements={};terminals={};opens=[];stops=[]
    while queue:
        current,path=queue.popleft();ckey=key(current)
        if ckey in seen:continue
        seen.add(ckey)
        if len(seen)>5000:raise ValueError('Trace limit exceeded; no completeness claimed.')
        e=current.Owner;identifier=eid(e.Id)
        if e.Id==hrv.Id:
            stops.append({'element_id':identifier,'reason':'RETURNS_TO_HRV','connector_id':int(current.Id)})
            continue
        elements.setdefault(identifier,element_info(e))
        category=eid(e.Category.Id) if e.Category else None
        if category==int(DB.BuiltInCategory.OST_DuctTerminal):
            terminals.setdefault(identifier,dict(elements[identifier],path_element_ids=path))
            continue
        allowed=isinstance(e,DB.MEPCurve) or category in (
            int(DB.BuiltInCategory.OST_DuctFitting),int(DB.BuiltInCategory.OST_DuctAccessory))
        # Some manifold families are mechanical equipment or generic models.
        family=e.Symbol.FamilyName.casefold() if isinstance(e,DB.FamilyInstance) else ''
        allowed=allowed or 'distribuidor' in family or 'manifold' in family
        if not allowed:
            stops.append({'element_id':identifier,'reason':'OTHER_EQUIPMENT_NOT_BRIDGED'});continue
        for port in connectors(e):
            refs=physical_refs(port)
            if not refs:opens.append({'element_id':identifier,'connector_id':int(port.Id),'diameter_mm':port.Radius*2*304.8 if port.Shape==DB.ConnectorProfileType.Round else None})
            for other in refs:
                if key(other) not in seen:queue.append((other,path+[eid(other.Owner.Id)]))
    terminal_rows=list(terminals.values())
    design=[];ambiguous=[]
    for t in terminal_rows:
        candidates=[p for p in t['flow_parameters'] if p['scope']=='INSTANCE' and p['value_lps'] is not None]
        preferred=[p for p in candidates if p['name']=='Flow']
        selected=preferred[0] if len(preferred)==1 else candidates[0] if len(candidates)==1 else None
        if selected:design.append({'element_id':t['element_id'],'parameter':selected['name'],'lps':selected['value_lps']})
        else:ambiguous.append(t['element_id'])
    return {'hrv_port':port_info(start),'terminal_count':len(terminals),'terminals':terminal_rows,
            'terminal_design_values':design,'terminal_design_sum_lps':sum(v['lps'] for v in design),
            'terminal_design_sum_complete':not ambiguous,'ambiguous_terminal_flow_ids':ambiguous,
            'elements':list(elements.values()),'open_ports':list({(r['element_id'],r['connector_id']):r for r in opens}.values()),'stops':stops}

hrvs=[e for e in DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance) if 'recuperador' in e.Symbol.FamilyName.casefold()]
report={'document':doc.Title,'read_only':True,'equipment':[]}
for hrv in hrvs:
    report['equipment'].append(dict(element_info(hrv),circuits=[trace(hrv,c) for c in connectors(hrv)]))
artifact=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','tests','artifacts','inspeccion_circuito_recuperador.json'))
with open(artifact,'w',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
result={'report_file':artifact,'equipment':[],'read_only':True}
for e in report['equipment']:
    summary={'element_id':e['element_id'],'family':e['family'],'type':e['type'],'circuits':[]}
    for c in e['circuits']:
        summary['circuits'].append({k:v for k,v in c.items() if k!='elements'})
        summary['circuits'][-1]['traversed_elements']=[{'id':v['element_id'],'category':v['category'],'family':v['family'],
            'connector_flows_lps':[(p['connector_id'],p.get('Flow_lps')) for p in v['connectors']]} for v in c['elements']]
    result['equipment'].append(summary)
