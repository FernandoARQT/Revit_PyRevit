"""Choose compatible routes and equivalent manifold ports as one bounded set."""
import copy
import time
from . import cooperative as c
from ..settings import for_route,profile as make_profile
from .route_geometry import validate_route_geometry,rejected_path
from .candidate_repository import audit


def source_id(route,request):
    return (route.get('chosen_source') or request['source'])['id']


def parallel_candidates(workers,fixed,pending,pools,configuration,fixed_ports,deadline,cancelled,pass_index=0,unresolved_ids=None):
    jobs=[];metadata=[];attempts=[];options=configuration['coordination'];groups=[]
    scenario='B' if (pass_index%2 or configuration['search']['max_iterations']==1) and configuration.get('structural_crossings')=='PROPOSAL_ONLY' else 'A'
    unresolved_ids=set(unresolved_ids) if unresolved_ids is not None else {r['route_id'] for r in pending}
    for request in pending:
        if time.monotonic()>=deadline or (cancelled and cancelled()):break
        rid=request['route_id'];original=request['source']
        ports={p['id']:p for p in [original]+request.get('source_alternatives',[]) if p['id'] not in fixed_ports}
        ports=sorted(ports.values(),key=lambda p:(p['id']!=original['id'],
            sum((a-b)**2 for a,b in zip(p['xyz_mm'],request['destination']['xyz_mm']))))
        from .multi_route import compact_anchor,_stub
        local=for_route(configuration,rid);p=make_profile(local);viable=[]
        for port in ports:
            if time.monotonic()>=deadline or (cancelled and cancelled()):break
            anchor=compact_anchor(fixed,port,p,local) if request.get('compact_connections') else _stub(port,local['connection_straight_mm'])
            if anchor is not None and fixed.free(anchor[1],anchor[1],p.radius_mm):viable.append(port)
            else:attempts.append({'route_id':rid,'source_id':port['id'],'reason':'NO_FREE_BULK_START','phase':'PORT_ADMISSION'})
        if not viable:continue
        offset=pass_index%len(viable);viable=viable[offset:]+viable[:offset]
        if configuration['search']['max_iterations']<=1:
            variants=[(port,False) for port in viable]
        else:
            # One focused port per pass. Unresolved routes receive a second
            # direction; solved routes contribute only one optional candidate.
            variants=[(port,bool(pass_index%2)) for port in viable] if rid in unresolved_ids else [(viable[0],bool(pass_index%2))]
            if rid in unresolved_ids and len(viable)==1:variants.append((viable[0],not bool(pass_index%2)))
        group=[]
        targets=[request['destination']]+request.get('destination_alternatives',[])
        if request.get('destination_alternatives'):
            # Interleave all poses across terminals. One successful angle
            # must not suppress the alternatives needed by global selection.
            alternatives=[]
            for tier in range(len(variants)):
                for index,target in enumerate(targets):
                    port,reverse=variants[(tier+index)%len(variants)]
                    alternatives.append((port,reverse,target))
        else:alternatives=[(port,reverse,request['destination']) for port,reverse in variants]
        for port,reverse,target in alternatives:
            variant=dict(request,source=port,attempt_number=pass_index,search_from_destination=reverse,
                destination=target,oriented_docking=bool(reverse and port.get('direction') and target.get('direction')))
            variant.pop('destination_alternatives',None)
            job={'operation':'plan','route_id':rid,'request':variant,'scenario':scenario,'refine':True,
                'diversity':[r['points_mm'] for r in pools.get(rid,[]) if source_id(r,request)==port['id']]}
            group.append((job,(rid,port,reverse)))
        groups.append(group)
    # Round-robin by route: variants of one terminal cannot occupy the whole
    # first wave while another terminal waits behind all of its siblings.
    for tier in range(max(map(len,groups),default=0)):
        for group in groups:
            if tier<len(group):
                job,meta=group[tier];jobs.append(job);metadata.append(meta)
    replies=workers.run(fixed,configuration,jobs,deadline,cancelled)
    if replies is None:return None
    for (rid,port,reverse),reply in zip(metadata,replies):
        if reply is None:
            attempts.append({'route_id':rid,'source_id':port['id'],'reason':'PARALLEL_RESULT_UNAVAILABLE'});continue
        attempts.append({'route_id':rid,'source_id':port['id'],'direction':'REVERSE' if reverse else 'FORWARD',
            'status':reply.get('status'),'reason':reply.get('reason'),'search_attempts':reply.get('attempts',[])})
        if not c.usable(reply):continue
        reply['chosen_source']=copy.deepcopy(port);pool=pools.setdefault(rid,[])
        # Diversity selection is applied jointly after collection.
        if not any(source_id(old,{'source':port})==port['id'] and old['points_mm']==reply['points_mm'] for old in pool):pool.append(reply)
    return attempts


