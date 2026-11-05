"""Check the production backend loader with pyRevit's bundled CPython."""
import ast
import json
import os
from pathlib import Path
import sys
import importlib.util
import io
import unittest

root = Path(__file__).resolve().parents[1]
source = root / "ARQ-T.extension/ARQ-T.tab/ARQ.panel/SuelosAutomaticos.pushbutton/arqt_geometry.py"
tree = ast.parse(source.read_text(encoding="utf-8"))
function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_polygon_backend")
scope = {"__file__": str(source), "os": os, "sys": sys, "GeometryError": RuntimeError}
exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), scope)
Polygon, merge = scope["_polygon_backend"]()
region = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
covers = [Polygon([(0, 0), (6, 0), (6, 10), (0, 10)]),
          Polygon([(4, 0), (10, 0), (10, 10), (4, 10)])]
remaining = region.difference(merge(covers)).area
assert remaining == 0
import shapely
import numpy
result = {"python": sys.version, "shapely": shapely.__version__, "numpy": numpy.__version__,
          "geos": shapely.geos_version_string, "module": shapely.__file__,
          "uncovered_area": remaining, "revit_model_tested": False}
spec = importlib.util.spec_from_file_location("arqt_plan_regressions", root / "tests/test_suelos.py")
regressions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(regressions)
suite = unittest.defaultTestLoader.loadTestsFromTestCase(regressions.ContinuousPipelineTests)
plan_count = suite.countTestCases()
suite = unittest.defaultTestLoader.loadTestsFromModule(regressions)
stream = io.StringIO()
run = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
assert run.wasSuccessful(), stream.getvalue()
result["production_plan_cases"] = plan_count
result["production_plan_cases_passed"] = run.wasSuccessful()
result["independent_regression_cases"] = run.testsRun
result["independent_regressions_passed"] = run.wasSuccessful()
artifacts = root / "tests/artifacts"
artifacts.mkdir(exist_ok=True)
(artifacts / "shapely-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
