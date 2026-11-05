"""Context-bound, deterministic diversity retention; no length-only dominance."""
import copy
import hashlib
import json
import contextvars
from contextlib import contextmanager

_AUDIT=contextvars.ContextVar('routing_candidate_audit',default=None)

@contextmanager
def audit_session():
    events=[];token=_AUDIT.set(events)
    try:yield events
    finally:_AUDIT.reset(token)

def audit(event,rid,route=None,**detail):
    events=_AUDIT.get()
    if events is not None:
        events.append(dict(event=event,route_id=rid,candidate_id=signature([rid,identity(route)]) if route else None,**detail))


def signature(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def context_signature(space,requests,configuration):
    return signature({'geometry':getattr(space,'geometry_signature',space.data),
        'ports':[dict({k:r.get(k) for k in ('route_id','network_type','sizing')},
            destination_options=sorted({p['id']:p for p in [r['destination']]+r.get('destination_alternatives',[])}.values(),key=lambda p:p['id']),
            sources=sorted({p['id']:p for p in [r['source']]+r.get('source_alternatives',[])}.values(),key=lambda p:p['id']))
            for r in sorted(requests,key=lambda r:r['route_id'])],
        'configuration':{k:configuration.get(k) for k in ('section','clearance_mm','bend_radius_mm','connection_straight_mm',
            'route_overrides','constraints','structural_crossings','element_rules','weights')}})


def identity(route):
    source=route.get('chosen_source') or route.get('source') or {}
    points=tuple(tuple(float(v) for v in p) for p in route.get('points_mm',[]))
    portals=route.get('passages',[])
    return {'source_port_id':source.get('id'),'destination_port_id':(route.get('chosen_destination') or route.get('destination') or {}).get('id'),
        'adapter_pose_signature':signature(route.get('adapter_pose')),
        'network_type':route.get('network_type'),'connection_signature':signature([source.get('id'),route.get('connection_anchors_mm')]),
        'topology_signature':signature([route.get('topology_nodes',[]),[tuple(sorted(p['element_ids'])) for p in portals]]),
        'portal_signature':signature([p['id'] for p in portals]),'geometry_signature':signature(points)}


class CandidateRepository:
    def __init__(self,context,limit=24,per_family=3):
        self.context=context;self.limit=limit;self.per_family=per_family;self.records={};self.protected=set();self.clock=0
        self.stats={'published':0,'duplicates':0,'retained':0}

    def publish(self,rid,route,protect=False):
        if len(route.get('points_mm') or [])<2:
            audit('REPOSITORY_REJECTED',rid,reason='NO_GEOMETRY');return
        if route.get('validation_context') not in (None,self.context):
            audit('REPOSITORY_REJECTED',rid,route,reason='CONTEXT_MISMATCH');return
        item=copy.deepcopy(route);meta=identity(item);key=signature([rid,meta])
        self.stats['published']+=1
        if protect:self.protected.add(key)
        if key in self.records:
            self.stats['duplicates']+=1;audit('REPOSITORY_REJECTED',rid,item,reason='EXACT_DUPLICATE');return
        self.clock+=1;item.update(candidate_id=key,validation_context=self.context,discovery_clock=self.clock,
            candidate_identity=meta,candidate_origin=item.get('search_method','RESTORED'))
        self.records[key]=(rid,item)
        audit('REPOSITORY_ADMITTED',rid,item)

    def bank(self):
        result={}
        for rid in sorted({v[0] for v in self.records.values()}):
            rows=[(key,item) for key,(r,item) in self.records.items() if r==rid]
            rows.sort(key=lambda pair:(pair[0] not in self.protected,pair[1].get('status')=='CONDITIONAL',
                pair[1].get('cost',{}).get('total',0),pair[0]))
            selected=[];coverage=set();families={}
            for key,item in rows:
                meta=item['candidate_identity'];family=(meta['source_port_id'],meta['topology_signature'],meta['portal_signature'],item.get('approval_status','DRAFT'))
                if key in self.protected or family not in coverage:
                    selected.append((key,item));coverage.add(family);families[family]=families.get(family,0)+1
            chosen={key for key,_ in selected}
            for key,item in rows:
                meta=item['candidate_identity'];family=(meta['source_port_id'],meta['topology_signature'],meta['portal_signature'],item.get('approval_status','DRAFT'))
                if key not in chosen and len(selected)<self.limit and families.get(family,0)<self.per_family:
                    selected.append((key,item));families[family]=families.get(family,0)+1
            # The family quota defines priority, not spatial dominance. A
            # longer member of the same family may unblock another route.
            chosen={key for key,_ in selected}
            selected.extend((key,item) for key,item in rows if key not in chosen)
            result[rid]=[copy.deepcopy(item) for _,item in selected]
        self.stats['retained']=sum(map(len,result.values()));return result

    def export_state(self):
        return {'context':self.context,'clock':self.clock,'protected':sorted(self.protected),'bank':self.bank(),'stats':self.stats}

    def restore_state(self,state):
        if state.get('context')!=self.context:return False
        self.protected.update(state.get('protected',[]))
        for rid,items in state.get('bank',{}).items():
            for item in items:self.publish(rid,item)
        self.clock=max(self.clock,state.get('clock',0));return True
