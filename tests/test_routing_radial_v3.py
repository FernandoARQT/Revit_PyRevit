"""Distribution acceptance tests exercise physical endpoints and full chains."""
import copy
import sys
import time
import unittest
from dataclasses import asdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ARQ-T.extension/lib'))
from test_routing_design import proposal
from test_routing_core import scene,configuration,obstacle
from test_routing_design_graph import profile
from ARQT_MEP.routing.design import endpoints,planner,schematic,model,axes,native_plan
from ARQT_MEP.routing.core.integer_master import solve
from ARQT_MEP.routing import settings


def port(owner,index,xyz=(100,500,250),normal=(1,0,0),diameter=50,connected=False):
    return {'id':owner+':connector:'+str(index),'element_key':owner,'element_id':100+index,'connector_id':index,
        'xyz_mm':list(xyz),'direction':list(normal),'local_origin_mm':list(xyz),'local_direction':list(normal),
        'size':{'shape':'Round','diameter_mm':diameter},'connected':connected,'provenance':'REVIT_CONNECTOR',
        'domain':'DomainHvac','connector_type':'End','physical_refs':[]}


def fixture():
    p=proposal();p.terminals={};devices=[];terminals=[]
    for network,y,owner in (('SUPPLY',800,'s'),('EXTRACT',1500,'e')):
        family='Distribuidor Impulsion_Suministro' if network=='SUPPLY' else 'Distribuidor Extracción_Aire viciado'
        devices.append({'id':owner,'element_id':1 if owner=='s' else 2,'family':family,'is_configured_distributor':True,
            'connectors':[port(owner,0,(200,y,250),diameter=125)]+[port(owner,i,(300,y+i*60,250)) for i in (1,2,3)]})
    for tid,network,y in (('s1','SUPPLY',400),('s2','SUPPLY',900),('e1','EXTRACT',1500)):
        owner='elbow-'+tid;xyz=(2600,y,250);original=port(tid,1,xyz,(0,0,1),125,True)
        original['physical_refs']=[{'owner_unique_id':owner,'connector_id':1}]
        lower=port(owner,1,xyz,(0,0,-1),125,True);lower['physical_refs']=[{'owner_unique_id':tid,'connector_id':1}]
        outlet=port(owner,2,xyz,(-1,0,0));devices.append({'id':owner,'element_id':200+len(devices),'family':endpoints.FAMILY,'connectors':[lower,outlet]})
        terminals.append({'id':tid,'element_id':300+len(terminals),'connectors':[original],'airflow_lps':5,'suggested_network_type':network})
        p.terminals[tid]={'terminal_id':tid,'network_type':network,'level_id':1,'position_mm':list(xyz),'flow_lps':5}
    p.catalog={'devices':devices,'terminals':terminals};p.configuration=configuration()
    p.configuration.update(equipment_assignments=[{'element_id':1,'network_type':'SUPPLY'},{'element_id':2,'network_type':'EXTRACT'}],design_clearance_mm=5,routing_strategy='GLOBAL_RADIAL_V3')
    p.snapshot=scene();p.snapshot['document_identity']=p.document_identity;p.geometry_revision=p.snapshot['geometry_revision']
    p.profiles={profile().profile_id:asdict(profile())}
    return p


