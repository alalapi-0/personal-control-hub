"""Reconcile the complete frozen set and recovery against one real ledger."""
import contextlib
import hashlib
import io
import json
import unittest
from unittest import mock

import yaml

from connection_fixtures import Fixture
from hub.connection_cli import main
from hub.connection_records import validate_result
from hub.connection_refresh import RefreshLedger, refresh
from hub.connection_sources import now
from hub.project_service import ProjectService
from hub.service_contract import ServiceError


class SyncReportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.a, _ = self.fixture.add('a')
        self.b, _ = self.fixture.add('b')
        self.scope = {'schema_version': '1.0', 'scope_id': 'report-test', 'baseline_total': 2,
                      'project_ids': ['a', 'b'], 'excluded_project_ids': {}}
        (self.fixture.hub / 'data/connections').mkdir()
        self.save_scope()
        self.ledger = RefreshLedger(self.fixture.hub, result_validator=validate_result)
        self.resolver = self.fixture.resolver()
        self.resolver.clock = now
        self.service = ProjectService(self.fixture.hub)

    def tearDown(self):
        self.fixture.close()

    def save_scope(self):
        self.scope_path = self.fixture.hub / 'data/connections/sync_scope.yaml'
        self.scope_path.write_text(yaml.safe_dump(self.scope))

    def test_real_quality_list_same_head_and_exact_set_not_filtered(self):
        declaration = self.b / 'hub.connection.yaml'
        value = yaml.safe_load(declaration.read_text())
        value['mapping'] = {}
        declaration.write_text(yaml.safe_dump(value))
        refresh(self.ledger, self.resolver, 'partial', ['a', 'b'])
        with mock.patch.object(type(self.resolver), 'refresh', side_effect=AssertionError('source probe')):
            status = self.service.sync_status()
        self.assertEqual(self.ledger.head, status['head'])
        self.assertEqual({'a': 'complete', 'b': 'partial'},
                         {p['project_id']: p['category'] for p in status['projects']})
        self.assertEqual(1, status['counts']['complete'])
        self.assertEqual(1, status['counts']['partial'])
        for row in status['projects']:
            dto = self.service.get_project(row['project_id'])
            self.assertEqual(dto['provenance']['latest_result_hash'], row['result_sha256'])
            self.assertEqual(dto['business']['quality']['core_missing'], row['core_missing'])
            self.assertEqual(dto['source']['observed_at'], row['observed_at'])
            self.assertEqual(dto['source']['sources'][0]['sha256'], row['sources'][0]['sha256'])
        report = status['reconciliation']
        self.assertEqual(hashlib.sha256(self.scope_path.read_bytes()).hexdigest(), report['scope_sha256'])
        self.assertEqual(['a', 'b'], report['baseline_project_ids'])
        self.assertEqual([], report['missing_registration_ids'])
        self.assertEqual([], report['duplicate_ids'])
        self.service.list_projects(query={'q': 'a'})
        self.assertEqual(2, self.service.sync_status()['scope_total'])

    def test_registry_change_retains_original_set_missing_and_outside_history(self):
        refresh(self.ledger, self.resolver, 'original', ['a', 'b'])
        old_hash = self.resolver.authority['registry_hash']
        self.fixture.projects = [p for p in self.fixture.projects if p['id'] != 'b']
        self.fixture.add('outside')
        status = self.service.sync_status()
        report = status['reconciliation']
        self.assertEqual(2, status['scope_total'])
        self.assertEqual(['a', 'b'], report['baseline_project_ids'])
        self.assertEqual(['a'], report['current_eligible_project_ids'])
        self.assertEqual(['b'], report['missing_registration_ids'])
        self.assertEqual(['b'], report['withheld_baseline_ids'])
        self.assertEqual(['outside'], report['outside_baseline_ids'])
        old = status['recovery']['request_versions'][0]
        self.assertEqual(['a', 'b'], old['project_ids'])
        self.assertEqual(old_hash, old['registry_sha256'])
        self.assertTrue(old['authority_drift'])
        self.assertNotEqual(old_hash, report['registry_sha256'])
        self.assertEqual(1, status['counts']['stale'])
        self.assertEqual(1, status['counts']['unread'])

    def test_success_failure_and_open_same_id_recovery_are_distinct(self):
        refresh(self.ledger, self.resolver, 'success', ['a', 'b'])
        (self.b / 'STATE.yaml').write_text('partial: [')
        refresh(self.ledger, self.resolver, 'failure', ['a', 'b'])
        original = self.resolver.refresh
        def interrupt(project_id):
            if project_id == 'b':
                raise KeyboardInterrupt()
            return original(project_id)
        with mock.patch.object(self.resolver, 'refresh', interrupt):
            with self.assertRaises(KeyboardInterrupt):
                refresh(self.ledger, self.resolver, 'interrupted', ['a', 'b'])
        status = self.service.sync_status()
        recovery = status['recovery']
        self.assertEqual('success', recovery['last_success']['request_id'])
        self.assertEqual('failure', recovery['last_failure']['request_id'])
        self.assertEqual({'invalid': 1}, recovery['last_failure']['failure_codes'])
        opened = recovery['open_requests'][0]
        self.assertEqual('interrupted', opened['request_id'])
        self.assertEqual(['b'], opened['remaining_project_ids'])
        self.assertTrue(opened['can_resume'])
        self.assertIsNone(opened['finished_at'])
        (self.b / 'STATE.yaml').write_text(yaml.safe_dump(self.fixture.business))
        refresh(self.ledger, self.resolver, 'interrupted', ['a', 'b'])
        recovered = self.service.sync_status()['recovery']
        self.assertEqual([], recovered['open_requests'])
        self.assertEqual('interrupted', recovered['last_success']['request_id'])
        self.assertEqual('failure', recovered['last_failure']['request_id'])

    def test_authority_drift_keeps_open_identity_but_disables_resume(self):
        self.ledger.begin_request('open', ['a', 'b'], self.resolver.authority)
        self.fixture.projects[0]['name'] = 'Renamed'
        self.fixture.save_registry()
        opened = self.service.sync_status()['recovery']['open_requests'][0]
        self.assertEqual('open', opened['request_id'])
        self.assertTrue(opened['authority_drift'])
        self.assertFalse(opened['can_resume'])

    def test_cli_status_uses_shared_read_only_projection_without_changing_ledger(self):
        path = self.ledger.path
        before = path.read_bytes()
        output = io.StringIO()
        with mock.patch('hub.refresh_schedule.refresh_schedule_status', return_value={'available': False}), contextlib.redirect_stdout(output):
            code = main(['--root', str(self.fixture.hub), 'status'])
        self.assertEqual(0, code)
        actual = json.loads(output.getvalue())
        self.assertEqual(self.service.sync_status()['projects'], actual['projects'])
        self.assertEqual({'available': False}, actual['schedule'])
        self.assertEqual(before, path.read_bytes())


if __name__ == '__main__':
    unittest.main()
