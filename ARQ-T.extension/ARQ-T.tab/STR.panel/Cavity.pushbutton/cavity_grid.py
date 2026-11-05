import math
import numpy as np
import shapely
from shapely.geometry import box


def solve(poly, width, height, gap=0.0):
    if not all(math.isfinite(v) for v in (width,height,gap)) or min(width,height)<=0 or gap<0:
        raise ValueError('Dimensiones o separación inválidas.')
    x0,y0,x1,y1=poly.bounds
    length,breadth=x1-x0,y1-y0
    nx=int(math.floor((length+gap)/(width+gap)))
    ny=int(math.floor((breadth+gap)/(height+gap)))
    if nx<1 or ny<1:
        raise ValueError('No cabe ninguna pieza completa.')
    if nx*ny>30000:
        raise ValueError('Más de 30.000 celdas: reduzca el ámbito de cálculo.')
    matrix_x=nx*width+(nx-1)*gap
    matrix_y=ny*height+(ny-1)*gap
    remainder_x=length-matrix_x
    remainder_y=breadth-matrix_y
    margin_x=remainder_x/2
    margin_y=remainder_y/2
    start_x=x0+margin_x
    start_y=y0+margin_y
    xs=start_x+width/2+np.arange(nx)*(width+gap)
    ys=start_y+height/2+np.arange(ny)*(height+gap)
    xx,yy=np.meshgrid(xs,ys)
    xx,yy=xx.ravel(),yy.ravel()
    shapely.prepare(poly)
    mask=shapely.covers(poly,shapely.box(xx-width/2,yy-height/2,xx+width/2,yy+height/2))
    points=list(zip(xx[mask].tolist(),yy[mask].tolist()))
    if not points:
        raise ValueError('No cabe ninguna pieza completa en la retícula centrada.')
    margins=(min(p[0] for p in points)-width/2-x0,x1-max(p[0] for p in points)-width/2,
             min(p[1] for p in points)-height/2-y0,y1-max(p[1] for p in points)-height/2)
    return {'points':points,'count':len(points),'rows':ny,'columns':nx,
            'floor_size':(length,breadth),'matrix_size':(matrix_x,matrix_y),
            'remainder':(remainder_x,remainder_y),'start':(start_x,start_y),
            'start_margins':(margin_x,margin_y),'margins':margins,
            'occupied':len(points)*width*height,'remaining':poly.area-len(points)*width*height,
            'exact':poly.equals(box(*poly.bounds)),'maximum_certified':len(points)==nx*ny,
            'centered':True,'score':(len(points),0)}
