"""Modal WPF client, sharing the MCP dispatcher and valid Revit API context."""
import copy
import json
import os
import traceback
import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System.Data')
from System import String,Boolean,Array,Action
from System.Data import DataTable
from System.Windows import MessageBox
from System.Windows.Markup import XamlReader
from System.Windows.Controls import DataGridEditingUnit
from System.Windows.Threading import Dispatcher,DispatcherFrame,DispatcherPriority
from . import settings,service,mcp
from .connector_labels import connection_info,validate_origin
from .origin_groups import bind_origin_groups


def project_identity(doc):
    from ..ownership import document_id
    return document_id(doc)


def _json(value):return json.dumps(value,ensure_ascii=False,indent=2)


def _table(columns,rows):
    t=DataTable()
    for name,kind in columns:t.Columns.Add(name,kind)
    for values in rows:
        r=t.NewRow()
        for name,value in values.items():r[name]=value
        t.Rows.Add(r)
    return t


def _pump():
    frame=DispatcherFrame()
    Dispatcher.CurrentDispatcher.BeginInvoke(DispatcherPriority.Background,Action(lambda:setattr(frame,'Continue',False)))
    Dispatcher.PushFrame(frame)


def _window(doc,state,on_loaded=None):
    path=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..','ARQ-T.tab','MEP.panel','TrazadoAutomaticoMEP.pushbutton','legacy_window.xaml'))
    with open(path,encoding='utf8') as f:window=XamlReader.Parse(f.read())
    names=('DocumentLabel HRV Tabs Refresh OriginsNone Origins OriginRole PreviousConnections Destinations DestRole DestAll DestNone '
           'Diameter Insulation Clearance BendRadius Resolution UseDistributors IncludeLinks Configuration SaveSettings '
           'ManualOrigins ManualDestinations Alternatives Alternative SelectAlternative Recalculate Routes RouteDiameter '
           'RouteClearance Waypoints Responsible TechnicalReference Approve Properties ListSaved LoadSaved Saved Summary '
           'Issues Analyze Calculate Preview Hide SaveProposal ModelNetwork AuditNetwork CancelSearch Close RoutingMode').split()
    ui={n:window.FindName(n) for n in names};state['action']=None
    s=state['configuration'];ui['DocumentLabel'].Text=doc.Title+' · recuperación de calor · recorridos y modelado explícito de conductos'
    for name in ('OriginRole','DestRole'):ui[name].ItemsSource=Array[String](['SUPPLY','EXTRACT'])
    for n,key in [('Insulation','insulation_mm'),('Diameter','outer_diameter_mm')]:ui[n].Text=str(s['section'][key])
    for n,key in [('Clearance','clearance_mm'),('BendRadius','bend_radius_mm')]:ui[n].Text=str(s[key])
    ui['Resolution'].Text=str(s['search']['fine_resolution_mm'])
    mode_names=list(settings.ROUTING_MODES)
    ui['RoutingMode'].ItemsSource=Array[String](['{} · {} s'.format(n,settings.ROUTING_MODES[n]) for n in mode_names])
    matching=next((n for n in mode_names if settings.ROUTING_MODES[n]==s['search']['max_total_seconds']),s.get('routing_mode','standard'))
    ui['RoutingMode'].SelectedIndex=mode_names.index(matching)
    ui['UseDistributors'].IsChecked=s['use_distributors'];ui['IncludeLinks'].IsChecked=s['include_links']
    ui['Configuration'].Text=_json(s)
    ui['ManualOrigins'].Text=_json(state.get('manual_origins',[]));ui['ManualDestinations'].Text=_json(state.get('manual_destinations',[]))
    ui['Issues'].Text=state.get('message','\n'.join(state['catalog'].get('warnings',[])) or 'Los pasos estructurales requieren revisión técnica explícita. Los radios de codo son provisionales.')
    hrvs=[d for d in state['catalog']['devices'] if 'recuperador' in d['family'].casefold()]
    for d in hrvs:ui['HRV'].Items.Add('{} · ID {}'.format(d['family'],d['element_id']))
    hrv_ids=[d['id'] for d in hrvs]
    if s.get('hrv_id') in hrv_ids:ui['HRV'].SelectedIndex=hrv_ids.index(s['hrv_id'])
    elif len(hrv_ids)==1:ui['HRV'].SelectedIndex=0
    ui['Tabs'].SelectedIndex=state.get('tab',0)
    busy_names=('HRV Tabs Analyze Calculate Preview Hide SaveProposal ModelNetwork AuditNetwork PreviousConnections SaveSettings ListSaved LoadSaved Close').split()

    def error(exc):
        state['message']=str(exc);ui['Issues'].Text=str(exc);print(traceback.format_exc())
        MessageBox.Show(str(exc),'ARQ-T | Trazado automático MEP')

    def selected():
        row=ui['Routes'].SelectedItem
        if row is None:raise ValueError('Seleccione una ruta.')
        return str(row['Id'])

    def commit():
        for n in ('Origins','Destinations','Routes'):
            ui[n].CommitEdit(DataGridEditingUnit.Cell,True);ui[n].CommitEdit(DataGridEditingUnit.Row,True)

    def inputs():
        commit();s=json.loads(ui['Configuration'].Text)
        number=lambda n:float(ui[n].Text.replace(',','.'))
        mode=mode_names[max(0,ui['RoutingMode'].SelectedIndex)]
        # Preserve an existing custom budget unless the user changes mode.
        if mode!=state['configuration'].get('routing_mode','standard'):
            s=settings.merge(s,{'routing_mode':mode,'search':{'max_total_seconds':settings.ROUTING_MODES[mode]}})
        s=settings.merge(s,{'section':{'shape':'CIRCULAR','nominal_diameter_mm':number('Diameter'),
              'outer_diameter_mm':number('Diameter'),'insulation_mm':number('Insulation')},
              'clearance_mm':number('Clearance'),'bend_radius_mm':number('BendRadius'),
              'search':{'fine_resolution_mm':number('Resolution')},'use_distributors':bool(ui['UseDistributors'].IsChecked),
              'include_links':bool(ui['IncludeLinks'].IsChecked)})
        if ui['HRV'].SelectedIndex>=0:s['hrv_id']=hrv_ids[ui['HRV'].SelectedIndex]
        if state.get('route_table') is not None:
            s['constraints']['locked_routes']=[str(r['Id']) for r in state['route_table'].Rows if r['Locked']]
        s=settings.validate(s)
        origins=[];destinations=[]
        assignments=[]
        for row in state['origin_table'].Rows:
            if row['Include']:
                if str(row['Network']) not in ('SUPPLY','EXTRACT'):raise ValueError('Asigne impulsión (SUPPLY) o extracción (EXTRACT) a cada distribuidor marcado.')
                assignments.append({'element_id':int(str(row['ElementId'])),'network_type':str(row['Network'])})
        for row in state['destination_table'].Rows:
            if row['Include']:
                if not str(row['PortId']):raise ValueError('Terminal sin conector: '+str(row['Device']))
                destinations.append({'terminal_id':str(row['TerminalId']),'port_id':str(row['PortId']),'network_type':str(row['Network'])})
        if hrv_ids or assignments or destinations:s['equipment_assignments']=assignments
        manual_origins=json.loads(ui['ManualOrigins'].Text);manual_destinations=json.loads(ui['ManualDestinations'].Text)
        if not isinstance(manual_origins,list) or not isinstance(manual_destinations,list):raise ValueError('Las referencias manuales deben ser listas JSON.')
        state.update(configuration=s,origins=origins+manual_origins,destinations=destinations+manual_destinations,
                     manual_origins=manual_origins,manual_destinations=manual_destinations)
        return s

    def cancelled():
        _pump();return state.get('cancelled',False)

    def operation(op,extra=None,write=False,read_inputs=True):
        if read_inputs:inputs()
        payload={'configuration':state['configuration']}
        if state.get('proposal'):payload['proposal']=state['proposal']
        if state.get('snapshot'):payload['snapshot']=state['snapshot']
        payload.update(extra or {})
        return mcp.dispatch(doc,op,payload,allow_write=write,cancelled=cancelled)

    def execute(callback):
        if state.get('busy'):return
        state['busy']=True;state['cancelled']=False
        for n in busy_names:ui[n].IsEnabled=False
        ui['Issues'].Text='Procesando… Puede cancelar el cálculo.';_pump()
        try:callback()
        except Exception as exc:error(exc)
        finally:
            state['busy']=False
            for n in busy_names:ui[n].IsEnabled=True

    def catalog_tables():
        if state.get('origin_table') is None:
            rows=[]
            assigned={str(a.get('element_id',a.get('element_uid'))):a for a in state['configuration'].get('equipment_assignments',[])}
            for device in state['catalog']['devices']:
                if not device.get('is_configured_distributor'):continue
                from .systems.heat_recovery_topology import distributor_ports
                try:
                    main,branches=distributor_ports(device)
                    from collections import Counter
                    sizes=Counter(p['size']['diameter_mm'] for p in branches)
                    details='Principal Ø{:.0f} mm · {}'.format(main['size']['diameter_mm'],', '.join('{} × Ø{:.0f} mm'.format(count,size) for size,count in sorted(sizes.items())))
                except ValueError as exc:main=None;branches=[];details=str(exc)
                assignment=assigned.get(str(device['element_id']),assigned.get(device['id']))
                classes={p['classification'] for p in device['connectors']}
                from ..family_rules import distributor_network
                suggested=distributor_network(device['family']) or ''
                rows.append(dict(Include=bool(assignment),Selectable=True,Device='ID {} · {}'.format(device['element_id'],device['name']),
                    ElementId=str(device['element_id']),Network=assignment['network_type'] if assignment else suggested,
                    Capacity=str(len(branches)),Available=str(sum(not p['connected'] for p in branches)),Details=details))
            columns=[(n,Boolean if n in ('Include','Selectable') else String) for n in ('Include Selectable Device ElementId Network Capacity Available Details').split()]
            state['origin_table']=_table(columns,rows)
        if state.get('destination_table') is None:
            rows=[]
            for terminal in state['catalog']['terminals']:
                ports=terminal['connectors'] or [None]
                room=doc.GetElement(terminal['room_uid']) if terminal.get('room_uid') else None
                if room:
                    from pyrevit.api import DB
                    room_name=room.get_Parameter(DB.BuiltInParameter.ROOM_NAME).AsString()
                else:room_name=''
                for p in ports:
                    rows.append(dict(Include=len(ports)==1 and terminal.get('suggested_network_type') in ('SUPPLY','EXTRACT') and terminal['airflow_lps'] is not None,Device=terminal['family']+' · '+str(terminal['element_id']),
                        TerminalId=terminal['id'],PortId=p['id'] if p else '',Port=str(p['connector_id']) if p else 'No disponible',
                        Network=terminal.get('suggested_network_type') or '',Flow=str(terminal['airflow_lps']) if terminal['airflow_lps'] is not None else 'No verificable',
                        Room=room_name,XYZ=', '.join('{:.1f}'.format(v) for v in p['xyz_mm']) if p else ''))
            columns=[(n,Boolean if n=='Include' else String) for n in ('Include Device TerminalId PortId Port Network Flow Room XYZ').split()]
            state['destination_table']=_table(columns,rows)
        ui['Origins'].ItemsSource=state['origin_table'].DefaultView
        ui['Destinations'].ItemsSource=state['destination_table'].DefaultView

    def show_results():
        from .readiness import status_label,blocker
        proposal=state.get('proposal')
        if not proposal:return
        rows=[];locked=proposal['configuration']['constraints']['locked_routes']
        for r in proposal['routes']:
            rows.append(dict(Locked=r['route_id'] in locked,Id=r['route_id'],Network=r['network_type'],Flow=str(r['airflow_lps']),
                Diameter=str(r['section'].get('outer_diameter_mm','rect.')),Length='{:.2f}'.format(r.get('length_m',0)),
                Bends=str(r.get('bends',0)),Status=status_label(r),Approval=r['approval_status']))
        state['route_table']=_table([(n,Boolean if n=='Locked' else String) for n in ('Locked Id Network Flow Diameter Length Bends Status Approval').split()],rows)
        ui['Routes'].ItemsSource=state['route_table'].DefaultView
        sm=proposal['summary']
        ui['Summary'].Text='{} rutas · {} válidas · {} condicionadas · {} sin resolver · {:.2f} m · {} codos · {} pasos condicionados · coste {:.2f}'.format(
            sm['route_count'],sm['valid'],sm['conditional'],sm['unresolved'],sm['total_length_m'],sm['bends'],sm['structural_crossings'],sm['cost'])
        counts=(proposal.get('topology') or {}).get('counts',{})
        ui['Issues'].Text='\n'.join(['{}: {} terminales · {} ramales nuevos · {} terminales conservados · {} tomas libres'.format(n,c['terminals'],c['new_branches'],c['preserved_terminals'],c['free_distributor_branches']) for n,c in counts.items()]+proposal.get('warnings',[]))
        pending=['Destino {} · {}: {}'.format(r['destination'].get('element_id','—'),status_label(r),blocker(r))
                 for r in proposal['routes'] if r.get('native_status') not in ('VALID','VALID_WITH_WARNINGS')]
        if pending:ui['Issues'].Text+='\n'+'\n'.join(pending)
        parallel=proposal.get('stats',{}).get('parallel') or {}
        if parallel.get('started_workers'):
            ui['Issues'].Text+='\nCálculo paralelo: hasta {} procesos; {} procesadores lógicos reservados.'.format(
                parallel['max_workers'],parallel['reserved_cpus'])
        exchanges=sum(e.get('accepted',False) for e in proposal.get('stats',{}).get('coordination',[]))
        if proposal['configuration'].get('coordination',{}).get('enabled',False):
            ui['Issues'].Text+='\nCoordinación conjunta: {} intercambios aceptados. Prioridad: completar conexiones, reducir pasos condicionados y reducir el coste total. Las rutas bloqueadas se conservan.'.format(exchanges)
            portfolio=proposal.get('stats',{}).get('portfolio') or {}
            ui['Issues'].Text+='\n{} iteraciones automáticas · {} candidatos comparados · {} cambios de toma. Búsqueda en ambos sentidos y por corredores del plenum.'.format(
                proposal.get('stats',{}).get('automatic_iterations',0),portfolio.get('candidate_count',0),len(portfolio.get('changed_ports',[])))
        ui['Configuration'].Text=_json(proposal['configuration']);state['configuration']=copy.deepcopy(proposal['configuration'])
        if rows:ui['Routes'].SelectedIndex=next((i for i,r in enumerate(rows) if r['Id']==state.get('selected')),0)

    def route_selected(sender,args):
        if not state.get('proposal') or ui['Routes'].SelectedItem is None:return
        rid=selected();state['selected']=rid;r=next(r for r in state['proposal']['routes'] if r['route_id']==rid)
        ui['RouteDiameter'].Text=str(r['section'].get('outer_diameter_mm',125));ui['RouteClearance'].Text=str(r['clearance_mm'])
        ui['Waypoints'].Text=_json(state['configuration']['constraints']['waypoints'].get(rid,[])).replace('\n',' ')
        shown={k:v for k,v in r.items() if k not in ('alternatives','primitives','sampled_points_mm','xyz_points','points_mm')}
        from .readiness import blocker
        shown['diagnostico']=blocker(r)
        ui['Properties'].Text=_json(shown);ui['Alternative'].Items.Clear()
        for i,a in enumerate(r.get('alternatives',[])):
            ui['Alternative'].Items.Add('{} · {} · {:.2f} m · {:.2f}'.format(i+1,a['collision_status'],a.get('length_m',0),a.get('cost',{}).get('total',0)))
        if r.get('alternatives'):ui['Alternative'].SelectedIndex=0

    def analyze():
        state['snapshot']=operation('analyze_mep_geometry',{'include_geometry':True})
        a=state['snapshot'];ui['Summary'].Text='{} obstáculos · {} triángulos · {} regiones de plenum · {:.2f} s'.format(a['stats']['elements'],a['stats']['triangles'],a['stats']['region_count'],a['stats']['extraction_seconds'])
        ui['Issues'].Text='\n'.join(a['warnings']) or 'Geometría leída. No se han modificado elementos.'

    def calculate(selection=False):
        s=inputs();old=state.get('proposal')
        rid=selected() if selection else None
        prepared=operation('prepare_terminal_adapters',{'destinations':state['destinations']},write=True,read_inputs=False)
        if prepared['created'] or prepared.get('removed_owned_caps'):old=None;state['proposal']=None
        if selection:
            s['route_overrides'][rid]={'section':{'nominal_diameter_mm':float(ui['RouteDiameter'].Text.replace(',','.')),
               'outer_diameter_mm':float(ui['RouteDiameter'].Text.replace(',','.'))},'clearance_mm':float(ui['RouteClearance'].Text.replace(',','.'))}
            s['constraints']['waypoints'][rid]=json.loads(ui['Waypoints'].Text)
            ui['Configuration'].Text=_json(s)
        state['snapshot']=None
        result=operation('optimize_mep_routes' if old else 'create_mep_route_proposal',
             dict({'origins':state['origins'],'destinations':state['destinations'],'configuration':s},**({'route_ids':[rid]} if selection else {})),read_inputs=False)
        state['proposal']=result;state['configuration']=result['configuration'];show_results();ui['Tabs'].SelectedIndex=4

    def alternatives():
        state['proposal']=operation('get_mep_route_alternatives',{'route_id':selected()},read_inputs=False);show_results()

    def choose_alternative():
        state['proposal']=operation('select_mep_route_alternative',{'route_id':selected(),'alternative_index':int(ui['Alternative'].SelectedIndex)},read_inputs=False);show_results()

    def approve():
        route=next(r for r in state['proposal']['routes'] if r['route_id']==selected())
        if route.get('native_status') not in ('VALID','VALID_WITH_WARNINGS'):
            from .readiness import blocker
            raise ValueError(blocker(route))
        state['proposal']=operation('approve_mep_route',{'route_id':selected(),'explicit':True,
            'technical_approval':{'responsible':ui['Responsible'].Text.strip(),'reference':ui['TechnicalReference'].Text.strip()}},read_inputs=False)
        show_results()

    def save_proposal():
        if not state.get('proposal'):raise ValueError('Calcule una propuesta antes de guardarla.')
        result=operation('save_mep_route_proposal',{'explicit':True},write=True,read_inputs=False)
        ui['Issues'].Text='Propuesta guardada en el proyecto: '+result['proposal_id']+' · '+result['status']

    def connection_report(result):
        state['connection_report']=result
        ui['Properties'].Text=_json(result)
        labels={'TOMA_SIN_USAR':'Tomas sin usar','RUTA_SIN_MODELAR':'Extremos de rutas sin modelar',
                'CONEXION_PREVIA_CONSERVADA':'Conexiones previas conservadas','CONEXION_PREVIA_YA_NO_EXISTE':'Conexiones previas que han desaparecido',
                'CONDUCTO_SIN_CONECTAR':'Extremos de conducto pendientes de conexión',
                'CONEXION_INCORRECTA':'Conexiones incorrectas','CONECTADO':'Conexiones correctas',
                'CONEXION_EXISTENTE_AJENA_A_PROPUESTA':'Conexiones existentes fuera de la propuesta'}
        lines=['{}: {}'.format(labels.get(k,k),v) for k,v in result['counts'].items()]
        for row in result['connections']:
            if row['status'] in ('CONDUCTO_SIN_CONECTAR','CONEXION_INCORRECTA','RUTA_SIN_MODELAR'):
                lines.append('{} · ruta {} · conducto {} · elemento {} / toma {}'.format(labels.get(row['status'],row['status']),row.get('route_id',''),row.get('duct_element_id','—'),row.get('target_element_id'),row.get('target_connector_id')))
        lines.extend('{}: {}'.format(f['route_id'],f['reason']) for f in result.get('failures',[]))
        for conflict in result.get('geometry_conflicts',[]):
            lines.append('{} · ruta {} · conducto/accesorio {} · conflicto con {} ({}){}'.format(
                conflict['status'],conflict.get('route_id',''),conflict.get('element_id','—'),
                conflict.get('conflicting_element_id','—'),conflict.get('conflicting_name',conflict.get('reason','')),
                ' · ruta revertida' if conflict.get('route_reverted') else ''))
        caps=result.get('caps',{})
        if caps:lines.append('Tapas añadidas: {}'.format(len(caps.get('created',[]))))
        lines.extend('Tapa pendiente · distribuidor {} / toma {}: {}'.format(f.get('distributor_id','—'),f.get('connector_id','—'),f['reason']) for f in caps.get('failures',[]))
        if result.get('note'):lines.append(result['note'])
        ui['Issues'].Text='\n'.join(lines);ui['Summary'].Text='Conexiones de recuperación de calor · '+', '.join('{}: {}'.format(labels.get(k,k),v) for k,v in result['counts'].items())
        ui['Tabs'].SelectedIndex=4

    def model_network():
        if not state.get('proposal'):raise ValueError('Calcule y apruebe los recorridos primero.')
        inputs()
        from .revit.network_model import model
        result=model(doc,state['proposal'],True,cancelled)
        connection_report(result)
        if not result.get('created') and result.get('failures'):
            messages=list(dict.fromkeys(f['reason'] for f in result['failures']))
            MessageBox.Show('No se ha creado ningún conducto.\n\n'+'\n\n'.join(messages),'ARQ-T · Modelado bloqueado')
        state['snapshot']=None

    def audit_network():
        from .revit.network_model import audit
        connection_report(audit(doc,state.get('proposal')))

    def list_saved():
        state['saved']=operation('get_mep_route_proposal',read_inputs=False);ui['Saved'].Items.Clear()
        for p in state['saved']:ui['Saved'].Items.Add('{} · {} · {} rutas · {}'.format(p['created_utc'],p['status'],len(p['routes']),p['proposal_id']))
        if state['saved']:ui['Saved'].SelectedIndex=0

    def load_saved():
        index=ui['Saved'].SelectedIndex
        if index<0:raise ValueError('Seleccione una propuesta guardada.')
        p=state['saved'][index];state['proposal']=copy.deepcopy(p);state['configuration']=copy.deepcopy(p['configuration']);state['snapshot']=None
        state['origins']=p['origins'];state['destinations']=p['destinations']
        state['manual_origins']=[o for o in p['origins'] if not o.get('port_id')]
        state['manual_destinations']=[o for o in p['destinations'] if not o.get('port_id')]
        assignments={str(a.get('element_id',a.get('element_uid'))):a for a in p['configuration'].get('equipment_assignments',[])}
        for d in state['catalog']['devices']:
            legacy=[o for o in p['origins'] if o.get('port_id') in {v['id'] for v in d['connectors']} and o.get('kind')=='DISTRIBUTOR']
            if legacy:assignments.setdefault(str(d['element_id']),{'network_type':legacy[0]['network_type']})
        for row in state['origin_table'].Rows:
            value=assignments.get(str(row['ElementId']));row['Include']=value is not None
            if value:row['Network']=value['network_type']
        by_port={o.get('port_id'):o for o in p['destinations'] if o.get('port_id')}
        for row in state['destination_table'].Rows:
            value=by_port.get(str(row['PortId']));row['Include']=value is not None
            if value:row['Network']=value['network_type']
        state['action']='REOPEN';window.Close()

    def refresh():
        state['catalog']=operation('get_mep_connection_points');state['origin_table']=None;state['destination_table']=None;catalog_tables()
        ui['Issues'].Text='{} equipos y {} terminales detectados. Revise las selecciones.\n{}'.format(len(state['catalog']['devices']),len(state['catalog']['terminals']),'\n'.join(state['catalog'].get('warnings',[])))

    def previous_connections():
        inputs()
        plan=mcp.dispatch(doc,'inspect_heat_recovery_previous',{'configuration':state['configuration'],'destinations':state['destinations']})
        from System.Windows import MessageBoxButton,MessageBoxResult
        blocked=[c for c in plan['components'] if c['blocked']]
        if not plan['delete_ids']:
            ui['Issues'].Text='No hay conexiones eliminables dentro de la selección. Se conserva lo existente.\n'+'\n'.join('; '.join(c['blocked']) for c in blocked)
            return
        text='Conexiones previas de los equipos y terminales seleccionados.\n\nElementos que se eliminarían: {}\n{}\n\nSí: eliminar esos elementos y recalcular después.\nNo: conservar todas las conexiones existentes.\nCancelar: volver sin cambios.'.format(plan['delete_ids'],'Se conservan {} componentes compartidos, fijados o agrupados.'.format(len(blocked)) if blocked else '')
        decision=MessageBox.Show(text,'ARQ-T · Conservar o eliminar conexiones previas',MessageBoxButton.YesNoCancel)
        if decision!=MessageBoxResult.Yes:
            ui['Issues'].Text='Se conservan todas las conexiones existentes.';return
        result=mcp.dispatch(doc,'remove_heat_recovery_previous',{'plan':plan,'confirmation_token':plan['confirmation_token'],'explicit':True},allow_write=True)
        state['proposal']=None;state['snapshot']=None;state['route_table']=None
        ui['Routes'].ItemsSource=None;ui['Configuration'].Text=_json(state['configuration'])
        refresh();ui['Issues'].Text='Eliminados los elementos revisados: {}. Calcule de nuevo los recorridos.'.format(result['deleted_ids'])

    def mark(name,value):
        commit()
        for row in state[name].Rows:
            row['Include']=value and (bool(row['Selectable']) if name=='origin_table' else bool(str(row['PortId'])) and str(row['Flow'])!='No verificable')

    def preview():
        if not state.get('proposal'):raise ValueError('Calcule o recupere una propuesta primero.')
        inputs();state['tab']=4;state['action']='PREVIEW';window.Close()

    def hide():
        result=operation('hide_mep_routes',{'explicit':True},write=True,read_inputs=False)
        ui['Issues'].Text='Se han retirado {} elementos de vista previa propios.'.format(len(result['removed_ids']))

    def save_settings():
        configuration=inputs();settings.save(configuration)
        if configuration.get('hrv_id'):
            mcp.dispatch(doc,'set_heat_recovery_equipment_assignments',{'configuration':configuration,'explicit':True},allow_write=True)
        ui['Issues'].Text='Preferencias guardadas y asignación lógica de distribuidores registrada en este proyecto.'

    ui['Routes'].SelectionChanged+=route_selected
    catalog_tables();show_results()
    actions={'Analyze':analyze,'Calculate':calculate,'Recalculate':lambda:calculate(True),'Alternatives':alternatives,
        'SelectAlternative':choose_alternative,'Approve':approve,'SaveProposal':save_proposal,'ListSaved':list_saved,
        'LoadSaved':load_saved,'Refresh':refresh,'DestAll':lambda:mark('destination_table',True),
        'DestNone':lambda:mark('destination_table',False),'OriginsNone':lambda:mark('origin_table',False),
        'SaveSettings':save_settings,'Preview':preview,'Hide':hide,'ModelNetwork':model_network,'AuditNetwork':audit_network,'PreviousConnections':previous_connections}
    for name,callback in actions.items():ui[name].Click+=lambda sender,args,fn=callback:execute(fn)
    ui['CancelSearch'].Click+=lambda sender,args:state.update(cancelled=True)
    ui['Close'].Click+=lambda sender,args:window.Close()
    def closing(sender,args):
        if state.get('busy') and state.get('action') is None:
            state['cancelled']=True;args.Cancel=True
    window.Closing+=closing
    from .design.editor import DesignEditor
    from .design import store as design_store
    from System import TimeSpan
    from System.Windows.Threading import DispatcherTimer
    def design_inputs():
        configuration=inputs()
        return {'configuration':configuration,'destinations':copy.deepcopy(state['destinations'])}
    design_editor=DesignEditor(window,state['document_id'],lambda:None,None,execute,doc,
        create_payload=design_inputs,get_configuration=inputs)
    state['design_editor']=design_editor
    if state.get('design_proposal_id'):
        try:design_editor.adopt(design_store.get(state['document_id'],state['design_proposal_id']))
        except (ValueError,OSError) as exc:design_editor.info(str(exc))
    timer=DispatcherTimer();timer.Interval=TimeSpan.FromMilliseconds(800)
    timer.Tick+=lambda *args:design_editor.poll()
    def design_closed(*args):
        timer.Stop()
        if design_editor.proposal:state['design_proposal_id']=design_editor.proposal.proposal_id
    window.Closed+=design_closed;timer.Start()
    if on_loaded:window.Loaded+=lambda sender,args:on_loaded(window,ui,state)
    window.ShowDialog()
    state['tab']=ui['Tabs'].SelectedIndex
    return state


