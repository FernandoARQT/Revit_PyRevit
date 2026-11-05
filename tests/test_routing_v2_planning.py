import copy
import json
import time
import unittest
from test_routing_core import scene,configuration,profile,request,obstacle
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core.topology_graph import TopologyGraph
from ARQT_MEP.routing.core.portal_graph import attach
from ARQT_MEP.routing.core.connectivity_analysis import analyze
from ARQT_MEP.routing.core.global_planner import prepare
from ARQT_MEP.routing.core.incremental_roadmap import search
from ARQT_MEP.routing.core.route_geometry import validate_route_geometry


class ConstructiveGraph(unittest.TestCase):
    def test_one_ceiling_spanning_wall_requires_portal_between_components(self):
        config=configuration();p=profile(config)
        wall=obstacle('partition',[1400,0,0,1600,2000,500],'PASSABLE')
        wall.update(wall_axis_mm=[[1500,0,0],[1500,2000,0]],wall_thickness_mm=200)
        space=build_spatial_model(scene([wall]))
        graph=TopologyGraph(space,30)
        self.assertFalse(analyze(graph,(200,500,250),(2800,500,250))['corridors'])
        config['constraints']['passages']=[{'id':'opening','element_ids':['partition'],
            'points_mm':[[1300,500,250],[1700,500,250]],'radius_mm':30,'classification':'PASSABLE'}]
        attach(graph,(200,500,250),(2800,500,250),p,config)
        paths=analyze(graph,(200,500,250),(2800,500,250))['corridors']
        self.assertTrue(paths)
        self.assertEqual(paths[0]['portal_ids'],['opening'])
        self.assertEqual(paths[0]['region_indices'],[0])

    def test_automatic_search_discovers_structural_design_but_never_approves_it(self):
        from ARQT_MEP.routing.core.multi_route import optimize_multiple_routes
        config=configuration();config['search'].update(max_total_seconds=2,max_iterations=2)
        config['coordination'].update(incremental_enabled=False,iris_gcs_enabled=False,roadmap_enabled=False)
        wall=obstacle('wall',[1400,0,0,1600,2000,500],'CONDITIONAL')
        portal={'id':'conditional','element_ids':['wall'],'points_mm':[[1300,500,250],[1700,500,250]],
            'radius_mm':30,'classification':'CONDITIONAL','evaluable_zone':'wall'}
        config['constraints']['passages']=[portal]
        space=build_spatial_model(scene([wall]),config['constraints'])
        result=optimize_multiple_routes(space,[request()],profile(config),config)
        route=result['routes']['r'];self.assertEqual(route['status'],'CONDITIONAL',route)
        self.assertFalse(config['constraints']['approved_passages'])
        self.assertTrue(any(i['scenario']=='B' for i in result['iterations']))

    def test_oriented_docking_reports_parallel_ceiling_turn_deficit(self):
        from ARQT_MEP.routing.core.connector_docking import analyze as docking
        config=configuration();config.update(clearance_mm=15,bend_radius_mm=125)
        config['section']['outer_diameter_mm']=90
        port={'id':'terminal','xyz_mm':[500,500,45],'direction':[0,0,1]}
        result=docking(build_spatial_model(scene(height=195)),port,profile(config),config)
        check=result['local_turn_checks'][0]
        self.assertAlmostEqual(check['deficit_mm'],35)
        self.assertEqual(check['status'],'LOCAL_QUARTER_TURN_CLEARANCE_CONFLICT')
        self.assertEqual(result['validation_status'],'DOCKING_NOT_CERTIFIED')

    def test_capacity_does_not_stack_two_large_tubes_in_thin_camera(self):
        from ARQT_MEP.routing.core.corridor_capacity import estimate
        report=estimate(240,200,{'a':60,'b':60,'c':60})
        self.assertEqual(len(report['lanes']),2)
        self.assertEqual(report['unplaced_routes'],['c'])
        a,b=report['lanes'];self.assertGreaterEqual(abs(a['center_cross_section_mm'][0]-b['center_cross_section_mm'][0]),120)

    def test_modes_and_custom_budgets_are_explicit(self):
        from ARQT_MEP.routing.settings import validate
        self.assertEqual(validate({})['search']['max_total_seconds'],240)
        for mode,seconds in [('benchmark',120),('standard',240),('deep',600),('exhaustive',1000)]:
            self.assertEqual(validate({'routing_mode':mode})['search']['max_total_seconds'],seconds)
        self.assertEqual(validate({'routing_mode':'deep','search':{'max_total_seconds':180}})['search']['max_total_seconds'],180)

    def test_adjacent_regions_and_real_gap_are_distinguished(self):
        data=scene();data['regions']=[{'box_mm':[0,0,0,1500,2000,500]}, {'box_mm':[1500,0,0,3000,2000,500]}]
        graph=TopologyGraph(build_spatial_model(data),30)
        self.assertTrue(analyze(graph,(200,500,250),(2800,500,250))['corridors'])
        data['regions'][1]['box_mm'][0]=1600
        report=analyze(TopologyGraph(build_spatial_model(data),30),(200,500,250),(2800,500,250))
        self.assertFalse(report['corridors']);self.assertFalse(report['physical_infeasibility_proven'])

    def test_profile_changes_connectivity_in_thin_plenum(self):
        data=scene(height=100);data['regions']=[{'box_mm':[0,0,0,1500,2000,100]}, {'box_mm':[1500,0,0,3000,2000,100]}]
        self.assertTrue(analyze(TopologyGraph(build_spatial_model(data),30),(200,500,50),(2800,500,50))['corridors'])
        self.assertFalse(analyze(TopologyGraph(build_spatial_model(data),60),(200,500,50),(2800,500,50))['corridors'])

    def test_all_ten_ports_accounted_before_any_routes_are_reserved(self):
        config=configuration();req=request();req['source_alternatives']=[dict(req['source'],id='port'+str(i),xyz_mm=[200,200+i*100,250]) for i in range(9)]
        space=build_spatial_model(scene());report=prepare(space,[req],config,time.monotonic()+2)
        self.assertEqual(len(report['routes']['r']['ports']),10)
        self.assertTrue(all(p['status']=='TOPOLOGICAL_CANDIDATES' for p in report['routes']['r']['ports']))
        self.assertFalse(space.reservations)

    def test_budget_reports_unvisited_ports_instead_of_disappearing(self):
        req=request();req['source_alternatives']=[dict(req['source'],id='late')]
        report=prepare(build_spatial_model(scene()),[req],configuration(),time.monotonic()-1)
        self.assertEqual([p['status'] for p in report['routes']['r']['ports']],['PENDING_BUDGET']*2)


