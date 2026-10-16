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
    def test_objective_only_stress_update_preserves_unknown_counts_and_collects(self):
        with FunctionalFixture() as f:
            f.stress_sample()
            before_a = f.projects.get_project('fixture-project-a')['business']['fields']
            before_b = f.projects.get_project('fixture-project-b')['business']['fields']
            self.assertIsNone(before_a['progress']['counting_basis'])
            self.assertIsNone(before_a['progress']['completed'])
            changed = f.update_source('fixture-project-a', completed=None,
                                     objective='Synthetic objective-only source update')
            self.assertFalse(changed['collected'])
            self.assertNotEqual(changed['old_sha256'], changed['new_sha256'])
            self.assertIsNone(changed['source']['completed'])
            self.assertEqual(f.sync('fixture-project-a')['exit_code'], 0)
            after_a = f.projects.get_project('fixture-project-a')
            self.assertEqual(after_a['freshness']['state'], 'fresh')
            self.assertEqual(after_a['business']['fields']['progress'], before_a['progress'])
            self.assertEqual(after_a['business']['fields']['current_work']['objective'],
                             'Synthetic objective-only source update')
            self.assertEqual(f.projects.get_project('fixture-project-b')['business']['fields'], before_b)
            self.assertEqual(f.task_store.tasks(), [])
            self.assertEqual(f.effects, [])

    def test_edge_cli_controls_keep_original_project_scope_and_bounded_grant(self):
        commands = [{'action': 'source-fault', 'project_id': 'fixture-project-a', 'kind': 'invalid'},
            {'action': 'source-restore', 'project_id': 'fixture-project-a'}, {'action': 'excluded-sample'},
            {'action': 'ordinary-sample', 'max_turn_seconds': 10},
            {'action': 'ordinary-scenario', 'project_id': 'fixture-project-a', 'scenario': 'failed_turn'},
            {'action': 'stop'}]
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('pch_functional_fixture.py')),
            '--serve', '--directory-size', '6'], input=''.join(json.dumps(command) + '\n' for command in commands),
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(rows[1]['source_fault'], 'invalid')
        self.assertTrue(rows[2]['source_restored'])
        self.assertTrue(rows[3]['excluded_ready'])
        self.assertEqual(rows[3]['excluded_probe_count'], 0)
        self.assertEqual(rows[4]['max_turn_seconds'], 10)
        self.assertEqual(rows[5], {'ordinary_scenario': 'failed_turn'})
        self.assertEqual(rows[6], {'stopped': True})

    def test_source_faults_real_collection_last_success_restore_and_b_isolation(self):
        for kind, disposition in [('missing', 'missing_source'), ('invalid', 'invalid'), ('permission', 'permission_denied')]:
            with self.subTest(kind=kind), FunctionalFixture() as f:
                f.handshake()
                a, b = 'fixture-project-a', 'fixture-project-b'
                path = f.roots[a] / 'STATE.yaml'
                original, mode = path.read_bytes(), path.stat().st_mode & 0o777
                before_a = f.call(path='/api/projects/' + a)[2]['data']
                before_b = f.call(path='/api/projects/' + b)[2]['data']
                metadata = f.source_fault(a, kind)
                self.assertEqual(metadata, {'project_id': a, 'source_fault': kind, 'collected': False})
                with self.assertRaises(ValueError):f.source_fault(a, 'missing')
                synced = f.sync(a)
                self.assertNotEqual(synced['exit_code'], 0)
                failed = f.call(path='/api/projects/' + a)[2]['data']
                self.assertEqual(failed['source']['availability'], disposition)
                self.assertFalse(failed['source']['latest_observation']['success'])
                self.assertEqual(failed['source']['last_success_at'], before_a['source']['last_success_at'])
                self.assertEqual(f.call(path='/api/projects/' + b)[2]['data']['source'], before_b['source'])
                self.assertEqual(f.source_restore(a), {'source_restored': True, 'project_id': a, 'collected': False})
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(path.stat().st_mode & 0o777, mode)
                self.assertEqual(f.sync(a)['exit_code'], 0)
                restored = f.call(path='/api/projects/' + a)[2]['data']
                self.assertTrue(restored['source']['latest_observation']['success'])
                self.assertEqual(restored['source']['availability'], 'resolved')
                with self.assertRaises(ValueError):f.source_restore(a)
                for foreign in ('fixture-directory-002', 'foreign'):
                    with self.assertRaises(ValueError):f.source_fault(foreign, kind)
                    with self.assertRaises(ValueError):f.source_restore(foreign)
                self.assertEqual(f.effects, [])

    def test_source_fault_restore_refuses_replacement_and_symlink(self):
        with FunctionalFixture() as f:
            project = 'fixture-project-a'
            path = f.roots[project] / 'STATE.yaml'
            original = path.read_bytes()
            f.source_fault(project, 'missing')
            path.write_text('Concurrent fixture edit must remain')
            with self.assertRaises(ValueError):f.source_restore(project)
            self.assertEqual(path.read_text(), 'Concurrent fixture edit must remain')
            path.unlink()
            f.source_restore(project)
            retained = path.with_name('retained-state.yaml')
            path.rename(retained)
            path.symlink_to(retained)
            with self.assertRaises(ValueError):f.source_fault(project, 'invalid')
            self.assertEqual(retained.read_bytes(), original)

    def test_excluded_sample_uses_real_registry_flags_frozen_scope_and_no_collection_probes(self):
        with FunctionalFixture(directory_size=6) as f:
            f.handshake()
            scope = f.root / 'data/connections/sync_scope.yaml'
            before = scope.read_bytes()
            metadata = f.excluded_sample()
            self.assertTrue(metadata['excluded_ready'])
            self.assertEqual(metadata['scope_total'], 6)
            self.assertEqual(len(metadata['excluded']), 4)
            self.assertEqual(metadata['eligible_project_ids'], ['fixture-project-a', 'fixture-project-b'])
            self.assertEqual([row['reason'] for row in metadata['excluded']],
                ['removed_local', 'cloud', 'connection_read_disabled', 'connection_read_disabled'])
            self.assertEqual(metadata['excluded_probe_count'], 0)
            self.assertEqual(metadata['sync']['exit_code'], 0)
            marker = len(f.probes)
            self.assertEqual(f.sync()['exit_code'], 0)
            excluded_roots = {f.directory_roots[row['project_id']] for row in metadata['excluded']}
            self.assertFalse(any(kind == 'source-refresh' and root in excluded_roots for kind, root in f.probes[marker:]))
            status, _, body = f.call(path='/api/sync-status')
            self.assertEqual(status, 200)
            self.assertEqual(scope.read_bytes(), before)
            self.assertEqual(len(f.source.projects), 6)
            with self.assertRaises(ValueError):f.excluded_sample()
        with FunctionalFixture() as f:
            with self.assertRaises(ValueError):f.excluded_sample()

    def test_ordinary_failed_turn_is_provider_failure_and_duration_only_changes_before_admission(self):
        with FunctionalFixture() as f:
            for invalid in (True, 10.0, 9, 11, 121, '10'):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):f.ordinary_sample(invalid)
            metadata = f.ordinary_sample(max_turn_seconds=10)
            self.assertEqual(metadata['max_turn_seconds'], 10)
            self.assertTrue(all(grant['max_turn_seconds'] == 10 for grant in f.ordinary_grants.values()))
            f.handshake()
            project = 'fixture-project-a'
            f.scenarios[project] = 'failed_turn'
            payload = {'kind': 'ordinary_project', 'request_id': 'failed-provider-turn', 'project_id': project,
                'objective': 'Synthetic native-terminal failure', 'scope': 'Registered readonly inputs',
                'effect': 'read_only', 'input_version': f.ordinary_grants[project]['input_version'],
                'mode': 'new', 'parent_task_id': None}
            self.assertEqual(f.call('POST', '/api/tasks', payload)[0], 200)
            with self.assertRaises(ValueError):f.ordinary_sample(120)
            self.assertTrue(f.run_worker(project, task_id=payload['request_id']))
            task = f.call(path='/api/tasks/' + payload['request_id'])[2]['data']['task']
            self.assertEqual(task['status'], 'failed')
            self.assertEqual(task['provider_status'], 'failed')
            self.assertEqual(task['result']['provider_status'], 'failed')
            self.assertNotIn('checks', task['result'])
            self.assertEqual(f.effects, [])
        with FunctionalFixture() as f:
            self.assertEqual(f.ordinary_sample()['max_turn_seconds'], 120)

    def test_empty_directory_is_real_authenticated_http_and_retains_ledger_sources_scope(self):
        with FunctionalFixture() as f:
            f.handshake()
            before = f.call()[2]['data']
            self.assertEqual(before['total'], 2)
            head = f.projects.ledger_head()
            scope = f.root / 'data/connections/sync_scope.yaml'
            scope_before = scope.read_bytes()
            sources_before = {project: (root / 'STATE.yaml').read_bytes() for project, root in f.roots.items()}
            metadata = f.empty_directory()
            self.assertTrue(metadata['empty_directory_ready'])
            self.assertEqual(metadata['classification'], 'synthetic_fixture')
            self.assertEqual(metadata['sync'], {'exit_code': 2, 'status': 'NO_ELIGIBLE_PROJECTS', 'appended_count': 0})
            self.assertEqual(metadata['scope_project_ids'], ['fixture-project-a', 'fixture-project-b'])
            self.assertEqual(metadata['ledger_head'], head)
            self.assertTrue(metadata['ledger_unchanged'])
            self.assertEqual(f.projects._resolver().projects, {})
            status, _, response = f.call(path='/api/projects')
            self.assertEqual(status, 200)
            self.assertEqual((response['data']['total'], response['data']['projects']), (0, []))
            status, _, response = f.call(path='/api/projects/fixture-project-a')
            self.assertEqual((status, response['error']['code']), (404, 'PROJECT_NOT_FOUND'))
            self.assertEqual(f.call(path='/api/sync-status')[0], 200)
            self.assertEqual(f.projects.ledger_head(), head)
            self.assertEqual(scope.read_bytes(), scope_before)
            self.assertEqual(sources_before, {project: (root / 'STATE.yaml').read_bytes() for project, root in f.roots.items()})
            self.assertEqual(f.empty_directory(), metadata)
            self.assertEqual(f.task_store.tasks(), [])
            self.assertEqual(f.effects, [])
            root = f.root
        self.assertTrue(f.closed)
        self.assertFalse(f.thread.is_alive())
        self.assertFalse(root.exists())
        with FunctionalFixture() as fresh:
            fresh.handshake()
            default = fresh.call()[2]['data']
            self.assertEqual(default['total'], 2)
            self.assertEqual([p['project_id'] for p in default['projects']], ['fixture-project-a', 'fixture-project-b'])

    def test_empty_directory_rejects_late_grants_tasks_and_foreign_roots_without_changes(self):
        for phase in ('ordinary-grants', 'task', 'additional-grant', 'foreign-root'):
            with self.subTest(phase=phase), FunctionalFixture() as f:
                registry = f.root / 'data/registry/external_projects.yaml'
                before = registry.read_bytes()
                roots = dict(f.directory_roots)
                if phase == 'ordinary-grants':
                    f.ordinary_sample()
                elif phase == 'task':
                    f.tasks.submit(f.payloads['fixture-project-a'])
                elif phase == 'additional-grant':
                    f.task_store.register_grant({'grant_id': 'additional-synthetic-grant', 'version': 1})
                else:
                    f.directory_roots['foreign'] = Path('/never-probe-this-foreign-root')
                try:
                    with self.assertRaises(ValueError):
                        f.empty_directory()
                    self.assertEqual(registry.read_bytes(), before)
                    self.assertEqual(len(f.source.projects), 2)
                finally:
                    f.directory_roots = roots

    def test_empty_directory_cli_control_and_103_project_default_remain_separate(self):
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('pch_functional_fixture.py')), '--serve'],
            input='{"action":"empty-directory"}\n{"action":"stop"}\n', capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertTrue(rows[1]['empty_directory_ready'])
        self.assertEqual(rows[1]['total'], 0)
        self.assertEqual(rows[1]['sync']['appended_count'], 0)
        self.assertEqual(rows[2], {'stopped': True})
        with FunctionalFixture(directory_size=103) as f:
            f.handshake()
            status, _, listing = f.call()
            self.assertEqual(status, 200)
            self.assertEqual(listing['data']['total'], 103)
            self.assertEqual(len(f.source.projects), 103)
            self.assertEqual(len(f.roots), 2)
            self.assertEqual(len(f.directory_roots), 103)

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

    def test_active_blocked_stress_is_explicit_and_preserves_local_failure_semantics(self):
        with FunctionalFixture() as f:
            for invalid in (1, "true", None):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    f.stress_sample(invalid)
            self.assertIsNone(f.stress_metadata)
            metadata = f.stress_sample(True)
            self.assertTrue(metadata['active_blocked'])
            self.assertEqual(f.stress_sample(True), metadata)
            with self.assertRaises(ValueError):
                f.stress_sample(False)
            f.handshake()
            a = f.call(path='/api/projects/fixture-project-a')[2]['data']
            b = f.call(path='/api/projects/fixture-project-b')[2]['data']
            self.assertEqual(a['business']['normalized_status'], 'active')
            self.assertFalse(a['business']['fields']['current_work']['completed'])
            self.assertEqual(a['business']['fields']['blockers'][0]['reason'], 'Candidate has changed')
            self.assertEqual(a['business']['fields']['blockers'][0]['recovery_condition'], 'Review B')
            self.assertEqual(b['business']['normalized_status'], 'unknown')
            self.assertIsNone(b['business']['fields']['blockers'])
            self.assertEqual(len(a['business']['unknown_fields']), metadata['projects'][0]['unknown_business_fields'])
            self.assertEqual(f.task_store.tasks(), [])
            self.assertEqual(f.effects, [])

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
