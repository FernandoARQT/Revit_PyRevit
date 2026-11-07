"""Virtual routing design and revisioned editing, without Revit or WPF."""
import copy
import hashlib
import json
import math
import uuid
import datetime
from dataclasses import dataclass,field,asdict

CONSTRAINTS=('PREFERRED_WAYPOINT','REQUIRED_WAYPOINT','LOCKED_WAYPOINT')
NETWORKS=('SUPPLY','EXTRACT','SHARED')
HISTORY_LIMIT=30


def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()


@dataclass(frozen=True)
class DuctProfile:
    profile_id:str
    network_type:str
    nominal_diameter_mm:float
    internal_diameter_mm:float|None
    external_diameter_mm:float
    insulation_thickness_mm:float
    minimum_bend_radius_mm:float
    manufacturer_reference:str|None=None
    geometry_verified:bool=False

    def __post_init__(self):
        if self.network_type not in ('SUPPLY','EXTRACT'):raise ValueError('Red desconocida.')
        for name in ('nominal_diameter_mm','external_diameter_mm','minimum_bend_radius_mm'):
            if not math.isfinite(getattr(self,name)) or getattr(self,name)<=0:raise ValueError('Perfil no válido: '+name)
        if not 0<=self.insulation_thickness_mm<float('inf'):raise ValueError('Aislamiento incompatible.')
        if self.internal_diameter_mm is not None and (not math.isfinite(self.internal_diameter_mm) or not 0<self.internal_diameter_mm<=self.external_diameter_mm):raise ValueError('Diámetro interior incompatible.')
        if self.geometry_verified and (self.internal_diameter_mm is None or not self.manufacturer_reference):raise ValueError('Un perfil verificado requiere datos de producto completos.')


@dataclass
class Corridor:
    corridor_id:str
    reference_xy_mm:list
    network_type:str
    level_id:int
    region_sequence:list=field(default_factory=list)
    portal_sequence:list=field(default_factory=list)
    width_profile:list=field(default_factory=list)
    height_profile:list=field(default_factory=list)
    reachable_terminal_ids:list=field(default_factory=list)
    construction_cost:float=0
    validation_status:str='NOT_EVALUATED'
    constraint_type:str='PREFERRED_WAYPOINT'
    reference_xyz_mm:list=field(default_factory=list)
    node_ids:list=field(default_factory=list)
    length_mm:float=0
    kind:str='MANUAL'
    generated_by:str|None=None
    source_edge_ids:list=field(default_factory=list)
    source_manifold_id:str|None=None
    route_ids:list=field(default_factory=list)
    entity_type:str='MANUAL_GUIDE'
    preliminary_capacity:dict=field(default_factory=dict)
    corridor_revision:int=1

    def __post_init__(self):
        _points(self.reference_xy_mm)
        if not self.corridor_id or self.network_type not in NETWORKS:raise ValueError('Identidad/red de corredor no válida.')
        if type(self.level_id)!=int:raise ValueError('Nivel no válido.')
        if self.constraint_type not in CONSTRAINTS:raise ValueError('Restricción desconocida.')
        if not math.isfinite(self.construction_cost) or self.construction_cost<0:raise ValueError('Coste no válido.')


@dataclass
class DuctBundle:
    bundle_id:str
    corridor_ids:list
    route_ids:list
    lane_order:list=field(default_factory=list)
    lane_offsets:dict=field(default_factory=dict)
    branch_event_ids:list=field(default_factory=list)


@dataclass
class BranchEvent:
    branch_id:str
    parent_bundle_id:str
    position_xy_mm:list
    continuing_route_ids:list
    departing_route_ids:list
    departure_side:str
    constraint_type:str='PREFERRED_WAYPOINT'


