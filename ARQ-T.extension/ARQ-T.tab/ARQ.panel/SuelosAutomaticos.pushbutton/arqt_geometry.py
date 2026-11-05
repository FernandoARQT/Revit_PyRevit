# -*- coding: utf-8 -*-
"""Native Revit contours; GEOS planar assembly; no document mutations.

All calculation solids have their base at Z=0 and height 1 internal foot.
Their volume therefore equals the plan area in internal square feet.
"""
import json
import os
import sys
import tempfile
from datetime import datetime
import Autodesk.Revit.DB as DB
from System.Collections.Generic import List


class GeometryError(ValueError):
    pass


EPS = 1e-7
AREA_EPS = 1e-7
# Internal feet: 0.00003048 mm. Resolve floating-point seams without closing
# physical modelling gaps or using a buffer to enlarge the selected footprint.
PLAN_GRID = 1e-7


def collect_rooms(doc):
    # Room is an API-only subtype and cannot be passed to OfClass. Use its
    # supported native base class, then narrow the result to the room category.
    return (DB.FilteredElementCollector(doc).OfClass(DB.SpatialElement)
            .OfCategory(DB.BuiltInCategory.OST_Rooms).WhereElementIsNotElementType())


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


def _bounds(solid):
    # Bounding boxes are used only to skip impossible operations, never to
    # construct a floor contour. Solid boxes have their own local transform.
    box = solid.GetBoundingBox()
    points = [box.Transform.OfPoint(DB.XYZ(x, y, z))
              for x in (box.Min.X, box.Max.X) for y in (box.Min.Y, box.Max.Y)
              for z in (box.Min.Z, box.Max.Z)]
    return (min(p.X for p in points), min(p.Y for p in points),
            max(p.X for p in points), max(p.Y for p in points))


def _overlap(a, b):
    return min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])


def boolean(a, b, operation, context="Cálculo del perfil"):
    try:
        return DB.BooleanOperationsUtils.ExecuteBooleanOperation(a, b, operation)
    except Exception as exc:
        # Keep the native exception as the cause for the technical logger, not
        # a multi-page .NET stack trace in the user dialog.
        raise GeometryError("{}: Revit no ha podido resolver la operación {} entre los contornos. "
                            "Revisa el encuentro de los límites en esta zona."
                            .format(context, operation)) from exc


def intersection_area(a, b, context="Comprobar solapes"):
    if min(_overlap(_bounds(a), _bounds(b))) <= 0:
        return 0.0
    return boolean(a, b, DB.BooleanOperationsType.Intersect, context).Volume


def difference(a, b):
    if min(_overlap(_bounds(a), _bounds(b))) <= 0:
        return a
    return boolean(a, b, DB.BooleanOperationsType.Difference)


def union(solids, context="Unir superficies"):
    solids = list(solids)
    if not solids:
        raise GeometryError("No hay superficies para unir.")
    result = solids[0]
    pending = solids[1:]
    while pending:
        result_bounds = _bounds(result)
        failure = None
        for index, solid in enumerate(pending):
            # Rooms may be separated by a partition. Try its connecting strip
            # before the room on the opposite side. Never union disjoint solids
            # and hope a later strip repairs an invalid intermediate topology.
            if min(_overlap(result_bounds, _bounds(solid))) < -EPS:
                continue
            try:
                merged = boolean(result, solid, DB.BooleanOperationsType.Union, context)
                if len(list(DB.SolidUtils.SplitVolumes(merged))) != 1:
                    continue
            except GeometryError as exc:
                # A corner contact can fail while the intervening strip is
                # still pending. Retry it after another valid union expands
                # the connected region. No offsets or artificial bridges.
                failure = exc
                continue
            result = merged
            del pending[index]
            break
        else:
            if failure is not None:
                raise failure
            raise GeometryError("{}: hay regiones desconectadas; no se crearán conexiones artificiales."
                                .format(context))
    return result


