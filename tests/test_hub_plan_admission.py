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

class PlanHistoryCapacityTests(unittest.TestCase):
 def fixture(self):
  f=PlanRevisionTests();f.setUp();self.addCleanup(f.doCleanups);return f
 def move(self,f,target,request,rollback=False):
  prior=digest(f.p);f.bind(target,prior)
  return f.store.reconcile_plan(target,expected_sha256=prior,request_id=request,authority=f.cap,rollback=rollback)
 def test_beyond_sixteen_preserves_source_budget_unknown_receipts_and_exact_rollback(self):
  import test_hub_source_admission as sources
  import test_hub_dispatch_budget as budgets
  from hub.dispatch_budget import split
  from hub.source_admission import split as split_source
  from hub.workbench_store import canonical
  f=sources.SourceAdmissionTests();f.setUp();self.addCleanup(f.doCleanups)
  receipt=f.f.evidence(BASE);receipt['plan_sha256']=digest(f.p);f.records[BASE]=receipt;f.configure()
  f.store.record_plan_evidence(f.p['plan_id'],receipt,authority=f.cap)
  f.record(f.candidate(status='unknown'))
  b=budgets.DispatchBudgetTests();b.store=f.store;b.p=f.p;b.plan_cap=f.cap;b.control=f.control
  b.policy={'schema_version':'1.0','lineage_id':f.p['plan_id'],'source_state_sha256':'a'*64,'cutoff_id':'synthetic-cutoff','covered_dispatch_ids':['covered-call'],'observed_model_dispatches':1,'historical_unknown_allowance':8,'closure_reserve':12,'conservative_deterministic_dispatches':0,'limit':480,'provenance_ref':'synthetic/state-cutoff.json'}
  b.initialize();request=b.req();b.reserve(request);b.close(request,outcome='unknown',resources_closed=False)
  def snapshot():
   with f.store._connection()as db:
    raw=f.store._decode(*db.execute('SELECT body,hash FROM plan_inputs').fetchone())
    receipts=[tuple(r)for r in db.execute('SELECT * FROM plan_receipts')]
   history,budget=split(raw);_,source=split_source(history)
   return canonical(budget),canonical(source),receipts
  original=copy.deepcopy(f.p);kept=snapshot();plans=[original];requests=[]
  for n in range(2,19):
   prior=copy.deepcopy(f.p);f.p={**copy.deepcopy(original),'revision':str(n)};f.configure(prior=prior)
   result=f.store.reconcile_plan(f.p,expected_sha256=digest(prior),request_id='history-'+str(n),authority=f.cap)
   requests.append(result['request']);plans.append(copy.deepcopy(f.p))
  self.assertEqual(snapshot(),kept);self.assertEqual(f.store.tasks(),[])
  status=f.store.plan_revision_status(f.p['plan_id'],authority=f.cap)
  self.assertEqual(status['version_hashes'],[digest(p)for p in plans]);self.assertEqual(status['requests'],requests)
  prior=copy.deepcopy(f.p);f.p=original;f.configure(prior=prior)
  f.store.reconcile_plan(f.p,expected_sha256=digest(prior),request_id='history-exact-rollback',authority=f.cap,rollback=True)
  before=f.store.path.read_bytes()
  self.assertTrue(f.store.reconcile_plan(f.p,expected_sha256=digest(prior),request_id='history-exact-rollback',authority=f.cap,rollback=True)['replayed'])
  self.assertEqual(f.store.path.read_bytes(),before);self.assertEqual(snapshot(),kept)
  self.assertEqual(f.store.plan_ready(f.p['plan_id'],authority=f.cap)['accepted'],[BASE])
  self.assertNotIn(sources.GRAPH,f.view()['ready']) # UNKNOWN was not washed away.
  from hub.task_storage_restore import _snapshot
  _snapshot(f.store,before)
 def test_exact_64_versions_128_requests_and_broken_history_fail_without_rewrite(self):
  import sqlite3
  from hub.workbench_store import canonical
  from hub.task_storage_restore import _snapshot
  f=self.fixture();plans=[copy.deepcopy(f.p)]
  for n in range(2,65):
   p={**copy.deepcopy(f.original),'revision':str(n)};self.move(f,p,'capacity-'+str(n));plans.append(p)
  before=f.store.path.read_bytes()
  with self.assertRaises(ServiceError)as error:self.move(f,{**copy.deepcopy(f.original),'revision':'65'},'capacity-over-version')
  self.assertEqual(error.exception.code,'PLAN_HISTORY_CAPACITY');self.assertEqual(f.store.path.read_bytes(),before)
  # Rebind to the actual current plan after the rejected candidate.
  f.p=copy.deepcopy(plans[-1])
  for n in range(65):self.move(f,copy.deepcopy(plans[0]if n%2==0 else plans[-1]),'capacity-rollback-'+str(n),True)
  status=f.store.plan_revision_status(f.p['plan_id'],authority=f.cap)
  self.assertEqual(len(status['version_hashes']),64);self.assertEqual(len(status['requests']),128)
  self.assertEqual(status['version_hashes'],[digest(p)for p in plans]);self.assertEqual(f.store.tasks(),[])
  before=f.store.path.read_bytes();target=copy.deepcopy(plans[-1]if f.p==plans[0]else plans[0])
  with self.assertRaises(ServiceError)as error:self.move(f,target,'capacity-over-request',True)
  self.assertEqual(error.exception.code,'PLAN_HISTORY_CAPACITY');self.assertEqual(f.store.path.read_bytes(),before)
  for mutation in ('hash','chain'):
   with self.subTest(mutation=mutation),sqlite3.connect(':memory:')as db:
    db.deserialize(before);value=json.loads(db.execute('SELECT body FROM plan_inputs').fetchone()[0])
    if mutation=='hash':value['versions'][17]['sha256']='f'*64
    else:value['requests'][17]['from_sha256']='f'*64
    db.execute('UPDATE plan_inputs SET body=?,hash=?',(canonical(value).decode(),digest(value)));db.commit()
    with self.assertRaises(ServiceError):_snapshot(f.store,db.serialize())
 def test_byte_limit_and_near_capacity_sqlite_failure_and_guard_remain_atomic(self):
  from unittest import mock
  import hub.task_store as stores
  self.assertEqual(stores.MAX_DATABASE_BYTES,64*1024*1024)
  f=self.fixture();original=copy.deepcopy(f.p);before=f.store.path.read_bytes();identity=(f.store.path.stat().st_dev,f.store.path.stat().st_ino)
  with mock.patch.object(stores,'MAX_DATABASE_BYTES',len(before)-1):
   with self.assertRaises(ServiceError)as error:f.store.plan_revision_status(f.p['plan_id'],authority=f.cap)
   self.assertEqual(error.exception.code,'TASK_LOCATION_REJECTED')
  self.assertEqual(f.store.path.read_bytes(),before)
  target={**copy.deepcopy(original),'revision':'large'};target['tasks'][1]['objective']='bounded synthetic padding '*10000
  with mock.patch.object(stores,'MAX_DATABASE_BYTES',len(before)+8192):
   with self.assertRaises(ServiceError)as error:self.move(f,target,'capacity-full')
   self.assertEqual(error.exception.code,'TASK_STORAGE_CAPACITY')
  self.assertEqual(f.store.path.read_bytes(),before);self.assertEqual((f.store.path.stat().st_dev,f.store.path.stat().st_ino),identity)
  target={**copy.deepcopy(original),'revision':'guarded'};f.p=original
  def fault(phase):
   if phase=='before_commit':raise OSError('synthetic precommit refusal')
  f.store.fault=fault
  with self.assertRaises(ServiceError)as error:self.move(f,target,'capacity-guard')
  self.assertEqual(error.exception.code,'TASK_NOT_COMMITTED');self.assertEqual(f.store.path.read_bytes(),before)

