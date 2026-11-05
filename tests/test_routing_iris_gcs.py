"""IRIS/GCS contracts. Written for the next validation phase; no Revit edits."""
import time
import unittest
from unittest.mock import patch

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union
from test_routing_core import configuration,profile,scene,obstacle
from ARQT_MEP.routing.core import iris_gcs
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core.route_geometry import validate_route_geometry

try:
    from scipy.spatial import ConvexHull
    HAS_SCIPY=True
except ImportError:
    HAS_SCIPY=False


class ConvexDecomposition(unittest.TestCase):
    def test_hole_is_not_filled_and_all_original_area_is_preserved(self):
        polygon=Polygon([(0,0),(1000,0),(1000,1000),(0,1000)],
                        holes=[[(400,400),(600,400),(600,600),(400,600)]])
        parts=iris_gcs.convex_parts(polygon)
        self.assertTrue(parts)
        self.assertTrue(all(polygon.covers(p) and p.equals(p.convex_hull) for p in parts))
        self.assertLess(unary_union(parts).symmetric_difference(polygon).area,1e-5)

    def test_nonconvex_cover_has_real_overlap_instead_of_only_shared_edges(self):
        polygon=Polygon([(0,0),(2000,0),(2000,800),(800,800),(800,2000),(0,2000)])
        parts=iris_gcs.convex_parts(polygon)
        self.assertTrue(any(a.intersection(b).area>1 for i,a in enumerate(parts) for b in parts[i+1:]))

    def test_identical_planes_merge_room_seams_without_filling_a_gap(self):
        data=scene();data['regions']=[{'id':'a','box_mm':[0,0,0,1500,2000,500]},
            {'id':'b','box_mm':[1500,0,0,3000,2000,500]},
            {'id':'c','box_mm':[4000,0,0,5000,2000,500]}]
        regions=iris_gcs._merged_regions(build_spatial_model(data),time.monotonic()+2,None)
        self.assertEqual(len(regions),2)
        self.assertTrue(any(r.contains((1500,1000,250),30) for r in regions))
        self.assertFalse(any(r.contains((3500,1000,250)) for r in regions))


@unittest.skipUnless(HAS_SCIPY,'Optional SciPy backend is not installed')
class ConvexRouting(unittest.TestCase):
    def setUp(self):
        self.settings=configuration();self.profile=profile(self.settings)

    def test_endpoint_near_real_clearance_is_not_excluded_by_extra_millimeter(self):
        space=build_spatial_model(scene())
        report=iris_gcs.find_gcs(space,(200,500,30.01),(2800,500,30.01),self.profile,self.settings,
                               deadline=time.monotonic()+5)
        self.assertIn(report['status'],('VALID','VALID_WITH_WARNINGS'),report)
        self.assertNotEqual(report.get('reason'),'IRIS_ENDPOINT_NOT_COVERED')

    def inflate(self,space,seed=(400,1000,250)):
        hulls=iris_gcs._obstacles(space,np,ConvexHull)
        cell=iris_gcs.inflate(space,space.regions[0],space.regions[0].polygon,seed,
            self.profile.radius_mm,hulls,time.monotonic()+15,iterations=1)
        self.assertIsNotNone(cell)
        return cell,hulls

    def test_every_obstacle_has_a_proven_separating_plane_and_ellipsoid_is_inside(self):
        space=build_spatial_model(scene([obstacle('block',[1200,700,0,1800,1300,500])]))
        cell,hulls=self.inflate(space)
        A,b=np.array(cell['A']),np.array(cell['b'])
        for vertices in hulls:
            self.assertTrue(any(np.min(vertices@normal)-offset>=self.profile.radius_mm-1e-4
                                for normal,offset in zip(A,b)))
        center,C=np.array(cell['center']),np.array(cell['ellipsoid_C_mm'])
        self.assertGreater(np.linalg.det(C),0)
        self.assertGreaterEqual(float(np.min(b-A@center-np.linalg.norm(A@C,axis=1))),-1e-3)

    def test_reserved_duct_is_excluded_from_cells_including_both_end_spheres(self):
        space=build_spatial_model(scene())
        space.reserve('protected',[(1500,600,250),(1500,1400,250)],75)
        cell,hulls=self.inflate(space)
        self.assertTrue(hulls)
        A,b=np.array(cell['A']),np.array(cell['b'])
        for vertices in hulls:
            self.assertTrue(any(np.min(vertices@normal)-offset>=self.profile.radius_mm-1e-4
                                for normal,offset in zip(A,b)))
        self.assertTrue(any(float(normal@np.array([1500,600,175]))>offset
                            for normal,offset in zip(A,b)))

    def test_degenerate_mesh_uses_padded_conservative_box(self):
        solid=obstacle('flat',[1400,0,250,1600,2000,250])
        solid['meshes']=[{'vertices_mm':[(1400,0,250),(1600,0,250),(1400,2000,250)],'faces':[(0,1,2)]}]
        space=build_spatial_model(scene([solid]))
        hulls=iris_gcs._obstacles(space,np,ConvexHull)
        self.assertEqual(np.linalg.matrix_rank(hulls[0]-hulls[0][0]),3)
        self.assertLess(np.min(hulls[0][:,2]),250)
        self.assertGreater(np.max(hulls[0][:,2]),250)

    def test_sloping_cell_stays_above_underside_and_below_upper_plane(self):
        data=scene();data['regions']=[{'id':'inclined',
            'polygon':{'type':'Polygon','coordinates':[[[0,0],[3000,0],[3000,2000],[0,2000],[0,0]]]},
            'lower':{'plane':{'origin':[0,0,0],'normal':[-.1,0,1]}},
            'upper':{'plane':{'origin':[0,0,500],'normal':[-.1,0,1]}}}]
        space=build_spatial_model(data);cell,_=self.inflate(space,seed=(400,1000,290))
        c,C=np.array(cell['center']),np.array(cell['ellipsoid_C_mm'])
        for axis in np.vstack([np.eye(3),-np.eye(3)]):
            self.assertTrue(space.regions[0].contains(c+C@axis,self.profile.radius_mm-1e-3))

    def test_free_convex_route_preserves_endpoints_and_passes_full_curved_geometry(self):
        space=build_spatial_model(scene());start=(200,500,250);goal=(2800,500,250)
        result=iris_gcs.find_gcs(space,start,goal,self.profile,self.settings,deadline=time.monotonic()+15)
        self.assertEqual(result.get('search_method'),'IRIS_GCS_EXPLICIT_PATH_SEARCH',result)
        self.assertEqual(result['points_mm'][0],start)
        self.assertEqual(result['points_mm'][-1],goal)
        self.assertEqual(validate_route_geometry(space,result,self.profile)['status'],'VALID_WITH_WARNINGS')

    def test_forbidden_full_height_barrier_cannot_be_used_as_a_portal(self):
        solid=obstacle('roof',[1400,0,0,1600,2000,500]);solid['hard_forbidden']=True
        space=build_spatial_model(scene([solid]))
        result=iris_gcs.find_gcs(space,(200,500,250),(2800,500,250),self.profile,self.settings,
                                  deadline=time.monotonic()+15)
        self.assertEqual(result['status'],'NOT_EVALUATED',result)
        self.assertNotIn('points_mm',result)


