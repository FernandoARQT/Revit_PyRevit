"""Virtual routing design and revisioned editing, without Revit or WPF."""
import copy
import hashlib
import json
import math
import uuid
from dataclasses import dataclass,field,asdict


@dataclass(frozen=True)
class DuctProfile:
    profile_id:str
    network_type:str
    nominal_diameter_mm:float
    internal_diameter_mm:float
    external_diameter_mm:float
    insulation_thickness_mm:float
    minimum_bend_radius_mm:float
    manufacturer_reference:str|None=None
    geometry_verified:bool=False

    def __post_init__(self):
        if self.network_type not in ('SUPPLY','EXTRACT'):raise ValueError('Red desconocida.')
        for name in ('nominal_diameter_mm','internal_diameter_mm','external_diameter_mm','minimum_bend_radius_mm'):
            if not math.isfinite(getattr(self,name)) or getattr(self,name)<=0:raise ValueError('Perfil no válido: '+name)
        if not 0<=self.insulation_thickness_mm<float('inf') or self.internal_diameter_mm>self.external_diameter_mm:raise ValueError('Diámetros o aislamiento incompatibles.')


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

    def to_dict(self):return asdict(self)

    @classmethod
    def from_dict(cls,data):
        p=cls(**copy.deepcopy(data))
        if p.schema_version!=3:raise ValueError('Esquema de diseño incompatible.')
        if p.revision<1 or not p.document_identity:raise ValueError('Identidad/revisión no válida.')
        json.dumps(p.to_dict(),allow_nan=False)
        for c in p.corridors.values():
            Corridor(**c);_points(c['reference_xy_mm'])
            if c['network_type'] not in ('SUPPLY','EXTRACT','SHARED'):raise ValueError('Red desconocida.')
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
    p.validation={};p.solver_state={};p.design_stage='SCHEMATIC'
    return sorted(affected)


def _snapshot(p):return {k:v for k,v in p.to_dict().items() if k not in ('revision','history','future')}


def edit(original,operation,payload,expected_revision):
    if original.revision!=expected_revision:raise ValueError('La propuesta ha cambiado; recargue su revisión.')
    p=copy.deepcopy(original);before=_snapshot(p)
    if operation in ('undo','redo'):
        source=p.history if operation=='undo' else p.future
        target=p.future if operation=='undo' else p.history
        if not source:raise ValueError('No hay operaciones para '+operation)
        state=source.pop();target.append(before)
        for k,v in state.items():setattr(p,k,v)
        p.validation={};p.solver_state={}
    else:
        cid=payload.get('corridor_id');c=p.corridors.get(cid)
        if operation=='add_corridor':
            cid=cid or uuid.uuid4().hex
            if cid in p.corridors:raise ValueError('Corredor duplicado.')
            c=Corridor(cid,_points(payload['points_mm']),payload.get('network_type','SUPPLY'),payload['level_id'])
            p.corridors[cid]=asdict(c)
        elif operation in ('move_corridor','move_vertex','add_vertex','delete_vertex','delete_corridor','set_constraint'):
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
            else:
                value=payload['constraint_type']
                if value not in ('PREFERRED_WAYPOINT','REQUIRED_WAYPOINT','LOCKED_WAYPOINT'):raise ValueError('Restricción desconocida.')
                c['constraint_type']=value
        else:raise ValueError('Operación de esquema desconocida: '+operation)
        affected=invalidate(p,cid)
        p.manual_edits.append({'operation':operation,'payload':copy.deepcopy(payload),'affected':affected})
        p.history.append(before);p.future=[]
    p.revision+=1
    return RoutingDesignProposal.from_dict(p.to_dict())
