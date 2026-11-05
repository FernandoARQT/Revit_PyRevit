# -*- coding: utf-8 -*-
"""Plan geometry. Native Revit solids only; no document mutations or tessellation.

All calculation solids have their base at Z=0 and height 1 internal foot.
Their volume therefore equals the plan area in internal square feet.
"""
import itertools
import Autodesk.Revit.DB as DB
from System.Collections.Generic import List


class GeometryError(ValueError):
    pass


EPS = 1e-7
AREA_EPS = 1e-7


def element_id(element):
    return int(element.Id.Value)


def room_label(room):
    name = room.get_Parameter(DB.BuiltInParameter.ROOM_NAME).AsString() or "Habitación"
    return "{} · {} [ID {}]".format(room.Number, name, element_id(room))


def loops_list(loops):
    result = List[DB.CurveLoop]()
    for loop in loops:
        result.Add(loop)
    return result


def flatten_loop(loop, z=0.0):
    curves = list(loop)
    dz = z - curves[0].GetEndPoint(0).Z
    transform = DB.Transform.CreateTranslation(DB.XYZ(0, 0, dz))
    return DB.CurveLoop.CreateViaTransform(loop, transform)


def prism(loops):
    loops = loops_list(flatten_loop(loop) for loop in loops)
    if not DB.BoundaryValidation.IsValidHorizontalBoundary(loops):
        raise GeometryError("Contorno abierto, no horizontal o con intersecciones.")
    solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry(loops, DB.XYZ.BasisZ, 1.0)
    if solid.Volume <= AREA_EPS:
        raise GeometryError("El contorno no tiene superficie positiva.")
    return solid


def boolean(a, b, operation):
    try:
        return DB.BooleanOperationsUtils.ExecuteBooleanOperation(a, b, operation)
    except Exception as exc:
        raise GeometryError("Revit no puede resolver una operación geométrica: {}".format(exc)) from exc


def intersection(a, b):
    return boolean(a, b, DB.BooleanOperationsType.Intersect)


def difference(a, b):
    return boolean(a, b, DB.BooleanOperationsType.Difference)


def union(solids):
    solids = list(solids)
    if not solids:
        raise GeometryError("No hay superficies para unir.")
    result = solids[0]
    for solid in solids[1:]:
        result = boolean(result, solid, DB.BooleanOperationsType.Union)
    return result


def profile(solid, context="Selección"):
    volumes = list(DB.SolidUtils.SplitVolumes(solid))
    if len(volumes) != 1:
        raise GeometryError("{}: hay regiones desconectadas. Selecciona un conjunto contiguo.".format(context))
    faces = [face for face in solid.Faces if isinstance(face, DB.PlanarFace)
             and face.FaceNormal.Z > 1.0 - EPS]
    if len(faces) != 1:
        raise GeometryError("{}: la superficie no forma un único perfil horizontal.".format(context))
    loops = [flatten_loop(loop) for loop in faces[0].GetEdgesAsCurveLoops()]
    # BoundaryValidation checks closure, horizontality and intersections. Extrusion
    # additionally checks that the interior loops form a valid positive region.
    prism(loops)
    return loops


def boundary_data(room, location=DB.SpatialElementBoundaryLocation.Finish):
    options = DB.SpatialElementBoundaryOptions()
    options.SpatialElementBoundaryLocation = location
    try:
        boundaries = room.GetBoundarySegments(options)
        if not boundaries:
            raise GeometryError("{}: habitación sin delimitar.".format(room_label(room)))
        loops, segments = [], []
        for boundary in boundaries:
            items = list(boundary)
            loop = DB.CurveLoop()
            for index, segment in enumerate(items):
                curve = segment.GetCurve()
                loop.Append(curve)
                segments.append({"room": room, "host": int(segment.ElementId.Value),
                                 "curve": curve,
                                 "before": int(items[index - 1].ElementId.Value),
                                 "after": int(items[(index + 1) % len(items)].ElementId.Value)})
            loops.append(flatten_loop(loop))
        return {"room": room, "loops": loops, "segments": segments, "solid": prism(loops)}
    finally:
        options.Dispose()


def _polygon(points):
    loop = DB.CurveLoop()
    for a, b in zip(points, points[1:] + points[:1]):
        if a.DistanceTo(b) < EPS:
            raise GeometryError("El encuentro entre límites contiene una arista degenerada.")
        loop.Append(DB.Line.CreateBound(a, b))
    return prism([loop])


