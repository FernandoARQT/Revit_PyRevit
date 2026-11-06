import copy
import tempfile
import unittest
from dataclasses import asdict
from test_routing_design import proposal
from test_routing_design_graph import profile
from test_routing_core import scene
from ARQT_MEP.routing.design import store,service,model


class GenerationIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=store.ROOT;store.ROOT=self.tmp.name
        self.p=store.create(proposal());self.snapshot=scene()
        self.snapshot['document_identity']=self.p.document_identity;self.snapshot['regions'][0]['level_id']=1
        self.p=store.capture(self.p.document_identity,self.p.proposal_id,1,self.snapshot,{},
            {'constraints':{},'design_clearance_mm':10},profile())

    def tearDown(self):store.ROOT=self.old;self.tmp.cleanup()

    def test_generate_publish_and_recover(self):
        p=service.generate_corridors(self.p.document_identity,self.p.proposal_id,2,{'max_nodes':80,'seconds':5,'spacing_mm':600})
        self.assertEqual(p.revision,3);self.assertTrue(p.corridor_graph['edges'])
        self.assertEqual(p.geometry_revision,self.snapshot['geometry_revision'])
        recovered=store.get(p.document_identity,p.proposal_id)
        self.assertEqual(p.to_dict(),recovered.to_dict())
        self.assertFalse(p.validation)

    def test_real_extractor_solid_identity_maps_to_ceiling_uid(self):
        p=copy.deepcopy(self.p);p.snapshot['regions'][0].pop('level_id')
        p.snapshot['regions'][0]['lower_element']=p.background['analysis']['rooms'][0]['ceilings'][0]['uid']+':0'
        self.assertEqual(service.region_levels(p)['room'],1)

    def test_regenerate_preserves_manual_corridor_and_lock(self):
        p=store.update(self.p.document_identity,self.p.proposal_id,2,'add_corridor',
            {'corridor_id':'manual','points_mm':[[400,400],[2000,600]],'level_id':1,'network_type':'SUPPLY'})
        p=store.update(p.document_identity,p.proposal_id,p.revision,'set_constraint',{'corridor_id':'manual','constraint_type':'LOCKED_WAYPOINT'})
        manual=copy.deepcopy(p.corridors['manual'])
        p=service.generate_corridors(p.document_identity,p.proposal_id,p.revision,{'max_nodes':80,'seconds':5,'spacing_mm':600})
        self.assertEqual(p.corridors['manual'],manual)

    def test_cancel_preserves_prior_manual_decisions(self):
        p=store.update(self.p.document_identity,self.p.proposal_id,2,'add_corridor',
            {'corridor_id':'manual','points_mm':[[400,400],[2000,600]],'level_id':1,'network_type':'SUPPLY'})
        p=service.generate_corridors(p.document_identity,p.proposal_id,p.revision,cancelled=lambda:True)
        self.assertIn('manual',p.corridors);self.assertTrue(p.corridor_graph['stats']['cancelled'])

    def test_recapture_and_cancel_keep_existing_generated_corridors(self):
        p=service.generate_corridors(self.p.document_identity,self.p.proposal_id,2,{'max_nodes':80,'seconds':5,'spacing_mm':600})
        before=copy.deepcopy(p.corridors);graph=copy.deepcopy(p.corridor_graph)
        p=store.capture(p.document_identity,p.proposal_id,p.revision,p.snapshot,p.catalog,p.configuration,profile())
        self.assertEqual(p.corridor_graph,graph)
        p=service.generate_corridors(p.document_identity,p.proposal_id,p.revision,cancelled=lambda:True)
        self.assertEqual(p.corridors,before)
        self.assertTrue(p.corridor_graph['last_attempt']['cancelled'])


if __name__=='__main__':unittest.main()
