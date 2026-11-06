"""Bounded production UI test; all native actions are read-only."""
import copy
import json
import tempfile
import time
from pathlib import Path
from ARQT_MEP import revit_read
from ARQT_MEP.routing import gui
from ARQT_MEP.routing.design import workspace,store


def run(doc,uidoc,artifacts):
    before=revit_read.fingerprint(doc);errors=[];report={};old_root=store.ROOT
    try:
        with tempfile.TemporaryDirectory(prefix='arqt-workspace-native-') as folder:
            store.ROOT=folder
            def loaded(window,ui,state):
                w=state['workspace']
                try:
                    assert window.FindName('Tabs') is None and window.FindName('ManualOrigins') is None
                    assert ui['OuterDiameter'].Text and ui['BendRadius'].Text
                    w.generate();started=time.monotonic()
                    while w.future is not None:
                        gui._pump();w.poll();time.sleep(.03)
                        if time.monotonic()-started>85:raise TimeoutError('El cálculo no terminó dentro del límite del ensayo.')
                    assert w.proposal and w.proposal.corridors,ui['Status'].Text
                    graph_stats=copy.deepcopy(w.proposal.corridor_graph['stats']);corridor_count=len(w.proposal.corridors)
                    # Exercise the actual production Cancel action and keep the
                    # preceding valid study, rather than replacing it by empty data.
                    w.generate();w.cancel();started=time.monotonic()
                    while w.future is not None:
                        gui._pump();w.poll();time.sleep(.03)
                        if time.monotonic()-started>15:raise TimeoutError('La cancelación no respondió.')
                    assert len(w.proposal.corridors)==corridor_count
                    cid=next(iter(w.proposal.corridors));ui['Corridors'].SelectedIndex=w.cids.index(cid)
                    before_xy=list(w.proposal.corridors[cid]['reference_xy_mm'][0]);ui['PointX'].Text=str(before_xy[0]+.125)
                    w.move_point();assert w.proposal.corridors[cid]['reference_xy_mm'][0][1]==before_xy[1]
                    w.request('undo');assert w.proposal.corridors[cid]['reference_xy_mm'][0]==before_xy
                    w.plan.fit();window.UpdateLayout();gui._pump()
                    from System.Windows.Media import PixelFormats
                    from System.Windows.Media.Imaging import RenderTargetBitmap,PngBitmapEncoder,BitmapFrame
                    from System.IO import FileStream,FileMode
                    image=RenderTargetBitmap(int(window.ActualWidth),int(window.ActualHeight),96,96,PixelFormats.Pbgra32);image.Render(window)
                    encoder=PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(image))
                    target=str(Path(artifacts)/'routing-workspace-native.png');stream=FileStream(target,FileMode.Create)
                    try:encoder.Save(stream)
                    finally:stream.Dispose()
                    assert revit_read.fingerprint(doc)==before
                    report.update(host='TrazadoAutomaticoMEP',native_read_only=True,fingerprint_unchanged=True,
                        primary_generation=True,corridors=corridor_count,graph_stats=graph_stats,
                        cancel_preserves_corridors=True,numeric_edit=True,undo=True,image=target,
                        units='mm',profile_verified=False)
                except Exception:
                    import traceback
                    errors.append(traceback.format_exc())
                finally:
                    w.cancel_event.set()
                    if w.future:
                        deadline=time.monotonic()+10
                        while not w.future.done() and time.monotonic()<deadline:time.sleep(.05)
                    window.Close()
            workspace.show(doc,uidoc,loaded)
    finally:store.ROOT=old_root
    (Path(artifacts)/'routing-workspace-native.json').write_text(json.dumps({'report':report,'errors':errors},ensure_ascii=False,indent=2),encoding='utf8')
    if errors:raise AssertionError(errors)
    return report
