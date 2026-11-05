"""V2 physical envelopes and continuous union regressions; no Revit writes."""
import unittest
from shapely.geometry import box, mapping
from test_routing_core import scene, obstacle, configuration
from test_routing_topology import fixture
from ARQT_MEP.routing.core.spatial_model import build_spatial_model, Region
from ARQT_MEP.routing.core.plenum_bridges import derive
from ARQT_MEP.routing.core.passages import generate_passages
from ARQT_MEP.routing.systems.heat_recovery_topology import requests
from ARQT_MEP.routing.construction_policy import construction_rule


class V2Geometry(unittest.TestCase):
    def test_native_polygon_reduction_never_fills_concavities_or_holes(self):
        from shapely.geometry import Polygon
        from ARQT_MEP.routing.core.polygon_cover import native_subset
        polygon=Polygon([(0,0),(.1,0),(10,0),(10,10),(5,10),(5,5),(4.9,5),(4.9,10),(0,10)],
            [[(1,1),(1,3),(3,3),(3,1)]])
        reduced=native_subset(polygon,1)
        self.assertTrue(polygon.covers(reduced))
        self.assertLessEqual(reduced.area,polygon.area)
        self.assertEqual(len(reduced.interiors),1)
        self.assertGreater(reduced.area,polygon.area-1)

    def test_whole_body_outside_or_spanning_unmodelled_gap_is_unverified(self):
        from ARQT_MEP.routing.core.geometry import box_mesh
        data=scene();data['regions']=[{'box_mm':[0,0,0,1000,2000,500]},
            {'box_mm':[1100,0,0,3000,2000,500]}]
        space=build_spatial_model(data)
        self.assertTrue(space.polyhedron_allowed(box_mesh([100,100,100,900,900,200])[0]))
        self.assertFalse(space.polyhedron_allowed(box_mesh([900,100,100,1200,900,200])[0]))
        self.assertFalse(space.polyhedron_allowed(box_mesh([3100,100,100,3200,900,200])[0]))

    def test_large_adapter_is_local_unknown_geometry_and_not_duct_envelope(self):
        from ARQT_MEP.routing.core.component_envelopes import describe
        route={'route_id':'r','section':{'outer_diameter_mm':75,'insulation_mm':0},'clearance_mm':15,
            'primitives':[{'kind':'LINE','start':[0,0,100],'end':[1000,0,100]}],
            'required_transitions':[{'endpoint':'source','from_mm':125,'to_mm':75}],
            'source':{'xyz_mm':[0,0,0]},'destination':{'xyz_mm':[1000,0,100]},
            'connection_anchors_mm':{'source':[0,0,100],'destination':[1000,0,100]}}
        pieces=describe(route,configuration(),{'flexible_duct':{'transition_type_id':12}})
        self.assertEqual(pieces[0]['outer_dimensions_mm'],{'outer_diameter_mm':75})
        fitting=next(p for p in pieces if p['component_type']=='Transition')
        self.assertIsNone(fitting['geometry'])
        self.assertEqual(fitting['geometry_verification_status'],'PENDING_LOCAL_FITTING_VERIFICATION')
        self.assertEqual(fitting['native_family_type_id'],12)

    def test_transition_does_not_inflate_bulk_duct_or_bend(self):
        catalog, config, destinations=fixture()
        catalog['configured_model']={'flexible_duct':{'sizing_diameters_mm':[75,90,125,160],'transition_type_id':123}}
        routes,_,_,overrides=requests(catalog,config,destinations)
        for route in routes:
            local=overrides[route['route_id']]
            self.assertEqual(local['clearance_mm'],config['clearance_mm'])
            self.assertEqual(local['bend_radius_mm'],config['bend_radius_mm'])
            self.assertEqual(route['sizing']['connection_geometry_status'],'PENDING_LOCAL_FITTING_VERIFICATION')

    def test_thin_adjacent_regions_do_not_add_half_search_cell(self):
        data=scene(height=121)
        data['regions']=[{'box_mm':[0,0,0,1500,2000,121]}, {'box_mm':[1500,0,0,3000,2000,121]}]
        space=build_spatial_model(data)
        self.assertTrue(space.allowed((1400,500,60.5),(1600,500,60.5),60))
        self.assertFalse(space.allowed((1400,500,59),(1600,500,59),60))
        data['regions'][1]['box_mm'][0]=1501
        self.assertFalse(build_spatial_model(data).allowed((1400,500,60.5),(1600,500,60.5),60))

    def test_differing_planes_have_continuous_union_without_fictitious_height(self):
        data=scene(height=121)
        data['regions']=[{'box_mm':[0,0,0,1500,2000,121]}, {'box_mm':[1500,0,.1,3000,2000,121.1]}]
        space=build_spatial_model(data)
        self.assertTrue(space.allowed((1400,500,60.6),(1600,500,60.6),60))
        self.assertFalse(space.allowed((1400,500,60),(1600,500,60),60))

    def test_forbidden_box_is_not_segment_bounding_box(self):
        space=build_spatial_model(scene(),{'forbidden_regions':[{'box_mm':[0,80,0,20,100,500]}]})
        self.assertFalse(space.forbidden_hit((0,0,250),(100,100,250),5))
        self.assertTrue(space.forbidden_hit((0,90,250),(100,90,250),5))

    def test_bridge_uses_conservative_bound_for_slightly_different_slopes(self):
        def region(x0,x1,z):
            return Region({'id':str(x0),'kind':'FALSE_CEILING','polygon':mapping(box(x0,0,x1,1000)),
                'lower':{'z':0},'upper':{'plane':{'origin':[0,0,z],'normal':[0,-.01,1]}}})
        wall=obstacle('wall',[1000,0,0,1100,1000,40])
        wall.update(wall_axis_mm=[[1050,0,0],[1050,1000,0]],wall_thickness_mm=100)
        from ARQT_MEP.routing.core.spatial_model import Obstacle
        bridges=derive([region(0,1000,200),region(1100,2100,200.1)],[Obstacle(wall)])
        self.assertTrue(bridges)
        self.assertLessEqual(bridges[0]['upper']['z'],200)

    def test_roof_category_and_floor_roof_name_are_absolute(self):
        self.assertTrue(construction_rule('Unnamed roof type','OST_Roofs')['hard_forbidden'])
        self.assertTrue(construction_rule('Forjado de cubierta','OST_Floors')['hard_forbidden'])
        self.assertTrue(construction_rule('Losa alveolar de cubierta','OST_Floors')['hard_forbidden'])

    def test_structural_wall_candidates_require_review_without_evaluable_list(self):
        wall=obstacle('wall',[1450,0,0,1550,2000,500],'CONDITIONAL')
        wall.update(wall_axis_mm=[[1500,0,0],[1500,2000,0]],wall_thickness_mm=100)
        request={'route_id':'r','source':{'xyz_mm':[500,500,250]},'destination':{'xyz_mm':[2500,500,250]}}
        config=configuration()
        passages=generate_passages(build_spatial_model(scene([wall])),[request],config)
        self.assertTrue(passages)
        self.assertTrue(all(p['classification']=='CONDITIONAL' and p['approval_status']=='PENDING_TECHNICAL_APPROVAL'
            and p['design_state']=='PROPOSED_STRUCTURAL_PENETRATION' for p in passages))
        wall['hard_forbidden']=True
        self.assertFalse(generate_passages(build_spatial_model(scene([wall])),[request],config))

    def test_passage_generation_reaches_wall_after_500_candidates(self):
        walls=[]
        for i in range(60):
            x=1000+i*1000
            wall=obstacle('wall'+str(i),[x-50,0,0,x+50,2000,500],'PASSABLE')
            wall.update(wall_axis_mm=[[x,0,0],[x,2000,0]],wall_thickness_mm=100)
            walls.append(wall)
        data=scene(walls);data['regions'][0]['box_mm'][3]=62000
        request={'route_id':'r','source':{'xyz_mm':[200,500,250]},'destination':{'xyz_mm':[61500,500,250]}}
        passages=generate_passages(build_spatial_model(data),[request],configuration())
        self.assertGreater(len(passages),500)
        self.assertEqual({p['element_ids'][0] for p in passages},{w['id'] for w in walls})


if __name__=='__main__':unittest.main()
