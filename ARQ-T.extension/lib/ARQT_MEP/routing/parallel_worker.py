"""Private line-protocol worker executable. Imports no Revit modules."""
import json
import os
from pathlib import Path
import sys
import time
import traceback

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))


def main():
    from ARQT_MEP.routing.core.spatial_model import build_spatial_model
    from ARQT_MEP.routing.core.multi_route import plan_route
    from ARQT_MEP.routing.core.route_geometry import validate_route_geometry
    from ARQT_MEP.routing.core.refinement import refine
    from ARQT_MEP.routing.settings import for_route,profile
    data=json.loads(Path(sys.argv[1]).read_text(encoding='utf8'));last=None;geometry_key=None
    for line in sys.stdin:
        received=time.monotonic();task=json.loads(line);output=Path(task['output']);search_started=None
        stop_path=Path(task['cancel_path']) if task.get('cancel_path') else None
        cancelled=lambda:bool(stop_path and stop_path.exists())
        try:
            if last!=task['context']:
                context=json.loads(Path(task['context']).read_text(encoding='utf8'))
                config=context['configuration'];key=json.dumps(config['constraints'],sort_keys=True)
                if key!=geometry_key:
                    base=build_spatial_model(data,config['constraints']);geometry_key=key
                from ARQT_MEP.routing.core.incremental_roadmap import restore_maps
                restore_maps(base,context.get('exploration_state'))
                base.iris_cells=context.get('iris_cells',{})
                base.reservations=context['reservations'];last=task['context']
            space=base.clone();rid=task['route_id'];p=profile(for_route(config,rid))
            search_started=time.monotonic()
            budget=task.get('budget_seconds',float('inf'))
            headroom=min(.25,max(.05,budget*.02))
            deadline=min(task['deadline']-headroom,search_started+budget)
            if cancelled():result={'status':'NOT_EVALUATED','reason':'CANCELLED'}
            elif time.monotonic()>=deadline:result={'status':'NOT_EVALUATED','reason':'PARALLEL_QUEUE_TIME_LIMIT'}
            elif task['operation']=='plan':
                result=plan_route(space,task['request'],p,config,scenario=task.get('scenario','A'),
                    diversity=task.get('diversity',()),deadline=deadline,cancelled=cancelled)
                if task.get('refine') and result.get('status') not in ('INVALID','NOT_EVALUATED'):
                    result=refine(space,result,p,min(deadline,time.monotonic()+.2),cancelled)
            elif task['operation']=='validate':result=validate_route_geometry(space,task['route'],p)
            elif task['operation']=='conflicts':
                from ARQT_MEP.routing.core.cooperative import overlaps
                result=[[rid,i,other,j,bool(same_port or overlaps(a,ra,b,rb))]
                    for rid,i,other,j,a,b,ra,rb,same_port in task['pairs'] if time.monotonic()<deadline and not cancelled()]
            else:raise ValueError('Operación de trabajador desconocida.')
            reply={'result':result,'worker_pid':os.getpid()}
            if task['operation']=='plan':
                from ARQT_MEP.routing.core.incremental_roadmap import export_maps
                reply['exploration_state']=export_maps(base)
                reply['iris_cells']=space.iris_cells
        except Exception:reply={'error':traceback.format_exc(limit=6),'worker_pid':os.getpid()}
        reply['job_id']=task.get('job_id')
        ended=time.monotonic()
        reply['timing']={'queue_or_startup_seconds':received-task.get('dispatched_at',received),
            'setup_seconds':(search_started or ended)-received,
            'compute_seconds':ended-(search_started or ended),
            'allocated_compute_seconds':task.get('budget_seconds')}
        temporary=output.with_suffix('.tmp')
        temporary.write_text(json.dumps(reply,allow_nan=False),encoding='utf8');os.replace(str(temporary),str(output))


if __name__=='__main__':main()
