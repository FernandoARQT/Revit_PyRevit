"""Validated corporate defaults plus explicit per-project overrides."""
import copy
import csv
import json
import math
import os
from dataclasses import dataclass
from ..airflow import normalize
from ..family_rules import FLEX_TYPE

DEFAULT={'version':1,'system_id':'ventilation_radial','routing_strategy':'RADIAL',
 'section':{'shape':'CIRCULAR','nominal_diameter_mm':125,'outer_diameter_mm':125,'insulation_mm':0},
 'clearance_mm':15,'bend_radius_mm':125,'bend_geometry_provisional':True,'connection_straight_mm':150,
 'search':{'coarse_resolution_mm':50,'fine_resolution_mm':25,'local_refinement_mm':10,
           'rescue_resolution_mm':5,'rescue_extent_mm':500,'rescue_seconds':2,
           'max_nodes':100000,'max_seconds':20,'max_total_seconds':120,'max_iterations':3,'max_alternatives':3},
 'weights':{'length':1.0,'bend':0.4,'proximity':0.15,'nonpreferred':0.1,'crossing':3.0,'conditional':20.0},
 'use_distributors':True,'include_links':False,'phase_id':None,'element_rules':{},'route_overrides':{},
 'constraints':{'allowed_regions':[],'forbidden_regions':[],'preferred_regions':[],
                'passages':[],'rejected_passages':[],'approved_passages':[],'passage_approvals':{},'evaluable_elements':[],'waypoints':{},'locked_routes':[]},
 'structural_crossings':'PROPOSAL_ONLY','native_validation':True}

ROUTING_MODES={'benchmark':120,'standard':240,'deep':600,'exhaustive':1000}


def merge(base,overrides):
    result=copy.deepcopy(base)
    for k,v in (overrides or {}).items():
        result[k]=merge(result[k],v) if isinstance(v,dict) and isinstance(result.get(k),dict) else copy.deepcopy(v)
    return result


