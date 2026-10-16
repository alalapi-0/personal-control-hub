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
    def test_stress_opt_in_collects_long_names_roles_proposals_and_honest_unknowns(self):
        with FunctionalFixture() as f:
            f.handshake()
            initial = f.call()[2]['data']['projects']
            self.assertEqual([project['name'] for project in initial], ['Synthetic Project A', 'Synthetic Project B'])
            original_roots = dict(f.roots)
            registry = f.projects._resolver().projects
            qualification = {project: {key: value for key, value in row.items() if key not in {'name', 'description'}}
                             for project, row in registry.items()}
            metadata = f.stress_sample()
            self.assertEqual(f.stress_sample(), metadata)
            self.assertEqual(metadata['classification'], 'synthetic_fixture')
            self.assertEqual(len(metadata['proposal_ids']), 3)
            self.assertEqual(f.roots, original_roots)
            registry_after = f.projects._resolver().projects
            self.assertEqual(qualification, {project: {key: value for key, value in row.items() if key not in {'name', 'description'}}
                                             for project, row in registry_after.items()})
            public_directory = f.call()[2]['data']['projects']
            self.assertEqual({p['name'] for p in public_directory}, {p['name'] for p in metadata['projects']})
            for row in metadata['projects']:
                project = f.call(path='/api/projects/' + row['project_id'])[2]['data']
                self.assertEqual(project['name'], row['name'])
                self.assertIn('LONG_DISPLAY_', project['name'])
                self.assertEqual(project['freshness']['state'], 'fresh')
                definition = project['definition']['fields']
                self.assertEqual(definition['roles']['state'], 'known')
                self.assertEqual(len(definition['roles']['value']), 3)
                self.assertEqual(definition['roles']['source']['kind'], 'source_extension')
                self.assertEqual(definition['outputs']['state'], 'unknown')
                self.assertIsNone(definition['outputs']['value'])
                self.assertEqual(len(project['business']['unknown_fields']), row['unknown_business_fields'])
                self.assertEqual(project['business']['normalized_status'], 'unknown')
                self.assertEqual({p['kind'] for p in project['relations']['relations']},
                                 {'pipeline', 'shared_review_pattern', 'shared_visual_language'})
                self.assertTrue(all(p['status'] == 'proposed' for p in project['relations']['relations']))
                self.assertFalse(project['relations']['program_link_authority'])
                self.assertFalse(project['relations']['design_selection_authority'])
                self.assertNotEqual(project['management']['relation_view']['state'], 'owner_confirmed')
            self.assertEqual(f.effects, [])
            self.assertEqual(f.task_store.tasks(), [])

    def test_stress_readonly_responses_follow_http_worker_and_exact_project_lineage(self):
        with FunctionalFixture() as f:
            metadata = f.stress_sample()
            f.handshake();f.ordinary_sample()
            before = {project: ((root / 'STATE.yaml').read_bytes(), (root / 'index.html').read_bytes())
                      for project, root in f.roots.items()}
            replies = []
            for row in metadata['projects']:
                project = row['project_id']
                payload = {'kind': 'ordinary_project', 'request_id': project + '-stress-reply',
                    'project_id': project, 'objective': 'Synthetic long read-only response review',
                    'scope': 'Only registered synthetic inputs', 'effect': 'read_only',
                    'input_version': f.ordinary_grants[project]['input_version'], 'mode': 'new', 'parent_task_id': None}
                status, _, accepted = f.call('POST', '/api/tasks', payload)
                self.assertEqual(status, 200, accepted)
                self.assertTrue(f.run_worker(project, task_id=payload['request_id']))
                status, _, public = f.call(path='/api/tasks/' + payload['request_id'])
                self.assertEqual(status, 200)
                task = public['data']['task']
                self.assertEqual(task['status'], 'checks_complete', task['result'])
                self.assertEqual(task['project'], project)
                self.assertEqual(task['task_spec'], payload)
                self.assertTrue(task['thread_id'].startswith('thread-' + project))
                self.assertTrue(task['turn_id'].startswith('turn-' + project))
                reply = task['result']['response']
                self.assertEqual(reply, f.stress_replies[project])
                self.assertEqual(len(reply.encode()), row['reply_bytes'])
                self.assertEqual(hashlib.sha256(reply.encode()).hexdigest(), row['reply_sha256'])
                self.assertIn(row['reply_marker'], reply)
                self.assertIn(row['reply_unbroken_marker'] + project[-1].upper() * 2048, reply)
                self.assertFalse(task['result']['response_truncated'])
                self.assertGreaterEqual(row['reply_bytes'], 12 * 1024)
                self.assertLessEqual(row['reply_bytes'], 24 * 1024)
                self.assertEqual(f.task_store.result_version(f.task_store.task(task['id'])), task['result_version'])
                self.assertNotIn('output_refs', task['result'])
                replies.append(reply)
            self.assertNotEqual(*replies)
            self.assertEqual(before, {project: ((root / 'STATE.yaml').read_bytes(), (root / 'index.html').read_bytes())
                                     for project, root in f.roots.items()})
            self.assertEqual(f.effects, [])
            self.assertTrue(all(adapter.closed for adapter in f.ordinary_adapters))

    def test_stress_sample_rejects_after_grants_or_tasks_and_defaults_keep_short_replies(self):
        with FunctionalFixture() as f:
            f.ordinary_sample()
            before = {project: (root / 'STATE.yaml').read_bytes() for project, root in f.roots.items()}
            with self.assertRaises(ValueError):
                f.stress_sample()
            self.assertEqual(before, {project: (root / 'STATE.yaml').read_bytes() for project, root in f.roots.items()})
            self.assertEqual(f.stress_replies, {})
            f.handshake()
            project = 'fixture-project-a'
            payload = {'kind': 'ordinary_project', 'request_id': 'default-short-reply', 'project_id': project,
                'objective': 'Synthetic ordinary response', 'scope': 'Only registered inputs', 'effect': 'read_only',
                'input_version': f.ordinary_grants[project]['input_version'], 'mode': 'new', 'parent_task_id': None}
            self.assertEqual(f.call('POST', '/api/tasks', payload)[0], 200)
            self.assertTrue(f.run_worker(project, task_id=payload['request_id']))
            response = f.call(path='/api/tasks/' + payload['request_id'])[2]['data']['task']['result']['response']
            self.assertEqual(response, 'Synthetic read-only response for fixture-project-a.')
        with FunctionalFixture() as f:
            f.tasks.submit(f.payloads['fixture-project-a'])
            with self.assertRaises(ValueError):
                f.stress_sample()
            self.assertEqual(f.stress_replies, {})

    def test_cli_stress_control_is_explicit_and_reports_only_bounded_metadata(self):
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('pch_functional_fixture.py')), '--serve'],
            input='{"action":"stress-sample"}\n{"action":"ordinary-sample"}\n{"action":"stop"}\n',
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        metadata = rows[1]
        self.assertTrue(metadata['stress_ready'])
        self.assertEqual({row['project_id'] for row in metadata['projects']}, {'fixture-project-a', 'fixture-project-b'})
        self.assertTrue(all('reply_sha256' in row and 'reply_bytes' in row for row in metadata['projects']))
        self.assertTrue(rows[2]['ordinary_ready'])
        self.assertEqual(rows[3], {'stopped': True})
        for private in ('root_path', 'cookie', 'csrf', 'reply_text'):
            self.assertNotIn(private, result.stdout)
        self.assertLess(len(result.stdout.encode()), 5000)

    def test_saved_previews_are_version_bound_historical_and_exactly_invalidated(self):
        with FunctionalFixture() as f:
            source_hashes = {p: hashlib.sha256((root / 'STATE.yaml').read_bytes()).hexdigest()
                             for p, root in f.roots.items()}
            f.handshake()
            self.assertEqual(f.preview_sample()['materials'], 6)
            self.assertEqual(f.preview_sample()['materials'], 6)
            catalog = f.call(path='/api/materials')[2]['data']['materials']
            self.assertEqual(len(catalog), 6)
            self.assertTrue(all(row['available'] for row in catalog))
            image = next(row for row in catalog if row['artifact_id'] == 'fixture-project-a-image')
            self.assertEqual(f.call(path=image['read_url'])[0], 200)
            capture_url = '/api/projects/fixture-project-a/captures'
            capture = f.call(path=capture_url)[2]['data']['previews'][0]
            self.assertEqual(capture['version_state'], 'historical')
            self.assertIsNone(capture['captured_at'])
            self.assertFalse(capture['runtime_verified'])
            self.assertEqual(f.call(path=capture['image_url'])[0], 200)
            f.drift_preview_materials()
            self.assertEqual(f.call(path=image['read_url'])[0], 409)
            current = next(row for row in f.call(path='/api/materials')[2]['data']['materials'] if row['id'] == image['id'])
            self.assertNotEqual(current['version'], image['version'])
            self.assertEqual(f.call(path=current['read_url'])[0], 200)
            f.invalidate_preview('fixture-project-a', 'material_image')
            self.assertEqual(f.call(path=current['read_url'])[0], 404)
            f.invalidate_preview('fixture-project-a', 'saved_capture')
            self.assertEqual(f.call(path=capture['image_url'])[0], 409)
            b = next(row for row in f.call(path='/api/materials')[2]['data']['materials'] if row['artifact_id'] == 'fixture-project-b-image')
            self.assertEqual(f.call(path=b['read_url'])[0], 200)
            for project, root in f.roots.items():
                self.assertEqual(hashlib.sha256((root / 'STATE.yaml').read_bytes()).hexdigest(), source_hashes[project])
            self.assertEqual(f.effects, [])

    def test_ordinary_two_workers_have_independent_exact_release_latches(self):
        with FunctionalFixture() as f:
            f.handshake();f.ordinary_sample()
            ids={}
            for project,grant in f.ordinary_grants.items():
                f.scenarios[project]='delay'
                payload={'kind':'ordinary_project','request_id':project+'-ordinary',
                         'project_id':project,'objective':'Synthetic independent readonly task',
                         'scope':'Only registered synthetic inputs','effect':'read_only',
                         'input_version':grant['input_version'],'mode':'new','parent_task_id':None}
                self.assertEqual(f.call('POST','/api/tasks',payload)[0],200)
                ids[project]=payload['request_id']
            f.start_ordinary_runtime()
            def wait(predicate):
                end=time.monotonic()+4
                while time.monotonic()<end:
                    if predicate():return
                    time.sleep(.01)
                self.fail('Synthetic concurrent tasks did not reach expected state')
            wait(lambda:all((t:=f.task_store.task(ident))['status']=='running'and t['turn_id']for ident in ids.values()))
            a,b=(f.task_store.task(ids['fixture-project-'+suffix])for suffix in ('a','b'))
            self.assertNotEqual(a['owner'],b['owner']);self.assertNotEqual(a['thread_id'],b['thread_id'])
            with self.assertRaises(ValueError):f.release_ordinary('foreign-task')
            self.assertEqual(f.call('POST','/api/tasks/'+a['id']+'/cancel',{})[0],200)
            wait(lambda:f.task_store.task(a['id'])['status']=='cancelled')
            self.assertEqual(f.task_store.task(b['id'])['status'],'running')
            observed=f.ordinary_observations()
            self.assertEqual(observed['interrupts'],[(a['thread_id'],a['turn_id'])])
            self.assertFalse(next(row for row in observed['ordinary_adapters']if row['thread_id']==b['thread_id'])['released'])
            self.assertEqual(f.release_ordinary(b['id'])['released_count'],1)
            wait(lambda:f.task_store.task(b['id'])['status']=='checks_complete')
            self.assertEqual(f.effects,[])

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
