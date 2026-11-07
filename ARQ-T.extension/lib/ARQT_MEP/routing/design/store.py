"""Independent V3 sidecars using the editor's proven atomic IO and file locks."""
import copy
import os
import uuid
from ... import proposal_store as terminal_store
from .model import RoutingDesignProposal, edit, now, context, invalidate

ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..','..','data','routing-design'))


def path(document_id,proposal_id):
    # Reuse ID validation/project hashing without changing the terminals root.
    relative=os.path.relpath(terminal_store._path(document_id,proposal_id),terminal_store.ROOT)
    return os.path.join(ROOT,relative)


def get(document_id,proposal_id):
    import json
    with open(path(document_id,proposal_id),encoding='utf8') as f:p=RoutingDesignProposal.from_dict(json.load(f))
    if p.document_identity!=document_id or p.proposal_id!=proposal_id:raise ValueError('Propuesta de otro documento o identidad.')
    return p


def create(p):
    p=RoutingDesignProposal.from_dict(p.to_dict());target=path(p.document_identity,p.proposal_id)
    with terminal_store._lock(target):
        if os.path.exists(target):raise ValueError('La propuesta ya existe.')
        terminal_store._write(target,p.to_dict())
    return p


def update(document_id,proposal_id,expected_revision,operation,payload=None):
    target=path(document_id,proposal_id)
    with terminal_store._lock(target):
        p=get(document_id,proposal_id)
        result=edit(p,operation,payload or {},expected_revision)
        terminal_store._write(target,result.to_dict())
    return result


def list_proposals(document_id,source_proposal_id=None):
    folder=os.path.dirname(path(document_id,'index'))
    if not os.path.isdir(folder):return []
    result=[get(document_id,f[:-5]) for f in os.listdir(folder) if f.endswith('.json')]
    return sorted((p for p in result if source_proposal_id is None or p.source_proposal_id==source_proposal_id),key=lambda p:p.updated_utc,reverse=True)


def entries(document_id):
    """Lightweight list metadata; do not retain every full geometry snapshot."""
    import json
    folder=os.path.dirname(path(document_id,'index'))
    if not os.path.isdir(folder):return []
    result=[]
    for name in os.listdir(folder):
        if not name.endswith('.json'):continue
        with open(os.path.join(folder,name),encoding='utf8') as stream:data=json.load(stream)
        if data.get('kind')!='RoutingDesignProposal' or data.get('document_identity')!=document_id:continue
        result.append({'proposal_id':data['proposal_id'],'name':data.get('name','Estudio'),
            'revision':data['revision'],'updated_utc':data['updated_utc'],'corridors':len(data.get('corridors',{})),
            'strategy':data.get('configuration',{}).get('routing_strategy'),'terminals':len(data.get('terminals',{})),
            'selected_terminals':len(data.get('selected_paths',{}))})
    return sorted(result,key=lambda p:p['updated_utc'],reverse=True)


def duplicate(document_id,proposal_id,expected_revision,name=None):
    original=get(document_id,proposal_id)
    if type(expected_revision)!=int or original.revision!=expected_revision:raise ValueError('VERSION_CONFLICT')
    p=copy.deepcopy(original);p.proposal_id=uuid.uuid4().hex;p.revision=1
    p.name=name or original.name+' · copia';p.created_utc=p.updated_utc=now()
    p.history=[];p.future=[];p.validation={};p.solver_state={}
    return create(p)


def publish(document_id,proposal_id,lease,configuration,result):
    """Worker CAS: reject stale results, including configuration changes."""
    target=path(document_id,proposal_id)
    with terminal_store._lock(target):
        current=get(document_id,proposal_id)
        if context(current,configuration)!=lease:raise ValueError('STALE_RESULT: el cálculo pertenece a otra revisión.')
        if result.proposal_id!=proposal_id or result.document_identity!=document_id or result.geometry_revision!=current.geometry_revision:
            raise ValueError('STALE_RESULT: contexto incompatible.')
        p=RoutingDesignProposal.from_dict(result.to_dict());p.revision=current.revision+1;p.updated_utc=now()
        terminal_store._write(target,p.to_dict())
    return p


def capture(document_id,proposal_id,expected_revision,snapshot,catalog,configuration,profile,background=None):
    target=path(document_id,proposal_id)
    with terminal_store._lock(target):
        current=get(document_id,proposal_id)
        if type(expected_revision)!=int or current.revision!=expected_revision:raise ValueError('VERSION_CONFLICT')
        if snapshot['document_identity']!=document_id:raise ValueError('Snapshot de otro documento.')
        p=copy.deepcopy(current);p.snapshot=copy.deepcopy(snapshot);p.catalog=copy.deepcopy(catalog)
        if background is not None:
            from .model import create
            refreshed=create(background)
            p.background=copy.deepcopy(background)
            for tid,t in refreshed.terminals.items():
                if tid in p.terminals:p.terminals[tid].update(position_mm=t['position_mm'],level_id=t['level_id'])
        new_profiles={profile.profile_id:__import__('dataclasses').asdict(profile)}
        changed=(current.geometry_revision!=snapshot['geometry_revision'] or current.configuration!=configuration or current.profiles!=new_profiles)
        p.configuration=copy.deepcopy(configuration);p.profiles=new_profiles;p.geometry_revision=snapshot['geometry_revision']
        if changed:
            p.corridor_graph={}
            for cid in p.corridors:invalidate(p,cid)
            p.validation={};p.solver_state={}
            if configuration.get('routing_strategy')!='GLOBAL_RADIAL_V3':p.history=[];p.future=[]
            else:
                for tid in p.connectivity:p.connectivity[tid]['status']='EDITED_PENDING_RECHECK'
        p.revision+=1;p.updated_utc=now();p=RoutingDesignProposal.from_dict(p.to_dict())
        terminal_store._write(target,p.to_dict())
    return p
