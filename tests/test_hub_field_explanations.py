"""Display commentary requires exact fresh source identity; source facts stay intact."""
import copy
import hashlib
import unittest
from unittest import mock

import yaml
from connection_fixtures import Fixture
from hub.connection_records import RecordError, validate_result
from hub.connection_refresh import RefreshLedger, refresh
from hub.connection_sources import SourceResolver
from hub.connections import validate_registry_value
from hub.field_explanations import field_explanations
from hub.project_service import ProjectService

KEY = 'current_work.next_action'


class FieldExplanationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)
        self.root, _ = self.fixture.add('a')
        self.row = self.fixture.projects[0]
        self.row['hub_field_explanations'] = {'schema_version': '1.0', 'fields': {KEY: {
            'text': '下一步：复核候选 B 的变化。',
            'binding': {'source_ref': 'state', 'selector': {'path': ['current_work', 'next_action']},
                        'sha256': hashlib.sha256((self.root / 'STATE.yaml').read_bytes()).hexdigest(),
                        'value': 'Review candidate B'},
            'basis': [{'path': 'README.md', 'sha256': 'b' * 64, 'locator': 'Current work, lines 3-5'}]}}}
        self.fixture.save_registry()
        self.ledger = RefreshLedger(self.fixture.hub, result_validator=validate_result)
        self.service = ProjectService(self.fixture.hub)
        self.collect('initial')
        self.dto = self.service.get_project('a')
        self.record = self.dto['source']['source_record']
        self.authority = self.record['authority']

    def collect(self, request_id):
        result = refresh(self.ledger, SourceResolver(self.fixture.hub), request_id, ['a'])
        self.assertFalse(result['resolver_errors'])
        self.assertEqual(result['request']['status'], 'FINISHED')

    def project(self, row=None, record=None, freshness=None, authority=None, read=True):
        return field_explanations(row if row is not None else self.row,
            record if record is not None else self.record,
            freshness if freshness is not None else {'state': 'fresh', 'authority_drift': False},
            authority if authority is not None else self.authority, read)

    def test_matched_commentary_preserves_business_proof_and_has_registry_declaration(self):
        before = copy.deepcopy((self.row, self.record))
        result = self.project()
        item = result['fields'][KEY]
        self.assertEqual(result['project_id'], 'a')
        self.assertEqual(item['text'], '下一步：复核候选 B 的变化。')
        self.assertEqual(item['declaration'], {'kind': 'registry',
            'field': 'hub_field_explanations.fields.' + KEY,
            'sha256': self.authority['registry_hash'], 'registry_sha256': self.authority['registry_hash'],
            'basis': self.row['hub_field_explanations']['fields'][KEY]['basis']})
        self.assertEqual(self.dto['field_explanations'], result)
        self.assertEqual(self.dto['business']['fields'], self.record['business'])
        result['fields'][KEY]['binding']['selector']['path'].append('mutated')
        result['fields'][KEY]['declaration']['basis'][0]['locator'] = 'mutated'
        self.assertEqual((self.row, self.record), before)

    def test_work_strings_and_counting_basis_can_be_explained_without_other_fields(self):
        row = copy.deepcopy(self.row)
        fields = row['hub_field_explanations']['fields']
        for name in ('objective', 'phase', 'round'):
            key = 'current_work.' + name
            fields[key] = copy.deepcopy(fields[KEY])
            fields[key]['binding']['selector'] = {'path': ['current_work', name]}
            fields[key]['binding']['value'] = self.record['business']['current_work'][name]
        fields['progress.counting_basis'] = copy.deepcopy(fields[KEY])
        fields['progress.counting_basis']['binding']['selector'] = {'path': ['progress', 'counting_basis']}
        fields['progress.counting_basis']['binding']['value'] = self.record['business']['progress']['counting_basis']
        validate_registry_value({'projects': [row]})
        self.assertEqual(set(self.project(row=row)['fields']), {
            'current_work.objective', 'current_work.phase', 'current_work.round', KEY, 'progress.counting_basis'})

    def test_expired_or_failed_get_keeps_history_but_never_attaches_explanations(self):
        with mock.patch('hub.project_service._age', return_value=3601):
            stale = self.service.get_project('a')
        self.assertEqual(stale['freshness']['state'], 'stale')
        self.assertEqual(stale['field_explanations']['fields'], {})
        self.assertTrue(stale['last_success_snapshot']['available'])
        self.assertNotIn('field_explanations', stale['last_success_snapshot'])
        (self.root / 'STATE.yaml').unlink()
        self.collect('missing-source')
        failed = self.service.get_project('a')
        self.assertFalse(failed['source']['source_record']['success'])
        self.assertTrue(failed['last_success_snapshot']['available'])
        self.assertEqual(failed['field_explanations']['fields'], {})
        self.assertNotIn('field_explanations', failed['last_success_snapshot'])

    def test_no_source_reads_on_detail_or_listing(self):
        with mock.patch('hub.connection_sources._safe_relative_read', side_effect=AssertionError('GET read')), \
                mock.patch.object(SourceResolver, 'refresh', side_effect=AssertionError('GET refresh')):
            dto = self.service.get_project('a')
            listing = self.service.list_projects()['projects']
        self.assertIn(KEY, dto['field_explanations']['fields'])
        self.assertEqual(listing[0]['field_explanations'], dto['field_explanations'])

    def test_proof_value_source_and_project_mismatch_hide_without_mutation(self):
        for change in ('project', 'hash', 'selector', 'source_ref', 'value', 'proof', 'source', 'unknown', 'null'):
            with self.subTest(change=change):
                record = copy.deepcopy(self.record)
                if change == 'project': record['project_id'] = 'other'
                elif change == 'hash': record['field_provenance'][KEY]['sha256'] = 'c' * 64
                elif change == 'selector': record['field_provenance'][KEY]['selector'] = {'path': ['other']}
                elif change == 'source_ref': record['field_provenance'][KEY]['source_ref'] = 'other'
                elif change == 'value': record['business']['current_work']['next_action'] = 'New action'
                elif change == 'proof': record['field_provenance'].pop(KEY)
                elif change == 'source': record['sources'] = []
                elif change == 'unknown': record['unknown_fields'][KEY] = 'Unpublished'
                elif change == 'null': record['business']['current_work']['next_action'] = None
                before = copy.deepcopy(record)
                self.assertEqual(self.project(record=record)['fields'], {})
                self.assertEqual(record, before)

    def test_unavailable_history_failed_authority_and_removed_hide(self):
        for state in ('stale', 'unknown', 'removed_local'):
            with self.subTest(state=state):
                self.assertEqual(self.project(freshness={'state': state})['fields'], {})
        self.assertEqual(self.project(freshness={'state': 'fresh', 'authority_drift': True})['fields'], {})
        self.assertEqual(self.project(read=False)['fields'], {})
        authority = copy.deepcopy(self.authority)
        authority['registration_bindings']['a'] = 'd' * 64
        self.assertEqual(self.project(authority=authority)['fields'], {})
        failed = {**self.record, 'success': False, 'disposition': 'invalid'}
        self.assertEqual(self.project(record=failed)['fields'], {})
        for values in ({'local_presence': {'status': 'removed_local'}},
                       {'current_state_status': 'removed_local'}, {'connection_read_allowed': False},
                       {'access_profile': 'no_current_goal_access'}):
            with self.subTest(values=values):
                self.assertEqual(self.project(row={**self.row, **values})['fields'], {})

    def test_unrelated_registry_changes_retain_annotations_and_actual_dto_renders(self):
        import json, subprocess
        from pathlib import Path
        self.fixture.add('unrelated-b')
        self.fixture.projects[1]['name'] = 'Unrelated renamed project'
        self.fixture.save_registry()
        dto = self.service.get_project('a')
        self.assertNotEqual(dto['provenance']['registry_sha256'], self.record['authority']['registry_hash'])
        self.assertIn(KEY, dto['field_explanations']['fields'])
        self.assertEqual(dto['business']['source_record'], self.record)
        scoped = dto['provenance']['project_binding']
        self.assertEqual(set(scoped), {'version', 'project_id', 'registration_sha256', 'observed_sha256', 'comparison_basis'})
        self.assertEqual(scoped['comparison_basis'], 'registration_only')
        self.assertIsNone(scoped['observed_sha256'])
        module = (Path(__file__).resolve().parents[1] / 'src/hub/web/connection_view.mjs').as_uri()
        script = ("import {projectFacts} from " + json.dumps(module) + ";"
            "let input='';for await(const part of process.stdin)input+=part;"
            "const p=JSON.parse(input);console.log(JSON.stringify(projectFacts(p).flatMap(g=>g.fields).find(f=>f.key==='current_work.next_action')));")
        def project(value):
            result = subprocess.run(['node', '--input-type=module', '-e', script],
                input=json.dumps(value), text=True, capture_output=True, timeout=10)
            self.assertEqual(0, result.returncode, result.stderr)
            return json.loads(result.stdout)
        self.assertEqual(project(dto)['explanation']['text'], dto['field_explanations']['fields'][KEY]['text'])
        for name in ('registration', 'schema', 'adapter', 'unknown_version', 'missing_binding', 'permission'):
            with self.subTest(name=name):
                changed = copy.deepcopy(dto)
                if name == 'registration': changed['provenance']['project_binding']['registration_sha256'] = 'e' * 64
                elif name == 'schema': changed['provenance']['schema_sha256'] = 'e' * 64
                elif name == 'adapter': changed['provenance']['adapter_version'] = '99'
                elif name == 'unknown_version': changed['provenance']['project_binding']['version'] = '99'
                elif name == 'missing_binding': changed['provenance'].pop('project_binding')
                else: changed['declared']['connection_read_allowed'] = False
                self.assertIsNone(project(changed)['explanation'])

    def test_selected_shared_unknown_and_legacy_boundaries_reject(self):
        for name in ('registration', 'schema', 'adapter', 'unknown', 'missing', 'legacy'):
            with self.subTest(name=name):
                authority = copy.deepcopy(self.authority)
                if name == 'registration': authority['registration_bindings']['a'] = 'c' * 64
                elif name == 'schema': authority['schema_hash'] = 'c' * 64
                elif name == 'adapter': authority['adapter_version'] = '99'
                elif name == 'unknown': authority['binding_version'] = '99'
                elif name == 'missing': authority['registration_bindings'].pop('a'); authority['project_bindings'].pop('a')
                else: authority = {key: authority[key] for key in ('registry_hash', 'schema_hash', 'adapter_version')}
                self.assertEqual({}, self.project(authority=authority)['fields'])
        # Full audit changes alone do not change a selected project's permission.
        authority = copy.deepcopy(self.authority); authority['registry_hash'] = 'd' * 64
        self.assertIn(KEY, self.project(authority=authority)['fields'])

    def test_new_source_refresh_succeeds_but_old_commentary_is_absent(self):
        state = copy.deepcopy(self.fixture.business)
        state['current_work']['next_action'] = 'Review changed candidate C'
        (self.root / 'STATE.yaml').write_text(yaml.safe_dump(state))
        self.collect('changed')
        dto = self.service.get_project('a')
        self.assertEqual(dto['freshness']['state'], 'fresh')
        self.assertEqual(dto['business']['fields']['current_work']['next_action'], 'Review changed candidate C')
        self.assertEqual(dto['field_explanations']['fields'], {})
        validate_result(dto['source']['source_record'])

    def test_changed_source_sha_hides_even_if_bound_field_value_is_identical(self):
        with (self.root / 'STATE.yaml').open('a') as stream: stream.write('\n# Source revision\n')
        self.collect('hash-changed')
        dto = self.service.get_project('a')
        self.assertEqual(dto['business']['fields']['current_work']['next_action'], 'Review candidate B')
        self.assertEqual(dto['field_explanations']['fields'], {})

    def test_unconfigured_or_empty_explanations_are_optional(self):
        row = copy.deepcopy(self.row)
        row.pop('hub_field_explanations')
        self.assertEqual(self.project(row=row)['fields'], {})
        row['hub_field_explanations'] = {'schema_version': '1.0', 'fields': {}}
        validate_registry_value({'projects': [row]})
        self.assertEqual(self.project(row=row)['fields'], {})

    def test_malformed_declarations_rejected_before_publication(self):
        base = self.row['hub_field_explanations']
        mutations = [lambda x: x.update(extra=True), lambda x: x.update(schema_version='2.0'),
            lambda x: x.update(fields={'current_work.accepted': x['fields'][KEY]}),
            lambda x: x['fields'][KEY].update(extra=True),
            lambda x: x['fields'][KEY].update(text=''),
            lambda x: x['fields'][KEY].update(text='x' * 4001),
            lambda x: x['fields'][KEY]['binding'].update(value='x' * 16001),
            lambda x: x['fields'][KEY]['binding'].update(value=''),
            lambda x: x['fields'][KEY]['binding'].update(source_ref='../bad'),
            lambda x: x['fields'][KEY]['binding'].update(sha256='X' * 64),
            lambda x: x['fields'][KEY]['binding'].update(selector={'execute': 'anything'}),
            lambda x: x['fields'][KEY]['binding'].update(extra=True),
            lambda x: x['fields'][KEY].update(basis=[]),
            lambda x: x['fields'][KEY].update(basis=[x['fields'][KEY]['basis'][0]] * 9),
            lambda x: x['fields'][KEY]['basis'][0].update(path='../README.md'),
            lambda x: x['fields'][KEY]['basis'][0].update(path='/README.md'),
            lambda x: x['fields'][KEY]['basis'][0].update(path='.ssh/file.txt'),
            lambda x: x['fields'][KEY]['basis'][0].update(path='credentials.json'),
            lambda x: x['fields'][KEY]['basis'][0].update(path='file.py'),
            lambda x: x['fields'][KEY]['basis'][0].update(sha256='broken'),
            lambda x: x['fields'][KEY]['basis'][0].update(locator=''),
            lambda x: x['fields'][KEY]['basis'][0].update(locator='x' * 513),
            lambda x: x['fields'][KEY]['basis'][0].update(extra=True)]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index), self.assertRaises(RecordError):
                value = copy.deepcopy(base)
                mutate(value)
                validate_registry_value({'projects': [{**self.row, 'hub_field_explanations': value}]})
