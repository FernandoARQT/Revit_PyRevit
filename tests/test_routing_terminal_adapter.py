"""Real adapter geometry choices, inclined axes and selected endpoints."""
import copy
import json
import math
import unittest
from unittest.mock import patch
from test_routing_core import scene,configuration,request,profile
from ARQT_MEP.routing.core import geometry as g
from ARQT_MEP.routing.core.terminal_adapter import variants,rotate,ANGLES
from ARQT_MEP.routing.core.multi_route import plan_route
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core.global_incumbent import validate_network
from ARQT_MEP.routing.core.candidate_repository import context_signature


def adapter(axis=(0,0,1)):
    pivot=[2500,1000,200]
    return dict(id='adapter',element_id=2,type_id=3,pivot_mm=pivot,axis=axis,
        terminal_port={'id':'grille','xyz_mm':pivot},outlets=[dict(id='p'+str(i),connector_id=i,
        xyz_mm=g.add(pivot,offset),direction=[1,0,0],connected=False) for i,offset in
        ((2,[132,54,115.5]),(3,[132,-54,115.5]))])


class TerminalAdapter(unittest.TestCase):
    def test_adapter_anchor_reserves_bend_space_laterally(self):
        from ARQT_MEP.routing.core.multi_route import compact_anchor
        c=configuration();p=profile(c);space=build_spatial_model(scene())
        port={'id':'adapter-outlet','kind':'TERMINAL_ADAPTER_OUTLET','xyz_mm':[1000,1000,250],'direction':[1,0,0]}
        origin,anchor=compact_anchor(space,port,p,c)
        self.assertGreaterEqual(g.distance(origin,anchor),p.bend_radius_mm+p.radius_mm)
        self.assertEqual(anchor[2],origin[2])

    def test_short_connection_bend_is_not_hidden_by_pending_stub(self):
        from ARQT_MEP.routing.core.route_geometry import validate_route_geometry
        c=configuration();space=build_spatial_model(scene())
        route={'points_mm':[[200,200,250],[500,200,250],[500,250,250],[450,250,250]],
            'routing_points_mm':[[200,200,250],[500,200,250]],'connection_validation_pending':True,
            'endpoint_stubs':[{'adapter_endpoint':True}]}
        result=validate_route_geometry(space,route,profile(c))
        self.assertEqual(result['reason'],'ADAPTER_CONNECTION_BEND_NOT_CONSTRUCTIBLE')

    def test_worker_round_trip_keeps_pose_authorized_and_rejects_changes(self):
        from ARQT_MEP.routing.core.terminal_adapter import allowed_destination
        choices=variants(adapter(),{'xyz_mm':[1000,1000,300]},'SUPPLY')
        req={'destination':choices[0],'destination_alternatives':choices[1:]}
        # Legacy tuple-valued descriptors must also survive checkpoints.
        req['destination']['direction']=tuple(req['destination']['direction'])
        received=json.loads(json.dumps(req['destination']))
        self.assertTrue(allowed_destination(received,req))
        received['xyz_mm'][0]+=.1
        self.assertFalse(allowed_destination(received,req))

    def test_rotated_body_replaces_old_obstacle_without_changing_other_geometry(self):
        from test_routing_core import obstacle
        from ARQT_MEP.routing.core.terminal_adapter import posed_space
        space=build_spatial_model(scene([obstacle('adapter',[400,400,100,600,600,400]),
            obstacle('roof',[900,400,100,1100,600,400])]))
        # Prime the collision cache before changing the pose.
        self.assertFalse(space.free((500,500,250),(500,500,250),5))
        pose={'element_key':'adapter','element_id':2,'body_box_mm':[1400,400,100,1600,600,400]}
        changed=posed_space(space,pose)
        self.assertTrue(changed.free((500,500,250),(500,500,250),5))
        self.assertFalse(changed.free((1500,500,250),(1500,500,250),5))
        self.assertFalse(changed.free((1000,500,250),(1000,500,250),5))
        self.assertFalse(space.free((500,500,250),(500,500,250),5))

    def test_two_outlets_at_seven_angles_and_center_towards_distributor(self):
        a=adapter();source={'xyz_mm':[1000,1000,300]};choices=variants(a,source,'SUPPLY')
        self.assertEqual(len(choices),14)
        self.assertEqual({p['adapter_pose']['offset_deg'] for p in choices},set(ANGLES))
        midpoint=g.mul(g.add(choices[0]['xyz_mm'],choices[1]['xyz_mm']),.5)
        self.assertLess(midpoint[0],a['pivot_mm'][0]);self.assertAlmostEqual(midpoint[1],1000)
        for p in choices:
            original=next(o for o in a['outlets'] if o['connector_id']==p['connector_id'])
            self.assertAlmostEqual(g.distance(p['xyz_mm'],a['pivot_mm']),g.distance(original['xyz_mm'],a['pivot_mm']))
            self.assertEqual(p['physical_id'],original['id'])
        self.assertEqual(a,adapter())

    def test_inclined_rotation_preserves_axial_height_and_radius(self):
        axis=g.unit([0,-.158,.987]);point=[132,54,115.5]
        for angle in ANGLES:
            result=rotate(point,axis,math.radians(angle))
            self.assertAlmostEqual(g.dot(result,axis),g.dot(point,axis))
            self.assertAlmostEqual(g.norm(result),g.norm(point))

    def test_selected_outlet_is_validated_instead_of_grille(self):
        config=configuration();space=build_spatial_model(scene())
        req=request(start=(200,500,250),end=(2800,500,250))
        selected=dict(id='outlet2',xyz_mm=[2800,1000,250],adapter_pose={'offset_deg':15})
        req['destination_alternatives']=[selected]
        route=plan_route(space,dict(req,destination=selected,destination_alternatives=[]),profile(config),config)
        report=validate_network(space,[req],config,{req['route_id']:route})
        self.assertTrue(report['valid'],report['issues'])
        wrong=copy.deepcopy(route);wrong['chosen_destination']=req['destination']
        self.assertFalse(validate_network(space,[req],config,{req['route_id']:wrong})['valid'])

    def test_blocked_first_outlet_does_not_terminate_pose_search(self):
        config=configuration();req=request();req['destination']['adapter_pose']={'offset_deg':0}
        second=dict(req['destination'],id='other-outlet',adapter_pose={'offset_deg':15})
        req['destination_alternatives']=[second]
        def fake(space,r,p,s,scenario,cancelled,diversity,deadline):
            if r['destination']['id']!=second['id']:return {'status':'INVALID','reason':'BLOCKED'}
            return {'status':'VALID','points_mm':[r['source']['xyz_mm'],second['xyz_mm']]}
        with patch('ARQT_MEP.routing.core.multi_route._plan_route_impl',side_effect=fake):
            result=plan_route(None,req,profile(config),config)
        self.assertEqual(result['chosen_destination'],second)
        self.assertEqual(len(result['adapter_attempts']),2)

    def test_checkpoint_context_independent_of_selected_primary_pose(self):
        config=configuration();space=build_spatial_model(scene());req=request()
        other=dict(req['destination'],id='other')
        req['destination_alternatives']=[other]
        swapped=dict(req,destination=other,destination_alternatives=[req['destination']])
        self.assertEqual(context_signature(space,[req],config),context_signature(space,[swapped],config))


