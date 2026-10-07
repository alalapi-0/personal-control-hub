from __future__ import annotations

import copy
import sys
import unittest
import json
import threading
import http.client
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.preview_bridge import FixtureBridge
from hub.task_store import TaskStore
from hub.task_service import TaskService
from hub.workbench_store import digest
from hub.service_contract import OwnerAction, ServiceError
from hub.local_service import HubHTTPServer
from hub.owner_auth import OwnerAuth
import test_hub_workbench as fixture


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.WorkbenchTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        TaskStore(self.f.root).register_storage()  # Explicit disposable ledger setup.
        self.service = self.f.service
        self.context = ('fixture-session-a', b'fixture-version')
        self.now = 1
        self.bridge = FixtureBridge(self.service, 'http://localhost:34567', clock=lambda: self.now)
        self.service.fixture_bridge = self.bridge
        self.descriptor = self.bridge.issue(self.context)

    def element(self, **updates):
        d = self.descriptor
        hint = {'bridge_id': d['bridge_id'], 'bridge_version': d['bridge_version'],
                'manifest_hash': d['manifest_hash'], 'binding_hash': d['binding_hash'],
                'load_id': d['load_id'], 'element_id': 'sample-card', 'role': 'button',
                'rect': {'x1': .1, 'y1': .2, 'x2': .5, 'y2': .8}, 'source_mapping': 'unknown'}
        hint.update(updates)
        return self.f.command(kind='element', region=None, element_hint=hint)

    def save(self, command, context=None):
        return self.service.save(command, backend_instance='b' * 32, context=context or self.context)

    def decide(self, **changes):
        c = self.f.fixture.decision(action='request_changes', feedback='Make spacing clearer; retain all behavior')
        c.update(changes)
        return self.f.fixture.service.decide(c, owner_action=OwnerAction(fixture=True))

    def test_explicit_design_to_annotation_to_hash_reference_no_dispatch(self):
        self.assertIsNone(self.service.catalog()['previews'][0]['design_feedback'])
        self.assertEqual(self.f.fixture.store.read()['events'], [])
        self.decide()
        metadata = self.service.catalog()['previews'][0]['design_feedback']
        command = self.f.command(kind='whole', region=None, requested_change=metadata['feedback'], design_reference=metadata['reference'])
        saved = self.save(command)
        tasks = TaskStore(self.f.root)
        options = TaskService(self.f.projects, self.service, tasks).options(command['request_id'])
        self.assertEqual(options['reference']['command_hash'], digest(saved['receipt']['command']))
        self.assertEqual(options['reference']['design_reference'], metadata['reference'])
        self.assertEqual(options['choices'], []); self.assertEqual(tasks.tasks(), [])
        before = self.f.store.path.read_bytes()
        self.assertTrue(self.save(command)['replayed']); self.assertEqual(before, self.f.store.path.read_bytes())
        for key, value in [('event_id', 'other'), ('event_hash', '0' * 64), ('store_revision', 1), ('action', 'select')]:
            bad = copy.deepcopy(command); bad['design_reference'][key] = value
            with self.assertRaises(ServiceError): self.save(bad)
        self.assertEqual(before, self.f.store.path.read_bytes())

    def test_element_version_private_geometry_session_expiry_and_replay(self):
        command = self.element(); saved = self.save(command)
        self.assertEqual(saved['receipt']['record']['annotation']['element_hint']['source_mapping'], 'unknown')
        before = self.f.store.path.read_bytes()
        for changes in [{'bridge_version': 2}, {'manifest_hash': '0' * 64}, {'binding_hash': '0' * 64},
                        {'element_id': 'private-input'}, {'role': 'article'}, {'url': 'https://example.invalid'},
                        {'source_mapping': {'file': '/private/source'}}, {'rect': {'x1': 0, 'x2': float('nan'), 'y1': 0, 'y2': 1}}]:
            with self.assertRaises(ServiceError): self.save(self.element(**changes))
        with self.assertRaises(ServiceError): self.save(command, ('fixture-session-b', b'fixture-version'))
        self.bridge.issue(('fixture-session-b', b'fixture-version'))
        self.assertTrue(self.save(command)['replayed'])
        self.bridge.issue(self.context)
        with self.assertRaises(ServiceError): self.save(command)
        self.now = 302
        with self.assertRaises(ServiceError): self.save(command)
        self.assertEqual(before, self.f.store.path.read_bytes())
        self.assertEqual(self.service.annotations()['records'][0]['material_state'], 'current')

    def test_reference_drift_at_commit_stale_history_and_task_reference(self):
        self.decide(); ref = self.service.catalog()['previews'][0]['design_feedback']['reference']
        command = self.f.command(kind='whole', region=None, design_reference=ref)
        def drift(stage):
            if stage == 'before_replace':
                self.decide(request_id='fixture-second', event_id='fixture-second-event',
                            expected_revision=self.f.fixture.store.read()['revision'], supersedes=ref['event_id'], feedback='New feedback')
        self.f.store.fault = drift
        with self.assertRaises(ServiceError): self.save(command)
        self.assertEqual(self.f.store.read()['revision'], 0)
        self.f.store.fault = lambda _: None
        self.save(self.element())
        before = self.f.store.path.read_bytes()
        self.f.registry.authority['registry_hash'] = 'c' * 64
        self.assertEqual(self.service.annotations()['records'][0]['material_state'], 'stale')
        with self.assertRaises(ServiceError): TaskService(self.f.projects, self.service, TaskStore(self.f.root)).options('fixture-annotation')
        self.assertEqual(before, self.f.store.path.read_bytes())

    def test_default_off_and_trusted_construction_cannot_enable_real_store(self):
        self.service.fixture_bridge = None
        with self.assertRaises(ServiceError): self.save(self.element())
        for origin in ['http://127.0.0.1:3', 'http://localhost:3/evil', 'https://localhost:3', 'http://localhost:3?x=1', 'http://user@localhost:3']:
            with self.assertRaises(ServiceError): FixtureBridge(self.service, origin)
        self.f.fixture.store.fixture = False
        with self.assertRaises(ServiceError): FixtureBridge(self.service, 'http://localhost:3')

    def test_http_auth_two_sessions_rotation_default_csp_and_no_browser_enable(self):
        proof = ['synthetic-owner-' + 'x' * 64]
        server = HubHTTPServer(self.f.projects, self.f.fixture.service, previews=self.service,
                               owner_auth=OwnerAuth(provider=lambda: proof[0]))
        thread = threading.Thread(target=server.serve_forever); thread.start()
        self.addCleanup(thread.join); self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        def request(path, method='GET', body=None, headers=None):
            client = http.client.HTTPConnection(*server.server_address)
            h = {'Host': server.host_header, **(headers or {})}
            if method == 'POST': h.update({'Content-Type': 'application/json', 'Origin': server.origin})
            client.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
            response = client.getresponse(); raw = response.read(); status = response.status
            meta = dict(response.getheaders()); client.close()
            return status, meta, json.loads(raw) if raw.startswith(b'{') else raw
        def login():
            _, headers, value = request('/api/session')
            cookie = headers['Set-Cookie'].split(';')[0]
            status, headers, value = request('/api/owner/login', 'POST', {'token': proof[0]},
                  {'Cookie': cookie, 'X-Hub-CSRF': value['data']['csrf_token']})
            self.assertEqual(status, 200)
            return {'Cookie': headers['Set-Cookie'].split(';')[0], 'X-Hub-CSRF': value['data']['csrf_token']}
        path = '/api/preview-bridge/' + self.bridge.binding['preview_id']
        self.assertEqual(request(path)[0], 401)
        a, b = login(), login()
        self.descriptor = request(path, headers=a)[2]['data']
        d_b = request(path, headers=b)[2]['data']
        self.assertNotEqual(self.descriptor['load_id'], d_b['load_id'])
        command = self.element()
        self.assertEqual(request('/api/annotations', 'POST', command, b)[0], 409)
        bad_csrf = {**a, 'X-Hub-CSRF': 'wrong'}
        self.assertEqual(request('/api/annotations', 'POST', command, bad_csrf)[0], 403)
        self.assertEqual(request('/api/annotations', 'POST', command, a)[0], 200)
        self.assertEqual(self.f.store.read()['revision'], 1)
        for extra in ['?origin=http://evil.invalid', '?manifest=bad', '?project_id=other']:
            self.assertEqual(request(path+extra, headers=a)[0], 400)
        self.assertEqual(request(path, 'POST', {'origin': 'http://evil.invalid'}, a)[0], 404)
        csp = request('/')[1]['Content-Security-Policy']
        self.assertIn('frame-src http://localhost:34567', csp); self.assertNotIn('frame-src *', csp)
        self.service.fixture_bridge = None
        disabled = request(path, headers=b)[2]['data']
        self.assertFalse(disabled['available']); self.assertFalse(disabled['execution_allowed'])
        self.assertNotIn('frame-src', request('/')[1]['Content-Security-Policy'])
        proof[0] = 'rotated-fixture-owner-' + 'y' * 64
        self.assertEqual(request(path, headers=a)[0], 401)

    def test_owner_revocation_at_commit_never_publishes_annotation(self):
        checks = [0]
        def authorize():
            checks[0] += 1
            raise ServiceError('SESSION_REQUIRED', status=401)
        with self.assertRaises(ServiceError):
            self.service.save(self.element(), backend_instance='b' * 32, context=self.context, authorize=authorize)
        self.assertEqual(self.f.store.read()['revision'], 0)
        self.assertEqual(checks[0], 1)

    def test_superseded_event_keeps_saved_history_but_has_no_task_reference(self):
        self.decide(); ref = self.service.catalog()['previews'][0]['design_feedback']['reference']
        command = self.f.command(kind='whole', region=None, design_reference=ref)
        self.save(command); before = self.f.store.path.read_bytes()
        self.decide(request_id='next-decision', event_id='next-event', action='defer', feedback='Defer explicitly',
                    expected_revision=self.f.fixture.store.read()['revision'], supersedes=ref['event_id'])
        self.assertEqual(self.service.annotations()['records'][0]['material_state'], 'stale')
        with self.assertRaises(ServiceError): TaskService(self.f.projects, self.service, TaskStore(self.f.root)).options('fixture-annotation')
        self.assertEqual(before, self.f.store.path.read_bytes())
