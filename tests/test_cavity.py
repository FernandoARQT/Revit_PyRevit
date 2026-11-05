import sys
import unittest
from pathlib import Path
from shapely.geometry import Polygon, box
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ARQ-T.extension/ARQ-T.tab/STR.panel/Cavity.pushbutton'))
from cavity_grid import solve


class CavityTests(unittest.TestCase):
    def test_example(self):
        result=solve(box(0,0,10300,8200),500,500)
        self.assertEqual((result['count'],result['rows'],result['columns']),(320,16,20))
        self.assertEqual(result['margins'],(150,150,100,100))

    def test_half_of_each_remainder(self):
        result=solve(box(1200,-300,11300,7750),500,500)
        self.assertEqual(result['columns'],20)
        self.assertEqual(result['rows'],16)
        self.assertEqual(result['matrix_size'],(10000,8000))
        self.assertEqual(result['remainder'],(100,50))
        self.assertEqual(result['start_margins'],(50,25))
        self.assertEqual(result['start'],(1250,-275))
        self.assertEqual(result['points'][0],(1500,-25))

    def test_spacing_in_matrix_extent(self):
        result=solve(box(0,0,10300,8200),500,400,10)
        self.assertEqual(result['matrix_size'],(10190,8190))
        self.assertEqual(result['start_margins'],(55,5))

    def test_successive_floors_do_not_reuse_dimensions(self):
        cases=[(box(0,0,10100,8050),500,500,20,16),
               (box(20000,-18000,31555,-11354),543,543,21,12),
               (box(-9000,4000,-2000,6600),800,450,8,5)]
        for floor,w,h,columns,rows in cases:
            result=solve(floor,w,h)
            x0,y0,x1,y1=floor.bounds
            self.assertEqual((result['columns'],result['rows']),(columns,rows))
            self.assertAlmostEqual(result['points'][0][0],x0+((x1-x0)-columns*w)/2+w/2)
            self.assertAlmostEqual(result['points'][0][1],y0+((y1-y0)-rows*h)/2+h/2)
            self.assertEqual(result['count'],columns*rows)

    def test_holes_and_concavities(self):
        shapes=[Polygon([(0,0),(6,0),(6,2),(3,2),(3,6),(0,6)]),
                Polygon([(0,0),(8,0),(8,8),(0,8)],holes=[[(2,2),(6,2),(6,6),(2,6)]]),
                Polygon([(0,0),(8,1),(5,7),(0,5)])]
        for shape in shapes:
            result=solve(shape,.8,1.2,.1)
            for x,y in result['points']:
                self.assertTrue(shape.covers(box(x-.4,y-.6,x+.4,y+.6)))
            self.assertEqual(len(set(result['points'])),result['count'])

    def test_type_dimensions_and_gap(self):
        self.assertEqual(solve(box(0,0,10,8),2,1)['count'],40)
        self.assertEqual(solve(box(0,0,10,8),1,1)['count'],80)
        self.assertLess(solve(box(0,0,10,8),1,1,.1)['count'],80)

    def test_invalid(self):
        for dims in [(0,1,0),(1,1,-1),(20,20,0)]:
            with self.assertRaises(ValueError):
                solve(box(0,0,10,8),*dims)

    def test_nearly_rectangular_is_centered(self):
        shape=Polygon([(0,0),(10.3,0.0001),(10.2999,8.2001),(-0.0001,8.2)])
        result=solve(shape,.543,.543)
        left,right,bottom,top=result['margins']
        self.assertAlmostEqual(left,right,places=7)
        self.assertAlmostEqual(bottom,top,places=7)
        for x,y in result['points']:
            self.assertTrue(shape.covers(box(x-.543/2,y-.543/2,x+.543/2,y+.543/2)))


if __name__=='__main__':
    unittest.main()
