"""Interactive WPF plan over cached proposal geometry, without Revit API calls."""
import copy
import math
import time
import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
from System.Windows import Point
from System.Windows.Controls import Canvas,TextBlock
from System.Windows.Shapes import Polyline,Ellipse,Polygon as WpfPolygon,Rectangle
from System.Windows.Media import BrushConverter,PointCollection,Brushes,DoubleCollection
from System.Windows.Input import MouseButtonState,Key,Keyboard,ModifierKeys
from shapely.geometry import Point as XY,Polygon,shape
from .plan_transform import PlanTransform
from . import proposal as model

BRUSH=BrushConverter()


def color(value):return BRUSH.ConvertFromString(value)


def label(t):return t.get('display_id') or ('I-' if t['role']=='impulsion' else 'E-')+t['terminal_id'][-6:]


def draw_scene(canvas,p,level_id,transform,selected=None,ghost=None,polygon_points=None,zone_selected=None):
    canvas.Children.Clear();hits=[];room_uids=set(p['room_uids'])
    rooms=[r for r in p['analysis']['rooms'] if r['uid'] in room_uids and r['level_id']==level_id]
    def polyline(ring,stroke,width=1,dashed=False,fill=None):
        if not ring:return
        obj=WpfPolygon() if fill else Polyline();points=PointCollection()
        for xy in list(ring)+([] if fill else [ring[0]]):points.Add(Point(*transform.to_screen(xy)))
        obj.Points=points;obj.Stroke=color(stroke);obj.StrokeThickness=width
        if dashed:
            dash=DoubleCollection();dash.Add(4.0);dash.Add(3.0);obj.StrokeDashArray=dash
        if fill:obj.Fill=color(fill)
        obj.IsHitTestVisible=False;canvas.Children.Add(obj)
    def text(value,xy,offset=(0,0),fill='#666666',size=11):
        item=TextBlock();item.Text=value;item.FontSize=size;item.Foreground=color(fill);item.IsHitTestVisible=False
        x,y=transform.to_screen(xy);Canvas.SetLeft(item,x+offset[0]);Canvas.SetTop(item,y+offset[1]);canvas.Children.Add(item)
    for r in rooms:
        for ring in r['rings']:polyline(ring,'#858C98',1.3)
        for boundary in r.get('boundaries',[]):
            if boundary['kind']=='VIRTUAL':polyline(boundary['points_mm'],'#BEAA82',1,True)
        for c in r.get('ceilings',[]):
            geom=shape(c['geometry']);parts=list(geom.geoms) if hasattr(geom,'geoms') else [geom]
            for part in parts:
                if hasattr(part,'exterior'):polyline(list(part.exterior.coords),'#D9DEE7',.8)
        text(r['name'],r['centroid'],(-35,18),'#5D6470')
        for d in r.get('doors',[]):
            x,y=transform.to_screen(d);marker=Ellipse();marker.Width=7;marker.Height=7;marker.Stroke=color('#699571');marker.IsHitTestVisible=False
            Canvas.SetLeft(marker,x-3.5);Canvas.SetTop(marker,y-3.5);canvas.Children.Add(marker)
    for zone in p['zones']:
        r=next((r for r in rooms if r['uid']==zone['room_uid']),None)
        if not r:continue
        if zone.get('polygon_mm'):
            vertices=zone['polygon_mm'];polyline(vertices,'#C98B30',1.6,False,'#18F4B350')
            text(zone['use'],vertices[0],(4,4),'#9E681B')
            if zone['zone_id']==zone_selected:
                for i,xy in enumerate(vertices):
                    if i==len(vertices)-1 and xy==vertices[0]:continue
                    x,y=transform.to_screen(xy);obj=Rectangle();obj.Width=9;obj.Height=9;obj.Fill=color('#F47721')
                    Canvas.SetLeft(obj,x-4.5);Canvas.SetTop(obj,y-4.5);canvas.Children.Add(obj)
                    hits.append(('vertex',zone['zone_id'],i,x,y))
        elif zone.get('reference_mm'):
            text('⊕ COCINA · referencia',zone['reference_mm'],(0,0),'#B78023',12)
    for t in model.active(p):
        if t['room_uid'] not in {r['uid'] for r in rooms}:continue
        xy=ghost['xy'] if ghost and ghost.get('id')==t['terminal_id'] else (t['x'],t['y'])
        x,y=transform.to_screen(xy);stroke='#F47721' if t['role']=='impulsion' else '#287CAB'
        if ghost and ghost.get('id')==t['terminal_id']:
            status=ghost['validation']['status'];stroke='#C63535' if status=='NO_VALIDO' else '#BE8B20' if status=='VALIDO_CON_ADVERTENCIAS' else '#478950'
        obj=Ellipse();radius=8 if t['terminal_id']==selected else 6;obj.Width=radius*2;obj.Height=radius*2
        obj.Stroke=Brushes.Black if t['terminal_id']==selected else color(stroke);obj.StrokeThickness=2;obj.Fill=color(stroke)
        Canvas.SetLeft(obj,x-radius);Canvas.SetTop(obj,y-radius);canvas.Children.Add(obj)
        text('{} · {:.2f} L/s{}'.format(label(t),t['flow'],' 🔒' if t['lock_full'] or t['lock_position'] else ''),xy,(11,-10),stroke)
        hits.append(('terminal',t['terminal_id'],None,x,y))
    if polygon_points:
        polyline(polygon_points,'#F47721',2,True)
        for i,xy in enumerate(polygon_points):text(str(i+1),xy,(3,3),'#F47721')
    return hits