def _line_span(segment, axis, origin):
    curve = segment["curve"]
    if not isinstance(curve, DB.Line):
        raise GeometryError("El modo continuo requiere tabiques compartidos rectos. "
                            "Los perímetros curvos se conservan en los demás límites.")
    points = [DB.XYZ(p.X, p.Y, 0) for p in (curve.GetEndPoint(0), curve.GetEndPoint(1))]
    ends = [(axis.DotProduct(p - origin), p, neighbor)
            for p, neighbor in zip(points, (segment["before"], segment["after"]))]
    ends.sort(key=lambda item: item[0])
    return ends


def _at(ends, station):
    ratio = (station - ends[0][0]) / (ends[1][0] - ends[0][0])
    return ends[0][1] + (ends[1][1] - ends[0][1]).Multiply(ratio)


def partition_patches(doc, records):
    """Bridge opposite finish faces, limited to their shared room spans.

    Matching adjacent hosts retain the exact miter endpoints (also at diagonal
    exterior walls). At T joins, only the overlapping span belongs to a pair.
    Small enclosed junctions are resolved separately using native center loops.
    """
    by_host = {}
    for record in records:
        for segment in record["segments"]:
            by_host.setdefault(segment["host"], []).append(segment)
    patches, extended = [], []
    for host_id, segments in by_host.items():
        if len({element_id(s["room"]) for s in segments}) < 2:
            continue
        host = doc.GetElement(DB.ElementId(host_id))
        # Shared separation lines already touch and require no infill.
        if not isinstance(host, DB.Wall):
            continue
        for a, b in itertools.combinations(segments, 2):
            if a["room"].Id == b["room"].Id:
                continue
            if not isinstance(host.Location, DB.LocationCurve) or not isinstance(host.Location.Curve, DB.Line):
                raise GeometryError("Tabique compartido curvo o complejo [ID {}]. "
                                    "Utiliza el modo por habitación.".format(host_id))
            axis = host.Location.Curve.Direction
            if abs(axis.Z) > EPS:
                raise GeometryError("Tabique no horizontal en planta [ID {}].".format(host_id))
            origin = DB.XYZ.Zero
            x, y = _line_span(a, axis, origin), _line_span(b, axis, origin)
            lo, hi = max(x[0][0], y[0][0]), min(x[1][0], y[1][0])
            if hi - lo <= doc.Application.ShortCurveTolerance:
                continue
            normal = DB.XYZ(-axis.Y, axis.X, 0)
            width = abs(normal.DotProduct(x[0][1] - y[0][1]))
            if width <= EPS:
                continue
            if abs(abs((x[1][1] - x[0][1]).Normalize().DotProduct(axis)) - 1) > EPS or \
                    abs(abs((y[1][1] - y[0][1]).Normalize().DotProduct(axis)) - 1) > EPS:
                raise GeometryError("Caras de tabique no paralelas [ID {}].".format(host_id))
            p0, p1, q0, q1 = _at(x, lo), _at(x, hi), _at(y, lo), _at(y, hi)
            if x[0][2] == y[0][2] and x[0][2] >= 0:
                p0, q0 = x[0][1], y[0][1]
            if x[1][2] == y[1][2] and x[1][2] >= 0:
                p1, q1 = x[1][1], y[1][1]
            patches.append(_polygon([p0, p1, q1, q0]))
            # Used exclusively to recognize enclosed wall-junction holes, never
            # as an expansion of the exterior perimeter.
            extended.append(_polygon([_at(x, lo - width), _at(x, hi + width),
                                      _at(y, hi + width), _at(y, lo - width)]))
    return patches, extended


