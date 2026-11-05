import math
import unittest
from test_routing_topology import fixture
from ARQT_MEP.routing.sizing import circular_size
from ARQT_MEP.routing.systems.heat_recovery_topology import requests


class AirflowSizing(unittest.TestCase):
    def test_selects_smallest_native_size_covering_final_flow(self):
        self.assertEqual(circular_size(13.5658,[75,90,100,125])['diameter_mm'],90)
        self.assertEqual(circular_size(9,[75,90,100,125])['diameter_mm'],75)
        self.assertEqual(circular_size(33,[75,90,100,125])['diameter_mm'],125)

    def test_limit_and_invalid_values(self):
        with self.assertRaises(ValueError):circular_size(100,[75])
        with self.assertRaises(ValueError):circular_size(float('nan'),[75])
        q=3*math.pi*.075**2/4*1000
        self.assertEqual(circular_size(q,[75])['diameter_mm'],75)

    def test_branch_and_main_sizes_and_transition_requirements(self):
        catalog,configuration,destinations=fixture()
        catalog['configured_model']={'flexible_duct':{'sizing_diameters_mm':[75,90,100,125,160],'transition_type_id':123}}
        for terminal in catalog['terminals']:terminal['connectors'][0]['size']['diameter_mm']=80
        routes,_,_,overrides=requests(catalog,configuration,destinations)
        for route in routes:
            self.assertNotIn('error',route)
            diameter=overrides[route['route_id']]['section']['nominal_diameter_mm']
            self.assertEqual(diameter,90 if route['route_kind']=='MAIN' and route['network_type']=='SUPPLY' else 75)
            self.assertTrue(route['required_transitions'])
            self.assertLessEqual(route['sizing']['velocity_mps'],3)


if __name__=='__main__':unittest.main()