class JointCorridors(unittest.TestCase):
    def test_constructive_cost_changes_preferred_station_without_authorizing_structure(self):
        from ARQT_MEP.routing.core.corridor_assignment import assign
        from ARQT_MEP.routing.construction_policy import intervention_cost
        space,reqs,config=self.fixture()
        near,far=config['constraints']['passages']
        near.update(intervention_cost(['Concrete']))
        far.update(intervention_cost(['Gypsum']))
        result=assign(space,reqs[:1],config,time.monotonic()+2)
        self.assertEqual(result['selected']['a']['corridor']['portal_ids'],['p1500'])
        near['equivalent_length_m']=0
        result=assign(space,reqs[:1],config,time.monotonic()+2)
        self.assertEqual(result['selected']['a']['corridor']['portal_ids'],['p500'])
        self.assertFalse(config['constraints']['approved_passages'])
        self.assertIsNone(intervention_cost(['Concrete'],'IMPASSABLE_ROOF'))
        self.assertGreater(intervention_cost([],'HOLLOW_CORE_SLAB')['equivalent_length_m'],4)

    def fixture(self):
        config=configuration()
        wall=obstacle('wall',[1400,0,0,1600,2000,500],'PASSABLE')
        wall.update(wall_axis_mm=[[1500,0,0],[1500,2000,0]],wall_thickness_mm=200)
        config['constraints']['passages']=[{'id':'p'+str(y),'element_ids':['wall'],
            'points_mm':[[1300,y,250],[1700,y,250]],'radius_mm':30,'classification':'PASSABLE'} for y in (500,1500)]
        reqs=[request('a'),request('b')]
        # Both initially want exactly the same source and crossing.
        reqs[1]['source']=copy.deepcopy(reqs[0]['source'])
        for req in reqs:
            req['source_alternatives']=[dict(req['source'],id='second',xyz_mm=[200,600,250])]
        space=build_spatial_model(scene([wall]),config['constraints'])
        prepare(space,reqs,config,time.monotonic()+3)
        return space,reqs,config

    def test_joint_plan_assigns_distinct_ports_and_separated_wall_stations(self):
        from ARQT_MEP.routing.core.corridor_assignment import assign
        space,reqs,config=self.fixture()
        result=assign(space,reqs,config,time.monotonic()+3)
        self.assertTrue(result['completed'],result)
        self.assertEqual(result['score'][:2],[0,0],result)
        self.assertEqual(len({r['source']['id'] for r in reqs}),2)
        chosen=[r['global_corridor_preference']['corridor']['portal_ids'] for r in reqs]
        self.assertEqual(len({p[0] for p in chosen}),2)
        self.assertFalse(space.reservations)
        self.assertTrue(all(len(r['source_alternatives'])==1 for r in reqs))

    def test_expired_assignment_does_not_discard_ports_or_claim_completion(self):
        from ARQT_MEP.routing.core.corridor_assignment import assign
        space,reqs,config=self.fixture();before=copy.deepcopy(reqs)
        result=assign(space,reqs,config,time.monotonic()-1)
        self.assertFalse(result['completed'])
        self.assertEqual(reqs,before)


