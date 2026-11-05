"""Explicit, atomic model writes. Called only after review of a sealed proposal."""
import math
import uuid
from pyrevit.api import DB
from shapely.geometry import Point,box,shape
from . import revit_read as rr
from . import ownership,service
from . import surface
from .spatial import polygon_from_rings


def assign_terminal_flow(element,liters_per_second):
    matches=list(element.GetParameters('Flow'))
    if len(matches)!=1:
        raise ValueError('Se requiere un único parámetro de instancia Flow en {}'.format(rr.eid(element.Id)))
    p=matches[0]
    if p.IsReadOnly or p.StorageType!=DB.StorageType.Double or p.Definition.GetDataType()!=DB.SpecTypeId.AirFlow:
        raise ValueError('Flow no es un Double Air Flow editable de instancia.')
    value=DB.UnitUtils.ConvertToInternalUnits(float(liters_per_second),DB.UnitTypeId.LitersPerSecond)
    if not p.Set(value):
        raise ValueError('Revit rechazó Flow.')
    element.Document.Regenerate()
    p=element.GetParameters('Flow')[0]
    actual=DB.UnitUtils.ConvertFromInternalUnits(p.AsDouble(),DB.UnitTypeId.LitersPerSecond)
    if abs(actual-liters_per_second)>1e-6:
        raise ValueError('La lectura posterior de Flow no coincide.')
    return actual


def offset_parameter(element):
    for key in ('INSTANCE_ELEVATION_PARAM','INSTANCE_FREE_HOST_OFFSET_PARAM'):
        p=element.get_Parameter(getattr(DB.BuiltInParameter,key))
        if p is not None and p.StorageType==DB.StorageType.Double:
            return p
    raise ValueError('La instancia no expone un desfase compatible verificable.')


def _selected(proposal,choices):
    chosen=[]
    for p in proposal['terminals']:
        choice=choices.get(p['key'],0)
        if type(choice)!=int or choice<0 or choice>=len(p['alternatives']):
            raise ValueError('Alternativa no válida para '+p['key'])
        alt=p['alternatives'][choice]
        if p.get('protected') and choice!=0:
            raise ValueError('El terminal protegido solo admite mantener la posición existente.')
        selected=dict(p,**alt)
        selected['installation_offset_mm']=surface.INSTALLATION_OFFSET_MM
        if p.get('existing_position'):
            desired=surface.installation_insertion(alt,proposal['symbols'][p['role']]['visible_z'])
            same_pose=(math.dist(desired,p['existing_position'])<=proposal['settings']['tolerance_mm'] and
                       abs(desired[2]-p['existing_position'][2])<=.001 and
                       surface.basis_error_degrees(p.get('existing_basis') or [[1,0,0],[0,1,0],[0,0,1]],surface.frame(alt))<=0.01)
            if not same_pose and (p.get('protected') or p.get('lock_position') or p.get('lock_full')):
                raise ValueError('Desbloquee la posición antes de aplicar el desfase de instalación: '+p['key'])
            selected['preserve']=same_pose and abs(p['existing_flow']-p['flow'])<1e-6
        chosen.append(selected)
    spacing=proposal['settings']['terminal_spacing_mm']
    for index,p in enumerate(chosen):
        for q in chosen[:index]:
            if p['room_uid']==q['room_uid'] and math.hypot(p['x']-q['x'],p['y']-q['y'])<spacing-0.01:
                raise ValueError('Las alternativas elegidas incumplen la separación entre bocas; elija otra combinación.')
    return chosen


