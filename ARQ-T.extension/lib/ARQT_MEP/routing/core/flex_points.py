"""Trim obsolete interpolation points after native transitions move duct ends."""
from . import geometry as g


def fitted_points(points,start,end,start_tangent,end_tangent,minimum_mm=1.):
    values=[tuple(p) for p in points[1:-1]];start=tuple(start);end=tuple(end)
    a,b=g.unit(start_tangent),g.unit(end_tangent)
    while values and (g.dot(g.sub(values[0],start),a)<minimum_mm or g.distance(values[0],start)<minimum_mm):values.pop(0)
    while values and (g.dot(g.sub(values[-1],end),b)>-minimum_mm or g.distance(values[-1],end)<minimum_mm):values.pop()
    result=[start]
    for p in values+[end]:
        if g.distance(result[-1],p)>1e-6:result.append(p)
    if len(result)<2:raise ValueError('Transiciones sin espacio para el flexible.')
    return result
