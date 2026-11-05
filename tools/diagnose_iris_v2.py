"""Bounded independent IRIS diagnostics on captured geometry; never imports Revit."""
import collections
import json
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ARQ-T.extension/lib'))
from ARQT_MEP.routing.core.iris_gcs import find_gcs
from ARQT_MEP.routing.core.multi_route import compact_anchor
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.settings import validate,for_route,profile


def main():
    proposal=json.loads((ROOT/'tests/artifacts/routing-network-coordination.json').read_text(encoding='utf8'))
    snapshot=json.loads((ROOT/'tests/artifacts/routing-current-snapshot.json').read_text(encoding='utf8'))
    config=validate(proposal['configuration']);space=build_spatial_model(snapshot,config['constraints'])
    records=[]
    for route in proposal['routes']:
        if route['status']!='NOT_EVALUATED':continue
        local=for_route(config,route['route_id']);p=profile(local)
        anchors=[compact_anchor(space,route[end],p,local) for end in ('source','destination')]
        if not all(anchors):
            records.append({'destination':route['destination']['element_id'],'reason':'NO_FREE_BULK_ANCHOR'});continue
        started=time.monotonic()
        result=find_gcs(space,anchors[0][1],anchors[1][1],p,local,deadline=started+10)
        record={'destination':route['destination']['element_id'],'seconds':time.monotonic()-started,
                'status':result['status'],'reason':result.get('reason'),'diagnostics':result.get('iris_diagnostics'),
                'source_covered':result.get('source_covered'),'destination_covered':result.get('destination_covered')}
        records.append(record)
        summary=collections.Counter(r.get('status') for r in (record['diagnostics'] or {}).get('seed_attempts',[]))
        print(json.dumps({'destination':record['destination'],'reason':record['reason'],
                          'seed_outcomes':dict(summary)},ensure_ascii=False),flush=True)
    (ROOT/'tests/artifacts/routing-v2-iris-diagnostics.json').write_text(json.dumps(records,ensure_ascii=False),encoding='utf8')


if __name__=='__main__':main()
