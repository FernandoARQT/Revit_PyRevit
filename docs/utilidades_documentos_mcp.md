# Utilidades de documentos y familias sin botones

Los archivos `tools/revit_document_utils.py` y `tools/document_mcp.py` se importan
desde el MCP existente. No registran botones, eventos ni otro servidor. No cambian
la instalacion de pyRevit ni su politica de seguridad.

## Operaciones disponibles

| Funcion | Condicion |
|---|---|
| `list_documents(uiapp)` | Consulta; devuelve identificadores de sesion y estado. |
| `edit_and_reload_family(uiapp, doc, family_id, editor)` | Proyecto activo; familia no abierta previamente. Abre una copia temporal, ejecuta el callback dentro de una transaccion, recarga y cierra la copia sin guardar. |
| `edit_active_family(uiapp, editor)` | Familia activa; conserva el editor y sus cambios anteriores. |
| `load_open_family(uiapp, doc, selector)` | Proyecto activo; carga una familia abierta sin modificar ni cerrar su editor. |
| `activate_document(uiapp, selector)` | Solo confirma si ya esta activo. El cambio de pestana esta bloqueado tras fallar la prueba de API publica en este host. |
| `save_document(uiapp, selector)` | Devuelve un error explicito. Save/SaveAs estan bloqueados por el servidor. Guardado manual. |

`family_id` admite identificadores de Family, FamilySymbol o FamilyInstance.
`selector` admite titulo exacto, ruta o `session_id` de la sesion actual; rechaza
coincidencias ambiguas y documentos vinculados.

Las funciones de escritura se llaman con `run_modify`, primero `dry_run=true`.
El callback no debe guardar, crear eventos diferidos ni abrir transacciones propias.
Por defecto se conservan los valores de parametros existentes al recargar.
La aprobacion de MCP decide si la carga se conserva. `loaded_in_run` indica que la
API realizo la carga provisional; no equivale a un commit confirmado por el host.

## Llamadas desde MCP

Usar `workspace` con la ruta absoluta de `tools`. Consultas en CPython; para
recargas usar IronPython porque el callback IFamilyLoadOptions de pyRevit esta
implementado para ese motor.

```python
import revit_document_utils as u
result = u.list_documents(uiapp)
```

Editar y recargar en una sola llamada, sin cambiar de pestana:

```python
import revit_document_utils as u

def editar(fd):
    # Aqui se introduce exclusivamente la modificacion autorizada y verificada.
    return {'verified': True}

operation = u.edit_and_reload_family(uiapp, doc, inputs['family_id'], editar)
result = {'status': operation['status'], 'details': operation['details']}
```

Cargar una familia que ya esta abierta, con el proyecto activo:

```python
import revit_document_utils as u
operation = u.load_open_family(uiapp, doc, inputs['family_document'])
result = {'status': operation['status'], 'family_id': operation['family_id']}
```

No se reutiliza ni se cierra automaticamente un editor de familia preexistente.
Si esa familia esta abierta, se edita con ella activa y se recarga en una segunda
llamada cuando el proyecto este activo. Los cambios de pestana son manuales.

## Verificacion en Revit 2025

- Ciclo temporal sin edicion: `0e1f89e3e00a`, carga aceptada por API y dos documentos
  abiertos antes/despues, sin cambios.
- Edicion real de configuracion del conector y recarga: `e4c110eaccbc`,
  `decision=rolled_back`. El host detecto la carga y la revirtio. La copia temporal
  quedo cerrada; el proyecto siguio `modified=false` y el editor del distribuidor
  preexistente siguio abierto con `modified=true`.
- Consulta de metadatos con acentos: `946ac7567b24`, CPython, correcta.
- Cambio de pestana: prueba `f092598efe15`, la API devolvio error interno.
  La utilidad queda deshabilitada para ese caso; no se reintenta ni difiere.

El host instalado presenta un problema de serializacion de cadenas con acentos
en resultados de IronPython. Para las operaciones de escritura devolver IDs,
estados ASCII y resultados numericos, como en los ejemplos; consultar nombres
completos en una llamada CPython posterior.

Estas son utilidades invocables mediante `run_query`/`run_modify`, no nombres nuevos
en el catalogo MCP. Para registrar herramientas nuevas y permitir cambios de
documento o guardado hay que desarrollar soporte explicito en el servidor/host.
