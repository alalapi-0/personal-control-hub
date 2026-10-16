"""Runtime assembly tests use existing registered SQLite and synthetic providers."""
import hashlib
from pathlib import Path
import sys
import threading
import yaml
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import test_hub_ordinary_worker as fixtures
from hub.ordinary_runtime import OrdinaryRuntime
from hub.ordinary_tasks import prompt
from hub.service_contract import ServiceError
from hub.task_store import TaskStore
from hub.workbench_store import digest


class OrdinaryRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.OrdinaryWorkerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store, self.grant = self.fixture.store, self.fixture.grant
        class Adapter(self.fixture.Adapter):
            created = []
            interrupts = []
            hold = False
            closed = []
            def start(self, thread, text, image):
                self.events = [self.event('turn/started', turn={'id':'ordinary-turn', 'status':'inProgress'}),
                    self.event('item/completed', item={'id':'reply','type':'agentMessage','text':'Synthetic reply'}),
                    self.event('turn/completed', turn={'id':'ordinary-turn','status':'completed'})]
                return 'ordinary-turn'
            def poll(self, timeout):
                if self.hold and self.events and self.events[0]['method'] == 'turn/started':
                    return self.events.pop(0)
                if self.hold:
                    time.sleep(.01)
                    return None
                return self.events.pop(0)
            def interrupt(self, thread, turn):
                self.hold = False
                super().interrupt(thread, turn)
            def verify_parent_binding(self, thread, turn):
                if (thread, turn) != ('ordinary-thread','ordinary-turn'):
                    raise ServiceError('CODEX_PARENT_BINDING_REJECTED')
            def close(self): self.closed.append(self)
        self.Adapter = Adapter
        self.runtime = self.make_runtime()
        self.addCleanup(self.runtime.stop)

    def make_runtime(self, **extra):
        return OrdinaryRuntime(self.store, grants=[self.grant], adapter_factory=self.Adapter,
            provider_status={'available':True,'classification':'synthetic_fixture'}, poll_interval=.01, **extra)

    def admit(self, ident, *, grant=None, kind='ordinary_project', parent=None, mode='new'):
        grant = grant or self.grant
        spec = {**self.fixture.spec, 'request_id':ident, 'project_id':grant['project_id'],
                'input_version':grant['input_version'], 'parent_task_id':parent, 'mode':mode}
        intent = {**self.fixture.intent, 'grant':grant, 'grant_hash':digest(grant), 'task_spec':spec,
                  'prompt_sha256':hashlib.sha256(prompt(spec,input_files=grant['input_files']).encode()).hexdigest()}
        if parent:
            intent.update(previous_thread='ordinary-thread',
                parent_binding={'thread_id':'ordinary-thread','turn_id':'ordinary-turn'})
        self.store.admit({**spec, 'kind':kind}, intent)

    def wait(self, condition):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if condition(): return
            time.sleep(.01)
        self.fail('synthetic runtime did not reach expected state')

    def completed(self, ident):
        return self.store.task(ident)['status'] == 'checks_complete'

    def parallel_runtime(self, count=2):
        from hub.ordinary_tasks import make_grant
        roots = [self.fixture.root]
        for index in range(1, count):
            root = Path(self.fixture.temp.name) / ('project-' + str(index))
            root.mkdir()
            (root / 'README.md').write_text('Synthetic independent input')
            roots.append(root)
            self.fixture.registry['projects'].append({'id': 'fixture-' + str(index),
                'name': 'Synthetic', 'enabled': True, 'connection_read_allowed': True,
                'root_path': str(root), 'current_state_paths': ['README.md']})
        self.fixture.registry_path.write_text(yaml.safe_dump(self.fixture.registry))
        registry_hash = hashlib.sha256(self.fixture.registry_path.read_bytes()).hexdigest()
        self.fixture.intent['registry_hash'] = registry_hash
        ids = [row['id'] for row in self.fixture.registry['projects']]
        (self.store.root / 'data/connections/sync_scope.yaml').write_text(yaml.safe_dump({
            'schema_version': '1.0', 'scope_id': 'ordinary-synthetic-scope',
            'baseline_total': count, 'project_ids': ids, 'excluded_project_ids': {}}))
        grants = [make_grant(root, ident, registry_hash, 'parallel-' + str(index),
            input_paths=['README.md']) for index, (root, ident) in enumerate(zip(roots, ids))]
        for grant in grants:
            self.store.register_grant(grant)
        self.grant = grants[0]
        runtime = OrdinaryRuntime(self.store, grants=grants, adapter_factory=self.Adapter,
            provider_status={'available': True, 'classification': 'synthetic_fixture'}, poll_interval=.01)
        self.addCleanup(runtime.stop)
        self.Adapter.hold = True
        return runtime, grants

    def both_started(self):
        return all(self.store.task(ident)['turn_id'] is not None for ident in ('a', 'b'))

    def test_two_roots_run_simultaneously_exact_cancel_keeps_b_running(self):
        runtime, grants = self.parallel_runtime()
        self.admit('a', grant=grants[0])
        self.admit('b', grant=grants[1])
        runtime.start()
        self.wait(self.both_started)
        self.assertEqual(len(runtime._handles), 2)
        self.wait(lambda: all(any(e['data'].get('method') == 'turn/started' for e in self.store.events(i)) for i in ('a', 'b')))
        self.store.cancel('a', worker_owner=self.store.task('a')['owner'])
        self.wait(lambda: self.store.task('a')['status'] == 'cancelled')
        self.assertEqual(self.store.task('b')['status'], 'running')
        self.assertFalse(self.store.task('b')['cancel'])
        self.assertEqual(len(self.Adapter.interrupts), 1)
        next(a for a in self.Adapter.created if a.root == Path(grants[1]['root'])).hold = False
        self.wait(lambda: self.completed('b'))
        self.assertTrue(runtime.status()['worker_runtime']['available'])

    def test_blocked_same_project_claim_yields_slot_to_independent_b(self):
        runtime, grants = self.parallel_runtime()
        self.admit('a', grant=grants[0])
        self.admit('a-waiting', grant=grants[0])
        self.admit('b', grant=grants[1])
        runtime.start()
        self.wait(self.both_started)
        self.assertEqual(self.store.task('a-waiting')['status'], 'queued')
        self.assertEqual(len(self.Adapter.created), 2)

    def test_exact_two_slot_bound_and_completion_releases_slot(self):
        runtime, grants = self.parallel_runtime(3)
        for ident, grant in zip(('a', 'b', 'c'), grants):
            self.admit(ident, grant=grant)
        runtime.start()
        self.wait(self.both_started)
        time.sleep(.06)
        self.assertEqual(len(self.Adapter.created), 2)
        self.assertEqual(self.store.task('c')['status'], 'queued')
        next(a for a in self.Adapter.created if a.root == Path(grants[0]['root'])).hold = False
        self.wait(lambda: self.store.task('c')['turn_id'] is not None)
        self.assertEqual(self.store.task('b')['status'], 'running')
        self.assertLessEqual(len(runtime._handles), 2)
        runtime.stop()
        self.assertTrue(self.store.task('b')['cancel'])
        self.assertTrue(self.store.task('c')['cancel'])
        self.assertEqual(len(self.Adapter.closed), 3)
        self.assertEqual(runtime._handles, {})

    def test_stop_joins_both_owned_workers(self):
        runtime, grants = self.parallel_runtime()
        for ident, grant in zip(('a', 'b'), grants):
            self.admit(ident, grant=grant)
        runtime.start()
        self.wait(self.both_started)
        handles = list(runtime._handles.values())
        runtime.stop(timeout=2)
        self.assertTrue(all(not h['thread'].is_alive() for h in handles))
        self.assertTrue(all(self.store.task(i)['cancel'] for i in ('a', 'b')))
        self.assertEqual(len(self.Adapter.closed), 2)
        self.assertEqual(runtime._handles, {})

    def test_active_a_grant_revocation_does_not_stop_b(self):
        runtime, grants = self.parallel_runtime()
        for ident, grant in zip(('a', 'b'), grants):
            self.admit(ident, grant=grant)
        runtime.start()
        self.wait(self.both_started)
        self.store.register_grant({**grants[0], 'version': 2})
        self.wait(lambda: self.store.task('a')['status'] == 'requires_reconcile')
        self.assertEqual(self.store.task('b')['status'], 'running')
        self.assertFalse(self.store.task('b')['cancel'])
        next(a for a in self.Adapter.created if a.root == Path(grants[1]['root'])).hold = False
        self.wait(lambda: self.completed('b'))
        self.assertTrue(runtime.status()['worker_runtime']['available'])

    def test_two_timed_out_handles_are_retained_for_exact_cleanup_retry(self):
        runtime, grants = self.parallel_runtime()
        release = threading.Event()
        original_poll = self.Adapter.poll
        def blocked(adapter, timeout):
            release.wait(2)
            return original_poll(adapter, timeout)
        for ident, grant in zip(('a', 'b'), grants):
            self.admit(ident, grant=grant)
        with mock.patch.object(self.Adapter, 'poll', blocked):
            runtime.start()
            self.wait(self.both_started)
            handles = dict(runtime._handles)
            try:
                with self.assertRaises(ServiceError) as error:
                    runtime.stop(timeout=.01)
                self.assertEqual(error.exception.code, 'ORDINARY_RUNTIME_STOP_TIMEOUT')
                self.assertEqual(runtime._handles, handles)
                self.assertTrue(all(h['thread'].is_alive() for h in handles.values()))
                self.assertTrue(all(self.store.task(i)['cancel'] for i in ('a', 'b')))
            finally:
                release.set()
                runtime.stop(timeout=3)
        self.assertTrue(all(not h['thread'].is_alive() for h in handles.values()))
        self.assertEqual(len(self.Adapter.closed), 2)
        self.assertEqual(runtime._handles, {})

    def test_cancel_error_still_cleans_b_and_retains_a_for_retry(self):
        runtime, grants = self.parallel_runtime()
        for ident, grant in zip(('a', 'b'), grants):
            self.admit(ident, grant=grant)
        runtime.start()
        self.wait(self.both_started)
        cancel = self.store.cancel
        attempted = []
        def reject_a(ident, **kwargs):
            attempted.append(ident)
            if ident == 'a':
                raise ServiceError('SYNTHETIC_CANCEL_FAILURE')
            return cancel(ident, **kwargs)
        with mock.patch.object(self.store, 'cancel', reject_a):
            with self.assertRaises(ServiceError) as error:
                runtime.stop(timeout=.05)
            self.assertEqual(error.exception.code, 'SYNTHETIC_CANCEL_FAILURE')
            self.assertIn('b', attempted)
            self.assertTrue(self.store.task('b')['cancel'])
            self.assertIn('a', runtime._handles)
            self.assertTrue(runtime._handles['a']['thread'].is_alive())
        runtime.stop(timeout=2)
        self.assertEqual(runtime._handles, {})
        self.assertEqual(len(self.Adapter.closed), 2)

    def test_lease_failure_closes_a_only_while_b_completes(self):
        runtime, grants = self.parallel_runtime()
        for ident, grant in zip(('a', 'b'), grants):
            self.admit(ident, grant=grant)
        heartbeat = self.store.heartbeat
        def lose_a(ident, owner):
            if ident == 'a':
                raise ServiceError('TASK_LEASE_LOST')
            return heartbeat(ident, owner)
        with mock.patch.object(self.store, 'heartbeat', lose_a):
            runtime.start()
            self.wait(self.both_started)
            self.wait(lambda: self.store.task('a')['status'] == 'requires_reconcile')
        self.assertEqual(self.store.task('a')['result']['error_class'], 'TASK_LEASE_LOST')
        self.assertEqual(self.store.task('b')['status'], 'running')
        self.assertFalse(self.store.task('b')['cancel'])
        next(a for a in self.Adapter.created if a.root == Path(grants[1]['root'])).hold = False
        self.wait(lambda: self.completed('b'))
        self.assertTrue(runtime.status()['worker_runtime']['available'])

    def test_escaped_worker_exception_cleans_up_other_owned_worker(self):
        runtime, grants = self.parallel_runtime()
        for ident, grant in zip(('a', 'b'), grants):
            self.admit(ident, grant=grant)
        from hub.task_worker import TaskWorker
        original = TaskWorker.run_one
        release = threading.Event()
        def run(worker):
            if worker.only_task_id == 'a':
                release.wait(2)
                raise RuntimeError('synthetic escaped worker failure')
            return original(worker)
        with mock.patch.object(TaskWorker, 'run_one', run):
            runtime.start()
            self.wait(lambda: self.store.task('b')['turn_id'] is not None)
            release.set()
            self.wait(lambda: runtime.status()['worker_runtime']['status'] == 'failed')
            self.wait(lambda: not runtime._thread.is_alive())
        self.assertTrue(self.store.task('b')['cancel'])
        self.assertEqual(runtime._handles, {})
        self.assertEqual(len(self.Adapter.closed), 1)
        self.assertEqual(self.store.task('a')['status'], 'queued')

    def test_observers_and_constructor_never_open_adapter(self):
        self.admit('first')
        for _ in range(3):
            self.assertFalse(self.runtime.status()['worker_runtime']['available'])
            self.assertFalse(self.runtime.writer_check(self.grant))
        self.assertEqual(self.Adapter.created, [])
        self.assertEqual(self.store.task('first')['status'], 'queued')

    def test_start_runs_queue_and_later_tasks_with_exact_existing_claim(self):
        self.admit('first')
        self.runtime.start()
        self.wait(lambda:self.completed('first'))
        self.assertTrue(self.runtime.status()['worker_runtime']['available'])
        self.admit('later')
        self.wait(lambda:self.completed('later'))
        self.assertEqual(len(self.Adapter.created), 2)
        self.assertEqual(len(self.Adapter.closed), 2)
        event = next(e for e in self.store.events('first') if e['key'].startswith('dispatch-intent-'))
        self.assertTrue(event['data']['worker']['owner'].startswith('worker-'))
        self.assertTrue(event['data']['worker']['start_ticks'])

    def test_busy_feedback_waits_then_runs_without_extra_executor(self):
        self.Adapter.hold = True
        self.admit('parent')
        self.runtime.start()
        self.wait(lambda:self.store.task('parent')['turn_id'] is not None)
        self.admit('child', parent='parent', mode='busy_feedback')
        time.sleep(.05)
        self.assertEqual(len(self.Adapter.created), 1)
        self.Adapter.hold = False
        self.Adapter.created[0].hold = False
        self.wait(lambda:self.completed('child'))
        self.assertEqual(len(self.Adapter.created), 2)

    def test_unlisted_and_fixture_tasks_are_never_claimed(self):
        alternate = {**self.grant,'grant_id':'unlisted-grant'}
        self.store.register_grant(alternate)
        self.admit('unlisted', grant=alternate)
        self.admit('fixture-kind', kind='fixture')
        self.admit('owned')
        self.runtime.start()
        self.wait(lambda:self.completed('owned'))
        self.assertEqual(self.store.task('unlisted')['status'], 'queued')
        self.assertEqual(self.store.task('fixture-kind')['status'], 'queued')
        self.assertEqual(len(self.Adapter.created), 1)

    def test_stop_cancels_only_owned_active_claim_and_closes_adapter(self):
        self.Adapter.hold = True
        self.admit('owned')
        self.admit('other')
        self.runtime.start()
        self.wait(lambda:self.store.task('owned')['turn_id'] is not None)
        started = time.monotonic()
        self.runtime.stop(timeout=2)
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(self.store.task('owned')['cancel'])
        self.assertFalse(self.store.task('other')['cancel'])
        self.assertEqual(self.store.task('other')['status'], 'queued')
        self.assertEqual(len(self.Adapter.closed), 1)

    def test_revoked_active_grant_can_still_stop_its_exact_owned_claim(self):
        entered, release = threading.Event(), threading.Event()
        original_poll = self.Adapter.poll
        def blocked_poll(adapter, timeout):
            entered.set()
            release.wait(2)
            return original_poll(adapter, timeout)
        self.Adapter.hold = True
        self.admit('revoked-active')
        with mock.patch.object(self.Adapter, 'poll', blocked_poll):
            self.runtime.start()
            self.assertTrue(entered.wait(1))
            self.store.register_grant({**self.grant,'version':2})
            timer = threading.Timer(.05, release.set)
            timer.start()
            try:
                self.runtime.stop(timeout=2)
            finally:
                release.set()
                timer.join()
        self.assertTrue(self.store.task('revoked-active')['cancel'])
        self.assertEqual(len(self.Adapter.closed),1)
        self.assertFalse(self.runtime.status()['worker_runtime']['available'])

    def test_wrong_worker_cannot_cancel_another_active_claim(self):
        self.admit('foreign')
        self.store.claim('foreign-worker',only_task_id='foreign')
        with self.assertRaises(ServiceError) as error:
            self.store.cancel('foreign',worker_owner='different-worker')
        self.assertEqual(error.exception.code,'TASK_LEASE_LOST')
        self.assertFalse(self.store.task('foreign')['cancel'])
        self.assertEqual(self.store.task('foreign')['owner'],'foreign-worker')

    def test_stop_preserves_foreign_claim(self):
        self.admit('foreign')
        self.store.claim('foreign-worker', only_task_id='foreign')
        self.runtime.start()
        time.sleep(.04)
        self.runtime.stop()
        self.assertFalse(self.store.task('foreign')['cancel'])
        self.assertEqual(self.store.task('foreign')['owner'], 'foreign-worker')
        self.assertEqual(self.Adapter.created, [])

    def test_revoked_grant_remains_queued(self):
        self.runtime.start()
        self.store.register_grant({**self.grant, 'version':2})
        self.admit('revoked')
        time.sleep(.06)
        self.assertFalse(self.runtime.writer_check(self.grant))
        self.assertEqual(self.store.task('revoked')['status'], 'queued')
        self.assertEqual(self.Adapter.created, [])

    def test_requires_reconcile_is_never_restarted(self):
        self.admit('unknown')
        task = self.store.claim('previous-worker', only_task_id='unknown')
        self.store.update(task['id'], 'previous-worker', status='requires_reconcile')
        self.runtime.start()
        time.sleep(.05)
        self.runtime.stop()
        self.assertEqual(self.store.task('unknown')['status'], 'requires_reconcile')
        self.assertEqual(self.Adapter.created, [])

    def test_unavailable_provider_refuses_start_without_creating_adapter(self):
        runtime = OrdinaryRuntime(self.store, grants=[self.grant], adapter_factory=self.Adapter,
            provider_status={'available':False,'classification':'unknown'})
        with self.assertRaises(ServiceError): runtime.start()
        self.assertFalse(runtime.status()['worker_runtime']['available'])
        self.assertFalse(runtime.status()['provider']['available'])
        self.assertEqual(self.Adapter.created, [])

    def test_missing_store_is_never_initialized(self):
        root = Path(self.fixture.temp.name) / 'missing'
        runtime = OrdinaryRuntime(TaskStore(root), grants=[self.grant], adapter_factory=self.Adapter,
            provider_status={'available':True,'classification':'synthetic_fixture'})
        runtime.status()
        with self.assertRaises(ServiceError): runtime.start()
        self.assertFalse(root.exists())
        self.assertEqual(self.Adapter.created, [])

    def test_initial_grant_must_exactly_match_server_registration(self):
        for change in ({'version':2}, {'root_identity':'0'*64}):
            runtime = OrdinaryRuntime(self.store, grants=[{**self.grant, **change}],
                adapter_factory=self.Adapter, provider_status={'available':True,'classification':'synthetic_fixture'})
            with self.assertRaises(ServiceError): runtime.start()
        self.assertEqual(self.Adapter.created, [])

    def test_revoked_group_and_unknown_task_do_not_block_independent_registered_root(self):
        # Both roots are synthetic and explicitly enumerated; classification
        # alone never confers authority on the second root.
        from hub.ordinary_tasks import make_grant
        root_b = Path(self.fixture.temp.name) / 'project-b'
        root_b.mkdir()
        (root_b / 'README.md').write_text('Second synthetic root')
        registry = self.fixture.registry
        registry['projects'].append({'id':'fixture-b','name':'B','enabled':True,
            'connection_read_allowed':True,'root_path':str(root_b),'current_state_paths':['README.md']})
        self.fixture.registry_path.write_text(yaml.safe_dump(registry))
        registry_hash = hashlib.sha256(self.fixture.registry_path.read_bytes()).hexdigest()
        self.fixture.intent['registry_hash'] = registry_hash
        scope = self.store.root / 'data/connections/sync_scope.yaml'
        scope.write_text(yaml.safe_dump({'schema_version':'1.0','scope_id':'ordinary-synthetic-scope',
            'baseline_total':2,'project_ids':['ordinary-fixture','fixture-b'],'excluded_project_ids':{}}))
        self.grant = make_grant(self.fixture.root,'ordinary-fixture',registry_hash,'ordinary-grant',input_paths=['README.md'],version=2)
        grant_b = make_grant(root_b,'fixture-b',registry_hash,'grant-b',input_paths=['README.md'])
        self.store.register_grant(self.grant)
        self.store.register_grant(grant_b)
        runtime = OrdinaryRuntime(self.store, grants=[self.grant, grant_b], adapter_factory=self.Adapter,
            provider_status={'available':True,'classification':'synthetic_fixture'}, poll_interval=.01)
        self.addCleanup(runtime.stop)
        self.admit('unknown-a')
        self.store.claim('previous-owner', only_task_id='unknown-a')
        self.store.update('unknown-a','previous-owner',status='requires_reconcile')
        runtime.start()
        self.store.register_grant({**self.grant,'version':3})
        self.admit('revoked-a')
        self.admit('independent-b',grant=grant_b)
        self.wait(lambda:self.completed('independent-b'))
        self.assertEqual(self.store.task('revoked-a')['status'],'queued')
        self.assertEqual(self.store.task('unknown-a')['status'],'requires_reconcile')
        self.assertEqual(len(self.Adapter.created),1)
        self.assertTrue(runtime.status()['worker_runtime']['available'])

    def test_stop_timeout_reports_failure_and_retains_exact_thread_handle(self):
        entered, release = threading.Event(), threading.Event()
        def blocked_worker(worker):
            entered.set()
            release.wait(2)
        self.admit('blocked')
        with mock.patch('hub.ordinary_runtime.TaskWorker.run_one', blocked_worker):
            self.runtime.start()
            self.assertTrue(entered.wait(1))
            try:
                with self.assertRaises(ServiceError) as error:
                    self.runtime.stop(timeout=.01)
                self.assertEqual(error.exception.code,'ORDINARY_RUNTIME_STOP_TIMEOUT')
                self.assertEqual(self.runtime.status()['worker_runtime']['status'],'failed')
                self.assertFalse(self.store.task('blocked')['cancel'])
                self.assertEqual(self.Adapter.created,[])
            finally:
                release.set()
                self.runtime.stop()

    def test_runtime_exception_is_reported_without_retry(self):
        with mock.patch.object(self.store, 'tasks', side_effect=RuntimeError('synthetic failure')):
            self.runtime.start()
            self.wait(lambda:self.runtime.status()['worker_runtime']['status'] == 'failed')
        with self.assertRaises(ServiceError): self.runtime.start()
        self.assertEqual(self.runtime.status()['worker_runtime']['reason_code'], 'ORDINARY_RUNTIME_FAILED')
        self.assertEqual(self.Adapter.created, [])


if __name__ == '__main__': unittest.main()
