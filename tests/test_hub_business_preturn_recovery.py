"""Synthetic recovery of a rejected resume before any new model turn exists."""
import copy
import hashlib
import os
from pathlib import Path
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import test_hub_business_runtime_amendment as fixture
from hub.service_contract import ServiceError
from hub.workbench_store import digest


class BusinessPreturnRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.BusinessRuntimeAmendmentTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.store=self.f.store
        self.store.event('task-1','business-resumed',{'thread_id':'same-thread','prior_turn_id':'first-interrupted-turn','additional_turn_limit':1})
        self.f.amend()
        self.store.event('task-1','business-runtime-amendment-resume-intent',{'thread_id':'same-thread','authority_sha256':'b'*64})
        self.store.claim('closed-preturn-worker')
        message=b'thread same-thread already has an active writer'
        result={'error_class':'CODEX_RPC_REJECTED','details':{'method':'thread/resume','rpc_code':-32600,
                'message_sha256':hashlib.sha256(message).hexdigest(),'message_bytes':len(message),
                'raw_message_retained':False},'effects_rolled_back':False,'request_denials':[]}
        self.store.update('task-1','closed-preturn-worker',status='failed',result=result)
        self.original=self.store.task('task-1');self.old_events=self.store.events('task-1')
        self.authority={'contract':'PCH-STORY-PRETURN-RECOVERY-v1','actor':'trusted_local_owner',
            'owner_decision_sha256':digest('允许全部改为10 分钟上限'),'task_hash':digest(self.original),
            'events_hash':digest(self.old_events),'thread_id':'same-thread','turn_history_sha256':'a'*64,'additional_turn_limit':1}
        self.observation={'source':'app_server_stdio.thread/read','root':str(self.f.f.root),'thread_id':'same-thread',
            'thread_status':'notLoaded','turns':[{'id':'first-interrupted-turn','status':'interrupted'},
            {'id':'interrupted-turn','status':'interrupted'}],'turn_history_sha256':'a'*64,'observed_at':time.time(),
            'lifecycle':{'method':'supported_owned_thread_archive_unarchive','cycles':1,'same_history':True,
            'prior_holder':{'pid':123456789,'start_ticks':'1','fd':1364,'device':1,'inode':1}}}
        self.grant=copy.deepcopy(self.f.grant);self.grant.update(version=3,expires_at=time.time()+3000)
        self.grant['story_exchange']['registration_sha256']='8'*64

    def recover(self):
        return self.store.reconcile_business_preturn_rejection('task-1',authority=self.authority,
                                                               observation=self.observation,grant=self.grant)

    def unchanged(self):
        self.assertEqual(self.store.task('task-1'),self.original)
        self.assertEqual(self.store.grant(self.f.grant['grant_id']),self.f.grant)
        self.assertEqual(self.store.events('task-1'),self.old_events)

    def refresh_fence(self):
        self.original=self.store.task('task-1');self.old_events=self.store.events('task-1')
        self.authority.update(task_hash=digest(self.original),events_hash=digest(self.old_events))

    def test_exact_recovery_keeps_task_thread_history_and_original_failure(self):
        self.recover();task=self.store.task('task-1')
        self.assertEqual(task['id'],'task-1');self.assertEqual(task['thread_id'],'same-thread')
        self.assertEqual(task['status'],'queued');self.assertIsNone(task['owner']);self.assertIsNone(task['lease_until'])
        self.assertIsNone(task['turn_id']);self.assertIsNone(task['provider_status']);self.assertIsNone(task['result'])
        self.assertEqual(task['payload']['grant_version'],3)
        self.assertEqual(task['intent']['grant'],self.grant)
        self.assertEqual(self.store.grant(self.grant['grant_id']),self.grant)
        events=self.store.events('task-1');self.assertEqual(events[:len(self.old_events)],self.old_events)
        event=next(e['data']for e in events if e['key']=='business-preturn-reconciled-v1')
        self.assertEqual(event['original_thread_id'],'same-thread')
        self.assertEqual(event['original_turn_id'],'interrupted-turn')
        self.assertEqual(event['original_payload'],self.original['payload'])
        self.assertEqual(event['original_intent'],self.original['intent'])
        self.assertEqual(event['original_result'],self.original['result'])
        self.assertEqual(event['action'],'same_owned_thread_new_turn_after_verified_interruption')
        with self.assertRaises(ServiceError):self.recover()

    def test_mismatched_authority_and_native_lifecycle_rejected_atomically(self):
        for field in self.authority:
            old=self.authority[field];self.authority[field]='wrong'
            with self.subTest(authority=field),self.assertRaises(ServiceError):self.recover()
            self.authority[field]=old;self.unchanged()
        changes=[lambda p:p.update(source='thread/list'),lambda p:p.update(root='/other'),
                 lambda p:p.update(thread_id='other'),lambda p:p.update(thread_status='active'),
                 lambda p:p.update(turn_history_sha256='d'*64),lambda p:p.update(observed_at=time.time()-31),
                 lambda p:p['turns'][-1].update(status='completed'),
                 lambda p:p['turns'][0].update(id='wrong'),lambda p:p['lifecycle'].update(cycles=2),
                 lambda p:p['lifecycle'].update(method='unsupported'),lambda p:p['lifecycle'].update(same_history=False)]
        for mutate in changes:
            old=copy.deepcopy(self.observation);mutate(self.observation)
            with self.subTest(proof=self.observation),self.assertRaises(ServiceError):self.recover()
            self.observation=old;self.unchanged()

    def test_wrong_rpc_or_unknown_effects_cannot_be_recovered(self):
        changes=[lambda r:r.update(error_class='TASK_WORKER_ERROR'),
                 lambda r:r['details'].update(method='turn/start'),lambda r:r['details'].update(rpc_code=-1),
                 lambda r:r['details'].update(message_sha256='0'*64),lambda r:r['details'].update(message_bytes=0),
                 lambda r:r['details'].update(raw_message_retained=True)]
        baseline=copy.deepcopy(self.original['result'])
        for mutate in changes:
            result=copy.deepcopy(baseline);mutate(result)
            with self.store._connection(True)as db:
                from hub.workbench_store import canonical
                db.execute('UPDATE tasks SET result=?',(canonical({'data':result,'hash':digest(result)}).decode(),))
            self.refresh_fence()
            with self.subTest(result=result),self.assertRaises(ServiceError):self.recover()
            self.unchanged()

    def test_new_turn_or_successful_amended_resume_after_consumption_blocks_replay(self):
        for key in ('turn-intent-unexpected','business-runtime-amendment-resumed'):
            self.store.event('task-1',key,{'thread_id':'same-thread'})
            self.refresh_fence()
            with self.subTest(key=key),self.assertRaises(ServiceError):self.recover()
            self.unchanged()
            with self.store._connection(True)as db:db.execute('DELETE FROM events WHERE task_id=?AND event_key=?',('task-1',key))
            self.refresh_fence()

    def test_scope_output_and_source_changes_rejected(self):
        for mutate in (lambda g:g.update(max_turn_seconds=601),lambda g:g.update(expires_at=time.time()-1),lambda g:g.update(writable_scope=['.']),
                       lambda g:g['task_spec'].update(scope='different'),
                       lambda g:g['story_exchange']['source_preconditions'].update(workspace_revision=999)):
            old=copy.deepcopy(self.grant);mutate(self.grant)
            with self.subTest(grant=self.grant),self.assertRaises(ServiceError):self.recover()
            self.grant=old;self.unchanged()
        output=self.f.f.root/'output/result.json';output.write_text('{}')
        with self.assertRaises(ServiceError):self.recover()
        self.unchanged();output.unlink()
        from hub.story_promotion import WORKSPACE
        (self.f.f.source/WORKSPACE).write_text('{"revision":999}')
        with self.assertRaises((ServiceError,KeyError)):self.recover()
        self.unchanged()

    def test_cancel_or_live_recorded_worker_or_exact_prior_fd_blocks_recovery(self):
        with self.store._connection(True)as db:db.execute('UPDATE tasks SET cancel=1')
        self.refresh_fence()
        with self.assertRaises(ServiceError):self.recover()
        self.unchanged()
        with self.store._connection(True)as db:db.execute('UPDATE tasks SET cancel=0')
        self.refresh_fence()
        ticks=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]
        fd=os.open(self.f.f.root/'AGENTS.md',os.O_RDONLY)
        try:
            info=os.fstat(fd)
            self.observation['lifecycle']['prior_holder']={'pid':os.getpid(),'start_ticks':ticks,'fd':fd,'device':info.st_dev,'inode':info.st_ino}
            with self.assertRaises(ServiceError):self.recover()
            self.unchanged()
        finally:os.close(fd)
        self.observation['lifecycle']['prior_holder']={'pid':123456789,'start_ticks':'1','fd':1364,'device':1,'inode':1}
        process={'pid':os.getpid(),'start_ticks':ticks}
        for key,value in (('dispatch-intent-preturn-worker',{'worker':process}),('stdio-child-preturn-worker',process)):
            self.store.event('task-1',key,value)
            self.refresh_fence()
            with self.subTest(key=key),self.assertRaises(ServiceError):self.recover()
            self.unchanged()
            with self.store._connection(True)as db:db.execute('DELETE FROM events WHERE task_id=?AND event_key=?',('task-1',key))
            self.refresh_fence()

    def test_event_append_failure_rolls_back_every_change(self):
        append=self.store._append_event
        def fail(*args,**kwargs):append(*args,**kwargs);raise RuntimeError('synthetic failure')
        with mock.patch.object(self.store,'_append_event',side_effect=fail),self.assertRaises(RuntimeError):self.recover()
        self.unchanged()


if __name__=='__main__':unittest.main()
