from pyrevit.api import DB
from System.Collections.Generic import List
from geometry import rectangle,dimensions,plate_profiles
from settings import validate_settings
from optimizer import optimize_margins


def mm(value):
    return DB.UnitUtils.ConvertFromInternalUnits(value,DB.UnitTypeId.Millimeters)


def feet(value):
    return DB.UnitUtils.ConvertToInternalUnits(value,DB.UnitTypeId.Millimeters)


def snapshot(doc,floor):
    if not isinstance(floor,DB.Floor) or floor.Document!=doc:
        raise ValueError('Seleccione un suelo del documento activo.')
    sketch=doc.GetElement(floor.SketchId)
    if not sketch:
        raise ValueError('El suelo no tiene croquis accesible.')
    loops=list(sketch.Profile)
    if len(loops)!=1 or len(list(loops[0]))!=4:
        raise ValueError('Solo se admite un rectángulo de cuatro líneas, sin huecos.')
    curves=list(loops[0])
    if not all(isinstance(c,DB.Line) for c in curves):
        raise ValueError('No se admiten arcos, splines ni cortes diagonales.')
    points=[c.GetEndPoint(0) for c in curves]
    if max(p.Z for p in points)-min(p.Z for p in points)>feet(.01):
        raise ValueError('El croquis no es horizontal.')
    for c,nxt in zip(curves,curves[1:]+curves[:1]):
        if c.GetEndPoint(1).DistanceTo(nxt.GetEndPoint(0))>feet(.01):
            raise ValueError('El croquis no es continuo.')
    bounds=rectangle([(mm(p.X),mm(p.Y)) for p in points])
    editor=floor.GetSlabShapeEditor()
    if editor and editor.IsEnabled:
        raise ValueError('No se admite un suelo con edición de forma habilitada.')
    opt=DB.Options()
    upward=[]
    for solid in floor.get_Geometry(opt):
        if isinstance(solid,DB.Solid) and solid.Volume>0:
            for face in solid.Faces:
                if isinstance(face,DB.PlanarFace) and face.FaceNormal.Z>0:
                    if face.FaceNormal.Z<.999999999:
                        raise ValueError('El suelo está inclinado.')
                    upward.append(face)
    if len(upward)!=1:
        raise ValueError('Se requiere una única cara superior horizontal.')
    expected=(bounds[2]-bounds[0])*(bounds[3]-bounds[1])
    area=mm(1)**2*upward[0].Area
    if abs(area-expected)>max(1,expected*1e-7):
        raise ValueError('El sólido actual no coincide con el rectángulo del croquis (cortes o uniones).')
    level=doc.GetElement(floor.LevelId)
    floor_type=doc.GetElement(floor.GetTypeId())
    offset=floor.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
    if not level or not floor_type or not offset:
        raise ValueError('Tipo, nivel o desfase inaccesible.')
    return {'uid':floor.UniqueId,'id':floor.Id.Value,'version':str(floor.VersionGuid),'type':floor.GetTypeId().Value,
            'level':floor.LevelId.Value,'offset':offset.AsDouble(),'bounds':bounds,'type_name':floor_type.Name,
            'level_name':level.Name,'top_z':upward[0].Origin.Z,'area_mm2':expected}


def create_profile(points,z):
    xyz=[DB.XYZ(feet(x),feet(y),z) for x,y in points]
    loop=DB.CurveLoop()
    for a,b in zip(xyz,xyz[1:]+xyz[:1]):
        loop.Append(DB.Line.CreateBound(a,b))
    loops=List[DB.CurveLoop]()
    loops.Add(loop)
    if not DB.BoundaryValidation.IsValidHorizontalBoundary(loops):
        raise ValueError('Perfil de placa inválido.')
    return loops


def generate(doc,source,saved,direction,solution,settings,reverse=False,fail_after=None):
    validate_settings(settings)
    now=snapshot(doc,source)
    if now!=saved:
        raise ValueError('El forjado ha cambiado. Seleccione y calcule de nuevo.')
    for eid in source.GetDependentElements(None):
        if eid!=source.Id:
            dependent=doc.GetElement(eid)
            automatic_internal=(dependent is not None and dependent.GetType().Name=='Element' and dependent.Category is None and not dependent.Name)
            if dependent and not automatic_internal and dependent.GetType().Name not in ('Sketch','SketchPlane','ModelLine','ModelCurve'):
                raise ValueError('El suelo tiene elementos dependientes ({}) que requieren revisión antes de sustituirlo.'.format(dependent.GetType().Name))
    if not solution['valid']:
        raise ValueError('La solución no es admisible.')
    active={o['width'] for o in settings['widths'] if o['enabled'] and o['width']>=settings['minimum_exceptional']}
    if not solution['order'] or any(w not in active for w in solution['order']):
        raise ValueError('La solución no corresponde al catálogo activo.')
    u0,u1,v0,v1=dimensions(now['bounds'],direction)
    margins=optimize_margins(v1-v0-sum(solution['order']),settings['left'],settings['right'])
    if margins is None or max(abs(margins[0]-solution['left']),abs(margins[1]-solution['right']))>1e-6:
        raise ValueError('Las holguras de la solución ya no son válidas.')
    profiles=plate_profiles(now['bounds'],direction,solution,reverse)
    tx=DB.Transaction(doc,'ARQ-T | Despiece losa alveolar')
    tx.Start()
    created=[]
    try:
        for profile in profiles:
            loops=create_profile(profile,now['top_z'])
            new=DB.Floor.Create(doc,loops,DB.ElementId(now['type']),DB.ElementId(now['level']),True,None,0.0)
            param=new.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
            if not param or param.IsReadOnly:
                raise ValueError('El desfase de la placa no es modificable.')
            param.Set(now['offset'])
            created.append(new)
            if fail_after is not None and len(created)>=fail_after:
                raise ValueError('Fallo de creación provocado para verificar rollback.')
        doc.Regenerate()
        for new,profile in zip(created,profiles):
            check=snapshot(doc,new)
            if check['type']!=now['type'] or check['level']!=now['level'] or abs(check['offset']-now['offset'])>1e-9:
                raise ValueError('Tipo, nivel o desfase incorrecto.')
            wanted=rectangle(profile)
            if max(abs(a-b) for a,b in zip(check['bounds'],wanted))>.01 or abs(check['top_z']-now['top_z'])>feet(.01):
                raise ValueError('Geometría o elevación incorrecta de una placa.')
        area=sum(snapshot(doc,n)['area_mm2'] for n in created)
        if abs(area-(u1-u0)*sum(solution['order']))>max(1,area*1e-7):
            raise ValueError('La superficie de las placas no coincide.')
        if abs(area+(u1-u0)*(solution['left']+solution['right'])-now['area_mm2'])>max(1,area*1e-7):
            raise ValueError('Las placas y holguras no completan la superficie original.')
        deleted=list(doc.Delete(source.Id))
        for eid in deleted:
            if eid.Value in [n.Id.Value for n in created]:
                raise ValueError('La eliminación del original afecta a placas nuevas.')
        if tx.Commit()!=DB.TransactionStatus.Committed:
            raise ValueError('Revit no ha confirmado la operación.')
        return [n.Id.Value for n in created]
    except:
        if tx.GetStatus()==DB.TransactionStatus.Started:
            tx.RollBack()
        raise
