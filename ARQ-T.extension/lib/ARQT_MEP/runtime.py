"""Resolve deferred package imports after a pyRevit/MCP command restores sys.path."""
import os
import sys
from contextlib import ContextDecorator


class library_context(ContextDecorator):
    def __enter__(self):
        self.path=os.path.dirname(os.path.dirname(__file__))
        self.added=self.path not in sys.path
        if self.added:sys.path.insert(0,self.path)
        return self

    def __exit__(self,*args):
        if self.added and self.path in sys.path:sys.path.remove(self.path)
        return False

    def _recreate_cm(self):return type(self)()


class scientific_context(ContextDecorator):
    """Find optional SciPy beside the already loaded Shapely installation."""
    def __enter__(self):
        import shapely
        self.path=os.path.dirname(os.path.dirname(shapely.__file__))
        self.added=self.path not in sys.path
        if self.added:sys.path.insert(0,self.path)
        return self

    def __exit__(self,*args):
        if self.added and self.path in sys.path:sys.path.remove(self.path)
        return False

    def _recreate_cm(self):return type(self)()
