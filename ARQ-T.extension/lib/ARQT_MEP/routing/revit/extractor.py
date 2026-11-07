"""Read real host/link solids, plenum boundaries and typed MEP connectors."""
import hashlib
import json
import math
import time
from pyrevit.api import DB
from shapely.geometry import mapping,shape,box,Point
from shapely.ops import unary_union
from ... import revit_read as rr,surface,ownership
from ...airflow import is_exterior,normalize
from ...family_rules import distributor_network,terminal_matches
from ..core import geometry as g
from ..settings import identify_construction
from ..construction_policy import construction_rule,intervention_cost

CATEGORIES=('OST_Walls','OST_Floors','OST_Roofs','OST_Ceilings','OST_StructuralFraming','OST_StructuralColumns',
 'OST_Columns','OST_Doors','OST_Windows','OST_GenericModel','OST_Furniture','OST_LightingFixtures',
 'OST_MechanicalEquipment','OST_DuctTerminal','OST_DuctCurves','OST_FlexDuctCurves','OST_DuctFitting',
 'OST_DuctAccessory','OST_PipeCurves','OST_FlexPipeCurves','OST_PipeFitting','OST_PipeAccessory',
 'OST_CableTray','OST_CableTrayFitting','OST_Conduit','OST_ConduitFitting','OST_PlumbingFixtures','OST_Stairs')


def _box(element,transform=None):
    b=element.get_BoundingBox(None)
    if b is None:return None
    pts=[b.Transform.OfPoint(DB.XYZ(x,y,z)) for x in (b.Min.X,b.Max.X) for y in (b.Min.Y,b.Max.Y) for z in (b.Min.Z,b.Max.Z)]
    if transform is not None:pts=[transform.OfPoint(p) for p in pts]
    return g.bounds([(rr.mm(p.X),rr.mm(p.Y),rr.mm(p.Z)) for p in pts])


def _category(element):return str(DB.BuiltInCategory(rr.eid(element.Category.Id)))


def construction_name(element):
    element_type=element.Document.GetElement(element.GetTypeId())
    return ' '.join(filter(None,(rr.name(element),rr.name(element_type) if element_type else '',getattr(element_type,'FamilyName',''))))


def construction_identity(element,settings):
    text=construction_name(element)
    rule=construction_rule(text,_category(element))
    return rule['identity'] if rule else identify_construction(text,settings)


def classify_obstacle(element,settings,element_key):
    mandatory=construction_rule(construction_name(element),_category(element))
    if mandatory:return mandatory['classification']
    override=settings['element_rules'].get(element_key)
    if override:
        if override not in ('FREE','PASSABLE','CONDITIONAL','FORBIDDEN','UNKNOWN'):raise ValueError('Regla de elemento inválida.')
        return override
    cat=_category(element)
    if isinstance(element,DB.Ceiling) or construction_identity(element,settings)=='FALSE_CEILING':return 'FREE'
    if cat in ('OST_StructuralFraming','OST_StructuralColumns','OST_Columns'):return 'FORBIDDEN'
    if isinstance(element,DB.Wall):
        p=element.get_Parameter(DB.BuiltInParameter.WALL_STRUCTURAL_SIGNIFICANT)
        if p and p.AsInteger():return 'CONDITIONAL'
        identity=construction_identity(element,settings)
        if identity=='STRUCTURE':return 'CONDITIONAL'
        if identity=='PASSABLE':return 'PASSABLE'
        material_classes=[]
        for mid in element.GetMaterialIds(False):
            material=element.Document.GetElement(mid)
            if material:material_classes.append(normalize(material.MaterialClass or ''))
        if any(k in m for m in material_classes for k in ('concrete','hormigon','masonry','mamposteria','brick','ladrillo')):return 'CONDITIONAL'
        if any(k in m for m in material_classes for k in ('gypsum','yeso','plaster','metal','wood','madera')):return 'PASSABLE'
        return 'UNKNOWN'
    if cat=='OST_Floors':
        p=element.get_Parameter(DB.BuiltInParameter.FLOOR_PARAM_IS_STRUCTURAL)
        return 'CONDITIONAL' if p and p.AsInteger() else 'FORBIDDEN'
    return 'FORBIDDEN' if cat not in ('OST_GenericModel',) else 'UNKNOWN'


