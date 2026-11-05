"""GEOS plan regressions and tests of atomic Revit-call failure handling.

No Revit is started. GEOS is real; vectors and Revit API calls use doubles.
Model extraction and actual floor creation still require integration QA.
"""
import importlib.util
import ast
import json
import logging
from pathlib import Path
import sys
import types
import unittest
import tempfile
from unittest.mock import patch, Mock
from math import hypot

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "ARQ-T.extension/ARQ-T.tab/ARQ.panel/SuelosAutomaticos.pushbutton"


class Vector:
    def __init__(self, x, y, z=0):
        self.X, self.Y, self.Z = x, y, z

    def __add__(self, other):
        return Vector(self.X + other.X, self.Y + other.Y, self.Z + other.Z)

    def __sub__(self, other):
        return Vector(self.X - other.X, self.Y - other.Y, self.Z - other.Z)

    def Multiply(self, value):
        return Vector(self.X * value, self.Y * value, self.Z * value)

    def DotProduct(self, other):
        return self.X * other.X + self.Y * other.Y + self.Z * other.Z

    def Normalize(self):
        return self.Multiply(1 / hypot(self.X, self.Y))

    def DistanceTo(self, other):
        return ((self.X-other.X)**2 + (self.Y-other.Y)**2 + (self.Z-other.Z)**2) ** .5


Vector.Zero = Vector(0, 0)
Vector.BasisZ = Vector(0, 0, 1)


class Line:
    def __init__(self, a, b):
        self.points = [Vector(*a), Vector(*b)]
        self.Direction = (self.points[1] - self.points[0]).Normalize()

    def GetEndPoint(self, index):
        return self.points[index]


class Wall:
    def __init__(self, direction=((0, 0), (10, 0))):
        self.Location = LocationCurve()
        self.Location.Curve = Line(*direction)


class LocationCurve:
    pass


class GenericList:
    def __class_getitem__(cls, unused):
        return list


def load_modules():
    db = types.ModuleType("Autodesk.Revit.DB")
    db.SpatialElementBoundaryLocation = types.SimpleNamespace(Finish=0, Center=1)
    db.IFailuresPreprocessor = object
    db.XYZ, db.Wall, db.Line, db.LocationCurve = Vector, Wall, Line, LocationCurve
    db.ElementId = lambda value: value
    modules = {name: types.ModuleType(name) for name in
               ("Autodesk", "Autodesk.Revit", "System", "System.Collections", "System.Collections.Generic")}
    modules["Autodesk.Revit.DB"] = db
    modules["Autodesk"].Revit = modules["Autodesk.Revit"]
    modules["Autodesk.Revit"].DB = db
    modules["System.Collections.Generic"].List = GenericList
    loaded = []
    with patch.dict(sys.modules, modules):
        for name in ("arqt_geometry", "arqt_floors"):
            spec = importlib.util.spec_from_file_location(name, BUNDLE / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            loaded.append(module)
    return loaded


geometry, floors = load_modules()
geometry._polygon_backend()  # Load the same installed GEOS backend as pyRevit.


class ModuleReloadTests(unittest.TestCase):
    def test_local_source_replaces_stale_cpython_module(self):
        tree = ast.parse((BUNDLE / "script.py").read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "_load_local_module")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "arqt_reload_probe.py").write_text("value = 'current source'\n", encoding="utf-8")
            stale = types.ModuleType("arqt_reload_probe")
            stale.value = "cached old code"
            namespace = {"__file__": str(path / "script.py"), "os": __import__("os"),
                         "sys": sys, "importlib": __import__("importlib.util")}
            exec(compile(ast.Module(body=[function], type_ignores=[]), str(BUNDLE / "script.py"), "exec"), namespace)
            with patch.dict(sys.modules, {"arqt_reload_probe": stale}):
                loaded = namespace["_load_local_module"]("arqt_reload_probe")
                self.assertEqual(loaded.value, "current source")
                self.assertIsNot(loaded, stale)
                self.assertEqual(Path(loaded.__file__).parent, path)
                # Equal-length edits are also read directly, without bytecode.
                (path / "arqt_reload_probe.py").write_text("value = 'updated source'\n", encoding="utf-8")
                loaded = namespace["_load_local_module"]("arqt_reload_probe")
                self.assertEqual(loaded.value, "updated source")


class ConnectedUnionTests(unittest.TestCase):
    @staticmethod
    def solid(name, bounds):
        return types.SimpleNamespace(names={name}, bounds=bounds, Volume=1.0)

    def environment(self, operation):
        return patch.multiple(geometry.DB, create=True,
            BooleanOperationsType=types.SimpleNamespace(Union="Union", Intersect="Intersect", Difference="Difference"),
            BooleanOperationsUtils=types.SimpleNamespace(ExecuteBooleanOperation=operation),
            SolidUtils=types.SimpleNamespace(SplitVolumes=lambda solid: [solid]))

    def merge(self, a, b, operation):
        self.assertGreaterEqual(min(geometry._overlap(a.bounds, b.bounds)), 0)
        return types.SimpleNamespace(names=a.names | b.names, Volume=1,
            bounds=(min(a.bounds[0], b.bounds[0]), min(a.bounds[1], b.bounds[1]),
                    max(a.bounds[2], b.bounds[2]), max(a.bounds[3], b.bounds[3])))

    def test_partition_strip_is_unioned_before_separated_room(self):
        a = self.solid("A", (0, 0, 4.9, 5))
        b = self.solid("B", (5.1, 0, 10, 5))
        strip = self.solid("tabique", (4.9, 0, 5.1, 5))
        operation = Mock(side_effect=self.merge)
        with self.environment(operation), patch.object(geometry, "_bounds", side_effect=lambda s: s.bounds):
            result = geometry.union([a, b, strip], "Modo continuo")
        self.assertEqual(result.names, {"A", "B", "tabique"})
        self.assertIs(operation.call_args_list[0].args[1], strip)
        self.assertIs(operation.call_args_list[1].args[1], b)

    def test_corner_failure_retried_after_valid_connection(self):
        a = self.solid("A", (0, 0, 1, 1))
        b = self.solid("B", (1, 1, 2, 2))
        strip = self.solid("encuentro", (.9, .9, 1.1, 1.1))

        def native(x, y, operation):
            if x.names == {"A"} and y is b:
                raise RuntimeError("Non-manifold corner")
            return self.merge(x, y, operation)

        operation = Mock(side_effect=native)
        with self.environment(operation), patch.object(geometry, "_bounds", side_effect=lambda s: s.bounds):
            result = geometry.union([a, b, strip])
        self.assertEqual(result.names, {"A", "B", "encuentro"})
        self.assertEqual(operation.call_count, 3)

    def test_disconnected_selection_does_not_call_native_union(self):
        operation = Mock()
        with self.environment(operation), patch.object(geometry, "_bounds", side_effect=lambda s: s.bounds):
            with self.assertRaisesRegex(geometry.GeometryError, "regiones desconectadas"):
                geometry.union([self.solid("A", (0, 0, 1, 1)), self.solid("B", (2, 0, 3, 1))])
        operation.assert_not_called()

    def test_touching_bounds_skip_zero_area_intersection_and_difference(self):
        a, b = self.solid("A", (0, 0, 1, 1)), self.solid("B", (1, 0, 2, 1))
        operation = Mock()
        with self.environment(operation), patch.object(geometry, "_bounds", side_effect=lambda s: s.bounds):
            self.assertEqual(geometry.intersection_area(a, b), 0)
            self.assertIs(geometry.difference(a, b), a)
        operation.assert_not_called()

    def test_boolean_failure_keeps_context_and_native_cause(self):
        operation = Mock(side_effect=RuntimeError("Native technical diagnostic"))
        with self.environment(operation), patch.object(geometry, "_bounds", side_effect=lambda s: s.bounds):
            with self.assertRaises(geometry.GeometryError) as result:
                geometry.union([self.solid("A", (0, 0, 1, 1)), self.solid("B", (1, 0, 2, 1))],
                               "Modo continuo · habitación 01 [ID 123]")
        self.assertIn("[ID 123]", str(result.exception))
        self.assertNotIn("Native technical diagnostic", str(result.exception))
        self.assertIn("Native technical diagnostic", str(result.exception.__cause__))


