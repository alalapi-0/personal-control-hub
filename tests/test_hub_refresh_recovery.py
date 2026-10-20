"""Real owned-process death and fresh CLI recovery of isolated source ledgers."""
from __future__ import annotations

import copy
import json
import os
import select
import signal
import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path

import yaml

from connection_fixtures import Fixture
from hub.connection_records import validate_result
from hub.connection_refresh import RefreshLedger

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
CLI = ROOT / 'scripts/hub_sync.py'

CHILD = r'''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hub.connection_sources import SourceResolver
from hub.connection_refresh import RefreshLedger, refresh
from hub.connection_records import validate_result
root, ready, mode = Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
def stop_here():
    os.write(ready, b'READY\n')
    sys.stdin.buffer.read(1)
    raise AssertionError('parent must terminate blocked child')
resolver = SourceResolver(root)
if mode == 'between':
    class PausedResolver:
        authority = resolver.authority
        validate_result = staticmethod(validate_result)
        def refresh(self, project_id):
            if project_id == 'b':
                stop_here()
            return resolver.refresh(project_id)
    ledger = RefreshLedger(root, result_validator=validate_result)
    refresh(ledger, PausedResolver(), 'kill-between', ['a', 'b'])
else:
    def fault(operation, context):
        if operation == 'append_project_result':
            stop_here()
    ledger = RefreshLedger(root, result_validator=validate_result, precommit_fault=fault)
    ledger.append_project_result('kill-precommit', 'a', resolver.refresh('a'))
'''

GUARDED_CLI = r'''
import os, runpy, sys
from pathlib import Path
script, root, request_id, *protected = sys.argv[1:]
protected = [os.path.abspath(p) for p in protected]
def guard(event, args):
    if event not in {'open', 'os.listdir', 'os.scandir'} or not args:
        return
    path = args[0]
    if not isinstance(path, (str, bytes, os.PathLike)):
        return
    path = os.path.abspath(os.fsdecode(path))
    if any(path == base or path.startswith(base + os.sep) for base in protected):
        raise AssertionError('finished replay attempted a project-root read')
sys.addaudithook(guard)
sys.argv = [script, '--root', root, 'sync', '--request-id', request_id]
runpy.run_path(script, run_name='__main__')
'''