def select_candidates(space,requests,pools,configuration,incumbent,protected,deadline,cancelled=None):
    selection_started=time.monotonic()
    by_id={r['route_id']:r for r in requests};protected=set(protected)
    radii={rid:make_profile(for_route(configuration,rid)).radius_mm for rid in by_id}
    fixed={rid:copy.deepcopy(route) for rid,route in incumbent.items() if rid in protected}
    fixed_ports={source_id(route,by_id[rid]) for rid,route in fixed.items() if c.usable(route)}
    fixed_space=space.clone()
    for rid,route in fixed.items():
        if c.usable(route):fixed_space.reserve(rid,c.points(route),radii[rid])
    admitted={};validation_count=0
    from ..parallel import current
    workers=current();validation_jobs=[];validation_keys=[]
    for rid,options in pools.items():
        if rid not in protected:
            for index,candidate in enumerate(options):
                if c.usable(candidate):
                    validation_keys.append((rid,index));validation_jobs.append({'operation':'validate','route_id':rid,'route':candidate})
    replies=workers.run(fixed_space,configuration,validation_jobs,
        time.monotonic()+max(0,deadline-time.monotonic())*.5,cancelled) if workers else None
    validations=dict(zip(validation_keys,replies)) if replies is not None else {}
    for rid,options in pools.items():
        if rid in protected:continue
        admitted[rid]=[]
        for index,candidate in enumerate(options):
            if (cancelled and cancelled()) or time.monotonic()>=deadline:break
            audit('SELECTOR_CONSIDERED',rid,candidate)
            if not c.usable(candidate) or rejected_path(candidate,rid,configuration) or source_id(candidate,by_id[rid]) in fixed_ports:
                audit('SELECTOR_REJECTED',rid,candidate,reason='UNUSABLE' if not c.usable(candidate) else 'REJECTED_PATH' if rejected_path(candidate,rid,configuration) else 'PROTECTED_PORT');continue
            selected=candidate.get('chosen_source') or by_id[rid]['source']
            allowed=[by_id[rid]['source']]+by_id[rid].get('source_alternatives',[])
            if selected not in allowed:
                audit('SELECTOR_REJECTED',rid,candidate,reason='SOURCE_OUTSIDE_ALLOWED_PORTS');continue
            from . import geometry as g
            from .terminal_adapter import destination,allowed_destination
            target=destination(candidate,by_id[rid])
            if not allowed_destination(target,by_id[rid]) or g.distance(candidate['points_mm'][0],selected['xyz_mm'])>.01 or g.distance(candidate['points_mm'][-1],target['xyz_mm'])>.01:
                audit('SELECTOR_REJECTED',rid,candidate,reason='ENDPOINT_GEOMETRY_MISMATCH');continue
            checked=validations.get((rid,index))
            if checked is None:checked=validate_route_geometry(fixed_space,candidate,make_profile(for_route(configuration,rid)))
            validation_count+=1
            audit('INDIVIDUALLY_VALIDATED',rid,candidate,status=checked['status'],errors=checked.get('errors',[]))
            if checked['status'] not in ('INVALID','NOT_EVALUATED'):
                route=copy.deepcopy(candidate);route.update(checked);admitted[rid].append(route)
            else:audit('SELECTOR_REJECTED',rid,candidate,reason='INDIVIDUAL_VALIDATION',detail=checked)
    validation_seconds=time.monotonic()-selection_started
    order=sorted((rid for rid in by_id if rid not in protected),key=lambda rid:(len(admitted.get(rid,[])),c.priority(space,by_id[rid],configuration)))
    from ..pressure import assess
    incumbent=copy.deepcopy(incumbent)
    for rid,route in incumbent.items():
        if c.usable(route):
            route['section']=for_route(configuration,rid)['section']
            route['pressure_assessment']=assess(route,by_id[rid],configuration)
    for rid,choices in admitted.items():
        for route in choices:
            route['section']=for_route(configuration,rid)['section']
            route['pressure_assessment']=assess(route,by_id[rid],configuration)
    all_routes=[r for choices in admitted.values() for r in choices]+[r for r in incumbent.values() if c.usable(r)]
    pressure_ready=bool(all_routes) and all(r.get('pressure_assessment',{}).get('status')=='ESTIMATED_FROM_REFERENCED_DATA' for r in all_routes)
    for route in all_routes:route['pressure_objective_enabled']=pressure_ready
    conflict_cache={}
    # Pairwise exclusions are independent; the integer master stays central.
    # Batch several pairs per task to amortize process/serialization overhead.
    if workers and workers.enabled and not workers.disabled:
        pairs=[]
        keys=[(rid,i) for rid,choices in admitted.items() for i in range(len(choices))]
        for position,(rid,i) in enumerate(keys):
            for other,j in keys[position+1:]:
                if rid==other:continue
                a,b=admitted[rid][i],admitted[other][j]
                pairs.append((rid,i,other,j,a,b,radii[rid],radii[other],
                              source_id(a,by_id[rid])==source_id(b,by_id[other])))
        if len(pairs)>=16:
            import math
            size=max(8,math.ceil(len(pairs)/workers.report['max_workers']))
            jobs=[{'operation':'conflicts','route_id':keys[0][0],'pairs':pairs[i:i+size]} for i in range(0,len(pairs),size)]
            replies=workers.run(fixed_space,configuration,jobs,
                time.monotonic()+max(0,deadline-time.monotonic())*.3,cancelled)
            for reply in replies or []:
                if isinstance(reply,list):
                    for rid,i,other,j,value in reply:conflict_cache[tuple(sorted(((rid,i),(other,j))))]=value
    def conflict(rid,i,other,j):
        key=tuple(sorted(((rid,i),(other,j))))
        if key not in conflict_cache:
            a,b=admitted[rid][i],admitted[other][j]
            conflict_cache[key]=(source_id(a,by_id[rid])==source_id(b,by_id[other])
                                 or c.overlaps(a,radii[rid],b,radii[other]))
        return conflict_cache[key]
    best=copy.deepcopy(incumbent);best_score=c.score(best,by_id);visited=0;chosen={}
    maximum=configuration['coordination']['max_assignment_nodes'];stopped=False
    fixed_missing=sum(not c.usable(fixed.get(rid,{})) for rid in protected if rid in by_id)
    fixed_conditional=sum(r.get('status')=='CONDITIONAL' for r in fixed.values())
    fixed_cost=sum(r.get('cost',{}).get('total',0) for r in fixed.values())
    def visit(index,missing,conditional,cost):
        nonlocal best,best_score,visited,stopped
        visited+=1
        if visited>maximum or (cancelled and cancelled()) or time.monotonic()>=deadline:stopped=True;return
        remaining=[admitted.get(rid,[]) for rid in order[index:]]
        bound=(missing+fixed_missing+sum(not values for values in remaining),
               conditional+fixed_conditional+sum(min(r['status']=='CONDITIONAL' for r in values) for values in remaining if values),
               0,
               cost+fixed_cost+sum(min(r.get('cost',{}).get('total',0) for r in values) for values in remaining if values))
        if bound>=best_score:return
        if index==len(order):
            result=copy.deepcopy(incumbent)
            for rid in order:
                if rid in chosen:result[rid]=admitted[rid][chosen[rid]]
                elif c.usable(result.get(rid,{})):result[rid]={'status':'NOT_EVALUATED','reason':'NO_COMPATIBLE_ROUTE_COMBINATION'}
            trial_score=c.score(result,by_id)
            if trial_score<best_score:best,best_score=copy.deepcopy(result),trial_score
            return
        rid=order[index]
        options=sorted(range(len(admitted.get(rid,[]))),key=lambda i:(admitted[rid][i]['status']=='CONDITIONAL',admitted[rid][i].get('cost',{}).get('total',0)))
        for i in options:
            if any(conflict(rid,i,other,j) for other,j in chosen.items()):continue
            chosen[rid]=i;route=admitted[rid][i]
            visit(index+1,missing,conditional+(route['status']=='CONDITIONAL'),cost+route.get('cost',{}).get('total',0))
            chosen.pop(rid)
            if stopped:return
        visit(index+1,missing+1,conditional,cost)
    from .integer_master import solve
    solver_started=time.monotonic()
    selected,solver_report=solve(admitted,order,conflict,deadline,cancelled,
        known_conflicts=[key for key,value in conflict_cache.items() if value])
    if selected is not None:
        trial=copy.deepcopy(incumbent)
        for rid in order:
            if rid in selected:trial[rid]=admitted[rid][selected[rid]]
            elif c.usable(trial.get(rid,{})):trial[rid]={'status':'NOT_EVALUATED','reason':'NO_COMPATIBLE_ROUTE_COMBINATION'}
        if c.score(trial,by_id)<best_score:best,best_score=trial,c.score(trial,by_id)
    else:visit(0,0,0,0)
    for rid,choices in admitted.items():
        for item in choices:
            if item.get('points_mm')!=best.get(rid,{}).get('points_mm') or source_id(item,by_id[rid])!=source_id(best.get(rid,{}),by_id[rid]):
                blockers=[other for other,r in best.items() if other!=rid and c.usable(r) and
                    (source_id(item,by_id[rid])==source_id(r,by_id[other]) or c.overlaps(item,radii[rid],r,radii[other]))]
                audit('SELECTOR_REJECTED',rid,item,reason='DEMONSTRATED_CONFLICT' if blockers else 'SOLVER_LIMIT_OR_SECONDARY_OBJECTIVE',blockers=blockers)
    return best,{'phase':'GLOBAL_CANDIDATE_SELECTION','candidate_count':sum(map(len,admitted.values())),
                 'assignment_nodes':visited,'limit_reached':stopped,'validated_candidates':validation_count,
                 'before':list(c.score(incumbent,by_id)),'after':list(best_score),
                 'accepted':best_score<c.score(incumbent,by_id),'solver':solver_report,
                 'seconds':time.monotonic()-selection_started,'validation_seconds':validation_seconds,
                 'solver_and_decisions_seconds':time.monotonic()-solver_started}


