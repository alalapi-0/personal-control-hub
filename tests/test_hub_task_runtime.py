from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import queue
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub.codex_adapter import AppServerAdapter,sanitized
import hub.codex_adapter as codex_module
from hub.task_store import TaskStore
from hub.task_service import TaskService,fingerprint,root_identity,FIXED_CHECKS
from hub.task_worker import TaskWorker
from hub.workbench_store import digest
from hub.service_contract import ServiceError
from hub.local_service import HubHTTPServer
from hub.owner_auth import OwnerAuth
import test_hub_workbench as annotation_fixture
import test_hub_local_service as http_fixture

BEFORE='<!doctype html><title>Fixture</title><h1>before</h1>\n'
AFTER='<!doctype html><title>Fixture</title><h1>after</h1>\n'


class FakeAdapter:
    """Deterministic mock; none of its events count as live Codex evidence."""
    transport='mock'
    opened=0
    effects=0
    check_exit=0
    interrupt_count=0

    def __init__(self,root):self.root=root;self.denials=[];self.events=[]
    def open(self):type(self).opened+=1;return self
    def thread(self,previous=None):return previous or'fixture-thread'
    def start(self,thread,text,image):
        type(self).effects+=1;(self.root/'index.html').write_text(AFTER)
        event={'method':'turn/started','params':{'threadId':thread,'turn':{'id':'fixture-turn','status':'inProgress'}}}
        self.events=[event,copy.deepcopy(event),{'method':'item/completed','params':{'threadId':thread,'turnId':'fixture-turn','item':{'type':'agentMessage','text':'MOCK fixture marker'}}},
            {'method':'turn/tokenUsage/updated','params':{'threadId':thread,'turnId':'fixture-turn','tokenUsage':{'total':{'totalTokens':1}}}},
            {'method':'turn/completed','params':{'threadId':thread,'turn':{'id':'fixture-turn','status':'completed'}}}]
        return'fixture-turn'
    def poll(self,_):return self.events.pop(0)
    def check(self,argv):
        assert tuple(argv)==FIXED_CHECKS['fixture_sample']
        return{'exitCode':type(self).check_exit,'stdout':'actual check mock','stderr':''}
    def interrupt(self,thread,turn):type(self).interrupt_count+=1
    def close(self):pass


class TaskRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.helper=annotation_fixture.WorkbenchTests();self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.control=self.helper.root
        self.workspace=self.control/'workspace';self.workspace.mkdir()
        (self.workspace/'index.html').write_text(BEFORE)
        (self.workspace/'AGENTS.md').write_text('Synthetic fixture marker\n')
        (self.workspace/'fixture.png').write_bytes(self.helper.fixture.png_data)
        self.helper.registry.projects['fixture-project']['root_path']=str(self.workspace)
        self.command=self.helper.command(kind='whole',region=None,request_id='fixture-annotation')
        self.helper.save(self.command)
        self.store=TaskStore(self.control)
        self.store.register_storage()  # Explicit owned fixture setup, never runtime recovery.
        self.service=TaskService(self.helper.projects,self.helper.service,self.store,test_gate=True,writer_check=lambda g:g['root']==str(self.workspace))
        self.grant={'grant_id':'fixture-grant','version':1,'source':'verified_owner_decision','task_id':'fixture-task','project_id':'fixture-project',
            'root':str(self.workspace),'root_identity':root_identity(self.workspace),'fingerprint':fingerprint(self.workspace),
            'annotation_hash':digest(self.command),'checks':['fixture_sample'],'expires_at':time.time()+3600,'isolated_root':True,
            'writable_scope':['.'],'prohibited':[],'auth':'ChatGPT','max_turn_seconds':20,'image':'fixture.png',
            'image_sha256':hashlib.sha256((self.workspace/'fixture.png').read_bytes()).hexdigest(),'mode':'new','parent_task_id':None}
        self.store.register_grant(self.grant)
        self.payload={'request_id':'fixture-task','project_id':'fixture-project','annotation_id':'fixture-annotation','annotation_revision':1,
            'grant_id':'fixture-grant','grant_version':1,'mode':'new','parent_task_id':None}
        FakeAdapter.opened=FakeAdapter.effects=FakeAdapter.interrupt_count=FakeAdapter.check_exit=0

    def worker(self,**kwargs):return TaskWorker(self.store,adapter_factory=FakeAdapter,writer_check=lambda g:g['root']==str(self.workspace),**kwargs)

    def test_intent_precedes_dispatch_duplicate_effect_and_actual_check_separation(self):
        saved=self.service.submit(self.payload);self.assertEqual(saved['task']['status'],'queued');self.assertEqual(FakeAdapter.effects,0)
        self.assertTrue(self.service.submit(self.payload)['replayed']);self.assertEqual(len(self.store.tasks()),1)
        self.worker().run_one();task=self.store.task('fixture-task')
        self.assertEqual(task['status'],'checks_complete');self.assertEqual(task['provider_status'],'completed');self.assertEqual(task['human_acceptance'],'pending')
        self.assertEqual(task['thread_id'],'fixture-thread');self.assertEqual(task['turn_id'],'fixture-turn')
        self.assertEqual(task['result']['changed_paths'],['index.html']);self.assertIn('-<!doctype',task['result']['diff'])
        self.assertEqual(len([e for e in self.store.events('fixture-task')if e['data'].get('method')=='turn/started']),1)
        self.assertFalse(self.worker().run_one());self.assertTrue(self.service.submit(self.payload)['replayed']);self.assertEqual(FakeAdapter.effects,1)
        connection=sqlite3.connect(self.store.path)
        try:self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        finally:connection.close()

    def test_browser_evidence_does_not_expose_control_tokens_paths_or_argv(self):
        self.service.submit(self.payload);self.worker().run_one()
        self.store.event('fixture-task','capability-proof-fixture',{'argv':['private-control-argv'],'worker':{'owner':'private-control-token'},
            'root':str(self.workspace),'method':'fixture/status','digest':'a'*64})
        public=self.service.get('fixture-task');encoded=json.dumps(public)
        for private in ('private-control-argv','private-control-token',str(self.workspace)):self.assertNotIn(private,encoded)
        self.assertEqual(public['task']['mode'],'new');self.assertEqual(public['task']['annotation_id'],'fixture-annotation')
        self.assertEqual(public['events'][-1]['data']['method'],'fixture/status')
        with self.assertRaises(ServiceError)as caught:self.service.options('missing-annotation')
        self.assertEqual(caught.exception.status,404)

    def test_untrusted_fields_wrong_project_grant_and_annotation_zero_dispatch(self):
        for key in ['cwd','path','command','env','config','thread_id','model','sandbox','network','url']:
            with self.subTest(key=key),self.assertRaises(ServiceError):self.service.submit({**self.payload,key:'untrusted'})
        for changes in [{'project_id':'unknown'},{'grant_version':99},{'grant_id':'no-grant'},{'annotation_revision':99},{'mode':'idle_continue','parent_task_id':'guess'}]:
            with self.assertRaises(ServiceError):self.service.submit({**self.payload,**changes})
        for changes in [{'enabled':False},{'current_state_status':'removed_local'},{'local_presence':{'status':'removed_local'}},{'root_kind':'cloud'}]:
            original=copy.deepcopy(self.helper.registry.projects['fixture-project']);self.helper.registry.projects['fixture-project'].update(changes)
            with self.assertRaises(ServiceError):self.service.submit(self.payload)
            self.helper.registry.projects['fixture-project']=original
        self.assertEqual(self.store.tasks(),[]);self.assertEqual(FakeAdapter.effects,0)
        self.service.writer_check=lambda _:False
        with self.assertRaises(ServiceError):self.service.submit(self.payload)
        self.service.test_gate=False
        with self.assertRaises(ServiceError):self.service.submit(self.payload)

    def test_unbound_native_unknown_requires_fresh_root_reconciliation(self):
        class UnknownThread(FakeAdapter):
            def thread(self,previous=None):raise ServiceError('CODEX_PROTOCOL_REJECTED',outcome='UNKNOWN')
        self.service.submit(self.payload)
        worker=TaskWorker(self.store,adapter_factory=UnknownThread,writer_check=lambda _:True)
        worker.run_one()
        self.assertEqual(self.store.task('fixture-task')['status'],'requires_reconcile')
        proof={'source':'app_server_stdio.thread/list','root':str(self.workspace),'thread_ids':[],
            'archived_checked':True,'observed_at':time.time()}
        with self.assertRaises(ServiceError)as caught:self.store.reconcile_owned('fixture-task',proof)
        self.assertEqual(caught.exception.code,'TASK_OWNER_STILL_ACTIVE')
        self.assertEqual(FakeAdapter.effects,0)

    def test_root_recovery_preserves_failure_and_never_replays_post_turn(self):
        class UnknownThread(FakeAdapter):
            def thread(self,previous=None):raise ServiceError('CODEX_PROTOCOL_REJECTED',outcome='UNKNOWN')
        self.service.submit(self.payload)
        worker=TaskWorker(self.store,adapter_factory=UnknownThread,writer_check=lambda _:True)
        # Mock retired identity only; no live provider acceptance is inferred.
        worker.identity={'owner':worker.owner,'pid':999999999,'start_ticks':'0'}
        worker.run_one()
        proof={'source':'app_server_stdio.thread/list','root':str(self.workspace),'thread_ids':[],
            'archived_checked':True,'observed_at':time.time()}
        for changes in [{'thread_ids':['unknown']},{'observed_at':time.time()-60},{'archived_checked':False}]:
            with self.assertRaises(ServiceError):self.store.reconcile_owned('fixture-task',{**proof,**changes})
        (self.workspace/'index.html').write_text(AFTER)
        with self.assertRaises(ServiceError):self.store.reconcile_owned('fixture-task',proof)
        (self.workspace/'index.html').write_text(BEFORE)
        self.store.reconcile_owned('fixture-task',proof)
        self.assertEqual(self.store.task('fixture-task')['status'],'queued')
        evidence=self.store.events('fixture-task')[-1]['data']
        self.assertEqual(evidence['original_result']['error_class'],'CODEX_PROTOCOL_REJECTED')
        self.assertEqual(evidence['action'],'same_id_requeue_before_any_turn')
        with self.assertRaises(ServiceError):self.store.reconcile_owned('fixture-task',proof)
        self.worker(fault=lambda point:(_ for _ in ()).throw(ServiceError('TEST_UNKNOWN',outcome='UNKNOWN'))if point=='before_turn'else None).run_one()
        with self.assertRaises(ServiceError):self.store.reconcile_owned('fixture-task',proof)
        self.assertEqual(FakeAdapter.effects,0)

    def test_expired_stale_scope_unknown_checks_and_registry_drift(self):
        for key,value in [('expires_at',0),('checks',['arbitrary-shell']),('isolated_root',False),('image','../private.png')]:
            grant=copy.deepcopy(self.grant);grant[key]=value;grant['version']+=1;self.store.register_grant(grant)
            with self.assertRaises(ServiceError):self.service.submit({**self.payload,'grant_version':2})
            self.grant['version']=grant['version']+1;self.store.register_grant(self.grant);self.payload['grant_version']=self.grant['version']
        (self.workspace/'index.html').write_text('changed elsewhere')
        with self.assertRaises(ServiceError):self.service.submit(self.payload)
        (self.workspace/'index.html').write_text(BEFORE);self.helper.registry.authority['registry_hash']='c'*64
        with self.assertRaises(ServiceError):self.service.submit(self.payload)
        self.assertEqual(FakeAdapter.effects,0)

    def test_known_check_failure_recovery_is_explicit_and_requires_zero_file_effect(self):
        class NoEffect(FakeAdapter):
            check_exit=1
            def start(self,thread,text,image):
                turn=super().start(thread,text,image);(self.root/'index.html').write_text(BEFORE);return turn
        self.service.submit(self.payload)
        worker=TaskWorker(self.store,adapter_factory=NoEffect,writer_check=lambda _:True)
        worker.identity={'owner':worker.owner,'pid':999999999,'start_ticks':'0'};worker.run_one()
        task=self.store.task('fixture-task');self.assertEqual(task['status'],'failed');self.assertEqual(task['result']['changed_paths'],[])
        proof={'source':'app_server_stdio.thread/read','root':str(self.workspace),'thread_id':task['thread_id'],'turn_id':task['turn_id'],
            'thread_status':'idle','turn_status':'completed','observed_at':time.time()}
        for changes in [{'turn_id':'guessed'},{'thread_status':'active'},{'turn_status':'inProgress'}]:
            with self.assertRaises(ServiceError):self.store.reconcile_owned(task['id'],{**proof,**changes})
        self.store.reconcile_owned(task['id'],proof);current=self.store.task(task['id'])
        self.assertEqual(current['thread_id'],task['thread_id']);self.assertIsNone(current['turn_id']);self.assertEqual(current['status'],'queued')
        self.assertEqual(self.store.events(task['id'])[-1]['data']['original_result']['checks'][0]['exit'],1)

    def test_unexpected_tool_stops_owned_turn(self):
        class Unexpected(FakeAdapter):
            def start(self,thread,text,image):
                turn=super().start(thread,text,image)
                self.events.insert(1,{'method':'item/started','params':{'threadId':thread,'turnId':turn,'item':{'type':'mcpToolCall'}}})
                return turn
        self.service.submit(self.payload)
        TaskWorker(self.store,adapter_factory=Unexpected,writer_check=lambda _:True).run_one()
        task=self.store.task('fixture-task');self.assertEqual(task['status'],'requires_reconcile')
        self.assertEqual(task['result']['error_class'],'CODEX_TOOL_BOUNDARY_REJECTED');self.assertEqual(Unexpected.interrupt_count,1)

    def test_approval_refusal_ends_owned_probe(self):
        class Approval(FakeAdapter):
            def start(self,thread,text,image):
                turn=super().start(thread,text,image);self.denials=[{'method':'unexpected approval'}];return turn
        self.service.submit(self.payload)
        TaskWorker(self.store,adapter_factory=Approval,writer_check=lambda _:True).run_one()
        task=self.store.task('fixture-task');self.assertEqual(task['status'],'requires_reconcile')
        self.assertEqual(task['result']['error_class'],'CODEX_PERMISSION_DENIED');self.assertEqual(Approval.interrupt_count,1)

    def test_commit_fault_receipt_and_conflict_not_replayed(self):
        def before(stage):
            if stage=='before_commit':raise OSError('fixture precommit')
        self.store.fault=before
        with self.assertRaises(ServiceError)as caught:self.service.submit(self.payload)
        self.assertEqual(caught.exception.outcome,'NOT_COMMITTED');self.assertIsNone(self.store.task('fixture-task'))
        def after(stage):
            if stage=='after_commit':raise OSError('fixture postcommit')
        self.store.fault=after
        with self.assertRaises(ServiceError)as caught:self.service.submit(self.payload)
        self.assertEqual(caught.exception.outcome,'UNKNOWN');self.assertEqual(self.store.task('fixture-task')['status'],'queued')
        self.store.fault=lambda _:None;self.assertTrue(self.service.submit(self.payload)['replayed'])
        with self.assertRaises(ServiceError):self.service.submit({**self.payload,'grant_version':99})
        self.assertEqual(FakeAdapter.effects,0)

    def test_claim_fence_expiry_cancellation_and_uncertain_dispatch(self):
        self.service.submit(self.payload);self.store.claim('fixture-owner')
        self.assertIsNone(self.store.claim('other-owner'))
        self.assertIsNone(self.store.claim('other-owner',time.time()+20));self.assertEqual(self.store.task('fixture-task')['status'],'requires_reconcile')
        with self.assertRaises(ServiceError):self.store.update('fixture-task','fixture-owner',status='checks_complete')
        # A separate uncertain boundary is tested against a fresh fixture-owned ID.
        self.store.cancel('fixture-task');self.assertEqual(self.store.task('fixture-task')['status'],'requires_reconcile')

    def test_after_dispatch_fault_no_second_writer_or_blind_recovery(self):
        self.service.submit(self.payload)
        def fault(stage):
            if stage=='after_turn_before_binding':raise OSError('synthetic crash boundary')
        self.worker(fault=fault).run_one();task=self.store.task('fixture-task')
        self.assertEqual(task['status'],'requires_reconcile');self.assertEqual(FakeAdapter.effects,1)
        self.assertFalse(self.worker().run_one());self.assertEqual(FakeAdapter.effects,1)
        self.assertTrue(self.service.submit(self.payload)['replayed'])

    def test_complete_provider_but_failed_check_and_cancel_before_effect(self):
        self.service.submit(self.payload);FakeAdapter.check_exit=1;self.worker().run_one()
        task=self.store.task('fixture-task');self.assertEqual(task['status'],'failed');self.assertEqual(task['provider_status'],'completed')
        self.assertEqual(task['human_acceptance'],'pending')

    def test_cancel_queued_prevents_all_effects(self):
        self.service.submit(self.payload);self.service.cancel('fixture-task');self.assertFalse(self.worker().run_one());self.assertEqual(FakeAdapter.effects,0)
        self.assertEqual(self.store.task('fixture-task')['status'],'cancelled')

    def test_cancel_committed_after_final_read_before_success_publication(self):
        self.service.submit(self.payload)
        original=self.store.update
        def publish(ident,owner,**values):
            if values.get('status')=='checks_complete':self.store.cancel(ident)
            return original(ident,owner,**values)
        with mock.patch.object(self.store,'update',side_effect=publish):self.worker().run_one()
        task=self.store.task('fixture-task')
        self.assertEqual(task['status'],'cancelled')
        self.assertTrue(task['cancel'])
        self.assertEqual(task['provider_status'],'completed')
        self.assertEqual(task['result']['error_class'],'TASK_CANCEL_AFTER_EFFECT')
        self.assertFalse(task['result']['effects_rolled_back'])

    def test_exact_owned_idle_continuation_and_busy_queue(self):
        self.service.submit(self.payload);self.worker().run_one()
        grant={**self.grant,'version':2,'task_id':'fixture-continue','mode':'idle_continue','parent_task_id':'fixture-task','fingerprint':fingerprint(self.workspace)}
        self.store.register_grant(grant)
        payload={**self.payload,'request_id':'fixture-continue','grant_version':2,'mode':'idle_continue','parent_task_id':'fixture-task'}
        self.service.submit(payload);self.worker().run_one()
        self.assertEqual(self.store.task('fixture-continue')['thread_id'],self.store.task('fixture-task')['thread_id'])
        self.assertEqual(self.store.task('fixture-continue')['status'],'checks_complete');self.assertEqual(FakeAdapter.effects,2)
        grant={**grant,'version':3,'task_id':'fixture-busy','mode':'busy_feedback','parent_task_id':'fixture-continue'}
        self.store.register_grant(grant)
        payload={**payload,'request_id':'fixture-busy','grant_version':3,'mode':'busy_feedback','parent_task_id':'fixture-continue'}
        self.service.submit(payload)
        claimed=self.store.claim('busy-owner');self.assertEqual(claimed['id'],'fixture-busy')
        self.assertIsNone(self.store.claim('second-writer'));self.assertEqual(FakeAdapter.effects,2)

    def test_busy_feedback_waits_for_exact_active_parent(self):
        self.service.submit(self.payload);self.store.claim('parent-owner');self.store.update('fixture-task','parent-owner',thread_id='fixture-thread',turn_id='fixture-turn',provider_status='inProgress')
        grant={**self.grant,'version':2,'task_id':'fixture-busy','mode':'busy_feedback','parent_task_id':'fixture-task'}
        self.store.register_grant(grant);payload={**self.payload,'request_id':'fixture-busy','grant_version':2,'mode':'busy_feedback','parent_task_id':'fixture-task'}
        self.service.submit(payload);self.assertIsNone(self.store.claim('other-owner'));self.assertEqual(self.store.task('fixture-busy')['status'],'queued')
        self.store.update('fixture-task','parent-owner',status='cancelled')
        self.assertIsNone(self.store.claim('other-owner'));self.assertEqual(self.store.task('fixture-busy')['status'],'requires_reconcile');self.assertEqual(FakeAdapter.effects,0)

    def test_late_cancel_and_wrong_provider_identity_never_publish_success(self):
        self.service.submit(self.payload)
        store=self.store
        class LateCancel(FakeAdapter):
            def start(self,*args):
                result=super().start(*args);store.cancel('fixture-task');return result
        worker=TaskWorker(self.store,adapter_factory=LateCancel,writer_check=lambda _:True);worker.run_one()
        task=self.store.task('fixture-task');self.assertEqual(task['status'],'cancelled');self.assertEqual((self.workspace/'index.html').read_text(),AFTER)

    def test_wrong_thread_event_and_preserve_scope_change_are_uncertain(self):
        self.service.submit(self.payload)
        class WrongEvent(FakeAdapter):
            def start(self,*args):
                result=super().start(*args);self.events[0]['params']['threadId']='other-thread';return result
        TaskWorker(self.store,adapter_factory=WrongEvent,writer_check=lambda _:True).run_one()
        task=self.store.task('fixture-task');self.assertEqual(task['status'],'requires_reconcile');self.assertEqual(task['result']['error_class'],'CODEX_EVENT_BINDING_REJECTED')

    def test_two_admissions_and_claims_are_serialized(self):
        import threading
        barrier=threading.Barrier(2);results=[]
        def admit():
            barrier.wait();results.append(self.service.submit(self.payload))
        threads=[threading.Thread(target=admit)for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(3);self.assertFalse(thread.is_alive())
        self.assertEqual(len(results),2);self.assertEqual(sum(r['replayed']for r in results),1);self.assertEqual(len(self.store.tasks()),1)

    def test_corrupt_unknown_schema_and_symlink_store_preserved(self):
        self.service.submit(self.payload)
        connection=sqlite3.connect(self.store.path);connection.execute("UPDATE tasks SET payload='{}'");connection.commit();connection.close()
        with self.assertRaises(ServiceError):self.store.task('fixture-task')
        self.store.path.unlink();self.store.path.write_bytes(b'corrupt SQLite preserved');before=self.store.path.read_bytes()
        with self.assertRaises(ServiceError):self.store.register_grant(self.grant)
        self.assertEqual(before,self.store.path.read_bytes())
        self.store.path.unlink();outside=self.control/'outside';outside.write_text('preserve');self.store.path.symlink_to(outside)
        with self.assertRaises(ServiceError):self.store.tasks()
        self.assertEqual(outside.read_text(),'preserve')

    def test_http_owner_csrf_gate_and_no_browser_self_authorization(self):
        helper=http_fixture.LocalHTTPTests();helper.projects=self.helper.projects;helper.designs=self.helper.fixture.service
        helper.owner_proof='fixture-only-public-test-vector-00000000000000000000'
        helper.server=HubHTTPServer(helper.projects,helper.designs,tasks=self.service,owner_auth=OwnerAuth(provider=lambda:helper.owner_proof))
        import threading
        helper.thread=threading.Thread(target=helper.server.serve_forever,kwargs={'poll_interval':.01});helper.thread.start();self.addCleanup(helper.stop)
        helper.cookie=helper.csrf=None
        _,headers,data=helper.call(path='/api/session',authenticated=False);helper.cookie=headers['Set-Cookie'].split(';')[0];helper.csrf=data['data']['csrf_token']
        self.assertEqual(helper.call('POST','/api/tasks',self.payload)[0],401);self.assertEqual(helper.call(path='/api/tasks')[0],401)
        helper.session()
        self.assertEqual(helper.call('POST','/api/tasks',self.payload,headers={'X-Hub-CSRF':'bad'})[0],403)
        self.assertEqual(helper.call('POST','/api/tasks',{**self.payload,'command':'touch /etc/x'})[0],400)
        self.assertEqual(helper.call('POST','/api/tasks',self.payload)[0],200)
        self.assertEqual(helper.call(path='/api/tasks/fixture-task')[2]['data']['task']['status'],'queued')
        self.assertEqual(helper.call('POST','/api/tasks/fixture-task/cancel',{})[0],200)
        self.assertEqual(FakeAdapter.effects,0)


class ProtocolBoundaryTests(unittest.TestCase):
    def test_profiles_are_consistent_and_missing_echo_never_dispatches(self):
        adapter=AppServerAdapter('/fixture',schemas={})
        adapter.call=mock.Mock(return_value={'thread':{'id':'owned','cwd':'/fixture'},
                                            'activePermissionProfile':{'id':codex_module.PERMISSION_PROFILE,'extends':None}})
        self.assertEqual(adapter.thread(),'owned')
        self.assertEqual(adapter.call.call_args.args[1]['permissions'],codex_module.PERMISSION_PROFILE)
        self.assertNotIn('sandbox',adapter.call.call_args.args[1])
        for echo in [None,{'id':'unexpected'},{'id':codex_module.PERMISSION_PROFILE,'extends':':workspace'}]:
            adapter.call.reset_mock();adapter.call.return_value={'thread':{'id':'owned','cwd':'/fixture'},'activePermissionProfile':echo}
            with self.assertRaises(ServiceError):adapter.thread()
            self.assertIsNone(adapter.active_profile)
            with self.assertRaises(ServiceError):adapter.start('owned','synthetic')
            with self.assertRaises(ServiceError):adapter.check(['/usr/bin/true'])
            self.assertEqual(adapter.call.call_count,1)

    def test_profile_resume_turn_command_and_config_drift_have_no_legacy_fallback(self):
        adapter=AppServerAdapter('/fixture',schemas={});calls=[]
        def call(method,params):
            calls.append((method,params))
            if method=='thread/read':return {'thread':{'id':'owned','cwd':'/fixture','status':{'type':'idle'}}}
            if method=='thread/resume':return {'thread':{'id':'owned','cwd':'/fixture'},'activePermissionProfile':{'id':codex_module.PERMISSION_PROFILE}}
            return {'turn':{'id':'turn'}}if method=='turn/start'else{'exitCode':0,'stdout':'','stderr':''}
        adapter.call=call
        self.assertEqual(adapter.thread(previous='owned'),'owned');adapter.capability_thread='owned'
        with mock.patch.object(codex_module,'config_fingerprint',return_value=codex_module.REGISTERED_CONFIG_SHA256):
            adapter.start('owned','synthetic');adapter.check(['/usr/bin/true'])
        for method,params in calls:
            if method=='thread/read':continue
            self.assertEqual(params['permissionProfile'if method=='command/exec'else'permissions'],codex_module.PERMISSION_PROFILE)
            self.assertFalse({'sandbox','sandboxPolicy'}&set(params))
        old=len(calls)
        with mock.patch.object(codex_module,'config_fingerprint',return_value='0'*64):
            with self.assertRaises(ServiceError):adapter.start('owned','synthetic')
            with self.assertRaises(ServiceError):adapter.check(['/usr/bin/true'])
        self.assertEqual(len(calls),old)

    def test_registered_names_only_and_config_drift_never_parsed(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'config.toml'
            original=b'[mcp_servers.node_repl.env]\nPRIVATE_TEST_VALUE="synthetic-private-sentinel"\n'
            config.write_bytes(original)
            with mock.patch.object(codex_module,'REGISTERED_CONFIG_SHA256',hashlib.sha256(original).hexdigest()), \
                 mock.patch.object(Path,'read_text',side_effect=AssertionError('never parse config')):
                args,names=codex_module.isolated_launch(('codex','app-server','--stdio'),config_path=config)
                self.assertEqual(names,list(codex_module.REGISTERED_SERVERS))
                self.assertEqual([args[i+1]for i,x in enumerate(args[:-1])if x=='-c' and args[i+1].startswith('mcp_servers.')],
                    ['mcp_servers.'+name+'.enabled=false'for name in names])
                self.assertNotIn('synthetic-private-sentinel',json.dumps(args))
                config.write_bytes(original+b'[mcp_servers.unexpected]\ncommand="synthetic-private-sentinel"\n')
                with self.assertRaises(ServiceError)as caught:
                    codex_module.isolated_launch(('codex',),config_path=config)
                self.assertEqual(caught.exception.code,'CODEX_CONFIG_DRIFT')
                self.assertNotIn('synthetic-private-sentinel',str(caught.exception))

    def test_child_exact_environment_omits_parent_control_and_secrets(self):
        names=['CODEX_APP_TOOLS_PIPE_PATH','CODEX_SESSION_ID','CODEX_THREAD_ID','CODEX_PERMISSION_PROFILE',
               'CODEX_HOME','SSH_AUTH_SOCK','DBUS_SESSION_BUS_ADDRESS','DISPLAY','WAYLAND_DISPLAY','XAUTHORITY',
               'HUB_OWNER_TOKEN','OPENAI_API_KEY','CODEX_API_KEY','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY',
               'SSL_CERT_FILE','UNRELATED_UNKNOWN_SECRET']
        with mock.patch.dict(os.environ,{name:'synthetic-private-sentinel'for name in names}), \
             mock.patch.object(codex_module,'config_fingerprint',return_value=codex_module.REGISTERED_CONFIG_SHA256), \
             mock.patch.object(codex_module.subprocess,'Popen',side_effect=RuntimeError('synthetic stopped'))as popen:
            with self.assertRaises(RuntimeError):AppServerAdapter('/fixture',schemas={}).open()
            env=popen.call_args.kwargs['env']
            self.assertEqual(env,codex_module.child_environment())
            self.assertEqual(set(env),{'HOME','PATH','LANG','LC_ALL'})
            self.assertFalse(set(names)&set(env))
            self.assertNotIn('synthetic-private-sentinel',json.dumps([popen.call_args.args,env]))
            self.assertEqual(popen.call_args.kwargs['start_new_session'],True)

    def test_version_probe_also_uses_allowlisted_environment(self):
        with mock.patch.dict(os.environ,{'CODEX_APP_TOOLS_PIPE_PATH':'synthetic-private-sentinel'}), \
             mock.patch.object(codex_module,'config_fingerprint',return_value=codex_module.REGISTERED_CONFIG_SHA256), \
             mock.patch.object(codex_module.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout=''))as run:
            with self.assertRaises(ServiceError):AppServerAdapter('/fixture').open()
            self.assertEqual(run.call_args.kwargs['env'],codex_module.child_environment())
            self.assertNotIn('synthetic-private-sentinel',json.dumps(run.call_args.kwargs['env']))

    def test_resume_baseline_old_usage_never_binds_to_next_turn(self):
        adapter=AppServerAdapter('/fixture',schemas={})
        adapter.notifications=[{'method':'thread/tokenUsage/updated','params':{'threadId':'owned','turnId':'previous'}}]
        baseline=adapter.drain_baseline('owned');self.assertEqual(baseline[0]['turn_id'],'previous')
        self.assertEqual(baseline[0]['phase'],'before_turn_dispatch');self.assertEqual(adapter.notifications,[])
        for note in [{'method':'thread/tokenUsage/updated','params':{'threadId':'other'}},
                     {'method':'turn/started','params':{'threadId':'owned'}}]:
            adapter.notifications=[note]
            with self.assertRaises(ServiceError):adapter.drain_baseline('owned')

    def test_versioned_notification_timestamp_only_on_notifications(self):
        good=b'{"method":"thread/started","params":{},"emittedAtMs":1791331219000}\n'
        adapter=AppServerAdapter('/fixture',schemas={});adapter.proc=SimpleNamespace(stdout=io.BytesIO(good));adapter._read()
        self.assertEqual(adapter.inbox.get()['emittedAtMs'],1791331219000)
        for raw in [b'{"id":1,"result":{},"emittedAtMs":1}\n',b'{"method":"thread/started","emittedAtMs":true}\n',
                    b'{"method":"thread/started","emittedAtMs":-1}\n',b'{"method":"thread/started","emittedAtMs":NaN}\n']:
            adapter=AppServerAdapter('/fixture',schemas={});adapter.proc=SimpleNamespace(stdout=io.BytesIO(raw));adapter._read()
            self.assertEqual(adapter.failed,'CODEX_PROTOCOL_REJECTED')

    def test_duplicate_json_oversize_and_unknown_envelope_fail_closed(self):
        for raw in [b'{"id":1,"id":2}\n',b'x'*(1024*1024+1),b'{"command":"untrusted"}\n']:
            adapter=AppServerAdapter('/fixture',schemas={});adapter.proc=SimpleNamespace(stdout=io.BytesIO(raw));adapter._read()
            self.assertEqual(adapter.failed,'CODEX_PROTOCOL_REJECTED')

    def test_approval_unknown_request_and_method_cannot_expand_permissions(self):
        adapter=AppServerAdapter('/fixture',schemas={});sent=[];adapter._send=sent.append
        for method in ['item/commandExecution/requestApproval','item/fileChange/requestApproval','unknown/request']:
            adapter.inbox.put({'id':17,'method':method,'params':{'cwd':'/outside','command':'touch /outside'}})
            self.assertIsNone(adapter._message(.01))
        self.assertEqual(sent[0]['result']['decision'],'decline');self.assertIn('error',sent[2])
        with self.assertRaises(ServiceError):adapter.call('thread/shellCommand',{})
        self.assertEqual(len(adapter.denials),3)
        self.assertNotIn('sk-testsecret',sanitized('api_key=sk-testsecret\x1b[31m'))

    def test_eof_timeout_and_wrong_response_id_remain_unknown(self):
        adapter=AppServerAdapter('/fixture',schemas={});adapter.proc=SimpleNamespace(stdout=io.BytesIO(b''));adapter._read()
        with self.assertRaises(ServiceError)as caught:adapter.poll(.01)
        self.assertEqual(caught.exception.outcome,'UNKNOWN')
        schemas={'InitializeParams':{'type':'object'},'InitializeResponse':{'type':'object'}}
        adapter=AppServerAdapter('/fixture',schemas=schemas);adapter._send=lambda _:None
        with self.assertRaises(ServiceError)as caught:adapter.call('initialize',{},timeout=.01)
        self.assertEqual(caught.exception.code,'CODEX_TIMEOUT')
        adapter.inbox.put({'id':999,'result':{}})
        with self.assertRaises(ServiceError)as caught:adapter.call('initialize',{})
        self.assertEqual(caught.exception.code,'CODEX_RESPONSE_CONFLICT')