class PlanCanvas:
    def __init__(self,canvas,get_proposal,get_level,request,select,info,edit=None):
        self.canvas=canvas;self.get_proposal=get_proposal;self.get_level=get_level;self.request=request;self.select=select;self.info=info
        self.edit=edit;self.transform=PlanTransform();self.selected=None;self.zone_selected=None;self.mode=None;self.drag=None
        self.vertices=[];self.hits=[];self.fitted=None;self.last_move=0
        canvas.Focusable=True;canvas.Background=Brushes.White
        canvas.MouseLeftButtonDown+=self.down;canvas.MouseLeftButtonUp+=self.up;canvas.MouseMove+=self.move
        canvas.MouseWheel+=self.wheel;canvas.MouseRightButtonDown+=self.pan_down;canvas.MouseRightButtonUp+=self.pan_up
        canvas.MouseDown+=self.middle_down;canvas.MouseUp+=self.middle_up;canvas.KeyDown+=self.key
        canvas.SizeChanged+=self.resize;canvas.LostMouseCapture+=self.lost_capture

    def data(self):return self.get_proposal()

    def fit(self):
        p=self.data();lid=self.get_level()
        if not p:return
        self.transform=PlanTransform(p['analysis'].get('view_frame'))
        points=[xy for r in p['analysis']['rooms'] if r['uid'] in p['room_uids'] and r['level_id']==lid for ring in r['rings'] for xy in ring]
        self.transform.fit(points,self.canvas.ActualWidth,self.canvas.ActualHeight);self.fitted=(p['proposal_id'],lid)
        self.draw()

    def draw(self):
        p=self.data();lid=self.get_level()
        if not p:return
        if self.fitted!=(p['proposal_id'],lid):self.fit();return
        ghost=self.drag if self.drag and self.drag['kind']=='terminal' else None
        self.hits=draw_scene(self.canvas,p,lid,self.transform,self.selected,ghost,self.vertices,self.zone_selected)

    def resize(self,*args):
        if self.transform.zoom==1 and self.transform.pan==[0,0]:self.fit()
        else:self.transform.resize(self.canvas.ActualWidth,self.canvas.ActualHeight);self.draw()

    def position(self,e):
        point=e.GetPosition(self.canvas);return point.X,point.Y

    def nearest(self,screen):
        hits=sorted(self.hits,key=lambda h:math.hypot(h[3]-screen[0],h[4]-screen[1]))
        return hits[0] if hits and math.hypot(hits[0][3]-screen[0],hits[0][4]-screen[1])<14 else None

    def find_room(self,xy):
        p=self.data();lid=self.get_level()
        rooms=[r for r in p['analysis']['rooms'] if r['uid'] in p['placement_room_uids'] and r['level_id']==lid and polygon_contains(r,xy)]
        if len(rooms)!=1:raise ValueError('Seleccione un punto interior inequívoco de una Room del ámbito.')
        return rooms[0]

    def set_mode(self,mode):
        self.mode=mode;self.vertices=[]
        prompts={'impulsion':'Pulse en el plano para añadir impulsión. Esc cancela.',
                 'extraccion':'Pulse en el plano para añadir extracción. Esc cancela.',
                 'kitchen_point':'Pulse para marcar la referencia de cocina. Esc cancela.',
                 'kitchen_polygon':'Marque los vértices de cocina. Enter termina; Esc cancela.'}
        self.info(prompts.get(mode,''));self.canvas.Focus()

    def down(self,sender,e):
        p=self.data()
        if not p:return
        self.canvas.Focus();screen=self.position(e);xy=self.transform.to_model(screen);e.Handled=True
        try:
            if self.mode in ROLES_FOR_ADD:
                r=self.find_room(xy);self.request('add_ventilation_terminal',{'room_uid':r['uid'],'role':self.mode,'position_mm':list(xy)},p['version'])
                self.mode=None;return
            if self.mode=='kitchen_point':
                r=self.find_room(xy);self.request('define_ventilation_zone',{'room_uid':r['uid'],'reference_mm':list(xy),'use':'COCINA'},p['version']);self.mode=None;return
            if self.mode=='kitchen_polygon':
                if e.ClickCount==2:self.finish_polygon();return
                self.vertices.append(list(xy));self.draw();return
            hit=self.nearest(screen)
            if hit and hit[0]=='terminal':
                tid=hit[1];self.selected=tid;self.select(tid);t=model.terminal(p,tid)
                if e.ClickCount>=2 and self.edit:self.edit(tid);return
                if t['lock_position'] or t['lock_full']:self.info('Posición bloqueada.');self.draw();return
                self.drag={'kind':'terminal','id':tid,'xy':[t['x'],t['y']],'version':p['version'],
                           'validation':model.validate_position(p,t,[t['x'],t['y']])};self.canvas.CaptureMouse()
            elif hit and hit[0]=='vertex':
                self.drag={'kind':'vertex','id':hit[1],'index':hit[2],'version':p['version'],'xy':list(xy)};self.canvas.CaptureMouse()
        except Exception as exc:self.info(str(exc))

    def move(self,sender,e):
        if not self.drag:return
        screen=self.position(e)
        if self.drag['kind']=='pan':
            prior=self.drag['screen'];self.transform.shift(screen[0]-prior[0],screen[1]-prior[1]);self.drag['screen']=screen;self.draw();return
        if time.monotonic()-self.last_move<.025:return
        self.last_move=time.monotonic();xy=self.transform.to_model(screen);self.drag['xy']=list(xy)
        if self.drag['kind']=='terminal':
            self.drag['validation']=model.validate_position(self.data(),model.terminal(self.data(),self.drag['id']),xy)
            v=self.drag['validation'];z=v.get('pose',{}).get('z')
            self.info('; '.join(v['errors']+v['warnings']) or 'Posición válida.')
            self.draw()
        elif self.drag['kind']=='vertex':
            self.info('Vértice provisional XY mm ({:.1f}, {:.1f})'.format(*xy))
            p=copy.deepcopy(self.data());z=next(z for z in p['zones'] if z['zone_id']==self.drag['id'])
            z['polygon_mm'][self.drag['index']]=list(xy)
            if self.drag['index']==0 and z['polygon_mm'][-1]==next(q for q in self.data()['zones'] if q['zone_id']==self.drag['id'])['polygon_mm'][0]:z['polygon_mm'][-1]=list(xy)
            self.hits=draw_scene(self.canvas,p,self.get_level(),self.transform,self.selected,zone_selected=self.zone_selected)

    def up(self,sender,e):
        if not self.drag or self.drag['kind']=='pan':return
        drag=self.drag;xy=list(self.transform.to_model(self.position(e)));self.drag=None;self.canvas.ReleaseMouseCapture()
        try:
            if drag['kind']=='terminal':
                current=model.terminal(self.data(),drag['id'])
                if math.hypot(xy[0]-current['x'],xy[1]-current['y'])<1e-6:self.draw();return
                result=model.validate_position(self.data(),model.terminal(self.data(),drag['id']),xy)
                if not result['valid']:self.info('Posición rechazada; se conserva la última válida. '+'; '.join(result['errors']))
                else:self.request('move_ventilation_terminal',{'terminal_id':drag['id'],'position_mm':xy},drag['version'])
            else:
                zone=copy.deepcopy(next(z for z in self.data()['zones'] if z['zone_id']==drag['id']))
                original=zone['polygon_mm'][0];zone['polygon_mm'][drag['index']]=xy
                if drag['index']==0 and zone['polygon_mm'][-1]==original:zone['polygon_mm'][-1]=xy
                self.request('define_ventilation_zone',zone,drag['version'])
        except Exception as exc:self.info(str(exc))
        self.draw()

    def pan_down(self,sender,e):
        self.drag={'kind':'pan','screen':self.position(e)};self.canvas.CaptureMouse();e.Handled=True
    def pan_up(self,sender,e):
        if self.drag and self.drag['kind']=='pan':self.drag=None;self.canvas.ReleaseMouseCapture();e.Handled=True
    def middle_down(self,sender,e):
        if str(e.ChangedButton)=='Middle':self.pan_down(sender,e)
    def middle_up(self,sender,e):
        if str(e.ChangedButton)=='Middle':self.pan_up(sender,e)
    def lost_capture(self,*args):
        if self.drag:self.drag=None;self.draw()
    def wheel(self,sender,e):
        self.transform.zoom_at(self.position(e),1.15**(e.Delta/120));self.draw();e.Handled=True
    def finish_polygon(self):
        if len(self.vertices)<3:raise ValueError('Defina al menos tres vértices.')
        r=self.find_room(self.vertices[0]);p=self.data()
        self.request('define_ventilation_zone',{'room_uid':r['uid'],'polygon_mm':self.vertices,'use':'COCINA'},p['version'])
        self.mode=None;self.vertices=[];self.draw()
    def key(self,sender,e):
        try:
            p=self.data()
            if e.Key==Key.Escape:self.mode=None;self.vertices=[];self.drag=None;self.canvas.ReleaseMouseCapture();self.draw()
            elif e.Key==Key.Enter and self.mode=='kitchen_polygon':self.finish_polygon()
            elif e.Key==Key.Delete and self.selected:self.request('delete_ventilation_terminal',{'terminal_id':self.selected},p['version'])
            elif Keyboard.Modifiers & ModifierKeys.Control and e.Key in (Key.Z,Key.Y):self.request('undo_ventilation' if e.Key==Key.Z else 'redo_ventilation',{},p['version'])
            else:return
            e.Handled=True
        except Exception as exc:self.info(str(exc))


ROLES_FOR_ADD=('impulsion','extraccion')


def polygon_contains(room,xy):
    from .spatial import polygon_from_rings
    return polygon_from_rings(room['rings']).covers(XY(xy))
