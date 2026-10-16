"""Synthetic ledger isolation: foreign content never becomes runtime authority."""
import sqlite3
import json
from contextlib import closing
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

import test_hub_ordinary_runtime as runtime_fixtures
from hub.ordinary_runtime import OrdinaryRuntime
from hub.workbench_store import canonical, digest


class OrdinaryScopeTests(unittest.TestCase):
    def setUp(self):
        # Compose the existing fixture without inheriting its discovered tests.
        self.fixture = runtime_fixtures.OrdinaryRuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store
        _, self.grants = self.fixture.parallel_runtime(3)
        self.selected = tuple(grant['grant_id'] for grant in self.grants[1:])

    def sql(self, statement, values=()):
        with closing(sqlite3.connect(self.store.path)) as connection:
            with connection:
                return connection.execute(statement, values).fetchall()

    def foreign(self, status='requires_reconcile'):
        self.fixture.admit('foreign', grant=self.grants[0])
        self.sql("UPDATE tasks SET status=?,owner='previous-owner',lease_until=1,payload='not json',result='not json' WHERE id='foreign'", (status,))
        return self.sql("SELECT status,owner,lease_until,cancel,payload,result FROM tasks WHERE id='foreign'")

    def admit(self, ident='own', index=1):
        self.fixture.admit(ident, grant=self.grants[index])

    def claim(self, ident='own'):
        return self.store.claim('scoped-worker', only_task_id=ident, selected_grant_ids=self.selected)

    def runtime(self):
        runtime = OrdinaryRuntime(self.store, grants=self.grants[1:], adapter_factory=self.fixture.Adapter,
            provider_status={'available':True,'classification':'synthetic_fixture'}, poll_interval=.01)
        self.addCleanup(runtime.stop)
        return runtime

    def assert_foreign_unchanged(self, before):
        self.assertEqual(before, self.sql("SELECT status,owner,lease_until,cancel,payload,result FROM tasks WHERE id='foreign'"))

    def test_scoped_query_and_runtime_skip_corrupt_foreign_terminal_and_queued(self):
        for status in ('checks_complete', 'queued'):
            with self.subTest(status=status):
                self.sql("DELETE FROM tasks")
                before = self.foreign(status)
                self.admit()
                self.assertEqual([task['id'] for task in self.store.tasks(selected_grant_ids=self.selected)], ['own'])
                self.assertIsNone(self.store.task('foreign', selected_grant_ids=self.selected))
                self.fixture.Adapter.hold = False
                runtime = self.runtime()
                runtime.start()
                self.fixture.wait(lambda:self.store.task('own')['status']=='checks_complete')
                runtime.stop()
                self.assert_foreign_unchanged(before)

    def test_verified_foreign_reconcile_does_not_decode_payload_result_or_spec(self):
        before = self.foreign()
        self.admit()
        original = self.store._task
        def decode_selected(row):
            self.assertNotEqual(row['id'], 'foreign')
            return original(row)
        with mock.patch.object(self.store, '_task', side_effect=decode_selected):
            self.assertEqual(self.claim()['id'], 'own')
        self.assert_foreign_unchanged(before)

    def test_foreign_story_fence_projects_metadata_without_decoding_private_spec(self):
        self.foreign()
        from hub.task_service import root_identity
        stage = Path(self.fixture.fixture.temp.name)/'foreign-stage'
        stage.mkdir()
        marker = 'OPAQUE_SYNTHETIC_FOREIGN_SPEC'
        # Only administrative fields matter to an unresolved fence. Deliberately
        # opaque specification data must never be interpreted as a task to run.
        old = self.grants[0]
        grant = {**old,'kind':'story_exchange','version':2,'root':str(stage),
            'root_identity':root_identity(stage),'task_spec':{'opaque':marker},
            'story_exchange':{'canonical_root':old['root'],'canonical_root_identity':old['root_identity'],
                              'registry_hash':old['registry_hash']}}
        self.store.register_grant(grant)
        intent = json.loads(self.sql("SELECT intent FROM tasks WHERE id='foreign'")[0][0])
        intent.update(grant=grant,grant_hash=digest(grant),task_spec={'opaque':marker})
        self.sql("UPDATE tasks SET intent=?,intent_hash=? WHERE id='foreign'",(canonical(intent).decode(),digest(intent)))
        self.admit()
        decode = self.store._decode
        def selected_only(raw, hashed):
            self.assertNotIn(marker, raw)
            return decode(raw, hashed)
        with mock.patch.object(self.store,'_decode',side_effect=selected_only):
            self.assertEqual(self.claim()['id'],'own')
        self.assertEqual(self.sql("SELECT status,lease_until,payload,result FROM tasks WHERE id='foreign'"),
                         [('requires_reconcile',1.0,'not json','not json')])

    def test_foreign_live_and_expired_running_keep_global_fence_without_mutation(self):
        for expiry in (1, time.time()+300):
            with self.subTest(expiry=expiry):
                self.sql("DELETE FROM tasks")
                self.foreign('running')
                self.sql("UPDATE tasks SET lease_until=? WHERE id='foreign'", (expiry,))
                before = self.sql("SELECT status,owner,lease_until,cancel,payload,result FROM tasks WHERE id='foreign'")
                self.admit()
                self.assertIsNone(self.claim())
                self.assert_foreign_unchanged(before)

    def test_unknown_foreign_intent_is_global_fence_and_never_decoded(self):
        before = self.foreign()
        self.sql("UPDATE tasks SET intent='unknown intent' WHERE id='foreign'")
        self.admit()
        self.assertIsNone(self.claim())
        self.assert_foreign_unchanged(before)

    def test_foreign_intent_integrity_is_required_without_decoding_it(self):
        self.foreign()
        self.sql("UPDATE tasks SET intent_hash='unverified' WHERE id='foreign'")
        self.admit()
        self.assertIsNone(self.claim())

    def test_foreign_same_project_fences_before_workspace_probes(self):
        self.foreign()
        self.sql("UPDATE tasks SET project=? WHERE id='foreign'", (self.grants[1]['project_id'],))
        self.admit()
        with mock.patch('hub.writer_handoff.workspace_identity', side_effect=AssertionError('unexpected probe')):
            self.assertIsNone(self.claim())

    def test_foreign_unregistered_grant_and_registry_drift_fence_before_foreign_probe(self):
        self.foreign()
        self.admit()
        from hub.writer_handoff import workspace_identity
        def probe(root):
            self.assertNotEqual(str(root), self.grants[0]['root'])
            return workspace_identity(root)
        with mock.patch('hub.writer_handoff.workspace_identity', side_effect=probe):
            self.sql('DELETE FROM grants WHERE id=?', (self.grants[0]['grant_id'],))
            self.assertIsNone(self.claim())
            self.store.register_grant(self.grants[0])
            self.fixture.fixture.registry['projects'][0]['enabled'] = False
            import yaml
            self.fixture.fixture.registry_path.write_text(yaml.safe_dump(self.fixture.fixture.registry))
            self.assertIsNone(self.claim())

    def test_foreign_shared_git_common_directory_fences_other_project(self):
        root = Path(self.grants[0]['root'])
        other = Path(self.grants[1]['root'])
        def git(*args):
            result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        git('init', '-q')
        git('-c','user.name=Synthetic','-c','user.email=synthetic@example.invalid',
            'commit','--allow-empty','-qm','Synthetic fixture')
        (other/'README.md').unlink()
        other.rmdir()
        git('worktree','add','--detach',str(other))
        (other/'README.md').write_text('Synthetic independent input')
        from hub.ordinary_tasks import make_grant
        self.grants[1]=make_grant(other,self.grants[1]['project_id'],self.grants[1]['registry_hash'],
            self.grants[1]['grant_id'],input_paths=['README.md'],version=2)
        self.store.register_grant(self.grants[1])
        self.foreign()
        self.admit()
        self.assertIsNone(self.claim())

    def test_foreign_nested_root_fences_alias_project(self):
        self.foreign()
        self.admit()
        from hub.writer_handoff import workspace_identity
        actual = workspace_identity(self.grants[1]['root'])
        def identity(root):
            if str(root)==self.grants[0]['root']:
                return {**actual,'canonical_root':str(Path(actual['canonical_root'])/'nested')}
            return workspace_identity(root)
        with mock.patch('hub.writer_handoff.workspace_identity',side_effect=identity):
            self.assertIsNone(self.claim())

    def test_two_disjoint_owned_tasks_run_and_cancel_independently_beside_foreign_fence(self):
        before = self.foreign()
        self.admit('a',1)
        self.admit('b',2)
        runtime = self.runtime()
        runtime.start()
        self.fixture.wait(self.fixture.both_started)
        self.store.cancel('a',worker_owner=self.store.task('a')['owner'])
        self.fixture.wait(lambda:self.store.task('a')['status']=='cancelled')
        self.assertEqual(self.store.task('b')['status'],'running')
        self.assertFalse(self.store.task('b')['cancel'])
        for adapter in self.fixture.Adapter.created:
            if adapter.root==Path(self.grants[2]['root']):adapter.hold=False
        self.fixture.wait(lambda:self.store.task('b')['status']=='checks_complete')
        runtime.stop()
        self.assert_foreign_unchanged(before)

    def test_project_grant_lookup_does_not_decode_unrelated_corrupt_body(self):
        self.sql("UPDATE grants SET body='not json' WHERE id=?", (self.grants[0]['grant_id'],))
        self.assertEqual(self.store.grants(project_id=self.grants[1]['project_id']), [self.grants[1]])

    def legacy_metadata(self, *, checks=None):
        from hub.task_service import validate_grant
        old=self.grants[0]
        grant={'grant_id':old['grant_id'],'version':2,'source':'verified_owner_decision',
            'task_id':'foreign','project_id':old['project_id'],'root':old['root'],
            'root_identity':old['root_identity'],'fingerprint':[],'mode':'new','parent_task_id':None,
            'annotation_hash':'a'*64,'checks':checks or ['fixture_sample'],'expires_at':time.time()+600,
            'isolated_root':True,'writable_scope':['.'],'prohibited':[],'auth':'ChatGPT',
            'max_turn_seconds':20,'image':'fixture.png','image_sha256':'b'*64}
        if checks is None:self.assertEqual(validate_grant(grant,live=False),grant)
        self.store.register_grant(grant)
        intent=json.loads(self.sql("SELECT intent FROM tasks WHERE id='foreign'")[0][0])
        intent.update(grant=grant,grant_hash=digest(grant),task_spec={'opaque':'FOREIGN_LEGACY_PRIVATE_SPEC'})
        self.sql("UPDATE tasks SET intent=?,intent_hash=? WHERE id='foreign'",(canonical(intent).decode(),digest(intent)))

    def legacy_foreign_wrapper(self):
        original=self.foreign
        def legacy(*args,**kwargs):
            before=original(*args,**kwargs)
            self.legacy_metadata()
            return before
        self.foreign=legacy

    def test_legacy_registered_disjoint_root_runs_without_decoding_foreign_bodies(self):
        before=self.foreign()
        self.legacy_metadata()
        self.admit()
        decode=self.store._decode
        def selected_only(raw,hashed):
            self.assertNotIn('FOREIGN_LEGACY_PRIVATE_SPEC',raw)
            return decode(raw,hashed)
        with mock.patch.object(self.store,'_decode',side_effect=selected_only):
            self.assertEqual(self.claim()['id'],'own')
        self.assert_foreign_unchanged(before)

    def test_legacy_shared_git_common_directory_remains_fenced(self):
        self.legacy_foreign_wrapper()
        self.test_foreign_shared_git_common_directory_fences_other_project()

    def test_legacy_nested_overlap_remains_fenced(self):
        self.legacy_foreign_wrapper()
        self.test_foreign_nested_root_fences_alias_project()

    def test_legacy_root_symlink_replacement_is_never_accepted(self):
        self.foreign()
        self.legacy_metadata()
        self.admit()
        root=Path(self.grants[0]['root'])
        relocated=root.with_name('moved-foreign-root')
        root.rename(relocated)
        root.symlink_to(relocated,target_is_directory=True)
        self.assertIsNone(self.claim())

    def test_legacy_hash_integrity_cannot_admit_foreign_scope(self):
        self.foreign()
        self.legacy_metadata()
        self.admit()
        self.sql("UPDATE tasks SET intent_hash='stale' WHERE id='foreign'")
        self.assertIsNone(self.claim())

    def test_legacy_explicit_null_kind_is_not_recognized_as_missing_kind(self):
        self.foreign()
        self.legacy_metadata()
        grant=self.store.grant(self.grants[0]['grant_id'])
        grant.update(version=3,kind=None)
        self.store.register_grant(grant)
        intent=json.loads(self.sql("SELECT intent FROM tasks WHERE id='foreign'")[0][0])
        intent.update(grant=grant,grant_hash=digest(grant))
        self.sql("UPDATE tasks SET intent=?,intent_hash=? WHERE id='foreign'",(canonical(intent).decode(),digest(intent)))
        self.admit()
        self.assertIsNone(self.claim())

    def test_legacy_css_extra_scope_does_not_fall_through_single_root_fence(self):
        self.foreign()
        self.legacy_metadata(checks=['csp_css_trial'])
        self.admit()
        self.assertIsNone(self.claim())

    def test_scoped_expiry_only_reconciles_owned_lease(self):
        before = self.foreign('running')
        self.admit()
        self.sql("UPDATE tasks SET status='running',owner='own-old',lease_until=1 WHERE id='own'")
        self.assertIsNone(self.claim())
        self.assertEqual(self.store.task('own')['status'],'requires_reconcile')
        self.assert_foreign_unchanged(before)
