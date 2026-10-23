"""Synthetic plan inputs never confer execution authority."""
from pathlib import Path
import sys,tempfile,unittest,copy,json
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

class PlanRevisionTests(unittest.TestCase):
 def setUp(self):
  PlanStoreTests.setUp(self);self.original=copy.deepcopy(self.p);self.store.import_plan(self.p,authority=self.cap);self.version_records={}
 configure=PlanStoreTests.configure
 evidence=PlanStoreTests.evidence
 def bind(self,target,expected):
  self.p=target;self.configure();binding=self.cap.snapshot();binding['reconcile_from_sha256']=expected;binding['verified_evidence_versions']=copy.deepcopy(self.version_records)
  self.cap=PlanAuthority.configured(binding,authorize=lambda:None,controls=lambda:copy.deepcopy(self.control),routes=lambda _:True,package_for=lambda ident:self.packages.get(ident))
 def target(self):
  p=copy.deepcopy(self.original);p['revision']='2';next(t for t in p['tasks']if t['id']==POLICY)['objective']='Changed synthetic definition';return p
 def record(self,ident,unknown=False):
  r=self.evidence(ident,unknown=unknown);self.records[ident]=r;self.version_records.setdefault(ident,{})[r['plan_sha256']]=digest(r)
  expected=self.cap.snapshot().get('reconcile_from_sha256');self.bind(self.p,expected)
  self.store.record_plan_evidence(self.p['plan_id'],r,authority=self.cap);return r
 def test_cas_versions_exact_applicability_receipt_replay_and_guarded_rollback(self):
  base=self.record(BASE);old=self.record(POLICY);before=self.store.path.read_bytes();p=self.target();self.bind(p,digest(self.original))
  with self.assertRaises(ServiceError):self.store.admit_plan_window(p['plan_id'],authority=self.cap)
  self.assertEqual(self.store.path.read_bytes(),before)
  result=self.store.reconcile_plan(p,expected_sha256=digest(self.original),request_id='revision-one',authority=self.cap)
  self.assertEqual(result['request']['affected_task_ids'],[POLICY]);self.assertEqual(self.store.tasks(),[])
  view=self.store.plan_ready(p['plan_id'],authority=self.cap);self.assertIn(BASE,view['accepted']);self.assertNotIn(POLICY,view['accepted']);self.assertIn(POLICY,view['ready'])
  new=self.record(POLICY);self.assertTrue(self.store.record_plan_evidence(p['plan_id'],new,authority=self.cap)['replayed'])
  self.assertIn(POLICY,self.store.plan_ready(p['plan_id'],authority=self.cap)['accepted'])
  with self.store._connection()as db:
   self.assertEqual(self.store._decode(*db.execute('SELECT body,hash FROM plan_receipts WHERE task_id=?',(POLICY,)).fetchone()),old)
   self.assertEqual(db.execute('SELECT count(*)FROM plan_conflicts').fetchone()[0],1)
  self.bind(self.original,digest(p));self.store.reconcile_plan(self.original,expected_sha256=digest(p),request_id='rollback-one',authority=self.cap,rollback=True)
  self.assertIn(POLICY,self.store.plan_ready(p['plan_id'],authority=self.cap)['accepted'])
  from hub.task_storage_restore import _snapshot
  self.assertEqual(len(_snapshot(self.store,self.store.path.read_bytes())[1]['plan_conflicts']),1)
 def test_request_replay_conflict_and_stale_cas_preserve_bytes(self):
  p=self.target();expected=digest(self.original);self.bind(p,expected)
  self.store.reconcile_plan(p,expected_sha256=expected,request_id='revision-one',authority=self.cap);before=self.store.path.read_bytes()
  self.assertTrue(self.store.reconcile_plan(p,expected_sha256=expected,request_id='revision-one',authority=self.cap)['replayed']);self.assertEqual(self.store.path.read_bytes(),before)
  for request,target,code in [('stale',p,'PLAN_CAS_STALE'),('revision-one',{**p,'revision':'3'},'PLAN_REVISION_REQUEST_CONFLICT')]:
   self.bind(target,expected)
   with self.assertRaises(ServiceError)as error:self.store.reconcile_plan(target,expected_sha256=expected,request_id=request,authority=self.cap)
   self.assertEqual(error.exception.code,code);self.assertEqual(self.store.path.read_bytes(),before)
 def test_atomic_fault_and_unknown_commit_can_be_queried_without_reexecution(self):
  p=self.target();expected=digest(self.original);self.bind(p,expected);before=self.store.path.read_bytes()
  def fault(phase):
   if phase=='before_plan_revision_commit':raise OSError('synthetic rollback')
  self.store.fault=fault
  with self.assertRaises(OSError):self.store.reconcile_plan(p,expected_sha256=expected,request_id='revision-one',authority=self.cap)
  self.assertEqual(self.store.path.read_bytes(),before)
  def lost(phase):
   if phase=='after_commit':raise OSError('synthetic lost reply')
  self.store.fault=lost
  with self.assertRaises(ServiceError)as error:self.store.reconcile_plan(p,expected_sha256=expected,request_id='revision-one',authority=self.cap)
  self.assertEqual(error.exception.outcome,'UNKNOWN');self.store.fault=lambda _:None
  status=self.store.plan_revision_status(p['plan_id'],authority=self.cap);self.assertEqual(status['current_sha256'],digest(p));self.assertEqual(len(status['requests']),1)
  self.assertTrue(self.store.reconcile_plan(p,expected_sha256=expected,request_id='revision-one',authority=self.cap)['replayed'])
 def test_paused_cancelled_forged_authority_and_changed_control_reject(self):
  p=self.target();expected=digest(self.original);self.bind(p,expected);before=self.store.path.read_bytes()
  for state in ('paused','cancelled','unknown'):
   self.control={'state':state,'revision':2}
   with self.assertRaises(ServiceError):self.store.reconcile_plan(p,expected_sha256=expected,request_id='revision-one',authority=self.cap)
   self.assertEqual(self.store.path.read_bytes(),before)
  self.control={'state':'active','revision':3}
  with self.assertRaises(ServiceError):self.store.reconcile_plan(p,expected_sha256='e'*64,request_id='revision-one',authority=self.cap)
  def fault(phase):
   if phase=='before_commit':self.control={'state':'paused','revision':4}
  self.store.fault=fault
  with self.assertRaises(ServiceError):self.store.reconcile_plan(p,expected_sha256=expected,request_id='revision-one',authority=self.cap)
  self.assertEqual(self.store.path.read_bytes(),before)
 def test_changed_queued_package_and_unknown_effect_block_switch(self):
  self.record(POLICY,unknown=True);p=self.target();self.bind(p,digest(self.original));before=self.store.path.read_bytes()
  with self.assertRaises(ServiceError)as error:self.store.reconcile_plan(p,expected_sha256=digest(self.original),request_id='revision-one',authority=self.cap)
  self.assertEqual(error.exception.code,'PLAN_REVISION_UNKNOWN_EFFECT');self.assertEqual(self.store.path.read_bytes(),before)
  self.store.admit({'request_id':POLICY,'project_id':'synthetic','parent_task_id':None},{'synthetic':True});before=self.store.path.read_bytes()
  with self.assertRaises(ServiceError)as error:self.store.reconcile_plan(p,expected_sha256=digest(self.original),request_id='revision-one',authority=self.cap)
  self.assertEqual(error.exception.code,'PLAN_REVISION_EXISTING_EFFECT');self.assertEqual(self.store.path.read_bytes(),before)
 def test_offline_tampered_revision_links_versions_and_affected_set_reject(self):
  import sqlite3
  from hub.task_storage_restore import _snapshot
  from hub.workbench_store import canonical
  p=self.target();self.bind(p,digest(self.original));self.store.reconcile_plan(p,expected_sha256=digest(self.original),request_id='revision-one',authority=self.cap)
  raw=self.store.path.read_bytes();_snapshot(self.store,raw)
  for mutation in ('current','affected','prior','definition','operation'):
   with self.subTest(mutation=mutation),sqlite3.connect(':memory:')as db:
    db.deserialize(raw);value=json.loads(db.execute('SELECT body FROM plan_inputs').fetchone()[0])
    if mutation=='current':value['current_sha256']='e'*64
    if mutation=='affected':value['requests'][0]['affected_task_ids']=[]
    if mutation=='prior':value['requests'][0]['from_sha256']='e'*64
    if mutation=='definition':value['versions'][0]['plan']['tasks'][0]['status']='done'
    if mutation=='operation':value['requests'][0]['operation']='rollback'
    db.execute('UPDATE plan_inputs SET body=?,hash=?',(canonical(value).decode(),digest(value)));db.commit()
    with self.assertRaises(ServiceError):_snapshot(self.store,db.serialize())
 def test_versioned_priority_contract_preserves_entrances_and_other_priorities(self):
  p=plan([task(BASE),task(POLICY),task('P2-R2-PCH-DAG')]);p['revision']='1.1.0-synthetic';p['tasks'][2]['dispatch_priority']=20
  self.assertEqual(ready_window(p,{},set(),lambda _:False),[BASE,POLICY,'P2-R2-PCH-DAG'])
  for field in ('revision','other_priority','dag_priority'):
   q=copy.deepcopy(p)
   if field=='revision':q['revision']='1.0.1-synthetic'
   if field=='other_priority':q['tasks'].append(task(OFFLINE));q['tasks'][-1]['dispatch_priority']=20
   if field=='dag_priority':q['tasks'][2]['dispatch_priority']=100
   with self.subTest(field=field),self.assertRaises(ServiceError):validate_plan(q)