def _mesh(solid):
    vertices=[];faces=[]
    for face in solid.Faces:
        mesh=face.Triangulate();offset=len(vertices)
        vertices.extend((rr.mm(p.X),rr.mm(p.Y),rr.mm(p.Z)) for p in mesh.Vertices)
        for i in range(mesh.NumTriangles):
            t=mesh.get_Triangle(i)
            ids=tuple(offset+int(t.get_Index(j)) for j in range(3))
            if g.norm(g.cross(g.sub(vertices[ids[1]],vertices[ids[0]]),g.sub(vertices[ids[2]],vertices[ids[0]])))>1e-8:faces.append(ids)
    return {'vertices_mm':vertices,'faces':faces}


def _planes(solid,sign,uid):
    result=[]
    for index,face in enumerate(solid.Faces):
        if not isinstance(face,DB.PlanarFace) or sign*face.FaceNormal.Z<1e-6:continue
        loops=[[c for c in loop] for loop in face.GetEdgesAsCurveLoops()]
        poly=rr.polygon_from_rings(rr.rings_from_curves(loops))
        p=face.Origin;n=face.FaceNormal
        result.append({'id':uid,'surface_id':uid+':'+str(index),'z':rr.mm(p.Z),'polygon':poly,
                       'plane':{'origin':[rr.mm(p.X),rr.mm(p.Y),rr.mm(p.Z)],'normal':[n.X,n.Y,n.Z]}})
    return result


def _elements(doc,settings):
    categories={int(getattr(DB.BuiltInCategory,c)) for c in CATEGORIES}
    for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType():
        if e.Category and rr.eid(e.Category.Id) in categories:
            if settings.get('phase_id') is not None and e.HasPhases() and not rr.visible_in_phase(e,doc,int(settings['phase_id'])):continue
            yield e,None,None
    if settings['include_links']:
        for link in DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance):
            linked=link.GetLinkDocument()
            if linked is None:continue
            transform=link.GetTotalTransform()
            for e in DB.FilteredElementCollector(linked).WhereElementIsNotElementType():
                if e.Category and rr.eid(e.Category.Id) in categories:yield e,link,transform


