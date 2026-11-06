"""V3 controller hosted by Trazado automático MEP, with shared plan controls."""
from System import String,Array
from System.Windows import Visibility
from . import model,store,operations,adapter
from .canvas import CorridorCanvas
from ...runtime import library_context
from concurrent.futures import ThreadPoolExecutor
from threading import Event


class DesignEditor:
    def __init__(self,window,document_id,get_source,get_level,api,doc,create_payload=None,get_configuration=None):
        self.document_id=document_id;self.get_source=get_source;self.get_level=get_level
        self.api=api;self.doc=doc;self.proposal=None;self.choices=[];self.cids=[];self.rendering=False;self.mtime=None
        self.levels=[];self.create_payload=create_payload;self.get_configuration=get_configuration
        if get_level is None:self.get_level=self.selected_level
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='ARQT-V3');self.future=None;self.cancel_event=Event()
        names=('Create Proposals Load Duplicate Export Network Draw Finish Insert Delete Undo Redo Fit Corridors '
               'Constraint SetConstraint SetNetwork Vertex X Y MoveVertex DeleteVertex Preview Info Tab '
               'ProfileReference Nominal Internal External Insulation BendRadius Clearance Analyze Generate CancelSearch Level').split()
        self.ui={n:window.FindName('Design'+n) for n in names}
        self.plan=CorridorCanvas(self.ui['Preview'],lambda:self.proposal,self.get_level,self.request,self.select,self.info,self.network)
        actions={'Create':self.create,'Load':self.load,'Duplicate':self.duplicate,'Export':self.export,
            'Draw':lambda:self.begin('corridor'),'Finish':self.plan.finish,'Insert':lambda:self.begin('insert_vertex'),
            'Delete':lambda:self.request('delete_corridor',self.selected()),
            'Undo':lambda:self.request('undo',{}),'Redo':lambda:self.request('redo',{}),'Fit':self.plan.fit,
            'SetConstraint':lambda:self.request('set_constraint',dict(self.selected(),constraint_type=model.CONSTRAINTS[self.ui['Constraint'].SelectedIndex])),
            'SetNetwork':lambda:self.request('set_network',dict(self.selected(),network_type=self.network())),
            'MoveVertex':self.move_vertex,'DeleteVertex':self.delete_vertex,
            'Analyze':self.analyze,'Generate':self.generate,'CancelSearch':self.cancel_event.set}
        for name,callback in actions.items():self.ui[name].Click+=self.safe(callback)
        self.ui['Corridors'].SelectionChanged+=self.safe(self.corridor_changed)
        self.ui['Proposals'].SelectionChanged+=self.safe(self.proposal_changed)
        self.ui['Vertex'].LostFocus+=self.safe(self.show_vertex)
        self.ui['Level'].SelectionChanged+=self.safe(self.plan.draw)
        def tab_changed(*args):
            visibility=Visibility.Collapsed if self.ui['Tab'].IsVisible else Visibility.Visible
            for name in ('Calculate','Analyze','Preview','Hide','ModelNetwork','AuditNetwork','SaveProposal','CancelSearch','Summary','Issues'):
                control=window.FindName(name)
                if control is not None:control.Visibility=visibility
            from System.Windows import GridLength
            window.Content.RowDefinitions[4].Height=GridLength(0 if self.ui['Tab'].IsVisible else 95)
            self.plan.draw()
        self.ui['Tab'].IsVisibleChanged+=self.safe(tab_changed)
        window.Closed+=lambda *args:self.close()

    def info(self,message):self.ui['Info'].Text=str(message)
    def network(self):return model.NETWORKS[max(0,self.ui['Network'].SelectedIndex)]
    def selected_level(self):
        index=self.ui['Level'].SelectedIndex
        return self.levels[index][0] if 0<=index<len(self.levels) else None
    def safe(self,callback):
        @library_context()
        def run(*args):
            try:callback()
            except Exception as exc:self.info(str(exc))
        return run

    def begin(self,mode):
        if self.proposal is None:raise ValueError('Cree o recupere un esquema antes de dibujar.')
        if self.get_level() is None:raise ValueError('Seleccione una planta.')
        self.plan.set_mode(mode)

    def selected(self):
        if not self.proposal or self.plan.selected not in self.proposal.corridors:raise ValueError('Seleccione un corredor.')
        return {'corridor_id':self.plan.selected}

    def request(self,operation,payload=None,revision=None):
        if self.proposal is None:raise ValueError('Cree o recupere un esquema.')
        p=self.proposal
        try:
            result=operations.dispatch(self.document_id,'routing_design_'+operation,
                dict(payload or {},proposal_id=p.proposal_id,expected_revision=p.revision if revision is None else revision))
            self.adopt(model.RoutingDesignProposal.from_dict(result))
            self.info('Guardado · revisión {} · esquema sin validación 3D'.format(self.proposal.revision))
        except Exception:
            self.adopt(store.get(self.document_id,p.proposal_id));raise

    def create(self):
        if self.create_payload is not None:
            payload=self.create_payload()
            def work():
                result=adapter.dispatch(self.doc,'routing_design_create',payload)
                self.adopt(model.RoutingDesignProposal.from_dict(result));self.refresh_choices()
                self.info('Esquema del trazador creado desde los destinos Revit seleccionados.')
            self.api(work);return
        source=self.get_source()
        if source is None:raise ValueError('Calcule o recupere primero la distribución de rejillas.')
        pid=source['proposal_id'];version=source['version']
        def work():
            result=adapter.dispatch(self.doc,'routing_design_create',{'source_proposal_id':pid,'source_version':version})
            self.adopt(model.RoutingDesignProposal.from_dict(result));self.refresh_choices()
            self.info('Esquema creado. Dibuje corredores en planta; cada edición se guarda automáticamente.')
        self.api(work)

    def adopt(self,p):
        changed=self.proposal is None or self.proposal.proposal_id!=p.proposal_id
        self.proposal=p
        if changed:self.plan.cancel();self.plan.selected=None
        self.mtime=__import__('os').stat(store.path(self.document_id,p.proposal_id)).st_mtime_ns
        self.rendering=True
        try:
            prior=self.get_level()
            self.levels=sorted({(r['level_id'],r['level']) for r in p.background['analysis']['rooms'] if r['uid'] in p.background['room_uids']},key=lambda q:q[1])
            self.ui['Level'].ItemsSource=Array[String]([name for _,name in self.levels])
            self.ui['Level'].SelectedIndex=next((i for i,row in enumerate(self.levels) if row[0]==prior),0 if self.levels else -1)
            self.cids=list(p.corridors)
            networks={'SUPPLY':'Impulsión','EXTRACT':'Extracción','SHARED':'Compartido'}
            self.ui['Corridors'].ItemsSource=Array[String]([cid[-8:]+' · '+networks[p.corridors[cid]['network_type']] for cid in self.cids])
            if self.plan.selected in self.cids:self.ui['Corridors'].SelectedIndex=self.cids.index(self.plan.selected)
            else:self.plan.selected=None
            self.choices=[p]+[q for q in self.choices if q.proposal_id!=p.proposal_id]
            self.ui['Proposals'].ItemsSource=Array[String]([q.name+' · r'+str(q.revision)+' · '+q.proposal_id[:8] for q in self.choices])
            self.ui['Proposals'].SelectedIndex=0
        finally:self.rendering=False
        self.select(self.plan.selected)
        self.plan.draw()

    def refresh_choices(self):
        source=self.get_source();drafts=store.list_proposals(self.document_id)
        if source:drafts=[p for p in drafts if p.background.get('housing_id')==source.get('housing_id')]
        self.choices=drafts;self.rendering=True
        try:
            self.ui['Proposals'].ItemsSource=Array[String]([p.name+' · r'+str(p.revision)+' · '+p.proposal_id[:8] for p in drafts])
            if self.proposal:self.ui['Proposals'].SelectedIndex=next((i for i,p in enumerate(drafts) if p.proposal_id==self.proposal.proposal_id),-1)
        finally:self.rendering=False

    def load(self):
        self.refresh_choices()
        if not self.choices:raise ValueError('No hay esquemas guardados para esta vivienda.')
        if self.proposal is None:self.adopt(self.choices[0])
        self.info('Esquema recuperado · r'+str(self.proposal.revision))

    def proposal_changed(self,*args):
        if self.rendering:return
        i=self.ui['Proposals'].SelectedIndex
        if 0<=i<len(self.choices):self.adopt(store.get(self.document_id,self.choices[i].proposal_id))

    def duplicate(self):
        if not self.proposal:raise ValueError('Recupere un esquema.')
        self.adopt(store.duplicate(self.document_id,self.proposal.proposal_id,self.proposal.revision));self.refresh_choices()

    def select(self,cid):
        if self.rendering:return
        self.rendering=True
        try:self.ui['Corridors'].SelectedIndex=self.cids.index(cid) if cid in self.cids else -1
        finally:self.rendering=False
        if self.proposal and cid in self.proposal.corridors:
            c=self.proposal.corridors[cid];self.ui['Constraint'].SelectedIndex=model.CONSTRAINTS.index(c['constraint_type'])
            try:index=int(self.ui['Vertex'].Text)-1
            except ValueError:index=0
            if not 0<=index<len(c['reference_xy_mm']):self.ui['Vertex'].Text='1'
            self.show_vertex();self.info(cid+' · '+c['network_type']+' · '+c['constraint_type'])

    def corridor_changed(self,*args):
        if self.rendering:return
        i=self.ui['Corridors'].SelectedIndex
        self.plan.selected=self.cids[i] if 0<=i<len(self.cids) else None
        self.select(self.plan.selected);self.plan.draw()

    def vertex_index(self):
        index=int(self.ui['Vertex'].Text)-1;c=self.proposal.corridors[self.selected()['corridor_id']]
        if not 0<=index<len(c['reference_xy_mm']):raise ValueError('Número de vértice fuera del corredor.')
        return index

    def show_vertex(self):
        if not self.proposal or self.plan.selected not in self.proposal.corridors:return
        xy=self.proposal.corridors[self.plan.selected]['reference_xy_mm'][self.vertex_index()]
        self.ui['X'].Text=str(xy[0]);self.ui['Y'].Text=str(xy[1])

    def move_vertex(self):
        self.request('move_vertex',dict(self.selected(),index=self.vertex_index(),point_mm=[float(self.ui[k].Text.replace(',','.')) for k in ('X','Y')]))

    def delete_vertex(self):self.request('delete_vertex',dict(self.selected(),index=self.vertex_index()))

    def export(self):
        if self.proposal is None:raise ValueError('Seleccione un esquema.')
        from .preview import export
        result=export(self.proposal,self.get_level());self.info('Imagen guardada: '+result['path'])

    def analyze(self):
        if self.proposal is None:raise ValueError('Cree un esquema antes de analizar.')
        if self.future and not self.future.done():raise ValueError('Cancele o espere al cálculo en curso.')
        number=lambda k:float(self.ui[k].Text.replace(',','.'))
        reference=self.ui['ProfileReference'].Text.strip()
        if not reference:raise ValueError('Indique la referencia del producto del conducto.')
        profile=model.DuctProfile('project-profile','SUPPLY',number('Nominal'),number('Internal'),number('External'),
            number('Insulation'),number('BendRadius'),reference,False)
        from dataclasses import asdict
        p=self.proposal;payload={'proposal_id':p.proposal_id,'expected_revision':p.revision,
            'duct_profile':asdict(profile),'clearance_mm':number('Clearance')}
        if self.get_configuration:payload['configuration']=self.get_configuration()
        def work():
            result=adapter.dispatch(self.doc,'routing_design_analyze',payload)
            self.adopt(model.RoutingDesignProposal.from_dict(result))
            self.info('Geometría real capturada. El perfil introducido queda pendiente de verificación del producto.')
        self.api(work)

    def generate(self):
        if self.proposal is None:raise ValueError('Cree un esquema antes de generar.')
        if self.future and not self.future.done():raise ValueError('Ya hay un cálculo en curso.')
        from .service import generate_corridors
        p=self.proposal;self.cancel_event.clear()
        self.future=self.executor.submit(generate_corridors,self.document_id,p.proposal_id,p.revision,None,self.cancel_event.is_set)
        self.info('Generando corredores. Puede seguir navegando por la planta o cancelar el cálculo.')

    def close(self):
        self.cancel_event.set();self.executor.shutdown(wait=False,cancel_futures=True)

    @library_context()
    def poll(self):
        if self.plan.drag or self.plan.mode:return
        if self.future and self.future.done():
            task=self.future;self.future=None
            try:
                p=task.result()
                if self.proposal and p.proposal_id==self.proposal.proposal_id:
                    self.adopt(p);stats=p.corridor_graph['stats']
                    self.info('{} corredores candidatos · {} nodos · {}. Curvas y capacidad de haces pendientes.'.format(
                        stats['edges'],stats['nodes'],'cálculo parcial' if not stats['enumeration_finished'] else 'enumeración terminada'))
            except Exception as exc:self.info('No se ha publicado el cálculo: '+str(exc))
        if not self.proposal or self.plan.drag or self.plan.mode:return
        p=self.proposal
        try:
            mtime=__import__('os').stat(store.path(self.document_id,p.proposal_id)).st_mtime_ns
            if mtime!=self.mtime:self.adopt(store.get(self.document_id,p.proposal_id));self.info('Esquema actualizado desde otro cliente.')
            source=self.get_source()
            if source and (source['proposal_id']!=p.source_proposal_id or source['version']!=p.source_version):
                self.info('La distribución de rejillas ha cambiado. Este esquema conserva su revisión de origen; cree otro esquema para la nueva distribución.')
        except Exception as exc:self.info('No se pudo sincronizar el esquema: '+str(exc))