def _polygon_backend():
    """Use installed CPython-compatible Shapely; never install at runtime."""
    def imports():
        from shapely import set_precision  # Require the GEOS precision API (Shapely 2).
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
        return Polygon, unary_union
    try:
        return imports()
    except ImportError:
        pass
    version = "Python{}{}".format(sys.version_info.major, sys.version_info.minor)
    candidates = [os.path.join(os.path.dirname(__file__), "lib"),
                  os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python",
                               version, "Lib", "site-packages")]
    last_error = None
    for directory in candidates:
        if not os.path.isdir(directory):
            continue
        # The path is needed only while importing NumPy/Shapely. Their package
        # __path__ and loaded native DLLs remain valid after removing this entry.
        sys.path.insert(0, directory)
        try:
            return imports()
        except ImportError as exc:
            last_error = exc
        finally:
            sys.path.remove(directory)
    raise GeometryError("La comprobación 2D requiere Shapely y NumPy compatibles con CPython {}.{}. "
                        "Consulta las dependencias del README.".format(sys.version_info.major,
                                                                      sys.version_info.minor)) from last_error


def _plan_polygon(solid, origin, polygon_class):
    return _loops_polygon(profile(solid), origin, polygon_class)


def _loops_polygon(loops, origin, polygon_class):
    rings = []
    for loop in loops:
        coordinates = []
        for curve in loop:
            if not isinstance(curve, DB.Line):
                raise GeometryError("El cálculo continuo contiene límites curvos. "
                                    "No se aproximarán; utiliza el modo por habitación.")
            point = curve.GetEndPoint(0)
            coordinates.append((point.X - origin[0], point.Y - origin[1]))
        rings.append(coordinates)
    if not rings:
        raise GeometryError("El encuentro no tiene un único perímetro exterior válido.")
    outer = max(range(len(rings)), key=lambda i: polygon_class(rings[i]).area)
    polygon = polygon_class(rings[outer], [ring for i, ring in enumerate(rings) if i != outer])
    if not polygon.is_valid or polygon.is_empty or polygon.area <= AREA_EPS:
        raise GeometryError("El encuentro contiene un perímetro o hueco 2D no válido.")
    return polygon


def _plan_shape(shape):
    from shapely import set_precision
    if not shape.is_valid:
        raise GeometryError("Se ha recibido un contorno 2D no válido.")
    result = set_precision(shape, PLAN_GRID)
    if result.geom_type == "MultiPolygon" and len(result.geoms) == 1:
        result = result.geoms[0]
    # Microscopic holes, needles and detached slivers may legitimately collapse
    # on the precision grid. Counting rings or comparing boundary Hausdorff
    # distances would reject those harmless removals (even a vanished pinhole
    # deep inside a room can have a large boundary Hausdorff distance).
    # Check actual changed AREA instead, bounded by the grid swept along the
    # input perimeter. Real patios/shafts remain far above this numerical scale.
    if shape.symmetric_difference(result).area > _plan_area_tolerance(shape):
        raise GeometryError("La limpieza numérica alteraría una superficie significativa del contorno; "
                            "necesita revisión.")
    return result


def _plan_area_tolerance(shape):
    # Bound the area swept by sub-micrometre precision changes along the edge.
    return max(AREA_EPS, shape.length * PLAN_GRID * 2)


def _union_room_passages(polygons, passages, merge):
    rooms = [_plan_shape(shape) for shape in polygons]
    passages = [_plan_shape(shape) for shape in passages]
    return merge(rooms + passages), merge(rooms).area


