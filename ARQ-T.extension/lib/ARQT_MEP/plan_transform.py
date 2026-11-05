"""Revit internal mm -> view-local mm -> WPF device-independent units, reversible."""
import math


class PlanTransform:
    def __init__(self,frame=None):
        frame=frame or {};self.origin=frame.get('origin_mm',[0,0])
        self.right=frame.get('right',[1,0]);self.up=frame.get('up',[0,1])
        self.det=self.right[0]*self.up[1]-self.right[1]*self.up[0]
        if abs(self.det)<1e-9:raise ValueError('Base de planta degenerada.')
        self.scale=1;self.zoom=1;self.pan=[0,0];self.center=[0,0];self.viewport=[1,1]

    def local(self,xy):
        x=xy[0]-self.origin[0];y=xy[1]-self.origin[1]
        return ((x*self.up[1]-y*self.up[0])/self.det,(-x*self.right[1]+y*self.right[0])/self.det)

    def model(self,uv):
        return (self.origin[0]+uv[0]*self.right[0]+uv[1]*self.up[0],
                self.origin[1]+uv[0]*self.right[1]+uv[1]*self.up[1])

    def fit(self,points,width,height):
        local=[self.local(p) for p in points]
        if not local:return
        lo=[min(p[i] for p in local) for i in (0,1)];hi=[max(p[i] for p in local) for i in (0,1)]
        self.viewport=[max(1,width),max(1,height)];self.center=[(lo[i]+hi[i])/2 for i in (0,1)]
        self.scale=max(.000001,min(max(1,width-60)/max(1,hi[0]-lo[0]),max(1,height-60)/max(1,hi[1]-lo[1])))
        self.pan=[0,0];self.zoom=1

    def resize(self,width,height):self.viewport=[max(1,width),max(1,height)]

    def to_screen(self,xy):
        u,v=self.local(xy);s=self.scale*self.zoom
        return (self.viewport[0]/2+(u-self.center[0])*s+self.pan[0],
                self.viewport[1]/2-(v-self.center[1])*s+self.pan[1])

    def to_model(self,screen):
        s=self.scale*self.zoom
        return self.model(((screen[0]-self.viewport[0]/2-self.pan[0])/s+self.center[0],
                           -(screen[1]-self.viewport[1]/2-self.pan[1])/s+self.center[1]))

    def zoom_at(self,screen,factor):
        anchor=self.to_model(screen);self.zoom=max(.05,min(50,self.zoom*factor))
        after=self.to_screen(anchor);self.pan=[self.pan[i]+screen[i]-after[i] for i in (0,1)]

    def shift(self,dx,dy):self.pan=[self.pan[0]+dx,self.pan[1]+dy]

    def physical_pixels_to_dip(self,point,dpi_x=96,dpi_y=96):
        if min(dpi_x,dpi_y)<=0:raise ValueError('DPI no válido.')
        return point[0]*96/dpi_x,point[1]*96/dpi_y
