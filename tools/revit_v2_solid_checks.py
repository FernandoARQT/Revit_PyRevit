"""Transient native solids only: no element creation or model transactions."""
import math
from pyrevit.api import DB
from shapely.geometry import Polygon,mapping
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.revit.authorized_volume import AuthorizedVolume,box_solid,oriented_clearance_bound


def run():
    ring=Polygon([(0,0),(500,0),(500,500),(0,500)],holes=[[(200,200),(300,200),(300,300),(200,300)]])
    cases=[]
    def check(name,region,bounds,expected):
        scene={'document_identity':'TRANSIENT_FIXTURE','geometry_revision':'TRANSIENT_FIXTURE',
            'regions':[region],'obstacles':[]}
        answer=AuthorizedVolume(build_spatial_model(scene),15).contains(box_solid(bounds))
        cases.append({'case':name,'verified':answer['verified'],'expected':expected,'passed':answer['verified']==expected})
    flat={'id':'flat','polygon':mapping(ring),'lower':{'z':0},'upper':{'z':200}}
    check('entire body and clearance inside',flat,[40,40,40,100,100,100],True)
    check('body fits but installation clearance does not',flat,[40,40,10,100,100,70],False)
    check('hole remains forbidden',flat,[220,220,60,280,280,120],False)
    slope={'id':'sloping','polygon':mapping(ring),
        'lower':{'plane':{'origin':[0,0,0],'normal':[0,-.2,1]}},
        'upper':{'plane':{'origin':[0,0,200],'normal':[0,-.2,1]}}}
    check('sloping parallel planes inside',slope,[100,100,70,160,160,130],True)
    check('sloping underside clearance rejected',slope,[100,100,25,150,150,50],False)
    native=box_solid([0,0,0,500,80,80]);rotation=DB.Transform.CreateRotation(DB.XYZ.BasisY,math.radians(25))
    rotated=DB.SolidUtils.CreateTransformed(native,rotation);bound=oriented_clearance_bound(rotated,15)
    remainder=DB.BooleanOperationsUtils.ExecuteBooleanOperation(rotated,bound,DB.BooleanOperationsType.Difference)
    cases.append({'case':'oriented native bound contains entire rotated body','passed':remainder.Volume<=1e-9})
    return {'all_passed':all(c['passed'] for c in cases),'cases':cases,'model_elements_created':0}
