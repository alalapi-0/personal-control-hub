"""Forecasts stay typed, bounded and separate from actual acceptance."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.connection_records import RecordError
from hub.rolling_plan import validate_rolling_plan


def rolling_plan():
    return {'schema_version': '1.0', 'kind': 'rolling_estimate', 'as_of': '2026-10-15',
            'next_review': '2026-10-22', 'completion_window': None, 'confidence': 'low',
            'basis': [{'kind': 'source', 'reference': 'source-state', 'sha256': 'a'*64, 'summary': 'Current stage remains open'},
                      {'kind': 'assumption', 'reference': 'weekly-review', 'sha256': None, 'summary': 'Owner availability unknown'}],
            'conditions': ['Resume after owner review'],
            'next_milestone': {'id': 'next', 'label': 'Next increment', 'window': {'earliest': '2026-10-16', 'latest': '2026-10-23'}, 'condition': None},
            'acceptance_windows': [
                {'id': 'auto', 'kind': 'automatic', 'label': 'Checks', 'window': None, 'depends_on': ['next'], 'scope': 'Automatic checks'},
                {'id': 'human', 'kind': 'human', 'label': 'Owner review', 'window': None, 'depends_on': ['auto'], 'scope': 'Owner verifies runtime'}]}


class RollingPlanTests(unittest.TestCase):
    def reject_changes(self, mutations):
        for label, change in mutations:
            with self.subTest(label=label):
                value = rolling_plan(); change(value)
                with self.assertRaises(RecordError): validate_rolling_plan(value)

    def test_null_unknown_and_detached_forecast(self):
        self.assertIsNone(validate_rolling_plan(None))
        value = rolling_plan(); checked = validate_rolling_plan(value)
        self.assertEqual(checked, value)
        checked['basis'][0]['summary'] = 'Changed'
        self.assertNotEqual(checked, value)
        value.update(confidence='unknown', basis=[], conditions=[], next_milestone=None, acceptance_windows=[])
        self.assertEqual(validate_rolling_plan(value), value)

    def test_historical_expected_window_is_literal_and_never_actual(self):
        value = rolling_plan(); value['completion_window'] = {'earliest': '2020-01-01', 'latest': '2020-01-02'}
        self.assertEqual(validate_rolling_plan(value)['completion_window'], value['completion_window'])
        for field in ('actual_start', 'actual_end', 'accepted_at', 'completed'):
            with self.subTest(field=field), self.assertRaises(RecordError):
                validate_rolling_plan(dict(value, **{field: '2026-10-15'}))

    def test_calendar_review_and_ranges_are_strict(self):
        self.reject_changes([
            ('invalid day', lambda v: v.update(as_of='2026-02-30')),
            ('timestamp', lambda v: v.update(as_of='2026-10-15T00:00:00Z')),
            ('null date', lambda v: v.update(as_of=None)),
            ('past review', lambda v: v.update(next_review='2026-10-14')),
            ('reversed window', lambda v: v.update(completion_window={'earliest': '2026-10-17', 'latest': '2026-10-16'})),
            ('null endpoint', lambda v: v.update(completion_window={'earliest': None, 'latest': '2026-10-16'})),
            ('extra endpoint', lambda v: v.update(completion_window={'earliest': '2026-10-16', 'latest': '2026-10-17', 'actual': True})),
        ])

    def test_source_requires_hash_assumptions_and_bounds(self):
        self.reject_changes([
            ('source no hash', lambda v: v['basis'][0].update(sha256=None)),
            ('bad hash', lambda v: v['basis'][0].update(sha256='z'*64)),
            ('hash not string', lambda v: v['basis'][0].update(sha256=1)),
            ('empty reference', lambda v: v['basis'][0].update(reference='  ')),
            ('long summary', lambda v: v['basis'][0].update(summary='x'*501)),
            ('wrong basis kind', lambda v: v['basis'][0].update(kind='actual')),
            ('basis capacity', lambda v: v.update(basis=v['basis']*11)),
            ('condition capacity', lambda v: v.update(conditions=['x']*21)),
            ('blank condition', lambda v: v.update(conditions=[' '])),
            ('wrong confidence', lambda v: v.update(confidence='certain')),
        ])

    def test_acceptance_ids_references_and_cycles_are_rejected(self):
        self.reject_changes([
            ('milestone id collision', lambda v: v['acceptance_windows'][0].update(id='next')),
            ('duplicate acceptance', lambda v: v['acceptance_windows'][1].update(id='auto')),
            ('self dependency', lambda v: v['acceptance_windows'][0].update(depends_on=['auto'])),
            ('missing dependency', lambda v: v['acceptance_windows'][0].update(depends_on=['absent'])),
            ('cycle', lambda v: v['acceptance_windows'][0].update(depends_on=['human'])),
            ('duplicate dependency', lambda v: v['acceptance_windows'][0].update(depends_on=['next', 'next'])),
            ('wrong kind', lambda v: v['acceptance_windows'][0].update(kind='engineering_actual')),
            ('actual receipt', lambda v: v['acceptance_windows'][0].update(accepted_at='2026-10-15')),
            ('empty scope', lambda v: v['acceptance_windows'][0].update(scope=' ')),
            ('acceptance capacity', lambda v: v.update(acceptance_windows=v['acceptance_windows']*11)),
        ])

    def test_acceptance_forward_dependency_and_shared_milestone_are_allowed(self):
        value = rolling_plan()
        value['acceptance_windows'].reverse()
        value['acceptance_windows'][0]['depends_on'].append('next')
        self.assertEqual(validate_rolling_plan(value), value)

    def test_milestone_null_and_bounds_are_strict(self):
        self.reject_changes([
            ('blank label', lambda v: v['next_milestone'].update(label=' ')),
            ('invalid id', lambda v: v['next_milestone'].update(id='bad id')),
            ('blank condition', lambda v: v['next_milestone'].update(condition=' ')),
            ('unknown field', lambda v: v['next_milestone'].update(accepted=False)),
            ('null acceptance', lambda v: v.update(acceptance_windows=None)),
        ])

    def test_human_history_and_automatic_forecast_associations_remain_literal_and_validated(self):
        value=rolling_plan()
        value['acceptance_windows'][0]['depends_on']=['human']
        value['acceptance_windows'][1]['depends_on']=['next']
        before=copy.deepcopy(value);checked=validate_rolling_plan(value)
        self.assertEqual(checked,before);self.assertEqual(value,before)
        self.assertEqual(checked['acceptance_windows'][0]['depends_on'],['human'])
        self.assertEqual(checked['acceptance_windows'][1]['kind'],'human')
        self.assertEqual(checked['acceptance_windows'][1]['window'],None)
        self.assertEqual((checked['as_of'],checked['basis'],checked['acceptance_windows'][1]['scope']),
                         (before['as_of'],before['basis'],before['acceptance_windows'][1]['scope']))
        for target in ('missing','auto'):
            bad=copy.deepcopy(value);bad['acceptance_windows'][1]['depends_on']=[target]
            with self.subTest(target=target),self.assertRaises(RecordError):validate_rolling_plan(bad)


if __name__ == '__main__': unittest.main()
