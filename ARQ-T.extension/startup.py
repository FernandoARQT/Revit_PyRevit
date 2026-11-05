#! python3
import arqt_ribbon_assets

try:
    arqt_ribbon_assets.apply(__revit__)
except Exception as exc:
    print('ARQ-T: no se pudieron actualizar los recursos de la cinta: {}'.format(exc))
