import math
import re
import unicodedata
from collections import deque

SOURCE='https://www.codigotecnico.org/pdf/Documentos/HS/DBHS.pdf'
USES=('dormitorio','salon','despacho','cocina','bano','aseo','mixto',
      'dormitorio_mixto','vestidor','transferencia','excluir','revisar')
STANDARD_VERSION='DB-HS 14/06/2022, HS 3 tabla 2.1; verificado 02/11/2026'


def normalize(name):
    text=''.join(c for c in unicodedata.normalize('NFD',name.casefold()) if unicodedata.category(c)!='Mn')
    return re.sub(r'[^a-z0-9]+',' ',text).strip()


def classify_room(name):
    text=normalize(name)
    if is_exterior(name):return 'excluir'
    if 'vestidor' in text or 'dressing' in text:return 'vestidor'
    dry=any(k in text for k in ('salon','comedor','estar'))
    if 'dormitorio' in text or 'habitacion' in text or re.search(r'\bhab\b',text):
        return 'dormitorio_mixto' if 'cocina' in text else 'dormitorio'
    if 'cocina' in text and (dry or 'despacho' in text):return 'mixto'
    if dry:return 'salon'
    if 'despacho' in text or 'sala de juegos' in text:return 'despacho'
    if 'cocina' in text:return 'cocina'
    if 'bano' in text:return 'bano'
    if 'aseo' in text:return 'aseo'
    if any(k in text for k in ('pasillo','distribuidor','vestibulo','escalera')):return 'transferencia'
    if any(k in text for k in ('porche','terraza','garaje','sala de maquinas')):return 'excluir'
    return 'revisar'


def is_exterior(name):
    return bool(re.search(r'\b(?:porches?|terrazas?)\b',normalize(name)))


def room_use(room,overrides=None):
    if is_exterior(room['name']):return 'excluir'
    use=(overrides or {}).get(room['uid'],{}).get('use',classify_room(room['name']))
    return {'principal':'dormitorio','principal_mixto':'dormitorio_mixto'}.get(use,use)


def choose_main_bedroom(rooms,overrides=None,selected_uid=None):
    candidates=[r for r in rooms if room_use(r,overrides) in ('dormitorio','dormitorio_mixto')]
    if selected_uid:
        if selected_uid not in {r['uid'] for r in candidates}:raise ValueError('El dormitorio principal seleccionado no es un dormitorio válido de esta vivienda.')
        return {'uid':selected_uid,'reason':'selección manual','candidates':[r['uid'] for r in candidates]}
    def priority(r):
        text=normalize(r['name'])
        rank=0 if ('principal' in text or re.search(r'\bppal\b',text)) else 1 if text in ('habitacion','dormitorio','hab') else 2 if re.fullmatch(r'(habitacion|dormitorio|hab) 0?1',text) else 3
        return rank,-float(r['area_m2']),str(r['uid'])
    winner=min(candidates,key=priority) if candidates else None
    return {'uid':winner['uid'] if winner else None,'reason':'prioridad de nombre y superficie; revisable',
            'candidates':[r['uid'] for r in sorted(candidates,key=priority)]}


