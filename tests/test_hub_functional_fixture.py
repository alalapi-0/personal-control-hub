"""Real transport verification of the complete disposable product assembly."""
import hashlib
import json
import unittest
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

from pch_functional_fixture import FunctionalFixture, BEFORE, AFTER
from hub.connection_sources import SourceResolver
from hub.local_service import WEB_ASSETS


class FunctionalFixtureTests(unittest.TestCase):
    def test_idle_runtime_restart_requires_new_explicit_owned_handle(self):
        with FunctionalFixture() as f:
            f.ordinary_sample()
            f.start_ordinary_runtime()
            old = f.ordinary_runtime
            self.assertTrue(old.status()['worker_runtime']['available'])
            f.restart_server()
            self.assertIsNone(f.ordinary_runtime)
            self.assertFalse(old.status()['worker_runtime']['available'])
            self.assertFalse(f.tasks.capabilities()['ordinary_execution']['available'])
            f.start_ordinary_runtime()
            self.assertIsNot(f.ordinary_runtime, old)
            self.assertTrue(f.tasks.capabilities()['ordinary_execution']['available'])
            self.assertEqual([probe for probe in f.probes if probe[0] == 'worker-root'], [])

    def test_complete_http_guest_owner_refresh_sources_static_and_shutdown(self):
        with FunctionalFixture() as f:
            root = f.root
            self.assertEqual(f.call(path='/api/health')[0], 200)
            for path in WEB_ASSETS:
                self.assertEqual(f.call(path=path)[0], 200, path)
            self.assertEqual(f.call(path='/api/tasks')[0], 401)
            f.handshake(owner=False)
            self.assertEqual(f.call(path='/api/sessions')[0], 401)
            f.handshake()
            for path in ('/api/projects', '/api/designs', '/api/previews', '/api/task-drafts', '/api/tasks', '/api/sessions', '/api/host', '/api/sync-status', '/api/management?project_id=fixture-project-a'):
                self.assertEqual(f.call(path=path)[0], 200, path)
            projects = f.call()[2]['data']
            self.assertEqual(projects['total'], 2)
            source_hashes = {hashlib.sha256((root / 'STATE.yaml').read_bytes()).hexdigest() for root in f.roots.values()}
            self.assertEqual(len(source_hashes), 2)
            self.assertNotEqual(f.grants['fixture-project-a']['annotation_hash'], f.grants['fixture-project-b']['annotation_hash'])
            command = {'request_id': 'fixture-refresh', 'project_ids': list(f.roots), 'expected_head': f.projects.ledger_head()}
            self.assertEqual(f.call('POST', '/api/refresh', command)[0], 200)
            with mock.patch.object(SourceResolver, 'refresh', side_effect=AssertionError('unexpected source refresh')):
                self.assertEqual(f.call()[0], 200)
                self.assertEqual(f.call('POST', '/api/refresh', command)[0], 200)
            self.assertEqual(f.call('POST', '/api/tasks', f.payloads['fixture-project-a'], {'X-Hub-CSRF': 'wrong'})[0], 403)
            self.assertEqual(f.call(path='/api/sessions')[2]['data']['classification'], 'mock')
        self.assertTrue(f.closed)
        self.assertFalse(root.exists())
        self.assertFalse(f.thread.is_alive())

    def test_cli_in_memory_handshake_and_bounded_stop(self):
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('pch_functional_fixture.py')), '--serve'],
                                input='{"action":"health"}\n{"action":"handshake"}\n{"action":"stop"}\n',
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertTrue(rows[0]['ready'])
        self.assertEqual(rows[1], {'status': 200})
        self.assertEqual(rows[2], {'classification': 'synthetic_fixture', 'owner_authenticated': True})
        self.assertEqual(rows[3], {'stopped': True})
        for private in ('token', 'cookie', 'csrf'):
            self.assertNotIn(private, result.stdout.lower())

    def test_dual_project_exact_binding_worker_and_duplicate_dispatch(self):
        with FunctionalFixture() as f:
            f.handshake()
            for project, payload in f.payloads.items():
                wrong = {**payload, 'project_id': 'fixture-project-b' if project.endswith('a') else 'fixture-project-a'}
                self.assertGreaterEqual(f.call('POST', '/api/tasks', wrong)[0], 400)
                self.assertEqual(f.effects, [])
            for project, payload in f.payloads.items():
                self.assertEqual(f.call('POST', '/api/tasks', payload)[0], 200)
                self.assertTrue(f.call('POST', '/api/tasks', payload)[2]['data']['replayed'])
                self.assertTrue(f.run_worker(project))
                task = f.task_store.task(payload['request_id'])
                self.assertEqual(task['status'], 'checks_complete', task['result'])
                self.assertEqual(task['human_acceptance'], 'pending')
                self.assertEqual(task['result']['changed_paths'], ['index.html'])
                self.assertEqual((f.roots[project] / 'index.html').read_text(), AFTER)
                self.assertFalse(f.run_worker(project))
            self.assertEqual(len(f.effects), 2)
            self.assertEqual(set(f.effects), set(f.roots.values()))
            public = json.dumps(f.call(path='/api/tasks')[2])
            self.assertNotIn(f.owner, public)
            self.assertNotIn(str(f.root), public)

    def test_delayed_worker_cancel_is_owned_and_joined(self):
        with FunctionalFixture() as f:
            project = 'fixture-project-a'
            f.scenarios[project] = 'delay'
            f.tasks.submit(f.payloads[project])
            worker = f.run_worker(project, background=True)
            deadline = time.monotonic() + 2
            while not f.effects and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertEqual(f.effects, [f.roots[project]])
            f.tasks.cancel(f.payloads[project]['request_id'])
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(f.task_store.task(f.payloads[project]['request_id'])['status'], 'cancelled')
            self.assertEqual(f.interrupts, [('thread-' + project, 'turn-' + project)])

    def test_fake_failures_preserve_unknown_and_failed_status(self):
        for scenario, expected in (('disconnect', 'requires_reconcile'), ('lost_receipt', 'requires_reconcile'), ('failure', 'failed')):
            with self.subTest(scenario=scenario), FunctionalFixture() as f:
                project = 'fixture-project-a'
                f.scenarios[project] = scenario
                f.tasks.submit(f.payloads[project])
                f.run_worker(project)
                self.assertEqual(f.task_store.task(f.payloads[project]['request_id'])['status'], expected)
                self.assertEqual((f.roots['fixture-project-b'] / 'index.html').read_text(), BEFORE)


if __name__ == '__main__': unittest.main()
