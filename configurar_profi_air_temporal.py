"""Standalone Revit MCP task. Safe to delete after execution; no ribbon dependencies.
Call inspect(doc), configure(doc), verify(doc). configure changes only newly
duplicated types and adds the missing nominal 63 size. Test ducts are rolled back.
Technical reference data supplied by user; ID/OD from manufacturer catalogue.
"""
from pyrevit.api import DB
from pyrevit.revit.db import query
from System.Collections.Generic import List

SPECS=((63,'78363805',23,52,64),(75,'78375805',30,63,76),(90,'78390805',45,78,91))
def name(e):return query.get_name(e)
def types(doc):return list(DB.FilteredElementCollector(doc).OfClass(DB.Mechanical.FlexDuctType))
def size_table(doc):return DB.Mechanical.DuctSizeSettings.GetDuctSizeSettings(doc)
def inspect(doc):
    return {'types':[{'id':int(t.Id.Value),'name':name(t),'shape':str(t.Shape)} for t in types(doc)],
            'sizes':[{'nominal_mm':s.NominalDiameter*304.8,'inner_mm':s.InnerDiameter*304.8,'outer_mm':s.OuterDiameter*304.8} for s in size_table(doc)[DB.Mechanical.DuctShape.Round]]}
def configure(doc):
    original={int(e.Id.Value):str(e.VersionGuid) for e in DB.FilteredElementCollector(doc)}
    settings=size_table(doc);base=next(t for t in types(doc) if str(t.Shape)=='Round');rows=[]
    tx=DB.Transaction(doc,'FRÄNKISCHE | Tipos flexibles nativos');tx.Start()
    try:
        for dn,ref,flow,inner,outer in SPECS:
            title='FRA_profi-air_classic_DN'+str(dn)
            found=next((t for t in types(doc) if name(t)==title),None)
            created=found is None;t=base.Duplicate(title) if created else found
            if str(t.Shape)!='Round':raise ValueError('Tipo existente no circular: '+title)
            values={'ALL_MODEL_MANUFACTURER':'FRÄNKISCHE','ALL_MODEL_MODEL':'profi-air classic',
                'ALL_MODEL_DESCRIPTION':'profi-air classic pipe, grey, NW '+str(dn),
                'ALL_MODEL_TYPE_MARK':ref,
                'ALL_MODEL_TYPE_COMMENTS':'Referencia FRÄNKISCHE: {}; DN nominal {} mm; PE-HD; impulsión y extracción; caudal máximo de referencia {} m³/h (no impuesto); radio mínimo 150 mm (verificar en trazado); diámetro interior fabricante {} mm, exterior {} mm. Aspereza heredada, no verificada.'.format(ref,dn,flow,inner,outer)}
            incorporated={};missing=[]
            for key,value in values.items():
                p=t.get_Parameter(getattr(DB.BuiltInParameter,key))
                if p is None or p.IsReadOnly or p.StorageType!=DB.StorageType.String:missing.append(key);continue
                if created:p.Set(value)
                if p.AsString()==value:incorporated[p.Definition.Name]=p.AsString()
                else:missing.append('Existente conservado: '+p.Definition.Name)
            available=any(abs(s.NominalDiameter*304.8-dn)<.001 for s in settings[DB.Mechanical.DuctShape.Round])
            if not available:settings.AddSize(DB.Mechanical.DuctShape.Round,DB.MEPSize(dn/304.8,inner/304.8,outer/304.8,True,True))
            rows.append({'name':title,'id':int(t.Id.Value),'nominal_mm':dn,'reference':ref,'status':'creado' if created else 'existente','parameters':incorporated,'limitations':missing,'size_added':not available})
        doc.Regenerate();tx.Commit()
    except BaseException:
        if tx.GetStatus()==DB.TransactionStatus.Started:tx.RollBack()
        raise
    changed=[i for i,v in original.items() if i!=int(settings.Id.Value) and (doc.GetElement(DB.ElementId(i)) is None or str(doc.GetElement(DB.ElementId(i)).VersionGuid)!=v)]
    if changed:raise ValueError('Se detectaron cambios en elementos originales: '+str(changed))
    return {'types':rows,'tests':verify(doc),'original_elements_preserved':True,'size_settings_id':int(settings.Id.Value),'limitations':['Diámetro de ejemplar: el nombre del tipo no fija el tamaño.','Material, caudal máximo y radio guardados como texto técnico; no existen parámetros específicos verificados.','No se han conectado accesorios reales; las preferencias de uniones/transiciones heredadas están vacías.','Aspereza heredada de la base; no verificada para el producto.','No se corrigen los diámetros interiores/exteriores anómalos de los tamaños originales 75/90.']}
def verify(doc):
    results=[];level=next(iter(DB.FilteredElementCollector(doc).OfClass(DB.Level)))
    systems=[s for s in DB.FilteredElementCollector(doc).OfClass(DB.Mechanical.MechanicalSystemType) if str(s.SystemClassification) in ('SupplyAir','ExhaustAir')]
    assert len({str(s.SystemClassification) for s in systems})==2
    before={int(e.Id.Value) for e in DB.FilteredElementCollector(doc)}
    tx=DB.Transaction(doc,'Ensayo temporal flexibles');tx.Start()
    try:
        for dn,ref,flow,inner,outer in SPECS:
            t=next(t for t in types(doc) if name(t)=='FRA_profi-air_classic_DN'+str(dn))
            for system in systems:
                points=List[DB.XYZ]();points.Add(DB.XYZ(0,0,100));points.Add(DB.XYZ(3,0,100.5));points.Add(DB.XYZ(6,2,101))
                e=DB.Mechanical.FlexDuct.Create(doc,system.Id,t.Id,level.Id,points)
                p=e.get_Parameter(DB.BuiltInParameter.RBS_CURVE_DIAMETER_PARAM)
                assert p is not None and not p.IsReadOnly;p.Set(dn/304.8);doc.Regenerate()
                assert abs(p.AsDouble()*304.8-dn)<.001
                assert int(e.Category.Id.Value)==int(DB.BuiltInCategory.OST_FlexDuctCurves)
                connectors=list(e.ConnectorManager.Connectors)
                assert len(connectors)==2 and all(str(c.Domain)=='DomainHvac' for c in connectors)
                length=e.get_Parameter(DB.BuiltInParameter.CURVE_ELEM_LENGTH).AsDouble()*304.8
                assert length>0
                results.append({'type_id':int(t.Id.Value),'diameter_mm':p.AsDouble()*304.8,'system':str(system.SystemClassification),'connectors':len(connectors),'domain':'DomainHvac','length_mm':length,'native_3d':True})
    finally:tx.RollBack()
    assert before=={int(e.Id.Value) for e in DB.FilteredElementCollector(doc)}
    return {'samples':results,'temporary_elements_rolled_back':True,'sizes_available':[s.NominalDiameter*304.8 for s in size_table(doc)[DB.Mechanical.DuctShape.Round] if any(abs(s.NominalDiameter*304.8-d[0])<.001 for d in SPECS)]}