class AdapterTopology(unittest.TestCase):
    def fixture(self):
        from test_routing_topology import fixture,port
        catalog,config,destinations=fixture();terminal=catalog['terminals'][0]
        original=terminal['connectors'][0];original['connected']=True
        a=adapter();a['pivot_mm']=original['xyz_mm'];a['terminal_port']=copy.deepcopy(original)
        a['outlets']=[dict(port('adapter:'+str(i),75,x=2100),xyz_mm=[2100,100+50*i,350]) for i in (2,3)]
        terminal['terminal_adapter']=a;catalog['terminal_adapters_required']=True
        return catalog,config,[destinations[0]],a

    def test_connected_lower_port_still_generates_branch_to_lateral_outlet(self):
        from ARQT_MEP.routing.systems.heat_recovery_topology import requests
        cat,c,d,a=self.fixture();routes,_,topology,_=requests(cat,c,d)
        branch=next(r for r in routes if r['route_kind']=='BRANCH')
        self.assertEqual(len(branch['destination_alternatives']),13)
        self.assertEqual(branch['destination']['kind'],'TERMINAL_ADAPTER_OUTLET')
        self.assertEqual(branch['terminal_port']['id'],d[0]['port_id'])
        self.assertEqual(topology['counts']['SUPPLY']['new_branches'],1)

    def test_existing_branch_with_free_second_outlet_is_preserved(self):
        from ARQT_MEP.routing.systems.heat_recovery_topology import requests
        cat,c,d,a=self.fixture();a['outlets'][0]['connected']=True
        routes,_,topology,_=requests(cat,c,d)
        self.assertEqual(routes,[])
        self.assertEqual(topology['counts']['SUPPLY']['preserved_terminals'],1)

    def test_cap_is_not_mistaken_for_existing_branch(self):
        from ARQT_MEP.routing.systems.heat_recovery_topology import requests
        cat,c,d,a=self.fixture();a['outlets'][0].update(connected=True,connected_to_cap=True)
        routes,_,topology,_=requests(cat,c,d)
        branch=next(r for r in routes if r['route_kind']=='BRANCH')
        self.assertEqual(branch['error'],'TERMINAL_ADAPTER_MANUAL_CAP_REQUIRES_REVIEW')
        self.assertEqual(topology['counts']['SUPPLY']['preserved_terminals'],0)

    def test_native_missing_adapter_is_explicit_blocker(self):
        from ARQT_MEP.routing.systems.heat_recovery_topology import requests
        cat,c,d,a=self.fixture();del cat['terminals'][0]['terminal_adapter']
        cat['terminals'][0]['connectors'][0]['connected']=False
        routes,*_=requests(cat,c,d)
        self.assertEqual(next(r for r in routes if r['route_kind']=='BRANCH')['error'],'MISSING_TERMINAL_ADAPTER')

    def test_preparation_requires_explicit_write_permission(self):
        from ARQT_MEP.routing.mcp import dispatch
        with self.assertRaisesRegex(ValueError,'allow_write'):
            dispatch(None,'prepare_terminal_adapters',allow_write=False)


if __name__=='__main__':unittest.main()
