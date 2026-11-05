# -*- coding: utf-8 -*-
"""Consulta MCP de solo lectura. Puede eliminarse sin afectar a los botones."""
from pyrevit.api import DB
from pyrevit.revit.db import query

equipos = []
for elemento in DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance):
    if 'recuperador' not in elemento.Symbol.FamilyName.casefold():
        continue
    modelo = elemento.MEPModel
    gestor = modelo.ConnectorManager if modelo else None
    conectores = []
    for conector in gestor.Connectors if gestor else []:
        if conector.Domain != DB.Domain.DomainHvac:
            continue
        punto = conector.Origin
        normal = conector.CoordinateSystem.BasisZ
        sistema = str(conector.DuctSystemType)
        sentido = str(conector.Direction)
        funcion = {
            ('SupplyAir', 'Out'): 'DISTRIBUIDOR_IMPULSION',
            ('ReturnAir', 'In'): 'DISTRIBUIDOR_EXTRACCION',
            ('SupplyAir', 'In'): 'ADMISION_EXTERIOR',
            ('ExhaustAir', 'Out'): 'EXPULSION_EXTERIOR',
        }.get((sistema, sentido), 'REVISAR')
        conectores.append({
            'connector_id': int(conector.Id),
            'description': conector.Description,
            'classification': sistema,
            'flow_direction': sentido,
            'function': funcion,
            'diameter_mm': conector.Radius * 2 * 304.8 if conector.Shape == DB.ConnectorProfileType.Round else None,
            'connected': bool(conector.IsConnected),
            'position_mm': [punto.X * 304.8, punto.Y * 304.8, punto.Z * 304.8],
            'outward_normal': [normal.X, normal.Y, normal.Z],
        })
    equipos.append({'element_id': int(elemento.Id.Value), 'family': elemento.Symbol.FamilyName,
                    'type': query.get_name(elemento.Symbol), 'connectors': conectores})

result = {'equipment': equipos, 'read_only': True}