def profile(solid, context="Selección"):
    volumes = list(DB.SolidUtils.SplitVolumes(solid))
    if len(volumes) != 1:
        raise GeometryError("{}: hay regiones desconectadas. Selecciona un conjunto contiguo.".format(context))
    faces = [face for face in solid.Faces if isinstance(face, DB.PlanarFace)
             and face.FaceNormal.Z > 1.0 - EPS]
    if len(faces) == 1:
        face = faces[0]
        analyzer = None
    else:
        # Boolean operations may retain coplanar face divisions. The native
        # extrusion analyzer removes those seams without stitching curves.
        analyzer = DB.ExtrusionAnalyzer.Create(solid,
            DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, DB.XYZ.Zero), DB.XYZ.BasisZ)
        face = analyzer.GetExtrusionBase()
    loops = []
    for loop in face.GetEdgesAsCurveLoops():
        if face.FaceNormal.Z < 0:
            loop.Flip()
        loops.append(flatten_loop(loop))
    if analyzer is not None:
        analyzer.Dispose()
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
                                 "after": int(items[(index + 1) % len(items)].ElementId.Value),
                                 "before_curve": items[index - 1].GetCurve(),
                                 "after_curve": items[(index + 1) % len(items)].GetCurve()})
            loops.append(flatten_loop(loop))
        solid = prism(loops)
        return {"room": room, "loops": profile(solid, room_label(room)),
                "segments": segments, "solid": solid}
    finally:
        options.Dispose()


def _line_span(segment, axis, origin):
    curve = segment["curve"]
    if not isinstance(curve, DB.Line):
        raise GeometryError("El modo continuo requiere tabiques compartidos rectos. "
                            "Los perímetros curvos se conservan en los demás límites.")
    points = [DB.XYZ(p.X, p.Y, 0) for p in (curve.GetEndPoint(0), curve.GetEndPoint(1))]
    ends = [(axis.DotProduct(p - origin), p, neighbor, adjacent)
            for p, neighbor, adjacent in zip(points, (segment["before"], segment["after"]),
                                              (segment.get("before_curve"), segment.get("after_curve")))]
    ends.sort(key=lambda item: item[0])
    return ends


def _revit_plan_polygon(polygon, tolerance):
    from shapely.geometry import Polygon
    from math import hypot

    def short_edges(shape):
        return [hypot(b[0]-a[0], b[1]-a[1])
                for ring in [shape.exterior] + list(shape.interiors)
                for a, b in zip(list(ring.coords), list(ring.coords)[1:])
                if hypot(b[0]-a[0], b[1]-a[1]) < tolerance]

    polygon = polygon.simplify(PLAN_GRID * 2, preserve_topology=True)
    if not short_edges(polygon):
        return polygon
    # Revit cannot represent a hole narrower than its minimum edge length,
    # including long slivers left by nearly coincident partition strips.
    holes = []
    for ring in polygon.interiors:
        hole = Polygon(ring)
        envelope = hole.minimum_rotated_rectangle
        if envelope.geom_type != "Polygon":
            continue
        rectangle = list(envelope.exterior.coords)
        width = min(hypot(b[0]-a[0], b[1]-a[1]) for a, b in zip(rectangle, rectangle[1:]))
        if width < tolerance:
            continue
        holes.append(ring)
    base = Polygon(polygon.exterior, holes)
    cleaned = base.simplify(tolerance, preserve_topology=True)
    if not cleaned.is_valid or cleaned.geom_type != "Polygon" or \
            len(cleaned.interiors) != len(base.interiors) or \
            cleaned.hausdorff_distance(base) > tolerance * 1.001:
        raise GeometryError("No se puede limpiar el contorno dentro de la tolerancia de Revit.")
    # Douglas-Peucker retains ring anchors and topology constraints; it does
    # not guarantee a minimum edge length. Resolve residual short edges locally.
    # Try the intersection of the neighbouring support lines first, and both
    # endpoint collapses. Validate the WHOLE polygon after every replacement.
    while short_edges(cleaned):
        rings = [list(cleaned.exterior.coords)[:-1]] + [list(r.coords)[:-1] for r in cleaned.interiors]
        replacement = None
        for ring_index, points in enumerate(rings):
            if len(points) <= 3:
                continue
            for index, a in enumerate(points):
                b = points[(index+1) % len(points)]
                if hypot(b[0]-a[0], b[1]-a[1]) >= tolerance:
                    continue
                rotated = points[index:] + points[:index]
                proposals = [rotated[1:], [rotated[0]] + rotated[2:]]
                p, q = rotated[-1], rotated[2]
                u, v = (a[0]-p[0], a[1]-p[1]), (q[0]-b[0], q[1]-b[1])
                cross = u[0]*v[1] - u[1]*v[0]
                if abs(cross) > EPS * hypot(*u) * hypot(*v):
                    ratio = ((b[0]-p[0])*v[1] - (b[1]-p[1])*v[0]) / cross
                    corner = (p[0]+ratio*u[0], p[1]+ratio*u[1])
                    proposals.insert(0, [corner] + rotated[2:])
                choices = []
                for proposal in proposals:
                    revised = rings[:]
                    revised[ring_index] = proposal
                    candidate = Polygon(revised[0], revised[1:])
                    if not candidate.is_valid or candidate.is_empty:
                        continue
                    if candidate.boundary.hausdorff_distance(base.boundary) > tolerance * 1.001:
                        continue
                    changed_area = candidate.symmetric_difference(base).area
                    if changed_area > base.length * tolerance:
                        continue
                    choices.append((len(short_edges(candidate)), changed_area, candidate))
                if choices:
                    replacement = min(choices, key=lambda item: item[:2])[2]
                    break
            if replacement is not None:
                break
        if replacement is None:
            break
        cleaned = replacement
    remaining = short_edges(cleaned)
    if remaining:
        raise GeometryError("Queda una arista de {:.4f} mm en un encuentro que no puede simplificarse "
                            "sin modificar su forma. Tolerancia de Revit: {:.4f} mm."
                            .format(min(remaining)*304.8, tolerance*304.8))
    return cleaned


