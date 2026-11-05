"""V2.1 regression definitions. Execution intentionally pending user request."""
import copy
import unittest
import time
from test_routing_core import configuration,request,scene,profile
from ARQT_MEP.routing.core.global_incumbent import GlobalIncumbent,validate_network
from ARQT_MEP.routing.core.candidate_repository import CandidateRepository
from ARQT_MEP.routing.core.search_budget import attempt_seed
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core.multi_route import plan_route


class V21Conservation(unittest.TestCase):
    def test_master_prefers_nine_even_with_expensive_conditioned_alternatives(self):
        from ARQT_MEP.routing.core.integer_master import solve
        pools={str(i):[{'status':'CONDITIONAL','cost':{'total':10000}},
            {'status':'VALID','cost':{'total':0}}] for i in range(9)}
        # Cheap alternatives have six available ports; the nine expensive
        # options are mutually compatible and use distinct ports.
        def conflict(rid,i,other,j):
            return i==j==1 and int(rid)%6==int(other)%6
        for reverse in (False,True):
            order=sorted(pools,reverse=reverse)
            selected,report=solve(pools,order,conflict,time.monotonic()+10)
            if not report.get('available',True):self.skipTest('SciPy not available')
            self.assertIsNotNone(selected)
            self.assertEqual(len(selected),9)
            self.assertTrue(report['primary_optimal'])
            self.assertEqual(report['primary_connections'],9)
            self.assertGreaterEqual(sum(i==0 for i in selected.values()),3)

    def test_selector_recovers_nine_with_shared_ports_and_reversed_input_order(self):
        from ARQT_MEP.routing.core.portfolio import select_candidates
        config=configuration();space=build_spatial_model(scene());requests=[];pools={}
        shared={'id':'shared','xyz_mm':[200,50,250],'provenance':'MANUAL_REFERENCE'}
        for i in range(9):
            req=request(str(i),start=(200,100+200*i,250),end=(2800,100+200*i,250))
            good=plan_route(space,req,profile(config),config)
            alternative=plan_route(space,dict(req,source=shared),profile(config),config)
            # Inexpensive partial alternatives must never beat completion.
            good['cost']={'total':10000}
            alternative['cost']={'total':0}
            req['source_alternatives']=[shared];requests.append(req);pools[req['route_id']]=[alternative,good]
        for reverse in (False,True):
            chosen,_=select_candidates(space,list(reversed(requests)) if reverse else requests,
                {k:list(reversed(v)) if reverse else v for k,v in pools.items()},config,{},set(),time.monotonic()+10)
            self.assertEqual(len(chosen),9)
            self.assertTrue(validate_network(space,requests,config,chosen)['valid'])

    def test_seven_are_not_replaced_by_six_then_eight_replace_seven(self):
        incumbent=GlobalIncumbent('fixture')
        network=lambda n:{str(i):{'status':'VALID','points_mm':[[0,i,0],[1,i,0]],'cost':{'total':100}} for i in range(n)}
        validator=lambda n:{'valid':True,'routes':n,'issues':[]}
        incumbent.consider(network(7),validator)
        self.assertFalse(incumbent.consider(network(6),validator));self.assertEqual(len(incumbent.best()),7)
        self.assertTrue(incumbent.consider(network(8),validator));self.assertEqual(len(incumbent.best()),8)

    def test_invalid_complete_combination_never_replaces_incumbent(self):
        incumbent=GlobalIncumbent('fixture')
        incumbent.consider({'a':{'status':'VALID','points_mm':[[0,0,0],[1,0,0]]}},lambda n:{'valid':True,'routes':n})
        self.assertFalse(incumbent.consider({'bad':{}},lambda n:{'valid':False,'routes':{},'issues':['collision']}))
        self.assertIn('a',incumbent.best())

    def test_restore_context_change_invalidates_explicitly(self):
        incumbent=GlobalIncumbent('new')
        self.assertFalse(incumbent.restore_state({'context':'old'},lambda n:None))
        self.assertEqual(incumbent.events[-1]['event'],'INVALIDATED')

    def test_joint_validation_detects_shared_source(self):
        config=configuration();space=build_spatial_model(scene());a=request('a');b=request('b',start=(200,1500,250),end=(2800,1500,250))
        routes={r['route_id']:plan_route(space,r,profile(config),config) for r in (a,b)}
        self.assertTrue(validate_network(space,[a,b],config,routes)['valid'])
        b['source']['id']=a['source']['id'];routes['b']['chosen_source']['id']=a['source']['id']
        self.assertFalse(validate_network(space,[a,b],config,routes)['valid'])

    def test_distinct_constructive_families_survive_preferred_limit(self):
        repository=CandidateRepository('fixture',limit=2)
        for i in range(4):
            repository.publish('r',{'chosen_source':{'id':str(i)},'points_mm':[[0,0,0],[1,i,0]],'cost':{'total':i}})
        self.assertEqual(len(repository.bank()['r']),4)

    def test_stable_seed_is_route_port_corridor_and_attempt_specific(self):
        seed=attempt_seed(0,'r','p',['wall'],0)
        self.assertEqual(seed,attempt_seed(0,'r','p',['wall'],0))
        self.assertNotEqual(seed,attempt_seed(0,'r','p',['wall'],1))

    def test_same_family_geometry_is_not_discarded_by_preferred_quota(self):
        repository=CandidateRepository('fixture',limit=2,per_family=1)
        for i in range(7):
            repository.publish('r',{'chosen_source':{'id':'same-port'},'points_mm':[[0,0,0],[1,i,0]],'cost':{'total':i}})
        self.assertEqual(len(repository.bank()['r']),7)
        restored=CandidateRepository('fixture',limit=2,per_family=1)
        self.assertTrue(restored.restore_state(repository.export_state()))
        self.assertEqual(len(restored.bank()['r']),7)

    def test_candidate_of_different_context_is_rejected_with_reason(self):
        from ARQT_MEP.routing.core.candidate_repository import audit_session
        repository=CandidateRepository('new')
        with audit_session() as events:
            repository.publish('r',{'validation_context':'old','points_mm':[[0,0,0],[1,0,0]]})
        self.assertEqual(repository.bank(),{})
        self.assertEqual(events[-1]['reason'],'CONTEXT_MISMATCH')


if __name__=='__main__':unittest.main()
