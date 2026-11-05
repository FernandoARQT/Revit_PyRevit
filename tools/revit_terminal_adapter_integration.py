"""Run through MCP run_modify(dry_run=True): real adapter + FlexDuct + cap."""
import copy
import math
import time
from pyrevit.api import DB
from ARQT_MEP.routing import settings,service
from ARQT_MEP.routing.revit import extractor,terminal_adapters,proposals,network_model
from ARQT_MEP.routing.core.terminal_adapter import variants
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core.multi_route import plan_route
from ARQT_MEP import revit_read as rr


def run(doc,previous,network='SUPPLY',terminal_id=3384628,choice_index=0,distance_mm=900):
    group=DB.TransactionGroup(doc,'ARQ-T · Ensayo de adaptadores siempre revertido');group.Start()
    try:return _run(doc,previous,network,terminal_id,choice_index,distance_mm)
    finally:group.RollBack()


def _run(doc,previous,network,terminal_id,choice_index,distance_mm):
    config=settings.validate(previous['configuration'])
    catalog=extractor.get_mep_connection_points(doc,config)
    destinations=[dict(terminal_id=t['id'],port_id=t['connectors'][0]['id'],
        network_type=t['suggested_network_type'],include=True) for t in catalog['terminals']]
    prepared=terminal_adapters.prepare(doc,config,destinations)
    terminal=doc.GetElement(DB.ElementId(terminal_id))
    original=terminal_adapters.ports(terminal)[0]
    adapter=terminal_adapters.attached(terminal,original)[0]
    assignment=next(a for a in config['equipment_assignments'] if a['network_type']==network)
    distributor=doc.GetElement(DB.ElementId(int(assignment['element_id'])))
    capture=terminal_adapters.record(terminal,original,adapter)
    target=list(capture['pivot_mm']);target[0]-=900
    choice=variants(capture,{'xyz_mm':target},network)[choice_index]
    # A nearby disposable manifold isolates physical modeling from the
    # unrelated long-range search across the dwelling. Dry-run only.
    tx=DB.Transaction(doc,'Ensayo distribuidor temporal');tx.Start()
    try:
        copied=doc.GetElement(list(DB.ElementTransformUtils.CopyElement(doc,distributor.Id,DB.XYZ.Zero))[0])
        branch=min(terminal_adapters.ports(copied),key=lambda c:c.Radius);branch_id=branch.Id
        direction=DB.XYZ(-1,0,0);target=direction.Negate()
        current=branch.CoordinateSystem.BasisZ
        angle=math.atan2(DB.XYZ.BasisZ.DotProduct(current.CrossProduct(target)),current.DotProduct(target))
        DB.ElementTransformUtils.RotateElement(doc,copied.Id,DB.Line.CreateUnbound(branch.Origin,DB.XYZ.BasisZ),angle)
        doc.Regenerate();branch=next(c for c in terminal_adapters.ports(copied) if c.Id==branch_id)
        end=proposals.xyz(choice['xyz_mm']);wanted=end+direction.Multiply(rr.internal(distance_mm))
        DB.ElementTransformUtils.MoveElement(doc,copied.Id,wanted-branch.Origin);doc.Regenerate()
        tx.Commit()
    except Exception:
        if tx.GetStatus()==DB.TransactionStatus.Started:tx.RollBack()
        raise
    branch=next(c for c in terminal_adapters.ports(copied) if c.Id==branch_id)
    source=dict(terminal_adapters.descriptor(branch),kind='DISTRIBUTOR',network_type=network)
    rid='test:adapter-real-connection'
    config['equipment_assignments']=[]
    config['route_overrides'][rid]={'section':{'shape':'CIRCULAR','nominal_diameter_mm':90,'outer_diameter_mm':90,'insulation_mm':0}}
    config['parallel']['enabled']=False
    config=settings.validate(config)
    snapshot=extractor.analyze_mep_geometry(doc,config)
    request=dict(route_id=rid,route_kind='BRANCH',network_type=network,source=source,
        destination=choice,airflow_lps=8,compact_connections=True)
    space=build_spatial_model(snapshot,config['constraints']);local=settings.for_route(config,rid)
    core=plan_route(space,request,settings.profile(local),local,deadline=time.monotonic()+5)
    if len(core.get('points_mm',[]))<2:return {'prepared':prepared,'planning':core}
    route=dict(core,route_id=rid,route_kind='BRANCH',source=source,destination=choice,source_id=source['id'],destination_id=choice['id'],
        network_type=network,section=local['section'],clearance_mm=local['clearance_mm'],bend_radius_mm=local['bend_radius_mm'],
        collision_status=core['status'],native_status='NOT_EVALUATED',approval_status='DRAFT',units='mm',airflow_lps=8)
    proposal=copy.deepcopy(previous)
    proposal.update(configuration=config,document_identity=snapshot['document_identity'],geometry_revision=snapshot['geometry_revision'],
        requests=[request],routes=[route],destinations=[],origins=[],status='DRAFT')
    proposal=proposals.validate_mep_routes(doc,snapshot,service.seal(proposal))
    route=proposal['routes'][0]
    if route['native_status'] not in ('VALID','VALID_WITH_WARNINGS'):
        return {'prepared':prepared,'validation':route.get('native_checks'),'status':route['native_status']}
    route['approval_status']='APPROVED';proposal=service.seal(proposal)
    result=network_model.model(doc,proposal,explicit=True)
    return dict(prepared=prepared,created=result['created'],failures=result.get('failures'),connections=result['connections'],
        geometry_conflicts=result['geometry_conflicts'])
