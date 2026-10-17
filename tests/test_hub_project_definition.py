"""Source-backed long-term definitions never borrow current work or permissions."""
import copy
from pathlib import Path
import unittest
from unittest import mock

import yaml
from connection_fixtures import Fixture
from hub.connection_extensions import validate_project_definition
from hub.connection_records import RecordError, validate_result
from hub.connection_refresh import RefreshLedger, refresh
from hub.connection_sources import SourceResolver
from hub.connections import validate_registry_value
from hub.project_service import ProjectService


def known(value): return {'value': value, 'state': 'known', 'reason': None}


class ProjectDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)
        self.a, self.declaration = self.fixture.add('a')
        self.b, _ = self.fixture.add('b')
        self.service = ProjectService(self.fixture.hub)
        self.fixture.projects[0].update(name='Official A', description='Long-term purpose A', project_type='synthetic_tool')
        self.fixture.projects[1].update(description=None)
        self.fixture.save_registry()

    def publish(self, definition, **changes):
        state = copy.deepcopy(self.fixture.business)
        state['definition'] = definition
        state.update(changes)
        (self.a / 'STATE.yaml').write_text(yaml.safe_dump(state))
        self.declaration['extensions'] = {'schema_version': '1.4',
            'fact_mapping': {'project_definition': {'source_ref': 'state', 'selector': {'path': ['definition']}}},
            'unknown_fields': {'project_definition': 'No definition published.'}}
        (self.a / 'hub.connection.yaml').write_text(yaml.safe_dump(self.declaration))

    def collect(self):
        ledger = RefreshLedger(self.fixture.hub, result_validator=validate_result)
        result = refresh(ledger, SourceResolver(self.fixture.hub), 'collect-definitions', ['a', 'b'])
        self.assertFalse(result['resolver_errors'])

    def test_registry_metadata_missing_unknown_and_no_get_source_reads(self):
        with mock.patch('hub.connection_sources._safe_relative_read', side_effect=AssertionError('GET source read')):
            a = self.service.get_project('a')['definition']
            b = self.service.get_project('b')['definition']
        self.assertEqual(a['fields']['official_name']['value'], 'Official A')
        self.assertEqual(a['fields']['purpose']['value'], 'Long-term purpose A')
        self.assertEqual(a['fields']['purpose']['source']['kind'], 'registry')
        self.assertEqual(a['fields']['outputs']['state'], 'missing')
        self.assertEqual(b['fields']['purpose']['state'], 'unknown')
        self.assertEqual(b['fields']['roles']['value'], None)
        self.assertEqual(a['fields']['aliases']['state'], 'missing')
        self.assertNotIn('current_work', a)

    def test_source_definition_proofs_confirmed_aliases_and_multiple_roles(self):
        self.publish({'purpose': known('Publish reusable synthetic assets'),
                      'outputs': known(['Editable asset', 'Review package']),
                      'roles': known(['Producer', 'Library']),
                      'aliases': known([{'name': 'Confirmed A', 'confirmed': True}]),
                      'primary_project_id': known('a'), 'working_copies': known(['b'])})
        self.collect()
        with mock.patch.object(SourceResolver, 'refresh', side_effect=AssertionError('GET collected sources')):
            definition = self.service.get_project('a')['definition']
        fields = definition['fields']
        self.assertEqual(fields['purpose']['value'], 'Publish reusable synthetic assets')
        self.assertEqual(fields['roles']['value'], ['Producer', 'Library'])
        self.assertEqual(fields['aliases']['value'], ['Confirmed A'])
        self.assertEqual(fields['working_copies']['value'], ['b'])
        source = fields['purpose']['source']
        self.assertEqual(source['selector'], {'path': ['definition']})
        self.assertEqual(len(source['sha256']), 64)
        self.assertTrue(source['observed_at'])
        self.assertEqual(self.service.get_project('b')['definition']['fields']['aliases']['state'], 'missing')

    def test_registry_definition_confirmed_aliases_are_hashed_without_root_reads(self):
        self.fixture.projects[0]['project_definition'] = {
            'aliases': known([{'name': '三十天', 'confirmed': True}]),
            'outputs': known([]),
            'roles': {'value': None, 'state': 'unknown', 'reason': 'Roles unpublished'}}
        self.fixture.save_registry()
        with mock.patch('hub.connection_sources._safe_relative_read', side_effect=AssertionError('GET source read')), \
                mock.patch.object(SourceResolver, 'refresh', side_effect=AssertionError('GET refresh')):
            dto = self.service.get_project('a')
            listed = self.service.list_projects()['projects']
        fields = dto['definition']['fields']
        self.assertEqual(fields['aliases']['value'], ['三十天'])
        self.assertEqual(fields['aliases']['state'], 'known')
        self.assertEqual(fields['outputs']['state'], 'empty')
        self.assertEqual(fields['roles']['state'], 'unknown')
        self.assertEqual(fields['roles']['reason'], 'Roles unpublished')
        self.assertEqual(fields['working_copies']['state'], 'missing')
        self.assertEqual(fields['official_name']['value'], 'Official A')
        source = fields['aliases']['source']
        self.assertEqual(source['kind'], 'registry')
        self.assertEqual(source['field'], 'project_definition.aliases')
        self.assertEqual(source['sha256'], source['registry_sha256'])
        self.assertEqual(len(source['sha256']), 64)
        self.assertIsNone(source['observed_at'])
        self.assertEqual(next(row for row in listed if row['project_id'] == 'a')['definition'], dto['definition'])

    def test_registry_definition_rejects_unconfirmed_and_malformed_fields(self):
        for definition in (None, [], {'aliases': known([{'name': 'Actor 4', 'confirmed': False}])},
                           {'aliases': known(['Actor 4'])},
                           {'aliases': known([{'name': 'Actor 4', 'confirmed': True, 'extra': True}])},
                           {'aliases': known([{'name': 'Actor 4', 'confirmed': True}] * 2)},
                           {'roles': known('Producer')}, {'unpublished': known('guess')}):
            with self.subTest(definition=definition), self.assertRaises(RecordError):
                validate_registry_value({'projects': [{**self.fixture.projects[0], 'project_definition': definition}]})

    def test_registry_definition_priority_and_stale_source_fallback(self):
        self.fixture.projects[0]['project_definition'] = {
            'official_name': known('Explicit registry A'),
            'purpose': {'value': None, 'state': 'unknown', 'reason': 'Purpose not confirmed'},
            'aliases': known([{'name': 'Registry A', 'confirmed': True}])}
        self.fixture.save_registry()
        self.publish({'official_name': known('Source A'),
                      'aliases': known([{'name': 'Source alias', 'confirmed': True}])})
        self.collect()
        fresh = self.service.get_project('a')['definition']['fields']
        self.assertEqual(fresh['official_name']['value'], 'Source A')
        self.assertEqual(fresh['aliases']['value'], ['Source alias'])
        self.assertEqual(fresh['aliases']['source']['kind'], 'source_extension')
        self.assertEqual(fresh['purpose']['state'], 'unknown')
        self.assertEqual(fresh['purpose']['reason'], 'Purpose not confirmed')
        self.assertEqual(fresh['project_type']['value'], 'synthetic_tool')
        with mock.patch('hub.project_service._age', return_value=3601), \
                mock.patch('hub.connection_sources._safe_relative_read', side_effect=AssertionError('GET source read')):
            stale_dto = self.service.get_project('a')
        self.assertEqual(stale_dto['freshness']['state'], 'stale')
        stale = stale_dto['definition']['fields']
        self.assertEqual(stale['official_name']['value'], 'Explicit registry A')
        self.assertEqual(stale['aliases']['value'], ['Registry A'])
        self.assertEqual(stale['aliases']['source']['kind'], 'registry')
        self.assertEqual(stale['outputs']['state'], 'unavailable')
        self.fixture.projects[0]['description'] = 'Changed registry hash'
        self.fixture.save_registry()
        drift_dto = self.service.get_project('a')
        self.assertTrue(drift_dto['freshness']['authority_drift'])
        drift_alias = drift_dto['definition']['fields']['aliases']
        self.assertEqual(drift_alias['value'], ['Registry A'])
        self.assertNotEqual(drift_alias['source']['registry_sha256'], fresh['aliases']['source']['registry_sha256'])

    def test_failed_source_preserves_registry_alias_without_promoting_old_source(self):
        self.fixture.projects[0]['project_definition'] = {
            'aliases': known([{'name': 'Registry A', 'confirmed': True}])}
        self.fixture.save_registry()
        self.publish({'aliases': known([{'name': 'Historical alias', 'confirmed': True}]),
                      'outputs': known(['Historical output'])})
        self.collect()
        (self.a / 'STATE.yaml').unlink()
        ledger = RefreshLedger(self.fixture.hub, result_validator=validate_result)
        refresh(ledger, SourceResolver(self.fixture.hub), 'failed-definition', ['a'])
        with mock.patch('hub.connection_sources._safe_relative_read', side_effect=AssertionError('GET source read')):
            dto = self.service.get_project('a')
        self.assertFalse(dto['source']['source_record']['success'])
        self.assertEqual(dto['freshness']['state'], 'stale')
        self.assertEqual(dto['definition']['fields']['aliases']['value'], ['Registry A'])
        self.assertEqual(dto['definition']['fields']['aliases']['source']['kind'], 'registry')
        self.assertIsNone(dto['definition']['fields']['outputs']['value'])

    def test_empty_unknown_not_applicable_and_active_blocked_zero_progress(self):
        state = copy.deepcopy(self.fixture.business)
        state['current_work'].update(status='active', objective='Current iteration only', completed=False)
        state['progress'].update(completed=0, total=7)
        self.publish({'outputs': known([]), 'roles': {'value': None, 'state': 'unknown', 'reason': 'Roles unpublished'},
                      'primary_project_id': {'value': None, 'state': 'not_applicable', 'reason': 'Standalone project'}},
                     current_work=state['current_work'], progress=state['progress'])
        self.collect()
        dto = self.service.get_project('a')
        fields = dto['definition']['fields']
        self.assertEqual(fields['outputs']['state'], 'empty')
        self.assertEqual(fields['outputs']['value'], [])
        self.assertEqual(fields['roles']['state'], 'unknown')
        self.assertEqual(fields['primary_project_id']['state'], 'not_applicable')
        self.assertEqual(fields['purpose']['value'], 'Long-term purpose A')
        self.assertEqual(dto['business']['normalized_status'], 'active')
        self.assertTrue(dto['business']['blockers'])
        self.assertEqual(dto['business']['fields']['progress']['completed'], 0)
        self.assertEqual(dto['business']['fields']['progress']['total'], 7)
        self.assertIn('progress.completed', dto['business']['quality']['value_coverage']['fields'])

    def test_unknown_denominator_is_not_zero_and_old_failures_do_not_define_purpose(self):
        state = copy.deepcopy(self.fixture.business)
        state['current_work'].update(status='active', completed=False)
        state['progress'].update(completed=0, total=None)
        state['verification']['status'] = 'failed'
        self.publish({}, current_work=state['current_work'], progress=state['progress'], verification=state['verification'])
        self.collect()
        dto = self.service.get_project('a')
        self.assertIsNone(dto['business']['fields']['progress']['total'])
        self.assertEqual(dto['definition']['fields']['purpose']['value'], 'Long-term purpose A')
        self.assertEqual(dto['business']['normalized_status'], 'active')
        self.assertNotIn('progress.total', dto['business']['quality']['value_coverage']['fields'])

    def test_reject_unconfirmed_aliases_wrong_shapes_and_unsafe_identity(self):
        for definition in ({'aliases': known([{'name': 'Actor 4', 'confirmed': False}])},
                           {'aliases': known(['Unconfirmed shorthand'])},
                           {'roles': known(0)}, {'outputs': known('guessed')},
                           {'primary_project_id': known('../other')},
                           {'purpose': {'value': 'content', 'state': 'unknown', 'reason': 'missing'}}):
            with self.subTest(definition=definition), self.assertRaises(RecordError):
                validate_project_definition(definition)

    def test_removed_disabled_cloud_registry_only_projection_never_probes(self):
        for project in self.fixture.projects:
            project['project_definition'] = {'aliases': known([{'name': 'Alias ' + project['id'], 'confirmed': True}])}
        self.fixture.projects[0].update(current_state_status='removed_local', enabled=True,
                                        connection_read_allowed=True, root_path='/no-read-removed')
        self.fixture.projects[1].update(enabled=False, root_kind='cloud', root_path='/no-read-cloud')
        self.fixture.save_registry()
        with mock.patch('hub.connection_sources._safe_relative_read', side_effect=AssertionError('foreign read')):
            rows = self.service.list_projects()['projects']
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertIn('definition', row)
            self.assertEqual(row['definition']['fields']['roles']['state'], 'missing')
            self.assertEqual(row['definition']['fields']['aliases']['value'], ['Alias ' + row['project_id']])


if __name__ == '__main__': unittest.main()