def _native_collision(doc,element,ceiling_id,phase_id):
    geometry=rr.solids(element)
    bbox=element.get_BoundingBox(None)
    if bbox is None or not geometry:
        raise ValueError('No se pudo validar el volumen real de la rejilla.')
    outline=DB.Outline(bbox.Min,bbox.Max)
    nearby=DB.FilteredElementCollector(doc).WhereElementIsNotElementType().WherePasses(DB.BoundingBoxIntersectsFilter(outline))
    checked=0
    for other in nearby:
        if rr.eid(other.Id) in (rr.eid(element.Id),ceiling_id):
            continue
        if isinstance(other,(DB.SpatialElement,DB.Level,DB.View)) or other.Category is None:
            continue
        if other.Category.CategoryType!=DB.CategoryType.Model:
            continue
        if other.HasPhases() and not rr.visible_in_phase(other,doc,phase_id):
            continue
        if isinstance(other,DB.RevitLinkInstance):
            raise ValueError('Vínculo próximo no resuelto: {}. Revise la interferencia.'.format(rr.eid(other.Id)))
        for a in geometry:
            for b in rr.solids(other):
                checked+=1
                try:
                    overlap=DB.BooleanOperationsUtils.ExecuteBooleanOperation(a,b,DB.BooleanOperationsType.Intersect)
                except Exception as exc:
                    raise ValueError('Intersección sólida no resuelta con {}: {}'.format(rr.eid(other.Id),exc))
                if overlap.Volume>1e-9:
                    raise ValueError('Interferencia sólida con elemento {}.'.format(rr.eid(other.Id)))
    return checked


def _validate_one(doc,element,p,symbol_info,tolerance_mm):
    location=element.Location
    if not isinstance(location,DB.LocationPoint):
        raise ValueError('Terminal sin punto de inserción.')
    point=location.Point
    actual=[rr.mm(point.X),rr.mm(point.Y),rr.mm(point.Z)]
    correction=p.get('installation_offset_mm',0)
    desired=surface.installation_insertion(p,symbol_info['visible_z'],correction)
    if math.dist(actual,desired)>tolerance_mm:
        raise ValueError('XYZ de inserción fuera de tolerancia: '+str(rr.eid(element.Id)))
    if rr.eid(element.LevelId)!=p['level_id'] or rr.eid(element.GetTypeId())!=p['symbol_id']:
        raise ValueError('Tipo o nivel incorrecto: '+str(rr.eid(element.Id)))
    offset=rr.mm(offset_parameter(element).AsDouble())
    if abs(offset-(desired[2]-p['level_z']))>min(tolerance_mm,.01) or abs(actual[2]-desired[2])>min(tolerance_mm,.01):
        raise ValueError('Desfase incorrecto: '+str(rr.eid(element.Id)))
    transform=element.GetTransform()
    basis=rr.instance_basis(element)
    orientation_error=surface.basis_error_degrees(basis,surface.frame(p))
    if orientation_error>0.01:
        raise ValueError('La orientación de la rejilla no coincide con la cara inferior del techo: '+str(rr.eid(element.Id)))
    actual_visible=transform.OfPoint(DB.XYZ(0,0,rr.internal(symbol_info['visible_z'])))
    visible=[rr.mm(actual_visible.X),rr.mm(actual_visible.Y),rr.mm(actual_visible.Z)]
    expected_visible=[p['x'],p['y'],p['z']+correction]
    if math.dist(visible,expected_visible)>tolerance_mm:
        raise ValueError('El centro visible no coincide con el desfase de instalación previsto.')
    inverse=transform.Inverse;normal=surface.plane(p)['normal'];samples=[]
    for vertex in rr.geometry_points(element):
        if abs(rr.mm(inverse.OfPoint(vertex).Z)-symbol_info['visible_z'])<0.01:
            xyz=[rr.mm(vertex.X),rr.mm(vertex.Y),rr.mm(vertex.Z)]
            samples.append(abs(surface.dot(normal,[xyz[i]-expected_visible[i] for i in range(3)])))
    if len(samples)<3 or max(samples)>tolerance_mm:
        raise ValueError('La geometría inferior no respeta el plano y desfase de instalación previstos.')
    param=element.LookupParameter('Flow')
    actual_flow=DB.UnitUtils.ConvertFromInternalUnits(param.AsDouble(),DB.UnitTypeId.LitersPerSecond)
    if abs(actual_flow-p['flow'])>1e-6:
        raise ValueError('Flow no coincide con el diseño.')
    return {'id':rr.eid(element.Id),'key':p['key'],'position_mm':actual,'visible_z_mm':visible[2],
            'visible_point_mm':visible,'visible_plane_error_mm':max(samples),'orientation_error_deg':orientation_error,
            'slope_deg':surface.slope_degrees(p),'ceiling_normal':normal,
            'ceiling_z_mm':p['z'],'installation_offset_mm':correction,'level_id':p['level_id'],'offset_mm':offset,'flow_l_s':actual_flow,'valid':True}


