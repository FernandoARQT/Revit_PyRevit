"""Inclined face geometry and pose checks independent of Revit."""
import math
import sys
import unittest
import copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ARQ-T.extension/lib'))
from shapely.geometry import box,Polygon,Point
from ARQT_MEP import surface,spatial,config


def ceiling(id=1,origin=(0,0,2600),normal=(-.3,.2,1),poly=None):
    return {'id':id,'surface_id':str(id),'level_id':1,'z':origin[2],
            'polygon':poly if poly is not None else box(0,0,6000,6000),
            'plane':{'origin':list(origin),'normal':list(normal)}}


def apply_rotation(vector,axis,angle):
    cosine=math.cos(angle);sine=math.sin(angle)
    v=surface.cross(axis,vector);d=surface.dot(axis,vector)
    return [vector[i]*cosine+v[i]*sine+axis[i]*d*(1-cosine) for i in range(3)]


class InclinedSurfaces(unittest.TestCase):
    def test_height_on_both_axes_at_large_coordinates(self):
        c=ceiling(origin=(53000,58000,4400))
        self.assertAlmostEqual(surface.height(c,54000,59000),4500)
        self.assertAlmostEqual(surface.height(c,52000,57000),4300)

    def test_normal_orientation_is_canonical(self):
        up=ceiling(normal=(.1,-.2,1));down=ceiling(normal=(-.1,.2,-1))
        self.assertEqual(surface.plane(up),surface.plane(down))

    def test_basis_is_orthonormal_and_right_handed(self):
        for normal in ((0,0,1),(.2,0,1),(0,-.2,1),(.3,.5,1),(1,2,.01)):
            f=surface.frame(ceiling(normal=normal))
            self.assertAlmostEqual(surface.dot(surface.cross(f[0],f[1]),f[2]),1)
            for i in range(3):
                self.assertAlmostEqual(surface.dot(f[i],f[i]),1)
                for j in range(i):self.assertAlmostEqual(surface.dot(f[i],f[j]),0)

    def test_visible_plane_alignment_includes_xy_offset(self):
        c=ceiling();p=dict(c,x=2000,y=3000,z=surface.height(c,2000,3000))
        insert=surface.insertion(p,-1)
        reconstructed=surface.visible_point(insert,surface.frame(p),-1)
        for a,b in zip(reconstructed,(p['x'],p['y'],p['z'])):self.assertAlmostEqual(a,b)
        self.assertNotEqual(insert[0],p['x']);self.assertNotEqual(insert[1],p['y'])
    def test_installation_reduces_vertical_offset_exactly_one_mm(self):
        for normal in ((0,0,1),(-.3,.2,1)):
            c=ceiling(normal=normal);p=dict(c,x=2000,y=3000,z=surface.height(c,2000,3000))
            original=surface.insertion(p,-1);placed=surface.installation_insertion(p,-1)
            self.assertEqual(placed[:2],original[:2]);self.assertAlmostEqual(placed[2],original[2]-1)
            visible=surface.visible_point(placed,surface.frame(p),-1)
            self.assertAlmostEqual(visible[2],p['z']-1)
            self.assertEqual(surface.installation_insertion(p,-1),placed)

    def test_envelope_accounts_for_tilt_and_actual_vertical_range(self):
        c=ceiling(normal=(0,-.5,1));footprint,z0,z1=surface.envelope(c,2000,3000,(160,160,46))
        z=surface.height(c,2000,3000)
        self.assertLess(z0,z)
        self.assertGreater(z1,z+46)
        self.assertGreater(footprint.area,0)

    def test_height_band_clips_actual_plane(self):
        c=ceiling(normal=(-1,0,1),poly=box(0,0,4000,4000))
        clipped=surface.height_band(c['polygon'],c,3600,5600)
        self.assertAlmostEqual(clipped.area,2000*4000)
        self.assertEqual(clipped.bounds,(1000,0,3000,4000))

    def test_crossing_faces_keep_lowest_half_of_each(self):
        a=ceiling(1,origin=(0,0,2600),normal=(-.2,0,1),poly=box(0,0,4000,4000))
        b=ceiling(2,origin=(0,0,3400),normal=(.2,0,1),poly=box(0,0,4000,4000))
        visible=surface.visible_surfaces([a,b])
        pa=next(c['polygon'] for c in visible if c['id']==1)
        pb=next(c['polygon'] for c in visible if c['id']==2)
        self.assertTrue(pa.covers(Point(1000,2000)));self.assertFalse(pa.covers(Point(3000,2000)))
        self.assertTrue(pb.covers(Point(3000,2000)));self.assertFalse(pb.covers(Point(1000,2000)))
        self.assertAlmostEqual(pa.union(pb).area,4000*4000)

    def test_coplanar_faces_have_deterministic_owner(self):
        a=ceiling(1);b=ceiling(2)
        visible=surface.visible_surfaces([b,a])
        self.assertEqual(len(visible),1);self.assertEqual(visible[0]['id'],1)

    def test_clipping_retains_holes_and_concavity(self):
        poly=Polygon([(0,0),(4000,0),(4000,2000),(2000,2000),(2000,4000),(0,4000)],holes=[[(200,200),(800,200),(800,800),(200,800)]])
        c=ceiling(poly=poly,normal=(-.1,0,1))
        clipped=surface.height_band(poly,c,2600,2900)
        self.assertFalse(clipped.covers(Point(500,500)))
        self.assertFalse(clipped.covers(Point(3000,3000)))

    def test_rotation_maps_between_opposite_slopes(self):
        a=surface.frame(ceiling(normal=(0,.16,1)));b=surface.frame(ceiling(normal=(0,-.16,1)))
        axis,angle=surface.axis_angle(a,b)
        transformed=[apply_rotation(v,axis,angle) for v in a]
        self.assertLess(surface.basis_error_degrees(transformed,b),1e-5)

    def test_rotation_handles_half_turn(self):
        a=[[1,0,0],[0,1,0],[0,0,1]];b=[[-1,0,0],[0,-1,0],[0,0,1]]
        axis,angle=surface.axis_angle(a,b)
        self.assertAlmostEqual(angle,math.pi)
        self.assertLess(surface.basis_error_degrees([apply_rotation(v,axis,angle) for v in a],b),1e-5)

    def test_flat_rotation_is_noop_and_reflection_rejected(self):
        f=surface.frame(ceiling(normal=(0,0,1)))
        self.assertIsNone(surface.axis_angle(f,f))
        with self.assertRaises(ValueError):surface.axis_angle([[-1,0,0],[0,1,0],[0,0,1]],f)

    def test_candidates_follow_inclined_face_at_each_xy(self):
        c=ceiling();room=c['polygon'];s=copy.deepcopy(config.DEFAULT)
        points=spatial.optimize_terminal_positions(room,[c],3,(164,164,46),s)
        for p in points:
            self.assertAlmostEqual(p['z'],surface.height(c,p['x'],p['y']))
            for alt in p['alternatives']:
                self.assertAlmostEqual(alt['z'],surface.height(c,alt['x'],alt['y']))
                footprint,z0,z1=surface.envelope(alt,alt['x'],alt['y'],(164,164,46))
                self.assertTrue(room.covers(footprint))
                self.assertGreater(alt['slope_deg'],0)


if __name__=='__main__':unittest.main()