class PlanRevisionServiceTests(unittest.TestCase):
 def test_existing_service_owner_boundary_and_original_request_query(self):
  from hub.task_service import TaskService
  f=PlanRevisionTests();f.setUp();self.addCleanup(f.doCleanups)
  target=f.target();expected=digest(f.original);f.bind(target,expected)
  service=TaskService(None,None,f.store,plan_authority=f.cap)
  before=f.store.path.read_bytes()
  with self.assertRaises(ServiceError):service.reconcile_plan(target,expected_sha256=expected,request_id='service-revision',owner_context='wrong-owner')
  self.assertEqual(f.store.path.read_bytes(),before)
  result=service.reconcile_plan(target,expected_sha256=expected,request_id='service-revision',owner_context='synthetic-owner')
  status=service.plan_revision_status(target['plan_id'],owner_context='synthetic-owner')
  self.assertEqual(status['requests'],[result['request']]);self.assertEqual(status['current_sha256'],digest(target));self.assertFalse(status['execution_started'])

class PlanRevisionBoundaryTests(unittest.TestCase):
 def test_root_original_hash_mapping_is_required_for_unchanged_old_pass(self):
  f=PlanRevisionTests();f.setUp();self.addCleanup(f.doCleanups)
  f.record(BASE);target=f.target();f.bind(target,digest(f.original));f.store.reconcile_plan(target,expected_sha256=digest(f.original),request_id='version-mapping',authority=f.cap)
  binding=f.cap.snapshot();binding['verified_evidence']={};binding['verified_evidence_versions']={}
  cap=PlanAuthority.configured(binding,authorize=lambda:None,controls=lambda:{'state':'active','revision':1},routes=lambda _:True,package_for=lambda _:None)
  view=f.store.plan_ready(target['plan_id'],authority=cap)
  self.assertNotIn(BASE,view['accepted']);self.assertNotIn(BASE,view['ready'])
 def test_changed_task_set_and_reused_revision_identity_are_not_overwritten(self):
  f=PlanRevisionTests();f.setUp();self.addCleanup(f.doCleanups);before=f.store.path.read_bytes();old=digest(f.original)
  for mutation in ('add','remove','reuse_revision'):
   target=f.target()
   if mutation=='add':target['tasks'].append(task(OFFLINE))
   if mutation=='remove':target['tasks']=[t for t in target['tasks']if t['id']!=POLICY]
   if mutation=='reuse_revision':target['revision']=f.original['revision']
   f.bind(target,old)
   with self.subTest(mutation=mutation),self.assertRaises(ServiceError):f.store.reconcile_plan(target,expected_sha256=old,request_id='identity-guard',authority=f.cap)
   self.assertEqual(f.store.path.read_bytes(),before)

