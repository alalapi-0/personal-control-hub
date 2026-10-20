"""Real disposable TaskStore/registry tests; host/runtime facts are injected explicitly."""
import copy
import json
from pathlib import Path
import sys
import time
import unittest
from unittest import mock
from dataclasses import FrozenInstanceError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub.rebuild_contracts import RootAuthority,validate,receipt_binding,settled_evidence
from hub.task_service import TaskService,root_identity
from hub.service_contract import ServiceError
from hub.workbench_store import digest,canonical
from hub.ordinary_tasks import _version,validate_grant,validate_scoped_write_grant,observe_scoped_paths,scoped_input_version
import test_hub_ordinary_worker as fixtures


class RebuildContractsTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.OrdinaryWorkerTests()
        create=fixtures.tempfile.TemporaryDirectory
        with mock.patch.object(fixtures.tempfile,'TemporaryDirectory',side_effect=lambda *a,**k:create(*a,prefix='pch-contract4-',**k)):self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups);self.store=self.fixture.store;self.grant=self.fixture.grant
        self.now=time.time();self.clock=mock.patch('time.time',side_effect=lambda:self.now);self.clock.start();self.addCleanup(self.clock.stop)
        self.auth=mock.Mock();self.writer=mock.Mock(return_value=True);self.sandbox=mock.Mock(return_value=True)
        self.workspace={'repository':str(self.fixture.root),'worktree':str(self.fixture.root),'branch':'synthetic-main','baseline_commit':'a'*40,'worktree_identity':root_identity(self.fixture.root)}
        self.observed_workspace=copy.deepcopy(self.workspace)
        self.meta={'project_id':self.grant['project_id'],'source_ref':'synthetic-source','source_version':'b'*64,'fact_time':None,'unknown_reason':'Synthetic fixture has no business timestamp','collected_at':self.now,'origin':'synthetic_fixture','compatibility':{'major':2,'minimum_reader':'2.0'}}
        self.package={'schema_version':'2.0','kind':'task_package','task_id':'contract-task','lineage':{'goal_id':'synthetic-goal','original_task_id':'contract-task'},'phase_id':'P2','round_id':'P2-R1','project_id':self.grant['project_id'],'plan':{'revision':'synthetic-v2','sha256':'a'*64},'binding':{k:self.grant[k] for k in ('root','root_identity','registry_hash','input_version','input_files')},'route':{'owner_route':'route:synthetic-project','native_thread_id':'synthetic-native-thread','session_id':None},'effects':{'mode':'read_only','writable_scope':[]},'workspace':self.workspace,'allowed_modules':['README.md'],'source_versions':{'README.md':self.grant['input_files'][0]['sha256']},'grant':{**{k:self.grant[k] for k in ('grant_id','version','source','auth','expires_at')},'sha256':digest(self.grant)},'sandbox':{'version':'1.0','profile':'ordinary_readonly_v1','read_paths':['README.md'],'write_paths':[]},'dependencies':[],'acceptance':[{'check_id':x,'expected':'Controlled source verified','required':True} for x in ('first','second')],'risk':{'lane':'GOVERNED','review_ref':'evidence/review.json'},'budget':{'remaining_attempts':2,'remaining_seconds':1800,'timeout_seconds':900,'capacity':2,'deadline':self.now+2000},'retry':{'max_no_progress':2,'max_transient_retries':1,'idempotency_key':'synthetic-idempotency'},'rollback':{'strategy':'Keep original sources','preserve_paths':['README.md']},'receipt_target':'evidence/receipt.json',**self.meta}
        for k in ('delivery','integration','sync'):self.package[k]={'required':True,'allow_not_applicable':False,'target':'synthetic-local'}
        self.executor={'kind':'deterministic_host','host_id':'synthetic-host','thread_id':None,'turn_id':None,'session_id':None,'pid':12345,'start_ticks':'123'}
        self.configure()

    def configure(self,proofs=None):
        self.binding={'package_sha256':digest(self.package),'route':self.package['route'],'project_id':self.package['project_id'],'root':self.grant['root'],'owner_context':'synthetic-owner','executor':self.executor,'verified_dependencies':proofs or {}}
        self.authority=RootAuthority.configured(self.binding,authorize=self.auth,workspace_check=lambda _:copy.deepcopy(self.observed_workspace),sandbox_check=self.sandbox,writer_check=self.writer)
        self.service=TaskService(None,None,self.store,rebuild_authority=self.authority)

    def prepare(self):return self.service.prepare_rebuild(self.package,owner_context='synthetic-owner')
    def start(self,seconds=60):
        self.prepare();self.claim=self.store.claim_rebuild(self.package['task_id'],'synthetic-worker',authority=self.authority)
        self.attempt={'schema_version':'2.0','kind':'attempt','attempt_id':'attempt-1','task_id':self.package['task_id'],'package_sha256':digest(self.package),'lease':{k:self.claim[k] for k in ('owner','generation','fence','expires_at')},'coverage':{'claim_started_at':self.claim['started_at'],'observed_until':self.claim['expires_at']},'executor':copy.deepcopy(self.executor),'effect_intent':self.package['effects'],'budget_reservation':{'seconds':seconds,'attempts':1},'started_at':self.now,**self.meta}
        if self.package['effects']['mode']=='scoped_write':self.attempt['write_baseline_sha256']=digest(self.package['write_baselines'])
        return self.admit()
    def admit(self,a=None):return self.service.admit_rebuild(self.package,a or self.attempt,owner_context='synthetic-owner')
    def receipt(self):
        v={k:self.grant[k] for k in ('root_identity','input_version')}
        r={'schema_version':'2.0','kind':'receipt','receipt_id':'receipt-1','task_id':self.package['task_id'],'attempt_id':self.attempt['attempt_id'],'package_sha256':digest(self.package),'attempt_sha256':digest(self.attempt),'claim':{'generation':self.claim['generation'],'fence':self.claim['fence'],'claimed_at':self.claim['started_at']},'actual_baseline':self.workspace['baseline_commit'],'started_at':self.attempt['started_at'],'ended_at':self.now,'budget_coverage':{'reserved_seconds':self.attempt['budget_reservation']['seconds'],'actual_seconds':self.now-self.attempt['started_at']},'start_versions':v,'end_versions':copy.deepcopy(v),'artifacts':[],'checks':[{'check_id':x,'command':'Synthetic bounded check '+x,'scope':['README.md'],'candidate_hash':self.package['source_version'],'exit':0,'evidence_ref':'evidence/'+x+'.json'} for x in ('first','second')],'evidence_refs':['evidence/checks.json'],'effect_state':self.package['effects']['mode'],'unknowns':[],'next_action':'Root independent review','producer':{'kind':self.executor['kind'],'self_report':False},**self.meta}
        for k in ('delivery','integration','sync'):r[k]={'status':'passed','source_revision':self.package['source_version'],'source_at':None,'collected_at':self.now,'stored_at':self.now,'projected_at':None,'evidence_ref':'evidence/'+k+'.json'}
        if self.package['effects']['mode']=='scoped_write':
            r['write_states']=observe_scoped_paths(self.workspace['worktree'],self.workspace['worktree_identity'],self.grant['write_paths'])
        return r
    def record(self,r=None):return self.service.record_rebuild_receipt(r or self.receipt(),owner_context='synthetic-owner')
    def proof(self,r):return {'task_id':self.package['task_id'],'package_sha256':digest(self.package),'attempt_sha256':digest(self.attempt),'receipt_sha256':digest(r),'source':'root_verified','evidence_ref':'evidence/root-verification.json','evidence_sha256':'d'*64,'expires_at':self.now+120}

    def test_positive_existing_store_restart_replay_and_no_settlement(self):
        self.start();self.assertTrue(self.admit()['replayed']);before=self.store.task(self.package['task_id'])
        r=self.receipt();self.assertEqual(self.record(r),{'replayed':False,'settled':False});self.assertTrue(self.record(r)['replayed'])
        from hub.task_store import TaskStore
        restarted=TaskStore(self.store.root);self.assertEqual(restarted.rebuild_package(self.package['task_id']),self.package);self.assertEqual(restarted.task(self.package['task_id']),before)
        self.assertIsNone(self.store.claim('legacy-worker')) # No write/read contract executor enabled.

    def test_exact_attempt_replay_after_heartbeat_is_passive_and_conflict_rejects(self):
        self.start();self.now+=3;self.store.heartbeat(self.package['task_id'],'synthetic-worker');self.assertTrue(self.admit()['replayed'])
        a=copy.deepcopy(self.attempt);a['budget_reservation']['seconds']=20
        with self.assertRaises(ServiceError):self.admit(a)
        claim=self.claim
        self.assertEqual(claim['fence'],self.attempt['lease']['fence'])

    def test_unknown_versions_fields_and_no_authority_expansion(self):
        self.start()
        for kind,value in [('package',self.package),('attempt',self.attempt),('receipt',self.receipt())]:
            for changes in ({'schema_version':'99.0'},{'allow_all':True}):
                with self.subTest(kind=kind),self.assertRaises(ServiceError):validate({**value,**changes},kind)
        for key in ('phase_id','round_id','workspace','allowed_modules','source_versions','grant','sandbox'):
            p=copy.deepcopy(self.package);p.pop(key)
            with self.subTest(key=key),self.assertRaises(ServiceError):validate(p,'package')

    def test_stale_root_input_plan_and_forged_route_or_executor(self):
        for key in ('root','root_identity','registry_hash','input_version'):
            p=copy.deepcopy(self.package);p['binding'][key]='e'*64
            with self.subTest(key=key),self.assertRaises(ServiceError):self.service.prepare_rebuild(p,owner_context='synthetic-owner')
        self.observed_workspace['branch']='changed'
        with self.assertRaises(ServiceError):self.prepare()
        self.observed_workspace=copy.deepcopy(self.workspace);self.start();a=copy.deepcopy(self.attempt);a['executor']['pid']=1
        with self.assertRaises(ServiceError):self.admit(a)

    def test_auth_grant_sandbox_and_unknown_writer_fail_closed(self):
        with self.assertRaises(ServiceError):self.service.prepare_rebuild(self.package,owner_context='synthetic-owner',authorize=lambda:None)
        self.auth.side_effect=ServiceError('DENIED')
        with self.assertRaises(ServiceError):self.prepare()
        self.auth.side_effect=None
        for callback in (self.writer,self.sandbox):
            callback.return_value=None
            with self.assertRaises(ServiceError):self.prepare()
            callback.return_value=True
        with self.store._connection(True) as c:c.execute('DELETE FROM grants')
        with self.assertRaises(ServiceError):self.prepare()

    def test_authority_is_defensively_frozen(self):
        self.binding['package_sha256']='e'*64;self.binding['executor']['kind']='model'
        # Separate fixture executor object was intentionally mutable; capability retained bytes.
        self.assertEqual(self.authority.snapshot()['executor']['kind'],'deterministic_host')
        view=self.service.rebuild_binding;view['package_sha256']='f'*64
        with self.assertRaises(AttributeError):self.service.rebuild_binding=view
        with self.assertRaises(FrozenInstanceError):self.authority._data=b'forged'
        self.prepare()

    def test_dependencies_capacity_budget_deadline_pause_cancel_and_lease(self):
        self.start();self.store.cancel(self.package['task_id'],worker_owner='synthetic-worker')
        with self.assertRaises(ServiceError):self.record()
        self.assertEqual(self.store.task(self.package['task_id'])['cancel'],1)

    def test_receipt_conflict_ambiguity_tamper_and_exit_zero_never_complete(self):
        self.start();r=self.receipt();self.record(r)
        for changes in ({'receipt_id':'other'},{'next_action':'different'},{'attempt_sha256':'e'*64}):
            with self.subTest(changes=changes),self.assertRaises(ServiceError):self.record({**r,**changes})
        self.assertEqual(self.store.task(self.package['task_id'])['status'],'running')

    def test_input_change_before_commit_rolls_back_all_contract_events(self):
        called=[0]
        def auth():
            called[0]+=1
            if called[0]==4:self.fixture.source.write_text('Changed synthetic source')
        self.auth.side_effect=auth
        with self.assertRaises(ServiceError):self.prepare()
        self.assertIsNone(self.store.task(self.package['task_id']))

    def test_actual_completed_dependency_receipt_chain_is_required(self):
        self.start();r=self.receipt();self.record(r);proof=self.proof(r)
        settled_evidence(self.package,r,proof,self.attempt)
        for change in ('subset','empty','pending','unknown','raw','cancelled_proof'):
            bad=copy.deepcopy(r);pr=copy.deepcopy(proof)
            if change=='subset':bad['checks']=bad['checks'][:1]
            if change=='empty':bad['checks']=[]
            if change=='pending':bad['sync']['status']='pending'
            if change=='unknown':bad['effect_state']='unknown'
            if change=='raw':bad['producer']['self_report']=True
            if change=='cancelled_proof':pr['source']='writer_report'
            pr['receipt_sha256']=digest(bad)
            with self.subTest(change=change),self.assertRaises(ServiceError):settled_evidence(self.package,bad,pr,self.attempt)

    def test_actual_store_dependency_admission_and_required_conditions(self):
        self.start();r=self.receipt();self.record(r);proof=self.proof(r)
        self.store.update(self.package['task_id'],'synthetic-worker',status='validating')
        self.store.update(self.package['task_id'],'synthetic-worker',status='checks_complete')
        depid=self.package['task_id'];dep_hash=digest(r)
        self.package=copy.deepcopy(self.package);self.package['task_id']='dependent-task';self.package['lineage']['original_task_id']='dependent-task'
        self.package['dependencies']=[{'task_id':depid,'receipt_sha256':dep_hash}]
        self.configure({dep_hash:proof});self.start()
        self.assertEqual(self.store.task('dependent-task')['status'],'running')

    def test_stale_dependency_without_root_provenance_is_rejected(self):
        self.package['dependencies']=[{'task_id':'not-complete','receipt_sha256':'e'*64}];self.configure()
        self.prepare();self.claim=self.store.claim_rebuild(self.package['task_id'],'synthetic-worker',authority=self.authority)
        # Build a genuine bound attempt via the existing fixture helper, preserving claim.
        self.attempt={'schema_version':'2.0','kind':'attempt','attempt_id':'attempt-1','task_id':self.package['task_id'],'package_sha256':digest(self.package),'lease':{k:self.claim[k] for k in ('owner','generation','fence','expires_at')},'coverage':{'claim_started_at':self.claim['started_at'],'observed_until':self.claim['expires_at']},'executor':self.executor,'effect_intent':self.package['effects'],'budget_reservation':{'seconds':60,'attempts':1},'started_at':self.now,**self.meta}
        with self.assertRaises(ServiceError):self.admit()

    def test_model_native_provenance_and_explicit_unknown_session(self):
        self.executor.update(kind='model',thread_id='model-thread',turn_id='model-turn');self.configure();self.start()
        self.assertIsNone(self.attempt['executor']['session_id']);bad=copy.deepcopy(self.attempt);bad['executor']['session_id']='inferred'
        with self.assertRaises(ServiceError):self.admit(bad)

    def test_stored_hash_tamper_and_failed_retry_need_diagnosis(self):
        self.start();r=self.receipt();r['checks'][0]['exit']=1;self.record(r)
        a=copy.deepcopy(self.attempt);a['attempt_id']='attempt-2'
        with self.assertRaises(ServiceError):self.admit(a)
        with self.assertRaises(ServiceError):self.store.event(self.package['task_id'],'rebuild-renew-forged',{'expires_at':self.now+900})
        with self.store._connection(True) as c:c.execute("UPDATE events SET hash=? WHERE event_key='rebuild-package'",('e'*64,))
        with self.assertRaises(ServiceError):self.store.rebuild_package(self.package['task_id'])

    def test_unconfigured_route_preserves_legacy_capabilities(self):
        with self.assertRaises(ServiceError):TaskService(None,None,self.store).prepare_rebuild(self.package,owner_context='synthetic-owner')
        with self.assertRaises(ServiceError):TaskService(None,None,self.store,rebuild_binding=self.binding)
        validate_grant(self.grant,live=True)

    def test_continuous_lease_renewals_cover_more_than_fifteen_seconds(self):
        self.start(seconds=60);initial=self.now
        for instant in (initial+12,initial+24,initial+36):
            self.now=instant;self.store.heartbeat(self.package['task_id'],'synthetic-worker')
        self.now=initial+40;self.record();self.assertEqual(self.store.task(self.package['task_id'])['lease_until'],initial+51)

    def test_gap_expiry_owner_change_and_backdating_require_reconcile(self):
        self.start(seconds=60);self.now+=20
        # Caller cannot fabricate a later current lease without trusted renewal history.
        with self.store._connection(True) as c:c.execute('UPDATE tasks SET lease_until=? WHERE id=?',(self.now+15,self.package['task_id']))
        with self.assertRaises(ServiceError):self.record()
        self.assertEqual(self.store.task(self.package['task_id'])['status'],'requires_reconcile')

    def scoped(self,paths):
        g=copy.deepcopy(self.grant)
        g.update(kind='rebuild_scoped_write',effect='scoped_write',profile_version='2.0',task_id=self.package['task_id'],lineage=self.package['lineage'],workspace=self.workspace,allowed_modules=paths,write_paths=paths,max_turn_seconds=900,sandbox={'version':'1.0','profile':'exact_scoped_write_v1','read_paths':[i['path'] for i in g['input_files']],'write_paths':paths},writer_handoff={'task_id':self.package['task_id'],'root_identity':g['root_identity'],'evidence_ref':'evidence/writer.json','sha256':'c'*64,'source':'root_verified'})
        g['write_baselines']=observe_scoped_paths(self.workspace['worktree'],self.workspace['worktree_identity'],paths)
        g['input_version']=scoped_input_version(g);g['version']=2;self.grant=g
        self.store.register_grant(g)
        self.package['binding']={k:g[k] for k in ('root','root_identity','registry_hash','input_version','input_files')}
        self.package['grant']={**{k:g[k] for k in ('grant_id','version','source','auth','expires_at')},'sha256':digest(g)}
        self.package['write_baselines']=copy.deepcopy(g['write_baselines'])
        self.package['source_versions'].update({p:digest(v) for p,v in g['write_baselines'].items()})
        self.package['allowed_modules']=paths;self.package['effects']={'mode':'scoped_write','writable_scope':paths};self.package['sandbox']=g['sandbox'];self.configure()

    def changed_receipt(self):
        from hub.rebuild_contracts import current_versions
        r=self.receipt();r['end_versions']=current_versions(self.grant)
        r['artifacts']=[{'path':p,**({'sha256':v['sha256']} if v['state']=='file' else {'state':'absent'})} for p,v in r['write_states'].items() if v!=self.grant['write_baselines'][p]]
        return r

    def test_positive_distinct_scopedwrite_and_legacy_rejection(self):
        self.scoped(['README.md'])
        validate_scoped_write_grant(self.grant,live=True)
        with self.assertRaises(ServiceError):validate_grant(self.grant,live=True)
        self.start(seconds=900)
        self.fixture.source.write_text('Authorized synthetic change')
        r=self.receipt()
        from hub.rebuild_contracts import current_versions
        r['end_versions']=current_versions(self.grant)
        import hashlib
        r['artifacts']=[{'path':'README.md','sha256':hashlib.sha256(self.fixture.source.read_bytes()).hexdigest()}]
        self.record(r);self.assertIsNone(self.store.claim('legacy-worker'))
        for paths in (['.'],[],['elsewhere.py'],['*.py']):
            bad=copy.deepcopy(self.grant);bad['write_paths']=paths
            with self.subTest(paths=paths),self.assertRaises(ServiceError):validate_scoped_write_grant(bad)


    def test_extra_write_not_read_input_cannot_hide_change_or_hash(self):
        extra=self.fixture.root/'EXTRA.md';extra.write_text('original extra')
        self.scoped(['EXTRA.md']);self.start();extra.write_text('changed extra')
        r=self.changed_receipt()
        self.assertNotEqual(r['end_versions'],r['start_versions'])
        self.assertEqual(r['artifacts'],[{'path':'EXTRA.md','sha256':__import__('hashlib').sha256(b'changed extra').hexdigest()}])
        for change in ('missing','wrong_hash','wrong_state','stale_end'):
            bad=copy.deepcopy(r)
            if change=='missing':bad['artifacts']=[]
            if change=='wrong_hash':bad['artifacts'][0]['sha256']='e'*64
            if change=='wrong_state':bad['write_states']['EXTRA.md']={'state':'absent'}
            if change=='stale_end':bad['end_versions']=bad['start_versions']
            with self.subTest(change=change),self.assertRaises(ServiceError):self.record(bad)
        self.record(r);self.assertTrue(self.record(r)['replayed'])

    def test_explicit_absence_creation_deletion_and_noop_versions(self):
        self.scoped(['planned/NEW.md']);self.assertEqual(self.package['write_baselines'],{'planned/NEW.md':{'state':'absent'}})
        self.start();noop=self.changed_receipt();self.assertEqual(noop['artifacts'],[]);self.assertEqual(noop['end_versions'],noop['start_versions'])
        # Reject a bogus no-op artifact before any receipt is committed.
        bad=copy.deepcopy(noop);bad['artifacts']=[{'path':'planned/NEW.md','sha256':'e'*64}]
        with self.assertRaises(ServiceError):self.record(bad)
        new=self.fixture.root/'planned/NEW.md';new.parent.mkdir();new.write_bytes(b'new file')
        r=self.changed_receipt();self.assertNotEqual(r['end_versions'],noop['end_versions'])
        self.assertEqual(r['artifacts'],[{'path':'planned/NEW.md','sha256':__import__('hashlib').sha256(b'new file').hexdigest()}]);self.record(r)

    def test_existing_file_deletion_has_explicit_absent_artifact(self):
        self.scoped(['README.md']);self.start();self.fixture.source.unlink()
        r=self.changed_receipt();self.assertEqual(r['write_states'],{'README.md':{'state':'absent'}})
        self.assertEqual(r['artifacts'],[{'path':'README.md','state':'absent'}]);self.record(r)

    def test_noop_receipt_has_no_artifacts_and_unchanged_version(self):
        self.scoped(['README.md']);self.start();r=self.changed_receipt()
        self.assertEqual(r['artifacts'],[]);self.assertEqual(r['start_versions'],r['end_versions']);self.record(r)

    def test_every_write_baseline_is_required_and_observed_at_admission(self):
        extra=self.fixture.root/'EXTRA.md';extra.write_text('extra')
        self.scoped(['EXTRA.md'])
        for change in ('missing','unversioned','wrong_version'):
            p=copy.deepcopy(self.package)
            if change=='missing':p.pop('write_baselines')
            if change=='unversioned':p['write_baselines']={}
            if change=='wrong_version':p['source_versions']['EXTRA.md']='e'*64
            with self.subTest(change=change),self.assertRaises(ServiceError):validate(p,'package')
        extra.write_text('changed before admission')
        with self.assertRaises(ServiceError):self.prepare()
        self.assertIsNone(self.store.task(self.package['task_id']))

    def test_unsafe_or_unreadable_paths_never_become_absence(self):
        link=self.fixture.root/'dangling.md';link.symlink_to('missing-target')
        with self.assertRaises(ServiceError):observe_scoped_paths(self.fixture.root,self.workspace['worktree_identity'],['dangling.md'])
        target=self.fixture.root/'unreadable.md';target.write_text('present')
        import os
        original=os.stat
        def denied(path,*a,**k):
            if path=='unreadable.md' and k.get('dir_fd') is not None:raise PermissionError('synthetic permission denial')
            return original(path,*a,**k)
        with mock.patch('hub.ordinary_tasks.os.stat',side_effect=denied),self.assertRaises(ServiceError):
            observe_scoped_paths(self.fixture.root,self.workspace['worktree_identity'],['unreadable.md'])

    def test_nonwritable_read_input_change_cannot_expand_write_authority(self):
        self.scoped(['NEW.md']);self.start();self.fixture.source.write_text('unapproved change')
        with self.assertRaises(ServiceError):self.changed_receipt()

    def _history(self):
        with self.store._connection() as c:return self.store._rebuild_rows(c,self.package['task_id'])

    def test_readonly_effect_mismatch_rejects_record_and_root_verified_dependency(self):
        self.start();r=self.receipt();r['effect_state']='scoped_write';r['write_states']={}
        with self.assertRaises(ServiceError):self.record(r)
        with self.assertRaises(ServiceError):settled_evidence(self.package,r,self.proof(r),self.attempt)
        self.assertFalse(any(x['key'].startswith('rebuild-receipt-') for x in self._history()))

    def test_scoped_effect_mismatch_and_unknown_require_reconciliation(self):
        self.scoped(['README.md']);self.start();r=self.changed_receipt();r['effect_state']='read_only'
        with self.assertRaises(ServiceError):self.record(r)
        with self.assertRaises(ServiceError):settled_evidence(self.package,r,self.proof(r),self.attempt)
        r['effect_state']='unknown';r['unknowns']=['Actual effect outcome requires reconciliation']
        result=self.record(r);self.assertTrue(result['requires_reconcile'])
        self.assertEqual(self.store.task(self.package['task_id'])['status'],'requires_reconcile')
        self.assertTrue(self.record(r)['replayed'])
        with self.assertRaises(ServiceError):settled_evidence(self.package,r,self.proof(r),self.attempt)

    def test_readonly_unknown_is_durable_but_not_dependency_eligible(self):
        self.start();r=self.receipt();r['effect_state']='unknown';r['unknowns']=['Uncertain completion']
        self.assertTrue(self.record(r)['requires_reconcile']);self.assertTrue(self.record(r)['replayed'])
        with self.assertRaises(ServiceError):settled_evidence(self.package,r,self.proof(r),self.attempt)


    def test_unversioned_or_false_absence_grant_rejects(self):
        extra=self.fixture.root/'EXTRA.md';extra.write_text('existing')
        self.scoped(['EXTRA.md'])
        for change in ('missing_baseline','false_absence','wrong_baseline_hash'):
            g=copy.deepcopy(self.grant)
            if change=='missing_baseline':g.pop('write_baselines')
            if change=='false_absence':g['write_baselines']['EXTRA.md']={'state':'absent'};g['input_version']=scoped_input_version(g)
            if change=='wrong_baseline_hash':g['write_baselines']['EXTRA.md']['sha256']='e'*64;g['input_version']=scoped_input_version(g)
            with self.subTest(change=change),self.assertRaises(ServiceError):validate_scoped_write_grant(g,live=True)

    def test_scoped_changed_artifacts_are_required_even_for_root_provenance(self):
        self.scoped(['NEW.md']);self.start();(self.fixture.root/'NEW.md').write_text('actual new source')
        r=self.changed_receipt();settled_evidence(self.package,r,self.proof(r),self.attempt)
        for change in ('missing','extra_state','bad_end','bad_attempt_baseline'):
            bad=copy.deepcopy(r);attempt=copy.deepcopy(self.attempt)
            if change=='missing':bad['artifacts']=[]
            if change=='extra_state':bad['write_states']['OTHER.md']={'state':'absent'}
            if change=='bad_end':bad['end_versions']=bad['start_versions']
            if change=='bad_attempt_baseline':attempt['write_baseline_sha256']='e'*64;bad['attempt_sha256']=digest(attempt)
            proof=self.proof(bad);proof['attempt_sha256']=digest(attempt)
            with self.subTest(change=change),self.assertRaises(ServiceError):settled_evidence(self.package,bad,proof,attempt)


    def test_scoped_dependency_uses_created_file_state_not_only_read_inputs(self):
        readonly=copy.deepcopy(self.grant)
        self.scoped(['NEW.md']);self.start();(self.fixture.root/'NEW.md').write_text('new dependency artifact')
        r=self.changed_receipt();self.record(r);proof=self.proof(r);depid=self.package['task_id'];dep_hash=digest(r)
        self.store.update(depid,'synthetic-worker',status='validating');self.store.update(depid,'synthetic-worker',status='checks_complete')
        readonly['grant_id']='dependent-grant';self.store.register_grant(readonly);self.grant=readonly
        self.package=copy.deepcopy(self.package);self.package['task_id']='dependent-task';self.package['lineage']['original_task_id']='dependent-task'
        self.package.pop('write_baselines');self.package['source_versions']={'README.md':readonly['input_files'][0]['sha256']}
        self.package['allowed_modules']=['README.md'];self.package['effects']={'mode':'read_only','writable_scope':[]}
        self.package['sandbox']={'version':'1.0','profile':'ordinary_readonly_v1','read_paths':['README.md'],'write_paths':[]}
        self.package['binding']={k:readonly[k] for k in ('root','root_identity','registry_hash','input_version','input_files')}
        self.package['grant']={**{k:readonly[k] for k in ('grant_id','version','source','auth','expires_at')},'sha256':digest(readonly)}
        self.package['dependencies']=[{'task_id':depid,'receipt_sha256':dep_hash}];self.configure({dep_hash:proof});self.start()
        self.assertEqual(self.store.task('dependent-task')['status'],'running')

    def test_absence_observation_rejects_parent_replacement(self):
        parent=self.fixture.root/'planned';parent.mkdir()
        import os
        original=os.stat;changed=[False]
        def replace(path,*a,**k):
            if path=='NEW.md' and k.get('dir_fd') is not None and not changed[0]:
                changed[0]=True;parent.rename(self.fixture.root/'moved');parent.mkdir()
            return original(path,*a,**k)
        with mock.patch('hub.ordinary_tasks.os.stat',side_effect=replace),self.assertRaises(ServiceError):
            observe_scoped_paths(self.fixture.root,self.workspace['worktree_identity'],['planned/NEW.md'])


    def test_readonly_absent_artifact_rejected_at_all_boundaries(self):
        self.start();r=self.receipt();r['artifacts']=[{'path':'README.md','state':'absent'}]
        self.assertNotIn('write_states',r)
        self.assertEqual(r['start_versions'],r['end_versions'])
        for boundary in ('schema','binding','record','dependency'):
            with self.subTest(boundary=boundary),self.assertRaises(ServiceError):
                if boundary=='schema':validate(r,'receipt')
                if boundary=='binding':receipt_binding(r,self.package,self.attempt)
                if boundary=='record':self.record(r)
                if boundary=='dependency':settled_evidence(self.package,r,self.proof(r),self.attempt)
        self.assertFalse(any(x['key'].startswith('rebuild-receipt-') for x in self._history()))

    def test_unknown_readonly_cannot_claim_absent_artifact(self):
        self.start();r=self.receipt();r.update(effect_state='unknown',unknowns=['Uncertain completion'])
        r['artifacts']=[{'path':'README.md','state':'absent'}]
        for boundary in ('schema','binding','record','dependency'):
            with self.subTest(boundary=boundary),self.assertRaises(ServiceError):
                if boundary=='schema':validate(r,'receipt')
                if boundary=='binding':receipt_binding(r,self.package,self.attempt)
                if boundary=='record':self.record(r)
                if boundary=='dependency':settled_evidence(self.package,r,self.proof(r),self.attempt)
        self.assertFalse(any(x['key'].startswith('rebuild-receipt-') for x in self._history()))

    def test_hashed_readonly_evidence_remains_valid(self):
        self.start();r=self.receipt();r['artifacts']=[{'path':'README.md','sha256':self.grant['input_files'][0]['sha256']}]
        validate(r,'receipt');receipt_binding(r,self.package,self.attempt)
        self.assertEqual(self.record(r),{'replayed':False,'settled':False})
        settled_evidence(self.package,r,self.proof(r),self.attempt)
        self.assertTrue(self.record(r)['replayed'])

    def test_unknown_scoped_deletion_is_audited_and_requires_reconcile(self):
        self.scoped(['README.md']);self.start();self.fixture.source.unlink()
        r=self.changed_receipt();r.update(effect_state='unknown',unknowns=['Deletion observed; execution outcome unresolved'])
        self.assertEqual(r['write_states'],{'README.md':{'state':'absent'}})
        self.assertEqual(r['artifacts'],[{'path':'README.md','state':'absent'}])
        validate(r,'receipt');receipt_binding(r,self.package,self.attempt)
        self.assertTrue(self.record(r)['requires_reconcile']);self.assertTrue(self.record(r)['replayed'])
        self.assertEqual(self.store.task(self.package['task_id'])['status'],'requires_reconcile')
        stored=[x['data'] for x in self._history() if x['key'].startswith('rebuild-receipt-')]
        self.assertEqual(stored,[r])
        with self.assertRaises(ServiceError):settled_evidence(self.package,r,self.proof(r),self.attempt)

if __name__=='__main__':unittest.main()
