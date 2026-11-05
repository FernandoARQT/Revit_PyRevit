"""Physical space unions, finite turns and joint candidate/port assignment."""
import copy
import time
import unittest
from unittest.mock import patch
from test_routing_core import configuration,profile,request,scene,obstacle
from test_routing_topology import fixture
from ARQT_MEP.routing import service
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core.multi_route import compact_anchor,plan_route,optimize_multiple_routes
from ARQT_MEP.routing.core.route_geometry import validate_route_geometry,primitives,tangent_joint
from ARQT_MEP.routing.core.corridors import find_corridor
from ARQT_MEP.routing.core.portfolio import select_candidates,coordinate
from ARQT_MEP.routing.core import cooperative as c
from ARQT_MEP.routing.systems.heat_recovery_topology import requests as topology


class ContinuousPlenum(unittest.TestCase):
    def space(self,gap=0,obstacles=()):
        return build_spatial_model({'regions':[{'box_mm':[0,0,0,1500,2000,500]},
            {'box_mm':[1500+gap,0,0,3000,2000,500]}],'obstacles':list(obstacles)})

    def test_adjacent_free_volumes_have_no_artificial_boundary(self):
        space=self.space()
        self.assertFalse(any(r.contains((1500,1000,250),30) for r in space.regions))
        self.assertTrue(space.free((200,1000,250),(2800,1000,250),30))

    def test_gap_and_real_obstacle_remain_blocked(self):
        self.assertFalse(self.space(1).free((200,1000,250),(2800,1000,250),30))
        wall=obstacle('roof_never_cross',[1400,0,0,1600,2000,500])
        self.assertFalse(self.space(obstacles=[wall]).free((200,1000,250),(2800,1000,250),30))

    def test_step_requires_full_envelope_above_both_lower_faces(self):
        space=build_spatial_model({'regions':[{'box_mm':[0,0,0,1500,2000,200]},
            {'box_mm':[1500,0,100,3000,2000,300]}],'obstacles':[]})
        self.assertTrue(space.free((1400,1000,150),(1600,1000,150),30))
        self.assertFalse(space.free((1400,1000,110),(1600,1000,110),30))

    def test_anchor_detects_narrow_height_interval_without_cell_inflation(self):
        config=configuration();config['section'].update(outer_diameter_mm=160,nominal_diameter_mm=160)
        config.update(clearance_mm=15,bend_radius_mm=160)
        port={'xyz_mm':[1000,1000,0],'direction':[0,0,1]}
        anchor=compact_anchor(build_spatial_model(scene(height=195)),port,profile(config),config)
        self.assertIsNotNone(anchor);self.assertTrue(95<=anchor[1][2]<=100)

    def wall_gap(self,wall_height=150,include_wall=True):
        wall=obstacle('partition',[1400,0,0,1600,2000,wall_height],'PASSABLE')
        wall.update(wall_axis_mm=[[1500,0,0],[1500,2000,0]],wall_thickness_mm=200)
        return build_spatial_model({'regions':[{'id':'left','kind':'FALSE_CEILING','box_mm':[0,0,0,1400,2000,500]},
            {'id':'right','kind':'FALSE_CEILING','box_mm':[1600,0,0,3000,2000,500]}],
            'obstacles':[wall] if include_wall else []})

    def test_air_over_a_wall_connects_ceilings_but_does_not_ignore_solids(self):
        self.assertTrue(self.wall_gap().free((1200,1000,250),(1800,1000,250),30))
        self.assertFalse(self.wall_gap(400).free((1200,1000,250),(1800,1000,250),30))
        self.assertFalse(self.wall_gap(include_wall=False).free((1200,1000,250),(1800,1000,250),30))
        self.assertFalse(self.wall_gap().free((1200,1000,490),(1800,1000,490),30))


