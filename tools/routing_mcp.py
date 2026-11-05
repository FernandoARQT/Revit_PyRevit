"""Entry script for the existing MCP. workspace = ARQ-T.extension/lib."""
from ARQT_MEP.routing.mcp import dispatch
result=dispatch(doc,inputs['operation'],inputs.get('payload',{}),inputs.get('allow_write',False))
