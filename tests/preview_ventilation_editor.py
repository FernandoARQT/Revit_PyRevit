"""Actual WPF handlers over synthetic geometry, no Revit session or model writes."""
import sys
import types
import copy
import json
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'ARQ-T.extension/lib'));sys.path.insert(0,str(ROOT/'tests'))
import ARQT_MEP
from test_ventilation_editor import fixture
from ARQT_MEP import config,proposal as model,proposal_store as store
data=fixture();settings=copy.deepcopy(config.DEFAULT)
for room in data['analysis']['rooms']:
    room['boundaries']=[{'kind':'VIRTUAL','element_id':9000,'points_mm':room['rings'][0][:2]}]
data=model.seal(data)
settings['housing_groups']={data['document_id']:{'Ensayo':[r['uid'] for r in data['analysis']['rooms']]}}
config.load=lambda:copy.deepcopy(settings)
service=types.ModuleType('ARQT_MEP.service');service.analyze_ventilation_rooms=lambda doc,s:copy.deepcopy(data['analysis'])
service.calculate_ventilation_flows=lambda a,uids,s:copy.deepcopy(data['flows'])
read=types.ModuleType('ARQT_MEP.revit_read');read.eid=lambda v:int(v)
own=types.ModuleType('ARQT_MEP.ownership');own.document_id=lambda doc:data['document_id'];own.inventory=lambda *args:[]
write=types.ModuleType('ARQT_MEP.revit_write')
for module in (service,read,own,write):sys.modules[module.__name__]=module
from ARQT_MEP import editor_gui as gui
from ARQT_MEP.preview_image import export
from System.Windows import RoutedEventArgs,Point,MessageBoxResult
from System.Windows.Controls import Button,MenuItem,DataGridCellInfo,DataGridEditingUnit,TextBox,ComboBox
from System.Windows.Media import PixelFormats
from System.Windows.Media.Imaging import RenderTargetBitmap,PngBitmapEncoder,BitmapFrame
from System.IO import FileStream,FileMode
errors=[];report={}
class Messages:
    @staticmethod
    def Show(*args):
        if str(args[0]).startswith('Propuesta de reequilibrado') or '¿Mantener esta función/posición provisional?' in str(args[0]):return MessageBoxResult.Yes
        errors.append(str(args[0]));return MessageBoxResult.No
gui.MessageBox=Messages
class Document:Title='Ensayo editor · datos ficticios';IsFamilyDocument=False
def create(doc,uids,s,scope,housing):
    p=copy.deepcopy(data);p['housing_id']=housing;return store.create(model.seal(p))
gui.adapter.create=create;gui.adapter.current=lambda *args:None
def click(button):button.RaiseEvent(RoutedEventArgs(MenuItem.ClickEvent if isinstance(button,MenuItem) else Button.ClickEvent))
def pump(window):
    from System.Windows.Threading import DispatcherFrame,DispatcherPriority,Dispatcher
    from System import Action
    frame=DispatcherFrame();window.Dispatcher.BeginInvoke(DispatcherPriority.Background,Action(lambda:setattr(frame,'Continue',False)));Dispatcher.PushFrame(frame);window.UpdateLayout()
class Mouse:
    def __init__(self,xy):self.xy=xy;self.Handled=False;self.ClickCount=1;self.Delta=120
    def GetPosition(self,canvas):return Point(*self.xy)
