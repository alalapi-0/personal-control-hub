import copy
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import test_hub_task_runtime as fixture
from hub.writer_handoff import WriterHandoffCheck,snapshot_scope
from hub.workbench_store import digest
from hub.service_contract import ServiceError


class HandoffDispatchTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.TaskRuntimeTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.calls=0;self.from_owner='previous-owner';self.actual_authority_owner='previous-owner'

    def evidence(self,grant):
        # These synthetic native facts verify routing, not live native ownership.
        self.calls+=1;now=time.time()
        observation={'thread_id':'mock-previous-thread','cwd':grant['root'],'source':'appServer','provider':'openai',
                     'status':'interrupted','observed_at':now}
        authority={'source':'root_backend','decision_id':'mock-explicit-release','release_source':'native_turn_terminal',
                   'released':True,'from_owner':self.actual_authority_owner,'thread_id':observation['thread_id'],'observed_at':now}
        proof={'grant_hash':digest(grant),'registry_hash':'a'*64,'task_id':grant['task_id'],'from_owner':self.from_owner,
               'to_owner':'hub-worker','observation_hash':digest(observation),'authority_hash':digest(authority),
               'scope':snapshot_scope(grant['root'],['index.html','AGENTS.md','fixture.png']),
               'issued_at':now,'expires_at':now+60}
        return {'proof':proof,'registry_hash':'a'*64,'observation':observation,'authority':authority,
                'from_owner':self.from_owner,'to_owner':'hub-worker','task_id':grant['task_id']}

    def test_existing_service_and_worker_resolve_handoff_at_both_boundaries(self):
        check=WriterHandoffCheck(self.evidence);self.f.service.writer_check=check
        self.f.service.submit(self.f.payload)
        worker=fixture.TaskWorker(self.f.store,adapter_factory=fixture.FakeAdapter,writer_check=check)
        self.assertTrue(worker.run_one());self.assertGreaterEqual(self.calls,2)
        self.assertEqual(self.f.store.task('fixture-task')['status'],'checks_complete')

    def test_wrong_owner_at_dispatch_prevents_provider_and_stops_no_other_task(self):
        check=WriterHandoffCheck(self.evidence);self.f.service.writer_check=check
        self.f.service.submit(self.f.payload);self.actual_authority_owner='different-owner'
        worker=fixture.TaskWorker(self.f.store,adapter_factory=fixture.FakeAdapter,writer_check=check)
        self.assertTrue(worker.run_one())
        self.assertEqual(fixture.FakeAdapter.opened,0);self.assertEqual(fixture.FakeAdapter.interrupt_count,0)
        self.assertEqual(self.f.store.task('fixture-task')['status'],'failed')
