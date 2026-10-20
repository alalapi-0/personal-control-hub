"""Synthetic plan inputs never confer execution authority."""
from pathlib import Path
import sys,tempfile,unittest,copy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub.service_contract import ServiceError
from hub.workbench_store import digest
from hub.task_store import TaskStore
from hub.plan_admission import validate_plan,ready_window,PlanAuthority,BOOTSTRAP_IDS

BASE='P1-R1-PCH-BASE';POLICY='P1-R3-PSH-POLICY';OFFLINE='P1-R1-PCH-OFFLINE'
def task(ident,deps=(),status='planned'):
 return {'id':ident,'phase_id':ident.split('-')[0],'round_id':ident.split('-')[0]+'-R1','project_id':'synthetic','owner_route':'route:synthetic','depends_on':list(deps),'status':status,'dispatch_priority':0 if ident==BASE else 10 if ident==POLICY else 100,'admission_class':'bootstrap' if ident in BOOTSTRAP_IDS else 'routed'}
def plan(tasks):
 return {'plan_id':'SYNTHETIC-PLAN','revision':'1','tasks':tasks,'admission_policy':{'first_dispatch_task':BASE,'bootstrap_task_ids':sorted(BOOTSTRAP_IDS),'general_execution_ready_after':'P2-R1-PCH-ROUTES'}}

class PlanAdmissionTests(unittest.TestCase):
 def test_priority_no_phase_or_style_barrier(self):
  p=plan([task(POLICY),task(BASE),task('P2-R2-PCH-DAG')]);self.assertEqual(ready_window(p,{},set(),lambda _:False),[BASE,POLICY,'P2-R2-PCH-DAG'])
 def test_duplicate_missing_cycle_and_done_dependency(self):
  cases=[([task(BASE),task(BASE)],'PLAN_DUPLICATE'),
         ([task(BASE),task(OFFLINE,['missing'])],'PLAN_MISSING_DEPENDENCY'),
         ([task(BASE),task(OFFLINE,['P1-R1-PCH-REFRESH']),task('P1-R1-PCH-REFRESH',[OFFLINE])],'PLAN_CYCLE'),
         ([task(BASE),task(OFFLINE,[BASE],'done')],'PLAN_DONE_DEPENDENCY')]
  for tasks,code in cases:
   with self.subTest(tasks=tasks):
    with self.assertRaises(ServiceError) as error:validate_plan(plan(tasks))
    self.assertEqual(error.exception.code,code)
 def test_relationship_cycle_is_not_dependency_cycle(self):
  p=plan([task(BASE),task(POLICY)]);p['relations']=[{'from':BASE,'to':POLICY},{'from':POLICY,'to':BASE}];validate_plan(p)
 def test_full_plan_is_not_queue_and_window_is_32(self):
  p=plan([task(BASE)]+[task('P3-R1-T%03d'%n) for n in range(258)])
  self.assertEqual(len(validate_plan(p)['tasks']),259);self.assertEqual(len(ready_window(p,{},set(),lambda _:True)),32)
 def test_no_self_report_done_or_capability(self):
  p=plan([task(BASE,(), 'done'),task(OFFLINE,[BASE]),task('P3-R1-T')])
  self.assertEqual(ready_window(p,{},set(),lambda _:False),[])
 def test_paused_cancelled_unknown_and_existing_never_redispatch(self):
  p=plan([task(BASE),task(POLICY),task('P2-R2-PCH-DAG')]);self.assertEqual(ready_window(p,{BASE:'paused',POLICY:'cancelled','P2-R2-PCH-DAG':'requires_reconcile'},set(),lambda _:True),[])
 def test_policy_forgery_and_wrong_priority(self):
  for mutation in ('bootstrap','priority'):
   p=plan([task(BASE)])
   if mutation=='bootstrap':p['admission_policy']['bootstrap_task_ids'].append('P3-R1-T')
   else:p['tasks'][0]['dispatch_priority']=1
   with self.assertRaises(ServiceError):validate_plan(p)

