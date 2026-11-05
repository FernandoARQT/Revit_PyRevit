import math
from shapely.geometry import Polygon,Point,box
from shapely.ops import unary_union
from .airflow import normalize
from . import surface


def polygon_from_rings(rings):
    regions=[Polygon(r) for r in rings if len(r)>=3]
    if not regions or any(not r.is_valid for r in regions):raise ValueError('Contornos inválidos.')
    result=Polygon()
    for region in sorted(regions,key=lambda p:-p.area):result=result.symmetric_difference(region)
    if result.is_empty or not result.is_valid:raise ValueError('Superficie vacía o inválida.')
    return result


def optimize_terminal_positions(room,ceilings,count,dimensions,config,doors=(),obstacles=(),occupied=(),role='impulsion',furniture=(),preferred=None):
    step=float(config['resolution_mm']);clear=float(config['wall_clearance_mm'])
    if role=='extraccion':clear=max(clear,100.01)
    if not 50<=step<=2000 or clear<0:raise ValueError('Resolución de 50–2000 mm y separación no negativa.')
    width,depth,height=dimensions
    avoiding=[f for f in furniture if any(k in normalize(f.get('label','')) for k in
              (('cama','sofa','sillon','mesa') if role=='impulsion' else ('coccion','vitro','placa','campana')))]
    wet=[f for f in furniture if role=='extraccion' and any(k in normalize(f.get('label','')) for k in ('ducha','banera','lavabo','lavadero'))]
    proposals=[]
    poly=room
    for ceiling in ceilings:
        useful=poly.intersection(ceiling['polygon']).buffer(-clear-0.1)
        if useful.is_empty:continue
        x0,y0,x1,y1=useful.bounds
        nx=int((x1-x0)//step)+1;ny=int((y1-y0)//step)+1
        if nx*ny>100000:raise ValueError('Demasiadas posiciones candidatas; aumente la resolución.')
        for i in range(nx):
            for j in range(ny):
                x=x0+(x1-x0-(nx-1)*step)/2+i*step;y=y0+(y1-y0-(ny-1)*step)/2+j*step
                footprint,z0,z1=surface.envelope(ceiling,x,y,dimensions)
                if not useful.covers(footprint):continue
                z=surface.height(ceiling,x,y)
                if any(b['z0']<z1 and b['z1']>z0 and footprint.intersects(b['polygon']) for b in obstacles if b.get('id')!=ceiling['id']):continue
                if any(math.hypot(x-p[0],y-p[1])<max(width,depth,config['terminal_spacing_mm']) for p in occupied):continue
                wall=Point(x,y).distance(poly.boundary)
                door=min((math.hypot(x-d[0],y-d[1]) for d in doors),default=1000)
                center=Point(x,y).distance(poly.centroid)
                if role=='extraccion' and any(f['polygon'].covers(Point(x,y)) for f in avoiding):continue
                occupied_distance=min((Point(x,y).distance(f['polygon']) for f in avoiding),default=1500)
                wet_distance=min((Point(x,y).distance(f['polygon']) for f in wet),default=0)
                score=config['weights']['wall']*min(wall,1200)/1200+config['weights']['door']*min(door,2000)/2000-config['weights']['center']*center/max(1,math.sqrt(poly.area))+config['weights'].get('furniture',1)*min(occupied_distance,1500)/1500-config['weights'].get('wet',0.4)*min(wet_distance,2000)/2000
                proposals.append({'x':x,'y':y,'z':z,'preferred_zone':preferred is None or preferred.covers(Point(x,y)),
                                  'plane':surface.plane(ceiling),'ceiling_surface_id':surface.key(ceiling),
                                  'slope_deg':surface.slope_degrees(ceiling),'ceiling_id':ceiling['id'],'level_id':ceiling['level_id'],'score':score,
                                  'criteria':{'wall_mm':wall,'door_mm':door,'centroid_mm':center,'occupied_mm':occupied_distance}})
    proposals.sort(key=lambda p:(not p['preferred_zone'],-p['score'],p['ceiling_id'],p['x'],p['y']))
    chosen=[]
    for index in range(count):
        remaining=[p for p in proposals if all(math.hypot(p['x']-q['x'],p['y']-q['y'])>=config['terminal_spacing_mm'] for q in chosen)]
        if not remaining:raise ValueError('No hay espacio libre para todas las bocas solicitadas.')
        def joint(p):
            separation=min((math.hypot(p['x']-q['x'],p['y']-q['y']) for q in chosen),default=0)
            return (not p['preferred_zone'],-p['score']-config['weights']['spread']*min(separation,3000)/3000,p['ceiling_id'],p['x'],p['y'])
        remaining.sort(key=joint)
        alternatives=[]
        for p in remaining:
            if all(math.hypot(p['x']-q['x'],p['y']-q['y'])>=step for q in alternatives):alternatives.append(dict(p))
            if len(alternatives)==3:break
        chosen.append(dict(alternatives[0],alternatives=alternatives))
    for p in chosen:
        p['alternatives']=[a for a in p['alternatives'] if all(
            q is p or math.hypot(a['x']-q['x'],a['y']-q['y'])>=config['terminal_spacing_mm']-0.001
            for q in chosen)]
    return chosen


def determine_terminal_count(room,requested=1):
    """Initial count is user-controlled; only suggest more for geometric irregularity."""
    if type(requested)!=int or not 1<=requested<=30:
        raise ValueError('Número de bocas entre 1 y 30.')
    x0,y0,x1,y1=room.bounds
    aspect=max(x1-x0,y1-y0)/max(1,min(x1-x0,y1-y0))
    irregular=room.area/max(1,room.convex_hull.area)<0.7 or aspect>2.5
    return {'count':requested,'suggested_count':max(requested,2) if irregular else requested,
            'reason':'Contorno alargado o muy cóncavo: valore dos o más bocas.' if irregular else
                     'Una boca inicial; comprobar difusión, acústica y caudal con fabricante.'}
