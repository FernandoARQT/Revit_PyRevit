"""Explicit diagnostic runner. Not executed during the V2.1 code-only phase."""
import argparse
import copy
import json
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ARQ-T.extension/lib'))


def main():
    from ARQT_MEP.routing import settings,service
    from ARQT_MEP.routing.core.spatial_model import build_spatial_model
    from ARQT_MEP.routing.core.global_incumbent import validate_network
    from ARQT_MEP.routing.core.candidate_repository import context_signature
    from ARQT_MEP.routing.core.route_conflict_graph import RouteConflictGraph
    from ARQT_MEP.routing.core.multi_route import plan_route
    from ARQT_MEP.routing.core.search_budget import SearchBudgetManager
    parser=argparse.ArgumentParser();parser.add_argument('--incumbent',required=True)
    parser.add_argument('--snapshot',required=True);parser.add_argument('--historical',required=True)
    parser.add_argument('--seconds',type=float,default=600);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    read=lambda path:json.loads((ROOT/path).read_text(encoding='utf8'))
    current=read(args.incumbent);old=read(args.historical);snapshot=read(args.snapshot)
    service.check(current);service.check(old)
    if (current['document_identity'],current['geometry_revision'])!=(snapshot['document_identity'],snapshot['geometry_revision']):
        raise ValueError('Current proposal and capture do not match')
    config=settings.validate(current['configuration']);space=build_spatial_model(snapshot,config['constraints'])
    report={'historical_reuse':'REJECTED_CONTEXT_MISMATCH','target_3366668':[],'target_3366660':[]}
    matching=(old['document_identity'],old['geometry_revision'])==(current['document_identity'],current['geometry_revision']) and context_signature(space,old['requests'],old['configuration'])==context_signature(space,current['requests'],config)
    requests={r['route_id']:r for r in current['requests']};graph=RouteConflictGraph()
    for r in current['routes']:
        if r.get('points_mm') and r.get('status') in ('VALID','VALID_WITH_WARNINGS','CONDITIONAL'):
            graph.add(r['route_id'],r,settings.profile(settings.for_route(config,r['route_id'])).radius_mm,r['source']['id'])
    if matching:
        report['historical_reuse']='CONTEXT_MATCHED_REQUIRES_REVALIDATION'
        for r in old['routes']:
            if r['destination']['element_id']!=3366668 or not r.get('points_mm'):continue
            checked=validate_network(space,current['requests'],config,{r['route_id']:r})
            report['target_3366668'].append(checked)
            if checked['valid']:graph.add('historical:'+r['route_id'],r,settings.profile(settings.for_route(config,r['route_id'])).radius_mm,r['source']['id'])
    report['conflicts']=graph.export_state()
    deadline=time.monotonic()+args.seconds;manager=SearchBudgetManager(deadline)
    target=next(r for r in current['requests'] if r['destination']['element_id']==3366660)
    ports={p['id']:p for p in [target['source']]+target.get('source_alternatives',[])}
    for index,port in enumerate(sorted(ports.values(),key=lambda p:p['id'])):
        if manager.stopped():break
        candidate=plan_route(space,dict(target,source=port,attempt_number=index),settings.profile(settings.for_route(config,target['route_id'])),config,
            scenario='B' if config['structural_crossings']=='PROPOSAL_ONLY' else 'A',deadline=manager.allocate(port['id'],pending=len(ports)-index))
        report['target_3366660'].append({'source':port,'candidate':candidate})
    (ROOT/args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':main()