@dataclass
class RoutingDesignProposal:
    document_identity:str
    geometry_revision:str
    background:dict
    proposal_id:str=field(default_factory=lambda:uuid.uuid4().hex)
    schema_version:int=3
    design_stage:str='SCHEMATIC'
    revision:int=1
    equipment:dict=field(default_factory=dict)
    terminals:dict=field(default_factory=dict)
    corridors:dict=field(default_factory=dict)
    corridor_assignments:dict=field(default_factory=dict)
    bundles:dict=field(default_factory=dict)
    branches:dict=field(default_factory=dict)
    individual_routes:dict=field(default_factory=dict)
    constraints:dict=field(default_factory=dict)
    manual_edits:list=field(default_factory=list)
    validation:dict=field(default_factory=dict)
    solver_state:dict=field(default_factory=dict)
    dependencies:dict=field(default_factory=dict)
    history:list=field(default_factory=list)
    future:list=field(default_factory=list)
    kind:str='RoutingDesignProposal'
    name:str='Esquema de conductos'
    source_proposal_id:str|None=None
    source_version:int|None=None
    created_utc:str=field(default_factory=now)
    updated_utc:str=field(default_factory=now)
    snapshot:dict=field(default_factory=dict)
    configuration:dict=field(default_factory=dict)
    corridor_graph:dict=field(default_factory=dict)
    profiles:dict=field(default_factory=dict)
    catalog:dict=field(default_factory=dict)
    endpoints:dict=field(default_factory=dict)
    connectivity:dict=field(default_factory=dict)
    schematic_junctions:dict=field(default_factory=dict)
    alternatives:list=field(default_factory=list)
    selected_paths:dict=field(default_factory=dict)
    main_connections:dict=field(default_factory=dict)
    elbow_rotations:dict=field(default_factory=dict)

    def to_dict(self):return asdict(self)

    @classmethod
    def from_dict(cls,data):
        # JSON canonicalisation makes in-memory and recovered proposals equal.
        p=cls(**json.loads(json.dumps(data,allow_nan=False)))
        if p.kind!='RoutingDesignProposal' or p.schema_version!=3:raise ValueError('Esquema de diseño incompatible.')
        if type(p.revision)!=int or p.revision<1 or not p.document_identity or not p.geometry_revision:raise ValueError('Identidad/revisión no válida.')
        json.dumps(p.to_dict(),allow_nan=False)
        levels={r['level_id'] for r in p.background.get('analysis',{}).get('rooms',[])}
        if p.background.get('document_id')!=p.document_identity:raise ValueError('Fondo de otro documento.')
        for key,c in p.corridors.items():
            record=Corridor(**c)
            if key!=record.corridor_id or record.level_id not in levels:raise ValueError('Corredor fuera de la planta del proyecto.')
        for tid,ids in p.corridor_assignments.items():
            if tid not in p.terminals or not isinstance(ids,list) or not ids or len(set(ids))!=len(ids):raise ValueError('Asignación de terminal no válida.')
            for cid in ids:
                c=p.corridors.get(cid);t=p.terminals[tid]
                if c is None or c['network_type'] not in (t['network_type'],'SHARED') or c['level_id']!=t['level_id']:
                    raise ValueError('Corredor incompatible con terminal/red/planta.')
        if len(p.history)>HISTORY_LIMIT or len(p.future)>HISTORY_LIMIT:raise ValueError('Historial fuera de límite.')
        return p


def _xy(point):
    if len(point)!=2 or any(not math.isfinite(float(v)) for v in point):raise ValueError('Coordenada XY no válida.')
    return [float(v) for v in point]


def _points(points):
    values=[_xy(p) for p in points]
    if len(values)<2 or any(math.dist(a,b)<.001 for a,b in zip(values,values[1:])):raise ValueError('Corredor con tramo degenerado.')
    return values


def context(p,configuration):
    return {'proposal_id':p.proposal_id,'proposal_revision':p.revision,'geometry_revision':p.geometry_revision,
            'configuration_hash':hashlib.sha256(json.dumps(configuration,sort_keys=True,allow_nan=False).encode()).hexdigest()}


def invalidate(p,root):
    pending=[root];affected=set()
    while pending:
        key=pending.pop()
        if key in affected:continue
        affected.add(key);pending.extend(p.dependencies.get(key,[]))
    for key in affected:
        for records in (p.corridors,p.bundles,p.branches,p.individual_routes):
            if key in records:records[key]['validation_status']='NOT_EVALUATED'
    # Aggregate certificates are stale. Preserve independent candidates/results;
    # every solver result still needs the exact context lease before publication.
    p.validation={};p.solver_state={};p.design_stage='SCHEMATIC'
    return sorted(affected)


EDITABLE=('corridors','corridor_assignments','bundles','branches','individual_routes','constraints','manual_edits','dependencies','equipment','design_stage',
          'selected_paths','connectivity','schematic_junctions','main_connections','elbow_rotations')


def _snapshot(p):return {k:copy.deepcopy(getattr(p,k)) for k in EDITABLE}


def _delta(before,after):
    """Store only changed records, rather than 30 copies of the corridor graph."""
    result={'records':{},'values':{}}
    for key in EDITABLE:
        a,b=before[key],getattr(after,key)
        if isinstance(a,dict) and isinstance(b,dict):
            changes={k:copy.deepcopy(a.get(k)) for k in set(a)|set(b) if a.get(k)!=b.get(k)}
            if changes:result['records'][key]=changes
        elif a!=b:result['values'][key]=copy.deepcopy(a)
    return result


def _restore(p,state):
    if 'records' not in state:
        reverse=_snapshot(p)
        for key in EDITABLE:
            if key in state:setattr(p,key,copy.deepcopy(state[key]))
        return reverse
    reverse={'records':{},'values':{}}
    for key,changes in state['records'].items():
        target=getattr(p,key);reverse['records'][key]={k:copy.deepcopy(target.get(k)) for k in changes}
        for k,value in changes.items():
            if value is None:target.pop(k,None)
            else:target[k]=copy.deepcopy(value)
    for key,value in state['values'].items():
        reverse['values'][key]=copy.deepcopy(getattr(p,key));setattr(p,key,copy.deepcopy(value))
    return reverse


