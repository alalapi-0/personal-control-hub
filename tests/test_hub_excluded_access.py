"""Excluded synthetic identities cannot reach root access through any refresh route."""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import threading
import unittest
from unittest import mock

import yaml

from connection_fixtures import Fixture
from hub.connection_records import empty_business, validate_result
from hub.connection_refresh import RefreshLedger
from hub.connection_sources import SourceResolver
from hub.connection_sync import incremental_request, scheduled_request, scope_plan
from hub.local_service import HubHTTPServer
from hub.owner_auth import OwnerAuth
from hub.project_service import ProjectService
import test_hub_local_service as transport
from test_pch_project_api import UnavailableDesign


class ExcludedAccessTests(unittest.TestCase):
    call = transport.LocalHTTPTests.call
    stop = transport.LocalHTTPTests.stop

    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)
        self.excluded = {}
        cases = {
            'cloud-type': {'project_type': 'cloud'},
            'cloud-legacy-type': {'project_type': 'cloud_project'},
            'cloud-root-code': {'root_kind': 'cloud', 'project_type': 'code'},
            'cloud-root-only': {'root_kind': 'cloud'},
            'removed-status': {'current_state_status': 'removed_local'},
            'removed-presence': {'local_presence': {'status': 'removed_local'}},
            'explicit-denial': {'connection_read_allowed': False},
            'access-denial': {'access_profile': 'no_current_goal_access'},
            'disabled-default': {'enabled': False},
        }
        self.source_payloads = set()
        for ident, changes in cases.items():
            root, _ = self.fixture.add(ident)
            row = self.fixture.projects[-1]
            if ident != 'disabled-default':
                row['connection_read_allowed'] = True
            row.update(changes)
            # Distinct bytes allow a parser trap without blocking allowed input.
            state = yaml.safe_load((root / 'STATE.yaml').read_text())
            state['current_work']['objective'] = 'Excluded synthetic sentinel ' + ident
            (root / 'STATE.yaml').write_text(yaml.safe_dump(state))
            self.source_payloads.update((root.joinpath(name).read_bytes()
                                         for name in ('STATE.yaml', 'hub.connection.yaml')))
            self.excluded[ident] = root
        self.allowed, _ = self.fixture.add('allowed')
        self.fixture.save_registry()
        self.root = self.fixture.hub
        scope = self.root / 'data/connections/sync_scope.yaml'
        scope.parent.mkdir(parents=True)
        scope.write_text(yaml.safe_dump({'schema_version': '1.0', 'scope_id': 'excluded-access',
            'baseline_total': len(cases) + 1, 'project_ids': [*cases, 'allowed'], 'excluded_project_ids': {}}))
        self.resolver = SourceResolver(self.root)
        self.ledger = RefreshLedger(self.root, result_validator=validate_result)
        self.service = ProjectService(self.root)
        # Entirely synthetic proof; it stays in process memory and HTTP headers.
        self.owner_proof = secrets.token_urlsafe(40)
        self.server = HubHTTPServer(self.service, UnavailableDesign(),
                                   owner_auth=OwnerAuth(provider=lambda: self.owner_proof))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.thread.start()
        self.addCleanup(self.stop)
        self.cookie = self.csrf = None
        status, headers, data = self.call(path='/api/session', authenticated=False)
        self.assertEqual(status, 200)
        self.cookie = headers['Set-Cookie'].split(';', 1)[0]
        self.csrf = data['data']['csrf_token']
        status, headers, data = self.call('POST', '/api/owner/login', {'token': self.owner_proof})
        self.assertEqual(status, 200)
        self.cookie = headers['Set-Cookie'].split(';', 1)[0]
        self.csrf = data['data']['csrf_token']

    @contextmanager
    def no_excluded_access(self):
        """Active traps: root resolution, metadata, bytes, parsing and launch."""
        roots = tuple(map(str, self.excluded.values()))
        hits = []

        def check(value, operation, *, dir_fd=None):
            if isinstance(value, int) or not isinstance(value, (str, bytes, os.PathLike)):
                return
            text = os.fsdecode(value)
            if not os.path.isabs(text) and dir_fd is not None:
                text = os.path.join(os.readlink('/proc/self/fd/' + str(dir_fd)), text)
            text = os.path.abspath(text)
            if any(text == root or text.startswith(root + os.sep) for root in roots):
                hits.append(operation)
                raise AssertionError('excluded synthetic root reached ' + operation)

        with ExitStack() as stack:
            for name in ('expanduser', 'resolve', 'stat', 'open', 'read_bytes', 'read_text'):
                original = getattr(Path, name)
                def guarded(path, *args, _name=name, _original=original, **kwargs):
                    check(path, 'Path.' + _name)
                    return _original(path, *args, **kwargs)
                stack.enter_context(mock.patch.object(Path, name, guarded))
            original_open = os.open
            def open_fd(path, flags, *args, **kwargs):
                check(path, 'os.open', dir_fd=kwargs.get('dir_fd'))
                return original_open(path, flags, *args, **kwargs)
            stack.enter_context(mock.patch('os.open', open_fd))
            import hub.connection_sources as sources
            original_read = sources._safe_relative_read
            def read(root, *args, **kwargs):
                check(root, '_safe_relative_read')
                return original_read(root, *args, **kwargs)
            stack.enter_context(mock.patch.object(sources, '_safe_relative_read', read))
            for name in ('parse_source', 'parse_owned_hub_state'):
                original = getattr(sources, name)
                def parse(data, *args, _name=name, _original=original, **kwargs):
                    if isinstance(data, bytes) and data in self.source_payloads:
                        hits.append(_name)
                        raise AssertionError('excluded synthetic source parsed')
                    return _original(data, *args, **kwargs)
                stack.enter_context(mock.patch.object(sources, name, parse))
            stack.enter_context(mock.patch('subprocess.Popen', side_effect=AssertionError('project process launched')))
            yield hits
        self.assertEqual(hits, [])

    def test_direct_resolver_rejects_every_excluded_identity_before_root_access(self):
        with self.no_excluded_access():
            for ident in self.excluded:
                with self.subTest(project=ident):
                    result = self.resolver.refresh(ident)
                    self.assertFalse(result['success'])
                    self.assertEqual(result['disposition'], 'removed_local' if ident.startswith('removed-') else 'disabled')
                    self.assertEqual(result['business'], empty_business())
                    self.assertEqual(result['sources'], [])
                    self.assertEqual(result['field_provenance'], {})
            # The traps are selective: real eligible resolution still works.
            self.assertTrue(self.resolver.refresh('allowed')['success'])

    def test_scope_incremental_and_scheduled_refresh_keep_exclusions_in_denominator(self):
        with self.no_excluded_access():
            plan = scope_plan(self.root, self.resolver)
            self.assertEqual(plan['registered_count'], len(self.excluded) + 1)
            self.assertEqual(plan['scope_total'], len(self.excluded) + 1)
            self.assertEqual(plan['eligible_project_ids'], ['allowed'])
            self.assertEqual({row['project_id'] for row in plan['excluded']}, set(self.excluded))
            result, code = incremental_request(self.root, self.resolver, self.ledger, 'incremental-exclusions')
            self.assertEqual(code, 0)
            self.assertEqual(result['checked_count'], 1)
            self.assertEqual(result['appended_count'], 1)
            result, code = scheduled_request(self.root, self.resolver, self.ledger,
                instant=datetime(2026, 10, 16, 1, tzinfo=timezone.utc))
            self.assertEqual(code, 0)
            self.assertEqual(result['mode'], 'daily')
            history = self.ledger.history()
            self.assertEqual({row['project_id'] for row in history['results']}, {'allowed'})

    def test_http_detail_and_explicit_refresh_cannot_expand_excluded_root_access(self):
        with self.no_excluded_access():
            status, _, listing = self.call()
            self.assertEqual(status, 200)
            self.assertEqual(listing['data']['total'], len(self.excluded) + 1)
            head = self.ledger.head
            self.assertEqual(self.call(path='/api/projects/not-registered')[0], 404)
            self.assertEqual(self.call('POST', '/api/refresh', {'request_id': 'unknown-candidate',
                'project_ids': ['not-registered'], 'expected_head': head})[0], 404)
            self.assertEqual(self.ledger.head, head)
            for ident in self.excluded:
                with self.subTest(project=ident):
                    status, _, detail = self.call(path='/api/projects/' + ident)
                    self.assertEqual(status, 200)
                    self.assertEqual(detail['data']['project_id'], ident)
                    status, _, response = self.call('POST', '/api/refresh', {'request_id': 'http-' + ident,
                        'project_ids': [ident], 'expected_head': self.ledger.head})
                    self.assertEqual(status, 200, response)
                    row = self.ledger.history('http-' + ident)['results'][0]
                    self.assertFalse(row['success'])
                    self.assertEqual(row['result']['sources'], [])
                    self.assertEqual(row['result']['business'], empty_business())
            # Replays preserve one durable denial rather than probing again.
            before = self.ledger.history()
            status, _, response = self.call('POST', '/api/refresh', {'request_id': 'http-cloud-root-code',
                'project_ids': ['cloud-root-code'], 'expected_head': {'sequence': 0, 'hash': '0' * 64}})
            self.assertEqual(status, 200, response)
            self.assertEqual(before, self.ledger.history())


if __name__ == '__main__':
    unittest.main()
