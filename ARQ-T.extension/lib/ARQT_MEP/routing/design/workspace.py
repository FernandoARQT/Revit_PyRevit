"""Focused plan-first MEP workspace; one visible capture/generate/edit flow."""
import copy
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from threading import Event
from System import String,Boolean,Array,TimeSpan
from System.Windows import Visibility
from System.Windows.Markup import XamlReader
from System.Windows.Controls import DataGridEditingUnit
from System.Windows.Threading import DispatcherTimer
from . import model,store,workflow,products
from .canvas import CorridorCanvas
from ...plan_canvas import color
from ...runtime import library_context

NETWORK_LABELS={'SUPPLY':'Impulsión','EXTRACT':'Extracción','SHARED':'Compartido'}
BUDGETS=(30,120,240,600,1000)


class Workspace:
    def __init__(self,window,doc,uidoc,identity,configuration,catalog,dispatch):
        self.window=window;self.doc=doc;self.uidoc=uidoc;self.identity=identity
        self.configuration=copy.deepcopy(configuration);self.catalog=catalog;self.dispatch=dispatch
        self.proposal=None;self.levels=[];self.cids=[];self.studies=[];self.rendering=False
        self.busy=False;self.capturing=False;self.closed=False;self.future=None;self.started=None;self.last_stat=0;self.mtime=None
        self.cancel_event=Event();self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='ARQT-Plan')
        names=('DocumentLabel Studies NewStudy DuplicateStudy RecoverStudies ExportPlan HeatRecovery SupplyManifold ExtractManifold '
            'EquipmentInfo RefreshEquipment Terminals TerminalInfo OuterDiameter BendRadius Insulation Clearance Budget IncludeLinks '
            'Generate CancelGeneration Level FitPlan ManualNetwork DrawCorridor FinishDrawing CancelDrawing Undo Redo Corridors Plan EmptyPlan '
            'SelectionPanel SelectionTitle SelectionSummary CorridorNetwork CorridorConstraint Vertex PointX PointY MovePoint InsertPoint '
            'DeletePoint DeleteCorridor Status Progress Close Mode BranchProduct ProfileInfo CalculateAxes ReviewModel CommitModel '
            'Alternatives AlternativeInfo Diagnostics DiagnosticGraph LegacyPlanner AxisHeightPanel PointZ SetAxisHeight LockAxisSegment '
            'ManifoldNetwork ManifoldX ManifoldY ManifoldAngle MoveManifold RotateManifold MainDuctType MainDiameter MainExterior MainRadius').split()
        self.ui={n:window.FindName(n) for n in names}
        missing=[n for n,v in self.ui.items() if v is None]
        if missing:raise ValueError('Controles de trazado ausentes: '+', '.join(missing))
        self.plan=CorridorCanvas(self.ui['Plan'],self.view,self.level,self.request,self.select,self.info,self.manual_network)
        self.plan.can_edit=lambda:not self.busy
        self.plan.show_diagnostics=lambda:bool(self.ui['DiagnosticGraph'].IsChecked)
        self._fill_catalog(workflow.defaults(catalog,configuration))
        self.product_choices=products.choices(configuration);self.ui['BranchProduct'].ItemsSource=Array[String]([v[0] for v in self.product_choices])
        suggestion=products.suggest(catalog)
        self.ui['BranchProduct'].SelectedIndex=next((i for i,(_,p) in enumerate(self.product_choices) if suggestion and p.profile_id==suggestion.profile_id),0)
        self._fill_parameters(configuration)
        self.ui['Budget'].SelectedIndex=1
        actions={'Generate':self.generate,'CancelGeneration':self.cancel,'RefreshEquipment':self.refresh,
            'NewStudy':self.new,'DuplicateStudy':self.duplicate,'RecoverStudies':self.list_studies,'ExportPlan':self.export,
            'FitPlan':self.plan.fit,'DrawCorridor':lambda:self.plan.set_mode('corridor'),'FinishDrawing':self.plan.finish,
            'CancelDrawing':self.plan.cancel,'Undo':lambda:self.request('undo'),'Redo':lambda:self.request('redo'),
            'MovePoint':self.move_point,'InsertPoint':lambda:self.plan.set_mode('insert_vertex'),
            'DeletePoint':self.delete_point,'DeleteCorridor':lambda:self.request('delete_corridor',{'corridor_id':self.selected()}),
            'CalculateAxes':self.calculate_axes,'ReviewModel':self.review_model,'CommitModel':self.commit_model,'LegacyPlanner':self.legacy,
            'SetAxisHeight':self.set_axis_height,'LockAxisSegment':self.lock_axis_segment,
            'MoveManifold':self.move_manifold,'RotateManifold':self.rotate_manifold,'Close':window.Close}
        for name,fn in actions.items():self.ui[name].Click+=self.safe(fn)
        self.ui['Studies'].SelectionChanged+=self.safe(self.study_changed)
        self.ui['Corridors'].SelectionChanged+=self.safe(self.corridor_changed)
        self.ui['Level'].SelectionChanged+=self.safe(self.level_changed)
        self.ui['Vertex'].SelectionChanged+=self.safe(self.vertex_changed)
        self.ui['CorridorNetwork'].SelectionChanged+=self.safe(self.network_changed)
        self.ui['CorridorConstraint'].SelectionChanged+=self.safe(self.constraint_changed)
        self.ui['Mode'].SelectionChanged+=self.safe(self.mode_changed)
        self.ui['BranchProduct'].SelectionChanged+=self.safe(self.product_changed)
        self.ui['Alternatives'].SelectionChanged+=self.safe(self.alternative_changed)
        self.ui['ManifoldNetwork'].SelectionChanged+=self.safe(self.manifold_changed)
        self.ui['DiagnosticGraph'].Checked+=self.safe(self.plan.draw);self.ui['DiagnosticGraph'].Unchecked+=self.safe(self.plan.draw)
        self.timer=DispatcherTimer();self.timer.Interval=TimeSpan.FromMilliseconds(250);self.timer.Tick+=self.poll
        window.Closing+=self.closing;window.Closed+=self.close
        window.Tag={'workspace':self,'ui':self.ui,'document_id':identity}
        self.list_studies()
        self.timer.Start();self.controls()

    def safe(self,fn):
        @library_context()
        def action(*args):
            try:fn()
            except Exception as exc:self.error(exc)
        return action

    def info(self,message):
        self.ui['Status'].Text=str(message);self.ui['Status'].Foreground=color('#5C6572');self.controls()

    def error(self,exc):
        message=str(exc).splitlines()[-1] if str(exc) else type(exc).__name__
        if message.startswith('ValueError: '):message=message[12:]
        self.ui['Status'].Text=message;self.ui['Status'].Foreground=color('#B04737');self.controls()

    def level(self):
        i=self.ui['Level'].SelectedIndex
        return self.levels[i][0] if 0<=i<len(self.levels) else None

    def manual_network(self):return model.NETWORKS[max(0,self.ui['ManualNetwork'].SelectedIndex)]

    def axes_mode(self):return self.ui['Mode'].SelectedIndex==1

    def view(self):
        p=self.proposal
        if not p:return None
        view=copy.copy(p)
        if self.axes_mode():
            view.corridors={}
            for rid,r in dict(p.individual_routes,**p.main_connections).items():
                points=r.get('points_mm',[])
                if len(points)<2:continue
                samples=[q for piece in r.get('primitives',[]) for q in piece['points_mm']]
                view.corridors[rid]={'corridor_id':rid,'network_type':r['network_type'],'level_id':r['level_id'],
                    'reference_xy_mm':[q[:2] for q in points],'reference_xyz_mm':points,
                    'display_polyline_xy_mm':[q[:2] for q in samples] if r.get('validation_status')=='GEOMETRICALLY_VALID' else [],
                    'validation_status':r.get('validation_status'),'constraint_type':'LOCKED_WAYPOINT' if p.constraints.get('axis_locks',{}).get(rid) else 'PREFERRED_WAYPOINT',
                    'kind':'AXIS','entity_type':r.get('route_kind','BRANCH'),'route':r,'length_mm':r.get('length_m',0)*1000}
        else:
            view.corridors={k:c for k,c in p.corridors.items() if c.get('generated_by')!='V3_FREE_SPACE_GRAPH' or c.get('kind')=='MANUAL'}
        return view

    def mode_changed(self):
        if self.rendering:return
        self.plan.cancel();self.plan.selected=None;self.plan.selected_vertex=None;self._corridor_list();self.plan.draw();self.controls();self.diagnostics()
        if self.axes_mode() and self.proposal and not self.proposal.individual_routes:self.info('Pulse Calcular ejes para obtener los tubos individuales de la distribución seleccionada.')

    def product_changed(self):
        if self.rendering:return
        self._fill_parameters(self.configuration,self.product_choices[max(0,self.ui['BranchProduct'].SelectedIndex)][1])

    def alternative_changed(self):
        if not self.rendering and self.proposal and self.ui['Alternatives'].SelectedIndex>=0:
            self.request('select_alternative',{'index':self.ui['Alternatives'].SelectedIndex})
            self.ui['Mode'].SelectedIndex=0

    def diagnostics(self):
        p=self.proposal
        if not p:return
        names={t['terminal_id']:t.get('display_id',t['terminal_id']) for t in p.background['terminals']}
        if self.axes_mode():
            labels={'INVALID':'Geometría pendiente de resolver','SEARCH_BUDGET_EXHAUSTED':'Sin evaluar: tiempo de cálculo agotado',
                'JOINT_CHECK_PENDING':'Pendiente de comprobar cruces','JOINT_COLLISION_CONFLICT':'Cruce con otro tubo',
                'EDITED_PENDING_RECHECK':'Edición pendiente de recalcular','CORRIDOR_CAPACITY_CONFLICT':'Falta anchura en el corredor'}
            rows=[]
            for rid,route in dict(p.individual_routes,**p.main_connections).items():
                state=route.get('validation_status','NOT_EVALUATED')
                if state=='GEOMETRICALLY_VALID':continue
                detail=next((r.get('search_reason') or r.get('reason') for r in reversed(route.get('search_attempts',[])) if r.get('search_reason') or r.get('reason')),route.get('reason') or route.get('detail',''))
                rows.append('{} · {}{}'.format(names.get(route.get('terminal_id'),rid),labels.get(state,state),' · '+detail if detail else ''))
            self.ui['Diagnostics'].Text='\n'.join(rows) or ('Ejes geométricamente comprobados. Validación nativa pendiente.' if p.individual_routes else 'Calcule los ejes de la distribución seleccionada.')
        else:self.ui['Diagnostics'].Text='\n'.join(names.get(tid,tid)+' · '+r['status'] for tid,r in p.connectivity.items() if r.get('status')!='TOPOLOGICALLY_REACHABLE') or 'Sin destinos pendientes en el esquema. Ejes y modelado pendientes.'

    def manifold_changed(self):
        if not self.proposal:return
        network=('SUPPLY','EXTRACT')[max(0,self.ui['ManifoldNetwork'].SelectedIndex)];frame=self.proposal.equipment.get(network,{}).get('proposed_transform')
        if frame:
            self.ui['ManifoldX'].Text='{:g}'.format(frame['origin_mm'][0]);self.ui['ManifoldY'].Text='{:g}'.format(frame['origin_mm'][1])

    def move_manifold(self):self.request('move_manifold',{'network_type':('SUPPLY','EXTRACT')[max(0,self.ui['ManifoldNetwork'].SelectedIndex)],'point_mm':[float(self.ui[n].Text.replace(',','.')) for n in ('ManifoldX','ManifoldY')]})
    def rotate_manifold(self):self.request('rotate_manifold',{'network_type':('SUPPLY','EXTRACT')[max(0,self.ui['ManifoldNetwork'].SelectedIndex)],'angle_degrees':float(self.ui['ManifoldAngle'].Text.replace(',','.'))})

    def legacy(self):self.window.Tag['open_legacy']=True;self.window.Close()

    def controls(self):
        p=self.proposal;view=self.view();selected=bool(view and self.plan.selected in view.corridors)
        c=view.corridors[self.plan.selected] if selected else None;unlocked=bool(c and c['constraint_type']!='LOCKED_WAYPOINT')
        self.ui['EmptyPlan'].Visibility=Visibility.Collapsed if p else Visibility.Visible
        self.ui['SelectionPanel'].Visibility=Visibility.Visible if selected else Visibility.Collapsed
        self.ui['Progress'].Visibility=Visibility.Visible if self.busy else Visibility.Collapsed
        self.ui['CancelGeneration'].Visibility=Visibility.Visible if self.busy else Visibility.Collapsed
        drawing=self.plan.mode=='corridor'
        for name in ('FinishDrawing','CancelDrawing'):self.ui[name].Visibility=Visibility.Visible if drawing else Visibility.Collapsed
        self.ui['FinishDrawing'].IsEnabled=not self.busy and len(self.plan.vertices)>=2
        self.ui['DrawCorridor'].IsEnabled=bool(p and not self.busy)
        self.ui['FitPlan'].IsEnabled=bool(p)
        self.ui['Undo'].IsEnabled=bool(p and p.history and not self.busy);self.ui['Redo'].IsEnabled=bool(p and p.future and not self.busy)
        for name in ('HeatRecovery','SupplyManifold','ExtractManifold','Terminals','OuterDiameter','BendRadius','Insulation','Clearance','BranchProduct','MainDuctType','MainDiameter','MainExterior','MainRadius',
                     'Budget','IncludeLinks','Generate','RefreshEquipment','Studies','NewStudy','DuplicateStudy','RecoverStudies','LegacyPlanner','Alternatives','Mode'):
            self.ui[name].IsEnabled=not self.busy
        for name in ('MovePoint','InsertPoint','DeleteCorridor','CorridorNetwork'):self.ui[name].IsEnabled=bool(unlocked and not self.busy)
        self.ui['DeletePoint'].IsEnabled=bool(unlocked and len(c['reference_xy_mm'])>2 and not self.busy)
        self.ui['CorridorConstraint'].IsEnabled=selected and not self.busy
        self.ui['Vertex'].IsEnabled=selected and not self.busy
        self.ui['ExportPlan'].IsEnabled=bool(p)
        self.ui['DrawCorridor'].IsEnabled=bool(p and not self.busy and not self.axes_mode())
        self.ui['CalculateAxes'].IsEnabled=bool(p and p.selected_paths and not self.busy)
        self.ui['ReviewModel'].IsEnabled=bool(p and p.individual_routes and not self.busy)
        self.ui['CommitModel'].IsEnabled=bool(p and p.validation.get('valid_route_ids') and len(p.selected_paths)==len(p.terminals) and not p.validation.get('invalid_route_ids') and all(r.get('validation_status')=='GEOMETRICALLY_VALID' for r in p.main_connections.values()) and len(p.main_connections)==2 and not self.busy)
        self.ui['AxisHeightPanel'].Visibility=Visibility.Visible if self.axes_mode() and selected else Visibility.Collapsed
        self.ui['DeleteCorridor'].IsEnabled=bool(unlocked and not self.busy and not self.axes_mode())
        self.ui['CorridorNetwork'].IsEnabled=bool(c and c.get('kind')=='MANUAL' and not self.busy)
        for name in ('MoveManifold','RotateManifold'):self.ui[name].IsEnabled=bool(p and p.equipment and not self.busy)

    def _fill_parameters(self,configuration,profile=None):
        profile=profile or self.product_choices[max(0,self.ui['BranchProduct'].SelectedIndex)][1]
        for name,value in [('OuterDiameter',profile.external_diameter_mm),('BendRadius',profile.minimum_bend_radius_mm),
            ('Insulation',profile.insulation_thickness_mm),('Clearance',configuration.get('design_clearance_mm',configuration['clearance_mm']))]:
            self.ui[name].Text='{:g}'.format(value)
        self.ui['IncludeLinks'].IsChecked=configuration.get('include_links',False)
        self.ui['ProfileInfo'].Text='Referencia de producto · interior {:g} mm · nominal {:g} mm'.format(profile.internal_diameter_mm,profile.nominal_diameter_mm) if profile.geometry_verified else 'Envolvente declarada; producto sin verificar. El diámetro nominal y el exterior se conservan separados.'
        self.ui['ProfileInfo'].ToolTip=profile.manufacturer_reference or 'Datos del proyecto'
        main=configuration.get('main_connection',{})
        for name,value in (('MainDiameter',main.get('diameter_mm',125)),('MainExterior',main.get('outer_diameter_mm',125)),('MainRadius',main.get('bend_radius_mm',125))):self.ui[name].Text='{:g}'.format(value)
        self.main_types=self.catalog.get('configured_model',{}).get('rigid_main_types',[])
        self.ui['MainDuctType'].ItemsSource=Array[String]([t['name'] for t in self.main_types])
        self.ui['MainDuctType'].SelectedIndex=next((i for i,t in enumerate(self.main_types) if t['element_id']==main.get('type_id')),0 if len(self.main_types)==1 else -1)

    def _selected_equipment(self):
        result={}
        for key,name in [('HRV','HeatRecovery'),('SUPPLY','SupplyManifold'),('EXTRACT','ExtractManifold')]:
            i=self.ui[name].SelectedIndex;choices=self.equipment[key]
            result[key]=choices[i]['id'] if 0<=i<len(choices) else None
        return result

    def _fill_catalog(self,selected,terminal_ids=None):
        from ..gui import _table
        self.equipment=workflow.equipment_choices(self.catalog);self.rendering=True
        try:
            for key,name in [('HRV','HeatRecovery'),('SUPPLY','SupplyManifold'),('EXTRACT','ExtractManifold')]:
                choices=self.equipment[key]
                labels=[(d.get('name') or d['family'])+' · '+str(d['element_id']) for d in choices]
                self.ui[name].ItemsSource=Array[String](labels)
                self.ui[name].SelectedIndex=next((i for i,d in enumerate(choices) if d['id']==selected.get(key)),-1)
            rows=[];counters={'SUPPLY':0,'EXTRACT':0};unavailable=0
            for t in self.catalog.get('terminals',[]):
                network=t.get('suggested_network_type');flow=t.get('airflow_lps');ports=t.get('connectors',[]) or [None]
                if network in counters:counters[network]+=1
                valid=network in counters and flow is not None and math.isfinite(flow) and flow>0
                if not valid or ports==[None]:unavailable+=1
                for port in ports:
                    include=valid and port is not None and len(ports)==1 and (terminal_ids is None or t['id'] in terminal_ids)
                    label=('I' if network=='SUPPLY' else 'E' if network=='EXTRACT' else '?')+str(counters.get(network,0)).zfill(2)
                    if len(ports)>1:label+=' · toma '+str(port['connector_id'])
                    rows.append({'Include':include,'Label':label,'Flow':'{:.2f}'.format(flow) if flow is not None else '—',
                        'TerminalId':t['id'],'PortId':port['id'] if port else '', 'Network':network or ''})
            self.terminals=_table([(n,Boolean if n=='Include' else String) for n in ('Include Label Flow TerminalId PortId Network').split()],rows)
            self.ui['Terminals'].ItemsSource=self.terminals.DefaultView
            self.ui['TerminalInfo'].Text='{} terminales · {} sin conector o caudal utilizable'.format(len(self.catalog.get('terminals',[])),unavailable)
            self.ui['EquipmentInfo'].Text='Seleccione un distribuidor independiente por red. La selección se guarda con el estudio.'
        finally:self.rendering=False

    def inputs(self):
        self.ui['Terminals'].CommitEdit(DataGridEditingUnit.Cell,True);self.ui['Terminals'].CommitEdit(DataGridEditingUnit.Row,True)
        destinations=[]
        for row in self.terminals.Rows:
            if not bool(row['Include']):continue
            if not str(row['PortId']) or str(row['Network']) not in ('SUPPLY','EXTRACT'):
                raise ValueError('El destino seleccionado necesita un conector y una red identificados.')
            destinations.append({'terminal_id':str(row['TerminalId']),'port_id':str(row['PortId']),'network_type':str(row['Network'])})
        number=lambda name:float(self.ui[name].Text.replace(',','.'))
        profile=self.product_choices[max(0,self.ui['BranchProduct'].SelectedIndex)][1]
        same=(number('OuterDiameter')==profile.external_diameter_mm and number('Insulation')==profile.insulation_thickness_mm and number('BendRadius')==profile.minimum_bend_radius_mm)
        profile=model.DuctProfile(profile.profile_id,profile.network_type,profile.nominal_diameter_mm,profile.internal_diameter_mm,
            number('OuterDiameter'),number('Insulation'),number('BendRadius'),profile.manufacturer_reference,profile.geometry_verified and same)
        cfg=copy.deepcopy(self.configuration);cfg['include_links']=bool(self.ui['IncludeLinks'].IsChecked)
        cfg['main_connection']=dict(cfg.get('main_connection',{}),kind='RIGID_CIRCULAR',diameter_mm=number('MainDiameter'),outer_diameter_mm=number('MainExterior'),bend_radius_mm=number('MainRadius'))
        index=self.ui['MainDuctType'].SelectedIndex
        if 0<=index<len(self.main_types):cfg['main_connection']['type_id']=self.main_types[index]['element_id']
        result=workflow.payload(self.catalog,cfg,self._selected_equipment(),destinations,profile,number('Clearance'))
        result['analyze']=True
        return result

    def generate(self):
        if self.busy:raise ValueError('Ya hay un estudio en curso.')
        payload=self.inputs();self.plan.cancel();self.busy=True;self.capturing=True;self.started=time.monotonic();self.cancel_event.clear()
        self.info('Leyendo la geometría real y preparando el estudio…');self.window.UpdateLayout()
        try:
            p=self.proposal;reuse=False
            if p:
                existing={(k,t['network_type']) for k,t in p.terminals.items()}
                requested={(d['terminal_id'],d['network_type']) for d in payload['destinations']}
                reuse=existing==requested and p.configuration.get('hrv_id')==payload['configuration'].get('hrv_id') and \
                    p.configuration.get('equipment_assignments')==payload['configuration'].get('equipment_assignments')
            if reuse:
                try:
                    data=self.dispatch(self.doc,'routing_design_analyze',dict(payload,proposal_id=p.proposal_id,expected_revision=p.revision))
                except ValueError as exc:
                    if not str(exc).startswith('OUTDATED'):raise
                    data=self.dispatch(self.doc,'routing_design_create',payload)
            else:data=self.dispatch(self.doc,'routing_design_create',payload)
            self.adopt(model.RoutingDesignProposal.from_dict(data),load_settings=False)
            from .service import generate_corridors
            p=self.proposal;seconds=BUDGETS[max(0,self.ui['Budget'].SelectedIndex)]
            self.future=self.executor.submit(generate_corridors,self.identity,p.proposal_id,p.revision,
                {'seconds':seconds,'max_nodes':1600},self.cancel_event.is_set)
            self.info('Generando corredores en el volumen disponible. Puede navegar por la planta o cancelar.')
        except Exception:
            self.busy=False;raise
        finally:self.capturing=False;self.controls()

    def cancel(self):
        self.cancel_event.set();self.info('Cancelando el cálculo; se conserva el estudio guardado.')

    def calculate_axes(self):
        if self.busy or not self.proposal:return
        p=self.proposal
        self.dispatch(self.doc,'routing_design_assert_current',{'proposal_id':p.proposal_id})
        from .service import calculate_axes
        self.plan.cancel();self.busy=True;self.cancel_event.clear();self.started=time.monotonic()
        self.future=self.executor.submit(calculate_axes,self.identity,p.proposal_id,p.revision,
            {'seconds':BUDGETS[max(0,self.ui['Budget'].SelectedIndex)]},self.cancel_event.is_set)
        self.info('Calculando tomas, haces, carriles y ejes sobre la distribución seleccionada…')

    def review_model(self):
        if not self.proposal:return None
        plan=self.dispatch(self.doc,'routing_design_native_plan',{'proposal_id':self.proposal.proposal_id})
        if not plan['ready']:
            self.ui['Diagnostics'].Text='\n'.join(v.get('route_id','Instalación')+' · '+v['code'] for v in plan['blockers'])
            self.info('Modelado pendiente: '+str(len(plan['blockers']))+' condiciones por resolver. Consulte las incidencias.')
        else:self.info('Revisión preparada: {} ramales flexibles y {} principales rígidas; {} movimientos virtuales.'.format(plan['summary']['flexible_branches'],plan['summary']['rigid_mains'],plan['summary']['equipment_movements']))
        return plan

    def commit_model(self):
        plan=self.review_model()
        if not plan or not plan['ready']:return
        from System.Windows import MessageBox,MessageBoxButton,MessageBoxImage,MessageBoxResult
        text='Aplicar la revisión {} de «{}» en {}.\n\n{} ramales flexibles\n{} principales rígidas\n{} movimientos de equipos\n{} pasos previstos: los huecos de obra quedan pendientes.\n\nSe crearán y comprobarán las conexiones. Cualquier fallo revierte el conjunto.'.format(
            self.proposal.revision,self.proposal.name,self.doc.Title,plan['summary']['flexible_branches'],plan['summary']['rigid_mains'],plan['summary']['equipment_movements'],plan['summary']['planned_penetrations'])
        if MessageBox.Show(self.window,text,'ARQ-T · Confirmar modelado',MessageBoxButton.YesNo,MessageBoxImage.Question)!=MessageBoxResult.Yes:return
        report=self.dispatch(self.doc,'routing_design_commit',{'proposal_id':self.proposal.proposal_id,
            'expected_revision':self.proposal.revision,'approval_token':plan['approval_token'],'explicit':True},allow_write=True)
        self.info('Conexiones nativas comprobadas · '+str(report['routes'])+' recorridos.');self.ui['CommitModel'].IsEnabled=False

    def set_axis_height(self):self.request('set_axis_height',{'route_id':self.selected(),'index':self.ui['Vertex'].SelectedIndex,'z_mm':float(self.ui['PointZ'].Text.replace(',','.'))})
    def lock_axis_segment(self):
        rid=self.selected();index=self.ui['Vertex'].SelectedIndex;records=self.proposal.constraints.get('axis_locked_segments',{}).get(rid,{})
        self.request('set_axis_lock',{'route_id':rid,'segment_index':index,'locked':str(index) not in records})

    def refresh(self):
        if self.busy:return
        selected=self._selected_equipment()
        self.catalog=self.dispatch(self.doc,'get_mep_connection_points',{'configuration':self.configuration})
        self._fill_catalog(selected)
        self.info('Equipos y destinos actualizados. La propuesta guardada no se modifica hasta generar de nuevo.')

    def adopt(self,p,load_settings=True):
        changed=self.proposal is None or self.proposal.proposal_id!=p.proposal_id
        prior=self.level();self.proposal=p
        if changed:self.plan.selected=None;self.plan.selected_vertex=None;self.plan.cancel()
        self.rendering=True
        try:
            self.levels=sorted({(r['level_id'],r['level']) for r in p.background['analysis']['rooms'] if r['uid'] in p.background['room_uids']},key=lambda r:r[1])
            self.ui['Level'].ItemsSource=Array[String]([r[1] for r in self.levels])
            self.ui['Level'].SelectedIndex=next((i for i,r in enumerate(self.levels) if r[0]==prior),0 if self.levels else -1)
            if changed and load_settings and p.configuration:
                self.configuration=copy.deepcopy(p.configuration)
                self._fill_catalog(workflow.defaults(self.catalog,p.configuration),set(p.terminals))
                profile=model.DuctProfile(**next(iter(p.profiles.values()))) if p.profiles else None
                if profile:
                    self.product_choices=products.choices(p.configuration)
                    index=next((i for i,(_,v) in enumerate(self.product_choices) if v.profile_id==profile.profile_id),None)
                    if index is None:self.product_choices.append(('Perfil guardado del estudio',profile));index=len(self.product_choices)-1
                    self.ui['BranchProduct'].ItemsSource=Array[String]([v[0] for v in self.product_choices]);self.ui['BranchProduct'].SelectedIndex=index
                self._fill_parameters(p.configuration,profile)
            self.ui['Alternatives'].ItemsSource=Array[String](['Propuesta {} · {}/{} destinos · {:.1f} m de tubos'.format(i+1,a['connected'],len(p.terminals),a['estimated_tube_length_mm']/1000) for i,a in enumerate(p.alternatives)])
            self.ui['Alternatives'].SelectedIndex=next((i for i,a in enumerate(p.alternatives) if a.get('selected_paths')==p.selected_paths),-1)
            self.ui['AlternativeInfo'].Text='Organizaciones distintas por corredores y pasos. La longitud suma todos los tubos.'
            self.diagnostics()
            self.mtime=os.stat(store.path(self.identity,p.proposal_id)).st_mtime_ns
        finally:self.rendering=False
        self._corridor_list();self._study_list();self.select(self.plan.selected);self.plan.draw();self.manifold_changed();self.controls()

    def _corridor_list(self):
        p=self.view();self.cids=[cid for cid,c in p.corridors.items() if c['level_id']==self.level()] if p else []
        self.rendering=True
        try:
            self.ui['Corridors'].ItemsSource=Array[String]([('Tubo' if self.axes_mode() else 'Ramal' if p.corridors[cid].get('entity_type')=='BRANCH' else 'Corredor')+' {:02d} · {}'.format(i+1,NETWORK_LABELS[p.corridors[cid]['network_type']]) for i,cid in enumerate(self.cids)])
            self.ui['Corridors'].SelectedIndex=self.cids.index(self.plan.selected) if self.plan.selected in self.cids else -1
        finally:self.rendering=False

    def list_studies(self):
        if self.busy:return
        self._study_list()
        if self.proposal is None and self.studies:
            self.adopt(store.get(self.identity,self.studies[0]['proposal_id']))
        self.info(workflow.summary(self.proposal))

    def _study_list(self):
        self.studies=store.entries(self.identity);self.rendering=True
        try:
            self.ui['Studies'].ItemsSource=Array[String]([p['name']+' · '+('{}/{} destinos'.format(p.get('selected_terminals',0),p.get('terminals',0)) if p.get('strategy')=='GLOBAL_RADIAL_V3' else 'estudio anterior') for p in self.studies])
            self.ui['Studies'].SelectedIndex=next((i for i,p in enumerate(self.studies) if self.proposal and p['proposal_id']==self.proposal.proposal_id),-1)
        finally:self.rendering=False

    def study_changed(self):
        if self.rendering or self.busy:return
        i=self.ui['Studies'].SelectedIndex
        if 0<=i<len(self.studies):self.adopt(store.get(self.identity,self.studies[i]['proposal_id']));self.info(workflow.summary(self.proposal))

    def new(self):
        if self.busy:return
        self.plan.cancel();self.proposal=None;self.plan.selected=None;self.plan.draw();self._corridor_list();self._study_list()
        self.info('Nuevo estudio. Revise los equipos y pulse Generar corredores. Los estudios anteriores siguen guardados.')

    def duplicate(self):
        if not self.proposal:raise ValueError('Seleccione un estudio guardado.')
        p=self.proposal;self.adopt(store.duplicate(self.identity,p.proposal_id,p.revision));self.info('Copia independiente del estudio guardada.')

    def export(self):
        if not self.proposal:raise ValueError('Genere o recupere un estudio.')
        from .preview import export
        capture=export(self.proposal,self.level());self.info('Imagen guardada: '+capture['path'])

    def selected(self):
        if not self.proposal or self.plan.selected not in self.view().corridors:raise ValueError('Seleccione un elemento en la planta.')
        return self.plan.selected

    def select(self,cid):
        if self.rendering:return
        p=self.view()
        if not p or cid not in p.corridors:self.controls();return
        self.rendering=True
        try:
            c=p.corridors[cid];index=self.cids.index(cid)+1 if cid in self.cids else 1
            self.ui['SelectionTitle'].Text=('Tubo' if self.axes_mode() else 'Ramal' if c.get('entity_type')=='BRANCH' else 'Corredor')+' {:02d}'.format(index)
            length=sum(math.dist(a,b) for a,b in zip(c['reference_xy_mm'],c['reference_xy_mm'][1:]))/1000
            self.ui['SelectionSummary'].Text='{:.2f} m · {} puntos\n{}\n{}'.format(length,len(c['reference_xy_mm']),NETWORK_LABELS[c['network_type']],c.get('validation_status','Pendiente'))
            if self.axes_mode():
                route=c['route'];self.ui['SelectionSummary'].Text+='\nToma {} → codo {}\nØ nominal {} mm · exterior {} mm · R mínimo {} mm'.format(
                    route.get('source',{}).get('connector_id','?'),route.get('destination',{}).get('element_id','?'),route.get('profile',{}).get('nominal_diameter_mm','?'),route.get('profile',{}).get('external_diameter_mm','?'),route.get('bend_radius_mm','?'))
                self.ui['SelectionSummary'].Text+='\nR de construcción {} mm'.format(route.get('construction_bend_radius_mm',route.get('bend_radius_mm','?')))
            elif c.get('reachable_terminal_ids'):self.ui['SelectionSummary'].Text+='\n{} destinos · toma física pendiente de asignar'.format(len(c['reachable_terminal_ids']))
            self.ui['Corridors'].SelectedIndex=self.cids.index(cid) if cid in self.cids else -1
            self.ui['CorridorNetwork'].SelectedIndex=model.NETWORKS.index(c['network_type'])
            self.ui['CorridorConstraint'].SelectedIndex=model.CONSTRAINTS.index(c['constraint_type'])
            selected=self.plan.selected_vertex
            self.ui['Vertex'].ItemsSource=Array[String](['Punto '+str(i+1) for i in range(len(c['reference_xy_mm']))])
            self.ui['Vertex'].SelectedIndex=selected if selected is not None and 0<=selected<len(c['reference_xy_mm']) else 0
        finally:self.rendering=False
        self.vertex_changed();self.controls()

    def corridor_changed(self):
        if self.rendering:return
        i=self.ui['Corridors'].SelectedIndex;self.plan.selected=self.cids[i] if 0<=i<len(self.cids) else None
        self.plan.selected_vertex=None;self.select(self.plan.selected);self.plan.draw()

    def level_changed(self):
        if self.rendering:return
        self.plan.selected=None;self.plan.selected_vertex=None;self._corridor_list();self.controls();self.plan.fit()

    def vertex_changed(self):
        if self.rendering:return
        if not self.proposal or self.plan.selected not in self.view().corridors:return
        i=self.ui['Vertex'].SelectedIndex;c=self.view().corridors[self.plan.selected]
        if not 0<=i<len(c['reference_xy_mm']):return
        self.plan.selected_vertex=i;xy=c['reference_xy_mm'][i];self.point_original=list(xy)
        self.point_text=['{:.6f}'.format(v).rstrip('0').rstrip('.') for v in xy]
        self.ui['PointX'].Text=self.point_text[0];self.ui['PointY'].Text=self.point_text[1]
        if self.axes_mode():self.ui['PointZ'].Text='{:g}'.format(c['reference_xyz_mm'][i][2])

    def move_point(self):
        xy=[self.point_original[i] if self.ui[name].Text==self.point_text[i] else float(self.ui[name].Text.replace(',','.')) for i,name in enumerate(('PointX','PointY'))]
        self.request('move_vertex',{'corridor_id':self.selected(),'index':self.ui['Vertex'].SelectedIndex,'point_mm':xy})

    def delete_point(self):self.request('delete_vertex',{'corridor_id':self.selected(),'index':self.ui['Vertex'].SelectedIndex})
    def network_changed(self):
        if not self.rendering:self.request('set_network',{'corridor_id':self.selected(),'network_type':model.NETWORKS[self.ui['CorridorNetwork'].SelectedIndex]})
    def constraint_changed(self):
        if not self.rendering:
            if self.axes_mode():self.request('set_axis_lock',{'route_id':self.selected(),'locked':self.ui['CorridorConstraint'].SelectedIndex==2})
            else:self.request('set_constraint',{'corridor_id':self.selected(),'constraint_type':model.CONSTRAINTS[self.ui['CorridorConstraint'].SelectedIndex]})

    def request(self,operation,payload=None,revision=None):
        if self.busy:raise ValueError('Espere a que termine el cálculo para editar; puede seguir navegando por la planta.')
        if not self.proposal:raise ValueError('Genere o recupere un estudio antes de editar.')
        p=self.proposal
        if self.axes_mode() and operation in ('move_corridor','move_vertex','add_vertex','delete_vertex'):
            payload=dict(payload or {});payload['route_id']=payload.pop('corridor_id')
            operation={'move_corridor':'move_axis','move_vertex':'move_axis_waypoint','add_vertex':'add_axis_waypoint','delete_vertex':'delete_axis_waypoint'}[operation]
        try:
            result=store.update(self.identity,p.proposal_id,p.revision if revision is None else revision,operation,payload or {})
            self.adopt(result,load_settings=False);self.info('Edición guardada · revisión '+str(result.revision))
        except Exception:
            self.adopt(store.get(self.identity,p.proposal_id),load_settings=False);raise

    @library_context()
    def poll(self,*args):
        try:
            self.controls()
            if self.future and self.future.done():
                task=self.future;self.future=None;self.busy=False
                try:
                    p=task.result()
                    self.rendering=True
                    try:self.ui['Mode'].SelectedIndex=1 if p.design_stage=='AXES' else 0
                    finally:self.rendering=False
                    self.plan.selected=None;self.adopt(p,load_settings=False)
                    if p.corridor_graph.get('last_attempt',{}).get('cancelled'):self.info('Cálculo cancelado. Se conserva el estudio anterior y sus corredores.')
                    elif p.corridor_graph.get('stats',{}).get('cancelled'):self.info('Cálculo cancelado · se han conservado los resultados de estudio disponibles.')
                    else:self.info(workflow.summary(p));self.plan.fit()
                except Exception as exc:self.error(exc)
            if self.proposal and not self.busy and not self.plan.drag and not self.plan.mode and time.monotonic()-self.last_stat>.8:
                self.last_stat=time.monotonic();path=store.path(self.identity,self.proposal.proposal_id)
                if os.stat(path).st_mtime_ns!=self.mtime:
                    self.adopt(store.get(self.identity,self.proposal.proposal_id),load_settings=False);self.info('Estudio actualizado desde otro cliente.')
        except Exception as exc:self.error(exc)

    def closing(self,sender,args):
        if self.capturing:args.Cancel=True
        elif self.busy:self.cancel_event.set()

    def close(self,*args):
        self.closed=True;self.timer.Stop();self.cancel_event.set();self.executor.shutdown(wait=False,cancel_futures=True)


