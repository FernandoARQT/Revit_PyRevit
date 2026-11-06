import copy
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ARQ-T.extension/lib'))
from ARQT_MEP.routing.design import workflow,display,model
from ARQT_MEP.routing import settings


def catalog():
    return {'devices':[{'id':'hrv','element_id':1,'family':'I-VE-Recuperador'},
        {'id':'s','element_id':2,'family':'Distribuidor Impulsion_Suministro','is_configured_distributor':True},
        {'id':'e','element_id':3,'family':'Distribuidor Extracción_Aire viciado','is_configured_distributor':True}],
        'terminals':[]}


class UserWorkflow(unittest.TestCase):
    def test_project_profile_is_populated_without_inventing_internal_diameter(self):
        cfg=settings.validate({});p=workflow.configured_profile(cfg)
        self.assertEqual(p.external_diameter_mm,cfg['section']['outer_diameter_mm'])
        self.assertEqual(p.minimum_bend_radius_mm,cfg['bend_radius_mm'])
        self.assertIsNone(p.internal_diameter_mm);self.assertFalse(p.geometry_verified)

    def test_unique_equipment_is_suggested_by_its_network(self):
        self.assertEqual(workflow.defaults(catalog(),{}),{'HRV':'hrv','SUPPLY':'s','EXTRACT':'e'})

    def test_ambiguous_manifolds_require_a_choice(self):
        data=catalog();data['devices'].append(dict(data['devices'][1],id='s2',element_id=4))
        self.assertIsNone(workflow.defaults(data,{})['SUPPLY'])

    def test_primary_action_requires_a_complete_selection(self):
        data=catalog();cfg=settings.validate({});p=workflow.configured_profile(cfg);selected=workflow.defaults(data,cfg)
        with self.assertRaises(ValueError):workflow.payload(data,cfg,selected,[],p,10)
        selected['SUPPLY']=None
        with self.assertRaises(ValueError):workflow.payload(data,cfg,selected,[{'network_type':'SUPPLY'}],p,10)

    def test_equipment_choices_are_saved_in_the_generation_payload(self):
        data=catalog();cfg=settings.validate({});before=copy.deepcopy(cfg)
        result=workflow.payload(data,cfg,workflow.defaults(data,cfg),[{'network_type':'SUPPLY'}],workflow.configured_profile(cfg),10)
        self.assertEqual(result['configuration']['hrv_id'],'hrv')
        self.assertEqual(result['configuration']['equipment_assignments'],[{'element_id':2,'network_type':'SUPPLY'}])
        self.assertEqual(cfg,before)

    def test_only_complete_product_data_can_be_marked_verified(self):
        with self.assertRaises(ValueError):model.DuctProfile('unknown','SUPPLY',75,None,76,0,150,None,True)


class DisplayCorridors(unittest.TestCase):
    def test_continuous_edges_become_one_editable_line(self):
        nodes={str(i):{'node_id':str(i),'xyz_mm':[i*1000,0,100],'level_id':1,'kind':'MEDIAL'} for i in range(3)}
        edges={}
        for i in range(2):
            edges[str(i)]={'node_ids':[str(i),str(i+1)],'kind':'MEDIAL','length_mm':1000,
                'width_profile':[{'station_mm':0,'width_mm':200},{'station_mm':1000,'width_mm':300}],
                'height_profile':[{'station_mm':0,'axis_intervals_mm':[[50,200]]}],
                'region_sequence':['room']}
        result=display.corridors({'nodes':nodes,'edges':edges})
        self.assertEqual(len(result),1);c=next(iter(result.values()))
        self.assertEqual(len(c['reference_xy_mm']),3);self.assertEqual(c['length_mm'],2000)
        self.assertEqual(set(c['source_edge_ids']),{'0','1'})
        self.assertEqual([v['station_mm'] for v in c['width_profile']],[0,1000,1000,2000])


if __name__=='__main__':unittest.main()
