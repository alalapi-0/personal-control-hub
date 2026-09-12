import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from hub.metric_fixture_report import collect_fixture_report, SOURCES, POSITIVE, NEGATIVE, _aggregate
from hub.metrics import validate_metric


class FixtureReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        files = {}
        for path in SOURCES:
            p = self.root / path
            p.parent.mkdir(exist_ok=True, parents=True)
            p.write_text('synthetic source')
            files[path] = hashlib.sha256(p.read_bytes()).hexdigest()
        self.report = dict(schema_version='rpn.client-fixture-result.v1', test_id='client-generation-placeholder',
            run_id='11111111-1111-1111-1111-111111111111', status='pass', exit_code=0, stage='complete',
            started_at='2026-09-09T00:00:00Z', finished_at='2026-09-09T00:00:20Z',
            manifest_count=9, artifact_count=9, cleanup='pass', event_stream_valid=True,
            positive=dict(expected=4, completed_count=4, completed=list(POSITIVE)),
            negative=dict(expected=12, completed_count=12, completed=list(NEGATIVE)),
            code=dict(files=files, stable=True, sha256=_aggregate(files), sha256_after=_aggregate(files)),
            modern=dict(binary_validation='pass', version='1.13.14', sha256_before='a'*64, sha256_after='a'*64, unchanged=True),
            legacy=dict(target='1.11.4', schema='pass', binary_validation='not_run'))

    def collect(self, report=None):
        (self.root / 'result.json').write_text(json.dumps(self.report if report is None else report))
        group = collect_fixture_report(self.root, 'rpn', '2026-09-09T00:01:00Z',
                                       dict(report_root=str(self.root), report_path='result.json'))
        for row in group['metrics']:
            validate_metric(row)
        return {x['metric_id'].replace('validation.client_fixture.', ''): x for x in group['metrics']}

    def test_saved_result_and_scope(self):
        rows = self.collect()
        self.assertEqual(rows['current_selected_code_passed']['value'], 1)
        self.assertEqual(rows['generated_artifacts']['value'], 9)
        self.assertEqual(rows['negative.completed']['value'], 12)
        self.assertEqual(rows['legacy_binary_checks_executed']['value'], 0)
        self.assertEqual(rows['duration']['value'], 20)
        self.assertEqual(rows['age']['value'], 40)
        self.assertEqual(rows['saved_run_passed']['business_at'], '2026-09-09T00:00:20Z')

    def test_source_change_keeps_historical_success(self):
        before = self.collect()
        (self.root / SOURCES[0]).write_text('changed source')
        after = self.collect()
        self.assertEqual(after['bound_to_selected_code']['value'], 0)
        self.assertIsNone(after['current_selected_code_passed']['value'])
        self.assertEqual(after['saved_run_passed']['value'], 1)
        self.assertEqual(after['generated_artifacts']['source_version'], before['generated_artifacts']['source_version'])
        self.assertEqual(after['saved_run_passed']['source_version'], before['saved_run_passed']['source_version'])

    def test_duplicate_identity_and_bad_count_isolate(self):
        self.report['negative']['completed'][0] = self.report['negative']['completed'][1]
        rows = self.collect()
        self.assertIsNone(rows['negative.completed']['value'])
        self.assertEqual(rows['positive.completed']['value'], 4)
        self.assertEqual(rows['generated_artifacts']['value'], 9)
        self.assertIsNone(rows['saved_run_passed']['value'])

    def test_bad_generated_count_preserves_manifest(self):
        self.report['artifact_count'] = 10
        rows = self.collect()
        self.assertEqual(rows['manifest_entries']['value'], 9)
        self.assertIsNone(rows['generated_artifacts']['value'])
        self.assertEqual(rows['positive.completed']['value'], 4)

    def test_early_failure_never_approves_unrun_checks(self):
        self.report.update(status='fail', exit_code=78, stage='storage', manifest_count=None, artifact_count=0, cleanup='not_created')
        self.report['positive'].update(completed=[], completed_count=0)
        self.report['negative'].update(completed=[], completed_count=0)
        self.report['modern']['binary_validation'] = 'not_completed'
        self.report['legacy']['schema'] = 'not_completed'
        rows = self.collect()
        self.assertEqual(rows['saved_run_passed']['value'], 0)
        self.assertIsNone(rows['manifest_entries']['value'])
        self.assertEqual(rows['positive.completed']['value'], 0)

    def test_invalid_fingerprint_cannot_read_report_selected_path(self):
        self.report['code']['files']['../../credentials.json'] = 'a' * 64
        rows = self.collect()
        self.assertIsNone(rows['bound_to_selected_code']['value'])
        self.assertEqual(rows['generated_artifacts']['value'], 9)
        self.assertNotIn('credentials.json', json.dumps(rows))

    def test_budget_and_private_fields(self):
        self.report['raw_config'] = 'PRIVATE_PLACEHOLDER'
        self.assertNotIn('PRIVATE_PLACEHOLDER', json.dumps(self.collect()))
        self.report['raw_config'] = 'x' * 8192
        self.assertIsNone(self.collect()['generated_artifacts']['value'])


if __name__ == '__main__':
    unittest.main()
