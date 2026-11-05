"""Discrete circular sizing from native document sizes and final route airflow."""
import math


def circular_size(flow_lps,diameters_mm,max_velocity_mps=3.0):
    if not math.isfinite(flow_lps) or flow_lps<=0 or not math.isfinite(max_velocity_mps) or max_velocity_mps<=0:
        raise ValueError('Caudal y velocidad de dimensionado deben ser positivos y finitos.')
    for diameter in sorted(set(diameters_mm)):
        if not math.isfinite(diameter) or diameter<=0:continue
        velocity=(flow_lps/1000)/(math.pi*(diameter/1000)**2/4)
        if velocity<=max_velocity_mps+1e-9:
            return {'diameter_mm':diameter,'velocity_mps':velocity,'airflow_lps':flow_lps,
                    'max_velocity_mps':max_velocity_mps,'method':'NATIVE_SIZES_MAX_VELOCITY'}
    raise ValueError('Ningún diámetro disponible admite {:.3f} L/s a {:.2f} m/s.'.format(flow_lps,max_velocity_mps))


def size_requests(requests,catalog,configuration,overrides):
    flex=catalog.get('configured_model',{}).get('flexible_duct',{})
    sizes=flex.get('sizing_diameters_mm')
    if not sizes:return
    for request in requests:
        if request.get('error') not in (None,'CONNECTION_SIZE_REQUIRES_UNMODELLED_TRANSITION','MAIN_TRANSITION_REQUIRED'):continue
        selected=circular_size(request['airflow_lps'],sizes,configuration.get('sizing',{}).get('max_velocity_mps',3.0))
        diameter=selected['diameter_mm'];rid=request['route_id']
        ports=[request['source'],request['destination']]
        if any(p.get('size',{}).get('shape')!='Round' for p in ports):
            request['error']='ROUND_CONNECTORS_REQUIRED';continue
        transitions=[{'endpoint':endpoint,'from_mm':p['size']['diameter_mm'],'to_mm':diameter}
                     for endpoint,p in zip(('source','destination'),ports) if abs(p['size']['diameter_mm']-diameter)>.01]
        if transitions and not flex.get('transition_type_id'):
            request['error']='TRANSITION_TYPE_NOT_CONFIGURED';continue
        request.pop('error',None)
        request['sizing']=selected;request['required_transitions']=transitions
        request['compact_connections']=True
        # End fittings belong to docking, never to the entire duct corridor.
        envelope=max([diameter]+[p['size']['diameter_mm'] for p in ports])
        overrides[rid]={'section':{'shape':'CIRCULAR','nominal_diameter_mm':diameter,
                                  'outer_diameter_mm':diameter,'insulation_mm':0},
                        'clearance_mm':configuration['clearance_mm'],
                        'bend_radius_mm':configuration['bend_radius_mm']}
        request['sizing']['connection_envelope_diameter_mm']=envelope
        request['sizing']['connection_geometry_status']='PENDING_LOCAL_FITTING_VERIFICATION'
