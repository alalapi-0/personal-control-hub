"""Terminal CSS receipts only release disjoint synthetic workspace fences."""
import copy
import hashlib
import json
import os
from pathlib import Path
import time
import unittest
from unittest import mock

import test_hub_ordinary_scope as scope_fixtures
from hub.css_trial import FIELDS
from hub.service_contract import ServiceError
from hub.task_service import GRANT_FIELDS, root_identity
from hub.workbench_store import canonical, digest


class TerminalScopeTests(unittest.TestCase):
    def setUp(self):
        # Composition avoids discovering all tests on either fixture class again.
        self.fixture = scope_fixtures.OrdinaryScopeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store
        self.fixture.foreign()
        self.sql("UPDATE tasks SET payload_hash=? WHERE id='foreign'",
                 (hashlib.sha256(b'not json').hexdigest(),))
        self.fixture.legacy_metadata(checks=['csp_css_trial'])
        self.canonical = Path(self.fixture.grants[0]['root'])
        self.trial = self.canonical.with_name('css-trial')
        self.trial.mkdir()
        # TMPDIR is inside the Hub checkout. Synthetic independent projects
        # must not inherit its enclosing real Git identity. Shared-Git cases
        # below explicitly supply the synthetic common identity they exercise.
        git_identity = mock.patch('hub.writer_handoff._git_identity', return_value=None)
        git_identity.start()
        self.addCleanup(git_identity.stop)
        self.grant = self.store.grant(self.fixture.grants[0]['grant_id'])
        self.grant.update(root=str(self.trial), root_identity=root_identity(self.trial))
        self.grant['css_trial'] = {key: 'synthetic' for key in FIELDS}
        self.grant['css_trial'].update(canonical_root=str(self.canonical),
            registry_hash=self.fixture.grants[0]['registry_hash'])
        self.replace_grant(self.grant)
        self.sql("UPDATE tasks SET thread_id='old-thread',turn_id='old-turn',provider_status='completed' WHERE id='foreign'")
        for suffix in ('older', 'previous-owner'):
            process = {'pid': 1000000000, 'start_ticks': '1'}
            self.store.event('foreign', 'dispatch-intent-' + suffix,
                             {'worker': {'owner': suffix, **process}})
            self.store.event('foreign', 'stdio-child-' + suffix, process)
        self.fixture.admit()

    def sql(self, statement, values=()):
        return self.fixture.sql(statement, values)

    def replace_grant(self, grant):
        # A direct synthetic SQL edit preserves arbitrary legacy metadata without
        # running modern grant validation or reading any business contents.
        self.sql('UPDATE grants SET body=?,hash=?,version=? WHERE id=?',
                 (canonical(grant).decode(), digest(grant), grant['version'], grant['grant_id']))
        intent = json.loads(self.sql("SELECT intent FROM tasks WHERE id='foreign'")[0][0])
        intent.update(grant=grant, grant_hash=digest(grant))
        self.sql("UPDATE tasks SET intent=?,intent_hash=? WHERE id='foreign'",
                 (canonical(intent).decode(), digest(intent)))

    def old_bytes(self):
        return (self.sql("SELECT * FROM tasks WHERE id='foreign'"),
                self.sql('SELECT * FROM grants ORDER BY id'),
                self.sql("SELECT * FROM events WHERE task_id='foreign' ORDER BY sequence"))

    def proof(self):
        return {'actor': 'trusted_local_owner', 'action': 'terminal_scope_resolution_v1',
                'snapshot': self.store.terminal_scope_snapshot('foreign'),
                'native': {'source': 'codex_app.read_thread', 'cwd': str(self.trial),
                           'thread_id': 'old-thread', 'thread_status': 'idle',
                           'turns_complete': True,
                           'turns': [{'id': 'older-turn', 'status': 'interrupted'},
                                     {'id': 'old-turn', 'status': 'completed'}],
                           'observed_at': time.time()}}

    def resolve(self, proof=None):
        return self.store.record_terminal_scope_resolution('foreign', proof or self.proof())

    def claim(self):
        return self.fixture.claim()

    def test_snapshot_is_read_only_and_lists_both_roots_and_all_processes(self):
        before = self.old_bytes()
        snapshot = self.store.terminal_scope_snapshot('foreign')
        self.assertEqual(set(snapshot), {'task_id', 'project_id', 'grant_hash', 'task_hash',
            'events_hash', 'registry_hash', 'thread_id', 'turn_id', 'root', 'scopes', 'processes'})
        self.assertEqual(snapshot['root'], str(self.trial))
        self.assertEqual({s['canonical_root'] for s in snapshot['scopes']},
                         {str(self.trial), str(self.canonical)})
        self.assertEqual(len(snapshot['processes']), 4)
        self.assertEqual({p['event_key'] for p in snapshot['processes']},
                         {row[2] for row in before[2]})
        self.assertEqual(self.old_bytes(), before)
        self.assertEqual(set(self.grant), GRANT_FIELDS | {'css_trial'})

    def test_missing_receipt_stays_closed_then_exact_proof_allows_disjoint_claim(self):
        self.assertIsNone(self.claim())
        before = self.old_bytes()
        proof = self.proof()
        receipt = self.resolve(proof)
        after = self.old_bytes()
        self.assertEqual(before[:2], after[:2])
        self.assertEqual(before[2], after[2][:-1])
        self.assertTrue(after[2][-1][2].startswith('terminal-scope-resolution-v1-'))
        self.assertEqual(receipt['expires_at'], proof['native']['observed_at'] + 30)
        self.assertEqual(self.resolve(proof), receipt)
        self.assertEqual(self.old_bytes(), after)
        self.assertEqual(self.claim()['id'], 'own')
        self.assertEqual(self.old_bytes(), after)

    def test_supported_stdio_source_and_not_loaded_are_accepted(self):
        proof = self.proof()
        proof['native'].update(source='app_server_stdio.thread/read', thread_status='notLoaded')
        self.resolve(proof)
        self.assertEqual(self.claim()['id'], 'own')

    def test_unconfirmed_or_wrong_native_observations_never_append(self):
        baseline = self.proof()
        native_cases = [
            {}, {'source': 'unconfirmed'}, {'cwd': str(self.canonical)},
            {'thread_id': 'another-thread'}, {'thread_status': 'active'},
            {'observed_at': time.time() - 31}, {'observed_at': time.time() + 60},
            {'observed_at': float('nan')}, {'observed_at': float('inf')},
            {'observed_at': True}, {'turns': []},
            {'turns_complete': False}, {'turns_complete': None},
            {'source': []}, {'thread_status': []},
            {'turns': [{'id': 'wrong-turn', 'status': 'completed'}]},
            {'turns': [{'id': 'old-turn', 'status': 'inProgress'}]},
            {'turns': [{'id': 'old-turn', 'status': 'interrupted'}]},
            {'turns': [{'id': 'older-turn', 'status': 'inProgress'},
                       {'id': 'old-turn', 'status': 'completed'}]},
            {'turns': [{'id': 'old-turn', 'status': 'completed', 'extra': True}]},
            {'extra': True},
        ]
        for change in native_cases:
            with self.subTest(change=change):
                proof = copy.deepcopy(baseline)
                if change:
                    proof['native'].update(change)
                else:
                    proof.pop('native')
                before = self.old_bytes()
                with self.assertRaises(ServiceError):
                    self.resolve(proof)
                self.assertEqual(self.old_bytes(), before)
                self.assertIsNone(self.claim())

    def test_wrong_proof_authority_snapshot_and_extra_fields_reject(self):
        for change in ({'actor': 'model'}, {'action': 'release'}, {'extra': True},
                       {'snapshot': {}}, {'snapshot': {'task_id': 'different'}}):
            with self.subTest(change=change):
                proof = self.proof()
                proof.update(change)
                before = self.old_bytes()
                with self.assertRaises(ServiceError): self.resolve(proof)
                self.assertEqual(self.old_bytes(), before)

    def test_row_grant_event_registry_and_scope_drift_invalidate_proof(self):
        # Each independent fresh fixture is small and automatically cleaned.
        changes = [
            lambda f: f.sql("UPDATE tasks SET result='changed' WHERE id='foreign'"),
            lambda f: f.sql("UPDATE grants SET hash='changed' WHERE id=?", (f.grant['grant_id'],)),
            lambda f: f.sql("UPDATE grants SET body='{}' WHERE id=?", (f.grant['grant_id'],)),
            lambda f: f.sql("UPDATE events SET body='{}' WHERE task_id='foreign'"),
            lambda f: f.fixture.fixture.fixture.registry_path.write_text('projects: []\n'),
            lambda f: f.trial.rename(f.trial.with_name('relocated-trial')),
        ]
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                case = TerminalScopeTests()
                case.setUp()
                try:
                    proof = case.proof()
                    change(case)
                    before = case.old_bytes()
                    with self.assertRaises(ServiceError): case.resolve(proof)
                    self.assertEqual(case.old_bytes(), before)
                    self.assertIsNone(case.claim())
                finally:
                    case.doCleanups()

    def test_drift_after_receipt_is_rechecked_at_claim(self):
        changes = [
            lambda f: f.sql("UPDATE tasks SET result='changed' WHERE id='foreign'"),
            lambda f: f.sql("UPDATE grants SET hash='changed' WHERE id=?", (f.grant['grant_id'],)),
            lambda f: f.fixture.fixture.fixture.registry_path.write_text('projects: []\n'),
            lambda f: f.sql("UPDATE events SET hash='changed' WHERE event_key='stdio-child-older'"),
        ]
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                case = TerminalScopeTests()
                case.setUp()
                try:
                    case.resolve()
                    change(case)
                    before = case.old_bytes()
                    self.assertIsNone(case.claim())
                    self.assertEqual(case.old_bytes(), before)
                finally:
                    case.doCleanups()

    def test_nonterminal_row_or_unconfirmed_ownership_cannot_be_resolved(self):
        columns = [('status', 'running'), ('provider_status', 'inProgress'),
                   ('thread_id', None), ('turn_id', None), ('owner', None),
                   ('lease_until', time.time() + 600), ('cancel', 1)]
        for column, value in columns:
            with self.subTest(column=column):
                original = self.sql('SELECT ' + column + " FROM tasks WHERE id='foreign'")[0][0]
                self.sql('UPDATE tasks SET ' + column + "=? WHERE id='foreign'", (value,))
                with self.assertRaises(ServiceError): self.store.terminal_scope_snapshot('foreign')
                self.sql('UPDATE tasks SET ' + column + "=? WHERE id='foreign'", (original,))

    def test_unknown_additional_scope_and_explicit_kind_are_closed(self):
        original = copy.deepcopy(self.grant)
        for change in ({'story_exchange': {'canonical_root': str(self.canonical)}},
                       {'kind': None}, {'extra_scope': str(self.canonical)}):
            with self.subTest(change=change):
                self.replace_grant({**copy.deepcopy(original), **change})
                with self.assertRaises(ServiceError): self.store.terminal_scope_snapshot('foreign')
                self.assertIsNone(self.claim())
        grant = copy.deepcopy(original)
        grant['css_trial']['extra_canonical_scope'] = str(self.canonical)
        self.replace_grant(grant)
        with self.assertRaises(ServiceError): self.store.terminal_scope_snapshot('foreign')
        self.assertIsNone(self.claim())

    def test_live_identity_in_older_history_cannot_be_ignored(self):
        ticks = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]
        process = {'pid': os.getpid(), 'start_ticks': ticks}
        for key, value in (('dispatch-intent-older', {'worker': {'owner': 'older', **process}}),
                           ('stdio-child-older', process)):
            self.sql('UPDATE events SET body=?,hash=? WHERE task_id=? AND event_key=?',
                     (canonical(value).decode(), digest(value), 'foreign', key))
        with self.assertRaises(ServiceError): self.store.terminal_scope_snapshot('foreign')
        self.assertIsNone(self.claim())

    def test_process_permission_unknown_stays_closed(self):
        original = Path.read_text
        def read(path, *args, **kwargs):
            if str(path).startswith('/proc/1000000000/'):
                raise PermissionError('synthetic denied process observation')
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'read_text', read):
            with self.assertRaises(ServiceError): self.store.terminal_scope_snapshot('foreign')
            self.assertIsNone(self.claim())

    def test_claim_reobserves_process_liveness_and_denied_observation(self):
        self.resolve()
        original = Path.read_text
        for denied in (False, True):
            with self.subTest(permission_denied=denied):
                def read(path, *args, **kwargs):
                    if str(path).startswith('/proc/1000000000/'):
                        if denied: raise PermissionError('synthetic denied observation')
                        return '1000000000 (synthetic) S ' + '0 ' * 18 + '1 0'
                    return original(path, *args, **kwargs)
                with mock.patch.object(Path, 'read_text', read):
                    self.assertIsNone(self.claim())

    def test_unpaired_process_event_stays_closed(self):
        self.sql("DELETE FROM events WHERE event_key='stdio-child-older'")
        with self.assertRaises(ServiceError): self.store.terminal_scope_snapshot('foreign')
        self.assertIsNone(self.claim())

    def test_receipt_expiry_is_closed_and_fresh_renewal_covers_previous_history(self):
        proof = self.proof()
        self.resolve(proof)
        with mock.patch('hub.task_store.time.time', return_value=proof['native']['observed_at'] + 31):
            self.assertIsNone(self.claim())
            with self.assertRaises(ServiceError): self.resolve(proof)
            renewed = self.proof()
            self.assertNotEqual(renewed['snapshot']['events_hash'], proof['snapshot']['events_hash'])
            self.resolve(renewed)
            self.assertEqual(self.claim()['id'], 'own')
        receipts = [row for row in self.old_bytes()[2] if row[2].startswith('terminal-scope-resolution-v1-')]
        self.assertEqual(len(receipts), 2)

    def test_receipt_body_tampering_and_post_receipt_event_block_claim(self):
        self.resolve()
        receipt = self.sql("SELECT event_key,body,hash FROM events WHERE event_key LIKE 'terminal-scope-resolution-v1-%'")[0]
        self.sql('UPDATE events SET body=? WHERE event_key=?', ('{}', receipt[0]))
        self.assertIsNone(self.claim())
        self.sql('UPDATE events SET body=?,hash=? WHERE event_key=?', (receipt[1], receipt[2], receipt[0]))
        self.store.event('foreign', 'later-evidence', {'synthetic': True})
        self.assertIsNone(self.claim())

    def test_expiry_during_scope_checks_or_before_claim_update_stays_closed(self):
        for phase in ('snapshot', 'final_decision'):
            with self.subTest(phase=phase):
                case = TerminalScopeTests()
                case.setUp()
                try:
                    proof = case.proof()
                    clock = [proof['native']['observed_at']]
                    with mock.patch('hub.task_store.time.time', side_effect=lambda: clock[0]):
                        case.resolve(proof)
                        original = (case.store._terminal_scope_snapshot if phase == 'snapshot'
                                    else case.store._foreign_fence_scopes)
                        def slow(*args, **kwargs):
                            value = original(*args, **kwargs)
                            clock[0] += 31
                            return value
                        method = '_terminal_scope_snapshot' if phase == 'snapshot' else '_foreign_fence_scopes'
                        with mock.patch.object(case.store, method, side_effect=slow):
                            self.assertIsNone(case.claim())
                finally:
                    case.doCleanups()

    def test_alias_replacement_of_either_root_stays_closed(self):
        for name in ('trial', 'canonical'):
            with self.subTest(root=name):
                case = TerminalScopeTests()
                case.setUp()
                try:
                    case.resolve()
                    root = getattr(case, name)
                    moved = root.with_name(root.name + '-moved')
                    root.rename(moved)
                    root.symlink_to(moved, target_is_directory=True)
                    self.assertIsNone(case.claim())
                finally:
                    case.doCleanups()

    def test_same_project_and_nested_or_shared_git_scopes_stay_closed(self):
        self.resolve()
        self.sql("UPDATE tasks SET project=? WHERE id='own'", (self.grant['project_id'],))
        self.assertIsNone(self.claim())
        self.sql("UPDATE tasks SET project=? WHERE id='own'", (self.fixture.grants[1]['project_id'],))
        from hub.writer_handoff import workspace_identity
        own = workspace_identity(self.fixture.grants[1]['root'])
        for root in (self.trial, self.canonical):
            for kind in ('nested', 'shared_git'):
                with self.subTest(root=root, overlap=kind):
                    def identity(path):
                        value = workspace_identity(path)
                        if kind == 'shared_git':
                            value['git'] = {'common': {'identity': [123, 456]}}
                        elif str(path) == str(root):
                            value['canonical_root'] = str(Path(own['canonical_root']) / 'nested')
                        return value
                    with mock.patch('hub.writer_handoff.workspace_identity', side_effect=identity):
                        # Freeze the overlapping identity in a fresh receipt so
                        # this exercises overlap, rather than identity drift.
                        self.resolve()
                        self.assertIsNone(self.claim())


if __name__ == '__main__':
    unittest.main()
