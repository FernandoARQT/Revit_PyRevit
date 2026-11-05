"""Modeless controller for the existing Distribuir ventilación WPF controls."""
import copy
import json
import os
import traceback
import clr
clr.AddReference('PresentationFramework');clr.AddReference('PresentationCore');clr.AddReference('WindowsBase');clr.AddReference('System.Data')
from System import String,Boolean,Action,Array,TimeSpan,AppDomain
from System.Data import DataTable
from System.Windows import MessageBox,MessageBoxButton,MessageBoxResult
from System.Windows.Markup import XamlReader
from System.Windows.Threading import DispatcherTimer,DispatcherPriority,DispatcherFrame,Dispatcher
from System.Windows.Controls import DataGridEditingUnit,DataGridCellInfo,DataGridEditAction
from . import service,config,ownership,revit_read,revit_write,proposal as model,proposal_store as store,proposal_adapter as adapter,airflow
from .plan_canvas import PlanCanvas,label
from .runtime import library_context

WINDOWS={}


def table(columns,rows):
    result=DataTable()
    for name,kind in columns:result.Columns.Add(name,kind)
    for values in rows:
        row=result.NewRow()
        for key,value in values.items():row[key]=value
        result.Rows.Add(row)
    return result


def show(doc,uidoc,_on_loaded=None,_modeless=None):
    if doc is None or doc.IsFamilyDocument:raise ValueError('Abra un documento de proyecto.')
    document_id=ownership.document_id(doc);modeless=(_on_loaded is None) if _modeless is None else _modeless
    window_key='ARQT_VentilationWindow_'+document_id
    existing=AppDomain.CurrentDomain.GetData(window_key) if modeless else None
    if existing is not None:existing.Activate();return WINDOWS.get(document_id)
    path=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','ARQ-T.tab','MEP.panel','DistribuirVentilacion.pushbutton','window.xaml'))
    with open(path,encoding='utf8') as f:window=XamlReader.Parse(f.read())
    names=('DocumentLabel Housing Phase Scope Level Rooms UseColumn Flows Terminals Preview CandidateInfo '
     'Resolution Wall Spacing Tolerance Weights Owned Totals Issues Details Analyze IncludeAll IncludeNone FromSelection '
     'Save Calculate Optimize Place Cancel RefreshOwned Lock Unlock Validate Tabs MainBedroom SetMainBedroom AddSupply AddExtract '
     'DeleteTerminal Undo Redo FitPlan Rebalance RebalanceAll KitchenPoint KitchenPolygon FinishKitchen DetectKitchen Zones DeleteZone '
     'ReoptFree ReoptRoom LoadDraft ExportPreview SelectedInfo LockPosition LockFlow LockFull NextAlternative '
     'ConfirmFunction FlowCountColumn TerminalTypeColumn').split()
    ui={name:window.FindName(name) for name in names}
    proposal_actions=('AddSupply AddExtract DeleteTerminal FitPlan Undo Redo Rebalance RebalanceAll ReoptFree ReoptRoom KitchenPoint KitchenPolygon FinishKitchen DetectKitchen DeleteZone ExportPreview ConfirmFunction NextAlternative LockPosition LockFlow LockFull').split()
    for name in proposal_actions:ui[name].IsEnabled=False
    state={'settings':config.load(),'analysis':None,'proposal':None,'flows':None,'busy':False,'cancelled':False,'document_id':document_id,
       'room_table':None,'flow_table':None,'terminal_table':None,'owned_table':None,'levels':[],'phases':[],'selected':None,
       'rendering':False,'outdated':False,'cell_edit':None,'modeless':modeless,'window':window,'ui':ui}
    window.Tag=state
    from System.Windows import SystemParameters
    work=SystemParameters.WorkArea;window.Width=min(window.Width,max(900,work.Width-30));window.Height=min(window.Height,max(700,work.Height-30))
    window.MinWidth=min(window.MinWidth,window.Width);window.MinHeight=min(window.MinHeight,window.Height)
    scheduler=None
    if modeless:
        from .external_event import ApiQueue
        from System.Windows.Interop import WindowInteropHelper
        scheduler=ApiQueue();state['scheduler']=scheduler;WindowInteropHelper(window).Owner=uidoc.Application.MainWindowHandle
        WINDOWS[document_id]=state;AppDomain.CurrentDomain.SetData(window_key,window)
    s=state['settings'];s.pop('phase_id',None);phase=s.get('phase_by_document',{}).get(document_id)
    if phase is not None:s['phase_id']=phase
    role_names={'impulsion':'Impulsión','extraccion':'Extracción'}
    ui['UseColumn'].ItemsSource=Array[String](list(airflow.USES));ui['TerminalTypeColumn'].ItemsSource=Array[String](list(role_names.values()))
    ui['DocumentLabel'].Text=doc.Title+' · DB-HS 3'
    for control,key in [('Resolution','resolution_mm'),('Wall','wall_clearance_mm'),('Spacing','terminal_spacing_mm'),('Tolerance','tolerance_mm')]:ui[control].Text=str(s[key])
    ui['Weights'].Text=json.dumps(s['weights']);groups=s['housing_groups'].setdefault(document_id,{})
    for group in sorted(groups):ui['Housing'].Items.Add(group)
    ui['Housing'].Text=next(iter(sorted(groups)),'Vivienda 1')

    def info(message):ui['CandidateInfo'].Text=str(message)
    def error(exc,modal=True):
        ui['Issues'].Text=str(exc);ui['Details'].IsExpanded=True;print(traceback.format_exc())
        if modal:MessageBox.Show(str(exc),'ARQ-T | Distribuir ventilación')
    def yes(message):return MessageBox.Show(message,'ARQ-T | Revisar propuesta',MessageBoxButton.YesNo)==MessageBoxResult.Yes
    def commit_tables():
        for name in ('Rooms','Flows','Terminals'):
            ui[name].CommitEdit(DataGridEditingUnit.Cell,True);ui[name].CommitEdit(DataGridEditingUnit.Row,True)

    def read_inputs():
        commit_tables();settings=copy.deepcopy(state['settings'])
        for control,key in [('Resolution','resolution_mm'),('Wall','wall_clearance_mm'),('Spacing','terminal_spacing_mm'),('Tolerance','tolerance_mm')]:settings[key]=float(ui[control].Text.replace(',','.'))
        settings['weights']=json.loads(ui['Weights'].Text)
        if ui['Phase'].SelectedIndex>=0:
            settings['phase_id']=state['phases'][ui['Phase'].SelectedIndex]['id'];settings.setdefault('phase_by_document',{})[document_id]=settings['phase_id']
        uids=[];scope_uids=[]
        for r in state['room_table'].Rows if state['room_table'] is not None else []:
            uid=str(r['Uid']);settings['overrides'].setdefault(uid,{})['use']=str(r['Use'])
            if r['Include']:uids.append(uid)
            if r['Include'] and r['Place']:scope_uids.append(uid)
        if state['proposal'] is None:
            for r in state['flow_table'].Rows if state['flow_table'] is not None else []:
                count=float(str(r['Count']).replace(',','.'))
                if not count.is_integer():raise ValueError('El número de bocas debe ser entero.')
                settings['overrides'][str(r['Key'])]={'design':float(str(r['Requested']).replace(',','.')),'count':int(count)}
        group=ui['Housing'].Text.strip()
        if not group:raise ValueError('Indique el nombre de la vivienda.')
        settings['housing_groups'].setdefault(document_id,{})[group]=sorted(uids)
        main=settings.get('main_bedrooms',{}).get(document_id,{}).get(group)
        if main:settings['main_bedroom_uid']=main
        else:settings.pop('main_bedroom_uid',None)
        scope={'kind':'all'}
        if ui['Scope'].SelectedIndex==1:
            if ui['Level'].SelectedIndex<0:raise ValueError('Seleccione una planta.')
            scope={'kind':'level','level_id':state['levels'][ui['Level'].SelectedIndex][0]}
        elif ui['Scope'].SelectedIndex==2:scope={'kind':'rooms','room_uids':scope_uids}
        return config.validate(settings),sorted(uids),scope

    def pump_cancel():
        frame=DispatcherFrame();window.Dispatcher.BeginInvoke(DispatcherPriority.Background,Action(lambda:setattr(frame,'Continue',False)));Dispatcher.PushFrame(frame)
        return state['cancelled']
    def api(fn):
        if state['busy']:return
        state['busy']=True;state['cancelled']=False;ui['Place'].IsEnabled=False
        @library_context()
        def run(app=None):
            try:
                if app is not None and (app.ActiveUIDocument is None or app.ActiveUIDocument.Document!=doc):raise ValueError('Active el proyecto de esta propuesta antes de ejecutar la operación.')
                fn()
            except Exception as exc:error(exc)
            finally:state['busy']=False;ui['Place'].IsEnabled=bool(state.get('proposal') and not state['outdated'])
        if scheduler:
            try:scheduler.submit(run)
            except Exception as exc:state['busy']=False;error(exc)
        else:run()
    def selected():
        if not state['proposal'] or not state['selected']:raise ValueError('Seleccione una rejilla de la propuesta.')
        return model.terminal(state['proposal'],state['selected'])
    def choose_terminal(tid):
        state['selected']=tid;plan.selected=tid
        if state['terminal_table'] is not None:
            for i,row in enumerate(state['terminal_table'].Rows):
                if str(row['Key'])==tid and ui['Terminals'].SelectedIndex!=i:ui['Terminals'].SelectedIndex=i;break
        if ui['Terminals'].SelectedItem is not None:ui['Terminals'].ScrollIntoView(ui['Terminals'].SelectedItem)
        properties();plan.draw()
    def edit_terminal(tid):
        choose_terminal(tid);grid=ui['Terminals'];grid.Focus()
        grid.CurrentCell=DataGridCellInfo(grid.SelectedItem,grid.Columns[3]);grid.BeginEdit()
    plan=PlanCanvas(ui['Preview'],lambda:state['proposal'],lambda:state['levels'][ui['Level'].SelectedIndex][0] if ui['Level'].SelectedIndex>=0 else None,
        lambda op,payload,version:request(op,payload,version),choose_terminal,info,edit_terminal)
    state['plan']=plan

    def properties(force=False):
        if not state['proposal'] or not state['selected']:return
        try:t=selected()
        except StopIteration:return
        p=state['proposal'];r=model.row(p,t['room_uid'],t['role']);room=model.room(p,t['room_uid'])
        balance=' · estancia: {:.2f} / {:.2f} L/s asignados'.format(r['assigned'],r['design']) if r else ''
        ui['SelectedInfo'].Text='{} · {} · {:.2f} L/s · {:.1f}°{}'.format(label(t),room['name'],t['flow'],t.get('slope_deg',0),balance)
        for control,key,text in [('LockPosition','lock_position','posición'),('LockFlow','lock_flow','caudal'),('LockFull','lock_full','todo')]:
            ui[control].Header=('Desbloquear ' if t[key] else 'Bloquear ')+text
    def render_flows(flows):
        ui['FlowCountColumn'].IsReadOnly=state['proposal'] is not None
        rooms={r['uid']:r for r in state['analysis']['rooms']}
        rows=[dict(Key=r['key'],Level=rooms[r['room_uid']]['level'],Room=r['name'],Role=r['role'],Area='{:.2f}'.format(r['area_m2']),Minimum=str(r['minimum']),
            Requested=repr(r['design'] if state['proposal'] else r.get('requested',r['design'])),Count=str(r['count']),Assigned='{:.3f}'.format(r.get('assigned',0))) for r in flows['rows']]
        state['flow_table']=table([(k,String) for k in ('Key Level Room Role Area Minimum Requested Count Assigned').split()],rows);ui['Flows'].ItemsSource=state['flow_table'].DefaultView
    def render():
        p=state['proposal']
        if not p:return
        state['rendering']=True
        try:
            state['flows']=p['flows'];state['analysis']=p['analysis'];state['settings']=copy.deepcopy(p['settings']);render_flows(p['flows']);ts=model.active(p)
            rows=[dict(Key=t['terminal_id'],Label=label(t),Room=t['room_name'],Role=role_names[t['role']],Flow='{:.3f}'.format(t['flow']),X='{:.2f}'.format(t['x']),Y='{:.2f}'.format(t['y']),Z='{:.2f}'.format(t['z'])) for t in ts]
            state['terminal_table']=table([(k,String) for k in ('Key Label Room Role Flow X Y Z').split()],rows);ui['Terminals'].ItemsSource=state['terminal_table'].DefaultView
            v=p['validation'];ui['Totals'].Text='{} rejillas · impulsión {:.2f} · extracción {:.2f} L/s'.format(len(ts),v['supply_lps'],v['extract_lps'])
            messages=(['Actualice el análisis: el modelo ha cambiado.'] if state['outdated'] else [])+v['errors']+v['warnings']+p['warnings']
            for i,message in enumerate(messages):
                for t in ts:message=message.replace(t['terminal_id'],label(t)+' · '+t['room_name'])
                messages[i]=message
            messages=list(dict.fromkeys(messages));ui['Issues'].Text='\n'.join(messages)
            ui['Details'].Header='Avisos y detalles'+(' · '+str(len(messages)) if messages else '')
            ui['Place'].IsEnabled=not state['busy'] and not state['outdated']
            prior_zone=plan.zone_selected;ui['Zones'].Items.Clear()
            for z in p['zones']:ui['Zones'].Items.Add(z['use']+' · '+z['zone_id'][-8:])
            if p['zones']:ui['Zones'].SelectedIndex=next((i for i,z in enumerate(p['zones']) if z['zone_id']==prior_zone),0)
            ids=[t['terminal_id'] for t in ts]
            if state['selected'] not in ids:state['selected']=ids[0] if ids else None
            if state['selected']:ui['Terminals'].SelectedIndex=ids.index(state['selected']);plan.selected=state['selected']
            properties();plan.draw();state['store_mtime']=os.stat(store._path(document_id,p['proposal_id'])).st_mtime_ns
            for name in proposal_actions:ui[name].IsEnabled=True
        finally:state['rendering']=False
    @library_context()
    def request(operation,payload=None,expected=None):
        if state['busy']:raise ValueError('Espere a que termine la operación API.')
        p=state['proposal']
        if p is None:raise ValueError('Optimice o recupere una propuesta primero.')
        if ui['Housing'].Text!=p['housing_id']:raise ValueError('Seleccione o genere la propuesta de la vivienda indicada antes de editar.')
        payload=dict(payload or {});expected=p['version'] if expected is None else expected
        if operation=='add_ventilation_terminal' and model.row(p,payload['room_uid'],payload['role']) is None:
            if not yes('Esta función no está prevista por el uso de la habitación. ¿Añadirla con 8 L/s provisionales y revisar su caudal en la tabla?'):return p
            payload.update(flow_lps=8,confirm_warnings=True)
        try:result=store.update(document_id,p['proposal_id'],expected,operation,payload)
        except ValueError as exc:
            if str(exc).startswith('CONFIRM_WARNINGS:') and yes(str(exc).split(':',1)[1]+'\n\n¿Mantener esta función/posición provisional?'):
                payload['confirm_warnings']=True;result=store.update(document_id,p['proposal_id'],expected,operation,payload)
            else:raise
        state['proposal']=result;render();return result
    def safe(fn):
        @library_context()
        def action(*args):
            try:fn()
            except Exception as exc:error(exc)
        return action

    def fill_rooms(analysis,selected_uids=None):
        state['analysis']=analysis;state['phases']=analysis['phases'];old=state['settings'].get('phase_id');ui['Phase'].Items.Clear()
        for phase in state['phases']:ui['Phase'].Items.Add(phase['name'])
        ui['Phase'].SelectedIndex=next((i for i,p in enumerate(state['phases']) if p['id']==old),0 if len(state['phases'])==1 else -1)
        selected_uids=set(selected_uids if selected_uids is not None else state['settings']['housing_groups'].get(document_id,{}).get(ui['Housing'].Text,[]))
        rows=[dict(Uid=r['uid'],Include=r['uid'] in selected_uids,Place=r['uid'] in selected_uids,Number=r['number'],Level=r['level'],Name=r['name'],Use=r.get('use',airflow.classify_room(r['name'])),Area='{:.2f}'.format(r['area_m2']),
            Ceilings=' / '.join('{} · {:.1f}°'.format(c['id'],c.get('slope_deg',0)) for c in r.get('ceilings',[])) or 'Sin techo apto') for r in analysis['rooms'] if not airflow.is_exterior(r['name'])]
        state['room_table']=table([(k,Boolean if k in ('Include','Place') else String) for k in ('Uid Include Place Number Level Name Use Area Ceilings').split()],rows);ui['Rooms'].ItemsSource=state['room_table'].DefaultView
        prior=state['levels'][ui['Level'].SelectedIndex][0] if state['levels'] and ui['Level'].SelectedIndex>=0 else None
        state['levels']=sorted({(r['level_id'],r['level']) for r in analysis['rooms']},key=lambda r:r[1]);ui['Level'].Items.Clear()
        for lid,name in state['levels']:ui['Level'].Items.Add(name)
        if state['levels']:ui['Level'].SelectedIndex=next((i for i,v in enumerate(state['levels']) if v[0]==prior),0)
        state['bedrooms']=[r for r in analysis['rooms'] if airflow.room_use(r,state['settings']['overrides']) in ('dormitorio','dormitorio_mixto')];ui['MainBedroom'].Items.Clear()
        for r in state['bedrooms']:ui['MainBedroom'].Items.Add(r['name']+' · '+r['number'])
        ui['Issues'].Text='\n'.join(analysis['issues'])
    def analyze():
        settings,uids,scope=read_inputs();state['settings']=settings
        if state['proposal'] and (state['proposal']['settings'].get('phase_id')!=settings.get('phase_id') or state['proposal']['housing_id']!=ui['Housing'].Text):
            state['proposal']=None;state['selected']=None;plan.draw()
        if state['proposal']:
            if settings!=state['proposal']['settings']:
                state['busy']=False
                try:request('set_ventilation_configuration',{'settings':settings})
                finally:state['busy']=True
            state['proposal']=adapter.refresh(doc,state['proposal']['proposal_id'],state['proposal']['version']);state['outdated']=False
            fill_rooms(state['proposal']['analysis'],state['proposal']['room_uids']);render()
        else:fill_rooms(service.analyze_ventilation_rooms(doc,settings),uids)
    def calculate():
        settings,uids,scope=read_inputs();state['settings']=settings
        if state['proposal'] and sorted(uids)==state['proposal']['room_uids'] and state['proposal']['housing_id']==ui['Housing'].Text and state['proposal']['settings'].get('phase_id')==settings.get('phase_id'):
            values=[(str(r['Key']),float(str(r['Requested']).replace(',','.'))) for r in state['flow_table'].Rows]
            if settings!=state['proposal']['settings']:
                state['busy']=False
                try:request('set_ventilation_configuration',{'settings':settings})
                finally:state['busy']=True
            for key,value in values:
                r=next(v for v in state['proposal']['flows']['rows'] if v['key']==key)
                if abs(value-r['design'])>1e-6:
                    state['busy']=False
                    try:request('set_room_design_flow',{'room_uid':r['room_uid'],'role':r['role'],'flow_lps':value,'redistribute':True})
                    finally:state['busy']=True
            render();ui['Tabs'].SelectedIndex=1;return
        if state['proposal']:
            state['proposal']=None;state['selected']=None;plan.draw()
        if state['analysis'].get('phase_id')!=settings.get('phase_id'):
            fill_rooms(service.analyze_ventilation_rooms(doc,settings),uids)
        flows=service.calculate_ventilation_flows(state['analysis'],uids,settings);state['flows']=flows;render_flows(flows)
        ui['Totals'].Text='Diseño: impulsión {:.5f} L/s · extracción {:.5f} L/s · principal {}'.format(flows['supply'],flows['extract'],flows['main_bedroom']['uid'])
        main=flows['main_bedroom']['uid'];ui['MainBedroom'].SelectedIndex=next((i for i,r in enumerate(state['bedrooms']) if r['uid']==main),-1)
        ui['Issues'].Text='\n'.join(flows['warnings']);ui['Tabs'].SelectedIndex=1
    def optimize():
        settings,uids,scope=read_inputs();state['settings']=settings;p=state['proposal']
        if p and p['room_uids']==uids and p['scope']==scope and p['housing_id']==ui['Housing'].Text and p['settings'].get('phase_id')==settings.get('phase_id'):
            try:adapter.current(doc,p)
            except ValueError:state['proposal']=adapter.refresh(doc,p['proposal_id'],p['version'])
            state['busy']=False
            try:
                if settings!=state['proposal']['settings']:request('set_ventilation_configuration',{'settings':settings})
                request('reoptimize_ventilation')
            finally:state['busy']=True
        else:state['proposal']=adapter.create(doc,uids,settings,scope,ui['Housing'].Text)
        state['outdated']=False;render();ui['Tabs'].SelectedIndex=2
    def calculate_and_optimize():
        calculate();optimize()
    def rebalance(include_manual=False):
        p=state['proposal']
        if p is None:raise ValueError('Calcule una propuesta antes de equilibrar.')
        plan_data=model.rebalance_plan(p,include_manual)
        if not plan_data['compatible']:raise ValueError('; '.join(plan_data['issues']))
        text='\n'.join('{}: {:.4f} → {:.4f} L/s'.format(c['terminal_id'][-12:],c['before'],c['after']) for c in plan_data['changes'])
        if yes('Propuesta de reequilibrado: objetivo {:.4f} L/s por red.\n{}\n\n¿Aplicar estos ajustes a la propuesta?'.format(plan_data['target_lps'],text)):
            request('rebalance_ventilation',{'confirmed':True,'include_manual':include_manual},plan_data['base_version'])
    def place():
        p=store.get(document_id,state['proposal']['proposal_id'])
        if p['housing_id']!=ui['Housing'].Text:raise ValueError('La propuesta corresponde a otra vivienda seleccionada.')
        p=adapter.refresh(doc,p['proposal_id'],p['version'])
        state['proposal']=p;state['outdated']=False;render()
        validation=model.refresh(p)
        errors=[e for e in validation['errors'] if not e.startswith('Advertencias de función sin confirmar:')]
        if errors:raise ValueError('; '.join(errors))
        pending=[t for t in model.active(p) if model.design_warnings(p,t,[t['x'],t['y']])!=t.get('warnings_confirmed',[])]
        legacy=adapter.legacy_for_commit(p);deletes=[a for a in legacy['actions'] if a['action']=='eliminar propio obsoleto']
        warnings='\n'.join(label(t)+' · '+t['room_name']+': '+'; '.join(model.design_warnings(p,t,[t['x'],t['y']])) for t in pending)
        if not yes('Se aplicarán {} rejillas y se eliminarán {} elementos propios.\n{}\n\n¿Confirmar la propuesta completa y estos avisos para aplicarla en Revit?'.format(len(model.active(p)),len(deletes),warnings)):return
        if pending:p=store.update(document_id,p['proposal_id'],p['version'],'confirm_ventilation_warnings',{'terminal_ids':[t['terminal_id'] for t in pending],'confirmed':True})
        approved=adapter.approve(doc,p['proposal_id'],p['version'],True);result=adapter.commit(doc,p['proposal_id'],approved['version'],True,bool(deletes),pump_cancel)
        state['proposal']=result['proposal'];state['outdated']=False;render();MessageBox.Show('{} terminales aplicados y validados. La propuesta sigue disponible para editar.'.format(result['model_result']['count']),'ARQ-T | Ventilación')
    def set_main():
        index=ui['MainBedroom'].SelectedIndex
        if index<0:raise ValueError('Seleccione un dormitorio válido; los vestidores están excluidos.')
        settings,uids,scope=read_inputs();uid=state['bedrooms'][index]['uid']
        if uid not in uids:raise ValueError('El principal debe pertenecer a la vivienda seleccionada.')
        state['settings'].setdefault('main_bedrooms',{}).setdefault(document_id,{})[ui['Housing'].Text]=uid;state['settings']['main_bedroom_uid']=uid
        if state['proposal']:request('set_main_bedroom',{'room_uid':uid})
        else:api(calculate)
    def detect_kitchen():
        t=selected();uid=t['room_uid'];hints=[h for h in state['analysis'].get('kitchen_hints',[]) if h['room_uid']==uid]
        if not hints:raise ValueError('No hay evidencias de cocina en esta Room. Marque un punto o delimite su zona manualmente.')
        from shapely.geometry import MultiPoint
        from .spatial import polygon_from_rings
        geom=MultiPoint([h['position_mm'][:2] for h in hints]).convex_hull.buffer(600).simplify(25,preserve_topology=True).intersection(polygon_from_rings(model.room(state['proposal'],uid)['rings']))
        if geom.geom_type!='Polygon':raise ValueError('Las evidencias no definen una zona única; delimítela manualmente.')
        if yes('Zona aproximada basada en elementos reales:\n'+'\n'.join(str(h['element_id'])+' · '+h['label'] for h in hints)+'\n\n¿Usarla como zona funcional revisable?'):
            request('define_ventilation_zone',{'room_uid':uid,'polygon_mm':list(geom.exterior.coords),'source':'REVIT_ELEMENTS','use':'COCINA'})
    def export_preview():
        from .preview_image import export
        result=export(state['proposal'],state['levels'][ui['Level'].SelectedIndex][0]);state['last_capture']=result;info('Captura guardada y accesible: '+result['path'])
    def save_config():
        settings,uids,scope=read_inputs();config.save(settings);state['settings']=settings;ui['Issues'].Text='Configuración guardada. La propuesta se guarda por versión en cada edición.'
    def load_draft():
        drafts=store.list_proposals(document_id,ui['Housing'].Text)
        if not drafts:raise ValueError('No hay propuesta guardada para esta vivienda.')
        p=drafts[0];state['proposal']=p;state['settings']=copy.deepcopy(p['settings']);fill_rooms(p['analysis'],p['room_uids'])
        try:adapter.current(doc,p);state['outdated']=False
        except ValueError:state['outdated']=True
        render();ui['Tabs'].SelectedIndex=2
    def refresh_owned():
        rows=[dict(Id=str(e['id']),Room=e['metadata']['room_uid'],Role=e['metadata']['role'],Locked=str(e['metadata'].get('locked',False)),Pinned=str(e['pinned']),Manual=str(e['manual_change'])) for e in ownership.inventory(doc) if e['own']]
        state['owned_table']=table([(k,String) for k in ('Id Room Role Locked Pinned Manual').split()],rows);ui['Owned'].ItemsSource=state['owned_table'].DefaultView
    def lock_owned(value):
        row=ui['Owned'].SelectedItem
        if row is None:raise ValueError('Seleccione un terminal propio.')
        p=state['proposal']
        if not p:raise ValueError('Recupere o genere una propuesta antes de cambiar sus bloqueos.')
        t=next((t for t in p['terminals'] if t.get('existing_id')==int(str(row['Id']))),None)
        if t is None:raise ValueError('El elemento no pertenece a la propuesta actual.')
        request('set_proposal_terminal_lock',{'terminal_id':t['terminal_id'],'lock_full':value})
    def validate():
        if state['proposal']:p=state['proposal'];adapter.current(doc,p);result=model.refresh(p)
        else:result=revit_write.validate_ventilation_terminals(doc)
        ui['Issues'].Text=json.dumps(result,ensure_ascii=False,indent=2)
    def mark_all(value):
        commit_tables()
        for row in state['room_table'].Rows:row['Include']=value;row['Place']=value
    def from_selection():
        ids={revit_read.eid(i) for i in uidoc.Selection.GetElementIds()};uid_ids={r['uid']:r['id'] for r in state['analysis']['rooms']}
        for row in state['room_table'].Rows:row['Place']=uid_ids[str(row['Uid'])] in ids
        ui['Scope'].SelectedIndex=2
    def housing_changed(*args):
        if state['room_table'] is None or state['rendering']:return
        group=str(ui['Housing'].SelectedItem) if ui['Housing'].SelectedItem is not None else ui['Housing'].Text;chosen=set(state['settings']['housing_groups'].get(document_id,{}).get(group,[]))
        for row in state['room_table'].Rows:row['Include']=str(row['Uid']) in chosen;row['Place']=str(row['Uid']) in chosen
    def table_selection(*args):
        if state['rendering']:return
        row=ui['Terminals'].SelectedItem
        if row is not None:state['selected']=str(row['Key']);plan.selected=state['selected'];properties(True);plan.draw()
    def cell_edited(sender,e):
        if state['rendering'] or not state['proposal']:return
        snapshot=state['cell_edit'];state['cell_edit']=None
        if e.EditAction==DataGridEditAction.Cancel or snapshot is None:return
        header=str(e.Column.Header);value=str(e.EditingElement.SelectedItem) if header=='Tipo' else e.EditingElement.Text
        if value==snapshot['before']:return
        def apply():
            try:table_edit(snapshot['terminal_id'],header,value,snapshot['version'])
            except Exception as exc:
                state['proposal']=store.get(document_id,state['proposal']['proposal_id']);render();error(exc,modal=False)
        window.Dispatcher.BeginInvoke(DispatcherPriority.Background,Action(safe(apply)))
    def beginning_edit(sender,e):
        header=str(e.Column.Header);field={'Tipo':'Role','L/s':'Flow','X · mm':'X','Y · mm':'Y'}.get(header)
        if not field:return
        state['cell_edit']={'terminal_id':str(e.Row.Item['Key']),'version':state['proposal']['version'],'before':str(e.Row.Item[field])}
    def table_edit(tid,header,value,version):
        if header=='Tipo':return request('change_terminal_type',{'terminal_id':tid,'role':next(k for k,v in role_names.items() if v==value)},version)
        number=float(str(value).replace(',','.'))
        if header=='L/s':return request('set_terminal_flow',{'terminal_id':tid,'flow_lps':number},version)
        t=model.terminal(state['proposal'],tid);xy=[t['x'],t['y']];xy[0 if header=='X · mm' else 1]=number
        return request('move_ventilation_terminal',{'terminal_id':tid,'position_mm':xy},version)
    state['table_edit']=table_edit
    def toggle_lock(key):request('set_proposal_terminal_lock',{'terminal_id':selected()['terminal_id'],key:not selected()[key]})
    def confirm_function():
        t=selected();warnings=model.design_warnings(state['proposal'],t,[t['x'],t['y']])
        if not warnings:info('Esta rejilla no tiene avisos funcionales pendientes.');return
        if yes('\n'.join(warnings)+'\n\n¿Confirmar esta función y posición?'):request('confirm_ventilation_warnings',{'terminal_ids':[t['terminal_id']],'confirmed':True})
    def next_alternative():
        t=selected()
        if len(t['alternatives'])<2:info('No hay más alternativas disponibles.');return
        request('select_ventilation_alternative',{'terminal_id':t['terminal_id'],'alternative_index':1})
    def zone_selected(*args):
        p=state['proposal'];i=ui['Zones'].SelectedIndex;plan.zone_selected=p['zones'][i]['zone_id'] if p and 0<=i<len(p['zones']) else None;plan.draw()
    def delete_zone():
        if not plan.zone_selected:raise ValueError('Seleccione una zona.')
        request('delete_ventilation_zone',{'zone_id':plan.zone_selected})
    def reopt_room():request('reoptimize_ventilation',{'room_uid':selected()['room_uid']})
    native={'Analyze':analyze,'Calculate':calculate,'Optimize':calculate_and_optimize,'Place':place,'FromSelection':from_selection,'RefreshOwned':refresh_owned,'Validate':validate,'LoadDraft':load_draft}
    for name,fn in native.items():ui[name].Click+=lambda sender,args,callback=fn:api(callback)
    actions={'IncludeAll':lambda:mark_all(True),'IncludeNone':lambda:mark_all(False),'Save':save_config,
      'AddSupply':lambda:plan.set_mode('impulsion'),'AddExtract':lambda:plan.set_mode('extraccion'),'DeleteTerminal':lambda:request('delete_ventilation_terminal',{'terminal_id':selected()['terminal_id']}),
      'Undo':lambda:request('undo_ventilation'),'Redo':lambda:request('redo_ventilation'),'FitPlan':plan.fit,'Rebalance':rebalance,'RebalanceAll':lambda:rebalance(True),'ReoptFree':lambda:request('reoptimize_ventilation'),'ReoptRoom':reopt_room,
      'KitchenPoint':lambda:plan.set_mode('kitchen_point'),'KitchenPolygon':lambda:plan.set_mode('kitchen_polygon'),'FinishKitchen':plan.finish_polygon,'DetectKitchen':detect_kitchen,'DeleteZone':delete_zone,
      'ExportPreview':export_preview,'ConfirmFunction':confirm_function,'NextAlternative':next_alternative,
      'LockPosition':lambda:toggle_lock('lock_position'),'LockFlow':lambda:toggle_lock('lock_flow'),'LockFull':lambda:toggle_lock('lock_full'),'SetMainBedroom':set_main,
      'Lock':lambda:lock_owned(True),'Unlock':lambda:lock_owned(False)}
    for name,fn in actions.items():ui[name].Click+=safe(fn)
    ui['Housing'].SelectionChanged+=housing_changed;ui['Terminals'].SelectionChanged+=table_selection;ui['Terminals'].CellEditEnding+=cell_edited;ui['Terminals'].BeginningEdit+=beginning_edit
    ui['Level'].SelectionChanged+=lambda *args:plan.draw();ui['Zones'].SelectionChanged+=zone_selected
    timer=DispatcherTimer();timer.Interval=TimeSpan.FromMilliseconds(800)
    @library_context()
    def poll(*args):
        p=state['proposal']
        if not p or state['busy'] or state['rendering'] or state['cell_edit'] or plan.drag:return
        try:
            mtime=os.stat(store._path(document_id,p['proposal_id'])).st_mtime_ns
            if mtime!=state.get('store_mtime'):
                fresh=store.get(document_id,p['proposal_id'])
                if fresh['version']!=p['version']:state['proposal']=fresh;render();info('Propuesta actualizada.')
        except Exception as exc:info('No se pudo sincronizar la propuesta: '+str(exc))
    timer.Tick+=poll;state['poll']=poll;state['request']=request
    def closing(sender,args):
        if state['busy']:state['cancelled']=True;args.Cancel=True
    def closed(*args):
        timer.Stop()
        if scheduler:scheduler.close();WINDOWS.pop(document_id,None);AppDomain.CurrentDomain.SetData(window_key,None)
    window.Closing+=closing;window.Closed+=closed;ui['Cancel'].Click+=lambda *args:state.update(cancelled=True) if state['busy'] else window.Close()
    fill_rooms(service.analyze_ventilation_rooms(doc,state['settings']))
    if store.list_proposals(document_id,ui['Housing'].Text):
        try:load_draft()
        except Exception as exc:info(str(exc))
    if _on_loaded is not None:window.ContentRendered+=lambda *args:_on_loaded(window,ui,state)
    if modeless:timer.Start();window.Show()
    else:window.ShowDialog()
    return state