class PhysicalEndpoints(unittest.TestCase):
    def test_world_move_refreshes_xyz_but_keeps_local_signature(self):
        original=port('elbow',7);saved=asdict(endpoints.endpoint(original,'SUPPLY','terminal'))
        moved=copy.deepcopy(original);moved['xyz_mm']=[5100,6500,7250];moved['direction']=[0,1,0]
        fresh=endpoints.resolve(saved,[moved])
        self.assertEqual(fresh.endpoint_id,saved['endpoint_id']);self.assertEqual(fresh.origin_xyz_mm,tuple(moved['xyz_mm']))

    def test_changed_type_or_ambiguous_signature_is_invalidated(self):
        original=port('elbow',7);saved=asdict(endpoints.endpoint(original,'SUPPLY','terminal'))
        changed=copy.deepcopy(original);changed['size']['diameter_mm']=90
        for ports in ([changed],[original,copy.deepcopy(original)]):
            with self.assertRaisesRegex(ValueError,'ELBOW_CONNECTOR_UNRESOLVED'):endpoints.resolve(saved,ports)

    def test_nearby_elbow_is_not_associated_without_physical_connection(self):
        p=fixture();p.catalog['terminals'][0]['connectors'][0]['physical_refs']=[]
        reports=endpoints.elbow_destinations(p.catalog,planner.selections(p))
        self.assertEqual(reports['s1']['status'],'ELBOW_NOT_FOUND');self.assertTrue(reports['s2']['endpoints'])

    def test_occupied_elbow_is_not_available_even_if_connected_to_cap(self):
        p=fixture();device=next(d for d in p.catalog['devices'] if d['id']=='elbow-s1');device['connectors'][1].update(connected=True,connected_to_cap=True)
        report=endpoints.elbow_destinations(p.catalog,planner.selections(p))['s1']
        self.assertEqual(report['status'],'ELBOW_CONNECTOR_OCCUPIED');self.assertFalse(report['endpoints'])

    def test_two_physically_attached_elbows_are_ambiguous(self):
        p=fixture();other=copy.deepcopy(next(d for d in p.catalog['devices'] if d['id']=='elbow-s1'));other['id']='other'
        p.catalog['devices'].append(other);p.catalog['terminals'][0]['connectors'][0]['physical_refs'].append({'owner_unique_id':'other','connector_id':1})
        self.assertEqual(endpoints.elbow_destinations(p.catalog,planner.selections(p))['s1']['status'],'ELBOW_CONNECTOR_UNRESOLVED')

    def test_occupied_source_port_reduces_available_capacity(self):
        p=fixture();p.catalog['devices'][0]['connectors'][1]['connected']=True
        self.assertEqual(endpoints.manifolds(p.catalog,p.configuration)['SUPPLY']['available_count'],2)


class JointMaster(unittest.TestCase):
    def test_resource_capacity_applies_to_both_networks_together(self):
        admitted={t:[{'status':'VALID','edge_ids':['shared'],'cost':{'total':1}}] for t in ('s','e')}
        chosen,report=solve(admitted,list(admitted),lambda *a:False,time.monotonic()+3,
            resources=[{'capacity':1,'demands':{('s',0):1,('e',0):1}}],activation_costs={'shared':1})
        self.assertEqual(len(chosen),1);self.assertEqual(report['resource_constraints'],1)

    def test_global_choice_can_use_two_corridors_when_one_has_no_capacity(self):
        admitted={t:[{'status':'VALID','edge_ids':[e],'cost':{'total':i+1}} for i,e in enumerate(('left','right'))] for t in ('s','e')}
        resources=[{'capacity':1,'demands':{(t,i):1 for t in admitted}} for i in (0,1)]
        chosen,_=solve(admitted,list(admitted),lambda *a:False,time.monotonic()+3,resources=resources,activation_costs={'left':1,'right':1})
        self.assertEqual(len(chosen),2);self.assertEqual(set(chosen.values()),{0,1})

    def test_v3_preserves_rigid_main_without_changing_legacy(self):
        cfg={'main_connection':{'kind':'RIGID_CIRCULAR','diameter_mm':125},'routing_strategy':'GLOBAL_RADIAL_V3'}
        self.assertEqual(settings.validate(cfg)['main_connection']['kind'],'RIGID_CIRCULAR')
        cfg['routing_strategy']='LEGACY';self.assertEqual(settings.validate(cfg)['main_connection']['kind'],'FLEXIBLE_CIRCULAR')


