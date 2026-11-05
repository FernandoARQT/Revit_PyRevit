"""Executable independent routing fixtures; no Revit geometry is mocked as valid."""
import copy
import math
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ARQ-T.extension/lib'))
from ARQT_MEP.routing.settings import validate,MEPSystemProfile,effective_radius
from ARQT_MEP.routing.core import geometry as g
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core.astar_3d import find_route_3d
from ARQT_MEP.routing.core.multi_route import plan_route,optimize_multiple_routes,route_alternatives
from ARQT_MEP.routing.core.route_geometry import simplify_route,primitives,validate_route_geometry


def obstacle(id,b,classification='FORBIDDEN'):
    v,f=g.box_mesh(b)
    return dict(id=id,box_mm=b,classification=classification,meshes=[dict(vertices_mm=v,faces=f)])


def scene(obstacles=(),height=500):
    return {'regions':[{'id':'room','kind':'EXPLICIT','box_mm':[0,0,0,3000,2000,height]}],
            'obstacles':list(obstacles),'document_identity':'fixture','geometry_revision':'fixture'}


def configuration():
    return validate({'parallel':{'enabled':False},'section':{'outer_diameter_mm':50,'nominal_diameter_mm':50},'clearance_mm':5,'bend_radius_mm':50,
         'connection_straight_mm':0,'search':{'coarse_resolution_mm':100,'fine_resolution_mm':50,'local_refinement_mm':25,
         'max_seconds':3,'max_nodes':30000,'max_total_seconds':10}})


def profile(settings):return MEPSystemProfile(settings['section'],settings['clearance_mm'],settings['bend_radius_mm'],settings['weights'])


def request(id='r',start=(200,500,250),end=(2800,500,250),network='SUPPLY'):
    return {'route_id':id,'network_type':network,'source':{'id':'source'+id,'xyz_mm':start,'provenance':'MANUAL_REFERENCE'},
            'destination':{'id':'end'+id,'xyz_mm':end,'provenance':'MANUAL_REFERENCE'},'airflow_lps':8}


class Geometry(unittest.TestCase):
    def test_segment_triangle_and_inside_real_mesh(self):
        v,f=g.box_mesh([100,100,100,200,200,200]);solid=g.TriangleSolid(v,f)
        self.assertTrue(solid.inside((150,150,150)))
        self.assertFalse(solid.inside((300,150,150)))
        self.assertTrue(solid.capsule((0,150,150),(300,150,150),1))
        self.assertFalse(solid.capsule((0,50,150),(300,50,150),10))

    def test_triangle_distance(self):
        self.assertAlmostEqual(g.point_triangle((.2,.2,2),(0,0,0),(1,0,0),(0,1,0)),2)
        self.assertAlmostEqual(g.segment_distance((0,0,0),(10,0,0),(5,-5,0),(5,5,0)),0)

    def test_flat_ended_connection_volume(self):
        v,f=g.cylinder_mesh((0,0,100),(0,0,300),25)
        self.assertAlmostEqual(min(p[2] for p in v),100)
        solid=g.TriangleSolid(v,f)
        self.assertTrue(solid.inside((0,0,200)))
        self.assertFalse(solid.inside((0,0,90)))

    def test_profile_validation(self):
        self.assertAlmostEqual(effective_radius(validate({})),77.5)
        for change in ({'clearance_mm':-1},{'section':{'outer_diameter_mm':-1}},
                       {'search':{'fine_resolution_mm':0}},{'weights':{'bend':-1}}):
            with self.assertRaises(ValueError):validate(change)

    def test_rectangular_profile_has_conservative_envelope(self):
        s=validate({'section':{'shape':'RECTANGULAR','width_mm':100,'height_mm':50}})
        self.assertAlmostEqual(effective_radius(s),math.hypot(100,50)/2+15)


