"""Focused MCP helper: connect the current two instances; never move the grille."""
from pyrevit.api import DB

def run(doc):
    adapter=doc.GetElement(DB.ElementId(3379549))
    grille=doc.GetElement(DB.ElementId(3378775))
    if adapter is None or grille is None:raise ValueError('Las dos instancias previstas ya no existen.')
    def connector(e):
        return next(c for c in e.MEPModel.ConnectorManager.Connectors if c.Id==1)
    def frame(e):
        t=e.GetTransform()
        return [[p.X,p.Y,p.Z] for p in (t.Origin,t.BasisX,t.BasisY,t.BasisZ)]
    before=frame(grille);grille_type=grille.GetTypeId()
    a,b=connector(adapter),connector(grille)
    if a.IsConnectedTo(b):return {'connected':True,'already_connected':True,'grille_id':3378775,'adapter_id':3379549}
    if a.IsConnected or b.IsConnected:raise ValueError('Una toma ya pertenece a otra conexión; no se desconecta.')
    if abs(a.Radius-b.Radius)>1e-6:raise ValueError('Diámetros distintos; no se cambia ninguna familia.')
    delta=b.Origin-a.Origin
    tx=DB.Transaction(doc,'Unir codo a rejilla sin mover la rejilla');tx.Start()
    try:
        DB.ElementTransformUtils.MoveElement(doc,adapter.Id,delta)
        doc.Regenerate()
        a,b=connector(adapter),connector(grille)
        a.ConnectTo(b)
        doc.Regenerate()
        after=frame(grille)
        if max(abs(x-y) for row,other in zip(before,after) for x,y in zip(row,other))>1e-9 or grille.GetTypeId()!=grille_type:
            raise ValueError('Revit intentó cambiar la rejilla; se revierte la operación.')
        a,b=connector(adapter),connector(grille)
        if not a.IsConnectedTo(b) or not b.IsConnectedTo(a):raise ValueError('Revit no confirma conexión física recíproca.')
        result={'connected':True,'adapter_id':3379549,'grille_id':3378775,'diameter_mm':b.Radius*609.6,
            'grille_transform_unchanged':before==after,'adapter_translation_mm':[delta.X*304.8,delta.Y*304.8,delta.Z*304.8],
            'remaining_ports':[{'id':int(c.Id),'connected':c.IsConnected} for c in adapter.MEPModel.ConnectorManager.Connectors if c.Id!=1]}
        tx.Commit()
        return result
    except Exception:
        if tx.GetStatus()==DB.TransactionStatus.Started:tx.RollBack()
        raise
