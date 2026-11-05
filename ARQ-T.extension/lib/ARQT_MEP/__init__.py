"""Motor compartido ARQ-T MEP para pyRevit y el despachador MCP existente."""
import os
import sys

try:
    import shapely
except ImportError:
    candidate = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs',
                             'Python', 'Python312', 'Lib', 'site-packages')
    if os.path.isdir(candidate):
        sys.path.append(candidate)
    import shapely
