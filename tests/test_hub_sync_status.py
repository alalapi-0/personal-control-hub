"""Finite-scope status is a read-only full-ledger projection, not a page count."""
import hashlib
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import yaml

from connection_fixtures import Fixture
from hub.connection_records import validate_result
from hub.connection_refresh import RefreshLedger, refresh
from hub.project_service import ProjectService
from hub.service_contract import ServiceError
from hub.graph_records import validate_graph
from hub.pch_domain_projection import PROJECT, project_pch_domain


class PchDomainFixture:
    """Existing verified refresh/history and real ProjectService; no real data."""
    def __init__(self):
        from hub.connection_sources import SourceResolver
        self.fixture = Fixture()
        self.source, self.declaration = self.fixture.add(PROJECT)
        self.root = self.fixture.hub
        (self.root / 'data/connections').mkdir()
        (self.root / 'data/connections/sync_scope.yaml').write_text(yaml.safe_dump({
            'schema_version': '1.0', 'scope_id': 'synthetic-pch-domain', 'baseline_total': 1,
            'project_ids': [PROJECT], 'excluded_project_ids': {}}))
        self.resolver = SourceResolver(self.root)
        self.ledger = RefreshLedger(self.root, result_validator=validate_result)
        self.service = ProjectService(self.root)

    def collect(self, request='domain-first'):
        return refresh(self.ledger, self.resolver, request, [PROJECT])

    def snapshot(self):
        return self.ledger.history(), self.service.get_project(PROJECT)

    def project(self):
        result = project_pch_domain(*self.snapshot()); validate_graph(result); return result

    def publish_page(self):
        state = yaml.safe_load((self.source / 'STATE.yaml').read_bytes())
        state['pages'] = {'schema_version': '1.0', 'project_id': PROJECT, 'domains': [
            {'domain': 'implementation', 'known_page_count': 1, 'completeness': 'complete',
             'counting_basis': 'One explicitly published synthetic page', 'pages': [
                 {'id': 'synthetic-home', 'label': 'Synthetic home', 'kind': 'route',
                  'source_sha256': 'a' * 64, 'build_sha256': None}]},
            {'domain': 'design', 'known_page_count': None, 'completeness': 'unknown',
             'counting_basis': 'No design pages published', 'pages': []}], 'previews': []}
        self.declaration['extensions'] = {'schema_version': '1.3',
            'fact_mapping': {'page_inventory': {'source_ref': 'state', 'selector': {'path': ['pages']}}},
            'unknown_fields': {'page_inventory': 'No explicit page publication.'}}
        (self.source / 'STATE.yaml').write_text(yaml.safe_dump(state))
        (self.source / 'hub.connection.yaml').write_text(yaml.safe_dump(self.declaration))
        from hub.connection_sources import SourceResolver
        self.resolver = SourceResolver(self.root)

    def close(self): self.fixture.close()


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