class PlanRevisionExactDefinitionTests(unittest.TestCase):
 def test_false_and_zero_are_different_definition_versions(self):
  from hub.plan_admission import changed_definitions
  old=plan([task(BASE),task(POLICY)]);old['tasks'][1]['synthetic_flag']=False
  new=copy.deepcopy(old);new['revision']='2';new['tasks'][1]['synthetic_flag']=0
  self.assertEqual(changed_definitions(old,new),[POLICY])

class PlanRevisionScaleTests(unittest.TestCase):
 def test_two_large_plans_twelve_receipts_reconcile_replay_and_rollback(self):
  from hub.workbench_store import canonical
  f=PlanRevisionTests();f.setUp();self.addCleanup(f.doCleanups)
  # A separate plan uses the normal Store and default SQLite settings. No real
  # manifest, cache_spill/journal override or mocked storage check is involved.
  ids=[BASE,POLICY]+sorted(BOOTSTRAP_IDS-{BASE,POLICY})[:10]
  rows=[task(ident)for ident in ids]+[task('P3-R1-SCALE%03d'%n)for n in range(247)]
  for row in rows:row['synthetic_text']='x'*3200
  old=plan(rows);old['plan_id']='SYNTHETIC-SCALE';f.p=old;f.original=copy.deepcopy(old);f.configure();f.store.import_plan(old,authority=f.cap)
  for ident in ids:f.record(ident)
  for old_bytes in [len(canonical(old))]:self.assertGreater(old_bytes,850000);self.assertLess(old_bytes,1000000)
  target=copy.deepcopy(old);target['revision']='2';target['tasks'][-1]['synthetic_text']='y'*3200
  self.assertGreater(len(canonical(target)),850000);self.assertLess(len(canonical(target)),1000000)
  with f.store._connection()as db:
   original_receipts=[tuple(r)for r in db.execute('SELECT *FROM plan_receipts ORDER BY rowid')];profile=[tuple(r)for r in db.execute('SELECT *FROM storage_profile')]
  f.bind(target,digest(old));result=f.store.reconcile_plan(target,expected_sha256=digest(old),request_id='scale-revision',authority=f.cap)
  self.assertEqual(result['request']['affected_task_ids'],[rows[-1]['id']]);self.assertEqual(f.store.tasks(),[])
  self.assertTrue(f.store.reconcile_plan(target,expected_sha256=digest(old),request_id='scale-revision',authority=f.cap)['replayed'])
  self.assertEqual(set(f.store.plan_ready(old['plan_id'],authority=f.cap)['accepted']),set(ids))
  f.bind(old,digest(target));f.store.reconcile_plan(old,expected_sha256=digest(target),request_id='scale-rollback',authority=f.cap,rollback=True)
  status=f.store.plan_revision_status(old['plan_id'],authority=f.cap);self.assertEqual(status['current_sha256'],digest(old));self.assertEqual(len(status['requests']),2)
  with f.store._connection()as db:
   self.assertEqual([tuple(r)for r in db.execute('SELECT *FROM plan_receipts ORDER BY rowid')],original_receipts)
   self.assertEqual([tuple(r)for r in db.execute('SELECT *FROM storage_profile')],profile)
  from hub.task_storage_restore import _snapshot
  content=_snapshot(f.store,f.store.path.read_bytes())[1];self.assertEqual(len(content['plan_receipts']),12)
  self.assertLess(f.store.path.stat().st_size,16*1024*1024)

