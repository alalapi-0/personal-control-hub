"""Disposable, source-only lineage budget guards; never invokes a model."""
import sys,copy,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import test_hub_rebuild_contracts as contracts
from hub.workbench_store import digest

class PackageLocalCounterexample(unittest.TestCase):
 def test_prepolicy_legacy_is_not_current_lineage_admission_proof(self):
  f=contracts.RebuildContractsTests();f.setUp();self.addCleanup(f.doCleanups)
  f.executor.update(kind='model',thread_id='synthetic-thread',turn_id='synthetic-turn')
  f.package['budget'].update(remaining_attempts=1,remaining_seconds=60,timeout_seconds=60);f.configure();f.start()
  p=copy.deepcopy(f.package);p['task_id']='second-task';p['lineage']['original_task_id']='second-task';f.package=p;f.configure();f.start()
  with f.store._connection()as db:count=db.execute("SELECT count(*) FROM events WHERE event_key LIKE 'rebuild-attempt-%'").fetchone()[0]
  self.assertEqual(count,2) # Explicit activation/invocation gap; not the active-policy gate.

import json,time,shutil,threading,sqlite3,hashlib
from unittest import mock
from hub.dispatch_budget import BudgetAuthority,FORMAT,split
from hub.plan_admission import PlanAuthority,plan_history
from hub.service_contract import ServiceError
from hub.task_store import TaskStore
from hub.rebuild_contracts import RootAuthority
import test_hub_plan_admission as plans