def orient_instance(doc,element,target):
    rotation=surface.axis_angle(rr.instance_basis(element),surface.frame(target))
    if rotation is not None:
        axis,angle=rotation
        DB.ElementTransformUtils.RotateElement(doc,element.Id,DB.Line.CreateUnbound(element.Location.Point,DB.XYZ(*axis)),angle)
        doc.Regenerate()


def create_ventilation_terminals(doc,proposal,approved=False,choices=None,delete_obsolete=False,cancelled=None):
    service.get_ventilation_proposal(proposal)
    if not approved:
        raise ValueError('Se requiere approved=True tras revisar posiciones, caudales y acciones.')
    if not proposal['ready']:
        raise ValueError('Propuesta incompleta o bloqueada; corrija incidencias antes de colocar.')
    if ownership.document_id(doc)!=proposal['document_id'] or rr.fingerprint(doc)!=proposal['fingerprint']:
        raise ValueError('El documento ha cambiado; analice y optimice de nuevo.')
    selected=_selected(proposal,choices or {})
    # Resolve every type before starting a transaction or deleting any terminal.
    resolved_symbols={}
    for p in selected:
        info=proposal['symbols'][p['role']]
        symbol=doc.GetElement(DB.ElementId(p['symbol_id']))
        if (not isinstance(symbol,DB.FamilySymbol) or p['symbol_id']!=info['id'] or
                symbol.UniqueId!=info['uid'] or symbol.FamilyName!=info['family'] or rr.name(symbol)!=info['type']):
            raise ValueError('El tipo de rejilla guardado ya no coincide con el proyecto. Pulse Calcular caudales u Optimizar y previsualizar para actualizar la propuesta y revísela antes de colocar.')
        resolved_symbols[p['symbol_id']]=symbol
    obsolete=[a for a in proposal['actions'] if a['action']=='eliminar propio obsoleto']
    if obsolete and not delete_obsolete:
        raise ValueError('La propuesta requiere eliminar {} terminales propios obsoletos. Autorice esa acción explícitamente.'.format(len(obsolete)))
    run_id=str(uuid.uuid4());created=[];deleted=[]
    transaction=DB.Transaction(doc,'ARQ-T | Distribuir ventilación')
    transaction.Start()
    try:
        for a in obsolete:
            if a.get('blocked'):
                raise ValueError('No se elimina un terminal protegido: '+str(a['id']))
            e=doc.GetElement(DB.ElementId(a['id']))
            metadata=ownership.read(e)
            if not metadata or metadata.get('document_id')!=ownership.document_id(doc) or e.Pinned:
                raise ValueError('Elemento obsoleto ajeno o bloqueado.')
            removed=list(doc.Delete(e.Id))
            if len(removed)!=1:
                raise ValueError('La eliminación afectaría elementos dependientes; operación revertida.')
            deleted.append(a['id'])
        for p in selected:
            if cancelled and cancelled():
                raise InterruptedError('Operación cancelada; todos los cambios se revierten.')
            symbol=resolved_symbols[p['symbol_id']]
            info=proposal['symbols'][p['role']]
            if not symbol.IsActive:
                symbol.Activate();doc.Regenerate()
            xyz=DB.XYZ(*[rr.internal(v) for v in surface.installation_insertion(p,info['visible_z'])])
            level=doc.GetElement(DB.ElementId(p['level_id']))
            if p['existing_id']:
                element=doc.GetElement(DB.ElementId(p['existing_id']))
                metadata=ownership.read(element)
                if metadata is None or metadata.get('document_id')!=ownership.document_id(doc) or ((element.Pinned or metadata.get('locked')) and not p.get('protected')):
                    raise ValueError('Terminal protegido o ajeno.')
                if element.LevelId!=level.Id:
                    raise ValueError('Cambio de nivel/alojamiento de terminal existente requiere adaptación específica.')
                if element.GetTypeId()!=symbol.Id:
                    if not p.get('editor_update') or element.Pinned or metadata.get('locked'):
                        raise ValueError('Cambio de tipo de terminal existente requiere revisión explícita en la propuesta.')
                    if str(symbol.Family.FamilyPlacementType)!='OneLevelBased' or element.Symbol.Family.Id!=symbol.Family.Id:
                        raise ValueError('No se cambia el alojamiento de una familia existente.')
                    replacement=element.ChangeTypeId(symbol.Id)
                    if replacement!=DB.ElementId.InvalidElementId:element=doc.GetElement(replacement)
                    doc.Regenerate()
            else:
                element=doc.Create.NewFamilyInstance(xyz,symbol,level,DB.Structure.StructuralType.NonStructural)
            if not p.get('preserve'):
                offset=offset_parameter(element)
                desired=xyz.Z-level.Elevation
                if offset.IsReadOnly:
                    if abs(offset.AsDouble()-desired)>rr.internal(proposal['settings']['tolerance_mm']):
                        raise ValueError('El desfase requerido es de solo lectura.')
                else:
                    offset.Set(desired)
                doc.Regenerate()
                location=element.Location
                if not isinstance(location,DB.LocationPoint):
                    raise ValueError('No se puede asignar una posición compatible.')
                delta=xyz.Subtract(location.Point)
                if delta.GetLength()>rr.internal(0.001):
                    DB.ElementTransformUtils.MoveElement(doc,element.Id,delta)
                orient_instance(doc,element,p)
                assign_terminal_flow(element,p['flow'])
            doc.Regenerate()
            location=element.Location
            validation=_validate_one(doc,element,p,info,proposal['settings']['tolerance_mm'])
            room=next(r for r in proposal['analysis']['rooms'] if r['uid']==p['room_uid'])
            footprint=rr.projected_footprint(element)
            if not polygon_from_rings(room['rings']).buffer(0.1).covers(footprint):
                raise ValueError('La geometría de rejilla queda fuera de la Room asignada.')
            ceiling=next(c for c in room['ceilings'] if c['id']==p['ceiling_id'] and
                         surface.key(c)==p.get('ceiling_surface_id',surface.key(c)))
            if not shape(ceiling['geometry']).buffer(0.1).covers(footprint):
                raise ValueError('La rejilla queda fuera del paño real de techo.')
            metadata={'document_id':ownership.document_id(doc),'room_uid':p['room_uid'],'role':p['role'],
                      'flow':p['flow'],'approval':'accepted','run_id':run_id,'key':p['key'],
                      'ceiling_uid':p['ceiling_uid'],'symbol_id':p['symbol_id'],'locked':bool(p.get('lock_full',False)),
                      'lock_position':bool(p.get('lock_position',False)),'lock_flow':bool(p.get('lock_flow',False)),
                      'terminal_id':p.get('terminal_id',p['key']),'proposal_id':proposal.get('editor_proposal_id'),
                      'position':validation['position_mm'],'rotation':float(location.Rotation),'basis':rr.instance_basis(element),
                      'target':{k:p[k] for k in ('key','x','y','z','level_id','level_z','symbol_id','flow','role','plane','ceiling_surface_id','slope_deg','installation_offset_mm') if k in p},
                      'symbol_info':info}
            prior=ownership.read(element) if p.get('preserve') and p.get('editor_update') else None
            changed=prior is not None and any(prior.get(k)!=metadata.get(k) for k in ('role','symbol_id','ceiling_uid','locked','lock_position','lock_flow','terminal_id','proposal_id'))
            if not p.get('preserve') or changed:ownership.write(element,metadata)
            created.append(validation)
        doc.Regenerate()
        for validation,p in zip(created,selected):
            element=doc.GetElement(DB.ElementId(validation['id']))
            validation['solid_checks']=_native_collision(doc,element,p['ceiling_id'],proposal['analysis']['phase_id'])
        for row in proposal['flows']['rows']:
            values=[v['flow_l_s'] for v,p in zip(created,selected) if p['room_uid']==row['room_uid'] and p['role']==row['role']]
            if values and abs(sum(values)-row['design'])>1e-6:
                raise ValueError('La suma de Flow por estancia no coincide.')
        status=transaction.Commit()
        if status!=DB.TransactionStatus.Committed:
            raise ValueError('Revit no confirmó la transacción: '+str(status))
        return {'run_id':run_id,'validated':created,'deleted_ids':deleted,'count':len(created),
                'warnings':proposal['warnings'],'connector_flow_associated':False}
    except BaseException:
        if transaction.GetStatus()==DB.TransactionStatus.Started:
            transaction.RollBack()
        raise


