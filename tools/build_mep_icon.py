"""Native vector/raster icon, same palette and 512 px output as ARQ/STR."""
from pathlib import Path
from PIL import Image
from svg_draw import VectorDraw
ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'ARQ-T.extension/ARQ-T.tab/MEP.panel/DistribuirVentilacion.pushbutton'


def render(dark):
    size=2048;s=size/32
    bg='#25272B' if dark else '#F5F6F8';fg='#F5F6F8' if dark else '#25272B';orange='#F47721'
    image=Image.new('RGBA',(size,size),(0,0,0,0));d=VectorDraw(image)
    d.rounded_rectangle((0,0,size-1,size-1),radius=4*s,fill=bg)
    def points(seq):return [(x*s,y*s) for x,y in seq]
    def line(seq,color=fg,width=1.2):d.line(points(seq),fill=color,width=round(width*s),joint='curve')
    d.polygon(points([(5,11),(19,5),(28,11),(14,18)]),fill=orange)
    d.polygon(points([(5,11),(14,18),(14,21),(5,14)]),fill='#D9681E')
    d.polygon(points([(14,18),(28,11),(28,14),(14,21)]),fill='#E76C1C')
    line([(5,11),(19,5),(28,11),(28,14),(14,21),(5,14),(5,11)])
    line([(5,11),(14,18),(28,11)])
    for t in (.25,.45,.65,.85):
        a=(5+14*t,11-6*t);b=(14+14*t,18-7*t)
        line([a,b],fg,1.0)
    line([(7,20),(7,27),(4,24)],orange,1.7)
    line([(7,27),(10,24)],orange,1.7)
    line([(23,27),(23,20),(20,23)],fg,1.7)
    line([(23,20),(26,23)],fg,1.7)
    d.save(DEST/('icon.dark.svg' if dark else 'icon.light.svg'))
    result=image.resize((512,512),Image.Resampling.LANCZOS)
    result.save(DEST/('icon.dark.png' if dark else 'icon.png'))
    result.resize((32,32),Image.Resampling.LANCZOS).save(DEST/('icon.dark.preview32.png' if dark else 'icon.preview32.png'))
    return result


if __name__=='__main__':
    render(False);render(True)
