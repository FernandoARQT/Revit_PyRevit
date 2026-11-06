"""Spatial graph proofs on continuous geometry; no claims from centre samples."""
import copy
import math
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ARQ-T.extension/lib'))
from shapely.geometry import Polygon,mapping
from test_routing_core import scene,obstacle
from ARQT_MEP.routing.design.model import DuctProfile
from ARQT_MEP.routing.design.graph import generate
from ARQT_MEP.routing.design.spatial import valid_height_intervals
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core import geometry as g


def profile():return DuctProfile('fixture-50','SUPPLY',50,40,50,0,100,'SYNTHETIC_FIXTURE',True)


class CorridorGraph(unittest.TestCase):
    def generate(self,data,**kwargs):return generate(data,profile(),{'room':1},spacing_mm=600,max_nodes=150,seconds=8,**kwargs)

    def test_free_straight_room_has_verified_edges_and_variable_sections(self):
        data=scene();graph=self.generate(data);space=build_spatial_model(data)
        self.assertGreater(len(graph['edges']),0)
        for edge in graph['edges'].values():
            self.assertTrue(space.free(*edge['reference_xyz_mm'],35))
            self.assertGreaterEqual(len(edge['width_profile']),2)
            self.assertTrue(all(s['width_mm']>0 for s in edge['width_profile']))
        self.assertFalse(graph['physical_infeasibility_proven'])
        self.assertTrue(any(e['kind']=='MEDIAL' for e in graph['edges'].values()))
        self.assertTrue(any(e['kind']=='PERIMETER' for e in graph['edges'].values()))

    def test_concrete_and_unknown_are_not_crossed(self):
        for classification in ('CONDITIONAL','UNKNOWN','FORBIDDEN','PASSABLE'):
            data=scene([obstacle('wall',[1400,0,0,1600,2000,500],classification)])
            graph=self.generate(data)
            self.assertTrue(graph['edges'])
            for e in graph['edges'].values():
                a,b=e['reference_xyz_mm'];self.assertFalse(min(a[0],b[0])<1400 and max(a[0],b[0])>1600)

    def test_sloping_ceiling_generates_different_axis_heights(self):
        data=scene();data['regions']=[{'id':'room','polygon':mapping(Polygon([(0,0),(3000,0),(3000,2000),(0,2000)])),
            'lower':{'plane':{'origin':[0,0,0],'normal':[-.1,0,1]}},'upper':{'z':700}}]
        graph=self.generate(data)
        heights={round(n['xyz_mm'][2],2) for n in graph['nodes'].values()}
        self.assertGreater(len(heights),4)

    def test_disjoint_height_intervals_do_not_skip_a_thin_obstacle(self):
        data=scene([obstacle('thin',[0,0,248,3000,2000,252])])
        intervals=valid_height_intervals(build_spatial_model(data),1500,1000,profile(),10)
        self.assertEqual(len(intervals),2)
        self.assertLess(intervals[0][1],213.001);self.assertGreater(intervals[1][0],286.999)
        thick=DuctProfile('insulated','SUPPLY',50,40,50,20,100)
        larger=valid_height_intervals(build_spatial_model(data),1500,1000,thick,10)
        self.assertLess(larger[0][1],intervals[0][1])

    def test_holes_are_preserved(self):
        data=scene();poly=Polygon([(0,0),(3000,0),(3000,2000),(0,2000)],holes=[[(1200,600),(1800,600),(1800,1400),(1200,1400)]])
        data['regions']=[{'id':'room','polygon':mapping(poly),'lower':{'z':0},'upper':{'z':500}}]
        graph=self.generate(data);space=build_spatial_model(data)
        self.assertTrue(graph['edges'])
        self.assertTrue(all(space.allowed(*edge['reference_xyz_mm'],35) for edge in graph['edges'].values()))

    def test_cancelled_graph_is_partial_not_impossible(self):
        graph=self.generate(scene(),cancelled=lambda:True)
        self.assertFalse(graph['stats']['enumeration_finished']);self.assertTrue(graph['stats']['cancelled'])
        self.assertFalse(graph['physical_infeasibility_proven'])

    def test_no_invented_plenum(self):
        graph=generate({'regions':[],'obstacles':[],'geometry_revision':'none'},profile(),{},seconds=1)
        self.assertFalse(graph['nodes']);self.assertFalse(graph['edges'])

    def test_region_narrower_than_profile_collapses_without_crash(self):
        data=scene();data['regions'][0]['box_mm']=[0,0,0,30,3000,500]
        graph=self.generate(data)
        self.assertFalse(graph['nodes']);self.assertFalse(graph['physical_infeasibility_proven'])

    def test_sloping_roof_box_does_not_erase_real_headroom(self):
        vertices,faces=g.box_mesh([0,0,300,3000,2000,400])
        vertices=[[x,y,z+.1*x] for x,y,z in vertices]
        roof={'id':'roof','classification':'FORBIDDEN','box_mm':g.bounds(vertices),
            'meshes':[{'vertices_mm':vertices,'faces':faces}]}
        poly=Polygon([(0,0),(3000,0),(3000,2000),(0,2000)])
        data={'regions':[{'id':'room','polygon':mapping(poly),'lower':{'z':400},
            'upper':{'plane':{'origin':[0,0,300],'normal':[-.1,0,1]}}}],
            'obstacles':[roof],'geometry_revision':'roof-regression'}
        space=build_spatial_model(data);intervals=valid_height_intervals(space,2600,1000,profile(),10)
        self.assertTrue(intervals)
        for lo,hi in intervals:self.assertTrue(space.free((2600,1000,lo),(2600,1000,hi),35))

    def test_full_height_solid_interior_stays_blocked(self):
        data=scene([obstacle('column',[0,0,-100,3000,2000,600])])
        self.assertFalse(valid_height_intervals(build_spatial_model(data),1500,1000,profile(),10))

    def test_separate_floors_do_not_inherit_other_floors_height_bands(self):
        data=scene();data['regions'].append({'id':'upper','kind':'EXPLICIT','box_mm':[0,0,1000,3000,2000,1500]})
        graph=generate(data,profile(),{'room':1,'upper':2},spacing_mm=700,max_nodes=50,seconds=5)
        self.assertEqual({n['level_id'] for n in graph['nodes'].values()},{1,2})
        self.assertTrue(all((n['xyz_mm'][2]<500 if n['level_id']==1 else n['xyz_mm'][2]>1000) for n in graph['nodes'].values()))


if __name__=='__main__':unittest.main()
