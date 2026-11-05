"""Native simplified GUI flow. Invoke only from run_modify(dry_run=True)."""
import copy
import json
import os
import tempfile
from ARQT_MEP import config,editor_gui as gui,proposal_store as store,ownership,proposal as model
from System.Windows import RoutedEventArgs,MessageBoxResult
from System.Windows.Controls import Button


def run(doc,uidoc,uids,artifacts):
    previous_load=config.load;previous_root=store.ROOT;previous_message=gui.MessageBox
    settings=copy.deepcopy(config.DEFAULT);settings['housing_groups']={ownership.document_id(doc):{'ENSAYO UI SIMPLE':uids}}
    config.load=lambda:copy.deepcopy(settings)
    report={};messages=[];errors=[]
    class Messages:
        @staticmethod
        def Show(*args):
            message=str(args[0]);messages.append(message)
            if message.startswith('Se aplicarán '):return MessageBoxResult.Yes
            if 'terminales aplicados y validados' not in message:errors.append(message)
            return MessageBoxResult.OK
    gui.MessageBox=Messages
    def loaded(window,ui,state):
        try:
            ui['Optimize'].RaiseEvent(RoutedEventArgs(Button.ClickEvent));assert state['proposal'] is not None
            p=state['proposal'];assert any(model.design_warnings(p,t,[t['x'],t['y']]) for t in p['terminals'])
            assert not any(t.get('accepted') for t in p['terminals'])
            ui['Place'].RaiseEvent(RoutedEventArgs(Button.ClickEvent))
            assert state['proposal']['status']=='PLACED',errors
            assert len(ownership.inventory(doc))==len(p['terminals'])
            assert len([m for m in messages if m.startswith('Se aplicarán ')])==1
            report.update(placed=len(p['terminals']),single_confirmation=True,whole_proposal_approval=True,
                          checked_native_solids=True,checkbox_columns=0,errors=errors)
        except Exception:
            import traceback
            errors.append(traceback.format_exc())
        finally:window.Close()
    try:
        with tempfile.TemporaryDirectory(prefix='arqt-simple-ui-') as temporary:
            store.ROOT=temporary;gui.show(doc,uidoc,loaded)
    finally:config.load=previous_load;store.ROOT=previous_root;gui.MessageBox=previous_message
    assert not errors,errors
    with open(os.path.join(artifacts,'ventilation-simple-native-ui.json'),'w',encoding='utf8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    return report
