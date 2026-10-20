"""Refactor identity acceptance uses isolated roots, never business projects."""
import copy
import unittest
from unittest.mock import patch

import pytest
import yaml

from connection_fixtures import Fixture
from hub.connection_records import RecordError
from hub.connection_records import update_key, validate_authority, validate_result
from hub.connection_refresh import RefreshLedger, refresh, RequestConflictError
from hub.connection_sync import incremental_request
from hub.connection_sources import _safe_relative_read
from hub.project_service import project_snapshot


class ProjectBindingJourneyTests(unittest.TestCase):
    """Discoverable caller counterexamples, using real synthetic files/ledger."""
    def setUp(self):
        self.f = Fixture()
        self.addCleanup(self.f.close)
        self.a, _ = self.f.add('a')
        self.b, self.declaration = self.f.add('b')
        self.ledger = RefreshLedger(self.f.hub, result_validator=validate_result)

    def test_unrelated_registry_edit_does_not_append_or_stale_b(self):
        r = self.f.resolver()
        first, code = incremental_request(self.f.hub, r, self.ledger, 'first', plan={'eligible_project_ids': ['b']})
        self.assertEqual(code, 0)
        head = self.ledger.head
        self.f.projects[0]['name'] = 'Unrelated A renamed'
        self.f.add('c')
        current = self.f.resolver()
        self.assertNotEqual(r.authority['registry_hash'], current.authority['registry_hash'])
        second, code = incremental_request(self.f.hub, current, self.ledger, 'second', plan={'eligible_project_ids': ['b']})
        self.assertEqual((second['appended_count'], code), (0, 0))
        self.assertEqual(head, self.ledger.head)
        rebuilt = self.ledger.rebuild(current_authority=current.authority)
        self.assertFalse(rebuilt['projects']['b']['authority_drift'])
        view = project_snapshot(current.registry, current.authority, self.ledger, observed_at=r.clock())
        self.assertEqual(next(x for x in view['projects'] if x['project_id'] == 'b')['freshness']['state'], 'fresh')

    def test_unrelated_inflight_edit_and_interrupted_resume_keep_b_binding(self):
        r = self.f.resolver()
        self.ledger.begin_request('open', ['b'], r.authority)
        fired = []
        def read(root, relative, **kwargs):
            value = _safe_relative_read(root, relative, **kwargs)
            if root == self.b and not fired:
                fired.append(True)
                self.f.projects[0]['name'] = 'Changed during B read'
                self.f.save_registry()
            return value
        with patch('hub.connection_sources._safe_relative_read', read):
            result = r.refresh('b')
        self.assertTrue(result['success'])
        current = self.f.resolver()
        outcome = refresh(self.ledger, current, 'open', ['b'])
        self.assertEqual(outcome['request']['status'], 'FINISHED')
        self.assertEqual(outcome['request']['authority']['registry_hash'], r.authority['registry_hash'])
        self.assertEqual(self.ledger.history('open')['results'][0]['result']['authority']['registry_hash'], current.authority['registry_hash'])

    def test_b_changes_and_shared_policy_invalidate_and_refuse_resume(self):
        import shutil
        mutations = ['permission', 'path', 'declaration', 'root', 'shared']
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                f = Fixture()
                try:
                    b, declaration = f.add('b')
                    r = f.resolver()
                    ledger = RefreshLedger(f.hub, result_validator=validate_result)
                    refresh(ledger, r, 'saved', ['b'])
                    ledger.begin_request('open', ['b'], r.authority)
                    if mutation == 'permission':
                        f.projects[0]['connection_read_allowed'] = False
                        f.save_registry()
                    elif mutation == 'path':
                        f.projects[0]['root_path'] = str(b.with_name('absent'))
                        f.save_registry()
                    elif mutation == 'declaration':
                        declaration['max_read_age_seconds'] = 120
                        (b / 'hub.connection.yaml').write_text(yaml.safe_dump(declaration))
                    elif mutation == 'root':
                        old = b.with_name('old-b'); b.rename(old); shutil.copytree(old, b)
                    else:
                        p = f.hub / 'data/registry/external_projects.yaml'
                        data = yaml.safe_load(p.read_text()); data['security_policy'] = {'revision': 2}
                        p.write_text(yaml.safe_dump(data))
                    current = f.resolver()
                    self.assertTrue(ledger.rebuild(current_authority=current.authority)['projects']['b']['authority_drift'])
                    with self.assertRaises(RequestConflictError):
                        refresh(ledger, current, 'open', ['b'])
                finally:
                    f.close()

    def test_legacy_is_readable_but_cannot_upgrade_its_authority(self):
        r = self.f.resolver()
        value = r.refresh('b')
        value.pop('binding_hash', None)
        value['authority'] = {k: value['authority'][k] for k in ('registry_hash', 'schema_hash', 'adapter_version')}
        value['update_key'] = update_key(value)
        validate_result(value)
        self.ledger.begin_request('legacy', ['b'], value['authority'])
        self.ledger.append_project_result('legacy', 'b', value)
        self.ledger.finish_request('legacy')
        self.assertEqual(self.ledger.history()['results'][0]['result'], value)
        self.assertTrue(self.ledger.rebuild(current_authority=r.authority)['projects']['b']['authority_drift'])
        bad = copy.deepcopy(r.authority); bad['binding_version'] = '999'
        with self.assertRaises(RecordError):
            validate_authority(bad)

    def test_existing_resolver_cannot_publish_changed_physical_identity(self):
        import shutil
        for mutation in ('root', 'declaration'):
            with self.subTest(mutation=mutation):
                f = Fixture()
                try:
                    root, declaration = f.add('b')
                    resolver = f.resolver()
                    if mutation == 'root':
                        original = root.with_name('original-b')
                        root.rename(original)
                        shutil.copytree(original, root)
                    else:
                        declaration['max_read_age_seconds'] = 123
                        (root / 'hub.connection.yaml').write_text(yaml.safe_dump(declaration))
                    result = resolver.refresh('b')
                    self.assertFalse(result['success'])
                    self.assertEqual('authority_drift', result['disposition'])
                    self.assertIsNone(result['business']['current_work']['status'])
                    self.assertEqual({}, result['field_provenance'])
                finally:
                    f.close()

    def test_ledger_only_authority_cannot_be_used_to_write_or_resume(self):
        from hub.connection_sources import SourceResolver
        observed = self.f.resolver()
        self.ledger.begin_request('observed-open', ['b'], observed.authority)
        query = SourceResolver(self.f.hub, observe_sources=False)
        self.assertEqual({}, query.authority['project_bindings'])
        for ident in ('new-unobserved', 'observed-open'):
            with self.assertRaises(RequestConflictError):
                self.ledger.begin_request(ident, ['b'], query.authority)
        with self.assertRaises(RecordError):
            query.refresh('b')

    def test_binding_and_update_key_stay_stable_for_other_project_changes(self):
        first = self.f.resolver().refresh('b')
        self.f.projects[0]['enabled'] = False; self.f.save_registry()
        second = self.f.resolver().refresh('b')
        self.assertEqual(first['binding_hash'], second['binding_hash'])
        self.assertEqual(first['update_key'], second['update_key'])
        tampered = copy.deepcopy(second); tampered['binding_hash'] = '0' * 64
        tampered['update_key'] = update_key(tampered)
        with self.assertRaises(RecordError):
            validate_result(tampered)

    def test_reverse_direction_and_shared_validator_change(self):
        from hub.connection_records import record_schema
        before = self.f.resolver()
        refresh(self.ledger, before, 'saved', ['a', 'b'])
        self.f.projects[1]['connection_read_allowed'] = False
        self.f.save_registry()
        current = self.f.resolver()
        view = self.ledger.rebuild(current_authority=current.authority)['projects']
        self.assertFalse(view['a']['authority_drift'])
        self.assertTrue(view['b']['authority_drift'])
        schema = record_schema(); schema['shared_safety_revision'] = 'incompatible'
        with patch('hub.connection_sources.record_schema', return_value=schema):
            changed = self.f.resolver()
        self.assertTrue(self.ledger.rebuild(current_authority=changed.authority)['projects']['a']['authority_drift'])

    def test_binding_drift_during_source_read_is_rejected(self):
        r = self.f.resolver()
        fired = []
        def read(root, relative, **kwargs):
            value = _safe_relative_read(root, relative, **kwargs)
            if root == self.b and relative == 'STATE.yaml' and not fired:
                fired.append(True)
                self.f.projects[1]['connection_read_allowed'] = False
                self.f.save_registry()
            return value
        with patch('hub.connection_sources._safe_relative_read', read):
            result = r.refresh('b')
        self.assertEqual(result['disposition'], 'authority_drift')
        self.assertFalse(result['success'])
        self.assertEqual(result['field_provenance'], {})

    def test_excluded_roots_are_never_resolved_while_binding_metadata_is_collected(self):
        from pathlib import Path
        original = Path.resolve
        excluded = '/never-probe-removed-root'
        self.f.projects.append({'id': 'removed', 'name': 'removed', 'root_path': excluded,
            'enabled': True, 'connection_read_allowed': True, 'local_presence': {'status': 'removed_local'}})
        self.f.save_registry()
        def guarded(path, *args, **kwargs):
            self.assertNotEqual(str(path), excluded)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'resolve', guarded):
            resolver = self.f.resolver()
            self.assertEqual(resolver.refresh('removed')['disposition'], 'removed_local')

    def test_incremental_inflight_unrelated_edit_uses_selected_scope(self):
        r = self.f.resolver()
        fired = []
        def read(root, relative, **kwargs):
            value = _safe_relative_read(root, relative, **kwargs)
            if root == self.b and not fired:
                fired.append(True)
                self.f.projects[0]['name'] = 'inflight A'
                self.f.save_registry()
            return value
        with patch('hub.connection_sources._safe_relative_read', read):
            outcome, code = incremental_request(self.f.hub, r, self.ledger, 'selected', plan={'eligible_project_ids': ['b']})
        self.assertEqual((outcome['appended_count'], code), (1, 0))

    def test_project_service_refresh_does_not_mark_b_stale_for_inflight_a(self):
        from hub.project_service import ProjectService
        fired = []
        def read(root, relative, **kwargs):
            value = _safe_relative_read(root, relative, **kwargs)
            if root == self.b and relative == 'STATE.yaml' and not fired:
                fired.append(True)
                self.f.projects[0]['name'] = 'A changed during facade refresh'
                self.f.save_registry()
            return value
        with patch('hub.connection_sources._safe_relative_read', read):
            outcome = ProjectService(self.f.hub).refresh({'request_id': 'facade', 'project_ids': ['b'],
                'expected_head': {'sequence': 0, 'hash': '0' * 64}})
        self.assertEqual(outcome['current_authority']['state'], 'matched')
        self.assertEqual(outcome['projection']['projects']['b']['freshness'], 'fresh')


