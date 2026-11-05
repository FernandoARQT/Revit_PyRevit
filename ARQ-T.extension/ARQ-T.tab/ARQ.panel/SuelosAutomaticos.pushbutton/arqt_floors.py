# -*- coding: utf-8 -*-
"""Read model choices, detect likely duplicates, create floors atomically."""
import math
import uuid
import Autodesk.Revit.DB as DB
from arqt_geometry import (GeometryError, element_id, loops_list, flatten_loop,
                          intersection_area, collect_rooms, AREA_EPS)


def type_name(element):
    # ElementType.Name can be write-only in Python.NET. Use the native parameter.
    parameter = element.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
    return parameter.AsString() if parameter else str(element.Id.Value)


def model_choices(doc, view):
    if doc.IsFamilyDocument or doc.IsReadOnly:
        raise ValueError("Abre un proyecto editable de Revit.")
    if not isinstance(view, DB.ViewPlan) or view.IsTemplate or view.GenLevel is None:
        raise ValueError("Abre una vista de planta con nivel asociado.")
    phase_parameter = view.get_Parameter(DB.BuiltInParameter.VIEW_PHASE)
    phase = doc.GetElement(phase_parameter.AsElementId()) if phase_parameter else None
    if not isinstance(phase, DB.Phase):
        raise ValueError("La vista activa no tiene una fase válida.")
    level = view.GenLevel
    rooms, excluded = [], 0
    options = DB.SpatialElementBoundaryOptions()
    options.SpatialElementBoundaryLocation = DB.SpatialElementBoundaryLocation.Finish
    try:
        for room in collect_rooms(doc):
            if room.LevelId != level.Id or room.get_Parameter(DB.BuiltInParameter.ROOM_PHASE_ID).AsElementId() != phase.Id:
                continue
            if room.DesignOption is not None:
                continue
            if room.Location is None or room.Area <= AREA_EPS or not room.GetBoundarySegments(options):
                excluded += 1
                continue
            rooms.append(room)
    finally:
        options.Dispose()
    if not rooms:
        raise ValueError("No hay habitaciones delimitadas con superficie válida en {} · fase {}."
                         .format(level.Name, phase.Name))
    # Architectural and structural usage are instance properties. Every normal
    # floor type can create an architectural floor; foundation slab types cannot.
    types = sorted((t for t in DB.FilteredElementCollector(doc).OfClass(DB.FloorType)
                    if not t.IsFoundationSlab), key=lambda t: type_name(t).casefold())
    if not types:
        raise ValueError("El proyecto no contiene tipos de suelo arquitectónico utilizables.")
    levels = sorted(DB.FilteredElementCollector(doc).OfClass(DB.Level), key=lambda l: l.ProjectElevation)
    return rooms, types, levels, level, phase, excluded


def validate_settings(doc, floor_type, level, offset_mm):
    if not math.isfinite(offset_mm):
        raise ValueError("Introduce un desfase numérico finito, expresado en milímetros.")
    if not isinstance(doc.GetElement(floor_type.Id), DB.FloorType) or floor_type.IsFoundationSlab:
        raise ValueError("El tipo de suelo ya no es válido.")
    if not isinstance(doc.GetElement(level.Id), DB.Level):
        raise ValueError("El nivel de creación ya no es válido.")
    return DB.UnitUtils.ConvertToInternalUnits(offset_mm, DB.UnitTypeId.Millimeters)


def likely_duplicates(doc, prepared, level, offset):
    """A warning, not an automatic replacement: plan overlap at same top height."""
    from arqt_geometry import prism, _loops_polygon, _polygon_backend
    target = level.ProjectElevation + offset
    matches = []
    for floor in DB.FilteredElementCollector(doc).OfClass(DB.Floor):
        floor_level = doc.GetElement(floor.LevelId)
        height = floor.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
        if floor_level is None or height is None or abs(floor_level.ProjectElevation + height.AsDouble() - target) > 1e-5:
            continue
        for reference in DB.HostObjectUtils.GetTopFaces(floor):
            face = floor.GetGeometryObjectFromReference(reference)
            if not isinstance(face, DB.PlanarFace) or face.FaceNormal.Z < 1 - 1e-7:
                continue
            try:
                continuous_items = [item for item in prepared if item.get("plan") is not None]
                if continuous_items:
                    polygon_class, _ = _polygon_backend()
                    overlaps = any(item["plan"].intersection(
                        _loops_polygon(face.GetEdgesAsCurveLoops(), item["plan_origin"], polygon_class)
                    ).area > AREA_EPS for item in continuous_items)
                    if overlaps:
                        matches.append(element_id(floor))
                        break
                    continue
                existing = prism(face.GetEdgesAsCurveLoops())
                if any(intersection_area(item["solid"], existing,
                                         "Comprobar suelo existente [ID {}]".format(element_id(floor))) > AREA_EPS
                       for item in prepared):
                    matches.append(element_id(floor))
                    break
            except GeometryError as exc:
                # Do not claim a duplicate check succeeded if Revit cannot compare.
                raise ValueError("No se ha podido comprobar el solape con el suelo [ID {}]: {}"
                                 .format(element_id(floor), exc)) from exc
    return matches


