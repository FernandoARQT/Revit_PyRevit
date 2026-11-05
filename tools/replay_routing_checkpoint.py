"""Two bounded service continuations of an explicitly recovered checkpoint."""
import copy
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ARQ-T.extension/lib'))
from ARQT_MEP.routing import service

def main():
    folder=ROOT/'tests/artifacts'
    read=lambda n:json.loads((folder/n).read_text(encoding='utf8'))
    prior=read('routing-v21-validation-stage-1000.json')
    recovered=read('routing-regression-recovered-network.json')['routes']
    # Deliberate, documented checkpoint recovery, not an unseeded benchmark.
    for i,route in enumerate(prior['routes']):
        if route['route_id'] in recovered:
            item=copy.deepcopy(route);item.update(copy.deepcopy(recovered[route['route_id']]))
            item['route_id']=route['route_id'];item['source']=item['chosen_source']
            item.update(approval_status='DRAFT',native_status='NOT_EVALUATED')
            prior['routes'][i]=item
    snapshot=read('routing-v2-fresh-snapshot.json');catalog=read('routing-current-catalog.json')
    # Rebuild the equivalent-port universe from the verified unchanged catalog,
    # exactly as in diagnose_routing_regression (legacy serialization repair).
    from ARQT_MEP.routing.systems.heat_recovery_topology import requests as topology_requests
    from ARQT_MEP.routing import settings
    canonical,_,_,_=topology_requests(catalog,settings.validate(prior['configuration']),prior['destinations'])
    by_id={r['route_id']:r for r in canonical}
    for req in prior['requests']:
        base=by_id[req['route_id']]
        ports={p['id']:p for p in [base['source']]+base.get('source_alternatives',[])}
        if any(ports.get(p['id'])!=p for p in [req['source']]+req.get('source_alternatives',[])):
            raise ValueError('Legacy connector geometry mismatch')
        req['source_alternatives']=[p for k,p in ports.items() if k!=req['source']['id']]
    config=copy.deepcopy(prior['configuration']);config['search']['max_total_seconds']=20
    report={'scope':'RECOVERED_CHECKPOINT_NOT_FRESH_SEARCH','stages':[]}
    for stage in (1,2):
        current=service.create_mep_route_proposal(snapshot,catalog,prior['origins'],prior['destinations'],config,prior)
        filename='routing-regression-checkpoint-%d.json'%stage
        (folder/filename).write_text(json.dumps(current,ensure_ascii=False),encoding='utf8')
        report['stages'].append({'summary':current['summary'],'continuation':current['continuation'],
            'next_pass':current['planner_state'].get('next_pass'),'audit_events':len(current['candidate_audit']),
            'seconds':current['stats']['total_seconds'],'file':filename})
        prior=current
    (folder/'routing-regression-checkpoint-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
