"""Large source checks use synthetic documents and isolated authorities only."""
import copy
import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

from connection_fixtures import Fixture
from hub.connection_records import RecordError, empty_business, validate_result
from hub.connection_refresh import RefreshLedger, refresh
from hub.connection_sources import (MAX_LIGHT_NOVEL_STATE_BYTES, _safe_light_novel_read,
                                    _safe_relative_read, _read_pinned)
from hub.project_service import project_snapshot


class LargeLightNovelTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)
        self.root, self.declaration = self.fixture.add('light-novel')
        self.source = self.root / 'governance/round_state.yaml'
        self.source.parent.mkdir()
        self.small = (self.root / 'STATE.yaml').read_bytes()
        self.declaration['source_refs'][0]['path'] = 'governance/round_state.yaml'
        self.save_declaration()
        self.fixture.projects[0]['current_state_paths'] = ['governance/round_state.yaml']
        self.fixture.save_registry()

    def save_declaration(self):
        (self.root / 'hub.connection.yaml').write_text(yaml.safe_dump(self.declaration, sort_keys=False))

    def source_bytes(self, tail=b''):
        return self.small + b'\nhistory: ' + b'x' * 4_600_000 + b'\n' + tail

    def test_full_history_and_source_identity_with_actual_worker_limits(self):
        # Many records exercise parser allocation, rather than a single scalar.
        raw = (self.small + b'\nhistory:\n' +
               b''.join(b'  - id: h%05d\n    note: ' % i + b'x' * 540 + b'\n' for i in range(8200)) +
               b'last_history_marker: synthetic-private-history-marker\n')
        self.assertGreater(len(raw), 4_600_000)
        self.source.write_bytes(raw)
        calls = []
        real_run = subprocess.run
        def run(*args, **kwargs):
            response = real_run(*args, **kwargs)
            calls.append((args, kwargs, response))
            return response
        with mock.patch('hub.connection_sources.subprocess.run', side_effect=run):
            result = self.fixture.resolver().refresh('light-novel')
        self.assertTrue(result['success'])
        self.assertEqual(result['sources'][0]['id'], 'state')
        self.assertEqual(result['sources'][0]['bytes'], len(raw))
        self.assertEqual(result['sources'][0]['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(self.source.read_bytes(), raw)
        self.assertNotIn('synthetic-private-history-marker', json.dumps(result))
        self.assertEqual(len(calls), 1)
        output = json.loads(calls[0][2].stdout)
        self.assertLessEqual(output['limits']['address_space_bytes'], 512 * 1024 * 1024)
        self.assertLessEqual(output['limits']['cpu_seconds'], 30)
        self.assertGreater(output['peak_rss_kib'], 0)
        self.assertLess(output['peak_rss_kib'] * 1024, output['limits']['address_space_bytes'])
        print('synthetic_worker_peak_rss_kib=', output['peak_rss_kib'], 'limits=', output['limits'])
        # A real lower inherited address-space limit makes this same parse fail
        # closed; normal limits still recover on the next refresh.
        worker = str(Path(__file__).resolve().parents[1] / 'src/hub/connection_large_worker.py')
        limiter = ('import os, resource, sys; '
                   'resource.setrlimit(resource.RLIMIT_AS, (48*1024*1024, 48*1024*1024)); '
                   'os.execv(sys.executable, [sys.executable, "-I", sys.argv[1]])')
        limited = real_run([sys.executable, '-I', '-c', limiter, worker], input=calls[0][1]['input'],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=45)
        self.assertTrue(limited.returncode != 0 or not json.loads(limited.stdout).get('ok'))
        self.assertTrue(self.fixture.resolver().refresh('light-novel')['success'])

    def test_tail_errors_and_aliases_never_publish_truncated_history(self):
        for tail in (b'broken: [\n', b'tail: one\ntail: two\n', b'tail: &history_anchor value\n'):
            with self.subTest(tail=tail[:12]):
                self.source.write_bytes(self.source_bytes(tail))
                result = self.fixture.resolver().refresh('light-novel')
                self.assertEqual(result['disposition'], 'invalid')
                self.assertEqual(result['business'], empty_business())
                self.assertEqual(result['field_provenance'], {})

    def test_failure_preserves_ledger_history_and_recovers(self):
        good = self.source_bytes()
        self.source.write_bytes(good)
        ledger = RefreshLedger(self.fixture.hub, result_validator=validate_result)
        resolver = self.fixture.resolver()
        refresh(ledger, resolver, 'first', ['light-novel'])
        first = ledger.history('first')['results'][0]['result']
        self.assertTrue(first['success'])
        self.source.write_bytes(good + b'broken: [\n')
        refresh(ledger, self.fixture.resolver(), 'failed', ['light-novel'])
        failed = ledger.history('failed')['results'][0]['result']
        self.assertEqual(failed['disposition'], 'invalid')
        projection = project_snapshot(self.fixture.resolver().registry, self.fixture.resolver().authority, ledger)
        item = next(p for p in projection['projects'] if p['project_id'] == 'light-novel')
        self.assertEqual(item['last_success'], first)
        self.assertEqual(item['freshness']['state'], 'stale')
        self.source.write_bytes(good)
        refresh(ledger, self.fixture.resolver(), 'recovered', ['light-novel'])
        self.assertTrue(ledger.history('recovered')['results'][0]['result']['success'])

    def test_limits_are_not_extended_to_other_identity_or_route(self):
        self.source.write_bytes(self.source_bytes())
        with self.assertRaises(RecordError):
            _safe_relative_read(self.root, 'governance/round_state.yaml', authority_check=lambda: None,
                                max_bytes=MAX_LIGHT_NOVEL_STATE_BYTES)
        with self.assertRaises(RecordError):
            _read_pinned(self.root, 'governance/round_state.yaml', authority_check=lambda: None,
                         max_bytes=MAX_LIGHT_NOVEL_STATE_BYTES)
        other_root, other_declaration = self.fixture.add('other')
        other_source = other_root / 'governance/round_state.yaml'
        other_source.parent.mkdir()
        other_source.write_bytes(self.source.read_bytes())
        other_declaration['source_refs'][0]['path'] = 'governance/round_state.yaml'
        (other_root / 'hub.connection.yaml').write_text(yaml.safe_dump(other_declaration))
        self.fixture.projects[1]['current_state_paths'] = ['governance/round_state.yaml']
        self.fixture.save_registry()
        self.assertEqual(self.fixture.resolver().refresh('other')['disposition'], 'invalid')
        alternate = self.root / 'other.yaml'
        alternate.write_bytes(self.source.read_bytes())
        self.declaration['source_refs'][0]['path'] = 'other.yaml'
        self.save_declaration()
        self.fixture.projects[0]['current_state_paths'] = ['other.yaml']
        self.fixture.save_registry()
        self.assertEqual(self.fixture.resolver().refresh('light-novel')['disposition'], 'invalid')

    def test_large_extension_fact_uses_source_bound_selection(self):
        self.declaration['extensions'] = {
            'schema_version': '1.0',
            'fact_mapping': {'has_ui': {'source_ref': 'state', 'selector': {'path': ['has_ui']}}},
            'unknown_fields': {'has_ui': 'Not reported.'},
        }
        self.save_declaration()
        self.source.write_bytes(self.source_bytes(b'has_ui: false\n'))
        result = self.fixture.resolver().refresh('light-novel')
        self.assertTrue(result['success'])
        fact = result['extensions']['fact']['has_ui']
        self.assertIs(fact['value'], False)
        self.assertEqual(fact['proof']['sha256'], result['sources'][0]['sha256'])
        self.assertEqual(fact['proof']['selector'], {'path': ['has_ui']})

    def test_unrepresentable_fields_are_unknown_without_losing_source_or_progress(self):
        state = copy.deepcopy(self.fixture.business)
        state['current_work']['status'] = 'in_progress'
        state['current_work']['objective'] = 'api_key=synthetic-sensitive-value'
        state['progress']['milestones'] = {'history': ['preserved']}
        state['verification']['evidence_refs'] = ['evidence/%04d' % i for i in range(1001)]
        state['delivery']['status'] = 'unrepresented'
        document = {'codex_governance': state}
        for rule in self.declaration['mapping'].values():
            rule['selector']['path'].insert(0, 'codex_governance')
        self.save_declaration()
        raw = yaml.safe_dump(document, sort_keys=False).encode() + b'\nhistory: ' + b'x' * 4_600_000 + b'\n'
        self.source.write_bytes(raw)
        result = self.fixture.resolver().refresh('light-novel')
        self.assertTrue(result['success'])
        self.assertEqual(result['business']['current_work']['status'], 'active')
        proof = result['field_provenance']['current_work.status']
        self.assertEqual(proof['raw_value'], 'in_progress')
        self.assertEqual(proof['value_map'], {'in_progress': 'active'})
        self.assertEqual((result['business']['progress']['completed'], result['business']['progress']['total']), (2, 3))
        for field in ('current_work.objective', 'progress.milestones', 'verification.evidence_refs', 'delivery.status'):
            self.assertIn(field, result['unknown_fields'])
            self.assertNotIn(field, result['field_provenance'])
            self.assertIn('complete source remains unchanged', result['unknown_fields'][field])
        self.assertIsNone(result['business']['verification']['evidence_refs'])
        self.assertNotIn('synthetic-sensitive-value', json.dumps(result))
        self.assertEqual(result['sources'][0]['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(self.source.read_bytes(), raw)

    def test_oversize_and_timeout_fail_then_recover(self):
        self.source.write_bytes(self.source_bytes())
        with mock.patch('hub.connection_sources.subprocess.run', side_effect=subprocess.TimeoutExpired('worker', 45)):
            failed = self.fixture.resolver().refresh('light-novel')
        self.assertEqual(failed['disposition'], 'invalid')
        self.assertIn('timed out', failed['errors'][0]['message'])
        self.assertTrue(self.fixture.resolver().refresh('light-novel')['success'])
        prefix = self.small + b'\nhistory: '
        at_limit = prefix + b'x' * (MAX_LIGHT_NOVEL_STATE_BYTES - len(prefix) - 1) + b'\n'
        self.assertEqual(len(at_limit), MAX_LIGHT_NOVEL_STATE_BYTES)
        self.source.write_bytes(at_limit)
        self.assertTrue(self.fixture.resolver().refresh('light-novel')['success'])
        self.source.write_bytes(at_limit + b'\n')
        self.assertEqual(self.fixture.resolver().refresh('light-novel')['disposition'], 'invalid')
        self.source.write_bytes(self.source_bytes())
        self.assertTrue(self.fixture.resolver().refresh('light-novel')['success'])

    def test_link_and_changed_source_protections_still_apply(self):
        raw = self.source_bytes()
        outside = self.fixture.base / 'outside.yaml'
        outside.write_bytes(raw)
        self.source.symlink_to(outside)
        self.assertEqual(self.fixture.resolver().refresh('light-novel')['disposition'], 'unsafe_path')
        self.source.unlink()
        os.link(outside, self.source)
        self.assertEqual(self.fixture.resolver().refresh('light-novel')['disposition'], 'invalid')
        self.source.unlink()
        self.source.write_bytes(raw)
        calls = []
        def change(root, source, **kwargs):
            value = _safe_light_novel_read(root, source, **kwargs)
            if source['path'] == 'governance/round_state.yaml' and not calls:
                calls.append(True)
                self.source.write_bytes(raw + b'changed: true\n')
            return value
        with mock.patch('hub.connection_sources._safe_light_novel_read', side_effect=change):
            self.assertFalse(self.fixture.resolver().refresh('light-novel')['success'])
        self.assertEqual(calls, [True])


if __name__ == '__main__':
    unittest.main()
