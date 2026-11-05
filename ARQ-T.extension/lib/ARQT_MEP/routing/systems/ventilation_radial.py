"""Independent supply/extract demands; Flow is design storage, not connector flow."""
import hashlib
import math
from ..core import geometry as g
from ..settings import for_route


class VentilationRadialSystem:
    system_id='ventilation_radial'

    def __init__(self,catalog,configuration):
        self.catalog=catalog;self.configuration=configuration
        self.ports={p['id']:p for e in catalog.get('devices',[])+catalog.get('terminals',[]) for p in e['connectors']}
        self.terminals={e['id']:e for e in catalog.get('terminals',[])}

    def resolve(self,value):
        port_id=value.get('port_id')
        if port_id:
            if port_id not in self.ports:raise ValueError('Conector real ausente: '+port_id)
            port=dict(self.ports[port_id])
            if port['connected']:raise ValueError('Conector ya ocupado; no se modifica su red: '+port_id)
        else:
            xyz=value.get('xyz_mm')
            if not isinstance(xyz,(list,tuple)) or len(xyz)!=3 or not all(math.isfinite(float(v)) for v in xyz):
                raise ValueError('La referencia manual necesita tres coordenadas finitas en mm.')
            port={'id':value.get('id') or 'manual:'+hashlib.sha256(str(xyz).encode()).hexdigest()[:12],
                  'xyz_mm':[float(v) for v in xyz],'provenance':'MANUAL_REFERENCE'}
            if value.get('direction'):port['direction']=list(g.unit(value['direction']))
        role=value.get('network_type')
        if role not in ('SUPPLY','EXTRACT'):raise ValueError('Asigne explícitamente SUPPLY o EXTRACT al punto de conexión.')
        port['network_type']=role;port['kind']=value.get('kind','MANUAL_REFERENCE' if not port_id else '')
        port['network_assigned_by_user']=True
        return port

    def requests(self,origins,destinations):
        terminal_ids=[v['terminal_id'] for v in destinations if v.get('include',True) and v.get('terminal_id')]
        if len(terminal_ids)!=len(set(terminal_ids)):raise ValueError('Seleccione un único conector por terminal; no duplique su Flow.')
        ports=[self.resolve(o) for o in origins if o.get('include',True)]
        if len({p['id'] for p in ports})!=len(ports):raise ValueError('Cada salida radial debe ser un conector/punto distinto.')
        for p in ports:
            if self.configuration['use_distributors'] and p['kind']!='DISTRIBUTOR':
                raise ValueError('La estrategia seleccionada exige distribuidores; no se conectan ramales directamente al recuperador.')
            if self.configuration.get('hrv_id') and self.configuration['hrv_id']==p.get('element_key') and self.configuration['use_distributors']:
                raise ValueError('El elemento seleccionado como recuperador no es un distribuidor radial.')
        requests=[];used=set();warnings=[]
        for value in sorted(destinations,key=lambda d:str(d.get('terminal_id',d.get('id',d.get('xyz_mm'))))):
            if not value.get('include',True):continue
            terminal=self.terminals.get(value.get('terminal_id'))
            if terminal:
                if value.get('port_id') not in {p['id'] for p in terminal['connectors']}:
                    raise ValueError('Seleccione uno de los conectores reales del terminal: '+terminal['id'])
                hint=terminal.get('suggested_network_type')
                if not terminal.get('network_assignment_requires_review',True) and value['network_type']!=hint:
                    raise ValueError('No se permite mezclar la función de un terminal ARQ-T con la otra red.')
                flow=terminal['airflow_lps']
            else:flow=value.get('airflow_lps')
            if flow is None or not math.isfinite(float(flow)) or flow<=0:
                raise ValueError('Cada destino necesita un Flow de diseño positivo; no se inventan caudales.')
            destination=self.resolve(value)
            eligible=[p for p in ports if p['network_type']==destination['network_type'] and p['id'] not in used]
            rid='route:'+destination['id']
            if not eligible:
                requests.append({'route_id':rid,'network_type':destination['network_type'],'destination':destination,
                    'airflow_lps':flow,'error':'MISSING_DISTRIBUTOR_PORT','source':None})
                continue
            source=min(eligible,key=lambda p:(g.distance(p['xyz_mm'],destination['xyz_mm']),p['id']))
            used.add(source['id'])
            source_size=source.get('size',{});dest_size=destination.get('size',{})
            nominal=for_route(self.configuration,rid)['section'].get('nominal_diameter_mm')
            mismatch=any(s.get('shape')=='Round' and abs(s['diameter_mm']-nominal)>1 for s in (source_size,dest_size)) if nominal else False
            request={'route_id':rid,'network_type':destination['network_type'],'source':source,'destination':destination,
                     'airflow_lps':float(flow),'room_uid':terminal.get('room_uid') if terminal else None,
                     'waypoints_mm':self.configuration['constraints']['waypoints'].get(rid,[])}
            if mismatch:request['error']='CONNECTION_SIZE_REQUIRES_UNMODELLED_TRANSITION'
            requests.append(request)
        if not requests:raise ValueError('No hay destinos incluidos. Coloque rejillas o defina referencias manuales de diseño.')
        warnings.append('Flow contiene caudal de diseño, sin dimensionado hidráulico ni propagación al conector. Crear la propuesta no modela conductos: utilice Modelar y conectar tras aprobar las rutas.')
        if any(p['provenance']=='MANUAL_REFERENCE' for p in ports):warnings.append('Orígenes manuales: referencias de diseño, no conectores Revit reales.')
        return requests,warnings
