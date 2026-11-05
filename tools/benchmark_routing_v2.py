"""One isolated captured-scene benchmark. No Revit access or model writes."""
import argparse
import json
import sys
import time
import hashlib
import platform
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ARQ-T.extension/lib'))
from ARQT_MEP.routing import service,settings


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--seconds',type=float,default=240)
    parser.add_argument('--output',default='tests/artifacts/routing-v2-latest.json')
    parser.add_argument('--resume')
    parser.add_argument('--merge',action='append',default=[])
    parser.add_argument('--seed',type=int,default=0)
    parser.add_argument('--snapshot',default='tests/artifacts/routing-current-snapshot.json')
    parser.add_argument('--iterations',type=int,default=3)
    parser.add_argument('--rrt-only',action='store_true')
    for option in ('topology','iris','lazy','coordination','rrt'):parser.add_argument('--no-'+option,action='store_true')
    args=parser.parse_args()
    folder=ROOT/'tests/artifacts'
    source=json.loads((folder/'routing-budget-probe-v3.json').read_text(encoding='utf8'))
    snapshot_path=ROOT/args.snapshot
    snapshot=json.loads(snapshot_path.read_text(encoding='utf8'))
    catalog=json.loads((folder/'routing-current-catalog.json').read_text(encoding='utf8'))
    config=settings.validate(source['configuration']);config['random_seed']=args.seed
    config['search']['max_total_seconds']=args.seconds
    config['search'].update(max_iterations=args.iterations,max_seconds=20)
    config['coordination'].update(topology_enabled=not args.no_topology,iris_gcs_enabled=not args.no_iris,
        incremental_enabled=not args.no_lazy,enabled=not args.no_coordination,rrt_connect_enabled=not args.no_rrt)
    if args.rrt_only:config['coordination'].update(rrt_connect_enabled=True,incremental_enabled=False,iris_gcs_enabled=False,roadmap_enabled=False)
    existing=json.loads((ROOT/args.resume).read_text(encoding='utf8')) if args.resume else None
    if existing and (existing['geometry_revision']!=snapshot['geometry_revision'] or existing['document_identity']!=snapshot['document_identity']):
        raise ValueError('El checkpoint no pertenece a la geometría capturada.')
    for filename in args.merge:
        other=json.loads((ROOT/filename).read_text(encoding='utf8'))
        service.check(other)
        if other['geometry_revision']!=snapshot['geometry_revision'] or other['document_identity']!=snapshot['document_identity']:
            raise ValueError('Los candidatos pertenecen a otra geometría.')
        if existing is None:existing={'document_identity':snapshot['document_identity'],'geometry_revision':snapshot['geometry_revision'],'routes':[]}
        bank=existing.setdefault('candidate_bank',{})
        for route in other['routes']:
            if route.get('status') in ('VALID','VALID_WITH_WARNINGS','CONDITIONAL'):
                route=dict(route,chosen_source=route.get('chosen_source') or route['source'])
                bank.setdefault(route['route_id'],[]).append(route)
    started=time.monotonic()
    result=service.create_mep_route_proposal(snapshot,catalog,source['origins'],source['destinations'],config,existing)
    report={'budget_seconds':args.seconds,'iterations_limit':args.iterations,'wall_seconds':time.monotonic()-started,
        'snapshot_file':args.snapshot,'snapshot_sha256':hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
        'engine_code_sha256':hashlib.sha256(b''.join(str(p.relative_to(ROOT)).encode()+p.read_bytes()
            for p in sorted((ROOT/'ARQ-T.extension/lib/ARQT_MEP/routing').rglob('*.py')))).hexdigest(),
        'python_version':platform.python_version(),'logical_processors':os.cpu_count(),
        'cache_mode':'JSON_CHECKPOINT_RESUME' if existing else 'FRESH_PROCESS_STATIC_CACHE',
        'merged_candidates':args.merge,
        'seed':args.seed,'summary':result['summary'],'native_validation':'NOT_RUN',
        'features':{k:config['coordination'].get(k,False) for k in ('topology_enabled','iris_gcs_enabled','incremental_enabled','enabled','rrt_connect_enabled')},
        'routes':[{'terminal':r['destination']['element_id'],'status':r['status'],'method':r.get('search_method'),
                   'reason':r.get('reason'),'attempts':r.get('attempts')} for r in result['routes']]}
    result['benchmark']=report;result=service.seal(result)
    output=ROOT/args.output
    output.write_text(json.dumps(result,ensure_ascii=False),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k!='routes'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
