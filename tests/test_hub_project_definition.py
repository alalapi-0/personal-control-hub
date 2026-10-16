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


if __name__ == '__main__': unittest.main()
