"""Referenced product envelopes; choosing a product is not native validation."""
from dataclasses import asdict
from .model import DuctProfile

BROCHURE='https://excentshop.com/wp-content/uploads/2023/04/Frankische-Profi-Air-Pipe-System-Components_EN.pdf#page=4'
PRODUCT_PAGE='https://www.fraenkische.com/en-GB/product/profi-air-classic-pipe-grey-in-coils'
# Manufacturer's product brochure p.4, checked 2026-11-07. Nominal connection
# sizes, exterior diameters and bore diameters are separate quantities.
CLASSIC={75:(76,63),90:(91,78)}


def classic(nominal,network='SUPPLY'):
    exterior,interior=CLASSIC[nominal]
    return DuctProfile('fraenkische-classic-'+str(nominal),network,nominal,interior,exterior,0,150,
        'FRANKISCHE profi-air classic NW '+str(nominal)+'; '+BROCHURE,True)


def choices(configuration):
    from .workflow import configured_profile
    return [('Envolvente del proyecto',configured_profile(configuration))]+[
        ('profi-air classic NW {} · exterior {} mm'.format(n,CLASSIC[n][0]),classic(n)) for n in CLASSIC]


def suggest(catalog):
    sizes={round(p['size']['diameter_mm']) for t in catalog.get('terminals',[]) for p in t.get('terminal_adapter',{}).get('outlets',[]) if not p.get('connected')}
    return classic(next(iter(sizes))) if len(sizes)==1 and next(iter(sizes)) in CLASSIC else None
