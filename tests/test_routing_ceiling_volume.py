import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ARQ-T.extension/lib'))
from shapely.geometry import box,mapping
from ARQT_MEP.routing.core.spatial_model import Region


class CeilingVolume(unittest.TestCase):
    def region(self,gap=0,sloped=False):
        normal=[0,-.16,1] if sloped else [0,0,1]
        return Region({'kind':'FALSE_CEILING','polygon':mapping(box(0,0,5000,5000)),
                       'lower':{'plane':{'origin':[0,0,2500],'normal':normal}},
                       'upper':{'plane':{'origin':[0,0,2700+gap],'normal':normal}}})

    def test_full_ceiling_thickness_is_available_without_extra_gap(self):
        region=self.region()
        self.assertTrue(region.contains((2500,2500,2600),60))
        self.assertFalse(region.contains((2500,2500,2499),0))
        self.assertFalse(region.contains((2500,2500,2701),0))

    def test_extra_gap_extends_upper_limit(self):
        self.assertTrue(self.region(gap=300).contains((2500,2500,2900),60))

    def test_inclination_and_entire_duct_envelope_respected(self):
        region=self.region(sloped=True)
        self.assertTrue(region.contains((2500,2500,3000),60))
        self.assertFalse(region.contains((2500,2500,2899),0))
        self.assertFalse(region.contains((2500,2500,2910),60))


if __name__=='__main__':unittest.main()
