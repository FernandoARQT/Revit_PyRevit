# -*- coding: utf-8 -*-
"""Utilidades sin interfaz para llamadas MCP. Compatibles con IronPython 2.7.

No mantienen trabajo pendiente ni ejecutan cambios fuera del run guardado por MCP.
Las familias temporales se cierran sin guardar; solo LoadFamily cambia el proyecto.
"""
from pyrevit.api import DB, UI


def _text(value):
    try:
        unicode_type = unicode
    except NameError:
        unicode_type = str
    if isinstance(value, unicode_type):
        return value
    if hasattr(value, 'decode'):
        try:
            return value.decode('utf-8')
        except UnicodeDecodeError:
            return value.decode('cp1252')
    return unicode_type(value)


class DocumentOperationError(RuntimeError):
    """Operacion no admitida sin perder cambios o vulnerar el guard del MCP."""


def list_documents(uiapp):
    """Devuelve identificadores de sesion, rutas y estado sin modificar documentos."""
    active = uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
    return [{'session_id':str(d.GetHashCode()), 'title':_text(d.Title),
             'path':_text(d.PathName), 'family':bool(d.IsFamilyDocument),
             'active':active is not None and d.Equals(active),
             'modified':bool(d.IsModified), 'read_only':bool(d.IsReadOnly),
             'linked':bool(d.IsLinked)} for d in uiapp.Application.Documents]


def find_document(uiapp, selector):
    """Resuelve id de sesion, titulo o ruta exactos; rechaza nombres ambiguos."""
    matches = [d for d in uiapp.Application.Documents
               if selector in (str(d.GetHashCode()), d.Title, d.PathName)]
    if len(matches) != 1:
        raise DocumentOperationError('Documento ausente o ambiguo: {0}'.format(selector))
    if matches[0].IsLinked:
        raise DocumentOperationError('No se permite operar sobre documentos vinculados.')
    return matches[0]


def activate_document(uiapp, selector):
    """Comprueba el destino y comunica la restriccion verificada del host actual."""
    target = find_document(uiapp, selector)
    active = uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
    if active is not None and target.Equals(active):
        return {'status':'already_active', 'document':target.Title}
    raise DocumentOperationError('Cambio de pestana no soportado por este host MCP: la prueba de OpenAndActivateDocument fallo dentro del guard. Active la pestana manualmente. No se ejecutaran acciones diferidas.')


def _active_target(uiapp, target):
    active = uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
    if active is None or not target.Equals(active):
        raise DocumentOperationError('El documento que se modifica debe ser el activo al comenzar el run MCP.')
    if target.IsReadOnly or target.IsLinked or target.IsModifiable:
        raise DocumentOperationError('Documento no editable o con transaccion abierta.')


def _family(project, family_id):
    family = project.GetElement(DB.ElementId(int(family_id)))
    if isinstance(family, DB.FamilyInstance):
        family = family.Symbol.Family
    elif isinstance(family, DB.FamilySymbol):
        family = family.Family
    if not isinstance(family, DB.Family) or not family.IsEditable:
        raise DocumentOperationError('El identificador no corresponde a una familia editable.')
    return family


def _existing_family_editor(uiapp, family):
    candidates = set([family.Name, family.Name + '.rfa'])
    return [d for d in uiapp.Application.Documents
            if d.IsFamilyDocument and (d.Title in candidates or
               (d.OwnerFamily is not None and d.OwnerFamily.Name == family.Name))]


def _load_options(overwrite_parameter_values):
    from pyrevit.revit.db.create import FamilyLoaderOptionsHandler
    return FamilyLoaderOptionsHandler(overwriteParameterValues=overwrite_parameter_values)