def calculate_airflows(rooms,overrides=None,main_room_uid=None,zones=None):
    overrides=overrides or {}
    classified=[]
    for room in rooms:
        use=room_use(room,overrides)
        if use not in USES or use=='revisar':
            raise ValueError('Clasifique el uso de {} ({}).'.format(room['name'],room['number']))
        classified.append((room,use))
    beds=sum(use in ('dormitorio','dormitorio_mixto') for r,use in classified)
    main=choose_main_bedroom(rooms,overrides,main_room_uid)
    living,wet_total,wet_local=(6,12,6) if beds<=1 else (8,24,7) if beds==2 else (10,33,8)
    rows=[]
    for room,use in classified:
        roles=[]
        if use in ('dormitorio','salon','despacho','mixto','dormitorio_mixto'):
            minimum=(8 if room['uid']==main['uid'] else 4) if use.startswith('dormitorio') else living
            if any(k in normalize(room['name']) for k in ('salon','comedor','estar','despacho')):
                minimum=max(minimum,living)
            roles.append(('impulsion',minimum))
        if use in ('cocina','bano','aseo','mixto','dormitorio_mixto'):roles.append(('extraccion',wet_local))
        for role,minimum in roles:
            area=float(room.get('area_m2',0))
            if not math.isfinite(area) or area<=0:
                raise ValueError('Superficie útil no válida: '+room['name'])
            key=room['uid']+':'+role
            config=overrides.get(key,{})
            design=float(config.get('design',minimum))
            count=config.get('count',1)
            if not math.isfinite(design) or design<minimum or type(count)!=int or count<1 or count>30:
                raise ValueError('Caudal inferior al mínimo o número de bocas inválido: '+room['name'])
            rows.append({'key':key,'room_uid':room['uid'],'name':room['name'],'number':room['number'],'level_id':room['level_id'],
                         'use':use,'role':role,'minimum':float(minimum),'requested':design,'count':count,'area_m2':area,
                         'minimum_basis':'DB-HS 3 tabla 2.1; principal' if room['uid']==main['uid'] and role=='impulsion' else 'DB-HS 3 tabla 2.1'})
    zone_rooms={z['room_uid'] for z in zones or [] if z.get('use')=='COCINA'}
    for room,use in classified:
        if room['uid'] in zone_rooms and not any(r['room_uid']==room['uid'] and r['role']=='extraccion' for r in rows):
            key=room['uid']+':extraccion';item=overrides.get(key,{})
            requested=float(item.get('design',wet_local));count=item.get('count',1)
            if not math.isfinite(requested) or requested<wet_local or type(count)!=int or not 1<=count<=30:raise ValueError('Caudal o número de bocas inválido para zona de cocina.')
            rows.append({'key':key,'room_uid':room['uid'],'name':room['name'],'number':room['number'],'level_id':room['level_id'],
                'use':use,'role':'extraccion','minimum':float(wet_local),'requested':requested,'count':count,'area_m2':float(room['area_m2']),
                'minimum_basis':'DB-HS 3 tabla 2.1, zona húmeda en local mixto'})
    if not any(r['role']=='impulsion' for r in rows) or not any(r['role']=='extraccion' for r in rows):
        raise ValueError('La vivienda requiere locales secos y húmedos definidos; no se puede equilibrar.')
    result=balance_airflows(rows,wet_total)
    result.update(main_bedroom=main,bedroom_count=beds,standard_version=STANDARD_VERSION)
    result['warnings']+=['Vestidor: revisar ventilación o transferencia, sin mínimo automático de dormitorio: '+r['name'] for r,u in classified if u=='vestidor']
    return result


def balance_airflows(rows,wet_total):
    sums={role:sum(r['requested'] for r in rows if r['role']==role) for role in ('impulsion','extraccion')}
    target=max(sums['impulsion'],sums['extraccion'],wet_total)
    for role in sums:
        group=[r for r in rows if r['role']==role]
        extra=target-sums[role]
        area_total=sum(r['area_m2'] for r in group)
        for r in group:r['design']=r['requested']+extra*r['area_m2']/area_total
        group[-1]['design']+=target-sum(r['design'] for r in group)
        for r in group:r['flow_each']=r['design']/r['count']
    return {'rows':rows,'supply':target,'extract':target,'difference':0.0,'wet_total_minimum':wet_total,'source':SOURCE,
            'balancing_basis':'Incrementos proporcionales a superficie útil; sin reducir mínimos ni caudales solicitados.',
            'bedroom_policy':'Principal 8 L/s; secundarios 4 L/s; selección automática revisable.',
            'warnings':['Se identifica un dormitorio principal por nombre y superficie; revise la selección.',
                        'La superficie reparte los incrementos de equilibrado; no sustituye los mínimos de DB-HS 3.',
                        'Selección de bocas provisional: sin datos acústicos ni de caudal del fabricante.','Flow almacena diseño; no está asociado al conector HVAC.','Campana de cocina y red de conductos excluidas.']}


def transfer_issues(rooms,edges,rows):
    graph={r['uid']:set() for r in rooms}
    for a,b in edges:
        if a in graph and b in graph:graph[a].add(b);graph[b].add(a)
    wet={r['room_uid'] for r in rows if r['role']=='extraccion'}
    issues=[]
    for r in rows:
        if r['role']!='impulsion':continue
        reached=set();queue=deque([r['room_uid']])
        while queue:
            uid=queue.popleft()
            if uid in reached:continue
            reached.add(uid);queue.extend(graph.get(uid,set())-reached)
        if not reached.intersection(wet):issues.append('No consta camino por puertas hasta un local húmedo: '+r['name'])
    return issues+['Revisar aberturas de transferencia: las puertas no acreditan una sección de paso suficiente.']
