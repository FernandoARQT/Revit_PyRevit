"""Pure proposal and transform tests; native integration has separate fixtures."""
import copy
import math
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ARQ-T.extension/lib'))
from shapely.geometry import box,mapping
from ARQT_MEP import airflow,config,proposal as m,proposal_store as store,surface
from ARQT_MEP.plan_transform import PlanTransform


def fixture(sloped=False):
    rooms=[]
    specs=[('0','Hab. Ppal',0,0,'dormitorio'),('1','Habitación 2',8000,0,'dormitorio'),('2','Habitación 3',16000,0,'dormitorio'),
           ('3','Salón-cocina-comedor',0,8000,'mixto'),('4','Baño',10000,8000,'bano'),('5','Hab. Ppal Vestidor',18000,8000,'vestidor')]
    for uid,name,x,y,use in specs:
        geometry=box(x,y,x+6000,y+6000);c={'id':100+int(uid),'uid':'ceiling'+uid,'geometry':mapping(geometry),'z':2700,
             'level_id':1,'level_z':0,'surface_id':'ceiling'+uid+':face',
             'plane':{'origin':[x,y,2700],'normal':[-.1,0,1] if sloped and uid=='0' else [0,0,1]}}
        rooms.append({'uid':uid,'id':int(uid),'name':name,'number':uid,'use':use,'level_id':1,'level':'Planta baja','level_z':0,
           'area_m2':36,'phase_id':0,'rings':[list(geometry.exterior.coords)],'centroid':[x+3000,y+3000],'doors':[[x+1000,y]],'ceilings':[c]})
    s=copy.deepcopy(config.DEFAULT);s['phase_id']=0
    flows=airflow.calculate_airflows(rooms);terminals=[]
    symbols={role:{'id':200+i,'uid':'symbol'+role,'placement':'OneLevelBased','dimensions':[164,164,46],'visible_z':0,'type':role,'family':'Fixture'} for i,role in enumerate(m.ROLES)}
    for r in flows['rows']:
        room=next(v for v in rooms if v['uid']==r['room_uid']);x,y=room['centroid']
        if room['uid']=='3':x=1500 if r['role']=='impulsion' else 5000
        c=room['ceilings'][0];pose=dict(x=x,y=y,z=surface.height(c,x,y),ceiling_id=c['id'],ceiling_uid=c['uid'],plane=c['plane'],
             ceiling_surface_id=c['surface_id'],level_id=1,level_z=0,slope_deg=surface.slope_degrees(c),score=0,criteria={'wall_mm':1000,'door_mm':1000})
        terminals.append(dict(pose,key=r['key']+':0',room_uid=room['uid'],room_name=room['name'],role=r['role'],flow=r['design'],symbol_id=symbols[r['role']]['id'],
             existing_id=None,existing_position=None,existing_flow=None,existing_basis=None,alternatives=[copy.deepcopy(pose)]))
    legacy={'version':1,'document_id':'editor-fixture','fingerprint':'fixture','room_uids':[r['uid'] for r in rooms],
            'scope':{'kind':'all'},'settings':s,'analysis':{'rooms':rooms,'phases':[{'id':0,'name':'Nueva'}],'phase_id':0,'issues':[],'edges':[]},
            'flows':flows,'terminals':terminals,'symbols':symbols,'actions':[],'warnings':[],'issues':[],'obstacles':[],'manual_terminals':[]}
    zone=m.VentilationZone('kitchen','3','COCINA',[[4000,8000],[6000,8000],[6000,14000],[4000,14000]]).to_dict()
    return m.upgrade(legacy,'Vivienda ensayo',[zone])


