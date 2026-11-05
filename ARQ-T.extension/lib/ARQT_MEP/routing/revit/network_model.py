"""Explicit heat recovery duct creation and live endpoint auditing.

Does not change routing algorithms, families or terminal flows.
Each route is its own transaction; failed connections roll the route back.
"""
import math
from pyrevit.api import DB
from System.Collections.Generic import List
from ... import revit_read as rr,ownership
from .. import service,settings
from ..readiness import preflight,blocker
from . import proposals,extractor
from ..core import geometry as g
from ...family_rules import terminal_matches,distributor_network

KIND='HRV_MODELED_NETWORK'


def manager(element):
    if isinstance(element,DB.MEPCurve):return element.ConnectorManager
    model=getattr(element,'MEPModel',None)
    return model.ConnectorManager if model else None


def resolve(doc,port):
    if not port or port.get('provenance')!='REVIT_CONNECTOR':
        raise ValueError('Se necesitan dos conectores Revit reales; una referencia manual no se modela.')
    element=doc.GetElement(port['element_key'])
    if element is None:raise ValueError('Elemento de conexión ausente: '+str(port.get('element_id')))
    connector=next((c for c in manager(element).Connectors if c.Id==port['connector_id']),None)
    if connector is None or connector.Domain!=DB.Domain.DomainHvac:raise ValueError('Conector HVAC ausente.')
    return connector


def connected(a,b):
    if a.IsConnectedTo(b):return True
    for ref in a.AllRefs:
        fitting=ref.Owner
        if fitting.Category and rr.eid(fitting.Category.Id)==int(DB.BuiltInCategory.OST_DuctFitting):
            cm=manager(fitting)
            if cm and any(c.IsConnectedTo(b) for c in cm.Connectors if c.Id!=ref.Id):return True
    return False


def links(connector):
    return [{'element_id':rr.eid(c.Owner.Id),'connector_id':int(c.Id)} for c in connector.AllRefs
            if c.Owner.Id!=connector.Owner.Id and c.ConnectorType==DB.ConnectorType.End]


def owned(doc):
    result=[]
    for element in DB.FilteredElementCollector(doc).WhereElementIsNotElementType():
        if not isinstance(element,(DB.ExtensibleStorage.DataStorage,DB.Mechanical.Duct,DB.Mechanical.FlexDuct,DB.FamilyInstance)):continue
        data=proposals.metadata(element)
        if data and data.get('kind')==KIND and data.get('document_id')==ownership.document_id(doc):
            result.append((element,data))
    return result


