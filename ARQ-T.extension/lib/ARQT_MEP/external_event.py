"""CPython modeless API queue; pyRevit's maintained helper rejects CPython."""
import uuid
from collections import deque
from pyrevit.api import UI


class ApiQueue(UI.IExternalEventHandler):
    __namespace__='ARQT.Ventilation.Q'+uuid.uuid4().hex

    def __init__(self):
        self.callbacks=deque();self.event=UI.ExternalEvent.Create(self)

    def Execute(self,uiapp):
        while self.callbacks:
            callback=self.callbacks.popleft();callback(uiapp)

    def GetName(self):return 'ARQ-T | Editor de ventilación'

    def submit(self,callback):
        self.callbacks.append(callback);status=self.event.Raise()
        if status in (UI.ExternalEventRequest.Denied,UI.ExternalEventRequest.TimedOut):
            self.callbacks.remove(callback);raise ValueError('Revit no acepta la solicitud API: '+str(status))

    def close(self):self.event.Dispose();self.callbacks.clear()
