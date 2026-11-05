"""Auxiliary connector identification, independent of WPF and route algorithms."""

HRV_FUNCTIONS = {
    ('SupplyAir', 'Out'): ('SUPPLY', 'Impulsión → habitaciones / salón'),
    ('ReturnAir', 'In'): ('EXTRACT', 'Extracción ← baños / cocina'),
    ('ExhaustAir', 'In'): ('EXTRACT', 'Extracción ← baños / cocina'),
    ('SupplyAir', 'In'): (None, 'Admisión ← aire del exterior'),
    ('ExhaustAir', 'Out'): (None, 'Expulsión → aire al exterior'),
}


def connection_info(device, port):
    is_hrv = 'recuperador' in device.get('family', '').casefold()
    if is_hrv:
        network, label = HRV_FUNCTIONS.get(
            (port.get('classification'), port.get('flow_direction')),
            (None, 'Función no identificada · revisar conector'))
        return {'label': label, 'network': network or '', 'kind': 'HRV',
                'is_hrv': True, 'interior': network is not None}
    network = port.get('network_type') or ''
    label = {'SUPPLY': 'Impulsión interior', 'EXTRACT': 'Extracción interior'}.get(
        network, 'Revisar clasificación / dirección')
    return {'label': label, 'network': network,
            'kind': 'DISTRIBUTOR' if device.get('is_configured_distributor') else '',
            'is_hrv': False, 'interior': network in ('SUPPLY', 'EXTRACT')}


def validate_origin(device, port, network):
    info = connection_info(device, port)
    if info['is_hrv']:
        if not info['interior']:
            raise ValueError('Este conector es de exterior o no está identificado; no sirve como origen de una red interior.')
        if info['network'] != network:
            raise ValueError('La red elegida no coincide con la función del recuperador: '+info['label'])