class PchDomainProjectionTests(unittest.TestCase):
    def setUp(self):
        self.f = PchDomainFixture(); self.addCleanup(self.f.close)

    def test_verified_snapshot_projects_runs_sources_rows_and_unknowns_without_io(self):
        f = self.f; f.collect(); history, dto = f.snapshot(); inputs = copy.deepcopy((history, dto))
        state = (f.source / 'STATE.yaml').read_bytes(); head = f.ledger.head
        with patch('hub.connection_sources.SourceResolver.refresh', side_effect=AssertionError('projection refreshed')), \
                patch('sqlite3.connect', side_effect=AssertionError('projection opened DB')), \
                patch.object(Path, 'read_bytes', side_effect=AssertionError('projection read source')):
            result = project_pch_domain(history, dto); validate_graph(result)
        self.assertEqual((history, dto), inputs)
        self.assertEqual(f.ledger.head, head); self.assertEqual((f.source / 'STATE.yaml').read_bytes(), state)
        self.assertEqual(len([o for o in result['objects'] if o['type'] == 'Run']), 1)
        files = [o for o in result['objects'] if o['type'] == 'FileRef']; self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['data']['observation']['head'], history['head'])
        self.assertEqual(files[0]['data']['version_inputs']['source_sha256'], hashlib.sha256(state).hexdigest())
        self.assertIn('Artifact', result['unknown_types'])
        run = next(o for o in result['objects'] if o['type'] == 'Run')
        self.assertIsNone(run['data']['native_run_id']); self.assertFalse(run['data']['execution_authority'])
        self.assertEqual(run['explanations']['actor']['state'], 'unknown')
        keys = {(o['id'], o['revision']) for o in result['objects']}
        self.assertEqual({(r['object_id'], r['revision']) for r in result['rows']}, keys)
        self.assertTrue(all(r['projection_version'] == result['projection_version'] for r in result['rows']))
        self.assertNotIn(str(f.fixture.base), json.dumps(result))

    def test_explicit_page_has_exact_source_edges_without_artifact_read_inference(self):
        f = self.f; f.publish_page(); f.collect(); result = f.project(); history, _ = f.snapshot()
        artifact = next(o for o in result['objects'] if o['type'] == 'Artifact')
        self.assertEqual(artifact['data']['page_id'], 'synthetic-home')
        self.assertEqual(artifact['data']['availability']['state'], 'unknown')
        self.assertIsNone(artifact['data']['artifact']['sha256']['value'])
        self.assertEqual(artifact['data']['artifact']['input_version']['value'], 'a' * 64)
        edge = next(e for e in result['relations'] if e['to']['id'] == artifact['id'])
        self.assertEqual(edge['type'], 'references'); self.assertEqual(edge['to']['revision'], artifact['revision'])
        self.assertEqual(edge['source']['sha256'], history['results'][0]['result']['sources'][0]['sha256'])
        self.assertIn(history['results'][0]['result_ref'], edge['evidence'])
        self.assertTrue(any(history['head']['hash'] in item for item in edge['evidence']))
        self.assertFalse(any(e['type'] == 'produces' for e in result['relations']))

    def test_mismatched_heads_projects_anchors_and_malformed_snapshots_reject(self):
        f = self.f; f.collect(); history, dto = f.snapshot()
        mutations = [lambda h, d: d.update(project_id='other-project'),
                     lambda h, d: d['provenance']['ledger_head'].update(hash='f' * 64),
                     lambda h, d: h['events'].pop(), lambda h, d: h['results'].clear(),
                     lambda h, d: h['results'][0].update(result_ref='invented'),
                     lambda h, d: h['results'][0].update(result_hash='f' * 64),
                     lambda h, d: h['events'][0].update(event_hash='f' * 64),
                     lambda h, d: h['observation_requests'].append(h['requests'].pop(0)),
                     lambda h, d: d['source']['source_record'].update(success=False),
                     lambda h, d: d['provenance']['latest_authority'].update(schema_hash='f' * 64),
                     lambda h, d: d['operational'].update(last_success=None),
                     lambda h, d: d['provenance']['project_binding'].update(registration_sha256='f' * 64)]
        for n, mutate in enumerate(mutations):
            h, d = copy.deepcopy((history, dto)); mutate(h, d)
            with self.subTest(n=n), self.assertRaises(ServiceError) as error: project_pch_domain(h, d)
            self.assertEqual(error.exception.code, 'PCH_DOMAIN_SNAPSHOT_INVALID')
        for changes in ({'connection_read_allowed': False}, {'access_profile': 'no_current_goal_access'}):
            d = copy.deepcopy(dto); d['declared'].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ServiceError) as error: project_pch_domain(history, d)
            self.assertEqual(error.exception.code, 'PCH_DOMAIN_READ_DENIED')

    def test_page_history_keeps_exact_semantic_provenance_without_observation_revisions(self):
        from hub.connection_sources import SourceResolver
        f = self.f; f.publish_page()
        state_path = f.source / 'STATE.yaml'
        state = yaml.safe_load(state_path.read_bytes())
        state['alternate_pages'] = copy.deepcopy(state['pages'])
        state['alternate_objective'] = state['current_work']['objective']
        state_path.write_text(yaml.safe_dump(state))
        original_bytes = state_path.read_bytes()
        f.collect('page-original'); original = f.project()
        first = next(o for o in original['objects'] if o['type'] == 'Artifact')
        first_inputs = copy.deepcopy(first['data']['version_inputs'])

        # Same page, source bytes and build; only the declaration's selectors
        # change. Both the rule fingerprint and binding are real resolver facts.
        f.declaration['mapping']['current_work.objective']['selector']['path'] = ['alternate_objective']
        f.declaration['extensions']['fact_mapping']['page_inventory']['selector']['path'] = ['alternate_pages']
        (f.source / 'hub.connection.yaml').write_text(yaml.safe_dump(f.declaration))
        f.resolver = SourceResolver(f.root)
        f.collect('page-new-rules'); changed = f.project(); history = f.ledger.history()
        self.assertEqual(state_path.read_bytes(), original_bytes)
        self.assertTrue(all(row['result']['success'] for row in history['results']))
        self.assertEqual(history['results'][0]['result']['sources'][0]['sha256'],
                         history['results'][1]['result']['sources'][0]['sha256'])
        artifacts = [o for o in changed['objects'] if o['type'] == 'Artifact']
        self.assertEqual(len(artifacts), 2)
        self.assertEqual({o['id'] for o in artifacts}, {first['id']})
        self.assertEqual(len({o['revision'] for o in artifacts}), 2)
        prior = next(o for o in artifacts if o['revision'] == first['revision'])
        latest = next(o for o in artifacts if o['revision'] != first['revision'])
        self.assertEqual(prior['data']['version_inputs'], first_inputs)
        for key in ('declaration_sha256', 'rules_sha256', 'binding_sha256'):
            with self.subTest(semantic_version=key):
                self.assertNotEqual(prior['data']['version_inputs'][key], latest['data']['version_inputs'][key])
        self.assertEqual(prior['data']['artifact'], latest['data']['artifact'])
        self.assertEqual(prior['source']['applicability'], 'historical')
        self.assertEqual(latest['source']['applicability'], 'current')

        objects = {(o['id'], o['revision']): o for o in changed['objects']}
        edges = [e for e in changed['relations'] if e['type'] == 'references' and e['to']['id'] == first['id']]
        self.assertEqual(len(edges), 2)
        for edge in edges:
            target = objects[(edge['to']['id'], edge['to']['revision'])]
            source = objects[(edge['from']['id'], edge['from']['revision'])]
            with self.subTest(target_revision=target['revision']):
                self.assertEqual(source['type'], 'FileRef')
                self.assertTrue(all(source['data']['version_inputs'][k] == v
                                    for k, v in target['data']['version_inputs'].items()))
                self.assertEqual(edge['evidence'][0], target['data']['observation']['result_ref'])
                self.assertEqual(target['data']['availability']['state'], 'unknown')
                self.assertIsNone(target['data']['artifact']['sha256']['value'])

        semantic = {(o['id'], o['revision']) for o in artifacts}
        previous_head = history['head']
        f.collect('page-reobserved'); reobserved = f.project()
        self.assertNotEqual(f.ledger.head, previous_head)
        self.assertEqual({(o['id'], o['revision']) for o in reobserved['objects'] if o['type'] == 'Artifact'}, semantic)
        previous_head = f.ledger.head
        f.ledger.begin_request('page-head-only', [PROJECT], f.resolver.authority)
        head_only = f.project(); self.assertNotEqual(f.ledger.head, previous_head)
        self.assertEqual({(o['id'], o['revision']) for o in head_only['objects'] if o['type'] == 'Artifact'}, semantic)
        self.assertEqual({(r['object_id'], r['revision']) for r in head_only['rows'] if r['type'] == 'Artifact'}, semantic)
        self.assertEqual(state_path.read_bytes(), original_bytes)

    def test_management_relations_stay_candidate_separate_from_source_versions(self):
        from hub.management_store import ManagementStore
        from hub.connection_records import content_hash
        from test_hub_management_store import overlay
        from test_hub_graph_relations import endpoint, relation
        f = self.f; f.collect(); before = f.project()
        base = yaml.safe_load((Path(__file__).parents[1] / 'data/connections/management_config.yaml').read_text())
        base.update(schema_version='1.0', projects={}); base.pop('revision', None); base.pop('history', None)
        (f.root / 'data/connections/management_config.yaml').write_text(yaml.safe_dump(base, sort_keys=False))
        store = ManagementStore(f.root); snapshot = store.snapshot()
        root = next(o for o in before['objects'] if o['type'] == 'Project')
        target = endpoint(native=PROJECT, kind='Project', project=PROJECT, revision=root['revision'])
        item = relation(a=target, b=target)
        value = overlay(relations={'belongs_to': None, 'depends_on': [], 'related_to': [], 'independent': None,
                                   'typed': {'schema_version': '1.0', 'items': [item]}})
        f.service.save_management({'request_id': 'domain-manage', 'project_id': PROJECT,
            'expected_revision': snapshot['revision'], 'expected_hash': snapshot['config_hash'], 'operation': 'set', 'value': value},
            authorize=lambda: None)
        snapshot = store.snapshot()
        f.service.save_management({'request_id': 'domain-accept', 'project_id': PROJECT,
            'expected_revision': snapshot['revision'], 'expected_hash': snapshot['config_hash'], 'operation': 'accept_relation',
            'value': {'id': item['id'], 'sha256': content_hash(item)}}, authorize=lambda: None)
        after = f.project(); edges = [e for e in after['relations'] if 'management' in e]
        self.assertEqual(len(edges), 1); self.assertEqual(edges[0]['management']['state'], 'accepted')
        self.assertEqual(edges[0]['identity'], 'candidate'); self.assertEqual(edges[0]['source']['origin'], 'hub_management')
        self.assertEqual(before['source_version'], after['source_version'])
        self.assertNotEqual(before['projection_version'], after['projection_version'])


if __name__ == '__main__':
    unittest.main()
