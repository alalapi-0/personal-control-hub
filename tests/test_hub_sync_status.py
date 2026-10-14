"""Finite-scope status is a read-only full-ledger projection, not a page count."""
import hashlib
import json
import unittest
from unittest.mock import patch

import yaml

from connection_fixtures import Fixture
from hub.connection_records import validate_result
from hub.connection_refresh import RefreshLedger, refresh
from hub.project_service import ProjectService
from hub.service_contract import ServiceError


class SyncStatusTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)
        self.fixture.add('a')
        self.fixture.add('b')
        self.root = self.fixture.hub
        (self.root / 'data/connections').mkdir()
        self.scope = {'schema_version': '1.0', 'scope_id': 'fixture-scope',
                      'baseline_total': 2, 'project_ids': ['a', 'b'],
                      'excluded_project_ids': {}}
        self.write_scope()
        self.service = ProjectService(self.root)

    def write_scope(self):
        (self.root / 'data/connections/sync_scope.yaml').write_text(yaml.safe_dump(self.scope))

    def collect(self, request='one'):
        ledger = RefreshLedger(self.root, result_validator=validate_result)
        refresh(ledger, self.fixture.resolver(), request, ['a', 'b'])
        return ledger

    def test_unread_and_missing_member_preserve_frozen_denominator_without_probes(self):
        self.fixture.projects.pop()
        self.fixture.save_registry()
        with patch('hub.connection_sources.SourceResolver.refresh', side_effect=AssertionError('refresh called')):
            status = self.service.sync_status()
        self.assertEqual(status['counts']['scope'], 2)
        self.assertEqual(status['counts']['registered'], 1)
        self.assertEqual(status['counts']['eligible'], 1)
        self.assertEqual(status['counts']['unread'], 2)
        self.assertTrue(self.service.list_projects()['projects'][0]['declared']['summary_enabled'])
        self.assertIsNone(status['head'])
        self.assertFalse((self.root / 'data/connections/connection_refresh.sqlite3').exists())

    def test_current_failed_history_and_stale_are_distinct_no_get_writes(self):
        # Current wall-clock fixture times make the first read fresh.
        from hub.connection_sources import SourceResolver
        ledger = RefreshLedger(self.root, result_validator=validate_result)
        refresh(ledger, SourceResolver(self.root), 'one', ['a', 'b'])
        (self.fixture.base / 'b/STATE.yaml').write_text('invalid: [')
        refresh(ledger, SourceResolver(self.root), 'two', ['b'])
        path = self.root / 'data/connections/connection_refresh.sqlite3'
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        status = self.service.sync_status()
        self.assertEqual(status['counts']['complete'], 1)
        self.assertEqual(status['counts']['failed'], 1)
        self.assertEqual(status['counts']['stale'], 0)
        self.assertEqual(sum(status['failure_codes'].values()), 1)
        self.assertEqual(status['last_request']['request_id'], 'one')
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertNotIn(str(self.fixture.base), json.dumps(status))
        # A source rule change makes previous observations stale, without collecting.
        self.fixture.projects[0]['name'] = 'New name'
        self.fixture.save_registry()
        stale = self.service.sync_status()
        self.assertEqual(stale['counts']['stale'], 1)
        self.assertEqual(stale['counts']['failed'], 1)

    def test_partial_and_disabled_member_cannot_turn_into_complete_or_disappear(self):
        declaration = yaml.safe_load((self.fixture.base / 'a/hub.connection.yaml').read_text())
        for field in list(declaration['mapping']):
            del declaration['mapping'][field]
        (self.fixture.base / 'a/hub.connection.yaml').write_text(yaml.safe_dump(declaration))
        from hub.connection_sources import SourceResolver
        ledger = RefreshLedger(self.root, result_validator=validate_result)
        refresh(ledger, SourceResolver(self.root), 'one', ['a', 'b'])
        self.assertEqual(self.service.sync_status()['counts']['partial'], 1)
        self.fixture.projects[1]['enabled'] = False
        self.fixture.save_registry()
        status = self.service.sync_status()
        self.assertEqual(status['counts']['scope'], 2)
        self.assertEqual(status['counts']['eligible'], 1)
        self.assertEqual(status['counts']['ineligible'], 1)
        self.assertEqual(status['counts']['unread'], 1)
        self.assertNotIn('b', status['eligible_project_ids'])

    def test_absent_scope_is_unavailable_and_does_not_guess_registry_denominator(self):
        (self.root / 'data/connections/sync_scope.yaml').unlink()
        with self.assertRaises(ServiceError) as caught:
            self.service.sync_status()
        self.assertEqual(caught.exception.code, 'SYNC_SCOPE_UNAVAILABLE')

    def test_cli_keeps_committed_receipt_when_summary_cannot_be_verified(self):
        from hub.connection_sources import SourceResolver
        from hub.connection_sync import sync_request
        ledger = RefreshLedger(self.root, result_validator=validate_result)
        with patch.object(ProjectService, 'sync_status', side_effect=ServiceError('SYNC_AUTHORITY_CHANGED')):
            payload, code = sync_request(self.root, SourceResolver(self.root), ledger, 'committed')
        self.assertEqual(code, 2)
        self.assertEqual(payload['status'], 'FINISHED')
        self.assertEqual(payload['appended_count'], 2)
        self.assertEqual(payload['summary_error'], 'SYNC_AUTHORITY_CHANGED')
        self.assertIsNone(payload['summary'])
        self.assertEqual(ledger.history('committed')['requests'][0]['status'], 'FINISHED')


if __name__ == '__main__':
    unittest.main()
