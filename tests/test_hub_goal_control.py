"""Trusted synthetic controls, disposable ledger and fake native transport only."""
import copy,time,unittest
from unittest import mock
import test_hub_normal_dispatch_budget as fixtures
from hub.dispatch_budget import BudgetAuthority,NormalDispatchBinding
from hub.ordinary_runtime import OrdinaryRuntime
from hub.plan_admission import PlanAuthority
from hub.service_contract import ServiceError
from hub.task_worker import TaskWorker
from hub.workbench_store import digest

class GoalControlTests(unittest.TestCase):
 setUp=fixtures.NormalDispatchBudgetTests.setUp
 cap=fixtures.NormalDispatchBudgetTests.cap
 status=fixtures.NormalDispatchBudgetTests.status
 admit=fixtures.NormalDispatchBudgetTests.admit
 worker=fixtures.NormalDispatchBudgetTests.worker
 def run_change(self,change,*,fail_interrupt=False):
  self.admit();test=self;interrupts=[];polls=[]
  class Adapter(self.Adapter):
   def poll(self,timeout):
    polls.append(timeout);change();return super().poll(timeout)
   def interrupt(self,thread,turn):
    interrupts.append((thread,turn))
    if fail_interrupt:raise ServiceError('SYNTHETIC_INTERRUPT_UNKNOWN',outcome='UNKNOWN')
    return super().interrupt(thread,turn)
  w=TaskWorker(self.store,adapter_factory=Adapter,writer_check=lambda _:True,normal_budget_bindings=self.bindings)
  began=time.monotonic();w.run_one();elapsed=time.monotonic()-began
  task=self.store.task(self.f.spec['request_id'])
  self.assertEqual(task['status'],'requires_reconcile');self.assertFalse(w.run_one())
  self.assertEqual(interrupts,[('ordinary-thread','ordinary-turn')]);self.assertEqual(self.calls,['open','start'])
  events=self.store.events(task['id']);stops=[e for e in events if e['key'].startswith('normal-control-stop-')]
  self.assertEqual(len(stops),3);self.assertEqual(task['result']['goal_control']['observation']['control']['state'],self.control.get('state','unknown')if self.control.get('state')in {'active','paused','cancelled'}else'unknown')
  self.assertLess(elapsed,2) # Only this immediate synthetic callback/adapter.
  return task,events,elapsed
 def test_running_pause_interrupts_exact_owned_once_and_keeps_budget(self):
  task,events,_=self.run_change(lambda:self.control.update(state='paused'))
  self.assertEqual(task['result']['goal_control']['interrupt_status'],'returned_not_stop_proof')
  self.assertEqual(task['result']['goal_control']['observation']['baseline']['revision'],1)
  self.control['state']='active';self.assertEqual(self.status()['charged_total'],1)
  self.assertEqual(self.status()['held_dispatch_ids'],['normal-call'])
 def test_active_version_or_target_change_stops_old_dispatch(self):
  for change in ({'revision':2},{'objective_sha256':'e'*64}):
   with self.subTest(change=change):
    f=GoalControlTests();f.setUp()
    try:f.run_change(lambda:f.control.update(change))
    finally:f.doCleanups()
 def test_unknown_and_unconfirmed_interrupt_are_not_stop_proof(self):
  task,events,_=self.run_change(lambda:self.control.update(state='unknown'),fail_interrupt=True)
  self.assertEqual(task['result']['goal_control']['interrupt_status'],'unknown')
  self.assertEqual(task['result']['error_class'],'NORMAL_GOAL_CONTROL_STOPPED')
 def approval_worker(self,mode):
  self.admit();test=self;interrupts=[];decisions=[];original=self.store.controls
  def controls(ident):
   rows=original(ident)
   if rows and rows[-1]['version']==1:
    if mode=='waiting_pause':test.control['state']='paused'
    else:
     test.store.respond_control(ident,rows[-1]['id'],1,'decline',owner_context=rows[-1]['owner_context'])
     rows=original(ident)
     test.control.update({'state':'cancelled'}if mode=='cancel_return'else{'revision':2})
   return rows
  class Adapter(self.Adapter):
   def poll(self,timeout):
    answer=self.request_handler({'id':7,'method':'item/commandExecution/requestApproval','params':{
      'threadId':'ordinary-thread','turnId':'ordinary-turn','itemId':'synthetic-item','command':'synthetic-command'}})
    decisions.append(answer);return super().poll(timeout)
   def interrupt(self,thread,turn):interrupts.append((thread,turn));return super().interrupt(thread,turn)
  with mock.patch.object(self.store,'controls',side_effect=controls):
   worker=TaskWorker(self.store,adapter_factory=Adapter,writer_check=lambda _:True,normal_budget_bindings=self.bindings)
   start=time.monotonic();worker.run_one();elapsed=time.monotonic()-start
  self.assertEqual(decisions,[]);self.assertEqual(interrupts,[('ordinary-thread','ordinary-turn')])
  task=self.store.task(self.f.spec['request_id']);self.assertEqual(task['status'],'requires_reconcile')
  self.assertLess(elapsed,2);self.assertLess(task['result']['goal_control']['interrupt_returned_at']-task['result']['goal_control']['observation']['observed_at'],5)
  return task
 def test_pause_during_approval_wait_does_not_return_decision(self):self.approval_worker('waiting_pause')
 def test_cancel_or_revision_at_decision_return_boundary(self):
  for mode in ('cancel_return','version_return'):
   with self.subTest(mode=mode):
    f=GoalControlTests();f.setUp()
    try:
     task=f.approval_worker(mode);self.assertEqual(task['cancel'],int(mode=='cancel_return'))
    finally:f.doCleanups()
 def test_read_control_is_fenced_nonconsuming_and_not_full_turn_budget(self):
  self.admit();w=self.worker();task=self.store.claim(w.owner)
  self.store.reserve_normal_dispatch(task['id'],w.owner,task,self.f.grant,w.identity,self.binding)
  before=self.store.events(task['id']);now=time.time()
  with mock.patch('hub.task_store.time.time',return_value=now+5):
   for _ in range(5):
    observed=self.store.normal_dispatch_control(task['id'],binding=self.binding,owner=w.owner,worker=w.identity)
    self.assertTrue(observed['progress_allowed'])
  self.assertEqual(self.store.events(task['id']),before)
  with self.assertRaises(ServiceError):self.store.normal_dispatch_control(task['id'],binding=self.binding,owner='foreign-worker',worker=w.identity)
  r={**self.req,'dispatch_id':'wrong-dispatch'}
  wrong=NormalDispatchBinding.configured(lineage_id=self.plan['plan_id'],request_value=r,grant=self.f.grant,authority_value=self.cap('reserve',r))
  with self.assertRaises(ServiceError):self.store.normal_dispatch_control(task['id'],binding=wrong,owner=w.owner,worker=w.identity)
 def test_provider_failure_unknown_and_reserve_still_requires_active(self):
  def broken():raise RuntimeError('synthetic private text')
  cap=BudgetAuthority.configured({'policy_sha256':digest(self.policy),'requests':{'reserve':digest(self.req)}},plan_authority=self.pc,authorize=lambda:None,controls=broken,resource_check=lambda _:False)
  observed=cap.observe_controls(self.plan,self.policy,'reserve',self.req)
  self.assertEqual(observed['state'],'unknown');self.assertNotIn('private',str(observed))
  with self.assertRaises(ServiceError):cap.check(self.plan,self.policy,'reserve',self.req)
 def test_runtime_blocks_only_selected_lineage_and_legacy_continues(self):
  self.admit();self.control['state']='paused'
  peer={**self.f.spec,'request_id':'unrelated-task'};intent={**self.f.intent,'task_spec':peer}
  self.store.admit(peer,intent)
  # Another explicitly bound lineage stays active independently of the first.
  plan={**copy.deepcopy(self.plan),'plan_id':'UNRELATED-LINEAGE'}
  pc=PlanAuthority.configured({'plan_sha256':digest(plan)},authorize=lambda:None,
      controls=lambda:{'state':'active','revision':1},routes=lambda _:False,package_for=lambda _:None)
  self.store.import_plan(plan,authority=pc)
  policy={**self.policy,'lineage_id':plan['plan_id']}
  def cap(op,body):
   return BudgetAuthority.configured({'policy_sha256':digest(policy),'requests':{op:digest(body)}},
       plan_authority=pc,authorize=lambda:None,controls=lambda:{'state':'active','revision':1},resource_check=lambda _:False)
  self.store.initialize_dispatch_budget(plan['plan_id'],policy,authority=cap('baseline',policy))
  spec={**self.f.spec,'request_id':'other-lineage-task'}
  req={**self.req,'dispatch_id':'other-lineage-dispatch','task_id':spec['request_id'],'attempt_id':'other-lineage-attempt'}
  binding=NormalDispatchBinding.configured(lineage_id=plan['plan_id'],request_value=req,grant=self.f.grant,authority_value=cap('reserve',req))
  self.store.admit(spec,{**self.f.intent,'task_spec':spec,'normal_dispatch_budget':binding.marker(spec['request_id'],self.f.grant)})
  bindings={**self.bindings,spec['request_id']:binding};calls=[]
  class Adapter(self.f.Adapter):
   def open(self):calls.append('open');return super().open()
   def start(self,*args):calls.append('start');return super().start(*args)
  runtime=OrdinaryRuntime(self.store,grants=[self.f.grant],provider_status={'available':True,'classification':'synthetic_fixture'},adapter_factory=Adapter,poll_interval=.01,normal_budget_bindings=bindings)
  self.addCleanup(runtime.stop);runtime.start();end=time.monotonic()+3
  while time.monotonic()<end and any(self.store.task(i)['status']not in {'checks_complete','failed','requires_reconcile'}for i in (peer['request_id'],spec['request_id'])):time.sleep(.01)
  self.assertEqual(self.store.task(peer['request_id'])['status'],'checks_complete')
  self.assertEqual(self.store.task(self.f.spec['request_id'])['status'],'queued')
  self.assertEqual(self.store.task(spec['request_id'])['status'],'checks_complete')
  self.assertEqual(calls.count('open'),2);self.assertEqual(calls.count('start'),2);runtime.stop()
 def test_legacy_unconfigured_worker_does_not_read_goal(self):
  self.admit(False)
  with mock.patch.object(self.store,'normal_dispatch_control',side_effect=AssertionError('No implicit goal scope')):
   TaskWorker(self.store,adapter_factory=self.f.Adapter,writer_check=lambda _:True).run_one()
  self.assertEqual(self.store.task(self.f.spec['request_id'])['status'],'checks_complete')
