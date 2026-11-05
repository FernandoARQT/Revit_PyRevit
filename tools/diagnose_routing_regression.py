"""Replay captured candidates, without route search or Revit model writes."""
import copy
import json
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ARQ-T.extension/lib'))
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core.global_incumbent import validate_network
from ARQT_MEP.routing.core.candidate_repository import context_signature
from ARQT_MEP.routing.core.portfolio import select_candidates
from ARQT_MEP.routing.core import cooperative as c
from ARQT_MEP.routing import settings
from ARQT_MEP.routing.core.candidate_repository import audit_session,signature,identity
from ARQT_MEP.routing.parallel import Session
from ARQT_MEP.routing.core import geometry as g

def main():
    folder=ROOT/'tests/artifacts'
    read=lambda name:json.loads((folder/name).read_text(encoding='utf8'))
    old=read('routing-v2-final-1000.json');new=read('routing-v21-validation-stage-1000.json')
    snapshot=read('routing-v2-fresh-snapshot.json');config=settings.validate(new['configuration'])
    space=build_spatial_model(snapshot,config['constraints'])
    report={'identity':{k:old[k]==new[k]==snapshot[k] for k in ('document_identity','geometry_revision')},
        'configuration_differences':{k:{'v2':old['configuration'].get(k),'v21':v} for k,v in config.items() if old['configuration'].get(k)!=v},
        'physical_context_equal':context_signature(space,old['requests'],old['configuration'])==context_signature(space,new['requests'],config)}
    # Explicit migration only after the immutable connector catalog proves
    # exactly which equivalent ports the serialized requests dropped.
    from ARQT_MEP.routing.systems.heat_recovery_topology import requests as topology_requests
    catalog=read('routing-current-catalog.json')
    canonical,_,_,_=topology_requests(catalog,config,new['destinations'])
    by_id={r['route_id']:r for r in canonical}
    report['port_recovery']=[]
    for req in new['requests']:
        ports={p['id']:p for p in [by_id[req['route_id']]['source']]+by_id[req['route_id']].get('source_alternatives',[])}
        prior={p['id']:p for p in [req['source']]+req.get('source_alternatives',[])}
        if not all(ports.get(k)==v for k,v in prior.items()):raise ValueError('Changed connector geometry')
        report['port_recovery'].append({'route':req['route_id'],'missing':sorted(set(ports)-set(prior))})
        req['source_alternatives']=[p for k,p in ports.items() if k!=req['source']['id']]
    report['catalog_recovered_context_equal']=context_signature(space,old['requests'],old['configuration'])==context_signature(space,new['requests'],config)
    if not all(report['identity'].values()) or not report['catalog_recovered_context_equal']:
        report['replay']='BLOCKED_CONTEXT_MISMATCH'
    else:
        historical={r['route_id']:r for r in old['routes'] if c.usable(r)}
        report['individual']={rid:validate_network(space,new['requests'],config,{rid:r}) for rid,r in historical.items()}
        report['joint']=validate_network(space,new['requests'],config,historical)
        current={r['route_id']:r for r in new['routes'] if c.usable(r)}
        pools=copy.deepcopy(new['candidate_bank'])
        for rid,r in historical.items():pools.setdefault(rid,[]).append(r)
        start=time.monotonic()
        with audit_session() as events,Session(space,config):
            chosen,selection=select_candidates(space,new['requests'],pools,config,current,set(),start+60)
        report['selector_replay']={'seconds':time.monotonic()-start,'selection':selection,
            'joint':validate_network(space,new['requests'],config,chosen),'audit':events}
        # Persist the selected geometry for subsequent native verification.
        (folder/'routing-regression-recovered-network.json').write_text(json.dumps({'routes':chosen,
            'validation':report['selector_replay']['joint'],'scope':'GEOMETRIC_CENTRAL_ROUTES_ONLY'},ensure_ascii=False),encoding='utf8')
        report['conflicts']=[]
        reqs={r['route_id']:r for r in new['requests']}
        for rid,items in pools.items():
            if reqs[rid]['destination']['element_id'] not in (3366660,3366670,3366668):continue
            for item in items:
                checked=validate_network(space,new['requests'],config,{rid:item})
                if not checked['valid']:continue
                item=checked['routes'][rid];radius=settings.profile(settings.for_route(config,rid)).radius_mm
                for other,route in current.items():
                    if other==rid:continue
                    r=settings.profile(settings.for_route(config,other)).radius_mm
                    same=(item.get('chosen_source') or item['source'])['id']==(route.get('chosen_source') or route['source'])['id']
                    if not same and not c.overlaps(item,radius,route,r):continue
                    aa=c.points(item);bb=c.points(route)
                    witnesses=[{'candidate_segment_mm':[a,b],'blocking_segment_mm':[x,y],
                        'axis_distance_mm':g.segment_distance(a,b,x,y)}
                        for a,b in zip(aa,aa[1:]) for x,y in zip(bb,bb[1:]) if g.segment_distance(a,b,x,y)<radius+r]
                    report['conflicts'].append({'route':rid,'candidate':signature(identity(item)),
                        'blocker':other,'terminal':reqs[other]['destination']['element_id'],
                        'ports':[(item.get('chosen_source') or item['source'])['id'],(route.get('chosen_source') or route['source'])['id']],
                        'reason':'CONNECTOR_EXCLUSIVITY' if same else 'GEOMETRIC_INTERFERENCE',
                        'passages':[p['id'] for p in item.get('passages',[])],'witness_segments':witnesses})
        report['historical_in_current_bank']={rid:any(x.get('points_mm')==r['points_mm'] for x in new['candidate_bank'].get(rid,[])) for rid,r in historical.items()}
        report['local_repairs']=[]
        for target in (3366670,3366660,3366668):
            rid=next(r['route_id'] for r in new['requests'] if r['destination']['element_id']==target)
            group={rid}|{x['blocker'] for x in report['conflicts'] if x['route']==rid}
            start=time.monotonic()
            with Session(space,config):
                local,selection=select_candidates(space,new['requests'],pools,config,current,set(current)-group,start+20)
            report['local_repairs'].append({'terminal':target,'group':sorted(group),'seconds':time.monotonic()-start,
                'selection':selection,'network_valid':validate_network(space,new['requests'],config,local)['valid'],
                'target_resolved':c.usable(local.get(rid,{})),'connections':sum(c.usable(v) for v in local.values())})
    report['terminal_table']=[{'terminal':r['destination']['element_id'],'v2':old['routes'][i]['status'],'v21':r['status'],'reason':r.get('reason')} for i,r in enumerate(new['routes'])]
    report['stages']=[]
    for stage in (120,240,600,1000):
        p=read('routing-v21-validation-stage-%d.json'%stage);stats=p['stats']
        report['stages'].append({'stage':stage,'summary':p['summary'],'wall_seconds':p['benchmark']['wall_seconds'],
            'preparation_seconds':(stats.get('global_plan') or {}).get('seconds'),
            'parallel':stats.get('parallel'),'optimization':stats.get('optimization'),
            'budget':p['planner_state'].get('budget'),'context':p['planner_state']['context'],
            'candidate_counts':{rid:len(v) for rid,v in p['candidate_bank'].items()},
            'exploration_maps':len(p.get('exploration_state',{}))})
    (folder/'routing-regression-diagnostic.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('stages','individual','joint','configuration_differences','selector_replay','conflicts','local_repairs')},ensure_ascii=False))
    if 'joint' in report:print('historical joint',report['joint']['valid'],report['joint']['issues']);print('selector',report['selector_replay']['selection'])

if __name__=='__main__':main()