def validate_ventilation_terminals(doc,room_uids=None,tolerance_mm=1):
    results=[];issues=[]
    for item in ownership.inventory(doc,tolerance_mm):
        if not item['own'] or room_uids and item['metadata']['room_uid'] not in room_uids:
            continue
        m=item['metadata'];element=doc.GetElement(DB.ElementId(item['id']))
        try:
            p=m['target'];ceiling=doc.GetElement(m['ceiling_uid'])
            if ceiling is None:
                raise ValueError('Falso techo original ausente.')
            room=doc.GetElement(m['room_uid'])
            if room is None or room.Area<=0:
                raise ValueError('Room original ausente o sin área.')
            if item['manual_change']:
                raise ValueError('Terminal modificado manualmente desde la aprobación.')
            current_ceilings,_=rr.read_ceilings(doc,rr.eid(room.get_Parameter(DB.BuiltInParameter.ROOM_PHASE).AsElementId()))
            footprint=rr.projected_footprint(element)
            if not any(c['uid']==ceiling.UniqueId and abs(surface.height(c,p['x'],p['y'])-p['z'])<=tolerance_mm and
                       surface.basis_error_degrees(surface.frame(c),surface.frame(p))<=0.01 and
                       c['polygon'].buffer(0.1).covers(footprint) for c in current_ceilings):
                raise ValueError('El techo ya no coincide con el plano de instalación.')
            options=DB.SpatialElementBoundaryOptions()
            options.SpatialElementBoundaryLocation=DB.SpatialElementBoundaryLocation.Finish
            rings=rr.rings_from_curves([[seg.GetCurve() for seg in loop] for loop in room.GetBoundarySegments(options)])
            if not polygon_from_rings(rings).buffer(0.1).covers(footprint):
                raise ValueError('La Room ya no contiene toda la rejilla en planta.')
            validation=_validate_one(doc,element,p,m['symbol_info'],tolerance_mm)
            phase=rr.eid(room.get_Parameter(DB.BuiltInParameter.ROOM_PHASE).AsElementId())
            validation['solid_checks']=_native_collision(doc,element,rr.eid(ceiling.Id),phase)
            results.append(validation)
        except Exception as exc:
            issues.append({'id':item['id'],'error':str(exc)})
    return {'validated':results,'issues':issues,'valid':not issues,'count':len(results),
            'connector_flow_associated':False}


def set_terminal_lock(doc,element_id,locked,approved=False):
    if not approved:
        raise ValueError('Bloqueo requiere invocación explícita approved=True.')
    element=doc.GetElement(DB.ElementId(int(element_id)));metadata=ownership.read(element)
    if metadata is None or metadata['document_id']!=ownership.document_id(doc):
        raise ValueError('Solo puede bloquear un terminal propio de este documento.')
    t=DB.Transaction(doc,'ARQ-T | Protección de terminal');t.Start()
    try:
        metadata['locked']=bool(locked);ownership.write(element,metadata);t.Commit()
    except BaseException:
        if t.GetStatus()==DB.TransactionStatus.Started:t.RollBack()
        raise
    return {'id':int(element_id),'locked':bool(locked)}