def analyze_mep_geometry(doc,settings,cancelled=None):
    started=time.monotonic();rooms,issues=rr.analyze_rooms(doc,settings.get('phase_id'))
    interior=[r['polygon'] for r in rooms if not is_exterior(r['name'])]
    allowed=settings['constraints']['allowed_regions']
    if interior:roi=unary_union(interior)
    elif allowed:roi=unary_union([box(r['box_mm'][0],r['box_mm'][1],r['box_mm'][3],r['box_mm'][4]) for r in allowed if 'box_mm' in r])
    else:raise ValueError('Defina el ámbito mediante Rooms o volúmenes autorizados explícitos.')
    if allowed:
        roi=unary_union([roi]+[box(r['box_mm'][0],r['box_mm'][1],r['box_mm'][3],r['box_mm'][4]) for r in allowed if 'box_mm' in r])
    lower=[];upper=[];obstacles=[];revision=['ROUTING_SPACE_ENVELOPE_V3'];count=0
    for e,link,transform in _elements(doc,settings):
        count+=1
        if count%32==0 and cancelled and cancelled():raise InterruptedError('Extracción cancelada.')
        b=_box(e,transform)
        if b is None or not roi.buffer(500).intersects(box(b[0],b[1],b[3],b[4])):continue
        key=(link.UniqueId+'|' if link else '')+e.UniqueId
        revision.append(key+':'+str(e.VersionGuid))
        solids=rr.solids(e)
        if transform is not None:solids=[DB.SolidUtils.CreateTransformed(s,transform) for s in solids]
        is_false_ceiling=isinstance(e,DB.Ceiling) or construction_identity(e,settings)=='FALSE_CEILING'
        # The authorized routing volume includes the full ceiling build-up.
        # Collect its underside before omitting the ceiling from obstacles.
        for i,s in enumerate(solids):
            if is_false_ceiling:lower.extend(_planes(s,-1,key+':'+str(i)))
            elif isinstance(e,(DB.Floor,DB.RoofBase)):upper.extend(_planes(s,-1,key+':'+str(i)))
        meshes=[_mesh(s) for s in solids]
        classification=classify_obstacle(e,settings,key)
        if classification=='FREE':continue
        obstacle={'id':key,'element_id':rr.eid(e.Id),'element_uid':e.UniqueId,'link_uid':link.UniqueId if link else None,
                  'category':_category(e),'name':rr.name(e),'construction_identity':construction_identity(e,settings),'classification':classification,'box_mm':b,'meshes':meshes}
        mandatory=construction_rule(construction_name(e),_category(e))
        if mandatory:obstacle.update(mandatory)
        if isinstance(e,DB.Wall) or obstacle['construction_identity']=='HOLLOW_CORE_SLAB':
            classes=[]
            for mid in e.GetMaterialIds(False):
                material=e.Document.GetElement(mid)
                if material:classes.append(material.MaterialClass or '')
            cost=intervention_cost(classes,obstacle['construction_identity'])
            if cost:obstacle.update(cost,cost_basis='MATERIAL_CLASS_REFERENCE_EQUIVALENT_METRES')
        if isinstance(e,DB.Wall) and isinstance(e.Location,DB.LocationCurve):
            curve=e.Location.Curve
            if isinstance(curve,DB.Line):
                ends=[curve.GetEndPoint(i) for i in (0,1)]
                if transform is not None:ends=[transform.OfPoint(p) for p in ends]
                obstacle['wall_axis_mm']=[[rr.mm(p.X),rr.mm(p.Y),rr.mm(p.Z)] for p in ends]
                obstacle['wall_thickness_mm']=rr.mm(e.Width)
        obstacles.append(obstacle)
        if not meshes:issues.append('Sin sólido resoluble, tratado conservadoramente como UNKNOWN: '+key)
    regions=[]
    for lo in lower:
        candidates=[];a,b,c=surface.coefficients(lo)
        for hi in upper:
            poly=lo['polygon'].intersection(hi['polygon']).intersection(roi)
            u,v,w=surface.coefficients(hi)
            poly=surface.clip_linear(poly,u-a,v-b,w-c-0.1)
            if poly.area>1:candidates.append(dict(hi,polygon=poly))
        for hi in surface.visible_surfaces(candidates):
            regions.append({'id':'plenum:'+lo['surface_id']+':'+hi['surface_id'],'kind':'FALSE_CEILING',
                            'polygon':mapping(hi['polygon']),'lower':{'plane':surface.plane(lo)},
                            'upper':{'plane':surface.plane(hi)},'lower_element':lo['id'],'upper_element':hi['id']})
    if not regions:issues.append('No se ha identificado volumen entre la cara inferior del falso techo y la cara inferior del forjado/cubierta superior. Defina un corredor permitido si procede.')
    links=list(DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance))
    revision.extend('link:'+l.UniqueId+':'+str(l.VersionGuid) for l in links)
    revision.extend('room:'+r['uid']+':'+str(doc.GetElement(r['uid']).VersionGuid) for r in rooms)
    if links and not settings['include_links']:issues.append('Modelos vinculados excluidos por configuración; la validación no cubre sus elementos.')
    if links and settings['include_links'] and settings.get('phase_id') is not None:
        issues.append('No se presupone equivalencia entre fases del anfitrión y vínculos: se incluyen conservadoramente todas las fases vinculadas.')
    for cat in (DB.BuiltInCategory.OST_DuctTerminal,DB.BuiltInCategory.OST_MechanicalEquipment,DB.BuiltInCategory.OST_DuctFitting,DB.BuiltInCategory.OST_DuctAccessory,DB.BuiltInCategory.OST_GenericModel):
        revision.extend('connection:'+e.UniqueId+':'+str(e.VersionGuid) for e in DB.FilteredElementCollector(doc).OfCategory(cat).WhereElementIsNotElementType())
    return {'version':1,'document_identity':ownership.document_id(doc),'geometry_revision':hashlib.sha256('\n'.join(sorted(revision)).encode()).hexdigest(),
            'units':'mm','coordinate_system':'Revit project internal coordinates converted to mm; link transforms applied',
            'regions':regions,'obstacles':obstacles,'warnings':issues,'stats':{'extraction_seconds':time.monotonic()-started,
                 'elements':len(obstacles),'triangles':sum(len(m['faces']) for o in obstacles for m in o['meshes']),
                 'region_count':len(regions),'serialized_bytes':None}}


