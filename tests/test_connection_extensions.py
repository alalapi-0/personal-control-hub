"""Synthetic contract tests; no live project or transport acceptance claims."""
import copy
import json
import unittest
from pathlib import Path
from unittest import mock

import yaml

from connection_fixtures import Fixture, TIME
from hub.connection_records import FIELDS, RecordError, content_hash, record_schema, update_key, validate_result
from hub.connection_extensions import (COUNTS, derive_progress, validate_extension_declaration,
                                       validate_extensions, validate_record_extensions, validate_task_nodes)

HASH = 'a' * 64
CONTEXT = {'state': {'sha256': HASH, 'observed_at': TIME, 'format': 'yaml'}}


def field(value):
    return {'value': value, 'unknown_reason': 'not published' if value is None else None,
            'proof': None if value is None else {'source_ref': 'state', 'sha256': HASH,
                'observed_at': TIME, 'selector': {'path': ['fact']}}}


def envelope(**facts):
    fields = {k: field(v) for k, v in facts.items()}
    for key, wrapped in fields.items():
        if wrapped['proof'] is not None:
            wrapped['proof']['selector'] = {'path': ['fact', key]}
    return {'schema_version': '1.0', 'fact': fields,
            'managed': {}, 'derived': {}, 'proposed': {}}


def metric(n, **kwargs):
    return {'value': n, 'unit': 'task', 'plan_version': '1.0', 'counting_basis': 'leaf',
            'baseline_denominator': 6, 'unknown_reason': 'not published' if n is None else None, **kwargs}


def node(n, parent=None, kind='leaf', deps=None):
    return {'id': n, 'parent': parent, 'kind': kind, 'dependencies': deps or [],
            'status': 'not_started', 'condition': None, 'evidence': []}


def page_inventory(project_id='a'):
    return {'schema_version': '1.0', 'project_id': project_id,
            'domains': [{'domain': 'implementation', 'known_page_count': 1,
                         'completeness': 'partial', 'counting_basis': 'Declared routes and dialogs',
                         'pages': [{'id': 'home', 'label': 'Home', 'kind': 'route',
                                    'source_sha256': HASH, 'build_sha256': None}]},
                        {'domain': 'design', 'known_page_count': None,
                         'completeness': 'unknown', 'counting_basis': 'Design pages not declared', 'pages': []}],
            'previews': [{'id': 'home-preview', 'project_id': project_id,
                          'domain': 'implementation', 'page_id': 'home',
                          'artifact_ref': 'data/previews/home.png', 'artifact_sha256': 'b' * 64,
                          'captured_at': None, 'source_sha256': HASH, 'build_sha256': None}]}