def _native_plan_loops(polygon, origin, tolerance):
    from shapely.geometry.polygon import orient
    polygon = orient(_revit_plan_polygon(polygon, tolerance), sign=1.0)
    loops = []
    for ring in [polygon.exterior] + list(polygon.interiors):
        points = [DB.XYZ(x+origin[0], y+origin[1], 0) for x, y in list(ring.coords)[:-1]]
        loop = DB.CurveLoop()
        for a, b in zip(points, points[1:] + points[:1]):
            if a.DistanceTo(b) < tolerance:
                raise GeometryError("Una arista sigue siendo menor que la tolerancia de Revit tras la limpieza.")
            loop.Append(DB.Line.CreateBound(a, b))
        loops.append(loop)
    return loops


def _door_type_passage(doc, door, origin, polygon_class, side=0):
    """User's type Anchura is the clear passage; depth comes from host faces."""
    wall = door.Host
    if not isinstance(wall, DB.Wall) or not isinstance(door.Location, DB.LocationPoint) or \
            not isinstance(wall.Location, DB.LocationCurve) or not isinstance(wall.Location.Curve, DB.Line):
        raise GeometryError("El paso requiere una puerta centrada sobre un muro local recto.")
    door_type = doc.GetElement(door.GetTypeId())
    parameter = door_type.LookupParameter("Anchura") if door_type is not None else None
    if parameter is None or parameter.StorageType != DB.StorageType.Double or not parameter.HasValue:
        raise GeometryError("El tipo de puerta no tiene un parámetro de longitud «Anchura» válido.")
    width = parameter.AsDouble()  # Revit length parameters already use internal feet.
    if width <= doc.Application.ShortCurveTolerance:
        raise GeometryError("La Anchura del tipo de puerta no tiene un valor positivo utilizable.")
    point = door.Location.Point
    normal, low, high = _wall_planes(doc, wall, point)
    median = (low + high) / 2
    if side < 0:
        high = median
    elif side > 0:
        low = median
    axis = wall.Location.Curve.Direction
    station = axis.DotProduct(point)
    points = [axis.Multiply(s) + normal.Multiply(depth)
              for s, depth in ((station-width/2, low), (station+width/2, low),
                               (station+width/2, high), (station-width/2, high))]
    return _plan_shape(polygon_class([(p.X-origin[0], p.Y-origin[1]) for p in points]))