class DispatchBudgetTests(unittest.TestCase):
 def setUp(self):
  f=plans.PlanStoreTests();f.setUp();self.addCleanup(f.doCleanups);self.fixture=f;self.store=f.store;self.p=f.p;self.control={'state':'active','revision':1};self.plan_cap=f.cap
  self.store.import_plan(self.p,authority=self.plan_cap);self.policy={'schema_version':'1.0','lineage_id':self.p['plan_id'],'source_state_sha256':'a'*64,'cutoff_id':'synthetic-cutoff','covered_dispatch_ids':['covered-call'],'observed_model_dispatches':1,'historical_unknown_allowance':8,'closure_reserve':12,'conservative_deterministic_dispatches':0,'limit':480,'provenance_ref':'synthetic/state-cutoff.json'}
 def cap(self,op,body,*,resource=False,p=None,plan_cap=None):
  b={'policy_sha256':digest(p or self.policy),'requests':{op:digest(body)}}
  if resource:b['resource_authority_sha256']='d'*64
  return BudgetAuthority.configured(b,plan_authority=plan_cap or self.plan_cap,authorize=lambda:None,controls=lambda:copy.deepcopy(self.control),resource_check=lambda _:resource)
 def initialize(self,n=1):
  self.policy['observed_model_dispatches']=n;return self.store.initialize_dispatch_budget(self.p['plan_id'],self.policy,authority=self.cap('baseline',self.policy))
 def req(self,n=1,**changes):
  r={'dispatch_id':'dispatch-'+str(n),'task_id':'task-'+str(n),'attempt_id':'attempt-'+str(n),'package_sha256':None,'executor':{'kind':'model','thread_id':'synthetic-thread-'+str(n),'turn_id':'synthetic-turn','session_id':None},'purpose':'normal','effect':'read_only','gpu':False,'seconds':30,'deadline':time.time()+100};r.update(changes);return r
 def reserve(self,r,resource=False):return self.store.reserve_dispatch(self.p['plan_id'],r,authority=self.cap('reserve',r,resource=resource))
 def status(self):return self.store.dispatch_budget_status(self.p['plan_id'],authority=self.cap('status',{}))
 def close(self,r,**changes):
  proof={'closure_id':'close-'+r['dispatch_id'],'dispatch_id':r['dispatch_id'],'reservation_sha256':digest(r),'outcome':'failed','resources_closed':True,'evidence_ref':'synthetic/closed.json','evidence_sha256':'c'*64,'token_usage':None,'cost':None,'native_usage':None};proof.update(changes);self.store.close_dispatch_resources(self.p['plan_id'],proof,authority=self.cap('close',proof,resource=True));return proof
 def test_cross_task_failure_new_session_and_redecomposition_never_refund(self):
  self.initialize(459);r=self.req();self.reserve(r);self.close(r,outcome='failed')
  for n in(2,3):
   with self.subTest(n=n),self.assertRaises(ServiceError):self.reserve(self.req(n))
  self.assertEqual(self.status()['charged_total'],460)
 def test_exact_replay_conflict_baseline_cutoff_and_covered_ids(self):
  self.initialize();before=self.store.path.read_bytes();self.assertTrue(self.store.initialize_dispatch_budget(self.p['plan_id'],self.policy,authority=self.cap('baseline',self.policy))['replayed']);self.assertEqual(before,self.store.path.read_bytes())
  bad=copy.deepcopy(self.policy);bad['cutoff_id']='reset'
  with self.assertRaises(ServiceError):self.store.initialize_dispatch_budget(self.p['plan_id'],bad,authority=self.cap('baseline',bad,p=bad))
  r=self.req();self.reserve(r);self.assertTrue(self.reserve(r)['replayed']);changed={**r,'task_id':'other'}
  with self.assertRaises(ServiceError):self.reserve(changed)
  with self.assertRaises(ServiceError):self.reserve(self.req(dispatch_id='covered-call'))
  self.assertEqual(self.status()['charged_total'],2)
 def test_normal_cannot_use_closure_allowance_and_telemetry_remains_unknown(self):
  self.initialize(460)
  with self.assertRaises(ServiceError):self.reserve(self.req())
  r=self.req(purpose='closure');s=self.reserve(r);self.assertEqual(s['charged_total'],461);self.assertIsNone(s['token_usage']);self.assertIsNone(s['cost']);self.assertIsNone(s['native_usage'])
  with self.assertRaises(ServiceError):self.reserve(self.req(2,seconds=901))
 def test_writer_readonly_gpu_capacity_and_unknown_holds(self):
  self.initialize();a=self.req(effect='scoped_write');self.reserve(a)
  with self.assertRaises(ServiceError):self.reserve(self.req(2,effect='scoped_write'))
  self.close(a,outcome='unknown',resources_closed=False)
  with self.assertRaises(ServiceError):self.reserve(self.req(3,effect='scoped_write'))
  self.close(a,closure_id='verified-later',outcome='unknown');self.reserve(self.req(4,effect='scoped_write'))
  self.reserve(self.req(5));self.reserve(self.req(6))
  with self.assertRaises(ServiceError):self.reserve(self.req(7))
 def test_gpu_exact_internal_authority_and_second_gpu_refused(self):
  self.initialize();r=self.req(gpu=True)
  with self.assertRaises(ServiceError):self.reserve(r)
  self.reserve(r,True)
  with self.assertRaises(ServiceError):self.reserve(self.req(2,gpu=True),True)
 def test_forged_closure_and_lease_expiry_cannot_free_resources(self):
  self.initialize();r=self.req(effect='scoped_write');self.reserve(r)
  proof={'closure_id':'forged','dispatch_id':r['dispatch_id'],'reservation_sha256':digest(r),'outcome':'succeeded','resources_closed':True,'evidence_ref':'synthetic/closed','evidence_sha256':'d'*64,'token_usage':None,'cost':None,'native_usage':None}
  with self.assertRaises(ServiceError):self.store.close_dispatch_resources(self.p['plan_id'],proof,authority=self.cap('close',proof))
  with mock.patch('time.time',return_value=time.time()+1000):
   with self.assertRaises(ServiceError):self.reserve(self.req(2,effect='scoped_write'))
  self.assertEqual(self.status()['held_dispatch_ids'],[r['dispatch_id']])
 def test_concurrent_last_slot_only_one_and_unrelated_lineage(self):
  self.initialize(459);out=[];gate=threading.Barrier(2)
  def worker(n):
   try:gate.wait();self.reserve(self.req(n));out.append('reserved')
   except ServiceError:out.append('refused')
  threads=[threading.Thread(target=worker,args=(n,))for n in(1,2)]
  for t in threads:t.start()
  for t in threads:t.join(timeout=10);self.assertFalse(t.is_alive())
  self.assertEqual(sorted(out),['refused','reserved'])
  other=copy.deepcopy(self.p);other['plan_id']='UNRELATED';pc=PlanAuthority.configured({'plan_sha256':digest(other)},authorize=lambda:None,controls=lambda:self.control,routes=lambda _:False,package_for=lambda _:None);self.store.import_plan(other,authority=pc);p=copy.deepcopy(self.policy);p['lineage_id']='UNRELATED';p['observed_model_dispatches']=1;self.store.initialize_dispatch_budget('UNRELATED',p,authority=self.cap('baseline',p,p=p,plan_cap=pc));r=self.req(9);self.store.reserve_dispatch('UNRELATED',r,authority=self.cap('reserve',r,p=p,plan_cap=pc))
 def test_control_authority_storage_failure_before_invocation(self):
  self.initialize();r=self.req()
  with self.assertRaises(ServiceError):self.store.reserve_dispatch(self.p['plan_id'],r,authority={'policy':self.policy})
  self.control['state']='paused'
  with self.assertRaises(ServiceError):self.reserve(r)
  self.control['state']='active'
  def fault(phase):
   if phase=='before_dispatch_budget_commit':raise OSError('synthetic storage full')
  self.store.fault=fault
  with self.assertRaises(OSError):self.reserve(r)
  self.store.fault=lambda _:None;self.assertEqual(self.status()['charged_total'],1)
 def test_revision_rollback_return_and_strict_cold_restore_retain_counts(self):
  self.initialize();r=self.req();self.reserve(r);original=copy.deepcopy(self.p);before=self.status();target=copy.deepcopy(self.p);target['revision']='2';target['tasks'][1]['objective']='changed definition'
  def revision(p,prior,request,rollback=False):
   cap=PlanAuthority.configured({'plan_sha256':digest(p),'reconcile_from_sha256':digest(prior)},authorize=lambda:None,controls=lambda:self.control,routes=lambda _:True,package_for=lambda _:None);self.store.reconcile_plan(p,expected_sha256=digest(prior),request_id=request,authority=cap,rollback=rollback);self.p=p;self.plan_cap=cap
  revision(target,original,'change');self.assertEqual(self.status()['charged_total'],before['charged_total']);revision(original,target,'rollback',True);revision(target,original,'return',True)
  from hub.task_storage_restore import _snapshot
  raw=self.store.path.read_bytes();_snapshot(self.store,raw);root=self.fixture.root.parent/'cold';root.mkdir(mode=0o700);copy_store=TaskStore(root);copy_store.path.parent.mkdir(mode=0o700,parents=True);shutil.copy2(self.store.path,copy_store.path);shutil.copy2(self.store.registration_path,copy_store.registration_path);h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();copy_store.restore_storage(database_sha256=h(copy_store.path),registration_sha256=h(copy_store.registration_path),confirm_offline=True);self.assertEqual(copy_store.dispatch_budget_status(self.p['plan_id'],authority=self.cap('status',{}))['charged_total'],2)
 def test_offline_malformed_budget_and_payload_hash_rejected(self):
  self.initialize()
  from hub.task_storage_restore import _snapshot
  from hub.workbench_store import canonical
  with sqlite3.connect(':memory:')as db:
   db.deserialize(self.store.path.read_bytes());value=json.loads(db.execute('SELECT body FROM plan_inputs').fetchone()[0]);value['dispatch_budget']['policy']['limit']=999;db.execute('UPDATE plan_inputs SET body=?,hash=?',(canonical(value).decode(),digest(value)));db.commit()
   with self.assertRaises(ServiceError):_snapshot(self.store,db.serialize())