class AutomaticRecoveryPolicyTests(unittest.TestCase):
    def setUp(self):
        from datetime import datetime, timezone
        self.f = Fixture(); self.addCleanup(self.f.close)
        self.a, _ = self.f.add('a'); self.b, _ = self.f.add('b')
        self.start = datetime(2026, 10, 20, 0, tzinfo=timezone.utc)
        scope = self.f.hub / 'data/connections/sync_scope.yaml'
        scope.parent.mkdir(parents=True)
        scope.write_text(yaml.safe_dump({'schema_version': '1.0', 'scope_id': 'automatic-fixture',
            'baseline_total': 2, 'project_ids': ['a', 'b'], 'excluded_project_ids': {}}))
        self.store = RefreshLedger(self.f.hub, result_validator=validate_result)

    def resolver(self, seconds):
        from datetime import timedelta
        from hub.connection_sources import SourceResolver
        instant = self.start + timedelta(seconds=seconds)
        return SourceResolver(self.f.hub, clock=lambda: instant.isoformat()), instant

    def run_at(self, seconds, events=None, paused=()):
        from hub.connection_sync import scheduled_request
        resolver, instant = self.resolver(seconds)
        return scheduled_request(self.f.hub, resolver, self.store, instant=instant,
            recovery_events=events, paused_project_ids=paused)

    def state(self, seconds, ident='a'):
        from hub.connection_sync import project_recovery
        _, instant = self.resolver(seconds)
        return project_recovery(self.store.history(), ident, instant)

    def event(self, seconds, kind='source_changed', ident='a'):
        from hub.connection_sync import _source_version
        from hub.connection_records import content_hash
        resolver, _ = self.resolver(seconds)
        # Explicit observation is durable proof; arbitrary event hashes are not.
        from hub.connection_sync import record_recovery_observation
        state = self.state(seconds, ident)
        record_recovery_observation(self.store, resolver, 'proof-' + ident + '-' + str(seconds), ident)
        proof = self.store.history()['observation_results'][-1]
        return {'kind': kind, 'failure_ref': state['failure_ref'],
            'source_version': _source_version(proof['result']),
            'binding_hash': resolver.authority['project_bindings'][ident],
            'evidence_hash': proof['result_hash'], 'proof_ref': proof['result_ref']}

    def test_missing_registered_root_isolated_and_actual_root_restoration_bound(self):
        from hub.connection_sync import scheduled_request
        from hub.project_service import project_snapshot
        self.f.projects[0].pop('root_path')
        self.f.save_registry()
        resolver, instant = self.resolver(0)
        read = resolver.refresh
        with mock.patch.object(resolver, 'refresh', wraps=read) as calls:
            initial, code = scheduled_request(self.f.hub, resolver, self.store, instant=instant)
        self.assertEqual(['a', 'b'], [call.args[0] for call in calls.call_args_list])
        self.assertEqual((2, 2, 2), (initial['scope_total'], initial['appended_count'], code))
        self.assertEqual(['a', 'b'], initial['scope_project_ids'])
        original = self.store.history()
        rows = {row['project_id']: row for row in original['results']}
        self.assertFalse(rows['a']['success'])
        self.assertEqual('invalid', rows['a']['result']['disposition'])
        self.assertEqual([], rows['a']['result']['sources'])
        self.assertTrue(rows['b']['success'])
        failure = self.state(0)
        self.assertEqual(1, failure['failure_count'])
        self.assertIsNone(failure['source_version'])
        self.assertRegex(failure['method_version'], r'^[0-9a-f]{64}$')
        self.assertRegex(failure['substantive_binding'], r'^[0-9a-f]{64}$')
        self.assertTrue(failure['recovery_condition'])
        self.assertEqual([], original['observation_results'])
        for second in (29, 30):
            resolver, instant = self.resolver(second)
            with mock.patch.object(resolver, 'refresh', side_effect=AssertionError('unchanged missing-root reread')):
                replay, _ = scheduled_request(self.f.hub, resolver, self.store, instant=instant)
            self.assertEqual(0, replay['appended_count'])
            self.assertEqual(original, self.store.history())
        # Restore the actual authorized fixture root, without guessing a route.
        self.f.projects[0]['root_path'] = str(self.a)
        self.f.save_registry()
        resolver, instant = self.resolver(29)
        with mock.patch.object(resolver, 'refresh', side_effect=AssertionError('root restoration before deadline')):
            waiting, _ = scheduled_request(self.f.hub, resolver, self.store, instant=instant)
        self.assertEqual(0, waiting['appended_count'])
        resolver, instant = self.resolver(30)
        with mock.patch.object(resolver, 'refresh', wraps=resolver.refresh) as calls:
            recovered, _ = scheduled_request(self.f.hub, resolver, self.store, instant=instant)
        self.assertEqual(['a'], [call.args[0] for call in calls.call_args_list])
        self.assertEqual(1, recovered['appended_count'])
        self.assertEqual(2, recovered['scope_total'])
        self.assertEqual('ready', self.state(30)['state'])
        self.assertEqual('ready', self.state(30, 'b')['state'])
        after = self.store.history()
        self.assertEqual(rows['b'], next(row for row in after['results'] if row['project_id'] == 'b'))
        self.assertEqual(original['results'], self.store.history(initial['request_id'])['results'])
        self.assertEqual([], after['observation_results'])
        snapshot = project_snapshot(resolver.registry, resolver.authority, self.store,
                                    observed_at=instant.isoformat())
        projected = {row['project_id']: row for row in snapshot['projects']}
        self.assertEqual('fresh', projected['b']['freshness']['state'])
        self.assertFalse(projected['b']['freshness']['authority_drift'])
        self.assertEqual(rows['b']['result'], projected['b']['latest_attempt'])

    def test_proof_audit_is_separate_from_business_projection_until_admitted(self):
        from hub.connection_sources import SourceResolver
        from hub.connection_sync import record_recovery_observation, scheduled_request, recovery_summary
        from hub.project_service import project_snapshot
        (self.a / 'STATE.yaml').write_text('broken: [a')
        (self.b / 'STATE.yaml').write_text('broken: [b')
        initial, _ = self.run_at(0)
        failed = self.store.history()['results']
        (self.a / 'STATE.yaml').write_text(yaml.safe_dump(self.f.business))
        event = self.event(30)
        resolver, instant = self.resolver(30)
        b_event = self.event(30, ident='b')
        before = self.store.history()
        proof = before['observation_results'][0]
        self.assertTrue(proof['success'])
        import sqlite3
        def raw_proof():
            from contextlib import closing
            with closing(sqlite3.connect(self.store.path)) as connection:
                return connection.execute('SELECT result_json,result_hash FROM refresh_results WHERE request_id=?', ('proof-a-30',)).fetchone()
        original_bytes = raw_proof()
        self.assertEqual(failed, before['results'])
        self.assertIsNone(recovery_summary(before, {'a', 'b'}, instant=instant)['last_success'])
        self.assertEqual(['proof-a-30', 'proof-b-30'], [row['request_id'] for row in before['observation_requests']])
        self.assertEqual([initial['request_id']], [row['request_id'] for row in before['requests']])
        self.assertEqual(1, self.state(30)['failure_count'])
        projected = {row['project_id']: row for row in project_snapshot(
            resolver.registry, resolver.authority, self.store, observed_at=instant.isoformat())['projects']}
        rebuilt = self.store.rebuild(current_authority=resolver.authority)['projects']
        for ident in ('a', 'b'):
            self.assertFalse(projected[ident]['latest_attempt']['success'])
            self.assertIsNone(projected[ident]['last_success'])
            self.assertEqual('unknown', projected[ident]['freshness']['state'])
            self.assertFalse(rebuilt[ident]['latest_attempt']['success'])
            self.assertIsNone(rebuilt[ident]['last_success'])
            self.assertEqual('unknown', rebuilt[ident]['freshness'])
        with mock.patch.object(resolver, 'refresh', side_effect=AssertionError('proof replay business read')):
            record_recovery_observation(self.store, resolver, 'proof-a-30', 'a')
        self.assertEqual(before, self.store.history())
        self.store = RefreshLedger(self.f.hub, result_validator=validate_result)
        self.assertEqual(proof, self.store.history()['observation_results'][0])
        self.assertEqual(original_bytes, raw_proof())
        with mock.patch.object(resolver, 'refresh', side_effect=AssertionError('restart proof replay read')):
            record_recovery_observation(self.store, resolver, 'proof-a-30', 'a')
        self.assertEqual(before, self.store.history())
        # Every mismatch is checked with the real class method identity intact;
        # only this resolver instance denies business reads.
        bad_events = [dict(event, proof_ref='forged'), dict(event, proof_ref=b_event['proof_ref'], evidence_hash=b_event['evidence_hash'], source_version=b_event['source_version']),
                      dict(event, source_version='f' * 64), dict(event, binding_hash='e' * 64),
                      dict(event, evidence_hash='d' * 64), dict(event, failure_ref='c' * 64)]
        with mock.patch.object(resolver, 'refresh', side_effect=AssertionError('unproven business read')):
            for bad in bad_events:
                with self.subTest(bad=bad):
                    outcome, _ = scheduled_request(self.f.hub, resolver, self.store, instant=instant,
                                                   recovery_events={'a': bad})
                    self.assertEqual(0, outcome['appended_count'])
        self.assertEqual(before, self.store.history())
        # A stale timestamp or a genuinely different loaded parser method also
        # cannot turn a previously recorded observation into new admission.
        from datetime import timedelta
        with mock.patch.object(resolver, 'refresh', side_effect=AssertionError('future proof read')):
            outcome, _ = scheduled_request(self.f.hub, resolver, self.store,
                instant=instant-timedelta(seconds=1), recovery_events={'a': event})
            self.assertEqual(0, outcome['appended_count'])
        from hub import connection_sources
        parser = connection_sources.parse_source
        def changed_parser(*args, **kwargs): return parser(*args, **kwargs)
        with mock.patch('hub.connection_sources.parse_source', changed_parser), mock.patch.object(
                resolver, 'refresh', side_effect=AssertionError('method mismatch read')):
            outcome, _ = scheduled_request(self.f.hub, resolver, self.store, instant=instant,
                                           recovery_events={'a': event})
            self.assertEqual(0, outcome['appended_count'])
        outcome, _ = scheduled_request(self.f.hub, resolver, self.store, instant=instant,
                                       recovery_events={'a': event})
        self.assertEqual(1, outcome['appended_count'])
        self.assertEqual('ready', self.state(30)['state'])
        self.assertEqual(1, self.state(30, 'b')['failure_count'])
        after = {row['project_id']: row for row in project_snapshot(
            resolver.registry, resolver.authority, self.store, observed_at=instant.isoformat())['projects']}
        rebuilt = self.store.rebuild(current_authority=resolver.authority)['projects']
        self.assertTrue(after['a']['latest_attempt']['success'])
        self.assertEqual('fresh', after['a']['freshness']['state'])
        self.assertEqual('fresh', rebuilt['a']['freshness'])
        self.assertFalse(after['b']['latest_attempt']['success'])
        self.assertEqual(proof, self.store.history()['observation_results'][0])
        self.assertEqual(failed, self.store.history(initial['request_id'])['results'])
        self.assertEqual(original_bytes, raw_proof())
        with mock.patch.object(resolver, 'refresh', side_effect=AssertionError('consumed proof read')):
            replay, _ = scheduled_request(self.f.hub, resolver, self.store, instant=instant,
                                          recovery_events={'a': event})
            self.assertEqual(0, replay['appended_count'])
        # Even a newly anchored failure cannot reuse a consumed/older proof.
        (self.a / 'STATE.yaml').write_text('broken: [later')
        from hub.connection_refresh import refresh
        new_resolver, _ = self.resolver(60)
        refresh(self.store, new_resolver, 'later-explicit-failure', ['a'])
        old_proof = dict(event, failure_ref=self.state(90)['failure_ref'])
        new_resolver, later = self.resolver(90)
        unchanged = self.store.history()
        with mock.patch.object(new_resolver, 'refresh', side_effect=AssertionError('consumed proof re-anchored read')):
            denied, _ = scheduled_request(self.f.hub, new_resolver, self.store, instant=later,
                                          recovery_events={'a': old_proof})
            self.assertEqual(0, denied['appended_count'])
        self.assertEqual(unchanged, self.store.history())
        self.assertEqual(original_bytes, raw_proof())

    def test_observed_authority_drift_proof_rejects_before_business_read(self):
        from hub.connection_sync import scheduled_request
        (self.a / 'STATE.yaml').write_text('broken: [same')
        self.run_at(0)
        (self.a / 'STATE.yaml').write_text(yaml.safe_dump(self.f.business))
        event = self.event(30)
        os.utime(self.a / 'hub.connection.yaml', None)
        resolver, instant = self.resolver(30)
        self.assertNotEqual(event['binding_hash'], resolver.authority['project_bindings']['a'])
        # Supplying the new capsule hash cannot disguise the stale proof capsule.
        event['binding_hash'] = resolver.authority['project_bindings']['a']
        before = self.store.history()
        with mock.patch.object(resolver, 'refresh', side_effect=AssertionError('stale authority proof read')):
            outcome, _ = scheduled_request(self.f.hub, resolver, self.store, instant=instant,
                                           recovery_events={'a': event})
        self.assertEqual(0, outcome['appended_count'])
        self.assertEqual(before, self.store.history())
        self.assertEqual(1, self.state(30)['failure_count'])

    def test_timed_progression_cap_event_recovery_and_immutable_replay(self):
        (self.a / 'STATE.yaml').write_text('broken: [zero')
        initial, code = self.run_at(0)
        self.assertEqual((2, 2), (initial['appended_count'], code))
        initial_results = self.store.history(initial['request_id'])['results']
        self.assertEqual(30, self.state(0)['backoff_seconds'])
        from hub.connection_sources import SourceResolver
        with mock.patch.object(SourceResolver, 'refresh', side_effect=AssertionError('deferred business read')):
            deferred, _ = self.run_at(29)
        self.assertEqual(0, deferred['appended_count'])
        for seconds, word, delay in ((30, 'one', 120), (150, 'two', 600), (750, 'three', 600)):
            (self.a / 'STATE.yaml').write_text('broken: [' + word)
            event = self.event(seconds)
            result, _ = self.run_at(seconds, {'a': event})
            self.assertEqual(1, result['appended_count'])
            self.assertEqual(delay, self.state(seconds)['backoff_seconds'])
        with mock.patch.object(SourceResolver, 'refresh', side_effect=AssertionError('cap business read')):
            capped, _ = self.run_at(900)
        self.assertEqual('event_wait', capped['recovery']['projects']['a']['state'])
        self.assertIsNone(capped['recovery']['projects']['a']['next_retry_at'])
        # Another project's failure is independent of A's eventual success.
        (self.b / 'STATE.yaml').write_text('broken: [other')
        resolver, _ = self.resolver(900)
        from hub.connection_refresh import refresh
        refresh(self.store, resolver, 'manual-b-failure', ['b'])
        (self.a / 'STATE.yaml').write_text(yaml.safe_dump(self.f.business))
        event = self.event(901)
        recovered, _ = self.run_at(901, {'a': event})
        self.assertEqual('ready', recovered['recovery']['projects']['a']['state'])
        self.assertEqual(1, recovered['recovery']['projects']['b']['failure_count'])
        self.assertEqual(initial_results, self.store.history(initial['request_id'])['results'])
        with mock.patch.object(SourceResolver, 'refresh', side_effect=AssertionError('duplicate event read')):
            replay, code = self.run_at(902, {'a': event})
        self.assertEqual(0, replay['appended_count']); self.assertEqual(2, code)
        self.store = RefreshLedger(self.f.hub, result_validator=validate_result)
        self.assertEqual('ready', self.state(902)['state'])
        self.assertEqual(1, self.state(902, 'b')['failure_count'])

    def test_one_transient_two_no_progress_and_diagnosed_actual_version(self):
        from hub.connection_sources import _safe_relative_read, SourceResolver
        reads = []
        def transient_io(root, relative, **kwargs):
            value = _safe_relative_read(root, relative, **kwargs)
            if root == self.a and relative == 'STATE.yaml':
                reads.append(relative)
                if len(reads) == 2: raise PermissionError('injected transient second read')
            return value
        with mock.patch('hub.connection_sources._safe_relative_read', transient_io):
            self.run_at(0)
        with mock.patch('hub.connection_sources._safe_relative_read', transient_io):
            event = self.event(30, 'transient')
        unproven = dict(event); unproven['proof_ref'] = 'unproven'
        with mock.patch.object(SourceResolver, 'refresh', side_effect=AssertionError('unproven transient')):
            rejected, _ = self.run_at(30, {'a': unproven})
        self.assertEqual(0, rejected['appended_count'])
        reads.clear()
        with mock.patch('hub.connection_sources._safe_relative_read', transient_io):
            retried, _ = self.run_at(30, {'a': event})
        self.assertEqual(1, retried['appended_count'])  # unchanged failure still durable
        state = self.state(30)
        self.assertEqual(2, state['failure_count'])
        self.assertEqual(1, state['transient_retries'])
        self.assertEqual('diagnosis_required', state['state'])
        from hub.connection_sources import SourceResolver
        second = self.event(150, 'transient')
        with mock.patch.object(SourceResolver, 'refresh', side_effect=AssertionError('blind retry')):
            self.run_at(150, {'a': second})
        (self.a / 'STATE.yaml').write_text(yaml.safe_dump(self.f.business))
        ordinary = self.event(151)
        with mock.patch.object(SourceResolver, 'refresh', side_effect=AssertionError('diagnosis required')):
            self.run_at(151, {'a': ordinary})
        diagnosed = self.event(152, 'diagnosed')
        result, _ = self.run_at(152, {'a': diagnosed})
        self.assertEqual(1, result['appended_count'])
        self.assertEqual('ready', self.state(152)['state'])

    def test_offline_isolation_unrelated_registry_and_no_business_source_read(self):
        self.a.rename(self.a.with_name('offline-a'))
        self.run_at(0)
        original = self.state(0)
        self.f.add('unrelated-c')
        self.f.projects[-1]['name'] = 'Unrelated version'; self.f.save_registry()
        from hub.connection_sources import SourceResolver
        with mock.patch.object(SourceResolver, 'refresh', side_effect=AssertionError('unrelated event read')):
            self.run_at(30, {'unrelated-c': {'ignored': True}})
        self.assertEqual(original['failure_ref'], self.state(30)['failure_ref'])
        # A later cadence admits healthy B while A is waiting for relevant evidence.
        data = dict(self.f.business); data['current_work'] = dict(data['current_work'], objective='Ready B changes')
        (self.b / 'STATE.yaml').write_text(yaml.safe_dump(data))
        resolver, instant = self.resolver(43200)
        original_refresh = resolver.refresh
        calls = []
        def read(ident):
            calls.append(ident); self.assertEqual('b', ident); return original_refresh(ident)
        from hub.connection_sync import scheduled_request
        with mock.patch.object(resolver, 'refresh', read):
            result, code = scheduled_request(self.f.hub, resolver, self.store, instant=instant)
        self.assertEqual(['b'], calls); self.assertEqual(1, result['appended_count']); self.assertEqual(2, code)
        self.assertIsNone(result['recovery']['projects']['a']['source_version'])
        self.assertEqual(1, result['recovery']['projects']['a']['failure_count'])

    def test_actual_offline_root_recovery_is_admitted_without_an_event_transport(self):
        old = self.a.with_name('offline-a'); self.a.rename(old)
        initial, _ = self.run_at(0)
        before = self.store.history(initial['request_id'])['results']
        old.rename(self.a)
        recovered, _ = self.run_at(30)
        self.assertEqual(1, recovered['appended_count'])
        self.assertEqual('ready', recovered['recovery']['projects']['a']['state'])
        self.assertEqual(before, self.store.history(initial['request_id'])['results'])

    def test_mismatched_and_duplicate_recovery_events_do_not_publish_or_reset(self):
        (self.a / 'STATE.yaml').write_text('broken: [same')
        self.run_at(0)
        event = self.event(30)
        event['source_version'] = 'f' * 64  # arbitrary changed hash is no evidence
        from hub.connection_sources import SourceResolver
        with mock.patch.object(SourceResolver, 'refresh', side_effect=AssertionError('unproven event read')):
            result, _ = self.run_at(30, {'a': event})
            repeated, _ = self.run_at(1000, {'a': event})
        self.assertEqual(0, result['appended_count'])
        self.assertEqual(0, repeated['appended_count'])
        self.assertEqual(1, self.state(1000)['failure_count'])

    def test_deadline_and_clock_boundaries_are_not_retry_grants(self):
        from hub.refresh_schedule import retry_readiness
        from datetime import datetime
        with self.assertRaises(ValueError): retry_readiness([], datetime(2026, 1, 1))
        (self.a / 'STATE.yaml').write_text('broken: [same')
        self.run_at(0)
        self.assertEqual('retry_wait', self.state(29)['state'])
        self.assertEqual('retry_due', self.state(30)['state'])
        self.assertTrue(self.state(-1)['clock_regressed'])
        from hub.connection_sources import SourceResolver
        with mock.patch.object(SourceResolver, 'refresh', side_effect=AssertionError('deadline blind read')):
            result, _ = self.run_at(30)
        self.assertEqual(0, result['appended_count'])

    def test_pause_permissions_and_removed_exclusions(self):
        from hub.connection_sync import scheduled_request
        resolver, instant = self.resolver(0)
        original = resolver.refresh; calls = []
        def read(ident): calls.append(ident); return original(ident)
        with mock.patch.object(resolver, 'refresh', read):
            value, _ = scheduled_request(self.f.hub, resolver, self.store, instant=instant, paused_project_ids=['a'])
        self.assertEqual(['b'], calls)
        self.assertEqual('paused', value['deferred_projects']['a']['state'])
        for count, change in enumerate(({'connection_read_allowed': False},
                {'local_presence': {'status': 'removed_local'}}, {'project_type': 'cloud'}, {'enabled': False}), 1):
            self.f.projects[0] = {'id': 'a', 'name': 'a', 'root_path': str(self.a),
                'enabled': True, 'current_state_paths': ['STATE.yaml'], **change}
            self.f.save_registry()
            resolver, instant = self.resolver(count * 43200)
            original = resolver.refresh; calls = []
            with mock.patch.object(resolver, 'refresh', read):
                scheduled_request(self.f.hub, resolver, self.store, instant=instant)
            self.assertEqual(['b'], calls)

    def test_deferred_boundary_no_business_body_reads_and_bad_event_peer_isolation(self):
        from hub.connection_sources import _safe_relative_read
        from hub.connection_sync import scheduled_request
        (self.a / 'STATE.yaml').write_text('broken: [same')
        self.run_at(0)
        resolver, instant = self.resolver(43200)
        observed_paths = []
        def checked(root, relative, **kwargs):
            observed_paths.append((str(root), relative))
            if root == self.a and relative != 'hub.connection.yaml':
                raise AssertionError('deferred A business body read')
            return _safe_relative_read(root, relative, **kwargs)
        with mock.patch('hub.connection_sources._safe_relative_read', checked), mock.patch('hub.connection_sync._safe_relative_read', checked):
            result, _ = scheduled_request(self.f.hub, resolver, self.store, instant=instant,
                recovery_events={'a': {'kind': 'diagnosed', 'source_version': 'f' * 64}})
        self.assertEqual(1, result['appended_count'])
        self.assertEqual('invalid_recovery_event', result['deferred_projects']['a']['event_rejection'])
        self.assertTrue(any(root == str(self.b) and path == 'STATE.yaml' for root, path in observed_paths))
        self.assertFalse(any(root == str(self.a) and path == 'STATE.yaml' for root, path in observed_paths))

    def test_request_metadata_precedes_first_read_and_changed_open_method_rejects(self):
        from hub.connection_sync import scheduled_request
        resolver, instant = self.resolver(0)
        read = resolver.refresh
        def stop_after_first(ident):
            begun = self.store.history()['events'][0]['payload']['attempt_metadata']
            self.assertEqual('1.0', begun['version'])
            self.assertIn(ident, begun['project_inputs'])
            if ident == 'b': raise KeyboardInterrupt()
            return read(ident)
        with mock.patch.object(resolver, 'refresh', stop_after_first):
            with self.assertRaises(KeyboardInterrupt):
                scheduled_request(self.f.hub, resolver, self.store, instant=instant)
        before = self.store.history()['results']
        from hub.connection_refresh import RequestConflictError
        with mock.patch('hub.connection_sync._method_version', return_value='f' * 64), mock.patch.object(resolver, 'refresh', side_effect=AssertionError('changed open method read')):
            with self.assertRaises(RequestConflictError):
                scheduled_request(self.f.hub, resolver, self.store, instant=instant)
        self.assertEqual(before, self.store.history()['results'])

    def test_declaration_touch_is_not_substantive_progress(self):
        (self.a / 'STATE.yaml').write_text('broken: [same')
        self.run_at(0)
        before = self.state(0)
        declaration = self.a / 'hub.connection.yaml'
        raw = declaration.read_bytes(); os.utime(declaration, None)
        resolver, _ = self.resolver(30)
        self.assertNotEqual(before['binding_hash'], resolver.authority['project_bindings']['a'])
        from hub.connection_sources import SourceResolver
        with mock.patch.object(SourceResolver, 'refresh', side_effect=AssertionError('touch read')):
            result, _ = self.run_at(30)
        self.assertEqual(0, result['appended_count'])
        self.assertEqual(raw, declaration.read_bytes())
        self.assertEqual(before['failure_signature'], self.state(30)['failure_signature'])

    def test_historical_method_and_helper_identity_are_durable(self):
        from hub.connection_sync import _loaded_method_version, project_recovery
        (self.a / 'STATE.yaml').write_text('broken: [same')
        self.run_at(0); before = self.state(0)['method_version']
        self.store = RefreshLedger(self.f.hub, result_validator=validate_result)
        with mock.patch('hub.connection_sync._method_version', return_value='f' * 64):
            self.assertEqual(before, self.state(1)['method_version'])
        original = _loaded_method_version()
        def changed_helper(*args, **kwargs): raise AssertionError('not called')
        with mock.patch('hub.connection_sources._safe_relative_read', changed_helper):
            self.assertNotEqual(original, _loaded_method_version())
        history = self.store.history()
        for row in history['results']: row['attempt_metadata'] = None; row['observation_metadata'] = None
        self.assertIsNone(project_recovery(history, 'a', self.start)['method_version'])

    def test_diagnosis_cannot_bypass_second_deadline_or_use_forged_hashes(self):
        from hub.connection_refresh import refresh
        from hub.connection_sync import _attempt_metadata, _observation
        (self.a / 'STATE.yaml').write_text('broken: [same')
        self.run_at(0)
        resolver, _ = self.resolver(30); metadata = _attempt_metadata(resolver, ['a'])
        refresh(self.store, resolver, 'explicit-second', ['a'], attempt_metadata=metadata,
            observation_factory=lambda pid, result: _observation(result, metadata['project_inputs'][pid]))
        self.assertEqual(2, self.state(31)['no_progress_count'])
        event = self.event(31, 'diagnosed')
        event['source_version'] = 'e' * 64; event['evidence_hash'] = 'd' * 64
        from hub.connection_sources import SourceResolver
        with mock.patch.object(SourceResolver, 'refresh', side_effect=AssertionError('forged read')):
            for second in (31, 150, 900, 1000):
                result, _ = self.run_at(second, {'a': event})
                self.assertEqual(0, result['appended_count'])
        self.assertEqual(2, self.state(1000)['no_progress_count'])
        self.assertIsNotNone(self.state(31)['next_retry_at'])


class ProcessRefreshRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)
        self.a, _ = self.fixture.add('a')
        self.b, _ = self.fixture.add('b')
        self.root = self.fixture.hub
        scope = self.root / 'data/connections/sync_scope.yaml'
        scope.parent.mkdir(parents=True)
        scope.write_text(yaml.safe_dump({'schema_version': '1.0', 'scope_id': 'isolated-recovery',
                                        'baseline_total': 2, 'project_ids': ['a', 'b'],
                                        'excluded_project_ids': {}}))

    def ledger(self):
        return RefreshLedger(self.root, result_validator=validate_result)

    @staticmethod
    def _collect(child):
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)

    def blocked_child(self, mode):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        try:
            child = subprocess.Popen([sys.executable, '-c', CHILD, str(SRC), str(self.root), str(write_fd), mode],
                                     pass_fds=(write_fd,), stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.fixture.base)
        finally:
            os.close(write_fd)
        self.addCleanup(self._collect, child)
        ready, _, _ = select.select([read_fd], [], [], 10)
        self.assertTrue(ready, 'owned child did not reach the exact bounded crash point')
        self.assertEqual(b'READY\n', os.read(read_fd, 64))
        self.assertIsNone(child.poll())
        return child

    def kill(self, child):
        child.kill()
        stdout, stderr = child.communicate(timeout=10)
        self.assertEqual(-signal.SIGKILL, child.returncode, (stdout, stderr))
        self.assertEqual(b'', stderr)

    def cli(self, request_id, *, no_project_reads=False):
        command = ([sys.executable, '-c', GUARDED_CLI, str(CLI), str(self.root), request_id, str(self.a), str(self.b)]
                   if no_project_reads else
                   [sys.executable, str(CLI), '--root', str(self.root), 'sync', '--request-id', request_id])
        result = subprocess.run(command, cwd=self.fixture.base, capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, (result.stdout, result.stderr))
        self.assertEqual('', result.stderr)
        return json.loads(result.stdout)

    def test_sigkill_after_one_commit_resumes_only_missing_project_and_replay_reads_no_roots(self):
        child = self.blocked_child('between')
        before = self.ledger().history('kill-between')
        request = before['requests'][0]
        self.assertEqual('OPEN', request['status'])
        self.assertEqual(['a'], request['completed_project_ids'])
        self.assertEqual(['b'], request['remaining_project_ids'])
        self.assertEqual(2, before['head']['sequence'])
        committed = copy.deepcopy(before['results'][0])
        self.assertEqual('a', committed['project_id'])
        self.assertTrue(committed['result']['success'])
        self.assertTrue(committed['result']['field_provenance']['current_work.objective']['sha256'])
        self.kill(child)
        self.assertEqual(before, self.ledger().history('kill-between'))
        # The resumed CLI must not revisit a. Its original immutable proof wins.
        (self.a / 'STATE.yaml').write_text('this is not valid mapped current state\n')
        resumed = self.cli('kill-between')
        self.assertEqual('FINISHED', resumed['status'])
        self.assertEqual(1, resumed['appended_count'])
        after = self.ledger().history('kill-between')
        self.assertEqual(['a', 'b'], after['requests'][0]['completed_project_ids'])
        self.assertEqual([], after['requests'][0]['remaining_project_ids'])
        self.assertEqual(4, after['head']['sequence'])
        self.assertEqual(committed, after['results'][0])  # ref/hash/observed_at/source SHA and whole row
        self.assertTrue(after['results'][1]['result']['success'])
        replay = self.cli('kill-between', no_project_reads=True)
        self.assertEqual(0, replay['appended_count'])
        self.assertEqual(after, self.ledger().history('kill-between'))
        # A deliberately new ID is allowed to collect a fresh source fact.
        changed = copy.deepcopy(self.fixture.business)
        changed['current_work']['objective'] = 'Fresh explicit request sees this new fact'
        (self.a / 'STATE.yaml').write_text(yaml.safe_dump(changed))
        fresh = self.cli('new-explicit-request')
        self.assertEqual(2, fresh['appended_count'])
        latest = self.ledger().history('new-explicit-request')['results'][0]
        self.assertEqual(changed['current_work']['objective'], latest['result']['business']['current_work']['objective'])
        self.assertNotEqual(committed['result_ref'], latest['result_ref'])
        self.assertNotEqual(committed['result']['sources'][0]['sha256'], latest['result']['sources'][0]['sha256'])
        self.assertEqual(after['results'], self.ledger().history('kill-between')['results'])

    def test_sigkill_inside_precommit_transaction_publishes_no_partial_result_or_head(self):
        ledger = self.ledger()
        ledger.begin_request('kill-precommit', ['a', 'b'], self.fixture.resolver().authority)
        before = ledger.history('kill-precommit')
        child = self.blocked_child('precommit')
        # A concurrent reader cannot see the writer's uncommitted event/result.
        self.assertEqual(before, self.ledger().history('kill-precommit'))
        self.kill(child)
        recovered = self.ledger().history('kill-precommit')
        self.assertEqual(before, recovered)
        self.assertEqual([], recovered['results'])
        self.assertEqual(1, recovered['head']['sequence'])
        result = self.cli('kill-precommit')
        self.assertEqual('FINISHED', result['status'])
        self.assertEqual(2, result['appended_count'])
        final = self.ledger().history('kill-precommit')
        self.assertEqual(4, final['head']['sequence'])
        self.assertEqual(['REQUEST_BEGUN', 'PROJECT_RESULT', 'PROJECT_RESULT', 'REQUEST_FINISHED'],
                         [event['event_type'] for event in final['events']])
        self.assertTrue(all(row['result']['success'] for row in final['results']))
