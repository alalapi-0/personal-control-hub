"""Exact Markdown facts preserve source text and fail closed on ambiguity."""
import hashlib
import json
import unittest
from pathlib import Path

import yaml

from connection_fixtures import Fixture
from hub.connection_records import RecordError, record_schema, validate_selector
from hub.connections import select_value

PHASE = '**2：当前候选已准备，待用户审核**；历史范围保留'
NEXT = '用户首审 **故事MVP**，选择下一步。'
SOURCE = f'''# STATUS
## Snapshot
| Field | Value |
| --- | --- |
| Phase | {PHASE} |
| Status | waiting_review |

## Next (ordered)
1. {NEXT}
2. 保留资料。
## History
| Field | Value |
| --- | --- |
| Phase | old |
1. old
'''


class MarkdownSelectorTests(unittest.TestCase):
    def test_boolean_label_is_explicit_and_preserves_string_label_behavior(self):
        selector = {'heading': 'Interface', 'boolean_label': 'has_ui'}
        for literal, expected in [('true', True), ('false', False)]:
            source = '## Interface\n- has_ui: ' + literal + '\n'
            self.assertIs(expected, select_value(source, selector))
            self.assertEqual(literal, select_value(source, {'heading': 'Interface', 'label': 'has_ui'}))
        for literal in ['False', 'FALSE', '0', '1', 'yes', 'no', 'null', '"false"', '**false**', 'false # note', '']:
            with self.subTest(literal=literal), self.assertRaises(RecordError):
                select_value('## Interface\nhas_ui: ' + literal, selector)
        for source in ['## Interface\nhas_ui: false\nhas_ui: true',
                       '## Interface\n```\nhas_ui: false\n```',
                       '## Other\nhas_ui: false',
                       '## Interface\nhas_ui: false\n## Interface\nhas_ui: false']:
            with self.subTest(source=source), self.assertRaises(RecordError):
                select_value(source, selector)
        for invalid in [{**selector, 'label': 'has_ui'}, {**selector, 'extra': False},
                        {'heading': 'Interface', 'boolean_label': ''}]:
            with self.subTest(selector=invalid), self.assertRaises(RecordError):
                validate_selector(invalid)

    def test_boolean_extension_reselects_observed_markdown_and_fails_closed(self):
        from copy import deepcopy
        from hub.connection_extensions import validate_extensions
        from hub.connection_records import validate_result
        fixture = Fixture()
        try:
            root, declaration = fixture.add()
            declaration['source_refs'][0].update(path='STATUS.md', format='markdown')
            declaration['mapping'] = {}
            declaration['extensions'] = {'schema_version': '1.0', 'fact_mapping': {
                'has_ui': {'source_ref': 'state', 'selector': {'heading': 'Interface', 'boolean_label': 'has_ui'}}},
                'unknown_fields': {'has_ui': 'Current interface not declared'}}
            fixture.projects[0]['current_state_paths'] = ['STATUS.md']
            fixture.save_registry()
            source = '## Interface\nhas_ui: false\n'
            (root / 'STATUS.md').write_text(source)
            (root / 'hub.connection.yaml').write_text(yaml.safe_dump(declaration))
            result = fixture.resolver().refresh('a')
            self.assertTrue(result['success'], result['errors'])
            field = result['extensions']['fact']['has_ui']
            self.assertIs(False, field['value'])
            self.assertEqual(hashlib.sha256(source.encode()).hexdigest(), field['proof']['sha256'])
            self.assertEqual(result, validate_result(result))
            context = {'state': {'sha256': field['proof']['sha256'], 'observed_at': result['observed_at'],
                                 'format': 'markdown', 'data': source}}
            for changed in ['value', 'selector']:
                forged = deepcopy(result['extensions'])
                if changed == 'value':
                    forged['fact']['has_ui']['value'] = True
                else:
                    forged['fact']['has_ui']['proof']['selector'] = {'heading': 'Interface', 'label': 'has_ui'}
                with self.subTest(changed=changed), self.assertRaises(RecordError):
                    validate_extensions(forged, source_context=context)
            (root / 'STATUS.md').write_text('## Interface\nhas_ui: False\n')
            failed = fixture.resolver().refresh('a')
            self.assertFalse(failed['success'])
            self.assertNotIn('extensions', failed)
        finally:
            fixture.close()

    def test_exact_table_and_ordered_text_preserves_markup(self):
        self.assertEqual(PHASE, select_value(SOURCE, {'heading': 'Snapshot', 'table_label': 'Phase'}))
        self.assertEqual(NEXT, select_value(SOURCE, {'heading': 'Next (ordered)', 'ordered_item': 1}))
        escaped = '## S\n| Key | Value |\n| --- | :---: |\n| Phase | a\\|b |'
        self.assertEqual('a\\|b', select_value(escaped, {'heading': 'S', 'table_label': 'Phase'}))

    def test_table_absence_duplicates_and_malformed_structures_fail(self):
        cases = [SOURCE.replace('| Phase | ' + PHASE + ' |', '| phase | other |'),
                 SOURCE.replace('| Status | waiting_review |', '| Phase | duplicate |'),
                 SOURCE.replace('| --- | --- |', '| bad | --- |', 1),
                 SOURCE.replace('| Phase | ' + PHASE + ' |', '| Phase | one | two |'),
                 SOURCE.replace('| Phase | ' + PHASE + ' |', '| Phase | |'),
                 SOURCE + '\n## Snapshot\n',
                 SOURCE.replace('| Status | waiting_review |', '| --- | --- |'),
                 SOURCE.replace('| Phase | ' + PHASE + ' |', '| Phase | broken\\|')]
        for source in cases:
            with self.subTest(source=source), self.assertRaises(RecordError):
                select_value(source, {'heading': 'Snapshot', 'table_label': 'Phase'})

    def test_section_boundary_excludes_other_section_decoys(self):
        source = SOURCE.replace('## Next (ordered)', '## Other\n| Phase | duplicate |\n## Next (ordered)')
        self.assertEqual(PHASE, select_value(source, {'heading': 'Snapshot', 'table_label': 'Phase'}))

    def test_ordered_absence_duplicate_nested_multiline_fail(self):
        for body in ('2. other', '1. one\n1. duplicate', '  1. nested',
                     '1. one\n  continuation', '1. one\n\n  continuation',
                     '1. one\ncontinuation', '1. one\n  2. nested', '1. '):
            with self.subTest(body=body), self.assertRaises(RecordError):
                select_value('## Next\n' + body, {'heading': 'Next', 'ordered_item': 1})

    def test_fenced_decoys_do_not_supply_headings_or_facts(self):
        for marker in ('```', '~~~~'):
            decoy = f'{marker}\n## Snapshot\n| Field | Value |\n| --- | --- |\n| Phase | wrong |\n## Next (ordered)\n1. wrong\n{marker}\n'
            self.assertEqual(PHASE, select_value(decoy + SOURCE, {'heading': 'Snapshot', 'table_label': 'Phase'}))
            self.assertEqual(NEXT, select_value(decoy + SOURCE, {'heading': 'Next (ordered)', 'ordered_item': 1}))
            with self.assertRaises(RecordError):
                select_value(decoy, {'heading': 'Snapshot', 'table_label': 'Phase'})
        with self.assertRaises(RecordError):
            select_value('## Next\n```\n1. fake\n```', {'heading': 'Next', 'ordered_item': 1})

    def test_huge_source_list_number_is_not_cast_or_confused_with_target(self):
        self.assertEqual('actual', select_value('## Next\n' + '9' * 5000 + '. decoy\n1. actual', {'heading': 'Next', 'ordered_item': 1}))

    def test_old_label_selector_stays_exact_and_ignores_fences(self):
        selector = {'heading': 'Current', 'label': 'Status'}
        source = '# Current\n```\n# Current\nStatus: fake\n```\n- Status: **active**\n# History\nStatus: old'
        self.assertEqual('**active**', select_value(source, selector))
        with self.assertRaises(RecordError):
            select_value('# Current\nStatus: one\nStatus: two', selector)

    def test_exact_keys_and_types_apply_at_selector_boundary(self):
        for selector in ({'heading': 'S', 'ordered_item': True},
                         {'heading': 'S', 'ordered_item': 0},
                         {'heading': 'S', 'ordered_item': -1},
                         {'heading': 'S', 'ordered_item': '1'},
                         {'heading': 'S', 'ordered_item': 1.0},
                         {'heading': 'S', 'table_label': ''},
                         {'heading': 'S', 'table_label': []},
                         {'heading': 'S', 'table_label': 'Phase', 'label': 'Phase'},
                         {'heading': 'S', 'ordered_item': 1, 'extra': None}):
            with self.subTest(selector=selector), self.assertRaises(RecordError):
                validate_selector(selector)
            with self.subTest(selector=selector), self.assertRaises(RecordError):
                select_value(SOURCE, selector)
        self.assertIsNone(select_value({'branch': None}, {'path': ['branch', 'status']}))
        self.assertEqual(False, select_value({'flag': False}, {'path': ['flag']}))

    def test_resolver_provenance_value_map_and_unknown_progress(self):
        fixture = Fixture()
        try:
            root, declaration = fixture.add()
            declaration['source_refs'][0].update(path='STATUS.md', format='markdown')
            declaration['mapping'] = {
                'current_work.phase': {'source_ref': 'state', 'selector': {'heading': 'Snapshot', 'table_label': 'Phase'}, 'value_map': None},
                'current_work.status': {'source_ref': 'state', 'selector': {'heading': 'Snapshot', 'table_label': 'Status'}, 'value_map': {'waiting_review': 'blocked'}},
                'current_work.next_action': {'source_ref': 'state', 'selector': {'heading': 'Next (ordered)', 'ordered_item': 1}, 'value_map': None},
            }
            fixture.projects[0]['current_state_paths'] = ['STATUS.md']
            fixture.save_registry()
            (root / 'STATUS.md').write_text(SOURCE)
            (root / 'hub.connection.yaml').write_text(yaml.safe_dump(declaration))
            result = fixture.resolver().refresh('a')
            self.assertTrue(result['success'], result['errors'])
            self.assertEqual('blocked', result['business']['current_work']['status'])
            self.assertEqual(PHASE, result['business']['current_work']['phase'])
            self.assertEqual(NEXT, result['business']['current_work']['next_action'])
            digest = hashlib.sha256((root / 'STATUS.md').read_bytes()).hexdigest()
            for field, raw in (('current_work.phase', PHASE), ('current_work.status', 'waiting_review'), ('current_work.next_action', NEXT)):
                rule = declaration['mapping'][field]
                self.assertEqual({'source_ref': 'state', 'sha256': digest, 'selector': rule['selector'],
                                  'value_map': rule['value_map'], 'raw_value': raw}, result['field_provenance'][field])
            for field in ('completed', 'total'):
                self.assertIsNone(result['business']['progress'][field])
                self.assertIn('progress.' + field, result['unknown_fields'])
                self.assertNotIn('progress.' + field, result['field_provenance'])
            declaration['mapping']['current_work.next_action']['selector']['ordered_item'] = True
            (root / 'hub.connection.yaml').write_text(yaml.safe_dump(declaration))
            invalid = fixture.resolver().refresh('a')
            self.assertFalse(invalid['success'])
            self.assertEqual({}, invalid['field_provenance'])
        finally:
            fixture.close()

    def test_generated_contract_snapshot_matches(self):
        path = Path(__file__).resolve().parents[1] / 'data/connections/schema.json'
        self.assertEqual(record_schema(), json.loads(path.read_text()))