class BudgetArchiveTests(unittest.TestCase):
 def test_archive_and_history_preserve_budget_same_row(self):
  f=plans.ContractPlanIntegrationTests();f.setUp();self.addCleanup(f.doCleanups);f.settled();r=f.records[plans.BASE];p=f.p
  policy={'schema_version':'1.0','lineage_id':p['plan_id'],'source_state_sha256':'a'*64,'cutoff_id':'archive-cutoff','covered_dispatch_ids':['covered'],'observed_model_dispatches':1,'historical_unknown_allowance':8,'closure_reserve':12,'conservative_deterministic_dispatches':12,'limit':480,'provenance_ref':'synthetic/state.json'}
  def cap(op,body):return BudgetAuthority.configured({'policy_sha256':digest(policy),'requests':{op:digest(body)}},plan_authority=f.cap,authorize=lambda:None,controls=lambda:{'state':'active','revision':1},resource_check=lambda _:False)
  f.store.initialize_dispatch_budget(p['plan_id'],policy,authority=cap('baseline',policy));before=f.store.dispatch_budget_status(p['plan_id'],authority=cap('status',{}));events=f.store.events(plans.BASE)
  f.store.archive_plan_completed(p['plan_id'],authority=f.cap);self.assertEqual(f.store.dispatch_budget_status(p['plan_id'],authority=cap('status',{})),before);self.assertEqual(f.store.events(plans.BASE),events);f.store.restore_plan_archive(p['plan_id'],authority=f.cap);self.assertEqual(f.store.dispatch_budget_status(p['plan_id'],authority=cap('status',{})),before)
  self.assertEqual(before['conservative_deterministic_dispatches'],12)

