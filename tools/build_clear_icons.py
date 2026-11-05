from pathlib import Path
from PIL import Image,ImageDraw
from svg_draw import VectorDraw
ROOT=Path(__file__).resolve().parents[1]
BG='#25272B';ORANGE='#F47721';WHITE='#FFFFFF'

def render(kind,size=512,white=WHITE,bg=BG,svg=None):
    scale=size/32
    im=Image.new('RGBA',(size,size),(0,0,0,0));d=VectorDraw(im)
    def pts(seq):return [(round(x*scale),round(y*scale)) for x,y in seq]
    def poly(seq,fill,outline=None):
        d.polygon(pts(seq),fill=fill)
        if outline:d.line(pts(seq+[seq[0]]),fill=outline,width=max(1,round(scale)),joint='curve')
    def line(seq,color=white,width=1):d.line(pts(seq),fill=color,width=max(1,round(width*scale)),joint='curve')
    def curve(a,b,c,e):
        return [((1-t)**3*a[0]+3*(1-t)**2*t*b[0]+3*(1-t)*t*t*c[0]+t**3*e[0],(1-t)**3*a[1]+3*(1-t)**2*t*b[1]+3*(1-t)*t*t*c[1]+t**3*e[1]) for t in [i/24 for i in range(25)]]
    d.rounded_rectangle((0,0,size-1,size-1),radius=round(4*scale),fill=bg)
    if kind=='floor':
        poly([(5,5),(27,5),(27,19),(19,19),(19,27),(5,27)],ORANGE)
        line([(5,27),(5,5),(27,5),(27,19),(19,19),(19,27),(5,27)],width=2)
        line([(5,15),(12,15)],width=2);line([(17,15),(27,15)],width=2)
        line([(19,15),(19,19)],width=2)
        line([(12,15),(12,10)],width=1)
    elif kind=='cavity':
        poly([(5,11),(16,17),(16,28),(5,22)],'#D9681E',white)
        poly([(16,17),(27,11),(27,22),(16,28)],ORANGE,white)
        left=curve((7,23),(7,12),(14,17),(14,27))
        poly(left+[(7,23)],bg);line(left,width=1)
        right=curve((18,27),(18,17),(25,12),(25,23))
        poly(right+[(18,27)],bg);line(right,width=1)
        top=curve((5,11),(8,5),(12,4),(16,5))+curve((16,5),(21,6),(25,8),(27,11))+[(16,17)]
        poly(top,ORANGE,white)
        line([(5,11),(16,17),(27,11)],width=1)
    else:
        for i in range(3):
            x=4+i*6
            poly([(x,14),(x+5,16),(x+14,7),(x+9,5)],ORANGE,white)
            poly([(x,14),(x+5,16),(x+5,22),(x,20)],'#D9681E',white)
            poly([(x+5,16),(x+14,7),(x+14,13),(x+5,22)],'#F47721',white)
            for dx in (1.6,3.6):
                cx=x+dx;cy=18+(dx-2.5)*.4
                d.ellipse((round((cx-.65)*scale),round((cy-1)*scale),round((cx+.65)*scale),round((cy+1)*scale)),fill=bg)
    if svg:d.save(svg)
    return im

if __name__=='__main__':
    locations={'floor':'ARQ.panel/SuelosAutomaticos.pushbutton','cavity':'STR.panel/Cavity.pushbutton','slab':'STR.panel/DespieceLosaAlveolar.pushbutton'}
    for kind,folder in locations.items():
        dest=ROOT/'ARQ-T.extension/ARQ-T.tab'/folder
        render(kind,2048,white='#25272B',bg='#F5F6F8',svg=dest/'icon.light.svg').resize((512,512),Image.Resampling.LANCZOS).save(dest/'icon.png')
        render(kind,2048,white='#F5F6F8',bg='#25272B',svg=dest/'icon.dark.svg').resize((512,512),Image.Resampling.LANCZOS).save(dest/'icon.dark.png')
        render(kind).save(dest/'icon.ico',sizes=[(s,s) for s in (16,24,32,48,64,128,256)])
        render(kind,2048,white='#25272B',bg='#F5F6F8',svg=dest/'icon.light.svg').resize((32,32),Image.Resampling.LANCZOS).save(dest/'icon.preview32.png')
    preview=Image.new('RGB',(720,270),'#F5F6F8');p=ImageDraw.Draw(preview)
    for i,kind in enumerate(locations):
        preview.paste(render(kind,192),(24+i*240,16),render(kind,192))
        p.text((24+i*240,230),kind,fill=BG)
    preview.save(ROOT/'tests/artifacts/ribbon-icons-preview.png')
