"""Persistent Owner HTTP boundaries using transient synthetic credentials only."""
import errno
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import test_hub_local_service as fixtures
from hub.local_service import HubHTTPServer
from hub.owner_auth import OwnerAuth
from hub.owner_sessions import OwnerSessionStore
from hub.service_contract import ServiceError


class PersistentOwnerHTTPTests(unittest.TestCase):
    call = fixtures.LocalHTTPTests.call

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pch-owner-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.projects, self.designs = fixtures.ProjectSpy(), fixtures.DesignSpy()
        self.proof = secrets.token_urlsafe(40)
        self.cookie = self.csrf = None
        self.server = self.thread = None
        self.addCleanup(self.stop)
        self.start()

    @property
    def store_path(self):
        return self.root / 'data' / 'owner-auth' / 'sessions.json'

    def start(self, *, root=None, port=0, capacity=32):
        self.server = HubHTTPServer(self.projects, self.designs, port=port,
            owner_auth=OwnerAuth(provider=lambda: self.proof),
            owner_session_store=OwnerSessionStore(root or self.root, capacity=capacity))
        self.thread = threading.Thread(target=self.server.serve_forever,
            kwargs={'poll_interval': .01})
        self.thread.start()

    def stop(self):
        if self.thread is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(3)
            self.assertFalse(self.thread.is_alive())
            self.thread = None

    def restart(self, *, root=None, same_port=True, capacity=32):
        port = self.server.server_address[1] if same_port else 0
        self.stop()
        self.start(root=root, port=port, capacity=capacity)

    def bootstrap(self):
        status, headers, body = self.call(path='/api/session')
        self.assertEqual(status, 200)
        self.cookie = headers['Set-Cookie'].split(';', 1)[0]
        self.csrf = body['data']['csrf_token']
        return headers

    def login(self):
        self.bootstrap()
        status, headers, body = self.call('POST', '/api/owner/login', {'token': self.proof})
        self.assertEqual(status, 200)
        self.cookie = headers['Set-Cookie'].split(';', 1)[0]
        self.csrf = body['data']['csrf_token']
        return headers

    def assert_no_effects(self):
        self.assertEqual(self.projects.calls, [])
        self.assertEqual(self.designs.calls, [])

    def assert_persistent_cookie(self, headers):
        cookie = headers['Set-Cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        self.assertIn('Path=/api', cookie)
        attributes = dict(part.strip().split('=', 1) for part in cookie.split(';')[1:] if '=' in part)
        self.assertGreaterEqual(int(attributes['Max-Age']), 400 * 86400)

    def test_lazy_store_and_no_raw_auth_material_at_rest(self):
        self.assertFalse(self.store_path.exists())
        self.bootstrap()
        self.assertFalse(self.store_path.exists())
        status, _, _ = self.call('POST', '/api/owner/login', {'token': secrets.token_urlsafe(40)})
        self.assertEqual(status, 401)
        self.assertFalse(self.store_path.exists())
        self.assert_persistent_cookie(self.login())
        persisted = self.store_path.read_bytes()
        self.assertTrue(json.loads(persisted))
        for secret in (self.proof, self.cookie.split('=', 1)[1], self.csrf):
            self.assertNotIn(secret.encode(), persisted)
        self.assertEqual(self.store_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store_path.parent.stat().st_mode & 0o777, 0o700)
        self.assert_no_effects()

    def test_owner_survives_guest_ttl_and_renews_persistent_cookie(self):
        clock = [100.0]
        self.server.sessions.clock = lambda: clock[0]
        self.login()
        owner, csrf = self.cookie, self.csrf
        self.cookie = self.csrf = None
        guest_headers = self.bootstrap()
        guest, guest_csrf = self.cookie, self.csrf
        self.assertNotIn('Max-Age', guest_headers['Set-Cookie'])
        clock[0] += 3601
        self.cookie, self.csrf = guest, guest_csrf
        self.assertEqual(self.call('POST', '/api/refresh', {})[0], 401)
        self.cookie, self.csrf = owner, csrf
        status, headers, body = self.call(path='/api/identity')
        self.assertEqual(status, 200)
        self.assertTrue(body['data']['owner_authenticated'])
        self.assert_persistent_cookie(headers)
        self.assertEqual(self.projects.calls, [('head',)])
        self.assertEqual(self.designs.calls, [('snapshot',)])
        self.projects.calls.clear()
        self.designs.calls.clear()
        self.assert_persistent_cookie(self.bootstrap())
        self.assertEqual(self.cookie, owner)
        self.assert_no_effects()

    def test_normal_restart_same_origin_restores_owner_and_working_csrf(self):
        self.login()
        owner = self.cookie
        with self.assertRaises(OSError) as conflict:
            HubHTTPServer(self.projects, self.designs, port=self.server.server_address[1],
                owner_auth=OwnerAuth(provider=lambda: self.proof),
                owner_session_store=OwnerSessionStore(self.root))
        self.assertEqual(conflict.exception.errno, errno.EADDRINUSE)
        self.restart()
        self.assert_persistent_cookie(self.bootstrap())
        self.assertEqual(self.cookie, owner)
        status, _, _ = self.call('POST', '/api/refresh', {'request_id': 'synthetic-restart'})
        self.assertEqual(status, 200)
        self.assertEqual([call[0] for call in self.projects.calls], ['refresh'])
        self.assertEqual(self.designs.calls, [])

    def test_logout_clears_cookie_and_durably_revokes_replay(self):
        self.login()
        owner, csrf = self.cookie, self.csrf
        status, headers, _ = self.call('POST', '/api/owner/logout', {})
        self.assertEqual(status, 200)
        self.assertIn('Max-Age=0', headers['Set-Cookie'])
        self.assertEqual(self.call(path='/api/identity')[0], 401)
        self.restart()
        self.cookie, self.csrf = owner, csrf
        self.assertEqual(self.call('POST', '/api/refresh', {})[0], 401)
        self.assertEqual(self.call(path='/api/identity')[0], 401)
        self.assert_no_effects()

    def test_wrong_csrf_and_guest_reject_before_project_and_design_effects(self):
        self.bootstrap()
        guest, guest_csrf = self.cookie, self.csrf
        self.login()
        for path in ('/api/refresh', '/api/designs/decisions'):
            with self.subTest(path=path):
                self.assertEqual(self.call('POST', path, {}, headers={'X-Hub-CSRF': 'wrong'})[0], 403)
                self.assertEqual(self.call('POST', path, {}, headers={
                    'Cookie': guest, 'X-Hub-CSRF': guest_csrf})[0], 401)
        self.assert_no_effects()

    def test_credential_change_and_loss_reject_persisted_owner(self):
        self.login()
        owner, csrf = self.cookie, self.csrf
        self.proof = secrets.token_urlsafe(40)
        self.restart()
        for path in ('/api/refresh', '/api/designs/decisions'):
            self.assertEqual(self.call('POST', path, {})[0], 401)
        self.cookie = self.csrf = None
        self.login()
        self.proof = None
        self.restart()
        for path in ('/api/refresh', '/api/designs/decisions'):
            self.assertIn(self.call('POST', path, {})[0], (401, 503))
        self.proof = secrets.token_urlsafe(40)
        self.cookie, self.csrf = owner, csrf
        self.assertEqual(self.call(path='/api/identity')[0], 401)
        self.assert_no_effects()

    def test_owner_cookie_is_bound_to_root_and_actual_port(self):
        self.login()
        owner, csrf = self.cookie, self.csrf
        original_port = self.server.server_address[1]
        # Keep the original socket occupied so a new ephemeral port must differ.
        original_server, original_thread = self.server, self.thread
        self.server = self.thread = None
        try:
            self.start()
            self.assertNotEqual(self.server.server_address[1], original_port)
            for path in ('/api/refresh', '/api/designs/decisions'):
                self.assertEqual(self.call('POST', path, {})[0], 401)
            self.stop()
        finally:
            self.stop()
            self.server, self.thread = original_server, original_thread
        different_root = self.root / 'different-root'
        copied_store = different_root / 'data' / 'owner-auth' / 'sessions.json'
        copied_store.parent.mkdir(parents=True, mode=0o700)
        copied_store.write_bytes(self.store_path.read_bytes())
        copied_store.chmod(0o600)
        self.restart(root=different_root)
        self.cookie, self.csrf = owner, csrf
        for path in ('/api/refresh', '/api/designs/decisions'):
            self.assertEqual(self.call('POST', path, {})[0], 401)
        self.assert_no_effects()

    def test_capacity_refuses_new_owner_without_evicting_existing(self):
        self.restart(capacity=1)
        self.login()
        owner, csrf = self.cookie, self.csrf
        self.cookie = self.csrf = None
        self.bootstrap()
        status, _, _ = self.call('POST', '/api/owner/login', {'token': self.proof})
        self.assertEqual(status, 409)
        self.assert_no_effects()
        self.cookie, self.csrf = owner, csrf
        self.assertEqual(self.call(path='/api/identity')[0], 200)
        self.restart(capacity=1)
        self.assertEqual(self.call(path='/api/identity')[0], 200)

    def test_fifo_store_and_lock_fail_closed_in_bounded_child(self):
        # A blocking open must never trap the unittest server's cleanup thread.
        # subprocess.run kills and collects the entire owned child on timeout.
        self.stop()
        child = r"""
import os
import sys
from test_hub_persistent_owner_sessions import PersistentOwnerHTTPTests
fixture = PersistentOwnerHTTPTests()
try:
    fixture.setUp()
    fixture.stop()
    path = fixture.store_path.with_name(sys.argv[1])
    path.parent.mkdir(parents=True, mode=0o700)
    os.mkfifo(path, mode=0o600)
    fixture.start()
    fixture.bootstrap()
    status, _, _ = fixture.call('POST', '/api/owner/login', {'token': fixture.proof})
    fixture.assertEqual(status, 503)
    fixture.assertEqual(fixture.call('POST', '/api/refresh', {})[0], 401)
    fixture.assert_no_effects()
finally:
    fixture.doCleanups()
print('FIFO_REJECTED')
"""
        for filename in ('sessions.json', 'sessions.lock'):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory(
                    prefix='fifo-child-', dir=self.root) as temporary:
                environment = os.environ.copy()
                environment.update(TMPDIR=temporary, PYTHONDONTWRITEBYTECODE='1')
                environment['PYTHONPATH'] = str(Path(__file__).resolve().parent)
                completed = subprocess.run([sys.executable, '-c', child, filename],
                    env=environment, capture_output=True, text=True, timeout=3)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout.strip(), 'FIFO_REJECTED')

    def test_malformed_insecure_symlink_and_hardlink_store_fail_closed(self):
        self.stop()
        for variant in ('malformed', 'file-mode', 'directory-mode', 'symlink', 'hardlink'):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory(
                    prefix='pch-owner-bad-', dir=self.root) as temporary:
                root = Path(temporary)
                path = root / 'data' / 'owner-auth' / 'sessions.json'
                path.parent.mkdir(parents=True, mode=0o700)
                target = root / 'protected-target'
                marker = b'preserve synthetic target'
                if variant in ('symlink', 'hardlink'):
                    target.write_bytes(marker)
                    target.chmod(0o600)
                    if variant == 'symlink':
                        path.symlink_to(target)
                    else:
                        os.link(target, path)
                else:
                    path.write_text('{broken' if variant == 'malformed' else '{}')
                    path.chmod(0o644 if variant == 'file-mode' else 0o600)
                    if variant == 'directory-mode':
                        path.parent.chmod(0o755)
                before = path.read_bytes()
                try:
                    self.start(root=root)
                    self.cookie = self.csrf = None
                    self.bootstrap()
                    status, _, _ = self.call('POST', '/api/owner/login', {'token': self.proof})
                    self.assertEqual(status, 503)
                    self.assertEqual(self.call('POST', '/api/refresh', {})[0], 401)
                except ServiceError:
                    pass  # Refusing server construction is also fail-closed.
                finally:
                    self.stop()
                self.assertEqual(path.read_bytes(), before)
                if target.exists():
                    self.assertEqual(target.read_bytes(), marker)
                self.assert_no_effects()


if __name__ == '__main__':
    unittest.main()
