import json
import tempfile
import unittest
from pathlib import Path
from hub.metric_saved_tools import collect_downloader, collect_workspace_checks, LOG_BYTES, LOG_LINES

NOW = '2026-09-09T00:00:00Z'


class SavedToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def put(self, path, obj):
        dest = self.root/path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(obj))

    def collect(self, fn):
        result = fn(self.root, 'fixture', NOW, {})
        return result, {r['metric_id'].split('.', 1)[1]: r for r in result['metrics']}

    def downloader(self):
        self.failures = {'https://PRIVATE/identity': {'last_failed_at': 1773400000, 'has_partial': False, 'reason': 'SECRET'}}
        self.cache = {'PRIVATE_KEY': {'status': 'usable', 'cached_at': 1773400000, 'last_verified_at': 1773400001, 'last_status_at': 1773400002, 'video': {'url': 'https://PRIVATE/video'}}}
        self.put('state/failed_jobs.json', self.failures)
        self.put('state/plan_cache.json', self.cache)
        self.log([{'event': 'download_start', 'ts': 1773400000, 'url': 'https://PRIVATE'}, {'event': 'download_start', 'ts': 1773400000}, {'event': 'probe_failed', 'ts': 1773400001}])

    def log(self, rows):
        (self.root/'state').mkdir(exist_ok=True)
        (self.root/'state/run_log.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))

    def workspace(self):
        self.protocol = {'projects': {'PRIVATE_PROJECT': {'presence': {'PRIVATE_PATH': True, 'b': False}, 'versions': {'a': {'exists': True}}, 'local_override_issues': {'file': ['SECRET', 'SECRET']}}}}
        self.comparison = {'projects': {'PRIVATE_PROJECT': {'versions': {'a': {'exists': True}}, 'template_coverage': {'template': {'real_exists': True, 'candidate_exists': False, 'real_path': 'PRIVATE'}}}}}
        self.put('reports/project_protocol_check.json', self.protocol)
        self.put('reports/protocol_version_comparison.json', self.comparison)

    def test_saved_semantics_privacy_event_multiplicity(self):
        self.downloader()
        result, rows = self.collect(collect_downloader)
        for name, n in [('saved_latest_failure_records',1),('saved_failure_partial_records',0),('saved_event_lines',3),('saved_event_download_start',2),('saved_event_download_success',0),('saved_log_coverage_complete',1)]:
            self.assertEqual(rows[name]['value'], n)
        self.assertIsNotNone(rows['saved_failure_latest_at']['business_at'])
        self.assertIsNone(rows['current_code_bound']['value'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('SECRET', json.dumps(result))

    def test_semantic_versions(self):
        self.downloader()
        first, base = self.collect(collect_downloader)
        self.failures[next(iter(self.failures))]['reason'] = 'other'
        self.cache['PRIVATE_KEY']['video'] = {'url': 'https://DIFFERENT'}
        self.put('state/failed_jobs.json',self.failures)
        self.put('state/plan_cache.json',self.cache)
        second, _ = self.collect(collect_downloader)
        self.assertEqual(first['source_version'], second['source_version'])
        self.cache['PRIVATE_KEY']['status'] = 'suspected_expired'
        self.put('state/plan_cache.json', self.cache)
        third, changed = self.collect(collect_downloader)
        self.assertNotEqual(second['source_version'],third['source_version'])
        self.assertEqual(base['saved_latest_failure_records']['source_version'],changed['saved_latest_failure_records']['source_version'])
        self.cache['PRIVATE_KEY']['cached_at'] += 1
        self.put('state/plan_cache.json',self.cache)
        fourth, _ = self.collect(collect_downloader)
        self.assertNotEqual(third['source_version'],fourth['source_version'])

    def test_bad_field_and_read_isolation(self):
        self.downloader()
        self.cache['PRIVATE_KEY'].update(status='PRIVATE_UNKNOWN',cached_at='SECRET')
        self.put('state/plan_cache.json',self.cache)
        self.log([{'event':'download_success','ts':'bad'}])
        result, rows = self.collect(collect_downloader)
        self.assertEqual(rows['saved_plan_records']['value'],1)
        self.assertIsNone(rows['saved_plan_usable']['value'])
        self.assertIsNone(rows['saved_plan_latest_cached_at']['value'])
        self.assertIsNotNone(rows['saved_plan_latest_last_verified_at']['value'])
        self.assertEqual(rows['saved_event_download_success']['value'],1)
        self.assertIsNone(rows['saved_log_latest_at']['value'])
        self.assertNotIn('SECRET',json.dumps(result))
        (self.root/'state/failed_jobs.json').unlink()
        _, rows = self.collect(collect_downloader)
        self.assertIsNone(rows['saved_latest_failure_records']['value'])
        self.assertEqual(rows['saved_plan_records']['value'],1)

    def test_log_budget_bad_event_and_malformed_line(self):
        self.downloader()
        for data in (' '*(LOG_BYTES+1),'\n'*(LOG_LINES+1)):
            (self.root/'state/run_log.jsonl').write_text(data)
            _, rows = self.collect(collect_downloader)
            self.assertEqual(rows['saved_log_coverage_complete']['value'],0)
            self.assertIsNone(rows['saved_event_lines']['value'])
            self.assertIsNone(rows['saved_event_download_success']['value'])
            self.assertEqual(rows['saved_latest_failure_records']['value'],1)
        for data in ('{"event":42,"ts":1773400000}\n','not json\n', '{"event":"download_success","event":"download_start"}\n'):
            (self.root/'state/run_log.jsonl').write_text(data)
            result, rows = self.collect(collect_downloader)
            self.assertEqual(rows['saved_event_lines']['value'],1)
            self.assertIsNone(rows['saved_event_download_success']['value'])
            self.assertNotIn('PRIVATE',json.dumps(result))

    def test_duplicate_identity_source_isolation(self):
        self.downloader()
        (self.root/'state/failed_jobs.json').write_text('{"duplicate":{},"duplicate":{}}')
        _, rows = self.collect(collect_downloader)
        self.assertIsNone(rows['saved_latest_failure_records']['value'])
        self.assertEqual(rows['saved_plan_records']['value'],1)

    def test_workspace_scope_unknowns_privacy(self):
        self.workspace()
        result, rows = self.collect(collect_workspace_checks)
        for name,n in [('protocol_projects_checked',1),('protocol_presence_checks',2),('protocol_presence_present',1),('protocol_presence_absent',1),('protocol_version_file_checks',1),('comparison_version_file_checks',1),('protocol_override_issue_items',2),('comparison_template_real_exists_present',1),('comparison_template_candidate_exists_absent',1),('protocol_versions_judgable',0)]:
            self.assertEqual(rows[name]['value'],n,name)
        self.assertIsNone(rows['protocol_stale_versions']['value'])
        self.assertTrue(all(r['business_at'] is None for r in rows.values()))
        self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertNotIn('SECRET',json.dumps(result))

    def test_workspace_field_report_and_version_isolation(self):
        self.workspace()
        first, base = self.collect(collect_workspace_checks)
        project = self.protocol['projects']['PRIVATE_PROJECT']
        project['local_override_issues']['file'][0] = 'new text'
        self.put('reports/project_protocol_check.json',self.protocol)
        second, _ = self.collect(collect_workspace_checks)
        self.assertEqual(first['source_version'],second['source_version'])
        project['presence']['PRIVATE_PATH'] = 'invalid'
        self.put('reports/project_protocol_check.json',self.protocol)
        third, rows = self.collect(collect_workspace_checks)
        self.assertIsNone(rows['protocol_presence_present']['value'])
        self.assertEqual(rows['protocol_presence_checks']['value'],2)
        self.assertEqual(rows['protocol_version_file_present']['value'],1)
        self.assertEqual(base['comparison_version_file_present']['source_version'],rows['comparison_version_file_present']['source_version'])
        self.assertNotEqual(second['source_version'],third['source_version'])
        (self.root/'reports/project_protocol_check.json').unlink()
        _, rows = self.collect(collect_workspace_checks)
        self.assertIsNone(rows['protocol_projects_checked']['value'])
        self.assertEqual(rows['comparison_projects_checked']['value'],1)

    def test_unclassified_string_preserves_known_event_counts(self):
        self.downloader()
        self.log([{'event': 'PRIVATE_EVENT', 'ts': 1773400000}, {'event': 'download_start', 'ts': 1773400001}])
        result, rows = self.collect(collect_downloader)
        self.assertEqual(rows['saved_event_unclassified']['value'], 1)
        self.assertEqual(rows['saved_event_download_start']['value'], 1)
        self.assertEqual(rows['saved_event_download_success']['value'], 0)
        self.assertNotIn('PRIVATE_EVENT', json.dumps(result))
        self.log([{'ts': 1773400000}])
        _, rows = self.collect(collect_downloader)
        self.assertEqual(rows['saved_event_lines']['value'], 1)
        self.assertIsNone(rows['saved_event_download_success']['value'])

    def test_record_count_time_binding_and_bad_time_isolation(self):
        self.downloader()
        _, before = self.collect(collect_downloader)
        for name in ('saved_latest_failure_records', 'saved_plan_records', 'saved_plan_usable', 'saved_event_download_start', 'saved_event_lines'):
            self.assertIsNotNone(before[name]['business_at'], name)
        self.assertIsNone(before['saved_event_download_success']['business_at'])
        self.failures[next(iter(self.failures))]['last_failed_at'] += 1
        self.put('state/failed_jobs.json', self.failures)
        _, after = self.collect(collect_downloader)
        self.assertNotEqual(before['saved_latest_failure_records']['source_version'], after['saved_latest_failure_records']['source_version'])
        self.assertNotEqual(before['saved_latest_failure_records']['business_at'], after['saved_latest_failure_records']['business_at'])
        self.cache['PRIVATE_KEY']['last_status_at'] = 'bad'
        self.put('state/plan_cache.json', self.cache)
        _, after = self.collect(collect_downloader)
        self.assertEqual(after['saved_plan_usable']['value'], 1)
        self.assertIsNone(after['saved_plan_usable']['business_at'])
        self.assertEqual(after['saved_plan_usable']['dimensions'], before['saved_plan_usable']['dimensions'])
        self.cache['PRIVATE_KEY']['last_status_at'] = 10 ** 400
        self.put('state/plan_cache.json', self.cache)
        _, after = self.collect(collect_downloader)
        self.assertEqual(after['saved_plan_usable']['value'], 1)
        self.assertIsNone(after['saved_plan_usable']['business_at'])
        del self.cache['PRIVATE_KEY']['last_status_at']
        self.put('state/plan_cache.json', self.cache)
        _, after = self.collect(collect_downloader)
        self.assertIsNotNone(after['saved_plan_usable']['business_at'])

    def test_dynamic_stale_coverage_and_target_version(self):
        self.workspace()
        self.protocol['global_protocol_version'] = 'v1.2'
        versions = self.protocol['projects']['PRIVATE_PROJECT']['versions']
        versions['a']['stale'] = True
        self.put('reports/project_protocol_check.json', self.protocol)
        _, first = self.collect(collect_workspace_checks)
        self.assertEqual(first['protocol_versions_judgable']['value'], 1)
        self.assertEqual(first['protocol_stale_versions']['value'], 1)
        versions['b'] = {'exists': False}
        self.put('reports/project_protocol_check.json', self.protocol)
        _, rows = self.collect(collect_workspace_checks)
        self.assertEqual(rows['protocol_versions_judgable']['value'], 1)
        self.assertIsNone(rows['protocol_stale_versions']['value'])
        versions['b']['stale'] = False
        self.put('reports/project_protocol_check.json', self.protocol)
        result, rows = self.collect(collect_workspace_checks)
        self.assertEqual(rows['protocol_versions_judgable']['value'], 2)
        self.assertEqual(rows['protocol_stale_versions']['value'], 1)
        self.protocol['global_protocol_version'] = 'v1.3'
        self.put('reports/project_protocol_check.json', self.protocol)
        changed, new = self.collect(collect_workspace_checks)
        self.assertNotEqual(result['source_version'], changed['source_version'])
        self.assertNotEqual(rows['protocol_presence_present']['dimensions'], new['protocol_presence_present']['dimensions'])
        self.assertEqual(rows['comparison_version_file_present'], new['comparison_version_file_present'])
        self.assertIsNone(new['current_code_bound']['value'])
        self.protocol['global_protocol_version'] = 'https://PRIVATE/SECRET'
        self.put('reports/project_protocol_check.json', self.protocol)
        result, rows = self.collect(collect_workspace_checks)
        self.assertEqual(rows['protocol_presence_present']['dimensions']['target_protocol_version'], 'unknown')
        self.assertNotIn('SECRET', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
