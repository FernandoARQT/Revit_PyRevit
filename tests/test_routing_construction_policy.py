import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ARQ-T.extension/lib'))
from ARQT_MEP.routing.construction_policy import construction_rule
from ARQT_MEP.routing.core.astar_3d import passage_cost,SparseGrid
from ARQT_MEP.routing.core.passages import generate_passages
from test_routing_core import obstacle,scene,configuration,profile
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core.route_geometry import validate_route_geometry


class ConstructionPolicy(unittest.TestCase):
    def test_names_and_cost(self):
        self.assertTrue(construction_rule('Cubierta TEJA SOBRE FORJADO 30 cm')['hard_forbidden'])
        rule=construction_rule('Familia Losa alveolar 25 cm')
        weights=configuration()['weights']
        self.assertEqual(passage_cost(rule,weights),5*weights['conditional'])

    def test_roof_cannot_be_ignored_by_explicit_passage(self):
        roof=obstacle('roof',[1400,0,0,1600,2000,500])
        roof['hard_forbidden']=True
        space=build_spatial_model(scene([roof]))
        self.assertEqual(space.collisions((1000,500,250),(2000,500,250),30,['roof']),['roof'])
        passage={'id':'override','element_ids':['roof'],'points_mm':[[1000,500,250],[2000,500,250]],
                 'radius_mm':30,'classification':'PASSABLE'}
        grid=SparseGrid(space,(200,500,250),(2800,500,250),100,profile(configuration()),[passage])
        self.assertFalse(grid.portals)
        report=validate_route_geometry(space,{'points_mm':passage['points_mm'],'passages':[passage]},profile(configuration()))
        self.assertEqual(report['status'],'INVALID')

    def test_slab_generates_conditional_crossing_with_higher_cost(self):
        slab=obstacle('slab',[0,0,900,3000,2000,1100],'CONDITIONAL')
        slab.update(construction_identity='HOLLOW_CORE_SLAB',crossing_multiplier=5)
        space=build_spatial_model(scene([slab],height=2000))
        request={'route_id':'r','source':{'xyz_mm':[500,500,500]},'destination':{'xyz_mm':[500,500,1500]}}
        passages=generate_passages(space,[request],configuration())
        self.assertTrue(passages)
        self.assertTrue(all(p['classification']=='CONDITIONAL' and p['crossing_multiplier']==5 for p in passages))


if __name__=='__main__':unittest.main()