def continuous(doc, records):
    patches, extended = partition_patches(doc, records)
    solid = union([record["solid"] for record in records] + patches)
    loops = profile(solid, "Modo continuo")
    holes = [loop for loop in loops if not loop.IsCounterclockwise(DB.XYZ.BasisZ)]
    if holes and extended:
        # A T/cross junction may leave a small enclosed patch between the strips.
        # Fill only if Revit's *selected room center footprints* cover all of it
        # AND the extended shared strips cover all of it. Courtyards and spaces
        # outside the selection remain holes. Original room holes are protected.
        centers = union(boundary_data(r["room"], DB.SpatialElementBoundaryLocation.Center)["solid"]
                        for r in records)
        candidates = union(extended)
        original_holes = [prism([loop]) for record in records for loop in record["loops"]
                          if not loop.IsCounterclockwise(DB.XYZ.BasisZ)]
        for loop in holes:
            hole = prism([loop])
            if any(intersection(hole, keep).Volume > AREA_EPS for keep in original_holes):
                continue
            if difference(hole, centers).Volume <= AREA_EPS and \
                    difference(hole, candidates).Volume <= AREA_EPS:
                solid = union([solid, hole])
    profile(solid, "Modo continuo")
    return solid


def _wall_planes(doc, wall, point):
    """Actual finish planes, not Wall.Width or the wall location line."""
    sides = []
    for side in (DB.ShellLayerType.Exterior, DB.ShellLayerType.Interior):
        faces = [wall.GetGeometryObjectFromReference(ref)
                 for ref in DB.HostObjectUtils.GetSideFaces(wall, side)]
        if not faces or any(not isinstance(face, DB.PlanarFace) or abs(face.FaceNormal.Z) > EPS
                            for face in faces):
            raise GeometryError("El muro de la puerta tiene caras curvas, inclinadas o complejas.")
        face = min(faces, key=lambda f: abs(f.FaceNormal.DotProduct(point - f.Origin)))
        sides.append(face)
    normal = sides[0].FaceNormal
    if abs(abs(normal.DotProduct(sides[1].FaceNormal)) - 1) > EPS:
        raise GeometryError("Las caras del muro no son paralelas; no se puede fijar un centro fiable.")
    values = sorted(normal.DotProduct(face.Origin) for face in sides)
    if values[1] - values[0] <= EPS:
        raise GeometryError("No se ha podido medir el espesor real del muro.")
    return normal, values[0], values[1]


def door_footprint(doc, door, source_z):
    """Real IFC cut, intersected at the room's source floor elevation.

    Python.NET exposes the native out XYZ as the second item of the return tuple.
    The cut is extruded both ways then clipped to the actual wall finish planes.
    Both adjacent rooms use this SAME cached solid and median plane.
    """
    from Autodesk.Revit.DB.IFC import ExporterIFCUtils
    wall = door.Host
    if not isinstance(wall, DB.Wall) or not isinstance(door.Location, DB.LocationPoint):
        raise GeometryError("La puerta no está alojada en un muro local reconocible.")
    normal, low, high = _wall_planes(doc, wall, door.Location.Point)
    cut, direction = ExporterIFCUtils.GetInstanceCutoutFromWall(doc, wall, door, DB.XYZ.Zero)
    if cut is None or direction is None or abs(direction.Z) > EPS:
        raise GeometryError("Revit no proporciona un hueco de puerta vertical fiable.")
    direction = direction.Normalize()
    if abs(direction.DotProduct(normal)) < 0.9:
        raise GeometryError("El corte de puerta es oblicuo respecto al muro.")
    cut_points = [c.GetEndPoint(i) for c in cut for i in (0, 1)]
    reach = max(abs(normal.DotProduct(p) - bound) for p in cut_points for bound in (low, high))
    reach = (reach + high - low + 1.0) / abs(direction.DotProduct(normal))
    shifted = DB.CurveLoop.CreateViaTransform(cut, DB.Transform.CreateTranslation(direction.Multiply(-reach)))
    solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry(loops_list([shifted]), direction, 2 * reach)
    solid = DB.BooleanOperationsUtils.CutWithHalfSpace(solid,
                DB.Plane.CreateByNormalAndOrigin(normal, normal.Multiply(low)))
    solid = DB.BooleanOperationsUtils.CutWithHalfSpace(solid,
                DB.Plane.CreateByNormalAndOrigin(-normal, normal.Multiply(high)))
    # Sample just above the finish plane to avoid coincident sill faces. A raised
    # or sloped sill that changes through this 1 mm slice is rejected.
    step = DB.UnitUtils.ConvertToInternalUnits(1.0, DB.UnitTypeId.Millimeters)
    solid = DB.BooleanOperationsUtils.CutWithHalfSpace(solid,
                DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, DB.XYZ(0, 0, source_z + step)))
    solid = DB.BooleanOperationsUtils.CutWithHalfSpace(solid,
                DB.Plane.CreateByNormalAndOrigin(-DB.XYZ.BasisZ, DB.XYZ(0, 0, source_z + 2 * step)))
    if solid.Volume <= AREA_EPS:
        raise GeometryError("El hueco no alcanza la cota de pavimento de la habitación (umbral elevado).")
    top_faces = [f for f in solid.Faces if isinstance(f, DB.PlanarFace) and f.FaceNormal.Z > 1 - EPS]
    bottom_faces = [f for f in solid.Faces if isinstance(f, DB.PlanarFace) and f.FaceNormal.Z < -1 + EPS]
    if len(top_faces) != 1 or len(bottom_faces) != 1:
        raise GeometryError("El hueco tiene varias regiones o no produce un umbral horizontal.")
    top = prism(top_faces[0].GetEdgesAsCurveLoops())
    bottom = prism(bottom_faces[0].GetEdgesAsCurveLoops())
    if difference(top, bottom).Volume + difference(bottom, top).Volume > AREA_EPS:
        raise GeometryError("El hueco cambia junto al umbral; necesita revisión manual.")
    return top, normal, (low + high) / 2.0


