"""Replay stage 1 over self-contained native captures, never touching Revit."""
import argparse
import json
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ARQ-T.extension/lib'))
from ARQT_MEP.routing.design import model,planner,workflow,schematic


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--seconds',type=float,default=120)
    parser.add_argument('--profile-mm',type=float);parser.add_argument('--output',default='tests/artifacts/radial-v3-schematic.json')
    parser.add_argument('--product',choices=['classic90'])
    args=parser.parse_args()
    p=model.RoutingDesignProposal.from_dict(json.loads((ROOT/'tests/artifacts/routing-v3-h2-capture.json').read_text(encoding='utf8')))
    data=json.loads((ROOT/'tests/artifacts/radial-v3-native-connections.json').read_text(encoding='utf8'));p.catalog=data['catalog']
    p.snapshot=json.loads((ROOT/'tests/artifacts/radial-v3-native-space.json').read_text(encoding='utf8'));p.geometry_revision=p.snapshot['geometry_revision']
    chosen=workflow.defaults(p.catalog,p.configuration)
    p.configuration.update(hrv_id=chosen['HRV'],equipment_assignments=[{'element_uid':chosen[n],'network_type':n} for n in ('SUPPLY','EXTRACT')])
    p.corridors={};p.corridor_assignments={};p.manual_edits=[];p.history=[];p.future=[]
    profile=model.DuctProfile(**next(iter(p.profiles.values())))
    if args.product:
        from ARQT_MEP.routing.design.products import classic
        profile=classic(90)
    if args.profile_mm:
        from dataclasses import replace
        profile=replace(profile,profile_id='EXPLICIT_ENVELOPE_TEST',nominal_diameter_mm=args.profile_mm,
            external_diameter_mm=args.profile_mm,internal_diameter_mm=None,geometry_verified=False,manufacturer_reference=None)
    started=time.monotonic();result=planner.calculate(p,profile,{'seconds':args.seconds});schematic.check(result)
    (ROOT/args.output).write_text(json.dumps(result.to_dict(),ensure_ascii=False),encoding='utf8')
    print(json.dumps({'seconds':time.monotonic()-started,'connected':len(result.corridor_assignments),'corridors':len(result.corridors),
        'diagnostics':result.connectivity,'graph':result.corridor_graph['stats']},ensure_ascii=False))


if __name__=='__main__':main()
