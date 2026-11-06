"""MEP plan PNG using the shared WPF canvas and coordinate transform."""
import os
from ...plan_transform import PlanTransform
from .canvas import overlay
from . import store


def export(proposal,level_id=None,path=None,width=1600,height=1100):
    from .background import check
    from ...plan_canvas import draw_scene,label,color
    from System.Windows import Size,Rect
    from System.Windows.Controls import Canvas,TextBlock
    from System.Windows.Media import PixelFormats,Brushes
    from System.Windows.Media.Imaging import RenderTargetBitmap,PngBitmapEncoder,BitmapFrame
    from System.IO import FileStream,FileMode
    background=check(proposal.background)
    if not 320<=width<=4000 or not 240<=height<=4000:raise ValueError('Tamaño de captura fuera de rango.')
    if level_id is None:level_id=next(r['level_id'] for r in background['analysis']['rooms'] if r['uid'] in background['room_uids'])
    rooms=[r for r in background['analysis']['rooms'] if r['uid'] in background['room_uids'] and r['level_id']==level_id]
    if not rooms:raise ValueError('La planta no pertenece al ámbito del trazador.')
    transform=PlanTransform(background['analysis'].get('view_frame'))
    transform.fit([xy for r in rooms for ring in r['rings'] for xy in ring],width,height)
    canvas=Canvas();canvas.Width=width;canvas.Height=height;canvas.Background=Brushes.White
    draw_scene(canvas,background,level_id,transform);overlay(canvas,proposal,level_id,transform)
    caption=TextBlock();caption.Text='ESQUEMA · revisión {} · sin validación constructiva'.format(proposal.revision)
    caption.Foreground=color('#707782');caption.FontSize=12
    Canvas.SetLeft(caption,20);Canvas.SetTop(caption,12);canvas.Children.Add(caption)
    canvas.Measure(Size(width,height));canvas.Arrange(Rect(0,0,width,height));canvas.UpdateLayout()
    image=RenderTargetBitmap(width,height,96,96,PixelFormats.Pbgra32);image.Render(canvas)
    if path is None:
        path=os.path.join(store.ROOT,'previews',proposal.proposal_id+'-r'+str(proposal.revision)+'.png')
    if not os.path.isabs(path):raise ValueError('La captura necesita una ruta absoluta.')
    os.makedirs(os.path.dirname(path),exist_ok=True);stream=FileStream(path,FileMode.Create)
    try:
        encoder=PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(image));encoder.Save(stream)
    finally:stream.Dispose()
    result={'path':path,'level_id':level_id,'width':width,'height':height,
        'coordinate_system':'Revit internal project coordinates in mm; WPF DIP capture at 96dpi',
        'legend':[{'label':label(t),'terminal_id':t['terminal_id'],'position_mm':[t['x'],t['y'],t['z']],'flow_lps':t['flow']}
            for t in background['terminals'] if t['room_uid'] in {r['uid'] for r in rooms}]}
    result.update(proposal_id=proposal.proposal_id,revision=proposal.revision,design_stage=proposal.design_stage,
        validation_status='NOT_EVALUATED',source_proposal_id=proposal.source_proposal_id,source_version=proposal.source_version,
        geometry_revision=proposal.geometry_revision,
        corridor_legend=[c for c in proposal.corridors.values() if c['level_id']==result['level_id']])
    return result
