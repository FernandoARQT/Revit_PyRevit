"""Read-only Revit adapter. All planar calculations use millimetres."""
import hashlib
import math
from pyrevit.api import DB
from pyrevit.revit.db import query
from shapely.geometry import Point, box, mapping,MultiPoint
from .spatial import polygon_from_rings
from . import surface
from .family_rules import TERMINALS,terminal_matches


def eid(value):
    return int(value.Value)


def mm(value):
    return DB.UnitUtils.ConvertFromInternalUnits(value, DB.UnitTypeId.Millimeters)


def internal(value):
    return DB.UnitUtils.ConvertToInternalUnits(float(value), DB.UnitTypeId.Millimeters)


def name(element):
    return query.get_name(element)


def solids(element):
    options = DB.Options()
    options.DetailLevel = DB.ViewDetailLevel.Fine
    def walk(geometry):
        if geometry is None:
            return
        for obj in geometry:
            if isinstance(obj, DB.Solid) and obj.Volume > 1e-12:
                yield obj
            elif isinstance(obj, DB.GeometryInstance):
                yield from walk(obj.GetInstanceGeometry())
    return list(walk(element.get_Geometry(options)))


def bounds(element):
    """Bounds of visible solids, not the element bbox (which includes controls)."""
    points = []
    for solid in solids(element):
        for face in solid.Faces:
            mesh = face.Triangulate()
            points.extend(mesh.Vertices)
    if not points:
        raise ValueError('Sin geometría sólida visible: {}'.format(eid(element.Id)))
    return [min(mm(getattr(p, axis)) for p in points) for axis in ('X','Y','Z')] + [
        max(mm(getattr(p, axis)) for p in points) for axis in ('X','Y','Z')]


def geometry_points(element):
    return [p for solid in solids(element) for face in solid.Faces for p in face.Triangulate().Vertices]


def projected_footprint(element):
    return MultiPoint([(mm(p.X),mm(p.Y)) for p in geometry_points(element)]).convex_hull


def sample_curve(curve, tolerance_mm=0.05):
    """Adaptive subdivision, also tests quarter points to handle splines/inflections."""
    def point(t):
        p = curve.Evaluate(t, True)
        return (mm(p.X), mm(p.Y), mm(p.Z))
    def distance(p,a,b):
        ab=[b[i]-a[i] for i in range(3)];ap=[p[i]-a[i] for i in range(3)]
        t=max(0,min(1,sum(ap[i]*ab[i] for i in range(3))/max(1e-30,sum(v*v for v in ab))))
        return math.sqrt(sum((ap[i]-t*ab[i])**2 for i in range(3)))
    def split(a,b,pa,pb,depth):
        ts=[a+(b-a)*t for t in (.25,.5,.75)]
        ps=[point(t) for t in ts]
        if max(distance(p,pa,pb) for p in ps)<=tolerance_mm:
            return [pa]
        if depth>=16:
            raise ValueError('Curva no resuelta con tolerancia de 0,05 mm.')
        return split(a,ts[1],pa,ps[1],depth+1)+split(ts[1],b,ps[1],pb,depth+1)
    return split(0,1,point(0),point(1),0)+[point(1)]


def rings_from_curves(loops):
    result=[]
    for loop in loops:
        ring=[]
        for curve in loop:
            ring.extend((p[0],p[1]) for p in sample_curve(curve)[:-1])
        result.append(ring)
    return result


def fingerprint(doc):
    """Versions plus live placement: VersionGuid can lag inside transaction groups."""
    values=[]
    def xyz(p):return (p.X,p.Y,p.Z)
    for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType():
        state=[eid(e.Id),str(e.VersionGuid)]
        if isinstance(e,DB.FamilyInstance):
            transform=e.GetTransform()
            state.extend([eid(e.GetTypeId()),xyz(transform.Origin),xyz(transform.BasisX),xyz(transform.BasisY),xyz(transform.BasisZ),e.Pinned])
            # Read the stored values, not formatted strings, including individual Flow.
            if e.Category and eid(e.Category.Id)==int(DB.BuiltInCategory.OST_DuctTerminal):
                state.append(sorted((eid(p.Id),str(query.get_param_value(p))) for p in e.Parameters if p.HasValue))
        values.append(repr(state))
    values.sort()
    values += sorted('{}:{}'.format(eid(e.Id),str(e.VersionGuid))
                     for e in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol))
    return hashlib.sha256('\n'.join(values).encode('utf-8')).hexdigest()


