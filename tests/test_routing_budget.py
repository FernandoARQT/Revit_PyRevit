import copy
import types
import unittest
from unittest.mock import patch
from test_routing_core import scene,configuration,profile,request
from ARQT_MEP.routing.core import multi_route as routing
from ARQT_MEP.routing.core.spatial_model import build_spatial_model,Region
from ARQT_MEP.routing.core.astar_3d import SparseGrid
from shapely.geometry import box,mapping


class RoutingBudget(unittest.TestCase):
    def test_budget_uses_shared_clock_and_divides_the_current_phase(self):
        from ARQT_MEP.routing.core.search_budget import SearchBudgetManager
        clock=[100.]
        budget=SearchBudgetManager(200.,clock=lambda:clock[0])
        self.assertEqual(budget.allocate('route',pending=3,consolidation=True,deadline=112.),104.)
        clock[0]=104.
        self.assertEqual(budget.allocate('next',pending=2,consolidation=True,deadline=112.),108.)
        clock[0]=200.
        self.assertTrue(budget.stopped())

    def test_each_route_gets_a_turn_without_discarded_preliminary_search(self):
        config=configuration();config['search'].update(max_iterations=1,max_total_seconds=12);config['coordination']['enabled']=False
        clock=[0.];calls=[]
        def plan(space,req,p,settings,**kwargs):
            calls.append((req['route_id'],kwargs['deadline']))
            clock[0]=kwargs['deadline']+.001
            return {'status':'NOT_EVALUATED','reason':'SEARCH_LIMIT'}
        requests=[request('a'),request('b'),request('c')]
        with patch.object(routing,'time',types.SimpleNamespace(monotonic=lambda:clock[0])),patch.object(routing,'plan_route',side_effect=plan):
            result=routing.optimize_multiple_routes(build_spatial_model(scene()),requests,profile(config),config)
        self.assertEqual(len(calls),3)
        self.assertAlmostEqual(calls[0][1],4)
        self.assertEqual(result['preliminary_count'],0)
        self.assertEqual(len(result['routes']),3)

    def test_successful_pass_survives_later_budget_exhaustion(self):
        config=configuration();config['search'].update(max_iterations=2,max_total_seconds=12)
        clock=[0.];calls=[]
        def plan(space,req,p,settings,**kwargs):
            calls.append(req['route_id'])
            if len(calls)>2:
                clock[0]=13
                return {'status':'NOT_EVALUATED','reason':'TOTAL_TIME_LIMIT'}
            clock[0]+=1
            return {'status':'VALID_WITH_WARNINGS','points_mm':[req['source']['xyz_mm'],req['destination']['xyz_mm']],
                    'primitives':[{'points_mm':[req['source']['xyz_mm'],req['destination']['xyz_mm']]}],'cost':{'total':1}}
        requests=[request('a'),request('b',(200,1200,250),(2800,1200,250))]
        with patch.object(routing,'time',types.SimpleNamespace(monotonic=lambda:clock[0])),patch.object(routing,'plan_route',side_effect=plan):
            result=routing.optimize_multiple_routes(build_spatial_model(scene()),requests,profile(config),config)
        self.assertTrue(all(r['status']=='VALID_WITH_WARNINGS' for r in result['routes'].values()))

    def test_sloped_grid_edges_follow_ceiling_and_cannot_skip_collision(self):
        data={'regions':[{'kind':'FALSE_CEILING','polygon':mapping(box(0,0,3000,3000)),
             'lower':{'plane':{'origin':[0,0,2500],'normal':[0,-.16,1]}},
             'upper':{'plane':{'origin':[0,0,2695],'normal':[0,-.16,1]}}}], 'obstacles':[]}
        config=configuration();config['section'].update(outer_diameter_mm=90,nominal_diameter_mm=90);config['clearance_mm']=15
        space=build_spatial_model(data);p=profile(config)
        grid=SparseGrid(space,(500,500,2680),(500,2500,3000),10,p)
        neighbors=list(grid.neighbors(grid.start))
        inclined=[grid.point(node) for node,_ in neighbors if grid.point(node)[1]!=500 and grid.point(node)[2]!=2680]
        self.assertTrue(inclined)
        self.assertTrue(all(space.free(grid.point(grid.start),point,p.radius_mm) for point in inclined))

    def test_coarse_grid_checks_entire_edge_between_free_nodes(self):
        from test_routing_core import obstacle
        config=configuration();p=profile(config)
        space=build_spatial_model(scene([obstacle('thin_wall',[250,0,0,260,2000,500])]))
        grid=SparseGrid(space,(200,500,250),(2800,500,250),100,p)
        next_node=grid.index((300,500,250))
        self.assertTrue(grid.node_free(grid.start));self.assertTrue(grid.node_free(next_node))
        self.assertNotIn(next_node,[n for n,_ in grid.neighbors(grid.start)])

    def test_large_main_does_not_remove_small_branch_passages(self):
        from test_routing_core import obstacle
        from ARQT_MEP.routing.core.passages import generate_passages
        config=configuration()
        config['route_overrides']={'main':{'section':{'nominal_diameter_mm':200,'outer_diameter_mm':200},'bend_radius_mm':150}}
        wall=obstacle('partition',[1400,0,0,1600,2000,195],'PASSABLE')
        wall.update(wall_axis_mm=[[1500,0,0],[1500,2000,0]],wall_thickness_mm=200)
        space=build_spatial_model(scene([wall],height=195))
        requests=[request('branch',(200,500,100),(2800,500,100)),request('main',(200,1200,100),(2800,1200,100))]
        portals=generate_passages(space,requests,config)
        self.assertTrue(portals)
        self.assertTrue(any(p['radius_mm']==30 for p in portals))
        self.assertFalse(any(p['radius_mm']==105 for p in portals))


if __name__=='__main__':unittest.main()
