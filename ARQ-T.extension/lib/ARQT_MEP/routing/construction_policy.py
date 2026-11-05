"""Mandatory construction rules, ahead of project overrides."""
from ..airflow import normalize


def construction_rule(name,category=None,roof_evidence=False):
    text=normalize(name)
    if category=='OST_Roofs' or roof_evidence or any(k in text for k in ('teja sobre forjado','cubierta','roof')):
        return {'identity':'IMPASSABLE_ROOF','classification':'FORBIDDEN','hard_forbidden':True,'crossing_multiplier':1.0}
    if 'losa alveolar' in text:
        return {'identity':'HOLLOW_CORE_SLAB','classification':'CONDITIONAL','hard_forbidden':False,'crossing_multiplier':5.0}
    return None


def intervention_cost(material_classes=(),identity=None):
    """Reference equivalent metres only; never a penetration permission."""
    classes=[normalize(value) for value in material_classes]
    if identity=='IMPASSABLE_ROOF':return None
    if identity=='HOLLOW_CORE_SLAB':return {'construction_class':'HOLLOW_CORE_SLAB','equivalent_length_m':8.0}
    for kind,words,cost in (('CONCRETE',('concrete','hormigon'),4.0),
                            ('MASONRY',('masonry','mamposteria','brick','ladrillo'),2.0),
                            ('LIGHT_PARTITION',('gypsum','yeso','plaster','metal','wood','madera'),1.0)):
        if any(word in value for value in classes for word in words):
            return {'construction_class':kind,'equivalent_length_m':cost}
    return None
