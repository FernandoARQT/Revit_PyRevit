"""WPF gestures against the real embedded V3 controller, isolated sidecars."""
import copy
import math
import tempfile
from pathlib import Path
from System.Windows import Point
from ARQT_MEP.routing.design import model,store,operations


class Mouse:
    def __init__(self,xy,count=1):self.xy=xy;self.Handled=False;self.ClickCount=count;self.Delta=120
    def GetPosition(self,canvas):return Point(*self.xy)


def run(window,state,pump,artifact):
    editor=state['design_editor'];plan=editor.plan;source=copy.deepcopy(state['proposal']);old_root=store.ROOT
    report={}
    with tempfile.TemporaryDirectory(prefix='routing-design-ui-') as tmp:
        store.ROOT=tmp
        try:
            editor.ui['Tab'].IsSelected=True;window.UpdateLayout();pump(window)
            editor.create();assert editor.proposal is not None,editor.ui['Info'].Text
            plan.fit();plan.set_mode('corridor')
            for xy in ([1000,1000],[5000,1000],[6000,4500]):plan.down(plan.canvas,Mouse(plan.transform.to_screen(xy)))
            plan.finish();p=editor.proposal;cid=next(iter(p.corridors));assert p.revision==2
            assert len(p.corridors[cid]['reference_xy_mm'])==3
            # Clicking a segment selects it; no-op gestures never write a revision.
            pos=plan.transform.to_screen([3000,1000]);plan.down(plan.canvas,Mouse(pos));plan.up(plan.canvas,Mouse(pos))
            assert plan.selected==cid and editor.proposal.revision==2
            start=plan.transform.to_screen([3000,1000]);end=plan.transform.to_screen([3500,1400])
            plan.down(plan.canvas,Mouse(start));plan.move(plan.canvas,Mouse(end))
            assert store.get(p.document_identity,p.proposal_id).revision==2
            plan.up(plan.canvas,Mouse(end));assert editor.proposal.revision==3
            shifted=copy.deepcopy(editor.proposal.corridors[cid]['reference_xy_mm'])
            assert all(math.dist(a,b)<1e-6 for a,b in zip(shifted,[[1500,1400],[5500,1400],[6500,4900]])),shifted
            # Vertex drag uses the same screen/model transform after zoom and pan.
            plan.wheel(plan.canvas,Mouse((200,180)));plan.transform.shift(35,-19);plan.draw()
            start=plan.transform.to_screen(shifted[1]);end=plan.transform.to_screen([5301.123,1802.456])
            plan.down(plan.canvas,Mouse(start));plan.move(plan.canvas,Mouse(end));plan.up(plan.canvas,Mouse(end))
            assert abs(editor.proposal.corridors[cid]['reference_xy_mm'][1][0]-5301.123)<1e-6
            editor.request('undo');assert editor.proposal.corridors[cid]['reference_xy_mm']==shifted
            editor.request('redo');assert abs(editor.proposal.corridors[cid]['reference_xy_mm'][1][0]-5301.123)<1e-6
            before=copy.deepcopy(editor.proposal.corridors)
            plan.pan_down(plan.canvas,Mouse((100,100)));plan.move(plan.canvas,Mouse((180,160)));plan.pan_up(plan.canvas,Mouse((180,160)))
            plan.resize();assert editor.proposal.corridors==before
            # Insert/delete a point and preserve independent proposal copies.
            a,b=editor.proposal.corridors[cid]['reference_xy_mm'][:2];middle=[(a[i]+b[i])/2 for i in (0,1)]
            plan.set_mode('insert_vertex');plan.down(plan.canvas,Mouse(plan.transform.to_screen(middle)))
            assert len(editor.proposal.corridors[cid]['reference_xy_mm'])==4
            editor.ui['Vertex'].Text='2';editor.delete_vertex()
            assert len(editor.proposal.corridors[cid]['reference_xy_mm'])==3
            before=copy.deepcopy(editor.proposal.to_dict());editor.duplicate();copied=editor.proposal
            assert copied.proposal_id!=before['proposal_id']
            editor.request('delete_corridor',{'corridor_id':cid});assert not editor.proposal.corridors
            editor.adopt(store.get(before['document_identity'],before['proposal_id']));assert cid in editor.proposal.corridors
            assert editor.choices[editor.ui['Proposals'].SelectedIndex].proposal_id==editor.proposal.proposal_id
            editor.request('set_constraint',{'corridor_id':cid,'constraint_type':'LOCKED_WAYPOINT'})
            revision=editor.proposal.revision;pos=plan.transform.to_screen(middle)
            plan.down(plan.canvas,Mouse(pos));plan.up(plan.canvas,Mouse(pos));assert editor.proposal.revision==revision
            # An external MCP mutation is visible on poll and invalidates old leases.
            operations.dispatch(editor.document_id,'routing_design_set_constraint',{'proposal_id':editor.proposal.proposal_id,
                'expected_revision':revision,'corridor_id':cid,'constraint_type':'REQUIRED_WAYPOINT'})
            editor.poll();assert editor.proposal.revision==revision+1
            assert editor.ui['Constraint'].SelectedIndex==1
            from ARQT_MEP.routing.design.preview import export
            capture=export(editor.proposal,1,str(Path(artifact)/'routing-v3-corridors.png'))
            from System.Windows.Media import PixelFormats
            from System.Windows.Media.Imaging import RenderTargetBitmap,PngBitmapEncoder,BitmapFrame
            from System.IO import FileStream,FileMode
            plan.fit();window.UpdateLayout();pump(window)
            bitmap=RenderTargetBitmap(int(window.ActualWidth),int(window.ActualHeight),96,96,PixelFormats.Pbgra32);bitmap.Render(window)
            encoder=PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(bitmap))
            stream=FileStream(str(Path(artifact)/'routing-v3-editor.png'),FileMode.Create)
            try:encoder.Save(stream)
            finally:stream.Dispose()
            assert capture['corridor_legend'][0]['corridor_id']==cid and capture['validation_status']=='NOT_EVALUATED'
            assert state['proposal']==source,'V3 modificó la propuesta anterior del trazador.'
            report.update(draw=True,select_without_edit=True,drag_preview_without_write=True,move_corridor=True,
                move_vertex=True,insert_delete_vertex=True,zoom_pan_resize=True,undo_redo=True,duplicate_switch=True,
                lock=True,external_mcp_sync=True,terminal_proposal_unchanged=True,capture=capture)
            return report
        finally:store.ROOT=old_root
