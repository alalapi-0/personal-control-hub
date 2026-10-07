"""Isolated Hub request/queue tests; provider events are explicitly mock."""
import copy
import hashlib
import json
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_hub_task_runtime as fixture
from hub.codex_adapter import AppServerAdapter
from hub.service_contract import ServiceError
from hub.task_store import TaskStore
from hub.task_worker import TaskWorker
from hub.local_service import HubHTTPServer
from hub.owner_auth import OwnerAuth
from hub.workbench_store import canonical


class BusyControlTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.TaskRuntimeTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def active(self):
        f=self.f;f.service.submit(f.payload);f.store.claim('owned-parent')
        f.store.update('fixture-task','owned-parent',thread_id='fixture-thread',turn_id='fixture-turn',provider_status='inProgress')

    def feedback(self,index=1):
        f=self.f;grant={**f.grant,'grant_id':f'feedback-grant-{index}','task_id':f'feedback-task-{index}','mode':'busy_feedback','parent_task_id':'fixture-task'}
        f.store.register_grant(grant)
        payload={**f.payload,'request_id':grant['task_id'],'grant_id':grant['grant_id'],'mode':'busy_feedback','parent_task_id':'fixture-task'}
        return payload

    def request(self,kind='approval',expiry=10):
        method='item/tool/requestUserInput' if kind=='input' else 'item/commandExecution/requestApproval'
        return self.f.store.open_control('fixture-task','owned-parent',request_hash='a'*64,method=method,
            thread_id='fixture-thread',turn_id='fixture-turn',expires_at=time.time()+expiry)

    def test_persistent_fifo_two_clients_idempotency_and_bound_turn(self):
        self.active();f=self.f;p1=self.feedback(1);p2=self.feedback(2)
        second=f.helper.command(request_id='second-feedback-annotation',expected_revision=1,requested_change='Second distinct feedback: preserve the title and review spacing.')
        receipt=f.helper.save(second)
        grant=f.store.grant(p2['grant_id']);f.store.register_grant({**grant,'version':2,'annotation_hash':fixture.digest(second)})
        p2.update(annotation_id=second['request_id'],annotation_revision=receipt['receipt']['revision'],grant_version=2)
        t1=f.service.submit(p1)['task'];t2=f.service.submit(p2)['task']
        self.assertLess(t1['sequence'],t2['sequence']);self.assertTrue(f.service.submit(p1)['replayed'])
        self.assertFalse(t1['feedback']['applied_to_active_turn']);self.assertEqual(t1['feedback']['mechanism'],'persistent_queue')
        self.assertEqual(t1['feedback']['parent_binding'],{'thread_id':'fixture-thread','turn_id':'fixture-turn'})
        reopened=TaskStore(f.control);self.assertEqual(len(reopened.tasks()),3)
        self.assertIsNone(reopened.claim('second-worker'));self.assertEqual(fixture.FakeAdapter.effects,0)
        with self.assertRaises(ServiceError):f.service.submit({**p1,'annotation_revision':2})
        f.store.update('fixture-task','owned-parent',status='validating',provider_status='completed')
        f.store.update('fixture-task','owned-parent',status='checks_complete')
        self.assertEqual(reopened.claim('feedback-worker')['id'],p1['request_id'])
        self.assertIsNone(reopened.claim('other-worker'))

    def test_unknown_external_writer_keeps_queue_no_provider(self):
        self.active();f=self.f;p=self.feedback();f.service.writer_check=lambda _:False
        task=f.service.submit(p)['task'];self.assertEqual(task['feedback']['writer_status'],'unknown')
        self.assertIsNone(f.store.claim('other-worker'));self.assertEqual(fixture.FakeAdapter.opened,0)

    def test_queue_cancel_and_parent_failure_never_dispatch(self):
        self.active();f=self.f;p=self.feedback();f.service.submit(p);f.service.cancel(p['request_id'])
        self.assertEqual(f.store.task(p['request_id'])['status'],'cancelled');self.assertIsNone(f.store.claim('other-worker'))
        p=self.feedback(2);f.service.submit(p);f.store.update('fixture-task','owned-parent',status='failed')
        self.assertIsNone(f.store.claim('other-worker'));self.assertEqual(f.store.task(p['request_id'])['status'],'requires_reconcile')
        self.assertEqual(fixture.FakeAdapter.effects,0)

    def test_wrong_expected_binding_is_rejected(self):
        self.active();f=self.f;p=self.feedback()
        with self.assertRaises(ServiceError):f.service.submit({**p,'expectedTurnId':'wrong-turn'})
        with self.assertRaises(ServiceError):f.store.update('fixture-task','owned-parent',turn_id='wrong-turn')
        with self.assertRaises(ServiceError):f.store.open_control('fixture-task','owned-parent',request_hash='a'*64,
            method='item/commandExecution/requestApproval',thread_id='fixture-thread',turn_id='wrong-turn',expires_at=time.time()+1)
        self.assertEqual(f.store.controls('fixture-task'),[])

    def legacy_feedback(self, binding):
        f=fixture.TaskRuntimeTests();f.setUp();self.addCleanup(f.doCleanups)
        f.service.submit(f.payload);f.store.claim('owned-parent')
        f.store.update('fixture-task','owned-parent',thread_id='fixture-thread',turn_id='fixture-turn',provider_status='completed',status='validating')
        grant={**f.grant,'grant_id':'legacy-feedback-grant','task_id':'legacy-feedback','mode':'busy_feedback','parent_task_id':'fixture-task'}
        f.store.register_grant(grant)
        payload={**f.payload,'request_id':grant['task_id'],'grant_id':grant['grant_id'],'mode':'busy_feedback','parent_task_id':'fixture-task'}
        f.service.submit(payload);f.store.update('fixture-task','owned-parent',status='checks_complete')
        intent=f.store.task('legacy-feedback')['intent']
        if binding is ...:intent.pop('parent_binding')
        else:intent['parent_binding']=binding
        # A canonical schema-1 legacy record is readable, but gains no execution authority.
        with f.store._connection(True) as connection:
            connection.execute('UPDATE tasks SET intent=?,intent_hash=? WHERE id=?',
                (canonical(intent).decode(),fixture.digest(intent),'legacy-feedback'))
        self.assertEqual(f.store.task('legacy-feedback')['intent'],intent)
        return f

    def test_legacy_unbound_or_malformed_feedback_reconciles_without_provider(self):
        for binding in [...,None,{},[],{'thread_id':'fixture-thread'},
                        {'thread_id':'fixture-thread','turn_id':'wrong-turn'},
                        {'thread_id':'fixture-thread','turn_id':'fixture-turn','extra':True},
                        {'thread_id':'fixture-thread','turn_id':''}]:
            with self.subTest(binding=binding):
                f=self.legacy_feedback(binding)
                self.assertFalse(f.worker().run_one())
                self.assertEqual(f.store.task('legacy-feedback')['status'],'requires_reconcile')
                self.assertEqual((fixture.FakeAdapter.opened,fixture.FakeAdapter.effects),(0,0))
                self.assertEqual(f.store.events('legacy-feedback'),[])

    def test_worker_rechecks_legacy_binding_before_opening_provider(self):
        for binding in [...,None,{}, {'thread_id':'fixture-thread','turn_id':'wrong-turn'}]:
            with self.subTest(binding=binding):
                f=self.legacy_feedback(binding)
                # Force a stale claim implementation to exercise the second boundary independently.
                worker=f.worker()
                with f.store._connection(True) as connection:
                    connection.execute("UPDATE tasks SET status='running',owner=?,lease_until=? WHERE id=?",
                        (worker.owner,time.time()+15,'legacy-feedback'))
                with patch.object(f.store,'claim',return_value=f.store.task('legacy-feedback')):
                    self.assertTrue(worker.run_one())
                task=f.store.task('legacy-feedback')
                self.assertEqual(task['status'],'requires_reconcile')
                self.assertEqual(task['result']['error_class'],'TASK_PARENT_NOT_IDLE')
                self.assertEqual((fixture.FakeAdapter.opened,fixture.FakeAdapter.effects),(0,0))
                self.assertEqual(f.store.events('legacy-feedback'),[])

    def test_restart_request_deny_two_client_cas_and_private_fields(self):
        self.active();f=self.f;ident=self.request();reopened=TaskStore(f.control)
        self.assertEqual(reopened.task('fixture-task')['status'],'waiting_approval')
        with self.assertRaises(ServiceError):reopened.respond_control('fixture-task',ident,1,'decline',owner_context='wrong-owner')
        for version,decision in [(2,'decline'),(1,'approve'),(True,'decline')]:
            with self.assertRaises(ServiceError):reopened.respond_control('fixture-task',ident,version,decision)
        self.assertFalse(reopened.respond_control('fixture-task',ident,1,'decline'))
        self.assertTrue(f.store.respond_control('fixture-task',ident,1,'decline'))
        self.assertEqual(len([e for e in f.store.events('fixture-task') if e['key'].startswith('control-decision-')]),1)
        public=json.dumps(f.service.get('fixture-task'))
        for secret in ['owned-parent','trusted_local_owner',str(f.workspace),'grant_hash','owner_context']:self.assertNotIn(secret,public)
        self.assertEqual(f.service.get('fixture-task')['controls'][0]['status'],'declined')

    def test_expiry_revocation_unknown_request_and_cancel(self):
        for reason in ['expired','grant_revoked','cancelled']:
            with self.subTest(reason=reason):
                f=fixture.TaskRuntimeTests();f.setUp()
                try:
                    f.service.submit(f.payload);f.store.claim('owned-parent');f.store.update('fixture-task','owned-parent',thread_id='fixture-thread',turn_id='fixture-turn')
                    ident=f.store.open_control('fixture-task','owned-parent',request_hash='a'*64,method='item/tool/requestUserInput',thread_id='fixture-thread',turn_id='fixture-turn',expires_at=time.time()+10)
                    if reason=='expired':
                        with patch('hub.task_store.time.time',return_value=time.time()+11):
                            with self.assertRaises(ServiceError):f.store.respond_control('fixture-task',ident,1,'decline')
                    elif reason=='grant_revoked':
                        f.store.register_grant({**f.grant,'version':2})
                        with self.assertRaises(ServiceError):f.store.respond_control('fixture-task',ident,1,'decline')
                    else:
                        f.store.cancel('fixture-task')
                        with self.assertRaises(ServiceError):f.store.respond_control('fixture-task',ident,1,'decline')
                    f.store.close_control('fixture-task','owned-parent',ident,reason)
                    self.assertEqual(f.store.controls('fixture-task')[0]['status'],reason)
                finally:f.doCleanups()
        self.active()
        with self.assertRaises(ServiceError):self.f.store.open_control('fixture-task','owned-parent',request_hash='a'*64,method='unknown/request',thread_id='fixture-thread',turn_id='fixture-turn',expires_at=time.time()+10)

    def test_auth_guard_after_fault_rolls_back_and_after_commit_is_reconcilable(self):
        self.active();f=self.f;ident=self.request();valid=[True]
        def guard():
            if not valid[0]:raise ServiceError('OWNER_AUTH_FAILED',status=401)
        f.store.fault=lambda phase:valid.__setitem__(0,False) if phase=='before_commit' else None
        with self.assertRaises(ServiceError):f.store.respond_control('fixture-task',ident,1,'decline',authorize=guard)
        self.assertEqual(f.store.controls('fixture-task')[0]['version'],1)
        f.store.fault=lambda phase:(_ for _ in ()).throw(OSError()) if phase=='after_commit' else None
        with self.assertRaises(ServiceError) as caught:f.store.respond_control('fixture-task',ident,1,'decline')
        self.assertEqual(caught.exception.outcome,'UNKNOWN');f.store.fault=lambda _:None
        self.assertTrue(f.store.respond_control('fixture-task',ident,1,'decline'))
        self.assertEqual(f.store.controls('fixture-task')[0]['version'],2)

    def test_owned_worker_waits_for_explicit_decline_and_interrupts_exact_turn(self):
        f=self.f
        class RequestAdapter(fixture.FakeAdapter):
            asked=False;interrupts=[]
            def poll(self,timeout):
                if not self.asked:
                    self.asked=True;req={'id':7,'method':'item/commandExecution/requestApproval','params':{'threadId':'fixture-thread','turnId':'fixture-turn','command':'PRIVATE provider command'}}
                    result=self.request_handler(req);self.denials.append({'method':req['method'],**result});return None
                return super().poll(timeout)
            def interrupt(self,thread,turn):self.interrupts.append((thread,turn))
        f.service.submit(f.payload);worker=TaskWorker(f.store,adapter_factory=RequestAdapter,writer_check=lambda _:True)
        thread=threading.Thread(target=worker.run_one);thread.start();self.addCleanup(lambda:thread.join(3))
        deadline=time.monotonic()+2
        while f.store.task('fixture-task')['status']!='waiting_approval' and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(f.store.task('fixture-task')['status'],'waiting_approval')
        control=f.store.controls('fixture-task')[0];self.assertNotIn('PRIVATE',json.dumps(control))
        f.service.respond_control('fixture-task',{'control_id':control['id'],'version':1,'decision':'decline'})
        thread.join(2);self.assertFalse(thread.is_alive());self.assertEqual(f.store.task('fixture-task')['status'],'requires_reconcile')
        self.assertEqual(RequestAdapter.interrupts,[('fixture-thread','fixture-turn')])
        self.assertFalse(worker.run_one())

    def test_adapter_unknown_denies_and_notloaded_does_not_prove_idle(self):
        adapter=AppServerAdapter(self.f.workspace,schemas={'ServerRequest':{'type':'object'}});sent=[];adapter._send=sent.append
        adapter.inbox.put({'id':2,'method':'unknown/request','params':{}});adapter._message(.1)
        self.assertIn('error',sent[0]);self.assertEqual(len(adapter.denials),1)
        adapter.call=lambda *_args,**_kwargs:{'thread':{'id':'fixture-thread','cwd':str(self.f.workspace),'status':{'type':'notLoaded'}}}
        with self.assertRaises(ServiceError) as caught:adapter.thread(previous='fixture-thread')
        self.assertEqual(caught.exception.code,'CODEX_THREAD_NOT_IDLE')

    def test_actual_http_two_owner_sessions_csrf_rotation_and_deny_only(self):
        f=self.f;h=fixture.http_fixture.LocalHTTPTests();h.projects=f.helper.projects;h.designs=f.helper.fixture.service
        h.owner_proof='fixture-only-public-test-vector-'+'x'*40
        h.server=HubHTTPServer(h.projects,h.designs,tasks=f.service,owner_auth=OwnerAuth(provider=lambda:h.owner_proof))
        h.thread=threading.Thread(target=h.server.serve_forever,kwargs={'poll_interval':.01});h.thread.start();self.addCleanup(h.stop)
        h.cookie=h.csrf=None;h.session()
        self.assertEqual(h.call('POST','/api/tasks',f.payload)[0],200)
        f.store.claim('owned-parent');f.store.update('fixture-task','owned-parent',thread_id='fixture-thread',turn_id='fixture-turn')
        control=self.request();command={'control_id':control,'version':1,'decision':'decline'};url='/api/tasks/fixture-task/respond'
        self.assertEqual(h.call('POST',url,command,headers={'X-Hub-CSRF':'wrong'})[0],403)
        self.assertEqual(h.call('POST',url,{**command,'decision':'approve'})[0],409)
        self.assertEqual(h.call('POST',url,{**command,'thread_id':'wrong'})[0],400)
        cookie,csrf=h.cookie,h.csrf;h.cookie=h.csrf=None;h.session();second=(h.cookie,h.csrf)
        self.assertNotEqual(cookie,second[0]);self.assertEqual(h.call('POST',url,command)[0],200)
        h.cookie,h.csrf=cookie,csrf;response=h.call('POST',url,command)
        self.assertEqual(response[0],200);self.assertTrue(response[2]['data']['replayed'])
        h.owner_proof='different-public-test-vector-'+'y'*40
        self.assertEqual(h.call('POST',url,command)[0],401)
        self.assertEqual(len([e for e in f.store.events('fixture-task') if e['key'].startswith('control-decision-')]),1)

    def test_owned_mock_input_timeout_returns_safe_decline(self):
        f=self.f
        class InputAdapter(fixture.FakeAdapter):
            asked=False
            def poll(self,timeout):
                if not self.asked:
                    self.asked=True;req={'id':8,'method':'item/tool/requestUserInput','params':{'threadId':'fixture-thread','turnId':'fixture-turn','questions':[{'id':'q','question':'PRIVATE form'}]}}
                    result=self.request_handler(req);self.denials.append({'method':req['method'],**result});return None
                return super().poll(timeout)
        f.service.submit(f.payload);TaskWorker(f.store,adapter_factory=InputAdapter,writer_check=lambda _:True,request_timeout=1).run_one()
        self.assertEqual(f.store.controls('fixture-task')[0]['status'],'expired')
        self.assertEqual(f.store.task('fixture-task')['status'],'requires_reconcile')
        self.assertNotIn('PRIVATE',json.dumps(f.service.get('fixture-task')))

    def test_large_stream_is_aggregated_but_lifecycle_and_wrong_binding_are_preserved(self):
        f=self.f
        class StreamAdapter(fixture.FakeAdapter):
            def start(self,*args):
                ident=super().start(*args)
                self.events[1:1]=[{'method':'item/agentMessage/delta','params':{'threadId':'fixture-thread','turnId':'fixture-turn','delta':'PRIVATE chunk '+str(i)}} for i in range(600)]
                return ident
        f.service.submit(f.payload);TaskWorker(f.store,adapter_factory=StreamAdapter,writer_check=lambda _:True).run_one()
        t=f.store.task('fixture-task');self.assertEqual(t['status'],'checks_complete')
        self.assertEqual(t['result']['stream_observation']['count'],600)
        events=f.store.events('fixture-task');self.assertEqual(sum(e['data'].get('method')=='turn/completed' for e in events),1)
        self.assertFalse(any(e['data'].get('method')=='item/agentMessage/delta' for e in events));self.assertNotIn('PRIVATE',json.dumps(f.service.get('fixture-task')))
        self.assertLess(t['result']['stream_observation']['critical_events'],256)

    def test_stream_binding_rejected_and_critical_capacity_remains_hard(self):
        for wrong in (True,False):
            f=fixture.TaskRuntimeTests();f.setUp()
            try:
                class StreamAdapter(fixture.FakeAdapter):
                    def start(self,*args):
                        ident=super().start(*args)
                        if wrong:self.events.insert(1,{'method':'item/agentMessage/delta','params':{'threadId':'fixture-thread','turnId':'wrong-turn','delta':'PRIVATE'}})
                        else:self.events[1:1]=[{'method':'item/started','params':{'threadId':'fixture-thread','turnId':'fixture-turn','item':{'type':'reasoning','id':str(i)}}} for i in range(260)]
                        return ident
                f.service.submit(f.payload);TaskWorker(f.store,adapter_factory=StreamAdapter,writer_check=lambda _:True).run_one()
                self.assertEqual(f.store.task('fixture-task')['status'],'requires_reconcile')
                self.assertEqual(f.store.task('fixture-task')['result']['error_class'],'CODEX_EVENT_BINDING_REJECTED' if wrong else 'TASK_EVENT_CAPACITY')
                self.assertFalse(f.worker().run_one())
            finally:f.doCleanups()


if __name__=='__main__':unittest.main()
