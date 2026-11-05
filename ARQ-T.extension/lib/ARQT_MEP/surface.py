"""Planar ceiling geometry and terminal poses, independent of the Revit API."""
import math
from shapely.geometry import Polygon,MultiPoint

INSTALLATION_OFFSET_MM=-1.0


def dot(a,b):return sum(x*y for x,y in zip(a,b))


def cross(a,b):return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]


def unit(v):
    size=math.sqrt(dot(v,v))
    if size<1e-12:raise ValueError('Normal o eje degenerado.')
    return [x/size for x in v]


def plane(surface):
    p=surface.get('plane',{'origin':[surface.get('x',0),surface.get('y',0),surface.get('z',0)],'normal':[0,0,1]})
    normal=unit(p['normal'])
    if normal[2]<0:normal=[-v for v in normal]
    if normal[2]<=1e-6:raise ValueError('Cara vertical: no constituye un plano inferior de techo proyectable.')
    return {'origin':list(p['origin']),'normal':normal}


def coefficients(surface):
    p=plane(surface);x,y,z=p['origin'];nx,ny,nz=p['normal']
    return -nx/nz,-ny/nz,z+(nx*x+ny*y)/nz


def height(surface,x,y):
    p=plane(surface);ox,oy,oz=p['origin'];nx,ny,nz=p['normal']
    return oz-(nx*(x-ox)+ny*(y-oy))/nz


def slope_degrees(surface):return math.degrees(math.acos(min(1,max(-1,plane(surface)['normal'][2]))))


def key(surface):
    return surface.get('surface_id',surface.get('ceiling_surface_id','{}:{}'.format(surface.get('id',surface.get('ceiling_id')),surface.get('z'))))


def frame(surface):
    """Right-handed minimal rotation from local +Z to the upward ceiling normal."""
    n=plane(surface)['normal'];v=cross([0,0,1],n);c=n[2]
    def rotate(a):
        va=cross(v,a);vva=cross(v,va)
        return [a[i]+va[i]+vva[i]/(1+c) for i in range(3)]
    return [rotate([1,0,0]),rotate([0,1,0]),n]


def insertion(surface,visible_z):
    n=plane(surface)['normal']
    return [surface[a]-n[i]*visible_z for i,a in enumerate(('x','y','z'))]


def installation_insertion(surface,visible_z,offset_mm=INSTALLATION_OFFSET_MM):
    """Absolute installation point: lower Revit's vertical offset once, in mm."""
    point=insertion(surface,visible_z);point[2]+=offset_mm
    return point


def visible_point(position,basis,visible_z):
    return [position[i]+basis[2][i]*visible_z for i in range(3)]


def envelope(surface,x,y,dimensions):
    width,depth,extent=dimensions;basis=frame(surface);z=height(surface,x,y)
    corners=[]
    for u in (-width/2,width/2):
        for v in (-depth/2,depth/2):
            for w in (0,extent):
                corners.append([a+u*basis[0][i]+v*basis[1][i]+w*basis[2][i] for i,a in enumerate((x,y,z))])
    footprint=MultiPoint([(p[0],p[1]) for p in corners]).convex_hull
    return footprint,min(p[2] for p in corners),max(p[2] for p in corners)


def clip_linear(poly,a,b,c):
    """Intersect polygon with a*x+b*y+c >= 0, preserving holes/multipart results."""
    if poly.is_empty:return poly
    if abs(a)+abs(b)<1e-14:return poly if c>=0 else Polygon()
    x0,y0,x1,y1=poly.bounds
    pad=max(x1-x0,y1-y0,1)+1
    vertices=[(x0-pad,y0-pad),(x1+pad,y0-pad),(x1+pad,y1+pad),(x0-pad,y1+pad)]
    out=[]
    for start,end in zip(vertices,vertices[1:]+vertices[:1]):
        f0=a*start[0]+b*start[1]+c;f1=a*end[0]+b*end[1]+c
        if f0>=0:out.append(start)
        if (f0>=0)!=(f1>=0):
            t=f0/(f0-f1)
            out.append((start[0]+t*(end[0]-start[0]),start[1]+t*(end[1]-start[1])))
    return poly.intersection(Polygon(out)) if len(out)>=3 else Polygon()


def height_band(poly,surface,minimum,maximum=float('inf')):
    a,b,c=coefficients(surface)
    poly=clip_linear(poly,a,b,c-minimum)
    return clip_linear(poly,-a,-b,maximum-c) if math.isfinite(maximum) else poly


def visible_surfaces(surfaces):
    """Keep the lowest real face at each XY, also when two planes cross."""
    result=[]
    for s in surfaces:
        poly=s['polygon'];a,b,c=coefficients(s)
        for other in surfaces:
            if other is s:continue
            u,v,w=coefficients(other)
            overlap=poly.intersection(other['polygon'])
            if overlap.is_empty:continue
            if abs(a-u)+abs(b-v)<1e-10 and abs(c-w)<0.1:
                hidden=overlap if str(key(other))<str(key(s)) else Polygon()
            else:
                hidden=clip_linear(overlap,a-u,b-v,c-w-0.001)
            poly=poly.difference(hidden)
            if poly.is_empty:break
        if not poly.is_empty:result.append(dict(s,polygon=poly))
    return result


def basis_error_degrees(actual,desired):
    return max(math.degrees(math.acos(min(1,max(-1,dot(unit(a),unit(b)))))) for a,b in zip(actual,desired))


def axis_angle(actual,desired):
    """World rotation mapping actual basis columns onto desired columns."""
    if dot(actual[0],cross(actual[1],actual[2]))<0:
        raise ValueError('Instancia reflejada: la orientación requiere revisión manual.')
    r=[[sum(desired[k][i]*actual[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    angle=math.acos(min(1,max(-1,(sum(r[i][i] for i in range(3))-1)/2)))
    if angle<1e-9:return None
    if math.pi-angle<1e-6:
        i=max(range(3),key=lambda i:r[i][i]);axis=[0,0,0]
        axis[i]=math.sqrt(max(0,(r[i][i]+1)/2))
        for j in range(3):
            if j!=i:axis[j]=(r[i][j]+r[j][i])/(4*axis[i])
    else:
        axis=[r[2][1]-r[1][2],r[0][2]-r[2][0],r[1][0]-r[0][1]]
    return unit(axis),angle
