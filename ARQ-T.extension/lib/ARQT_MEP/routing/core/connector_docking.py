"""Oriented docking diagnostics; no inferred manufacturer fitting geometry."""
import math
from shapely.geometry import Point
from . import geometry as g


def analyze(space,port,profile,configuration):
    origin=port['xyz_mm'];direction=port.get('direction');reports=[]
    for region in space.regions:
        if not region.contains(origin,0):continue
        a,b,c=region.lower_coefficients;u,v,w=region.upper_coefficients
        normal=g.unit((-a,-b,1));upper_normal=g.unit((-u,-v,1))
        if not direction or g.dot(normal,upper_normal)<1-1e-8 or g.dot(normal,g.unit(direction))<1-1e-6:continue
        x,y,z=origin;offset=(z-a*x-b*y-c)*region.lower_normal_z
        height=(w-c)*region.lower_normal_z
        required=offset+profile.bend_radius_mm+profile.radius_mm
        reports.append({'region_id':region.data.get('id'),'normal_offset_mm':offset,
            'available_normal_height_mm':height,'quarter_turn_required_height_mm':required,
            'deficit_mm':max(0,required-height),'region_edge_distance_mm':region.polygon.boundary.distance(Point(x,y)),
            'status':'LOCAL_QUARTER_TURN_CLEARANCE_CONFLICT' if required>height+1e-6 else 'HEIGHT_PRECHECK_PASSED',
            'scope':'Normal outlet and circular quarter-turn within parallel bounding planes; not a proof about all fittings or trajectories',
            'bend_radius_provisional':configuration.get('bend_geometry_provisional',True)})
    return {'connector_id':port['id'],'connector_origin':origin,'connector_direction':direction,
            'local_turn_checks':reports,'native_compatibility':'PENDING_REAL_FITTING_VERIFICATION',
            'validation_status':'DOCKING_NOT_CERTIFIED'}
