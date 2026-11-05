"""End-to-end trial using production dispatch. Caller chooses MCP dry-run."""
import json
from pyrevit.api import DB
from ARQT_MEP.routing import settings,mcp
from ARQT_MEP.routing.revit import extractor,terminal_adapters,network_model


def run(doc,path,budget=180):
    with open(path,encoding='utf8') as f:previous=json.load(f)
    config=settings.validate(previous['configuration'])
    config['search'].update(max_seconds=20,max_total_seconds=budget,max_iterations=3)
    config['parallel']['enabled']=True
    catalog=extractor.get_mep_connection_points(doc,config)
    destinations=[dict(terminal_id=t['id'],port_id=t['connectors'][0]['id'],network_type=t['suggested_network_type'],include=True)
        for t in catalog['terminals'] if t.get('suggested_network_type') in ('SUPPLY','EXTRACT')]
    prep=mcp.dispatch(doc,'prepare_terminal_adapters',dict(configuration=config,destinations=destinations),allow_write=True)
    proposal=mcp.dispatch(doc,'create_mep_route_proposal',dict(configuration=config,destinations=destinations,origins=previous['origins']))
    approved=[]
    for route in list(proposal['routes']):
        if route['native_status'] not in ('VALID','VALID_WITH_WARNINGS'):continue
        proposal=mcp.dispatch(doc,'approve_mep_route',dict(proposal=proposal,route_id=route['route_id'],explicit=True))
        approved.append(route['route_id'])
    modeled=mcp.dispatch(doc,'model_heat_recovery_network',dict(proposal=proposal,explicit=True),allow_write=True)
    retry_reports=[]
    candidates=[r for r in proposal['routes'] if r.get('route_kind')=='BRANCH' and r['native_status'] in ('VALID','VALID_WITH_WARNINGS')]
    if candidates and not any(c['route_id'].startswith('route:') for c in modeled['created']):
        target=candidates[0];uid=target['adapter_pose']['terminal_port']['element_key']
        selected=[d for d in destinations if d['terminal_id']==uid]
        retry_config=settings.validate(proposal['configuration'])
        retry_config['search'].update(max_total_seconds=90,max_seconds=20,max_iterations=3)
        for conflict in modeled.get('geometry_conflicts',[]):
            point=conflict.get('position_mm')
            if not point or conflict.get('route_id')!=target['route_id']:continue
            retry_config['constraints']['forbidden_regions'].append({'id':'native-retry-'+str(len(retry_config['constraints']['forbidden_regions'])),
                'box_mm':[v-125 for v in point]+[v+125 for v in point]})
        mcp.dispatch(doc,'prepare_terminal_adapters',dict(configuration=retry_config,destinations=selected),allow_write=True)
        retry=mcp.dispatch(doc,'create_mep_route_proposal',dict(configuration=retry_config,destinations=selected,origins=previous['origins']))
        for r in list(retry['routes']):
            if r['native_status'] in ('VALID','VALID_WITH_WARNINGS'):
                retry=mcp.dispatch(doc,'approve_mep_route',dict(proposal=retry,route_id=r['route_id'],explicit=True))
        result=mcp.dispatch(doc,'model_heat_recovery_network',dict(proposal=retry,explicit=True),allow_write=True)
        retry_reports.append(dict(summary=retry['summary'],model=result))
        modeled['created'].extend(result['created'])
    with open(path.replace('routing-current-family-test.json','complete-network-proposal.json'),'w',encoding='utf8') as f:json.dump(proposal,f,ensure_ascii=False)
    flows=[]
    ids={i for row in modeled['created'] for i in row['members']}
    ids.update([3199617,3229184,3230469])
    for i in sorted(ids):
        e=doc.GetElement(DB.ElementId(i));cm=network_model.manager(e)
        row={'id':i,'class':e.GetType().Name,'connectors':[]}
        if cm:
            for c in cm.Connectors:
                if c.Domain!=DB.Domain.DomainHvac or c.ConnectorType!=DB.ConnectorType.End:continue
                try:row['connectors'].append(dict(id=c.Id,connected=c.IsConnected,flow_lps=DB.UnitUtils.ConvertFromInternalUnits(c.Flow,DB.UnitTypeId.LitersPerSecond),classification=str(c.DuctSystemType),direction=str(c.Direction)))
                except Exception as exc:row['connectors'].append(dict(id=c.Id,error=str(exc)))
        flows.append(row)
    report=dict(prepared=prep,summary=proposal['summary'],approved=approved,model=modeled,retries=retry_reports,flows=flows,
        routes=[dict(id=r['route_id'],status=r['native_status'],reason=r.get('reason'),checks=r.get('native_checks')) for r in proposal['routes']])
    with open(path.replace('routing-current-family-test.json','complete-network-trial.json'),'w',encoding='utf8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    return report
