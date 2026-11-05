#! python3
"""Calcula, edita y distribuye terminales de ventilación de una vivienda.

Revisa habitaciones de todas las plantas, equilibra los caudales de DB-HS 3 y
propone posiciones sobre las caras inferiores de los falsos techos. Permite
arrastrar, añadir o eliminar rejillas, ajustar Flow y revisar zonas de cocina.
No crea conductos. Flow todavía no está asociado al conector HVAC.
"""
from pyrevit import revit
__persistentengine__ = True

try:
    from System import AppDomain
    window=AppDomain.CurrentDomain.GetData('ARQT_VentilationWindow_'+revit.doc.ProjectInformation.UniqueId)
    if window is not None:
        window.Activate()
    else:
        # Persistent CPython retains imports. Refresh only this editor's modules
        # after its window closes; do not disturb the separate routing tool.
        import importlib
        import sys
        import ARQT_MEP
        for name in ('family_rules','airflow','config','surface','spatial','ownership','revit_read','service','revit_write',
                     'plan_transform','proposal','proposal_store','proposal_adapter','runtime',
                     'plan_canvas','preview_image','external_event','editor_gui','gui'):
            module='ARQT_MEP.'+name
            sys.modules.pop(module,None)
            if hasattr(ARQT_MEP,name):delattr(ARQT_MEP,name)
        importlib.invalidate_caches()
        from ARQT_MEP.gui import show
        show(revit.doc,revit.uidoc)
except Exception as exc:
    import traceback
    from pyrevit import script
    script.get_output().print_md('### Distribuir ventilación\nNo se ha completado la operación.')
    print(traceback.format_exc())
    import clr
    clr.AddReference('PresentationFramework')
    from System.Windows import MessageBox
    MessageBox.Show(str(exc),'ARQ-T | Distribuir ventilación')
