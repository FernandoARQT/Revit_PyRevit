"""Offline real-snapshot geometry regression; never a native network certificate."""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ARQ-T.extension/lib'))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--capture',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--seconds',type=float,default=10)
    parser.add_argument('--nodes',type=int,default=200)
    parser.add_argument('--nominal-mm',type=float,required=True)
    parser.add_argument('--inner-mm',type=float,required=True)
    parser.add_argument('--outer-mm',type=float,required=True)
    parser.add_argument('--bend-mm',type=float,required=True)
    args=parser.parse_args()
    from ARQT_MEP.routing.design.model import DuctProfile
    from ARQT_MEP.routing.design.graph import generate
    from ARQT_MEP.routing.core.spatial_model import build_spatial_model
    source=ROOT/args.capture;data=json.loads(source.read_text(encoding='utf8'));snapshot=data['snapshot']
    p=DuctProfile('geometry-envelope-test','SUPPLY',args.nominal_mm,args.inner_mm,args.outer_mm,0,args.bend_mm,
        'DECLARED TEST ENVELOPE; NOT INSTALLED PRODUCT DATA',False)
    # A raw geometry capture lacks Rooms and level provenance. This synthetic
    # grouping tests only 3D free-space edges, not native floor assignment.
    levels={r['id']:0 for r in snapshot['regions']}
    started=time.monotonic()
    if data.get('kind')=='RoutingDesignProposal':
        from ARQT_MEP.routing.design.model import RoutingDesignProposal
        from ARQT_MEP.routing.design.process import calculate
        result=calculate(RoutingDesignProposal.from_dict(data),{'seconds':args.seconds,'max_nodes':args.nodes,'spacing_mm':600})
        graph=result.corridor_graph
        level_mapping='NATIVE_CEILING_IDENTITIES_FROM_TRACER_CONTEXT'
        declared=next(iter(result.profiles.values()));radius=declared['external_diameter_mm']/2+declared['insulation_thickness_mm']+result.configuration['design_clearance_mm']
    else:
        graph=generate(snapshot,p,levels,spacing_mm=600,max_nodes=args.nodes,seconds=args.seconds)
        level_mapping='SYNTHETIC_GEOMETRY_TEST_ONLY';radius=args.outer_mm/2+10
    space=build_spatial_model(snapshot)
    failures=[cid for cid,e in graph['edges'].items() if not space.free(*e['reference_xyz_mm'],radius)]
    report={'source':args.capture,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'wall_seconds':time.monotonic()-started,'graph_stats':graph['stats'],
        'all_edges_rechecked':not failures,'failed_edges':failures,
        'level_mapping':level_mapping,'profile_verified':False,
        'native_validation':'NOT_RUN','physical_infeasibility_proven':False}
    output=ROOT/args.output;output.write_text(json.dumps({'report':report,'graph':graph},ensure_ascii=False),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
    if failures:raise AssertionError(failures)


if __name__=='__main__':main()