def validate(settings):
    s=merge(DEFAULT,settings)
    s.setdefault('routing_mode','standard')
    if s['routing_mode'] not in ROUTING_MODES:raise ValueError('Modo de cálculo desconocido.')
    if 'routing_mode' in settings and 'max_total_seconds' not in settings.get('search',{}):
        s['search']['max_total_seconds']=ROUTING_MODES[s['routing_mode']]
    s['parallel']=merge({'enabled':True},s.get('parallel',{}))
    if type(s['parallel']['enabled']) is not bool:raise ValueError('parallel.enabled debe ser booleano.')
    s['coordination']=merge({'enabled':True,'initial_budget_fraction':.35,'max_repair_trials':8,'max_evictions':3,
        'max_candidates':24,'max_port_candidates':3,'max_assignment_nodes':50000,
        'roadmap_neighbors':20,'max_roadmap_portals':80,'roadmap_enabled':True},s.get('coordination',{}))
    coordination=s['coordination']
    for key in ('topology_enabled','incremental_enabled','rrt_connect_enabled'):
        coordination.setdefault(key,True)
        if type(coordination[key]) is not bool:raise ValueError(key+' debe ser booleano.')
    coordination.setdefault('topology_paths',8)
    coordination.setdefault('max_incremental_nodes',4096)
    if type(coordination['max_incremental_nodes']) is not int or not 64<=coordination['max_incremental_nodes']<=20000:
        raise ValueError('max_incremental_nodes debe estar entre 64 y 20000.')
    if type(coordination['topology_paths']) is not int or not 1<=coordination['topology_paths']<=64:raise ValueError('Número de corredores entre 1 y 64.')
    coordination.setdefault('iris_gcs_enabled',True)
    coordination.setdefault('max_iris_cells',24)
    coordination.setdefault('iris_min_seconds',3.)
    if type(coordination['iris_min_seconds']) not in (int,float) or not math.isfinite(coordination['iris_min_seconds']) or not .1<=coordination['iris_min_seconds']<=60:
        raise ValueError('iris_min_seconds debe estar entre 0.1 y 60 segundos.')
    if type(coordination['iris_gcs_enabled']) is not bool:raise ValueError('iris_gcs_enabled debe ser booleano.')
    if type(coordination['max_iris_cells']) is not int or not 2<=coordination['max_iris_cells']<=64:raise ValueError('max_iris_cells debe estar entre 2 y 64.')
    if type(coordination['enabled']) is not bool:raise ValueError('coordination.enabled debe ser booleano.')
    if type(coordination['roadmap_enabled']) is not bool:raise ValueError('coordination.roadmap_enabled debe ser booleano.')
    if not .25<=coordination['initial_budget_fraction']<=.9:raise ValueError('Fracción de tiempo de planificación entre 0.25 y 0.9.')
    for key,maximum in (('max_repair_trials',20),('max_evictions',5),('max_candidates',128),('max_port_candidates',8),
                        ('max_assignment_nodes',500000),('roadmap_neighbors',60),('max_roadmap_portals',200)):
        if type(coordination[key]) is not int or not 1<=coordination[key]<=maximum:raise ValueError('Límite de coordinación inválido: '+key)
    if 'flexible_duct' in s:
        s['flexible_duct']['type_name']=FLEX_TYPE
    s.setdefault('sizing',{'max_velocity_mps':3.0})
    velocity=s['sizing'].get('max_velocity_mps',3.0)
    if not math.isfinite(float(velocity)) or velocity<=0:raise ValueError('Velocidad máxima positiva y finita requerida.')
    if s['version']!=1 or s['system_id']!='ventilation_radial' or s['routing_strategy'] not in ('RADIAL','LEGACY','GLOBAL_RADIAL_V3'):
        raise ValueError('Solo está implementado ventilation_radial / RADIAL.')
    section=s['section']
    if type(section.get('outer_includes_insulation',False)) is not bool:raise ValueError('outer_includes_insulation debe ser booleano.')
    if section['shape'] not in ('CIRCULAR','RECTANGULAR'):raise ValueError('Sección no admitida.')
    keys=['nominal_diameter_mm','outer_diameter_mm'] if section['shape']=='CIRCULAR' else ['width_mm','height_mm']
    for k in keys:
        if not math.isfinite(float(section[k])) or section[k]<=0:raise ValueError('Dimensión exterior inválida: '+k)
    for k,v in [('insulation_mm',section['insulation_mm'])]+[(k,s[k]) for k in ('clearance_mm','bend_radius_mm','connection_straight_mm')]:
        if not math.isfinite(float(v)) or v<0:raise ValueError('Valor no negativo y finito requerido: '+k)
    if s['bend_radius_mm'] and s['bend_radius_mm']<effective_radius(s):
        raise ValueError('El radio de giro debe superar la envolvente exterior con holgura.')
    search=s['search']
    for k in ('rescue_resolution_mm','rescue_extent_mm','rescue_seconds'):
        if not math.isfinite(float(search[k])) or search[k]<=0:raise ValueError('Refinamiento local inválido: '+k)
    if search['rescue_resolution_mm']>search['local_refinement_mm']:raise ValueError('El rescate local debe ser más fino que el refinamiento.')
    if not 1<=search['local_refinement_mm']<=search['fine_resolution_mm']<=search['coarse_resolution_mm']<=1000:
        raise ValueError('Resoluciones ordenadas: refinamiento ≤ detalle ≤ preliminar.')
    for k in ('max_nodes','max_seconds','max_total_seconds','max_iterations','max_alternatives'):
        if not math.isfinite(float(search[k])) or search[k]<=0:raise ValueError('Límite de búsqueda inválido: '+k)
    if search['max_alternatives']>3 or search['max_iterations']>10 or search['max_nodes']>2000000:
        raise ValueError('Límites de cálculo excesivos.')
    for k,v in s['weights'].items():
        if not math.isfinite(float(v)) or v<0:raise ValueError('Costes no negativos: '+k)
    if s['weights']['length']<=0:raise ValueError('El peso de longitud debe ser positivo.')
    for p in s['constraints']['passages']:
        if len(p.get('points_mm',[]))!=2 or p.get('radius_mm',0)<=0 or not p.get('element_ids'):
            raise ValueError('Un paso requiere dos puntos, radio e IDs de elementos atravesados.')
        if p.get('classification') not in ('PASSABLE','CONDITIONAL'):raise ValueError('Clasificación de paso inválida.')
        if any(len(q)!=3 or not all(math.isfinite(float(v)) for v in q) for q in p['points_mm']):
            raise ValueError('Coordenadas finitas requeridas para el paso.')
    for pid in s['constraints']['approved_passages']:
        record=s['constraints']['passage_approvals'].get(pid,{})
        if not record.get('responsible') or not record.get('reference'):
            raise ValueError('La autorización de un paso requiere responsable y referencia técnica: '+pid)
    for group in ('allowed_regions','forbidden_regions','preferred_regions'):
        for region in s['constraints'][group]:
            if 'box_mm' in region:
                b=region['box_mm']
                if len(b)!=6 or not all(math.isfinite(float(v)) for v in b) or any(b[i]>=b[i+3] for i in range(3)):
                    raise ValueError('Región vacía, invertida o no finita: '+group)
    for points in s['constraints']['waypoints'].values():
        if any(len(p)!=3 or not all(math.isfinite(float(v)) for v in p) for p in points):raise ValueError('Waypoints XYZ finitos requeridos.')
    for rule in s.get('model_identification',{}).get('construction',[]):
        if not normalize(rule.get('contains','')) or rule.get('meaning') not in ('FALSE_CEILING','STRUCTURE','PASSABLE'):
            raise ValueError('Identificación constructiva inválida.')
    if 'flexible_duct' in s:
        flex=s['flexible_duct'];sizes=flex['available_diameters_mm']
        if not flex['type_name'].strip() or flex['category']!='OST_FlexDuctCurves':raise ValueError('Tipo flexible inválido.')
        if not sizes or len(set(sizes))!=len(sizes) or any(not math.isfinite(float(d)) or d<=0 for d in sizes):
            raise ValueError('Diámetros flexibles positivos, finitos y únicos requeridos.')
    if 'distributors' in s:
        capacities=s['distributors']['branch_capacities']
        if not s['distributors']['name_contains'].strip() or not capacities or any(type(n)!=int or n<=0 for n in capacities):
            raise ValueError('Capacidades de distribuidor inválidas.')
    if 'main_connection' in s:
        main=s['main_connection']
        if main['kind'] not in ('RIGID_CIRCULAR','FLEXIBLE_CIRCULAR') or not math.isfinite(float(main['diameter_mm'])) or main['diameter_mm']<=0:
            raise ValueError('Conexión principal circular inválida.')
        if s['routing_strategy']!='GLOBAL_RADIAL_V3':main['kind']='FLEXIBLE_CIRCULAR'
    return s


