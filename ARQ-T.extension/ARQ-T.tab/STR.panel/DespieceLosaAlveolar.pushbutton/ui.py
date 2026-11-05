import os
import copy
import uuid
import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System.Data')
from System import Int32,Double,Boolean,Action
from System.Data import DataTable,DataView,DataRowState
from System.Windows import MessageBox,MessageBoxButton,MessageBoxResult,Thickness
from System.Windows.Markup import XamlReader
from System.Windows.Controls import TextBox,TextBlock,StackPanel,Canvas,DataGridEditingUnit
from System.Windows.Shapes import Rectangle,Line
from System.Windows.Media import Brushes,BrushConverter
from pyrevit.api import DB,UI
import settings
from optimizer import solve_k_best
from candidates import ordered_widths
from geometry import dimensions,plate_profiles
from revit_adapter import snapshot,generate,mm

FIELDS=[('left0','Izq. mínima',('left',0)),('left1','Izq. nominal',('left',1)),('left2','Izq. máxima',('left',2)),
        ('right0','Der. mínima',('right',0)),('right1','Der. nominal',('right',1)),('right2','Der. máxima',('right',2)),
        ('minimum_preferred','Mín. preferente',None),('minimum_exceptional','Mín. excepcional',None),
        ('plate_cost','Coste por placa',None),('activation_cost','Activar ancho ≠1200',None),
        ('tolerance_standard','Tol. ±1200',None),('tolerance_other','Tol. ±otros',None)]


class FloorFilter(UI.Selection.ISelectionFilter):
    __namespace__='ARQT.Alveolar.Filter'+uuid.uuid4().hex
    def AllowElement(self,e):return isinstance(e,DB.Floor)
    def AllowReference(self,r,p):return False


def alert(text):
    MessageBox.Show(str(text),'ARQ-T | Losa alveolar')


