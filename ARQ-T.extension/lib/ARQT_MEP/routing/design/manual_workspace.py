"""Manual-bundle controller on the existing WPF workspace and plan canvas."""
import copy
import math
import time
from System import Array,String
from System.Windows import Visibility
from System.Windows.Controls import DataGridEditingUnit
from .workspace import Workspace,NETWORK_LABELS
from . import model,store,workflow,manual_bundle


class ManualWorkspace(Workspace):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        names=('ActiveManifold AssignTerminals RemoveTerminals BranchWaypointPanel BranchX BranchY SetBranchWaypoint ClearBranchWaypoint ElbowAngle SetElbowAngle IncludeMains')
        self.ui.update({name:self.window.FindName(name) for name in names.split()})
        self.ui['Generate'].Content='Preparar / actualizar planta';self.ui['CalculateAxes'].Content='Calcular ejes';self.ui['CommitModel'].Content='Modelar conexiones'
        for name in ('Alternatives','DiagnosticGraph','Budget','CorridorConstraint','AxisHeightPanel','ManifoldNetwork','SetAxisHeight','LockAxisSegment'):
            self.ui[name].Visibility=Visibility.Collapsed
        self.ui['AlternativeInfo'].Text='Dibuje un corredor, seleccione las rejillas y pulse Asignar al corredor.'
        self.ui['ManualNetwork'].Items.RemoveAt(2);self.ui['ManualNetwork'].SelectedIndex=0
        self.ui['ManualNetwork'].IsEnabled=False
        self.ui['IncludeMains'].IsChecked=self.configuration.get('manual_bundle',{}).get('include_mains',True)
        self.ui['ActiveManifold'].SelectionChanged+=self.safe(self.active_manifold_changed)
        for name,fn in (('AssignTerminals',self.assign_terminals),('RemoveTerminals',self.remove_terminals),
            ('SetBranchWaypoint',self.set_branch_waypoint),('ClearBranchWaypoint',lambda:self.set_branch_waypoint(clear=True)),
            ('SetElbowAngle',self.set_elbow_angle)):
            self.ui[name].Click+=self.safe(fn)
        self.plan.select_terminal=self.toggle_terminal
        self.plan.select_manifold=self.select_manifold
        self.plan.axis_read_only=self.axes_mode
        self.plan.set_branch_point=lambda rid,xy:self.request('set_branch_waypoint',{'route_id':rid,'point_mm':xy})
        self._active_choices();self.controls()

    def _fill_catalog(self,selected,terminal_ids=None):
        super()._fill_catalog(selected,terminal_ids)
        self.terminals.Columns.Add('Corridor',String)
        for row in self.terminals.Rows:row['Include']=False;row['Corridor']='Sin asignar'
        self.ui['EquipmentInfo'].Text='Los corredores y sus terminales se guardan en el estudio manual.'

    def _active_choices(self):
        if not self.ui.get('ActiveManifold'):return
        selected=self.active_uid() if hasattr(self,'manual_equipment') else None
        self.manual_equipment=list(self.proposal.equipment.values()) if self.proposal else []
        self.rendering=True
        try:
            self.ui['ActiveManifold'].ItemsSource=Array[String]([NETWORK_LABELS[v['network_type']]+' · ID '+str(v['element_id']) for v in self.manual_equipment])
            self.ui['ActiveManifold'].SelectedIndex=next((i for i,v in enumerate(self.manual_equipment) if v['id']==selected),0 if self.manual_equipment else -1)
        finally:self.rendering=False
        self.active_manifold_changed()

    def active_uid(self):
        control=self.ui.get('ActiveManifold');i=control.SelectedIndex if control else -1
        return self.manual_equipment[i]['id'] if hasattr(self,'manual_equipment') and 0<=i<len(self.manual_equipment) else None

    def active_manifold_changed(self):
        if self.rendering:return
        uid=self.active_uid()
        if uid and self.proposal:
            item=self.proposal.equipment[uid];self.ui['ManualNetwork'].SelectedIndex=0 if item['network_type']=='SUPPLY' else 1
            frame=item.get('proposed_transform')
            if frame:
                self.ui['ManifoldX'].Text='{:g}'.format(frame['origin_mm'][0]);self.ui['ManifoldY'].Text='{:g}'.format(frame['origin_mm'][1])

    def manifold_changed(self):
        if self.ui.get('ActiveManifold'):self.active_manifold_changed()

    def select_manifold(self,uid):
        if not hasattr(self,'manual_equipment'):return
        self.ui['ActiveManifold'].SelectedIndex=next((i for i,v in enumerate(self.manual_equipment) if v['id']==uid),-1)
        self.info('Distribuidor seleccionado. Arrastre para moverlo en la propuesta o indique un giro.')

    def move_manifold(self):
        self.request('move_manifold',{'distributor_uid':self.active_uid(),'point_mm':[float(self.ui[n].Text.replace(',','.')) for n in ('ManifoldX','ManifoldY')]})

    def rotate_manifold(self):
        self.request('rotate_manifold',{'distributor_uid':self.active_uid(),'angle_degrees':float(self.ui['ManifoldAngle'].Text.replace(',','.'))})

    def inputs(self):
        # The grid is a selection for assigning destinations, not a filter
        # that silently removes unselected terminals from the project view.
        self.ui['Terminals'].CommitEdit(DataGridEditingUnit.Cell,True);self.ui['Terminals'].CommitEdit(DataGridEditingUnit.Row,True)
        previous=[bool(row['Include']) for row in self.terminals.Rows]
        lookup={t['id']:t for t in self.catalog.get('terminals',[])}
        try:
            for row in self.terminals.Rows:
                flow=lookup[str(row['TerminalId'])].get('airflow_lps')
                row['Include']=bool(row['PortId'] and str(row['Network']) in ('SUPPLY','EXTRACT') and flow is not None and math.isfinite(flow) and flow>0)
            result=super().inputs();result['configuration']['routing_strategy']=manual_bundle.STRATEGY
            result['configuration'].setdefault('manual_bundle',{})['include_mains']=bool(self.ui['IncludeMains'].IsChecked)
            return result
        finally:
            for row,value in zip(self.terminals.Rows,previous):row['Include']=value

    def generate(self):
        if self.busy:return
        payload=self.inputs();self.plan.cancel();self.busy=True;self.capturing=True
        try:
            if self.proposal:
                data=self.dispatch(self.doc,'routing_design_analyze',dict(payload,proposal_id=self.proposal.proposal_id,expected_revision=self.proposal.revision))
            else:data=self.dispatch(self.doc,'routing_design_create',payload)
            self.adopt(model.RoutingDesignProposal.from_dict(data),load_settings=False)
            self.info('Planta preparada. Seleccione un distribuidor y dibuje el corredor.')
        finally:self.busy=False;self.capturing=False;self.controls()

    def calculate_axes(self):
        if self.busy or self.proposal is None:return
        self.generate()
        p=self.proposal
        if not p.corridor_assignments:raise ValueError('Asigne las rejillas a sus corredores antes de calcular.')
        from .service import calculate_axes
        self.plan.cancel();self.busy=True;self.started=time.monotonic();self.cancel_event.clear()
        self.future=self.executor.submit(calculate_axes,self.identity,p.proposal_id,p.revision,{'seconds':30},self.cancel_event.is_set)
        self.info('Ordenando tomas y carriles, calculando cotas medias y formando los ramales…')

    def selected_terminals(self):
        self.ui['Terminals'].CommitEdit(DataGridEditingUnit.Cell,True);self.ui['Terminals'].CommitEdit(DataGridEditingUnit.Row,True)
        return [str(row['TerminalId']) for row in self.terminals.Rows if bool(row['Include'])]

    def toggle_terminal(self,tid):
        for row in self.terminals.Rows:
            if str(row['TerminalId'])==tid:row['Include']=not bool(row['Include'])
        self.info('Terminal seleccionado. Use Asignar al corredor para guardar la asociación.')
        self.plan.draw()

    def assign_terminals(self):
        if self.axes_mode():raise ValueError('Vuelva al modo Corredores para asignar destinos.')
        tids=self.selected_terminals()
        if not tids:raise ValueError('Seleccione las rejillas en planta o en la lista.')
        self.request('assign_terminals',{'corridor_id':self.selected(),'terminal_ids':tids})

    def remove_terminals(self):
        cid=self.selected()
        for tid in self.selected_terminals():self.request('remove_terminal',{'corridor_id':cid,'terminal_id':tid})

    def set_branch_waypoint(self,clear=False):
        rid=self.selected()
        if not self.axes_mode() or not rid.startswith('axis:'):raise ValueError('Seleccione un tubo radial en Ejes de tubos.')
        xy=None if clear else [float(self.ui[n].Text.replace(',','.')) for n in ('BranchX','BranchY')]
        self.request('set_branch_waypoint',{'route_id':rid,'point_mm':xy})

    def set_elbow_angle(self):
        rid=self.selected();text=self.ui['ElbowAngle'].Text.strip()
        self.request('set_elbow_rotation',{'route_id':rid,'angle_degrees':float(text.replace(',','.')) if text else None})

    def commit_model(self):
        plan=self.review_model()
        if not plan or not plan['ready']:return
        payload={'proposal_id':self.proposal.proposal_id,'expected_revision':self.proposal.revision,
            'approval_token':plan['approval_token'],'explicit':True}
        self.info('Comprobando temporalmente movimientos y conexiones nativas…')
        self.window.UpdateLayout()
        report=self.dispatch(self.doc,'routing_design_commit',dict(payload,dry_run=True),allow_write=True)
        if not report['native_connections_valid']:raise ValueError('La comprobación nativa no permite aplicar esta revisión.')
        from System.Windows import MessageBox,MessageBoxButton,MessageBoxImage,MessageBoxResult
        summary=plan['summary']
        text=chr(10).join([
            'Revision {} de {}'.format(self.proposal.revision,self.proposal.name),'',
            '{} distribuidores a mover'.format(summary['manifold_translations']),
            '{} distribuidores a girar'.format(summary['manifold_rotations']),
            '{} codos a rotar'.format(summary['elbow_rotations']),
            '{} flexibles y {} principales rigidas'.format(summary['flexible_branches'],summary['rigid_mains']),
            '{} pasos previstos, con huecos de obra pendientes'.format(summary['planned_penetrations']),'',
            'La comprobacion reversible ha finalizado correctamente. Aplicar esta revision al proyecto?'])
        if MessageBox.Show(self.window,text,'ARQ-T · Aplicar conexiones',MessageBoxButton.YesNo,MessageBoxImage.Question)!=MessageBoxResult.Yes:
            self.info('Se conserva el borrador; la comprobación temporal se ha revertido.');return
        final=self.dispatch(self.doc,'routing_design_commit',payload,allow_write=True)
        self.info('Conexiones nativas comprobadas · {} recorridos.'.format(final['routes']))

    def request(self,operation,payload=None,revision=None):
        if self.busy:raise ValueError('Espere a que termine el cálculo.')
        if not self.proposal:raise ValueError('Prepare la planta para crear el estudio manual.')
        payload=copy.deepcopy(payload or {})
        if operation=='add_corridor':payload['distributor_uid']=self.active_uid()
        if self.axes_mode() and operation in ('move_vertex','add_vertex','move_corridor','delete_corridor','delete_vertex'):
            raise ValueError('Edite el corredor o utilice el único punto de paso opcional del ramal.')
        p=self.proposal
        try:
            result=store.update(self.identity,p.proposal_id,p.revision if revision is None else revision,operation,payload)
            self.adopt(result,load_settings=False)
            if operation=='add_corridor':
                cid=next(iter(set(result.corridors)-set(p.corridors)));self.plan.selected=cid;self.select(cid);self.plan.draw()
            self.info('Borrador guardado · revisión '+str(result.revision))
        except Exception:
            self.adopt(store.get(self.identity,p.proposal_id),load_settings=False);raise

    def adopt(self,p,load_settings=True):
        if p.configuration.get('routing_strategy')!=manual_bundle.STRATEGY:raise ValueError('Este estudio pertenece al planificador anterior; cree un estudio manual nuevo.')
        self.catalog=p.catalog
        super().adopt(p,load_settings)
        self.configuration=copy.deepcopy(p.configuration)
        if load_settings and self.ui.get('IncludeMains'):self.ui['IncludeMains'].IsChecked=p.configuration.get('manual_bundle',{}).get('include_mains',True)
        self.ui['AlternativeInfo'].Text='Dibuje los corredores y asigne sus rejillas. Cada terminal pertenece a un único corredor.'
        mapping=manual_bundle.assigned(p)
        labels={cid:'Corredor '+str(i+1) for i,cid in enumerate(p.corridors)}
        for row in self.terminals.Rows:row['Corridor']=labels.get(mapping.get(str(row['TerminalId'])),'Sin asignar')
        self._active_choices()

    def _study_list(self):
        self.studies=[v for v in store.entries(self.identity) if v.get('strategy')==manual_bundle.STRATEGY];self.rendering=True
        try:
            self.ui['Studies'].ItemsSource=Array[String]([v['name'] for v in self.studies])
            self.ui['Studies'].SelectedIndex=next((i for i,v in enumerate(self.studies) if self.proposal and v['proposal_id']==self.proposal.proposal_id),-1)
        finally:self.rendering=False

    def new(self):
        super().new();self._active_choices();self.info('Nuevo estudio manual. Prepare la planta, dibuje los corredores y asigne las rejillas.')

    def controls(self):
        super().controls()
        p=self.proposal;axis=self.axes_mode();selected=bool(p and self.plan.selected in self.view().corridors)
        self.ui['CalculateAxes'].IsEnabled=bool(p and p.corridor_assignments and not self.busy)
        self.ui['ManualNetwork'].IsEnabled=False;self.ui['CorridorNetwork'].IsEnabled=False
        for name in ('MovePoint','InsertPoint','DeletePoint'):self.ui[name].IsEnabled=bool(selected and not axis and not self.busy)
        self.ui['PointX'].IsReadOnly=axis;self.ui['PointY'].IsReadOnly=axis
        self.ui['AxisHeightPanel'].Visibility=Visibility.Visible if axis and selected else Visibility.Collapsed
        self.ui['PointZ'].IsReadOnly=True
        if self.ui.get('BranchWaypointPanel'):
            self.ui['BranchWaypointPanel'].Visibility=Visibility.Visible if axis and selected and self.plan.selected.startswith('axis:') else Visibility.Collapsed
            for name in ('AssignTerminals','RemoveTerminals'):self.ui[name].IsEnabled=bool(selected and not axis and not self.busy)
            self.ui['ActiveManifold'].IsEnabled=not self.busy
            self.ui['IncludeMains'].IsEnabled=not self.busy
        if p:
            from .native_plan import prepare
            self.ui['CommitModel'].IsEnabled=not self.busy and prepare(p)['ready']

    def select(self,cid):
        super().select(cid)
        if not self.axes_mode() or not self.proposal or cid not in self.proposal.individual_routes:return
        route=self.proposal.individual_routes[cid];tid=route['terminal_id'];point=self.proposal.constraints.get('branch_waypoints',{}).get(tid)
        if self.ui.get('BranchX'):
            for name,value in zip(('BranchX','BranchY'),point or route.get('destination',{}).get('xyz_mm',[0,0])[:2]):self.ui[name].Text='{:g}'.format(value)
            angle=self.proposal.constraints.get('elbow_angles_degrees',{}).get(tid)
            self.ui['ElbowAngle'].Text='' if angle is None else '{:g}'.format(angle)
            self.ui['SelectionSummary'].Text+='\nCorredor {} · carril {}'.format(route.get('corridor_id','?'),route.get('lane_index',0)+1)
            if route.get('elbow_pose'):self.ui['SelectionSummary'].Text+='\nGiro propuesto del codo: {:.1f}°'.format(math.degrees(route['elbow_pose']['rotation_rad']))
