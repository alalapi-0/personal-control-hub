"""Disposable ordinary-task budget integration; no native/model invocation."""
import copy,time,unittest
from types import SimpleNamespace
from unittest import mock
import test_hub_ordinary_worker as ordinary
import test_hub_plan_admission as plans
from hub.dispatch_budget import BudgetAuthority,NormalDispatchBinding
from hub.plan_admission import PlanAuthority
from hub.task_worker import TaskWorker
from hub.task_service import TaskService
from hub.ordinary_runtime import OrdinaryRuntime
from hub.service_contract import ServiceError
from hub.workbench_store import digest

class NormalDispatchBudgetTests(unittest.TestCase):
 def setUp(self):
  self.f=ordinary.OrdinaryWorkerTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
  self.store=self.f.store;self.control={'state':'active','revision':1};self.calls=[]
  self.plan=plans.plan([plans.task(plans.BASE)])
  self.pc=PlanAuthority.configured({'plan_sha256':digest(self.plan)},authorize=lambda:None,controls=lambda:self.control,routes=lambda _:False,package_for=lambda _:None)
  self.store.import_plan(self.plan,authority=self.pc)
  self.policy={'schema_version':'1.0','lineage_id':self.plan['plan_id'],'source_state_sha256':'a'*64,'cutoff_id':'synthetic-cutoff','covered_dispatch_ids':[],'observed_model_dispatches':0,'historical_unknown_allowance':8,'closure_reserve':12,'conservative_deterministic_dispatches':0,'limit':480,'provenance_ref':'synthetic/state.json'}
  self.store.initialize_dispatch_budget(self.plan['plan_id'],self.policy,authority=self.cap('baseline',self.policy))
  self.req={'dispatch_id':'normal-call','task_id':self.f.spec['request_id'],'attempt_id':'normal-attempt','package_sha256':None,'executor':{'kind':'model','thread_id':None,'turn_id':None,'session_id':None},'purpose':'normal','effect':'read_only','gpu':False,'seconds':self.f.grant['max_turn_seconds'],'deadline':time.time()+self.f.grant['max_turn_seconds']+3}
  self.binding=NormalDispatchBinding.configured(lineage_id=self.plan['plan_id'],request_value=self.req,grant=self.f.grant,authority_value=self.cap('reserve',self.req))
  self.bindings={self.f.spec['request_id']:self.binding};test=self
  class Adapter(self.f.Adapter):
   def open(self):
    test.calls.append('open');test.assertIn(test.req['dispatch_id'],test.status()['held_dispatch_ids'])
    test.assertTrue(any(e['key']=='normal-dispatch-reserved'for e in test.store.events(test.f.spec['request_id'])))
    return super().open()
   def start(self,*args):
    test.calls.append('start');test.assertTrue(any(e['key']=='normal-dispatch-start'for e in test.store.events(test.f.spec['request_id'])))
    return super().start(*args)
  self.Adapter=Adapter
 def cap(self,op,body,resource=False):
  return BudgetAuthority.configured({'policy_sha256':digest(self.policy),'requests':{op:digest(body)}},plan_authority=self.pc,authorize=lambda:None,controls=lambda:copy.deepcopy(self.control),resource_check=lambda _:resource)
 def status(self):return self.store.dispatch_budget_status(self.plan['plan_id'],authority=self.cap('status',{}))
 def admit(self,marked=True):
  intent=copy.deepcopy(self.f.intent)
  if marked:intent['normal_dispatch_budget']=self.binding.marker(self.f.spec['request_id'],self.f.grant)
  self.store.admit(self.f.spec,intent)
 def worker(self,**kw):return TaskWorker(self.store,adapter_factory=self.Adapter,writer_check=lambda _:True,normal_budget_bindings=self.bindings,**kw)
 def close(self):
  c={'closure_id':'verified-close','dispatch_id':self.req['dispatch_id'],'reservation_sha256':digest(self.req),'outcome':'unknown','resources_closed':True,'evidence_ref':'synthetic/closed.json','evidence_sha256':'b'*64,'token_usage':None,'cost':None,'native_usage':None}
  self.store.close_dispatch_resources(self.plan['plan_id'],c,authority=self.cap('close',c,True))
 def test_reserve_precedes_open_consumption_precedes_start_and_native_ids_persist(self):
  self.admit();w=self.worker();self.assertTrue(w.run_one());self.assertFalse(w.run_one())
  self.assertEqual(self.calls,['open','start']);self.assertEqual(self.status()['charged_total'],1)
  self.assertEqual(self.status()['held_dispatch_ids'],['normal-call'])
  task=self.store.task(self.f.spec['request_id']);self.assertEqual(task['status'],'checks_complete')
  native=[e['data']for e in self.store.events(task['id'])if e['key'].startswith('normal-dispatch-native-')]
  self.assertEqual([(e['thread_id'],e['turn_id'])for e in native],[('ordinary-thread',None),('ordinary-thread','ordinary-turn')])
  self.assertEqual(native[1]['marker']['request_sha256'],digest(self.req));self.assertEqual(native[1]['worker']['owner'],w.owner)
 def test_missing_or_wrong_binding_has_zero_open(self):
  self.admit();TaskWorker(self.store,adapter_factory=self.Adapter,writer_check=lambda _:True).run_one()
  self.assertEqual(self.calls,[]);self.assertEqual(self.status()['charged_total'],0)
  self.assertEqual(self.store.task(self.f.spec['request_id'])['result']['error_class'],'NORMAL_DISPATCH_BINDING_REQUIRED')
 def test_selected_none_cannot_fall_back_on_legacy_task(self):
  self.admit(False);self.bindings={self.f.spec['request_id']:None};self.worker().run_one()
  self.assertEqual(self.calls,[]);self.assertEqual(self.status()['charged_total'],0)
 def test_pause_and_uncommitted_reservation_have_zero_open(self):
  self.admit();self.control['state']='paused';self.worker().run_one();self.assertEqual(self.calls,[])
 def test_storage_commit_failure_before_open_never_invokes(self):
  self.admit()
  self.store.fault=lambda phase:(_ for _ in ()).throw(OSError('synthetic full'))if phase=='before_dispatch_budget_commit'else None
  self.worker().run_one();self.store.fault=lambda _:None
  self.assertEqual(self.calls,[]);self.assertEqual(self.status()['charged_total'],0)
 def test_budget_exhausted_has_zero_open(self):
  # Different disposable baseline; no policy reset of the existing lineage.
  f=NormalDispatchBudgetTests();f.setUp()
  try:
   # Create exhaustion through the supported immutable baseline on a new plan.
   p=copy.deepcopy(f.plan);p['plan_id']='SECOND-LINEAGE'
   pc=PlanAuthority.configured({'plan_sha256':digest(p)},authorize=lambda:None,controls=lambda:f.control,routes=lambda _:False,package_for=lambda _:None)
   f.store.import_plan(p,authority=pc);policy={**f.policy,'lineage_id':p['plan_id'],'observed_model_dispatches':460}
   def cap(op,body):return BudgetAuthority.configured({'policy_sha256':digest(policy),'requests':{op:digest(body)}},plan_authority=pc,authorize=lambda:None,controls=lambda:f.control,resource_check=lambda _:False)
   f.store.initialize_dispatch_budget(p['plan_id'],policy,authority=cap('baseline',policy))
   f.binding=NormalDispatchBinding.configured(lineage_id=p['plan_id'],request_value=f.req,grant=f.f.grant,authority_value=cap('reserve',f.req));f.bindings={f.f.spec['request_id']:f.binding};f.admit();f.worker().run_one();self.assertEqual(f.calls,[])
  finally:f.doCleanups()
 def test_before_turn_pause_close_and_deadline_zero_start(self):
  for mode in ('pause','close','deadline'):
   with self.subTest(mode=mode):
    f=NormalDispatchBudgetTests();f.setUp()
    try:
     f.admit()
     def hook(phase):
      if phase=='before_turn':
       if mode=='pause':f.control['state']='paused'
       elif mode=='close':f.close()
       else:
        clock=mock.patch('hub.task_store.time.time',return_value=time.time()+5);clock.start();f.addCleanup(clock.stop)
     f.worker(fault=hook).run_one();self.assertEqual(f.calls,['open'])
     self.assertEqual(f.store.task(f.f.spec['request_id'])['status'],'requires_reconcile')
    finally:f.doCleanups()
 def test_unknown_effect_is_consumed_held_and_second_start_rejected(self):
  self.admit();test=self
  class Unknown(self.Adapter):
   def start(self,*args):test.calls.append('unknown-start');raise ServiceError('SYNTHETIC_UNKNOWN',outcome='UNKNOWN')
  w=TaskWorker(self.store,adapter_factory=Unknown,writer_check=lambda _:True,normal_budget_bindings=self.bindings)
  w.run_one();self.assertEqual(self.calls,['open','unknown-start']);task=self.store.task(self.f.spec['request_id'])
  self.assertEqual(task['status'],'requires_reconcile');self.assertEqual(self.status()['held_dispatch_ids'],['normal-call'])
  self.assertFalse(w.run_one());self.assertEqual(self.status()['charged_total'],1)
  self.assertTrue(self.store.reserve_dispatch(self.plan['plan_id'],self.req,authority=self.cap('reserve',self.req))['replayed'])
  # Same durable owner/intent cannot consume again even before any turn binding.
  with self.store._connection(True)as db:db.execute("UPDATE tasks SET status='running' WHERE id=?",(task['id'],))
  with self.assertRaisesRegex(ServiceError,'NORMAL_DISPATCH_ALREADY_CONSUMED'):
   self.store.assert_turn_start(task['id'],w.owner,task,self.f.grant,task['thread_id'],normal_budget_binding=self.binding,worker_identity=w.identity)
  self.close()
  with self.assertRaises(ServiceError):self.store.assert_turn_start(task['id'],w.owner,task,self.f.grant,task['thread_id'],normal_budget_binding=self.binding,worker_identity=w.identity)
 def test_wrong_grant_or_request_authority_rejects(self):
  with self.assertRaises(ServiceError):self.binding.marker(self.f.spec['request_id'],{**self.f.grant,'version':2})
  with self.assertRaises(ServiceError):NormalDispatchBinding.configured(lineage_id=self.plan['plan_id'],request_value={**self.req,'task_id':'other-task'},grant=self.f.grant,authority_value=self.cap('reserve',self.req))
 def test_service_runtime_worker_same_binding_and_store(self):
  service=TaskService(None,None,self.store,writer_check=lambda _:True,normal_budget_bindings=self.bindings)
  resolver=SimpleNamespace(authority={'registry_hash':self.f.registry_hash},_check_registry=lambda:None)
  service._ordinary_project=lambda _:(resolver,self.f.registry['projects'][0]);service._ordinary_grant=lambda *args,**kw:self.f.grant
  service.capabilities=lambda:{'ordinary_execution':{'available':True}}
  runtime=OrdinaryRuntime(self.store,grants=[self.f.grant],provider_status={'available':True,'classification':'synthetic_fixture'},adapter_factory=self.Adapter,poll_interval=.01)
  self.addCleanup(runtime.stop);service.runtime_status=runtime.status
  self.assertIs(runtime._normal_budgets[self.f.spec['request_id']],self.binding)
  service.submit_ordinary(self.f.spec,owner_context='synthetic-owner',authorize=lambda:None)
  runtime.start();end=time.monotonic()+3
  while time.monotonic()<end and self.store.task(self.f.spec['request_id'])['status']not in {'checks_complete','failed','requires_reconcile'}:time.sleep(.01)
  runtime.stop();self.assertEqual(self.store.task(self.f.spec['request_id'])['status'],'checks_complete');self.assertEqual(self.calls,['open','start'])
 def test_unconfigured_legacy_path_unchanged(self):
  self.admit(False);TaskWorker(self.store,adapter_factory=self.f.Adapter,writer_check=lambda _:True).run_one()
  self.assertEqual(self.store.task(self.f.spec['request_id'])['status'],'checks_complete');self.assertEqual(self.status()['charged_total'],0)