class ExtensionTests(unittest.TestCase):
    def validate(self, data, domain='source', source_context=None):
        # Synthetic observed input, not a live source or transport assertion.
        context = {'state': {**CONTEXT['state'], 'data': {'fact': {
            k: copy.deepcopy(v['value']) for k, v in data['fact'].items()}}}}
        return validate_extensions(data, source_context=context if source_context is None else source_context, domain=domain)

    def test_absent_preserves_legacy_21_fields_result_and_hash(self):
        fixture = Fixture()
        try:
            fixture.add()
            result = fixture.resolver().refresh('a')
            before = json.dumps(result, sort_keys=True)
            key = update_key(result)
            self.assertIsNone(validate_extensions(None))
            self.assertEqual(len(FIELDS), 21)
            validate_result(result)
            self.assertEqual(before, json.dumps(result, sort_keys=True))
            self.assertEqual(key, update_key(result))
            self.assertEqual(result['schema_version'], '2.0')
        finally:
            fixture.close()

    def test_known_false_empty_and_unknown_are_distinct(self):
        data = envelope(has_ui=False, task_nodes=[], usable_versions=[], fact_updated_at=None)
        validated = self.validate(data)
        self.assertEqual(validated, data)
        self.assertIs(validated['fact']['has_ui']['value'], False)
        self.assertEqual(validated['fact']['task_nodes']['value'], [])
        validated['fact']['task_nodes']['value'].append({})
        self.assertEqual(data['fact']['task_nodes']['value'], [])
        for reason in (None, '', '  '):
            data['fact']['fact_updated_at']['unknown_reason'] = reason
            with self.assertRaises(RecordError):
                self.validate(data)

    def test_fact_proof_requires_matching_context(self):
        original = envelope(has_ui=False)
        for name, bad in [('sha256', 'b' * 64), ('observed_at', '2026-09-09T00:00:00Z'),
                          ('source_ref', 'other'), ('selector', {'heading': 'State', 'label': 'UI'})]:
            data = copy.deepcopy(original)
            data['fact']['has_ui']['proof'][name] = bad
            with self.subTest(name=name), self.assertRaises(RecordError):
                self.validate(data)
        with self.assertRaises(RecordError):
            validate_extensions(original)

    def test_forged_value_or_wrong_selector_cannot_reuse_valid_hash(self):
        original = envelope(has_ui=False, plan_version='1.0')
        context = {'state': {**CONTEXT['state'], 'data': {'fact': {'has_ui': False, 'plan_version': '1.0'}}}}
        self.validate(original, source_context=context)
        forged = copy.deepcopy(original)
        forged['fact']['has_ui']['value'] = True
        with self.assertRaises(RecordError):
            self.validate(forged, source_context=context)
        wrong_selector = copy.deepcopy(original)
        wrong_selector['fact']['has_ui']['proof']['selector'] = {'path': ['fact', 'plan_version']}
        with self.assertRaises(RecordError):
            self.validate(wrong_selector, source_context=context)
        absent = copy.deepcopy(original)
        absent['fact']['has_ui']['proof']['selector'] = {'path': ['missing']}
        with self.assertRaises(RecordError):
            self.validate(absent, source_context=context)
        with self.assertRaises(RecordError):
            self.validate(original, source_context=CONTEXT)
        context['state']['data']['fact']['has_ui'] = 0
        with self.assertRaises(RecordError):
            self.validate(original, source_context=context)

    def test_canonical_evidence_preserves_nested_numeric_types(self):
        original = envelope(usable_versions=[{'version': '1.0', 'scope': 'core', 'verified': False, 'evidence': []}])
        observed = copy.deepcopy(original['fact']['usable_versions']['value'])
        observed[0]['verified'] = 0
        context = {'state': {**CONTEXT['state'], 'data': {'fact': {'usable_versions': observed}}}}
        with self.assertRaises(RecordError):
            self.validate(original, source_context=context)

    def test_four_authority_domains_do_not_allow_source_impersonation(self):
        for domain, payload in [('managed', {'watch': False}),
                                ('derived', {'progress': {}}),
                                ('proposed', {'watch': {'value': True, 'status': 'proposed', 'reason': 'Owner review'}})]:
            data = envelope()
            data[domain] = payload
            with self.subTest(domain=domain), self.assertRaises(RecordError):
                self.validate(data)
        data = envelope()
        data['managed'] = {'watch': False, 'planned_start': None, 'priority': 0}
        data['proposed'] = {'watch': {'value': True, 'status': 'proposed', 'reason': 'Owner review'}}
        self.assertEqual(self.validate(data, 'hub'), data)
        data['proposed']['watch']['status'] = 'accepted'
        with self.assertRaises(RecordError):
            self.validate(data, 'hub')

    def test_task_reference_and_cycle_validation(self):
        valid = [node('root', kind='project'), node('stage', 'root', 'stage'), node('a', 'stage'), node('b', 'stage', deps=['a'])]
        validate_task_nodes(valid)
        mutations = [lambda x: x.append(copy.deepcopy(x[-1])),
                     lambda x: x[-1].update(parent='missing'),
                     lambda x: x[-1].update(dependencies=['b']),
                     lambda x: x[-1].update(dependencies=['a', 'a']),
                     lambda x: x[2].update(dependencies=['b']),
                     lambda x: x[0].update(parent='stage'),
                     lambda x: x[-1].update(parent='a')]
        for change in mutations:
            bad = copy.deepcopy(valid)
            change(bad)
            with self.subTest(change=change), self.assertRaises(RecordError):
                validate_task_nodes(bad)

    def test_deep_tree_is_not_limited_by_python_recursion(self):
        nodes = [node('n0', kind='project')]
        nodes += [node(f'n{i}', f'n{i-1}', 'stage') for i in range(1, 1500)]
        validate_task_nodes(nodes)

    def test_version_evidence_binds_exact_version_and_scope(self):
        item = {'version': '1.0', 'scope': 'local core', 'verified': True,
                'evidence': [{'version': '1.0', 'scope': 'local core', 'status': 'passed', 'ref': 'tests/core.json'}]}
        self.validate(envelope(usable_versions=[item]))
        for mutate in [lambda x: x['evidence'][0].update(version='2.0'),
                       lambda x: x['evidence'][0].update(scope='runtime'),
                       lambda x: x['evidence'][0].update(status='pending'),
                       lambda x: x.update(evidence=[]), lambda x: x.update(verified=1)]:
            bad = copy.deepcopy(item)
            mutate(bad)
            with self.subTest(mutate=mutate), self.assertRaises(RecordError):
                self.validate(envelope(core_capabilities=[bad]))

    def test_progress_original_denominator_zero_and_unknown(self):
        self.assertEqual(derive_progress(metric(0), metric(6))['ratio'], 0)
        zero = derive_progress(metric(0), metric(0))
        self.assertIsNone(zero['ratio'])
        self.assertEqual(zero['unknown_reason'], 'not_applicable')
        unknown = derive_progress(metric(None), metric(6))
        self.assertIsNone(unknown['numerator'])
        self.assertIsNone(unknown['ratio'])
        self.assertEqual(unknown['denominator'], 6)
        for key, bad in [('unit', 'video'), ('plan_version', '2.0'),
                         ('counting_basis', 'parent'), ('baseline_denominator', 111)]:
            with self.subTest(key=key), self.assertRaises(RecordError):
                derive_progress(metric(0), metric(6, **{key: bad}))
        for bad in [-1, 1.5, True, float('inf'), float('nan')]:
            with self.subTest(bad=bad), self.assertRaises(RecordError):
                derive_progress(metric(bad), metric(6))
        with self.assertRaises(RecordError):
            derive_progress(metric(7), metric(6))

    def test_parent_and_leaf_are_not_double_counted(self):
        counts = {'counts': {k: metric(2 if k == 'total' else 0) for k in COUNTS}, 'counted_node_ids': ['a', 'b']}
        data = envelope(task_nodes=[node('root', kind='project'), node('a', 'root'), node('b', 'root')], progress_counts=counts)
        self.validate(data)
        for ids in [['root', 'a'], ['a', 'a'], ['a'], ['missing', 'a']]:
            bad = copy.deepcopy(data)
            bad['fact']['progress_counts']['value']['counted_node_ids'] = ids
            with self.subTest(ids=ids), self.assertRaises(RecordError):
                self.validate(bad)

    def test_derived_progress_cannot_promote_implementation_to_verified(self):
        counts = {'counts': {k: metric(6 if k in {'total', 'implemented'} else 0) for k in COUNTS}, 'counted_node_ids': []}
        data = envelope(progress_counts=counts)
        data['derived']['progress'] = derive_progress(counts['counts']['verified'], counts['counts']['total'])
        self.validate(data, 'hub')
        data['derived']['progress']['numerator'] = 6
        data['derived']['progress']['ratio'] = 1
        with self.assertRaises(RecordError):
            self.validate(data, 'hub')

    def test_legacy_versions_keep_exact_fact_sets_and_canonical_hashes(self):
        baseline_hashes = {
            '1.0': '07614f95862f4560fc94b1d86b6274ae40331265ac25e8eea78dfb2bf4e9baaf',
            '1.1': '40e98513c145ec20af242a427d057096afca55cd20c49037f0703bb1c3e3ee74',
            '1.2': 'ee71a46194aaeffa663475e15b71cabdc447fddf28c5076775c2f241b73cd54a',
        }
        source = {'id': 'state', 'format': 'yaml'}
        for version, expected_hash in baseline_hashes.items():
            with self.subTest(version=version):
                data = envelope(has_ui=False, task_nodes=[], fact_updated_at=None)
                data['schema_version'] = version
                before = copy.deepcopy(data)
                self.assertEqual(expected_hash, content_hash(self.validate(data)))
                self.assertEqual(before, data)
                with mock.patch('builtins.open', side_effect=AssertionError('legacy history source reopen')), \
                     mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('legacy history source reopen')):
                    history = validate_record_extensions(data,
                        [{'id': 'state', 'sha256': HASH, 'format': 'yaml'}], TIME)
                self.assertEqual(before, history)
                self.assertEqual(expected_hash, content_hash(history))
                # Even unknown new fields may not leak into old versions.
                for new_field in ({'1.0': ('task_plan', 'task_timeline', 'page_inventory'),
                                   '1.1': ('task_timeline', 'page_inventory'),
                                   '1.2': ('page_inventory',)}[version]):
                    invalid = copy.deepcopy(data)
                    invalid['fact'][new_field] = field(None)
                    with self.assertRaises(RecordError): self.validate(invalid)
                    declaration = {'schema_version': version,
                        'fact_mapping': {new_field: {'source_ref': 'state', 'selector': {'path': [new_field]}}},
                        'unknown_fields': {new_field: 'Not published'}}
                    with self.assertRaises(RecordError):
                        validate_extension_declaration(declaration, source)

    def test_page_inventory_13_literal_values_are_detached_with_existing_proof_rules(self):
        data = envelope(page_inventory=page_inventory())
        data['schema_version'] = '1.3'
        before = copy.deepcopy(data)
        validated = self.validate(data)
        self.assertEqual(before, validated)
        validated['fact']['page_inventory']['value']['domains'][0]['pages'].clear()
        self.assertEqual(before, data)
        context = {'state': {**CONTEXT['state'], 'data': {'fact': {'page_inventory': page_inventory()}}}}
        for mode in ('sha256', 'observed_at', 'source_ref', 'selector-format', 'selector-value', 'value'):
            invalid = copy.deepcopy(data)
            proof = invalid['fact']['page_inventory']['proof']
            if mode == 'sha256': proof['sha256'] = 'f' * 64
            elif mode == 'observed_at': proof['observed_at'] = '2020-01-01T00:00:00Z'
            elif mode == 'source_ref': proof['source_ref'] = 'other'
            elif mode == 'selector-format': proof['selector'] = {'heading': 'Pages', 'label': 'Inventory'}
            elif mode == 'selector-value': proof['selector'] = {'path': ['fact']}
            else: invalid['fact']['page_inventory']['value']['domains'][0]['pages'][0]['label'] = 'Forged home'
            with self.subTest(mode=mode), self.assertRaises(RecordError):
                self.validate(invalid, source_context=context)
        for authority in ('managed', 'derived', 'proposed'):
            invalid = copy.deepcopy(data)
            invalid[authority] = {'page_inventory': page_inventory()}
            with self.subTest(authority=authority), self.assertRaises(RecordError): self.validate(invalid)

    def test_page_inventory_13_unknown_and_strict_shape(self):
        unknown = envelope(page_inventory=None)
        unknown['schema_version'] = '1.3'
        self.assertEqual(unknown, self.validate(unknown))
        for mode in ('unknown-proof', 'unknown-reason', 'extra-field', 'preview-project', 'missing-domain'):
            data = copy.deepcopy(unknown)
            if mode == 'unknown-proof': data['fact']['page_inventory']['proof'] = field(False)['proof']
            elif mode == 'unknown-reason': data['fact']['page_inventory']['unknown_reason'] = None
            else:
                data['fact']['page_inventory'] = field(page_inventory())
                data['fact']['page_inventory']['proof']['selector'] = {'path': ['fact', 'page_inventory']}
                value = data['fact']['page_inventory']['value']
                if mode == 'extra-field': value['credentials'] = 'forbidden'
                elif mode == 'preview-project': value['previews'][0]['project_id'] = 'other'
                else: value['domains'].pop()
            with self.subTest(mode=mode), self.assertRaises(RecordError): self.validate(data)

    def test_page_inventory_declaration_remains_literal_single_source_only(self):
        declaration = {'schema_version': '1.3',
            'fact_mapping': {'page_inventory': {'source_ref': 'state', 'selector': {'path': ['pages']}}},
            'unknown_fields': {'page_inventory': 'No published page inventory'}}
        source = {'id': 'state', 'format': 'yaml'}
        self.assertEqual(declaration, validate_extension_declaration(declaration, source))
        for mode in ('source', 'selector', 'authority', 'unknown-field', 'reason'):
            invalid = copy.deepcopy(declaration)
            if mode == 'source': invalid['fact_mapping']['page_inventory']['source_ref'] = 'other'
            elif mode == 'selector': invalid['fact_mapping']['page_inventory']['selector'] = {'heading': 'Pages', 'label': 'Inventory'}
            elif mode == 'authority': invalid['managed'] = {'watch': True}
            elif mode == 'unknown-field': invalid['fact_mapping']['capture_policy'] = invalid['fact_mapping']['page_inventory']
            else: invalid['unknown_fields'] = {}
            with self.subTest(mode=mode), self.assertRaises(RecordError):
                validate_extension_declaration(invalid, source)

    def test_page_inventory_record_identity_and_offline_history_binding(self):
        fixture = Fixture()
        self.addCleanup(fixture.close)
        root, declaration = fixture.add('a')
        declaration['extensions'] = {'schema_version': '1.3',
            'fact_mapping': {'page_inventory': {'source_ref': 'state', 'selector': {'path': ['pages']}}},
            'unknown_fields': {'page_inventory': 'Not published'}}
        (root / 'hub.connection.yaml').write_text(yaml.safe_dump(declaration))
        state = {**fixture.business, 'pages': page_inventory()}
        (root / 'STATE.yaml').write_text(yaml.safe_dump(state))
        record = fixture.resolver().refresh('a')
        self.assertTrue(record['success'], record['errors'])
        self.assertEqual('2.0', record['schema_version'])
        self.assertEqual(21, len(FIELDS))
        self.assertEqual(fixture.business, record['business'])
        before = copy.deepcopy(record)
        with mock.patch('builtins.open', side_effect=AssertionError('offline source reopen')), \
             mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('offline source reopen')):
            self.assertEqual(before, validate_result(record))
            self.assertEqual(before['extensions'], validate_record_extensions(
                record['extensions'], record['sources'], record['observed_at']))
        self.assertEqual(before, record)
        invalid = copy.deepcopy(record)
        inventory = invalid['extensions']['fact']['page_inventory']['value']
        inventory['project_id'] = inventory['previews'][0]['project_id'] = 'other'
        invalid['update_key'] = update_key(invalid)
        with self.assertRaisesRegex(RecordError, 'page inventory project mismatch'):
            validate_result(invalid)
        for bad_proof in ('sha256', 'observed_at'):
            invalid = copy.deepcopy(record)
            invalid['extensions']['fact']['page_inventory']['proof'][bad_proof] = (
                'f' * 64 if bad_proof == 'sha256' else '2020-01-01T00:00:00Z')
            invalid['update_key'] = update_key(invalid)
            with self.subTest(proof=bad_proof), self.assertRaises(RecordError): validate_result(invalid)
        self.assertEqual(['1.0', '1.1', '1.2', '1.3', '1.4'], record_schema()['optional_extensions']['versions'])


if __name__ == '__main__':
    unittest.main()
