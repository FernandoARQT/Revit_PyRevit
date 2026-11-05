"""Render actual WinForms with bundled CPython, fake read-only model choices.

Revit imports/units are replaced ONLY here. This is a UI interoperability test,
not evidence of model creation or geometry API correctness.
"""
import importlib.util
import ast
import json
from pathlib import Path
import sys
import types

import clr
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
from System.Drawing import Bitmap
import System.Windows.Forms as WF

root = Path(__file__).resolve().parents[1]
bundle = root / "ARQ-T.extension/ARQ-T.tab/ARQ.panel/SuelosAutomaticos.pushbutton"
artifacts = root / "tests/artifacts"
artifacts.mkdir(exist_ok=True)


class Item:
    def __init__(self, id, name, number="", area=0, elevation=0):
        self.Id = types.SimpleNamespace(Value=id)
        self.Name, self.Number, self.Area = name, number, area
        self.Elevation, self.ProjectElevation = elevation, elevation

    def get_Parameter(self, unused):
        return types.SimpleNamespace(AsString=lambda: self.Name)


db = types.ModuleType("Autodesk.Revit.DB")
db.UnitTypeId = types.SimpleNamespace(Meters="m", SquareMeters="m2")
db.UnitUtils = types.SimpleNamespace(ConvertFromInternalUnits=lambda value, unit: value)
db.BuiltInParameter = types.SimpleNamespace(ROOM_NAME="name")
for name in ("Autodesk", "Autodesk.Revit", "Autodesk.Revit.UI"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["Autodesk.Revit.DB"] = db
sys.modules["Autodesk.Revit.UI"].TaskDialog = object
sys.modules["Autodesk"].Revit = sys.modules["Autodesk.Revit"]
sys.modules["Autodesk.Revit"].DB = db
fake_geometry = types.ModuleType("arqt_geometry")
fake_geometry.element_id = lambda element: element.Id.Value
sys.modules["arqt_geometry"] = fake_geometry
fake_floors = types.ModuleType("arqt_floors")
fake_floors.type_name = lambda element: element.Name
sys.modules["arqt_floors"] = fake_floors

# Only the three Revit assemblies are skipped. Forms/Drawing/controls and event
# handlers are the installed .NET / Python.NET implementations.
original_add = clr.AddReference
clr.AddReference = lambda name: None if name in ("RevitAPI", "RevitAPIUI", "RevitAPIIFC") else original_add(name)
spec = importlib.util.spec_from_file_location("arqt_ui_preview", bundle / "script.py")
module = importlib.util.module_from_spec(spec)
module.geometry, module.floors = fake_geometry, fake_floors
# Only replace the two model-module initializations; execute the actual UI and
# its event handlers, including all real WinForms/Python.NET control creation.
tree = ast.parse((bundle / "script.py").read_text(encoding="utf-8"))
tree.body = [node for node in tree.body if not (
    isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and
    isinstance(node.value.func, ast.Name) and node.value.func.id == "_load_local_module")]
exec(compile(tree, str(bundle / "script.py"), "exec"), module.__dict__)
rooms = [Item(index + 1, name, "{:02d}".format(index + 1), area) for index, (name, area) in enumerate(
    [("Salón", 42.50), ("Cocina", 18.30), ("Dormitorio 1", 16.20), ("Baño 1", 7.80),
     ("Dormitorio 2", 13.20), ("Vestíbulo", 8.50), ("Lavadero", 5.70), ("Estudio", 12.40)])]
levels = [Item(100, "Planta baja"), Item(101, "Planta primera", elevation=3)]
import logging
window = module.FloorsWindow(None, rooms, [Item(200, "Pavimento · porcelánico 15 mm"),
                            Item(201, "Pavimento · madera 20 mm")], levels, levels[0],
                            Item(300, "Nueva construcción"), 2, logging.getLogger("preview"))
assert not window.selected and not window.generate.Enabled
window._select_all(None, None)
assert len(window.selected) == 8 and window.generate.Enabled
window.search.Text = "cocina"
assert window.grid.Rows.Count == 1 and len(window.selected) == 8
window._select_none(None, None)
assert not window.selected and not window.generate.Enabled
window.search.Text = ""
for index in range(4):
    window.grid.Rows[index].Cells[0].Value = True
assert len(window.selected) == 4
from System.ComponentModel import ListSortDirection
window.grid.Sort(window.grid.Columns["name"], ListSortDirection.Descending)
assert len(window.selected) == 4
for row in window.grid.Rows:
    assert bool(row.Cells[0].Value) == (int(row.Tag) in window.selected)
window.grid.Sort(window.grid.Columns["number"], ListSortDirection.Ascending)
window.continuous.Checked = True
assert not window.per_room.Checked
window.per_room.Checked = True
assert not window.continuous.Checked
window.offset.Value = module.Decimal(120)
assert "+0,120" in window.elevation.Text
window.level_combo.SelectedIndex = 1
window.offset.Value = module.Decimal(-120)
assert "+2,880" in window.elevation.Text
window.level_combo.SelectedIndex = 0
window.offset.Value = module.Decimal(120)
# Exercise the actual continuous confirmation handler, without model mutation.
fake_geometry.AREA_EPS = 1e-7
group_diagnostics = {"added_area":15, "preserved_holes":1, "preserved_hole_area":2,
                     "cleanup_area":.001,"group_count":2,"groups":[["3","4"],["6"]]}
fake_geometry.prepare = lambda *args: [{"area":100,"diagnostics":group_diagnostics},
                                      {"area":20,"diagnostics":group_diagnostics}]
fake_floors.validate_settings = lambda *args: 0
fake_floors.likely_duplicates = lambda *args: []
def unexpected_creation(*args):
    raise AssertionError("No must not create floors")
fake_floors.create = unexpected_creation
messages = []
class Confirmation:
    @staticmethod
    def Show(owner, message, *args):
        messages.append(message)
        return WF.DialogResult.No
actual_forms = module.WF
module.WF = types.SimpleNamespace(MessageBox=Confirmation, DialogResult=WF.DialogResult,
    MessageBoxButtons=WF.MessageBoxButtons, MessageBoxIcon=WF.MessageBoxIcon,
    MessageBoxDefaultButton=WF.MessageBoxDefaultButton)
window.continuous.Checked = True
try:
    window._generate(None, None)
finally:
    module.WF = actual_forms
assert len(messages) == 1 and "Superficie añadida en pasos de puerta" in messages[0]
assert "Huecos interiores conservados: 1" in messages[0]
assert "Ajuste por tolerancia de Revit:" in messages[0]
assert "Suelos que se crearán: 2" in messages[0]
assert "Suelo 2 · habitaciones: 6" in messages[0]
window.per_room.Checked = True
window.CreateControl()
window.Show()
WF.Application.DoEvents()
bitmap = Bitmap(window.Width, window.Height)
window.DrawToBitmap(bitmap, window.ClientRectangle.__class__(0, 0, window.Width, window.Height))
bitmap.Save(str(artifacts / "winforms-preview.png"))
bitmap.Dispose()
window.Close()
window.Dispose()
(artifacts / "winforms-result.json").write_text(json.dumps({
    "python": sys.version,
    "host": ".NET Framework (standalone test; not Revit)",
    "checks": ["actual WinForms construction", "empty default selection", "select/deselect all",
               "filter preserves selection", "sort preserves selection", "checkbox events", "exclusive modes",
               "positive/negative offset and creation level", "bitmap rendering"],
    "continuous_confirmation": "added area and preserved holes verified; No creates nothing",
    "revit_model_tested": False,
}, indent=2), encoding="utf-8")