class ContinuousNativeCurveBounds(unittest.TestCase):
    def test_straight_cubic_has_unbounded_radius_and_stays_inside_axis_capsule(self):
        from ARQT_MEP.routing.core.bezier_bounds import certify
        result=certify([[0,0,0],[100,0,0],[200,0,0],[300,0,0]],150,[([0,0,0],[300,0,0])],.01)
        self.assertTrue(result['verified']);self.assertIsNone(result['radius_lower_bound_mm'])

    def test_curved_cubic_is_subdivided_and_its_radius_is_bounded(self):
        from ARQT_MEP.routing.core.bezier_bounds import certify
        r=200;k=.5522847498307936
        control=[[r,0,0],[r,k*r,0],[k*r,r,0],[0,r,0]]
        import math
        arc=[[r*math.cos(i*math.pi/128),r*math.sin(i*math.pi/128),0] for i in range(65)]
        result=certify(control,150,list(zip(arc,arc[1:])),2)
        self.assertTrue(result['verified'],result);self.assertGreater(result['cells'],1)

    def test_axis_deviation_and_tight_curve_are_not_certified(self):
        from ARQT_MEP.routing.core.bezier_bounds import certify
        control=[[0,0,0],[0,200,0],[300,200,0],[300,0,0]]
        result=certify(control,150,[([0,0,0],[300,0,0])],5)
        self.assertFalse(result['verified'])


class OrientedGeometry(unittest.TestCase):
    def test_native_face_coverage_cannot_hide_a_missing_triangle(self):
        from ARQT_MEP.routing.core.volume_bounds import planar_boundary_covered
        rings=[[(0,0),(100,0),(100,100),(0,100)]]
        triangles=[[(0,0),(100,0),(100,100)],[(0,0),(100,100),(0,100)]]
        self.assertTrue(planar_boundary_covered(rings,triangles))
        self.assertFalse(planar_boundary_covered(rings,triangles[:1]))
        shifted=[[(.001,.001),(100,.001),(100,100)],[(.001,.001),(100,100),(.001,100)]]
        self.assertTrue(planar_boundary_covered(rings,shifted,.01))
        self.assertFalse(planar_boundary_covered(rings,shifted,0))

    def test_box_certificate_keeps_xy_clearance_holes_and_sloping_planes(self):
        from ARQT_MEP.routing.core.volume_bounds import contained
        from ARQT_MEP.routing.core.spatial_model import Region
        from shapely.geometry import Polygon,mapping
        region=Region({'box_mm':[0,0,0,1000,1000,500]})
        self.assertTrue(contained([100,100,100,200,200,200],[region],10))
        self.assertFalse(contained([1,100,100,200,200,200],[region],10))
        poly=Polygon([(0,0),(1000,0),(1000,1000),(0,1000)],holes=[[(400,400),(600,400),(600,600),(400,600)]])
        hole=Region({'polygon':mapping(poly),'lower':{'z':0},'upper':{'z':500}})
        self.assertFalse(contained([300,300,100,700,700,200],[hole],10))
        slope=Region({'polygon':mapping(poly),'lower':{'z':0},'upper':{'origin':[0,0,500],'normal':[-.2,0,1]}})
        self.assertFalse(contained([100,100,400,200,200,550],[slope],10))

    def test_opposite_travel_directions_keep_distinct_physical_lanes(self):
        p=fixture();a=[[100,500,250],[2900,500,250]];b=list(reversed(a))
        p.selected_paths={tid:{'edge_ids':['shared'],'oriented_edges':{'shared':points},'points_mm':points,
            'endpoint':{'origin_xyz_mm':points[-1]}} for tid,points in (('a',a),('b',b))}
        p.corridor_graph={'edges':{'shared':{'points_mm':a,'width_mm':1000}}}
        offsets,_=axes._lanes(p,profile(),10)
        paths=[axes._offset_vertices(path,offsets[tid]) for tid,path in p.selected_paths.items()]
        self.assertGreater(abs(paths[0][0][1]-paths[1][0][1]),50)

    def test_return_turn_seed_has_real_finite_radius_and_exact_tangents(self):
        from ARQT_MEP.routing.core.oriented_path import seeds
        from ARQT_MEP.routing.core.route_geometry import primitives
        from ARQT_MEP.routing.core.natural_path import straight_vertices
        from ARQT_MEP.routing.core import geometry as g
        candidates=seeds([0,0,0],[2000,-500,0],[-1,0,0],[0,-1,0],150)
        self.assertEqual(len(candidates),4)
        for seed in candidates:
            pieces=primitives(straight_vertices(seed),150)
            self.assertTrue(pieces)
            self.assertGreater(g.dot(g.unit(g.sub(seed[1],seed[0])),[-1,0,0]),.99999)
            self.assertGreater(g.dot(g.unit(g.sub(seed[-1],seed[-2])),[0,-1,0]),.99999)
            self.assertTrue(all(p.get('radius_mm',150)>=150 for p in pieces))

    def test_only_the_actual_bore_can_ignore_its_wall(self):
        from ARQT_MEP.routing.core.spatial_model import build_spatial_model
        from ARQT_MEP.routing.core.route_geometry import validate_route_geometry
        cfg=configuration();prof=settings.profile(cfg)
        wall=obstacle('partition',[1000,0,0,1100,2000,500],'PASSABLE')
        space=build_spatial_model(scene([wall]),cfg['constraints'])
        passage={'id':'p','points_mm':[[900,500,250],[1200,500,250]],'radius_mm':30,'element_ids':['partition'],'classification':'PASSABLE'}
        route={'points_mm':[[200,500,250],[2800,500,250]],'passages':[passage],'split_passages':True}
        self.assertNotEqual(validate_route_geometry(space,route,prof)['status'],'INVALID')
        # Reusing the same host id must not exempt a second, remote wall body.
        second=obstacle('partition',[1600,0,0,1700,2000,500],'PASSABLE')
        blocked=build_spatial_model(scene([wall,second]),cfg['constraints'])
        self.assertEqual(validate_route_geometry(blocked,route,prof)['status'],'INVALID')