def _continuous_door_patches(doc, records, phase, polygons, origin, polygon_class):
    """Join selected rooms using type Anchura and actual wall finish planes."""
    by_id = {element_id(record["room"]): (record, polygon)
             for record, polygon in zip(records, polygons)}
    patches, door_ids, errors = [], [], []
    doors = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Doors).WhereElementIsNotElementType()
    for door in doors:
        if not isinstance(door, DB.FamilyInstance) or door.DesignOption is not None:
            continue
        try:
            if door.GetPhaseStatus(phase.Id) not in (DB.ElementOnPhaseStatus.New, DB.ElementOnPhaseStatus.Existing):
                continue
            a, b = _door_rooms(doc, door, records, phase)
            if a is None or b is None or element_id(a) == element_id(b) or \
                    any(element_id(room) not in by_id for room in (a, b)):
                # In particular, never extend into an excluded porch.
                continue
            elevations = [room.Level.ProjectElevation + room.BaseOffset for room in (a, b)]
            if abs(elevations[0] - elevations[1]) > EPS:
                raise GeometryError("Las habitaciones a ambos lados tienen distintas cotas de base.")
            opening = _door_type_passage(doc, door, origin, polygon_class)
            for room in (a, b):
                contact = opening.intersection(by_id[element_id(room)][1])
                if contact.area <= AREA_EPS and contact.length <= doc.Application.ShortCurveTolerance:
                    raise GeometryError("El hueco real no alcanza el contorno de la habitación {}."
                                        .format(room.Number))
            patches.append(opening)
            door_ids.append(element_id(door))
        except Exception as exc:
            detail = (str(exc).splitlines() or [type(exc).__name__])[0]
            errors.append("Puerta [ID {}]: {}".format(element_id(door), detail))
    return patches, door_ids, errors


def _disconnection_report(result, records, polygons):
    parts = sorted(result.geoms, key=lambda part: part.area, reverse=True)
    groups = [[index for index, shape in enumerate(polygons) if part.intersection(shape).area > AREA_EPS]
              for part in parts]
    lines = ["Modo continuo: quedan {} regiones desconectadas.".format(len(parts))]
    for number, indices in enumerate(groups, 1):
        names = ["{} [ID {}]".format(records[i]["room"].Number, element_id(records[i]["room"])) for i in indices]
        lines.append("Grupo {}: {}.".format(number, ", ".join(names) or "Fragmento sin habitación"))
    distance, i, j = min((a.distance(b), i, j) for i, a in enumerate(parts) for j, b in enumerate(parts) if j > i)
    if distance <= PLAN_GRID:
        lines.append("Los grupos {} y {} tienen contacto puntual, sin un paso con anchura."
                     .format(i+1, j+1))
    else:
        lines.append("Separación mínima entre grupos {} y {}: {:.6f} mm."
                     .format(i+1, j+1, distance * 304.8))
    return "\n".join(lines)


def _write_plan_diagnostic(records, polygons, patches, result, origin, door_errors):
    # Save enough numeric input to replay the failure without Revit. This is
    # local technical evidence, not a change to the model or a remote upload.
    directory = os.path.join(tempfile.gettempdir(), "ARQ-T", "SuelosAutomaticos")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "continuo-{}.json".format(datetime.now().strftime("%Y%m%d-%H%M%S-%f")))
    data = {"format": 2, "units": "internal feet", "origin": origin, "precision_grid": PLAN_GRID,
            "surface_rule": "rooms_and_type_width_door_passages", "rooms": [],
            "patches": [shape.__geo_interface__ for shape in patches],
            "result": result.__geo_interface__, "door_errors": door_errors}
    def endpoints(curve):
        return [[curve.GetEndPoint(i).X, curve.GetEndPoint(i).Y] for i in (0, 1)] if curve is not None else None
    for record, polygon in zip(records, polygons):
        data["rooms"].append({"id": element_id(record["room"]), "number": record["room"].Number,
                              "geometry": polygon.__geo_interface__,
                              "segments": [{"host": s["host"], "points": endpoints(s["curve"]),
                                            "before": endpoints(s.get("before_curve")),
                                            "after": endpoints(s.get("after_curve"))}
                                           for s in record["segments"]]})
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    return path