def show(doc,uidoc,_on_loaded=None,_context=None,_strategy='MANUAL_BUNDLE'):
    if doc is None or doc.IsFamilyDocument:raise ValueError('Abra un proyecto Revit.')
    if _context is None:
        from .. import settings
        from ...integration import dispatch as shared_dispatch
        from ..gui import project_identity
        configuration=settings.load();saved=shared_dispatch(doc,'get_heat_recovery_equipment_assignments')
        if saved:configuration=settings.merge(configuration,saved)
        catalog=shared_dispatch(doc,'get_mep_connection_points',{'configuration':configuration})
        identity=project_identity(doc);dispatch=shared_dispatch
    else:
        configuration=_context['configuration'];catalog=_context['catalog'];identity=_context['identity'];dispatch=_context['dispatch']
    configuration=copy.deepcopy(configuration);configuration['routing_strategy']=_strategy
    filename='window.xaml' if _strategy=='MANUAL_BUNDLE' else 'global_v3_window.xaml'
    path=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..','..','ARQ-T.tab','MEP.panel','TrazadoAutomaticoMEP.pushbutton',filename))
    with open(path,encoding='utf8') as f:window=XamlReader.Parse(f.read())
    from System.Windows import SystemParameters
    area=SystemParameters.WorkArea;window.Width=min(window.Width,area.Width-24);window.Height=min(window.Height,area.Height-24)
    window.MinWidth=min(window.MinWidth,window.Width);window.MinHeight=min(window.MinHeight,window.Height)
    if uidoc is not None:
        from System.Windows.Interop import WindowInteropHelper
        WindowInteropHelper(window).Owner=uidoc.Application.MainWindowHandle
    if _strategy=='MANUAL_BUNDLE':
        from .manual_workspace import ManualWorkspace
        controller=ManualWorkspace(window,doc,uidoc,identity,configuration,catalog,dispatch)
    else:controller=Workspace(window,doc,uidoc,identity,configuration,catalog,dispatch)
    controller.ui['DocumentLabel'].Text=doc.Title+' · ventilación radial · '+('corredores manuales y haces ordenados' if _strategy=='MANUAL_BUNDLE' else 'planificador anterior')
    if _on_loaded:window.ContentRendered+=lambda *args:_on_loaded(window,controller.ui,window.Tag)
    window.ShowDialog()
    if window.Tag.get('open_legacy'):
        from ..gui import show_legacy
        return show_legacy(doc,uidoc)
    return window.Tag