def identify_construction(type_name,configuration):
    text=normalize(type_name)
    return next((rule['meaning'] for rule in configuration.get('model_identification',{}).get('construction',[])
                 if normalize(rule['contains']) in text),None)


class ClearanceRadius(float):
    """Obstacle envelope retaining the allowance for pairwise tube checks.

    Numeric consumers keep the full envelope. Reservations store only the
    physical radius so the next tube supplies the clearance exactly once.
    """
    def __new__(cls,value,clearance_mm=0):
        obj=super().__new__(cls,value)
        obj.clearance_mm=float(clearance_mm)
        return obj


def effective_radius(s):
    section=s['section']
    r=section['outer_diameter_mm']/2 if section['shape']=='CIRCULAR' else math.hypot(section['width_mm'],section['height_mm'])/2
    return ClearanceRadius(r+(0 if section.get('outer_includes_insulation',False) else section['insulation_mm'])+s['clearance_mm'],s['clearance_mm'])


def for_route(configuration,route_id):
    override=configuration.get('route_overrides',{}).get(route_id,{})
    if any(k not in ('section','clearance_mm','bend_radius_mm','weights') for k in override):
        raise ValueError('Sobrescritura de ruta no admitida.')
    # The shared configuration is already validated. Copy only route-local
    # fields; cloning hundreds of geometric passages for every profile lookup
    # dominates traced MCP runs. Shared search/constraints are read-only here.
    result=dict(configuration)
    for key in ('section','clearance_mm','bend_radius_mm','weights'):
        value=configuration[key]
        result[key]=merge(value,override.get(key,{})) if isinstance(value,dict) else override.get(key,value)
    checked=validate({key:result[key] for key in ('section','clearance_mm','bend_radius_mm','weights')})
    for key in ('section','clearance_mm','bend_radius_mm','weights'):result[key]=checked[key]
    result['route_overrides']={}
    return result