def continuous(doc, records, phase):
    polygon_class, merge = _polygon_backend()
    bounds = _bounds(records[0]["solid"])
    origin = ((bounds[0]+bounds[2])/2, (bounds[1]+bounds[3])/2)
    polygons = [_plan_shape(_loops_polygon(record["loops"], origin, polygon_class)) for record in records]
    # Rooms sharing a separator already touch: a planar union removes that
    # zero-thickness internal boundary without requiring any wall or door.
    door_patches, door_ids, door_errors = _continuous_door_patches(
        doc, records, phase, polygons, origin, polygon_class)
    tolerance = doc.Application.ShortCurveTolerance
    # The required surface is ONLY rooms + door passages. Keep every resulting
    # interior ring and exterior recess under walls. Never fill those voids
    # from wall footprints or room center boundaries.
    patches = door_patches
    candidate, room_area = _union_room_passages(polygons, patches, merge)
    if candidate.geom_type not in ("Polygon", "MultiPolygon") or door_errors:
        message = (_disconnection_report(candidate, records, polygons) if candidate.geom_type == "MultiPolygon"
                   else "No se han podido verificar todos los pasos entre habitaciones seleccionadas.")
        if door_errors:
            message += "\n\nNo se han podido verificar estos pasos:\n" + "\n".join(door_errors[:4])
        try:
            path = _write_plan_diagnostic(records, polygons, patches, candidate, origin, door_errors)
            message += "\n\nDiagnóstico local: " + path
        except OSError:
            message += "\n\nNo se ha podido guardar el diagnóstico local."
        raise GeometryError(message)
    parts = [candidate] if candidate.geom_type == "Polygon" else list(candidate.geoms)
    parts.sort(key=lambda part: (part.bounds[0], part.bounds[1], -part.area))
    solids, plans, groups = [], [], []
    try:
        for part in parts:
            result = _revit_plan_polygon(part, tolerance)
            loops = _native_plan_loops(result, origin, tolerance)
            solid = prism(loops)
            reconstructed = _plan_polygon(solid, origin, polygon_class)
            if reconstructed.symmetric_difference(result).area > _plan_area_tolerance(result):
                raise GeometryError("El perfil nativo no coincide con el área continua calculada.")
            solids.append(solid)
            plans.append(result)
            groups.append([record["room"].Number for record, polygon in zip(records, polygons)
                           if part.intersection(polygon).area > AREA_EPS])
        for index, plan in enumerate(plans):
            if any(plan.intersection(other).area > AREA_EPS for other in plans[index+1:]):
                raise GeometryError("La limpieza produciría un solape entre grupos independientes.")
    except GeometryError as exc:
        try:
            path = _write_plan_diagnostic(records, polygons, patches, candidate, origin, door_errors)
        except OSError:
            raise exc
        raise GeometryError(str(exc) + "\n\nDiagnóstico local: " + path) from exc
    result = merge(plans)
    return solids, {"room_area": room_area, "added_area": result.area-room_area,
                   "bridge_count": len(patches), "surface_rule": "rooms_and_type_width_door_passages",
                   "room_source": "Room.GetBoundarySegments(Finish)",
                   "verified_doors": door_ids, "precision_grid_mm": PLAN_GRID * 304.8,
                   "door_geometry_issues": door_errors,
                   "cleanup_area": candidate.symmetric_difference(result).area,
                   "preserved_holes": sum(len(plan.interiors) for plan in plans),
                   "preserved_hole_area": sum(polygon_class(ring).area for plan in plans for ring in plan.interiors),
                   "group_count": len(plans), "groups": groups,
                   "origin": origin, "polygon": result, "polygons": plans}


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


def _side_points(normal, low, high, point, source_z):
    clearance = DB.UnitUtils.ConvertToInternalUnits(5.0, DB.UnitTypeId.Millimeters)
    base = DB.XYZ(point.X, point.Y, source_z + 1.0)
    return [base + normal.Multiply(bound - normal.DotProduct(base) + direction * clearance)
            for bound, direction in ((low, -1), (high, 1))]