class PlanPolygonTests(unittest.TestCase):
    @staticmethod
    def loop(points, outer=True):
        class Loop(list):
            def IsCounterclockwise(self, axis):
                return outer
        return Loop(Line(a, b) for a, b in zip(points, points[1:] + points[:1]))

    def test_native_hole_is_preserved_in_plan_polygon(self):
        shell = self.loop([(0, 0), (10, 0), (10, 10), (0, 10)])
        hole = self.loop([(3, 3), (3, 7), (7, 7), (7, 3)], False)
        polygon_class, _ = geometry._polygon_backend()
        with patch.object(geometry, "profile", return_value=[shell, hole]):
            polygon = geometry._plan_polygon(object(), (5, 5), polygon_class)
        self.assertEqual(polygon.area, 84)
        self.assertEqual(len(polygon.interiors), 1)

    def test_curves_are_rejected_without_tessellation(self):
        polygon_class, _ = geometry._polygon_backend()
        with patch.object(geometry, "profile", return_value=[[object()]]):
            with self.assertRaisesRegex(geometry.GeometryError, "curvos"):
                geometry._plan_polygon(object(), (0, 0), polygon_class)

    def test_invalid_polygon_is_not_silently_repaired(self):
        shell = self.loop([(0, 0), (2, 2), (0, 2), (2, 0)])
        polygon_class, _ = geometry._polygon_backend()
        with patch.object(geometry, "profile", return_value=[shell]):
            with self.assertRaisesRegex(geometry.GeometryError, "no válido"):
                geometry._plan_polygon(object(), (0, 0), polygon_class)


def segment(room_id, start, end, before=20, after=21):
    return {"room": types.SimpleNamespace(Id=types.SimpleNamespace(Value=room_id)),
            "host": 10, "curve": Line(start, end), "before": before, "after": after}


class RoomCollectionTests(unittest.TestCase):
    def test_collects_rooms_without_using_unsupported_room_class(self):
        class SpatialElement:
            pass

        class Room(SpatialElement):
            category = "rooms"

        class Space(SpatialElement):
            category = "spaces"

        room, space = Room(), Space()

        class Collector:
            def __init__(self, doc):
                self.items = [room, space, object()]

            def OfClass(self, cls):
                if cls is not SpatialElement:
                    raise ValueError("This API-only subtype is not supported by OfClass")
                self.items = [item for item in self.items if isinstance(item, cls)]
                return self

            def OfCategory(self, category):
                self.items = [item for item in self.items if item.category == category]
                return self

            def WhereElementIsNotElementType(self):
                return self

            def __iter__(self):
                return iter(self.items)

        with patch.multiple(geometry.DB, create=True, SpatialElement=SpatialElement,
                            BuiltInCategory=types.SimpleNamespace(OST_Rooms="rooms"),
                            FilteredElementCollector=Collector):
            self.assertEqual(list(geometry.collect_rooms(object())), [room])


class PlanFixtures:
    """Run the production pairing + assembly on full plans with real GEOS."""
    def grid(self, cols=3, rows=3, selected=None, layered=False, angle=0, widths=None):
        from shapely.geometry import box
        from shapely.affinity import rotate
        widths = widths or [4.] * cols
        heights = [4.] * rows
        xs, ys = [0.], [0.]
        for width in widths[:-1]:
            xs.append(xs[-1] + width + .2)
        for height in heights[:-1]:
            ys.append(ys[-1] + height + .2)
        xmax, ymax = xs[-1]+widths[-1], ys[-1]+heights[-1]
        vertical = [(-.2, 0)] + [(xs[i]-.2, xs[i]) for i in range(1, cols)] + [(xmax, xmax+.2)]
        horizontal = [(-.2, 0)] + [(ys[i]-.2, ys[i]) for i in range(1, rows)] + [(ymax, ymax+.2)]
        walls, v_ids, h_ids = {}, [], []
        for index, (lo, hi) in enumerate(vertical):
            a, b = 100+index*2, 101+index*2 if layered and 0 < index < cols else 100+index*2
            v_ids.append((a, b))
            mid = (lo+hi)/2
            walls[a] = box(lo, 0, mid if a != b else hi, ymax)
            if a != b:
                walls[b] = box(mid, 0, hi, ymax)
        for index, (lo, hi) in enumerate(horizontal):
            a, b = 200+index*2, 201+index*2 if layered and 0 < index < rows else 200+index*2
            h_ids.append((a, b))
            mid = (lo+hi)/2
            walls[a] = box(0, lo, xmax, mid if a != b else hi)
            if a != b:
                walls[b] = box(0, mid, xmax, hi)
        transform = lambda polygon: rotate(polygon, angle, origin=(0, 0))
        walls = {key: transform(value) for key, value in walls.items()}
        records, polygons, centers = [], [], []
        for row in range(rows):
            for col in range(cols):
                if selected is not None and (col, row) not in selected:
                    continue
                x, y = xs[col], ys[row]
                from shapely.geometry import Polygon
                polygon = transform(Polygon([(x,y), (x+widths[col],y), (x+widths[col],y+heights[row]),
                                             (x,y+heights[row])]))
                points = list(polygon.exterior.coords)[:-1]
                ids = [h_ids[row][1], v_ids[col+1][0], h_ids[row+1][0], v_ids[col][1]]
                curves = [Line(a, b) for a, b in zip(points, points[1:]+points[:1])]
                room = types.SimpleNamespace(Id=types.SimpleNamespace(Value=row*cols+col+1),
                                             Number=str(row*cols+col+1))
                segments = [{"room":room, "host":ids[i], "curve":curve,
                             "before":ids[i-1], "after":ids[(i+1)%4],
                             "before_curve":curves[i-1], "after_curve":curves[(i+1)%4]}
                            for i, curve in enumerate(curves)]
                records.append({"room":room, "segments":segments})
                polygons.append(polygon)
                centers.append(transform(box(sum(vertical[col])/2, sum(horizontal[row])/2,
                                             sum(vertical[col+1])/2, sum(horizontal[row+1])/2)))
        return records, polygons, centers, walls, transform(box(0, 0, xmax, ymax))


