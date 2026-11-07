"""Corridor overlay and gestures on the existing ventilation plan canvas."""
import copy
import math
from ...plan_canvas import PlanCanvas,draw_scene,color
from System.Windows import Point
from System.Windows.Shapes import Polyline,Rectangle,Ellipse,Path as WpfPath
from System.Windows.Controls import Canvas,TextBlock
from System.Windows.Media import PointCollection,PathGeometry,PathFigure,LineSegment,DoubleCollection
from System.Windows.Input import Key,Keyboard,ModifierKeys

COLORS={'SUPPLY':'#F47721','EXTRACT':'#287CAB','SHARED':'#8751A1'}


def segment_distance(point,a,b):
    dx,dy=b[0]-a[0],b[1]-a[1];d=dx*dx+dy*dy
    t=max(0,min(1,((point[0]-a[0])*dx+(point[1]-a[1])*dy)/d)) if d else 0
    return math.hypot(point[0]-a[0]-t*dx,point[1]-a[1]-t*dy)


def overlay(canvas,p,level_id,transform,selected=None,preview=None,vertices=()):
    hits=[];groups={};seen=set()
    corridors=dict(p.corridors) if preview else p.corridors
    if preview:corridors[preview['corridor_id']]=preview
    display_number=0
    for cid,c in corridors.items():
        if c['level_id']!=level_id:continue
        display_number+=1
        controls=[transform.to_screen(xy) for xy in c['reference_xy_mm']]
        screen=[transform.to_screen(xy) for xy in c.get('display_polyline_xy_mm',[])] or controls
        signature=(c['network_type'],tuple(tuple(round(v,3) for v in xy) for xy in c['reference_xy_mm']))
        if signature in seen and cid!=selected:continue
        seen.add(signature)
        pending=c.get('validation_status') in ('EDITED_PENDING_RECHECK','MANUAL_SCHEMATIC_INVALID')
        invalid=c.get('validation_status') in ('INVALID','JOINT_COLLISION_CONFLICT','CORRIDOR_CAPACITY_CONFLICT')
        group=(c['network_type'],cid==selected,pending,invalid)
        if group not in groups:groups[group]=PathGeometry()
        figure=PathFigure();figure.StartPoint=Point(*screen[0]);figure.IsClosed=False
        for xy in screen[1:]:figure.Segments.Add(LineSegment(Point(*xy),True))
        groups[group].Figures.Add(figure)
        for i,(a,b) in enumerate(zip(screen,screen[1:])):
            control_index=min(range(len(controls)-1),key=lambda j:segment_distance(((a[0]+b[0])/2,(a[1]+b[1])/2),controls[j],controls[j+1]))
            hits.append(('corridor',cid,control_index,a,b))
        if len(corridors)<40 or cid==selected or c.get('kind','MANUAL')=='MANUAL':
            prefix='I' if c['network_type']=='SUPPLY' else 'E' if c['network_type']=='EXTRACT' else 'G'
            item=TextBlock();item.Text=prefix+'-{:02d}'.format(display_number)+(' · bloqueado' if c['constraint_type']=='LOCKED_WAYPOINT' else '')
            item.Foreground=color(COLORS[c['network_type']]);item.IsHitTestVisible=False
            length=sum(math.dist(a,b) for a,b in zip(screen,screen[1:]));remaining=length/2;position=screen[0]
            for a,b in zip(screen,screen[1:]):
                segment=math.dist(a,b)
                if remaining<=segment:
                    t=remaining/max(1e-8,segment);position=(a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1]));break
                remaining-=segment
            Canvas.SetLeft(item,position[0]+8);Canvas.SetTop(item,position[1]-22);canvas.Children.Add(item)
        if cid==selected:
            for i,xy in enumerate(controls):
                dot=Rectangle();dot.Width=9;dot.Height=9;dot.Fill=color(COLORS[c['network_type']]);dot.IsHitTestVisible=False
                Canvas.SetLeft(dot,xy[0]-4.5);Canvas.SetTop(dot,xy[1]-4.5);canvas.Children.Add(dot)
                hits.append(('vertex',cid,i,xy,xy))
    for (network,is_selected,pending,invalid),geometry in sorted(groups.items(),key=lambda entry:entry[0][1]):
        path=WpfPath();path.Data=geometry;path.Stroke=color('#B04737' if invalid else '#9097A2' if pending else COLORS[network]);path.StrokeThickness=4 if is_selected else 2
        if pending:
            dashes=DoubleCollection();dashes.Add(4.);dashes.Add(3.);path.StrokeDashArray=dashes
        path.IsHitTestVisible=False;canvas.Children.Insert(0,path)
    if vertices:
        points=PointCollection()
        for xy in vertices:points.Add(Point(*transform.to_screen(xy)))
        line=Polyline();line.Points=points;line.Stroke=color('#8751A1');line.StrokeThickness=2;line.IsHitTestVisible=False;canvas.Children.Add(line)
    return hits