def _door_rooms(doc, door, records, phase):
    """Resolve phase-aware room relations, checking local wall faces if needed."""
    selected = {element_id(record["room"]) for record in records}
    a, b = door.get_FromRoom(phase), door.get_ToRoom(phase)
    relevant = [room for room in (a, b) if room is not None and element_id(room) in selected]
    host = door.Host
    candidates = [record for record in records if host is not None and
                  any(s["host"] == element_id(host) for s in record["segments"])]
    if (a is None or b is None or not relevant) and candidates and \
            door.LevelId == records[0]["room"].LevelId and \
            isinstance(host, DB.Wall) and isinstance(door.Location, DB.LocationPoint) and \
            isinstance(host.Location, DB.LocationCurve) and isinstance(host.Location.Curve, DB.Line):
        point = door.Location.Point
        axis = host.Location.Curve.Direction
        local = []
        for record in candidates:
            for segment in record["segments"]:
                if segment["host"] != element_id(host) or not isinstance(segment["curve"], DB.Line):
                    continue
                ends = _line_span(segment, axis, DB.XYZ.Zero)
                station = axis.DotProduct(point)
                if ends[0][0] - EPS <= station <= ends[1][0] + EPS:
                    local.append(record)
                    break
        if local:
            normal, low, high = _wall_planes(doc, host, point)
            source_z = local[0]["room"].Level.ProjectElevation + local[0]["room"].BaseOffset
            sampled = [doc.GetRoomAtPoint(p, phase) for p in _side_points(normal, low, high, point, source_z)]
            if any(room is not None and element_id(room) in selected for room in sampled):
                a, b = sampled
                relevant = [room for room in sampled if room is not None and element_id(room) in selected]
            elif not relevant:
                raise GeometryError("No se pueden relacionar las habitaciones de la puerta con sus caras reales.")
    return (a, b) if relevant else (None, None)


def _apply_passage_half(room_plan, half, opposite, merge):
    contact = room_plan.intersection(half)
    if contact.area <= AREA_EPS and contact.length <= EPS:
        raise GeometryError("La mitad del paso no alcanza el contorno de su habitación.")
    result = merge([room_plan.difference(opposite), half])
    if result.geom_type != "Polygon" or not result.is_valid:
        raise GeometryError("La mitad del paso queda separada de su habitación.")
    return result