class CorridorAndPortfolio(unittest.TestCase):
    def test_roadmap_preparation_does_not_certify_every_suggested_point(self):
        config=configuration();space=build_spatial_model(scene())
        with patch.object(space,'free',wraps=space.free) as free:
            route=find_corridor(space,(200,300,250),(2800,300,250),profile(config),config,
                                deadline=time.monotonic()+3)
        point_queries=sum(tuple(call.args[0])==tuple(call.args[1]) for call in free.call_args_list)
        self.assertEqual(point_queries,2)
        self.assertEqual(validate_route_geometry(space,route,profile(config))['status'],'VALID_WITH_WARNINGS')
        self.assertIn('preparation_seconds',route)
        self.assertGreater(route['tested_edges'],0)

    def test_distinct_ports_are_tried_before_repeating_one_port_in_another_direction(self):
        config=configuration();space=build_spatial_model(scene())
        req=request('a');alternative=dict(req['source'],id='alternate',xyz_mm=(200,1200,250))
        req['source_alternatives']=[alternative];seen=[]
        def planner(space,variant,*args,**kwargs):
            seen.append(variant['source']['id'])
            return {'status':'NOT_EVALUATED','reason':'fixture'}
        coordinate(space,{'a':{'status':'NOT_EVALUATED'}},[req],config,set(),planner,time.monotonic()+2)
        self.assertEqual(seen[:2],[req['source']['id'],alternative['id']])

    def test_microscopic_bends_are_rejected_before_revit_and_tangent_joints_are_exact(self):
        config=configuration();space=build_spatial_model(scene())
        route={'points_mm':[(200,300,250),(1000,300,250),(1800,300,250.5)]}
        checked=validate_route_geometry(space,route,profile(config))
        self.assertEqual(checked['reason'],'CURVE_BELOW_NATIVE_TOLERANCE')
        pieces=primitives([(200,300,250),(1000,300,250),(1000,1300,250)],50)
        self.assertTrue(tangent_joint(pieces[0],pieces[1]));self.assertTrue(tangent_joint(pieces[1],pieces[2]))
        self.assertFalse(tangent_joint(pieces[2],pieces[0]))
    def test_blocked_primary_port_is_replaced_automatically_even_for_one_terminal(self):
        config=configuration();config['search'].update(max_iterations=1,max_total_seconds=4,max_seconds=1)
        req=request('a',(200,300,250),(2800,1200,250))
        alternative=dict(req['source'],id='alternate',xyz_mm=(200,1200,250))
        req['source_alternatives']=[req['source'],alternative]
        space=build_spatial_model(scene([obstacle('blocked_port',[100,200,0,300,400,500])]))
        result=optimize_multiple_routes(space,[req],profile(config),config)
        route=result['routes']['a'];self.assertTrue(c.usable(route),result)
        self.assertEqual(route['chosen_source'],alternative)
        self.assertEqual(validate_route_geometry(space,route,profile(config))['status'],'VALID_WITH_WARNINGS')

    def test_deferred_connection_geometry_does_not_allow_reusing_one_port(self):
        config=configuration();space=build_spatial_model(scene())
        a=request('a',(200,1000,250),(2800,500,250));b=request('b',(200,1000,250),(2800,1500,250))
        b['source']=copy.deepcopy(a['source'])
        pools={}
        for req,y in ((a,500),(b,1500)):
            route={'points_mm':[req['source']['xyz_mm'],(200,y,250),req['destination']['xyz_mm']],
                   'routing_points_mm':[(200,y,250),req['destination']['xyz_mm']],
                   'connection_validation_pending':True}
            route.update(validate_route_geometry(space,route,profile(config)));pools[req['route_id']]=[route]
        seed={rid:{'status':'NOT_EVALUATED'} for rid in ('a','b')}
        result,_=select_candidates(space,[a,b],pools,config,seed,set(),time.monotonic()+2)
        self.assertEqual(sum(c.usable(r) for r in result.values()),1)

    def test_two_walls_with_offset_passages_and_real_rounded_bends(self):
        config=configuration();config['constraints']['passages']=[
            dict(id='p1',points_mm=[[800,500,250],[1200,500,250]],element_ids=['w1'],radius_mm=30,classification='PASSABLE'),
            dict(id='p2',points_mm=[[1800,1300,250],[2200,1300,250]],element_ids=['w2'],radius_mm=30,classification='PASSABLE')]
        space=build_spatial_model(scene([obstacle('w1',[950,0,0,1050,2000,500],'PASSABLE'),
            obstacle('w2',[1950,0,0,2050,2000,500],'PASSABLE')]),config['constraints'])
        route=find_corridor(space,(200,300,250),(2800,1500,250),profile(config),config,deadline=time.monotonic()+5)
        self.assertIn('points_mm',route,route)
        self.assertEqual({p['id'] for p in route['passages']},{'p1','p2'})
        self.assertEqual(validate_route_geometry(space,route,profile(config))['status'],'VALID_WITH_WARNINGS')

    def test_global_choice_takes_longer_corridor_to_complete_both_routes(self):
        config=configuration();space=build_spatial_model(scene())
        requests=[request('a',(200,1000,250),(2800,1000,250)),request('b',(1500,300,250),(1500,1700,250))]
        paths={'a':[[(200,1000,250),(2800,1000,250)],
                    [(200,1000,250),(200,100,250),(2800,100,250),(2800,1000,250)]],
               'b':[[(1500,300,250),(1500,1700,250)]]}
        pools={}
        for rid,choices in paths.items():
            pools[rid]=[]
            for points in choices:
                route={'points_mm':points};route.update(validate_route_geometry(space,route,profile(config)))
                self.assertTrue(c.usable(route));pools[rid].append(route)
        seed={'a':pools['a'][0],'b':{'status':'NOT_EVALUATED'}}
        result,report=select_candidates(space,requests,pools,config,seed,set(),time.monotonic()+2)
        self.assertTrue(report['accepted']);self.assertEqual(c.score(result,['a','b'])[0],0)
        self.assertGreater(result['a']['length_m'],seed['a']['length_m'])
        # A later automatic pass can combine its new B with A candidates
        # discovered earlier, without searching those corridors again.
        later={'a':{'status':'NOT_EVALUATED'},'b':pools['b'][0]}
        reused,_=coordinate(space,later,requests,config,set(),lambda *args,**kwargs:{'status':'NOT_EVALUATED'},
                            time.monotonic()+2,candidate_bank=pools)
        self.assertEqual(c.score(reused,['a','b'])[0],0)

    def test_reverse_search_returns_source_to_destination_geometry(self):
        config=configuration();space=build_spatial_model(scene());req=request()
        route=plan_route(space,dict(req,search_from_destination=True),profile(config),config)
        self.assertEqual(tuple(route['points_mm'][0]),req['source']['xyz_mm'])
        self.assertEqual(tuple(route['points_mm'][-1]),req['destination']['xyz_mm'])
        self.assertEqual(route['search_direction'],'DESTINATION_TO_SOURCE')

    def test_only_free_equivalent_ports_of_the_same_distributor_are_offered(self):
        catalog,config,destinations=fixture()
        catalog['devices'][1]['connectors'][2]['connected']=True
        routes,*_=topology(catalog,config,destinations)
        for route in routes:
            for alternative in route.get('source_alternatives',[]):
                self.assertFalse(alternative['connected'])
                self.assertEqual(alternative['element_key'],route['source']['element_key'])
                self.assertEqual(alternative['size'],route['source']['size'])
                self.assertNotIn(':main',alternative['id'])

    def test_chosen_port_is_serialized_consistently_in_route_and_request(self):
        catalog,config,destinations=fixture();routes,*_=topology(catalog,config,destinations)
        req=routes[0];alternative=next(p for p in req['source_alternatives'] if p['id']!=req['source']['id'])
        computed={'routes':{req['route_id']:{'status':'NOT_EVALUATED','chosen_source':alternative}},'iterations':[]}
        snapshot={'document_identity':'fixture','geometry_revision':'fixture','regions':[],'obstacles':[]}
        with patch('ARQT_MEP.routing.core.multi_route.optimize_multiple_routes',return_value=computed):
            proposal=service.create_mep_route_proposal(snapshot,catalog,[],destinations,config)
        route=next(r for r in proposal['routes'] if r['route_id']==req['route_id'])
        request_record=next(r for r in proposal['requests'] if r['route_id']==req['route_id'])
        self.assertEqual(route['source'],alternative);self.assertEqual(route['source_id'],alternative['id'])
        self.assertEqual(request_record['source'],alternative)
        service.check(proposal)


if __name__=='__main__':unittest.main()
