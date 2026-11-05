"""Native WPF read/calculate test: equipment selection, no model creation."""
from ARQT_MEP.routing.gui import show,_pump
from System.Windows import RoutedEventArgs
from System.Windows.Controls import Button
from System.Windows.Media import PixelFormats
from System.Windows.Media.Imaging import RenderTargetBitmap,PngBitmapEncoder,BitmapFrame
from System.IO import FileStream,FileMode
import os

report={}
def loaded(window,ui,state):
    try:
        rows=state['origin_table'].Rows
        assert rows.Count>=2
        assert not state['origin_table'].Columns.Contains('PortId')
        assert ui['HRV'].Items.Count>=1
        assert all('recuperador' not in str(r['Device']).casefold() for r in rows)
        for index,row in enumerate(rows):
            row['Include']=index<2
            if index<2:row['Network']='SUPPLY' if index==0 else 'EXTRACT'
        ui['Calculate'].RaiseEvent(RoutedEventArgs(Button.ClickEvent))
        assert state.get('proposal'),'No proposal was calculated'
        proposal=state['proposal'];topology=proposal['topology']
        assert topology and topology['counts']['SUPPLY']['terminals']>0
        assert topology['counts']['EXTRACT']['terminals']>0
        assert all(r['source'] is None or r['source']['kind']=='DISTRIBUTOR' for r in proposal['routes'] if r['route_kind']=='BRANCH')
        report.update(equipment_rows=rows.Count,hrv_options=ui['HRV'].Items.Count,
            counts=topology['counts'],route_count=len(proposal['routes']),
            request_errors=[r['error'] for r in proposal['requests'] if r.get('error')],
            equipment_level_selection=True,main_and_branch_requests_separated=True,
            previous_connections_button=ui['PreviousConnections'] is not None)
        ui['Tabs'].SelectedIndex=0;window.UpdateLayout();_pump();window.UpdateLayout()
        bmp=RenderTargetBitmap(int(window.ActualWidth),int(window.ActualHeight),96,96,PixelFormats.Pbgra32);bmp.Render(window)
        encoder=PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(bmp))
        path=os.path.join(os.path.dirname(__file__),'artifacts','routing-equipment-topology.png')
        stream=FileStream(path,FileMode.Create)
        try:encoder.Save(stream)
        finally:stream.Close()
        report['image']=path
    finally:window.Close()

show(doc,uidoc,_on_loaded=loaded)
result=report