class LazyMap(unittest.TestCase):
    def test_static_rejection_reasons_survive_checkpoint_merge_and_remapping(self):
        from ARQT_MEP.routing.core.incremental_roadmap import Roadmap,export_maps,restore_maps
        space=build_spatial_model(scene());graph=Roadmap();graph.add((1,2,3));graph.add((4,5,6))
        graph.static[(0,1)]=False;graph.rejections=[{'edge':[0,1],'reason':'STATIC_ENVELOPE_BLOCKED'}]
        space.roadmap_cache['fixture']=graph;checkpoint=json.loads(json.dumps(export_maps(space)))
        fresh=build_spatial_model(scene());other=Roadmap();other.add((99,99,99));fresh.roadmap_cache['fixture']=other
        restore_maps(fresh,checkpoint);restore_maps(fresh,checkpoint)
        self.assertFalse(other.static[(1,2)])
        self.assertEqual(other.rejections,[{'edge':[1,2],'reason':'STATIC_ENVELOPE_BLOCKED'}])

    def test_checkpoint_survives_json_and_reuses_verified_static_edges(self):
        from ARQT_MEP.routing.core.incremental_roadmap import export_maps,restore_maps
        config=configuration();p=profile(config);space=build_spatial_model(scene())
        search(space,(200,500,250),(2800,500,250),p,config,deadline=time.monotonic()+1)
        checkpoint=json.loads(json.dumps(export_maps(space)))
        space.roadmap_cache.clear()
        fresh=build_spatial_model(scene());restore_maps(fresh,checkpoint)
        result=search(fresh,(200,500,250),(2800,500,250),p,config,deadline=time.monotonic()+1)
        self.assertEqual(result['status'],'VALID_WITH_WARNINGS',result)
        self.assertGreater(result['static_edges_reused'],0)

    def test_all_five_viable_ports_get_detailed_jobs_including_beyond_first_three(self):
        from ARQT_MEP.routing.core.portfolio import parallel_candidates
        from unittest.mock import Mock
        req=request();req['source_alternatives']=[dict(req['source'],id='port'+str(i),xyz_mm=[200,200+i*100,250]) for i in range(4)]
        workers=Mock();workers.run.return_value=None
        parallel_candidates(workers,build_spatial_model(scene()),[req],{},configuration(),set(),time.monotonic()+2,None)
        jobs=workers.run.call_args.args[2]
        self.assertEqual({j['request']['source']['id'] for j in jobs},{req['source']['id']}|{'port'+str(i) for i in range(4)})

    def test_shared_checked_edges_reused_but_new_reservation_is_respected(self):
        config=configuration();p=profile(config);space=build_spatial_model(scene())
        first=search(space,(200,500,250),(2800,500,250),p,config,deadline=time.monotonic()+2)
        self.assertEqual(first['status'],'VALID_WITH_WARNINGS',first)
        second=search(space,(200,500,250),(2800,500,250),p,config,deadline=time.monotonic()+2)
        self.assertGreater(second['static_edges_reused'],0)
        space.reserve('existing',[(200,500,250),(2800,500,250)],30)
        third=search(space,(200,500,250),(2800,500,250),p,config,deadline=time.monotonic()+.3)
        self.assertEqual(third['status'],'NOT_EVALUATED',third)

    def test_static_edge_cache_does_not_survive_new_forbidden_region(self):
        config=configuration();p=profile(config);data=scene();space=build_spatial_model(data)
        search(space,(200,500,250),(2800,500,250),p,config,deadline=time.monotonic()+1)
        config['constraints']['forbidden_regions']=[{'box_mm':[1400,0,0,1600,2000,500]}]
        changed=build_spatial_model(data,config['constraints'])
        result=search(changed,(200,500,250),(2800,500,250),p,config,deadline=time.monotonic()+.5)
        self.assertEqual(result['status'],'NOT_EVALUATED',result)

    def test_bent_candidate_avoids_solid_and_is_independently_checked(self):
        config=configuration();p=profile(config)
        space=build_spatial_model(scene([obstacle('block',[1200,700,0,1800,1300,500])]))
        result=search(space,(200,1000,250),(2800,1000,250),p,config,deadline=time.monotonic()+3)
        self.assertIn(result['status'],('VALID','VALID_WITH_WARNINGS'),result)
        self.assertEqual(validate_route_geometry(space,result,p)['status'],result['status'])
        self.assertTrue(any(y<670 or y>1330 for x,y,z in result['points_mm']))


