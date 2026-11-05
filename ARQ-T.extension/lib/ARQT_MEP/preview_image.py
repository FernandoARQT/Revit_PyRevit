"""Clean WPF plan capture, accessible to Codex as a local PNG and structured legend."""
import os
from . import proposal as model,proposal_store as store
from .plan_transform import PlanTransform


def export(proposal,level_id=None,path=None,width=1600,height=1100):
    model.check(proposal)
    if not 320<=width<=4000 or not 240<=height<=4000:raise ValueError('Tamaño de captura fuera de rango.')
    import clr
    clr.AddReference('PresentationFramework');clr.AddReference('PresentationCore');clr.AddReference('WindowsBase')
    from System.Windows import Size,Rect
    from System.Windows.Controls import Canvas
    from System.Windows.Media import PixelFormats,Brushes
    from System.Windows.Media.Imaging import RenderTargetBitmap,PngBitmapEncoder,BitmapFrame
    from System.IO import FileStream,FileMode
    from .plan_canvas import draw_scene,label
    if level_id is None:level_id=next(r['level_id'] for r in proposal['analysis']['rooms'] if r['uid'] in proposal['placement_room_uids'])
    rooms=[r for r in proposal['analysis']['rooms'] if r['uid'] in proposal['room_uids'] and r['level_id']==level_id]
    if not rooms:raise ValueError('La planta no pertenece a la vivienda.')
    transform=PlanTransform(proposal['analysis'].get('view_frame'))
    transform.fit([xy for r in rooms for ring in r['rings'] for xy in ring],width,height)
    canvas=Canvas();canvas.Width=width;canvas.Height=height;canvas.Background=Brushes.White
    draw_scene(canvas,proposal,level_id,transform)
    canvas.Measure(Size(width,height));canvas.Arrange(Rect(0,0,width,height));canvas.UpdateLayout()
    image=RenderTargetBitmap(width,height,96,96,PixelFormats.Pbgra32);image.Render(canvas)
    if path is None:
        path=os.path.join(store.ROOT,'previews',proposal['proposal_id']+'-v'+str(proposal['version'])+'-L'+str(level_id)+'.png')
    if not os.path.isabs(path):raise ValueError('La captura necesita una ruta absoluta accesible.')
    os.makedirs(os.path.dirname(path),exist_ok=True);stream=FileStream(path,FileMode.Create)
    try:
        encoder=PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(image));encoder.Save(stream)
    finally:stream.Dispose()
    return {'path':path,'proposal_id':proposal['proposal_id'],'version':proposal['version'],'level_id':level_id,
            'width':width,'height':height,'coordinate_system':'Revit internal project coordinates in mm; WPF DIP capture at 96dpi',
            'legend':[{'label':label(t),'terminal_id':t['terminal_id'],'position_mm':[t['x'],t['y'],t['z']],'flow_lps':t['flow']}
                       for t in model.active(proposal) if t['room_uid'] in {r['uid'] for r in rooms}]}