def test_stable_identity_survives_checkout_rename_without_duplicate_registration():
    f = Fixture()
    try:
        root, declaration = f.add()
        renamed = root.with_name('renamed-checkout')
        root.rename(renamed)
        f.projects[0]['root_path'] = str(renamed)
        f.save_registry()
        result = f.resolver().refresh('a')
        assert result['success'] and result['project_id'] == 'a'
        assert len(f.resolver().projects) == 1
        assert result['business']['progress']['total'] == f.business['progress']['total']
    finally:
        f.close()


def test_duplicate_checkout_record_cannot_duplicate_stable_project_id():
    f = Fixture()
    try:
        f.add()
        clone = copy.deepcopy(f.projects[0])
        clone['root_path'] = str(f.base / 'second-checkout')
        f.projects.append(clone)
        f.save_registry()
        with pytest.raises(RecordError):
            f.resolver()
    finally:
        f.close()


def test_unregistered_candidate_is_not_discovered_or_read():
    f = Fixture()
    try:
        f.add()
        candidate = f.base / 'candidate'
        candidate.mkdir()
        (candidate / 'STATE.yaml').write_text('not registered\n')
        resolver = f.resolver()
        with patch('os.open', side_effect=AssertionError('candidate opened')):
            with pytest.raises(KeyError):
                resolver.refresh('candidate')
        assert set(resolver.projects) == {'a'}
    finally:
        f.close()


def test_source_role_cannot_widen_current_state_read_permission():
    f = Fixture()
    try:
        root, declaration = f.add()
        declaration['source_refs'][0]['role'] = 'exporter'
        (root / 'hub.connection.yaml').write_text(yaml.safe_dump(declaration))
        result = f.resolver().refresh('a')
        assert not result['success'] and result['disposition'] == 'invalid'
        assert result['sources'] == []
    finally:
        f.close()


def test_exporter_metadata_is_never_executed_and_source_is_unchanged():
    f = Fixture()
    try:
        root, declaration = f.add()
        declaration['metric_export'] = {'entry': 'export.py', 'snapshot': '.hub/status.json'}
        (root / 'hub.connection.yaml').write_text(yaml.safe_dump(declaration))
        before = (root / 'STATE.yaml').read_bytes()
        with patch('subprocess.run', side_effect=AssertionError('exporter executed')), \
                patch('subprocess.Popen', side_effect=AssertionError('exporter spawned')):
            result = f.resolver().refresh('a')
        assert result['success']
        assert before == (root / 'STATE.yaml').read_bytes()
        assert not (root / '.hub/status.json').exists()
    finally:
        f.close()
