"""Read-only plan context for the MEP tracer, independent of terminal drafts."""
import copy
import math
import uuid
from shapely.geometry import Point
from ... import proposal as terminals,surface
from ...spatial import polygon_from_rings


def check(data):
    if data.get('kind')=='VentilationProposal':return terminals.check(data)
    if data.get('kind')!='RoutingPlanBackground' or data.get('schema_version')!=1 or terminals.seal(data)['content_hash']!=data.get('content_hash'):
        raise ValueError('Fondo de trazado incompleto o incompatible.')
    return data


def from_catalog(analysis,catalog,destinations,document_id,fingerprint,room_uids=None):
    rooms=[r for r in analysis['rooms'] if not r.get('excluded_exterior') and (room_uids is None or r['uid'] in room_uids)]
    if not rooms:raise ValueError('No hay habitaciones interiores en el ámbito del trazador.')
    by_uid={r['uid']:r for r in rooms};lookup={t['id']:t for t in catalog['terminals']};items=[];seen=set()
    for value in destinations:
        if not value.get('include',True):continue
        tid=value.get('terminal_id');entry=lookup.get(tid)
        if not entry:raise ValueError('El esquema requiere destinos Revit identificados; revise la selección del trazador.')
        if tid in seen:raise ValueError('Seleccione una sola toma por terminal.')
        seen.add(tid);ports=[p for p in entry['connectors'] if p['id']==value.get('port_id')]
        if len(ports)!=1:raise ValueError('Conector de terminal ausente o ambiguo: '+tid)
        network=value.get('network_type')
        if network not in ('SUPPLY','EXTRACT'):raise ValueError('Asigne la red de cada destino.')
        point=ports[0]['xyz_mm'];room=by_uid.get(entry.get('room_uid'))
        if room is None:
            candidates=[r for r in rooms if polygon_from_rings(r['rings']).covers(Point(*point[:2]))]
            ranked=[]
            for r in candidates:
                distance=min((abs(point[2]-surface.height(c,*point[:2])) for c in r.get('ceilings',[])),default=abs(point[2]-r['level_z']))
                ranked.append((distance,r['uid'],r))
            ranked.sort(key=lambda row:(row[0],row[1]))
            if not ranked or len(ranked)>1 and abs(ranked[0][0]-ranked[1][0])<1e-4:
                raise ValueError('No se puede asociar inequívocamente el terminal a una habitación/planta: '+tid)
            room=ranked[0][2]
        flow=entry.get('airflow_lps')
        if flow is None or not math.isfinite(flow) or flow<=0:raise ValueError('Caudal de destino no verificable: '+tid)
        items.append({'terminal_id':tid,'existing_uid':tid,'existing_id':entry['element_id'],'room_uid':room['uid'],
            'level_id':room['level_id'],'role':'impulsion' if network=='SUPPLY' else 'extraccion','flow':flow,
            'x':point[0],'y':point[1],'z':point[2],'lock_full':False,'lock_position':False,
            'display_id':('I' if network=='SUPPLY' else 'E')+str(len(items)+1).zfill(2),
            'connector':copy.deepcopy(ports[0])})
    return terminals.seal({'kind':'RoutingPlanBackground','schema_version':1,'version':1,
        'proposal_id':'routing-background-'+uuid.uuid4().hex,'document_id':document_id,'fingerprint':fingerprint,
        'analysis':copy.deepcopy(analysis),'room_uids':list(by_uid),'placement_room_uids':list(by_uid),
        'zones':[],'terminals':items,'history':[],'future':[]})
