# -*- coding: utf-8 -*-
"""Correccion independiente. Ejecutar con la familia distribuidor activa, dry-run primero."""
from pyrevit.api import DB
assert doc.IsFamilyDocument and '5-fold' in doc.Title
ce = doc.GetElement(DB.ElementId(3228037))
p = ce.get_Parameter(DB.BuiltInParameter.RBS_DUCT_CONNECTOR_SYSTEM_CLASSIFICATION_PARAM)
before = p.AsInteger()
t = DB.Transaction(doc, 'Unificar conectores de union multipuerto')
t.Start()
p.Set(int(DB.Mechanical.DuctSystemType.Fitting))
fm = doc.FamilyManager
fm.Set(fm.get_Parameter('TH12'), 1)
fm.Set(fm.get_Parameter('TV12'), 0)
doc.Regenerate()
t.Commit()
result = {'connector':3228037, 'before':before, 'after':p.AsInteger(),
          'classification':str(ce.SystemClassification), 'TH12':fm.CurrentType.AsInteger(fm.get_Parameter('TH12')),
          'TV12':fm.CurrentType.AsInteger(fm.get_Parameter('TV12')), 'saved':False, 'reloaded':False}
