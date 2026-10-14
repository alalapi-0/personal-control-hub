"""Synthetic contract tests; no live project or transport acceptance claims."""
import copy
import json
import unittest
from pathlib import Path

from connection_fixtures import Fixture, TIME
from hub.connection_records import FIELDS, RecordError, update_key, validate_result
from hub.connection_extensions import COUNTS, derive_progress, validate_extensions, validate_task_nodes

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


if __name__ == '__main__':
    unittest.main()
