"""Pure engine checks, no Autodesk imports or open Revit needed."""
import copy
import json
import math
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ARQ-T.extension/lib'))
from ARQT_MEP.airflow import calculate_airflows,classify_room,transfer_issues,is_exterior
from ARQT_MEP.spatial import polygon_from_rings,optimize_terminal_positions,determine_terminal_count
from ARQT_MEP.config import DEFAULT,validate,migrate
from shapely.geometry import box,Polygon,Point


def dwelling(beds,wet=3,mixed=False):
    names=(['Dormitorio principal']+['Dormitorio '+str(i) for i in range(2,beds+1)] if beds else [])
    names+=['Salón-cocina' if mixed else 'Salón-comedor']
    names+=['Baño '+str(i) for i in range(wet)]
    if not mixed:names+=['Cocina']
    return [dict(uid=str(i),name=n,number=str(i),level_id=i%2,area_m2=10+i*2) for i,n in enumerate(names)]


class Airflows(unittest.TestCase):
    def test_bedrooms_zero_to_four(self):
        for beds,total in [(0,24),(1,24),(2,28),(3,33),(4,33)]:
            with self.subTest(beds=beds):
                result=calculate_airflows(dwelling(beds))
                self.assertAlmostEqual(result['supply'],result['extract'])
                for role in ('impulsion','extraccion'):
                    self.assertAlmostEqual(sum(r['design'] for r in result['rows'] if r['role']==role),result['supply'])
                for r in result['rows']:
                    self.assertGreaterEqual(r['design']+1e-10,r['minimum'])
                self.assertEqual(result['wet_total_minimum'],12 if beds<=1 else 24 if beds==2 else 33)
                if not beds:self.assertFalse(any(r['use']=='principal' for r in result['rows']))

    def test_global_not_by_level(self):
        rooms=dwelling(3)
        self.assertEqual(calculate_airflows(rooms)['wet_total_minimum'],33)
        self.assertEqual(len({r['level_id'] for r in rooms}),2)

    def test_compound_names(self):
        self.assertEqual(classify_room('  SALÓN—Comedor '),'salon')
        self.assertEqual(classify_room('Cocina-SALON'),'mixto')
        self.assertEqual(classify_room('VESTIDOR'),'vestidor')
        self.assertEqual(classify_room('Lavadero'),'revisar')
        result=calculate_airflows(dwelling(3,mixed=True))
        mixed=[r for r in result['rows'] if r['use']=='mixto']
        self.assertEqual({r['role'] for r in mixed},{'impulsion','extraccion'})
        self.assertEqual(len(mixed),2)

    def test_main_inferred_and_secondary_minima(self):
        rooms=dwelling(2);rooms[0]['name']='Habitación';rooms[0]['area_m2']=1000
        result=calculate_airflows(rooms)
        self.assertEqual([r['minimum'] for r in result['rows'] if r['use']=='dormitorio'],[8,4])
        self.assertFalse(any(r['use']=='principal' for r in result['rows']))
        result=calculate_airflows(rooms,{'0':{'use':'principal'}})
        self.assertEqual(result['wet_total_minimum'],24)

    def test_area_distributes_only_balancing_increment(self):
        rooms=dwelling(1)
        result=calculate_airflows(rooms)
        for role in ('impulsion','extraccion'):
            group=[r for r in result['rows'] if r['role']==role]
            rates=[(r['design']-r['requested'])/r['area_m2'] for r in group]
            self.assertLess(max(rates)-min(rates),1e-10)

    def test_exterior_exclusion_has_priority_over_name_and_override(self):
        rooms=dwelling(1)
        for i,name in enumerate(('Porche','Terraza cubierta','Terrazas','Porches habitación')):
            uid='outside'+str(i)
            self.assertTrue(is_exterior(name))
            self.assertEqual(classify_room(name),'excluir')
            rooms.append(dict(uid=uid,name=name,number=uid,level_id=1,area_m2=10000))
        overrides={r['uid']:{'use':'dormitorio'} for r in rooms if r['uid'].startswith('outside')}
        a=calculate_airflows(rooms,overrides);b=calculate_airflows(dwelling(1))
        self.assertEqual(a,b)

    def test_dry_combined_highest(self):
        rooms=dwelling(3);rooms[0]['name']='Dormitorio principal salón'
        r=calculate_airflows(rooms)['rows'][0]
        self.assertEqual(r['minimum'],10)

    def test_manual_design_and_counts(self):
        result=calculate_airflows(dwelling(1),{'0:impulsion':{'design':40,'count':3}})
        self.assertEqual(result['supply'],result['extract'])
        r=result['rows'][0];self.assertEqual(r['count'],3)
        self.assertAlmostEqual(r['flow_each']*3,r['design'])
        self.assertGreaterEqual(r['design'],40)
        for design in (-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):calculate_airflows(dwelling(1),{'0:impulsion':{'design':design}})

    def test_transfer_path_and_disconnected(self):
        rooms=dwelling(1);result=calculate_airflows(rooms)
        issues=transfer_issues(rooms,[],result['rows'])
        self.assertTrue(any('No consta camino' in i for i in issues))
        edges=[(str(i),str(i+1)) for i in range(len(rooms)-1)]
        self.assertFalse(any('No consta camino' in i for i in transfer_issues(rooms,edges,result['rows'])))

    def test_saved_old_bedroom_overrides_migrate_without_losing_increases(self):
        settings=copy.deepcopy(DEFAULT);settings.pop('airflow_policy_version')
        settings['overrides']={'a':{'use':'principal'},'a:impulsion':{'design':4,'count':2},
                               'b':{'use':'dormitorio'},'b:impulsion':{'design':15,'count':1}}
        updated=migrate(settings)
        self.assertEqual(updated['overrides']['a']['use'],'dormitorio')
        self.assertEqual(updated['overrides']['a:impulsion'],{'design':8,'count':2})
        self.assertEqual(updated['overrides']['b:impulsion']['design'],15)


