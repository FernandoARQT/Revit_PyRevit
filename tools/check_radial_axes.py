"""Replay detailed axes on an approved schematic capture, outside Revit."""
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'ARQ-T.extension/lib'))
from ARQT_MEP.routing.design import model,axes,products


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--input',default='tests/artifacts/radial-v3-schematic-product.json')
    parser.add_argument('--output',default='tests/artifacts/radial-v3-axes.json');parser.add_argument('--seconds',type=float,default=120)
    parser.add_argument('--classic90',action='store_true');parser.add_argument('--main-exterior-mm',type=float);parser.add_argument('--main-radius-mm',type=float)
    args=parser.parse_args();p=model.RoutingDesignProposal.from_dict(json.loads((ROOT/args.input).read_text(encoding='utf8')))
    profile=products.classic(90) if args.classic90 else model.DuctProfile(**next(iter(p.profiles.values())))
    p.profiles={profile.profile_id:asdict(profile)}
    if args.main_exterior_mm:p.configuration['main_connection']['outer_diameter_mm']=args.main_exterior_mm
    if args.main_radius_mm:p.configuration['main_connection']['bend_radius_mm']=args.main_radius_mm
    result=axes.calculate(p,profile,{'seconds':args.seconds})
    (ROOT/args.output).write_text(json.dumps(result.to_dict(),ensure_ascii=False),encoding='utf8')
    print(json.dumps({'validation':result.validation,'routes':{rid:{'status':r['validation_status'],'reason':r.get('reason'),
        'errors':r.get('errors'),'attempts':r.get('search_attempts')} for rid,r in dict(result.individual_routes,**result.main_connections).items()}},ensure_ascii=False))


if __name__=='__main__':main()
