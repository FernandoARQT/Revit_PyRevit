"""Physical connection identities. Pure, serialisable and independent of Revit.

Connector ids are native ids, never collection positions. Their identity is
checked against local geometry; world coordinates are refreshed after a move.
"""
import copy
import hashlib
import json
import math
from dataclasses import dataclass,asdict
from ..core import geometry as g
from ..core.terminal_adapter import FAMILY
from ..systems.heat_recovery_topology import distributor_ports
from ...airflow import normalize


@dataclass(frozen=True)
class MEPConnectionEndpoint:
    endpoint_id:str
    owner_unique_id:str
    connector_signature:str
    related_terminal_uid:str|None
    network_type:str
    origin_xyz_mm:tuple
    connector_normal_xyz:tuple
    nominal_diameter_mm:float
    is_connected:bool
    is_available:bool
    identification_status:str
    native_connector_id:int

    def __post_init__(self):
        if self.network_type not in ('SUPPLY','EXTRACT'):raise ValueError('Red de extremo desconocida.')
        if not self.owner_unique_id or not self.connector_signature:raise ValueError('Identidad de extremo incompleta.')
        if len(self.origin_xyz_mm)!=3 or any(not math.isfinite(v) for v in self.origin_xyz_mm):raise ValueError('XYZ de extremo inválido.')
        if len(self.connector_normal_xyz)!=3 or abs(g.norm(self.connector_normal_xyz)-1)>1e-5:raise ValueError('Normal de extremo inválida.')
        if not math.isfinite(self.nominal_diameter_mm) or self.nominal_diameter_mm<=0:raise ValueError('Diámetro de extremo inválido.')
        if self.is_available and self.is_connected:raise ValueError('Un extremo ocupado no está disponible.')


def signature(port):
    """Local geometry survives translations/rotations of the owning instance."""
    record={'owner':port['element_key'],'native_id':int(port['connector_id']),
        'shape':port['size']['shape'],'size':{k:round(float(v),4) if isinstance(v,(int,float)) else v for k,v in port['size'].items()},
        'domain':port.get('domain','DomainHvac'),'type':port.get('connector_type','End')}
    for key in ('local_origin_mm','local_direction'):
        if key in port:record[key]=[round(float(v),5) for v in port[key]]
    return hashlib.sha256(json.dumps(record,sort_keys=True,allow_nan=False).encode()).hexdigest()


def endpoint(port,network,terminal_uid=None):
    if port.get('provenance')!='REVIT_CONNECTOR' or port.get('domain','DomainHvac')!='DomainHvac' or port.get('connector_type','End')!='End':
        raise ValueError('El extremo requiere un conector físico HVAC.')
    if port['size'].get('shape')!='Round':raise ValueError('El extremo radial debe ser circular.')
    direction=tuple(g.unit(port['direction']))
    if g.norm(direction)<.999:raise ValueError('Normal de conector no identificable.')
    sig=signature(port)
    return MEPConnectionEndpoint(port['element_key']+':endpoint:'+sig[:20],port['element_key'],sig,terminal_uid,
        network,tuple(port['xyz_mm']),direction,float(port['size']['diameter_mm']),bool(port['connected']),
        not bool(port['connected']),'NATIVE_CONNECTOR_IDENTIFIED',int(port['connector_id']))


def resolve(saved,ports):
    """Resolve exactly one current descriptor, returning its fresh XYZ/normal."""
    matches=[p for p in ports if p.get('element_key')==saved['owner_unique_id'] and signature(p)==saved['connector_signature']]
    if len(matches)!=1:raise ValueError('ELBOW_CONNECTOR_UNRESOLVED')
    return endpoint(matches[0],saved['network_type'],saved.get('related_terminal_uid'))


def _is_elbow(device):
    return device.get('family')==FAMILY or device.get('part_type')=='Elbow' or 'codo' in normalize(device.get('family',''))


