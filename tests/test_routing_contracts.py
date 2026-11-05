"""Contracts, bounded coordination and regression cases independent of Revit."""
import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path
from test_routing_core import configuration,profile,scene,obstacle,request
from ARQT_MEP.routing import settings,service
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core.astar_3d import find_route_3d,SparseGrid
from ARQT_MEP.routing.core.multi_route import plan_route,optimize_multiple_routes,route_alternatives
from ARQT_MEP.routing.core.passages import generate_passages
from ARQT_MEP.routing.systems.ventilation_radial import VentilationRadialSystem


class ExternalConfiguration(unittest.TestCase):
    def test_csv_preserves_all_previous_corporate_parameters(self):
        root=Path(settings.__file__).parent/'config'
        previous=json.loads((root/'ventilation_radial.json').read_text(encoding='utf8'))
        current=settings.read_configuration(root/'ventilation_radial.csv')
        for key,value in previous.items():self.assertEqual(current[key],value,key)
        self.assertEqual(current['flexible_duct']['available_diameters_mm'],[75,93,125])
        self.assertEqual(current['flexible_duct']['instance_parameter'],'Diámetro')
        self.assertEqual(current['flexible_duct']['commercial_size_mapping'],{})

    def test_partial_names_ignore_case_accents_and_separators(self):
        for name,expected in [('AC-Paquete TECHO_40','FALSE_CEILING'),('MU-Hormigón armado','STRUCTURE'),
                              ('A-TA-Trasdosado 8cm','PASSABLE'),('A-TA-Tabique yeso','PASSABLE')]:
            self.assertEqual(settings.identify_construction(name,settings.DEFAULT),expected)
        self.assertIsNone(settings.identify_construction('Sin clasificación',settings.DEFAULT))

    def test_name_rules_are_read_from_configuration(self):
        config=settings.merge(settings.DEFAULT,{'model_identification':{'construction':[{'contains':'New ceiling','meaning':'FALSE_CEILING'}]}})
        self.assertEqual(settings.identify_construction('A-NEW CEILING-20cm',config),'FALSE_CEILING')
        self.assertIsNone(settings.identify_construction('Paquete techo',config))

    def test_new_configuration_rejects_invalid_dimensions(self):
        for change in ({'flexible_duct':{'available_diameters_mm':[75,75]}},
                       {'flexible_duct':{'available_diameters_mm':[float('nan')]}},
                       {'distributors':{'branch_capacities':[0]}},
                       {'sizing':{'max_velocity_mps':0}}):
            with self.assertRaises(ValueError):settings.validate(change)
        self.assertTrue(settings.validate({'main_connection':{'automatic_diameter_change':True}})['main_connection']['automatic_diameter_change'])

    def test_reject_ambiguous_csv_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.csv'
            with path.open('w',encoding='utf-8-sig',newline='') as stream:
                csv.writer(stream,delimiter=';').writerows([['key','value_json'],['version','1'],['version','2']])
            with self.assertRaises(ValueError):settings.read_configuration(path)

    def test_corporate_identification_is_not_shadowed_by_personal_preferences(self):
        self.assertNotIn('flexible_duct',settings._personal(settings.DEFAULT))
        self.assertNotIn('model_identification',settings._personal(settings.DEFAULT))

    def test_profile_exposes_conditional_creation_without_hydraulic_sizing(self):
        capabilities=service.get_mep_system_profiles()[0]['capabilities']
        self.assertTrue(capabilities['geometric_routing'])
        self.assertFalse(capabilities['automatic_hydraulic_sizing'])
        self.assertTrue(capabilities['native_connected_network_creation'])
        self.assertTrue(capabilities['native_creation_requires_approved_routes'])