class ActiveAttemptBudgetTests(unittest.TestCase):
 def setUp(self):
  f=contracts.RebuildContractsTests();f.setUp();self.addCleanup(f.doCleanups);self.f=f;self.store=f.store;self.p=plans.plan([plans.task(plans.BASE)]);self.control={'state':'active','revision':1};self.pc=PlanAuthority.configured({'plan_sha256':digest(self.p)},authorize=lambda:None,controls=lambda:self.control,routes=lambda _:False,package_for=lambda _:None);self.store.import_plan(self.p,authority=self.pc)
  self.policy={'schema_version':'1.0','lineage_id':self.p['plan_id'],'source_state_sha256':'a'*64,'cutoff_id':'cutoff','covered_dispatch_ids':[],'observed_model_dispatches':0,'historical_unknown_allowance':8,'closure_reserve':12,'conservative_deterministic_dispatches':0,'limit':480,'provenance_ref':'synthetic/state.json'}
  self.store.initialize_dispatch_budget(self.p['plan_id'],self.policy,authority=self.cap('baseline',self.policy));f.package['lineage']['goal_id']=self.p['plan_id'];f.package['plan']={'revision':self.p['revision'],'sha256':digest(self.p)};f.executor.update(kind='model',thread_id='synthetic-model-thread',turn_id='synthetic-model-turn');f.configure();f.prepare();f.claim=self.store.claim_rebuild(f.package['task_id'],'synthetic-worker',authority=f.authority)
  f.attempt={'schema_version':'2.0','kind':'attempt','attempt_id':'attempt-1','task_id':f.package['task_id'],'package_sha256':digest(f.package),'lease':{k:f.claim[k]for k in('owner','generation','fence','expires_at')},'coverage':{'claim_started_at':f.claim['started_at'],'observed_until':f.claim['expires_at']},'executor':copy.deepcopy(f.executor),'effect_intent':f.package['effects'],'budget_reservation':{'seconds':60,'attempts':1},'started_at':f.now,**f.meta}
  self.req={'dispatch_id':'native-dispatch','task_id':f.package['task_id'],'attempt_id':'attempt-1','package_sha256':digest(f.package),'executor':copy.deepcopy(f.executor),'purpose':'normal','effect':'read_only','gpu':False,'seconds':60,'deadline':f.now+100}
 def cap(self,operation,body):return BudgetAuthority.configured({'policy_sha256':digest(self.policy),'requests':{operation:digest(body)}},plan_authority=self.pc,authorize=lambda:None,controls=lambda:self.control,resource_check=lambda _:False)
 def root(self,dispatch='native-dispatch',cap=None):
  f=self.f;b=copy.deepcopy(f.binding);b['dispatch_id']=dispatch
  return RootAuthority.configured(b,authorize=f.auth,workspace_check=lambda _:copy.deepcopy(f.workspace),sandbox_check=f.sandbox,writer_check=f.writer,budget_authority=cap)
 def link(self):return {'dispatch_id':'native-dispatch','task_id':self.f.package['task_id'],'attempt_id':'attempt-1','package_sha256':digest(self.f.package),'executor_sha256':digest(self.f.attempt['executor'])}
 def record(self,cap):return self.store.record_rebuild_attempt(self.f.package,self.f.attempt,authority=self.root(cap=cap))
 def test_missing_authority_reservation_and_disguised_model_reject(self):
  with self.assertRaises(ServiceError):self.record(None)
  with self.assertRaises(ServiceError):self.record(self.cap('attempt',self.link()))
  forged=copy.deepcopy(self.req);forged['executor']={'kind':'deterministic_host','host_id':'synthetic-host','thread_id':None,'turn_id':None,'session_id':None};self.store.reserve_dispatch(self.p['plan_id'],forged,authority=self.cap('reserve',forged))
  with self.assertRaises(ServiceError):self.record(self.cap('attempt',self.link()))
 def test_positive_exact_attempt_and_replay_capacity_failure_keeps_charge(self):
  self.store.reserve_dispatch(self.p['plan_id'],self.req,authority=self.cap('reserve',self.req));cap=self.cap('attempt',self.link());self.assertFalse(self.record(cap)['replayed']);self.assertTrue(self.record(cap)['replayed'])
  bad=copy.deepcopy(self.f.attempt);bad['attempt_id']='new-attempt'
  with self.assertRaises(ServiceError):self.store.record_rebuild_attempt(self.f.package,bad,authority=self.root(cap=cap))
 def test_event_capacity_failure_before_execution_still_no_refund(self):
  self.store.reserve_dispatch(self.p['plan_id'],self.req,authority=self.cap('reserve',self.req))
  with mock.patch('hub.task_store.MAX_EVENTS',0):
   with self.assertRaises(ServiceError):self.record(self.cap('attempt',self.link()))
  with self.store._connection()as db:self.assertFalse(any(r['key'].startswith('rebuild-attempt-')for r in self.store._rebuild_rows(db,self.f.package['task_id'])))
  self.assertEqual(self.store.dispatch_budget_status(self.p['plan_id'],authority=self.cap('status',{}))['charged_total'],1)

 def test_expected_active_policy_never_falls_back_after_old_snapshot_restore(self):
  from hub.workbench_store import canonical
  cap=self.cap('attempt',self.link())
  with self.store._connection(True)as db:db.execute('UPDATE plan_inputs SET body=?,hash=? WHERE id=?',(canonical(self.p).decode(),digest(self.p),self.p['plan_id']))
  with self.assertRaisesRegex(ServiceError,'DISPATCH_POLICY_REQUIRED'):self.record(cap)