def audit(doc,proposal=None):
    if proposal:service.check(proposal)
    rows=[];used=set();modeled=owned(doc)
    if proposal:
        route_ids={r['route_id'] for r in proposal['routes']}
        modeled=[v for v in modeled if v[1]['route_id'] in route_ids]
    for element,data in modeled:
        for endpoint in ('source','destination'):
            port=data[endpoint];used.add(port['id'])
            row={'route_id':data['route_id'],'endpoint':endpoint,'duct_element_id':rr.eid(element.Id),
                 'target_element_id':port['element_id'],'target_connector_id':port['connector_id']}
            try:
                target=resolve(doc,port)
                endpoint_element=doc.GetElement(DB.ElementId(data.get('end_element_ids',{}).get(endpoint,rr.eid(element.Id))))
                if endpoint_element is None:raise ValueError('El segmento del extremo ha sido eliminado.')
                row['duct_element_id']=rr.eid(endpoint_element.Id)
                curve_port=next((c for c in manager(endpoint_element).Connectors if c.Id==data['end_ids'][endpoint]),None)
                if curve_port is None:raise ValueError('Extremo del conducto ausente.')
                row['duct_connector_id']=int(curve_port.Id);row['actual_connections']=links(curve_port)
                row['status']='CONECTADO' if connected(curve_port,target) else 'CONEXION_INCORRECTA' if curve_port.IsConnected else 'CONDUCTO_SIN_CONECTAR'
                classification=str(target.DuctSystemType)
                row['target_classification']=classification;row['target_flow_direction']=str(target.Direction)
                expected=data['network_type']
                incompatible=(expected=='SUPPLY' and classification in ('ReturnAir','ExhaustAir') or
                              expected=='EXTRACT' and classification=='SupplyAir')
                if row['status']=='CONECTADO' and incompatible:
                    row.update(status='CONEXION_INCORRECTA',reason='La clasificación real pertenece a la otra red.')
                if classification not in ('SupplyAir','ReturnAir','ExhaustAir') or str(target.Direction)=='Bidirectional':
                    row['functional_review']='Revisar clasificación/sentido del conector; la conexión física por sí sola no acredita su función.'
                row['target_connected_elsewhere']=bool(target.IsConnected and not connected(curve_port,target))
            except Exception as exc:row.update(status='CONDUCTO_SIN_CONECTAR',reason=str(exc))
            rows.append(row)
        for joint in data.get('joints',[]):
            row={'route_id':data['route_id'],'endpoint':'union_interior','duct_element_id':joint['a_element'],
                 'target_element_id':joint['b_element'],'target_connector_id':joint['b_connector']}
            try:
                a=doc.GetElement(DB.ElementId(joint['a_element']));b=doc.GetElement(DB.ElementId(joint['b_element']))
                ca=next(c for c in manager(a).Connectors if c.Id==joint['a_connector'])
                cb=next(c for c in manager(b).Connectors if c.Id==joint['b_connector'])
                row['status']='CONECTADO' if connected(ca,cb) else 'CONEXION_INCORRECTA' if ca.IsConnected else 'CONDUCTO_SIN_CONECTAR'
            except Exception as exc:row.update(status='CONDUCTO_SIN_CONECTAR',reason=str(exc))
            rows.append(row)
        if data.get('terminal_port'):
            row={'route_id':data['route_id'],'endpoint':'rejilla_adaptador',
                'target_element_id':data['terminal_port']['element_id'],
                'target_connector_id':data['terminal_port']['connector_id']}
            try:
                terminal_port=resolve(doc,data['terminal_port'])
                adapter=resolve(doc,data['destination']).Owner
                cm=manager(adapter)
                row['status']='CONECTADO' if any(c.IsConnectedTo(terminal_port) for c in cm.Connectors) else 'CONDUCTO_SIN_CONECTAR'
                used.add(data['terminal_port']['id'])
            except Exception as exc:row.update(status='CONDUCTO_SIN_CONECTAR',reason=str(exc))
            rows.append(row)
            for cap_id in data.get('adapter_cap_ids',[]):
                cap=doc.GetElement(DB.ElementId(cap_id))
                ok=cap is not None and any(c.IsConnected for c in manager(cap).Connectors)
                rows.append({'route_id':data['route_id'],'endpoint':'tapa_adaptador','target_element_id':cap_id,
                    'status':'CONECTADO' if ok else 'CONDUCTO_SIN_CONECTAR'})
    if proposal:
        for existing in (proposal.get('topology') or {}).get('preserved_connections',[]):
            port=existing['port'];used.add(port['id'])
            row={'target_element_id':port['element_id'],'target_connector_id':port['connector_id'],
                 'network_type':existing['network_type'],'status':'CONEXION_PREVIA_CONSERVADA',
                 'functional_review':'Se conserva la conexión física previa; revisar su destino y clasificación.'}
            try:
                target=resolve(doc,port)
                if not target.IsConnected:row['status']='CONEXION_PREVIA_YA_NO_EXISTE'
                row['actual_connections']=links(target)
            except Exception as exc:row.update(status='CONEXION_PREVIA_YA_NO_EXISTE',reason=str(exc))
            rows.append(row)
        for route in proposal['routes']:
            for endpoint in ('source','destination'):
                port=route.get(endpoint)
                if not port or port.get('provenance')!='REVIT_CONNECTOR' or port['id'] in used:continue
                used.add(port['id'])
                rows.append({'route_id':route['route_id'],'endpoint':endpoint,'target_element_id':port['element_id'],
                             'target_connector_id':port['connector_id'],'status':'RUTA_SIN_MODELAR'})
    catalog=extractor.get_mep_connection_points(doc,settings.validate(proposal['configuration'] if proposal else {}))
    for device in catalog['devices']+catalog['terminals']:
        if device in catalog['devices'] and not device.get('is_configured_distributor') and 'recuperador' not in device.get('family','').casefold():continue
        for port in device['connectors']:
            if port['id'] in used:continue
            rows.append({'target_element_id':port['element_id'],'target_connector_id':port['connector_id'],
                         'status':'TOMA_SIN_USAR' if not port['connected'] else 'CONEXION_EXISTENTE_AJENA_A_PROPUESTA'})
    geometry_conflicts=[]
    if modeled:
        from .geometry_conflicts import inspect
        config=settings.validate(proposal['configuration'] if proposal else {})
        geometry_snapshot=extractor.analyze_mep_geometry(doc,config)
        for element,data in modeled:
            parts=[doc.GetElement(DB.ElementId(i)) for i in data.get('members',[])]
            parts=[p for p in parts if p is not None]
            constraints=config['constraints'] if proposal else {'approved_passages':list(data.get('passage_approvals',{})),
                'passage_approvals':data.get('passage_approvals',{})}
            ignored=[data['source']['element_id'],data['destination']['element_id']]
            if data.get('terminal_port'):ignored.append(data['terminal_port']['element_id'])
            for conflict in inspect(doc,parts,geometry_snapshot,ignored,
                                    constraints,data.get('passages',[]),data.get('clearance_mm',0),data.get('connection_envelopes',[])):
                conflict['route_id']=data['route_id'];geometry_conflicts.append(conflict)
    return {'system_id':'ventilation_radial','connections':rows,'geometry_conflicts':geometry_conflicts,
            'counts':{status:sum(r['status']==status for r in rows) for status in sorted({r['status'] for r in rows})}}