class CorridorCanvas(PlanCanvas):
    def __init__(self,canvas,get_design,get_level,request,select,info,get_network):
        self.get_design=get_design;self.get_network=get_network;self.preview=None;self.selected_vertex=None
        super().__init__(canvas,lambda:get_design().background if get_design() else None,get_level,request,select,info)

    def draw(self):
        p=self.get_design();lid=self.get_level()
        if not p:self.canvas.Children.Clear();self.hits=[];return
        if self.fitted!=(p.background['proposal_id'],lid):self.fit();return
        draw_scene(self.canvas,p.background,lid,self.transform)
        if getattr(self,'show_diagnostics',lambda:False)():
            geometry=PathGeometry()
            for edge in p.corridor_graph.get('edges',{}).values():
                points=edge.get('points_mm') or edge.get('reference_xyz_mm',[])
                if len(points)<2:continue
                figure=PathFigure();figure.StartPoint=Point(*self.transform.to_screen(points[0][:2]))
                for q in points[1:]:figure.Segments.Add(LineSegment(Point(*self.transform.to_screen(q[:2])),True))
                geometry.Figures.Add(figure)
            graph=WpfPath();graph.Data=geometry;graph.Stroke=color('#C5CAD1');graph.StrokeThickness=1;graph.IsHitTestVisible=False;self.canvas.Children.Add(graph)
        self.hits=overlay(self.canvas,p,lid,self.transform,self.selected,self.preview,self.vertices)
        for tid,path in p.selected_paths.items():
            if p.terminals[tid]['level_id']!=lid:continue
            ep=path['endpoint'];xy=ep['origin_xyz_mm'][:2];x,y=self.transform.to_screen(xy)
            dot=Ellipse();dot.Width=8;dot.Height=8;dot.Fill=color('White');dot.Stroke=color(COLORS[path['network_type']]);dot.StrokeThickness=2;dot.IsHitTestVisible=False
            Canvas.SetLeft(dot,x-4);Canvas.SetTop(dot,y-4);self.canvas.Children.Add(dot)
            n=ep['connector_normal_xyz'];tip=self.transform.to_screen([xy[0]+n[0]*180,xy[1]+n[1]*180])
            line=Polyline();points=PointCollection();points.Add(Point(x,y));points.Add(Point(*tip));line.Points=points
            line.Stroke=color(COLORS[path['network_type']]);line.StrokeThickness=2;line.IsHitTestVisible=False;self.canvas.Children.Add(line)
        selected={str(a.get('element_id')):a['network_type'] for a in p.configuration.get('equipment_assignments',[])}
        rooms=[r for r in p.background['analysis']['rooms'] if r['level_id']==lid and r['uid'] in p.background['room_uids']]
        from ...spatial import polygon_from_rings
        from shapely.geometry import Point as XY
        for device in p.catalog.get('devices',[]):
            role=selected.get(str(device.get('element_id')))
            is_hrv=device['id']==p.configuration.get('hrv_id')
            ports=device.get('connectors',[])
            if not ports or not (role or is_hrv):continue
            virtual=p.equipment.get(role,{}) if role else {}
            frame=virtual.get('proposed_transform')
            xy=frame['origin_mm'][:2] if frame else [sum(port['xyz_mm'][i] for port in ports)/len(ports) for i in (0,1)]
            if not any(polygon_from_rings(r['rings']).covers(XY(*xy)) for r in rooms):continue
            x,y=self.transform.to_screen(xy);brush=color('#343D49' if is_hrv else COLORS[role])
            square=Rectangle();square.Width=10;square.Height=10;square.Fill=brush;square.IsHitTestVisible=False
            Canvas.SetLeft(square,x-5);Canvas.SetTop(square,y-5);self.canvas.Children.Add(square)
            label=TextBlock();label.Text='REC' if is_hrv else 'DI' if role=='SUPPLY' else 'DE';label.FontSize=10;label.Foreground=brush;label.IsHitTestVisible=False
            Canvas.SetLeft(label,x+8);Canvas.SetTop(label,y-7);self.canvas.Children.Add(label)

    def nearest(self,screen):
        vertices=[(math.dist(screen,h[3]),h) for h in self.hits if h[0]=='vertex']
        closest=min(vertices,key=lambda x:x[0]) if vertices else None
        if closest and closest[0]<10:return closest[1]
        edges=[(segment_distance(screen,h[3],h[4]),h) for h in self.hits if h[0]=='corridor']
        closest=min(edges,key=lambda x:x[0]) if edges else None
        return closest[1] if closest and closest[0]<10 else None

    def set_mode(self,mode):
        self.cancel();self.mode=mode
        self.info('Marque los puntos del corredor. Enter termina; Esc cancela.' if mode=='corridor' else 'Seleccione un tramo para insertar un vértice.')
        self.canvas.Focus()

    def down(self,sender,e):
        p=self.get_design()
        if p is None:return
        if hasattr(self,'can_edit') and not self.can_edit():self.info('El cálculo está en curso. Puede navegar por la planta; la edición se activa al terminar.');return
        self.canvas.Focus();screen=self.position(e);xy=list(self.transform.to_model(screen));e.Handled=True
        try:
            if self.mode=='corridor':
                self.vertices.append(xy);self.draw();self.info('Corredor en dibujo · {} puntos · Enter termina · Esc cancela'.format(len(self.vertices)));return
            hit=self.nearest(screen)
            if hit is None:self.selected=None;self.select(None);self.draw();return
            kind,cid,index,*_=hit;self.selected=cid;self.selected_vertex=index if kind=='vertex' else None;self.select(cid)
            if self.mode=='insert_vertex' or e.ClickCount>=2:
                if kind!='corridor':self.info('Pulse en el interior de un tramo para insertar un punto.');return
                self.selected_vertex=index+1
                self.request('add_vertex',{'corridor_id':cid,'index':index+1,'point_mm':xy},p.revision)
                self.mode=None;self.draw();return
            c=p.corridors[cid]
            if c['constraint_type']=='LOCKED_WAYPOINT':self.info('Corredor bloqueado. Desbloquéelo antes de editar.');self.draw();return
            self.drag={'kind':kind,'id':cid,'index':index,'start':xy,'revision':p.revision,'original':copy.deepcopy(c)}
            self.canvas.CaptureMouse();self.draw()
        except Exception as exc:self.info(str(exc));self.cancel()

    def move(self,sender,e):
        if self.drag and self.drag['kind']=='pan':return super().move(sender,e)
        if not self.drag:return
        xy=list(self.transform.to_model(self.position(e)));d=self.drag;c=copy.deepcopy(d['original'])
        if d['kind']=='vertex':c['reference_xy_mm'][d['index']]=xy
        else:
            dx,dy=xy[0]-d['start'][0],xy[1]-d['start'][1]
            c['reference_xy_mm']=[[x+dx,y+dy] for x,y in c['reference_xy_mm']]
        self.preview=c;self.draw()

    def up(self,sender,e):
        if not self.drag or self.drag['kind']=='pan':return
        d=self.drag;xy=list(self.transform.to_model(self.position(e)));self.drag=None;self.preview=None;self.canvas.ReleaseMouseCapture()
        try:
            if math.dist(xy,d['start'])<1e-6:self.draw();return
            if d['kind']=='vertex':op='move_vertex';payload={'index':d['index'],'point_mm':xy}
            else:op='move_corridor';payload={'delta_mm':[xy[i]-d['start'][i] for i in (0,1)]}
            self.request(op,dict(payload,corridor_id=d['id']),d['revision'])
        except Exception as exc:self.info(str(exc))
        self.draw()

    def finish(self):
        if self.mode!='corridor':return
        if len(self.vertices)<2:raise ValueError('Defina al menos dos puntos.')
        p=self.get_design()
        self.request('add_corridor',{'points_mm':copy.deepcopy(self.vertices),'network_type':self.get_network(),'level_id':self.get_level()},p.revision)
        self.cancel()

    def cancel(self):
        self.drag=None;self.preview=None;self.mode=None;self.vertices=[];self.canvas.ReleaseMouseCapture();self.draw()

    def lost_capture(self,*args):
        if self.drag:self.drag=None;self.preview=None;self.draw()

    def key(self,sender,e):
        try:
            p=self.get_design()
            if e.Key==Key.Escape:self.cancel()
            elif p is None:return
            elif e.Key==Key.Enter:self.finish()
            elif e.Key==Key.Delete and self.selected:self.request('delete_corridor',{'corridor_id':self.selected},p.revision)
            elif Keyboard.Modifiers & ModifierKeys.Control and e.Key in (Key.Z,Key.Y):self.request('undo' if e.Key==Key.Z else 'redo',{},p.revision)
            else:return
            e.Handled=True
        except Exception as exc:self.info(str(exc))
