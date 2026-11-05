"""Two-call native modeless test. No model transaction or persistent user settings."""
import copy
from System import AppDomain
from System.Windows import RoutedEventArgs,MessageBoxResult
from System.Windows.Controls import Button
from ARQT_MEP import config,service,ownership,editor_gui as gui

HOUSING='ENSAYO MODELESS MCP'


def open_fixture(doc,uidoc):
    key='ARQT_VentilationWindow_'+ownership.document_id(doc)
    if AppDomain.CurrentDomain.GetData(key) is not None:raise ValueError('Ya hay un editor abierto; no se sustituye.')
    settings=copy.deepcopy(config.DEFAULT);a=service.analyze_ventilation_rooms(doc,settings)
    settings['housing_groups']={ownership.document_id(doc):{HOUSING:[r['uid'] for r in a['rooms']]}}
    original_load=config.load;errors=[]
    class Messages:
        @staticmethod
        def Show(*args):errors.append(str(args[0]));return MessageBoxResult.No
    gui.MessageBox=Messages;config.load=lambda:copy.deepcopy(settings)
    try:state=gui.show(doc,uidoc,_modeless=True)
    finally:config.load=original_load
    state['test_errors']=errors;state['api_probe']=[]
    state['scheduler'].submit(lambda app:state['api_probe'].append(app.ActiveUIDocument.Document.Title))
    state['ui']['Optimize'].RaiseEvent(RoutedEventArgs(Button.ClickEvent))
    return {'opened':state['window'].IsVisible,'document_modified':doc.IsModified}


def state(doc):
    w=AppDomain.CurrentDomain.GetData('ARQT_VentilationWindow_'+ownership.document_id(doc))
    assert w is not None and w.Tag['ui']['Housing'].Text==HOUSING
    return w.Tag
