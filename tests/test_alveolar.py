import sys
import json
import random
import time
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ARQ-T.extension/ARQ-T.tab/STR.panel/DespieceLosaAlveolar.pushbutton'))
import settings
from optimizer import solve_k_best,optimize_margins
from geometry import rectangle,dimensions,plate_profiles


def exhaustive(width,s):
    options=sorted([o for o in s['widths'] if o['enabled'] and o['width']>=s['minimum_exceptional']],key=lambda o:-o['width'])
    found=[]
    def visit(i,counts,total):
        if i==len(options):
            margins=optimize_margins(width-total,s['left'],s['right'])
            if total and margins is not None:
                score=(sum(n*(o['penalty']+s['plate_cost'])+(s['activation_cost'] if n and o['width']!=1200 else 0) for n,o in zip(counts,options)),
                       sum(n for n,o in zip(counts,options) if o['category']==3),sum(n for n,o in zip(counts,options) if o['category']==2),
                       sum(n for n,o in zip(counts,options) if o['width']!=1200),sum(counts),-sum(n for n,o in zip(counts,options) if o['width']==1200))
                d=sum((a-b[1])**2 for a,b in zip(margins,[s['left'],s['right']]))
                found.append((score+(d,)+tuple(counts),tuple(counts)))
            return
        for n in range(int((width-total)//options[i]['width'])+1):visit(i+1,counts+[n],total+n*options[i]['width'])
    visit(0,[],0)
    return [counts for key,counts in sorted(found)[:3]]


class TestAlveolar(unittest.TestCase):
    def test_top3_against_independent_enumeration(self):
        s=settings.make_default_settings()
        s['widths']=[o for o in s['widths'] if o['width'] in (1200,900,750,600)]
        results=solve_k_best(8755,s)
        self.assertEqual([r['counts'] for r in results],exhaustive(8755,s))
        self.assertEqual(len(results),3)
        for r in results:self.assertEqual((r['left'],r['right']),(27.5,27.5))

    def test_random_exact(self):
        rng=random.Random(2026)
        for _ in range(70):
            s=settings.make_default_settings();s['minimum_preferred']=1;s['minimum_exceptional']=1
            s['widths']=[{'width':w,'category':rng.randint(1,3),'penalty':rng.randint(0,6),'enabled':True} for w in rng.sample(range(3,24),3)]
            s['left']=[0,2,5];s['right']=[1,3,7];s['activation_cost']=rng.randint(0,9)
            width=rng.randint(15,85)+.5
            expected=exhaustive(width,s)
            got=solve_k_best(width,s)
            if expected:self.assertEqual([r['counts'] for r in got],expected)
            else:self.assertTrue(all(not r['valid'] for r in got))

    def test_no_solution_and_no_invented_width(self):
        s=settings.make_default_settings();s['widths']=[{'width':1200,'category':1,'penalty':0,'enabled':True},{'width':325,'category':3,'penalty':1,'enabled':True}]
        results=solve_k_best(8755,s)
        self.assertTrue(all(not r['valid'] for r in results))
        self.assertTrue(all(set(r['order'])=={1200} for r in results))
        self.assertEqual(results[0]['excess'],295)
        with self.assertRaises(ValueError):plate_profiles((0,0,8755,10000),'N-S',results[0])

    def test_margin_asymmetry(self):
        self.assertEqual(optimize_margins(70,[10,20,40],[20,40,50]),(25,45))
        self.assertEqual(optimize_margins(55,[20,25,30],[20,25,30]),(27.5,27.5))
        self.assertIsNone(optimize_margins(100,[20,25,30],[20,25,30]))

    def test_three_nearest_even_when_panel_smaller_than_plate(self):
        s=settings.make_default_settings();s['widths']=[s['widths'][0]]
        results=solve_k_best(100,s)
        self.assertEqual([r['total_width'] for r in results],[1200,2400,3600])
        self.assertTrue(all(not r['valid'] for r in results))

    def test_persistence_and_validation(self):
        class Backend:
            def __init__(self):self.data={}
            def get_option(self,k,d=None):return self.data.get(k,d)
            def set_option(self,k,v):self.data[k]=v
        b=Backend();s=settings.make_default_settings()
        row=next(o for o in s['widths'] if o['width']==850);row.update(category=1,enabled=False,penalty=7)
        settings.save_settings(s,b)
        self.assertEqual(settings.load_settings(b),s)
        before=b.data.copy();b.data['settings_json']='invalid'
        with self.assertRaises(ValueError):settings.load_settings(b)
        self.assertEqual(b.data['settings_json'],'invalid')
        s['widths'].append(dict(s['widths'][0]))
        with self.assertRaises(ValueError):settings.save_settings(s,b)

    def test_geometry_both_directions(self):
        b=(5000,-7000,13755,10000)
        self.assertEqual(rectangle([(5000,-7000),(13755,-7000),(13755,10000),(5000,10000)]),b)
        s=settings.make_default_settings()
        for direction in ('N-S','E-O'):
            u0,u1,v0,v1=dimensions(b,direction)
            for sol in solve_k_best(v1-v0,s):
                if not sol['valid']:continue
                profiles=plate_profiles(b,direction,sol)
                previous=v0+sol['left']
                area=0
                for profile,w in zip(profiles,sol['order']):
                    q=rectangle(profile);a0,a1,c0,c1=dimensions(q,direction)
                    self.assertAlmostEqual(c0,previous);self.assertAlmostEqual(c1-c0,w)
                    self.assertEqual((a0,a1),(u0,u1));previous=c1;area+=(a1-a0)*(c1-c0)
                self.assertAlmostEqual(previous+sol['right'],v1)
                self.assertAlmostEqual(area+(u1-u0)*(sol['left']+sol['right']),(u1-u0)*(v1-v0))
        with self.assertRaises(ValueError):rectangle([(0,0),(10,1),(10,10),(0,10)])

    def test_performance_and_gcd_one(self):
        s=settings.make_default_settings()
        timings={}
        for width in (5000,10000,20000,30000):
            start=time.perf_counter();solve_k_best(width,s);timings[width]=time.perf_counter()-start
        s['widths'].append({'width':683,'category':3,'penalty':10,'enabled':True})
        start=time.perf_counter();result=solve_k_best(10000,s);timings['gcd1_10000']=time.perf_counter()-start
        self.assertTrue(result)
        print('ALVEOLAR tiempos (s):',timings)


if __name__=='__main__':unittest.main()
