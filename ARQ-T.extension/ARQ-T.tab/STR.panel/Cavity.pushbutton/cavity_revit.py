import math
from System import Guid, String
from pyrevit.api import DB
from cavity_geometry import solid_bounds, world, floor_data

SCHEMA_ID=Guid('d9211d8e-b3be-4da2-b06c-11d2ef80dfe9')


def schema(create=False):
    s=DB.ExtensibleStorage.Schema.Lookup(SCHEMA_ID)
    if s or not create:
        return s
    b=DB.ExtensibleStorage.SchemaBuilder(SCHEMA_ID)
    b.SetSchemaName('ARQT_Cavity_v1')
    b.SetReadAccessLevel(DB.ExtensibleStorage.AccessLevel.Public)
    b.SetWriteAccessLevel(DB.ExtensibleStorage.AccessLevel.Public)
    b.AddSimpleField('FloorUniqueId',String)
    return b.Finish()


def previous(doc,floor):
    s=schema()
    if s is None:
        return []
    result=[]
    for e in DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance):
        entity=e.GetEntity(s)
        if entity.IsValid() and entity.Get[String](s.GetField('FloorUniqueId'))==floor.UniqueId:
            result.append(e.Id)
    return result


def section_crop_counts(view,instances):
    if not isinstance(view,DB.View3D) or not view.IsSectionBoxActive:
        return (0,0)
    section=view.GetSectionBox()
    inverse=section.Transform.Inverse
    hidden,cut=0,0
    for instance in instances:
        bounds=solid_bounds(instance,inverse)
        if bounds is None:
            continue
        limits=[(getattr(section.Min,k),getattr(section.Max,k)) for k in ('X','Y','Z')]
        if any(b<lo or a>hi for (a,b),(lo,hi) in zip(bounds,limits)):
            hidden+=1
        elif any(a<lo or b>hi for (a,b),(lo,hi) in zip(bounds,limits)):
            cut+=1
    return (hidden,cut)


