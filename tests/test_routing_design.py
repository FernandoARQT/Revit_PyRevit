"""V3 domain/persistence contracts; no Revit objects and no WPF imports."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ARQ-T.extension/lib'))
from test_ventilation_editor import fixture
from ARQT_MEP.routing.design import model as m,store,operations


def proposal():return m.create(fixture())


def add(p,cid='north',network='SUPPLY'):
    return m.edit(p,'add_corridor',{'corridor_id':cid,'points_mm':[[1000,1000],[4000,1000],[5000,3000]],'network_type':network,'level_id':1},p.revision)


class DesignModel(unittest.TestCase):
    def test_roundtrip_and_background_isolation(self):
        source=fixture();before=copy.deepcopy(source);p=m.create(source)
        p=add(p);p=m.RoutingDesignProposal.from_dict(json.loads(json.dumps(p.to_dict())))
        self.assertEqual(p.revision,2);self.assertEqual(source,before)
        p.background['terminals'][0]['x']+=100
        self.assertEqual(source,before)

    def test_open_corridor_and_exact_coordinates(self):
        p=add(proposal());p=m.edit(p,'move_vertex',{'corridor_id':'north','index':1,'point_mm':[3423.456,2781.123]},2)
        self.assertEqual(p.corridors['north']['reference_xy_mm'][1],[3423.456,2781.123])
        self.assertNotEqual(p.corridors['north']['reference_xy_mm'][0],p.corridors['north']['reference_xy_mm'][-1])

    def test_invalid_edits_are_atomic(self):
        p=add(proposal());before=p.to_dict()
        for payload in ({'index':True,'point_mm':[2,3]},{'index':9,'point_mm':[2,3]},
                        {'index':1,'point_mm':[float('nan'),0]},{'index':1,'point_mm':[1000,1000]}):
            with self.assertRaises(ValueError):m.edit(p,'move_vertex',dict(payload,corridor_id='north'),p.revision)
            self.assertEqual(p.to_dict(),before)

    def test_bad_network_level_schema(self):
        for payload in ({'network_type':'WATER'},{'level_id':999},{'level_id':True}):
            values={'points_mm':[[0,0],[1,1]],'network_type':'SUPPLY','level_id':1};values.update(payload)
            with self.assertRaises(ValueError):m.edit(proposal(),'add_corridor',values,1)
        data=proposal().to_dict();data['schema_version']=2
        with self.assertRaises(ValueError):m.RoutingDesignProposal.from_dict(data)

    def test_revision_lease(self):
        p=add(proposal())
        for revision in (1,True,2.0,None):
            with self.assertRaisesRegex(ValueError,'VERSION_CONFLICT'):m.edit(p,'undo',{},revision)

    def test_history_undo_redo_is_monotonic(self):
        p=add(proposal());before=copy.deepcopy(p.corridors)
        p=m.edit(p,'move_corridor',{'corridor_id':'north','delta_mm':[750,-450]},p.revision)
        moved=copy.deepcopy(p.corridors);p=m.edit(p,'undo',{},p.revision)
        self.assertEqual(p.corridors,before);p=m.edit(p,'redo',{},p.revision)
        self.assertEqual(p.corridors,moved);self.assertEqual(p.revision,5)
        p=m.edit(p,'undo',{},p.revision);p=m.edit(p,'move_vertex',{'corridor_id':'north','index':0,'point_mm':[1500,2000]},p.revision)
        with self.assertRaises(ValueError):m.edit(p,'redo',{},p.revision)

    def test_history_bounded_and_background_not_duplicated(self):
        p=add(proposal())
        for _ in range(40):p=m.edit(p,'move_corridor',{'corridor_id':'north','delta_mm':[10,0]},p.revision)
        self.assertEqual(len(p.history),30)
        self.assertNotIn('background',p.history[-1]);self.assertNotIn('history',p.history[-1])

    def test_history_does_not_duplicate_independent_corridors(self):
        p=add(proposal());p.corridors['other']=copy.deepcopy(p.corridors['north']);p.corridors['other']['corridor_id']='other'
        p=m.edit(p,'move_corridor',{'corridor_id':'north','delta_mm':[100,0]},p.revision)
        self.assertNotIn('other',p.history[-1]['records']['corridors'])
        saved=copy.deepcopy(p.corridors['other']);p=m.edit(p,'undo',{},p.revision)
        self.assertEqual(p.corridors['other'],saved)
        self.assertEqual(p.corridors['north']['reference_xy_mm'][0],[1000,1000])

    def test_lock_preserved_across_reload(self):
        p=add(proposal());p=m.edit(p,'set_constraint',{'corridor_id':'north','constraint_type':'LOCKED_WAYPOINT'},2)
        p=m.RoutingDesignProposal.from_dict(p.to_dict())
        for operation in ('move_vertex','delete_vertex','delete_corridor','move_corridor','set_network'):
            with self.assertRaisesRegex(ValueError,'bloqueado'):m.edit(p,operation,{'corridor_id':'north'},p.revision)
        p=m.edit(p,'set_constraint',{'corridor_id':'north','constraint_type':'REQUIRED_WAYPOINT'},p.revision)
        self.assertEqual(p.corridors['north']['constraint_type'],'REQUIRED_WAYPOINT')

    def test_add_delete_vertices_and_minimum_points(self):
        p=add(proposal());p=m.edit(p,'add_vertex',{'corridor_id':'north','index':3,'point_mm':[6000,4000]},2)
        for _ in range(2):p=m.edit(p,'delete_vertex',{'corridor_id':'north','index':1},p.revision)
        with self.assertRaises(ValueError):m.edit(p,'delete_vertex',{'corridor_id':'north','index':0},p.revision)

    def test_assignments_respect_network_and_remove_deleted(self):
        p=add(proposal());tid=next(k for k,t in p.terminals.items() if t['network_type']=='SUPPLY')
        p=m.edit(p,'assign_terminal',{'terminal_id':tid,'corridor_ids':['north']},p.revision)
        with self.assertRaises(ValueError):m.edit(p,'set_network',{'corridor_id':'north','network_type':'EXTRACT'},p.revision)
        p=m.edit(p,'delete_corridor',{'corridor_id':'north'},p.revision)
        self.assertNotIn(tid,p.corridor_assignments)

    def test_dependency_invalidation_preserves_unaffected_route(self):
        p=add(proposal());p.dependencies={'north':['bundle'],'bundle':['route-a'],'route-a':['north']}
        p.individual_routes={'route-a':{'validation_status':'DETAILED_GEOMETRY_VALID'},'route-b':{'validation_status':'DETAILED_GEOMETRY_VALID','points':[[1,2,3]]}}
        independent=copy.deepcopy(p.individual_routes['route-b'])
        p=m.edit(p,'move_corridor',{'corridor_id':'north','delta_mm':[100,0]},p.revision)
        self.assertEqual(p.individual_routes['route-a']['validation_status'],'NOT_EVALUATED')
        self.assertEqual(p.individual_routes['route-b'],independent)
        self.assertFalse(p.validation)


class DesignStore(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.previous=store.ROOT;store.ROOT=self.tmp.name
        self.p=store.create(add(proposal()))

    def tearDown(self):store.ROOT=self.previous;self.tmp.cleanup()

    def test_reload_and_separate_document(self):
        q=store.get(self.p.document_identity,self.p.proposal_id);self.assertEqual(q.to_dict(),self.p.to_dict())
        self.assertEqual(store.list_proposals('other-document'),[])
        with self.assertRaises((FileNotFoundError,ValueError)):store.get('other-document',self.p.proposal_id)
        with self.assertRaises(ValueError):store.path(self.p.document_identity,'../outside')

    def test_concurrent_clients_one_winner(self):
        def update(n):
            try:return store.update(self.p.document_identity,self.p.proposal_id,2,'move_corridor',{'corridor_id':'north','delta_mm':[n,0]}).revision
            except ValueError as exc:return str(exc)
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(update,[100,200]))
        self.assertEqual(results.count(3),1);self.assertTrue(any('VERSION_CONFLICT' in str(r) for r in results))
        self.assertEqual(store.get(self.p.document_identity,self.p.proposal_id).revision,3)

    def test_independent_duplicate_and_history_persistence(self):
        p=store.update(self.p.document_identity,self.p.proposal_id,2,'move_corridor',{'corridor_id':'north','delta_mm':[200,0]})
        q=store.duplicate(p.document_identity,p.proposal_id,3)
        store.update(q.document_identity,q.proposal_id,1,'delete_corridor',{'corridor_id':'north'})
        self.assertIn('north',store.get(p.document_identity,p.proposal_id).corridors)
        restored=store.update(p.document_identity,p.proposal_id,3,'undo')
        self.assertEqual(restored.corridors,self.p.corridors)
        self.assertEqual(len(store.list_proposals(p.document_identity)),2)

    def test_stale_worker_cannot_overwrite_edit(self):
        lease=m.context(self.p,{'test':1})
        store.update(self.p.document_identity,self.p.proposal_id,2,'move_corridor',{'corridor_id':'north','delta_mm':[200,0]})
        with self.assertRaisesRegex(ValueError,'STALE_RESULT'):store.publish(self.p.document_identity,self.p.proposal_id,lease,{'test':1},self.p)
        self.assertEqual(store.get(self.p.document_identity,self.p.proposal_id).revision,3)

    def test_mcp_domain_and_wpf_use_same_edits(self):
        payload={'proposal_id':self.p.proposal_id,'expected_revision':2,'corridor_id':'north','index':1,'point_mm':[2345,6789]}
        actual=operations.dispatch(self.p.document_identity,'routing_design_move_vertex',payload)
        expected=m.edit(self.p,'move_vertex',payload,2)
        self.assertEqual(actual['corridors'],expected.to_dict()['corridors'])
        with self.assertRaises(ValueError):operations.dispatch(self.p.document_identity,'routing_design_model',payload)


if __name__=='__main__':unittest.main()