def add_doors(doc, records, phase):
    by_id = {element_id(r["room"]): r for r in records}
    solids = {key: record["solid"] for key, record in by_id.items()}
    doors = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Doors).WhereElementIsNotElementType()
    for door in doors:
        if not isinstance(door, DB.FamilyInstance):
            continue
        try:
            status = door.GetPhaseStatus(phase.Id)
            if status not in (DB.ElementOnPhaseStatus.New, DB.ElementOnPhaseStatus.Existing):
                continue
            a, b = door.get_FromRoom(phase), door.get_ToRoom(phase)
            relevant = [room for room in (a, b) if room is not None and element_id(room) in by_id]
            if not relevant:
                continue
            # No room on the opposite side: exterior opening, keep finish limit.
            if a is None or b is None or a.Id == b.Id:
                continue
            elevations = [r.Level.ProjectElevation + r.BaseOffset for r in (a, b)]
            if abs(elevations[0] - elevations[1]) > EPS:
                raise GeometryError("Las habitaciones a ambos lados tienen distintas cotas de base.")
            footprint, normal, median = door_footprint(doc, door, elevations[0])
            signs = []
            for room in (a, b):
                record = by_id.get(element_id(room)) or boundary_data(room)
                host_segments = [s for s in record["segments"] if s["host"] == element_id(door.Host)]
                if not host_segments:
                    raise GeometryError("La habitación relacionada no tiene el muro como límite local.")
                point = door.Location.Point
                segment = min(host_segments, key=lambda s: s["curve"].Distance(point))
                side_point = segment["curve"].Evaluate(0.5, True)
                signed = normal.DotProduct(side_point) - median
                if abs(signed) <= EPS:
                    raise GeometryError("El límite de habitación coincide con el centro del tabique.")
                signs.append(1 if signed > 0 else -1)
            if signs[0] == signs[1]:
                raise GeometryError("FromRoom y ToRoom no están a lados opuestos del tabique.")
            for room, sign in zip((a, b), signs):
                key = element_id(room)
                if key not in by_id:
                    continue
                half = DB.BooleanOperationsUtils.CutWithHalfSpace(footprint,
                           DB.Plane.CreateByNormalAndOrigin(normal.Multiply(sign), normal.Multiply(median)))
                # The half opening must meet this exact room at its finish face.
                solids[key] = union([solids[key], half])
                profile(solids[key], room_label(room))
        except Exception as exc:
            raise GeometryError("Puerta [ID {}]: {}".format(element_id(door), exc)) from exc
    return [solids[element_id(record["room"])] for record in records]


def prepare(doc, rooms, mode, phase):
    records = []
    for room in rooms:
        try:
            records.append(boundary_data(room))
        except Exception as exc:
            raise GeometryError("{}: {}".format(room_label(room), exc)) from exc
    solids = [continuous(doc, records)] if mode == "continuous" else add_doors(doc, records, phase)
    return [{"loops": profile(solid, "Modo continuo" if mode == "continuous" else room_label(rooms[index])),
             "area": solid.Volume, "solid": solid,
             "label": "Suelo continuo" if mode == "continuous" else room_label(rooms[index])}
            for index, solid in enumerate(solids)]
