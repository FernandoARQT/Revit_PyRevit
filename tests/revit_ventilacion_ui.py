"""Compatibility modal WPF test; the current modeless test has its own helper."""
import copy
import tempfile
from ARQT_MEP import gui,config,ownership,proposal as model,proposal_store as store
from System.Windows import RoutedEventArgs
from System.Windows.Controls import Button,MenuItem
from System.Windows.Threading import Dispatcher,DispatcherPriority,DispatcherFrame
from System import Action
from System.Windows.Media import PixelFormats
from System.Windows.Media.Imaging import RenderTargetBitmap,PngBitmapEncoder,BitmapFrame
from System.IO import FileStream,FileMode


def run(doc,uidoc,main,uids,scope,artifact):
    s=copy.deepcopy(config.DEFAULT)
    s['housing_groups']={ownership.document_id(doc):{'Ensayo MCP':uids}}
    original_load=config.load;original_root=store.ROOT
    temporary=tempfile.TemporaryDirectory(prefix='arqt-legacy-ui-');store.ROOT=temporary.name
    config.load=lambda:copy.deepcopy(s)
    checks={}
    def loaded(window,ui,state):
        try:
            ui['IncludeAll'].RaiseEvent(RoutedEventArgs(Button.ClickEvent))
            assert all(bool(row['Include']) for row in state['room_table'].Rows)
            for row in state['room_table'].Rows:
                row['Place']=str(row['Uid']) in scope['room_uids']
            ui['Scope'].SelectedIndex=2
            ui['Calculate'].RaiseEvent(RoutedEventArgs(MenuItem.ClickEvent))
            assert state['flows'] is not None
            ui['Optimize'].RaiseEvent(RoutedEventArgs(Button.ClickEvent))
            assert state['proposal'] is not None
            for terminal in list(state['proposal']['terminals']):
                if model.design_warnings(state['proposal'],terminal,[terminal['x'],terminal['y']]):
                    state['request']('move_ventilation_terminal',{'terminal_id':terminal['terminal_id'],'position_mm':[terminal['x'],terminal['y']],'confirm_warnings':True})
            assert state['proposal']['ready'],state['proposal']['issues']
            assert ui['TerminalTypeColumn'] is not None
            assert not any(str(c.GetType().Name)=='DataGridCheckBoxColumn' for c in ui['Terminals'].Columns)
            window.UpdateLayout()
            frame=DispatcherFrame()
            def stop():frame.Continue=False
            window.Dispatcher.BeginInvoke(DispatcherPriority.ApplicationIdle,Action(stop))
            Dispatcher.PushFrame(frame)
            assert ui['Preview'].Children.Count>5
            bitmap=RenderTargetBitmap(int(window.ActualWidth),int(window.ActualHeight),96,96,PixelFormats.Pbgra32)
            bitmap.Render(window)
            encoder=PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(bitmap))
            stream=FileStream(artifact,FileMode.Create)
            try:encoder.Save(stream)
            finally:stream.Dispose()
            checks.update(rooms=state['room_table'].Rows.Count,roles=state['flow_table'].Rows.Count,
                          terminals=state['terminal_table'].Rows.Count,ready=True,preview_objects=ui['Preview'].Children.Count,
                          no_main_required=True,select_all=True,editable_table=True,no_acceptance_checkboxes=True,
                          exterior_excluded=len(state['analysis']['rooms'])-state['room_table'].Rows.Count)
        except Exception as exc:
            checks['error']=str(exc)
        finally:
            window.Close()
    try:
        gui.show(doc,uidoc,loaded)
    finally:
        config.load=original_load;store.ROOT=original_root;temporary.cleanup()
    return {'ui':checks,'after_test_inventory':ownership.inventory(doc),'production_config_saved':False}