def add_doors(doc, records, phase):
    polygon_class, merge = _polygon_backend()
    by_id = {element_id(r["room"]): r for r in records}
    solids = {key: record["solid"] for key, record in by_id.items()}
    # A fixed grid origin keeps the doorway mid-line identical when each room
    # is generated in a separate execution or in a different selection order.
    origin = (0.0, 0.0)
    plans = {key: _plan_shape(_loops_polygon(record["loops"], origin, polygon_class))
             for key, record in by_id.items()
             if all(isinstance(curve, DB.Line) for loop in record["loops"] for curve in loop)}
    touched, shared_passages = set(), []
    doors = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Doors).WhereElementIsNotElementType()
    for door in doors:
        if not isinstance(door, DB.FamilyInstance):
            continue
        if door.DesignOption is not None:
            continue
        try:
            status = door.GetPhaseStatus(phase.Id)
            if status not in (DB.ElementOnPhaseStatus.New, DB.ElementOnPhaseStatus.Existing):
                continue
            a, b = _door_rooms(doc, door, records, phase)
            related = [room for room in (a, b) if room is not None]
            if not related or (len(related) == 2 and a.Id == b.Id):
                continue
            elevations = [r.Level.ProjectElevation + r.BaseOffset for r in related]
            if max(elevations) - min(elevations) > EPS:
                raise GeometryError("Las habitaciones a ambos lados tienen distintas cotas de base.")
            normal, low, high = _wall_planes(doc, door.Host, door.Location.Point)
            halves = {sign: _door_type_passage(doc, door, origin, polygon_class, side=sign)
                      for sign in (-1, 1)}
            sample_points = _side_points(normal, low, high, door.Location.Point, elevations[0])
            signs = []
            for room in related:
                membership = [room.IsPointInRoom(point) for point in sample_points]
                if membership.count(True) != 1:
                    raise GeometryError("No se puede identificar un único lado de la habitación junto al umbral.")
                signs.append(-1 if membership[0] else 1)
            if len(signs) == 2 and signs[0] == signs[1]:
                raise GeometryError("FromRoom y ToRoom no están a lados opuestos del tabique.")
            for room, sign in zip(related, signs):
                key = element_id(room)
                if key not in by_id:
                    continue
                if key in plans:
                    plans[key] = _apply_passage_half(plans[key], halves[sign], halves[-sign], merge)
                    touched.add(key)
                else:
                    # Keep native arcs for curved room boundaries. The door
                    # itself still uses the same type-width rectangular halves.
                    half = prism(_native_plan_loops(halves[sign], origin, doc.Application.ShortCurveTolerance))
                    opposite = prism(_native_plan_loops(halves[-sign], origin, doc.Application.ShortCurveTolerance))
                    solids[key] = union([difference(solids[key], opposite), half])
                    profile(solids[key], room_label(room))
            if len(related) == 2 and all(element_id(room) in plans for room in related):
                shared_passages.append((element_id(a), element_id(b), merge(list(halves.values())), element_id(door)))
        except Exception as exc:
            raise GeometryError("Puerta [ID {}]: {}".format(element_id(door), exc)) from exc
    for key in touched:
        loops = _native_plan_loops(plans[key], origin, doc.Application.ShortCurveTolerance)
        solids[key] = prism(loops)
        plans[key] = _plan_shape(_plan_polygon(solids[key], origin, polygon_class))
    for a, b, passage, door_id in shared_passages:
        if plans[a].intersection(plans[b]).intersection(passage).area > AREA_EPS or \
                passage.difference(merge([plans[a], plans[b]])).area > _plan_area_tolerance(passage):
            raise GeometryError("Puerta [ID {}]: las dos mitades dejan un hueco o solape en el encuentro."
                                .format(door_id))
    return [solids[element_id(record["room"])] for record in records]


def prepare(doc, rooms, mode, phase):
    records = []
    for room in rooms:
        try:
            records.append(boundary_data(room))
        except Exception as exc:
            raise GeometryError("{}: {}".format(room_label(room), exc)) from exc
    metadata = {}
    if mode == "continuous":
        solids, metadata = continuous(doc, records, phase)
    else:
        solids = add_doors(doc, records, phase)
    if mode == "continuous":
        selected = {element_id(room) for room in rooms}
        for other in collect_rooms(doc):
            if element_id(other) in selected or other.LevelId != rooms[0].LevelId or other.Area <= AREA_EPS:
                continue
            if other.DesignOption is not None:
                continue
            if other.get_Parameter(DB.BuiltInParameter.ROOM_PHASE_ID).AsElementId() != phase.Id:
                continue
            polygon_class, _ = _polygon_backend()
            other_record = boundary_data(other)
            other_polygon = _plan_shape(_loops_polygon(other_record["loops"], metadata["origin"], polygon_class))
            if metadata["polygon"].intersection(other_polygon).area > AREA_EPS:
                raise GeometryError("El contorno continuo invadiría la habitación no seleccionada {}. "
                                    "Revisa los encuentros de tabiques o utiliza el modo por habitación."
                                    .format(room_label(other)))
    return [{"loops": profile(solid, "Modo continuo" if mode == "continuous" else room_label(rooms[index])),
             "area": solid.Volume, "solid": solid,
             "label": "Suelo continuo · grupo {}".format(index+1) if mode == "continuous" else room_label(rooms[index]),
             "plan": metadata["polygons"][index] if mode == "continuous" else None,
             "plan_origin": metadata.get("origin"),
             "diagnostics": {key: value for key, value in metadata.items() if key not in ("origin", "polygon", "polygons")}}
            for index, solid in enumerate(solids)]
