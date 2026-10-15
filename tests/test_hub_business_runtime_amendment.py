"""Synthetic owner runtime amendment; never dispatches a model or child command."""
import copy
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import test_hub_story_exchange as fixture
from hub.service_contract import ServiceError
from hub.story_promotion import WORKSPACE, OBSERVATION, source_snapshot
from hub.task_store import TaskStore
from hub.task_service import validate_grant
from hub.story_exchange import validate as validate_story
import test_hub_task_grants as generic_fixture
from hub.workbench_store import digest


class BusinessRuntimeAmendmentTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.StoryExchangeTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.old_grant=copy.deepcopy(self.f.grant)
        for name,value in ((WORKSPACE,{'revision':22,'baseline_id':'baseline','dataset':'live'}),
                           (OBSERVATION,{'phase':'C01'})):
            path=self.f.source/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(value))
        self.old_grant['story_exchange']['source_preconditions']=source_snapshot(self.old_grant,expected=False)
        hub=self.f.source.parent/'control';hub.mkdir()
        self.store=TaskStore(hub);self.store.register_storage();self.store.register_grant(self.old_grant)
        payload={'kind':'story_exchange','request_id':'task-1','project_id':'story-faceless-utopia',
                 'grant_id':self.old_grant['grant_id'],'grant_version':1,'parent_task_id':None,'mode':'new',
                 'specification_sha256':self.old_grant['story_exchange']['specification_sha256']}
        intent={'grant':copy.deepcopy(self.old_grant),'grant_hash':digest(self.old_grant),
                'registry_hash':self.old_grant['story_exchange']['registry_hash'],
                'task_spec':copy.deepcopy(self.old_grant['task_spec']),
                'source_binding':copy.deepcopy(self.old_grant['story_exchange']['source_preconditions']),
                'previous_thread':None,'checks':['story_exchange']}
        self.store.admit(payload,intent);self.store.claim('closed-worker')
        self.store.update('task-1','closed-worker',status='requires_reconcile',thread_id='same-thread',
                          turn_id='interrupted-turn',provider_status='inProgress',
                          result={'code':'TASK_DEADLINE','outcome':'UNKNOWN'})
        self.original=self.store.task('task-1')
        self.authority={'contract':'PCH-STORY-RUNTIME-600-OWNER-v1','actor':'trusted_local_owner',
                        'owner_decision_sha256':digest('允许全部改为10 分钟上限'),
                        'task_hash':digest(self.original),'grant_hash':self.original['intent']['grant_hash'],
                        'thread_id':'same-thread','turn_id':'interrupted-turn','max_turn_seconds':600,
                        'additional_turn_limit':1}
        self.observation={'source':'app_server_stdio.thread/read','root':str(self.f.root),
                          'thread_id':'same-thread','turn_id':'interrupted-turn','thread_status':'notLoaded',
                          'turn_status':'interrupted','observed_at':time.time()}
        self.grant=copy.deepcopy(self.old_grant)
        self.grant.update(version=2,expires_at=time.time()+2000,max_turn_seconds=600)
        self.grant['story_exchange']['registration_sha256']='9'*64

    def amend(self):
        return self.store.authorize_business_runtime_amendment('task-1',authority=self.authority,
                                                               observation=self.observation,grant=self.grant)

    def assert_unchanged(self):
        self.assertEqual(self.store.task('task-1'),self.original)
        self.assertEqual(self.store.grant(self.old_grant['grant_id']),self.old_grant)
        self.assertNotIn('business-runtime-amendment-v1',[e['key']for e in self.store.events('task-1')])

    def test_owner_ceiling_accepts_600_preserves_old_limits_and_rejects_invalid(self):
        generic=generic_fixture.TaskGrantCommandTests();generic.setUp()
        for seconds in (20,120,600):
            for original,validator in ((self.old_grant,validate_story),(generic.grant,validate_grant)):
                grant=copy.deepcopy(original);grant['max_turn_seconds']=seconds
                with self.subTest(seconds=seconds,kind=grant.get('kind')):
                    self.assertEqual(validator(grant,live=False)['max_turn_seconds'],seconds)
        for seconds in (601,0,True,False):
            for original,validator in ((self.old_grant,validate_story),(generic.grant,validate_grant)):
                grant=copy.deepcopy(original);grant['max_turn_seconds']=seconds
                with self.subTest(seconds=seconds,kind=grant.get('kind')),self.assertRaises(ServiceError):
                    validator(grant,live=False)
        self.assertEqual(self.old_grant['max_turn_seconds'],120)
        self.assertEqual(generic.grant['max_turn_seconds'],20)

    def test_exact_once_amendment_preserves_failure_and_same_identity(self):
        self.amend();task=self.store.task('task-1')
        self.assertEqual(task['id'],self.original['id']);self.assertEqual(task['thread_id'],'same-thread')
        self.assertEqual(task['status'],'queued');self.assertIsNone(task['turn_id'])
        self.assertIsNone(task['provider_status']);self.assertIsNone(task['owner'])
        self.assertIsNone(task['result']);self.assertFalse(task['cancel'])
        self.assertEqual(task['payload']['grant_version'],2)
        self.assertEqual(task['intent']['grant'],self.grant)
        self.assertEqual(task['intent']['grant_hash'],digest(self.grant))
        self.assertEqual(self.store.grant(self.grant['grant_id']),self.grant)
        event=next(e['data']for e in self.store.events('task-1')if e['key']=='business-runtime-amendment-v1')
        self.assertEqual(event['original_thread_id'],'same-thread')
        self.assertEqual(event['original_turn_id'],'interrupted-turn')
        self.assertEqual(event['action'],'same_owned_thread_new_turn_after_verified_interruption')
        self.assertEqual(event['original_payload'],self.original['payload'])
        self.assertEqual(event['original_intent'],self.original['intent'])
        self.assertEqual(event['original_result'],self.original['result'])
        with self.assertRaises(ServiceError):self.amend()

    def test_fresh_observation_state_hash_change_is_allowed_without_workspace_change(self):
        (self.f.source/OBSERVATION).write_text('{"phase":"C01","native_observed":"interrupted"}')
        fresh=source_snapshot(self.grant,expected=False)
        self.assertEqual(fresh['workspace_sha256'],self.grant['story_exchange']['source_preconditions']['workspace_sha256'])
        self.grant['story_exchange']['source_preconditions']['source_state_sha256']=fresh['source_state_sha256']
        self.amend()
        self.assertEqual(self.store.task('task-1')['intent']['source_binding'],fresh)

    def test_failed_transaction_does_not_partially_update_event_task_or_grant(self):
        append=self.store._append_event
        def fail_after_append(*args,**kwargs):
            append(*args,**kwargs)
            raise RuntimeError('synthetic transaction failure')
        with mock.patch.object(self.store,'_append_event',side_effect=fail_after_append),self.assertRaises(RuntimeError):
            self.amend()
        self.assert_unchanged()

    def test_authority_and_native_observation_mismatch_are_atomic_rejections(self):
        for field in self.authority:
            old=self.authority[field];self.authority[field]='wrong'
            with self.subTest(authority_field=field),self.assertRaises(ServiceError):self.amend()
            self.authority[field]=old;self.assert_unchanged()
        cases={'source':'thread/list','root':'/other','thread_id':'other','turn_id':'other',
               'thread_status':'active','turn_status':'completed','observed_at':time.time()-31}
        for field,value in cases.items():
            old=self.observation[field];self.observation[field]=value
            with self.subTest(observation_field=field),self.assertRaises(ServiceError):self.amend()
            self.observation[field]=old;self.assert_unchanged()

    def test_grant_cannot_broaden_scope_inputs_or_workspace_contract(self):
        mutations=[lambda g:g.update(version=3),lambda g:g.update(max_turn_seconds=601),
                   lambda g:g.update(root=str(self.f.source)),lambda g:g.update(writable_scope=['.']),
                   lambda g:g['task_spec'].update(scope='extra writing'),
                   lambda g:g['story_exchange'].update(request_sha256='8'*64),
                   lambda g:g['story_exchange']['input_files'][0].update(sha256='7'*64),
                   lambda g:g['story_exchange']['source_preconditions'].update(workspace_revision=23),
                   lambda g:g['story_exchange']['source_preconditions'].update(workspace_sha256='6'*64),
                   lambda g:g['story_exchange']['source_preconditions'].update(baseline_id='other'),
                   lambda g:g['story_exchange']['source_preconditions'].update(dataset='fixture')]
        for mutate in mutations:
            before=copy.deepcopy(self.grant);mutate(self.grant)
            with self.subTest(grant=self.grant),self.assertRaises(ServiceError):self.amend()
            self.grant=before;self.assert_unchanged()

    def test_any_result_file_or_source_workspace_drift_blocks_amendment(self):
        output=self.f.root/'output/result.json';output.write_text('{}')
        with self.assertRaises(ServiceError):self.amend()
        self.assert_unchanged();output.unlink()
        source=self.f.source/WORKSPACE;source.write_text('{"revision":999}')
        with self.assertRaises((ServiceError,KeyError)):self.amend()
        self.assert_unchanged()

    def test_cancelled_task_or_live_owned_process_cannot_be_amended(self):
        with self.store._connection(True)as db:db.execute('UPDATE tasks SET cancel=1')
        self.original=self.store.task('task-1');self.authority['task_hash']=digest(self.original)
        with self.assertRaises(ServiceError):self.amend()
        self.assert_unchanged()
        with self.store._connection(True)as db:db.execute('UPDATE tasks SET cancel=0')
        self.original=self.store.task('task-1');self.authority['task_hash']=digest(self.original)
        ticks=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]
        self.store.event('task-1','dispatch-intent-closed-worker',{'worker':{'pid':os.getpid(),'start_ticks':ticks}})
        with self.assertRaises(ServiceError):self.amend()
        self.assert_unchanged()


if __name__=='__main__':unittest.main()
