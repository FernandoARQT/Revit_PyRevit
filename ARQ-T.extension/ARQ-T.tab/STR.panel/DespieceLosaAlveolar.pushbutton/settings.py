import copy
import json
import math

CONFIG_SECTION='ARQT_DespieceLosaAlveolar_v1'


def make_default_settings():
    groups=[([1200,900],1),([750,600,450],2),([1050,1000,950,850,825,800,725,700,675,650,625,575,550,500],3)]
    return {'version':1,'widths':[{'width':w,'category':c,'penalty':0 if w==1200 else 2 if w==900 else 5 if c==2 else 10,'enabled':True} for ws,c in groups for w in ws],
            'minimum_preferred':600,'minimum_exceptional':450,'left':[20,25,30],'right':[20,25,30],
            'plate_cost':1,'activation_cost':3,'tolerance_standard':5,'tolerance_other':25,
            'direction':'N-S','reverse':False,'edges':{'left':'Libre','right':'Libre'}}


def validate_settings(s):
    if s.get('version')!=1:
        raise ValueError('Versión de configuración no compatible.')
    seen=set()
    for o in s['widths']:
        w=o['width']
        if type(w)!=int or not 0<w<=1200 or w in seen:
            raise ValueError('Anchuras enteras únicas de 1 a 1200 mm.')
        seen.add(w)
        if o['category'] not in (1,2,3) or type(o['enabled'])!=bool:
            raise ValueError('Categoría o estado inválido.')
        if not math.isfinite(o['penalty']) or o['penalty']<0:
            raise ValueError('Penalización no negativa y finita.')
    if not 0<s['minimum_exceptional']<=s['minimum_preferred']<=1200:
        raise ValueError('Mínimos: 0 < excepcional ≤ preferente ≤ 1200.')
    for side in ('left','right'):
        a,b,c=s[side]
        if not all(math.isfinite(v) for v in (a,b,c)) or not 0<=a<=b<=c:
            raise ValueError('Holguras: 0 ≤ mínimo ≤ nominal ≤ máximo.')
    for key in ('plate_cost','activation_cost','tolerance_standard','tolerance_other'):
        if not math.isfinite(s[key]) or s[key]<0:
            raise ValueError('Costes y tolerancias no negativos y finitos.')
    if s['direction'] not in ('N-S','E-O'):
        raise ValueError('Dirección no compatible.')
    return s


def validate_and_migrate(s):
    return validate_settings(copy.deepcopy(s))


def load_settings(backend=None):
    if backend is None:
        from pyrevit import script
        backend=script.get_config(CONFIG_SECTION)
    raw=backend.get_option('settings_json',None)
    return make_default_settings() if not raw else validate_and_migrate(json.loads(raw))


def save_settings(s,backend=None,commit=None):
    validate_settings(s)
    if backend is None:
        from pyrevit import script
        backend=script.get_config(CONFIG_SECTION)
        commit=script.save_config
    backend.set_option('settings_json',json.dumps(s,ensure_ascii=False))
    if commit:
        commit()