class EntranceDependencyTests(unittest.TestCase):
 def mutations(self):
  for entrance,peer in ((BASE,POLICY),(POLICY,BASE)):
   for prerequisite in (peer,OFFLINE):
    for status in ('planned','done'):
     rows=[task(BASE,status=status),task(POLICY,status=status),task(OFFLINE,status=status)]
     next(row for row in rows if row['id']==entrance)['depends_on']=[prerequisite]
     yield entrance,prerequisite,status,plan(rows)
 def test_fixed_entrance_dependencies_reject_including_historical_done(self):
  for entrance,prerequisite,status,p in self.mutations():
   with self.subTest(entrance=entrance,prerequisite=prerequisite,status=status):
    with self.assertRaises(ServiceError) as error:validate_plan(p)
    self.assertEqual(error.exception.code,'PLAN_ENTRANCE_DEPENDENCY')
 def test_forged_import_is_rejected_without_static_or_execution_rows(self):
  for entrance,prerequisite,status,p in self.mutations():
   with self.subTest(entrance=entrance,prerequisite=prerequisite,status=status):
    f=PlanStoreTests();f.setUp()
    try:
     before=f.store.path.read_bytes();f.p=p;f.configure()
     with self.assertRaises(ServiceError) as error:f.store.import_plan(p,authority=f.cap)
     self.assertEqual(error.exception.code,'PLAN_ENTRANCE_DEPENDENCY')
     self.assertEqual(f.store.path.read_bytes(),before)
     self.assertEqual(f.store.tasks(),[])
    finally:f.doCleanups()
 def test_policy_remains_optional_and_independent_of_base_state(self):
  p=plan([task(BASE),task(OFFLINE,[BASE])])
  self.assertEqual(validate_plan(p),p)
  self.assertEqual(ready_window(p,{},set(),lambda _:False),[BASE])
  p['tasks'].append(task(POLICY))
  self.assertEqual(ready_window(p,{},set(),lambda _:False),[BASE,POLICY])
  self.assertEqual(ready_window(p,{BASE:'paused'},set(),lambda _:False),[POLICY])

if __name__=='__main__':unittest.main()

class PlanStoreTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='pch-dag-');self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name)/'hub';self.root.mkdir();self.store=TaskStore(self.root);self.store.register_storage()
  self.p=plan([task(BASE),task(POLICY),task('P2-R1-PCH-ROUTES'),task('P3-R1-T')]);self.records={};self.control={'state':'active','revision':1};self.packages={}
  self.configure()
 def configure(self):
  self.cap=PlanAuthority.configured({'plan_sha256':digest(self.p),'owner_context':'synthetic-owner','verified_evidence':{k:digest(v)for k,v in self.records.items()}},authorize=lambda:None,controls=lambda:copy.deepcopy(self.control),routes=lambda t:True,package_for=lambda ident:self.packages.get(ident))
 def evidence(self,ident,*,unknown=False):
  return {'schema_version':'1.0','plan_sha256':digest(self.p),'task_id':ident,'attempt_id':'original-attempt','receipt_id':'original-receipt','receipt_sha256':'b'*64,'evidence_ref':'synthetic/evidence.json','evidence_sha256':'c'*64,'effect_state':'unknown' if unknown else 'read_only','unknowns':['unresolved']if unknown else [],'origin':'bootstrap','provenance':{'source':'root_verified'},'native_thread_id':'synthetic-native-thread','session_id':None}
 def test_import_is_static_replay_and_conflict(self):
  self.assertEqual(self.store.import_plan(self.p,authority=self.cap)['execution_rows_created'],0)
  self.assertTrue(self.store.import_plan(self.p,authority=self.cap)['replayed']);self.assertEqual(self.store.tasks(),[])
  altered=copy.deepcopy(self.p);altered['revision']='other'
  cap=PlanAuthority.configured({'plan_sha256':digest(altered)},authorize=lambda:None,controls=lambda:self.control,routes=lambda _:True,package_for=lambda _:None)
  with self.assertRaises(ServiceError):self.store.import_plan(altered,authority=cap)
 def test_bootstrap_original_ids_once_no_redispatch_and_unknown(self):
  self.store.import_plan(self.p,authority=self.cap)
  r=self.evidence(BASE);self.records[BASE]=r;self.configure()
  self.assertFalse(self.store.record_plan_evidence(self.p['plan_id'],r,authority=self.cap)['requires_reconcile'])
  self.assertTrue(self.store.record_plan_evidence(self.p['plan_id'],r,authority=self.cap)['replayed'])
  self.assertNotIn(BASE,self.store.plan_ready(self.p['plan_id'],authority=self.cap)['ready'])
  with self.store._connection()as db:self.assertEqual(self.store._decode(*db.execute('SELECT body,hash FROM plan_receipts').fetchone()),r)
  u=self.evidence(POLICY,unknown=True);self.records[POLICY]=u;self.configure()
  self.assertTrue(self.store.record_plan_evidence(self.p['plan_id'],u,authority=self.cap)['requires_reconcile'])
  self.assertNotIn(POLICY,self.store.plan_ready(self.p['plan_id'],authority=self.cap)['ready'])
 def test_capability_requires_verified_routes_receipt(self):
  self.store.import_plan(self.p,authority=self.cap);self.assertNotIn('P3-R1-T',self.store.plan_ready(self.p['plan_id'],authority=self.cap)['ready'])
  r=self.evidence('P2-R1-PCH-ROUTES');self.records[r['task_id']]=r;self.configure();self.store.record_plan_evidence(self.p['plan_id'],r,authority=self.cap)
  self.assertIn('P3-R1-T',self.store.plan_ready(self.p['plan_id'],authority=self.cap)['ready'])
 def test_pause_and_missing_real_package_authority_admit_zero(self):
  self.store.import_plan(self.p,authority=self.cap)
  result=self.store.admit_plan_window(self.p['plan_id'],authority=self.cap);self.assertEqual(result['admitted'],[]);self.assertTrue(result['unavailable_authority']);self.assertEqual(self.store.tasks(),[])
  self.control={'state':'paused','revision':2};self.assertEqual(self.store.plan_ready(self.p['plan_id'],authority=self.cap)['ready'],[])
  self.assertEqual(self.store.admit_plan_window(self.p['plan_id'],authority=self.cap)['admitted'],[])
 def test_forged_evidence_or_mutated_binding_reject(self):
  self.store.import_plan(self.p,authority=self.cap);r=self.evidence(BASE)
  with self.assertRaises(ServiceError):self.store.record_plan_evidence(self.p['plan_id'],r,authority=self.cap)
  view=self.cap.snapshot();view['plan_sha256']='a'*64
  self.assertEqual(self.store.plan_ready(self.p['plan_id'],authority=self.cap)['ready'][0],BASE)
 def test_conflict_preserves_original_and_requires_reconcile(self):
  self.store.import_plan(self.p,authority=self.cap);r=self.evidence(BASE);self.records[BASE]=r;self.configure();self.store.record_plan_evidence(self.p['plan_id'],r,authority=self.cap)
  other=copy.deepcopy(r);other['attempt_id']='different-attempt';self.records[BASE]=other;self.configure()
  self.assertTrue(self.store.record_plan_evidence(self.p['plan_id'],other,authority=self.cap)['requires_reconcile'])
  # Rebind original accepted evidence for history verification; conflict stays explicit.
  self.records[BASE]=r;self.configure();self.assertNotIn(BASE,self.store.plan_ready(self.p['plan_id'],authority=self.cap)['ready'])
 def test_schema_fault_rolls_back_and_unknown_version_rejects(self):
  def fault(phase):
   if phase=='after_plan_schema':raise OSError('synthetic DDL failure')
  self.store.fault=fault
  with self.assertRaises(OSError):self.store.import_plan(self.p,authority=self.cap)
  with self.store._connection()as db:self.assertFalse(self.store._plan_schema(db))
  self.store.fault=lambda _:None;self.store.import_plan(self.p,authority=self.cap)
  with self.store._connection(True)as db:db.execute('PRAGMA ignore_check_constraints=ON');db.execute('UPDATE plan_schema SET version=99')
  with self.assertRaises(ServiceError):self.store.plan_ready(self.p['plan_id'],authority=self.cap)