def run(doc,uidoc,root):
    if not doc or doc.IsFamilyDocument:
        alert('Abra un proyecto.')
        return
    try:
        config=settings.load_settings()
    except Exception as exc:
        if MessageBox.Show('No se pudo leer la configuración: {}\n¿Recuperar valores predeterminados y conservar una copia del contenido anterior?'.format(exc),'Configuración',MessageBoxButton.YesNo)!=MessageBoxResult.Yes:
            return
        from pyrevit import script
        cfg=script.get_config(settings.CONFIG_SECTION)
        cfg.set_option('recovery_json',cfg.get_option('settings_json',''))
        config=settings.make_default_settings()
        settings.save_settings(config)
    selection=[doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    source=selection[0] if len(selection)==1 and isinstance(selection[0],DB.Floor) else None
    while True:
        window=Dialog(doc,source,config,root)
        window.win.ShowDialog()
        config=window.config
        if window.action=='select':
            try:
                ref=uidoc.Selection.PickObject(UI.Selection.ObjectType.Element,FloorFilter(),'Seleccione un forjado rectangular horizontal')
                source=doc.GetElement(ref.ElementId)
            except Exception:
                pass
            continue
        if window.action=='generate':
            try:
                result=generate(doc,source,window.saved,config['direction'],window.solutions[window.c['Solutions'].SelectedIndex],config)
                alert('{} placas creadas. Original sustituido. Puede deshacer la operación desde Revit.'.format(len(result)))
                return
            except Exception as exc:
                alert('Operación revertida:\n'+str(exc))
                continue
        return


class Dialog:
    def __init__(self,doc,source,config,root):
        self.doc,self.source,self.config=doc,source,copy.deepcopy(config)
        self.solutions=[]
        self.saved=None
        self.action=None
        self.loading=True
        with open(os.path.join(root,'ui.xaml'),encoding='utf-8') as f:
            self.win=XamlReader.Parse(f.read())
        names=['FloorLabel','Select','Direction','Catalog','Group1','Group2','Group3','Add','Delete','Save','Reset','Fields','LeftEdge','RightEdge','Solutions','Preview','Status','Calculate','Reverse','Generate','Cancel']
        self.c={n:self.win.FindName(n) for n in names}
        self.field_controls={}
        for key,label,path in FIELDS:
            panel=StackPanel();panel.Margin=Thickness(0,0,10,6)
            title=TextBlock();title.Text=label
            text=TextBox();text.Width=130
            text.Text=str(config[path[0]][path[1]] if path else config[key])
            panel.Children.Add(title);panel.Children.Add(text);self.c['Fields'].Children.Add(panel)
            self.field_controls[key]=text
            text.TextChanged+=self.invalidate
            text.LostFocus+=self.try_save
        self.c['LeftEdge'].Text=config['edges']['left'];self.c['RightEdge'].Text=config['edges']['right']
        for n in ('LeftEdge','RightEdge'):
            self.c[n].TextChanged+=self.invalidate
            self.c[n].LostFocus+=self.try_save
        self.c['Direction'].SelectedIndex=0 if config['direction']=='N-S' else 1
        self.c['Direction'].SelectionChanged+=self.direction_changed
        self.populate()
        actions={'Select':self.select,'Add':self.add,'Delete':self.delete,'Save':self.try_save,'Reset':self.reset,'Calculate':self.calculate,'Reverse':self.reverse,'Generate':self.generate,'Cancel':self.cancel}
        for name,handler in actions.items():self.c[name].Click+=handler
        self.c['Solutions'].SelectionChanged+=self.choose
        self.win.Closing+=self.closing
        self.loading=False
        self.describe()

    def populate(self):
        self.table=DataTable()
        for name,kind in [('Anchura_mm',Int32),('Categoria',Int32),('Penalizacion',Double),('Activa',Boolean)]:self.table.Columns.Add(name,kind)
        for o in self.config['widths']:self.table.Rows.Add(o['width'],o['category'],float(o['penalty']),o['enabled'])
        for i in (1,2,3):
            view=DataView(self.table);view.RowFilter='Categoria = '+str(i) if i!=3 else 'Categoria = 3 OR Categoria NOT IN (1, 2, 3)'
            grid=self.c['Group'+str(i)];grid.ItemsSource=view
            grid.CellEditEnding+=self.cell_edit

    def cell_edit(self,sender,args):
        self.invalidate(None,None)
        self.win.Dispatcher.BeginInvoke(Action(lambda:self.try_save(None,None)))

    def invalidate(self,sender,args):
        if not self.loading:
            self.solutions=[];self.c['Solutions'].Items.Clear();self.c['Preview'].Children.Clear();self.c['Generate'].IsEnabled=False
            self.c['Status'].Text='Cambios pendientes de cálculo.'

    def collect(self):
        for i in (1,2,3):
            grid=self.c['Group'+str(i)]
            if not grid.CommitEdit(DataGridEditingUnit.Cell,True) or not grid.CommitEdit(DataGridEditingUnit.Row,True):
                raise ValueError('Complete la edición numérica de la tabla.')
        c=copy.deepcopy(self.config)
        c['widths']=[{'width':int(r['Anchura_mm']),'category':int(r['Categoria']),'penalty':float(r['Penalizacion']),'enabled':bool(r['Activa'])} for r in self.table.Rows if r.RowState!=DataRowState.Deleted]
        for key,label,path in FIELDS:
            value=float(self.field_controls[key].Text.replace(',','.'))
            if path:c[path[0]][path[1]]=value
            else:c[key]=value
        c['direction']='N-S' if self.c['Direction'].SelectedIndex==0 else 'E-O'
        c['edges']={'left':self.c['LeftEdge'].Text,'right':self.c['RightEdge'].Text}
        settings.validate_settings(c)
        return c

    def try_save(self,sender,args):
        try:
            c=self.collect();settings.save_settings(c);self.config=c
            self.c['Status'].Text='Preferencias guardadas para próximas sesiones.'
            return True
        except Exception as exc:
            self.c['Status'].Text=str(exc)
            return False

    def closing(self,sender,args):
        if not self.try_save(None,None):
            args.Cancel=MessageBox.Show('Edición no válida o error de guardado. ¿Cerrar conservando las últimas preferencias guardadas?','Configuración',MessageBoxButton.YesNo)!=MessageBoxResult.Yes

    def describe(self):
        try:
            if not self.source:raise ValueError('Seleccione un forjado rectangular.')
            d=snapshot(self.doc,self.source);u0,u1,v0,v1=dimensions(d['bounds'],self.config['direction'])
            self.c['FloorLabel'].Text='{} · {} · Desfase {:.2f} mm · Longitud {:.2f} mm · Ancho {:.2f} mm'.format(d['type_name'],d['level_name'],mm(d['offset']),u1-u0,v1-v0)
        except Exception as exc:self.c['FloorLabel'].Text=str(exc)

    def direction_changed(self,sender,args):
        self.invalidate(sender,args)
        if self.try_save(None,None):self.describe()

    def select(self,sender,args):self.action='select';self.win.Close()
    def cancel(self,sender,args):self.win.Close()

    def add(self,sender,args):
        self.table.Rows.Add(683,self.c['Catalog'].SelectedIndex+1,10.0,True)
        self.invalidate(None,None);self.try_save(None,None)

    def delete(self,sender,args):
        grid=self.c['Group'+str(self.c['Catalog'].SelectedIndex+1)]
        if grid.SelectedItem is not None:
            grid.SelectedItem.Delete();self.invalidate(None,None);self.try_save(None,None)

    def reset(self,sender,args):
        if MessageBox.Show('¿Restaurar todo el catálogo y las preferencias predeterminadas?','Restaurar',MessageBoxButton.YesNo)==MessageBoxResult.Yes:
            self.config=settings.make_default_settings();settings.save_settings(self.config)
            self.action='select' if self.source is None else 'reopen'
            self.loading=True
            self.win.Closing-=self.closing
            self.win.Close()
            replacement=Dialog(self.doc,self.source,self.config,os.path.dirname(__file__))
            replacement.win.ShowDialog()
            self.config=replacement.config;self.action=replacement.action;self.saved=replacement.saved;self.solutions=replacement.solutions;self.c=replacement.c

    def calculate(self,sender,args):
        self.invalidate(None,None)
        if not self.try_save(None,None):return
        try:
            self.saved=snapshot(self.doc,self.source)
            u0,u1,v0,v1=dimensions(self.saved['bounds'],self.config['direction'])
            self.solutions=solve_k_best(v1-v0,self.config)
            for rank,s in enumerate(self.solutions,1):
                detail=', '.join('{}×{}'.format(n,o['width']) for n,o in zip(s['counts'],s['options']) if n)
                margins='H izq. {:.2f} / der. {:.2f} mm'.format(s['left'],s['right']) if s['valid'] else 'Sobrante {:.2f} / déficit {:.2f} mm'.format(s['excess'],s['deficit'])
                text=TextBlock();text.TextWrapping=__import__('System').Windows.TextWrapping.Wrap
                text.Text=('#{} · Puntuación {} · {} placas · 1200: {} · 900: {} · Especiales: {}\n{} · Mínima {} mm · {} · Placas {:.3f} m²\n{} · Tolerancia acumulada ±{} mm; extremos {} compatibles nominalmente.').format(rank,s['score'][0],s['plate_count'],s['order'].count(1200),s['order'].count(900),sum(w not in (1200,900) for w in s['order']),detail,min(s['order']),margins,(u1-u0)*s['total_width']/1e6,s['warning'],s['tolerance_sum'],'sí' if s['tolerance_extremes_fit'] else 'no')
                self.c['Solutions'].Items.Add(text)
            if self.solutions:self.c['Solutions'].SelectedIndex=0
            else:self.c['Status'].Text='Sin combinaciones.'
        except Exception as exc:alert(exc)

    def reverse(self,sender,args):
        self.config['reverse']=not self.config['reverse']
        settings.save_settings(self.config)
        for s in self.solutions:s['order'].reverse()
        self.choose(None,None)

    def choose(self,sender,args):
        i=self.c['Solutions'].SelectedIndex
        self.c['Generate'].IsEnabled=i>=0 and i<len(self.solutions) and self.solutions[i]['valid']
        canvas=self.c['Preview'];canvas.Children.Clear()
        if i<0 or i>=len(self.solutions):return
        s=self.solutions[i];x0,y0,x1,y1=self.saved['bounds']
        preview_solution=dict(s)
        if not s['valid']:
            preview_solution.update(valid=True,left=self.config['left'][1],right=self.config['right'][1])
            preview_solution['right']=(x1-x0 if self.config['direction']=='N-S' else y1-y0)-sum(s['order'])-preview_solution['left']
        profiles=plate_profiles(self.saved['bounds'],self.config['direction'],preview_solution) if s['valid'] else []
        if not s['valid']:
            u0,u1,v0,v1=dimensions(self.saved['bounds'],self.config['direction'])
            cursor=v0+self.config['left'][1]
            for w in s['order']:
                uv=[(u0,cursor),(u1,cursor),(u1,cursor+w),(u0,cursor+w)]
                profiles.append([(v,u) if self.config['direction']=='N-S' else (u,v) for u,v in uv]);cursor+=w
        extent_x=max([x1]+[x for p in profiles for x,y in p])-x0
        extent_y=max([y1]+[y for p in profiles for x,y in p])-y0
        scale=min(900/extent_x,105/extent_y)
        border=Rectangle();border.Width=(x1-x0)*scale;border.Height=(y1-y0)*scale;border.Stroke=Brushes.Black;Canvas.SetLeft(border,30);Canvas.SetTop(border,20);canvas.Children.Add(border)
        colors={1:'#B2DAD0',2:'#E8D594',3:'#DEB3B0'}
        for w,profile in zip(s['order'],profiles):
            o=next(o for o in s['options'] if o['width']==w)
            px=min(x for x,y in profile);py=min(y for x,y in profile);qx=max(x for x,y in profile);qy=max(y for x,y in profile)
            rect=Rectangle();rect.Width=(qx-px)*scale;rect.Height=(qy-py)*scale;rect.Fill=BrushConverter().ConvertFromString(colors[o['category']]);rect.Stroke=Brushes.White
            left=30+(px-x0)*scale;top=20+(y1-qy)*scale
            Canvas.SetLeft(rect,left);Canvas.SetTop(rect,top);canvas.Children.Add(rect)
            label=TextBlock();label.Text=str(w);label.FontSize=9;Canvas.SetLeft(label,left+2);Canvas.SetTop(label,top+2);canvas.Children.Add(label)
        label=TextBlock();label.Text=('↑ ' if self.config['direction']=='N-S' else '→ ')+'Longitud '+self.config['direction']+' · H izq./der.: '+str(s['left'])+' / '+str(s['right'])+' mm'+(' · NO GENERABLE' if not s['valid'] else '');Canvas.SetLeft(label,30);Canvas.SetTop(label,135);canvas.Children.Add(label)

    def generate(self,sender,args):
        if self.c['Generate'].IsEnabled and self.try_save(None,None):
            self.action='generate';self.win.Close()