class PlanPrecommitStorageGuardTests(unittest.TestCase):
 def test_uncommitted_profile_schema_and_hash_drift_are_rejected_and_rolled_back(self):
  from hub.workbench_store import canonical
  f=PlanStoreTests();f.setUp();self.addCleanup(f.doCleanups)
  before=f.store.path.read_bytes()
  for scenario in ('hash','profile','missing','schema'):
   with self.subTest(scenario=scenario):
    with self.assertRaises(ServiceError):
     with f.store._connection(True)as db:
      if scenario=='hash':db.execute("UPDATE storage_profile SET hash='bad'")
      if scenario=='missing':db.execute('DELETE FROM storage_profile')
      if scenario=='schema':db.execute('UPDATE meta SET version=99')
      if scenario=='profile':
       value=f.store._decode(*db.execute('SELECT body,hash FROM storage_profile').fetchone());value['inode']+=1
       db.execute('UPDATE storage_profile SET body=?,hash=?',(canonical(value).decode(),digest(value)))
    self.assertEqual(f.store.path.read_bytes(),before)
 def test_registration_mount_and_free_space_are_still_checked_before_commit(self):
  import os
  f=PlanStoreTests();f.setUp();self.addCleanup(f.doCleanups)
  registration=f.store.registration_path;backup=registration.with_name('owned-registration-backup');before=f.store.path.read_bytes()
  mount=f.store.storage_guard.mount;space=f.store.storage_guard.space
  for scenario,reason in (('registration','REGISTRATION_MISSING'),('mount','MOUNT_IDENTITY_CHANGED'),('space','LOW_SPACE')):
   with self.subTest(scenario=scenario):
    def fault(phase):
     if phase!='before_commit':return
     if scenario=='registration':registration.rename(backup)
     if scenario=='mount':f.store.storage_guard.mount=lambda root:{**f.store.storage_guard.profile['mount'],'source':'changed-synthetic-mount'}
     if scenario=='space':f.store.storage_guard.space=lambda root:type('Space',(),{'f_bavail':0,'f_frsize':4096})()
    f.store.fault=fault
    try:
     with self.assertRaises(ServiceError)as error:
      with f.store._connection(True):pass
     self.assertEqual(error.exception.details['reason_code'],reason)
    finally:
     if backup.exists():backup.rename(registration)
     f.store.storage_guard.mount=mount;f.store.storage_guard.space=space;f.store.fault=lambda _:None
    self.assertEqual(f.store.path.read_bytes(),before);self.assertEqual(f.store.storage_status()['state'],'ready')

