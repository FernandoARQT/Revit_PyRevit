"""External CPython workers; no Revit objects, threads or model access."""
import contextvars
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

_CURRENT=contextvars.ContextVar('arqt_routing_workers',default=None)


def current():return _CURRENT.get()


def capacity():
    available=os.cpu_count() or 1
    if hasattr(os,'sched_getaffinity'):available=len(os.sched_getaffinity(0))
    elif os.name=='nt':
        try:
            import ctypes
            process=ctypes.c_size_t();system=ctypes.c_size_t()
            kernel=ctypes.windll.kernel32
            kernel.GetCurrentProcess.restype=ctypes.c_void_p
            kernel.GetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_size_t),ctypes.POINTER(ctypes.c_size_t)]
            if kernel.GetProcessAffinityMask(kernel.GetCurrentProcess(),ctypes.byref(process),ctypes.byref(system)):
                available=int(process.value).bit_count()
        except (AttributeError,OSError):pass
    return available,max(1,available-2)


def executable():
    candidates=[os.environ.get('ARQT_ROUTING_PYTHON'),sys.executable,
        os.path.join(os.environ.get('LOCALAPPDATA',''),'Programs','Python','Python312','python.exe'),
        shutil.which('python3.12'),shutil.which('python')]
    for path in candidates:
        if path and Path(path).is_file() and Path(path).name.lower().startswith('python'):return str(path)
    raise RuntimeError('No se encuentra CPython externo. Configure ARQT_ROUTING_PYTHON.')