def _system_type(doc,network,configuration):
    classification='SupplyAir' if network=='SUPPLY' else 'ExhaustAir'
    candidates=[t for t in DB.FilteredElementCollector(doc).OfClass(DB.Mechanical.MechanicalSystemType)
                if str(t.SystemClassification)==classification]
    requested=configuration.get('duct_system_type_ids',{}).get(network)
    if requested:candidates=[t for t in candidates if rr.eid(t.Id)==int(requested)]
    if len(candidates)!=1:raise ValueError('Defina un tipo de sistema inequívoco en duct_system_type_ids para '+network)
    return candidates[0].Id


def _rigid(doc,route,configuration,system_id,level_id,diameter):
    types=[t for t in DB.FilteredElementCollector(doc).OfClass(DB.Mechanical.DuctType) if str(t.Shape)=='Round' and not rr.name(t).startswith('ARQT_MEP_')]
    requested=configuration['main_connection'].get('type_id')
    if requested:types=[t for t in types if rr.eid(t.Id)==int(requested)]
    if len(types)!=1:raise ValueError('Seleccione un tipo rígido circular mediante main_connection.type_id.')
    from .transitions import rigid_type_with_transition
    flex=extractor.configured_model_catalog(doc,configuration)['flexible_duct']
    duct_type=rigid_type_with_transition(doc,types[0],flex['transition_type_id'])
    points=[]
    for point in route['points_mm']:
        if points and g.distance(points[-1],point)<.001:continue
        while len(points)>1:
            a=g.sub(points[-1],points[-2]);b=g.sub(point,points[-1])
            if g.norm(g.cross(g.unit(a),g.unit(b)))>1e-7 or g.dot(a,b)<=0:break
            points.pop()
        points.append(point)
    ducts=[];joints=[];errors=[];joint_fittings=[]
    for a,b in zip(points,points[1:]):
        duct=DB.Mechanical.Duct.Create(doc,system_id,duct_type.Id,level_id,proposals.xyz(a),proposals.xyz(b))
        duct.get_Parameter(DB.BuiltInParameter.RBS_CURVE_DIAMETER_PARAM).Set(rr.internal(diameter));ducts.append(duct)
    doc.Regenerate()
    for index,(a,b) in enumerate(zip(ducts,ducts[1:])):
        position=proposals.xyz(points[index+1])
        ca=min(a.ConnectorManager.Connectors,key=lambda c:c.Origin.DistanceTo(position))
        cb=min(b.ConnectorManager.Connectors,key=lambda c:c.Origin.DistanceTo(position))
        joints.append({'a_element':rr.eid(a.Id),'a_connector':int(ca.Id),'b_element':rr.eid(b.Id),'b_connector':int(cb.Id)})
        sub=DB.SubTransaction(doc);sub.Start()
        try:
            if abs(ca.CoordinateSystem.BasisZ.DotProduct(cb.CoordinateSystem.BasisZ))>.9999:ca.ConnectTo(cb)
            else:joint_fittings.append(doc.Create.NewElbowFitting(ca,cb))
            doc.Regenerate()
            if not connected(ca,cb):raise ValueError('Unión interior no conectada.')
            sub.Commit()
        except Exception as exc:
            sub.RollBack()
            raise ValueError('No se pudo construir el codo interior {} del conducto rígido: {}'.format(index+1,exc))
    return ducts,joints,errors,joint_fittings