class ContractPlanIntegrationTests(unittest.TestCase):
 def setUp(self):
  import test_hub_rebuild_contracts as contracts
  self.f=contracts.RebuildContractsTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
  self.store=self.f.store;self.records={};self.control={'state':'active','revision':1};self.packages={}
 def configure(self):
  self.cap=PlanAuthority.configured({'plan_sha256':digest(self.p),'owner_context':'synthetic-owner','verified_evidence':{k:digest(v)for k,v in self.records.items()}},authorize=lambda:None,controls=lambda:copy.deepcopy(self.control),routes=lambda _:True,package_for=lambda ident:self.packages.get(ident))
 def definition(self,ident):
  t=task(ident);t['project_id']=self.f.grant['project_id'];t['owner_route']=self.f.package['route']['owner_route'];return t
 def package(self,ident):
  p=copy.deepcopy(self.f.package);p['task_id']=ident;p['lineage']['original_task_id']=ident;p['phase_id']=ident.split('-')[0];p['round_id']=ident.split('-')[0]+'-R1';p['plan']={'revision':self.p['revision'],'sha256':digest(self.p)};p['lineage']['goal_id']=self.p['plan_id']
  self.f.package=p;self.f.configure();return p,self.f.authority
 def bootstrap(self,ident):
  r={'schema_version':'1.0','plan_sha256':digest(self.p),'task_id':ident,'attempt_id':'original-attempt','receipt_id':'original-receipt','receipt_sha256':'b'*64,'evidence_ref':'synthetic/evidence.json','evidence_sha256':'c'*64,'effect_state':'read_only','unknowns':[],'origin':'bootstrap','provenance':{'source':'root_verified'},'native_thread_id':'synthetic-native','session_id':None}
  self.records[ident]=r;self.configure();self.store.record_plan_evidence(self.p['plan_id'],r,authority=self.cap)
 def test_real_contract_window_limits_even_repeated_calls(self):
  ids=[BASE,'P2-R1-PCH-ROUTES']+['P3-R1-T%03d'%n for n in range(40)]
  self.p=plan([self.definition(ident)for ident in ids]);self.configure();self.store.import_plan(self.p,authority=self.cap);self.bootstrap('P2-R1-PCH-ROUTES')
  self.packages.update({ident:self.package(ident)for ident in ids if ident!='P2-R1-PCH-ROUTES'})
  result=self.store.admit_plan_window(self.p['plan_id'],authority=self.cap)
  self.assertEqual(len(result['admitted']),32);self.assertEqual(result['admitted'][0],BASE);self.assertEqual(len(self.store.tasks()),32)
  self.assertEqual(self.store.admit_plan_window(self.p['plan_id'],authority=self.cap)['admitted'],[])
  self.assertIsNone(self.store.claim('legacy-worker'))
 def settled(self):
  self.p=plan([self.definition(BASE)]);self.configure();self.store.import_plan(self.p,authority=self.cap)
  self.package(BASE);self.f.start();r=self.f.receipt();self.f.record(r)
  self.store.update(BASE,'synthetic-worker',status='validating');self.store.update(BASE,'synthetic-worker',status='checks_complete')
  record={'schema_version':'1.0','plan_sha256':digest(self.p),'task_id':BASE,'attempt_id':r['attempt_id'],'receipt_id':r['receipt_id'],'receipt_sha256':digest(r),'evidence_ref':'synthetic/root.json','evidence_sha256':'d'*64,'effect_state':'read_only','unknowns':[],'origin':'contract','provenance':self.f.proof(r),'native_thread_id':None,'session_id':None}
  self.records[BASE]=record;self.configure();self.store.record_plan_evidence(self.p['plan_id'],record,authority=self.cap)
 def test_archive_restart_replay_and_rollback_preserve_exact_history(self):
  self.settled();before=self.store.task(BASE);events=self.store.events(BASE)
  self.assertEqual(self.store.archive_plan_completed(self.p['plan_id'],authority=self.cap)['archived'],[BASE])
  reopened=TaskStore(self.store.root);self.assertEqual(reopened.task(BASE),before);self.assertEqual(reopened.events(BASE),events)
  self.assertTrue(self.f.prepare()['replayed']);self.assertEqual(len(self.store.tasks()),1)
  with self.assertRaises(ServiceError):self.store.event(BASE,'new-event',{})
  self.store.restore_plan_archive(self.p['plan_id'],authority=self.cap)
  self.assertEqual(self.store.task(BASE),before);self.assertEqual(self.store.events(BASE),events)
 def test_archive_fault_rolls_back_and_unverified_work_stays(self):
  self.settled();before=self.store.task(BASE);events=self.store.events(BASE)
  def fault(phase):
   if phase=='before_plan_archive_commit':raise OSError('synthetic archive fault')
  self.store.fault=fault
  with self.assertRaises(OSError):self.store.archive_plan_completed(self.p['plan_id'],authority=self.cap)
  self.assertEqual(self.store.task(BASE),before);self.assertEqual(self.store.events(BASE),events)
  with self.store._connection()as db:self.assertEqual(db.execute('SELECT count(*)FROM plan_archive_tasks').fetchone()[0],0)
 def test_capacity_preserves_128_tasks_2048_events_and_blocks_unsafe_rollback(self):
  self.settled();original=self.store.task(BASE);original_events=self.store.events(BASE)
  for n in range(127):self.store.admit({'request_id':'filler-%d'%n,'project_id':'fixture','parent_task_id':None},{'synthetic':True})
  with self.store._connection(True)as db:
   count=db.execute('SELECT count(*)FROM events').fetchone()[0]
   for n in range(2048-count):self.store._append_event(db,'filler-0','filler-event-%d'%n,{'synthetic':n})
  with self.assertRaises(ServiceError):self.store.admit({'request_id':'overflow','project_id':'fixture','parent_task_id':None},{'synthetic':True})
  with self.assertRaises(ServiceError):self.store.event('filler-0','event-overflow',{})
  self.store.archive_plan_completed(self.p['plan_id'],authority=self.cap)
  self.store.admit({'request_id':'new-slot','project_id':'fixture','parent_task_id':None},{'synthetic':True})
  self.store.event('new-slot','new-evidence',{'synthetic':True})
  self.assertEqual(len(self.store.tasks()),129);self.assertEqual(self.store.task(BASE),original);self.assertEqual(self.store.events(BASE),original_events)
  with self.assertRaises(ServiceError):self.store.restore_plan_archive(self.p['plan_id'],authority=self.cap)
  with self.store._connection()as db:
   self.assertEqual(db.execute('SELECT count(*)FROM tasks').fetchone()[0],128)
   self.assertEqual(db.execute('SELECT count(*)FROM plan_archive_tasks').fetchone()[0],1)
   self.assertEqual(db.execute('SELECT count(*)FROM events').fetchone()[0]+db.execute('SELECT count(*)FROM plan_archive_events').fetchone()[0],2049)

