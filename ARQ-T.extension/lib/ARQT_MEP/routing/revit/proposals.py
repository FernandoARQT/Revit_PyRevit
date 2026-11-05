"""Explicit native validation, owned model-curve preview and recoverable storage."""
import copy
import json
import math
from System import Guid,String
from System.Collections.Generic import List
from pyrevit.api import DB
from ... import revit_read as rr,ownership
from .. import service
from .. import settings
from ..core import geometry as g
from ..core.route_geometry import passage_for,validate_route_geometry,tangent_joint
from ..core.spatial_model import build_spatial_model
from .extractor import resolve_solids,analyze_mep_geometry

SCHEMA_ID='63b4be1b-f0e9-41b4-af3e-658269fcb895'


def _schema(create=False):
    found=DB.ExtensibleStorage.Schema.Lookup(Guid(SCHEMA_ID))
    if found or not create:return found
    builder=DB.ExtensibleStorage.SchemaBuilder(Guid(SCHEMA_ID));builder.SetSchemaName('ARQT_MEPRouting_v1')
    builder.SetReadAccessLevel(DB.ExtensibleStorage.AccessLevel.Public);builder.SetWriteAccessLevel(DB.ExtensibleStorage.AccessLevel.Public)
    builder.AddSimpleField('Payload',String)
    return builder.Finish()


def metadata(element):
    s=_schema()
    if s is None:return None
    entity=element.GetEntity(s)
    return json.loads(entity.Get[String](s.GetField('Payload'))) if entity.IsValid() else None


def _tag(element,payload):
    s=_schema(True);entity=DB.ExtensibleStorage.Entity(s)
    entity.Set[String](s.GetField('Payload'),json.dumps(payload,ensure_ascii=False,allow_nan=False));element.SetEntity(entity)


def xyz(p):return DB.XYZ(*[rr.internal(v) for v in p])


