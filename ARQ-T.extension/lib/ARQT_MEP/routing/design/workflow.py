"""User-facing stage/preflight contracts; no WPF or Revit dependencies."""
import copy
from dataclasses import asdict
from .model import DuctProfile
from ...family_rules import distributor_network


def configured_profile(configuration):
    section=configuration['section']
    if section.get('shape')!='CIRCULAR':raise ValueError('El estudio de ventilación radial requiere un perfil circular.')
    return DuctProfile('project-envelope','SUPPLY',section['nominal_diameter_mm'],
        None,section['outer_diameter_mm'],section.get('insulation_mm',0),
        configuration['bend_radius_mm'],None,False)


def equipment_choices(catalog):
    devices=catalog.get('devices',[])
    return {'HRV':[d for d in devices if 'recuperador' in d.get('family','').casefold()],
        **{network:[d for d in devices if d.get('is_configured_distributor') and distributor_network(d.get('family',''))==network]
           for network in ('SUPPLY','EXTRACT')}}


def defaults(catalog,configuration):
    choices=equipment_choices(catalog);selected={}
    selected['HRV']=next((d['id'] for d in choices['HRV'] if str(configuration.get('hrv_id')) in (d['id'],str(d['element_id']))),
        choices['HRV'][0]['id'] if len(choices['HRV'])==1 else None)
    assignments=configuration.get('equipment_assignments',[])
    for network in ('SUPPLY','EXTRACT'):
        explicit=[d['id'] for d in choices[network] for a in assignments if a.get('network_type')==network and
            str(a.get('element_id',a.get('element_uid'))) in (d['id'],str(d['element_id']))]
        selected[network]=explicit[0] if len(explicit)==1 else choices[network][0]['id'] if len(choices[network])==1 else None
    return selected


def payload(catalog,configuration,selected,destinations,profile,clearance_mm):
    choices=equipment_choices(catalog);lookup={d['id']:d for d in catalog.get('devices',[])}
    if selected.get('HRV') not in {d['id'] for d in choices['HRV']}:raise ValueError('Seleccione el recuperador de referencia.')
    if not destinations:raise ValueError('Seleccione al menos un terminal para estudiar los corredores.')
    networks={d['network_type'] for d in destinations};assignments=[]
    for network in sorted(networks):
        did=selected.get(network)
        if did not in {d['id'] for d in choices.get(network,[])}:
            raise ValueError('Seleccione el distribuidor de '+('impulsión' if network=='SUPPLY' else 'extracción')+'.')
        assignments.append({'element_id':lookup[did]['element_id'],'network_type':network})
    from .spatial import envelope
    envelope(profile,clearance_mm)
    configuration=copy.deepcopy(configuration)
    strategy='MANUAL_BUNDLE' if configuration.get('routing_strategy')=='MANUAL_BUNDLE' else 'GLOBAL_RADIAL_V3'
    configuration.update(hrv_id=selected['HRV'],equipment_assignments=assignments,routing_strategy=strategy)
    if configuration.get('main_connection'):configuration['main_connection']['kind']='RIGID_CIRCULAR'
    return {'configuration':configuration,'destinations':copy.deepcopy(destinations),
        'duct_profile':asdict(profile),'clearance_mm':clearance_mm}


def stage(proposal,busy=False):
    if busy:return 'GENERATING'
    if proposal is None:return 'READY'
    if proposal.corridors:return 'EDITABLE'
    if proposal.corridor_graph:return 'NO_CORRIDORS'
    return 'CONTEXT_ONLY'


def summary(proposal):
    if proposal is not None and proposal.configuration.get('routing_strategy')=='MANUAL_BUNDLE':
        assigned=len(proposal.corridor_assignments)
        if proposal.design_stage=='AXES':
            valid=len(proposal.validation.get('valid_route_ids',[]))
            return '{} ejes válidos de {} asignados · {} corredores manuales · modelo nativo pendiente'.format(valid,assigned,len(proposal.corridors))
        return '{} corredores manuales · {} destinos asignados · dibuje, asigne rejillas y calcule los ejes'.format(len(proposal.corridors),assigned)
    if proposal is None:return 'Seleccione equipos y destinos. Generar corredores analiza el modelo y abre el resultado en planta.'
    graph=proposal.corridor_graph;stats=graph.get('stats',{})
    if proposal.configuration.get('routing_strategy')=='GLOBAL_RADIAL_V3':
        if proposal.design_stage=='AXES':
            routes=proposal.individual_routes;valid=sum(r.get('validation_status')=='GEOMETRICALLY_VALID' for r in routes.values())
            mains=sum(r.get('validation_status')=='GEOMETRICALLY_VALID' for r in proposal.main_connections.values())
            return 'Ejes · {} válidos / {} ramales · principales {}/2 · {} destinos sin distribución · conexiones sin modelar'.format(valid,len(routes),mains,len(proposal.terminals)-len(proposal.selected_paths))
        counts={n:sum(t['network_type']==n for t in proposal.terminals.values()) for n in ('SUPPLY','EXTRACT')}
        connected={n:sum(proposal.terminals[t]['network_type']==n for t in proposal.corridor_assignments) for n in counts}
        pending=len(proposal.terminals)-sum(connected.values())
        conditional=sum(r.get('status')=='CONDITIONAL_PASSAGE_REQUIRED' for r in proposal.connectivity.values())
        return 'Distribución · impulsión {}/{} · extracción {}/{} · {} pendientes · {} condicionados · ejes individuales pendientes'.format(
            connected['SUPPLY'],counts['SUPPLY'],connected['EXTRACT'],counts['EXTRACT'],pending,conditional)
    if any(c.get('generated_by')=='V3_FREE_SPACE_GRAPH' for c in proposal.corridors.values()):
        return 'Estudio anterior de candidatos geométricos. Pulse Proponer distribución para obtener conexiones completas hasta los codos.'
    if not graph:
        return 'Borrador sin cálculo automático. Pulse Generar corredores para analizar el edificio y obtener ejes de estudio.'
    count=len(proposal.corridors)
    if not count:
        if not stats.get('nodes'):return 'No hay candidatos para la envolvente y representación actuales. Revise volumen, perfil y alcance; no demuestra imposibilidad física.'
        return 'Hay puntos libres, pero no tramos continuos comprobados en el presupuesto disponible. Revise las incidencias o amplíe el cálculo.'
    suffix=' · estudio parcial por límite de cálculo' if not stats.get('enumeration_finished',False) else ''
    return '{} corredores de estudio · {} terminales{} · doble clic en un tramo para añadir un punto'.format(count,len(proposal.terminals),suffix)