def configured_model_catalog(doc,settings):
    """Read configured native types; naming never certifies connector compatibility."""
    flex=settings['flexible_duct'];distribution=settings['distributors']
    flex_types=[{'element_id':rr.eid(t.Id),'name':rr.name(t),'shape':str(t.Shape)}
                for t in DB.FilteredElementCollector(doc).OfClass(DB.Mechanical.FlexDuctType)
                if normalize(rr.name(t))==normalize(flex['type_name'])]
    distributors=[];construction=[]
    for t in DB.FilteredElementCollector(doc).WhereElementIsElementType():
        identity=identify_construction(rr.name(t),settings)
        if identity:construction.append({'type_id':rr.eid(t.Id),'type_name':rr.name(t),'identity':identity})
    for t in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol):
        if normalize(distribution['name_contains']) not in normalize(t.FamilyName+' '+rr.name(t)):continue
        dimensions={}
        for p in t.Parameters:
            if p.StorageType==DB.StorageType.Double and p.Definition.GetDataType()==DB.SpecTypeId.Length and p.HasValue:
                dimensions[p.Definition.Name]=rr.mm(p.AsDouble())
        distributors.append({'type_id':rr.eid(t.Id),'family':t.FamilyName,'type_name':rr.name(t),
                             'length_parameters_mm':dimensions,'connector_verification':'REQUIRES_INSTANCE'})
    sizes=DB.Mechanical.DuctSizeSettings.GetDuctSizeSettings(doc)[DB.Mechanical.DuctShape.Round]
    listed=[round(rr.mm(s.NominalDiameter),6) for s in sizes if s.UsedInSizeLists]
    sizing_sizes=[round(rr.mm(s.NominalDiameter),6) for s in sizes if s.UsedInSizeLists and s.UsedInSizing]
    transition_id=None;transition_name=None
    if len(flex_types)==1:
        native_type=doc.GetElement(DB.ElementId(flex_types[0]['element_id']))
        parameter=native_type.get_Parameter(DB.BuiltInParameter.RBS_CURVETYPE_DEFAULT_TRANSITION_PARAM)
        transition=doc.GetElement(parameter.AsElementId()) if parameter else None
        if isinstance(transition,DB.FamilySymbol):
            transition_id=rr.eid(transition.Id);transition_name=transition.FamilyName+' / '+rr.name(transition)
    warnings=[]
    if len(flex_types)!=1:warnings.append('Tipo flexible configurado ausente o ambiguo: '+flex['type_name'])
    if not distributors:warnings.append('No hay tipos cargados que coincidan con '+distribution['name_contains'])
    if not transition_id:warnings.append('Flex - Redondo no tiene asignado un tipo de transición cargado.')
    warnings.append('Dimensionado por caudal final y tamaños circulares habilitados de Revit; velocidad máxima {} m/s. Las pérdidas de carga y la presión disponible del recuperador requieren comprobación adicional.'.format(settings.get('sizing',{}).get('max_velocity_mps',3.0)))
    rigid_types=[{'element_id':rr.eid(t.Id),'name':rr.name(t)} for t in DB.FilteredElementCollector(doc).OfClass(DB.Mechanical.DuctType)
                 if str(t.Shape)=='Round' and not rr.name(t).startswith('ARQT_MEP_')]
    system_types=[{'element_id':rr.eid(t.Id),'name':rr.name(t),'classification':str(t.SystemClassification)}
                  for t in DB.FilteredElementCollector(doc).OfClass(DB.Mechanical.MechanicalSystemType)]
    return {'construction_types':construction,'rigid_main_types':rigid_types,'mechanical_system_types':system_types,
            'flexible_duct':{'configuration':flex,'matching_types':flex_types,
                            'listed_diameters_mm':listed,'sizing_diameters_mm':sizing_sizes,'requested_not_listed_mm':[],
                            'transition_type_id':transition_id,'transition_type_name':transition_name,
                            'instance_editability':'REQUIRES_INSTANCE'},
            'distributors':{'configuration':distribution,'loaded_types':distributors},
            'main_connection':settings['main_connection'],'warnings':warnings}