class CompleteSchematics(unittest.TestCase):
    def test_only_complete_chains_to_exact_elbow_endpoints_are_published(self):
        p=fixture();result=planner.calculate(p,profile(),{'seconds':8})
        self.assertEqual(len(result.solver_state['selected_paths']),3,result.connectivity)
        self.assertTrue(schematic.check(result));self.assertFalse(result.individual_routes)
        for tid,path in result.solver_state['selected_paths'].items():
            self.assertEqual(path['points_mm'][-1],list(path['endpoint']['origin_xyz_mm']))
            self.assertEqual(path['network_type'],p.terminals[tid]['network_type'])
        for c in result.corridors.values():
            self.assertTrue(c['reachable_terminal_ids']);self.assertIsNotNone(c['source_manifold_id'])
            self.assertNotEqual(c['network_type'],'SHARED')

    def test_missing_elbow_leaves_one_pending_and_preserves_other_connections(self):
        p=fixture();p.catalog['terminals'][0]['connectors'][0]['physical_refs']=[]
        result=planner.calculate(p,profile(),{'seconds':8})
        self.assertEqual(result.connectivity['s1']['status'],'ELBOW_NOT_FOUND')
        self.assertNotIn('s1',result.corridor_assignments);self.assertEqual(len(result.corridor_assignments),2)

    def test_forbidden_barrier_never_produces_a_complete_crossing(self):
        p=fixture();wall=obstacle('wall',[1300,0,0,1500,2000,500]);wall.update(wall_axis_mm=[[1400,0,0],[1400,2000,0]],wall_thickness_mm=200)
        p.snapshot['obstacles']=[wall]
        result=planner.calculate(p,profile(),{'seconds':3})
        self.assertFalse(result.corridor_assignments,result.connectivity)
        self.assertTrue(all(not r['physical_infeasibility_proven'] for r in result.connectivity.values()))

    def test_passable_wall_uses_explicit_portals_and_keeps_exact_destinations(self):
        p=fixture();wall=obstacle('partition',[1300,0,0,1500,2000,500],'PASSABLE')
        wall.update(wall_axis_mm=[[1400,0,0],[1400,2000,0]],wall_thickness_mm=200)
        p.snapshot['obstacles']=[wall];result=planner.calculate(p,profile(),{'seconds':8})
        self.assertEqual(len(result.corridor_assignments),3,result.connectivity)
        self.assertTrue(all(path['portal_ids'] for path in result.selected_paths.values()));schematic.check(result)

    def test_touching_covers_are_connected_by_the_free_union(self):
        p=fixture();p.snapshot['regions']=[{'id':'left','box_mm':[0,0,0,1500,2000,500]},
            {'id':'right','box_mm':[1500,0,0,3000,2000,500]}]
        result=planner.calculate(p,profile(),{'seconds':6})
        self.assertEqual(len(result.selected_paths),3,result.connectivity)
        self.assertTrue(all(not path['portal_ids'] for path in result.selected_paths.values()))