def elbow_destinations(catalog,selections):
    terminals={t['id']:t for t in catalog.get('terminals',[])};devices={d['id']:d for d in catalog.get('devices',[])};result={}
    for selected in selections:
        tid=selected['terminal_id'];network=selected['network_type'];t=terminals.get(tid)
        report={'terminal_id':tid,'network_type':network,'status':'ELBOW_NOT_FOUND','endpoints':[],'ports':{}}
        result[tid]=report
        if not t:continue
        # Native physical references establish association, never proximity.
        linked={r['owner_unique_id'] for p in t.get('connectors',[]) for r in p.get('physical_refs',[])}
        attached=[devices[k] for k in linked if k in devices and _is_elbow(devices[k])]
        adapter=t.get('terminal_adapter')
        if adapter and adapter.get('terminal_port',{}).get('element_key')==tid and adapter['terminal_port'].get('connected'):
            if adapter['id'] not in {d['id'] for d in attached}:
                attached.append(dict(adapter,connectors=adapter['outlets'],_record=True))
        if len(attached)>1:report['status']='ELBOW_CONNECTOR_UNRESOLVED';continue
        if not attached:continue
        elbow=attached[0];report['elbow_uid']=elbow['id'];report['elbow_element_id']=elbow['element_id']
        joined={r.get('connector_id') for p in t.get('connectors',[]) for r in p.get('physical_refs',[]) if r['owner_unique_id']==elbow['id']}
        ports=[p for p in elbow.get('connectors',[]) if p.get('connector_id') not in joined]
        if not ports:report['status']='ELBOW_CONNECTOR_UNRESOLVED';continue
        compatible=[]
        for port in ports:
            # An occupied connection to this terminal is not an outlet.
            if any(r['owner_unique_id']==tid for r in port.get('physical_refs',[])):continue
            try:item=endpoint(port,network,tid)
            except (ValueError,KeyError,TypeError):continue
            compatible.append(item)
            if item.is_available:
                report['endpoints'].append(asdict(item));report['ports'][item.endpoint_id]=copy.deepcopy(port)
        chosen=selected.get('elbow_endpoint_id')
        if chosen:
            report['endpoints']=[p for p in report['endpoints'] if p['endpoint_id']==chosen]
        if report['endpoints']:report['status']='ELBOW_CONNECTORS_IDENTIFIED'
        elif chosen:report['status']='ELBOW_CONNECTOR_UNRESOLVED'
        elif compatible:report['status']='ELBOW_CONNECTOR_OCCUPIED'
        else:report['status']='ELBOW_CONNECTOR_INCOMPATIBLE'
    return result


def manifolds(catalog,configuration):
    lookup={str(d[k]):d for d in catalog.get('devices',[]) for k in ('id','element_id')};result={}
    for assignment in configuration.get('equipment_assignments',[]):
        if not assignment.get('include',True):continue
        network=assignment['network_type'];d=lookup.get(str(assignment.get('element_id',assignment.get('element_uid'))))
        if not d:raise ValueError('Distribuidor seleccionado ausente.')
        if network in result:raise ValueError('Seleccione un único distribuidor por red en esta propuesta.')
        from ...family_rules import distributor_network
        if distributor_network(d.get('family',''))!=network:raise ValueError('Distribuidor incompatible con la red.')
        main,branches=distributor_ports(d);available=[]
        for port in branches:
            item=endpoint(port,network)
            if item.is_available:available.append({'endpoint':asdict(item),'port':copy.deepcopy(port)})
        result[network]={'id':d['id'],'element_id':d['element_id'],'network_type':network,
            'main_port':copy.deepcopy(main),'available_ports':available,'available_count':len(available),
            'original_transform':copy.deepcopy(d.get('transform')),'proposed_transform':copy.deepcopy(d.get('transform'))}
        result[network]['body_box_mm']=copy.deepcopy(d.get('body_box_mm'))
    return result