class Session:
    def __init__(self,space,configuration,exploration_state=None):
        available,workers=capacity()
        self.report={'available_logical_cpus':available,'reserved_cpus':min(2,max(0,available-1)),
                     'max_workers':workers,'started_workers':0,'batches':[]}
        self.enabled=configuration.get('parallel',{}).get('enabled',True)
        self.space=space;self.workers=[];self.temp=None;self.counter=0;self.disabled=False
        if not hasattr(space,'iris_cells'):space.iris_cells={}
        if exploration_state:
            from .core.incremental_roadmap import restore_maps
            restore_maps(space,exploration_state)
        self.inflight={};self.late_candidates={};self.context_identity=None
        self.worker_script=Path(__file__).with_name('parallel_worker.py')

    def __enter__(self):self.token=_CURRENT.set(self);return self

    def __exit__(self,*args):
        try:self.close()
        finally:_CURRENT.reset(self.token)

    def close(self):
        for process,log in self.workers:
            if process.poll() is None:process.terminate()
        for process,log in self.workers:
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            process.stdin.close();log.close()
        self.workers=[]
        self.inflight.clear()
        if self.temp:
            try:self.temp.cleanup()
            except OSError:pass
            self.temp=None

    def _start(self,count):
        if self.temp is None:
            self.temp=tempfile.TemporaryDirectory(prefix='arqt-routing-')
            self.directory=Path(self.temp.name)
            (self.directory/'scene.json').write_text(json.dumps(self.space.data,allow_nan=False),encoding='utf8')
            self.python=executable()
        while len(self.workers)<count:self.workers.append(self._spawn(len(self.workers)))

    def _spawn(self,slot):
        env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',
                 NUMEXPR_NUM_THREADS='1',VECLIB_MAXIMUM_THREADS='1',PYTHONUTF8='1')
        log=open(self.directory/('worker-%d.log'%slot),'w',encoding='utf8')
        try:
            process=subprocess.Popen([self.python,'-u',str(self.worker_script),
                str(self.directory/'scene.json'),str(self.directory)],stdin=subprocess.PIPE,
                stdout=log,stderr=log,text=True,encoding='utf8',env=env,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except Exception:log.close();raise
        self.report['started_workers']+=1
        return process,log

    def _replace(self,slot):
        process,log=self.workers[slot]
        if process.poll() is None:process.terminate()
        try:process.wait(timeout=.5)
        except subprocess.TimeoutExpired:process.kill();process.wait()
        try:process.stdin.close()
        except OSError:pass
        log.close();self.inflight.pop(slot,None)
        self.workers[slot]=self._spawn(slot)

    def _collect(self,results,batch,event):
        for slot,task in list(self.inflight.items()):
            path=task['path'];process,_=self.workers[slot]
            if path.exists():
                reply=json.loads(path.read_text(encoding='utf8'));path.unlink()
                if not isinstance(reply,dict) or not ('result' in reply or 'error' in reply):
                    raise ValueError('Respuesta de trabajador incompleta.')
                if reply.get('job_id')!=task.get('job_id'):
                    raise ValueError('Worker job identity mismatch')
                if task.get('request') and isinstance(reply.get('result'),dict):
                    from .core.candidate_repository import audit
                    audit('DISCOVERED',task['request']['route_id'],reply['result'],job_id=task['job_id'],
                        method=reply['result'].get('search_method'),late=task['batch']!=batch,
                        context_matches=task.get('context_identity')==self.context_identity)
                if task.get('context_identity')==self.context_identity and reply.get('iris_cells'):
                    self.space.iris_cells.update(reply['iris_cells'])
                if task['batch']==batch:
                    if reply.get('exploration_state'):
                        from .core.incremental_roadmap import restore_maps
                        restore_maps(self.space,reply['exploration_state'])
                    pid=reply.get('worker_pid')
                    if pid and pid not in event.setdefault('worker_pids',[]):event['worker_pids'].append(pid)
                    if reply.get('error'):event.setdefault('errors',[]).append(reply['error'])
                    else:results[task['index']]=reply['result'];event['completed']+=1
                    if 'timing' in reply:event.setdefault('task_timing',[]).append(reply['timing'])
                else:
                    event['stale_results_reaped']=event.get('stale_results_reaped',0)+1
                    if task.get('context_identity')==self.context_identity and reply.get('result') and task.get('request'):
                        from .core.cooperative import usable
                        value=reply['result'];request=task['request']
                        if usable(value):
                            value.update(chosen_source=request['source'],source=request['source'],destination=request['destination'],
                                late_result_job_id=task['job_id'])
                            if 'network_type' in request:value['network_type']=request['network_type']
                            self.late_candidates.setdefault(request['route_id'],[]).append(value)
                    if task.get('context_identity')==self.context_identity and reply.get('exploration_state'):
                        from .core.incremental_roadmap import restore_maps
                        restore_maps(self.space,reply['exploration_state'])
                del self.inflight[slot]
            elif process.poll() is not None:
                detail=(self.directory/('worker-%d.log'%slot)).read_text(encoding='utf8',errors='replace')[-3000:]
                event.setdefault('errors',[]).append('El trabajador terminó: '+detail)
                self._replace(slot)
            elif task.get('cancelled_at') and time.monotonic()-task['cancelled_at']>2.:
                # Only an unresponsive worker is replaced. Idle and normally
                # cancelled workers retain their geometry and scientific cache.
                self._replace(slot);event['forced_replacements']=event.get('forced_replacements',0)+1

    def run(self,space,configuration,jobs,deadline,cancelled=None):
        """Ordered results; None means unavailable/unstarted, never valid geometry."""
        if not self.enabled or self.disabled or len(jobs)<2 or deadline-time.monotonic()<.5 or (cancelled and cancelled()):return None
        from .core.candidate_repository import signature
        self.context_identity=signature([getattr(space,'geometry_signature',space.data),
            {k:v for k,v in configuration.items() if k not in ('search','parallel','routing_mode')}])
        started=time.monotonic();results=[None]*len(jobs);next_job=0
        self.counter+=1;batch=self.counter
        count=min(self.report['max_workers'],len(jobs))
        event={'jobs':len(jobs),'workers':count,'completed':0,'operations':sorted({j['operation'] for j in jobs})}
        try:
            self._start(count)
            context=self.directory/('batch-%d.json'%batch)
            from .core.incremental_roadmap import export_maps
            context.write_text(json.dumps({'configuration':configuration,'reservations':space.reservations,
                'exploration_state':export_maps(self.space),'iris_cells':getattr(self.space,'iris_cells',{})},allow_nan=False),encoding='utf8')
            # Each worker receives a substantial slot, not a serial share of
            # the entire job count. Queue waiting still counts against deadline.
            waves=max(1,math.ceil(len(jobs)/count))
            slot_budget=max(.01,(deadline-time.monotonic())/waves)
            event['nominal_task_seconds']=slot_budget
            cancel_path=self.directory/('cancel-%d'%batch)
            while next_job<len(jobs) or any(t['batch']==batch for t in self.inflight.values()):
                # Harvest BEFORE testing the deadline, including responses
                # written on the final polling tick. Old contexts never leak.
                self._collect(results,batch,event)
                if time.monotonic()>=deadline or (cancelled and cancelled()):
                    event['stopped']='CANCELLED' if cancelled and cancelled() else 'TIME_LIMIT'
                    cancel_path.touch()
                    for task in self.inflight.values():
                        if task['batch']==batch:task['cancelled_at']=time.monotonic()
                    if event['stopped']=='CANCELLED':self.close()
                    break
                for slot,(process,log) in enumerate(self.workers[:count]):
                    if slot not in self.inflight and next_job<len(jobs):
                        if process.poll() is not None:
                            self._replace(slot);process,_=self.workers[slot]
                        index=next_job;next_job+=1;path=self.directory/('%d-%d.json'%(batch,index))
                        remaining_waves=max(1,math.ceil((len(jobs)-index)/count))
                        budget=max(slot_budget,(deadline-time.monotonic())/remaining_waves)
                        task=dict(jobs[index],context=str(context),output=str(path),
                                  deadline=deadline,budget_seconds=budget,cancel_path=str(cancel_path),
                                  dispatched_at=time.monotonic())
                        request=task.get('request',{});source=request.get('source',{})
                        task.update(source_port_id=source.get('id'),corridor_signature=signature(request.get('topological_corridors',{})),
                            algorithm=task['operation'],attempt_number=request.get('attempt_number',batch))
                        from .core.search_budget import attempt_seed
                        # Transport jobs (for example a batch of pair checks)
                        # need an identity even when they have no single route.
                        route_id=task.get('route_id',request.get('route_id'))
                        task['seed']=attempt_seed(configuration.get('random_seed',0),route_id,source.get('id'),
                            [c.get('portal_ids') for c in request.get('topological_corridors',{}).get(source.get('id'),[])],task['attempt_number'])
                        task['job_id']=signature([self.context_identity,batch,index,route_id,task['source_port_id'],task['corridor_signature'],task['attempt_number']])
                        process.stdin.write(json.dumps(task,allow_nan=False)+'\n');process.stdin.flush()
                        self.inflight[slot]={'index':index,'path':path,'batch':batch,'job_id':task['job_id'],'context_identity':self.context_identity,'request':task.get('request')}
                if self.inflight:time.sleep(.02)
        except (OSError,ValueError,RuntimeError) as exc:
            event['error']=str(exc);self.disabled=True;self.close()
        finally:
            event['seconds']=time.monotonic()-started;self.report['batches'].append(event)
        return results
