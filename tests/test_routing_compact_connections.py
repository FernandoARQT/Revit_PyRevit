import unittest
from test_routing_core import scene,configuration,profile,obstacle
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core.multi_route import compact_anchor,plan_route
from ARQT_MEP.routing.core.route_geometry import validate_route_geometry


class CompactConnections(unittest.TestCase):
    def test_anchor_is_near_port_and_not_forced_to_300(self):
        settings=configuration();settings['connection_straight_mm']=300
        port={'id':'p','xyz_mm':[1000,1000,45],'direction':[0,0,1],'element_key':'terminal'}
        space=build_spatial_model(scene(height=195))
        a,b=compact_anchor(space,port,profile(settings),settings)
        self.assertEqual(a,tuple(port['xyz_mm']))
        self.assertLess(b[2]-a[2],100)
        self.assertTrue(space.free(b,b,profile(settings).radius_mm))

    def test_anchor_does_not_start_inside_equipment_or_roof(self):
        settings=configuration()
        port={'id':'p','xyz_mm':[1000,1000,45],'direction':[0,0,1],'element_key':'terminal'}
        body=obstacle('terminal',[900,900,0,1100,1100,45])
        roof=obstacle('roof',[0,0,195,3000,2000,300]);roof['hard_forbidden']=True
        space=build_spatial_model(scene([body,roof],height=195))
        a,b=compact_anchor(space,port,profile(settings),settings)
        self.assertGreater(b[2],45+profile(settings).radius_mm)
        self.assertFalse(space.collisions(b,b,profile(settings).radius_mm))

    def test_deferred_connection_has_separate_bulk_validation(self):
        settings=configuration();p=profile(settings)
        space=build_spatial_model(scene(height=195))
        route={'points_mm':[[500,500,20],[500,500,100],[2500,500,100],[2500,500,20]],
               'routing_points_mm':[[500,500,100],[2500,500,100]],'connection_validation_pending':True}
        report=validate_route_geometry(space,route,p)
        self.assertNotEqual(report['status'],'INVALID')
        self.assertEqual(report['sampled_points_mm'][0],tuple(route['points_mm'][0]))
        self.assertTrue(any('Extremos' in message for message in report['warnings']))

    def test_complete_plan_keeps_actual_ports_and_validates_bulk_between_anchors(self):
        settings=configuration()
        settings['section'].update(outer_diameter_mm=90,nominal_diameter_mm=90)
        settings['clearance_mm']=15;settings['bend_radius_mm']=125
        settings['search']['local_refinement_mm']=10
        bodies=[obstacle('a',[400,400,0,600,600,45]),obstacle('b',[2400,400,0,2600,600,45])]
        space=build_spatial_model(scene(bodies,height=195))
        request={'route_id':'compact','compact_connections':True,
                 'source':{'id':'a','element_key':'a','xyz_mm':[500,500,45],'direction':[0,0,1]},
                 'destination':{'id':'b','element_key':'b','xyz_mm':[2500,500,45],'direction':[0,0,1]}}
        route=plan_route(space,request,profile(settings),settings)
        self.assertIn(route['status'],('VALID','VALID_WITH_WARNINGS'))
        self.assertNotIn('network_type',route)
        self.assertEqual(route['chosen_source'],request['source'])
        self.assertEqual(route['destination'],request['destination'])
        self.assertTrue(route['connection_validation_pending'])
        self.assertEqual(route['points_mm'][0],(500,500,45))
        self.assertLess(route['connection_anchors_mm']['source'][2],195)


if __name__=='__main__':unittest.main()