def model(doc,proposal,explicit=False,cancelled=None):
    if not explicit:raise ValueError('Modelar requiere una acción explícita.')
    service.check(proposal)
    if proposal['system_id']!='ventilation_radial':raise ValueError('Solo recuperación de calor.')
    configuration=settings.validate(proposal['configuration'])
    hrv=doc.GetElement(configuration.get('hrv_id',''))
    if hrv is None or not getattr(hrv,'Symbol',None) or 'recuperador' not in hrv.Symbol.FamilyName.casefold():
        raise ValueError('Seleccione el recuperador de referencia de este sistema.')
    # A second click audits existing work and never duplicates ducts.
    route_ids={r['route_id'] for r in proposal['routes']}
    existing_route_ids={data['route_id'] for e,data in owned(doc)}&route_ids
    if existing_route_ids==route_ids and route_ids:
        from .distributor_caps import close_unused
        caps=close_unused(doc,proposal)
        return dict(audit(doc,proposal),created=[],caps=caps,note='La propuesta ya tiene elementos modelados; se han comprobado conexiones y tapas sin duplicar conductos.')
    readiness=preflight(proposal)
    if not readiness['ready_route_ids']:
        if not proposal['routes']:
            from .distributor_caps import close_unused
            caps=close_unused(doc,proposal)
            return dict(audit(doc,proposal),created=[],failures=[],caps=caps,
                        note='Sin rutas nuevas; se han cerrado las tomas sobrantes de los distribuidores seleccionados.')
        return dict(audit(doc,proposal),created=[],failures=readiness['failures'],
                    note='No se ha creado ningún conducto: no hay rutas aprobadas y validadas listas para modelar. Revise los motivos indicados por ruta.')
    fresh=extractor.analyze_mep_geometry(doc,configuration,cancelled)
    service.assert_current(fresh,proposal)
    validated=proposals.validate_mep_routes(doc,fresh,proposal)
    flexes=[t for t in DB.FilteredElementCollector(doc).OfClass(DB.Mechanical.FlexDuctType)
            if rr.name(t)==configuration['flexible_duct']['type_name'] and str(t.Shape)=='Round']
    native_flex=extractor.configured_model_catalog(doc,configuration)['flexible_duct']
    created=[];failures=[];rejected_conflicts=[]
    for route in validated['routes']:
        if cancelled and cancelled():break
        if route['route_id'] in existing_route_ids:continue
        tx=None;route_conflicts=[]
        try:
            if created:fresh=extractor.analyze_mep_geometry(doc,configuration,cancelled)
            message=blocker(route)
            if message:raise ValueError(message)
            if route['section']['shape']!='CIRCULAR' or route['section'].get('insulation_mm',0):
                raise ValueError('Este modelador admite sección circular sin aislamiento añadido.')
            source=resolve(doc,route['source']);destination=resolve(doc,route['destination'])
            network=route['network_type']
            distributor=destination.Owner if route['source'].get('kind')=='HRV' else source.Owner
            if distributor_network(distributor.Symbol.FamilyName)!=network:
                raise ValueError('Familia del distribuidor incompatible con '+network)
            terminal=resolve(doc,route['adapter_pose']['terminal_port']).Owner if route.get('adapter_pose') else destination.Owner
            terminal_frame=terminal.GetTransform() if route.get('adapter_pose') else None
            terminal_flow=terminal.LookupParameter('Flow').AsDouble() if terminal_frame else None
            if route['source'].get('kind')!='HRV' and not terminal_matches(terminal.Symbol.FamilyName,rr.name(terminal.Symbol),network):
                raise ValueError('Familia/tipo de rejilla incompatible con '+network)
            if source.IsConnected or destination.IsConnected:raise ValueError('Conector ocupado; no se desconecta ninguna red existente.')
            level_id=source.Owner.LevelId
            if level_id==DB.ElementId.InvalidElementId:level_id=destination.Owner.LevelId
            if level_id==DB.ElementId.InvalidElementId:raise ValueError('Nivel de referencia no verificable.')
            diameter=route['section']['nominal_diameter_mm']
            main=route['source'].get('kind')=='HRV'
            if main and source.Owner.Id!=hrv.Id:
                raise ValueError('La principal debe partir del recuperador de referencia seleccionado.')
            if not main and source.Owner.Id==hrv.Id:
                raise ValueError('No se modela como ramal flexible una conexión directa del recuperador.')
            if main and not route.get('sizing') and abs(diameter-configuration['main_connection']['diameter_mm'])>.01:
                raise ValueError('La principal debe conservar el diámetro rígido configurado.')
            if len(flexes)!=1:raise ValueError('Tipo flexible existente ausente o ambiguo.')
            if diameter not in native_flex['sizing_diameters_mm']:raise ValueError('Diámetro no habilitado en los tamaños circulares de Revit; recalcule.')
            for c in (source,destination):
                if c.Shape!=DB.ConnectorProfileType.Round:
                    raise ValueError('La conexión requiere tomas circulares.')
            system_id=_system_type(doc,route['network_type'],configuration)
            from ..core.natural_path import build as natural_path
            construction=natural_path(route['points_mm'],route['bend_radius_mm'])
            points=construction['native_controls_mm']
            native=List[DB.XYZ]()
            for point in points:native.Add(proposals.xyz(point))
            if route.get('adapter_pose'):
                tx=DB.Transaction(doc,'ARQ-T · Orientar adaptador y modelar ramal');tx.Start()
                from .terminal_adapters import apply_pose
                destination=apply_pose(doc,route['destination'])
            if source.Origin.DistanceTo(native[0])>rr.internal(.1) or destination.Origin.DistanceTo(native[native.Count-1])>rr.internal(.1):
                raise ValueError('La ruta no termina en sus conectores actuales.')
            if tx is None:tx=DB.Transaction(doc,'ARQ-T · Modelar ramal de recuperación de calor');tx.Start()
            start=source.CoordinateSystem.BasisZ
            end=destination.CoordinateSystem.BasisZ.Negate()
            joints=[];connection_errors=[];joint_fittings=[]
            # All interior HRV connections, including manifold mains, use
            # Flex - Redondo. Exterior rigid circuits are outside this modeler.
            duct=DB.Mechanical.FlexDuct.Create(doc,system_id,flexes[0].Id,level_id,start,end,native)
            ducts=[duct]
            parameter=duct.get_Parameter(DB.BuiltInParameter.RBS_CURVE_DIAMETER_PARAM)
            if parameter.IsReadOnly:raise ValueError('Diámetro de ejemplar de solo lectura.')
            parameter.Set(rr.internal(diameter));doc.Regenerate()
            if abs(rr.mm(parameter.AsDouble())-diameter)>.01:raise ValueError('Diámetro real distinto del solicitado.')
            ends=list(duct.ConnectorManager.Connectors)
            if len(ends)!=2:raise ValueError('El conducto no tiene dos extremos verificables.')
            first=min(ends,key=lambda c:c.Origin.DistanceTo(source.Origin))
            last=min(ducts[-1].ConnectorManager.Connectors,key=lambda c:c.Origin.DistanceTo(destination.Origin))
            if main and len(ducts)==1 and first.Id==last.Id:raise ValueError('Los dos objetivos corresponden al mismo extremo.')
            end_ids={'source':int(first.Id),'destination':int(last.Id)}
            fittings=list(joint_fittings);connection_envelopes=[];endpoint_fittings=[];fitting_owners=[]
            for label,a,b in (('source',first,source),('destination',last,destination)):
                sub=DB.SubTransaction(doc);sub.Start()
                try:
                    from .transitions import connect
                    fitting=connect(doc,a,b,native_flex['transition_type_id'],configuration['connection_straight_mm'])
                    if not connected(a,b):raise ValueError('Revit no confirma conexión física con la toma prevista.')
                    sub.Commit()
                    if fitting:
                        fittings.append(fitting);endpoint_fittings.append(fitting)
                        fitting_owners.append((fitting,rr.eid(b.Owner.Id)))
                    else:
                        from ..core import geometry as geometry
                        points_mm=[(rr.mm(p.X),rr.mm(p.Y),rr.mm(p.Z)) for p in (a.Origin,b.Origin)]
                        connection_envelopes.append({'element_id':rr.eid(b.Owner.Id),
                            'box_mm':geometry.expand(geometry.bounds(points_mm),rr.mm(max(a.Radius,b.Radius))+route['clearance_mm'])})
                except Exception as exc:sub.RollBack();connection_errors.append({'endpoint':label,'reason':str(exc)})
            if connection_errors:raise ValueError('Uniones nativas incompletas; se revierte la ruta. '+str(connection_errors))
            adapter_caps=[]
            if route.get('adapter_pose'):
                from .terminal_adapters import close_unused,mount_scope
                adapter_caps=close_unused(doc,destination.Owner,destination.Id)
                fittings.extend([destination.Owner]+adapter_caps)
                collar=mount_scope(doc,destination.Owner,route['clearance_mm'])
                if collar:
                    connection_envelopes.append({'element_id':rr.eid(terminal.Id),'box_mm':proposals._solid_box(collar)})
            if isinstance(duct,DB.Mechanical.FlexDuct) and len(ducts)==1:
                # Native reducers move FlexDuct endpoints. Old interpolation
                # points inside the reducer would reverse the spline and can
                # leave a valid Element with no materialized geometry.
                from ..core.flex_points import fitted_points
                def mm_point(p):return (rr.mm(p.X),rr.mm(p.Y),rr.mm(p.Z))
                repaired=fitted_points(construction['vertices_mm'],mm_point(first.Origin),mm_point(last.Origin),mm_point(start),mm_point(end))
                construction=natural_path(repaired,route['bend_radius_mm'])
                fitted=List[DB.XYZ]()
                for point in construction['native_controls_mm']:fitted.Add(proposals.xyz(point))
                duct.Points=fitted
            doc.Regenerate()
            if terminal_frame:
                actual=terminal.GetTransform()
                if any(a.DistanceTo(b)>1e-9 for a,b in zip(
                    (terminal_frame.Origin,terminal_frame.BasisX,terminal_frame.BasisY,terminal_frame.BasisZ),
                    (actual.Origin,actual.BasisX,actual.BasisY,actual.BasisZ))):
                    raise ValueError('La conexión intenta mover la rejilla; se revierte el ramal.')
                if abs(terminal.LookupParameter('Flow').AsDouble()-terminal_flow)>1e-9:
                    raise ValueError('La conexión altera el caudal de la rejilla; se revierte el ramal.')
            # Fitting bounds may be unavailable until the completed network
            # has regenerated. Rebuild those local scopes from final geometry.
            from .extractor import _box
            for fitting,owner_id in fitting_owners:
                box=_box(fitting)
                if box:connection_envelopes.append({'element_id':owner_id,'box_mm':g.expand(box,route['clearance_mm'])})
            if abs(rr.mm(parameter.AsDouble())-diameter)>.01:raise ValueError('La conexión nativa cambió el diámetro solicitado.')
            from .geometry_conflicts import inspect
            route_conflicts=inspect(doc,ducts+fittings,fresh,[rr.eid(source.Owner.Id),rr.eid(destination.Owner.Id),rr.eid(terminal.Id)],
                configuration['constraints'],route.get('passages',[]),route['clearance_mm'],connection_envelopes)
            for conflict in route_conflicts:conflict['route_id']=route['route_id']
            if connection_errors or not connected(first,source) or not connected(last,destination):
                raise ValueError('Ruta incompleta: conexiones de extremo o uniones interiores sin verificar; se revierte la ruta. '+str(connection_errors))
            if route_conflicts:
                raise ValueError('Colisión o geometría real no verificada; se revierte la ruta. '+str(route_conflicts))
            record={'kind':KIND,'document_id':ownership.document_id(doc),'proposal_id':proposal['proposal_id'],
                'route_id':route['route_id'],'source':route['source'],'destination':route['destination'],
                'end_ids':end_ids,'end_element_ids':{'source':rr.eid(duct.Id),'destination':rr.eid(ducts[-1].Id)},
                'members':[rr.eid(d.Id) for d in ducts+fittings],'joints':joints,'network_type':route['network_type'],
                'geometry_conflicts':route_conflicts,'passages':route.get('passages',[]),'clearance_mm':route['clearance_mm'],
                'connection_envelopes':connection_envelopes,
                'natural_path':construction,
                'terminal_port':route.get('adapter_pose',{}).get('terminal_port'),
                'adapter_pose':route.get('adapter_pose'),'adapter_cap_ids':[rr.eid(c.Id) for c in adapter_caps],
                'passage_approvals':configuration['constraints'].get('passage_approvals',{})}
            # Independent manifest keeps disconnected/deleted members auditable.
            manifest=DB.ExtensibleStorage.DataStorage.Create(doc);proposals._tag(manifest,record)
            proposals._tag(duct,dict(record,kind='HRV_MODELED_DUCT'))
            status=tx.Commit()
            if status!=DB.TransactionStatus.Committed:raise ValueError('Revit no confirmó la transacción del ramal.')
            created.append({'route_id':route['route_id'],'element_id':rr.eid(duct.Id),'diameter_mm':diameter,
                            'native_control_count':construction['native_control_count'],
                            'members':[rr.eid(d.Id) for d in ducts+fittings],'transition_ids':[rr.eid(f.Id) for f in endpoint_fittings],
                            'fitting_ids':[rr.eid(f.Id) for f in fittings],'connection_errors':connection_errors,
                            'geometry_status':'GEOMETRIA_REAL_COMPROBADA',
                            'construction_status':'PASOS_PREVISTOS_NO_PERFORADOS' if route.get('passages') else 'SIN_PASOS_NUEVOS'})
        except Exception as exc:
            if tx and tx.GetStatus()==DB.TransactionStatus.Started:tx.RollBack()
            for conflict in route_conflicts:conflict['route_reverted']=True;rejected_conflicts.append(conflict)
            failures.append({'route_id':route['route_id'],'reason':str(exc)})
    from .distributor_caps import close_unused
    caps=close_unused(doc,proposal)
    result=audit(doc,proposal);result.update(created=created,failures=failures,caps=caps,
        note='Conectividad comprobada por extremo y unión. La geometría real del flexible o de los accesorios y sus pérdidas de carga no se certifican con la validación del eje de propuesta.')
    result['geometry_conflicts'].extend(rejected_conflicts)
    return result