def create(background,name='Esquema de conductos'):
    from ... import proposal as terminal_model
    from .background import check
    check(background)
    cached=copy.deepcopy(background)
    cached['history']=[];cached['future']=[]
    cached=terminal_model.seal(cached)
    p=RoutingDesignProposal(background['document_id'],background['fingerprint'],cached,
        source_proposal_id=background['proposal_id'],source_version=background['version'],name=str(name).strip())
    rooms={r['uid']:r for r in background['analysis']['rooms']}
    for t in terminal_model.active(background):
        p.terminals[t['terminal_id']]={'terminal_id':t['terminal_id'],'network_type':{'impulsion':'SUPPLY','extraccion':'EXTRACT'}[t['role']],
            'level_id':rooms[t['room_uid']]['level_id'],'position_mm':[t['x'],t['y'],t['z']],
            'flow_lps':t['flow'],'existing_uid':t.get('existing_uid')}
    return RoutingDesignProposal.from_dict(p.to_dict())


def edit(original,operation,payload,expected_revision):
    if original.configuration.get('routing_strategy')=='MANUAL_BUNDLE':
        from .manual_bundle import edit as edit_manual
        return edit_manual(original,operation,payload,expected_revision)
    if original.configuration.get('routing_strategy')=='GLOBAL_RADIAL_V3' and operation!='add_corridor':
        from .editing import edit as edit_schematic
        return edit_schematic(original,operation,payload,expected_revision)
    if type(expected_revision)!=int or original.revision!=expected_revision:raise ValueError('VERSION_CONFLICT: la propuesta ha cambiado; recargue su revisión.')
    p=copy.deepcopy(original);before=_snapshot(p)
    if operation in ('undo','redo'):
        source=p.history if operation=='undo' else p.future
        target=p.future if operation=='undo' else p.history
        if not source:raise ValueError('No hay operaciones para '+operation)
        state=source.pop();target.append(_restore(p,state))
        # Undo/redo restores decisions, never a native approval or validation.
        for cid in set(original.corridors)|set(p.corridors):
            if original.corridors.get(cid)!=p.corridors.get(cid):invalidate(p,cid)
        p.validation={};p.solver_state={}
    else:
        cid=payload.get('corridor_id');c=p.corridors.get(cid)
        if operation=='add_corridor':
            cid=cid or uuid.uuid4().hex
            if cid in p.corridors:raise ValueError('Corredor duplicado.')
            c=Corridor(cid,_points(payload['points_mm']),payload.get('network_type','SUPPLY'),payload['level_id'])
            p.corridors[cid]=asdict(c)
        elif operation=='assign_terminal':
            tid=payload['terminal_id']
            if tid not in p.terminals:raise ValueError('Terminal ausente.')
            ids=payload.get('corridor_ids',[])
            if ids:p.corridor_assignments[tid]=list(ids)
            else:p.corridor_assignments.pop(tid,None)
            cid=tid
        elif operation in ('move_corridor','move_vertex','add_vertex','delete_vertex','delete_corridor','set_constraint','set_network'):
            if c is None:raise ValueError('Corredor ausente.')
            if c['constraint_type']=='LOCKED_WAYPOINT' and operation!='set_constraint':raise ValueError('Corredor bloqueado.')
            points=c['reference_xy_mm'];index=payload.get('index',0)
            if operation=='move_corridor':
                dx,dy=_xy(payload['delta_mm']);c['reference_xy_mm']=[[x+dx,y+dy] for x,y in points]
            elif operation in ('move_vertex','add_vertex','delete_vertex'):
                if type(index)!=int or not 0<=index<(len(points)+(operation=='add_vertex')):raise ValueError('Índice de vértice no válido.')
                if operation=='move_vertex':points[index]=_xy(payload['point_mm'])
                elif operation=='add_vertex':points.insert(index,_xy(payload['point_mm']))
                else:points.pop(index)
                _points(points)
            elif operation=='delete_corridor':
                del p.corridors[cid]
                p.corridor_assignments={t:v for t,v in p.corridor_assignments.items() if cid not in v}
            elif operation=='set_network':
                c['network_type']=payload['network_type']
            else:
                value=payload['constraint_type']
                if value not in CONSTRAINTS:raise ValueError('Restricción desconocida.')
                c['constraint_type']=value
        else:raise ValueError('Operación de esquema desconocida: '+operation)
        if c is not None and isinstance(c,dict) and operation in ('move_corridor','move_vertex','add_vertex','delete_vertex','set_network'):
            c['kind']='MANUAL';c['reference_xyz_mm']=[];c['width_profile']=[];c['height_profile']=[];c['node_ids']=[];c['length_mm']=0
            p.corridor_graph={}
        affected=invalidate(p,cid)
        p.manual_edits.append({'operation':operation,'payload':copy.deepcopy(payload),'affected':affected})
        p.history.append(_delta(before,p));p.history=p.history[-HISTORY_LIMIT:];p.future=[]
    p.revision+=1
    p.updated_utc=now()
    return RoutingDesignProposal.from_dict(p.to_dict())