class PreInvocationReservationTests(unittest.TestCase):
 setUp=DispatchBudgetTests.setUp
 cap=DispatchBudgetTests.cap
 initialize=DispatchBudgetTests.initialize
 req=DispatchBudgetTests.req
 reserve=DispatchBudgetTests.reserve
 status=DispatchBudgetTests.status
 close=DispatchBudgetTests.close
 def test_explicit_unknown_native_ids_charge_before_invocation_and_hold_resources(self):
  self.initialize();r=self.req(executor={'kind':'model','thread_id':None,'turn_id':None,'session_id':None},effect='scoped_write');result=self.reserve(r)
  self.assertEqual(result['reserved_model_dispatches'],1);self.assertFalse(result['invoked']);self.assertEqual(result['held_dispatch_ids'],[r['dispatch_id']]);self.assertTrue(self.reserve(r)['replayed'])
  with self.assertRaises(ServiceError):self.reserve(self.req(2,effect='scoped_write'))
  self.close(r,outcome='unknown',resources_closed=False)
  with self.assertRaises(ServiceError):self.reserve(self.req(3,effect='scoped_write'))
  self.assertEqual(self.status()['charged_total'],2) # baseline1 + one reserved call
 def test_known_identity_types_and_explicit_null_shape_are_strict(self):
  self.initialize()
  for field,bad in [('thread_id',0),('thread_id',''),('turn_id',False),('turn_id',[]),('session_id','')]:
   with self.subTest(field=field,bad=bad):
    ex={'kind':'model','thread_id':None,'turn_id':None,'session_id':None};ex[field]=bad
    with self.assertRaises(ServiceError):self.reserve(self.req(executor=ex))
  for missing in ('thread_id','turn_id','session_id'):
   ex={'kind':'model','thread_id':None,'turn_id':None,'session_id':None};del ex[missing]
   with self.subTest(missing=missing),self.assertRaises(ServiceError):self.reserve(self.req(executor=ex))

