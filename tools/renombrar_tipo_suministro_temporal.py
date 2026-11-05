"""Align the loaded supply type name with the user's explicit specification."""
from pyrevit.api import DB

def run(doc):
    symbols=[s for s in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol)
        if s.FamilyName=='Fraenkische_rejilla 125_Suministro']
    def name(s):return s.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString()
    existing=[s for s in symbols if name(s)=='Suministro']
    if len(existing)==1:return {'already_correct':True,'type_id':existing[0].Id.Value}
    if len(symbols)!=1 or name(symbols[0])!='Extracción':
        raise ValueError('Los tipos cargados ya no coinciden con la inspección; no se renombra ninguno.')
    symbol=symbols[0];uid=symbol.UniqueId
    tx=DB.Transaction(doc,'Nombrar Suministro el tipo de rejilla de suministro');tx.Start()
    try:
        symbol.Name='Suministro';doc.Regenerate()
        if name(symbol)!='Suministro' or symbol.UniqueId!=uid:raise ValueError('No se conserva la identidad del tipo.')
        tx.Commit()
    except Exception:
        if tx.GetStatus()==DB.TransactionStatus.Started:tx.RollBack()
        raise
    import sys,importlib
    for module in ('ARQT_MEP.family_rules','ARQT_MEP.revit_read'):sys.modules.pop(module,None)
    importlib.invalidate_caches()
    from ARQT_MEP import revit_read
    symbols=revit_read.read_symbols(doc)
    return {'renamed_type_id':symbol.Id.Value,'symbols':symbols,'geometry_changed':False}
