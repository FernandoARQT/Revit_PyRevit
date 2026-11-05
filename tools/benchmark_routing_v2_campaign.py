"""Sequential, isolated benchmark campaign; leaves two logical CPUs available.

Each child engine owns its configured CPU-minus-two pool. Campaign jobs never
run concurrently, avoiding oversubscribed timing comparisons.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--budgets',default='120,180,240,600,1000')
    parser.add_argument('--ablations',action='store_true')
    parser.add_argument('--staged',action='store_true',help='Single logical continuation at accumulated 120/240/600/1000 seconds')
    parser.add_argument('--snapshot',default='tests/artifacts/routing-v2-fresh-snapshot.json')
    parser.add_argument('--prefix',default='routing-v2-final')
    args=parser.parse_args()
    jobs=[(str(int(float(b))),float(b),[]) for b in args.budgets.split(',') if b.strip()]
    if args.staged:jobs=[('stage-120',120,[]),('stage-240',120,[]),('stage-600',360,[]),('stage-1000',400,[])]
    if args.staged and args.ablations:raise ValueError('Run independent ablations separately from continuation.')
    if args.ablations:
        jobs.extend([('without-'+name,120,['--no-'+name]) for name in ('topology','iris','lazy','coordination','rrt')])
    report={'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
        'snapshot':args.snapshot,'processes':'One fresh process per case; cases run sequentially','cases':[]}
    output=ROOT/'tests/artifacts'/f'{args.prefix}-campaign.json'
    previous=None;accumulated=0
    for label,seconds,options in jobs:
        filename=f'tests/artifacts/{args.prefix}-{label}.json'
        print(f'START {label}: {seconds:g} seconds',flush=True)
        command=[sys.executable,str(ROOT/'tools/benchmark_routing_v2.py'),'--seconds',str(seconds),
            '--snapshot',args.snapshot,'--output',filename]+options
        if args.staged and previous:command.extend(['--resume',previous])
        started=time.monotonic()
        completed=subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
        if completed.returncode:
            report['cases'].append({'case':label,'error':completed.stderr[-6000:],'returncode':completed.returncode})
            output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
            raise RuntimeError(completed.stderr)
        proposal=json.loads((ROOT/filename).read_text(encoding='utf8'))
        result=dict(proposal['benchmark'],case=label,file=filename,process_seconds=time.monotonic()-started)
        accumulated+=seconds
        if args.staged:
            result['accumulated_search_budget_seconds']=accumulated
            result['incumbent_state']=proposal.get('planner_state',{}).get('incumbent',{})
            previous=filename
        report['cases'].append(result)
        report['completed_cases']=len(report['cases']);report['total_cases']=len(jobs)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps({'case':label,'summary':result['summary'],'wall_seconds':result['wall_seconds']},ensure_ascii=False),flush=True)
    report['completed']=True
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':main()