def _native_curve(piece):
    if piece['kind']=='LINE':return DB.Line.CreateBound(xyz(piece['start']),xyz(piece['end']))
    points=piece['points_mm']
    return DB.Arc.Create(xyz(points[0]),xyz(points[-1]),xyz(points[len(points)//2]))


def swept_solid(piece,radius_mm):
    curve=_native_curve(piece)
    origin=curve.GetEndPoint(0);tangent=curve.ComputeDerivatives(0,True).BasisX.Normalize()
    ref=DB.XYZ.BasisX if abs(tangent.X)<.8 else DB.XYZ.BasisY
    u=tangent.CrossProduct(ref).Normalize();v=tangent.CrossProduct(u).Normalize()
    profile=DB.CurveLoop();radius=rr.internal(radius_mm)
    profile.Append(DB.Arc.Create(origin,radius,0,math.pi,u,v))
    profile.Append(DB.Arc.Create(origin,radius,math.pi,2*math.pi,u,v))
    profiles=List[DB.CurveLoop]();profiles.Add(profile)
    if piece['kind']=='LINE':return DB.GeometryCreationUtilities.CreateExtrusionGeometry(profiles,tangent,curve.Length)
    path=DB.CurveLoop();path.Append(curve)
    return DB.GeometryCreationUtilities.CreateSweptGeometry(path,0,curve.GetEndParameter(0),profiles)


def _solid_box(solid):
    b=solid.GetBoundingBox()
    pts=[b.Transform.OfPoint(DB.XYZ(x,y,z)) for x in (b.Min.X,b.Max.X) for y in (b.Min.Y,b.Max.Y) for z in (b.Min.Z,b.Max.Z)]
    return g.bounds([(rr.mm(p.X),rr.mm(p.Y),rr.mm(p.Z)) for p in pts])


def validate_mep_routes(doc,snapshot,proposal):
    service.assert_current(snapshot,proposal)
    result=copy.deepcopy(proposal);native_cache={};built=[]
    configuration=settings.validate(proposal['configuration']);space=build_spatial_model(snapshot,configuration['constraints'])
    for route in result['routes']:
        if len(route.get('points_mm') or [])<2:
            route['native_status']='NOT_EVALUATED';continue
        if route.get('collision_status') in ('INVALID','NOT_EVALUATED'):
            route['native_status']='NOT_EVALUATED';continue
        geometric=validate_route_geometry(space,route,settings.profile(settings.for_route(configuration,route['route_id'])))
        if geometric['status']=='INVALID':
            route['collision_status']='INVALID';route['native_status']='NOT_EVALUATED';route['errors']=geometric['errors'];route['approval_status']='INVALID';continue
        route['collision_status']=geometric['status']
        section=route['section']
        physical=(section['outer_diameter_mm']/2 if section['shape']=='CIRCULAR' else math.hypot(section['width_mm'],section['height_mm'])/2)
        radius=settings.effective_radius({'section':section,'clearance_mm':route['clearance_mm']})
        failures=[];volumes=[]
        for i,piece in enumerate(route.get('primitives',[])):
            try:
                solid=swept_solid(piece,radius);query=_solid_box(solid)
                portal=passage_for(piece['start'],piece['end'],route.get('passages',[]),radius) if piece['kind']=='LINE' else None
                ignored=set(portal['element_ids']) if portal else set()
                if route.get('adapter_pose'):
                    # Its old position is replaced by the proposed body below;
                    # posed_space checks the complete clearance envelope.
                    ignored.add(route['adapter_pose']['element_key'])
                if piece['kind']=='LINE':
                    for stub in route.get('endpoint_stubs',[]):
                        if all(g.point_segment(*stub['points_mm'],point)<.01 for point in (piece['start'],piece['end'])):
                            ignored.update(stub.get('ignore_owner_ids',[]))
                for obstacle in snapshot['obstacles']:
                    if (obstacle['id'] in ignored and not obstacle.get('hard_forbidden')) or not g.overlaps(query,obstacle['box_mm']):continue
                    if obstacle['id'] not in native_cache:native_cache[obstacle['id']]=resolve_solids(doc,obstacle)
                    if not native_cache[obstacle['id']]:raise ValueError('Sólido real no disponible: '+obstacle['id'])
                    for other in native_cache[obstacle['id']]:
                        intersection=DB.BooleanOperationsUtils.ExecuteBooleanOperation(solid,other,DB.BooleanOperationsType.Intersect)
                        if intersection.Volume>1e-9:failures.append({'segment':i,'element_id':obstacle['id'],'reason':'REAL_SOLID_INTERSECTION'})
                for previous_id,other,other_box in built+volumes:
                    if not g.overlaps(query,other_box):continue
                    # Boolean intersection is numerically degenerate at the
                    # common disk of adjacent tangent sweeps. Their local
                    # non-overlap follows from the finite-radius construction;
                    # never skip non-adjacent parts or another route.
                    if (previous_id==route['route_id'] and i>0 and len(volumes)==i
                        and other is volumes[-1][1] and tangent_joint(route['primitives'][i-1],piece)):
                        continue
                    overlap=DB.BooleanOperationsUtils.ExecuteBooleanOperation(solid,other,DB.BooleanOperationsType.Intersect)
                    if overlap.Volume>1e-9:failures.append({'segment':i,'route_id':previous_id,'reason':'SELF_VOLUME_INTERSECTION' if previous_id==route['route_id'] else 'ROUTE_VOLUME_INTERSECTION'})
                volumes.append((route['route_id'],solid,query))
            except Exception as exc:failures.append({'segment':i,'reason':'NATIVE_GEOMETRY_NOT_EVALUATED','message':str(exc)})
        if not route.get('primitives'):failures.append({'reason':'NO_VALIDATED_GEOMETRY'})
        if route.get('adapter_pose'):
            try:
                from .terminal_adapters import posed_solids,mount_scope
                from .geometry_conflicts import inspect
                adapter,solids=posed_solids(doc,route['adapter_pose'])
                if not solids:raise ValueError('El adaptador no tiene sólidos verificables.')
                terminal_id=route['adapter_pose']['terminal_port']['element_id']
                collar=mount_scope(doc,adapter,route['clearance_mm'])
                scopes=[{'element_id':terminal_id,'box_mm':_solid_box(collar)}] if collar else []
                conflicts=inspect(doc,[adapter],snapshot,[terminal_id],configuration['constraints'],
                    route.get('passages',[]),route['clearance_mm'],scopes,{rr.eid(adapter.Id):solids})
                for conflict in conflicts:failures.append({'reason':'ADAPTER_BODY_CONFLICT','detail':conflict})
                route_volumes=list(volumes)
                for solid in solids:
                    query=_solid_box(solid)
                    for previous_id,other,other_box in built+route_volumes:
                        if not g.overlaps(query,other_box):continue
                        if DB.BooleanOperationsUtils.ExecuteBooleanOperation(solid,other,DB.BooleanOperationsType.Intersect).Volume>1e-9:
                            failures.append({'reason':'ADAPTER_ROUTE_INTERSECTION','route_id':previous_id})
                    volumes.append((route['route_id'],solid,query))
                route['adapter_pose']['geometry_validation']='INVALID' if conflicts else 'NATIVE_BODY_CHECKED'
            except Exception as exc:failures.append({'reason':'NATIVE_GEOMETRY_NOT_EVALUATED','message':str(exc)})
        route['native_checks']={'primitives':len(volumes),'errors':failures,'method':'Revit Solid extrusion/sweep and BooleanOperationsUtils.Intersect; adjacent tangent joints certified analytically'}
        route['native_checks']['connection_geometry_pending']=bool(route.get('connection_validation_pending'))
        if route.get('connection_validation_pending'):
            route['native_checks']['scope']='Recorrido central; las transiciones y extremos se comprueban sobre los sólidos reales después de modelar.'
        if failures:
            route['native_status']='NOT_EVALUATED' if any(f['reason']=='NATIVE_GEOMETRY_NOT_EVALUATED' for f in failures) else 'INVALID'
            route['collision_status']=route['native_status'];route['approval_status']='INVALID'
        else:
            route['native_status']=route['collision_status'];built.extend(volumes)
        if route.get('routing_diagnostics') is not None:
            route['routing_diagnostics']['native_status']=route['native_status']
            if failures:route['routing_diagnostics']['final_status']='NATIVE_GEOMETRY_UNVERIFIED' if route['native_status']=='NOT_EVALUATED' else 'NATIVE_GEOMETRY_FAILED'
    result['summary']=service.summarize(result['routes'])
    if result['summary']['unresolved']:result['status']='INVALID'
    return service.seal(result)


def _current(doc,proposal):
    snapshot=analyze_mep_geometry(doc,proposal['configuration'])
    service.assert_current(snapshot,proposal)
    return snapshot


def save_mep_route_proposal(doc,proposal,explicit=False):
    if not explicit:raise ValueError('Guardar exige explicit=True.')
    snapshot=_current(doc,proposal)
    if any(r.get('approval_status')=='APPROVED' and r.get('native_status') not in ('VALID','VALID_WITH_WARNINGS') for r in proposal['routes']):
        raise ValueError('Una ruta aprobada necesita validación nativa válida y condiciones resueltas.')
    proposal=validate_mep_routes(doc,snapshot,proposal)
    found=[]
    for e in DB.FilteredElementCollector(doc).OfClass(DB.ExtensibleStorage.DataStorage):
        m=metadata(e)
        if m and m.get('kind')=='PROPOSAL' and m.get('proposal_id')==proposal['proposal_id']:found.append(e)
    if len(found)>1:raise ValueError('Almacenamiento duplicado; requiere revisión.')
    transaction=DB.Transaction(doc,'ARQ-T | Guardar propuesta de trazado MEP');transaction.Start()
    try:
        element=found[0] if found else DB.ExtensibleStorage.DataStorage.Create(doc)
        _tag(element,{'kind':'PROPOSAL','proposal_id':proposal['proposal_id'],'document_identity':ownership.document_id(doc),'proposal':proposal})
        transaction.Commit()
    except BaseException:
        if transaction.GetStatus()==DB.TransactionStatus.Started:transaction.RollBack()
        raise
    return {'proposal_id':proposal['proposal_id'],'element_id':rr.eid(element.Id),'status':proposal['status']}


def get_mep_route_proposal(doc,proposal_id=None):
    results=[]
    for e in DB.FilteredElementCollector(doc).OfClass(DB.ExtensibleStorage.DataStorage):
        m=metadata(e)
        if not m or m.get('kind')!='PROPOSAL':continue
        if proposal_id is not None and m['proposal_id']!=proposal_id:continue
        p=copy.deepcopy(m['proposal']);service.check(p)
        if p['document_identity']!=ownership.document_id(doc):p['status']='OUTDATED'
        else:
            try:_current(doc,p)
            except ValueError:p['status']='OUTDATED'
        results.append(service.seal(p))
    return results


def _clear_inside_transaction(doc,proposal_id=None):
    owned=[]
    for cls in (DB.CurveElement,DB.SketchPlane):
        for e in DB.FilteredElementCollector(doc).OfClass(cls):
            m=metadata(e)
            if m and m.get('document_identity')==ownership.document_id(doc) and m.get('kind') in ('ROUTE_PREVIEW','ROUTE_SKETCHPLANE') and (proposal_id is None or m['proposal_id']==proposal_id):owned.append(e)
    ids={rr.eid(e.Id) for e in owned};removed=[]
    for e in sorted(owned,key=lambda e:0 if isinstance(e,DB.CurveElement) else 1):
        if not e.IsValidObject:continue
        deleted=list(doc.Delete(e.Id))
        if any(rr.eid(i) not in ids for i in deleted):raise ValueError('La limpieza afectaría elementos dependientes ajenos; se revierte.')
        removed.extend(rr.eid(i) for i in deleted)
    return removed


def hide_preview(doc,proposal_id=None,explicit=False):
    if not explicit:raise ValueError('Ocultar visualización exige explicit=True.')
    t=DB.Transaction(doc,'ARQ-T | Ocultar trazado MEP');t.Start()
    try:ids=_clear_inside_transaction(doc,proposal_id);t.Commit()
    except BaseException:
        if t.GetStatus()==DB.TransactionStatus.Started:t.RollBack()
        raise
    return {'removed_ids':ids}


def preview_mep_routes(doc,proposal,explicit=False,route_ids=None,alternative_index=None):
    if not explicit:raise ValueError('Mostrar líneas en Revit exige explicit=True.')
    _current(doc,proposal)
    t=DB.Transaction(doc,'ARQ-T | Previsualizar trazado MEP');t.Start();created=[]
    try:
        _clear_inside_transaction(doc,proposal['proposal_id'])
        for route in proposal['routes']:
            if route_ids and route['route_id'] not in route_ids:continue
            geometry=route
            if alternative_index is not None:
                options=route.get('alternatives',[])
                if not 0<=alternative_index<len(options):continue
                geometry=options[alternative_index]
            from ..core.natural_path import straight_vertices
            from ..core.route_geometry import primitives
            try:pieces=primitives(straight_vertices(geometry.get('points_mm',[])),geometry.get('bend_radius_mm',route['bend_radius_mm']))
            except ValueError:pieces=geometry.get('primitives',[])
            if not pieces:
                pieces=[{'kind':'LINE','start':a,'end':b} for a,b in zip(geometry.get('points_mm',[]),geometry.get('points_mm',[])[1:])]
            status=geometry.get('collision_status',geometry.get('status',route['collision_status']))
            color=DB.Color(200,50,50) if status in ('INVALID','NOT_EVALUATED') else DB.Color(159,86,194) if status=='CONDITIONAL' else DB.Color(150,150,150) if alternative_index is not None else DB.Color(244,119,33) if route['network_type']=='SUPPLY' else DB.Color(40,124,171)
            for piece in pieces:
                if g.distance(piece['start'],piece['end'])<rr.mm(doc.Application.ShortCurveTolerance):continue
                curve=_native_curve(piece)
                if piece['kind']=='ARC':normal=DB.XYZ(*piece['axis'])
                else:
                    tangent=curve.Direction;ref=DB.XYZ.BasisX if abs(tangent.X)<.8 else DB.XYZ.BasisY
                    normal=tangent.CrossProduct(ref).Normalize()
                plane=DB.SketchPlane.Create(doc,DB.Plane.CreateByNormalAndOrigin(normal,curve.GetEndPoint(0)))
                element=doc.Create.NewModelCurve(curve,plane)
                payload={'proposal_id':proposal['proposal_id'],'route_id':route['route_id'],'document_identity':ownership.document_id(doc)}
                _tag(plane,dict(payload,kind='ROUTE_SKETCHPLANE'));_tag(element,dict(payload,kind='ROUTE_PREVIEW'))
                for view in DB.FilteredElementCollector(doc).OfClass(DB.View3D):
                    if view.IsTemplate:continue
                    override=DB.OverrideGraphicSettings();override.SetProjectionLineColor(color);override.SetProjectionLineWeight(4)
                    view.SetElementOverrides(element.Id,override)
                created.append(rr.eid(element.Id))
        t.Commit()
    except BaseException:
        if t.GetStatus()==DB.TransactionStatus.Started:t.RollBack()
        raise
    return {'preview_ids':created,'representation':'OWNED_MODELCURVES','temporary_cleanup_required':True,
            'note':'No son conductos. El cliente elimina estas líneas al ocultar o cancelar.'}