class Routing(unittest.TestCase):
    def setUp(self):self.s=configuration();self.p=profile(self.s)
    def route(self,data=None,r=None):
        space=build_spatial_model(data or scene(),self.s['constraints'])
        return plan_route(space,r or request(),self.p,self.s)

    def test_straight_free(self):
        r=self.route()
        self.assertEqual(r['status'],'VALID_WITH_WARNINGS',r)
        self.assertAlmostEqual(r['length_m'],2.6);self.assertEqual(r['bends'],0)

    def test_obstacle_central(self):
        r=self.route(scene([obstacle('block',[1200,200,0,1800,800,500])]))
        self.assertEqual(r['status'],'VALID_WITH_WARNINGS',r)
        self.assertGreater(r['length_m'],2.6)
        self.assertGreater(r['bends'],0)

    def test_two_hundred_and_three_hundred_mm_plenums(self):
        self.s=validate({'connection_straight_mm':0,'search':{'max_seconds':3,'max_nodes':30000}});self.p=profile(self.s)
        for h in (200,300):
            r=self.route(scene(height=h),request(start=(200,500,h/2),end=(2800,500,h/2)))
            self.assertEqual(r['status'],'VALID_WITH_WARNINGS',r)

    def test_140_mm_cavity_rejects_125_plus_clearance(self):
        self.s=validate({'connection_straight_mm':0,'search':{'max_seconds':1,'max_nodes':1000}});self.p=profile(self.s)
        r=self.route(scene(height=140),request(start=(200,500,70),end=(2800,500,70)))
        self.assertNotIn(r['status'],('VALID','VALID_WITH_WARNINGS'))

    def test_inclined_plenum(self):
        data=scene();data['regions']=[{'id':'sloped','polygon':{'type':'Polygon','coordinates':[[[0,0],[3000,0],[3000,2000],[0,2000],[0,0]]]},
            'lower':{'plane':{'origin':[0,0,0],'normal':[-.1,0,1]}},
            'upper':{'plane':{'origin':[0,0,500],'normal':[-.1,0,1]}}}]
        r=self.route(data,request(start=(200,500,250),end=(2800,500,550)))
        self.assertEqual(r['status'],'VALID_WITH_WARNINGS',r)

    def test_forbidden_beam_and_column(self):
        for cls in ('beam','column'):
            r=self.route(scene([obstacle(cls,[1000,0,0,2000,2000,500])]))
            self.assertNotIn(r['status'],('VALID','VALID_WITH_WARNINGS'))

    def test_authorized_wall_portal_charged_once(self):
        wall=obstacle('wall',[1400,0,0,1600,2000,500],'PASSABLE')
        passage={'id':'p','points_mm':[[1200,500,250],[1800,500,250]],'element_ids':['wall'],'radius_mm':100,'classification':'PASSABLE'}
        self.s['constraints']['passages']=[passage]
        r=self.route(scene([wall]))
        self.assertEqual(r['status'],'VALID_WITH_WARNINGS',r)
        self.assertEqual(len(r['passages']),1)
        self.assertEqual(r['cost']['components']['passages'],self.s['weights']['crossing'])

    def test_structural_passage_stays_conditional(self):
        wall=obstacle('wall',[1400,0,0,1600,2000,500],'CONDITIONAL')
        self.s['constraints']['passages']=[{'id':'p','points_mm':[[1200,500,250],[1800,500,250]],'element_ids':['wall'],'radius_mm':100,'classification':'CONDITIONAL','evaluable_zone':'wall'}]
        space=build_spatial_model(scene([wall]),self.s['constraints'])
        a=plan_route(space,request(),self.p,self.s,'A')
        b=plan_route(space,request(),self.p,self.s,'B')
        self.assertNotIn(a['status'],('VALID','VALID_WITH_WARNINGS'))
        self.assertEqual(b['status'],'CONDITIONAL',b)

    def test_waypoint_on_valid_path(self):
        r=request();r['waypoints_mm']=[[1500,500,250]]
        route=self.route(r=r)
        self.assertEqual(route['status'],'VALID_WITH_WARNINGS',route)

    def test_waypoint_inside_forbidden_obstacle(self):
        r=request();r['waypoints_mm']=[[1500,500,250]]
        route=self.route(scene([obstacle('x',[1400,400,100,1600,600,400])]),r)
        self.assertEqual(route['reason'],'WAYPOINT_BLOCKED')

    def test_simplification_cannot_cross_obstacle(self):
        space=build_spatial_model(scene([obstacle('x',[1000,400,0,2000,600,500])]))
        points=[(200,500,250),(200,1000,250),(2800,1000,250),(2800,500,250)]
        result=simplify_route(points,space,self.p.radius_mm)
        self.assertGreaterEqual(len(result),4)

    def test_bend_envelope_rejects_short_turns(self):
        with self.assertRaises(ValueError):primitives([(0,0,0),(10,0,0),(10,10,0)],125)

    def test_repeated_deterministic(self):
        a=self.route();b=self.route()
        self.assertEqual(a['points_mm'],b['points_mm']);self.assertEqual(a['cost'],b['cost'])

    def test_multiple_radial_and_distinct_networks(self):
        requests=[request(str(i),(200,200+i*300,250),(2800,200+i*300,250),'SUPPLY' if i%2 else 'EXTRACT') for i in range(5)]
        result=optimize_multiple_routes(build_spatial_model(scene()),requests,self.p,self.s)
        self.assertEqual(len(result['routes']),5)
        self.assertTrue(all(r['status']=='VALID_WITH_WARNINGS' for r in result['routes'].values()),result)

    def test_crossing_conduits_different_elevations(self):
        space=build_spatial_model(scene(height=800));space.reserve('first',[(200,1000,200),(2800,1000,200)],self.p.radius_mm)
        self.assertTrue(space.free((1500,200,600),(1500,1800,600),self.p.radius_mm))
        self.assertFalse(space.free((1500,200,200),(1500,1800,200),self.p.radius_mm))


if __name__=='__main__':unittest.main()
