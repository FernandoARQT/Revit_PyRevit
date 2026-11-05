"""Shared service orchestration with explicit read-adapter fixtures, no Revit API."""
import copy
import importlib.util
import sys
import types
import unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ARQ-T.extension/lib'))
from ARQT_MEP import config
from ARQT_MEP import surface
from shapely.geometry import box


class Service(unittest.TestCase):
    def setUp(self):
        self.rooms=[]
        for i,(name,lid,z,x) in enumerate([('Dormitorio principal',2,3000,0),('Salón',1,0,0),('Baño',2,3000,5000)]):
            poly=box(x,0,x+4000,4000)
            self.rooms.append(dict(id=i,uid=str(i),name=name,number=str(i),level_id=lid,level='Planta '+str(lid),
                  phase_id=0,level_z=z,polygon=poly,rings=[list(poly.exterior.coords)],centroid=[x+2000,2000],area_m2=16))
        self.ceilings=[dict(id=100+i,uid='c'+r['uid'],polygon=r['polygon'],z=r['level_z']+2600,
                        level_id=r['level_id'],level_z=r['level_z']) for i,r in enumerate(self.rooms)]
        self.existing=[]
        self.rr=types.ModuleType('ARQT_MEP.revit_read')
        self.rr.eid=lambda v:v
        self.rr.name=lambda p:'Nueva construcción'
        self.rr.analyze_rooms=lambda d,phase:(copy.deepcopy(self.rooms),[])
        self.rr.read_ceilings=lambda d,phase:(copy.deepcopy(self.ceilings),[])
        self.rr.read_doors=lambda d,rooms,phase:([['0','2'],['1','2']],{r['uid']:[] for r in rooms},[])
        self.rr.read_kitchen_hints=lambda d,rooms,phase:[]
        self.rr.find_room_ceilings=lambda r,cs,rooms,ids:[c for c in cs if c['id']==100+int(r['uid'])]
        self.rr.read_symbols=lambda d:{role:dict(id=i,uid='s'+str(i),family='Fixture',type=role,placement='OneLevelBased',bounds=[-82,-82,-1,82,82,45],visible_z=-1,dimensions=[164,164,46])
                                     for i,role in enumerate(('impulsion','extraccion'),500)}
        self.rr.read_obstacles=lambda d,phase:[]
        self.rr.public_room=lambda r:{k:v for k,v in r.items() if k!='polygon'}
        self.rr.fingerprint=lambda d:'unchanged-fixture'
        self.own=types.ModuleType('ARQT_MEP.ownership')
        self.own.document_id=lambda d:'fixture'
        self.own.inventory=lambda d,t:self.existing
        path=Path(__file__).resolve().parents[1]/'ARQ-T.extension/lib/ARQT_MEP/service.py'
        spec=importlib.util.spec_from_file_location('ARQT_MEP._fixture_service',path)
        self.service=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'ARQT_MEP.revit_read':self.rr,'ARQT_MEP.ownership':self.own}):
            spec.loader.exec_module(self.service)
        self.doc=types.SimpleNamespace(Title='Fixture',Phases=[types.SimpleNamespace(Id=0)])
        self.settings=copy.deepcopy(config.DEFAULT)

    def propose(self,scope=None):
        return self.service.propose_ventilation_terminals(self.doc,['0','1','2'],self.settings,scope)

    def test_stacked_floors_do_not_block_each_other(self):
        p=self.propose()
        self.assertTrue(p['ready'],p['issues'])
        self.assertEqual(len(p['terminals']),3)
        a=next(t for t in p['terminals'] if t['room_uid']=='0')
        b=next(t for t in p['terminals'] if t['room_uid']=='1')
        self.assertEqual((a['x'],a['y']),(b['x'],b['y']))
        self.assertNotEqual(a['z'],b['z'])

    def test_partial_scope_preserves_global_flow(self):
        full=self.propose();partial=self.propose({'kind':'level','level_id':1})
        self.assertTrue(partial['ready'])
        self.assertEqual(len(partial['terminals']),1)
        self.assertEqual(full['flows'],partial['flows'])
        rooms=self.propose({'kind':'rooms','room_uids':['2']})
        self.assertEqual(len(rooms['terminals']),1)
        self.assertEqual(full['flows'],rooms['flows'])

    def test_proposal_seal_and_roundtrip(self):
        import json
        p=self.propose()
        self.service.get_ventilation_proposal(json.loads(json.dumps(p)))
        p['terminals'][0]['x']+=1
        with self.assertRaises(ValueError):self.service.get_ventilation_proposal(p)

    def own_fixture(self,p,pinned=False,locked=False,changed=False,installation_offset=-1):
        t=p['terminals'][0]
        return dict(id=900,uid='own',own=True,metadata=dict(key=t['key'],room_uid=t['room_uid'],locked=locked,target={'installation_offset_mm':installation_offset}),
                    pinned=pinned,manual_change=changed,position=surface.installation_insertion(t,-1,installation_offset),basis=surface.frame(t),flow=t['flow'],
                    symbol_id=t['symbol_id'],level_id=t['level_id'])

    def test_existing_valid_reused_and_protected_maintained(self):
        p=self.propose();self.existing=[self.own_fixture(p)]
        again=self.propose()
        self.assertTrue(again['ready'],again['issues'])
        self.assertEqual(again['terminals'][0]['existing_id'],900)
        self.assertEqual(again['actions'][0]['action'],'mantener')
        for key in ('pinned','locked'):
            self.existing=[self.own_fixture(p,**{key:True})]
            protected=self.propose()
            self.assertTrue(protected['ready'],protected['issues'])
            self.assertEqual(protected['actions'][0]['action'],'mantener protegido')
            self.assertTrue(protected['terminals'][0]['protected'])

    def test_manual_change_blocks_write(self):
        p=self.propose();self.existing=[self.own_fixture(p,changed=True)]
        changed=self.propose()
        self.assertFalse(changed['ready'])
        self.assertTrue(any('modificado manualmente' in i for i in changed['issues']))

    def test_inclined_position_reused_with_its_basis(self):
        self.ceilings[0]['plane']={'origin':[0,0,5600],'normal':[.1,-.2,1]}
        p=self.propose();self.assertTrue(p['ready'],p['issues'])
        self.existing=[self.own_fixture(p)]
        repeated=self.propose()
        self.assertTrue(repeated['ready'],repeated['issues'])
        self.assertEqual(repeated['actions'][0]['action'],'mantener')
    def test_old_installation_requires_one_mm_adjustment_not_preservation(self):
        p=self.propose();self.existing=[self.own_fixture(p,installation_offset=0)]
        adjusted=self.propose()
        self.assertTrue(adjusted['ready'],adjusted['issues'])
        self.assertEqual(adjusted['actions'][0]['action'],'reubicar')
        self.existing=[self.own_fixture(p,locked=True,installation_offset=0)]
        self.assertFalse(self.propose()['ready'])

    def test_no_housing_or_wrong_scope_is_rejected(self):
        with self.assertRaises(ValueError):self.service.propose_ventilation_terminals(self.doc,[],self.settings)
        with self.assertRaises(ValueError):self.propose({'kind':'rooms','room_uids':['alien']})


if __name__=='__main__':unittest.main()