def analyze_rooms(doc, phase_id=None):
    output=[];issues=[]
    options=DB.SpatialElementBoundaryOptions()
    options.SpatialElementBoundaryLocation=DB.SpatialElementBoundaryLocation.Finish
    for room in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Rooms).WhereElementIsNotElementType():
        phase=eid(room.get_Parameter(DB.BuiltInParameter.ROOM_PHASE).AsElementId())
        if phase_id is not None and phase!=int(phase_id):
            continue
        try:
            if room.Area<=0:
                raise ValueError('sin superficie')
            loops=room.GetBoundarySegments(options)
            rings=rings_from_curves([[seg.GetCurve() for seg in loop] for loop in loops])
            poly=polygon_from_rings(rings)
            boundaries=[]
            for loop in loops:
                for seg in loop:
                    delimiter=doc.GetElement(seg.ElementId)
                    virtual=delimiter is not None and delimiter.Category is not None and eid(delimiter.Category.Id)==int(DB.BuiltInCategory.OST_RoomSeparationLines)
                    boundaries.append({'element_id':eid(seg.ElementId),'kind':'VIRTUAL' if virtual else 'PHYSICAL',
                         'points_mm':[[p[0],p[1]] for p in sample_curve(seg.GetCurve())]})
            level=doc.GetElement(room.LevelId)
            output.append({'id':eid(room.Id),'uid':room.UniqueId,
                           'name':room.get_Parameter(DB.BuiltInParameter.ROOM_NAME).AsString() or '',
                           'number':room.Number,'phase_id':phase,'level_id':eid(level.Id),'level':name(level),
                           'level_z':mm(level.Elevation),'area_m2':room.Area*0.09290304,
                           'polygon':poly,'rings':rings,'boundaries':boundaries,'centroid':[poly.centroid.x,poly.centroid.y]})
        except Exception as exc:
            issues.append('Room {}: {}'.format(eid(room.Id),exc))
    return output,issues


def visible_in_phase(element, doc, phase_id):
    phase_ids=[eid(p.Id) for p in doc.Phases]
    index=phase_ids.index(phase_id)
    created=eid(element.CreatedPhaseId);demolished=eid(element.DemolishedPhaseId)
    return (created not in phase_ids or phase_ids.index(created)<=index) and (
        demolished not in phase_ids or phase_ids.index(demolished)>index)


def read_ceilings(doc, phase_id):
    output=[];issues=[]
    for ceiling in DB.FilteredElementCollector(doc).OfClass(DB.Ceiling):
        if not visible_in_phase(ceiling,doc,phase_id):
            continue
        accepted=0;nonplanar=False;face_index=0
        for solid in solids(ceiling):
            for face in solid.Faces:
                face_index+=1
                if not isinstance(face,DB.PlanarFace):
                    uv=face.GetBoundingBox()
                    mid=DB.UV((uv.Min.U+uv.Max.U)/2,(uv.Min.V+uv.Max.V)/2)
                    if face.ComputeNormal(mid).Z<-1e-6:nonplanar=True
                    continue
                normal=face.FaceNormal
                if normal.Z>=-1e-6:
                    continue
                loops=[[curve for curve in loop] for loop in face.GetEdgesAsCurveLoops()]
                poly=polygon_from_rings(rings_from_curves(loops))
                level=doc.GetElement(ceiling.LevelId)
                if not isinstance(level,DB.Level):
                    issues.append('Techo {} sin nivel de referencia válido.'.format(eid(ceiling.Id)))
                    continue
                accepted+=1
                data={'id':eid(ceiling.Id),'uid':ceiling.UniqueId,'polygon':poly,'z':mm(face.Origin.Z),
                      'level_id':eid(level.Id),'level_z':mm(level.Elevation),
                      'surface_id':ceiling.UniqueId+':'+str(face_index),
                      'plane':{'origin':[mm(face.Origin.X),mm(face.Origin.Y),mm(face.Origin.Z)],
                               'normal':[-normal.X,-normal.Y,-normal.Z]}}
                data['z']=surface.height(data,poly.centroid.x,poly.centroid.y)
                data['slope_deg']=surface.slope_degrees(data)
                zs=[surface.height(data,x,y) for ring in rings_from_curves(loops) for x,y in ring]
                data['z_min']=min(zs);data['z_max']=max(zs)
                output.append(data)
        if nonplanar or not accepted:
            issues.append('Techo {}: superficies curvas no planas o sin cara inferior proyectable requieren revisión.'.format(eid(ceiling.Id)))
    return output,issues


