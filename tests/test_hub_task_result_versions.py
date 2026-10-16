"""Fixed-result reads use existing task snapshots, without executing or archiving."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.local_service import HubHTTPServer
from hub.owner_auth import OwnerAuth
from hub.service_contract import ServiceError
import test_hub_task_runtime as runtime
import test_hub_local_service as http_fixture


class TaskResultVersionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = runtime.TaskRuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store, self.service = self.fixture.store, self.fixture.service
        self.service.submit(self.fixture.payload)

    def complete(self):
        self.fixture.worker().run_one()
        task = self.service.get('fixture-task')['task']
        self.assertEqual(task['status'], 'checks_complete')
        return task

    def read(self, version):
        return self.service.get('fixture-task', expected_result_version=version)

    def test_unversioned_compatibility_and_exact_version_are_read_only(self):
        task = self.complete()
        before = (self.store.path.read_bytes(), self.store.task('fixture-task'),
                  self.store.events('fixture-task'), runtime.FakeAdapter.effects)
        with mock.patch.object(runtime.TaskWorker, 'run_one', side_effect=AssertionError('No execution')):
            self.assertEqual(self.read(task['result_version']), self.service.get('fixture-task'))
            self.assertEqual(self.read(None)['task']['result_version'], task['result_version'])
        self.assertEqual(before, (self.store.path.read_bytes(), self.store.task('fixture-task'),
                                 self.store.events('fixture-task'), runtime.FakeAdapter.effects))

    def test_valid_expected_version_with_no_result_is_stale(self):
        self.assertIsNone(self.service.get('fixture-task')['task']['result_version'])
        with self.assertRaises(ServiceError) as caught:
            self.read('0' * 64)
        self.assertEqual((caught.exception.status, caught.exception.code), (409, 'RESULT_VERSION_STALE'))

    def test_overwritten_result_rejects_old_version(self):
        task = self.store.claim('version-owner')
        self.store.update(task['id'], 'version-owner', thread_id='thread-one', turn_id='turn-one',
                          result={'provider_reply': 'First bounded synthetic reply'})
        first = self.service.get(task['id'])['task']['result_version']
        self.store.update(task['id'], 'version-owner', result={'provider_reply': 'Second bounded synthetic reply'})
        second = self.service.get(task['id'])['task']['result_version']
        self.assertNotEqual(first, second)
        with self.assertRaises(ServiceError) as caught:
            self.read(first)
        self.assertEqual(caught.exception.code, 'RESULT_VERSION_STALE')
        self.assertEqual(self.read(second)['task']['result']['provider_reply'], 'Second bounded synthetic reply')

    def test_review_state_changes_do_not_change_result_version(self):
        task = self.complete()
        command = {'request_id': 'version-review', 'expected_result_version': task['result_version'],
                   'expected_review_revision': 0, 'decision': 'accept'}
        self.service.review(task['id'], command)
        reread = self.read(task['result_version'])['task']
        self.assertEqual(reread['human_acceptance'], 'accepted')
        self.assertEqual(reread['result_version'], task['result_version'])

    def test_invalid_versions_rejected_without_reading_task(self):
        for value in ('', 'a' * 63, 'a' * 65, 'A' * 64, 'g' * 64, ' a' * 32,
                      '0' * 63 + '\n', 1, True, [], {}):
            with self.subTest(value=value), mock.patch.object(self.store, 'task') as read:
                with self.assertRaises(ServiceError) as caught:
                    self.read(value)
                self.assertEqual(caught.exception.code, 'INVALID_RESULT_VERSION')
                read.assert_not_called()

    def test_version_and_public_body_use_same_task_snapshot(self):
        task = self.complete()
        original = self.store.task(task['id'])
        altered = copy.deepcopy(original)
        altered['result']['provider_reply'] = 'A different later result'
        with mock.patch.object(self.store, 'task', side_effect=[original, altered]) as read:
            response = self.read(task['result_version'])
        self.assertEqual(read.call_count, 1)
        self.assertEqual(response['task']['result_version'], task['result_version'])
        self.assertEqual(response['task']['result'], task['result'])

    def start_http(self):
        helper = http_fixture.LocalHTTPTests()
        helper.projects = self.fixture.helper.projects
        helper.designs = self.fixture.helper.fixture.service
        helper.owner_proof = 'synthetic-fixed-result-test-vector-000000000000000000'
        helper.server = HubHTTPServer(helper.projects, helper.designs, previews=self.fixture.helper.service,
            tasks=self.service, owner_auth=OwnerAuth(provider=lambda: helper.owner_proof))
        helper.thread = threading.Thread(target=helper.server.serve_forever, kwargs={'poll_interval': .01})
        helper.thread.start()
        helper.cookie = helper.csrf = None
        self.addCleanup(helper.stop)
        return helper

    def test_http_exact_stale_and_owner_prior_to_task_read(self):
        task = self.complete()
        helper = self.start_http()
        path = '/api/tasks/fixture-task?expected_result_version=' + task['result_version']
        with mock.patch.object(self.service, 'get', wraps=self.service.get) as read:
            self.assertEqual(helper.call(path=path)[0], 401)
            read.assert_not_called()
            helper.session()
            status, _, body = helper.call(path=path)
            self.assertEqual(status, 200)
            self.assertEqual(body['data']['task']['result_version'], task['result_version'])
            self.assertEqual(read.call_args.kwargs, {'expected_result_version': task['result_version']})
        before = (self.store.path.read_bytes(), runtime.FakeAdapter.effects)
        status, _, body = helper.call(path='/api/tasks/fixture-task?expected_result_version=' + '0' * 64)
        self.assertEqual(status, 409)
        self.assertEqual(body['error']['code'], 'RESULT_VERSION_STALE')
        self.assertEqual(before, (self.store.path.read_bytes(), runtime.FakeAdapter.effects))
        self.assertEqual(helper.call(path='/api/tasks/fixture-task')[0], 200)

    def test_http_rejects_empty_duplicate_unknown_and_subroute_queries(self):
        helper = self.start_http()
        helper.session()
        suffix = '?expected_result_version=' + '0' * 64
        paths = ['/api/tasks/fixture-task?expected_result_version=',
                 '/api/tasks/fixture-task?expected_result_version=' + '0' * 64 + '&expected_result_version=' + '0' * 64,
                 '/api/tasks/fixture-task?unknown=value',
                 '/api/tasks/fixture-task' + suffix + '&unknown=value',
                 '/api/tasks' + suffix,
                 '/api/tasks/options/fixture' + suffix,
                 '/api/tasks/fixture-task/preview' + suffix,
                 '/api/tasks/fixture-task/respond' + suffix]
        with mock.patch.object(self.service, 'get') as read:
            for path in paths:
                with self.subTest(path=path):
                    status, _, body = helper.call(path=path)
                    self.assertEqual(status, 400)
                    self.assertEqual(body['error']['code'], 'INVALID_QUERY')
            read.assert_not_called()
        for value in ('abc', 'A' * 64, 'g' * 64):
            self.assertEqual(helper.call(path='/api/tasks/fixture-task?expected_result_version=' + value)[0], 400)

    def test_http_storage_and_request_boundaries_remain_enforced(self):
        task = self.complete()
        helper = self.start_http()
        helper.session()
        path = '/api/tasks/fixture-task?expected_result_version=' + task['result_version']
        self.assertEqual(helper.call(path=path, headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(helper.call('POST', path, {})[0], 400)
        with mock.patch.object(self.store, 'storage_status', return_value={
                'state': 'blocked', 'reason_code': 'ROOT_UNAVAILABLE'}):
            status, _, body = helper.call(path=path)
        self.assertEqual(status, 503)
        self.assertEqual(body['error']['code'], 'TASK_STORAGE_BLOCKED')


if __name__ == '__main__':
    unittest.main()
