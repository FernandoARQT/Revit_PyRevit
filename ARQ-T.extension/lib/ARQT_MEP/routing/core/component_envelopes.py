"""Component-local envelope manifest. Unknown fitting geometry stays unknown."""
import copy


def describe(route,configuration,configured_model):
    section=route['section'];rid=route['route_id'];components=[]
    dimensions={k:section[k] for k in ('outer_diameter_mm','width_mm','height_mm') if k in section}
    for i,piece in enumerate(route.get('primitives',[])):
        components.append({'component_id':rid+':piece:'+str(i),
            'component_type':'Elbow' if piece['kind']=='ARC' else 'DuctSegment',
            'start_xyz_mm':piece['start'],'end_xyz_mm':piece['end'],'geometry':copy.deepcopy(piece),
            'outer_dimensions_mm':dict(dimensions),'insulation_mm':section.get('insulation_mm',0),
            'installation_clearance_mm':route['clearance_mm'],'native_family_type_id':None,
            'geometry_verification_status':'CENTRAL_ENVELOPE_CHECKED',
            'manufacturer_geometry_status':'PROVISIONAL' if piece['kind']=='ARC' and configuration.get('bend_geometry_provisional',True) else 'NOT_VERIFIED'})
    flex=configured_model.get('flexible_duct',{})
    transitions={p['endpoint']:p for p in route.get('required_transitions',[])}
    if route.get('connection_validation_pending') or transitions:
        anchors=route.get('connection_anchors_mm',{})
        for endpoint in ('source','destination'):
            port=route[endpoint];transition=transitions.get(endpoint)
            components.append({'component_id':rid+':connection:'+endpoint,
                'component_type':'Transition' if transition else 'EndConnection',
                'start_xyz_mm':port['xyz_mm'],'end_xyz_mm':anchors.get(endpoint),
                'geometry':None,'outer_dimensions_mm':None,
                'connection_diameters_mm':dict(transition) if transition else copy.deepcopy(port.get('size',{})),
                'insulation_mm':None,'installation_clearance_mm':route['clearance_mm'],
                'native_family_type_id':flex.get('transition_type_id') if transition else None,
                'geometry_verification_status':'PENDING_LOCAL_FITTING_VERIFICATION',
                'manufacturer_geometry_status':'NOT_VERIFIED'})
    return components
