import unittest
import copy
from unittest.mock import patch
from test_routing_core import configuration,profile,scene,request
from test_routing_topology import fixture
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core.route_geometry import validate_route_geometry
from ARQT_MEP.routing.core import multi_route
from ARQT_MEP.routing import service


class SelectedRecalculation(unittest.TestCase):
    def test_complete_passages_are_reused_only_for_same_physical_context(self):
        catalog,config,destinations=fixture()
        snapshot={'document_identity':'fixture','geometry_revision':'fixture','regions':[],'obstacles':[]}
        with patch('ARQT_MEP.routing.core.global_planner.solve',return_value={'routes':{},'iterations':[]}):
            prior=service.create_mep_route_proposal(snapshot,catalog,[],destinations,config)
            with patch.object(service,'generate_passages',wraps=service.generate_passages) as generate:
                current=service.create_mep_route_proposal(snapshot,catalog,[],destinations,config,prior)
                generate.assert_not_called()
            self.assertTrue(current['stats']['spatial_queries']['passage_generation_reused'])
            changed=copy.deepcopy(config);changed['clearance_mm']=config['clearance_mm']+1
            with patch.object(service,'generate_passages',wraps=service.generate_passages) as generate:
                service.create_mep_route_proposal(snapshot,catalog,[],destinations,changed,prior)
                generate.assert_called_once()

    def test_service_keeps_equivalent_ports_after_selected_source_changes(self):
        catalog,config,destinations=fixture()
        snapshot={'document_identity':'fixture','geometry_revision':'fixture','regions':[],'obstacles':[]}
        originals={}
        def selected(space,requests,*args,**kwargs):
            result={}
            for req in requests:
                if req.get('source_alternatives'):
                    originals[req['route_id']]={p['id']:copy.deepcopy(p) for p in [req['source']]+req['source_alternatives']}
                    result[req['route_id']]={'status':'NOT_EVALUATED','chosen_source':req['source_alternatives'][0]}
            return {'routes':result,'iterations':[]}
        with patch('ARQT_MEP.routing.core.global_planner.solve',side_effect=selected):
            proposal=service.create_mep_route_proposal(snapshot,catalog,[],destinations,config)
        self.assertTrue(originals)
        for req in proposal['requests']:
            if req['route_id'] not in originals:continue
            actual=[req['source']]+req['source_alternatives']
            self.assertEqual(len(actual),len({p['id'] for p in actual}))
            self.assertEqual({p['id']:p for p in actual},originals[req['route_id']])

    def test_missing_points_is_pending_not_a_key_error(self):
        config=configuration()
        result=validate_route_geometry(build_spatial_model(scene()),{'status':'NOT_EVALUATED'},profile(config))
        self.assertEqual(result['status'],'NOT_EVALUATED')
        self.assertEqual(result['reason'],'NO_ROUTE_GEOMETRY')

    def test_selection_retries_only_target_and_preserves_unresolved_other(self):
        config=configuration();config['search']['max_iterations']=1
        config['constraints']['locked_routes']=['a','b']
        previous={'a':{'status':'NOT_EVALUATED','reason':'SEARCH_LIMIT'},
                  'b':{'status':'NOT_EVALUATED','reason':'TOTAL_TIME_LIMIT'}}
        with patch.object(multi_route,'plan_route',return_value={'status':'NOT_EVALUATED','reason':'SEARCH_LIMIT'}) as planner:
            result=multi_route.optimize_multiple_routes(build_spatial_model(scene()),[request('a'),request('b')],
                profile(config),config,previous,target_route_ids=['a'])
        self.assertEqual(planner.call_count,1)
        self.assertEqual(planner.call_args.args[1]['route_id'],'a')
        self.assertEqual(result['routes']['b']['reason'],'TOTAL_TIME_LIMIT')
        self.assertIn('b',result['ignored_locks'])

    def test_global_recalculation_ignores_empty_locked_geometry(self):
        config=configuration();config['search']['max_iterations']=1
        config['constraints']['locked_routes']=['a']
        with patch.object(multi_route,'plan_route',return_value={'status':'NOT_EVALUATED','reason':'SEARCH_LIMIT'}) as planner:
            result=multi_route.optimize_multiple_routes(build_spatial_model(scene()),[request('a')],
                profile(config),config,{'a':{'status':'NOT_EVALUATED'}})
        self.assertEqual(planner.call_count,1)
        self.assertEqual(result['ignored_locks'],['a'])

    def test_outdated_preserved_route_does_not_block_selected_success(self):
        config=configuration();config['search']['max_iterations']=1
        config['constraints']['locked_routes']=['a','b']
        a=request('a');b=request('b',start=(200,1200,250),end=(2800,1200,250))
        previous={'a':{'status':'NOT_EVALUATED'},'b':{'status':'VALID_WITH_WARNINGS',
            'source':dict(b['source'],xyz_mm=[300,1200,250]),
            'points_mm':[[300,1200,250],[2800,1200,250]]}}
        with patch.object(multi_route,'plan_route',wraps=multi_route.plan_route) as planner:
            result=multi_route.optimize_multiple_routes(build_spatial_model(scene()),[a,b],profile(config),config,
                previous,target_route_ids=['a'])
        self.assertEqual(planner.call_count,1)
        self.assertIn(result['routes']['a']['status'],('VALID','VALID_WITH_WARNINGS'))
        self.assertEqual(result['routes']['b']['reason'],'PRESERVED_ROUTE_OUTDATED')
        certified=result['planner_state']['incumbent']['network']
        self.assertIn('a',certified)
        self.assertNotIn('b',certified)

    def test_preserved_route_with_changed_connector_is_marked_outdated(self):
        config=configuration();config['search']['max_iterations']=1
        a,b=request('a'),request('b')
        previous={'b':{'status':'VALID_WITH_WARNINGS','source':dict(b['source'],xyz_mm=[300,500,250]),
                       'points_mm':[[300,500,250],[2800,500,250]]}}
        with patch.object(multi_route,'plan_route',return_value={'status':'NOT_EVALUATED','reason':'SEARCH_LIMIT'}):
            result=multi_route.optimize_multiple_routes(build_spatial_model(scene()),[a,b],profile(config),config,
                                                       previous,target_route_ids=['a'])
        self.assertEqual(result['routes']['b']['status'],'NOT_EVALUATED')
        self.assertEqual(result['routes']['b']['reason'],'PRESERVED_ROUTE_OUTDATED')

    def test_service_passes_selection_and_respects_edited_diameter(self):
        catalog,config,destinations=fixture()
        catalog['configured_model']={'flexible_duct':{'sizing_diameters_mm':[75,90,100,125],'transition_type_id':123}}
        rid='route:t0:port'
        config['route_overrides'][rid]={'section':{'nominal_diameter_mm':125,'outer_diameter_mm':125}}
        snapshot={'document_identity':'fixture','geometry_revision':'fixture','regions':[],'obstacles':[]}
        with patch('ARQT_MEP.routing.core.multi_route.optimize_multiple_routes',return_value={'routes':{},'iterations':[]}) as optimizer:
            proposal=service.create_mep_route_proposal(snapshot,catalog,[],destinations,config,route_ids=[rid])
        self.assertEqual(optimizer.call_args.kwargs['target_route_ids'],[rid])
        route=next(r for r in proposal['routes'] if r['route_id']==rid)
        self.assertEqual(route['section']['nominal_diameter_mm'],125)
        self.assertEqual(route['sizing']['method'],'MANUAL_ROUTE_SECTION')


if __name__=='__main__':unittest.main()