class RobustConnectivityTests(unittest.TestCase):
    def test_revit_tolerance_removes_a_short_corner_bevel(self):
        from shapely.geometry import Polygon
        polygon = Polygon([(0,0),(4,0),(4,3.9996),(3.9996,4),(0,4)])
        cleaned = geometry._revit_plan_polygon(polygon, .001)
        self.assertEqual(len(cleaned.exterior.coords), 5)
        self.assertLessEqual(cleaned.hausdorff_distance(polygon), .001)

    def test_revit_tolerance_discards_only_sub_tolerance_holes(self):
        from shapely.geometry import box
        polygon = box(0,0,10,10).difference(box(1,1,1.0002,1.0002)).difference(box(4,4,5,5))
        cleaned = geometry._revit_plan_polygon(polygon, .001)
        self.assertEqual(len(cleaned.interiors), 1)
        self.assertAlmostEqual(cleaned.area, 99)

    def test_revit_tolerance_keeps_the_porch_reentrant_corner(self):
        from shapely.geometry import box
        polygon = box(0,0,10,10).difference(box(0,0,4,4))
        self.assertTrue(geometry._revit_plan_polygon(polygon,.001).equals(polygon))

    def test_revit_tolerance_discards_an_unrepresentable_long_sliver(self):
        from shapely.geometry import box
        polygon = box(0,0,10,10).difference(box(4,4,4.0002,6))
        cleaned = geometry._revit_plan_polygon(polygon, .001)
        self.assertAlmostEqual(cleaned.area, 100)
        self.assertEqual(len(cleaned.interiors), 0)

    def test_revit_tolerance_preserves_a_representable_long_slot(self):
        from shapely.geometry import box
        polygon = box(0,0,10,10).difference(box(4,4,4.01,6))
        self.assertTrue(geometry._revit_plan_polygon(polygon,.001).equals(polygon))

    def test_reported_00561_mm_sliver_is_removed_at_actual_revit_tolerance(self):
        from shapely.geometry import Polygon, box
        width, tolerance = .0561/304.8, .7804/304.8
        # A triangular sliver must retain three vertices under topology-safe
        # simplification; it needs classification by width before simplification.
        sliver = Polygon([(4,4),(4+width,4),(4,8)])
        polygon = box(0,0,10,10).difference(sliver)
        cleaned = geometry._revit_plan_polygon(polygon, tolerance)
        self.assertEqual(len(cleaned.interiors), 0)
        self.assertAlmostEqual(cleaned.area, 100)

    def test_residual_ring_anchor_short_edge_is_collapsed_locally(self):
        from shapely.geometry import Polygon, box
        polygon = Polygon([(0,0),(.0002,0),(4,0),(4,4),(0,4)])
        # Force the GEOS topology simplifier to retain its anchors, exercising
        # the local minimum-edge repair independently of Douglas-Peucker.
        with patch.object(Polygon, "simplify", lambda self,*args,**kwargs:self):
            cleaned = geometry._revit_plan_polygon(polygon,.001)
        self.assertTrue(cleaned.equals(box(0,0,4,4)))
        coordinates = list(cleaned.exterior.coords)
        self.assertTrue(all(hypot(b[0]-a[0],b[1]-a[1]) >= .001
                            for a,b in zip(coordinates,coordinates[1:])))

    def test_precision_discards_a_microscopic_needle_on_the_perimeter(self):
        from shapely.geometry import box
        room = box(0,0,2,2).union(box(2,1,3,1+geometry.PLAN_GRID/4))
        cleaned = geometry._plan_shape(room)
        self.assertTrue(cleaned.equals(box(0,0,2,2)))

    def test_precision_discards_a_microscopic_hole(self):
        from shapely.geometry import Polygon
        tiny = geometry.PLAN_GRID / 4
        room = Polygon([(0,0),(2,0),(2,2),(0,2)],
                       [[(1,1),(1+tiny,1),(1+tiny,1+tiny),(1,1+tiny)]])
        cleaned = geometry._plan_shape(room)
        self.assertEqual(len(cleaned.interiors), 0)
        self.assertAlmostEqual(cleaned.area, 4)

    def test_precision_preserves_a_real_shaft(self):
        from shapely.geometry import box
        room = box(0,0,10,10).difference(box(4,4,4.5,4.5))
        cleaned = geometry._plan_shape(room)
        self.assertEqual(len(cleaned.interiors), 1)
        self.assertTrue(cleaned.equals(room))

    def test_precision_discards_a_detached_microscopic_fragment(self):
        from shapely.geometry import box
        tiny = geometry.PLAN_GRID / 4
        room = box(0,0,2,2).union(box(3,3,3+tiny,3+tiny))
        cleaned = geometry._plan_shape(room)
        self.assertEqual(cleaned.geom_type, "Polygon")
        self.assertTrue(cleaned.equals(box(0,0,2,2)))

    def test_cleanup_cannot_remove_significant_room_area(self):
        from shapely.geometry import box
        with patch("shapely.set_precision", return_value=box(0,0,1,1)):
            with self.assertRaisesRegex(geometry.GeometryError, "superficie significativa"):
                geometry._plan_shape(box(0,0,2,2))

    def test_roundoff_gap_is_resolved_without_geometric_buffer(self):
        from shapely.geometry import Polygon, box
        from shapely.ops import unary_union
        rooms = [box(0, 0, 4, 4), box(4.2, 0, 8.2, 4)]
        patch = box(4 + 1e-11, 0, 4.2 - 1e-11, 4)
        result, _ = geometry._union_room_passages(rooms, [patch], unary_union)
        self.assertLess(result.symmetric_difference(box(0, 0, 8.2, 4)).area, 1e-8)

    def test_real_gap_is_not_closed_by_numerical_precision(self):
        from shapely.geometry import Polygon, box
        from shapely.ops import unary_union
        # 0.1 mm is a modelling gap, over 3,000 times the precision grid.
        gap = .1 / 304.8
        rooms = [box(0, 0, 4, 4), box(4.2, 0, 8.2, 4)]
        result, _ = geometry._union_room_passages(rooms, [box(4 + gap, 0, 4.2, 4)], unary_union)
        self.assertEqual(result.geom_type, "MultiPolygon")


