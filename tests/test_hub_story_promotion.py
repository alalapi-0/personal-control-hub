"""Synthetic transaction receipts do not constitute native or business evidence."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub import story_promotion as p
from hub.service_contract import ServiceError
from hub.story_exchange import output_result
from hub.task_store import TaskStore
from hub.workbench_store import digest
import test_hub_story_exchange as fixture


class StoryPromotionTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.StoryExchangeTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.grant=self.f.grant;self.root=self.f.source
        for name,value in ((p.WORKSPACE,{'revision':22,'baseline_id':'baseline','dataset':'live'}),(p.OBSERVATION,{'protected':True})):
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
        self.grant['story_exchange']['source_preconditions']=p.source_snapshot(self.grant,expected=False)
        self.f.write_result();self.ready={**output_result(self.grant),'transport':'mock'}
        self.hub=self.root.parent/'hub';self.hub.mkdir();self.store=TaskStore(self.hub);self.store.register_storage()
        self.store.register_grant(self.grant)
        payload={'request_id':'task-1','project_id':'story-faceless-utopia','parent_task_id':None,'mode':'new','kind':'story_exchange'}
        self.store.admit(payload,{'grant':self.grant,'grant_hash':digest(self.grant)})
        self.store.claim('mock-owner');self.store.update('task-1','mock-owner',thread_id='mock-thread',turn_id='mock-turn',provider_status='completed',status='validating',result=self.ready)
        self.store.handoff_validation('task-1','mock-owner',digest(self.ready))
        self.calls=[]

    def command(self,root,*args):
        self.calls.append(args[0]);check={'id':'story-'+args[0],'exit':0}
        if args[0]=='import-result':
            raw=json.loads(Path(args[2]).read_text());self.assertEqual(raw['revision'],22)
            self.assertEqual(raw['result'],self.f.result)
            path=Path(root)/p.WORKSPACE;w=json.loads(path.read_text());w['revision']=23;path.write_text(json.dumps(w))
            return {'noop':False,'revision':23,'result':{'request_id':'request-1','result_sha256':'7'*64,'imported':[{'id':'one'},{'id':'two'},{'id':'three'}]}},check
        return {'valid':True},check

    def promote(self):
        with mock.patch.object(p,'child_command',side_effect=self.command):
            return p.promote(self.store,'task-1',import_path=self.hub/'import.json',verify_sources=lambda **_:None)

    def test_exact_local_transaction_then_checks_and_no_second_effect(self):
        result=self.promote();task=self.store.task('task-1')
        self.assertEqual(task['status'],'checks_complete');self.assertEqual(task['human_acceptance'],'pending')
        self.assertEqual(result['source_binding']['workspace_revision'],23)
        self.assertEqual(self.calls,['import-result','validate','workbench-handoff'])
        self.assertTrue(any(e['key']=='story-promoted'for e in self.store.events('task-1')))
        with self.assertRaises(ServiceError):self.promote()
        self.assertEqual(len(self.calls),3)

    def test_cancel_revocation_and_source_drift_have_zero_effect(self):
        for cause in ('cancel','revocation','source'):
            with self.subTest(cause=cause):
                if cause=='cancel':self.store.cancel('task-1')
                elif cause=='revocation':
                    with self.store._connection(True)as db:db.execute('DELETE FROM grants')
                else:(self.root/p.WORKSPACE).write_text('{}')
                with self.assertRaises((ServiceError,KeyError)):self.promote()
                self.assertEqual(self.calls,[])
                if cause=='cancel':
                    with self.store._connection(True)as db:db.execute('UPDATE tasks SET cancel=0')
                elif cause=='revocation':self.store.register_grant(self.grant)

    def test_uncertain_intent_blocks_retry(self):
        self.store.event('task-1','story-promotion-intent',{'outcome':'unknown'})
        with self.assertRaises(ServiceError)as error:self.promote()
        self.assertEqual(error.exception.code,'BUSINESS_PROMOTION_REQUIRES_RECONCILE');self.assertEqual(self.calls,[])

    def test_finalization_requires_matching_durable_import(self):
        result={**self.ready,'source_promoted':True,'validation_scope':'canonical_project','checks':[{'id':'mock','exit':0}]}
        with self.assertRaises(ServiceError):self.store.finalize_local_validation('task-1',digest(self.ready),result)
        self.assertEqual(self.store.task('task-1')['status'],'validating')

    def test_failed_post_import_check_retains_effect_and_blocks_retry(self):
        actual=self.command
        def fail(root,*args):
            if args[0]=='validate':raise ServiceError('BUSINESS_CHILD_CHECK_FAILED',status=409)
            return actual(root,*args)
        with mock.patch.object(p,'child_command',side_effect=fail),self.assertRaises(ServiceError):
            p.promote(self.store,'task-1',import_path=self.hub/'import.json',verify_sources=lambda **_:None)
        self.assertEqual(json.loads((self.root/p.WORKSPACE).read_text())['revision'],23)
        self.assertEqual(self.store.task('task-1')['status'],'validating')
        with self.assertRaises(ServiceError):self.promote()
        self.assertEqual(self.calls,['import-result'])

    def test_story_interruption_reconciliation_keeps_task_thread_and_failure(self):
        (self.f.root/'output/result.json').unlink()
        with self.store._connection(True)as db:
            db.execute("UPDATE tasks SET status='requires_reconcile',provider_status='inProgress'")
        proof={'source':'app_server_stdio.thread/read','root':str(self.f.root),'thread_id':'mock-thread',
               'turn_id':'mock-turn','thread_status':'notLoaded','turn_status':'interrupted','observed_at':time.time()}
        original=self.store.task('task-1');self.store.reconcile_owned('task-1',proof)
        queued=self.store.task('task-1');self.assertEqual(queued['status'],'queued')
        self.assertEqual(queued['thread_id'],'mock-thread');self.assertIsNone(queued['turn_id'])
        event=next(e for e in self.store.events('task-1')if e['key'].startswith('reconcile-'))
        self.assertEqual(event['data']['original_result'],original['result'])
        with self.store._connection(True)as db:
            db.execute("UPDATE tasks SET status='requires_reconcile',turn_id='mock-turn'")
        with self.assertRaises(ServiceError):self.store.reconcile_owned('task-1',proof)

    def test_story_reconciliation_rejects_any_output_or_changed_source(self):
        with self.store._connection(True)as db:db.execute("UPDATE tasks SET status='requires_reconcile'")
        proof={'source':'app_server_stdio.thread/read','root':str(self.f.root),'thread_id':'mock-thread',
               'turn_id':'mock-turn','thread_status':'notLoaded','turn_status':'interrupted','observed_at':time.time()}
        with self.assertRaises(ServiceError):self.store.reconcile_owned('task-1',proof)
        (self.f.root/'output/result.json').unlink()
        (self.root/p.WORKSPACE).write_text('{"revision":999}')
        with self.assertRaises((ServiceError,KeyError)):self.store.reconcile_owned('task-1',proof)


if __name__=='__main__':unittest.main()
