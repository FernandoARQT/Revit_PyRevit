"""Run through MCP: native WPF grouping/selection, no model transactions."""
from ARQT_MEP.routing.gui import show, _table, _pump
from ARQT_MEP.routing.origin_groups import bind_origin_groups
from System import Boolean, String
from System.Windows.Media import VisualTreeHelper, PixelFormats
from System.Windows.Controls import CheckBox
from System.Windows.Media.Imaging import RenderTargetBitmap, PngBitmapEncoder, BitmapFrame
from System.IO import FileStream, FileMode
import os

# Independent fixtures verify that a group never selects another equipment.
fixture = _table([('Device',String),('Include',Boolean),('Selectable',Boolean)], [
    dict(Device='A',Include=False,Selectable=True),
    dict(Device='A',Include=False,Selectable=True),
    dict(Device='A',Include=False,Selectable=False),
    dict(Device='B',Include=False,Selectable=True)])
view = bind_origin_groups(fixture)
assert view.Groups.Count == 2
fixture.Rows[0]['GroupSelected'] = True
assert [bool(r['Include']) for r in fixture.Rows] == [True,True,False,False]
fixture.Rows[1]['Include'] = False
assert not bool(fixture.Rows[0]['GroupSelected'])
assert bool(fixture.Rows[0]['Include'])
fixture.Rows[1]['Include'] = True
assert bool(fixture.Rows[0]['GroupSelected'])
fixture.Rows[0]['GroupSelected'] = False
assert not any(bool(r['Include']) for r in fixture.Rows)

report = {'fixture_group_isolation':True,'individual_selection_sync':True,'unavailable_excluded':True}

def descendants(parent):
    for index in range(VisualTreeHelper.GetChildrenCount(parent)):
        child=VisualTreeHelper.GetChild(parent,index)
        yield child
        for nested in descendants(child):yield nested

def loaded(window,ui,state):
    try:
        window.UpdateLayout();_pump();window.UpdateLayout()
        headers=[c for c in descendants(ui['Origins']) if isinstance(c,CheckBox)
                 and c.ToolTip and 'Seleccionar o desmarcar' in str(c.ToolTip)]
        report['groups']=len(headers)
        assert headers,'No equipment checkbox rendered'
        first=headers[0]
        name=str(first.DataContext.Name)
        first.IsChecked=True
        _pump()
        rows=[r for r in state['origin_table'].Rows if str(r['Device'])==name]
        assert all(bool(r['Include'])==bool(r['Selectable']) for r in rows)
        assert any(bool(r['Include']) for r in rows)
        available=next(r for r in rows if bool(r['Selectable']))
        available['Include']=False
        _pump()
        assert not first.IsChecked
        first.IsChecked=True;_pump()
        assert all(bool(r['Include'])==bool(r['Selectable']) for r in rows)
        first.IsChecked=False;_pump()
        assert not any(bool(r['Include']) for r in rows)
        report['native_header_binding']=True
        report['equipment']=[str(h.DataContext.Name) for h in headers]
        report['connector_count']=state['origin_table'].Rows.Count
        window.UpdateLayout()
        image=RenderTargetBitmap(int(window.ActualWidth),int(window.ActualHeight),96,96,PixelFormats.Pbgra32)
        image.Render(window)
        encoder=PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(image))
        path=os.path.join(os.path.dirname(__file__),'artifacts','routing-origin-groups.png')
        stream=FileStream(path,FileMode.Create)
        try:encoder.Save(stream)
        finally:stream.Close()
        report['image']=path
    finally:window.Close()

show(doc,uidoc,_on_loaded=loaded)
result=report
