"""End-to-end production WPF flow; native reading replaced by a saved capture."""
import copy
import json
import sys
import tempfile
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ARQ-T.extension/lib'));sys.path.insert(0,str(ROOT/'tests'))
from ARQT_MEP.routing.design import workspace,model,store
from ARQT_MEP.routing import settings
from test_routing_design import proposal
from test_routing_core import scene
from test_routing_workflow import catalog
from System import Action
from System.Windows import RoutedEventArgs,Visibility,Point
from System.Windows.Controls import Button
from System.Windows.Threading import DispatcherFrame,DispatcherPriority

cfg=settings.validate({'section':{'outer_diameter_mm':50,'nominal_diameter_mm':50},'bend_radius_mm':100,'clearance_mm':10})
data=catalog()
for d in data['devices']:d['name']=d['family'];d['connectors']=[]
data['terminals']=[{'id':'terminal','element_id':10,'suggested_network_type':'SUPPLY','airflow_lps':8,
    'connectors':[{'id':'port','connector_id':1,'xyz_mm':[2000,1000,100]}]}]
base=proposal();base.terminals={'terminal':{'terminal_id':'terminal','network_type':'SUPPLY','position_mm':[2000,1000,100],
    'level_id':1,'flow_lps':8,'existing_uid':'terminal'}}
captured=ROOT/'tests/artifacts/routing-v3-h2-capture.json'
if '--captured' in sys.argv:
    base=model.RoutingDesignProposal.from_dict(json.loads(captured.read_text(encoding='utf8')))
    data=copy.deepcopy(base.catalog);cfg=settings.validate(base.configuration)


def dispatch(doc,op,payload=None):
    payload=payload or {}
    if op=='get_mep_connection_points':return data
    if op=='routing_design_create':
        p=copy.deepcopy(base);p.proposal_id='workspace-test';p.name='Ensayo de generación';p.revision=1
        p.corridors={};p.corridor_graph={};p.history=[];p.future=[]
    elif op=='routing_design_analyze':p=store.get(base.document_identity,payload['proposal_id'])
    else:raise ValueError(op)
    p.configuration=copy.deepcopy(payload['configuration']);p.configuration['design_clearance_mm']=payload['clearance_mm']
    p.profiles={payload['duct_profile']['profile_id']:payload['duct_profile']}
    if not p.snapshot:
        p.snapshot=scene();p.snapshot['document_identity']=p.document_identity;p.snapshot['regions'][0]['level_id']=1
    p.geometry_revision=p.snapshot['geometry_revision']
    if op=='routing_design_create':return store.create(p).to_dict()
    return store.capture(p.document_identity,p.proposal_id,p.revision,p.snapshot,data,p.configuration,model.DuctProfile(**payload['duct_profile'])).to_dict()


def pump(window):
    frame=DispatcherFrame();window.Dispatcher.BeginInvoke(DispatcherPriority.Background,Action(lambda:setattr(frame,'Continue',False)))
    from System.Windows.Threading import Dispatcher
    Dispatcher.PushFrame(frame);window.UpdateLayout()


def click(control):control.RaiseEvent(RoutedEventArgs(Button.ClickEvent))


class Mouse:
    def __init__(self,xy):self.xy=xy;self.Handled=False;self.ClickCount=1;self.Delta=120
    def GetPosition(self,canvas):return Point(*self.xy)


errors=[];report={}
class Document:Title='Ensayo trazador · captura guardada' if '--captured' in sys.argv else 'Ensayo trazador · geometría sintética';IsFamilyDocument=False


def loaded(window,ui,state):
    w=state['workspace']
    try:
        assert window.FindName('ManualOrigins') is None and window.FindName('Tabs') is None
        assert ui['OuterDiameter'].Text and ui['BendRadius'].Text
        assert ui['SelectionPanel'].Visibility==Visibility.Collapsed
        click(ui['Generate']);assert w.busy and w.future is not None,ui['Status'].Text
        start=time.monotonic()
        while w.future is not None:
            pump(window);w.poll();time.sleep(.02)
            assert time.monotonic()-start<75,ui['Status'].Text
        assert w.proposal.corridors,ui['Status'].Text
        assert not w.busy and ui['Progress'].Visibility==Visibility.Collapsed
        assert any(getattr(c,'Data',None) is not None for c in ui['Plan'].Children),'No hay corredores dibujados.'
        cid=next(iter(w.proposal.corridors));c=w.proposal.corridors[cid]
        ui['Corridors'].SelectedIndex=w.cids.index(cid);assert ui['SelectionPanel'].Visibility==Visibility.Visible
        before_y=c['reference_xy_mm'][0][1];ui['PointX'].Text=str(c['reference_xy_mm'][0][0]+23.456)
        click(ui['MovePoint']);assert w.proposal.corridors[cid]['reference_xy_mm'][0][1]==before_y
        click(ui['Undo']);click(ui['Redo']);click(ui['Undo'])
        w.plan.fit();w.plan.wheel(ui['Plan'],Mouse((300,200)));w.plan.transform.shift(25,-18);w.plan.draw()
        assert w.proposal.corridors
        w.plan.fit();window.UpdateLayout();pump(window)
        from System.Windows.Media import PixelFormats
        from System.Windows.Media.Imaging import RenderTargetBitmap,PngBitmapEncoder,BitmapFrame
        from System.IO import FileStream,FileMode
        bitmap=RenderTargetBitmap(int(window.ActualWidth),int(window.ActualHeight),96,96,PixelFormats.Pbgra32);bitmap.Render(window)
        encoder=PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(bitmap))
        path=ROOT/'tests/artifacts'/('routing-workspace-captured.png' if '--captured' in sys.argv else 'routing-workspace.png')
        stream=FileStream(str(path),FileMode.Create)
        try:encoder.Save(stream)
        finally:stream.Dispose()
        count=len(w.proposal.corridors);w.new();assert w.proposal is None
        w.list_studies();assert w.proposal and len(w.proposal.corridors)==count
        report.update(primary_generates_without_hidden_fields=True,drawn_corridors=count,numeric_edit_preserves_other_coordinate=True,
            undo_redo=True,zoom_pan=True,recover=True,old_json_controls_removed=True,selected_properties_only=True,
            source='SAVED_NATIVE_CAPTURE' if '--captured' in sys.argv else 'SYNTHETIC_FIXTURE',seconds=time.monotonic()-start,image=str(path))
    except Exception:
        import traceback
        errors.append(traceback.format_exc())
    finally:
        w.cancel_event.set()
        if w.future:
            deadline=time.monotonic()+8
            while not w.future.done() and time.monotonic()<deadline:time.sleep(.05)
        window.Close()


old_root=store.ROOT
try:
    with tempfile.TemporaryDirectory(prefix='arqt-workspace-ui-') as folder:
        store.ROOT=folder
        workspace.show(Document(),None,loaded,_context={'identity':base.document_identity,'configuration':cfg,'catalog':data,'dispatch':dispatch})
finally:store.ROOT=old_root
(ROOT/'tests/artifacts/routing-workspace-ui.json').write_text(json.dumps({'report':report,'errors':errors},ensure_ascii=False,indent=2),encoding='utf8')
assert not errors,errors
print(json.dumps(report))
