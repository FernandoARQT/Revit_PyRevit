import math
import unittest
from test_routing_core import scene,configuration
from ARQT_MEP.routing.core.natural_path import build
from ARQT_MEP.routing.core.route_geometry import simplify_route
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core import geometry as g


class NaturalPath(unittest.TestCase):
    def test_long_straight_has_only_two_native_controls(self):
        result=build([[i*25,0,250] for i in range(401)],125)
        self.assertEqual(result['native_control_count'],2)
        self.assertEqual(len(result['segments']),1)
        self.assertEqual(len(result['segments'][0]['points_mm']),2)

    def test_curve_uses_tangencies_instead_of_corner_or_cell_samples(self):
        result=build([[0,0,250],[1000,0,250],[1000,1000,250]],100)
        self.assertEqual([p['kind'] for p in result['segments']],['LINE','ARC','LINE'])
        arc=result['segments'][1]
        self.assertAlmostEqual(g.distance(arc['start_mm'],[900,0,250]),0)
        self.assertAlmostEqual(g.distance(arc['end_mm'],[1000,100,250]),0)
        self.assertAlmostEqual(g.distance(arc['start_tangent'],[1.,0.,0.]),0)
        self.assertAlmostEqual(g.distance(arc['end_tangent'],[0.,1.,0.]),0)
        self.assertEqual(result['native_control_count'],5)
        self.assertNotIn([1000,0,250],result['native_controls_mm'])
        for p in result['native_controls_mm'][1:-1]:self.assertAlmostEqual(g.distance(p,arc['center_mm']),100)

    def test_straight_simplification_works_in_arbitrary_3d_direction(self):
        result=build([[i,i*2,i*3] for i in range(100)],20)
        self.assertEqual(result['native_control_count'],2)

    def test_reversal_and_short_bends_are_not_silently_removed(self):
        for pts in ([[0,0,250],[500,0,250],[0,0,250]],[[0,0,250],[50,0,250],[50,50,250]]):
            with self.assertRaises(ValueError):build(pts,100)

    def test_search_staircase_is_replaced_by_free_diagonal(self):
        space=build_spatial_model(scene())
        points=[[200,200,250],[200,800,250],[800,800,250],[1500,1400,250]]
        result=simplify_route(points,space,30,bend_radius_mm=50)
        self.assertEqual(len(result),2)

    def test_protected_anchor_survives_float_transport_noise(self):
        space=build_spatial_model(scene())
        result=simplify_route([[200,500,250],[1000+1e-10,500,250],[2000,500,250]],space,30,
            preserve=[[1000,500,250]],bend_radius_mm=50)
        self.assertEqual(len(result),3)


if __name__=='__main__':unittest.main()
