from __future__ import annotations
import copy
import hashlib
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub.task_store import TaskStore
from hub.service_contract import ServiceError
from hub.local_service import HubHTTPServer
from hub.owner_auth import OwnerAuth
from hub.task_worker import TaskWorker
from hub.workbench_store import digest
import test_hub_task_runtime as runtime
import test_hub_local_service as http_fixture

class TaskReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture=runtime.TaskRuntimeTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.store=self.fixture.store;self.service=self.fixture.service
    def complete(self,owner='trusted_local_owner'):
        self.service.submit(self.fixture.payload,owner_context=owner);self.fixture.worker().run_one()
        task=self.service.get('fixture-task')['task'];self.assertEqual(task['status'],'checks_complete');return task
    def command(self,task,**changes):
        return {'request_id':'review-one','expected_result_version':task['result_version'],
                'expected_review_revision':0,'decision':'accept',**changes}
    def test_exact_receipt_restart_replay_and_revision_conflict(self):
        task=self.complete();before=self.store.task('fixture-task');cmd=self.command(task)
        self.service.test_gate=False
        with mock.patch.object(TaskWorker,'run_one',side_effect=AssertionError('No new execution')):
            accepted=self.service.review('fixture-task',cmd)
            self.assertFalse(accepted['replayed']);self.assertEqual(accepted['task']['human_acceptance'],'accepted')
            self.assertTrue(accepted['task']['lifecycle']['human_accepted']);self.assertFalse(accepted['task']['lifecycle']['owner_review_required'])
            self.assertTrue(self.service.review('fixture-task',cmd)['replayed'])
        reopened=TaskStore(self.fixture.control)
        state=reopened.review_state(reopened.task('fixture-task'),reopened.events('fixture-task'))
        self.assertEqual(state['decision'],'accept');self.assertEqual(state['revision'],1);self.assertEqual(state['git_delivery'],'not_recorded')
        self.assertEqual(before,self.store.task('fixture-task'))
        for changed in [self.command(task,decision='defer'),self.command(task,request_id='stale-review')]:
            with self.assertRaises(ServiceError):self.service.review('fixture-task',changed)
        changed=self.command(task,request_id='review-two',expected_review_revision=1,decision='request_changes')
        self.assertEqual(self.service.review('fixture-task',changed)['task']['human_acceptance'],'changes_requested')
        self.assertEqual(self.service.get('fixture-task')['task']['status'],'checks_complete')
    def test_owner_hash_version_checks_and_lease_status_are_separate(self):
        task=self.complete();cmd=self.command(task);events=self.store.events('fixture-task')
        for key,value in [('expected_result_version','0'*64),('expected_review_revision',True),('decision',[]),('shell','anything')]:
            bad={**cmd,key:value}
            with self.subTest(key=key),self.assertRaises(ServiceError):self.service.review('fixture-task',bad)
        with self.assertRaises(ServiceError):self.service.review('fixture-task',cmd,owner_context='other-owner')
        def denied():raise ServiceError('OWNER_AUTH_FAILED',status=401)
        with self.assertRaises(ServiceError):self.service.review('fixture-task',cmd,authorize=denied)
        self.assertEqual(self.store.events('fixture-task'),events)
        changed=copy.deepcopy(self.store.task('fixture-task'));changed['turn_id']='another-turn'
        self.assertNotEqual(self.store.result_version(changed),task['result_version'])
        self.assertEqual(self.store.review_state(changed,events)['decision'],'pending')
    def test_provider_completion_or_message_alone_never_becomes_reviewable(self):
        self.service.submit(self.fixture.payload);claimed=self.store.claim('review-worker')
        self.store.update(claimed['id'],'review-worker',thread_id='fixture-thread',turn_id='fixture-turn',provider_status='completed',result={'provider_reply':'done','checks':[{'exit':0}]})
        task=self.service.get('fixture-task')['task']
        with self.assertRaises(ServiceError):self.service.review('fixture-task',self.command(task))
        self.assertEqual(task['human_acceptance'],'pending')
    def test_real_http_owner_csrf_and_result_receipt_with_execution_disabled(self):
        helper=http_fixture.LocalHTTPTests();helper.projects=self.fixture.helper.projects;helper.designs=self.fixture.helper.fixture.service
        helper.owner_proof='fixture-only-public-test-vector-00000000000000000000'
        auth=OwnerAuth(provider=lambda:helper.owner_proof)
        owner=hashlib.sha256(b'hub-task-owner-v1'+auth.version).hexdigest();task=self.complete(owner);self.service.test_gate=False
        helper.server=HubHTTPServer(helper.projects,helper.designs,previews=self.fixture.helper.service,tasks=self.service,owner_auth=auth)
        helper.thread=threading.Thread(target=helper.server.serve_forever,kwargs={'poll_interval':.01});helper.thread.start();helper.cookie=helper.csrf=None;self.addCleanup(helper.stop)
        path='/api/tasks/fixture-task/review';cmd=self.command(task)
        self.assertEqual(helper.call('POST',path,cmd)[0],401);helper.session()
        self.assertEqual(helper.call('POST',path,cmd,headers={'X-Hub-CSRF':'wrong'})[0],403)
        self.assertEqual(helper.call('POST',path,cmd,headers={'Origin':'https://evil.example'})[0],403)
        status,_,body=helper.call('POST',path,cmd);self.assertEqual(status,200);self.assertEqual(body['data']['task']['human_acceptance'],'accepted')
        self.assertEqual(helper.call('POST',path,cmd)[2]['data']['replayed'],True)
        self.assertFalse(body['data']['task']['result_review']['git_delivery']=='delivered')
        self.assertEqual(len(self.store.tasks()),1);self.assertEqual(runtime.FakeAdapter.effects,1)

if __name__=='__main__':unittest.main()
