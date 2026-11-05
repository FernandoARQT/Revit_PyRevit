"""MCP-only native fixture. ALWAYS rolls its own transaction group back.

No button, no production approval, no persistent model changes. The MCP caller
should additionally use run_modify(dry_run=True).
"""
import copy
import json
from pathlib import Path
from unittest.mock import patch
from pyrevit.api import DB
from ARQT_MEP.routing import service
from ARQT_MEP.routing.revit.network_model import model


def run(doc,root_path,terminal_id=3366668,observe_geometry=False):
    root=Path(root_path)
    proposal=json.loads((root/'tests/artifacts/routing-v2-120-rrt-native.json').read_text(encoding='utf8'))
    proposal=copy.deepcopy(proposal)
    proposal['routes']=[r for r in proposal['routes'] if r['destination']['element_id']==terminal_id]
    if len(proposal['routes'])!=1 or proposal['routes'][0]['native_status'] not in ('VALID','VALID_WITH_WARNINGS'):
        raise ValueError('Falta un recorrido verificado para el ensayo.')
    proposal['proposal_id']='ALWAYS_ROLLBACK_NATIVE_FIXTURE';proposal['test_mode']='ALWAYS_ROLLBACK'
    proposal['routes'][0]['approval_status']='APPROVED'
    proposal['topology']['preserved_connections']=[]
    proposal=service.seal(proposal)
    observations=[]
    from ARQT_MEP.routing.revit.geometry_conflicts import inspect as native_inspect
    def observed_inspect(document,parts,*args,**kwargs):
        from ARQT_MEP import revit_read as rr
        from ARQT_MEP.routing.revit.network_model import manager
        from ARQT_MEP.routing.revit.proposals import _solid_box
        for part in parts:
            cm=manager(part);curve=getattr(part.Location,'Curve',None)
            observations.append({'id':rr.eid(part.Id),'class':part.GetType().Name,
                'curve':curve.GetType().Name if curve else None,
                'solids':[{'box_mm':_solid_box(s),'volume_mm3':s.Volume*304.8**3} for s in rr.solids(part)],
                'connectors':[{'xyz_mm':[rr.mm(c.Origin.X),rr.mm(c.Origin.Y),rr.mm(c.Origin.Z)],
                    'direction':[c.CoordinateSystem.BasisZ.X,c.CoordinateSystem.BasisZ.Y,c.CoordinateSystem.BasisZ.Z],
                    'diameter_mm':rr.mm(c.Radius)*2 if c.Shape==DB.ConnectorProfileType.Round else None}
                    for c in cm.Connectors] if cm else []})
        return native_inspect(document,parts,*args,**kwargs)
    group=DB.TransactionGroup(doc,'ARQ-T · Ensayo SIEMPRE REVERTIDO');group.Start()
    try:
        with patch('ARQT_MEP.routing.revit.distributor_caps.close_unused',return_value={'created':[],'failures':[],'scope':'Excluded from this fixture'}),\
             patch('ARQT_MEP.routing.revit.geometry_conflicts.inspect',side_effect=observed_inspect if observe_geometry else native_inspect):
            result=model(doc,proposal,explicit=True)
        report={'test_mode':'ALWAYS_ROLLBACK','created_in_test':result.get('created'),
            'failures':result.get('failures'),'geometry_conflicts':result.get('geometry_conflicts'),
            'connections':[r for r in result.get('connections',[]) if r.get('route_id')]}
    finally:
        if group.GetStatus()==DB.TransactionStatus.Started:group.RollBack()
    report['group_status']=str(group.GetStatus())
    if observe_geometry:report['native_geometry_observations']=observations
    (root/'tests/artifacts/routing-v2-flex-dry-run-latest.json').write_text(json.dumps(report,ensure_ascii=False),encoding='utf8')
    return report