class PlanBoundaryTests(unittest.TestCase):
 setUp=PlanStoreTests.setUp
 configure=PlanStoreTests.configure
 evidence=PlanStoreTests.evidence
 def test_verified_descendant_waits_for_dependency_evidence(self):
  self.p=plan([task(BASE),task(OFFLINE,[BASE]),task('P2-R2-PCH-DAG',[OFFLINE])]);self.configure();self.store.import_plan(self.p,authority=self.cap)
  r=self.evidence(OFFLINE);self.records[OFFLINE]=r;self.configure();self.store.record_plan_evidence(self.p['plan_id'],r,authority=self.cap)
  self.assertNotIn('P2-R2-PCH-DAG',self.store.plan_ready(self.p['plan_id'],authority=self.cap)['ready'])
  r=self.evidence(BASE);self.records[BASE]=r;self.configure();self.store.record_plan_evidence(self.p['plan_id'],r,authority=self.cap)
  self.assertEqual(self.store.plan_ready(self.p['plan_id'],authority=self.cap)['ready'],['P2-R2-PCH-DAG'])
 def test_import_before_commit_fault_has_no_partial_static_input(self):
  def fault(phase):
   if phase=='before_plan_import_commit':raise OSError('synthetic fault')
  self.store.fault=fault
  with self.assertRaises(OSError):self.store.import_plan(self.p,authority=self.cap)
  with self.store._connection()as db:self.assertFalse(self.store._plan_schema(db))
 def test_cancel_and_unknown_controls_admit_zero(self):
  self.store.import_plan(self.p,authority=self.cap);self.control={'state':'cancelled','revision':2}
  self.assertEqual(self.store.admit_plan_window(self.p['plan_id'],authority=self.cap)['admitted'],[])
  self.control={'state':'unknown','revision':3}
  with self.assertRaises(ServiceError):self.store.admit_plan_window(self.p['plan_id'],authority=self.cap)

