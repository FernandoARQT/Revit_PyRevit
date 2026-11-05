import copy
import pickle
import unittest
from test_routing_core import scene,obstacle
from ARQT_MEP.routing.settings import effective_radius
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core.cooperative import overlaps
from ARQT_MEP.routing.core.route_geometry import simplify_route


def radius():
    return effective_radius({'section':{'shape':'CIRCULAR','outer_diameter_mm':90,
        'insulation_mm':0},'clearance_mm':15})


def route(y):
    return {'primitives':[{'points_mm':[(200,y,250),(2800,y,250)]}]}


class SurfaceClearance(unittest.TestCase):
    def test_two_90mm_tubes_have_15mm_surface_gap(self):
        space=build_spatial_model(scene())
        space.reserve('first',[(200,700,250),(2800,700,250)],radius())
        self.assertEqual(space.reservations[0]['radius_mm'],45)
        for method in (space.free,space.stub_free):
            self.assertTrue(method((200,805,250),(2800,805,250),radius()))
            self.assertFalse(method((200,804.9,250),(2800,804.9,250),radius()))
        self.assertFalse(overlaps(route(700),radius(),route(805),radius()))
        self.assertTrue(overlaps(route(700),radius(),route(804.9),radius()))

    def test_obstacle_and_ceiling_keep_full_15mm_allowance(self):
        space=build_spatial_model(scene([obstacle('wall',[0,0,0,3000,100,500])]))
        self.assertTrue(space.free((200,161,250),(2800,161,250),radius()))
        self.assertFalse(space.free((200,159,250),(2800,159,250),radius()))
        space=build_spatial_model(scene())
        self.assertTrue(space.free((200,500,61),(2800,500,61),radius()))
        self.assertFalse(space.free((200,500,59),(2800,500,59),radius()))

    def test_parallel_transport_preserves_clearance(self):
        for value in (copy.deepcopy(radius()),pickle.loads(pickle.dumps(radius()))):
            self.assertEqual(value,60)
            self.assertEqual(value.clearance_mm,15)

    def test_simplification_cannot_cut_through_obstacle_between_controls(self):
        space=build_spatial_model(scene([obstacle('block',[1400,800,0,1600,1200,500])]))
        points=[(200,1000,250),(200,1500,250),(2800,1500,250),(2800,1000,250)]
        simplified=simplify_route(points,space,radius(),bend_radius_mm=100)
        self.assertGreater(len(simplified),2)
        self.assertTrue(all(space.free(a,b,radius()) for a,b in zip(simplified,simplified[1:])))


if __name__=='__main__':unittest.main()