class Contracts(unittest.TestCase):
    def setUp(self):self.s=configuration();self.p=profile(self.s)

    def system(self,catalog=None):return VentilationRadialSystem(catalog or {'devices':[],'terminals':[]},self.s)

    def test_missing_connector_never_becomes_a_manual_point(self):
        with self.assertRaisesRegex(ValueError,'Conector real ausente'):
            self.system().resolve({'port_id':'missing','xyz_mm':[200,500,250],'network_type':'SUPPLY'})

    def test_network_role_must_be_explicit(self):
        with self.assertRaises(ValueError):self.system().resolve({'xyz_mm':[1,2,3]})

    def test_missing_flow_never_guessed(self):
        with self.assertRaisesRegex(ValueError,'Flow'):
            self.system().requests([], [{'xyz_mm':[100,100,100],'network_type':'SUPPLY'}])

    def test_flow_is_read_from_instance_catalog(self):
        port={'id':'port','xyz_mm':[2800,500,250],'connected':False,'provenance':'REVIT_CONNECTOR'}
        catalog={'devices':[],'terminals':[{'id':'terminal','connectors':[port],'airflow_lps':9.25,
                  'suggested_network_type':'SUPPLY','network_assignment_requires_review':False}]}
        requests,_=self.system(catalog).requests([{'id':'origin','xyz_mm':[200,500,250],'network_type':'SUPPLY','kind':'DISTRIBUTOR'}],
                  [{'terminal_id':'terminal','port_id':'port','network_type':'SUPPLY','airflow_lps':999}])
        self.assertEqual(requests[0]['airflow_lps'],9.25)

    def test_one_terminal_cannot_double_flow_through_two_ports(self):
        destinations=[{'terminal_id':'terminal'}]*2
        with self.assertRaisesRegex(ValueError,'único conector'):self.system().requests([],destinations)

    def test_supply_extract_do_not_share_ports(self):
        requests,_=self.system().requests([{'xyz_mm':[200,500,250],'network_type':'SUPPLY','kind':'DISTRIBUTOR'}],
               [{'xyz_mm':[2800,500,250],'network_type':'EXTRACT','airflow_lps':8}])
        self.assertEqual(requests[0]['error'],'MISSING_DISTRIBUTOR_PORT')

    def test_same_source_not_fanned_out(self):
        requests,_=self.system().requests([{'xyz_mm':[200,500,250],'network_type':'SUPPLY','kind':'DISTRIBUTOR'}],
               [{'xyz_mm':[2800,y,250],'network_type':'SUPPLY','airflow_lps':8} for y in (500,1000)])
        self.assertEqual(sum(r.get('error')=='MISSING_DISTRIBUTOR_PORT' for r in requests),1)

    def test_outdated_proposal_rejected(self):
        p=service.seal({'document_identity':'a','geometry_revision':'old'})
        with self.assertRaisesRegex(ValueError,'OUTDATED'):service.assert_current({'document_identity':'a','geometry_revision':'new'},p)

    def test_modified_proposal_rejected(self):
        p=service.seal({'document_identity':'a','geometry_revision':'old'});p['geometry_revision']='new'
        with self.assertRaises(ValueError):service.check(p)

    def test_cancel_before_search(self):
        r=find_route_3d(build_spatial_model(scene()),(200,500,250),(2800,500,250),self.p,self.s['search'],100,cancelled=lambda:True)
        self.assertEqual(r['reason'],'CANCELLED')

    def test_budget_is_not_geometric_impossibility(self):
        search=dict(self.s['search'],max_nodes=1)
        r=find_route_3d(build_spatial_model(scene()),(200,500,250),(2800,500,250),self.p,search,100)
        self.assertEqual(r['status'],'NOT_EVALUATED');self.assertEqual(r['reason'],'SEARCH_LIMIT')

    def test_unknown_wall_cannot_be_bypassed_by_forged_portal(self):
        data=scene([obstacle('unknown',[1400,0,0,1600,2000,500],'UNKNOWN')])
        space=build_spatial_model(data)
        portal={'id':'fake','points_mm':[[1200,500,250],[1800,500,250]],'element_ids':['unknown'],'radius_mm':100,'classification':'PASSABLE'}
        self.assertEqual(SparseGrid(space,(200,500,250),(2800,500,250),100,self.p,[portal]).portals,[])

    def test_locked_route_geometry_preserved(self):
        space=build_spatial_model(scene());req=request('locked')
        original=plan_route(space,req,self.p,self.s);self.s['constraints']['locked_routes']=['locked']
        result=optimize_multiple_routes(space,[req,request('other',(200,1200,250),(2800,1200,250))],self.p,self.s,{'locked':original})
        self.assertEqual(result['routes']['locked']['points_mm'],original['points_mm'])
        self.assertEqual(result['reserved_count'],2)

    def test_reorder_and_ripup_recalculate_bounded(self):
        requests=[request('a'),request('b',(200,1200,250),(2800,1200,250))]
        result=optimize_multiple_routes(build_spatial_model(scene()),requests,self.p,self.s)
        self.assertEqual(len(result['iterations']),2)
        self.assertNotEqual(result['iterations'][0]['order'],result['iterations'][1]['order'])
        self.assertTrue(result['iterations'][1]['rip_up'])

    def test_selected_diameter_changes_physical_viability(self):
        space=build_spatial_model(scene(height=200));req=request('diam',(200,500,100),(2800,500,100))
        small=plan_route(space,req,self.p,self.s)
        self.s['route_overrides']={'diam':{'section':{'nominal_diameter_mm':200,'outer_diameter_mm':200},'bend_radius_mm':150}}
        large=plan_route(space,req,self.p,self.s)
        self.assertEqual(small['status'],'VALID_WITH_WARNINGS');self.assertNotEqual(large['status'],'VALID_WITH_WARNINGS')

    def test_coincident_endpoint_not_empty_valid_route(self):
        r=plan_route(build_spatial_model(scene()),request(start=(200,500,250),end=(200,500,250)),self.p,self.s)
        self.assertNotIn(r['status'],('VALID','VALID_WITH_WARNINGS'))

    def test_automatic_partition_portals_are_real_interventions(self):
        wall=obstacle('partition',[1400,0,0,1600,2000,500],'PASSABLE')
        wall.update(wall_axis_mm=[[1500,0,0],[1500,2000,0]],wall_thickness_mm=200)
        space=build_spatial_model(scene([wall]));portals=generate_passages(space,[request()],self.s)
        self.assertTrue(portals)
        self.assertTrue(all(p['element_ids']==['partition'] and p['required_dimension_mm']==60 for p in portals))

    def test_conditional_candidates_are_proposed_without_evaluable_scope(self):
        wall=obstacle('struct',[1400,0,0,1600,2000,500],'CONDITIONAL')
        wall.update(wall_axis_mm=[[1500,0,0],[1500,2000,0]],wall_thickness_mm=200)
        space=build_spatial_model(scene([wall]));portals=generate_passages(space,[request()],self.s)
        self.assertTrue(portals)
        self.assertTrue(all(p['classification']=='CONDITIONAL' and p['approval_status']=='PENDING_TECHNICAL_APPROVAL' for p in portals))
        from ARQT_MEP.routing.core.astar_3d import SparseGrid
        self.assertFalse(SparseGrid(space,(200,500,250),(2800,500,250),100,self.p,portals,'A').portals)
        self.assertTrue(SparseGrid(space,(200,500,250),(2800,500,250),100,self.p,portals,'B').portals)

    def test_useful_alternatives_have_different_corridors(self):
        space=build_spatial_model(scene([obstacle('central',[1200,800,0,1800,1200,500])]))
        req=request(start=(200,1000,250),end=(2800,1000,250))
        options=route_alternatives(space,req,self.p,self.s)
        self.assertGreaterEqual(len(options),2,options)
        self.assertNotEqual(options[0]['points_mm'],options[1]['points_mm'])

    def test_portal_never_crosses_a_forbidden_volume(self):
        self.s['constraints']['forbidden_regions']=[{'box_mm':[1450,0,0,1550,2000,500]}]
        self.s['constraints']['passages']=[{'id':'pass','points_mm':[[1200,500,250],[1800,500,250]],
             'element_ids':['wall'],'radius_mm':100,'classification':'PASSABLE'}]
        space=build_spatial_model(scene([obstacle('wall',[1400,0,0,1600,2000,500],'PASSABLE')]),self.s['constraints'])
        result=plan_route(space,request(),self.p,self.s)
        self.assertNotIn(result['status'],('VALID','VALID_WITH_WARNINGS'))

    def test_approval_requires_a_traceable_technical_record(self):
        with self.assertRaisesRegex(ValueError,'responsable'):
            settings.validate({'constraints':{'approved_passages':['unauthorized']}})

    def test_cache_never_reuses_geometry_after_content_changes(self):
        a=build_spatial_model(scene());b=build_spatial_model(scene([obstacle('new',[1000,0,0,2000,2000,500])]))
        self.assertTrue(a.free((200,500,250),(2800,500,250),30))
        self.assertFalse(b.free((200,500,250),(2800,500,250),30))


if __name__=='__main__':unittest.main()
