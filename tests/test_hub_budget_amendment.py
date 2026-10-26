"""Owner960 is a task-local policy CAS, never an execution grant or counter reset."""
import copy
import hashlib
import json
import shutil
import sqlite3
import threading
import unittest
from unittest import mock
import test_hub_dispatch_budget as fixture
from hub.dispatch_budget import BudgetAuthority,NormalDispatchBinding,split,validate
from hub.plan_admission import PlanAuthority
from hub.service_contract import ServiceError
from hub.task_store import TaskStore
from hub.workbench_store import canonical,digest


class BudgetAmendmentTests(unittest.TestCase):
 setUp=fixture.DispatchBudgetTests.setUp
 cap=fixture.DispatchBudgetTests.cap
 initialize=fixture.DispatchBudgetTests.initialize
 req=fixture.DispatchBudgetTests.req
 reserve=fixture.DispatchBudgetTests.reserve
 close=fixture.DispatchBudgetTests.close
 status=fixture.DispatchBudgetTests.status
 def change(self,**updates):
  body={'amendment_id':'owner-increase','expected_policy_sha256':digest(self.policy),'previous_limit':480,'new_limit':960,
        'owner_decision':{'ref':'synthetic/owner-decision.json','sha256':'e'*64}}
  body.update(updates);return body
 def amend(self,body=None,cap=None):
  body=body or self.change();return self.store.amend_dispatch_budget(self.p['plan_id'],body,authority=cap or self.cap('amend',body))
 def stored(self):
  with self.store._connection()as db:return self.store._decode(*db.execute('SELECT body,hash FROM plan_inputs WHERE id=?',(self.p['plan_id'],)).fetchone())
 def test_exhausted_nonempty_amend_preserves_every_charge_request_closure_and_provenance(self):
  self.initialize(459);r=self.req();self.reserve(r);self.close(r,outcome='unknown')
  with self.assertRaises(ServiceError):self.reserve(self.req(2))
  history,before=split(self.stored());old_policy=copy.deepcopy(self.policy);result=self.amend();self.policy={**self.policy,'limit':960}
  after_history,after=split(self.stored());self.assertEqual(history,after_history)
  self.assertEqual(canonical(before['reservations']),canonical(after['reservations']));self.assertEqual(after['policy'],{**old_policy,'limit':960})
  self.assertEqual(result['charged_total'],460);self.assertEqual(result['limit'],960);self.assertIsNone(result['token_usage'])
  self.assertEqual(len(after['amendments']),1);self.assertEqual(after['amendments'][0]['previous_policy'],old_policy)
  self.reserve(self.req(2));self.assertEqual(self.status()['charged_total'],461)
 def test_replay_has_no_storage_rewrite_conflicts_and_stale_policy_refuse(self):
  self.initialize();body=self.change();cap=self.cap('amend',body);self.amend(body,cap);before=self.store.path.read_bytes()
  self.assertTrue(self.amend(body,cap)['replayed']);self.assertEqual(self.store.path.read_bytes(),before)
  for bad in ({**body,'owner_decision':{'ref':'synthetic/changed','sha256':'f'*64}},self.change(amendment_id='second')):
   with self.assertRaises(ServiceError):self.amend(bad,self.cap('amend',bad))
   self.assertEqual(self.store.path.read_bytes(),before)
 def test_stale_cas_and_request_data_without_exact_internal_authority_never_write(self):
  self.initialize();before=self.store.path.read_bytes();body=self.change(expected_policy_sha256='f'*64)
  with self.assertRaises(ServiceError):self.amend(body)
  body=self.change()
  for cap in (None,{},self.cap('status',{}),self.cap('amend',body,p={**self.policy,'cutoff_id':'other'})):
   with self.assertRaises(ServiceError):self.store.amend_dispatch_budget(self.p['plan_id'],body,authority=cap)
  self.assertEqual(before,self.store.path.read_bytes())
 def test_fresh_960_baseline_decrease_other_limit_and_malformed_proof_refuse(self):
  baseline={**self.policy,'limit':960}
  with self.assertRaises(ServiceError):self.store.initialize_dispatch_budget(self.p['plan_id'],baseline,authority=self.cap('baseline',baseline,p=baseline))
  self.initialize();before=self.store.path.read_bytes()
  for changes in ({'new_limit':480},{'new_limit':959},{'new_limit':961},{'previous_limit':960},{'new_limit':True},
                  {'owner_decision':{'ref':'synthetic/proof','sha256':'unknown'}},{'owner_decision':{'ref':'','sha256':'e'*64}}):
   body=self.change(**changes)
   with self.subTest(changes=changes),self.assertRaises(ServiceError):self.amend(body)
   self.assertEqual(before,self.store.path.read_bytes())
 def test_paused_unknown_cancelled_and_control_drift_rollback(self):
  self.initialize();before=self.store.path.read_bytes()
  for state in ('paused','cancelled','unknown'):
   self.control['state']=state
   with self.subTest(state=state),self.assertRaises(ServiceError):self.amend()
   self.assertEqual(before,self.store.path.read_bytes())
  self.control['state']='active'
  def fault(phase):
   if phase=='before_dispatch_budget_commit':self.control['revision']+=1
  self.store.fault=fault
  with self.assertRaises(ServiceError):self.amend()
  self.assertEqual(before,self.store.path.read_bytes())
 def test_commit_fault_rollback_keeps_480_and_all_accounting(self):
  self.initialize();r=self.req();self.reserve(r);before=self.store.path.read_bytes()
  def fault(phase):
   if phase=='before_dispatch_budget_commit':raise OSError('synthetic storage failure')
  self.store.fault=fault
  with self.assertRaises(OSError):self.amend()
  self.assertEqual(before,self.store.path.read_bytes());self.store.fault=lambda _:None
  self.assertEqual(self.status()['limit'],480);self.assertEqual(self.status()['charged_total'],2)
 def test_concurrent_same_cas_commits_once_and_replays_exact_receipt(self):
  self.initialize();body=self.change();cap=self.cap('amend',body);gate=threading.Barrier(2);out=[]
  def worker():
   try:gate.wait();out.append(self.amend(body,cap))
   except ServiceError as error:out.append(error.code)
  ts=[threading.Thread(target=worker)for _ in range(2)]
  for t in ts:t.start()
  for t in ts:t.join(10);self.assertFalse(t.is_alive())
  self.assertEqual(len([r for r in out if isinstance(r,dict)and not r['replayed']]),1)
  self.assertEqual(len(split(self.stored())[1]['amendments']),1)
  # A serialized exact request is either the replay or a bounded busy refusal.
  self.assertTrue(all(isinstance(r,dict)or r in ('TASK_STORE_BUSY','TASK_BUSY')for r in out),out)
 def test_unknown_resource_hold_and_unamended_policy_cap_remain_effective(self):
  self.initialize();r=self.req(effect='scoped_write');self.reserve(r);self.close(r,outcome='unknown',resources_closed=False)
  old=copy.deepcopy(self.policy);self.amend();self.policy={**self.policy,'limit':960}
  new_request=self.req(2)
  with self.assertRaises(ServiceError):self.store.reserve_dispatch(self.p['plan_id'],new_request,authority=self.cap('reserve',new_request,p=old))
  with self.assertRaises(ServiceError):self.reserve(self.req(3,effect='scoped_write'))
  self.assertEqual(self.status()['held_dispatch_ids'],[r['dispatch_id']]);self.assertEqual(self.status()['charged_total'],2)
 def test_960_requires_exact_480_derivation_and_receipt_in_offline_validation(self):
  self.initialize();self.amend();budget=split(self.stored())[1]
  for mutation in ('no-receipt','base-reset','new-policy','proof-hash','old-limit','duplicate'):
   bad=copy.deepcopy(budget)
   if mutation=='no-receipt':bad.pop('amendments')
   elif mutation=='base-reset':bad['policy']['observed_model_dispatches']=0
   elif mutation=='new-policy':bad['amendments'][0]['new_policy_sha256']='f'*64
   elif mutation=='proof-hash':bad['amendments'][0]['request']['owner_decision']['sha256']='f'*64
   elif mutation=='old-limit':bad['amendments'][0]['previous_policy']['limit']=960
   else:bad['amendments']*=2
   with self.subTest(mutation=mutation),self.assertRaises(ServiceError):validate(bad,self.p['plan_id'])
 def test_new_normal_capacity_ceiling_preserves_closure_allowance(self):
  self.initialize(460);self.amend();self.policy={**self.policy,'limit':960}
  # Accounting-only synthetic reservations are individually closed, not real calls.
  from hub.dispatch_budget import status
  budget=split(self.stored())[1];now=__import__('time').time()
  budget['reservations']=[{'request':self.req(n,executor={'kind':'deterministic_host','host_id':'synthetic-host','thread_id':None,'turn_id':None},deadline=now+100),'sha256':None,'reserved_at':now,'closures':[]}for n in range(480)]
  for row in budget['reservations']:row['sha256']=digest(row['request'])
  validate(budget,self.p['plan_id']);self.assertEqual(status(budget)['charged_total'],940)
  row=copy.deepcopy(budget['reservations'][0]);row['request']['dispatch_id']='one-too-many';row['sha256']=digest(row['request']);budget['reservations'].append(row)
  with self.assertRaises(ServiceError):validate(budget,self.p['plan_id'])
 def test_plan_revision_rollback_return_and_cold_restore_keep_amendment(self):
  self.initialize();r=self.req();self.reserve(r);self.close(r);self.amend();self.policy={**self.policy,'limit':960};before=split(self.stored())[1]
  original=copy.deepcopy(self.p);target=copy.deepcopy(original);target['revision']='changed';target['tasks'][1]['objective']='new definition'
  for next_plan,prior,ident,rollback in ((target,original,'change',False),(original,target,'rollback',True),(target,original,'return',True)):
   pc=PlanAuthority.configured({'plan_sha256':digest(next_plan),'reconcile_from_sha256':digest(prior)},authorize=lambda:None,controls=lambda:self.control,routes=lambda _:True,package_for=lambda _:None)
   self.store.reconcile_plan(next_plan,expected_sha256=digest(prior),request_id=ident,authority=pc,rollback=rollback);self.p=next_plan;self.plan_cap=pc
   self.assertEqual(split(self.stored())[1],before)
  from hub.task_storage_restore import _snapshot
  _snapshot(self.store,self.store.path.read_bytes());root=self.fixture.root.parent/'cold-amended';root.mkdir(mode=0o700);cold=TaskStore(root);cold.path.parent.mkdir(mode=0o700,parents=True)
  shutil.copy2(self.store.path,cold.path);shutil.copy2(self.store.registration_path,cold.registration_path);sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
  cold.restore_storage(database_sha256=sha(cold.path),registration_sha256=sha(cold.registration_path),confirm_offline=True)
  self.assertEqual(cold.dispatch_budget_status(self.p['plan_id'],authority=self.cap('status',{})),self.status())
 def test_source_evidence_wrapper_unchanged_by_amendment(self):
  from test_hub_source_admission import SourceAdmissionTests
  f=SourceAdmissionTests();f.setUp();self.addCleanup(f.doCleanups);f.record(f.candidate(status='unknown'))
  self.store=f.store;self.p=f.p;self.plan_cap=f.cap;self.policy={**self.policy,'lineage_id':self.p['plan_id']}
  self.initialize();before=canonical(split(self.stored())[0]);self.amend();self.assertEqual(canonical(split(self.stored())[0]),before)
  self.assertEqual(f.view()['ready'],[])
 def test_existing_normal_intent_requires_new_exact_policy_authority_not_wider_permission(self):
  from test_hub_normal_dispatch_budget import NormalDispatchBudgetTests
  f=NormalDispatchBudgetTests();f.setUp();self.addCleanup(f.doCleanups);f.admit();old=f.binding;intent=copy.deepcopy(f.store.task(f.f.spec['request_id'])['intent'])
  change={'amendment_id':'normal-owner-increase','expected_policy_sha256':digest(f.policy),'previous_limit':480,'new_limit':960,'owner_decision':{'ref':'synthetic/owner','sha256':'e'*64}}
  f.store.amend_dispatch_budget(f.plan['plan_id'],change,authority=f.cap('amend',change));f.policy={**f.policy,'limit':960}
  with self.assertRaises(ServiceError):f.store.reserve_dispatch(f.plan['plan_id'],f.req,authority=old._authority)
  f.binding=NormalDispatchBinding.configured(lineage_id=f.plan['plan_id'],request_value=f.req,grant=f.f.grant,authority_value=f.cap('reserve',f.req));f.bindings={f.f.spec['request_id']:f.binding}
  self.assertEqual(f.binding.marker(f.f.spec['request_id'],f.f.grant),old.marker(f.f.spec['request_id'],f.f.grant));f.worker().run_one()
  self.assertEqual(f.calls,['open','start']);self.assertEqual(f.status()['charged_total'],1)
  self.assertEqual(f.store.task(f.f.spec['request_id'])['intent'],intent)


