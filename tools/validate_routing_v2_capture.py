"""Read-only MCP helper: migrate only an exactly matching captured geometry."""
import copy
import json
from pathlib import Path
from ARQT_MEP.routing import service
from ARQT_MEP.routing.revit.extractor import analyze_mep_geometry
from ARQT_MEP.routing.revit.proposals import validate_mep_routes


def canonical(snapshot):
    fields=('id','element_id','element_uid','link_uid','category','construction_identity',
            'classification','box_mm','meshes','wall_axis_mm','wall_thickness_mm','crossing_multiplier')
    result={'regions':sorted(snapshot['regions'],key=lambda r:r['id']),
        'obstacles':sorted([dict({k:o.get(k) for k in fields},hard_forbidden=bool(o.get('hard_forbidden')))
            for o in snapshot['obstacles']],key=lambda o:o['id'])}
    # Revit's in-memory tuples and JSON arrays describe the same coordinates.
    # Normalize the representation, without rounding any geometric value.
    return json.loads(json.dumps(result,sort_keys=True,allow_nan=False))


def run(doc,root_path,source,output):
    root=Path(root_path)
    proposal=json.loads((root/source).read_text(encoding='utf8'));service.check(proposal)
    captured=json.loads((root/'tests/artifacts/routing-current-snapshot.json').read_text(encoding='utf8'))
    fresh=analyze_mep_geometry(doc,proposal['configuration'])
    if captured['document_identity']!=fresh['document_identity'] or canonical(captured)!=canonical(fresh):
        raise ValueError('La geometría o las reglas constructivas han cambiado; se exige recalcular.')
    previous=proposal['geometry_revision'];proposal=copy.deepcopy(proposal)
    proposal['geometry_revision']=fresh['geometry_revision']
    proposal['capture_migration']={'from':previous,'to':fresh['geometry_revision'],
        'basis':'Exact equality of captured regions, obstacle solids, identities and mandatory construction policy'}
    for route in proposal['routes']:
        route['approval_status']='DRAFT';route['native_status']='NOT_EVALUATED'
    result=validate_mep_routes(doc,fresh,service.seal(proposal))
    result['benchmark']=dict(result.get('benchmark',{}),native_validation='REVIT_CENTRAL_ROUTE_SOLIDS_ONLY')
    result=service.seal(result)
    (root/output).write_text(json.dumps(result,ensure_ascii=False),encoding='utf8')
    return {'summary':result['summary'],'routes':[{'element_id':r['destination']['element_id'],
        'native_status':r['native_status'],'errors':r.get('native_checks',{}).get('errors',[])} for r in result['routes']]}
