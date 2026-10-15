from __future__ import annotations

import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.service_contract import ServiceError
from hub.task_service import root_identity
from hub.workbench_store import digest
from hub.writer_handoff import snapshot_scope, verify_handoff, scopes_conflict


class WriterHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        (self.root / 'owned.txt').write_text('original')
        self.now = 1000
        self.grant = {'grant_id': 'grant-one', 'version': 1, 'task_id': 'task-one',
                      'project_id': 'project-one', 'root': str(self.root),
                      'root_identity': root_identity(self.root), 'expires_at': 1200}
        self.observation = {'thread_id': 'native-one', 'cwd': str(self.root), 'source': 'vscode',
                            'provider': 'openai', 'status': 'interrupted', 'observed_at': 995}
        self.authority = {'source': 'root_backend', 'decision_id': 'release-one',
                          'release_source': 'native_turn_terminal', 'released': True,
                          'from_owner': 'owner-one', 'thread_id': 'native-one', 'observed_at': 995}
        self.proof = {'grant_hash': digest(self.grant), 'registry_hash': 'a' * 64,
                      'task_id': 'task-one', 'from_owner': 'owner-one', 'to_owner': 'owner-two',
                      'observation_hash': digest(self.observation), 'authority_hash': digest(self.authority),
                      'scope': snapshot_scope(self.root, ['owned.txt', 'future/new.txt']),
                      'issued_at': 995, 'expires_at': 1050}

    def verify(self, proof=None):
        return verify_handoff(proof or self.proof, grant=self.grant, registry_hash='a' * 64,
                              observation=self.observation, authority=self.authority,
                              from_owner='owner-one', to_owner='owner-two', task_id='task-one', now=self.now)

    def test_explicit_release_binds_current_files_and_absence(self):
        result = self.verify()
        self.assertEqual(result, self.proof['scope'])
        self.assertEqual(result['paths']['future/new.txt'], {'state': 'absent'})
        self.assertEqual(result['git']['common']['identity'], result['git']['git_dir']['identity'])

    def test_owner_grant_task_registry_and_proof_drift_rejected(self):
        for field, value in [('from_owner', 'wrong'), ('to_owner', 'wrong'), ('task_id', 'wrong'),
                             ('grant_hash', 'b' * 64), ('registry_hash', 'b' * 64),
                             ('observation_hash', 'b' * 64), ('authority_hash', 'b' * 64)]:
            with self.subTest(field=field):
                proof = copy.deepcopy(self.proof)
                proof[field] = value
                with self.assertRaises(ServiceError):
                    self.verify(proof)
        self.grant['version'] += 1
        with self.assertRaises(ServiceError):
            self.verify()

    def test_expiry_and_observation_age_rejected(self):
        for field, value in [('expires_at', 999), ('expires_at', 1250), ('expires_at', float('nan')),
                             ('issued_at', 800), ('issued_at', 1001)]:
            proof = copy.deepcopy(self.proof)
            proof[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ServiceError):
                self.verify(proof)
        self.observation['observed_at'] = 800
        self.proof['observation_hash'] = digest(self.observation)
        with self.assertRaises(ServiceError):
            self.verify()

    def test_idle_not_loaded_and_unknown_release_never_prove_absence(self):
        for status in ['idle', 'notLoaded', 'running', 'unknown']:
            self.observation['status'] = status
            self.proof['observation_hash'] = digest(self.observation)
            with self.subTest(status=status), self.assertRaises(ServiceError):
                self.verify()
        self.authority['release_source'] = 'empty_thread_list'
        self.proof['authority_hash'] = digest(self.authority)
        with self.assertRaises(ServiceError):
            self.verify()

    def test_source_provider_cwd_observation_changes_rejected(self):
        for field, value in [('source', 'cli'), ('provider', 'other'), ('cwd', str(self.root.parent)),
                             ('thread_id', 'another-thread')]:
            old = self.observation[field]
            self.observation[field] = value
            with self.subTest(field=field), self.assertRaises(ServiceError):
                self.verify()
            self.observation[field] = old

    def test_explicit_owner_release_requires_root_decision_and_same_thread(self):
        self.authority['release_source'] = 'owner_explicit_release'
        self.observation['status'] = 'notLoaded'
        self.proof['observation_hash'] = digest(self.observation)
        self.proof['authority_hash'] = digest(self.authority)
        self.assertEqual(self.verify(), self.proof['scope'])
        for field, value in [('source', 'client'), ('released', False), ('thread_id', 'wrong'),
                             ('from_owner', 'wrong'), ('decision_id', '')]:
            old = self.authority[field]
            self.authority[field] = value
            self.proof['authority_hash'] = digest(self.authority)
            with self.subTest(field=field), self.assertRaises(ServiceError):
                self.verify()
            self.authority[field] = old
        self.proof['authority_hash'] = digest(self.authority)
        self.observation['status'] = 'running'
        self.proof['observation_hash'] = digest(self.observation)
        with self.assertRaises(ServiceError):
            self.verify()

    def test_preimages_and_explicit_absent_checked(self):
        (self.root / 'owned.txt').write_text('changed')
        with self.assertRaises(ServiceError):
            self.verify()
        (self.root / 'owned.txt').write_text('original')
        (self.root / 'future').mkdir()
        (self.root / 'future/new.txt').write_text('appeared')
        with self.assertRaises(ServiceError):
            self.verify()

    def test_unsafe_relative_paths_rejected(self):
        for path in ['.', '..', '/etc/passwd', 'a/../owned.txt', './owned.txt', 'a//b',
                     'a/', 'a\\b', '*', 'a?', 'a[0]', '.git/config']:
            with self.subTest(path=path), self.assertRaises(ServiceError):
                snapshot_scope(self.root, [path])
        with self.assertRaises(ServiceError):
            snapshot_scope(self.root, ['owned.txt', 'owned.txt'])

    def test_symlink_hardlink_and_ancestor_alias_rejected(self):
        (self.root / 'link').symlink_to('owned.txt')
        (self.root / 'aliasdir').symlink_to(self.root, target_is_directory=True)
        aliasroot = self.root.parent / 'root-alias'
        aliasroot.symlink_to(self.root, target_is_directory=True)
        for root, path in [(self.root, 'link'), (self.root, 'aliasdir/owned.txt'), (aliasroot, 'owned.txt')]:
            with self.subTest(path=path), self.assertRaises(ServiceError):
                snapshot_scope(root, [path])
        os.link(self.root / 'owned.txt', self.root / 'hard')
        with self.assertRaises(ServiceError):
            snapshot_scope(self.root, ['hard'])

    def item(self, paths, access='write', status='running', root=None):
        return {'scope': snapshot_scope(root or self.root, paths), 'access': access, 'status': status}

    def test_alias_project_names_overlap_and_read_read(self):
        a = self.item(['owned.txt'])
        b = self.item(['owned.txt'])
        a['project_id'], b['project_id'] = 'first', 'alias'
        self.assertTrue(scopes_conflict(a, b))
        self.assertFalse(scopes_conflict(self.item(['owned.txt'], 'read'), self.item(['owned.txt'], 'read')))
        self.assertFalse(scopes_conflict(a, self.item(['different.txt'], 'read')))
        self.assertTrue(scopes_conflict(a, self.item(['different.txt'], status='requires_reconcile')))

    def test_nested_root_paths_use_actual_file_identity(self):
        child = self.root / 'child'
        child.mkdir()
        (child / 'owned.txt').write_text('nested')
        self.assertFalse(scopes_conflict(self.item(['owned.txt']), self.item(['owned.txt'], root=child)))
        self.assertTrue(scopes_conflict(self.item(['child/owned.txt']), self.item(['owned.txt'], root=child)))

    def test_real_worktree_shared_repo_identity_is_not_file_alias(self):
        subprocess.run(['git', '-C', str(self.root), 'add', 'owned.txt'], check=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Test', '-c',
                        'user.email=test@example.invalid', 'commit', '-qm', 'fixture'], check=True)
        worktree = self.root.parent / 'worktree'
        subprocess.run(['git', '-C', str(self.root), 'worktree', 'add', '-q', '-b', 'other', str(worktree)], check=True)
        a, b = self.item(['owned.txt']), self.item(['owned.txt'], root=worktree)
        self.assertEqual(a['scope']['git']['common']['identity'], b['scope']['git']['common']['identity'])
        self.assertNotEqual(a['scope']['git']['worktree']['identity'], b['scope']['git']['worktree']['identity'])
        self.assertFalse(scopes_conflict(a, b))
        b['status'] = 'requires_reconcile'
        self.assertTrue(scopes_conflict(a, b))


if __name__ == '__main__':
    unittest.main()