class EditableSchematicAndAxes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.planned=planner.calculate(fixture(),profile(),{'seconds':5})

    def test_edit_is_a_persistent_solver_constraint_and_keeps_the_real_end_fixed(self):
        p=copy.deepcopy(self.planned);cid=next(k for k,c in p.corridors.items() if len(c['reference_xy_mm'])>2)
        c=p.corridors[cid];old=copy.deepcopy(c['reference_xy_mm'][1]);target=[old[0]+25,old[1]]
        edited=model.edit(p,'move_vertex',{'corridor_id':cid,'index':1,'point_mm':target},p.revision)
        self.assertTrue(edited.constraints['schematic_paths']);self.assertFalse(edited.validation)
        recalculated=planner.calculate(edited,profile(),{'seconds':6})
        for tid,decision in edited.constraints['schematic_paths'].items():
            self.assertIn(tid,recalculated.selected_paths,recalculated.connectivity)
            self.assertEqual(recalculated.selected_paths[tid]['points_mm'][-1],list(recalculated.selected_paths[tid]['endpoint']['origin_xyz_mm']))
            self.assertIn(target,[q[:2] for q in recalculated.selected_paths[tid]['points_mm']])
        undone=model.edit(edited,'undo',{},edited.revision);self.assertEqual(undone.corridors,p.corridors)

    def test_physical_connector_cannot_be_dragged_as_a_free_waypoint(self):
        p=copy.deepcopy(self.planned);tid=next(iter(p.selected_paths));cid=p.corridor_assignments[tid][-1]
        with self.assertRaisesRegex(ValueError,'extremo real'):
            model.edit(p,'move_vertex',{'corridor_id':cid,'index':len(p.corridors[cid]['reference_xy_mm'])-1,'point_mm':[999,999]},p.revision)

    def test_direct_axes_have_unique_sources_finite_bends_and_exact_real_ends(self):
        result=axes.calculate(self.planned,profile(),{'seconds':6});routes=result.individual_routes
        self.assertEqual(len(result.validation['valid_route_ids']),3,result.validation)
        self.assertEqual(len({r['source']['id'] for r in routes.values()}),3)
        for route in routes.values():
            self.assertEqual(route['points_mm'][0],route['source']['xyz_mm'])
            self.assertEqual(route['points_mm'][-1],route['destination']['xyz_mm'])
            self.assertTrue(all(piece.get('radius_mm',100)>=100 for piece in route['primitives']))
            self.assertEqual([a['algorithm'] for a in route['search_attempts']],['DIRECT_GEOMETRY'])
            self.assertEqual(route['connection_status'],'NOT_MODELED')

    def test_axis_edit_preserves_endpoints_and_requires_new_validation(self):
        p=axes.calculate(self.planned,profile(),{'seconds':6});rid='axis:s1';ends=copy.deepcopy([p.individual_routes[rid]['points_mm'][0],p.individual_routes[rid]['points_mm'][-1]])
        edited=model.edit(p,'move_axis_waypoint',{'route_id':rid,'index':2,'point_mm':[1600,650]},p.revision)
        route=edited.individual_routes[rid];self.assertEqual(route['validation_status'],'EDITED_PENDING_RECHECK')
        self.assertEqual([route['points_mm'][0],route['points_mm'][-1]],ends);self.assertTrue(edited.constraints['axis_waypoints'][rid])
        undone=model.edit(edited,'undo',{},edited.revision);self.assertEqual(undone.individual_routes,p.individual_routes)

    def test_partial_recalculation_reuses_unaffected_checked_geometry(self):
        p=axes.calculate(self.planned,profile(),{'seconds':6})
        again=axes.calculate(p,profile(),{'seconds':6})
        self.assertEqual(set(again.validation['reused_route_ids']),set(p.individual_routes))
        edited=model.edit(p,'set_axis_height',{'route_id':'axis:s1','index':2,'z_mm':9999},p.revision)
        checked=axes.calculate(edited,profile(),{'seconds':6})
        self.assertEqual(set(checked.validation['reused_route_ids']),{'axis:s2','axis:e1'})
        self.assertNotEqual(checked.individual_routes['axis:s1']['validation_status'],'GEOMETRICALLY_VALID')

    def test_invalid_axis_height_is_retained_and_reported(self):
        p=axes.calculate(self.planned,profile(),{'seconds':6});rid='axis:s1'
        edited=model.edit(p,'set_axis_height',{'route_id':rid,'index':2,'z_mm':9999},p.revision)
        recalculated=axes.calculate(edited,profile(),{'seconds':3})
        self.assertNotEqual(recalculated.individual_routes[rid]['validation_status'],'GEOMETRICALLY_VALID')
        self.assertEqual(edited.constraints['axis_waypoints'][rid][1]['xyz_mm'][2],9999)

    def test_axis_lock_blocks_editing(self):
        p=axes.calculate(self.planned,profile(),{'seconds':6});rid='axis:s1'
        locked=model.edit(p,'set_axis_lock',{'route_id':rid,'locked':True},p.revision)
        with self.assertRaisesRegex(ValueError,'bloqueado'):model.edit(locked,'move_axis_waypoint',{'route_id':rid,'index':1,'point_mm':[500,500]},locked.revision)

    def test_model_plan_reports_pending_mains_and_never_certifies_a_schematic(self):
        result=native_plan.prepare(self.planned)
        self.assertFalse(result['ready']);self.assertEqual(result['native_status'],'NOT_EVALUATED')
        self.assertIn('AXES_REQUIRED',{r['code'] for r in result['blockers']})
        detailed=axes.calculate(self.planned,profile(),{'seconds':6});plan=native_plan.prepare(detailed)
        self.assertFalse(plan['ready']);self.assertTrue(any(r['code']=='HRV_CONNECTOR_UNRESOLVED' for r in plan['blockers']))

    def test_approval_is_explicit_and_bound_to_the_exact_revision(self):
        p=axes.calculate(self.planned,profile(),{'seconds':6});plan=native_plan.prepare(p)
        with self.assertRaisesRegex(ValueError,'confirmación explícita'):native_plan.authorize(p,plan['approval_token'],False)
        changed=copy.deepcopy(p);changed.revision+=1
        with self.assertRaisesRegex(ValueError,'STALE_APPROVAL'):native_plan.authorize(changed,plan['approval_token'],True)

    def test_native_write_is_rejected_by_read_only_dispatch(self):
        from ARQT_MEP.routing.mcp import dispatch
        class Doc:IsFamilyDocument=False
        with self.assertRaisesRegex(ValueError,'allow_write'):dispatch(Doc(),'routing_design_commit',{'proposal_id':'fixture'})


if __name__=='__main__':unittest.main()
