"""Serialisable reachability diagnostics without false infeasibility claims."""


def analyze(graph,start,goal,k=8,deadline=float('inf'),cancelled=None):
    paths=graph.paths(start,goal,k,deadline,cancelled)
    return {'status':'TOPOLOGICAL_CANDIDATES' if paths else 'NO_PATH_IN_CURRENT_REPRESENTATION',
            'representation_complete':False,'graph_construction_completed':graph.complete,
            'source_regions':graph.regions_at(start),'destination_regions':graph.regions_at(goal),
            'corridors':paths,'physical_infeasibility_proven':False,
            'scope':'Region covers and admitted portals; interior obstacles and bend capacity require geometric search'}
