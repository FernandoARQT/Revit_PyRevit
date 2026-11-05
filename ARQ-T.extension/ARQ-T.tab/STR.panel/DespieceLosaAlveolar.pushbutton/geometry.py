import math

TOL_MM=0.01


def rectangle(points,tol=TOL_MM):
    if len(points)!=4:
        raise ValueError('El croquis debe tener exactamente cuatro bordes rectos.')
    xs=[p[0] for p in points]
    ys=[p[1] for p in points]
    xmin,xmax=min(xs),max(xs)
    ymin,ymax=min(ys),max(ys)
    if min(xmax-xmin,ymax-ymin)<=tol:
        raise ValueError('Rectángulo degenerado.')
    for a,b in zip(points,points[1:]+points[:1]):
        dx,dy=abs(a[0]-b[0]),abs(a[1]-b[1])
        if not ((dx<=tol and dy>tol) or (dy<=tol and dx>tol)):
            raise ValueError('Bordes no alineados con N-S/E-O del proyecto.')
    corners={(round((x-xmin)/(xmax-xmin)),round((y-ymin)/(ymax-ymin))) for x,y in points}
    if len(corners)!=4:
        raise ValueError('El contorno no es rectangular.')
    return xmin,ymin,xmax,ymax


def dimensions(bounds,direction):
    x0,y0,x1,y1=bounds
    return (y0,y1,x0,x1) if direction=='N-S' else (x0,x1,y0,y1)


def plate_profiles(bounds,direction,solution,reverse=False):
    if not solution['valid']:
        raise ValueError('No se puede generar una alternativa no admisible.')
    u0,u1,v0,v1=dimensions(bounds,direction)
    order=list(reversed(solution['order'])) if reverse else solution['order']
    result=[]
    cursor=v0+solution['left']
    for w in order:
        end=cursor+w
        if cursor<v0-1e-6 or end>v1+1e-6:
            raise ValueError('Una placa excede el perímetro.')
        uv=[(u0,cursor),(u1,cursor),(u1,end),(u0,end)]
        result.append([(v,u) if direction=='N-S' else (u,v) for u,v in uv])
        cursor=end
    if abs(cursor+solution['right']-v1)>1e-6:
        raise ValueError('Las placas y holguras no cierran el ancho del paño.')
    return result