def _terminal_flow(element):
    """Keep the established Flow design parameter; otherwise require one typed candidate."""
    candidates=[p for p in element.Parameters if p.HasValue and p.StorageType==DB.StorageType.Double
                and p.Definition.GetDataType()==DB.SpecTypeId.AirFlow]
    named=[p for p in candidates if p.Definition.Name=='Flow']
    selected=named[0] if len(named)==1 else candidates[0] if len(candidates)==1 else None
    values=[{'name':p.Definition.Name,'parameter_id':rr.eid(p.Id),'scope':'INSTANCE',
             'is_read_only':bool(p.IsReadOnly),
             'value_lps':DB.UnitUtils.ConvertFromInternalUnits(p.AsDouble(),DB.UnitTypeId.LitersPerSecond)} for p in candidates]
    return (DB.UnitUtils.ConvertFromInternalUnits(selected.AsDouble(),DB.UnitTypeId.LitersPerSecond) if selected else None,
            selected.Definition.Name if selected else None,values)


def get_mep_connection_points(doc,settings):
    configured=configured_model_catalog(doc,settings)
    result={'devices':[],'terminals':[],'configured_model':configured,'warnings':list(configured['warnings']),
            'terminal_adapters_required':True}
    for cat in (DB.BuiltInCategory.OST_DuctTerminal,DB.BuiltInCategory.OST_MechanicalEquipment,DB.BuiltInCategory.OST_DuctFitting,DB.BuiltInCategory.OST_DuctAccessory,DB.BuiltInCategory.OST_GenericModel):
        for e in DB.FilteredElementCollector(doc).OfCategory(cat).WhereElementIsNotElementType():
            manager=e.MEPModel.ConnectorManager if getattr(e,'MEPModel',None) else None
            if manager is None and cat!=DB.BuiltInCategory.OST_DuctTerminal:continue
            connectors=[]
            for connector in manager.Connectors if manager else []:
                try:
                    if connector.Domain!=DB.Domain.DomainHvac:continue
                    if connector.ConnectorType!=DB.ConnectorType.End:continue
                    from .connection_points import descriptor
                    system=str(connector.DuctSystemType)
                    role='SUPPLY' if system=='SupplyAir' and str(connector.Direction)=='Out' else 'EXTRACT' if system in ('ReturnAir','ExhaustAir') and str(connector.Direction)=='In' else None
                    connectors.append(dict(descriptor(connector),network_type=role))
                except Exception as exc:result['warnings'].append('Conector de {} no resuelto: {}'.format(rr.eid(e.Id),exc))
            entry={'id':e.UniqueId,'element_id':rr.eid(e.Id),'name':rr.name(e),'family':e.Symbol.FamilyName,'connectors':connectors}
            if isinstance(e,DB.FamilyInstance):
                frame=e.GetTransform()
                entry['transform']={'origin_mm':[rr.mm(frame.Origin.X),rr.mm(frame.Origin.Y),rr.mm(frame.Origin.Z)],
                    'basis_x':[frame.BasisX.X,frame.BasisX.Y,frame.BasisX.Z],
                    'basis_y':[frame.BasisY.X,frame.BasisY.Y,frame.BasisY.Z],
                    'basis_z':[frame.BasisZ.X,frame.BasisZ.Y,frame.BasisZ.Z]}
                entry['body_box_mm']=_box(e)
            if cat==DB.BuiltInCategory.OST_DuctTerminal:
                metadata=ownership.read(e);entry['room_uid']=metadata.get('room_uid') if metadata else None
                role={'impulsion':'SUPPLY','extraccion':'EXTRACT'}.get(metadata.get('role')) if metadata else None
                if role is None:
                    hint=normalize(rr.name(e))
                    role='SUPPLY' if 'impulsion' in hint else 'EXTRACT' if 'extraccion' in hint else None
                family_role=next((n for n in ('SUPPLY','EXTRACT') if terminal_matches(entry['family'],entry['name'],n)),None)
                if family_role:role=family_role
                entry['suggested_network_type']=role;entry['network_assignment_requires_review']=metadata is None and family_role is None
                entry['airflow_lps'],entry['airflow_parameter'],entry['airflow_candidates']=_terminal_flow(e)
                from .terminal_adapters import attached,record,ports as adapter_ports
                candidates=[]
                if manager:
                    for original_port in adapter_ports(e):
                        adapters=attached(e,original_port)
                        candidates.extend((original_port,a) for a in adapters)
                if len(candidates)==1:
                    try:entry['terminal_adapter']=record(e,*candidates[0])
                    except Exception as exc:entry['adapter_association_status']='ELBOW_CONNECTOR_UNRESOLVED';result['warnings'].append(str(exc))
                elif len(candidates)>1:entry['adapter_association_status']='ELBOW_CONNECTOR_UNRESOLVED'
                if entry['airflow_lps'] is None:result['warnings'].append('Caudal de instancia ausente o ambiguo: '+e.UniqueId)
                result['terminals'].append(entry)
            elif connectors:
                entry['is_configured_distributor']=distributor_network(entry['family']) is not None
                if entry['is_configured_distributor']:
                    round_ports=[c for c in connectors if c['size'].get('shape')=='Round']
                    main=max((c['size']['diameter_mm'] for c in round_ports),default=None)
                    branches=[c for c in round_ports if c['size']['diameter_mm']<main-0.01] if main is not None else []
                    entry['branch_count_candidate']=len(branches)
                    entry['branch_capacity_matches_configuration']=len(branches) in settings['distributors']['branch_capacities']
                result['devices'].append(entry)
    if not result['terminals']:result['warnings'].append('No hay terminales colocados para recuperar caudales de diseño en este proyecto.')
    return result


def resolve_solids(doc,obstacle):
    if obstacle.get('link_uid'):
        link=doc.GetElement(obstacle['link_uid']);linked=link.GetLinkDocument() if link else None
        if linked is None:raise ValueError('Vínculo ausente o descargado: '+obstacle['link_uid'])
        e=linked.GetElement(obstacle['element_uid'])
        return [DB.SolidUtils.CreateTransformed(s,link.GetTotalTransform()) for s in rr.solids(e)]
    e=doc.GetElement(obstacle['element_uid'])
    if e is None:raise ValueError('Elemento original ausente: '+obstacle['element_uid'])
    return rr.solids(e)
