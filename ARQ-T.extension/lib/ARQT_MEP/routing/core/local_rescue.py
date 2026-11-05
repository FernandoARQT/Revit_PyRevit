"""Small endpoint repair after the ordinary grid has reached its vicinity."""
import time
from . import geometry as g
from .astar_3d import find_route_3d


def rescue_tail(space,failed,goal,profile,search,passages,scenario,cancelled,deadline,diversity=()):
    prefix=failed.get('partial_points_mm',[])
    extent=search['rescue_extent_mm']
    if len(prefix)<2 or g.distance(prefix[-1],goal)>extent/2:return failed
    seconds=min(search['rescue_seconds'],deadline-time.monotonic())
    if seconds<=0 or (cancelled and cancelled()):return failed
    # A bounded cube, never a five-millimetre grid over the building.
    center=g.mul(g.add(prefix[-1],goal),.5)
    bounds=[v-extent/2 for v in center]+[v+extent/2 for v in center]
    options=dict(search,max_seconds=seconds,max_nodes=min(search['max_nodes'],10000),local_bounds_mm=bounds)
    tail=find_route_3d(space,prefix[-1],goal,profile,options,search['rescue_resolution_mm'],
                       passages,scenario,cancelled,diversity)
    diagnostic={'method':'LOCAL_ENDPOINT_RESCUE','resolution_mm':search['rescue_resolution_mm'],
                'box_mm':bounds,'expanded':tail.get('expanded',0),'reason':tail.get('reason'),
                'success':'points_mm' in tail}
    if 'points_mm' not in tail:return dict(failed,local_rescue=diagnostic)
    result=dict(tail,points_mm=prefix[:-1]+tail['points_mm'],local_rescue=diagnostic)
    result['passages']=list({p['id']:p for p in failed.get('partial_passages',[])+tail.get('passages',[])}.values())
    result['expanded']=failed.get('expanded',0)+tail.get('expanded',0)
    # The caller must validate all joins and bend envelopes of the full route.
    return result
