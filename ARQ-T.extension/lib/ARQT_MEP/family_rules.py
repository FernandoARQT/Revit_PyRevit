"""Shared, explicit family selection for ventilation and heat recovery."""
from .airflow import normalize

TERMINALS = {
    'impulsion': ('Fraenkische_rejilla 125_Suministro', 'Suministro'),
    'extraccion': ('Fraenkische_rejilla 125_Extraccion', 'Extracción'),
}
DISTRIBUTORS = {'SUPPLY': ('Impulsion_Suministro', 'Impusion_Suministro'), 'EXTRACT': ('Extracción_Aire viciado',)}
FLEX_TYPE = 'Flex - Redondo'


def distributor_network(family):
    matches = [network for network, fragments in DISTRIBUTORS.items()
               if any(normalize(fragment) in normalize(family) for fragment in fragments)]
    return matches[0] if len(matches) == 1 else None


def terminal_matches(family, type_name, network):
    role = {'SUPPLY': 'impulsion', 'EXTRACT': 'extraccion'}.get(network)
    if role is None:
        return False
    expected_family, expected_type = TERMINALS[role]
    return normalize(expected_family) == normalize(family) and type_name == expected_type
