"""Reuse a certified corridor with a newly checked manifold approach."""
import copy
from . import geometry as g
from .route_geometry import validate_route_geometry


def reassign(space,candidate,port,request,profile,configuration):
    if not request.get('compact_connections') or not candidate.get('connection_validation_pending'):return None
    from .multi_route import compact_anchor
    anchor=compact_anchor(space,port,profile,configuration)
    old=candidate.get('routing_points_mm')
    if anchor is None or not old or len(old)<2:return None
    from .terminal_adapter import destination
    target_port=destination(candidate,request)
    origin,entry=anchor;core=[entry]+list(old[1:]);target=target_port['xyz_mm']
    controls=([origin] if g.distance(origin,entry)>1e-6 else [])+core
    if g.distance(core[-1],target)>1e-6:controls.append(target)
    result=copy.deepcopy(candidate);result.update(points_mm=controls,routing_points_mm=core,chosen_source=copy.deepcopy(port))
    result.update(source=copy.deepcopy(port),source_id=port['id'],native_status='NOT_EVALUATED',approval_status='DRAFT',
        source_docking={'origin_mm':origin,'bulk_anchor_mm':entry,'native_compatibility':'PENDING_REAL_FITTING_VERIFICATION'})
    diameter=configuration['section'].get('nominal_diameter_mm')
    result['required_transitions']=[{'endpoint':end,'from_mm':p['size']['diameter_mm'],'to_mm':diameter}
        for end,p in (('source',port),('destination',target_port))
        if diameter and p.get('size',{}).get('diameter_mm') and abs(p['size']['diameter_mm']-diameter)>.01]
    result['connection_anchors_mm']=dict(result.get('connection_anchors_mm',{}),source=entry)
    prior=candidate.get('chosen_source') or candidate.get('source') or request['source']
    for stub in result.get('endpoint_stubs',[]):
        if g.distance(stub['points_mm'][0],prior['xyz_mm'])<.01:
            stub.update(points_mm=[origin,entry],ignore_owner_ids=[port['element_key']] if port.get('element_key') else [])
    checked=validate_route_geometry(space,result,profile)
    if checked['status'] in ('INVALID','NOT_EVALUATED'):return None
    result.update(checked);result.update(search_method='VALIDATED_SOURCE_PREFIX_REPAIR',
        original_search_method=candidate.get('search_method'),source_reassigned_from=prior['id'])
    result.pop('native_checks',None);return result
