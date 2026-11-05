#! python3
import os
import sys
import importlib.util

root=os.path.dirname(__file__)
if root not in sys.path:
    sys.path.insert(0,root)
for name in ('settings','candidates','optimizer','geometry','revit_adapter','ui'):
    spec=importlib.util.spec_from_file_location(name,os.path.join(root,name+'.py'))
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    spec.loader.exec_module(module)
from pyrevit import revit
import ui
try:
    ui.run(revit.doc,revit.uidoc,root)
except Exception as exc:
    ui.alert('No se pudo completar el comando:\n'+str(exc))
