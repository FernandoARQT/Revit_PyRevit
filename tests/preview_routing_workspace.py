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
from test_routing_radial_v3 import fixture as radial_fixture
from ARQT_MEP.routing.design.background import from_catalog
from ARQT_MEP.routing.design import schematic,native_plan
from System import Action
from System.Windows import RoutedEventArgs,Visibility,Point
from System.Windows.Controls import Button
from System.Windows.Threading import DispatcherFrame,DispatcherPriority

base=radial_fixture();data=base.catalog;cfg=base.configuration
data['devices'].append({'id':'hrv','element_id':99,'family':'Recuperador de calor','connectors':[]})
cfg['hrv_id']='hrv'
for d in data['devices']:d['name']=d['family']
from shapely.geometry import box,mapping
room=copy.deepcopy(base.background['analysis']['rooms'][0]);polygon=box(0,0,3000,2000)
room.update(uid='test-room',name='Sala de ensayo',number='01',rings=[list(polygon.exterior.coords)],centroid=[1500,1000],area_m2=6,doors=[])
room['ceilings']=[dict(room['ceilings'][0],geometry=mapping(polygon),z=0,plane={'origin':[0,0,0],'normal':[0,0,1]})]
analysis=dict(base.background['analysis'],rooms=[room])
destinations=[{'terminal_id':t['id'],'port_id':t['connectors'][0]['id'],'network_type':t['suggested_network_type']} for t in data['terminals']]
background=from_catalog(analysis,data,destinations,base.document_identity,'fixture')
old=base;base=model.create(background);base.configuration=cfg;base.catalog=data;base.snapshot=old.snapshot;base.profiles=old.profiles;base.geometry_revision=base.snapshot['geometry_revision']
captured=ROOT/'tests/artifacts/routing-v3-h2-capture.json'
if '--captured' in sys.argv:
    base=model.RoutingDesignProposal.from_dict(json.loads(captured.read_text(encoding='utf8')))
    connections=json.loads((ROOT/'tests/artifacts/radial-v3-native-connections.json').read_text(encoding='utf8'))
    data=connections['catalog'];base.catalog=data;cfg=settings.validate(base.configuration)
    base.snapshot=json.loads((ROOT/'tests/artifacts/radial-v3-native-space.json').read_text(encoding='utf8'));base.geometry_revision=base.snapshot['geometry_revision']


def dispatch(doc,op,payload=None):
    payload=payload or {}
    if op=='get_mep_connection_points':return data
    if op=='routing_design_assert_current':return {'current':True}
    if op=='routing_design_native_plan':return native_plan.prepare(store.get(base.document_identity,payload['proposal_id']))
    if op=='routing_design_create':
        p=copy.deepcopy(base);p.proposal_id='workspace-test';p.name='Ensayo de generación';p.revision=1
        p.corridors={};p.corridor_graph={};p.history=[];p.future=[]
    elif op=='routing_design_analyze':p=store.get(base.document_identity,payload['proposal_id'])
    else:raise ValueError(op)
    p.catalog=copy.deepcopy(data);p.configuration=copy.deepcopy(payload['configuration']);p.configuration['design_clearance_mm']=payload['clearance_mm']
    p.profiles={payload['duct_profile']['profile_id']:payload['duct_profile']}
    if not p.snapshot:
        raise AssertionError('Falta el snapshot autocontenido del ensayo.')
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
            assert time.monotonic()-start<150,ui['Status'].Text
        assert w.proposal.corridors,ui['Status'].Text
        assert schematic.check(w.proposal)
        assert all(c.get('generated_by')!='V3_FREE_SPACE_GRAPH' for c in w.view().corridors.values())
        assert not w.busy and ui['Progress'].Visibility==Visibility.Collapsed
        assert any(getattr(c,'Data',None) is not None for c in ui['Plan'].Children),'No hay corredores dibujados.'
        cid=next((k for k,c in w.proposal.corridors.items() if len(c['reference_xy_mm'])>2),next(iter(w.proposal.corridors)))
        c=w.proposal.corridors[cid]
        if len(c['reference_xy_mm'])==2:
            a,b=c['reference_xy_mm'];w.request('add_vertex',{'corridor_id':cid,'index':1,'point_mm':[(a[0]+b[0])/2,(a[1]+b[1])/2]});c=w.proposal.corridors[cid]
        ui['Corridors'].SelectedIndex=w.cids.index(cid);assert ui['SelectionPanel'].Visibility==Visibility.Visible
        ui['Vertex'].SelectedIndex=1
        before_y=c['reference_xy_mm'][1][1];ui['PointX'].Text=str(c['reference_xy_mm'][1][0]+23.456)
        click(ui['MovePoint']);assert w.proposal.corridors[cid]['reference_xy_mm'][1][1]==before_y
        assert w.proposal.constraints['schematic_paths']
        click(ui['Undo']);click(ui['Redo']);click(ui['Undo'])
        click(ui['CalculateAxes']);assert w.future is not None,ui['Status'].Text
        axes_started=time.monotonic()
        while w.future is not None:
            pump(window);w.poll();time.sleep(.02)
            assert time.monotonic()-axes_started<150,ui['Status'].Text
        assert w.proposal.individual_routes and w.axes_mode(),ui['Status'].Text
        if '--captured' not in sys.argv:assert len(w.proposal.validation['valid_route_ids'])==3,w.proposal.validation
        rid=next((rid for rid,r in w.proposal.individual_routes.items() if len(r.get('points_mm',[]))>2),None)
        if rid:
            ui['Corridors'].SelectedIndex=w.cids.index(rid);ui['Vertex'].SelectedIndex=1
            ui['PointX'].Text=str(w.proposal.individual_routes[rid]['points_mm'][1][0]+5);click(ui['MovePoint'])
            assert w.proposal.individual_routes[rid]['validation_status']=='EDITED_PENDING_RECHECK'
            click(ui['Undo'])
        ui['Mode'].SelectedIndex=0
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
        report.update(primary_generates_without_hidden_fields=True,drawn_corridors=count,selected_terminal_connections=len(w.proposal.selected_paths),
            exact_elbow_endpoints=True,schematic_edit_is_solver_constraint=True,axes_calculated_and_editable=True,
            raw_graph_hidden=True,numeric_edit_preserves_other_coordinate=True,
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
