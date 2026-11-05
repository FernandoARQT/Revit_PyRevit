import time
import unittest
from unittest.mock import patch
from test_routing_core import scene,configuration,profile
from ARQT_MEP.routing.settings import validate
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core.astar_3d import SparseGrid
from ARQT_MEP.routing.core.local_rescue import rescue_tail


class LocalRescue(unittest.TestCase):
    def setUp(self):
        self.config=configuration();self.p=profile(self.config);self.space=build_spatial_model(scene())
        self.failed={'reason':'SEARCH_LIMIT','partial_points_mm':[(200,500,250),(1000,500,250)],'expanded':100}

    def test_default_resolution_hierarchy(self):
        s=validate({})['search']
        self.assertEqual([s[k] for k in ('coarse_resolution_mm','fine_resolution_mm','local_refinement_mm','rescue_resolution_mm')],[50,25,10,5])

    def test_near_endpoint_uses_bounded_fine_search_and_retains_prefix(self):
        with patch('ARQT_MEP.routing.core.local_rescue.find_route_3d',return_value={
            'points_mm':[(1000,500,250),(1100,500,250)],'passages':[],'expanded':2}) as finder:
            r=rescue_tail(self.space,self.failed,(1100,500,250),self.p,self.config['search'],[],'A',None,time.monotonic()+10)
        args=finder.call_args.args;options=args[4]
        self.assertEqual(args[5],5)
        self.assertEqual(options['max_nodes'],10000)
        self.assertLessEqual(options['max_seconds'],2)
        box=options['local_bounds_mm']
        self.assertTrue(all(box[i+3]-box[i]==500 for i in range(3)))
        grid=SparseGrid(self.space,args[1],args[2],5,self.p,local_bounds=box)
        self.assertTrue(all(len(a)<=103 for a in grid.axes))
        self.assertEqual(r['points_mm'][0],(200,500,250))
        self.assertEqual(r['expanded'],102)

    def test_distant_or_expired_attempt_does_not_launch_fine_grid(self):
        with patch('ARQT_MEP.routing.core.local_rescue.find_route_3d') as finder:
            for goal,deadline in (((2800,500,250),time.monotonic()+10),((1100,500,250),time.monotonic()-1)):
                self.assertIs(rescue_tail(self.space,self.failed,goal,self.p,self.config['search'],[],'A',None,deadline),self.failed)
            finder.assert_not_called()

    def test_blocked_goal_is_not_promoted_to_valid(self):
        result=rescue_tail(self.space,self.failed,(1100,500,29),self.p,self.config['search'],[],'A',None,time.monotonic()+2)
        self.assertNotIn('points_mm',result)
        self.assertFalse(result['local_rescue']['success'])


if __name__=='__main__':unittest.main()
