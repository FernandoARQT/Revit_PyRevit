import copy
import math
import time
import unittest
from unittest.mock import patch
from test_routing_core import configuration,profile,request,scene
from ARQT_MEP.routing.pressure import assess
from ARQT_MEP.routing import native_feedback
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core.route_geometry import validate_route_geometry
from ARQT_MEP.routing.core.refinement import refine
from ARQT_MEP.routing.core.integer_master import solve


class AdvancedRouting(unittest.TestCase):
    def test_integer_master_learns_exclusion_instead_of_returning_colliding_routes(self):
        options={'a':[{'status':'VALID','cost':{'total':1}},{'status':'VALID','cost':{'total':5}}],
                 'b':[{'status':'VALID','cost':{'total':1}}]}
        chosen,report=solve(options,['a','b'],lambda a,i,b,j:i==0 and j==0,time.monotonic()+3)
        self.assertEqual(chosen,{'a':1,'b':0});self.assertGreater(report['cuts'],0)

    def test_unknown_pressure_is_not_reported_as_zero(self):
        result=assess({'section':{'nominal_diameter_mm':100}},request(),{})
        self.assertEqual(result['status'],'UNVERIFIED');self.assertNotIn('pressure_loss_pa',result)

    def test_referenced_pressure_table_is_interpolated_and_negative_data_rejected(self):
        route={'section':{'nominal_diameter_mm':100},'length_m':5,'bends':1}
        req=dict(request(),airflow_lps=10)
        config={'pressure_model':{'diameters':{'100':{'reference':'manufacturer/table/test',
            'flow_loss_table':[[5,1],[15,3]],'bend_k':.5}}}}
        result=assess(route,req,config)
        expected=10+.5*1.2*(.01/(math.pi*.1**2/4))**2/2
        self.assertAlmostEqual(result['pressure_loss_pa'],expected)
        config['pressure_model']['diameters']['100']['bend_k']=-1
        with self.assertRaises(ValueError):assess(route,req,config)

    def test_continuous_refinement_preserves_endpoints_and_exact_validity(self):
        config=configuration();p=profile(config);space=build_spatial_model(scene())
        route={'points_mm':[(200,500,250),(1500,1100,250),(2800,500,250)]}
        route.update(validate_route_geometry(space,route,p));before=route['cost']['total']
        result=refine(space,route,p,time.monotonic()+1)
        self.assertLessEqual(result['cost']['total'],before)
        self.assertEqual(result['points_mm'][0],route['points_mm'][0])
        self.assertEqual(result['points_mm'][-1],route['points_mm'][-1])
        self.assertEqual(validate_route_geometry(space,result,p)['status'],'VALID_WITH_WARNINGS')

    def test_native_feedback_retries_failed_geometry_without_authorizing_changes(self):
        config=configuration();config['search']['max_total_seconds']=3
        failed={'configuration':copy.deepcopy(config),'routes':[{'route_id':'r','points_mm':[[0,0,0],[100,0,0]],
            'native_status':'NOT_EVALUATED','native_checks':{'errors':[{'reason':'NATIVE_GEOMETRY_NOT_EVALUATED'}]}}],
            'stats':{},'summary':{'valid':0}}
        good=copy.deepcopy(failed);good['routes'][0]['native_status']='VALID';good['summary']['valid']=1
        with patch.object(native_feedback.service,'create_mep_route_proposal',side_effect=[failed,good]) as creator:
            result=native_feedback.design({}, {}, [], [],config,None,lambda p:p)
        self.assertEqual(result['summary']['valid'],1)
        retry=creator.call_args.args[4]
        self.assertEqual(retry['constraints']['rejected_paths']['r'],[[[0,0,0],[100,0,0]]])
        self.assertTrue(result['stats']['native_feedback'][0]['accepted'])

    def test_native_feedback_resumes_routes_with_no_geometric_candidate(self):
        config=configuration();config['search']['max_total_seconds']=3
        initial={'configuration':copy.deepcopy(config),'routes':[{'route_id':'r','status':'NOT_EVALUATED',
            'native_status':'NOT_EVALUATED','points_mm':[]}],'stats':{},'summary':{'valid':0,'unresolved':1,'conditional':0}}
        solved=copy.deepcopy(initial);solved['routes'][0].update(status='VALID',native_status='VALID')
        solved['summary'].update(valid=1,unresolved=0)
        with patch.object(native_feedback.service,'create_mep_route_proposal',side_effect=[initial,solved]) as creator:
            result=native_feedback.design({}, {}, [], [],config,None,lambda p:p)
        self.assertEqual(creator.call_count,2)
        self.assertEqual(creator.call_args.args[-1],['r'])
        self.assertFalse(creator.call_args.args[4]['constraints']['rejected_paths'])
        self.assertEqual(result['summary']['unresolved'],0)


if __name__=='__main__':unittest.main()