def profile(configuration):
    return MEPSystemProfile(configuration['section'],configuration['clearance_mm'],configuration['bend_radius_mm'],configuration['weights'])


@dataclass(frozen=True)
class MEPSystemProfile:
    """Geometry contract, independent of network topology and discipline rules."""
    section:dict
    clearance_mm:float
    bend_radius_mm:float
    weights:dict
    allowed_spaces:tuple=('FALSE_CEILING','SERVICE_SHAFT','EXPLICIT')
    max_slope:float|None=None
    min_curve_length_mm:float=1.0

    @property
    def radius_mm(self):return effective_radius({'section':self.section,'clearance_mm':self.clearance_mm})


def load():
    from pyrevit import script
    raw=script.get_config('ARQT_AutomaticMEPRouting_v1').get_option('settings_json',None)
    if not raw:return copy.deepcopy(DEFAULT)
    personal=_personal(json.loads(raw))
    search=personal.get('search',{})
    # Upgrade the former default without replacing custom finer resolutions.
    if 'rescue_resolution_mm' not in search and search.get('coarse_resolution_mm')==100:
        search['coarse_resolution_mm']=max(50,search.get('fine_resolution_mm',25))
    return validate(personal)


def _personal(configuration):
    project_keys={'constraints','element_rules','route_overrides','hrv_id','phase_id',
                    'model_identification','flexible_duct','distributors','main_connection','equipment_assignments'}
    return {k:v for k,v in configuration.items() if k not in project_keys}


def save(settings):
    from pyrevit import script
    s=validate(settings)
    script.get_config('ARQT_AutomaticMEPRouting_v1').set_option('settings_json',json.dumps(_personal(s),ensure_ascii=False))
    script.save_config()


with open(os.path.join(os.path.dirname(__file__),'config','ventilation_radial.json'),encoding='utf8') as _corporate:
    DEFAULT=validate(json.load(_corporate))

def read_configuration(path):
    """Provider boundary: the design engine receives dictionaries, not CSV cells."""
    with open(path,encoding='utf-8-sig',newline='') as stream:
        rows=csv.DictReader(stream,delimiter=';');result={}
        if rows.fieldnames!=['key','value_json']:raise ValueError('Cabecera de configuración no válida.')
        for row in rows:
            if not row['key'] or row['key'] in result:raise ValueError('Clave vacía o repetida en configuración.')
            result[row['key']]=json.loads(row['value_json'])
    return result

DEFAULT=validate(merge(DEFAULT,read_configuration(os.path.join(os.path.dirname(__file__),'config','ventilation_radial.csv'))))
DEFAULT=validate(merge(DEFAULT,{'routing_mode':'standard','search':{'max_total_seconds':240}}))
