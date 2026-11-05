"""Native bodies in an eroded domain; conservative obstacle clearances.

Offsetting the authorized domain avoids reconstructing a nominal sweep around
an actual FlexDuct spline. Every uncertainty still blocks route commitment.
"""
from pyrevit.api import DB
from ... import revit_read as rr
from ..core import geometry as g
from ..core.spatial_model import build_spatial_model
from .extractor import resolve_solids
from .proposals import _solid_box
from .authorized_volume import AuthorizedVolume,oriented_clearance_bound,box_solid


def intersect(a,b):
    return DB.BooleanOperationsUtils.ExecuteBooleanOperation(a,b,DB.BooleanOperationsType.Intersect)


def subtract(a,b):
    return DB.BooleanOperationsUtils.ExecuteBooleanOperation(a,b,DB.BooleanOperationsType.Difference)


def inspect(doc,parts,snapshot,ignore_element_ids=(),constraints=None,passages=(),clearance_mm=0,connection_envelopes=(),solids_by_id=None):
    from .network_model import owned,manager
    members={rr.eid(p.Id) for p in parts};result=[];cache={};previous=[];scopes={}
    owned_clearance={i:data.get('clearance_mm',0) for _,data in owned(doc) for i in data.get('members',[])}
    space=build_spatial_model(snapshot,constraints)
    # Eroding each certified cover yields a subset of the union's erosion:
    # conservative at difficult joins, never a clearance exemption.
    authorized=AuthorizedVolume(space,clearance_mm,passages)
    for envelope in connection_envelopes:
        scopes.setdefault(envelope['element_id'],[]).append((envelope['box_mm'],box_solid(envelope['box_mm'])))
    for part in parts:
        element_id=rr.eid(part.Id);solids=(solids_by_id or {}).get(element_id)
        if solids is None:solids=rr.solids(part)
        from .terminal_adapters import mount_scope
        collar=mount_scope(doc,part,clearance_mm) if isinstance(part,DB.FamilyInstance) else None
        if not solids:
            result.append({'element_id':element_id,'status':'GEOMETRIA_NO_COMPROBADA','reason':'Sin solidos nativos.','hard_forbidden':True})
            continue
        cm=manager(part)
        adjacent={rr.eid(ref.Owner.Id) for connector in cm.Connectors for ref in connector.AllRefs
                  if ref.ConnectorType==DB.ConnectorType.End} if cm else set()
        for other_id,other_solids in previous:
            if other_id in adjacent:continue
            for solid in solids:
                for other in other_solids:
                    if not g.overlaps(_solid_box(solid),_solid_box(other)):continue
                    try:
                        if intersect(solid,other).Volume>1e-9:
                            result.append({'element_id':element_id,'conflicting_element_id':other_id,
                                'status':'AUTOINTERSECCION_NATIVA_NO_ADYACENTE','hard_forbidden':True})
                    except Exception as exc:
                        result.append({'element_id':element_id,'status':'AUTOINTERSECCION_NO_COMPROBADA','reason':str(exc),'hard_forbidden':True})
        previous.append((element_id,solids))
        for solid in solids:
            try:
                # The grille collar may cross its mounting face. The rest of
                # the adapter and every duct remain above that face.
                contained=subtract(solid,collar) if collar else solid
                containment=authorized.contains(contained) if contained.Volume>1e-9 else {'verified':True}
                if not containment['verified']:
                    result.append({'element_id':element_id,'status':'FUERA_DEL_VOLUMEN_CON_HOLGURA_O_NO_VERIFICADO',
                        'hard_forbidden':True,'clearance_mm':clearance_mm,'containment':containment,
                        'method':'ACTUAL_NATIVE_BODY_IN_ERODED_AUTHORIZED_UNION'})
                for zone in space.forbidden:
                    expanded=g.expand(zone,clearance_mm)
                    if g.overlaps(_solid_box(solid),expanded) and intersect(solid,box_solid(expanded)).Volume>1e-9:
                        result.append({'element_id':element_id,'status':'REGION_PROHIBIDA_O_SU_HOLGURA','hard_forbidden':True})
            except Exception as exc:
                result.append({'element_id':element_id,'status':'GEOMETRIA_NO_COMPROBADA','reason':str(exc),'hard_forbidden':True})
        bounds=[_solid_box(s) for s in solids]
        query=tuple(min(b[i] for b in bounds) for i in range(3))+tuple(max(b[i+3] for b in bounds) for i in range(3))
        for obstacle in snapshot['obstacles']:
            if obstacle.get('element_id') in members and not obstacle.get('link_uid'):continue
            margin=max(clearance_mm,(owned_clearance.get(obstacle.get('element_id'),0) if not obstacle.get('link_uid') else 0))
            if not g.overlaps(query,g.expand(obstacle['box_mm'],margin)):continue
            try:
                if obstacle['id'] not in cache:
                    actual=resolve_solids(doc,obstacle)
                    if not actual:raise ValueError('Sin solidos nativos del obstaculo.')
                    cache[obstacle['id']]=(actual,[oriented_clearance_bound(s,margin) for s in actual] if margin else actual)
                actual,envelopes=cache[obstacle['id']];intersection=None
                for envelope in envelopes:
                    checked=envelope
                    if not obstacle.get('hard_forbidden'):
                        if not obstacle.get('link_uid') and obstacle.get('element_id') in ignore_element_ids:
                            # Subtract local scopes from the simple obstacle
                            # envelope, avoiding a cut through a complex spline.
                            for _,scope in scopes.get(obstacle['element_id'],[]):checked=subtract(checked,scope)
                        for portal,bore in authorized.bores:
                            if obstacle['id'] in portal['element_ids']:checked=subtract(checked,bore)
                    for solid in solids:
                        overlap=intersect(solid,checked)
                        if overlap.Volume>1e-9:intersection=overlap;break
                    if intersection is not None:break
                if intersection is None:continue
                physical=None
                # Equipment scopes and bore cylinders are valid intentional
                # contacts; don't report their original solid as a collision.
                if obstacle.get('hard_forbidden') or (obstacle.get('element_id') not in ignore_element_ids and
                    not any(obstacle['id'] in p['element_ids'] for p,_ in authorized.bores)):
                    try:physical=any(intersect(s,o).Volume>1e-9 for s in solids for o in actual)
                    except Exception:physical=None
                point=intersection.ComputeCentroid()
                result.append({'element_id':element_id,'conflicting_element_id':obstacle.get('element_id'),
                    'conflicting_element_key':obstacle['id'],'conflicting_name':obstacle.get('name'),
                    'status':'COLISION_CUERPO_NATIVO' if physical else 'HOLGURA_NO_CERTIFICADA_CON_ENVOLVENTE_CONSERVADORA',
                    'physical_intersection_confirmed':physical,'hard_forbidden':bool(obstacle.get('hard_forbidden')),
                    'clearance_mm':margin,'position_mm':[rr.mm(point.X),rr.mm(point.Y),rr.mm(point.Z)],
                    'envelope_method':'NATIVE_ORIENTED_BOUND_WITH_CLEARANCE' if margin else 'ACTUAL_NATIVE_SOLIDS'})
            except Exception as exc:
                result.append({'element_id':element_id,'conflicting_element_id':obstacle.get('element_id'),
                    'status':'GEOMETRIA_NO_COMPROBADA','reason':str(exc),'hard_forbidden':True})
    return result
