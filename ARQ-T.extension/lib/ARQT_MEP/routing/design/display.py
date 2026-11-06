"""Condense checked graph edges into useful editable centre/perimeter lines."""
import hashlib
import math
from dataclasses import asdict
from .model import Corridor


def corridors(graph):
    nodes=graph['nodes'];canonical={}
    for key,node in nodes.items():
        signature=(node['level_id'],tuple(round(v,3) for v in node['xyz_mm'][:2]))
        if signature not in canonical or node['xyz_mm'][2]<nodes[canonical[signature]]['xyz_mm'][2]:canonical[signature]=key
    admitted=set(canonical.values())
    edges={k:e for k,e in graph['edges'].items() if all(n in admitted for n in e['node_ids'])}
    preferred={k:e for k,e in edges.items() if e['kind'] in ('MEDIAL','PERIMETER')}
    if preferred:edges=preferred
    if not edges:edges=dict(graph['edges'])
    adjacency={}
    for key,edge in edges.items():
        for node in edge['node_ids']:adjacency.setdefault(node,[]).append(key)
    visited=set();result={}
    def walk(start,first):
        point=start;edge_id=first;path=[nodes[start]['xyz_mm']];source=[];widths=[];heights=[];distance=0;regions=[];kinds=set()
        while edge_id not in visited:
            visited.add(edge_id);edge=edges[edge_id];source.append(edge_id);kinds.add(edge['kind'])
            a,b=edge['node_ids'];reverse=a!=point;other=a if reverse else b;path.append(nodes[other]['xyz_mm'])
            length=edge['length_mm']
            for field,target in (('width_profile',widths),('height_profile',heights)):
                sections=list(reversed(edge[field])) if reverse else edge[field]
                target.extend(dict(s,station_mm=distance+(length-s['station_mm'] if reverse else s['station_mm'])) for s in sections)
            distance+=length;regions.extend(edge['region_sequence']);point=other
            options=[e for e in adjacency[point] if e not in visited]
            if len(adjacency[point])!=2 or len(options)!=1:break
            edge_id=options[0]
        cid='axis:'+hashlib.sha256('|'.join(sorted(source)).encode()).hexdigest()[:16]
        # Vertical transitions are properties of the 3D graph, not zero-length
        # plan handles. The generator does not emit pure vertical edges.
        result[cid]=asdict(Corridor(cid,[p[:2] for p in path],'SHARED',nodes[start]['level_id'],
            region_sequence=list(dict.fromkeys(regions)),width_profile=widths,height_profile=heights,
            construction_cost=distance/1000,validation_status='SPATIAL_CANDIDATE',reference_xyz_mm=path,
            length_mm=distance,kind=next(iter(kinds)) if len(kinds)==1 else 'MIXED',
            generated_by='V3_FREE_SPACE_GRAPH',source_edge_ids=source))
    starts=sorted(adjacency,key=lambda n:(len(adjacency[n])==2,n))
    for node in starts:
        for edge_id in sorted(adjacency[node]):
            if edge_id not in visited:walk(node,edge_id)
    return result