def edit_and_reload_family(uiapp, project, family_id, editor,
                           overwrite_parameter_values=False):
    """Edita una familia temporal y la recarga dentro de un unico run_modify.

    editor(family_document) se ejecuta dentro de una transaccion de familia.
    Debe devolver datos JSON. No debe abrir/cerrar transacciones ni guardar.
    No se toma control de un editor que el usuario ya tenia abierto.
    El dry-run del MCP revierte LoadFamily y finally descarta la familia temporal.
    """
    _active_target(uiapp, project)
    if project.IsFamilyDocument:
        raise DocumentOperationError('Se requiere un proyecto activo.')
    family = _family(project, family_id)
    name = _text(family.Name)
    if _existing_family_editor(uiapp, family):
        raise DocumentOperationError('La familia ya esta abierta. Active su editor para modificarla; no se descartaran sus cambios.')
    documents_before = list(uiapp.Application.Documents)
    fd = project.EditFamily(family)
    if any(fd.Equals(previous) for previous in documents_before):
        raise DocumentOperationError('EditFamily devolvio un editor preexistente. No se editara ni cerrara.')
    try:
        t = DB.Transaction(fd, 'MCP: editar familia temporal')
        t.Start()
        try:
            details = editor(fd)
            if t.Commit() != DB.TransactionStatus.Committed:
                raise DocumentOperationError('Revit no ha confirmado la transaccion de familia.')
        except BaseException:
            if t.GetStatus() == DB.TransactionStatus.Started:
                t.RollBack()
            raise
        loaded = fd.LoadFamily(project, _load_options(overwrite_parameter_values))
        if loaded is None:
            raise DocumentOperationError('Revit no ha cargado la familia modificada.')
        return {'status':'loaded_in_run', 'family':name,
                'family_id':int(loaded.Id.Value), 'details':details,
                'overwrite_parameter_values':bool(overwrite_parameter_values),
                'saved_to_disk':False}
    finally:
        if fd.IsValidObject and not fd.Close(False):
            raise DocumentOperationError('No se ha podido cerrar la familia temporal sin guardar.')


def load_open_family(uiapp, project, family_document_selector,
                     overwrite_parameter_values=False):
    """Carga una familia abierta sin editarla, guardarla ni cerrar su editor.

    El proyecto de destino debe estar activo al iniciar el run_modify.
    La carga queda dentro del rollback y de la aprobacion del MCP.
    """
    _active_target(uiapp, project)
    fd = find_document(uiapp, family_document_selector)
    if project.IsFamilyDocument or not fd.IsFamilyDocument or fd.IsModifiable:
        raise DocumentOperationError('Se requiere proyecto activo y familia abierta sin transacciones.')
    loaded = fd.LoadFamily(project, _load_options(overwrite_parameter_values))
    if loaded is None:
        raise DocumentOperationError('Revit no ha cargado la familia.')
    return {'status':'loaded_in_run', 'family_id':int(loaded.Id.Value),
            'source_document':_text(fd.Title), 'saved_to_disk':False}


def edit_active_family(uiapp, editor):
    """Modifica solo la familia activa; conserva su editor y sus cambios anteriores."""
    fd = uiapp.ActiveUIDocument.Document
    _active_target(uiapp, fd)
    if not fd.IsFamilyDocument:
        raise DocumentOperationError('La familia debe estar activa.')
    t = DB.Transaction(fd, 'MCP: editar familia activa')
    t.Start()
    try:
        details = editor(fd)
        if t.Commit() != DB.TransactionStatus.Committed:
            raise DocumentOperationError('Revit no ha confirmado los cambios.')
        return {'status':'edited_in_run', 'document':fd.Title,
                'details':details, 'saved_to_disk':False}
    except BaseException:
        if t.GetStatus() == DB.TransactionStatus.Started:
            t.RollBack()
        raise


def save_document(uiapp, selector):
    """Informa del bloqueo del host; no intenta eludirlo con comandos diferidos."""
    target = find_document(uiapp, selector)
    raise DocumentOperationError('MCP bloquea Save/SaveAs en todos los runs. Guarde {0} manualmente en Revit. Esta utilidad no cambia esa politica.'.format(target.Title))


def capabilities():
    """Distingue utilidades disponibles de capacidades restringidas por el host."""
    return {'list_documents':'supported',
            'edit_and_reload_family':'run_modify; proyecto activo; familia no abierta',
            'edit_active_family':'run_modify; familia activa',
            'load_open_family':'run_modify; proyecto activo; conserva parametros por defecto',
            'activate_document':'blocked_in_current_host; manual',
            'activate_unsaved_family':'manual; no soportado por la API publica usada',
            'save_document':'blocked_by_host; manual',
            'buttons_added':False, 'core_modified':False}
