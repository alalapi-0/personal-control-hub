"""Historical bootstrap witnesses do not create retrospective contract tasks."""
import copy
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from unittest import mock
from hub.plan_admission import PlanAuthority,BOOTSTRAP_IDS
from hub.rebuild_contracts import RootAuthority
from hub.workbench_store import digest,canonical
from hub.service_contract import ServiceError
import test_hub_plan_admission as plans

BASE=plans.BASE
CHILD='P2-R2-PCH-BUDGET'
class BootstrapDependencyTests(unittest.TestCase):
 def setUp(self):
  self.fixture=plans.ContractPlanIntegrationTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
  self.f=self.fixture.f;self.store=self.f.store;self.control={'state':'active','revision':1};self.records={};self.version_records={};self.packages={}
  self.p=plans.plan([self.fixture.definition(BASE),self.fixture.definition(CHILD),self.fixture.definition(plans.OFFLINE)]);self.p['tasks'][1]['depends_on']=[BASE];self.p['tasks'][2]['depends_on']=[BASE]
  self.configure_plan();self.store.import_plan(self.p,authority=self.cap);self.record(BASE);self.make_package()
 def configure_plan(self,prior=None):
  b={'plan_sha256':digest(self.p),'reconcile_from_sha256':prior,'verified_evidence':{k:digest(v)for k,v in self.records.items()},'verified_evidence_versions':copy.deepcopy(self.version_records)}
  self.cap=PlanAuthority.configured(b,authorize=lambda:None,controls=lambda:copy.deepcopy(self.control),routes=lambda _:True,package_for=lambda ident:self.packages.get(ident))
 def record(self,ident,**changes):
  r={'schema_version':'1.0','plan_sha256':digest(self.p),'task_id':ident,'attempt_id':'original-attempt','receipt_id':'original-receipt','receipt_sha256':digest({'task_id':ident,'original_receipt':True,'version':self.p['revision']}),'evidence_ref':'synthetic/bootstrap.json','evidence_sha256':'c'*64,'effect_state':'read_only','unknowns':[],'origin':'bootstrap','provenance':{'source':'root_verified'},'native_thread_id':'synthetic-native-thread','session_id':None}
  r.update(changes);self.records[ident]=r;self.version_records.setdefault(ident,{})[r['plan_sha256']]=digest(r);self.configure_plan();self.store.record_plan_evidence(self.p['plan_id'],r,authority=self.cap);return r
 def make_package(self,dependency=BASE):
  f=self.f;p=copy.deepcopy(f.package);p['task_id']=CHILD;p['lineage']={'goal_id':self.p['plan_id'],'original_task_id':CHILD};p['phase_id']='P2';p['round_id']='P2-R1';p['plan']={'revision':self.p['revision'],'sha256':digest(self.p)};p['dependencies']=[{'task_id':dependency,'receipt_sha256':self.records[dependency]['receipt_sha256']}];f.package=p;f.configure();self.configure_root()
 def configure_root(self):
  f=self.f;f.configure()
  self.authority=RootAuthority.configured(f.binding,authorize=f.auth,workspace_check=lambda _:copy.deepcopy(f.observed_workspace),sandbox_check=f.sandbox,writer_check=f.writer,plan_authority=self.cap)
  from hub.task_service import TaskService
  f.authority=self.authority;f.service=TaskService(None,None,self.store,rebuild_authority=self.authority);self.packages[CHILD]=(f.package,self.authority)
 def test_current_bootstrap_can_supply_admission(self):
  self.assertIsNone(self.store.task(BASE));self.assertIn(BASE,self.store.plan_ready(self.p['plan_id'],authority=self.cap)['accepted'])
  result=self.store.admit_plan_window(self.p['plan_id'],authority=self.cap)
  self.assertEqual(result['admitted'],[CHILD]);self.assertIsNone(self.store.task(BASE))

 def check(self):
  with self.store._connection(True)as db:self.store._rebuild_dependencies(db,self.f.package,self.authority)
 def revise(self,mutate):
  old=digest(self.p);p=copy.deepcopy(self.p);p['revision']=str(int(p['revision'])+1);mutate(p);self.p=p;self.configure_plan(old);self.store.reconcile_plan(p,expected_sha256=old,request_id='revision-'+p['revision'],authority=self.cap)
 def test_default_no_capability_or_serialized_allowlist_and_wrong_type_deny(self):
  f=self.f;b=copy.deepcopy(f.binding);b['verified_dependencies']={self.records[BASE]['receipt_sha256']:self.records[BASE]}
  cap=RootAuthority.configured(b,authorize=f.auth,workspace_check=lambda _:copy.deepcopy(f.workspace),sandbox_check=f.sandbox,writer_check=f.writer)
  self.assertIsNone(cap._plan)
  with self.store._connection(True)as db:
   with self.assertRaisesRegex(ServiceError,'REBUILD_DEPENDENCY'):self.store._rebuild_dependencies(db,f.package,cap)
  with self.assertRaises(ServiceError):RootAuthority.configured(b,authorize=f.auth,sandbox_check=f.sandbox,writer_check=f.writer,plan_authority=self.cap.snapshot())
  self.assertIsNone(self.store.task(CHILD))
 def test_same_connection_and_immutable_authority(self):
  from dataclasses import FrozenInstanceError
  with self.store._connection(True)as db:
   with mock.patch.object(self.store,'_connection',side_effect=AssertionError('nested SQLite reader')):self.store._rebuild_dependencies(db,self.f.package,self.authority)
  snapshot=self.cap.snapshot();snapshot['verified_evidence'].clear();snapshot['verified_evidence_versions'].clear();self.check()
  with self.assertRaises(FrozenInstanceError):self.authority._plan=None
 def test_forged_receipt_plan_membership_and_lineage_reject(self):
  for mutation in ('receipt','plan_hash','revision','lineage','undeclared','project'):
   with self.subTest(mutation=mutation):
    original=copy.deepcopy(self.f.package);p=self.f.package
    if mutation=='receipt':p['dependencies'][0]['receipt_sha256']='f'*64
    elif mutation=='plan_hash':p['plan']['sha256']='f'*64
    elif mutation=='revision':p['plan']['revision']='other'
    elif mutation=='lineage':p['lineage']['goal_id']='different-plan'
    elif mutation=='project':p['project_id']='different-project'
    else:p['dependencies'].append({'task_id':plans.OFFLINE,'receipt_sha256':'b'*64})
    self.configure_root()
    with self.assertRaises(ServiceError):self.check()
    self.f.package=original;self.configure_root()
 def test_unknown_conflict_and_stale_record_hash_reject(self):
  self.record(BASE,attempt_id='conflicting-attempt');self.configure_root()
  with self.assertRaises(ServiceError):self.check()
 def test_verified_but_unknown_and_tampered_records_reject(self):
  record=copy.deepcopy(self.records[BASE]);record['effect_state']='unknown';record['unknowns']=['unresolved'];self.records[BASE]=record;self.version_records[BASE][digest(self.p)]=digest(record);self.configure_plan();self.configure_root()
  with self.store._connection(True)as db:db.execute('UPDATE plan_receipts SET body=?,hash=? WHERE task_id=?',(canonical(record).decode(),digest(record),BASE))
  with self.assertRaises(ServiceError):self.check()
  record['effect_state']='read_only';record['unknowns']=[]
  with self.store._connection(True)as db:db.execute('UPDATE plan_receipts SET body=?,hash=? WHERE task_id=?',(canonical(record).decode(),digest(record),BASE))
  with self.assertRaises(ServiceError):self.check()
 def test_pause_cancel_and_missing_record_verification_reject(self):
  for state in ('paused','cancelled','unknown'):
   self.control['state']=state
   with self.subTest(state=state),self.assertRaises(ServiceError):self.check()
  self.control['state']='active';b=self.cap.snapshot();b['verified_evidence']={};b['verified_evidence_versions']={}
  self.cap=PlanAuthority.configured(b,authorize=lambda:None,controls=lambda:self.control,routes=lambda _:True,package_for=lambda _:None);self.configure_root()
  with self.assertRaises(ServiceError):self.check()
 def test_existing_real_or_archived_task_never_uses_bootstrap_fallback(self):
  self.store.admit({'request_id':BASE,'project_id':'synthetic','parent_task_id':None},{'synthetic':True})
  for status,cancel in [('queued',0),('failed',0),('cancelled',1),('checks_complete',0)]:
   with self.store._connection(True)as db:db.execute('UPDATE tasks SET status=?,cancel=? WHERE id=?',(status,cancel,BASE))
   with self.subTest(status=status),mock.patch.object(self.store,'_rebuild_bootstrap_dependency',side_effect=AssertionError('fallback bypassed actual row')):
    with self.assertRaises(ServiceError):self.check()
 def test_unchanged_old_definition_is_applicable_but_requires_original_mapping(self):
  self.revise(lambda p:p['tasks'][2].update(objective='unrelated revision'));self.make_package();self.check()
  b=self.cap.snapshot();b['verified_evidence']={};b['verified_evidence_versions']={}
  self.cap=PlanAuthority.configured(b,authorize=lambda:None,controls=lambda:self.control,routes=lambda _:True,package_for=lambda _:None);self.configure_root()
  with self.assertRaises(ServiceError):self.check()
 def test_changed_old_definition_rejected_then_exact_new_witness_required(self):
  old_receipt=self.records[BASE]['receipt_sha256'];self.revise(lambda p:p['tasks'][0].update(objective='changed BASE definition'));self.make_package()
  with self.assertRaises(ServiceError):self.check()
  self.record(BASE);self.make_package();self.check();self.f.package['dependencies'][0]['receipt_sha256']=old_receipt;self.configure_root()
  with self.assertRaises(ServiceError):self.check()
 def test_transitive_accepted_state_is_required(self):
  self.revise(lambda p:p['tasks'][1].update(depends_on=[plans.OFFLINE]));self.record(plans.OFFLINE);self.make_package(plans.OFFLINE);self.check()
  self.record(BASE);self.record(BASE,attempt_id='conflict');self.configure_root()
  with self.assertRaises(ServiceError):self.check()
 def test_stale_plan_or_control_after_admission_rejected_at_attempt(self):
  self.f.authority=self.authority
  # start() reaches record_rebuild_attempt through the real service API.
  self.f.start();self.control['state']='paused'
  with self.assertRaises(ServiceError):self.f.admit()
  self.control['state']='active';self.check()
  self.revise(lambda p:p['tasks'][0].update(objective='changed after admission'))
  with self.assertRaises(ServiceError):self.f.admit()
 def test_known_bad_dependency_cannot_create_task_on_direct_prepare(self):
  self.f.package['dependencies'][0]['receipt_sha256']='f'*64;self.configure_root()
  with self.assertRaises(ServiceError):self.store.prepare_rebuild(self.f.package,authority=self.authority)
  self.assertIsNone(self.store.task(CHILD))

 def test_existing_contract_origin_positive_keeps_full_receipt_grant_checks(self):
  child=copy.deepcopy(self.f.package);parent=copy.deepcopy(child);parent['task_id']=BASE;parent['lineage']['original_task_id']=BASE;parent['phase_id']='P1';parent['round_id']='P1-R1';parent['dependencies']=[];self.f.package=parent;self.f.configure();self.f.start();receipt=self.f.receipt();self.f.record(receipt);proof=self.f.proof(receipt)
  self.store.update(BASE,'synthetic-worker',status='validating');self.store.update(BASE,'synthetic-worker',status='checks_complete')
  child['dependencies'][0]['receipt_sha256']=digest(receipt);self.f.package=child;self.f.configure(proofs={digest(receipt):proof})
  with mock.patch.object(self.store,'_rebuild_bootstrap_dependency',side_effect=AssertionError('contract-origin fallback')):
   with self.store._connection(True)as db:self.store._rebuild_dependencies(db,self.f.package,self.f.authority)
   self.f.prepare()
   self.f.configure()
   with self.store._connection(True)as db:
    with self.assertRaises(ServiceError):self.store._rebuild_dependencies(db,self.f.package,self.f.authority)
  self.assertEqual(self.store.task(BASE)['status'],'checks_complete')
