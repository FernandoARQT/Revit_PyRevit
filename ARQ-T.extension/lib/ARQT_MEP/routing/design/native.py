"""Atomic, explicitly approved materialisation using existing native helpers."""
import copy
import math
from pyrevit.api import DB
from System.Collections.Generic import List
from ... import revit_read as rr,ownership
from ..revit import extractor,proposals,network_model
from ..revit.connection_points import descriptor
from ..revit.transitions import connect
from ..core import geometry as g
from ..core.natural_path import interpolation_supports
from . import endpoints,native_plan


KIND='RADIAL_V3_NETWORK'


def _resolve(doc,port,network,require_free=True):
    element=doc.GetElement(port['element_key'])
    if element is None:raise ValueError('CONNECTOR_OWNER_MISSING')
    manager=network_model.manager(element)
    current=[descriptor(c) for c in manager.Connectors if c.Domain==DB.Domain.DomainHvac and c.ConnectorType==DB.ConnectorType.End]
    saved=endpoints.endpoint(port,network)
    from dataclasses import asdict
    fresh=endpoints.resolve(asdict(saved),current)
    descriptor_now=next(p for p in current if p['connector_id']==fresh.native_connector_id)
    if any(port.get(key) is not None and port[key]!=descriptor_now.get(key) for key in ('classification','flow_direction')):
        raise ValueError('NATIVE_CONNECTOR_FUNCTION_CHANGED')
    if g.distance(fresh.origin_xyz_mm,port['xyz_mm'])>.1 or g.dot(fresh.connector_normal_xyz,g.unit(port['direction']))<.99999:
        raise ValueError('OUTDATED_CONNECTOR_POSITION_OR_ORIENTATION')
    connector=next(c for c in manager.Connectors if int(c.Id)==fresh.native_connector_id)
    if require_free and connector.IsConnected:raise ValueError('CONNECTOR_OCCUPIED')
    return connector


def _terminal_state(catalog,terminal_ids):
    result={}
    for terminal in catalog['terminals']:
        if terminal['id'] not in terminal_ids:continue
        result[terminal['id']]={k:copy.deepcopy(terminal.get(k)) for k in ('transform','airflow_lps','airflow_candidates')}
        result[terminal['id']]['connectors']=[{k:copy.deepcopy(port.get(k)) for k in ('id','xyz_mm','direction','physical_refs')} for port in terminal['connectors']]
    if set(result)!=set(terminal_ids):raise ValueError('PROTECTED_TERMINAL_MISSING')
    return result


def _assert_terminal_state(before,after):
    for tid,old in before.items():
        new=after.get(tid)
        if new is None:raise ValueError('PROTECTED_TERMINAL_MISSING')
        for key in ('transform','connectors','airflow_lps','airflow_candidates'):
            # Descriptors are read through the same extractor before/after;
            # even unintended parameter changes must roll the whole group back.
            if old[key]!=new[key]:raise ValueError('PROTECTED_TERMINAL_CHANGED: '+tid+' / '+key)


