"""Demonstrated candidate exclusions and expandable repair neighbourhoods."""
from .cooperative import overlaps,usable


class RouteConflictGraph:
    def __init__(self):self.nodes={};self.edges={}

    def add(self,key,route,radius,source_id):
        self.nodes[key]=(route,radius,source_id);self.edges.setdefault(key,{})
        for other,(candidate,r,port) in sorted(self.nodes.items()):
            if other==key:continue
            reason='CONNECTOR_EXCLUSIVITY' if port==source_id else 'GEOMETRIC_INTERFERENCE' if overlaps(route,radius,candidate,r) else None
            if reason:self.edges[key][other]=reason;self.edges[other][key]=reason

    def neighbourhood(self,seeds,depth=1):
        result=set(seeds)
        for _ in range(depth):result.update(n for key in list(result) for n in self.edges.get(key,{}))
        return sorted(result)

    def export_state(self):return {'nodes':sorted(self.nodes),'edges':self.edges,'capacity_estimates_are_exclusions':False}
