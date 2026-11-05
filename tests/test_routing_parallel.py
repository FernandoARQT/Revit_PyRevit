"""External-worker integration contracts, to run in the validation phase."""
import copy
import json
import time
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock,patch
from test_routing_core import configuration,profile,request,scene
from ARQT_MEP.routing.parallel import Session,capacity,current
from ARQT_MEP.routing.core.spatial_model import build_spatial_model
from ARQT_MEP.routing.core.route_geometry import validate_route_geometry


class ParallelRouting(unittest.TestCase):
    def test_late_geometric_reply_preserves_endpoints_without_inventing_network(self):
        config=configuration();space=build_spatial_model(scene())
        req=request();req.pop('network_type')
        with tempfile.TemporaryDirectory(prefix='arqt-test-reply-') as folder:
            workers=Session(space,config);workers.context_identity='current'
            workers.workers=[(Mock(),Mock())]
            path=Path(folder)/'reply.json'
            result={'status':'VALID','points_mm':[req['source']['xyz_mm'],req['destination']['xyz_mm']]}
            for context in ('obsolete','current'):
                path.write_text(json.dumps({'job_id':'job','result':result}),encoding='utf8')
                workers.inflight={0:{'path':path,'batch':1,'index':0,'job_id':'job',
                    'context_identity':context,'request':req}}
                workers._collect([],2,{})
                if context=='obsolete':self.assertEqual(workers.late_candidates,{})
            candidate=workers.late_candidates[req['route_id']][0]
            self.assertEqual(candidate['chosen_source'],req['source'])
            self.assertEqual(candidate['destination'],req['destination'])
            self.assertNotIn('network_type',candidate)
            self.assertEqual(workers.inflight,{})

    def test_reply_with_wrong_job_identity_is_rejected(self):
        config=configuration();space=build_spatial_model(scene())
        with tempfile.TemporaryDirectory(prefix='arqt-test-reply-') as folder:
            workers=Session(space,config);workers.workers=[(Mock(),Mock())]
            path=Path(folder)/'reply.json'
            path.write_text(json.dumps({'job_id':'wrong','result':{'status':'VALID'}}),encoding='utf8')
            workers.inflight={0:{'path':path,'batch':1,'index':0,'job_id':'expected'}}
            results=[None]
            with self.assertRaisesRegex(ValueError,'identity mismatch'):
                workers._collect(results,1,{'completed':0})
            self.assertEqual(results,[None])
            self.assertEqual(workers.late_candidates,{})

    def test_candidate_jobs_are_interleaved_by_terminal_and_ports_rotate(self):
        from ARQT_MEP.routing.core.portfolio import parallel_candidates
        from unittest.mock import Mock
        config=configuration();space=build_spatial_model(scene())
        requests=[request(rid) for rid in ('a','b')]
        for req in requests:
            req['source_alternatives']=[dict(req['source'],id='alternate-'+req['route_id'],xyz_mm=(200,1200,250))]
        workers=Mock();workers.run.return_value=None
        parallel_candidates(workers,space,requests,{},config,set(),time.monotonic()+3,None,
                            pass_index=1,unresolved_ids={'a','b'})
        jobs=workers.run.call_args.args[2]
        self.assertEqual([j['route_id'] for j in jobs],['a','b','a','b'])
        self.assertEqual([j['request']['source']['id'] for j in jobs],['alternate-a','alternate-b','sourcea','sourceb'])
        self.assertEqual([j['request']['search_from_destination'] for j in jobs],[True]*4)

    def test_workers_and_geometry_are_reused_between_successful_batches(self):
        config=configuration();config['parallel']['enabled']=True;space=build_spatial_model(scene())
        jobs=[{'operation':'plan','route_id':str(i),'request':request(str(i))} for i in range(2)]
        with Session(space,config) as workers:
            first=workers.run(space,config,jobs,time.monotonic()+20)
            pids=[p.pid for p,_ in workers.workers];started=workers.report['started_workers']
            second=workers.run(space,config,jobs,time.monotonic()+20)
            self.assertTrue(all(first) and all(second),workers.report)
            self.assertEqual(pids,[p.pid for p,_ in workers.workers])
            self.assertEqual(started,workers.report['started_workers'])

    def test_timeout_keeps_workers_and_late_reply_cannot_replace_new_batch_result(self):
        # Transport-only fixture: cancellation is deliberately slower than
        # result collection so the next batch must drain old replies safely.
        script='''import json,os,sys,time
from pathlib import Path
for line in sys.stdin:
 t=json.loads(line)
 if t['operation']=='block':
  while not Path(t['cancel_path']).exists():time.sleep(.01)
  time.sleep(.08)
 r={'result':{'marker':t['marker'],'budget':t['budget_seconds']},'worker_pid':os.getpid(),'job_id':t['job_id']}
 p=Path(t['output']);tmp=p.with_suffix('.tmp')
 tmp.write_text(json.dumps(r),encoding='utf8');os.replace(tmp,p)
'''
        config=configuration();config['parallel']['enabled']=True;space=build_spatial_model(scene())
        with tempfile.TemporaryDirectory(prefix='arqt-test-worker-') as folder:
            path=Path(folder)/'worker.py';path.write_text(script,encoding='utf8')
            with patch('ARQT_MEP.routing.parallel.capacity',return_value=(4,2)),Session(space,config) as workers:
                workers.worker_script=path
                old=[{'operation':'block','marker':'old'} for _ in range(2)]
                self.assertEqual(workers.run(space,config,old,time.monotonic()+.7),[None,None])
                pids=[p.pid for p,_ in workers.workers]
                jobs=[{'operation':'echo','marker':i} for i in range(6)]
                replies=workers.run(space,config,jobs,time.monotonic()+6)
                self.assertEqual([r['marker'] for r in replies],list(range(6)))
                self.assertEqual(pids,[p.pid for p,_ in workers.workers])
                self.assertEqual(workers.report['started_workers'],2)
                self.assertEqual(workers.report['batches'][-1]['stale_results_reaped'],2)
                self.assertGreaterEqual(replies[-1]['budget'],replies[0]['budget'])

    def test_uses_available_cpus_minus_two_with_single_worker_floor(self):
        available,workers=capacity()
        self.assertGreaterEqual(available,1)
        self.assertEqual(workers,max(1,available-2))

    def test_disabled_and_cancelled_sessions_start_no_processes(self):
        config=configuration();space=build_spatial_model(scene())
        with Session(space,config) as workers:
            self.assertIs(current(),workers)
            with patch.object(workers,'_start') as start:
                self.assertIsNone(workers.run(space,config,[{},{}],time.monotonic()+10))
                start.assert_not_called()
        self.assertIsNone(current())
        config['parallel']['enabled']=True
        with Session(space,config) as workers:
            with patch.object(workers,'_start') as start:
                self.assertIsNone(workers.run(space,config,[{},{}],time.monotonic()+10,lambda:True))
                start.assert_not_called()

    def test_external_routes_are_valid_and_processes_and_temporary_files_are_cleaned(self):
        config=configuration();config['parallel']['enabled']=True
        data=scene();before=copy.deepcopy(data);space=build_spatial_model(data)
        jobs=[{'operation':'plan','route_id':str(i),'request':request(str(i),
            (200,500+i*500,250),(2800,500+i*500,250))} for i in range(2)]
        with Session(space,config) as workers:
            replies=workers.run(space,config,jobs,time.monotonic()+20)
            self.assertTrue(all(r is not None for r in replies),workers.report)
            for reply,job in zip(replies,jobs):
                self.assertEqual(validate_route_geometry(space,reply,profile(config))['status'],'VALID_WITH_WARNINGS')
                self.assertEqual(tuple(reply['points_mm'][0]),job['request']['source']['xyz_mm'])
            self.assertEqual(len(workers.report['batches'][0]['worker_pids']),min(2,capacity()[1]))
            processes=[p for p,_ in workers.workers];directory=workers.directory
        self.assertTrue(all(p.poll() is not None for p in processes))
        self.assertFalse(directory.exists());self.assertEqual(data,before)

    def test_worker_error_is_not_accepted_as_geometry(self):
        config=configuration();config['parallel']['enabled']=True;space=build_spatial_model(scene())
        jobs=[{'operation':'invalid','route_id':str(i)} for i in range(2)]
        with Session(space,config) as workers:
            replies=workers.run(space,config,jobs,time.monotonic()+20)
            self.assertEqual(replies,[None,None])
            self.assertEqual(len(workers.report['batches'][0]['errors']),2)


if __name__=='__main__':unittest.main()