class SamplingDiscovery(unittest.TestCase):
    def test_reverse_search_keeps_topological_guide_in_reverse_orientation(self):
        from ARQT_MEP.routing.core import multi_route
        from unittest.mock import patch
        req=request();req['search_from_destination']=True
        req['topological_corridors']={req['source']['id']:[{'region_indices':[0,1],
            'topology_nodes':[3,8],'portal_ids':['door'],
            'edges':[{'kind':'PORTAL','portal_id':'door','points_mm':[[100,0,0],[200,0,0]]}]}]}
        original=multi_route.plan_route
        with patch.object(multi_route,'plan_route',return_value={'status':'NOT_EVALUATED'}) as nested:
            original(build_spatial_model(scene()),req,profile(configuration()),configuration())
        reversed_request=nested.call_args.args[1]
        corridor=reversed_request['topological_corridors'][req['destination']['id']][0]
        self.assertEqual(corridor['region_indices'],[1,0])
        self.assertEqual(corridor['edges'][0]['points_mm'],[[200,0,0],[100,0,0]])

    def test_native_rejected_path_cannot_survive_as_an_incumbent(self):
        from ARQT_MEP.routing.core.multi_route import optimize_multiple_routes,plan_route
        from ARQT_MEP.routing.core.route_geometry import rejected_path
        config=configuration();space=build_spatial_model(scene());req=request()
        prior=plan_route(space,req,profile(config),config)
        config['constraints']['rejected_paths']={'r':[list(reversed(prior['points_mm']))]}
        self.assertTrue(rejected_path(prior,'r',config))
        result=optimize_multiple_routes(space,[req],profile(config),config,{'r':prior},
            absolute_deadline=time.monotonic()-1)
        self.assertNotIn(result['routes'].get('r',{}).get('status'),('VALID','VALID_WITH_WARNINGS'))

    def test_diagnostic_preserves_unmeasured_facts_and_specific_budget_cause(self):
        from ARQT_MEP.routing.core.routing_diagnostics import build
        report=build({'status':'NOT_EVALUATED','reason':'NO_CERTIFIED_ROUTE_IN_SEARCH_LIMITS',
            'attempts':[{'method':'INCREMENTAL_LAZY_ROADMAP','reason':'LAZY_ROADMAP_TIME_LIMIT','seconds':2.5}]},request())
        self.assertEqual(report['final_status'],'TIME_BUDGET_EXHAUSTED')
        self.assertEqual(report['time_by_algorithm_seconds'],{'INCREMENTAL_LAZY_ROADMAP':2.5})
        self.assertIsNone(report['admissible_dockings'])
        self.assertFalse(report['physical_infeasibility_proven'])

    def test_native_reducer_endpoints_remove_backwards_spline_points(self):
        from ARQT_MEP.routing.core.flex_points import fitted_points
        points=[[0,0,0],[60,0,0],[500,0,0],[900,0,0],[940,0,0],[1000,0,0]]
        result=fitted_points(points,[200,0,0],[800,0,0],[1,0,0],[1,0,0])
        self.assertEqual(result,[(200,0,0),(500,0,0),(800,0,0)])

    def test_fixed_seed_finds_reproducible_curved_route_around_real_solid(self):
        from ARQT_MEP.routing.core.rrt_connect import discover
        config=configuration();p=profile(config)
        space=build_spatial_model(scene([obstacle('block',[1200,700,0,1800,1300,500])]))
        results=[discover(space,(200,1000,250),(2800,1000,250),p,config,time.monotonic()+4,seed=0) for _ in range(2)]
        self.assertTrue(all(r['status']=='VALID_WITH_WARNINGS' for r in results),results)
        self.assertEqual(results[0]['points_mm'],results[1]['points_mm'])
        self.assertEqual(validate_route_geometry(space,results[0],p)['status'],'VALID_WITH_WARNINGS')

    def test_sampling_never_crosses_full_hard_barrier(self):
        from ARQT_MEP.routing.core.rrt_connect import discover
        config=configuration();wall=obstacle('roof',[1400,0,0,1600,2000,500]);wall['hard_forbidden']=True
        space=build_spatial_model(scene([wall]))
        result=discover(space,(200,1000,250),(2800,1000,250),profile(config),config,time.monotonic()+.2,seed=0)
        self.assertEqual(result['status'],'NOT_EVALUATED')


if __name__=='__main__':unittest.main()