def loaded(window,ui,state):
    try:
        click(ui['Optimize']);assert state['proposal'] is not None,errors
        assert any(getattr(child,'StrokeDashArray',None) is not None and child.StrokeDashArray.Count==2 for child in ui['Preview'].Children), 'No se han dibujado los separadores virtuales.'
        window.UpdateLayout();pump(window);pump(window);plan=state['plan'];plan.fit();p=state['proposal'];tid=p['terminals'][0]['terminal_id']
        original_count=len(p['terminals']);start=plan.transform.to_screen((p['terminals'][0]['x'],p['terminals'][0]['y']));end=plan.transform.to_screen((3423.45,2871.23))
        plan.down(ui['Preview'],Mouse(start));plan.move(ui['Preview'],Mouse(end));plan.up(ui['Preview'],Mouse(end))
        assert abs(model.terminal(state['proposal'],tid)['x']-3423.45)<1e-6,errors
        assert model.terminal(state['proposal'],tid)['position_manual']
        before=copy.deepcopy(state['proposal']['terminals']);plan.wheel(ui['Preview'],Mouse((300,200)));window.Width+=80;window.Height+=20;pump(window)
        assert before==state['proposal']['terminals']
        click(ui['AddSupply']);point=plan.transform.to_screen((4500,4500));plan.down(ui['Preview'],Mouse(point));assert len(state['proposal']['terminals'])==original_count+1,errors
        added=next(t for t in state['proposal']['terminals'] if t['terminal_id'].startswith('manual_'))
        plan.select(added['terminal_id']);click(ui['DeleteTerminal']);assert len(state['proposal']['terminals'])==original_count
        click(ui['Undo']);assert len(state['proposal']['terminals'])==original_count+1
        click(ui['Redo']);assert len(state['proposal']['terminals'])==original_count
        def edit_cell(tid,column,value,while_editing=None):
            plan.select(tid);grid=ui['Terminals'];item=grid.SelectedItem
            grid.CurrentCell=DataGridCellInfo(item,grid.Columns[column]);grid.ScrollIntoView(item,grid.Columns[column]);grid.Focus()
            assert grid.BeginEdit(),'No se pudo editar la celda.'
            pump(window)
            editor=grid.Columns[column].GetCellContent(item)
            if isinstance(editor,ComboBox):editor.SelectedItem=value
            else:
                assert isinstance(editor,TextBox),str(type(editor));editor.Text=value
            if while_editing:while_editing()
            grid.CommitEdit(DataGridEditingUnit.Cell,True);grid.CommitEdit(DataGridEditingUnit.Row,True);pump(window);pump(window)
        plan.select(tid);edit_cell(tid,3,'25');assert model.terminal(state['proposal'],tid)['flow']==25,errors
        click(ui['Undo']);before_y=model.terminal(state['proposal'],tid)['y'];edit_cell(tid,4,'3487,321')
        assert model.terminal(state['proposal'],tid)['x']==3487.321
        assert model.terminal(state['proposal'],tid)['y']==before_y
        click(ui['Undo'])
        mixed=next(t for t in state['proposal']['terminals'] if t['room_uid']=='3' and t['role']=='impulsion')
        edit_cell(mixed['terminal_id'],2,'Extracción');assert model.terminal(state['proposal'],mixed['terminal_id'])['role']=='extraccion',errors
        click(ui['Undo']);plan.select(tid)
        p=state['proposal'];screen=plan.transform.to_screen((model.terminal(p,tid)['x'],model.terminal(p,tid)['y']))
        double=Mouse(screen);double.ClickCount=2;plan.down(ui['Preview'],double)
        assert state['cell_edit'] is not None,'Doble clic no activó edición de caudal.'
        ui['Terminals'].CancelEdit();state['cell_edit']=None
        assert len([c for c in ui['Terminals'].Columns if str(c.GetType().Name)=='DataGridCheckBoxColumn'])==0
        edit_cell(tid,3,'25');assert model.terminal(state['proposal'],tid)['flow']==25
        version=state['proposal']['version'];edit_cell(tid,3,'-1')
        assert state['proposal']['version']==version and model.terminal(state['proposal'],tid)['flow']==25
        assert 'positivo' in ui['Issues'].Text
        def concurrent_edit():
            p=state['proposal'];store.update(p['document_id'],p['proposal_id'],p['version'],'set_terminal_flow',{'terminal_id':tid,'flow_lps':26})
            before=state['proposal']['version'];state['poll']();assert state['proposal']['version']==before
        edit_cell(tid,3,'27',concurrent_edit)
        assert model.terminal(state['proposal'],tid)['flow']==26 and 'VERSION_CONFLICT' in ui['Issues'].Text
        click(ui['Undo'])
        click(ui['Undo']);p=state['proposal']
        update=store.update(p['document_id'],p['proposal_id'],p['version'],'set_terminal_flow',{'terminal_id':tid,'flow_lps':22})
        state['poll']();assert state['proposal']['version']==update['version'];assert model.terminal(state['proposal'],tid)['flow']==22
        click(ui['Undo']);click(ui['Rebalance']);assert state['proposal']['validation']['valid'],state['proposal']['validation']
        ui['Tabs'].SelectedIndex=2;window.UpdateLayout();pump(window);pump(window)
        artifact=ROOT/'tests/artifacts';artifact.mkdir(exist_ok=True)
        capture=export(state['proposal'],1,str(artifact/'ventilation-editor-plan.png'))
        bmp=RenderTargetBitmap(int(window.ActualWidth),int(window.ActualHeight),96,96,PixelFormats.Pbgra32);bmp.Render(window)
        encoder=PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(bmp));stream=FileStream(str(artifact/'ventilation-editor-ui.png'),FileMode.Create)
        try:encoder.Save(stream)
        finally:stream.Dispose()
        report.update(drag=True,continuous_coordinates=True,zoom_and_resize_preserve_data=True,add_delete=True,undo_redo=True,
            flow_edit=True,table_flow=True,table_coordinates=True,table_type=True,double_click_edit=True,no_acceptance_checkboxes=True,
            invalid_cell_reverts=True,concurrent_cell_conflict_preserves_external_edit=True,
            external_store_sync=True,terminals=original_count,capture=capture,width=window.ActualWidth,height=window.ActualHeight,errors=errors)
    except Exception as exc:
        import traceback
        errors.append(traceback.format_exc())
    finally:window.Close()
with tempfile.TemporaryDirectory(dir=str(ROOT/'tests/artifacts'),prefix='editor-ui-store-') as temp:
    store.ROOT=temp;gui.show(Document(),None,loaded)
(ROOT/'tests/artifacts/ventilation-editor-ui.json').write_text(json.dumps(report or {'errors':errors},ensure_ascii=False,indent=2),encoding='utf8')
assert not errors,errors