class ObservedIdentityBindingTests(unittest.TestCase):
 setUp=ActiveAttemptBudgetTests.setUp
 cap=ActiveAttemptBudgetTests.cap
 root=ActiveAttemptBudgetTests.root
 link=ActiveAttemptBudgetTests.link
 record=ActiveAttemptBudgetTests.record
 def reserve_unknown(self,*,known_thread=True,**changes):
  r=copy.deepcopy(self.req);r['executor']={'kind':'model','thread_id':self.f.executor['thread_id']if known_thread else None,'turn_id':None,'session_id':None};r.update(changes)
  self.store.reserve_dispatch(self.p['plan_id'],r,authority=self.cap('reserve',r));return r
 def stored(self):
  with self.store._connection()as db:return split(self.store._decode(*db.execute('SELECT body,hash FROM plan_inputs WHERE id=?',(self.p['plan_id'],)).fetchone()))[1]
 def test_known_thread_unknown_turn_binds_actual_full_executor_exact_replay_once(self):
  r=self.reserve_unknown();before=self.stored();cap=self.cap('attempt',self.link());self.assertFalse(self.record(cap)['replayed']);self.assertTrue(self.record(cap)['replayed']);self.assertEqual(before,self.stored());self.assertEqual(before['reservations'][0]['request'],r)
  with self.store._connection()as db:
   attempts=[e['data']for e in self.store._rebuild_rows(db,self.f.package['task_id'])if e['key'].startswith('rebuild-attempt-')]
  self.assertEqual(len(attempts),1);self.assertEqual(attempts[0]['executor'],self.f.executor)
 def test_new_thread_and_session_can_be_observed_only_through_typed_authority(self):
  self.reserve_unknown(known_thread=False)
  with self.assertRaises(ServiceError):self.record(None)
  self.f.attempt['executor']['session_id']='synthetic-observed-session'
  self.f.attempt['executor']['thread_id']='synthetic-new-observed-thread'
  b=copy.deepcopy(self.f.binding);b['dispatch_id']='native-dispatch';b['executor']=copy.deepcopy(self.f.attempt['executor'])
  root=RootAuthority.configured(b,authorize=self.f.auth,workspace_check=lambda _:copy.deepcopy(self.f.workspace),sandbox_check=self.f.sandbox,writer_check=self.f.writer,budget_authority=self.cap('attempt',self.link()))
  self.assertFalse(self.store.record_rebuild_attempt(self.f.package,self.f.attempt,authority=root)['replayed']);self.assertEqual(len(self.stored()['reservations']),1)
 def test_wrong_preknown_field_kind_task_attempt_package_rejected(self):
  cases=['thread','turn','session','host','pid-type','kind','task','attempt','package']
  for case in cases:
   with self.subTest(case=case):
    f=ObservedIdentityBindingTests();f.setUp()
    try:
     r=copy.deepcopy(f.req);r['executor']={'kind':'model','thread_id':None,'turn_id':None,'session_id':None}
     if case=='thread':r['executor']['thread_id']='different-known-thread'
     if case=='turn':r['executor']['turn_id']='different-known-turn'
     if case=='session':r['executor']['session_id']='different-known-session'
     if case=='host':r['executor']['host_id']='different-known-host'
     if case=='pid-type':r['executor']['pid']=float(f.f.executor['pid'])
     if case=='kind':r['executor']={'kind':'deterministic_host','host_id':'synthetic-host','thread_id':None,'turn_id':None,'session_id':None}
     if case=='task':r['task_id']='different-task'
     if case=='attempt':r['attempt_id']='different-attempt'
     if case=='package':r['package_sha256']='e'*64
     f.store.reserve_dispatch(f.p['plan_id'],r,authority=f.cap('reserve',r))
     with self.assertRaises(ServiceError):f.record(f.cap('attempt',f.link()))
     self.assertEqual(len(f.stored()['reservations']),1)
    finally:f.doCleanups()
 def test_second_observed_binding_conflicts_even_with_new_trusted_link(self):
  self.reserve_unknown(known_thread=False);self.record(self.cap('attempt',self.link()));before=self.stored();other=copy.deepcopy(self.f.attempt);other['executor']['turn_id']='second-real-observed-turn'
  b=copy.deepcopy(self.f.binding);b['dispatch_id']='native-dispatch';b['executor']=copy.deepcopy(other['executor']);link={**self.link(),'executor_sha256':digest(other['executor'])};root=RootAuthority.configured(b,authorize=self.f.auth,workspace_check=lambda _:copy.deepcopy(self.f.workspace),sandbox_check=self.f.sandbox,writer_check=self.f.writer,budget_authority=self.cap('attempt',link))
  with self.assertRaises(ServiceError):self.store.record_rebuild_attempt(self.f.package,other,authority=root)
  self.assertEqual(before,self.stored())
 def test_actual_v2_attempt_still_requires_real_turn_and_trusted_actual_link(self):
  self.reserve_unknown();bad=copy.deepcopy(self.f.attempt);bad['executor']['turn_id']=None
  with self.assertRaises(ServiceError):self.store.record_rebuild_attempt(self.f.package,bad,authority=self.root(cap=self.cap('attempt',self.link())))
  with self.assertRaises(ServiceError):self.record(self.cap('attempt',{**self.link(),'executor_sha256':'f'*64}))
  self.assertEqual(len(self.stored()['reservations']),1)
