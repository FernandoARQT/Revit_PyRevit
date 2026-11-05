import copy
import json

DEFAULT={'version':1,'airflow_policy_version':3,'resolution_mm':250,'wall_clearance_mm':110,'terminal_spacing_mm':1000,'tolerance_mm':1,
         'connection_space_mm':100,'weights':{'wall':1.0,'door':0.7,'center':0.3,'spread':1.5,'furniture':1.0},
         'overrides':{},'housing_groups':{},'main_bedrooms':{}}


def load():
    from pyrevit import script
    raw=script.get_config('ARQT_DistribuirVentilacion_v1').get_option('settings_json',None)
    return validate(migrate(json.loads(raw))) if raw else copy.deepcopy(DEFAULT)


def migrate(s):
    s=copy.deepcopy(s)
    if s.get('airflow_policy_version',1)<2:
        overrides=s.get('overrides',{})
        for uid,item in list(overrides.items()):
            if item.get('use') in ('principal','dormitorio','principal_mixto','dormitorio_mixto'):
                role=overrides.get(uid+':impulsion',{})
                requested=role.get('design')
                if requested is not None and 0<float(requested)<8:role['design']=8.0
                item['use']={'principal':'dormitorio','principal_mixto':'dormitorio_mixto'}.get(item['use'],item['use'])
        s['airflow_policy_version']=2
    if s.get('airflow_policy_version',1)<3:
        s['airflow_policy_version']=3
    for key,value in DEFAULT.items():s.setdefault(key,copy.deepcopy(value))
    return s


def validate(s):
    import math
    if s.get('version')!=1:raise ValueError('Versión de configuración desconocida.')
    for key in ('resolution_mm','wall_clearance_mm','terminal_spacing_mm','tolerance_mm'):
        if not math.isfinite(s[key]) or s[key]<0:raise ValueError('Configuración geométrica inválida.')
    if not 50<=s['resolution_mm']<=2000 or not 0<s['tolerance_mm']<=10:raise ValueError('Resolución o tolerancia fuera de rango.')
    if not math.isfinite(s.get('connection_space_mm',100)) or s.get('connection_space_mm',100)<0:raise ValueError('Reserva superior de conexión inválida.')
    if any(not math.isfinite(v) or v<0 for v in s['weights'].values()):raise ValueError('Pesos no negativos y finitos.')
    return s


def save(s):
    from pyrevit import script
    validate(s)
    script.get_config('ARQT_DistribuirVentilacion_v1').set_option('settings_json',json.dumps(s,ensure_ascii=False))
    script.save_config()
