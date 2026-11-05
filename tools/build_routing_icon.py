"""ARQ-T vector icon, light/dark with identical geometry and 512 px PNGs."""
from pathlib import Path
from PIL import Image
from svg_draw import VectorDraw
DEST=Path(__file__).resolve().parents[1]/'ARQ-T.extension/ARQ-T.tab/MEP.panel/TrazadoAutomaticoMEP.pushbutton'


def render(dark):
    size=2048;s=size/32;bg='#25272B' if dark else '#F5F6F8';fg='#F5F6F8' if dark else '#25272B';orange='#F47721'
    image=Image.new('RGBA',(size,size),(0,0,0,0));d=VectorDraw(image)
    d.rounded_rectangle((0,0,size-1,size-1),radius=4*s,fill=bg)
    def line(pts,color,width=1.6):d.line([(x*s,y*s) for x,y in pts],fill=color,width=round(s*width),joint='curve')
    d.polygon([(x*s,y*s) for x,y in [(4,14),(11,10),(16,13),(9,17)]],fill=orange)
    line([(4,14),(4,21),(9,24),(16,20),(16,13)],fg,1.2)
    line([(4,14),(9,17),(16,13)],fg,1.2);line([(9,17),(9,24)],fg,1.2)
    for pts in [[(12,16),(20,11),(27,15)],[(14,18),(22,14),(28,18)],[(13,21),(22,25),(28,21)]]:line(pts,orange,1.8)
    for x,y in [(27,15),(28,18),(28,21)]:
        d.ellipse(((x-1.1)*s,(y-1.1)*s,(x+1.1)*s,(y+1.1)*s),fill=fg)
    d.save(DEST/('icon.dark.svg' if dark else 'icon.light.svg'))
    image.resize((512,512),Image.Resampling.LANCZOS).save(DEST/('icon.dark.png' if dark else 'icon.png'))


if __name__=='__main__':render(False);render(True)
