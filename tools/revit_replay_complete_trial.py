"""Rebind a previously calculated trial to current adapter ids, then model."""
import json,copy
from ARQT_MEP.routing import mcp,service
from ARQT_MEP.routing.revit import extractor,terminal_adapters,proposals,network_model
from ARQT_MEP.routing.core.terminal_adapter import variants
from pyrevit.api import DB

def run(doc,path,mains_only=False):
    with open(path,encoding='utf8') as f:p=json.load(f)
    config=p['configuration']
    terminal_adapters.prepare(doc,config,p['destinations'])
    for r in p['routes']:
        if not r.get('adapter_pose'):continue
        pose=r['adapter_pose'];terminal=doc.GetElement(pose['terminal_port']['element_key'])
        port=next(c for c in terminal_adapters.ports(terminal) if c.Id==pose['terminal_port']['connector_id'])
        adapter=terminal_adapters.attached(terminal,port)[0]
        distributor=doc.GetElement(r['source']['element_key']);ports=terminal_adapters.ports(distributor)
        center=[sum(terminal_adapters.mm(c.Origin)[i] for c in ports)/len(ports) for i in range(3)]
        choices=variants(terminal_adapters.record(terminal,port,adapter),{'xyz_mm':center},r['network_type'])
        selected=next(c for c in choices if c['connector_id']==r['destination']['connector_id'] and c['adapter_pose']['offset_deg']==pose['offset_deg'])
        r.update(destination=selected,chosen_destination=copy.deepcopy(selected),destination_id=selected['id'],adapter_pose=copy.deepcopy(selected['adapter_pose']))
        request=next(v for v in p['requests'] if v['route_id']==r['route_id'])
        request.update(destination=copy.deepcopy(selected),destination_alternatives=[c for c in choices if c['id']!=selected['id']])
    if mains_only:
        p['routes']=[r for r in p['routes'] if r.get('route_kind')=='MAIN']
        ids={r['route_id'] for r in p['routes']}
        p['requests']=[r for r in p['requests'] if r['route_id'] in ids]
    snapshot=extractor.analyze_mep_geometry(doc,config)
    p.update(document_identity=snapshot['document_identity'],geometry_revision=snapshot['geometry_revision'])
    p=proposals.validate_mep_routes(doc,snapshot,service.seal(p))
    for r in p['routes']:
        if r['native_status'] in ('VALID','VALID_WITH_WARNINGS'):r['approval_status']='APPROVED'
    result=network_model.model(doc,service.seal(p),True)
    result['flows']=[]
    for row in result['created']:
        duct=doc.GetElement(DB.ElementId(row['element_id']))
        result['flows'].append({'id':row['element_id'],'route':row['route_id'],'lps':[DB.UnitUtils.ConvertFromInternalUnits(c.Flow,DB.UnitTypeId.LitersPerSecond) for c in duct.ConnectorManager.Connectors]})
    with open(path.replace('complete-network-proposal.json','complete-network-replay.json'),'w',encoding='utf8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    return {'created':result['created'],'failures':result.get('failures'),'flows':result['flows']}
