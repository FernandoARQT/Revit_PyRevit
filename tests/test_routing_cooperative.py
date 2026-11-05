"""Network exchanges use real finite-radius swept volumes, without model writes."""
import time
import unittest
from unittest.mock import patch
from test_routing_core import configuration,profile,request,scene,obstacle
from ARQT_MEP.routing.settings import for_route,profile as make_profile
from ARQT_MEP.routing.core import cooperative,multi_route
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core.route_geometry import validate_route_geometry,simplify_route,primitives
from ARQT_MEP.routing.core.astar_3d import find_route_3d


class CoordinatedRouting(unittest.TestCase):
    def setUp(self):
        self.config=configuration();self.space=build_spatial_model(scene())
        self.requests=[request('a',(200,1000,250),(2800,1000,250)),
                       request('b',(1500,300,250),(1500,1700,250))]
        self.paths={'a':[[(200,1000,250),(2800,1000,250)],
                         [(200,1000,250),(200,100,250),(2800,100,250),(2800,1000,250)]],
                    'b':[[(1500,300,250),(1500,1700,250)]]}

    def planner(self,space,req,p,settings,**kwargs):
        # A finite catalogue isolates the exchange decision; no collision result
        # is mocked. Every candidate uses the production geometric validator.
        for points in self.paths[req['route_id']]:
            candidate={'points_mm':points,'passages':[]}
            checked=validate_route_geometry(space,candidate,p)
            if checked['status']=='VALID_WITH_WARNINGS':return dict(candidate,**checked)
        return {'status':'NOT_EVALUATED','reason':'NO_COMPATIBLE_CANDIDATE'}

    def greedy(self):
        a=self.planner(self.space,self.requests[0],profile(self.config),self.config)
        return {'a':a,'b':{'status':'NOT_EVALUATED','reason':'BLOCKED'}}

    def negotiate(self,routes,protected=(),planner=None,cancelled=None):
        return cooperative.negotiate(self.space,routes,self.requests,self.config,protected,
                                     planner or self.planner,time.monotonic()+2,cancelled)

    def check_clearances(self,routes):
        occupied=self.space.clone()
        for rid,route in routes.items():
            p=make_profile(for_route(self.config,rid))
            self.assertEqual(validate_route_geometry(occupied,route,p)['status'],'VALID_WITH_WARNINGS')
            occupied.reserve(rid,cooperative.points(route),p.radius_mm)

    def test_exchange_completes_crossing_routes_despite_more_total_length(self):
        original=self.greedy();result,events=self.negotiate(original)
        self.assertTrue(all(cooperative.usable(r) for r in result.values()),events)
        self.assertGreater(result['a']['length_m'],original['a']['length_m'])
        self.assertEqual(events[0]['evicted'],['a']);self.assertTrue(events[0]['accepted'])
        self.check_clearances(result)
        self.assertEqual(original['b']['status'],'NOT_EVALUATED')

    def test_protected_route_is_never_displaced(self):
        original=self.greedy();result,events=self.negotiate(original,{'a'})
        self.assertEqual(result,original);self.assertFalse(any(e['accepted'] for e in events))

    def test_unsuccessful_or_cancelled_replacement_rolls_back_whole_exchange(self):
        for stop in ('failed','cancelled'):
            original=self.greedy();cancel=[False]
            def planner(space,req,p,settings,**kwargs):
                if req['route_id']=='a':return {'status':'NOT_EVALUATED'}
                candidate=self.planner(space,req,p,settings,**kwargs)
                if stop=='cancelled':cancel[0]=True
                return candidate
            result,events=self.negotiate(original,planner=planner,cancelled=lambda:cancel[0])
            self.assertEqual(result,original);self.assertFalse(any(e['accepted'] for e in events))

    def test_different_diameters_keep_their_own_envelopes(self):
        self.config['route_overrides']={'a':{'section':{'outer_diameter_mm':80,'nominal_diameter_mm':80}}}
        result,events=self.negotiate(self.greedy())
        self.assertTrue(events[0]['accepted']);self.check_clearances(result)

    def test_optimizer_runs_exchange_even_with_one_iteration(self):
        self.config['search'].update(max_iterations=1,max_total_seconds=2)
        with patch.object(multi_route,'plan_route',side_effect=self.planner):
            result=multi_route.optimize_multiple_routes(self.space,self.requests,profile(self.config),self.config)
        self.assertEqual(result['iterations'][0]['accepted_exchanges'],1)
        self.assertEqual(cooperative.score(result['routes'],['a','b'])[0],0)
        self.check_clearances(result['routes'])

    def test_real_astar_recovers_a_terminal_blocked_by_the_first_route(self):
        self.config['search'].update(max_iterations=1,max_total_seconds=5,max_seconds=2)
        requests=[request('a',(200,1000,50),(2800,1000,50)),
                  request('b',(1500,1000,50),(1500,1700,50))]
        space=build_spatial_model(scene(height=100))
        greedy=configuration()
        greedy['search'].update(max_iterations=1,max_total_seconds=5,max_seconds=2)
        greedy['coordination']['enabled']=False
        first=multi_route.optimize_multiple_routes(space,requests,profile(greedy),greedy)
        result=multi_route.optimize_multiple_routes(space,requests,profile(self.config),self.config)
        self.assertEqual(cooperative.score(first['routes'],['a','b'])[0],1)
        self.assertEqual(cooperative.score(result['routes'],['a','b'])[0],0)
        self.assertEqual(result['iterations'][0]['accepted_exchanges'],1)
        for rid,route in result['routes'].items():
            self.assertEqual(validate_route_geometry(space,route,profile(self.config))['status'],'VALID_WITH_WARNINGS')
            space.reserve(rid,cooperative.points(route),profile(self.config).radius_mm)


class EarlierTurns(unittest.TestCase):
    def test_first_turn_can_start_after_one_radius(self):
        config=configuration();space=build_spatial_model(scene([obstacle('blocks_late_turn',[100,300,0,210,350,500])]))
        p=profile(config)
        result=find_route_3d(space,(200,200,250),(250,400,250),p,config['search'],50)
        self.assertIn('points_mm',result)
        points=simplify_route(result['points_mm'],space,p.radius_mm)
        self.assertEqual(len(points),3,points)
        self.assertEqual(points[1],(250,200,250),points)
        checked=validate_route_geometry(space,{'points_mm':points},p)
        self.assertEqual(checked['status'],'VALID_WITH_WARNINGS',checked)
        self.assertEqual(checked['bends'],1)

    def test_adjacent_turns_and_short_end_still_require_the_full_radius(self):
        with self.assertRaises(ValueError):primitives([(0,0,0),(50,0,0),(50,50,0),(100,50,0)],50)
        with self.assertRaises(ValueError):primitives([(0,0,0),(40,0,0),(40,200,0)],50)
        with self.assertRaises(ValueError):primitives([(0,0,0),(200,0,0),(200,40,0)],50)


if __name__=='__main__':unittest.main()