def show_legacy(doc,uidoc,_on_loaded=None):
    if doc is None or doc.IsFamilyDocument:raise ValueError('Abra un proyecto Revit.')
    s=settings.load()
    saved=mcp.dispatch(doc,'get_heat_recovery_equipment_assignments')
    if saved:s=settings.merge(s,saved)
    catalog=mcp.dispatch(doc,'get_mep_connection_points',{'configuration':s})
    state={'configuration':s,'catalog':catalog,'proposal':None,'snapshot':None,'origin_table':None,'destination_table':None,
           'document_id':project_identity(doc)}
    preview_ids=[]
    try:
        while True:
            _window(doc,state,_on_loaded)
            if state['action']=='REOPEN':continue
            if state['action']!='PREVIEW':break
            from .revit import proposals
            from pyrevit.api import DB,UI
            from Autodesk.Revit.Exceptions import OperationCanceledException
            from System.Collections.Generic import List
            views=[v for v in DB.FilteredElementCollector(doc).OfClass(DB.View3D) if not v.IsTemplate and not v.IsPerspective]
            if not views:
                MessageBox.Show('Abra o cree una vista 3D ortogonal antes de mostrar los ejes.','ARQ-T');continue
            try:
                info=mcp.dispatch(doc,'preview_mep_routes',{'proposal':state['proposal'],'explicit':True},allow_write=True)
                preview_ids=info['preview_ids']
                uidoc.ActiveView=views[0]
                ids=List[DB.ElementId]([DB.ElementId(i) for i in preview_ids])
                uidoc.Selection.SetElementIds(ids)
                if preview_ids:uidoc.ShowElements(ids)
                if preview_ids:
                    try:
                        ref=uidoc.Selection.PickObject(UI.Selection.ObjectType.Element,'Seleccione un eje para consultar su ruta. Esc vuelve a la ventana y retira los ejes.')
                        data=proposals.metadata(doc.GetElement(ref.ElementId))
                        if data and data.get('kind')=='ROUTE_PREVIEW':state['selected']=data['route_id']
                    except OperationCanceledException:pass
                state['message']='Vista 3D revisada. Los ejes se han retirado; puede volver a mostrarlos.'
            except Exception as exc:
                state['message']=str(exc);MessageBox.Show(str(exc),'ARQ-T | Vista previa')
            finally:
                if preview_ids:
                    mcp.dispatch(doc,'hide_mep_routes',{'proposal_id':state['proposal']['proposal_id'],'explicit':True},allow_write=True);preview_ids=[]
    finally:
        if preview_ids:mcp.dispatch(doc,'hide_mep_routes',{'proposal_id':state['proposal']['proposal_id'],'explicit':True},allow_write=True)
    return state


def show(doc,uidoc,_on_loaded=None):
    from .design.workspace import show as show_workspace
    return show_workspace(doc,uidoc,_on_loaded)
