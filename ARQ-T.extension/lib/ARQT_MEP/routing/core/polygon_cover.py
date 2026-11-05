"""Inner polygon reduction for kernels with a minimum native edge length."""
from shapely.geometry import Polygon
from . import geometry as g


def native_subset(polygon,min_edge_mm):
    original=polygon;current=polygon
    while True:
        rings=[list(current.exterior.coords)[:-1]]+[list(r.coords)[:-1] for r in current.interiors]
        changed=False
        for index,ring in enumerate(rings):
            for i,a in enumerate(ring):
                b=ring[(i+1)%len(ring)]
                if g.distance((*a,0),(*b,0))>=min_edge_mm:continue
                if len(ring)<=3:continue
                for remove in (i,(i+1)%len(ring)):
                    trial_rings=[list(points) for points in rings]
                    del trial_rings[index][remove]
                    candidate=Polygon(trial_rings[0],trial_rings[1:])
                    # Simplify() alone may add space at concave vertices.
                    if candidate.is_valid and not candidate.is_empty and original.covers(candidate):
                        current=candidate;changed=True;break
                if changed:break
            if changed:break
        if not changed:return current