def find_room_ceilings(room, ceilings, rooms, allowed_ids=None):
    upper=min((r['level_z'] for r in rooms if r['level_z']>room['level_z']+100 and
               r['polygon'].intersects(room['polygon'])),default=float('inf'))
    found=[]
    for c in ceilings:
        if allowed_ids and c['id'] not in allowed_ids:
            continue
        poly=room['polygon'].intersection(c['polygon'])
        poly=surface.height_band(poly,c,room['level_z']+100,upper)
        if poly.area>1:found.append(dict(c,polygon=poly))
    return surface.visible_surfaces(found)


def instance_basis(instance):
    t=instance.GetTransform()
    return [[v.X,v.Y,v.Z] for v in (t.BasisX,t.BasisY,t.BasisZ)]


def read_symbols(doc):
    result={}
    for role,(family_name,type_name) in TERMINALS.items():
        candidates=[s for s in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol)
                    if terminal_matches(s.FamilyName,name(s),{'impulsion':'SUPPLY','extraccion':'EXTRACT'}[role]) and
                    eid(s.Category.Id)==int(DB.BuiltInCategory.OST_DuctTerminal)]
        if len(candidates)!=1:
            available=[name(s) for s in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol)
                       if s.FamilyName==family_name]
            raise ValueError('Se requiere exactamente un tipo: '+family_name+' / '+type_name+
                             '. Tipos cargados en esa familia: '+(', '.join(sorted(available)) or 'ninguno'))
        symbol=candidates[0]
        placement=str(symbol.Family.FamilyPlacementType)
        if placement!='OneLevelBased':
            raise ValueError('Tipo incompatible: {}. Alojamiento {} requiere adaptación explícita.'.format(type_name,placement))
        b=bounds(symbol)
        result[role]={'id':eid(symbol.Id),'uid':symbol.UniqueId,'family':symbol.FamilyName,'type':type_name,
                      'placement':placement,'bounds':b,'visible_z':b[2],
                      'dimensions':[2*max(abs(b[0]),abs(b[3])),2*max(abs(b[1]),abs(b[4])),b[5]-b[2]]}
    return result


def read_doors(doc,rooms,phase_id):
    phase=doc.GetElement(DB.ElementId(phase_id));edges=[];points={r['uid']:[] for r in rooms};issues=[]
    for door in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Doors).WhereElementIsNotElementType():
        if not visible_in_phase(door,doc,phase_id):
            continue
        try:
            a=door.get_FromRoom(phase);b=door.get_ToRoom(phase)
            uids=[r.UniqueId for r in (a,b) if r is not None and r.UniqueId in points]
            location=door.Location.Point
            xy=(mm(location.X),mm(location.Y))
            for uid in uids:
                points[uid].append(xy)
            if len(uids)==2:
                edges.append(uids)
        except Exception as exc:
            issues.append('Puerta {}: relaciones no disponibles ({})'.format(eid(door.Id),exc))
    for i,a in enumerate(rooms):
        for b in rooms[:i]:
            if a['level_id']!=b['level_id']:continue
            virtual_a={v['element_id'] for v in a.get('boundaries',[]) if v['kind']=='VIRTUAL'}
            virtual_b={v['element_id'] for v in b.get('boundaries',[]) if v['kind']=='VIRTUAL'}
            if virtual_a.intersection(virtual_b):edges.append([a['uid'],b['uid']])
    return edges,points,issues


