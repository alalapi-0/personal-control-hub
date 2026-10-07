from __future__ import annotations

import copy
import hashlib
import os
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.preview_service import PreviewService
from hub.workbench_store import WorkbenchStore, canonical, MAX_BYTES
from hub.service_contract import ServiceError
from hub.owner_auth import OwnerAuth
from hub.local_service import HubHTTPServer
import test_hub_design_service as designs_fixture
import test_hub_local_service as http_fixture


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = designs_fixture.DesignServiceTests()
        self.fixture.setUp(); self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        self.registry = SimpleNamespace(projects={'fixture-project': {
            'id': 'fixture-project', 'name': 'Fixture project', 'enabled': True,
            'root_path': '/fixture/never-probed', 'access_profile': 'registered_project_read'}},
            authority={'registry_hash': 'a' * 64}, _check_registry=lambda: None)
        self.projects = SimpleNamespace(_resolver=lambda: self.registry)
        self.store = WorkbenchStore(self.root, fixture=True)
        self.service = PreviewService(self.projects, self.fixture.service, self.store)
        self.old_design = self.fixture.store.path.read_bytes()

    def command(self, **changes):
        binding = self.service.catalog()['previews'][0]['binding']
        value = {'request_id': 'fixture-annotation', 'expected_revision': 0,
            'binding': binding, 'kind': 'region',
            'region': {'x1': .1, 'y1': .2, 'x2': .5, 'y2': .8},
            'requested_change': 'Synthetic fixture spacing feedback',
            'preserve_scope': 'Preserve all course and progress behavior',
            'view': {'viewport_width': 390, 'viewport_height': 844, 'dpr': 2,
                     'scroll_x': 0, 'scroll_y': 120, 'zoom': 1.25,
                     'image_width': 300, 'image_height': 400,
                     'natural_width': 600, 'natural_height': 800,
                     'image_left': 15, 'image_top': -20, 'fit': 'image-content-box'}}
        value.update(changes); return value

    def save(self, command):
        return self.service.save(command, backend_instance='b' * 32)

    def test_catalog_image_region_whole_reload_and_exact_replay(self):
        catalog = self.service.catalog()
        self.assertEqual(len(catalog['previews']), 1)
        preview = catalog['previews'][0]
        result = self.service.resolve(preview['binding']['preview_id'], image=True)
        self.assertEqual(result.sha256, preview['binding']['artifact_sha256'])
        self.assertEqual(result.data, self.fixture.png_data)
        self.assertFalse(preview['execution_allowed'])
        command = self.command(); saved = self.save(command)
        self.assertEqual(saved['receipt']['revision'], 1)
        original = self.store.path.read_bytes()
        self.assertTrue(self.save(command)['replayed'])
        self.assertEqual(original, self.store.path.read_bytes())
        reopened = WorkbenchStore(self.root, fixture=True)
        self.assertEqual(reopened.receipt(command['request_id']), saved['receipt'])
        self.assertFalse(saved['receipt']['record']['provenance']['live_page'])
        self.save(self.command(request_id='fixture-whole', expected_revision=1, kind='whole', region=None))
        annotations = self.service.annotations()
        self.assertEqual(annotations['revision'], 2)
        self.assertEqual(annotations['records'][0]['material_state'], 'current')
        self.assertEqual(self.old_design, self.fixture.store.path.read_bytes())

    def test_project_policy_and_bad_binding_reject_before_artifact_probe(self):
        command = self.command()
        with mock.patch.object(self.fixture.service, 'artifact', wraps=self.fixture.service.artifact) as probe:
            for field, value in [('project_id', 'other-project'), ('candidate_revision', 99),
                                 ('artifact_sha256', '0' * 64), ('registry_hash', '0' * 64),
                                 ('pages', ['other-page'])]:
                malformed = copy.deepcopy(command); malformed['binding'][field] = value
                with self.subTest(field=field), self.assertRaises(ServiceError): self.save(malformed)
            for changes in [{'enabled': False}, {'current_state_status': 'removed_local'},
                            {'local_presence': {'status': 'removed_local'}},
                            {'root_path': 'https://cloud.example/'}, {'root_kind': 'cloud'},
                            {'local_presence': {'status': 'cloud_only'}}, {'access_profile': 'no_current_goal_access'}]:
                original = copy.deepcopy(self.registry.projects['fixture-project'])
                self.registry.projects['fixture-project'].update(changes)
                self.assertEqual(self.service.catalog()['previews'], [])
                with self.assertRaises(ServiceError): self.save(command)
                self.registry.projects['fixture-project'] = original
            probe.assert_not_called()
        self.assertFalse(self.store.path.parent.exists())

    def test_malformed_geometry_arbitrary_fields_and_conflicts_zero_effect(self):
        good = self.command()
        bad = []
        for region in [None, {'x1': .5, 'x2': .1, 'y1': 0, 'y2': 1},
                       {'x1': -1, 'x2': .5, 'y1': 0, 'y2': 1},
                       {'x1': float('nan'), 'x2': .5, 'y1': 0, 'y2': 1}]:
            bad.append({**good, 'region': region})
        bad += [{**good, 'url': 'file:///etc/passwd'}, {**good, 'requested_change': 'x' * 2001},
                {**good, 'preserve_scope': ''}, {**good, 'requested_change': '\ud800'}]
        invalid = copy.deepcopy(good); invalid['view']['natural_width'] = 1; bad.append(invalid)
        with mock.patch.object(self.fixture.service, 'artifact') as probe:
            for item in bad:
                with self.assertRaises(ServiceError): self.save(item)
            probe.assert_not_called()
        self.assertFalse(self.store.path.parent.exists())
        self.save(good); before = self.store.path.read_bytes()
        for item in [self.command(requested_change='different'), self.command(request_id='second-client')]:
            with self.assertRaises(ServiceError) as caught: self.save(item)
            self.assertEqual(caught.exception.status, 409)
        self.assertEqual(before, self.store.path.read_bytes())

    def test_stale_missing_hash_mime_and_symlink_preserve_history(self):
        command = self.command(); self.save(command); before = self.store.path.read_bytes()
        self.registry.authority['registry_hash'] = 'c' * 64
        self.assertEqual(self.service.annotations()['records'][0]['material_state'], 'stale')
        with self.assertRaises(ServiceError): self.save(command)
        self.registry.authority['registry_hash'] = 'a' * 64
        self.fixture.png_path.write_bytes(b'changed')
        with self.assertRaises(ServiceError): self.save(self.command(request_id='wrong-image', expected_revision=1))
        self.fixture.png_path.unlink(); self.fixture.png_path.symlink_to(self.fixture.baseline_path)
        with self.assertRaises(ServiceError): self.save(self.command(request_id='symlink-image', expected_revision=1))
        self.assertEqual(before, self.store.path.read_bytes())

    def test_corrupt_duplicate_oversize_and_store_alias_reject_without_repair(self):
        self.save(self.command()); valid = self.store.path.read_bytes()
        for raw in [b'{"schema_version":1,"schema_version":1}', b'x' * (MAX_BYTES + 1),
                    valid.replace(b'"record_hash":"', b'"record_hash":"0', 1), valid + b' ']:
            self.store.path.write_bytes(raw)
            with self.assertRaises(ServiceError): self.store.save(self.command(), backend_instance='b' * 32)
            self.assertEqual(raw, self.store.path.read_bytes())
        self.store.path.unlink(); self.store.path.symlink_to(self.fixture.baseline_path)
        original = self.fixture.baseline_path.read_bytes()
        with self.assertRaises(ServiceError): self.store.read()
        self.assertEqual(original, self.fixture.baseline_path.read_bytes())
        self.store.path.unlink();self.store.path.mkdir()
        with self.assertRaises(ServiceError): self.store.read()
        alias=self.root.parent/'alias';alias.symlink_to(self.root,target_is_directory=True)
        nested=alias/'docs'
        unsafe=WorkbenchStore(nested,fixture=True)
        with self.assertRaises(ServiceError): unsafe.read()

    def test_pre_post_commit_failures_reconcile_and_capacity(self):
        command = self.command()
        def fault(stage):
            if stage == 'before_replace': raise OSError('synthetic failure')
        self.store.fault = fault
        with self.assertRaises(ServiceError) as caught: self.save(command)
        self.assertEqual(caught.exception.outcome, 'NOT_COMMITTED')
        self.assertEqual(self.store.read()['revision'], 0)
        def after(stage):
            if stage == 'after_replace': raise OSError('synthetic failure')
        self.store.fault = after
        with self.assertRaises(ServiceError) as caught: self.save(command)
        self.assertEqual(caught.exception.outcome, 'COMMITTED_DURABILITY_UNCONFIRMED')
        self.assertEqual(self.service.receipt(command['request_id'])['receipt']['revision'], 1)
        self.store.fault = lambda _: None
        self.assertTrue(self.save(command)['replayed'])
        before = self.store.path.read_bytes()
        with mock.patch('hub.workbench_store.MAX_RECORDS', 1), self.assertRaises(ServiceError):
            self.save(self.command(request_id='capacity', expected_revision=1))
        self.assertEqual(before, self.store.path.read_bytes())

    def test_two_client_race_has_exactly_one_effect(self):
        barrier = threading.Barrier(2); outcomes = []
        commands = [self.command(request_id='race-a'), self.command(request_id='race-b')]
        def submit(command):
            barrier.wait()
            try: outcomes.append(self.save(command)['outcome'])
            except ServiceError as error: outcomes.append(error.code)
        threads = [threading.Thread(target=submit, args=(command,)) for command in commands]
        for thread in threads: thread.start()
        for thread in threads: thread.join(2); self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes.count('COMMITTED'), 1)
        self.assertIn(next(v for v in outcomes if v != 'COMMITTED'), {'WORKBENCH_BUSY', 'ANNOTATION_REVISION_CONFLICT'})
        self.assertEqual(self.store.read()['revision'], 1)

    def test_http_owner_csrf_origin_guest_and_read_only_preview(self):
        helper = http_fixture.LocalHTTPTests()
        helper.projects = self.projects; helper.designs = self.fixture.service
        helper.owner_proof = 'fixture-only-public-test-vector-00000000000000000000'
        helper.server = HubHTTPServer(self.projects, self.fixture.service, previews=self.service,
                                     owner_auth=OwnerAuth(provider=lambda: helper.owner_proof))
        helper.thread = threading.Thread(target=helper.server.serve_forever, kwargs={'poll_interval': .01})
        helper.thread.start(); helper.cookie = helper.csrf = None
        self.addCleanup(helper.stop)
        status, headers, data = helper.call(path='/api/session', authenticated=False)
        helper.cookie = headers['Set-Cookie'].split(';')[0]; helper.csrf = data['data']['csrf_token']
        command = self.command()
        self.assertEqual(helper.call('POST', '/api/annotations', command)[0], 401)
        self.assertFalse(self.store.path.parent.exists())
        self.assertEqual(helper.call(path='/api/previews')[0], 200)
        helper.session()
        for headers in [{'Origin': 'https://evil.example'}, {'X-Hub-CSRF': 'wrong'}]:
            self.assertEqual(helper.call('POST', '/api/annotations', command, headers=headers)[0], 403)
        self.assertFalse(self.store.path.parent.exists())
        self.assertEqual(helper.call('POST', '/api/annotations', command)[0], 200)
        self.assertEqual(helper.call(path='/api/annotations/requests/fixture-annotation')[2]['data']['outcome'], 'COMMITTED')
        self.assertEqual(helper.call('POST', '/api/tasks', {})[0], 503)
        self.assertEqual(helper.server.tasks, None)
        self.assertEqual(self.old_design, self.fixture.store.path.read_bytes())
