"""Atomic claims parallelize only current, disjoint reads of one trusted class."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import test_hub_ordinary_worker as fixtures
from hub.ordinary_tasks import make_grant
from hub.task_store import TaskStore
from hub.workbench_store import digest


class OrdinaryParallelClaimTests(unittest.TestCase):
    classification = 'synthetic_fixture'

    def setUp(self):
        self.fixture = fixtures.OrdinaryWorkerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store
        self.base = Path(self.fixture.temp.name)
        self.a = self.fixture.root
        self.b = self.base / 'second'
        self.b.mkdir()
        (self.b / 'README.md').write_text('Second synthetic input\n')
        self.configure(self.a, self.b)

    def configure(self, a, b, c=None):
        version = getattr(self, '_grant_version', 9) + 1
        self._grant_version = version
        roots = [('project-a', a), ('project-b', b)] + ([('project-c', c)] if c is not None else [])
        registry = {'projects': [
            {'id': project, 'name': project, 'enabled': True,
             'connection_read_allowed': True, 'root_path': str(root),
             'current_state_paths': ['README.md']}
            for project, root in roots]}
        self.fixture.registry_path.write_text(yaml.safe_dump(registry))
        hashed = hashlib.sha256(self.fixture.registry_path.read_bytes()).hexdigest()
        (self.store.root / 'data/connections/sync_scope.yaml').write_text(yaml.safe_dump({
            'schema_version': '1.0', 'scope_id': 'parallel-controlled', 'baseline_total': len(roots),
            'project_ids': [project for project, _ in roots], 'excluded_project_ids': {}}))
        self.grants = [make_grant(root, project, hashed, 'grant-' + project,
                                 input_paths=['README.md'], version=version, classification=self.classification)
                       for project, root in roots]
        for grant in self.grants:
            self.store.register_grant(grant)

    def admit(self, ident, index=0, *, grant=None, payload_extra=None, intent_extra=None):
        grant = copy.deepcopy(grant or self.grants[index])
        payload = {**self.fixture.spec, 'request_id': ident, 'project_id': grant['project_id'],
                   'input_version': grant['input_version'], **(payload_extra or {})}
        intent = {**self.fixture.intent, 'grant': grant, 'grant_hash': digest(grant),
                  'task_spec': payload, 'registry_hash': grant['registry_hash'], **(intent_extra or {})}
        self.store.admit(payload, intent)

    def claim_together(self):
        barrier = threading.Barrier(2)
        def claim(ident):
            # Distinct store instances model actual competing executor owners.
            store = TaskStore(self.store.root)
            barrier.wait(timeout=5)
            return store.claim('owner-' + ident, only_task_id=ident)
        with ThreadPoolExecutor(max_workers=2) as workers:
            return list(workers.map(claim, ['task-a', 'task-b']))

    def test_two_distinct_registered_read_tasks_claim_atomically(self):
        self.admit('task-a'); self.admit('task-b', 1)
        claimed = self.claim_together()
        self.assertEqual({task['id'] for task in claimed}, {'task-a', 'task-b'})
        self.assertEqual(len({task['owner'] for task in claimed}), 2)
        self.assertTrue(all(self.store.task(ident)['status'] == 'running'
                            for ident in ['task-a', 'task-b']))
        self.assertIsNone(self.store.claim('third-owner', only_task_id='task-a'))

    def test_same_project_remains_fenced_under_competing_claims(self):
        self.admit('task-a'); self.admit('task-b')
        self.assertEqual(sum(task is not None for task in self.claim_together()), 1)

    def assert_serial(self):
        self.assertIsNotNone(self.store.claim('owner-a', only_task_id='task-a'))
        self.assertIsNone(self.store.claim('owner-b', only_task_id='task-b'))
        self.assertEqual(self.store.task('task-b')['status'], 'queued')

    def assert_concurrent_serial(self):
        self.assertEqual(sum(task is not None for task in self.claim_together()), 1)
        self.assertEqual(sorted(self.store.task(ident)['status'] for ident in ['task-a', 'task-b']),
                         ['queued', 'running'])

    def test_same_root_under_distinct_project_ids_is_fenced(self):
        self.configure(self.a, self.a)
        self.admit('task-a'); self.admit('task-b', 1)
        self.assert_concurrent_serial()

    def test_nested_roots_are_fenced(self):
        nested = self.a / 'nested'; nested.mkdir()
        (nested / 'README.md').write_text('Nested synthetic input')
        self.configure(self.a, nested)
        self.admit('task-a'); self.admit('task-b', 1)
        self.assert_concurrent_serial()

    def test_changed_root_symlink_is_fenced(self):
        self.admit('task-a'); self.admit('task-b', 1)
        self.assertIsNotNone(self.store.claim('owner-a', only_task_id='task-a'))
        moved = self.base / 'moved-second'; self.b.rename(moved)
        self.b.symlink_to(moved, target_is_directory=True)
        self.assertIsNone(self.store.claim('owner-b', only_task_id='task-b'))

    def test_shared_git_worktrees_are_fenced(self):
        # Local disposable test repositories only; no project Git or remotes.
        def git(*args):
            subprocess.run(['git', *args], check=True, capture_output=True)
        git('init', '-q', str(self.a))
        git('-C', str(self.a), '-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid',
            '-c', 'commit.gpgsign=false', 'commit', '-q', '--allow-empty', '-m', 'synthetic')
        worktree = self.base / 'git-worktree'
        git('-C', str(self.a), 'worktree', 'add', '-q', '--detach', str(worktree))
        (worktree / 'README.md').write_text('Second synthetic Git input')
        self.configure(self.a, worktree)
        self.admit('task-a'); self.admit('task-b', 1)
        self.assert_concurrent_serial()

    def test_revoked_active_grant_cannot_take_exception(self):
        self.admit('task-a'); self.admit('task-b', 1)
        self.assertIsNotNone(self.store.claim('owner-a', only_task_id='task-a'))
        self.store.register_grant({**self.grants[0], 'version': 11})
        self.assertIsNone(self.store.claim('owner-b', only_task_id='task-b'))

    def test_revoked_candidate_grant_cannot_take_exception(self):
        self.admit('task-a'); self.admit('task-b', 1)
        self.store.register_grant({**self.grants[1], 'version': 11})
        self.assert_serial()

    def test_registry_revocation_checked_before_workspace_probe(self):
        self.admit('task-a'); self.admit('task-b', 1)
        self.assertIsNotNone(self.store.claim('owner-a', only_task_id='task-a'))
        self.fixture.registry_path.write_text(yaml.safe_dump({'projects': []}))
        with mock.patch('hub.writer_handoff.workspace_identity', side_effect=AssertionError('scope probe')):
            self.assertIsNone(self.store.claim('owner-b', only_task_id='task-b'))

    def test_mixed_unknown_write_and_forged_markers_remain_serial(self):
        for participant in [0, 1]:
            for alteration in ['unknown_kind', 'write', 'opposite_classification', 'unknown_classification', 'forged_hash',
                               'unregistered', 'wrong_project', 'expired', 'input_changed', 'forged_spec', 'forged_registry']:
                with self.subTest(participant=participant, alteration=alteration):
                    fixture = type(self)(); fixture.setUp()
                    try:
                        for index, ident in enumerate(['task-a', 'task-b']):
                            grant = copy.deepcopy(fixture.grants[index]); extra = {}; intent = {}
                            if index == participant:
                                if alteration == 'unknown_kind': extra['kind'] = 'unknown'
                                elif alteration == 'write': extra['effect'] = 'write'
                                elif alteration in {'opposite_classification', 'unknown_classification'}:
                                    grant['classification'] = ('synthetic_fixture' if self.classification == 'real' else 'real') if alteration == 'opposite_classification' else 'unknown'
                                elif alteration == 'forged_hash': intent['grant_hash'] = '0' * 64
                                elif alteration == 'forged_registry': intent['registry_hash'] = '0' * 64
                                elif alteration == 'forged_spec': intent['task_spec'] = {'effect': 'write'}
                                elif alteration == 'unregistered': grant['grant_id'] = 'unregistered-grant'
                                elif alteration == 'wrong_project': extra['project_id'] = 'forged-project'
                                elif alteration == 'expired': grant['expires_at'] = time.time() - 1
                            fixture.admit(ident, index, grant=grant, payload_extra=extra, intent_extra=intent)
                        if alteration == 'input_changed':
                            (fixture.a if participant == 0 else fixture.b).joinpath('README.md').write_text('Changed')
                        fixture.assert_serial()
                    finally:
                        fixture.doCleanups()

    def test_trusted_registration_of_mixed_effect_never_widens_exception(self):
        for participant in [0, 1]:
            for field, value in [('kind', 'other'), ('effect', 'write'), ('classification', 'synthetic_fixture' if self.classification == 'real' else 'real'),
                                 ('classification', 'unknown')]:
                with self.subTest(participant=participant, field=field, value=value):
                    fixture = type(self)(); fixture.setUp()
                    try:
                        altered = {**fixture.grants[participant], 'version': 11, field: value}
                        fixture.store.register_grant(altered)
                        for index, ident in enumerate(['task-a', 'task-b']):
                            fixture.admit(ident, index, grant=altered if index == participant else None)
                        fixture.assert_serial()
                    finally:
                        fixture.doCleanups()

    def test_expired_lease_becomes_reconcile_and_only_fences_own_workspace(self):
        self.admit('task-a'); self.admit('task-b', 1)
        self.assertIsNotNone(self.store.claim('owner-a', time.time() - 20, only_task_id='task-a'))
        self.assertIsNotNone(self.store.claim('owner-b', only_task_id='task-b'))
        self.assertEqual(self.store.task('task-a')['status'], 'requires_reconcile')
        self.admit('task-a-next')
        self.assertIsNone(self.store.claim('owner-next', only_task_id='task-a-next'))

    def test_child_waits_for_completed_exact_parent_and_rejects_stale_binding(self):
        self.admit('task-a'); self.admit('task-b', 1)
        self.store.claim('owner-a', only_task_id='task-a')
        self.store.update('task-a', 'owner-a', thread_id='thread-a', turn_id='turn-a')
        extra = {'parent_task_id': 'task-a', 'mode': 'busy_feedback'}
        binding = {'parent_binding': {'thread_id': 'thread-a', 'turn_id': 'turn-a'}}
        self.admit('child', payload_extra=extra, intent_extra=binding)
        self.assertIsNone(self.store.claim('child-owner', only_task_id='child'))
        self.store.update('task-a', 'owner-a', status='validating', provider_status='completed')
        self.store.update('task-a', 'owner-a', status='checks_complete')
        self.assertIsNotNone(self.store.claim('owner-b', only_task_id='task-b'))
        self.assertIsNotNone(self.store.claim('child-owner', only_task_id='child'))
        self.admit('stale-child', payload_extra=extra, intent_extra={
            'parent_binding': {'thread_id': 'thread-a', 'turn_id': 'other-turn'}})
        self.assertIsNone(self.store.claim('stale-owner', only_task_id='stale-child'))
        self.assertEqual(self.store.task('stale-child')['status'], 'requires_reconcile')


class RealOrdinaryParallelClaimTests(OrdinaryParallelClaimTests):
    """Apply every physical/metadata failure guard to real-classified temp inputs."""
    classification = 'real'

    def test_three_competing_real_owners_claim_only_two_then_release_slot(self):
        third = self.base / 'third'; third.mkdir()
        (third / 'README.md').write_text('Third controlled input\n')
        self.configure(self.a, self.b, third)
        for index, ident in enumerate(('task-a','task-b','task-c')):
            self.admit(ident, index)
        barrier = threading.Barrier(3)
        def claim(ident):
            store = TaskStore(self.store.root)
            barrier.wait(timeout=5)
            return store.claim('owner-' + ident, only_task_id=ident)
        with ThreadPoolExecutor(max_workers=3) as workers:
            claims = list(workers.map(claim, ('task-a','task-b','task-c')))
        active = [task for task in claims if task is not None]
        self.assertEqual(len(active), 2)
        self.assertEqual(len({task['owner'] for task in active}), 2)
        waiting = next(task for task in self.store.tasks() if task['status']=='queued')
        self.assertIsNone(TaskStore(self.store.root).claim('fourth-owner', only_task_id=waiting['id']))
        finished = active[0]
        self.store.update(finished['id'], finished['owner'], status='validating', provider_status='completed')
        self.store.update(finished['id'], finished['owner'], status='checks_complete')
        released = TaskStore(self.store.root).claim('after-release', only_task_id=waiting['id'])
        self.assertIsNotNone(released)
        self.assertEqual(released['id'], waiting['id'])
        self.assertEqual(sum(task['status']=='running' for task in self.store.tasks()),2)


if __name__ == '__main__':
    unittest.main()
