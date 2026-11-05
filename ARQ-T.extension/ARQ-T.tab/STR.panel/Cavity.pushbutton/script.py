#! python3
import os
import sys
import math
import importlib
import uuid

ROOT=os.path.dirname(__file__)
if ROOT not in sys.path:
    sys.path.insert(0,ROOT)
try:
    import shapely
except ImportError:
    sys.path.append(os.path.join(os.environ['LOCALAPPDATA'],'Programs','Python','Python312','Lib','site-packages'))
    import shapely
import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
from System.Windows.Markup import XamlReader
from System.Windows import MessageBox, MessageBoxButton, MessageBoxResult
from pyrevit import revit
from pyrevit.api import DB, UI
from pyrevit.revit.db import query
import cavity_geometry
import cavity_grid
import cavity_revit
for module in (cavity_geometry,cavity_grid,cavity_revit):
    importlib.reload(module)


class FloorFilter(UI.Selection.ISelectionFilter):
    __namespace__='ARQT.Cavity.Selection'+uuid.uuid4().hex
    def AllowElement(self,e):
        return isinstance(e,DB.Floor)
    def AllowReference(self,r,p):
        return False


def alert(message):
    MessageBox.Show(str(message),'ARQ-T | CAVITY')


def main():
    doc,uidoc=revit.doc,revit.uidoc
    if not doc or doc.IsFamilyDocument:
        alert('Abra un documento de proyecto.')
        return
    entries=[]
    for symbol in cavity_geometry.symbols(doc):
        try:
            dims=cavity_geometry.dimensions(symbol)
            compatible=str(symbol.Family.FamilyPlacementType) in ('WorkPlaneBased','OneLevelBased','OneLevelBasedHosted')
            entries.append((symbol,dims,compatible))
        except Exception as exc:
            entries.append((symbol,None,False))
    if not entries:
        alert('No hay tipos cuyo nombre contenga IGLU.')
        return
    selected=[doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    floor=selected[0] if len(selected)==1 and isinstance(selected[0],DB.Floor) else None
    state={'type':0,'gap':'0','offset':'0','orientation':0}
    while True:
        with open(os.path.join(ROOT,'window.xaml'),encoding='utf-8') as f:
            win=XamlReader.Parse(f.read())
        controls={n:win.FindName(n) for n in ('FloorLabel','SelectFloor','Types','Gap','Offset','Orientation','Calculate','Results','Generate','Cancel')}
        action={'value':None,'plan':None}
        for symbol,dims,compatible in entries:
            size=' × '.join('{:.1f}'.format(v*304.8) for v in dims) if dims else 'Dimensiones no disponibles'
            controls['Types'].Items.Add('{} / {} / {}{}'.format(symbol.FamilyName,query.get_name(symbol),size,'' if compatible else ' · NO COMPATIBLE'))
        controls['Types'].SelectedIndex=state['type']
        controls['Gap'].Text=state['gap']
        controls['Offset'].Text=state['offset']
        controls['Orientation'].SelectedIndex=state['orientation']
        if floor:
            controls['FloorLabel'].Text='Suelo: {} [ID {}] · Nivel: {}'.format(query.get_name(floor),floor.Id.Value,query.get_name(doc.GetElement(floor.LevelId)))
        def invalidate(sender,args):
            action['plan']=None
            controls['Generate'].IsEnabled=False
            controls['Results'].Text='Parámetros cambiados. Recalcule la distribución.'
        for name in ('Gap','Offset'):
            controls[name].TextChanged+=invalidate
        for name in ('Types','Orientation'):
            controls[name].SelectionChanged+=invalidate
        def select(sender,args):
            action['value']='select'
            win.Close()
        def calculate(sender,args):
            invalidate(None,None)
            try:
                if not floor:
                    raise ValueError('Seleccione un suelo.')
                gap=float(controls['Gap'].Text.replace(',','.'))/304.8
                offset=float(controls['Offset'].Text.replace(',','.'))/304.8
                if not math.isfinite(gap) or not math.isfinite(offset):
                    raise ValueError('Introduzca números finitos.')
                symbol,dims,compatible=entries[controls['Types'].SelectedIndex]
                if not compatible:
                    raise ValueError('Este tipo no tiene dimensiones o colocación compatibles.')
                data=cavity_geometry.floor_data(floor)
                candidates=[]
                choice=controls['Orientation'].SelectedIndex
                for quarter in ([0,1] if choice==0 else [choice-1]):
                    width,height=(dims[0],dims[1]) if quarter==0 else (dims[1],dims[0])
                    try:
                        plan=cavity_grid.solve(data['poly'],width,height,gap)
                        plan['rotation']=quarter*math.pi/2
                        candidates.append(plan)
                    except ValueError as exc:
                        if 'No cabe' not in str(exc):
                            raise
                if not candidates:
                    raise ValueError('No cabe ninguna pieza completa.')
                plan=max(candidates,key=lambda p:p['score'])
                action['plan']=(symbol,dims,data,plan,offset)
                margins=' / '.join('{:.1f}'.format(v*304.8) for v in plan['margins'])
                controls['Results'].Text=('{} piezas · {} filas · {} columnas de retícula\n'
                    'Suelo X × Y: {:.2f} × {:.2f} mm\n'
                    'Matriz completa X × Y: {:.2f} × {:.2f} mm\n'
                    'Inicio desde borde X / Y (sobrante ÷ 2): {:.2f} / {:.2f} mm\n'
                    'Superficie ocupada: {:.3f} m²\nSuperficie sobrante: {:.3f} m²\n'
                    'Márgenes izq./der./inf./sup.: {} mm\n'
                    'Cota base: {:.3f} m · Giro local: {:.0f}°\n{}').format(
                    plan['count'],plan['rows'],plan['columns'],
                    *(v*304.8 for pair in (plan['floor_size'],plan['matrix_size'],plan['start_margins']) for v in pair),
                    plan['occupied']*0.09290304,
                    data['area']*0.09290304-plan['occupied']*0.09290304,margins,
                    (data['z']+offset)*0.3048,plan['rotation']*180/math.pi,
                    'Máximo rectangular exacto y centrado.' if plan['exact'] else 'Retícula centrada; el contorno y los huecos pueden limitar el equilibrado de márgenes.')
                if isinstance(doc.ActiveView,DB.View3D) and doc.ActiveView.IsSectionBoxActive:
                    controls['Results'].Text+='\nVista 3D con caja de sección activa: puede recortar el suelo y mostrar piezas partidas.'
                controls['Generate'].IsEnabled=True
            except Exception as exc:
                alert(exc)
        def generate(sender,args):
            if action['plan']:
                action['value']='generate'
                win.Close()
        def cancel(sender,args):
            win.Close()
        controls['SelectFloor'].Click+=select
        controls['Calculate'].Click+=calculate
        controls['Generate'].Click+=generate
        controls['Cancel'].Click+=cancel
        win.ShowDialog()
        state={'type':controls['Types'].SelectedIndex,'gap':controls['Gap'].Text,
               'offset':controls['Offset'].Text,'orientation':controls['Orientation'].SelectedIndex}
        if action['value']=='select':
            try:
                r=uidoc.Selection.PickObject(UI.Selection.ObjectType.Element,FloorFilter(),'Seleccione un suelo horizontal')
                floor=doc.GetElement(r.ElementId)
            except Exception:
                pass
            continue
        if action['value']=='generate':
            symbol,dims,data,plan,offset=action['plan']
            old=cavity_revit.previous(doc,floor)
            replace=False
            if old:
                answer=MessageBox.Show('Ya existen {} piezas CAVITY en este suelo.\nSí: sustituir matriz anterior.\nNo: crear matriz adicional.\nCancelar: salir sin cambios.'.format(len(old)),
                    'ARQ-T | CAVITY',MessageBoxButton.YesNoCancel)
                if answer==MessageBoxResult.Cancel:
                    continue
                replace=answer==MessageBoxResult.Yes
            try:
                count=cavity_revit.generate(doc,floor,symbol,data,plan,dims,offset,replace)
                margins=plan['actual_margins']
                message=('{} piezas creadas.\nRetícula colocada desde la mitad del sobrante en X e Y.\n'
                       'X · Izquierda: {:.2f} mm / Derecha: {:.2f} mm\n'
                       'Y · Inferior: {:.2f} mm / Superior: {:.2f} mm\n'
                       'Puede deshacer toda la operación desde Revit.').format(count,*(m*304.8 for m in margins))
                if plan['view_cut_count'] or plan['view_hidden_count']:
                    message+='\n\nLa caja de sección de esta vista recorta {} piezas y oculta otras {}. Las piezas están completas. Desactive o amplíe la caja para revisar los márgenes reales.'.format(plan['view_cut_count'],plan['view_hidden_count'])
                alert(message)
                return
            except Exception as exc:
                alert('No se ha conservado la operación:\n'+str(exc))
                continue
        return


try:
    main()
except Exception as exc:
    alert('No se pudo iniciar CAVITY:\n'+str(exc))