class PlanInputOverflowReplacementTests(unittest.TestCase):
 def fixture(self):
  import sqlite3
  from hub.workbench_store import canonical
  temp=tempfile.TemporaryDirectory(prefix='pch-overflow-');self.addCleanup(temp.cleanup)
  path=Path(temp.name)/'fixture.sqlite3';db=sqlite3.connect(path);self.addCleanup(db.close)
  db.execute('PRAGMA page_size=4096');db.execute('PRAGMA max_page_count=4096')
  db.execute('CREATE TABLE plan_inputs(id TEXT PRIMARY KEY,body TEXT NOT NULL,hash TEXT NOT NULL)')
  value={'payload':'x'*8291584};encoded=canonical(value).decode()
  db.execute('INSERT INTO plan_inputs VALUES(?,?,?)',('target',encoded,digest(value)))
  db.execute('INSERT INTO plan_inputs VALUES(?,?,?)',('anchor','a'*550000,'synthetic-anchor'))
  db.execute('INSERT INTO plan_inputs VALUES(?,?,?)',('padding','p'*7350000,'synthetic-padding'));db.commit()
  db.execute("DELETE FROM plan_inputs WHERE id='padding'");db.commit()
  return db,path,value
 def test_near_capacity_regular_update_fails_but_replacement_reuses_pages_and_preserves_rowid(self):
  import sqlite3
  from hub.workbench_store import canonical
  db,path,old=self.fixture();new={'payload':'y'*8292000};rowid=db.execute("SELECT rowid FROM plan_inputs WHERE id='target'").fetchone()[0];inode=path.stat().st_ino
  db.execute('BEGIN IMMEDIATE')
  with self.assertRaises(sqlite3.OperationalError)as error:db.execute('UPDATE plan_inputs SET body=?,hash=? WHERE id=?',(canonical(new).decode(),digest(new),'target'))
  self.assertEqual(error.exception.sqlite_errorcode,sqlite3.SQLITE_FULL);db.rollback()
  db.execute('BEGIN IMMEDIATE');TaskStore._replace_plan_input(db,'target',new);db.commit()
  body,hashed,rid=db.execute("SELECT body,hash,rowid FROM plan_inputs WHERE id='target'").fetchone();self.assertEqual(TaskStore._decode(body,hashed),new);self.assertEqual(rowid,rid);self.assertEqual(inode,path.stat().st_ino);self.assertLessEqual(path.stat().st_size,16*1024*1024)
 def test_second_write_guard_and_genuine_oversize_roll_back_exact_bytes(self):
  import sqlite3
  db,path,old=self.fixture();before=path.read_bytes()
  class FailWrite:
   @property
   def in_transaction(self):return db.in_transaction
   def execute(self,sql,args=()):
    if sql.startswith('UPDATE plan_inputs SET body=?'):raise OSError('synthetic second write failure')
    return db.execute(sql,args)
  for phase in ('second','guard','oversize'):
   with self.subTest(phase=phase):
    with self.assertRaises((OSError,sqlite3.OperationalError)):
     with db:
      db.execute('BEGIN IMMEDIATE');TaskStore._replace_plan_input(FailWrite()if phase=='second'else db,'target',{'payload':'y'*(17*1024*1024 if phase=='oversize'else 8292000)})
      if phase=='guard':raise OSError('synthetic commit guard refusal')
    if phase!='oversize':self.assertEqual(path.read_bytes(),before)
    else:
     # SQLite may reuse unjournaled FREE pages before SQLITE_FULL. The live
     # rows/hash and database identity must roll back; free-page bytes are not content.
     self.assertEqual(TaskStore._decode(*db.execute("SELECT body,hash FROM plan_inputs WHERE id='target'").fetchone()),old)
     self.assertEqual(db.execute("SELECT body,hash FROM plan_inputs WHERE id='anchor'").fetchone(),('a'*550000,'synthetic-anchor'))
 def test_reader_sees_old_or_busy_never_placeholder_and_commit_full_final_value(self):
  import sqlite3
  db,path,old=self.fixture();observed=[]
  def trace(sql):
   if sql.startswith('UPDATE plan_inputs SET body=')and "body=''"not in sql:
    reader=sqlite3.connect(path,timeout=0)
    try:
     try:
      body,hashed=reader.execute("SELECT body,hash FROM plan_inputs WHERE id='target'").fetchone();self.assertNotEqual(body,'');observed.append(TaskStore._decode(body,hashed)==old)
     except sqlite3.OperationalError as error:self.assertIn(error.sqlite_errorcode,(sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED));observed.append('busy')
    finally:reader.close()
  db.set_trace_callback(trace);db.execute('BEGIN IMMEDIATE');new={'payload':'z'*8292000};TaskStore._replace_plan_input(db,'target',new);db.commit();db.set_trace_callback(None)
  self.assertTrue(observed);self.assertNotIn(False,observed);self.assertEqual(TaskStore._decode(*db.execute("SELECT body,hash FROM plan_inputs WHERE id='target'").fetchone()),new)
 def test_requires_transaction_existing_row_and_encoding_before_any_write(self):
  db,path,old=self.fixture();before=path.read_bytes()
  with self.assertRaises(ServiceError):TaskStore._replace_plan_input(db,'target',old)
  for ident,value in [('missing',old),('target',{'bad':float('nan')})]:
   with self.subTest(ident=ident):
    with self.assertRaises((ServiceError,ValueError)):
     with db:db.execute('BEGIN IMMEDIATE');TaskStore._replace_plan_input(db,ident,value)
    self.assertEqual(path.read_bytes(),before)