class FailureCollector(DB.IFailuresPreprocessor):
    # Python.NET requires a CLR namespace to implement a managed interface.
    # A unique namespace permits repeated executions in a cached pyRevit process.
    __namespace__ = "ARQT.Suelos.Failures_" + uuid.uuid4().hex

    def __init__(self):
        self.warnings = []
        self.errors = []

    def PreprocessFailures(self, accessor):
        for failure in accessor.GetFailureMessages():
            message = failure.GetDescriptionText()
            if failure.GetSeverity() == DB.FailureSeverity.Warning:
                self.warnings.append(message)
                accessor.DeleteWarning(failure)
            else:
                self.errors.append(message)
        return (DB.FailureProcessingResult.ProceedWithRollBack if self.errors
                else DB.FailureProcessingResult.Continue)


def create(doc, prepared, floor_type, level, offset_mm, logger):
    offset = validate_settings(doc, floor_type, level, offset_mm)
    failures = FailureCollector()
    group = DB.TransactionGroup(doc, "ARQ-T · Suelos automáticos")
    transaction = DB.Transaction(doc, "Crear suelos arquitectónicos")
    created, context = [], "Selección"
    try:
        if group.Start() != DB.TransactionStatus.Started:
            raise ValueError("No se ha podido iniciar la operación.")
        if transaction.Start() != DB.TransactionStatus.Started:
            raise ValueError("No se ha podido iniciar la transacción.")
        options = transaction.GetFailureHandlingOptions()
        options.SetFailuresPreprocessor(failures)
        options.SetClearAfterRollback(True)
        options.SetForcedModalHandling(True)
        transaction.SetFailureHandlingOptions(options)
        for item in prepared:
            context = item["label"]
            # The profile is placed at the creation LEVEL, without the offset.
            # The offset is applied exactly once through the native parameter.
            loops = loops_list(flatten_loop(loop, level.ProjectElevation) for loop in item["loops"])
            if not DB.BoundaryValidation.IsValidHorizontalBoundary(loops):
                raise ValueError("El perfil calculado no es válido.")
            floor = DB.Floor.Create(doc, loops, floor_type.Id, level.Id)
            parameter = floor.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
            if parameter is None or parameter.IsReadOnly or not parameter.Set(offset):
                raise ValueError("Revit no permite aplicar el desfase solicitado.")
            structural = floor.get_Parameter(DB.BuiltInParameter.FLOOR_PARAM_IS_STRUCTURAL)
            if structural is not None and not structural.IsReadOnly:
                structural.Set(0)
            created.append(floor)
        doc.Regenerate()
        for floor in created:
            context = "Suelo [ID {}]".format(element_id(floor))
            if floor.LevelId != level.Id or floor.GetTypeId() != floor_type.Id:
                raise ValueError("El nivel o el tipo creado no coincide con la configuración.")
            actual = floor.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM).AsDouble()
            if abs(actual - offset) > 1e-7:
                raise ValueError("El desfase creado no coincide con el solicitado.")
            faces = [floor.GetGeometryObjectFromReference(ref) for ref in DB.HostObjectUtils.GetTopFaces(floor)]
            if not faces or any(not isinstance(face, DB.PlanarFace) or face.FaceNormal.Z < 1 - 1e-7 or
                                abs(face.Origin.Z - (level.ProjectElevation + offset)) > 1e-5 for face in faces):
                raise ValueError("La cota superior del suelo no coincide con nivel + desfase.")
        if transaction.Commit() != DB.TransactionStatus.Committed:
            raise ValueError("Revit ha rechazado la creación: {}".format("; ".join(failures.errors)))
        for warning in failures.warnings:
            logger.warning(warning)
        if group.Assimilate() != DB.TransactionStatus.Committed:
            raise ValueError("Revit no ha confirmado la operación completa.")
        return created, failures.warnings
    except Exception as exc:
        if transaction.GetStatus() == DB.TransactionStatus.Started:
            transaction.RollBack()
        if group.GetStatus() == DB.TransactionStatus.Started:
            group.RollBack()
        logger.exception("Fallo atómico en %s", context)
        raise ValueError("{}: {}\nNo se ha creado ningún suelo en esta ejecución.".format(context, exc)) from exc
    finally:
        transaction.Dispose()
        group.Dispose()
