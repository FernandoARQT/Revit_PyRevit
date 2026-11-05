"""WPF executable test with explicitly synthetic inputs, without a Revit session."""
import sys
import json
import copy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ARQ-T.extension/lib'))
sys.path.insert(0,str(ROOT/'tests'))
from test_routing_core import scene,configuration
from ARQT_MEP.routing import gui,settings,service
from System.Windows import RoutedEventArgs
from System.Windows.Controls import Button
from System.Windows.Media import PixelFormats
from System.Windows.Media.Imaging import RenderTargetBitmap,PngBitmapEncoder,BitmapFrame
from System.IO import FileStream,FileMode

configuration=configuration();settings.load=lambda:copy.deepcopy(configuration)
catalog={'devices':[],'terminals':[],'warnings':[]}
def dispatch(doc,operation,payload=None,**kwargs):
    if operation=='get_heat_recovery_equipment_assignments':return {}
    if operation=='get_mep_connection_points':return catalog
    if operation=='analyze_mep_geometry':
        data=scene();data.update(stats={'elements':0,'triangles':0,'region_count':1,'extraction_seconds':0},warnings=[]);return data
    if operation=='create_mep_route_proposal':
        return service.create_mep_route_proposal(scene(),catalog,payload['origins'],payload['destinations'],payload['configuration'])
    raise ValueError(operation)
gui.mcp.dispatch=dispatch
class Document:Title='Ensayo de interfaz · datos ficticios';IsFamilyDocument=False
report={}
def loaded(window,ui,state):
    try:
        assert state['origin_table'].Rows.Count==0 and state['destination_table'].Rows.Count==0
        ui['ManualOrigins'].Text=json.dumps([{'id':'manual-S','xyz_mm':[200,500,250],'network_type':'SUPPLY','kind':'DISTRIBUTOR'}])
        ui['ManualDestinations'].Text=json.dumps([{'id':'manual-dest','xyz_mm':[2800,500,250],'network_type':'SUPPLY','airflow_lps':8}])
        ui['Calculate'].RaiseEvent(RoutedEventArgs(Button.ClickEvent))
        assert state['proposal']['summary']['valid']==1
        assert state['route_table'].Rows.Count==1
        assert ui['Routes'].SelectedIndex==0
        assert 'airflow_lps' in ui['Properties'].Text
        window.UpdateLayout();gui._pump();window.UpdateLayout();gui._pump();window.UpdateLayout()
        bmp=RenderTargetBitmap(int(window.ActualWidth),int(window.ActualHeight),96,96,PixelFormats.Pbgra32);bmp.Render(window)
        encoder=PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(bmp))
        path=ROOT/'tests/artifacts/routing-ui.png';stream=FileStream(str(path),FileMode.Create)
        try:encoder.Save(stream)
        finally:stream.Close()
        report.update(rows=1,calculation=True,route_properties=True,width=window.ActualWidth,height=window.ActualHeight,
                      columns=[{'header':str(c.Header),'width':c.ActualWidth} for c in ui['Routes'].Columns])
        (ROOT/'tests/artifacts/routing-ui.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    finally:window.Close()
gui.show(Document(),None,_on_loaded=loaded)
print(json.dumps(report))