class PlanHistoryFormatV2Tests(unittest.TestCase):
 def fixture(self):
  f=PlanRevisionTests();f.setUp();self.addCleanup(f.doCleanups);return f
 def raw(self,f):
  with f.store._connection()as db:
   return f.store._decode(*db.execute('SELECT body,hash FROM plan_inputs').fetchone())
 def capability(self,f,*,readers=lambda _:True,authorize=lambda:None):
  from hub.plan_admission import PlanHistoryMigrationAuthority,HISTORY_V2
  command={'request_id':'synthetic-history-conversion','plan_id':f.p['plan_id'],
           'plan_sha256':digest(f.p),'expected_input_sha256':digest(self.raw(f)),
           'reader_set_sha256':'c'*64,'target_format':HISTORY_V2}
  cap=PlanHistoryMigrationAuthority.configured(command,plan_authority=f.cap,authorize=authorize,readers_compatible=readers)
  return command,cap
 def migrate(self,f):
  command,cap=self.capability(f)
  result=f.store.migrate_plan_history(f.p['plan_id'],command,authority=cap)
  self.assertFalse(result['replayed']);self.assertEqual(result['execution_rows_created'],0)
  return command,cap
 def move(self,f,target,request,rollback=False):
  return PlanHistoryCapacityTests.move(self,f,target,request,rollback)
 def test_legacy_reads_and_writes_do_not_migrate_explicit_conversion_replays_without_rewrite(self):
  from hub.plan_admission import plan_history,HISTORY_V2
  f=self.fixture();raw=self.raw(f);before=f.store.path.read_bytes()
  self.assertEqual(plan_history(raw)[0],f.p)
  f.store.plan_revision_status(f.p['plan_id'],authority=f.cap)
  self.assertEqual(f.store.path.read_bytes(),before)
  self.move(f,f.target(),'legacy-revision');self.assertEqual(self.raw(f)['schema_version'],'plan-revisions/1')
  logical=plan_history(self.raw(f));identity=(f.store.path.stat().st_dev,f.store.path.stat().st_ino)
  command,cap=self.migrate(f);converted=self.raw(f)
  self.assertEqual(converted['schema_version'],HISTORY_V2);self.assertEqual(plan_history(converted),logical)
  self.assertEqual((f.store.path.stat().st_dev,f.store.path.stat().st_ino),identity)
  before=f.store.path.read_bytes()
  self.assertTrue(f.store.migrate_plan_history(f.p['plan_id'],command,authority=cap)['replayed'])
  self.assertEqual(f.store.path.read_bytes(),before)
  self.move(f,{**f.p,'revision':'third'},'after-conversion')
  before=f.store.path.read_bytes()
  self.assertTrue(f.store.migrate_plan_history(f.p['plan_id'],command,authority=cap)['replayed'])
  self.assertEqual(f.store.path.read_bytes(),before)
  self.assertEqual(self.raw(f)['migration'],converted['migration']);self.assertEqual(f.store.tasks(),[])
  from hub.task_storage_restore import _snapshot
  _snapshot(f.store,before)

 def test_256_versions_512_requests_cas_replay_and_offline_restore(self):
  from hub.task_storage_restore import _snapshot
  from hub.plan_admission import HISTORY_V2
  f=self.fixture();self.migrate(f);plans=[copy.deepcopy(f.p)];commands=[]
  for n in range(2,257):
   target={**copy.deepcopy(f.original),'revision':str(n)}
   commands.append(self.move(f,target,'v2-version-'+str(n))['request']);plans.append(target)
  for n in range(257):
   target=copy.deepcopy(plans[0]if n%2==0 else plans[-1])
   commands.append(self.move(f,target,'v2-rollback-'+str(n),True)['request'])
  status=f.store.plan_revision_status(f.p['plan_id'],authority=f.cap)
  self.assertEqual(status['version_hashes'],[digest(p)for p in plans]);self.assertEqual(status['requests'],commands)
  self.assertEqual(len(status['requests']),512);self.assertEqual(self.raw(f)['schema_version'],HISTORY_V2)
  before=f.store.path.read_bytes();last=commands[-1]
  self.assertTrue(f.store.reconcile_plan(f.p,expected_sha256=last['from_sha256'],request_id=last['request_id'],authority=f.cap,rollback=True)['replayed'])
  self.assertEqual(f.store.path.read_bytes(),before)
  for target,rollback in (({**f.original,'revision':'257'},False),(plans[-1],True)):
   f.p=plans[0]
   with self.subTest(rollback=rollback),self.assertRaises(ServiceError)as error:self.move(f,target,'v2-overflow',rollback)
   self.assertEqual(error.exception.code,'PLAN_HISTORY_CAPACITY');self.assertEqual(f.store.path.read_bytes(),before)
  self.assertEqual(f.store.tasks(),[])
  f.bind(plans[0],digest(plans[-1]));f.store=TaskStore(f.root)
  self.assertEqual(f.store.plan_revision_status(f.p['plan_id'],authority=f.cap)['requests'],commands)
  _,tables=_snapshot(f.store,before);self.assertEqual(len(tables['plan_inputs']),1)

 def test_compressible_900kb_plans_preserve_all_256_canonical_hashes_and_512_requests(self):
  from hub.plan_admission import history_envelope,plan_history,HISTORY_V2
  from hub.workbench_store import canonical
  # Bounded synthetic source metadata, never a real plan or ledger. Different
  # revisions still reconstruct and validate as 256 complete original plans.
  base=plan([task(BASE),task(POLICY)]);base['synthetic_input']='bounded-source-input\n'*45000
  versions={};requests=[];prior=None
  for n in range(256):
   p={**base,'revision':str(n)};h=digest(p);versions[h]=p
   if prior is not None:requests.append({'request_id':'large-'+str(n),'from_sha256':prior,'to_sha256':h,'operation':'reconcile','affected_task_ids':[]})
   prior=h
  hashes=list(versions)
  for n in range(257):
   target=hashes[0]if n%2==0 else hashes[-1]
   requests.append({'request_id':'large-rollback-'+str(n),'from_sha256':prior,'to_sha256':target,'operation':'rollback','affected_task_ids':[]});prior=target
  command={'request_id':'synthetic-large-conversion','plan_id':base['plan_id'],'plan_sha256':hashes[0],
           'expected_input_sha256':'d'*64,'reader_set_sha256':'c'*64,'target_format':HISTORY_V2}
  encoded=history_envelope(versions[prior],versions,requests,migration=command)
  self.assertEqual(encoded,history_envelope(versions[prior],versions,requests,migration=command))
  self.assertGreater(len(canonical(base)),900000);self.assertLess(len(canonical(encoded)),16*1024*1024)
  current,restored,chain=plan_history(encoded)
  self.assertEqual(list(restored),hashes);self.assertEqual(chain,requests);self.assertEqual(current,versions[prior])
  for h,p in restored.items():self.assertEqual(p,versions[h]);self.assertEqual(digest(p),h)

 def test_malformed_compression_versions_requests_and_bounded_expansion_fail_closed(self):
  import base64,zlib,sqlite3
  from unittest import mock
  from hub import plan_admission as admission
  from hub.workbench_store import canonical
  from hub.task_storage_restore import _snapshot
  f=self.fixture();self.migrate(f);self.move(f,f.target(),'v2-second')
  self.move(f,{**f.p,'revision':'third'},'v2-third');good=self.raw(f);database=f.store.path.read_bytes()
  def compressed(row,raw):
   row['data']=base64.b64encode(zlib.compress(raw)).decode();row['bytes']=len(raw)
  def replace_plan(value,row,p):
   def intern(part):
    raw=canonical(part);h=digest(part)
    value['blobs'][h]={'sha256':h,'codec':'zlib-base64/1','bytes':len(raw),'data':base64.b64encode(zlib.compress(raw,9)).decode()}
    return h
   row.update(sha256=digest(p),bytes=len(canonical(p)),fields={k:intern(v)for k,v in p.items()if k!='tasks'},tasks=[intern(t)for t in p['tasks']])
   used={h for r in value['versions']for h in [*r['fields'].values(),*r['tasks']]}
   value['blobs']={h:r for h,r in value['blobs'].items()if h in used}
  def mutate(value,case):
   version=value['versions'][0];row=value['blobs'][version['fields']['revision']];binary=base64.b64decode(row['data'])
   if case=='truncated':row['data']=base64.b64encode(binary[:-1]).decode()
   elif case=='trailing':row['data']=base64.b64encode(binary+b'ignored').decode()
   elif case=='concatenated':row['data']=base64.b64encode(binary+binary).decode()
   elif case=='bomb':row['data']=base64.b64encode(zlib.compress(b'x'*(admission.MAX_HISTORY_PLAN+1))).decode();row['bytes']=1
   elif case=='base64':row['data']='!'+row['data']
   elif case=='size':row['bytes']=True
   elif case=='large':row['bytes']=admission.MAX_HISTORY_PLAN+1
   elif case=='hash':version['sha256']='f'*64
   elif case=='duplicate':value['versions'].append(copy.deepcopy(version))
   elif case=='reorder':value['versions'][1:]=reversed(value['versions'][1:])
   elif case=='revision':
    p=copy.deepcopy(f.original);p['revision']='2';replace_plan(value,version,p)
   elif case=='foreign':
    p=copy.deepcopy(f.original);p['plan_id']='OTHER-PLAN';replace_plan(value,version,p)
   elif case=='invalid-plan':
    p=copy.deepcopy(f.original);p['tasks'][0]['depends_on']=[POLICY];replace_plan(value,version,p)
   elif case=='noncanonical':compressed(row,b' '+canonical(f.original))
   elif case=='chain':value['requests'][0]['from_sha256']='f'*64
   elif case=='missing-request':value['requests'].pop()
   elif case=='duplicate-request':value['requests'].append(copy.deepcopy(value['requests'][0]))
   elif case=='migration':value['migration']['sha256']='f'*64
  for case in ('truncated','trailing','concatenated','bomb','base64','size','large','hash','duplicate','reorder','revision','foreign','invalid-plan','noncanonical','chain','missing-request','duplicate-request','migration'):
   with self.subTest(case=case):
    value=copy.deepcopy(good);mutate(value,case)
    with self.assertRaises(ServiceError):admission.plan_history(value)
    with sqlite3.connect(':memory:')as db:
     db.deserialize(database);db.execute('UPDATE plan_inputs SET body=?,hash=?',(canonical(value).decode(),digest(value)));db.commit()
     with self.assertRaises(ServiceError):_snapshot(f.store,db.serialize())
  for field in ('MAX_HISTORY_ENCODED','MAX_HISTORY_EXPANDED'):
   with mock.patch.object(admission,field,1),self.assertRaises(ServiceError):admission.plan_history(good)
  self.assertEqual(f.store.path.read_bytes(),database)

 def test_exact_internal_authority_controls_reader_revocation_and_faults_preserve_preimage(self):
  from unittest import mock
  from hub.plan_admission import PlanHistoryMigrationAuthority
  f=self.fixture();command,cap=self.capability(f);before=f.store.path.read_bytes()
  with self.assertRaises(ServiceError):f.store.migrate_plan_history(f.p['plan_id'],command,authority=f.cap)
  for readers in (False,None,1,{'compatible':True}):
   _,denied=self.capability(f,readers=lambda _,v=readers:v)
   with self.subTest(readers=readers),self.assertRaises(ServiceError):f.store.migrate_plan_history(f.p['plan_id'],command,authority=denied)
   self.assertEqual(f.store.path.read_bytes(),before)
  for state in ('paused','cancelled','unknown'):
   f.control={'state':state,'revision':2}
   with self.assertRaises(ServiceError):f.store.migrate_plan_history(f.p['plan_id'],command,authority=cap)
   self.assertEqual(f.store.path.read_bytes(),before)
  f.control={'state':'active','revision':3}
  for field in ('expected_input_sha256','plan_sha256','reader_set_sha256','request_id'):
   changed={**command,field:'other-request'if field=='request_id'else 'f'*64}
   with self.subTest(field=field),self.assertRaises(ServiceError):f.store.migrate_plan_history(f.p['plan_id'],changed,authority=cap)
   self.assertEqual(f.store.path.read_bytes(),before)
  changed={**command,'expected_input_sha256':'f'*64}
  stale=PlanHistoryMigrationAuthority.configured(changed,plan_authority=f.cap,authorize=lambda:None,readers_compatible=lambda _:True)
  with self.assertRaises(ServiceError):f.store.migrate_plan_history(f.p['plan_id'],changed,authority=stale)
  for phase in ('before_plan_history_write','before_plan_history_commit','before_commit'):
   def fail(where):
    if where==phase:raise OSError('synthetic migration rollback')
   f.store.fault=fail
   with self.subTest(phase=phase),self.assertRaises((OSError,ServiceError)):f.store.migrate_plan_history(f.p['plan_id'],command,authority=cap)
   self.assertEqual(f.store.path.read_bytes(),before)
  allowed=[True];_,revocable=self.capability(f,readers=lambda _:allowed[0])
  def revoke(phase):
   if phase=='before_commit':allowed[0]=False
  f.store.fault=revoke
  with self.assertRaises(ServiceError):f.store.migrate_plan_history(f.p['plan_id'],command,authority=revocable)
  self.assertEqual(f.store.path.read_bytes(),before)
  def pause(phase):
   if phase=='before_commit':f.control={'state':'paused','revision':4}
  f.store.fault=pause
  with self.assertRaises(ServiceError):f.store.migrate_plan_history(f.p['plan_id'],command,authority=cap)
  self.assertEqual(f.store.path.read_bytes(),before)
  f.control={'state':'active','revision':5};f.store.fault=lambda _:None
  with mock.patch.object(f.store,'_replace_plan_input',side_effect=OSError('synthetic second write')):
   with self.assertRaises(OSError):f.store.migrate_plan_history(f.p['plan_id'],command,authority=cap)
  self.assertEqual(f.store.path.read_bytes(),before)

 def test_unknown_commit_replay_source_budget_and_receipts_survive_conversion(self):
  import test_hub_source_admission as sources
  import test_hub_dispatch_budget as budgets
  from hub.dispatch_budget import split
  from hub.source_admission import split as split_source
  from hub.plan_admission import plan_history
  from hub.workbench_store import canonical
  from hub.task_storage_restore import _snapshot
  f=sources.SourceAdmissionTests();f.setUp();self.addCleanup(f.doCleanups)
  receipt=f.f.evidence(BASE);receipt['plan_sha256']=digest(f.p);f.records[BASE]=receipt;f.configure()
  f.store.record_plan_evidence(f.p['plan_id'],receipt,authority=f.cap);f.record(f.candidate(status='unknown'))
  b=budgets.DispatchBudgetTests();b.store=f.store;b.p=f.p;b.plan_cap=f.cap;b.control=f.control
  b.policy={'schema_version':'1.0','lineage_id':f.p['plan_id'],'source_state_sha256':'a'*64,'cutoff_id':'synthetic-cutoff','covered_dispatch_ids':['covered-call'],'observed_model_dispatches':1,'historical_unknown_allowance':8,'closure_reserve':12,'conservative_deterministic_dispatches':0,'limit':480,'provenance_ref':'synthetic/state-cutoff.json'}
  b.initialize();request=b.req();b.reserve(request);b.close(request,outcome='unknown',resources_closed=False)
  def retained():
   history,budget=split(self.raw(f));_,source=split_source(history)
   with f.store._connection()as db:receipts=[tuple(r)for r in db.execute('SELECT *FROM plan_receipts')]
   return canonical(budget),canonical(source),receipts
  kept=retained();logical=plan_history(self.raw(f));command,cap=self.capability(f)
  def lost(phase):
   if phase=='after_commit':raise OSError('synthetic lost conversion reply')
  f.store.fault=lost
  with self.assertRaises(ServiceError)as error:f.store.migrate_plan_history(f.p['plan_id'],command,authority=cap)
  self.assertEqual(error.exception.outcome,'UNKNOWN');f.store.fault=lambda _:None
  self.assertEqual(retained(),kept);self.assertEqual(plan_history(self.raw(f)),logical)
  before=f.store.path.read_bytes();self.assertTrue(f.store.migrate_plan_history(f.p['plan_id'],command,authority=cap)['replayed'])
  self.assertEqual(f.store.path.read_bytes(),before);self.assertNotIn(sources.GRAPH,f.view()['ready'])
  prior=copy.deepcopy(f.p);f.p={**copy.deepcopy(prior),'revision':'v2-wrapper-revision'};f.configure(prior=prior)
  f.store.reconcile_plan(f.p,expected_sha256=digest(prior),request_id='v2-wrapper-change',authority=f.cap)
  newer=copy.deepcopy(f.p);f.p=prior;f.configure(prior=newer)
  f.store.reconcile_plan(prior,expected_sha256=digest(newer),request_id='v2-wrapper-rollback',authority=f.cap,rollback=True)
  self.assertEqual(retained(),kept);self.assertNotIn(sources.GRAPH,f.view()['ready'])
  self.assertEqual(f.store.tasks(),[]);_snapshot(f.store,f.store.path.read_bytes())

 def test_v2_byte_cap_and_sidecars_remain_hard_and_failed_growth_is_atomic(self):
  from unittest import mock
  import hub.task_store as stores
  from hub.plan_admission import MAX_HISTORY_PLAN
  f=self.fixture();self.migrate(f);before=f.store.path.read_bytes()
  self.assertEqual(stores.MAX_DATABASE_BYTES,64*1024*1024)
  # Use the same production page-limit/transaction mechanism at a small test
  # ceiling; do not allocate a live 64MiB ledger just to test a rejection.
  target={**copy.deepcopy(f.p),'revision':'large-v2','synthetic_input':''.join(digest(str(i))for i in range(4000))}
  with mock.patch.object(stores,'MAX_DATABASE_BYTES',len(before)+8192):
   with self.assertRaises(ServiceError)as error:self.move(f,target,'v2-byte-full')
   self.assertEqual(error.exception.code,'TASK_STORAGE_CAPACITY')
  self.assertEqual(f.store.path.read_bytes(),before)
  f.p=f.original
  with self.assertRaises(ServiceError):self.move(f,{**f.p,'revision':'oversize','synthetic_input':'x'*MAX_HISTORY_PLAN},'v2-plan-full')
  self.assertEqual(f.store.path.read_bytes(),before)
  sidecar=Path(str(f.store.path)+'-wal')
  try:
   with sidecar.open('wb')as stream:stream.truncate(stores.MAX_DATABASE_BYTES+1)
   with self.assertRaises(ServiceError)as error:f.store.plan_revision_status(f.original['plan_id'],authority=f.cap)
   self.assertEqual(error.exception.code,'TASK_LOCATION_REJECTED')
  finally:sidecar.unlink()
  self.assertEqual(f.store.path.read_bytes(),before)

 def test_representative_1mb_256_changed_task_versions_512_requests_and_peak_memory(self):
  import base64,hashlib,resource,time,zlib
  from hub.plan_admission import history_envelope,plan_history,changed_definitions
  from hub.workbench_store import canonical
  from hub.task_storage_restore import _snapshot
  started=time.monotonic()
  rows=[task(BASE),task(POLICY)]+[task('P5-SYNTHETIC-'+str(i))for i in range(257)]
  for i,row in enumerate(rows):
   row['objective']=hashlib.sha256(('objective-'+str(i)).encode()).hexdigest()
   row['input_fingerprints']={f'input-{j:03d}':hashlib.sha256(f'synthetic-input-{i}-{j}'.encode()).hexdigest()for j in range(40)}
  base=plan(rows);base['revision']='representative-000';base['synthetic_plan_context']=''
  padding=1111746-len(canonical(base));self.assertGreater(padding,0)
  base['synthetic_plan_context']=''.join(hashlib.sha256(f'synthetic-context-{j}'.encode()).hexdigest()for j in range((padding+63)//64))[:padding]
  self.assertEqual(len(canonical(base)),1111746)
  f=PlanStoreTests();f.setUp();self.addCleanup(f.doCleanups);f.p=base;f.configure();f.store.import_plan(base,authority=f.cap)
  command,cap=self.capability(f)
  f.store.migrate_plan_history(base['plan_id'],command,authority=cap)
  versions={digest(base):base};requests=[];previous=base;prior=digest(base)
  for n in range(1,256):
   tasks=list(previous['tasks']);tasks[n+1]={**tasks[n+1],'objective':hashlib.sha256(f'changed-task-{n}'.encode()).hexdigest()}
   target={**base,'revision':f'representative-{n:03d}','tasks':tasks};h=digest(target)
   requests.append({'request_id':'representative-'+str(n),'from_sha256':prior,'to_sha256':h,'operation':'reconcile','affected_task_ids':[tasks[n+1]['id']]})
   versions[h]=target;previous=target;prior=h
  hashes=list(versions);affected=changed_definitions(base,previous);self.assertEqual(len(affected),255)
  for n in range(257):
   target=hashes[0]if n%2==0 else hashes[-1]
   requests.append({'request_id':'representative-rollback-'+str(n),'from_sha256':prior,'to_sha256':target,'operation':'rollback','affected_task_ids':list(affected)});prior=target
  encoded=history_envelope(versions[prior],versions,requests,migration=command)
  raw=canonical(encoded);logical_bytes=sum(r['bytes']for r in encoded['versions'])
  independent=len(base64.b64encode(zlib.compress(canonical(base),9)))*256
  self.assertGreater(independent,64*1024*1024)
  self.assertGreater(logical_bytes,256*1024*1024)
  self.assertLess(len(raw),16*1024*1024)
  self.assertEqual(raw,canonical(history_envelope(versions[prior],versions,requests,migration=command)))
  current,restored,chain=plan_history(encoded)
  self.assertEqual(current,versions[prior]);self.assertEqual(list(restored),hashes);self.assertEqual(chain,requests)
  for h in hashes:
   p=restored[h];self.assertEqual(digest(p),h);self.assertEqual(p,versions[h])
  changed=restored[hashes[0]];changed['tasks'][2]['objective']='caller-local-edit'
  self.assertEqual(restored[hashes[0]],base);self.assertEqual(restored[hashes[-1]],previous)
  # Install only this wholly synthetic snapshot in an isolated store to run
  # the existing offline validator, not a live migration/adoption assertion.
  inode=f.store.path.stat().st_ino
  with f.store._connection(True)as db:f.store._replace_plan_input(db,base['plan_id'],encoded)
  self.assertEqual(f.store.path.stat().st_ino,inode);self.assertLess(f.store.path.stat().st_size,64*1024*1024)
  before=f.store.path.read_bytes();_,tables=_snapshot(f.store,before)
  self.assertEqual(len(tables['plan_inputs']),1);self.assertEqual(f.store.path.read_bytes(),before);self.assertEqual(f.store.tasks(),[])
  for suffix in ('-wal','-shm','-journal'):
   path=Path(str(f.store.path)+suffix)
   if path.exists():self.assertLessEqual(path.stat().st_size,64*1024*1024)
  print('HISTORY_SHARED_CAPACITY '+json.dumps({'versions':len(hashes),'requests':len(chain),
   'plan_bytes':len(canonical(base)),'logical_plan_bytes':logical_bytes,'independent_compressed_estimate':independent,
   'encoded_bytes':len(raw),'unique_blob_bytes':sum(r['bytes']for r in encoded['blobs'].values()),
   'database_bytes':len(before),'process_peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
   'seconds':time.monotonic()-started}),flush=True)

 def test_shared_references_are_exact_typed_acyclic_bounded_and_detached(self):
  import base64,zlib
  from unittest import mock
  import hub.plan_admission as admission
  from hub.workbench_store import canonical
  f=self.fixture();self.migrate(f);self.move(f,f.target(),'shared-second');good=self.raw(f)
  def add(value,part):
   raw=canonical(part);h=digest(part)
   value['blobs'][h]={'sha256':h,'codec':'zlib-base64/1','bytes':len(raw),'data':base64.b64encode(zlib.compress(raw,9)).decode()}
   return h
  for case in ('missing','path','wrong-type','cycle','preimage','unused','field-tasks','depth','nodes','amplification'):
   value=copy.deepcopy(good);row=value['versions'][0]
   if case=='missing':row['tasks'][0]='f'*64
   elif case=='path':row['tasks'][0]={'path':['tasks',0]}
   elif case=='wrong-type':row['tasks'][0]=add(value,'not a task')
   elif case=='cycle':row['tasks'][0]=row['sha256'] # Version hashes are never blob links.
   elif case=='preimage':row['tasks'][0]=value['versions'][1]['tasks'][1]
   elif case=='unused':add(value,'unreferenced content')
   elif case=='field-tasks':row['fields']['tasks']=row['tasks'][0]
   elif case=='depth':
    nested='x'
    for _ in range(admission.MAX_HISTORY_JSON_DEPTH+1):nested=[nested]
    row['fields']['revision']=add(value,nested)
   elif case=='nodes':row['fields']['revision']=add(value,[0]*(admission.MAX_HISTORY_JSON_NODES+1))
   elif case=='amplification':
    large={**f.p['tasks'][0],'padding':'x'*1000000};ref=add(value,large);row['tasks']=[ref]*512
   with self.subTest(case=case),self.assertRaises(ServiceError):admission.plan_history(value)
  for field in ('MAX_HISTORY_BLOBS','MAX_HISTORY_FIELDS','MAX_HISTORY_SHARED_BYTES','MAX_HISTORY_JSON_NODES','MAX_HISTORY_JSON_DEPTH'):
   with mock.patch.object(admission,field,1),self.subTest(bound=field),self.assertRaises(ServiceError):admission.plan_history(good)
  # Identical mutable values may share a blob, but returned fields and
  # versions must never alias one another or mutate retained history.
  target={**copy.deepcopy(f.p),'revision':'shared-alias','field_a':{'nested':[1]},'field_b':{'nested':[1]}}
  self.move(f,target,'shared-alias');current,versions,_=admission.plan_history(self.raw(f));h=digest(target)
  current['field_a']['nested'].append(2)
  self.assertEqual(current['field_b'],{'nested':[1]});self.assertEqual(versions[h],target)

class PlanHistoryRawCompatibilityTests(unittest.TestCase):
 raw=PlanHistoryFormatV2Tests.raw
 capability=PlanHistoryFormatV2Tests.capability
 migrate=PlanHistoryFormatV2Tests.migrate
 def fixture(self,value=None):
  f=PlanRevisionTests();PlanStoreTests.setUp(f);self.addCleanup(f.doCleanups)
  if value is not None:f.p=copy.deepcopy(value)
  f.original=copy.deepcopy(f.p);f.version_records={};f.configure();return f
 def move(self,f,target,request,rollback=False):
  return PlanHistoryCapacityTests.move(self,f,target,request,rollback)
 def raw_cases(self):
  for schema in ('plan-revisions/2','plan-revisions/3','plan-revisions/future',None,{'optional':'metadata'}):
   yield {**plan([task(BASE),task(POLICY)]),'schema_version':schema}
  # Old raw plans could also carry names now used by envelopes. They remain
  # literal metadata, even when it resembles a conversion receipt or codec.
  yield {**plan([task(BASE),task(POLICY)]),'schema_version':'plan-revisions/2',
         'encoding':'plan-fields/1','blobs':{},'versions':[],'requests':[],
         'current_sha256':'f'*64,'migration':{'request':{'forged':True}}}

 def test_old_raw_schema_metadata_import_replay_cold_read_and_offline_snapshot(self):
  from hub.plan_admission import plan_history,is_history_v2
  from hub.task_storage_restore import _snapshot
  for p in self.raw_cases():
   with self.subTest(schema=p['schema_version'],collision='encoding'in p):
    self.assertEqual(validate_plan(p),p)
    self.assertEqual(plan_history(p),(p,{digest(p):p},[]));self.assertFalse(is_history_v2(p))
    f=self.fixture(p);result=f.store.import_plan(p,authority=f.cap)
    self.assertEqual(result['execution_rows_created'],0);self.assertEqual(self.raw(f),p)
    before=f.store.path.read_bytes();f.store=TaskStore(f.root)
    status=f.store.plan_revision_status(p['plan_id'],authority=f.cap)
    self.assertEqual(status['version_hashes'],[digest(p)]);self.assertEqual(status['requests'],[])
    self.assertEqual(f.store.plan_ready(p['plan_id'],authority=f.cap)['accepted'],[])
    self.assertTrue(f.store.import_plan(p,authority=f.cap)['replayed'])
    self.assertEqual(f.store.tasks(),[]);_snapshot(f.store,before)
    self.assertEqual(f.store.path.read_bytes(),before)

 def test_raw_collision_reconcile_stays_v1_rollback_and_replay_keep_metadata(self):
  from hub.plan_admission import plan_history,is_history_v2
  for p in self.raw_cases():
   with self.subTest(schema=p['schema_version'],collision='encoding'in p):
    f=self.fixture(p);f.store.import_plan(p,authority=f.cap)
    target={**copy.deepcopy(p),'revision':'next','schema_version':'plan-revisions/2'}
    target['tasks'][1]['objective']='Exact new definition'
    self.move(f,target,'raw-next');body=self.raw(f)
    self.assertEqual(body['schema_version'],'plan-revisions/1');self.assertFalse(is_history_v2(body))
    self.assertNotIn('migration',body);self.assertNotIn('blobs',body)
    current,versions,requests=plan_history(body)
    self.assertEqual(current,target);self.assertEqual(versions,{digest(p):p,digest(target):target})
    self.assertEqual(requests[0]['affected_task_ids'],[POLICY])
    before=f.store.path.read_bytes()
    self.assertTrue(f.store.reconcile_plan(target,expected_sha256=digest(p),request_id='raw-next',authority=f.cap)['replayed'])
    self.assertEqual(f.store.path.read_bytes(),before)
    self.move(f,p,'raw-back',True)
    self.assertEqual(plan_history(self.raw(f))[0],p);self.assertEqual(f.store.tasks(),[])

 def test_raw_v2_named_plan_only_converts_with_exact_internal_migration(self):
  from hub.plan_admission import plan_history,is_history_v2
  p=list(self.raw_cases())[-1];f=self.fixture(p);f.store.import_plan(p,authority=f.cap)
  command,cap=self.capability(f);before=f.store.path.read_bytes()
  with self.assertRaises(ServiceError)as error:f.store.migrate_plan_history(p['plan_id'],command,authority=f.cap)
  self.assertEqual(error.exception.code,'PLAN_HISTORY_AUTHORITY_REQUIRED');self.assertEqual(f.store.path.read_bytes(),before)
  self.migrate(f);body=self.raw(f);self.assertTrue(is_history_v2(body))
  self.assertEqual(plan_history(body),(p,{digest(p):p},[]))
  self.assertEqual(body['migration']['request'],command)
  self.move(f,{**copy.deepcopy(p),'revision':'migrated-next'},'migrated-next')
  self.assertTrue(is_history_v2(self.raw(f)));before=f.store.path.read_bytes()
  self.assertTrue(f.store.migrate_plan_history(p['plan_id'],command,authority=cap)['replayed'])
  self.assertEqual(f.store.path.read_bytes(),before)

 def test_envelopes_are_not_ordinary_plan_inputs_and_v1_stays_strict(self):
  from hub.plan_admission import plan_history
  from hub.task_storage_restore import _snapshot
  f=self.fixture();f.store.import_plan(f.p,authority=f.cap);self.move(f,f.target(),'strict-v1')
  v1=self.raw(f);self.migrate(f);v2=self.raw(f)
  cases=[v1,v2,{**plan([task(BASE)]),'schema_version':'plan-revisions/1'}]
  for envelope in (v1,v2):
   broken=copy.deepcopy(envelope);broken.pop('versions');cases.append(broken)
   broken=copy.deepcopy(envelope);broken['current_sha256']='f'*64;cases.append(broken)
   broken=copy.deepcopy(envelope);broken['schema_version']='plan-revisions/future';cases.append(broken)
   broken=copy.deepcopy(envelope);broken['revision']='raw-looking-but-incomplete';cases.append(broken)
  # Exact v1 remains reserved even if all raw keys are grafted onto it.
  cases.append({**v1,**plan([task(BASE)]),'schema_version':'plan-revisions/1'})
  for i,value in enumerate(cases):
   with self.subTest(case=i):
    if i>=2:
     with self.assertRaises(ServiceError):plan_history(value)
    empty=self.fixture(value);before=empty.store.path.read_bytes()
    with self.assertRaises(ServiceError):empty.store.import_plan(value,authority=empty.cap)
    self.assertEqual(empty.store.path.read_bytes(),before)
    old=plan_history(self.raw(f))[0];f.bind(value,digest(old));before=f.store.path.read_bytes()
    with self.assertRaises(ServiceError):f.store.reconcile_plan(value,expected_sha256=digest(old),request_id='forged-'+str(i),authority=f.cap)
    self.assertEqual(f.store.path.read_bytes(),before);_snapshot(f.store,before)
  self.assertEqual(f.store.tasks(),[])

 def test_readable_raw_import_and_reconcile_faults_preserve_database_bytes(self):
  p=list(self.raw_cases())[-1];f=self.fixture(p);before=f.store.path.read_bytes()
  def fail(phase):
   if phase=='before_plan_import_commit':raise OSError('synthetic raw import refusal')
  f.store.fault=fail
  with self.assertRaises(OSError):f.store.import_plan(p,authority=f.cap)
  self.assertEqual(f.store.path.read_bytes(),before)
  f.store.fault=lambda _:None;f.store.import_plan(p,authority=f.cap);before=f.store.path.read_bytes()
  def fail(phase):
   if phase=='before_plan_revision_commit':raise OSError('synthetic raw reconcile refusal')
  f.store.fault=fail
  with self.assertRaises(OSError):self.move(f,{**copy.deepcopy(p),'revision':'not-committed'},'raw-fault')
  self.assertEqual(f.store.path.read_bytes(),before);self.assertEqual(self.raw(f),p)
