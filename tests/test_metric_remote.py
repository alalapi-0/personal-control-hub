import json
import unittest
import subprocess
import sys
from unittest.mock import patch

from hub.metric_remote import ApiResponse, GitHubSharedClient, collect_remote, RemoteError, _api
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
        self.assertEqual(values['github_ci.runs.reported_total'], 301)
        self.assertEqual(values['github_ci.runs.observed'], 300)
        self.assertEqual(values['github_ci.pages.observed'], 3)

    def test_schema_and_target_corruption(self):
        for key, bad in [('head_sha', PRIMARY), ('run_attempt', True)]:
            record = run_record()
            record[key] = bad
            client, _ = self.client([record])
            result = self.collect(client, github_ci=True)
            self.assertTrue(result['issues'])
            values = self.values(result)
            self.assertTrue(all(values[mid] is None for mid in (
                'github_ci.runs.total', 'github_ci.runs.success', 'github_ci.runs.failure',
                'github_ci.runs.pending', 'github_ci.runs.cancelled', 'github_ci.runs.other')))
            self.assertEqual(values['github_ci.complete'], 0)
            self.assertEqual(values['github_ci.pages.observed'], 0)

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
        response_text = 'HTTP/2 200 OK\nETag: "v1"\nX-RateLimit-Remaining: 10\n\n{}'
        with patch('hub.metric_remote.subprocess.Popen', side_effect=spawn(f'print({response_text!r})')):
            response = _api('repos/owner/repo')
            self.assertEqual(response.payload, {})
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers['etag'], '"v1"')
        self.assertEqual(calls[0][0], ['gh', 'api', '--hostname', 'github.com', '--method', 'GET', '--include', 'repos/owner/repo'])
        self.assertEqual(calls[0][1]['stderr'], subprocess.DEVNULL)
        not_modified = 'HTTP/2 304 Not Modified\nCache-Control: max-age=60\n\n'
        with patch('hub.metric_remote.subprocess.Popen', side_effect=spawn(f'print({not_modified!r})')):
            response = _api('repos/owner/repo', {'If-None-Match': '"v1"'})
            self.assertEqual(response.status, 304)
        self.assertEqual(calls[1][0][-3:], ['--header', 'If-None-Match: "v1"', 'repos/owner/repo'])
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
        ci = [row for row in a['metrics'] if row['metric_id'].startswith('github_ci')]
        self.assertTrue(all(row['dimensions']['candidate_binding'] == 'exact_commit' for row in ci))
        self.assertTrue(all(row['dimensions']['check_scope'] == 'all_workflow_runs' for row in ci))
        self.assertTrue(all(row['dimensions']['required_checks_claimed'] is False for row in ci))
        self.assertTrue(all(row['source_ref'].endswith('@' + HEAD) for row in ci))

    def test_complete_two_page_listing(self):
        calls = []
        def request(path):
            calls.append(path)
            page = int(path.rsplit('=', 1)[1])
            records = ([run_record(i) for i in range(1, 101)] if page == 1
                       else [run_record(i) for i in range(101, 151)])
            return dict(total_count=150, workflow_runs=records)
        values = self.values(self.collect(request, github_ci=True))
        self.assertEqual(len(calls), 2)
        self.assertEqual(values['github_ci.complete'], 1)
        self.assertEqual(values['github_ci.runs.total'], 150)
        self.assertEqual(values['github_ci.runs.reported_total'], 150)
        self.assertEqual(values['github_ci.runs.observed'], 150)
        self.assertEqual(values['github_ci.pages.observed'], 2)

    def test_rate_limit_mid_pagination_preserves_coverage_not_results(self):
        calls = []
        def request(path):
            calls.append(path)
            if path.endswith('page=2'):
                raise RemoteError('github_rate_limited')
            return dict(total_count=150, workflow_runs=[run_record(i) for i in range(1, 101)])
        result = self.collect(request, github_ci=True)
        values = self.values(result)
        self.assertEqual(len(calls), 2)
        self.assertEqual(values['github_ci.complete'], 0)
        self.assertEqual(values['github_ci.runs.reported_total'], 150)
        self.assertEqual(values['github_ci.runs.observed'], 100)
        self.assertEqual(values['github_ci.pages.observed'], 1)
        self.assertTrue(all(values['github_ci.runs.' + key] is None for key in
                            ('total', 'success', 'failure', 'pending', 'cancelled', 'other')))
        self.assertIn('github_rate_limited', [item['code'] for item in result['issues']])

    def test_shared_client_deduplicates_and_revalidates_etag(self):
        clock = [0.0]
        calls = []
        payload = {'default_branch': 'trunk'}
        def transport(endpoint, headers):
            calls.append((endpoint, headers))
            if len(calls) == 1:
                return ApiResponse(200, {'etag': '"v1"', 'cache-control': 'max-age=10'}, payload)
            return ApiResponse(304, {'cache-control': 'max-age=20'}, None)
        client = GitHubSharedClient(transport, monotonic=lambda: clock[0], wallclock=lambda: 0)
        self.assertEqual(client('repos/owner/repo'), payload)
        payload['default_branch'] = 'mutated'
        self.assertEqual(client('repos/owner/repo'), {'default_branch': 'trunk'})
        self.assertEqual(len(calls), 1)
        clock[0] = 11
        self.assertEqual(client('repos/owner/repo'), {'default_branch': 'trunk'})
        self.assertEqual(calls[1][1], {'If-None-Match': '"v1"'})
        clock[0] = 25
        self.assertEqual(client('repos/owner/repo'), {'default_branch': 'trunk'})
        self.assertEqual(len(calls), 2)

    def test_same_repository_collections_share_all_requests(self):
        backend, backend_calls = self.client([run_record()])
        transport_calls = []
        def transport(endpoint, headers):
            transport_calls.append((endpoint, headers))
            return ApiResponse(200, {'etag': '"stable"', 'cache-control': 'max-age=60'}, backend(endpoint))
        shared = GitHubSharedClient(transport, monotonic=lambda: 0, wallclock=lambda: 0)
        first = self.collect(shared, remote_git=True, github_ci=True)
        second = self.collect(shared, remote_git=True, github_ci=True)
        self.assertEqual(self.values(first), self.values(second))
        self.assertEqual(len(backend_calls), 4)
        self.assertEqual(len(transport_calls), 4)

    def test_shared_client_rate_backoff_blocks_new_requests(self):
        clock = [0.0]
        calls = []
        def transport(endpoint, headers):
            calls.append((endpoint, headers))
            return ApiResponse(200, {'x-ratelimit-remaining': '0', 'retry-after': '30'}, {'ok': True})
        client = GitHubSharedClient(transport, monotonic=lambda: clock[0], wallclock=lambda: 0)
        self.assertEqual(client('repos/owner/first'), {'ok': True})
        with self.assertRaisesRegex(RemoteError, '^github_rate_limited$'):
            client('repos/owner/second')
        self.assertEqual(len(calls), 1)
        self.assertEqual(client('repos/owner/first'), {'ok': True})

    def test_rate_backoff_starts_after_response_and_covers_secondary_limit(self):
        for status, headers in (
            (429, {'retry-after': '5'}),
            (403, {'retry-after': '5', 'x-ratelimit-remaining': '4999'}),
        ):
            with self.subTest(status=status):
                clock = [0.0]
                calls = []
                def transport(endpoint, request_headers):
                    calls.append((endpoint, request_headers))
                    clock[0] = 10.0
                    return ApiResponse(status, headers, {'message': 'not retained'})
                client = GitHubSharedClient(transport, monotonic=lambda: clock[0], wallclock=lambda: 0)
                with self.assertRaisesRegex(RemoteError, '^github_rate_limited$'):
                    client('repos/owner/first')
                clock[0] = 14.0
                with self.assertRaisesRegex(RemoteError, '^github_rate_limited$'):
                    client('repos/owner/second')
                self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
