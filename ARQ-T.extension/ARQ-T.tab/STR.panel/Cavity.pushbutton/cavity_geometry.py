import math
from pyrevit.api import DB
from pyrevit.revit.db import query
from shapely.geometry import Polygon
from shapely import affinity

MM = 304.8


def symbols(doc):
    return sorted([s for s in query.get_types_by_class(DB.FamilySymbol,doc=doc)
                   if 'IGLU' in query.get_name(s).upper()],key=lambda s:(s.FamilyName,query.get_name(s)))


def solid_bounds(symbol, coordinate_transform=None):
    pts=[]
    def visit(geom,transform):
        if geom is None:
            return
        for o in geom:
            if isinstance(o,DB.GeometryInstance):
                visit(o.GetSymbolGeometry(),transform.Multiply(o.Transform))
            elif isinstance(o,DB.Solid) and o.Volume>1e-10:
                b=o.GetBoundingBox()
                for x in (b.Min.X,b.Max.X):
                    for y in (b.Min.Y,b.Max.Y):
                        for z in (b.Min.Z,b.Max.Z):
                            pts.append(transform.OfPoint(b.Transform.OfPoint(DB.XYZ(x,y,z))))
    opt=DB.Options()
    opt.DetailLevel=DB.ViewDetailLevel.Fine
    opt.IncludeNonVisibleObjects=False
    visit(symbol.get_Geometry(opt),coordinate_transform or DB.Transform.Identity)
    if not pts:
        return None
    return tuple((min(getattr(p,k) for p in pts),max(getattr(p,k) for p in pts)) for k in ('X','Y','Z'))


def dimensions(symbol):
    bounds=solid_bounds(symbol)
    if bounds:
        return tuple(b-a for a,b in bounds)
    values=[]
    for names in [('Length','Longitud','Largo'),('Width','Anchura','Ancho'),('Height','Altura')]:
        value=None
        for name in names:
            p=symbol.LookupParameter(name)
            if p and p.StorageType==DB.StorageType.Double and p.AsDouble()>0:
                value=p.AsDouble()
                break
        if value is None:
            raise ValueError('Tipo sin geometría disponible ni dimensiones inequívocas: '+query.get_name(symbol))
        values.append(value)
    return tuple(values)


def floor_data(floor):
    options=DB.Options()
    options.ComputeReferences=True
    options.DetailLevel=DB.ViewDetailLevel.Fine
    geometry=floor.get_Geometry(options)
    solids=[obj for obj in geometry if isinstance(obj,DB.Solid) and obj.Volume>1e-10]
    bottom_faces=[]
    top_faces=[]
    for solid in solids:
        for current in solid.Faces:
            if isinstance(current,DB.PlanarFace):
                if current.FaceNormal.Z<-0.999999999:
                    bottom_faces.append(current)
                elif current.FaceNormal.Z>0.999999999:
                    top_faces.append(current)
    if len(bottom_faces)!=1 or len(top_faces)!=1:
        raise ValueError('Se requieren caras inferior y superior únicas y horizontales, sin edición de forma.')
    face=bottom_faces[0]
    top=top_faces[0]
    reference=top.Reference
    if reference is None:
        references=list(DB.HostObjectUtils.GetTopFaces(floor))
        if len(references)!=1:
            raise ValueError('No se ha podido identificar la referencia anfitriona.')
        reference=references[0]
    if (not isinstance(face,DB.PlanarFace) or face.FaceNormal.Z>-0.999999999 or
            not isinstance(top,DB.PlanarFace) or top.FaceNormal.Z<0.999999999):
        raise ValueError('Solo se admiten suelos horizontales.')
    rings=[]
    lines=[]
    curved=False
    error=0.1/MM
    for loop in face.GetEdgesAsCurveLoops():
        points=[]
        for c in loop:
            if isinstance(c,DB.Line):
                p=c.GetEndPoint(0)
                points.append((p.X,p.Y))
                lines.append(c)
            elif isinstance(c,DB.Arc):
                curved=True
                sweep=c.Length/c.Radius
                step=2*math.acos(max(-1,min(1,1-error/c.Radius)))
                n=max(2,int(math.ceil(sweep/max(step,1e-6))))
                if n>20000:
                    raise ValueError('Arco demasiado complejo.')
                for i in range(n):
                    p=c.Evaluate(i/n,True)
                    points.append((p.X,p.Y))
            else:
                raise ValueError('Contorno con spline/elipse no compatible en esta versión.')
        rings.append(points)
    rings.sort(key=lambda r:Polygon(r).area,reverse=True)
    polygon=Polygon(rings[0],rings[1:])
    if not polygon.is_valid or polygon.area<=0:
        raise ValueError('Contorno no válido.')
    if abs(polygon.area-face.Area)>max(face.Area*1e-5,face.GetEdgesAsCurveLoops()[0].GetExactLength()*error*2):
        raise ValueError('El contorno calculado no coincide con el área de la cara inferior actual.')
    if curved:
        polygon=polygon.buffer(-2*error)
        if polygon.geom_type!='Polygon' or polygon.is_empty:
            raise ValueError('Contorno curvo demasiado estrecho.')
    if lines:
        longest=max(lines,key=lambda c:c.Length)
        d=longest.GetEndPoint(1)-longest.GetEndPoint(0)
        angle=math.atan2(d.Y,d.X)%math.pi
    else:
        angle=0.0
    origin=polygon.centroid.coords[0]
    shifted=affinity.translate(polygon,-origin[0],-origin[1])
    local=affinity.rotate(shifted,-angle,origin=(0,0),use_radians=True)
    return {'poly':local,'angle':angle,'origin':origin,'z':face.Origin.Z,
            'reference':reference,'host_z':top.Origin.Z,'level':floor.LevelId,'area':face.Area,'curved':curved,
            'floor_uid':floor.UniqueId,'document':floor.Document,'source':'current_solid'}


def world(data,x,y):
    a=data['angle']
    return (data['origin'][0]+x*math.cos(a)-y*math.sin(a),
            data['origin'][1]+x*math.sin(a)+y*math.cos(a))
