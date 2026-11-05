from PIL import ImageDraw


class VectorDraw:
    def __init__(self,image):
        self.draw=ImageDraw.Draw(image)
        self.size=image.width
        self.parts=[]

    def polygon(self,points,fill):
        self.draw.polygon(points,fill=fill)
        self.parts.append('<polygon points="{}" fill="{}"/>'.format(' '.join('{},{}'.format(x,y) for x,y in points),fill))

    def line(self,points,fill,width,joint=None):
        self.draw.line(points,fill=fill,width=width,joint=joint)
        self.parts.append('<polyline points="{}" fill="none" stroke="{}" stroke-width="{}" stroke-linejoin="round"/>'.format(' '.join('{},{}'.format(x,y) for x,y in points),fill,width))

    def rounded_rectangle(self,box,radius,fill):
        self.draw.rounded_rectangle(box,radius=radius,fill=fill)
        x,y,r,b=box
        self.parts.append('<rect x="{}" y="{}" width="{}" height="{}" rx="{}" fill="{}"/>'.format(x,y,r-x,b-y,radius,fill))

    def ellipse(self,box,fill):
        self.draw.ellipse(box,fill=fill)
        x,y,r,b=box
        self.parts.append('<ellipse cx="{}" cy="{}" rx="{}" ry="{}" fill="{}"/>'.format((x+r)/2,(y+b)/2,(r-x)/2,(b-y)/2,fill))

    def save(self,path):
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {0} {0}">{1}</svg>'.format(self.size,''.join(self.parts)),encoding='utf-8')