class PlanDependencyAdmissionTests(unittest.TestCase):
 setUp=ContractPlanIntegrationTests.setUp
 configure=ContractPlanIntegrationTests.configure
 definition=ContractPlanIntegrationTests.definition
 package=ContractPlanIntegrationTests.package
 bootstrap=ContractPlanIntegrationTests.bootstrap
 def test_readiness_does_not_replace_missing_contract_dependency_receipt(self):
  base=self.definition(BASE);child=self.definition(OFFLINE);child['depends_on']=[BASE]
  self.p=plan([base,child]);self.configure();self.store.import_plan(self.p,authority=self.cap);self.bootstrap(BASE)
  self.assertEqual(self.store.plan_ready(self.p['plan_id'],authority=self.cap)['ready'],[OFFLINE])
  package,cap=self.package(OFFLINE);package['dependencies']=[{'task_id':BASE,'receipt_sha256':'b'*64}]
  self.f.package=package;self.f.configure();self.packages[OFFLINE]=(package,self.f.authority)
  result=self.store.admit_plan_window(self.p['plan_id'],authority=self.cap)
  self.assertEqual(result['admitted'],[]);self.assertEqual(result['unavailable_dependency_receipts'],[OFFLINE]);self.assertEqual(self.store.tasks(),[])


class EngineeringLifecycleTests(unittest.TestCase):
 def test_rebuild_checks_do_not_create_owner_or_style_gate(self):
  from hub.task_lifecycle import lifecycle
  current={'id':'synthetic-task','status':'checks_complete','payload':{'kind':'rebuild_project'}}
  view=lifecycle(current,[]);self.assertEqual(view['stage'],'checks_complete');self.assertFalse(view['owner_review_required']);self.assertFalse(view['human_accepted'])
  current['payload']['kind']='ordinary_project';legacy=lifecycle(current,[])
  self.assertEqual(legacy['stage'],'await_owner_review');self.assertTrue(legacy['owner_review_required'])

class PlanBudgetAdmissionTests(unittest.TestCase):
 setUp=PlanDependencyAdmissionTests.setUp
 configure=PlanDependencyAdmissionTests.configure
 definition=PlanDependencyAdmissionTests.definition
 package=PlanDependencyAdmissionTests.package
 def test_expired_package_budget_is_not_queued(self):
  self.p=plan([self.definition(BASE)]);self.configure();self.store.import_plan(self.p,authority=self.cap)
  package,cap=self.package(BASE);package['budget']['deadline']=self.f.now-1;self.f.package=package;self.f.configure();self.packages[BASE]=(package,self.f.authority)
  result=self.store.admit_plan_window(self.p['plan_id'],authority=self.cap)
  self.assertEqual(result['admitted'],[]);self.assertEqual(result['unavailable_budget'],[BASE]);self.assertEqual(self.store.tasks(),[])