def coordinate(space,routes,requests,configuration,protected,planner,deadline,cancelled=None,candidate_bank=None,pass_index=0):
    protected=set(protected);by_id={r['route_id']:r for r in requests};options=configuration['coordination']
    fixed=space.clone();pools={};attempts=[];candidate_bank=candidate_bank if candidate_bank is not None else {}
    fixed_ports=set()
    for rid,route in routes.items():
        if rid in protected and c.usable(route):
            fixed.reserve(rid,c.points(route),make_profile(for_route(configuration,rid)).radius_mm)
            fixed_ports.add(source_id(route,by_id[rid]))
        if c.usable(route):pools[rid]=[copy.deepcopy(route)]
    for rid,stored in candidate_bank.items():
        if rid not in by_id:continue
        pool=pools.setdefault(rid,[])
        for route in stored:
            if time.monotonic()>=deadline:break
            if not any(source_id(old,by_id[rid])==source_id(route,by_id[rid]) and old['points_mm']==route['points_mm'] for old in pool):
                pool.append(copy.deepcopy(route))
    pending=sorted((r for r in requests if r['route_id'] not in protected),
                   key=lambda r:(c.usable(routes.get(r['route_id'],{})),c.priority(space,r,configuration)))
    # A useful corridor can outlive its initially assigned manifold port.
    # Check a local prefix repair before rediscovering the entire path.
    from .source_repair import reassign
    for request in pending:
        rid=request['route_id'];pool=pools.setdefault(rid,[]);originals=list(pool)
        local_config=for_route(configuration,rid);local_profile=make_profile(local_config)
        for port in {p['id']:p for p in [request['source']]+request.get('source_alternatives',[])}.values():
            if time.monotonic()>=deadline or (cancelled and cancelled()):break
            if port['id'] in fixed_ports or any(source_id(r,request)==port['id'] for r in pool):continue
            for old in originals:
                candidate=reassign(fixed,old,port,request,local_profile,local_config)
                if candidate:
                    pool.append(candidate);attempts.append({'route_id':rid,'source_id':port['id'],
                        'method':'VALIDATED_SOURCE_PREFIX_REPAIR','status':candidate['status']});break
    # Leave a quarter of this phase for the combinatorial selection itself.
    generation_deadline=time.monotonic()+max(0,deadline-time.monotonic())*.75
    from ..parallel import current
    workers=current()
    # Custom injected planners retain their serial test/extension contract.
    parallel_attempts=(parallel_candidates(workers,fixed,pending,pools,configuration,fixed_ports,generation_deadline,cancelled,
        pass_index,{r['route_id'] for r in pending if not c.usable(routes.get(r['route_id'],{}))})
        if workers and planner.__module__=='ARQT_MEP.routing.core.multi_route' else None)
    if parallel_attempts is not None:attempts.extend(parallel_attempts)
    for position,request in enumerate(pending if parallel_attempts is None else []):
        if (cancelled and cancelled()) or time.monotonic()>=generation_deadline:break
        rid=request['route_id'];pool=pools.setdefault(rid,[])
        route_deadline=time.monotonic()+(generation_deadline-time.monotonic())/max(1,len(pending)-position)
        original=request['source'];alternatives=[original]+list(request.get('source_alternatives',[]))
        unique={p['id']:p for p in alternatives if p['id'] not in fixed_ports}
        # Eliminate a geometrically blocked bulk start before limiting the
        # number of expensive route variants. Otherwise both slots can be
        # wasted on one impossible port and a free alternate is never tried.
        from .multi_route import compact_anchor,_stub
        local_config=for_route(configuration,rid);local_profile=make_profile(local_config)
        viable=[]
        for port in unique.values():
            if time.monotonic()>=route_deadline or (cancelled and cancelled()):break
            anchor=(compact_anchor(fixed,port,local_profile,local_config) if request.get('compact_connections') else
                    _stub(port,local_config['connection_straight_mm']))
            if anchor is not None and fixed.free(anchor[1],anchor[1],local_profile.radius_mm):viable.append(port)
            else:attempts.append({'route_id':rid,'source_id':port['id'],'status':'NOT_EVALUATED',
                                 'reason':'NO_FREE_BULK_START','phase':'PORT_ADMISSION'})
        # Keep the initially selected port, then nearest interchangeable ports.
        ports=sorted(viable,key=lambda p:(p['id']!=original['id'],sum((a-b)**2 for a,b in zip(p['xyz_mm'],request['destination']['xyz_mm']))))
        # Give each admitted port one opportunity before spending a second
        # slot on the opposite direction of the same port. This also works
        # when configuration permits only a single automatic pass.
        if ports:
            offset=pass_index%len(ports);ports=ports[offset:]+ports[:offset]
        reverse=bool(pass_index%2)
        scenario='B' if (pass_index%2 or configuration['search']['max_iterations']==1) and configuration.get('structural_crossings')=='PROPOSAL_ONLY' else 'A'
        variants=[(port,reverse,scenario) for port in ports]+[(port,not reverse,scenario) for port in ports]
        variants=variants[:max(2,len(ports))]
        for trial,(port,reverse,scenario) in enumerate(variants):
            if time.monotonic()>=route_deadline or (cancelled and cancelled()):break
            share=(route_deadline-time.monotonic())/max(1,min(len(variants)-trial,options['max_candidates']-len(pool)))
            variant=dict(request,source=port,attempt_number=pass_index,search_from_destination=reverse,
                         oriented_docking=bool(reverse and port.get('direction') and request['destination'].get('direction')))
            candidate=planner(fixed,variant,make_profile(for_route(configuration,rid)),configuration,scenario=scenario,
                cancelled=cancelled,diversity=[r['points_mm'] for r in pool if source_id(r,request)==port['id']],
                deadline=time.monotonic()+share)
            attempts.append({'route_id':rid,'source_id':port['id'],'direction':'REVERSE' if reverse else 'FORWARD',
                             'scenario':scenario,'status':candidate.get('status'),'reason':candidate.get('reason'),
                             'search_attempts':candidate.get('attempts',[])})
            if not c.usable(candidate):continue
            from .refinement import refine
            candidate=refine(fixed,candidate,make_profile(for_route(configuration,rid)),
                min(route_deadline,time.monotonic()+max(0,route_deadline-time.monotonic())*.15),cancelled)
            candidate['chosen_source']=copy.deepcopy(port)
            same=any(source_id(old,request)==port['id'] and old['points_mm']==candidate['points_mm'] for old in pool)
            if not same:pool.append(candidate)
    # Do not let the first integer solve consume the neighborhood-repair
    # budget. It may keep an incumbent while lazy cuts remain unresolved.
    selection_deadline=time.monotonic()+max(0,deadline-time.monotonic())*.6
    result,report=select_candidates(space,requests,pools,configuration,routes,protected,selection_deadline,cancelled)
    repairs=[]
    for request in pending:
        if c.usable(result.get(request['route_id'],{})) or time.monotonic()>=deadline or (cancelled and cancelled()):continue
        rid=request['route_id'];local=make_profile(for_route(configuration,rid))
        probes=pools.get(rid,[])
        neighbors={key for key,route in result.items() if key not in protected and c.usable(route)
            and any(c.overlaps(probe,local.radius_mm,route,make_profile(for_route(configuration,key)).radius_mm) for probe in probes)}
        if not neighbors:
            neighbors=set(sorted((key for key,r in result.items() if key not in protected and c.usable(r)),
                key=lambda key:sum((a-b)**2 for a,b in zip(by_id[key]['source']['xyz_mm'],request['source']['xyz_mm'])))[:2])
        from .route_conflict_graph import RouteConflictGraph
        graph=RouteConflictGraph()
        for key,value in result.items():
            if c.usable(value):graph.add(key,value,make_profile(for_route(configuration,key)).radius_mm,source_id(value,by_id[key]))
        for index,probe in enumerate(probes):graph.add(rid+'#'+str(index),probe,local.radius_mm,source_id(probe,request))
        report.setdefault('conflict_graphs',{})[rid]=graph.export_state()
        neighbors.update(key for key in graph.neighbourhood([rid+'#'+str(i) for i in range(len(probes))]) if key in by_id and key not in protected)
        group=neighbors|{rid};working=space.clone()
        for key,route in result.items():
            if key not in group and c.usable(route):working.reserve(key,c.points(route),make_profile(for_route(configuration,key)).radius_mm)
        share=max(0,deadline-time.monotonic())*.45
        variant=dict(request,search_from_destination=True)
        candidate=planner(working,variant,local,configuration,cancelled=cancelled,
            diversity=[r['points_mm'] for r in probes],deadline=time.monotonic()+share)
        if c.usable(candidate):
            candidate['chosen_source']=copy.deepcopy(request['source']);pools.setdefault(rid,[]).append(candidate)
            result,selection=select_candidates(space,requests,pools,configuration,result,protected,deadline,cancelled)
            repairs.append({'group':sorted(group),'accepted':selection['accepted']})
    report['neighborhood_repairs']=repairs
    for rid,pool in pools.items():candidate_bank[rid]=copy.deepcopy(pool)
    report['attempts']=attempts
    from .candidate_repository import CandidateRepository,context_signature
    repository=CandidateRepository(context_signature(space,requests,configuration))
    for key,items in candidate_bank.items():
        for item in items:repository.publish(key,item)
    for key,item in result.items():
        if c.usable(item):repository.publish(key,item,protect=True)
    candidate_bank.clear();candidate_bank.update(repository.bank())
    report['repository']=repository.stats
    report['changed_ports']=[rid for rid,route in result.items() if c.usable(route)
                             and source_id(route,by_id[rid])!=by_id[rid]['source']['id']]
    return result,report