def read_kitchen_hints(doc,rooms,phase_id):
    from .airflow import normalize
    result=[]
    categories=(DB.BuiltInCategory.OST_PlumbingFixtures,DB.BuiltInCategory.OST_Furniture,
                DB.BuiltInCategory.OST_Casework,DB.BuiltInCategory.OST_SpecialityEquipment,DB.BuiltInCategory.OST_MechanicalEquipment)
    for category in categories:
        for e in DB.FilteredElementCollector(doc).OfCategory(category).WhereElementIsNotElementType():
            if e.HasPhases() and not visible_in_phase(e,doc,phase_id):continue
            symbol=getattr(e,'Symbol',None)
            labels=[name(e),getattr(symbol,'FamilyName','')]
            for source in (e,symbol):
                if source is None:continue
                p=source.get_Parameter(DB.BuiltInParameter.ALL_MODEL_DESCRIPTION)
                if p and p.HasValue:labels.append(p.AsString() or '')
            text=normalize(' '.join(labels));evidence=[]
            for kind,words in (('SINK',('fregadero','fregador','sink')),('HOB',('placa de coccion','vitro','cooktop','hob','encimera cocina')),
                               ('HOOD',('campana','extractor cocina','range hood')),('KITCHEN_FURNITURE',('mueble cocina','kitchen','cocina'))):
                if any(word in text for word in words):evidence.append(kind)
            if not evidence:continue
            bbox=e.get_BoundingBox(None)
            if bbox is None:continue
            xyz=[mm((getattr(bbox.Min,a)+getattr(bbox.Max,a))/2) for a in ('X','Y','Z')]
            candidates=[r for r in rooms if r['polygon'].covers(Point(xyz[:2])) and r['level_z']<=xyz[2]<r['level_z']+4000]
            if not candidates:continue
            r=max(candidates,key=lambda r:r['level_z'])
            result.append({'room_uid':r['uid'],'element_id':eid(e.Id),'element_uid':e.UniqueId,'label':name(e),
                           'category':str(category),'evidence':evidence,'position_mm':xyz})
    return result


def read_obstacles(doc,phase_id=None):
    """Conservative bbox prefilter. Final write verifies actual solid intersection."""
    names=('OST_Walls','OST_Floors','OST_StructuralFraming','OST_StructuralColumns',
           'OST_LightingFixtures','OST_MechanicalEquipment','OST_DuctCurves','OST_FlexDuctCurves',
           'OST_DuctFitting','OST_DuctAccessory','OST_PipeCurves','OST_PipeFitting',
           'OST_CableTray','OST_Conduit','OST_Ceilings','OST_Furniture','OST_PlumbingFixtures',
           'OST_GenericModel','OST_DuctTerminal','OST_RvtLinks','OST_Casework','OST_SpecialityEquipment')
    categories={int(getattr(DB.BuiltInCategory,n)) for n in names}
    output=[]
    for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType():
        if e.Category is None or eid(e.Category.Id) not in categories:
            continue
        if phase_id is not None and e.HasPhases() and not visible_in_phase(e,doc,phase_id):
            continue
        bbox=e.get_BoundingBox(None)
        if bbox is None:
            continue
        corners=[bbox.Transform.OfPoint(DB.XYZ(x,y,z)) for x in (bbox.Min.X,bbox.Max.X)
                 for y in (bbox.Min.Y,bbox.Max.Y) for z in (bbox.Min.Z,bbox.Max.Z)]
        x0=min(mm(p.X) for p in corners);x1=max(mm(p.X) for p in corners)
        y0=min(mm(p.Y) for p in corners);y1=max(mm(p.Y) for p in corners)
        output.append({'id':eid(e.Id),'polygon':box(x0,y0,x1,y1),'z0':min(mm(p.Z) for p in corners),
                       'z1':max(mm(p.Z) for p in corners),'label':name(e),
                       'furniture':eid(e.Category.Id) in (int(DB.BuiltInCategory.OST_Furniture),int(DB.BuiltInCategory.OST_PlumbingFixtures)),
                       'linked':isinstance(e,DB.RevitLinkInstance)})
    return output


def public_room(room):
    return dict((k,v) for k,v in room.items() if k!='polygon')