def _joint_bodies(created,routes):
    """Certify actual cross-route bodies and a conservative surface clearance.

    Subdivision tightens bounds without dropping any part of a native solid.
    An ambiguous boolean or clearance bound blocks the atomic operation.
    """
    from ..revit.geometry_conflicts import intersect
    from ..revit.authorized_volume import box_solid,oriented_clearance_bound
    from ..revit.proposals import _solid_box
    bodies=[[s for element in record['ducts']+record['fittings'] for s in rr.solids(element)] for record in created]
    def clear(a,b,margin,depth=0):
        bounds=_solid_box(a)
        if not g.overlaps(g.expand(bounds,margin),_solid_box(b)):return True
        if intersect(a,b).Volume>1e-9:return False
        if margin<=0:return True
        if intersect(oriented_clearance_bound(a,margin),b).Volume<=1e-9:return True
        sizes=[bounds[i+3]-bounds[i] for i in range(3)];axis=max(range(3),key=sizes.__getitem__)
        if depth>=14 or sizes[axis]<20:return False
        middle=(bounds[axis]+bounds[axis+3])/2
        for lower in (True,False):
            box=list(bounds)
            box[axis+3 if lower else axis]=middle
            piece=intersect(a,box_solid(g.expand(box,.00001)))
            if piece.Volume>1e-9 and not clear(piece,b,margin,depth+1):return False
        return True
    for i,own in enumerate(bodies):
        for j,other in enumerate(bodies[:i]):
            margin=max(routes[i]['clearance_mm'],routes[j]['clearance_mm'])
            if not own or not other:raise ValueError('NATIVE_JOINT_BODY_UNAVAILABLE')
            if any(not clear(a,b,margin) for a in own for b in other):
                raise ValueError('NATIVE_JOINT_COLLISION_OR_UNVERIFIED_CLEARANCE: '+routes[i]['route_id']+' / '+routes[j]['route_id'])
    return {'verified':True,'route_pairs':len(routes)*(len(routes)-1)//2,'method':'ACTUAL_NATIVE_SOLIDS_WITH_SUBDIVIDED_CONSERVATIVE_CLEARANCE_BOUNDS'}


def _move_equipment(doc,movements):
    for change in movements:
        element=doc.GetElement(DB.ElementId(change['element_id']));original=change['original_transform'];proposed=change['proposed_transform']
        _modifiable(element)
        current=element.GetTransform();origin=[rr.mm(current.Origin.X),rr.mm(current.Origin.Y),rr.mm(current.Origin.Z)]
        if g.distance(origin,original['origin_mm'])>.1:raise ValueError('VIRTUAL_MANIFOLD_OUTDATED')
        for name,vector in (('x',current.BasisX),('y',current.BasisY),('z',current.BasisZ)):
            if g.distance((vector.X,vector.Y,vector.Z),original['basis_'+name])>1e-6:raise ValueError('VIRTUAL_MANIFOLD_ORIENTATION_OUTDATED')
        axis=max(('x','y','z'),key=lambda name:math.hypot(*original['basis_'+name][:2]))
        before=original['basis_'+axis];after=proposed['basis_'+axis];angle=math.atan2(before[0]*after[1]-before[1]*after[0],before[0]*after[0]+before[1]*after[1])
        if abs(angle)>1e-8:DB.ElementTransformUtils.RotateElement(doc,element.Id,DB.Line.CreateUnbound(current.Origin,DB.XYZ.BasisZ),angle)
        delta=g.sub(proposed['origin_mm'],original['origin_mm'])
        if g.norm(delta)>1e-5:DB.ElementTransformUtils.MoveElement(doc,element.Id,proposals.xyz(delta))
    doc.Regenerate()


def _modifiable(element):
    if element is None:raise ValueError('TRANSFORM_ELEMENT_MISSING')
    if element.Pinned:raise ValueError('TRANSFORM_ELEMENT_PINNED')
    if element.GroupId!=DB.ElementId.InvalidElementId:raise ValueError('TRANSFORM_ELEMENT_GROUPED')
    if getattr(element,'Host',None) is not None:raise ValueError('TRANSFORM_ELEMENT_HOSTED')
    if element.Document.IsReadOnly or not element.IsModifiable:raise ValueError('TRANSFORM_ELEMENT_READ_ONLY')


def _rotate_elbows(doc,routes):
    from ..revit.terminal_adapters import apply_pose
    moved=[]
    for route in routes:
        pose=route.get('elbow_pose')
        if not pose or abs(pose['rotation_rad'])<1e-8:continue
        element=doc.GetElement(pose['element_key']);_modifiable(element)
        apply_pose(doc,route['destination']);moved.append(element)
    doc.Regenerate()
    return moved


def _curve_check(duct,route,tolerance_mm):
    curve=getattr(getattr(duct,'Location',None),'Curve',None)
    if curve is None:return {'verified':False,'reason':'NATIVE_AXIS_CURVE_UNAVAILABLE'}
    minimum=float('inf');deviation=0.;samples=0
    reference=[q for piece in route.get('primitives',[]) for q in piece['points_mm']]
    if len(reference)<2:return {'verified':False,'reason':'REFERENCE_PRIMITIVES_MISSING'}
    try:
        for i in range(257):
            u=i/256;frame=curve.ComputeDerivatives(u,True);v=frame.BasisX;a=frame.BasisY;cross=v.CrossProduct(a).GetLength()
            if cross>1e-12:minimum=min(minimum,rr.mm(v.GetLength()**3/cross))
            xyz=curve.Evaluate(u,True);p=(rr.mm(xyz.X),rr.mm(xyz.Y),rr.mm(xyz.Z))
            deviation=max(deviation,min(g.point_segment(x,y,p) for x,y in zip(reference,reference[1:])));samples+=1
        sampled_ok=minimum+1e-3>=route['bend_radius_mm'] and deviation<=tolerance_mm
        bounds=[]
        if sampled_ok and isinstance(curve,DB.HermiteSpline):
            from ..core.bezier_bounds import certify
            parameters=list(curve.Parameters);lo=curve.GetEndParameter(0);hi=curve.GetEndParameter(1)
            parameters=sorted(set([lo,hi]+[v for v in parameters if lo<v<hi]))
            sagitta=max((q.get('sagitta_mm',0.) for q in route.get('primitives',[])),default=0.)
            segments=list(zip(reference,reference[1:]));tolerance=tolerance_mm-sagitta
            if tolerance<=0:return {'verified':False,'reason':'REFERENCE_SAMPLING_TOO_COARSE_FOR_TOLERANCE'}
            for a,b in zip(parameters,parameters[1:]):
                start=curve.Evaluate(a,False);end=curve.Evaluate(b,False)
                va=curve.ComputeDerivatives(a,False).BasisX.Multiply((b-a)/3)
                vb=curve.ComputeDerivatives(b,False).BasisX.Multiply((b-a)/3)
                controls=[[rr.mm(q.X),rr.mm(q.Y),rr.mm(q.Z)] for q in (start,start+va,end-vb,end)]
                # Confirm the native interval is represented by this cubic.
                for t in (.25,.5,.75):
                    expected=[sum(controls[j][k]*w for j,w in enumerate(((1-t)**3,3*(1-t)**2*t,3*(1-t)*t*t,t**3))) for k in range(3)]
                    actual=curve.Evaluate(a+(b-a)*t,False)
                    if g.distance(expected,[rr.mm(actual.X),rr.mm(actual.Y),rr.mm(actual.Z)])>.0001:
                        return {'verified':False,'reason':'NATIVE_CUBIC_RECONSTRUCTION_MISMATCH'}
                bounds.append(certify(controls,route['bend_radius_mm'],segments,tolerance))
        return {'verified':sampled_ok and bool(bounds) and all(v['verified'] for v in bounds),
            'minimum_sampled_radius_mm':None if math.isinf(minimum) else minimum,'maximum_axis_deviation_mm':deviation,
            'samples':samples,'continuous_bounds':bounds,'method':'NATIVE_HERMITE_CUBIC_CONTINUOUS_BOUNDS',
            'scope':'Continuous curvature and reference capsule bounds; body containment and clashes checked independently.'}
    except Exception as exc:return {'verified':False,'reason':'NATIVE_AXIS_CHECK_FAILED','detail':str(exc)}


def _create(doc,route,cfg):
    network=route['network_type'];source=_resolve(doc,route['source'],network);destination=_resolve(doc,route['destination'],network)
    system_id=network_model._system_type(doc,network,cfg);level_id=DB.ElementId(route['level_id']);diameter=route['section']['nominal_diameter_mm']
    joints=[];fittings=[]
    if route.get('route_kind')=='MAIN':
        ducts,joints,errors,fittings=network_model._rigid(doc,route,cfg,system_id,level_id,diameter)
        if errors:raise ValueError(str(errors))
    else:
        flexes=[t for t in DB.FilteredElementCollector(doc).OfClass(DB.Mechanical.FlexDuctType) if rr.name(t)==cfg['flexible_duct']['type_name'] and str(t.Shape)=='Round']
        if len(flexes)!=1:raise ValueError('FLEX_TYPE_UNRESOLVED')
        points=List[DB.XYZ]()
        for q in interpolation_supports(route['primitives'],min(30.,route['bend_radius_mm']/5)):points.Add(proposals.xyz(q))
        duct=DB.Mechanical.FlexDuct.Create(doc,system_id,flexes[0].Id,level_id,source.CoordinateSystem.BasisZ,destination.CoordinateSystem.BasisZ.Negate(),points)
        parameter=duct.get_Parameter(DB.BuiltInParameter.RBS_CURVE_DIAMETER_PARAM)
        if parameter.IsReadOnly:raise ValueError('INSTANCE_DIAMETER_READ_ONLY')
        parameter.Set(rr.internal(diameter));ducts=[duct]
    doc.Regenerate()
    first=min(network_model.manager(ducts[0]).Connectors,key=lambda c:c.Origin.DistanceTo(source.Origin))
    last=min(network_model.manager(ducts[-1]).Connectors,key=lambda c:c.Origin.DistanceTo(destination.Origin))
    if first.Owner.Id==last.Owner.Id and first.Id==last.Id:raise ValueError('NATIVE_ENDS_NOT_DISTINCT')
    configured=extractor.configured_model_catalog(doc,cfg)['flexible_duct'];scopes=[]
    for curve_port,target in ((first,source),(last,destination)):
        fitting=connect(doc,curve_port,target,configured.get('transition_type_id'))
        if fitting:fittings.append(fitting)
        a=[rr.mm(curve_port.Origin.X),rr.mm(curve_port.Origin.Y),rr.mm(curve_port.Origin.Z)]
        b=[rr.mm(target.Origin.X),rr.mm(target.Origin.Y),rr.mm(target.Origin.Z)]
        bound=extractor._box(fitting) if fitting else g.expand(g.bounds([a,b]),rr.mm(max(curve_port.Radius,target.Radius))+route['clearance_mm'])
        scopes.append({'element_id':rr.eid(target.Owner.Id),'box_mm':bound})
    doc.Regenerate()
    for duct in ducts:
        if abs(rr.mm(duct.get_Parameter(DB.BuiltInParameter.RBS_CURVE_DIAMETER_PARAM).AsDouble())-diameter)>.01:raise ValueError('NATIVE_DIAMETER_CHANGED')
    if not network_model.connected(first,source) or not network_model.connected(last,destination):raise ValueError('NATIVE_CONNECTION_INCORRECT')
    curve_checks=[_curve_check(d,route,cfg.get('native_axis_deviation_mm',5.)) for d in ducts if isinstance(d,DB.Mechanical.FlexDuct)]
    if any(not c['verified'] for c in curve_checks):raise ValueError('NATIVE_AXIS_NOT_VERIFIED: '+str(curve_checks))
    return {'route_id':route['route_id'],'ducts':ducts,'fittings':fittings,'joints':joints,'curve_checks':curve_checks,
        'end_ids':{'source':int(first.Id),'destination':int(last.Id)},
        'end_element_ids':{'source':rr.eid(ducts[0].Id),'destination':rr.eid(ducts[-1].Id)},'connection_envelopes':scopes}


def _principal_continuity(doc,port,hrv_uid,network):
    start=network_model.resolve(doc,port);queue=[start];seen=set();count=0
    while queue and count<256:
        connector=queue.pop();key=(rr.eid(connector.Owner.Id),int(connector.Id))
        if key in seen:continue
        seen.add(key);count+=1
        for ref in connector.AllRefs:
            if ref.ConnectorType!=DB.ConnectorType.End or ref.Owner.Id==connector.Owner.Id or not connector.IsConnectedTo(ref):continue
            owner=ref.Owner
            if owner.UniqueId==hrv_uid:
                classification=str(ref.DuctSystemType)
                return classification=='SupplyAir' if network=='SUPPLY' else classification in ('ReturnAir','ExhaustAir')
            if not isinstance(owner,(DB.Mechanical.Duct,DB.Mechanical.FlexDuct)) and (not owner.Category or rr.eid(owner.Category.Id) not in (int(DB.BuiltInCategory.OST_DuctFitting),int(DB.BuiltInCategory.OST_DuctAccessory))):continue
            manager=network_model.manager(owner)
            if manager:queue.extend(c for c in manager.Connectors if c.Domain==DB.Domain.DomainHvac and c.ConnectorType==DB.ConnectorType.End and int(c.Id)!=int(ref.Id))
    return False


def audit(doc,proposal_id):
    records=[]
    for element in DB.FilteredElementCollector(doc).OfClass(DB.ExtensibleStorage.DataStorage):
        data=proposals.metadata(element)
        if data and data.get('kind')==KIND and data.get('proposal_id')==proposal_id:records.append(data)
    failures=[];rows=[];principals=set()
    for record in records:
        principal=record.get('principal_source')
        if principal and principal['id'] not in principals:
            principals.add(principal['id'])
            try:
                connected=_principal_continuity(doc,principal,record['hrv_uid'],record['network_type'])
                rows.append({'route_id':record['route_id'],'endpoint':'manifold_to_hrv','connected':connected})
                if not connected:failures.append({'route_id':record['route_id'],'code':'MAIN_CONTINUITY_MISSING'})
            except Exception as exc:failures.append({'route_id':record['route_id'],'code':'MAIN_CONTINUITY_UNRESOLVED','detail':str(exc)})
        for key in ('source','destination'):
            try:
                target=_resolve(doc,record[key],record.get('network_type','SUPPLY'),False);owner=doc.GetElement(DB.ElementId(record['end_element_ids'][key]))
                endpoint=next(c for c in network_model.manager(owner).Connectors if int(c.Id)==record['end_ids'][key])
                ok=network_model.connected(endpoint,target)
                rows.append({'route_id':record['route_id'],'endpoint':key,'connected':ok,'target_element_id':rr.eid(target.Owner.Id),'target_connector_id':int(target.Id)})
                if not ok:failures.append({'route_id':record['route_id'],'code':'NATIVE_CONNECTION_INCORRECT','endpoint':key})
            except Exception as exc:failures.append({'route_id':record['route_id'],'code':'NATIVE_CONNECTION_MISSING','detail':str(exc)})
        for joint in record.get('joints',[]):
            try:
                a=doc.GetElement(DB.ElementId(joint['a_element']));b=doc.GetElement(DB.ElementId(joint['b_element']))
                ca=next(c for c in network_model.manager(a).Connectors if int(c.Id)==joint['a_connector'])
                cb=next(c for c in network_model.manager(b).Connectors if int(c.Id)==joint['b_connector'])
                if not network_model.connected(ca,cb):raise ValueError('Union interior desconectada.')
            except Exception as exc:failures.append({'route_id':record['route_id'],'code':'NATIVE_INTERNAL_JOINT_MISSING','detail':str(exc)})
        if record.get('terminal_port'):
            try:
                terminal=network_model.resolve(doc,record['terminal_port']);elbow=doc.GetElement(record['destination']['element_key'])
                if not any(c.IsConnectedTo(terminal) for c in network_model.manager(elbow).Connectors if c.ConnectorType==DB.ConnectorType.End):raise ValueError('Codo separado de la rejilla.')
                rows.append({'route_id':record['route_id'],'endpoint':'elbow_to_terminal','connected':True})
            except Exception as exc:failures.append({'route_id':record['route_id'],'code':'ELBOW_TO_TERMINAL_DISCONNECTED','detail':str(exc)})
        for element_id in record['members']:
            element=doc.GetElement(DB.ElementId(element_id))
            if element is None:failures.append({'route_id':record['route_id'],'code':'NATIVE_MEMBER_MISSING','element_id':element_id});continue
            cm=network_model.manager(element)
            if cm and any(c.ConnectorType==DB.ConnectorType.End and not c.IsConnected for c in cm.Connectors):
                failures.append({'route_id':record['route_id'],'code':'UNEXPECTED_OPEN_END','element_id':element_id})
    return {'routes':len(records),'connections':rows,'failures':failures,'native_connections_valid':bool(records) and not failures,
        'approval_tokens':sorted({r.get('approval_token','') for r in records}),'proposal_revisions':sorted({r.get('proposal_revision',0) for r in records})}


def commit(doc,p,approval_token,explicit=False,dry_run=False):
    plan=native_plan.authorize(p,approval_token,explicit)
    if ownership.document_id(doc)!=p.document_identity:raise ValueError('MODEL_DOCUMENT_MISMATCH')
    prior=audit(doc,p.proposal_id)
    if prior['routes']:
        if prior['routes']==len(plan['routes']) and prior['native_connections_valid'] and prior['approval_tokens']==[approval_token] and prior['proposal_revisions']==[p.revision]:return dict(prior,already_modeled=True,created=[])
        raise ValueError('EXISTING_NETWORK_REQUIRES_EXPLICIT_REVIEW')
    fresh=extractor.analyze_mep_geometry(doc,p.configuration)
    if fresh['geometry_revision']!=p.geometry_revision:raise ValueError('OUTDATED_GEOMETRY')
    catalog=extractor.get_mep_connection_points(doc,p.configuration)
    terminal_state=_terminal_state(catalog,p.terminals)
    group=DB.TransactionGroup(doc,'ARQ-T · Conexiones de ventilación V3');group.Start();tx=None;created=[]
    try:
        tx=DB.Transaction(doc,'ARQ-T · Aplicar distribución aprobada');tx.Start();_move_equipment(doc,plan['equipment_movements'])
        rotated_elbows=_rotate_elbows(doc,plan['routes'])
        if rotated_elbows:fresh=extractor.analyze_mep_geometry(doc,p.configuration)
        if plan['equipment_movements']:
            fresh=extractor.analyze_mep_geometry(doc,p.configuration)
            from ..revit.geometry_conflicts import inspect
            moved=[doc.GetElement(DB.ElementId(v['element_id'])) for v in plan['equipment_movements']]
            failures=inspect(doc,moved,fresh,constraints=p.configuration['constraints'],clearance_mm=p.configuration['design_clearance_mm'])
            if failures:raise ValueError('NATIVE_MOVED_EQUIPMENT_CONFLICT: '+str(failures))
        if rotated_elbows:
            from ..revit.geometry_conflicts import inspect,subtract
            from ..revit.terminal_adapters import mount_scope
            poses={pose['element_key']:pose for pose in plan['elbow_movements']};solids_by_id={}
            for elbow in rotated_elbows:
                pose=poses[elbow.UniqueId]
                collar=mount_scope(doc,elbow,p.configuration['design_clearance_mm'],pose['terminal_uid'])
                if collar is None:raise ValueError('ELBOW_MOUNT_SCOPE_UNRESOLVED')
                trimmed=[subtract(solid,collar) for solid in rr.solids(elbow)]
                solids_by_id[rr.eid(elbow.Id)]=[solid for solid in trimmed if solid.Volume>1e-9]
            failures=inspect(doc,rotated_elbows,fresh,constraints=p.configuration['constraints'],clearance_mm=p.configuration['design_clearance_mm'],solids_by_id=solids_by_id)
            if failures:raise ValueError('NATIVE_ELBOW_ROTATION_CONFLICT: '+str(failures))
        for route in plan['routes']:
            record=_create(doc,route,p.configuration);created.append(record)
        doc.Regenerate()
        joint_check=_joint_bodies(created,plan['routes'])
        after=extractor.get_mep_connection_points(doc,p.configuration)
        _assert_terminal_state(terminal_state,_terminal_state(after,p.terminals))
        from ..revit.geometry_conflicts import inspect
        for record,route in zip(created,plan['routes']):
            failures=inspect(doc,record['ducts']+record['fittings'],fresh,
                [route['source']['element_id'],route['destination']['element_id']],p.configuration['constraints'],route['passages'],route['clearance_mm'],record['connection_envelopes'])
            if failures:raise ValueError('NATIVE_GEOMETRY_CONFLICT: '+str(failures))
            manifest=DB.ExtensibleStorage.DataStorage.Create(doc)
            payload={'kind':KIND,'document_id':p.document_identity,'proposal_id':p.proposal_id,'proposal_revision':p.revision,
                'route_id':route['route_id'],'source':route['source'],'destination':route['destination'],
                'approval_token':approval_token,
                'network_type':route['network_type'],'joints':record['joints'],
                'terminal_port':next((t['terminal_adapter']['terminal_port'] for t in catalog['terminals'] if t['id']==route.get('terminal_id') and t.get('terminal_adapter')),None),
                'end_ids':record['end_ids'],'end_element_ids':record['end_element_ids'],
                'members':[rr.eid(e.Id) for e in record['ducts']+record['fittings']],
                'geometry_status':'NATIVE_GEOMETRY_VALID','curve_checks':record['curve_checks']}
            if p.configuration.get('routing_strategy')=='MANUAL_BUNDLE' and route.get('route_kind')=='BRANCH':
                equipment=p.equipment[route['distributor_uid']]
                payload.update(principal_source=descriptor(network_model.resolve(doc,equipment['main_port'])),hrv_uid=p.configuration['hrv_id'])
            proposals._tag(manifest,payload)
        report=audit(doc,p.proposal_id)
        if not report['native_connections_valid']:raise ValueError('NATIVE_AUDIT_FAILED: '+str(report['failures']))
        report.update(created=[{'route_id':r['route_id'],'members':[rr.eid(e.Id) for e in r['ducts']+r['fittings']]} for r in created],
            geometry_status='NATIVE_GEOMETRY_VALID_IN_AUTHORIZED_BORES',joint_check=joint_check,terminals_preserved=True,
            planned_penetrations=plan['summary']['planned_penetrations'],openings_created=False,hydraulic_status='NOT_EVALUATED',dry_run=dry_run,atomic=True)
        if dry_run:tx.RollBack();group.RollBack();report['native_elements_kept']=False
        else:
            if tx.Commit()!=DB.TransactionStatus.Committed:raise ValueError('NATIVE_TRANSACTION_NOT_COMMITTED')
            group.Assimilate();report['native_elements_kept']=True
        return report
    except Exception:
        if tx and tx.GetStatus()==DB.TransactionStatus.Started:tx.RollBack()
        if group.GetStatus()==DB.TransactionStatus.Started:group.RollBack()
        raise
