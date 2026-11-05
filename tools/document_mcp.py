# -*- coding: utf-8 -*-
"""Entrada sin botones. workspace=carpeta tools. Usar run_modify para escrituras."""
import revit_document_utils as utils

operation = inputs.get('operation', 'capabilities')
if operation == 'capabilities':
    result = utils.capabilities()
elif operation == 'list_documents':
    result = utils.list_documents(uiapp)
elif operation == 'activate_document':
    result = utils.activate_document(uiapp, inputs['document'])
elif operation == 'load_open_family':
    if inputs.get('allow_write') is not True:
        raise utils.DocumentOperationError('Se requiere allow_write=True y run_modify.')
    result = utils.load_open_family(uiapp, doc, inputs['family_document'],
                                    inputs.get('overwrite_parameter_values', False))
elif operation == 'save_document':
    result = utils.save_document(uiapp, inputs['document'])
else:
    raise utils.DocumentOperationError('Operacion desconocida: {0}'.format(operation))
