"""Source-declared pages traverse actual local source/ledger/HTTP, not screenshots."""
import copy
import hashlib
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from connection_fixtures import Fixture
from test_hub_page_inventory import inventory, page, domain, preview, A, B
from test_hub_timeline_pipeline import SyntheticDesignService
import test_hub_local_service as transport
from hub.connection_sources import SourceResolver
from hub.connection_refresh import RefreshLedger, refresh
from hub.connection_records import validate_result, update_key
from hub.local_service import HubHTTPServer
from hub.preview_service import PreviewService, page_catalog
from hub.project_service import ProjectService
from hub.metric_collect import MetricCollector


def representative_inventory():
    value = inventory()
    value['domains'][0] = domain('implementation', [page('home'), page('dialog', 'dialog'), page('tab', 'tab')], 'partial')
    value['previews'] = [preview('current', 'home'), preview('old', 'dialog', source=B),
                         preview('unknown', 'tab', source=None)]
    value['previews'][0]['captured_at'] = '2026-10-15T08:30:00+09:00'
    return value


class PagePipelineTests(unittest.TestCase):
    call = transport.LocalHTTPTests.call
    stop = transport.LocalHTTPTests.stop

    def setUp(self):
        self.fixture = Fixture(); self.addCleanup(self.fixture.close)
        self.root, self.declaration = self.fixture.add('business')
        self.declaration['extensions'] = {'schema_version': '1.3', 'fact_mapping': {
            'page_inventory': {'source_ref': 'state', 'selector': {'path': ['page_inventory']}}},
            'unknown_fields': {'page_inventory': 'Source has no declared page inventory'}}
        (self.root/'hub.connection.yaml').write_text(yaml.safe_dump(self.declaration, sort_keys=False))
        self.value = representative_inventory(); self.write(self.value)
        self.ledger = RefreshLedger(self.fixture.hub, result_validator=validate_result)
        self.service = ProjectService(self.fixture.hub); self.cookie = None

    def write(self, value):
        data = copy.deepcopy(self.fixture.business); data['page_inventory'] = value
        (self.root/'STATE.yaml').write_text(yaml.safe_dump(data, sort_keys=False))

    def publish(self, request='pages', age=0):
        when = (datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat()
        resolver = SourceResolver(self.fixture.hub, clock=lambda: when)
        return refresh(self.ledger, resolver, request, ['business'])

    def start(self):
        self.server = HubHTTPServer(self.service, SyntheticDesignService(), host='127.0.0.1', port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.thread.start(); self.addCleanup(self.stop)
        code, headers, _ = self.call(path='/api/session', authenticated=False)
        self.assertEqual(code, 200); self.cookie = headers['Set-Cookie'].split(';', 1)[0]

    def test_literal_source_proof_current_subset_and_preview_versions(self):
        result = self.publish(); self.assertEqual(result['resolver_errors'], {})
        dto = self.service.get_project('business'); view = dto['business']['page_inventory']
        self.assertTrue(view['available']); self.assertEqual(view['value'], self.value)
        self.assertEqual(view['proof']['sha256'], hashlib.sha256((self.root/'STATE.yaml').read_bytes()).hexdigest())
        self.assertEqual(view['proof']['selector'], {'path': ['page_inventory']})
        self.assertEqual(view['observed_at'], dto['operational']['latest_attempt']['observed_at'])
        self.assertEqual([p['version_state'] for p in view['previews']], ['current', 'historical', 'unknown_version'])
        self.assertEqual(view['previews'][0]['captured_at'], '2026-10-15T08:30:00+09:00')
        self.assertIsNone(view['previews'][1]['captured_at'])
        self.assertEqual(view['value']['domains'][0]['known_page_count'], 3)
        self.assertEqual(view['value']['domains'][0]['completeness'], 'partial')
        self.assertIsNone(view['value']['domains'][1]['known_page_count'])

    def test_offline_history_does_not_reopen_source_or_promote_old_capture(self):
        self.publish(); before = self.ledger.path.read_bytes(); prefix = self.ledger.history()['events']
        old = self.service.get_project('business')
        with mock.patch.object(Path, 'read_text', side_effect=AssertionError('source reopen')):
            validate_result(old['operational']['latest_attempt'])
        self.assertEqual(self.ledger.path.read_bytes(), before)
        bad = copy.deepcopy(self.value); bad['previews'][0]['page_id'] = 'missing'
        self.write(bad); result = self.publish('invalid-pages')
        dto = self.service.get_project('business')
        self.assertFalse(dto['operational']['latest_attempt']['success'])
        self.assertTrue(dto['operational']['latest_attempt']['errors'])
        self.assertFalse(dto['business']['page_inventory']['available'])
        self.assertIsNone(dto['business']['page_inventory']['value'])
        self.assertEqual(dto['operational']['last_success']['extensions']['fact']['page_inventory']['value'], self.value)
        self.assertEqual(self.ledger.history()['events'][:len(prefix)], prefix)

    def test_stale_authority_denied_removed_and_cloud_do_not_publish_current_pages(self):
        self.publish(age=7200)
        self.assertEqual(self.service.get_project('business')['business']['page_inventory']['reason'], 'SOURCE_STALE')
        self.publish('fresh-pages')
        original = copy.deepcopy(self.fixture.projects[0])
        for changes in [{'connection_read_allowed': False}, {'current_state_status': 'removed_local',
            'local_presence': {'status': 'removed_local'}}, {'root_kind': 'cloud', 'project_type': 'cloud_project'}]:
            self.fixture.projects[0].update(changes); self.fixture.save_registry()
            view = self.service.get_project('business')['business']['page_inventory']
            self.assertFalse(view['available']); self.assertIsNone(view['value'])
            self.fixture.projects[0] = copy.deepcopy(original); self.fixture.save_registry()

    def test_metadata_http_and_preview_index_gets_never_capture_or_open_artifacts(self):
        self.publish(); self.start(); before = self.ledger.path.read_bytes()
        with mock.patch.object(SourceResolver, 'refresh', side_effect=AssertionError('GET source read')), \
             mock.patch.object(MetricCollector, 'collect', side_effect=AssertionError('GET collector')):
            for route in ['/api/projects', '/api/projects/business', '/api/page-inventory']:
                status, _, body = self.call(path=route); self.assertEqual(status, 200, body)
            status, _, body = self.call(path='/api/page-inventory'); value = body['data']
            self.assertEqual(value['total'], 1); self.assertEqual(value['head'], self.ledger.head)
            self.assertFalse(value['capture_performed']); self.assertFalse(value['execution_allowed'])
            self.assertEqual(value['retention'], 'local_only')
            self.assertEqual(value['projects'][0]['page_inventory']['value'], self.value)
            self.assertEqual(PreviewService(self.service, None, None).page_catalog(), page_catalog(self.service))
        self.assertEqual(self.ledger.path.read_bytes(), before)
        self.assertFalse((self.fixture.hub/'data/previews').exists())
        self.assertEqual(self.call(path='/api/page-inventory?url=http://127.0.0.1:1/')[0], 400)

    def test_complete_index_over100_and_legacy_unknown_do_not_create_page_totals(self):
        self.publish(); template = copy.deepcopy(self.fixture.projects[0])
        for n in range(205): self.fixture.projects.append(dict(copy.deepcopy(template), id=f'p{n:03d}', name=f'Fixture {n}'))
        self.fixture.save_registry(); result = page_catalog(self.service)
        self.assertEqual(result['total'], 206); self.assertEqual(len(result['projects']), 206)
        self.assertEqual(len({p['project_id'] for p in result['projects']}), 206)
        rows = {row['project_id']: row for row in result['projects']}
        self.assertTrue(rows['business']['page_inventory']['available'])
        self.assertEqual(self.value, rows['business']['page_inventory']['value'])
        self.assertTrue(all(not rows[f'p{n:03d}']['page_inventory']['available'] for n in range(205)))
        self.assertTrue(all(rows[f'p{n:03d}']['page_inventory']['value'] is None for n in range(205)))
        # A genuine three-field legacy capsule never gains current new authority.
        legacy = copy.deepcopy(self.ledger.history('pages')['results'][0]['result'])
        legacy['authority'] = {key: legacy['authority'][key]
            for key in ('registry_hash', 'schema_hash', 'adapter_version')}
        legacy.pop('binding_hash')
        legacy['update_key'] = update_key(legacy)
        validate_result(legacy)
        self.ledger.begin_request('legacy-pages', ['business'], legacy['authority'])
        self.ledger.append_project_result('legacy-pages', 'business', legacy)
        self.ledger.finish_request('legacy-pages')
        historical = page_catalog(self.service)
        self.assertEqual(206, historical['total'])
        self.assertTrue(all(not row['page_inventory']['available'] for row in historical['projects']))
        self.assertTrue(all(row['page_inventory']['value'] is None for row in historical['projects']))
        self.assertEqual(legacy, self.ledger.history('legacy-pages')['results'][0]['result'])

    def test_unknown_field_and_known_empty_inventory_remain_distinct(self):
        self.write(None); self.publish('unknown-pages')
        self.assertFalse(self.service.get_project('business')['business']['page_inventory']['available'])
        value = inventory(); value['domains'][0] = domain('implementation', [], 'complete')
        self.write(value); self.publish('zero-pages')
        view = self.service.get_project('business')['business']['page_inventory']
        self.assertTrue(view['available']); self.assertEqual(view['value']['domains'][0]['known_page_count'], 0)
        self.assertEqual(view['value']['domains'][0]['completeness'], 'complete')
        self.assertIsNone(view['value']['domains'][1]['known_page_count'])


if __name__ == '__main__': unittest.main()
