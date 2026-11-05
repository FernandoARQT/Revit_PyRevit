"""Equipment-level topology invariants, independent of Revit or search fixtures."""
import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ARQ-T.extension/lib'))
from ARQT_MEP.routing import settings,service
from ARQT_MEP.routing.systems.heat_recovery_topology import requests


def port(uid,d=75,x=0,classification='UndefinedSystemType',direction='Bidirectional'):
    return dict(id=uid,element_key=uid.split(':')[0],element_id=1,connector_id=1,xyz_mm=[x,100,250],
                direction=[1,0,0],size={'shape':'Round','diameter_mm':d},classification=classification,
                flow_direction=direction,connected=False,provenance='REVIT_CONNECTOR')


def fixture():
    hrv=dict(id='hrv',element_id=1,family='Recuperador de calor',connectors=[
        port('hrv:s',125,classification='SupplyAir',direction='Out'),
        port('hrv:e',125,classification='ReturnAir',direction='In'),
        port('hrv:outside',125,classification='SupplyAir',direction='In')])
    devices=[hrv]
    for uid,eid,count in [('s',2,3),('e',3,2)]:
        devices.append(dict(id=uid,element_id=eid,family='Distribuidor_'+('Impusion_Suministro' if uid=='s' else 'Extracción_Aire viciado'),is_configured_distributor=True,
                            connectors=[port(uid+':main',125)]+[port(uid+':'+str(i),x=200+i) for i in range(count)]))
    terminals=[];destinations=[]
    for i,network in enumerate(['SUPPLY','SUPPLY','EXTRACT']):
        uid='t'+str(i);p=port(uid+':port',x=2000+i)
        terminals.append(dict(id=uid,element_id=10+i,family='Fraenkische_rejilla 125_Suministro' if network=='SUPPLY' else 'Fraenkische_rejilla 125_Extraccion',
                              name='Suministro' if network=='SUPPLY' else 'Extracción',connectors=[p],airflow_lps=8,
                              suggested_network_type=network,network_assignment_requires_review=False))
        destinations.append(dict(terminal_id=uid,port_id=p['id'],network_type=network))
    config=settings.validate({'hrv_id':'hrv','section':{'nominal_diameter_mm':75,'outer_diameter_mm':75},
        'equipment_assignments':[{'element_id':2,'network_type':'SUPPLY'},{'element_id':3,'network_type':'EXTRACT'}]})
    return {'devices':devices,'terminals':terminals},config,destinations


class Topology(unittest.TestCase):
    def test_exhaust_in_is_interior_and_exhaust_out_is_never_allocated(self):
        cat,c,d=fixture()
        cat['devices'][0]['connectors'][1]['classification']='ExhaustAir'
        cat['devices'][0]['connectors'].append(port('hrv:expulsion',125,classification='ExhaustAir',direction='Out'))
        routes,*_=requests(cat,c,d)
        extract_main=[r for r in routes if r['route_kind']=='MAIN' and r['network_type']=='EXTRACT']
        self.assertEqual(len(extract_main),1)
        self.assertEqual(extract_main[0]['source']['id'],'hrv:e')

    def test_rejects_wrong_terminal_family_and_distributor_network(self):
        cat,c,d=fixture();cat['terminals'][0]['family']='Fraenkische_rejilla 125_Extraccion'
        with self.assertRaises(ValueError):requests(cat,c,d)
        cat,c,d=fixture();cat['devices'][1]['family']='Distribuidor_Extracción_Aire viciado'
        with self.assertRaises(ValueError):requests(cat,c,d)

    def test_two_stage_network_counts_and_main_flows(self):
        cat,c,d=fixture();r,w,t,o=requests(cat,c,d)
        self.assertEqual(len(r),5);self.assertFalse(any(v.get('error') for v in r))
        self.assertEqual(t['counts']['SUPPLY']['terminals'],2)
        self.assertEqual(t['counts']['EXTRACT']['terminals'],1)
        self.assertEqual(sorted(v['airflow_lps'] for v in r if v['route_kind']=='MAIN'),[8,16])
        self.assertTrue(all(v['source']['kind']=='DISTRIBUTOR' for v in r if v['route_kind']=='BRANCH'))
        self.assertTrue(all(v['source']['kind']=='HRV' for v in r if v['route_kind']=='MAIN'))
        self.assertEqual({v['section']['nominal_diameter_mm'] for v in o.values()},{125})
        self.assertNotIn('hrv:outside',{v['source']['id'] for v in r})

    def test_each_distributor_branch_is_used_once_and_only_for_its_network(self):
        cat,c,d=fixture();r,*_=requests(cat,c,d)
        branches=[v for v in r if v['route_kind']=='BRANCH']
        self.assertEqual(len({v['source']['id'] for v in branches}),3)
        self.assertTrue(all(v['source']['element_key']==('s' if v['network_type']=='SUPPLY' else 'e') for v in branches))

    def test_capacity_shortage_does_not_reuse_ports(self):
        cat,c,d=fixture();cat['devices'][1]['connectors']=cat['devices'][1]['connectors'][:2]
        r,w,t,o=requests(cat,c,d)
        self.assertEqual(sum(v.get('error')=='MISSING_DISTRIBUTOR_PORT' for v in r),1)
        self.assertTrue(w)

    def test_existing_terminal_connection_is_kept_not_recreated(self):
        cat,c,d=fixture();cat['terminals'][0]['connectors'][0]['connected']=True
        r,w,t,o=requests(cat,c,d)
        self.assertEqual(t['counts']['SUPPLY']['preserved_terminals'],1)
        self.assertNotIn('route:t0:port',{v['route_id'] for v in r})
        self.assertEqual(len(t['preserved_connections']),1)

    def test_main_connection_is_never_allocated_to_a_terminal(self):
        cat,c,d=fixture();r,*_=requests(cat,c,d)
        self.assertTrue(all(not v['source']['id'].endswith(':main') for v in r if v['route_kind']=='BRANCH'))

    def test_incompatible_main_diameter_is_reported(self):
        cat,c,d=fixture();cat['devices'][1]['connectors'][0]['size']['diameter_mm']=160
        r,*_=requests(cat,c,d)
        self.assertEqual(sum(v.get('error')=='MAIN_TRANSITION_REQUIRED' for v in r),1)

    def test_network_assignment_and_duplicate_terminal_are_checked(self):
        cat,c,d=fixture();c['equipment_assignments'][0]['network_type']=''
        with self.assertRaises(ValueError):requests(cat,c,d)
        cat,c,d=fixture();d.append(copy.deepcopy(d[0]))
        with self.assertRaises(ValueError):requests(cat,c,d)

    def test_service_accepts_equipment_selection_without_individual_origins(self):
        cat,c,d=fixture()
        # All ports occupied: keep them and produce a zero-new-route proposal.
        for terminal in cat['terminals']:terminal['connectors'][0]['connected']=True
        snapshot={'document_identity':'fixture','geometry_revision':'fixture','regions':[],'obstacles':[]}
        result=service.create_mep_route_proposal(snapshot,cat,[],d,c)
        self.assertEqual(len(result['routes']),0)
        self.assertEqual(result['topology']['counts']['SUPPLY']['preserved_terminals'],2)


if __name__=='__main__':unittest.main()
