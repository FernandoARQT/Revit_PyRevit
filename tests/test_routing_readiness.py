import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ARQ-T.extension/lib'))
from ARQT_MEP.routing.readiness import blocker,preflight


class Readiness(unittest.TestCase):
    def test_actual_mismatch_explains_sizes_before_approval(self):
        route={'route_id':'r','reason':'CONNECTION_SIZE_REQUIRES_UNMODELLED_TRANSITION',
               'source':{'size':{'diameter_mm':90}},'destination':{'size':{'diameter_mm':80}},
               'section':{'nominal_diameter_mm':125},'native_status':'NOT_EVALUATED','approval_status':'DRAFT'}
        result=preflight({'routes':[route]})
        self.assertEqual(result['ready_route_ids'],[])
        for number in ('90','125','80'):self.assertIn(number,result['failures'][0]['reason'])
        self.assertIn('transiciones',blocker(route))

    def test_only_approved_and_natively_valid_routes_are_modelable(self):
        routes=[{'route_id':'good','native_status':'VALID','approval_status':'APPROVED'},
                {'route_id':'draft','native_status':'VALID','approval_status':'DRAFT'},
                {'route_id':'unverified','native_status':'NOT_EVALUATED','approval_status':'APPROVED'}]
        result=preflight({'routes':routes})
        self.assertEqual(result['ready_route_ids'],['good']);self.assertEqual(len(result['failures']),2)

    def test_capacity_blocker_is_not_reported_as_collision(self):
        message=blocker({'reason':'MISSING_DISTRIBUTOR_PORT','native_status':'NOT_EVALUATED'})
        self.assertIn('toma libre',message);self.assertNotIn('geométrica',message)


if __name__=='__main__':unittest.main()
