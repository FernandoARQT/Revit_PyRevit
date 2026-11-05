"""Manufacturer-data-only pressure estimates; unknown is never zero loss."""
import math


def assess(route,request,configuration):
    model=configuration.get('pressure_model',{})
    diameter=route.get('section',{}).get('nominal_diameter_mm') or request.get('sizing',{}).get('diameter_mm')
    data=model.get('diameters',{}).get(str(diameter),model.get('diameters',{}).get(str(int(diameter))) if diameter else None)
    if not data or not data.get('reference'):
        return {'status':'UNVERIFIED','reason':'MANUFACTURER_PRESSURE_DATA_REQUIRED'}
    flow=request['airflow_lps'];table=sorted(data.get('flow_loss_table',[]))
    if any(len(row)!=2 or not all(math.isfinite(float(v)) and v>=0 for v in row) for row in table):
        raise ValueError('Tabla de pérdidas no válida: se requieren caudales y pérdidas finitos no negativos.')
    if any(a[0]>=b[0] for a,b in zip(table,table[1:])):raise ValueError('Caudales repetidos en la tabla de pérdidas.')
    if any(not math.isfinite(float(data[k])) or data[k]<0 for k in ('bend_k','transition_k') if k in data):
        raise ValueError('Coeficientes de accesorios no válidos.')
    if len(table)<2 or not table[0][0]<=flow<=table[-1][0]:
        return {'status':'UNVERIFIED','reason':'FLOW_OUTSIDE_VERIFIED_TABLE'}
    for (qa,pa),(qb,pb) in zip(table,table[1:]):
        if qa<=flow<=qb:
            friction=pa+(pb-pa)*(flow-qa)/(qb-qa);break
    # Require an explicit verified coefficient for every modeled component.
    bends=route.get('bends',0);transitions=len(request.get('required_transitions',[]))
    if (bends and 'bend_k' not in data) or (transitions and 'transition_k' not in data):
        return {'status':'UNVERIFIED','reason':'FITTING_LOSS_DATA_REQUIRED'}
    rho=model.get('density_kg_m3',1.2);velocity=flow/1000/(math.pi*(diameter/1000)**2/4)
    loss=friction*route['length_m']+(bends*data.get('bend_k',0)+transitions*data.get('transition_k',0))*rho*velocity**2/2
    return {'status':'ESTIMATED_FROM_REFERENCED_DATA','pressure_loss_pa':loss,'reference':data['reference'],
            'scope':'route only; equipment and balancing require their own verified data'}