class DoorTypePassageTests(unittest.TestCase):
    def passage(self, width=.9, missing=False, vertical=False, side=0):
        from shapely.geometry import Polygon
        parameter = types.SimpleNamespace(StorageType="double", HasValue=True, AsDouble=lambda: width)
        symbol = types.SimpleNamespace(LookupParameter=Mock(return_value=None if missing else parameter))
        doc = types.SimpleNamespace(GetElement=Mock(return_value=symbol),
                                    Application=types.SimpleNamespace(ShortCurveTolerance=.001))
        host = Wall(((0,0),(0,10)) if vertical else ((0,0),(10,0)))
        class LocationPoint:
            Point = Vector(0,5) if vertical else Vector(5,0)
        door = types.SimpleNamespace(Host=host, Location=LocationPoint(), GetTypeId=lambda:123,
                                     LookupParameter=Mock(side_effect=AssertionError("Use type, not instance")))
        normal = Vector(-1,0) if vertical else Vector(0,1)
        with patch.multiple(geometry.DB, create=True, LocationPoint=LocationPoint,
                            StorageType=types.SimpleNamespace(Double="double")), \
                patch.object(geometry, "_wall_planes", return_value=(normal,.3,.5)):
            polygon = geometry._door_type_passage(doc, door, (0,0), Polygon, side=side)
        return polygon, symbol, doc

    def test_uses_type_anchura_and_offset_real_finish_planes(self):
        polygon, symbol, doc = self.passage()
        for actual, expected in zip(polygon.bounds, (4.55,.3,5.45,.5)):
            self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(polygon.area, .9*.2)
        symbol.LookupParameter.assert_called_once_with("Anchura")
        doc.GetElement.assert_called_once_with(123)

    def test_vertical_wall_uses_same_width_and_measured_depth(self):
        polygon, _, _ = self.passage(vertical=True)
        for actual, expected in zip(polygon.bounds, (-.5,4.55,-.3,5.45)):
            self.assertAlmostEqual(actual, expected)

    def test_half_passages_share_real_midplane_despite_offset_location_line(self):
        low, _, _ = self.passage(side=-1)
        high, _, _ = self.passage(side=1)
        full, _, _ = self.passage()
        self.assertAlmostEqual(low.area,full.area/2)
        self.assertAlmostEqual(high.area,full.area/2)
        self.assertAlmostEqual(low.bounds[3],.4)
        self.assertAlmostEqual(high.bounds[1],.4)
        self.assertEqual(low.intersection(high).area,0)
        self.assertAlmostEqual(low.intersection(high).length,.9)
        self.assertTrue(low.union(high).equals(full))

    def test_missing_type_anchura_is_reported(self):
        with self.assertRaisesRegex(geometry.GeometryError, "Anchura"):
            self.passage(missing=True)

    def test_zero_width_is_not_replaced_with_a_family_box(self):
        with self.assertRaisesRegex(geometry.GeometryError, "Anchura"):
            self.passage(width=0)


class ContinuousDoorTests(unittest.TestCase):
    def test_missing_room_relations_are_checked_at_real_wall_faces(self):
        records, polygons, door_class = self.fixture()
        rooms = [record["room"] for record in records]
        host = Wall()
        host.Id = types.SimpleNamespace(Value=10)
        for record in records:
            record["segments"] = [{"host":10, "curve":Line((0,0),(10,0)), "before":20, "after":21}]
        class LocationPoint:
            Point = Vector(5,0)
        door = door_class()
        door.Host, door.LevelId, door.Location = host, 1, LocationPoint()
        door.get_FromRoom = door.get_ToRoom = lambda phase: None
        doc = types.SimpleNamespace(GetRoomAtPoint=Mock(side_effect=rooms))
        with patch.object(geometry.DB, "LocationPoint", LocationPoint, create=True), \
                patch.object(geometry, "_wall_planes", return_value=(Vector(0,1),-.1,.1)), \
                patch.object(geometry, "_side_points", return_value=[object(),object()]):
            related = geometry._door_rooms(doc, door, records, types.SimpleNamespace(Id=1))
        self.assertEqual(related, tuple(rooms))
        self.assertEqual(doc.GetRoomAtPoint.call_count, 2)

    def fixture(self):
        from shapely.geometry import box
        rooms = [types.SimpleNamespace(Id=types.SimpleNamespace(Value=i+1), Number=str(i+1),
                                      LevelId=1, BaseOffset=0, Level=types.SimpleNamespace(ProjectElevation=0))
                 for i in range(2)]
        records = [{"room": room, "segments": []} for room in rooms]
        polygons = [box(0,0,4,4), box(4.2,0,8.2,4)]
        class Door:
            Id = types.SimpleNamespace(Value=42)
            DesignOption = None
            Host = None
            def get_FromRoom(self, phase):
                return rooms[0]
            def get_ToRoom(self, phase):
                return rooms[1]
            def GetPhaseStatus(self, phase):
                return "existing"
        return records, polygons, Door

    def read(self, records, polygons, door_class, opening):
        from shapely.geometry import Polygon
        collector = Mock()
        collector.OfCategory.return_value = collector
        collector.WhereElementIsNotElementType.return_value = [door_class()]
        footprint = Mock(return_value=opening)
        with patch.multiple(geometry.DB, create=True, FamilyInstance=door_class,
                            FilteredElementCollector=Mock(return_value=collector),
                            BuiltInCategory=types.SimpleNamespace(OST_Doors=1),
                            ElementOnPhaseStatus=types.SimpleNamespace(New="new", Existing="existing")), \
                patch.object(geometry, "_door_type_passage", footprint):
            result = geometry._continuous_door_patches(
                types.SimpleNamespace(Application=types.SimpleNamespace(ShortCurveTolerance=.001)),
                records, types.SimpleNamespace(Id=1), polygons, (0,0), Polygon)
        return result, footprint

    def test_verified_interior_opening_connects_both_selected_rooms(self):
        from shapely.geometry import box
        records, polygons, door_class = self.fixture()
        (patches, ids, errors), footprint = self.read(records, polygons, door_class, box(4,1,4.2,2))
        self.assertEqual(ids, [42])
        self.assertEqual(errors, [])
        self.assertEqual(len(patches), 1)
        footprint.assert_called_once()

    def test_door_to_excluded_porch_is_not_added(self):
        from shapely.geometry import box
        records, polygons, door_class = self.fixture()
        (patches, ids, errors), footprint = self.read(records[:1], polygons[:1], door_class, box(4,1,4.2,2))
        self.assertEqual((patches, ids, errors), ([], [], []))
        footprint.assert_not_called()

    def test_opening_must_reach_both_room_finish_faces(self):
        from shapely.geometry import box
        records, polygons, door_class = self.fixture()
        (patches, ids, errors), _ = self.read(records, polygons, door_class, box(4,1,4.1,2))
        self.assertEqual((patches, ids), ([], []))
        self.assertIn("Puerta [ID 42]", errors[0])
        self.assertIn("habitación 2", errors[0])

    def test_point_contact_is_not_a_valid_door_passage(self):
        from shapely.geometry import Polygon
        records, polygons, door_class = self.fixture()
        (patches, ids, errors), _ = self.read(records, polygons, door_class,
                                             Polygon([(4,1),(4.1,1),(4.2,2),(4.1,3),(4,3)]))
        self.assertEqual((patches, ids), ([], []))
        self.assertTrue(errors)

    def test_different_room_base_elevations_are_reported(self):
        from shapely.geometry import box
        records, polygons, door_class = self.fixture()
        records[1]["room"].BaseOffset = 1
        (patches, ids, errors), footprint = self.read(records, polygons, door_class, box(4,1,4.2,2))
        self.assertEqual((patches, ids), ([], []))
        self.assertIn("distintas cotas", errors[0])
        footprint.assert_not_called()

    def test_demolished_door_is_not_used(self):
        from shapely.geometry import box
        records, polygons, door_class = self.fixture()
        door_class.GetPhaseStatus = lambda self, phase: "demolished"
        (patches, ids, errors), footprint = self.read(records, polygons, door_class, box(4,1,4.2,2))
        self.assertEqual((patches, ids, errors), ([], [], []))
        footprint.assert_not_called()


