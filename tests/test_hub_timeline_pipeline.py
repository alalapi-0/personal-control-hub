"""Synthetic timeline source facts and owner plans traverse real local pipelines."""
import copy
import hashlib
import http.client
import json
import os
import secrets
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import yaml

from connection_fixtures import Fixture
from test_hub_business_tasks import plan
from hub.connection_extensions import validate_extensions
from hub.connection_records import RecordError, validate_result
from hub.connection_refresh import RefreshLedger, refresh
from hub.connection_sources import SourceResolver
from hub.connection_sync import daily_request_id, scope_plan, sync_request
from hub.local_service import HubHTTPServer
from hub.management_store import ManagementStore
from hub.owner_auth import OwnerAuth
from hub.project_service import ProjectService
from hub.time_semantics import calendar_projection


def point(value, kind='date', zone=None):
    return {'kind': kind, 'value': value, 'timezone': zone}


def timeline():
    return {'schema_version': '1.0', 'project_id': 'business', 'plan_version': 'P1',
            'actual_start': point('2026-10-14'),
            'actual_end': point('2026-10-14T23:30:00-07:00', 'timestamp', 'Asia/Tokyo'),
            'milestones': [{'id': 'actual-review', 'label': 'Source review', 'at': None}],
            'task_dates': [{'task_id': 's', 'actual_start': None,
                            'actual_end': point('2026-10-20', zone='Asia/Tokyo'),
                            'milestones': [{'id': 'stage-check', 'label': 'Stage check',
                                            'at': point('2026-10-19')}]}]}


def overlay(**changes):
    value = {'priority': 7, 'start_date': '2026-11-01', 'end_date': '2026-11-09',
             'watch': True, 'order': 1, 'layout': {'x': 8, 'y': 16}, 'relations': None,
             'timezone': 'Asia/Tokyo',
             'milestones': [{'id': 'owner-review', 'label': 'Owner review', 'date': '2026-11-04'},
                            {'id': 'unknown-plan', 'label': 'Undated plan', 'date': None}]}
    value.update(changes)
    return value


class SyntheticDesignService:
    def snapshot(self):
        return {'available': True, 'reason': None, 'store_revision': 0,
                'store_classification': 'synthetic_fixture', 'facts': [], 'history': [],
                'effective': {}, 'queues': {}}


class TimelinePipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)
        self.root, self.declaration = self.fixture.add('business')
        self.other, _ = self.fixture.add('other')
        self.now = datetime.now(timezone.utc).isoformat()
        self.declaration['extensions'] = {'schema_version': '1.2',
            'fact_mapping': {key: {'source_ref': 'state', 'selector': {'path': [key]}}
                             for key in ('task_plan', 'task_timeline')},
            'unknown_fields': {'task_plan': 'No declared task plan',
                               'task_timeline': 'No declared actual dates'}}
        self.save_declaration()
        self.write_source()
        self.ledger = RefreshLedger(self.fixture.hub, result_validator=validate_result)
        self.service = ProjectService(self.fixture.hub)

    def save_declaration(self):
        (self.root / 'hub.connection.yaml').write_text(yaml.safe_dump(self.declaration, sort_keys=False))

    def write_source(self, dates=None, task_plan=None):
        state = copy.deepcopy(self.fixture.business)
        state['task_plan'] = plan() if task_plan is None else task_plan
        state['task_timeline'] = timeline() if dates is None else dates
        (self.root / 'STATE.yaml').write_text(yaml.safe_dump(state, sort_keys=False))
        return state

    def resolver(self, at=None):
        return SourceResolver(self.fixture.hub, clock=lambda: at or self.now)

    def publish(self, request):
        receipt = refresh(self.ledger, self.resolver(), request, ['business', 'other'])
        self.assertEqual({}, receipt['resolver_errors'])
        self.assertEqual('FINISHED', receipt['request']['status'])
        return self.service.get_project('business')

    def assert_unavailable(self, dto):
        self.assertFalse(dto['business']['timeline']['available'])
        self.assertIsNone(dto['business']['timeline']['value'])
        self.assertIsNone(dto['business']['timeline']['proof'])

    def test_literal_hash_selector_observation_value_ledger_and_dto(self):
        source = self.resolver().refresh('business')
        self.assertTrue(source['success'], source['errors'])
        validate_result(source)
        fact = source['extensions']['fact']['task_timeline']
        expected_proof = {'source_ref': 'state', 'selector': {'path': ['task_timeline']},
            'sha256': hashlib.sha256((self.root / 'STATE.yaml').read_bytes()).hexdigest(),
            'observed_at': self.now}
        self.assertEqual(expected_proof, fact['proof'])
        self.assertEqual(timeline(), fact['value'])
        dto = self.publish('literal-dates')
        self.assertEqual({'available': True, 'reason': None, 'role': 'current',
                          'value': timeline(), 'proof': expected_proof}, dto['business']['timeline'])
        self.assertEqual(source['extensions'], dto['operational']['latest_attempt']['extensions'])
        self.assertEqual(self.fixture.business, source['business'])
        self.assertEqual(2, dto['business']['task_plan']['progress']['denominator'])
        self.assertEqual(0, dto['business']['task_plan']['progress']['numerator'])
        reread = RefreshLedger(self.fixture.hub, result_validator=validate_result, read_only=True)
        self.assertEqual(self.ledger.head, reread.head)
        self.assertEqual(source['extensions'], reread.history()['results'][0]['result']['extensions'])
        self.assert_unavailable(self.service.get_project('other'))

    def test_literal_crosszone_end_only_and_unknown_dates_remain_uninvented(self):
        raw = timeline()
        raw['actual_start'] = None
        self.write_source(raw)
        view = self.publish('end-only')['business']['timeline']['value']
        self.assertEqual(raw, view)
        self.assertIsNone(view['actual_start'])
        self.assertIsNone(view['milestones'][0]['at'])
        self.assertIsNone(view['task_dates'][0]['actual_start'])
        projected = calendar_projection(view['actual_end'])
        self.assertEqual('2026-10-15', projected['calendar_date'])
        self.assertEqual('2026-10-15', projected['axis_date'])
        self.assertEqual('2026-10-14T23:30:00-07:00', view['actual_end']['value'])
        offset_literal = point('2026-10-15T00:30:00+09:00', 'timestamp', 'Asia/Tokyo')
        offset_projection = calendar_projection(offset_literal)
        self.assertEqual('2026-10-15', offset_projection['calendar_date'])
        self.assertEqual('2026-10-14', offset_projection['axis_date'])
        self.assertEqual('2026-10-15T00:30:00+09:00', offset_literal['value'])
        date = point('2026-10-14', zone='Asia/Tokyo')
        self.assertEqual('2026-10-14', calendar_projection(date)['axis_date'])
        self.assertIsNone(calendar_projection(date)['instant_utc'])
        for key in ('actual_start', 'actual_end'):
            raw[key] = None
        self.write_source(raw)
        self.assertEqual(raw, self.publish('all-unknown')['business']['timeline']['value'])

    def test_project_only_dates_require_null_plan_and_no_task_references(self):
        raw = timeline()
        raw.update(plan_version=None, task_dates=[])
        del self.declaration['extensions']['fact_mapping']['task_plan']
        del self.declaration['extensions']['unknown_fields']['task_plan']
        self.save_declaration()
        self.write_source(raw)
        self.assertEqual(raw, self.publish('project-only')['business']['timeline']['value'])
        for index, bad in enumerate((dict(raw, plan_version='P1'),
                                    dict(raw, task_dates=timeline()['task_dates']))):
            self.write_source(bad)
            current = self.publish('project-only-invalid-' + str(index))
            self.assertFalse(current['operational']['latest_attempt']['success'])
            self.assert_unavailable(current)

    def test_invalid_dates_versions_identity_and_task_refs_preserve_only_history(self):
        first = self.publish('good-history')['operational']['last_success']
        for index, mode in enumerate(('invalid-day', 'naive-timestamp', 'plan', 'task', 'identity', 'reverse')):
            bad = timeline()
            if mode == 'invalid-day': bad['actual_start']['value'] = '2026-02-30'
            elif mode == 'naive-timestamp': bad['actual_end']['value'] = '2026-10-15T10:00:00'
            elif mode == 'plan': bad['plan_version'] = 'P0'
            elif mode == 'task': bad['task_dates'][0]['task_id'] = 'old-stage'
            elif mode == 'identity': bad['project_id'] = 'other'
            else: bad.update(actual_start=point('2026-10-20'), actual_end=point('2026-10-10'))
            with self.subTest(mode=mode):
                self.write_source(bad)
                current = self.publish('invalid-dates-' + str(index))
                self.assertFalse(current['operational']['latest_attempt']['success'])
                self.assertNotIn('extensions', current['operational']['latest_attempt'])
                self.assert_unavailable(current)
                self.assertEqual(first, current['operational']['last_success'])
                self.assertTrue(self.service.get_project('other')['operational']['latest_attempt']['success'])

    def test_forged_proof_value_selector_and_old_plan_are_rejected(self):
        source = self.resolver().refresh('business')
        self.assertTrue(source['success'], source['errors'])
        context = {'state': {'sha256': source['sources'][0]['sha256'], 'observed_at': self.now,
                            'format': 'yaml', 'data': yaml.safe_load((self.root / 'STATE.yaml').read_text())}}
        for key, replacement in (('sha256', 'f' * 64), ('observed_at', '2020-01-01T00:00:00+00:00'),
                                  ('source_ref', 'other'), ('selector', {'heading': 'Dates', 'label': 'Date'})):
            with self.subTest(proof=key):
                bad = copy.deepcopy(source)
                bad['extensions']['fact']['task_timeline']['proof'][key] = replacement
                with self.assertRaises(RecordError): validate_result(bad)
        for mode in ('value', 'selector', 'old-plan'):
            with self.subTest(mode=mode):
                bad = copy.deepcopy(source['extensions'])
                if mode == 'value': bad['fact']['task_timeline']['value']['actual_start']['value'] = '2026-01-01'
                elif mode == 'selector': bad['fact']['task_timeline']['proof']['selector'] = {'path': ['task_plan']}
                else: bad['fact']['task_timeline']['value']['plan_version'] = 'P0'
                with self.assertRaises(RecordError): validate_extensions(bad, source_context=context)

    def test_legacy_extensions_and_explicit_unknown_never_infer_dates(self):
        for version in ('1.0', '1.1'):
            self.declaration['extensions'] = {'schema_version': version,
                'fact_mapping': {} if version == '1.0' else {'task_plan': {'source_ref': 'state', 'selector': {'path': ['task_plan']}}},
                'unknown_fields': {} if version == '1.0' else {'task_plan': 'No plan'}}
            self.save_declaration()
            current = self.publish('legacy-dates-' + version)
            self.assertTrue(current['operational']['latest_attempt']['success'])
            self.assertEqual('TIMELINE_NOT_DECLARED', current['business']['timeline']['reason'])
            self.assert_unavailable(current)
            self.assertIsNotNone(current['source']['observed_at'])
            self.assertIsNotNone(current['freshness']['source_mtime_age_seconds'])
            self.assertEqual('R2', current['business']['fields']['current_work']['round'])
        self.declaration['extensions'] = {'schema_version': '1.2',
            'fact_mapping': {'task_timeline': {'source_ref': 'state', 'selector': {'path': ['absent_timeline']}}},
            'unknown_fields': {'task_timeline': 'Actual dates not published'}}
        self.save_declaration()
        state = yaml.safe_load((self.root / 'STATE.yaml').read_text())
        state['absent_timeline'] = None
        (self.root / 'STATE.yaml').write_text(yaml.safe_dump(state))
        current = self.publish('explicit-unknown')
        self.assertTrue(current['operational']['latest_attempt']['success'])
        self.assertEqual('Actual dates not published', current['business']['timeline']['reason'])
        self.assert_unavailable(current)

    def test_current_failure_stale_and_authority_drift_hide_actual_dates(self):
        original = self.publish('fresh')['operational']['last_success']
        old = (datetime.fromisoformat(self.now) - timedelta(days=2)).isoformat()
        refresh(self.ledger, self.resolver(old), 'expired', ['business'])
        self.assert_unavailable(self.service.get_project('business'))
        self.assertEqual('SOURCE_STALE', self.service.get_project('business')['business']['timeline']['reason'])
        self.publish('fresh-again')
        self.fixture.projects[0]['name'] = 'Renamed authority'
        self.fixture.save_registry()
        drift = self.service.get_project('business')
        self.assertTrue(drift['freshness']['authority_drift'])
        self.assert_unavailable(drift)
        self.publish('authority-reconciled')
        (self.root / 'STATE.yaml').unlink()
        failed = self.publish('missing-source')
        self.assertFalse(failed['operational']['latest_attempt']['success'])
        self.assert_unavailable(failed)
        self.assertEqual(original['extensions'], failed['operational']['last_success']['extensions'])

    def test_read_denial_removed_and_cloud_never_probe_roots_or_promote_dates(self):
        self.publish('pre-withholding')
        real_open, real_stat, real_read = os.open, Path.stat, Path.read_bytes
        def reject(path):
            if isinstance(path, (str, bytes, os.PathLike)):
                value = Path(os.fsdecode(path)).absolute()
                if value == self.root or self.root in value.parents:
                    raise AssertionError('withheld external project root probe')
        def guarded_open(path, *args, **kwargs):
            reject(path); return real_open(path, *args, **kwargs)
        def guarded_stat(path, *args, **kwargs):
            reject(path); return real_stat(path, *args, **kwargs)
        def guarded_read(path):
            reject(path); return real_read(path)
        for mode in ('denied', 'removed', 'cloud'):
            row = self.fixture.projects[0]
            row.pop('local_presence', None); row.pop('project_type', None)
            row['connection_read_allowed'] = mode != 'denied'
            if mode == 'removed': row['local_presence'] = {'status': 'removed_local'}
            if mode == 'cloud': row['project_type'] = 'cloud'
            self.fixture.save_registry()
            with self.subTest(mode=mode), mock.patch('os.open', guarded_open), \
                 mock.patch.object(Path, 'stat', guarded_stat), mock.patch.object(Path, 'read_bytes', guarded_read):
                source = self.resolver().refresh('business')
                self.assertFalse(source['success'])
                self.assertNotIn('extensions', source)
                self.assert_unavailable(self.service.get_project('business'))
                self.assertFalse(self.service.get_management('business')['editable'])