class SymbolRefresh(unittest.TestCase):
    def test_reload_updates_terminal_and_alternative_ids_by_role(self):
        p=fixture()
        positions=[(t['x'],t['y'],t['flow']) for t in m.active(p)]
        for t in m.active(p):t['alternatives'][0]['symbol_id']=t['symbol_id']
        for role,info in p['symbols'].items():info['id']+=1000
        m.synchronize_symbol_ids(p)
        for t in m.active(p):
            self.assertEqual(t['symbol_id'],p['symbols'][t['role']]['id'])
            self.assertEqual(t['alternatives'][0]['symbol_id'],t['symbol_id'])
        self.assertEqual(positions,[(t['x'],t['y'],t['flow']) for t in m.active(p)])


class Transform(unittest.TestCase):
    def test_roundtrip_rotated_pan_zoom_and_large_origin(self):
        for angle in (0,.31,1.4):
            t=PlanTransform({'origin_mm':[56000,-82000],'right':[math.cos(angle),math.sin(angle)],'up':[-math.sin(angle),math.cos(angle)]})
            t.fit([(56000,-82000),(70000,-70000)],1200,850);t.zoom_at((450,320),2.3);t.shift(73,-121)
            for point in ((56543.12,-79231.7),(62000,5400)):
                self.assertLess(math.dist(point,t.to_model(t.to_screen(point))),1e-7)
    def test_resize_does_not_change_model_coordinates(self):
        t=PlanTransform();t.fit([(0,0),(10000,6000)],800,500);t.resize(1600,900)
        self.assertLess(math.dist((2543,4289),t.to_model(t.to_screen((2543,4289)))),1e-7)
    def test_zoom_anchor_is_fixed(self):
        t=PlanTransform();t.fit([(0,0),(10000,6000)],800,500);before=t.to_model((321,232));t.zoom_at((321,232),3)
        self.assertLess(math.dist(before,t.to_model((321,232))),1e-7)
    def test_y_is_inverted_only_at_screen(self):
        t=PlanTransform();t.fit([(0,0),(1000,1000)],800,500)
        self.assertLess(t.to_screen((500,900))[1],t.to_screen((500,100))[1])
    def test_dpi_uses_device_independent_units(self):
        self.assertEqual(PlanTransform().physical_pixels_to_dip((600,300),144,144),(400,200))