class ContinuousPipelineTests(unittest.TestCase):
    def test_independent_rooms_generate_two_floor_profiles(self):
        fixture = PlanFixtures().grid(cols=3,rows=1,selected={(0,0),(2,0)})
        (result, metadata), _ = self.run_pipeline(fixture)
        self.assertEqual(metadata["group_count"],2)
        self.assertEqual(len(metadata["polygons"]),2)
        self.assertEqual(metadata["groups"],[["1"],["3"]])
        self.assertAlmostEqual(result.area,32)

    def test_connected_group_and_isolated_room_both_generate_profiles(self):
        from shapely.geometry import box
        fixture = PlanFixtures().grid(cols=4,rows=1,selected={(0,0),(1,0),(3,0)})
        (result, metadata), _ = self.run_pipeline(fixture,([box(4,1,4.2,2)],[42],[]))
        self.assertEqual(metadata["group_count"],2)
        self.assertEqual(metadata["groups"],[["1","2"],["4"]])
        self.assertAlmostEqual(result.area,48.2)
        self.assertAlmostEqual(metadata["polygons"][0].area,32.2)
        self.assertAlmostEqual(metadata["polygons"][1].area,16)

    def test_rotated_plan_keeps_partition_voids_and_all_door_passages(self):
        from shapely.affinity import rotate
        from shapely.geometry import Point
        fixture = PlanFixtures().grid(layered=True)
        passages, ids, errors = self.grid_passages(fixture)
        records, polygons, centers, walls, envelope = fixture
        angle = 17.329
        polygons = [rotate(shape,angle,origin=(0,0)) for shape in polygons]
        passages = [rotate(shape,angle,origin=(0,0)) for shape in passages]
        (result, metadata), _ = self.run_pipeline((records,polygons,[],{},envelope),(passages,ids,errors))
        self.assertAlmostEqual(result.area,146.4,places=5)
        self.assertFalse(result.contains(rotate(Point(4.1,1),angle,origin=(0,0))))
        self.assertTrue(result.contains(rotate(Point(4.1,2),angle,origin=(0,0))))

    def test_missing_door_passage_is_not_ignored_even_if_rooms_connect_elsewhere(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(geometry.tempfile,"gettempdir",return_value=directory):
            with self.assertRaisesRegex(geometry.GeometryError,"todos los pasos"):
                self.run_pipeline(self.separator_fixture(),([],[],["Puerta [ID 42]: Anchura inválida"]))

    def test_native_profile_keeps_both_exterior_and_wall_hole(self):
        from shapely.geometry import Polygon, box
        shape = box(0,0,10,10).difference(box(4,4,6,6))
        class Loop(list):
            def Append(self, curve):
                self.append(curve)
        with patch.object(geometry.DB,"CurveLoop",Loop,create=True), \
                patch.object(Line,"CreateBound",side_effect=lambda a,b:Line((a.X,a.Y),(b.X,b.Y)),create=True):
            loops = geometry._native_plan_loops(shape,(100,-500),.001)
        self.assertEqual(len(loops), 2)
        rings = [[(curve.GetEndPoint(0).X-100,curve.GetEndPoint(0).Y+500) for curve in loop] for loop in loops]
        reconstructed = Polygon(rings[0],rings[1:])
        self.assertTrue(reconstructed.equals(shape))

    def grid_passages(self, fixture):
        from shapely.geometry import box
        polygons = fixture[1]
        passages = []
        for i, a in enumerate(polygons):
            for b in polygons[i+1:]:
                ax0, ay0, ax1, ay1 = a.bounds
                bx0, by0, bx1, by1 = b.bounds
                if abs(ay0-by0) < 1e-8 and abs(bx0-ax1-.2) < 1e-8:
                    mid = (ay0+ay1)/2
                    passages.append(box(ax1,mid-.5,bx0,mid+.5))
                if abs(ax0-bx0) < 1e-8 and abs(by0-ay1-.2) < 1e-8:
                    mid = (ax0+ax1)/2
                    passages.append(box(mid-.5,ay1,mid+.5,by0))
        return passages, list(range(1000,1000+len(passages))), []

    def separator_fixture(self):
        from shapely.affinity import translate
        records, polygons, centers, walls, expected = PlanFixtures().grid(cols=2, rows=1)
        polygons[1] = translate(polygons[1], xoff=-.2)
        # All the boundary segments in this API-double fixture are separators;
        # neither material thickness nor a door is needed at their common edge.
        for record, polygon in zip(records, polygons):
            points = list(polygon.exterior.coords)[:-1]
            record["segments"] = [dict(segment(element_id, a, b), host=-999)
                                  for a, b in zip(points, points[1:]+points[:1])
                                  for element_id in [geometry.element_id(record["room"])]]
        return records, polygons, polygons[:], {}, polygons[0].union(polygons[1])

    def test_room_separator_disappears_in_one_continuous_floor(self):
        (result, metadata), _ = self.run_pipeline(self.separator_fixture())
        self.assertEqual(result.geom_type, "Polygon")
        self.assertAlmostEqual(result.area, 32)
        self.assertAlmostEqual(metadata["added_area"], 0)
        self.assertEqual(metadata["bridge_count"], 0)

    def test_unselected_room_beyond_separator_stays_excluded(self):
        records, polygons, centers, walls, expected = self.separator_fixture()
        (result, metadata), _ = self.run_pipeline((records[:1],polygons[:1],centers[:1],{},polygons[0]))
        self.assertTrue(result.equals(polygons[0]))
        self.assertEqual(result.intersection(polygons[1]).area, 0)

    def run_pipeline(self, fixture, door_result=([], [], [])):
        from shapely.affinity import translate
        records, polygons, centers, walls, expected = fixture
        for record, polygon in zip(records, polygons):
            record.update(loops=polygon, solid=polygon)
        doc = types.SimpleNamespace(Application=types.SimpleNamespace(ShortCurveTolerance=.001))
        phase = types.SimpleNamespace(Id=7)
        def local(shape, origin):
            return translate(shape, xoff=-origin[0], yoff=-origin[1])
        def door_patches(doc, records, phase, polygons, origin, polygon_class):
            shapes, ids, errors = door_result
            return [local(shape, origin) for shape in shapes], ids, errors
        with patch.object(geometry, "_bounds", side_effect=lambda shape: shape.bounds), \
                patch.object(geometry, "_loops_polygon", side_effect=lambda loops, origin, *args: local(loops, origin)), \
                patch.object(geometry, "boundary_data", side_effect=AssertionError("No center boundary extraction")), \
                patch.object(geometry, "_continuous_door_patches", side_effect=door_patches) as portals, \
                patch.object(geometry, "_native_plan_loops", side_effect=lambda shape, *args: shape), \
                patch.object(geometry, "prism", side_effect=lambda loops: loops), \
                patch.object(geometry, "_plan_polygon", side_effect=lambda solid, *args: solid):
            solids, metadata = geometry.continuous(doc, records, phase)
            from shapely.ops import unary_union
            solid = unary_union(solids)
        origin = metadata["origin"]
        return (translate(solid, xoff=origin[0], yoff=origin[1]), metadata), portals

    def test_eight_rooms_excluding_porch_keep_voids_under_partitions(self):
        from shapely.geometry import box, Point
        selected = {(x,y) for x in range(3) for y in range(3)} - {(0,0)}
        fixture = PlanFixtures().grid(selected=selected, layered=True)
        (result, metadata), portals = self.run_pipeline(fixture, self.grid_passages(fixture))
        self.assertAlmostEqual(result.area, 130)
        self.assertEqual(result.intersection(box(0,0,4,4)).area, 0)
        self.assertFalse(result.contains(Point(4.1,5)))
        self.assertAlmostEqual(metadata["added_area"], 2)
        portals.assert_called_once()

    def test_room_shapes_are_joined_by_door_passages(self):
        from shapely.geometry import box
        fixture = PlanFixtures().grid(cols=2, rows=1)
        records, polygons, centers, walls, expected = fixture
        # No usable wall segments: the two room shapes alone are disconnected.
        for record in records:
            record["segments"] = []
        (result, metadata), portals = self.run_pipeline(fixture, ([box(4,1,4.2,2)], [42], []))
        self.assertEqual(result.geom_type, "Polygon")
        self.assertEqual(metadata["verified_doors"], [42])
        portals.assert_called_once()

    def test_rooms_and_door_union_does_not_fill_partition_junction_holes(self):
        from shapely.geometry import Point
        fixture = PlanFixtures().grid(layered=True)
        (result, metadata), portals = self.run_pipeline(fixture, self.grid_passages(fixture))
        self.assertAlmostEqual(result.area, 146.4)
        self.assertFalse(result.contains(Point(4.1,4.1)))
        self.assertFalse(result.contains(Point(4.1,1)))
        self.assertTrue(result.contains(Point(4.1,2)))
        self.assertTrue(result.interiors)
        portals.assert_called_once()

    def test_point_contact_diagnostic_explains_missing_passage_width(self):
        from shapely.geometry import box
        from shapely.ops import unary_union
        polygons = [box(0,0,1,1), box(1,1,2,2)]
        records = [{"room":types.SimpleNamespace(Id=types.SimpleNamespace(Value=i+1), Number=str(i+1))}
                   for i in range(2)]
        message = geometry._disconnection_report(unary_union(polygons), records, polygons)
        self.assertIn("contacto puntual", message)
        self.assertIn("sin un paso con anchura", message)

    def test_unresolved_failure_names_rooms_measures_gap_and_saves_replayable_data(self):
        from shapely.geometry import shape
        fixture = PlanFixtures().grid(selected={(0,0),(2,2)})
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(geometry.tempfile, "gettempdir", return_value=directory):
            with self.assertRaisesRegex(geometry.GeometryError, "Grupo 1") as failure:
                self.run_pipeline(fixture, ([], [], ["Puerta [ID 42]: hueco no verificable"]))
            message = str(failure.exception)
            self.assertIn("Separación mínima", message)
            self.assertIn("[ID 1]", message)
            self.assertIn("[ID 9]", message)
            self.assertIn("Puerta [ID 42]", message)
            files = list(Path(directory).rglob("continuo-*.json"))
            self.assertEqual(len(files), 1)
            data = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(shape(data["result"]).geom_type, "MultiPolygon")
            self.assertEqual(len(data["rooms"]), 2)
            self.assertEqual(len(data["rooms"][0]["segments"]), 4)
            self.assertEqual(data["units"], "internal feet")


class WallFacesTests(unittest.TestCase):
    def planes(self, normals):
        class Face:
            def __init__(self, coordinate, normal):
                self.Origin, self.FaceNormal = Vector(coordinate, 0), normal
        faces = [Face(-.06, normals[0]), Face(.04, normals[1])]
        wall = types.SimpleNamespace(GetGeometryObjectFromReference=lambda index: faces[index])
        values = dict(PlanarFace=Face, ShellLayerType=types.SimpleNamespace(Exterior=0, Interior=1),
                      HostObjectUtils=types.SimpleNamespace(GetSideFaces=lambda wall, side: [side]))
        with patch.multiple(geometry.DB, create=True, **values):
            return geometry._wall_planes(None, wall, Vector(7, 0))

    def test_center_comes_from_real_faces_not_location_line(self):
        normal, low, high = self.planes([Vector(1, 0), Vector(-1, 0)])
        self.assertAlmostEqual(high - low, .1)
        self.assertAlmostEqual((low + high) / 2, -.01)

    def test_nonparallel_faces_are_rejected(self):
        with self.assertRaisesRegex(geometry.GeometryError, "no son paralelas"):
            self.planes([Vector(1, 0), Vector(0, 1)])

    def test_slanted_faces_are_rejected(self):
        with self.assertRaisesRegex(geometry.GeometryError, "inclinadas"):
            self.planes([Vector(1, 0, .1), Vector(-1, 0)])


class PerRoomPassageTests(unittest.TestCase):
    def calculate(self, selected=(0,1), already_enters=False, exterior=False, reversed_rooms=False, failure=False,
                  only_to=False):
        from shapely.geometry import box
        from shapely.affinity import translate
        rooms = [types.SimpleNamespace(Id=types.SimpleNamespace(Value=i+1),Number=str(i+1),
                                      BaseOffset=0,Level=types.SimpleNamespace(ProjectElevation=0)) for i in range(2)]
        rooms[0].IsPointInRoom = lambda point:point.X < 4
        rooms[1].IsPointInRoom = lambda point:point.X > 4.2
        shapes = [box(0,0,4,4),box(4.2,0,8.2,4)]
        if already_enters:
            shapes[0] = shapes[0].union(box(4,1,4.2,2))
        records = []
        for index in selected:
            coordinates = list(shapes[index].exterior.coords)[:-1]
            curves = [Line(a,b) for a,b in zip(coordinates,coordinates[1:]+coordinates[:1])]
            records.append({"room":rooms[index],"solid":shapes[index],"loops":[curves],"segments":[]})
        origin = (0,0)
        class Door:
            Id = types.SimpleNamespace(Value=186796)
            DesignOption = None
            Host = None
            Location = types.SimpleNamespace(Point=Vector(4.1,1.5))
            def GetPhaseStatus(self,phase):
                return "existing"
            def get_FromRoom(self,phase):
                return None if only_to else (rooms[1] if reversed_rooms else rooms[0])
            def get_ToRoom(self,phase):
                return None if exterior else (rooms[0] if reversed_rooms else rooms[1])
        collector = Mock()
        collector.OfCategory.return_value = collector
        collector.WhereElementIsNotElementType.return_value = [Door()]
        def passage(doc,door,origin,polygon_class,side=0):
            if failure:
                raise geometry.GeometryError("Anchura inválida")
            shape = box(4,1,4.1,2) if side < 0 else box(4.1,1,4.2,2)
            return translate(shape,xoff=-origin[0],yoff=-origin[1])
        with patch.multiple(geometry.DB,create=True,FamilyInstance=Door,
                            FilteredElementCollector=Mock(return_value=collector),
                            BuiltInCategory=types.SimpleNamespace(OST_Doors=1),
                            ElementOnPhaseStatus=types.SimpleNamespace(New="new",Existing="existing")), \
                patch.object(geometry,"_bounds",side_effect=lambda solid:solid.bounds), \
                patch.object(geometry,"_wall_planes",return_value=(Vector(1,0),4,4.2)), \
                patch.object(geometry,"_side_points",return_value=[Vector(3.99,1.5),Vector(4.21,1.5)]), \
                patch.object(geometry,"_door_type_passage",side_effect=passage), \
                patch.object(geometry,"boolean",side_effect=AssertionError("No native booleans for straight room plans")), \
                patch.object(geometry,"_native_plan_loops",side_effect=lambda shape,*args:shape), \
                patch.object(geometry,"prism",side_effect=lambda shape:translate(shape,xoff=origin[0],yoff=origin[1])), \
                patch.object(geometry,"_plan_polygon",side_effect=lambda solid,origin,*args:
                             translate(solid,xoff=-origin[0],yoff=-origin[1])):
            return geometry.add_doors(types.SimpleNamespace(Application=types.SimpleNamespace(ShortCurveTolerance=.001)),
                                      records,types.SimpleNamespace(Id=1))

    def test_each_room_receives_half_and_both_share_one_meeting_line(self):
        from shapely.geometry import box
        a,b = self.calculate()
        self.assertAlmostEqual(a.area,16.1)
        self.assertAlmostEqual(b.area,16.1)
        self.assertEqual(a.intersection(b).area,0)
        self.assertAlmostEqual(a.intersection(b).length,1)
        self.assertEqual(box(4,1,4.2,2).difference(a.union(b)).area,0)

    def test_selecting_only_one_room_still_stops_at_the_common_center(self):
        a, = self.calculate(selected=(0,))
        b, = self.calculate(selected=(1,))
        self.assertAlmostEqual(a.bounds[2],4.1)
        self.assertAlmostEqual(b.bounds[0],4.1)
        self.assertEqual(a.intersection(b).area,0)
        self.assertAlmostEqual(a.intersection(b).length,1)

    def test_original_boundary_entering_other_half_is_trimmed(self):
        a,b = self.calculate(already_enters=True)
        self.assertAlmostEqual(a.area,16.1)
        self.assertEqual(a.intersection(b).area,0)

    def test_from_to_order_does_not_change_the_geometry(self):
        normal = self.calculate()
        reversed_result = self.calculate(reversed_rooms=True)
        self.assertTrue(all(a.equals(b) for a,b in zip(normal,reversed_result)))

    def test_exterior_door_extends_existing_room_to_midpoint(self):
        from shapely.geometry import box
        a, = self.calculate(selected=(0,),exterior=True)
        self.assertTrue(a.equals(box(0,0,4,4).union(box(4,1,4.1,2))))
        self.assertAlmostEqual(a.area,16.1)

    def test_exterior_door_with_only_to_room_uses_its_own_half(self):
        from shapely.geometry import box
        b, = self.calculate(selected=(1,),only_to=True)
        self.assertTrue(b.equals(box(4.2,0,8.2,4).union(box(4.1,1,4.2,2))))
        self.assertAlmostEqual(b.area,16.1)

    def test_exterior_room_already_entering_whole_opening_is_trimmed_to_half(self):
        a, = self.calculate(selected=(0,),exterior=True,already_enters=True)
        self.assertAlmostEqual(a.area,16.1)
        self.assertAlmostEqual(a.bounds[2],4.1)

    def test_invalid_type_width_identifies_the_door(self):
        with self.assertRaisesRegex(geometry.GeometryError,r"Puerta \[ID 186796\].*Anchura"):
            self.calculate(failure=True)


class PreparedGroupsTests(unittest.TestCase):
    def test_prepare_keeps_each_floor_plan_separate_for_creation_and_duplicate_checks(self):
        from shapely.geometry import box
        from shapely.ops import unary_union
        plans = [box(0,0,4,4),box(8,0,10,2)]
        solids = [types.SimpleNamespace(Volume=16),types.SimpleNamespace(Volume=4)]
        rooms = [types.SimpleNamespace(Id=types.SimpleNamespace(Value=i+1),Number=str(i+1)) for i in range(2)]
        metadata = {"polygon":unary_union(plans),"polygons":plans,"origin":(0,0),
                    "group_count":2,"groups":[["1"],["2"]]}
        with patch.object(geometry,"boundary_data",side_effect=lambda room:{"room":room}), \
                patch.object(geometry,"continuous",return_value=(solids,metadata)), \
                patch.object(geometry,"collect_rooms",return_value=[]), \
                patch.object(geometry,"profile",side_effect=lambda solid,context:[solid]):
            prepared = geometry.prepare(object(),rooms,"continuous",types.SimpleNamespace(Id=1))
        self.assertEqual(len(prepared),2)
        for index,item in enumerate(prepared):
            self.assertIs(item["solid"],solids[index])
            self.assertTrue(item["plan"].equals(plans[index]))
            self.assertNotIn("polygons",item["diagnostics"])
        self.assertNotEqual(prepared[0]["label"],prepared[1]["label"])


class AtomicCreationTests(unittest.TestCase):
    def run_creation(self, fail_at=None, reject_commit=False, bad_height=False, offset_mm=120):
        status = types.SimpleNamespace(Started=1, Committed=2, RolledBack=3)
        doc = types.SimpleNamespace(elements=["existing"], Regenerate=lambda: None)
        level = types.SimpleNamespace(Id=100, ProjectElevation=10)
        floor_type = types.SimpleNamespace(Id=200)
        transactions = []

        class Transaction:
            def __init__(self, document, name):
                self.state, self.before = 0, None
                self.rolled_back = False
                transactions.append(self)

            def Start(self):
                self.before = list(doc.elements)
                self.state = status.Started
                return self.state

            def GetStatus(self):
                return self.state

            def RollBack(self):
                doc.elements[:] = self.before
                self.state = status.RolledBack
                self.rolled_back = True

            def Commit(self):
                if reject_commit:
                    self.RollBack()
                else:
                    self.state = status.Committed
                return self.state

            def Assimilate(self):
                self.state = status.Committed
                return self.state

            def GetFailureHandlingOptions(self):
                return Mock()

            def SetFailureHandlingOptions(self, options):
                pass

            def Dispose(self):
                pass

        class Face:
            FaceNormal = Vector(0, 0, 1)

            def __init__(self, z):
                self.Origin = Vector(0, 0, z)

        class Floor:
            calls = 0

            @staticmethod
            def Create(document, loops, type_id, level_id):
                Floor.calls += 1
                if Floor.calls == fail_at:
                    raise RuntimeError("Fallo geométrico inyectado")
                floor = Floor()
                floor.Id = types.SimpleNamespace(Value=Floor.calls)
                floor.LevelId, floor.offset = level_id, None
                doc.elements.append(floor)
                return floor

            def get_Parameter(self, parameter):
                if parameter == "offset":
                    return types.SimpleNamespace(IsReadOnly=False, Set=self.set_offset, AsDouble=lambda: self.offset)
                return types.SimpleNamespace(IsReadOnly=False, Set=lambda value: True)

            def set_offset(self, value):
                self.offset = value
                return True

            def GetTypeId(self):
                return 200

            def GetGeometryObjectFromReference(self, reference):
                return Face(level.ProjectElevation + self.offset + (1 if bad_height else 0))

        db_values = dict(TransactionGroup=Transaction, Transaction=Transaction, TransactionStatus=status,
                         Floor=Floor, PlanarFace=Face,
                         BuiltInParameter=types.SimpleNamespace(FLOOR_HEIGHTABOVELEVEL_PARAM="offset",
                                                               FLOOR_PARAM_IS_STRUCTURAL="structural"),
                         BoundaryValidation=types.SimpleNamespace(IsValidHorizontalBoundary=lambda value: True),
                         HostObjectUtils=types.SimpleNamespace(GetTopFaces=lambda floor: [1]))
        with patch.multiple(floors.DB, create=True, **db_values), \
                patch.object(floors, "validate_settings", return_value=offset_mm / 304.8), \
                patch.object(floors, "flatten_loop", side_effect=lambda loop, z: loop), \
                patch.object(floors, "loops_list", side_effect=list), \
                patch.object(floors, "FailureCollector", return_value=types.SimpleNamespace(warnings=[], errors=[])):
            try:
                result = floors.create(doc, [{"label": "A", "loops": [1]}, {"label": "B", "loops": [2]}],
                                       floor_type, level, offset_mm, Mock(spec=logging.Logger))
                return doc, transactions, result
            except ValueError:
                self.assertEqual(doc.elements, ["existing"])
                self.assertTrue(any(transaction.rolled_back for transaction in transactions))
                raise

    def test_second_floor_failure_rolls_back_first_floor(self):
        with self.assertRaisesRegex(ValueError, "No se ha creado ningún suelo"):
            self.run_creation(fail_at=2)

    def test_rejected_commit_rolls_back_group(self):
        with self.assertRaisesRegex(ValueError, "Revit ha rechazado"):
            self.run_creation(reject_commit=True)

    def test_wrong_top_elevation_rolls_back(self):
        with self.assertRaisesRegex(ValueError, "cota superior"):
            self.run_creation(bad_height=True)

    def test_positive_and_negative_offsets_applied_once(self):
        for offset in (120, -120, 0, 12.5):
            with self.subTest(offset_mm=offset):
                doc, _, (created, warnings) = self.run_creation(offset_mm=offset)
                self.assertEqual(len(created), 2)
                self.assertEqual(doc.elements[0], "existing")
                self.assertAlmostEqual(created[0].offset, offset / 304.8)
                self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
