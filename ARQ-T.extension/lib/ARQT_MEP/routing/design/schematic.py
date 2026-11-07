"""Only selected complete distributor-to-elbow chains become visible geometry."""
import hashlib
from dataclasses import asdict
from .model import Corridor


def build(selected,candidates,edge_records,terminals):
    used={};paths={};branches={};junctions={};assignments={}
    for tid,index in selected.items():
        path=candidates[tid][index];paths[tid]=path
        for eid in path['edge_ids']:used.setdefault((path['network_type'],eid),[]).append(tid)
    # Merge only consecutive edges with identical users and region/portal
    # semantics. Keep portals and decision vertices as explicit boundaries.
    predecessors={};successors={}
    for tid,path in paths.items():
        keys=[(path['network_type'],eid) for eid in path['edge_ids']]
        for i,key in enumerate(keys):
            if i:predecessors.setdefault(key,set()).add(keys[i-1])
            if i+1<len(keys):successors.setdefault(key,set()).add(keys[i+1])
    corridors={};mapping={};visited=set()
    for key in sorted(used):
        if key in visited:continue
        first=key
        while len(predecessors.get(first,()))==1:
            prior=next(iter(predecessors[first]))
            if prior in visited or prior==key or len(successors.get(prior,()))!=1 or sorted(used[prior])!=sorted(used[key]):break
            a,b=edge_records[first[1]],edge_records[prior[1]]
            # Portals/region changes remain protected vertices inside the
            # polyline, rather than becoming separate visible fragments.
            first=prior
        chain=[first];visited.add(first)
        while len(successors.get(chain[-1],()))==1:
            nxt=next(iter(successors[chain[-1]]));a,b=edge_records[chain[-1][1]],edge_records[nxt[1]]
            if nxt in visited or len(predecessors.get(nxt,()))!=1 or sorted(used[nxt])!=sorted(used[key]):break
            chain.append(nxt);visited.add(nxt)
        ids=[k[1] for k in chain];users=sorted(used[key]);sample=paths[users[0]]
        # Follow the selected path orientation, not lexicographic edge ids.
        ids=sorted(ids,key=sample['edge_ids'].index);points=[]
        for eid in ids:
            a,b=sample['oriented_edges'][eid]
            if not points:points.append(a)
            points.append(b)
        cid='corridor:'+hashlib.sha256(repr((key[0],ids,users)).encode()).hexdigest()[:18]
        xy=[p[:2] for p in points]
        # Keep vertical witness sections in the path data; no zero-length 2D
        # control points are invented in the corridor overlay.
        clean=[xy[0]]
        for p in xy[1:]:
            if sum((a-b)**2 for a,b in zip(p,clean[-1]))>1e-6:clean.append(p)
        if len(clean)<2:continue
        length=sum(edge_records[e]['length_mm'] for e in ids);ports=[edge_records[e]['portal_id'] for e in ids if edge_records[e]['portal_id']]
        record=asdict(Corridor(cid,clean,key[0],terminals[users[0]]['level_id'],
            region_sequence=list(dict.fromkeys(edge_records[e]['region_id'] for e in ids)),portal_sequence=ports,
            reachable_terminal_ids=users,construction_cost=length/1000,validation_status='TOPOLOGICALLY_REACHABLE',
            reference_xyz_mm=points,length_mm=length,kind='SCHEMATIC',generated_by='GLOBAL_RADIAL_V3',source_edge_ids=ids,
            source_manifold_id=sample['manifold_id'],route_ids=['branch:'+t for t in users],
            entity_type='CORRIDOR' if len(users)>1 else 'BRANCH',
            preliminary_capacity={'tube_count':len(users),'minimum_width_mm':min(edge_records[e]['width_mm'] for e in ids),
                'status':'DETAIL_PENDING'}))
        corridors[cid]=record
        for e in ids:mapping[(key[0],e)]=cid
    for tid,path in paths.items():
        ids=list(dict.fromkeys(mapping[(path['network_type'],e)] for e in path['edge_ids'] if (path['network_type'],e) in mapping))
        assignments[tid]=ids
        branches['branch:'+tid]={'branch_id':'branch:'+tid,'terminal_id':tid,'network_type':path['network_type'],
            'source_manifold_id':path['manifold_id'],'corridor_ids':ids,'endpoint':path['endpoint'],
            'reference_xyz_mm':path['points_mm'],'validation_status':path['status'],'constraint_type':'PREFERRED_WAYPOINT'}
    for cid,c in corridors.items():
        point=c['reference_xyz_mm'][-1];following=[d for d in corridors.values() if d['source_manifold_id']==c['source_manifold_id'] and d['corridor_id']!=cid and
            sum((a-b)**2 for a,b in zip(d['reference_xyz_mm'][0],point))<.01]
        if len(following)>1:
            jid='junction:'+cid;junctions[jid]={'junction_id':jid,'position_xyz_mm':point,'parent_corridor_id':cid,
                'child_corridor_ids':[d['corridor_id'] for d in following],'network_type':c['network_type'],'validation_status':'DETAIL_PENDING'}
    return {'corridors':corridors,'branches':branches,'junctions':junctions,'assignments':assignments}


def check(p):
    """Integrity gate before publishing a schematic, including recovered data."""
    selected=p.selected_paths or p.solver_state.get('selected_paths',{})
    for tid,path in selected.items():
        if tid not in p.terminals or path['network_type']!=p.terminals[tid]['network_type']:raise ValueError('SCHEMATIC_NETWORK_MISMATCH')
        points=path['points_mm'];endpoint=path['endpoint']
        if sum((a-b)**2 for a,b in zip(points[-1],endpoint['origin_xyz_mm']))>1e-6:raise ValueError('SCHEMATIC_ENDPOINT_MISMATCH')
        if not p.corridor_assignments.get(tid):raise ValueError('SCHEMATIC_ORPHAN_DESTINATION')
        edges=path['oriented_edges']
        for a,b in zip(path['edge_ids'],path['edge_ids'][1:]):
            if sum((x-y)**2 for x,y in zip(edges[a][-1],edges[b][0]))>1e-6:raise ValueError('SCHEMATIC_DISCONTINUITY')
    for c in p.corridors.values():
        if c.get('generated_by')!='GLOBAL_RADIAL_V3':continue
        if not c.get('source_manifold_id') or not c['reachable_terminal_ids']:raise ValueError('SCHEMATIC_ORPHAN_CORRIDOR')
        if any(t not in selected or c['corridor_id'] not in p.corridor_assignments[t] for t in c['reachable_terminal_ids']):raise ValueError('SCHEMATIC_ORPHAN_CORRIDOR')
    return True
