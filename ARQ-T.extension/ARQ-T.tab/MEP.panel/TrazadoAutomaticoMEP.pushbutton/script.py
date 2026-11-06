#! python3
"""Open the ventilation plan designer directly."""
from pyrevit import revit
try:
    # CPython may retain modules between executions. Reload the complete routing
    # dependency tree after its modal window closes, including imported mappings.
    import importlib
    import sys
    import ARQT_MEP
    sys.modules.pop('ARQT_MEP.family_rules',None)
    if hasattr(ARQT_MEP,'family_rules'):delattr(ARQT_MEP,'family_rules')
    prefix='ARQT_MEP.routing'
    for module_name in sorted([n for n in sys.modules if n==prefix or n.startswith(prefix+'.')],key=len,reverse=True):
        module=sys.modules.pop(module_name,None)
        parent_name,_,attribute=module_name.rpartition('.')
        parent=sys.modules.get(parent_name)
        if parent is not None and getattr(parent,attribute,None) is module:
            delattr(parent,attribute)
    importlib.invalidate_caches()
    from ARQT_MEP.routing.gui import show
    show(revit.doc,revit.uidoc)
except Exception as exc:
    import traceback
    import clr
    clr.AddReference('PresentationFramework')
    from System.Windows import MessageBox
    print(traceback.format_exc())
    MessageBox.Show(str(exc),'ARQ-T | Trazado automático MEP')