class Spatial(unittest.TestCase):
    def setUp(self):
        self.config=copy.deepcopy(DEFAULT)
        self.dimensions=(164,164,46)

    def ceiling(self,poly,z=2600,id=1,level_id=5):
        return dict(id=id,polygon=poly,z=z,level_id=level_id)

    def propose(self,room,ceilings,count=1,**kwargs):
        return optimize_terminal_positions(room,ceilings,count,self.dimensions,self.config,**kwargs)

    def test_irregular_l_and_hole(self):
        l=Polygon([(0,0),(6000,0),(6000,2000),(2000,2000),(2000,6000),(0,6000)])
        hole=box(500,500,1000,1000)
        poly=polygon_from_rings([list(l.exterior.coords),list(hole.exterior.coords)])
        self.assertAlmostEqual(poly.area,l.area-hole.area)
        result=self.propose(poly,[self.ceiling(poly)],3)
        for p in result:self.assertTrue(poly.covers(box(p['x']-82,p['y']-82,p['x']+82,p['y']+82)))

    def test_multiple_levels_heights_partial(self):
        room=box(0,0,6000,4000)
        ceilings=[self.ceiling(box(0,0,2000,4000),2500,1,5),self.ceiling(box(4000,0,6000,4000),3100,2,6)]
        result=self.propose(room,ceilings,3)
        for p in result:
            c=next(c for c in ceilings if c['id']==p['ceiling_id'])
            self.assertEqual(p['z'],c['z']);self.assertEqual(p['level_id'],c['level_id'])
            self.assertTrue(c['polygon'].covers(Point(p['x'],p['y'])))

    def test_missing_ceiling(self):
        with self.assertRaises(ValueError):self.propose(box(0,0,3000,3000),[])

    def test_joint_spacing_alternatives_deterministic(self):
        room=box(0,0,8000,5000);cs=[self.ceiling(room)]
        a=self.propose(room,cs,4);b=self.propose(room,cs,4)
        self.assertEqual(a,b)
        for i,p in enumerate(a):
            self.assertLessEqual(len(p['alternatives']),3)
            for q in a[:i]:self.assertGreaterEqual(math.hypot(p['x']-q['x'],p['y']-q['y']),1000)
        json.dumps(a,allow_nan=False)

    def test_obstacle_excluded_host_allowed(self):
        room=box(0,0,4000,4000);cs=[self.ceiling(room)]
        obstacle=dict(id=8,polygon=room,z0=2590,z1=2700)
        with self.assertRaises(ValueError):self.propose(room,cs,obstacles=[obstacle])
        obstacle['id']=1
        self.assertEqual(len(self.propose(room,cs,obstacles=[obstacle])),1)

    def test_normative_extraction_clearance_cannot_be_disabled(self):
        room=box(0,0,1000,1000);self.config['wall_clearance_mm']=0
        p=self.propose(room,[self.ceiling(room)],role='extraccion')[0]
        self.assertGreater(p['criteria']['wall_mm']-82,100)

    def test_curve_polygon_multipart(self):
        disc=Point(0,0).buffer(2000,quad_segs=100)
        other=box(5000,0,8000,3000)
        poly=polygon_from_rings([list(disc.exterior.coords),list(other.exterior.coords)])
        self.assertEqual(poly.geom_type,'MultiPolygon')
        self.assertEqual(len(self.propose(poly,[self.ceiling(poly)],2)),2)

    def test_count_suggestion_is_not_manufacturer_capacity(self):
        result=determine_terminal_count(box(0,0,12000,3000))
        self.assertEqual(result['count'],1)
        self.assertEqual(result['suggested_count'],2)
        self.assertEqual(determine_terminal_count(box(0,0,4000,4000),3)['count'],3)

    def test_config(self):
        for key,value in [('resolution_mm',0),('tolerance_mm',0),('terminal_spacing_mm',float('nan'))]:
            s=copy.deepcopy(DEFAULT);s[key]=value
            with self.assertRaises(ValueError):validate(s)


if __name__=='__main__':unittest.main()