class Editor(unittest.TestCase):
    def setUp(self):self.p=fixture();self.t=self.p['terminals'][0]['terminal_id']
    def edit(self,op,**payload):self.p=m.edit(self.p,op,payload,self.p['version']);return self.p
    def test_deleted_original_is_recreated_without_losing_draft_design(self):
        t=self.p['terminals'][0];before=copy.deepcopy(t)
        t.update(existing_id=123,existing_uid='old',existing_position=[1,2,3],existing_flow=8,
                 existing_basis=[[1,0,0],[0,1,0],[0,0,1]],preserve=True,status='PLACED',accepted=True)
        self.p['history']=[copy.deepcopy(self.p)];self.p['future']=[{}]
        self.assertEqual(m.detach_missing_terminals(self.p,{123}),[t['terminal_id']])
        for key in ('existing_id','existing_uid','existing_position','existing_flow','existing_basis'):
            self.assertIsNone(t[key])
        for key in ('x','y','z','symbol_id','flow','role','terminal_id'):
            self.assertEqual(t[key],before[key])
        self.assertNotIn('preserve',t);self.assertEqual(t['status'],'PROPOSED')
        self.assertEqual(self.p['history'],[]);self.assertEqual(self.p['future'],[])

    def test_existing_original_and_locks_are_preserved(self):
        t=self.p['terminals'][0];t.update(existing_id=123,lock_full=True,lock_position=True,lock_flow=True)
        before=copy.deepcopy(t)
        self.assertEqual(m.detach_missing_terminals(self.p,{456}),[])
        self.assertEqual(t,before)
        m.detach_missing_terminals(self.p,{123})
        self.assertTrue(t['lock_full']);self.assertTrue(t['lock_position']);self.assertTrue(t['lock_flow'])

    def test_already_deleted_pending_removal_does_not_reappear(self):
        t=self.p['terminals'][0];t.update(existing_id=123,status='PENDING_DELETE')
        self.p['legacy_actions']=[{'id':123,'action':'eliminar propio obsoleto'}]
        self.assertEqual(m.detach_missing_terminals(self.p,{123}),[])
        self.assertNotIn(t,self.p['terminals']);self.assertEqual(self.p['legacy_actions'],[])

    def test_initial_minima_and_balance(self):
        self.assertTrue(self.p['validation']['valid'],self.p['validation'])
        self.assertEqual([r['minimum'] for r in self.p['flows']['rows'] if r['use']=='dormitorio'],[8,4,4])
    def test_continuous_manual_move_not_grid_snapped(self):
        self.edit('move_ventilation_terminal',terminal_id=self.t,position_mm=[3423.45,2871.23])
        self.assertEqual(m.terminal(self.p,self.t)['x'],3423.45)
        self.assertEqual(m.terminal(self.p,self.t)['z'],2700)
    def test_move_on_inclined_ceiling_recomputes_z_and_normal(self):
        self.p=fixture(True);before=m.terminal(self.p,self.t)['z'];self.edit('move_ventilation_terminal',terminal_id=self.t,position_mm=[4500,3000])
        t=m.terminal(self.p,self.t);self.assertAlmostEqual(t['z']-before,150);self.assertGreater(t['slope_deg'],0)
    def test_switch_between_ceiling_faces(self):
        p=self.p;r=m.room(p,'0');a=copy.deepcopy(r['ceilings'][0]);b=copy.deepcopy(a)
        a['geometry']=mapping(box(0,0,2900,6000));b.update(id=900,uid='new',surface_id='new:face',geometry=mapping(box(3100,0,6000,6000)),plane={'origin':[0,0,3000],'normal':[0,0,1]})
        r['ceilings']=[a,b];self.p=m.seal(p);self.edit('move_ventilation_terminal',terminal_id=self.t,position_mm=[4500,3000])
        self.assertEqual(m.terminal(self.p,self.t)['ceiling_id'],900);self.assertEqual(m.terminal(self.p,self.t)['z'],3000)
    def test_outside_room_is_rejected_without_mutating_original(self):
        original=copy.deepcopy(self.p)
        with self.assertRaises(ValueError):self.edit('move_ventilation_terminal',terminal_id=self.t,position_mm=[-100,0])
        self.assertEqual(self.p,original)
    def test_obstacle_blocks_drag(self):
        self.p['obstacles']=[{'id':999,'geometry':mapping(box(4000,2000,5000,4000)),'z0':2600,'z1':3000}];self.p=m.seal(self.p)
        with self.assertRaisesRegex(ValueError,'Interferencia'):self.edit('move_ventilation_terminal',terminal_id=self.t,position_mm=[4500,3000])
    def test_connection_reservation_is_checked(self):
        self.p['obstacles']=[{'id':999,'geometry':mapping(box(4000,2000,5000,4000)),'z0':2800,'z1':2900}];self.p=m.seal(self.p)
        with self.assertRaises(ValueError):self.edit('move_ventilation_terminal',terminal_id=self.t,position_mm=[4500,3000])
    def test_add_terminal_redistributes_only_proposal(self):
        before=len(self.p['terminals']);design=m.row(self.p,'0','impulsion')['design']
        self.edit('add_ventilation_terminal',room_uid='0',role='impulsion',position_mm=[4500,4500])
        self.assertEqual(len(self.p['terminals']),before+1)
        self.assertAlmostEqual(sum(t['flow'] for t in m.active(self.p) if t['room_uid']=='0'),design)
        self.assertEqual(len({t['terminal_id'] for t in self.p['terminals']}),before+1)
    def test_delete_unplaced_terminal_removes_it(self):
        self.edit('delete_ventilation_terminal',terminal_id=self.t)
        self.assertNotIn(self.t,[t['terminal_id'] for t in self.p['terminals']]);self.assertFalse(self.p['validation']['valid'])
    def test_delete_existing_terminal_is_pending_not_native_delete(self):
        m.terminal(self.p,self.t)['existing_id']=99;self.p=m.seal(self.p);self.edit('delete_ventilation_terminal',terminal_id=self.t)
        self.assertEqual(m.terminal(self.p,self.t)['status'],'PENDING_DELETE')
    def test_flow_change_preserves_other_flows(self):
        before={t['terminal_id']:t['flow'] for t in self.p['terminals']};self.edit('set_terminal_flow',terminal_id=self.t,flow_lps=25)
        for t in self.p['terminals']:
            if t['terminal_id']!=self.t:self.assertEqual(t['flow'],before[t['terminal_id']])
        self.assertFalse(self.p['validation']['valid'])
    def test_invalid_flow_values_rejected(self):
        for value in (0,-5,float('nan'),'abc'):
            with self.assertRaises(ValueError):self.edit('set_terminal_flow',terminal_id=self.t,flow_lps=value)
    def test_room_design_without_redistribution_is_distinct(self):
        before=m.terminal(self.p,self.t)['flow'];self.edit('set_room_design_flow',room_uid='0',role='impulsion',flow_lps=30)
        self.assertEqual(m.terminal(self.p,self.t)['flow'],before);self.assertEqual(m.row(self.p,'0','impulsion')['design'],30)
    def test_room_design_redistribution_is_explicit(self):
        self.edit('set_room_design_flow',room_uid='0',role='impulsion',flow_lps=30,redistribute=True)
        self.assertEqual(m.terminal(self.p,self.t)['flow'],30)
    def test_below_normative_design_rejected(self):
        with self.assertRaises(ValueError):self.edit('set_room_design_flow',room_uid='0',role='impulsion',flow_lps=7)
    def test_rebalance_plan_needs_confirmation_and_preserves_manual(self):
        self.edit('set_terminal_flow',terminal_id=self.t,flow_lps=25);plan=m.rebalance_plan(self.p)
        self.assertTrue(plan['compatible'],plan)
        with self.assertRaises(ValueError):self.edit('rebalance_ventilation')
        self.edit('rebalance_ventilation',confirmed=True)
        self.assertEqual(m.terminal(self.p,self.t)['flow'],25);self.assertTrue(self.p['validation']['valid'],self.p['validation'])
    def test_all_locked_incompatible_balance_reported(self):
        self.edit('set_terminal_flow',terminal_id=self.t,flow_lps=25)
        for t in list(self.p['terminals']):self.edit('set_proposal_terminal_lock',terminal_id=t['terminal_id'],lock_flow=True)
        self.assertFalse(m.rebalance_plan(self.p)['compatible'])
    def test_position_lock_does_not_lock_flow(self):
        self.edit('set_proposal_terminal_lock',terminal_id=self.t,lock_position=True)
        with self.assertRaises(ValueError):self.edit('move_ventilation_terminal',terminal_id=self.t,position_mm=[3500,3500])
        self.edit('set_terminal_flow',terminal_id=self.t,flow_lps=20)
    def test_flow_lock_does_not_lock_position(self):
        self.edit('set_proposal_terminal_lock',terminal_id=self.t,lock_flow=True)
        with self.assertRaises(ValueError):self.edit('set_terminal_flow',terminal_id=self.t,flow_lps=20)
        self.edit('move_ventilation_terminal',terminal_id=self.t,position_mm=[3500,3500])
    def test_full_lock_protects_type_and_delete(self):
        self.edit('set_proposal_terminal_lock',terminal_id=self.t,lock_full=True)
        for op in ('delete_ventilation_terminal','change_terminal_type'):
            with self.assertRaises(ValueError):self.edit(op,terminal_id=self.t,role='extraccion')
    def test_reoptimization_keeps_manual_and_locked_positions(self):
        self.edit('move_ventilation_terminal',terminal_id=self.t,position_mm=[3423.45,2871.23]);before=copy.deepcopy(m.terminal(self.p,self.t))
        self.edit('reoptimize_ventilation')
        self.assertEqual(m.terminal(self.p,self.t)['position_mm'],before['position_mm'])
    def test_undo_redo_uses_proposal_not_revit(self):
        before=m.terminal(self.p,self.t)['position_mm'];self.edit('move_ventilation_terminal',terminal_id=self.t,position_mm=[3456,2876]);after=m.terminal(self.p,self.t)['position_mm']
        self.edit('undo_ventilation');self.assertEqual(m.terminal(self.p,self.t)['position_mm'],before)
        self.edit('redo_ventilation');self.assertEqual(m.terminal(self.p,self.t)['position_mm'],after)
    def test_stale_version_rejected(self):
        with self.assertRaisesRegex(ValueError,'VERSION_CONFLICT'):m.edit(self.p,'set_terminal_flow',{'terminal_id':self.t,'flow_lps':10},0)
    def test_vestidor_is_not_bedroom_or_main(self):
        self.assertEqual(airflow.classify_room('Hab. Ppal Vestidor'),'vestidor')
        self.assertFalse(any(r['room_uid']=='5' for r in self.p['flows']['rows']))
    def test_main_priorities_and_manual_override(self):
        rooms=copy.deepcopy(self.p['analysis']['rooms']);rooms[0]['name']='Habitación';rooms[1]['name']='Dormitorio principal';rooms[2]['area_m2']=1000
        self.assertEqual(airflow.choose_main_bedroom(rooms)['uid'],'1')
        self.assertEqual(airflow.choose_main_bedroom(rooms,selected_uid='2')['uid'],'2')
    def test_zone_polygon_is_room_scoped_and_undoable(self):
        self.edit('define_ventilation_zone',room_uid='0',polygon_mm=[[1000,1000],[2000,1000],[2000,2000]],use='COCINA')
        self.assertEqual(len(self.p['zones']),2);self.edit('undo_ventilation');self.assertEqual(len(self.p['zones']),1)
    def test_zone_outside_room_rejected(self):
        with self.assertRaises(ValueError):self.edit('define_ventilation_zone',room_uid='0',polygon_mm=[[-10,-10],[1000,0],[1000,1000]],use='COCINA')
    def test_dry_extraction_requires_function_confirmation(self):
        with self.assertRaisesRegex(ValueError,'CONFIRM_WARNINGS'):self.edit('add_ventilation_terminal',room_uid='0',role='extraccion',position_mm=[4500,4500],flow_lps=8)
        self.edit('add_ventilation_terminal',room_uid='0',role='extraccion',position_mm=[4500,4500],flow_lps=8,confirm_warnings=True)
        self.assertEqual(self.p['terminals'][-1]['validation_status'],'VALIDO_CON_ADVERTENCIAS')
    def test_extraction_optimized_into_kitchen_and_supply_outside(self):
        self.edit('reoptimize_ventilation',room_uid='3')
        for t in self.p['terminals']:
            if t['room_uid']=='3':self.assertEqual(bool(t['x']>=4000),t['role']=='extraccion')
    def test_room_reoptimization_preserves_other_rooms(self):
        before={t['terminal_id']:copy.deepcopy(t) for t in self.p['terminals'] if t['room_uid']!='3'}
        self.edit('reoptimize_ventilation',room_uid='3')
        for tid,t in before.items():self.assertEqual(m.terminal(self.p,tid)['position_mm'],t['position_mm'])
    def test_unbalanced_cannot_be_approved(self):
        self.edit('set_terminal_flow',terminal_id=self.t,flow_lps=25)
        with self.assertRaises(ValueError):self.edit('approve_ventilation_proposal',confirmed=True)
    def test_incompatible_family_rejects_manual_position(self):
        self.p['symbols']['impulsion']['placement']='OneLevelBasedHosted';self.p=m.seal(self.p)
        with self.assertRaisesRegex(ValueError,'alojamiento'):self.edit('move_ventilation_terminal',terminal_id=self.t,position_mm=[3500,3500])
    def test_missing_ceiling_rejects_manual_position(self):
        m.room(self.p,'0')['ceilings']=[];self.p=m.seal(self.p)
        with self.assertRaisesRegex(ValueError,'paño'):self.edit('move_ventilation_terminal',terminal_id=self.t,position_mm=[3500,3500])
    def test_stale_height_is_not_accepted_after_cached_ceiling_change(self):
        m.room(self.p,'0')['ceilings'][0]['plane']['origin'][2]+=20
        self.p=m.seal(self.p);self.assertFalse(m.refresh(self.p)['valid'])
    def test_review_function_warnings_does_not_move_locked_terminal(self):
        t=next(t for t in self.p['terminals'] if t['room_uid']=='3');tid=t['terminal_id']
        self.edit('delete_ventilation_zone',zone_id='kitchen')
        self.edit('set_proposal_terminal_lock',terminal_id=tid,lock_position=True)
        before=m.terminal(self.p,tid)['position_mm'][:]
        with self.assertRaises(ValueError):self.edit('confirm_ventilation_warnings',terminal_ids=[tid])
        self.edit('confirm_ventilation_warnings',terminal_ids=[tid],confirmed=True)
        self.assertEqual(m.terminal(self.p,tid)['position_mm'],before)
        self.assertTrue(m.terminal(self.p,tid)['warnings_confirmed'])
    def test_configuration_keeps_manual_positions_and_checks_reservation(self):
        self.edit('move_ventilation_terminal',terminal_id=self.t,position_mm=[3423.45,2871.23])
        s=copy.deepcopy(self.p['settings']);s['resolution_mm']=100
        self.edit('set_ventilation_configuration',settings=s)
        self.assertEqual(m.terminal(self.p,self.t)['position_mm'][:2],[3423.45,2871.23])
        s['connection_space_mm']=-10
        with self.assertRaises(ValueError):self.edit('set_ventilation_configuration',settings=s)
    def test_role_change_keeps_identity_and_requires_reconciliation(self):
        mixed=next(t for t in self.p['terminals'] if t['room_uid']=='3' and t['role']=='impulsion');tid=mixed['terminal_id']
        self.edit('change_terminal_type',terminal_id=tid,role='extraccion',confirm_warnings=True)
        self.assertEqual(m.terminal(self.p,tid)['symbol_id'],self.p['symbols']['extraccion']['id'])
        self.assertFalse(self.p['validation']['valid'])
    def test_reoptimization_preserves_available_alternatives(self):
        self.edit('reoptimize_ventilation',room_uid='3')
        self.assertTrue(all(len(t['alternatives'])>1 for t in self.p['terminals'] if t['room_uid']=='3'))


class Store(unittest.TestCase):
    def test_shared_proposal_roundtrip_and_compare_and_swap(self):
        old=store.ROOT
        with tempfile.TemporaryDirectory(prefix='arqt-editor-test-') as temp:
            store.ROOT=temp
            try:
                p=fixture();store.create(p);tid=p['terminals'][0]['terminal_id']
                new=store.update(p['document_id'],p['proposal_id'],1,'set_terminal_flow',{'terminal_id':tid,'flow_lps':20})
                self.assertEqual(store.get(p['document_id'],p['proposal_id'])['version'],2)
                with self.assertRaisesRegex(ValueError,'VERSION_CONFLICT'):store.update(p['document_id'],p['proposal_id'],1,'undo_ventilation')
                self.assertEqual(len(store.list_proposals(p['document_id'])),1)
            finally:store.ROOT=old


if __name__=='__main__':unittest.main()
