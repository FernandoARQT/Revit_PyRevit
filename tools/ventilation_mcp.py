"""Script entry for existing MCP tools; inputs contains operation and payload.

workspace must be ARQ-T.extension/lib. For write operations run_modify is required
and inputs.allow_write must explicitly be True. All operations share ARQT_MEP.
"""
from ARQT_MEP.integration import dispatch
result=dispatch(doc,inputs['operation'],inputs.get('payload',{}),inputs.get('allow_write',False))