class NormalBudgetRecoveryTests(unittest.TestCase):
 setUp=NormalDispatchBudgetTests.setUp
 cap=NormalDispatchBudgetTests.cap
 status=NormalDispatchBudgetTests.status
 admit=NormalDispatchBudgetTests.admit
 worker=NormalDispatchBudgetTests.worker
 close=NormalDispatchBudgetTests.close
 def failure(self,phase='after_thread_binding'):
  import multiprocessing
  self.admit();ctx=multiprocessing.get_context('fork');out=ctx.Queue()
  def run():
   test=self
   class Failing(self.Adapter):
    def open(self):
     if phase=='before_open':raise ServiceError('SYNTHETIC_OPEN_FAILED',outcome='UNKNOWN')
     return super().open()
   def fault(at):
    if at==phase:raise ServiceError('SYNTHETIC_BEFORE_START',outcome='UNKNOWN')
   TaskWorker(self.store,adapter_factory=Failing,writer_check=lambda _:True,
       normal_budget_bindings=self.bindings,fault=fault).run_one()
   out.put(self.calls)
  proc=ctx.Process(target=run);proc.start();proc.join(4)
  if proc.is_alive():proc.terminate();proc.join(2);self.fail('Owned fixture worker timeout')
  self.assertEqual(proc.exitcode,0);calls=out.get(timeout=1);out.close();out.join_thread()
  self.assertNotIn('start',calls)
  return self.store.task(self.f.spec['request_id'])
 def next_binding(self,**changes):
  r={**self.req,'dispatch_id':'normal-call-2','attempt_id':'normal-attempt-2',
     'deadline':time.time()+self.req['seconds']+3,**changes}
  return NormalDispatchBinding.configured(lineage_id=self.plan['plan_id'],request_value=r,
      grant=self.f.grant,authority_value=self.cap('reserve',r))
 def proof(self,task):
  if task['thread_id']is None:return {'source':'app_server_stdio.thread/list','root':self.f.grant['root'],
      'thread_ids':[],'archived_checked':True,'observed_at':time.time()}
  return {'source':'app_server_stdio.thread/read','root':self.f.grant['root'],'thread_id':task['thread_id'],
      'thread_status':'notLoaded','turns_complete':True,'turns':[],'observed_at':time.time()}
 def cas(self,task):
  events=self.store.events(task['id'])
  return {'expected_task_sha256':digest(task),'expected_marker':task['intent']['normal_dispatch_budget'],
      'expected_events_sha256':digest([{k:e[k]for k in ('sequence','key','data')}for e in events])}
 def recover(self,task,binding,**extra):
  return self.store.reconcile_owned(task['id'],self.proof(task),normal_budget_binding=binding,**{**self.cas(task),**extra})
 def test_budget_recovery_before_open_and_bound_thread_charges_new_attempt(self):
  for phase in ('before_open','after_thread_binding','before_turn'):
   with self.subTest(phase=phase):
    f=NormalBudgetRecoveryTests();f.setUp()
    try:
     task=f.failure(phase);history=f.store.events(task['id']);f.close();binding=f.next_binding()
     with mock.patch('hub.task_service.fingerprint',side_effect=AssertionError('No ordinary root traversal')):
      service=TaskService(None,None,f.store,normal_budget_bindings={task['id']:binding})
      service.reconcile_ordinary_budget(task['id'],f.proof(task),**f.cas(task))
     current=f.store.task(task['id']);self.assertEqual(current['thread_id'],task['thread_id'])
     self.assertEqual(current['status'],'queued');self.assertEqual(f.store.events(task['id'])[:-1],history)
     audit=f.store.events(task['id'])[-1]['data'];self.assertEqual(audit['original_task'],task)
     f.req=binding.snapshot()['request'];f.binding=binding;f.bindings={task['id']:binding}
     resumes=[]
     class Resuming(f.Adapter):
      def thread(self,previous=None):resumes.append(previous);return super().thread(previous)
     w=TaskWorker(f.store,adapter_factory=Resuming,writer_check=lambda _:True,normal_budget_bindings=f.bindings)
     self.assertTrue(w.run_one());self.assertFalse(w.run_one());self.assertEqual(f.calls,['open','start'])
     self.assertEqual(resumes,[task['thread_id']]);self.assertEqual(f.status()['charged_total'],2)
     self.assertEqual(f.status()['held_dispatch_ids'],['normal-call-2'])
     self.assertEqual(f.store.task(task['id'])['status'],'checks_complete')
     marker=binding.marker(task['id'],f.f.grant)
     current_events=[e for e in f.store.events(task['id'])if e['data'].get('marker')==marker]
     self.assertEqual(sum(e['key'].startswith('normal-dispatch-reserved')for e in current_events),1)
     self.assertEqual(sum(e['key'].startswith('normal-dispatch-start')for e in current_events),1)
     self.assertEqual([(e['data']['thread_id'],e['data']['turn_id'])for e in current_events
         if e['key'].startswith('normal-dispatch-native')],[('ordinary-thread',None),('ordinary-thread','ordinary-turn')])
     with f.store._connection()as db:
      self.assertIsNone(f.store._normal_dispatch_event(db,task['id'],'start',task['intent']['normal_dispatch_budget']))
     self.assertEqual(f.store.events(task['id'])[:len(history)],history)
     with self.assertRaises(ServiceError):f.recover(task,binding)
    finally:f.doCleanups()
 def test_recovery_cas_closure_and_new_dispatch_attempt_required(self):
  task=self.failure();binding=self.next_binding();history=self.store.events(task['id'])
  with self.assertRaises(ServiceError):self.recover(task,binding)
  self.close()
  for extra in ({'expected_task_sha256':'0'*64},{'expected_marker':{}},{'expected_events_sha256':'0'*64}):
   with self.subTest(extra=extra),self.assertRaises(ServiceError):self.recover(task,binding,**extra)
  for changes in ({'dispatch_id':self.req['dispatch_id']},{'attempt_id':self.req['attempt_id']}):
   with self.subTest(changes=changes),self.assertRaises(ServiceError):self.recover(task,self.next_binding(**changes))
  self.assertEqual(self.store.task(task['id']),task);self.assertEqual(self.store.events(task['id']),history)
 def test_recovery_rejects_bad_metadata_registration_inputs_grant_and_control(self):
  task=self.failure();self.close();binding=self.next_binding();proof=self.proof(task);history=self.store.events(task['id'])
  for changes in ({'turns_complete':False},{'turns':[{'id':'ambiguous'}]},{'thread_id':'foreign'},
                  {'observed_at':time.time()-31},{'source':'caller_claim'}):
   with self.subTest(changes=changes),self.assertRaises(ServiceError):
    self.store.reconcile_owned(task['id'],{**proof,**changes},normal_budget_binding=binding,**self.cas(task))
  missing=dict(proof);missing.pop('turns_complete')
  with self.assertRaises(ServiceError):self.store.reconcile_owned(task['id'],missing,normal_budget_binding=binding,**self.cas(task))
  self.control['state']='paused'
  with self.assertRaises(ServiceError):self.recover(task,binding)
  self.control['state']='active'
  source=self.f.source.read_bytes();self.f.source.write_text('changed declared input')
  with self.assertRaises(ServiceError):self.recover(task,binding)
  self.f.source.write_bytes(source)
  original=self.f.registry_path.read_bytes();self.f.registry_path.write_text('projects: []')
  with mock.patch('hub.ordinary_tasks.snapshot_inputs',side_effect=AssertionError('Registration must precede source reads')):
   with self.assertRaises(ServiceError):self.recover(task,binding)
  self.f.registry_path.write_bytes(original)
  self.store.register_grant({**self.f.grant,'version':2})
  with self.assertRaises(ServiceError):self.recover(task,binding)
  self.assertEqual(self.store.task(task['id']),task);self.assertEqual(self.store.events(task['id']),history)
 def test_recovery_rejects_live_worker_and_consumed_unknown_start(self):
  task=self.failure();self.close();binding=self.next_binding()
  import os
  from pathlib import Path
  ticks=Path('/proc',str(os.getpid()),'stat').read_text().rsplit(')',1)[1].split()[19]
  self.store.event(task['id'],'dispatch-intent-live-test',{'worker':{'pid':os.getpid(),'start_ticks':ticks}})
  with self.assertRaisesRegex(ServiceError,'TASK_OWNER_STILL_ACTIVE'):self.recover(task,binding)
  f=NormalBudgetRecoveryTests();f.setUp()
  try:
   task=f.failure('before_turn');f.close();binding=f.next_binding()
   marker=task['intent']['normal_dispatch_budget']
   f.store.event(task['id'],'normal-dispatch-start',{'marker':marker,'outcome':'unknown'})
   with self.assertRaisesRegex(ServiceError,'NORMAL_DISPATCH_START_UNCERTAIN'):f.recover(task,binding)
   self.assertIsNone(f.store.task(task['id'])['turn_id']);self.assertEqual(f.status()['charged_total'],1)
  finally:f.doCleanups()