class BackendAvailability(unittest.TestCase):
    def test_static_obstacle_cache_is_shared_but_new_reservations_are_not(self):
        if not HAS_SCIPY:self.skipTest('Optional SciPy backend is not installed')
        solid=obstacle('block',[1200,700,0,1800,1300,500])
        space=build_spatial_model(scene([solid]));iris_gcs._STATIC_OBSTACLES.clear()
        with patch.object(iris_gcs,'_static_obstacles',wraps=iris_gcs._static_obstacles) as prepare:
            first=iris_gcs._obstacles(space,np,ConvexHull)
            clone=space.clone();clone.reserve('new',[(500,500,250),(900,500,250)],30)
            second=iris_gcs._obstacles(clone,np,ConvexHull)
            self.assertEqual(prepare.call_count,1)
        self.assertEqual(len(second),len(first)+1)
        self.assertFalse(space.reservations)

    def test_short_attempt_defers_iris_without_claiming_geometry_is_blocked(self):
        from ARQT_MEP.routing.core.multi_route import plan_route
        from test_routing_core import request
        config=configuration();space=build_spatial_model(scene([obstacle('wall',[1400,0,0,1600,2000,500])]))
        with patch.object(iris_gcs,'find_gcs') as backend:
            result=plan_route(space,request(),profile(config),config,deadline=time.monotonic()+.2)
        backend.assert_not_called()
        self.assertTrue(any(a.get('reason')=='IRIS_DEFERRED_SHORT_BUDGET' for a in result.get('attempts',[])))

    def test_optional_90_degree_docking_guard_does_not_become_a_user_waypoint(self):
        from ARQT_MEP.routing.core.multi_route import plan_route
        from test_routing_core import request
        config=configuration();config['bend_radius_mm']=400
        req=request();req.update(compact_connections=True,oriented_docking=True)
        for end in ('source','destination'):req[end]['direction']=[0,0,1]
        result=plan_route(build_spatial_model(scene()),req,profile(config),config)
        self.assertEqual(result['status'],'VALID_WITH_WARNINGS',result)
        self.assertTrue(result['connection_validation_pending'])
        self.assertEqual(result['waypoints_mm'],[])
        req['waypoints_mm']=[(1500,500,700)]
        result=plan_route(build_spatial_model(scene()),req,profile(config),config)
        self.assertEqual(result['reason'],'WAYPOINT_BLOCKED')

    def test_missing_optional_dependency_is_explicit_not_a_geometry_failure(self):
        config=configuration();space=build_spatial_model(scene())
        with patch.object(iris_gcs,'scientific_context',side_effect=ImportError('fixture')):
            result=iris_gcs.find_gcs(space,(200,500,250),(2800,500,250),profile(config),config)
        self.assertEqual(result,{'status':'NOT_EVALUATED','reason':'IRIS_GCS_SCIPY_REQUIRED'})

    @unittest.skipUnless(HAS_SCIPY,'Optional SciPy backend is not installed')
    def test_cancelled_search_does_not_create_cells(self):
        config=configuration();space=build_spatial_model(scene())
        with patch.object(iris_gcs,'_build_cells') as build:
            result=iris_gcs.find_gcs(space,(200,500,250),(2800,500,250),profile(config),config,cancelled=lambda:True)
        self.assertEqual(result['reason'],'CANCELLED');build.assert_not_called()


if __name__=='__main__':unittest.main()