class TimelineHTTPManagementTests(TimelinePipelineTests):
    # Reuse fixture setup/helpers only, not duplicate inherited acceptance cases.
    test_literal_hash_selector_observation_value_ledger_and_dto = None
    test_literal_crosszone_end_only_and_unknown_dates_remain_uninvented = None
    test_project_only_dates_require_null_plan_and_no_task_references = None
    test_invalid_dates_versions_identity_and_task_refs_preserve_only_history = None
    test_forged_proof_value_selector_and_old_plan_are_rejected = None
    test_legacy_extensions_and_explicit_unknown_never_infer_dates = None
    test_current_failure_stale_and_authority_drift_hide_actual_dates = None
    test_read_denial_removed_and_cloud_never_probe_roots_or_promote_dates = None

    def setUp(self):
        super().setUp()
        self.config = self.fixture.hub / 'data/connections/management_config.yaml'
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.base_config = yaml.safe_load((Path(__file__).resolve().parents[1] /
                                          'data/connections/management_config.yaml').read_text())
        self.config.write_text(yaml.safe_dump(self.base_config, sort_keys=False))
        (self.config.parent / 'sync_scope.yaml').write_text(yaml.safe_dump({
            'schema_version': '1.0', 'scope_id': 'synthetic-timeline', 'baseline_total': 2,
            'project_ids': ['business', 'other'], 'excluded_project_ids': {}}))
        self.publish('seed-http')
        self.proof = secrets.token_urlsafe(40)
        self.server = HubHTTPServer(self.service, SyntheticDesignService(),
                                   owner_auth=OwnerAuth(provider=lambda: self.proof))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.cookie = self.csrf = None

    def stop_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)
        self.assertFalse(self.thread.is_alive())

    def call(self, method='GET', path='/api/projects/business', body=None, headers=None):
        supplied = {'Cookie': self.cookie} if self.cookie else {}
        if method == 'POST':
            supplied.update({'Origin': self.server.origin, 'X-Hub-CSRF': self.csrf,
                             'Content-Type': 'application/json'})
        supplied.update(headers or {})
        supplied = {key: value for key, value in supplied.items() if value is not None}
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request(method, path, json.dumps(body) if body is not None else None, supplied)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally: connection.close()

    def login(self, owner=False):
        status, headers, payload = self.call(path='/api/session')
        self.assertEqual(200, status)
        self.cookie = headers['Set-Cookie'].split(';')[0]; self.csrf = payload['data']['csrf_token']
        if owner:
            status, headers, payload = self.call('POST', '/api/owner/login', {'token': self.proof})
            self.assertEqual(200, status)
            self.cookie = headers['Set-Cookie'].split(';')[0]; self.csrf = payload['data']['csrf_token']

    def command(self, request, value=None):
        state = self.service.get_management('business')
        return {'request_id': request, 'expected_revision': state['revision'],
                'expected_hash': state['config_hash'], 'project_id': 'business',
                'operation': 'set', 'value': overlay() if value is None else value}

    def test_real_http_owner_csrf_cas_preserves_both_domains_and_ledger(self):
        self.login()
        before = self.config.read_bytes(); source = (self.root / 'STATE.yaml').read_bytes()
        head = self.ledger.head
        command = self.command('owner-plan')
        self.assertEqual(401, self.call('POST', '/api/management/project', command)[0])
        self.login(owner=True)
        for headers in ({'X-Hub-CSRF': None}, {'X-Hub-CSRF': 'invalid'}, {'Origin': 'https://evil.invalid'}):
            self.assertEqual(403, self.call('POST', '/api/management/project', command, headers)[0])
        self.assertEqual(before, self.config.read_bytes())
        self.assertEqual(200, self.call('POST', '/api/management/project', command)[0])
        saved = self.config.read_bytes()
        status, _, payload = self.call('POST', '/api/management/project', dict(command, request_id='stale'))
        self.assertEqual(409, status)
        self.assertEqual('MANAGEMENT_CONFLICT', payload['error']['code'])
        self.assertEqual(saved, self.config.read_bytes())
        self.assertEqual(source, (self.root / 'STATE.yaml').read_bytes())
        self.assertEqual(head, self.ledger.head)
        status, _, payload = self.call()
        self.assertEqual(200, status)
        self.assertEqual(timeline(), payload['data']['business']['timeline']['value'])
        self.assertEqual(overlay(), payload['data']['management']['overlay'])

    def test_save_same_source_daily_sync_restart_preserve_literal_fact_and_management_history(self):
        self.login(owner=True)
        before = self.service.get_project('business')
        source_bytes = (self.root / 'STATE.yaml').read_bytes()
        external_preimage = {str(path): path.read_bytes() for root in (self.root, self.other)
                             for path in root.rglob('*') if path.is_file()}
        initial_head = self.ledger.head
        self.assertEqual(200, self.call('POST', '/api/management/project', self.command('first-plan'))[0])
        self.assertEqual(initial_head, self.ledger.head)
        second = overlay(priority=9, timezone='America/Los_Angeles', milestones=[])
        self.assertEqual(200, self.call('POST', '/api/management/project', self.command('second-plan', second))[0])
        config_bytes = self.config.read_bytes()
        self.assertEqual(initial_head, self.ledger.head)
        resolver = self.resolver()
        frozen = scope_plan(self.fixture.hub, resolver)
        self.assertEqual(['business', 'other'], frozen['eligible_project_ids'])
        request = daily_request_id(frozen['scope_id'], instant=datetime.fromisoformat(self.now))
        with mock.patch('subprocess.Popen', side_effect=AssertionError('agent/model process effect')):
            receipt, exit_code = sync_request(self.fixture.hub, resolver, self.ledger, request, plan=frozen)
        self.assertEqual(0, exit_code, receipt)
        self.assertEqual(2, receipt['appended_count'])
        self.assertEqual('FINISHED', receipt['status'])
        self.assertEqual(source_bytes, (self.root / 'STATE.yaml').read_bytes())
        self.assertEqual(config_bytes, self.config.read_bytes())
        external_after = {str(path): path.read_bytes() for root in (self.root, self.other)
                          for path in root.rglob('*') if path.is_file()}
        self.assertEqual(external_preimage, external_after)
        restarted = ProjectService(self.fixture.hub)
        after = restarted.get_project('business')
        self.assertEqual(before['business'], after['business'])
        self.assertEqual(before['source']['sources'][0]['sha256'], after['source']['sources'][0]['sha256'])
        self.assertEqual(before['operational']['latest_attempt']['extensions'], after['operational']['latest_attempt']['extensions'])
        self.assertEqual(second, restarted.get_management('business')['overlay'])
        stored = ManagementStore(self.fixture.hub).snapshot()
        self.assertEqual(2, stored['revision'])
        self.assertEqual(2, len(stored['history']))
        self.assertEqual('1.2', yaml.safe_load(config_bytes)['schema_version'])
        disk = yaml.safe_load(config_bytes)
        for key, value in self.base_config.items():
            if key not in {'schema_version', 'projects'}: self.assertEqual(value, disk[key], key)
        self.assertEqual(overlay(), stored['history'][0]['after'])
        self.assertEqual(overlay(), stored['history'][1]['before'])
        self.assertEqual(second, stored['history'][1]['after'])
        status, _, payload = self.call()
        self.assertEqual(200, status)
        self.assertEqual(second, payload['data']['management']['overlay'])
        self.assertEqual(timeline(), payload['data']['business']['timeline']['value'])


if __name__ == '__main__':
    unittest.main()
