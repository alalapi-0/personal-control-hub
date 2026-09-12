import json
import unittest
import subprocess
import sys
from unittest.mock import patch

from hub.metric_remote import collect_remote, RemoteError, _api
from hub.metrics import validate_metric

HEAD, PRIMARY, OTHER = 'a' * 40, 'b' * 40, 'c' * 40
STAMP = '2026-09-09T01:00:00+00:00'


def run_record(i=1, conclusion='success', attempt=1):
    return dict(id=i, workflow_id=12, run_attempt=attempt,
                status='completed' if conclusion else 'queued', conclusion=conclusion,
                head_sha=HEAD, created_at=STAMP, updated_at=STAMP, run_started_at=STAMP)


class RemoteTests(unittest.TestCase):
    def collect(self, client, **kwargs):
        with patch('hub.metric_remote._head', return_value=HEAD), patch('hub.metric_remote._now', return_value=STAMP):
            result = collect_remote('.', 'demo', STAMP, kwargs.pop('spec', {'github_repository': 'owner/repo'}), client=client, **kwargs)
        for metric in result['metrics']:
            validate_metric(metric)
        return result

    def client(self, runs=None, ahead=2, behind=0, failures=None):
        calls = []
        def request(path):
            calls.append(path)
            if failures and any(s in path for s in failures):
                raise PermissionError('secret-token-and-url')
            if '/actions/runs?' in path:
                return dict(total_count=len(runs or []), workflow_runs=runs or [])
            if '/branches/' in path:
                self.assertTrue(path.endswith('/branches/trunk'))
                return {'commit': {'sha': PRIMARY}}
            if '/compare/' in path:
                self.assertTrue(path.endswith(HEAD + '...' + PRIMARY))
                return dict(base_commit={'sha': HEAD}, merge_base_commit={'sha': HEAD if not behind else OTHER},
                            ahead_by=ahead, behind_by=behind,
                            status='diverged' if ahead and behind else 'ahead' if ahead else 'behind' if behind else 'identical')
            return {'default_branch': 'trunk', 'description': 'do not expose'}
        return request, calls

    @staticmethod
    def values(result):
        return {m['metric_id']: m['value'] for m in result['metrics']}

    def test_no_optin_and_invalid_binding_do_not_call(self):
        def forbidden(_):
            self.fail('network must not run')
        self.assertEqual(self.collect(forbidden)['disposition'], 'disabled')
        for binding in (None, 'https://token@github.com/o/r', 'o/r?token=x', 'o/..', 'o/r/x'):
            result = self.collect(forbidden, spec={'github_repository': binding}, remote_git=True, github_ci=True)
            self.assertTrue(all(m['value'] is None for m in result['metrics']))
            self.assertNotIn('token', json.dumps(result))

    def test_actual_primary_github_only_compare_and_directions(self):
        client, calls = self.client()
        result = self.collect(client, remote_git=True)
        values = self.values(result)
        self.assertEqual(values['remote_git.main.contains_head'], 1)
        self.assertEqual(values['remote_git.main.ahead'], 0)
        self.assertEqual(values['remote_git.main.behind'], 2)
        self.assertEqual(len(calls), 3)
        self.assertNotIn('do not expose', json.dumps(result))
        client, _ = self.client(ahead=3, behind=4)
        values = self.values(self.collect(client, remote_git=True))
        self.assertEqual(values['remote_git.main.contains_head'], 0)
        self.assertEqual(values['remote_git.main.ahead'], 4)

    def test_groups_independent_and_sanitized(self):
        for failing in ('/actions/', '/compare/'):
            client, _ = self.client(failures=[failing])
            result = self.collect(client, remote_git=True, github_ci=True)
            values = self.values(result)
            self.assertNotIn('secret', json.dumps(result))
            self.assertEqual(values['github_ci.runs.total'], None if failing == '/actions/' else 0)
            self.assertEqual(values['remote_git.main.contains_head'], None if failing == '/compare/' else 1)
        def timeout(_):
            raise TimeoutError('secret')
        self.assertEqual(self.collect(timeout, remote_git=True)['issues'][0]['code'], 'github_timeout')

    def test_zero_runs_is_zero_and_mixed_outcomes(self):
        client, _ = self.client()
        values = self.values(self.collect(client, github_ci=True))
        self.assertEqual(values['github_ci.runs.total'], 0)
        self.assertEqual(values['github_ci.runs.success'], 0)
        self.assertEqual(values['github_ci.complete'], 1)
        client, _ = self.client([run_record(i, conclusion) for i, conclusion in enumerate(('success', 'failure', None, 'cancelled', 'neutral', 'timed_out'), 1)])
        values = self.values(self.collect(client, github_ci=True))
        self.assertEqual(values['github_ci.runs.total'], 6)
        self.assertEqual(values['github_ci.runs.failure'], 2)
        self.assertEqual(values['github_ci.runs.pending'], 1)

    def test_latest_attempt_and_truncation(self):
        def duplicate(_):
            return dict(total_count=1, workflow_runs=[run_record(attempt=1, conclusion='failure'), run_record(attempt=2)])
        values = self.values(self.collect(duplicate, github_ci=True))
        self.assertEqual(values['github_ci.runs.success'], 1)
        self.assertEqual(values['github_ci.runs.failure'], 0)
        calls = []
        def many(path):
            calls.append(path)
            page = int(path.rsplit('=', 1)[1])
            return dict(total_count=301, workflow_runs=[run_record(i) for i in range((page-1)*100+1, page*100+1)])
        values = self.values(self.collect(many, github_ci=True))
        self.assertEqual(len(calls), 3)
        self.assertEqual(values['github_ci.complete'], 0)
        self.assertIsNone(values['github_ci.runs.total'])
        self.assertIsNone(values['github_ci.runs.success'])

    def test_schema_and_target_corruption(self):
        for key, bad in [('head_sha', PRIMARY), ('run_attempt', True)]:
            record = run_record()
            record[key] = bad
            client, _ = self.client([record])
            result = self.collect(client, github_ci=True)
            self.assertTrue(result['issues'])
            self.assertTrue(all(m['value'] is None for m in result['metrics']))

    def test_optional_times_preserve_count_facts(self):
        for key in ('created_at', 'updated_at', 'run_started_at'):
            for bad in ('malformed-secret', None):
                record = run_record()
                if bad is None:
                    del record[key]
                else:
                    record[key] = bad
                client, _ = self.client([record])
                result = self.collect(client, github_ci=True)
                values = self.values(result)
                self.assertEqual(values['github_ci.runs.total'], 1)
                self.assertEqual(values['github_ci.runs.success'], 1)
                self.assertEqual(values['github_ci.complete'], 1)
                self.assertNotIn('malformed-secret', json.dumps(result))
                if key == 'updated_at':
                    rows = [r for r in result['metrics'] if r['metric_id'].startswith('github_ci.runs.')]
                    self.assertTrue(all(r['business_at'] is None for r in rows))
                if key != 'run_started_at' or bad is not None:
                    self.assertIn('github_ci_time_unknown', [i['code'] for i in result['issues']])

    def test_unknown_outcomes_preserve_identity_total_and_completeness(self):
        for key, bad in [('status', 'secret-unknown'), ('conclusion', 'secret-unknown'), ('status', []), ('conclusion', {})]:
            record = run_record()
            record[key] = bad
            client, _ = self.client([record])
            result = self.collect(client, github_ci=True)
            values = self.values(result)
            self.assertEqual(values['github_ci.runs.total'], 1)
            self.assertEqual(values['github_ci.complete'], 1)
            for category in ('success', 'failure', 'pending', 'cancelled', 'other'):
                self.assertIsNone(values['github_ci.runs.' + category])
            self.assertNotIn('secret-unknown', json.dumps(result))
            self.assertIn('github_ci_status_unknown', [i['code'] for i in result['issues']])

    def test_query_clock_does_not_change_non_observation_semantics(self):
        client, _ = self.client([run_record()])
        results = []
        for stamp in (STAMP, '2026-09-09T03:00:00+00:00'):
            with patch('hub.metric_remote._head', return_value=HEAD), patch('hub.metric_remote._now', return_value=stamp):
                results.append(collect_remote('.', 'demo', stamp, {'github_repository': 'owner/repo'}, remote_git=True, github_ci=True, client=client))
        semantic = lambda result: [{k: v for k, v in row.items() if k != 'observed_at'} for row in result['metrics'] if not row['metric_id'].endswith('observed_timestamp')]
        self.assertEqual(semantic(results[0]), semantic(results[1]))
        # Empty CI listings likewise have no observation clock masquerading as event time.
        client, _ = self.client([])
        with patch('hub.metric_remote._head', return_value=HEAD), patch('hub.metric_remote._now', return_value='2026-09-10T03:00:00+00:00'):
            empty = collect_remote('.', 'demo', STAMP, {'github_repository': 'owner/repo'}, github_ci=True, client=client)
        self.assertTrue(all(r['business_at'] is None for r in empty['metrics'] if not r['metric_id'].endswith('observed_timestamp')))

    def test_head_race_invalidates_dependent_groups(self):
        client, _ = self.client()
        with patch('hub.metric_remote._head', side_effect=[HEAD, PRIMARY]):
            result = collect_remote('.', 'demo', STAMP, {'github_repository': 'owner/repo'}, remote_git=True, github_ci=True, client=client)
        self.assertTrue(all(m['value'] is None for m in result['metrics']))
        self.assertTrue(all(m['reason'] == 'local_head_changed' for m in result['metrics']))

    def test_transport_argv_timeout_and_output_cap(self):
        original = subprocess.Popen
        calls = []
        def spawn(code):
            def fake(argv, **kwargs):
                calls.append((argv, kwargs))
                return original([sys.executable, '-c', code], **kwargs)
            return fake
        with patch('hub.metric_remote.subprocess.Popen', side_effect=spawn('print("{}")')):
            self.assertEqual(_api('repos/owner/repo'), {})
        self.assertEqual(calls[0][0], ['gh', 'api', '--hostname', 'github.com', '--method', 'GET', 'repos/owner/repo'])
        self.assertEqual(calls[0][1]['stderr'], subprocess.DEVNULL)
        with patch('hub.metric_remote.subprocess.Popen', side_effect=spawn('print("x" * 1000)')), patch('hub.metric_remote.MAX_OUTPUT_BYTES', 100):
            with self.assertRaisesRegex(RemoteError, '^github_output_limit$'):
                _api('repos/owner/repo')
        with patch('hub.metric_remote.subprocess.Popen', side_effect=spawn('import time; time.sleep(2)')), patch('hub.metric_remote.TIMEOUT_SECONDS', .03):
            with self.assertRaisesRegex(RemoteError, '^github_timeout$'):
                _api('repos/owner/repo')

    def test_ci_update_does_not_change_git_fields(self):
        first, _ = self.client([run_record(conclusion=None)])
        second, _ = self.client([run_record()])
        a = self.collect(first, remote_git=True, github_ci=True)
        b = self.collect(second, remote_git=True, github_ci=True)
        git = lambda r: [m for m in r['metrics'] if m['metric_id'].startswith('remote_git')]
        self.assertEqual(git(a), git(b))
        self.assertNotEqual(a['source_version'], b['source_version'])
        for row in a['metrics']:
            self.assertNotIn(HEAD, str(row['dimensions']))


if __name__ == '__main__':
    unittest.main()