def generate(doc,floor,symbol,data,plan,dims,offset,replace=False):
    if doc.IsReadOnly or not floor.IsValidObject or not symbol.IsValidObject:
        raise ValueError('Documento o selección no disponible.')
    if floor.Document!=doc or symbol.Document!=doc or data.get('floor_uid')!=floor.UniqueId:
        raise ValueError('El suelo o tipo no corresponde a la vista previa. Seleccione el suelo y recalcule.')
    current=floor_data(doc.GetElement(floor.Id))
    if (abs(current['z']-data['z'])>1e-8 or abs(current['host_z']-data['host_z'])>1e-8 or
            any(abs(a-b)>1e-8 for a,b in zip(current['origin'],data['origin'])) or
            abs(current['angle']-data['angle'])>1e-10 or not current['poly'].equals_exact(data['poly'],1e-8)):
        raise ValueError('El suelo ha cambiado desde el cálculo. Recalcule la distribución.')
    data=current
    mode=str(symbol.Family.FamilyPlacementType)
    if mode not in ('WorkPlaneBased','OneLevelBased','OneLevelBasedHosted'):
        raise ValueError('Sistema de colocación no compatible: '+mode)
    before=(floor.get_Parameter(DB.BuiltInParameter.HOST_AREA_COMPUTED).AsDouble(),
            floor.get_Parameter(DB.BuiltInParameter.HOST_VOLUME_COMPUTED).AsDouble())
    tx=DB.Transaction(doc,'ARQ-T | Automatización CAVITY')
    tx.Start()
    try:
        if not symbol.IsActive:
            symbol.Activate()
        doc.Regenerate()
        bounds=solid_bounds(symbol)
        if not bounds:
            raise ValueError('No se ha podido verificar la geometría sólida del tipo.')
        actual=tuple(b-a for a,b in bounds)
        if any(abs(a-b)>0.5/304.8 for a,b in zip(actual,dims)):
            raise ValueError('Las dimensiones geométricas difieren de la vista previa. Active el tipo y recalcule.')
        if replace:
            for eid in previous(doc,floor):
                element=doc.GetElement(eid)
                if any(i!=element.Id for i in element.GetDependentElements(None)):
                    raise ValueError('Una pieza anterior tiene elementos dependientes. Revísela antes de sustituir.')
                doc.Delete(eid)
        s=schema(True)
        a=data['angle']+plan['rotation']
        direction=DB.XYZ(math.cos(a),math.sin(a),0)
        bx=(bounds[0][0]+bounds[0][1])/2
        by=(bounds[1][0]+bounds[1][1])/2
        created=[]
        level=doc.GetElement(data['level'])
        for x,y in plan['points']:
            wx,wy=world(data,x,y)
            px=wx-bx*math.cos(a)+by*math.sin(a)
            py=wy-bx*math.sin(a)-by*math.cos(a)
            z=data['z']+offset-bounds[2][0]
            point=DB.XYZ(px,py,z)
            if mode=='WorkPlaneBased':
                instance=doc.Create.NewFamilyInstance(data['reference'],DB.XYZ(px,py,data['host_z']),direction,symbol)
                p=instance.get_Parameter(DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM)
                if p and not p.IsReadOnly:
                    p.Set(data['z']-data['host_z']+offset-bounds[2][0])
                elif abs(data['z']-data['host_z']+offset-bounds[2][0])>1e-8:
                    raise ValueError('La familia no admite el desfase solicitado.')
            else:
                structural=DB.Structure.StructuralType.NonStructural
                if mode=='OneLevelBasedHosted':
                    instance=doc.Create.NewFamilyInstance(point,symbol,floor,level,structural)
                else:
                    instance=doc.Create.NewFamilyInstance(point,symbol,level,structural)
                if abs(a)>1e-9:
                    DB.ElementTransformUtils.RotateElement(doc,instance.Id,DB.Line.CreateBound(point,point+DB.XYZ.BasisZ),a)
            entity=DB.ExtensibleStorage.Entity(s)
            entity.Set[String](s.GetField('FloorUniqueId'),floor.UniqueId)
            instance.SetEntity(entity)
            created.append((instance,point))
        doc.Regenerate()
        frame=DB.Transform.Identity
        frame.Origin=DB.XYZ(data['origin'][0],data['origin'][1],0)
        frame.BasisX=DB.XYZ(math.cos(data['angle']),math.sin(data['angle']),0)
        frame.BasisY=DB.XYZ(-math.sin(data['angle']),math.cos(data['angle']),0)
        measured=[solid_bounds(instance,frame.Inverse) for instance,point in created]
        if not all(measured):
            raise ValueError('No se ha podido medir el centrado de la geometría insertada.')
        x0,y0,x1,y1=data['poly'].bounds
        from shapely.geometry import box
        if any(not data['poly'].buffer(1e-8).covers(box(b[0][0],b[1][0],b[0][1],b[1][1])) for b in measured):
            raise ValueError('Una pieza invade el exterior o un hueco.')
        for bounds,(x,y) in zip(measured,plan['points']):
            if abs((bounds[0][0]+bounds[0][1])/2-x)>0.1/304.8 or abs((bounds[1][0]+bounds[1][1])/2-y)>0.1/304.8:
                raise ValueError('La geometría de la pieza no respeta la posición calculada desde la mitad del sobrante.')
        margins=(min(b[0][0] for b in measured)-x0,x1-max(b[0][1] for b in measured),
                 min(b[1][0] for b in measured)-y0,y1-max(b[1][1] for b in measured))
        plan['actual_margins']=margins
        plan['view_hidden_count'],plan['view_cut_count']=section_crop_counts(doc.ActiveView,[e for e,p in created])
        for instance,point in created:
            if instance.Location.Point.DistanceTo(point)>0.5/304.8:
                raise ValueError('Una instancia no ha respetado la posición prevista.')
            tr=instance.GetTransform()
            if tr.BasisX.DotProduct(direction)<0.999999 or tr.BasisZ.Z<0.999999:
                raise ValueError('Orientación inesperada de la familia.')
        after=(floor.get_Parameter(DB.BuiltInParameter.HOST_AREA_COMPUTED).AsDouble(),
               floor.get_Parameter(DB.BuiltInParameter.HOST_VOLUME_COMPUTED).AsDouble())
        if any(abs(a-b)>1e-7 for a,b in zip(before,after)):
            raise ValueError('La familia altera el suelo anfitrión; operación cancelada.')
        if tx.Commit()!=DB.TransactionStatus.Committed:
            raise ValueError('Revit no ha confirmado la operación.')
        return len(created)
    except:
        if tx.GetStatus()==DB.TransactionStatus.Started:
            tx.RollBack()
        raise