class ContinuousBudgetAmendmentTests(unittest.TestCase):
 """Each synthetic owner decision authorizes exactly one next policy CAS."""
 setUp=BudgetAmendmentTests.setUp
 cap=BudgetAmendmentTests.cap
 initialize=BudgetAmendmentTests.initialize
 req=BudgetAmendmentTests.req
 reserve=BudgetAmendmentTests.reserve
 close=BudgetAmendmentTests.close
 status=BudgetAmendmentTests.status
 change=BudgetAmendmentTests.change
 amend=BudgetAmendmentTests.amend
 stored=BudgetAmendmentTests.stored
 def next_change(self,**updates):
  previous=self.policy['limit'];target=previous*2
  body=self.change(amendment_id='owner-limit-'+str(target),previous_limit=previous,new_limit=target,
      owner_decision={'ref':'synthetic/owner-'+str(target),'sha256':digest({'owner_limit':target})})
  body.update(updates);return body
 def advance(self):
  body=self.next_change();cap=self.cap('amend',body);result=self.amend(body,cap)
  self.policy={**self.policy,'limit':body['new_limit']}
  self.assertEqual(result['limit'],self.policy['limit'])
  return body,cap
 def at_1920(self):
  self.initialize();return [self.advance(),self.advance()]
 def test_two_independent_increases_keep_nonempty_unknown_failures_and_all_old_bytes(self):
  self.initialize(459);held=self.req(effect='scoped_write');self.reserve(held)
  self.close(held,outcome='unknown',resources_closed=False)
  with self.assertRaises(ServiceError):self.reserve(self.req(2))
  history,old=split(self.stored());old=copy.deepcopy(old)
  first,first_cap=self.advance();_,middle=split(self.stored())
  self.assertEqual(canonical(middle['reservations']),canonical(old['reservations']))
  failed=self.req(2);self.reserve(failed);self.close(failed,outcome='failed')
  prior=copy.deepcopy(split(self.stored())[1]);second,second_cap=self.advance()
  after_history,after=split(self.stored());self.assertEqual(canonical(history),canonical(after_history))
  self.assertEqual(canonical(after['reservations']),canonical(prior['reservations']))
  self.assertEqual(canonical(after['amendments'][:1]),canonical(prior['amendments']))
  for receipt,(body,cap)in zip(after['amendments'],((first,first_cap),(second,second_cap))):
   self.assertEqual(receipt['request'],body)
   self.assertEqual(receipt['authorized_binding_sha256'],digest(cap.snapshot()))
  self.assertEqual(after['policy'],{**old['policy'],'limit':1920})
  self.assertEqual(self.status()['charged_total'],461)
  self.assertEqual(self.status()['held_dispatch_ids'],[held['dispatch_id']])
  self.assertEqual(after['reservations'][0]['closures'][0]['outcome'],'unknown')
  self.assertEqual(after['reservations'][1]['closures'][0]['outcome'],'failed')
  with self.assertRaises(ServiceError):self.reserve(self.req(3,effect='scoped_write'))
  self.reserve(self.req(4));self.assertEqual(self.status()['charged_total'],462)
  self.assertIsNone(self.status()['cost']);self.assertIsNone(self.status()['token_usage'])
 def test_both_older_receipts_replay_at_1920_without_storage_or_authority_rebinding(self):
  commands=self.at_1920();before=self.store.path.read_bytes()
  receipts=split(self.stored())[1]['amendments']
  for index,(body,cap)in enumerate(commands):
   self.assertTrue(self.amend(body,cap)['replayed'])
   self.assertEqual(self.store.path.read_bytes(),before)
   with self.assertRaises(ServiceError):self.amend(body,self.cap('amend',body))
   changed={**body,'owner_decision':{'ref':'synthetic/replaced','sha256':'f'*64}}
   with self.assertRaises(ServiceError)as error:self.amend(changed,cap)
   self.assertEqual(error.exception.code,'DISPATCH_AMENDMENT_CONFLICT')
   another={**body,'amendment_id':body['amendment_id']+'-different'}
   with self.assertRaises(ServiceError):self.amend(another,self.cap('amend',another,p=receipts[index]['previous_policy']))
   self.assertEqual(self.store.path.read_bytes(),before)
 def test_structurally_finite_doubling_never_allows_fresh_or_arbitrary_budget(self):
  from hub.dispatch_budget import policy,amendment
  before=self.store.path.read_bytes()
  for limit in (960,1920):
   baseline={**self.policy,'limit':limit}
   with self.subTest(fresh=limit),self.assertRaises(ServiceError):
    self.store.initialize_dispatch_budget(self.p['plan_id'],baseline,authority=self.cap('baseline',baseline,p=baseline))
   self.assertEqual(self.store.path.read_bytes(),before)
  self.initialize();self.advance();before=self.store.path.read_bytes()
  for updates in ({'previous_limit':True},{'new_limit':True},{'previous_limit':960.0},
      {'new_limit':1920.0},{'new_limit':480},{'new_limit':959},{'new_limit':961},
      {'new_limit':1440},{'new_limit':3840},{'previous_limit':720,'new_limit':1440},
      {'new_limit':float('inf')},{'expected_policy_sha256':'f'*64}):
   body=self.next_change(**updates)
   cap=self.cap('status',{})if updates.get('new_limit')==float('inf')else self.cap('amend',body)
   with self.subTest(updates=updates),self.assertRaises(ServiceError):self.amend(body,cap)
   self.assertEqual(self.store.path.read_bytes(),before)
  for limit in (True,0,1440,1921,480*2**45):
   with self.subTest(limit=limit),self.assertRaises(ServiceError):policy({**self.policy,'limit':limit},amended=True)
  with self.assertRaises(ServiceError):amendment(self.next_change(owner_decision={'ref':'','sha256':'f'*64}))
  self.advance();self.advance() # Later explicit decisions use the same API.
  self.assertEqual(self.status()['limit'],3840)
  self.assertEqual(len(split(self.stored())[1]['amendments']),3)
 def test_full_chain_rejects_missing_reordered_forged_and_reset_history(self):
  self.at_1920();budget=split(self.stored())[1]
  mutations=(lambda b:b.pop('amendments'),lambda b:b['amendments'].pop(0),
      lambda b:b['amendments'].pop(),lambda b:b['amendments'].reverse(),
      lambda b:b['amendments'].append(copy.deepcopy(b['amendments'][0])),
      lambda b:b['policy'].update(observed_model_dispatches=0),
      lambda b:b['policy'].update(cutoff_id='reset-cutoff'),
      lambda b:b['amendments'][1]['previous_policy'].update(observed_model_dispatches=0),
      lambda b:b['amendments'][1]['request'].update(expected_policy_sha256='f'*64),
      lambda b:b['amendments'][1]['request'].update(previous_limit=480),
      lambda b:b['amendments'][1].update(sha256='f'*64),
      lambda b:b['amendments'][1].update(new_policy_sha256='f'*64),
      lambda b:b['amendments'][1].update(authorized_binding_sha256=None),
      lambda b:b['amendments'][1].update(applied_at=float('inf')),
      lambda b:b['amendments'][1]['request']['owner_decision'].update(sha256='f'*64),
      lambda b:b['amendments'][1]['request'].update(amendment_id=b['amendments'][0]['request']['amendment_id']))
  before=self.store.path.read_bytes()
  for n,mutate in enumerate(mutations):
   bad=copy.deepcopy(budget);mutate(bad)
   with self.subTest(mutation=n),self.assertRaises(ServiceError):validate(bad,self.p['plan_id'])
  self.assertEqual(self.store.path.read_bytes(),before)
  from hub.task_storage_restore import _snapshot
  with sqlite3.connect(':memory:')as db:
   db.deserialize(before);value=json.loads(db.execute('SELECT body FROM plan_inputs').fetchone()[0])
   value['dispatch_budget']['amendments'].pop(0)
   db.execute('UPDATE plan_inputs SET body=?,hash=?',(canonical(value).decode(),digest(value)));db.commit()
   with self.assertRaises(ServiceError):_snapshot(self.store,db.serialize())
 def test_second_cas_concurrent_same_request_has_one_receipt_and_exact_replay(self):
  self.initialize();self.advance();body=self.next_change();cap=self.cap('amend',body)
  gate=threading.Barrier(2);out=[]
  def worker():
   try:gate.wait();out.append(self.amend(body,cap))
   except ServiceError as error:out.append(error.code)
  threads=[threading.Thread(target=worker)for _ in range(2)]
  for t in threads:t.start()
  for t in threads:t.join(10);self.assertFalse(t.is_alive())
  self.assertEqual(sum(isinstance(r,dict)and not r['replayed']for r in out),1)
  self.assertTrue(all(isinstance(r,dict)or r in ('TASK_STORE_BUSY','TASK_BUSY')for r in out),out)
  self.policy={**self.policy,'limit':1920};before=self.store.path.read_bytes()
  self.assertTrue(self.amend(body,cap)['replayed']);self.assertEqual(self.store.path.read_bytes(),before)
  self.assertEqual(len(split(self.stored())[1]['amendments']),2)
 def test_second_cas_different_requests_cannot_both_spend_the_same_old_policy(self):
  self.initialize();self.advance();bodies=[self.next_change(amendment_id='owner-choice-'+str(n))for n in range(2)]
  caps=[self.cap('amend',b)for b in bodies];gate=threading.Barrier(2);out=[]
  def worker(n):
   try:gate.wait();out.append(self.amend(bodies[n],caps[n]))
   except ServiceError as error:out.append(error.code)
  threads=[threading.Thread(target=worker,args=(n,))for n in range(2)]
  for t in threads:t.start()
  for t in threads:t.join(10);self.assertFalse(t.is_alive())
  self.assertEqual(sum(isinstance(r,dict)for r in out),1,out)
  self.assertTrue(all(isinstance(r,dict)or r in ('DISPATCH_AUTHORITY_MISMATCH','DISPATCH_AMENDMENT_STALE','TASK_STORE_BUSY','TASK_BUSY')for r in out),out)
  self.assertEqual(len(split(self.stored())[1]['amendments']),2)
 def test_second_increase_control_and_commit_faults_rollback_to_exact_960(self):
  self.initialize();self.advance();r=self.req();self.reserve(r);self.close(r,outcome='unknown')
  body=self.next_change();before=self.store.path.read_bytes()
  for state in ('paused','cancelled','unknown'):
   self.control['state']=state
   with self.subTest(state=state),self.assertRaises(ServiceError):self.amend(body)
   self.assertEqual(self.store.path.read_bytes(),before)
  self.control['state']='active'
  for failure in ('control','storage'):
   def fault(phase):
    if phase=='before_dispatch_budget_commit':
     if failure=='control':self.control['revision']+=1
     else:raise OSError('synthetic second amendment commit failure')
   self.store.fault=fault
   with self.subTest(failure=failure),self.assertRaises(ServiceError if failure=='control'else OSError):self.amend(body)
   self.assertEqual(self.store.path.read_bytes(),before)
  self.store.fault=lambda _:None
  with self.assertRaises(ServiceError):self.amend(body,self.cap('status',{}))
  self.assertEqual(self.status()['limit'],960);self.assertEqual(self.status()['charged_total'],2)
 def test_1920_normal_capacity_still_reserves_twenty_and_twelve_closure_slots(self):
  from hub.dispatch_budget import wrap
  self.initialize(460);self.advance();self.advance();history,budget=split(self.stored())
  now=__import__('time').time()
  # Bounded accounting-only disposable ledger at the last normal slot. These
  # deterministic fixtures are not real invocations or resource-close proof.
  budget['reservations']=[{'request':self.req(n,executor={'kind':'deterministic_host','host_id':'synthetic-host','thread_id':None,'turn_id':None},deadline=now+100),
      'sha256':None,'reserved_at':now,'closures':[]}for n in range(1440)]
  for row in budget['reservations']:row['sha256']=digest(row['request'])
  validate(budget,self.p['plan_id'])
  with self.store._connection(True)as db:self.store._replace_plan_input(db,self.p['plan_id'],wrap(history,budget))
  before=self.store.path.read_bytes()
  with self.assertRaises(ServiceError):self.reserve(self.req('normal-over-cap'))
  self.assertEqual(self.store.path.read_bytes(),before);self.assertEqual(self.status()['charged_total'],1900)
  for n in range(12):
   r=self.req('closure-'+str(n),purpose='closure',executor={'kind':'deterministic_host','host_id':'synthetic-host','thread_id':None,'turn_id':None})
   self.reserve(r)
  with self.assertRaises(ServiceError):self.reserve(self.req('closure-over-cap',purpose='closure'))
  self.assertEqual(self.status()['charged_total'],1912)
  self.assertEqual(self.status()['historical_unknown_allowance'],8)
  self.assertEqual(self.status()['closure_reserve'],12)
 def test_source_unknown_plan_rollback_and_cold_restore_keep_both_receipts(self):
  from test_hub_source_admission import SourceAdmissionTests
  f=SourceAdmissionTests();f.setUp();self.addCleanup(f.doCleanups);f.record(f.candidate(status='unknown'))
  self.store=f.store;self.p=f.p;self.plan_cap=f.cap;self.policy={**self.policy,'lineage_id':self.p['plan_id']}
  self.initialize();source_before=canonical(split(self.stored())[0]);self.advance();self.advance()
  self.assertEqual(canonical(split(self.stored())[0]),source_before);self.assertEqual(f.view()['ready'],[])
  before=copy.deepcopy(split(self.stored())[1]);original=copy.deepcopy(self.p);target=copy.deepcopy(original)
  target['revision']='1.2.0-amended-plan';target['tasks'][1]['objective']='synthetic next definition'
  for value,prior,name,rollback in ((target,original,'new-plan',False),(original,target,'old-plan',True)):
   pc=PlanAuthority.configured({'plan_sha256':digest(value),'reconcile_from_sha256':digest(prior)},authorize=lambda:None,controls=lambda:self.control,routes=lambda _:False,package_for=lambda _:None)
   self.store.reconcile_plan(value,expected_sha256=digest(prior),request_id=name,authority=pc,rollback=rollback)
   self.p=value;self.plan_cap=pc;self.assertEqual(split(self.stored())[1],before)
  from hub.task_storage_restore import _snapshot
  _snapshot(self.store,self.store.path.read_bytes());root=self.store.root.parent/'cold-double-amendment'
  root.mkdir(mode=0o700);cold=TaskStore(root);cold.path.parent.mkdir(mode=0o700,parents=True)
  shutil.copy2(self.store.path,cold.path);shutil.copy2(self.store.registration_path,cold.registration_path)
  sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
  cold.restore_storage(database_sha256=sha(cold.path),registration_sha256=sha(cold.registration_path),confirm_offline=True)
  self.assertEqual(cold.dispatch_budget_status(self.p['plan_id'],authority=self.cap('status',{})),self.status())
  with cold._connection()as db:raw=cold._decode(*db.execute('SELECT body,hash FROM plan_inputs').fetchone())
  self.assertEqual(split(raw)[1],before)
  from hub.plan_admission import plan_history
  plan_history(raw)
  self.assertEqual(canonical(split(raw)[0]['records']),canonical(f.view()['source_records']))
 def test_1920_requires_new_exact_normal_authority_without_changing_grant_or_intent(self):
  from test_hub_normal_dispatch_budget import NormalDispatchBudgetTests
  f=NormalDispatchBudgetTests();f.setUp();self.addCleanup(f.doCleanups);f.admit()
  original=f.binding;old_grant=copy.deepcopy(f.f.grant);intent=copy.deepcopy(f.store.task(f.f.spec['request_id'])['intent'])
  stale=[original._authority]
  for previous in (480,960):
   body={'amendment_id':'normal-owner-'+str(previous*2),'expected_policy_sha256':digest(f.policy),'previous_limit':previous,'new_limit':previous*2,
         'owner_decision':{'ref':'synthetic/owner-'+str(previous*2),'sha256':digest({'owner_limit':previous*2})}}
   f.store.amend_dispatch_budget(f.plan['plan_id'],body,authority=f.cap('amend',body))
   f.policy={**f.policy,'limit':previous*2}
   if previous==480:stale.append(f.cap('reserve',f.req))
  before=f.store.path.read_bytes()
  for cap in stale:
   with self.assertRaises(ServiceError):f.store.reserve_dispatch(f.plan['plan_id'],f.req,authority=cap)
   self.assertEqual(f.store.path.read_bytes(),before)
  self.assertEqual(f.calls,[])
  f.binding=NormalDispatchBinding.configured(lineage_id=f.plan['plan_id'],request_value=f.req,grant=f.f.grant,authority_value=f.cap('reserve',f.req))
  f.bindings={f.f.spec['request_id']:f.binding}
  self.assertEqual(f.binding.marker(f.f.spec['request_id'],f.f.grant),original.marker(f.f.spec['request_id'],f.f.grant))
  worker=f.worker();worker.run_one();self.assertFalse(worker.run_one())
  self.assertEqual(f.calls,['open','start']);self.assertEqual(f.status()['charged_total'],1)
  self.assertEqual(f.f.grant,old_grant);self.assertEqual(f.store.task(f.f.spec['request_id'])['intent'],intent)
