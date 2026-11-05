"""Portal admission shares the exact policy of the geometric grid."""
import time
from .astar_3d import SparseGrid,passage_cost
from . import geometry as g


def attach(graph,start,goal,profile,configuration,scenario='A',deadline=float('inf'),cancelled=None):
    grid=SparseGrid(graph.space,start,goal,configuration['search']['coarse_resolution_mm'],profile,
                    configuration['constraints']['passages'],scenario)
    count=0
    for portal in grid.portals:
        if time.monotonic()>=deadline or (cancelled and cancelled()):graph.complete=False;break
        a,b=portal['points_mm'];sources=graph.regions_at(a);targets=graph.regions_at(b)
        # Admission is constructive only. Final capsule, approaches, bends and
        # reservations are checked by independent route validation.
        for i in sources:
            for j in targets:
                if i!=j:
                    graph.add(i,j,{'kind':'PORTAL','portal_id':portal['id'],'points_mm':[a,b],
                        'classification':portal['classification'],'host_ids':portal['element_ids'],
                        'cost':g.distance(a,b)/1000+passage_cost(portal,profile.weights)})
                    count+=1
    return count
