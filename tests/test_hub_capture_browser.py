"""Mock-only browser transport checks; no browser or network is started."""
import copy
import io
from contextlib import redirect_stderr, redirect_stdout
import json
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.capture_browser import CaptureBrowserError, capture_web

URL = 'http://127.0.0.1:9417/ui#/overview'
ASSET = 'http://127.0.0.1:9417/app.js'
VERSION = 'http://127.0.0.1:9417/version'


def request(**changes):
    value = {'schema_version': '1.0', 'request_id': 'r-1', 'project_id': 'p-1', 'page_id': 'page-1',
             'url': URL, 'policy': {'schema_version': '1.0', 'project_id': 'p-1',
                  'allowed_urls': [URL, ASSET, VERSION], 'allowed_read_paths': [],
                  'max_redirects': 0, 'retention': 'local_only'},
             'source_sha256': '1' * 64, 'build_sha256': None, 'source_proof_sha256': '2' * 64,
             'version_url': None, 'viewport': {'width': 1200, 'height': 800}, 'timeout_ms': 30000,
             'ready_selector': None, 'refresh_reason': 'manual', 'expires_after_seconds': 3600}
    value.update(changes)
    return value


class FakeTimeout(Exception):
    pass


class Transport:
    def __init__(self):
        self.runtime = MagicMock()
        self.browser = self.runtime.chromium.launch.return_value
        self.context = self.browser.new_context.return_value
        self.page = self.context.new_page.return_value
        self.page.url = URL
        self.page.screenshot.return_value = b'png-test-bytes'
        self.routes = []; self.extras = []; self.context_events = {}; self.page_events = {}
        self.context.route.side_effect = lambda glob, callback: setattr(self, 'route', callback)
        self.context.route_web_socket.side_effect = lambda glob, callback: setattr(self, 'websocket', callback)
        self.context.on.side_effect = lambda event, callback: self.context_events.update({event: callback})
        self.page.on.side_effect = lambda event, callback: self.page_events.update({event: callback})
        self.goto_hook = None
        self.page.goto.side_effect = self.goto
        self.enter = MagicMock(); self.enter.__enter__.return_value = self.runtime
        self.api = types.ModuleType('playwright.sync_api')
        self.api.sync_playwright = MagicMock(return_value=self.enter)
        self.api.TimeoutError = FakeTimeout
        self.package = types.ModuleType('playwright')
        self.package.sync_api = self.api
        self.modules = {'playwright': self.package, 'playwright.sync_api': self.api}

    def event(self, url, method='GET', status=200, headers=None, request_headers=None, page=None):
        route = MagicMock()
        route.request = SimpleNamespace(url=url, method=method, headers=request_headers or {},
                                        frame=SimpleNamespace(page=page or self.page))
        route.fetch.return_value = SimpleNamespace(status=status, headers=headers or {})
        self.routes.append(route); self.route(route)
        return route

    def goto(self, url, **kwargs):
        self.page.url = url
        self.event(url.split('#')[0])
        for extra in self.extras:
            self.event(**extra)
        if self.goto_hook:
            self.goto_hook()

    def version(self, *values):
        handles = []
        for value in values:
            handle = MagicMock(); handle.json_value.return_value = {'raw': value}
            handles.append(handle)
        self.page.wait_for_function.side_effect = handles
        return handles

    def capture(self, value=None, **kwargs):
        with patch.dict(sys.modules, self.modules):
            return capture_web(value or request(), **kwargs)


class CaptureBrowserTests(unittest.TestCase):
    def error(self, code, action):
        with self.assertRaises(CaptureBrowserError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)
        return caught.exception

    def version(self, **changes):
        value = {name: request()[name] for name in ('schema_version', 'project_id', 'source_sha256', 'build_sha256')}
        value.update(changes); return json.dumps(value)

    def test_optional_dependency_is_lazy_and_missing_is_fixed_error(self):
        with patch.dict(sys.modules, {'playwright': None, 'playwright.sync_api': None}):
            self.error('CAPTURE_BROWSER_UNAVAILABLE', lambda: capture_web(request()))

    def test_invalid_requests_rejected_before_optional_import_or_launch(self):
        bad = [request(viewport={'width': 319, 'height': 800}),
               request(viewport={'width': 1200, 'height': True}), request(timeout_ms=0),
               request(timeout_ms=30001), request(timeout_ms=True), request(source_sha256=None),
               request(version_url='http://127.0.0.1:9417/private'), request(page_id='../x'),
               request(project_id='wrong'), request(ready_selector=''), request(extra=True)]
        for value in bad:
            with self.subTest(value=value), patch.dict(sys.modules, {'playwright.sync_api': None}):
                self.error('CAPTURE_INVALID_REQUEST', lambda: capture_web(value))

    def test_fresh_sandboxed_context_defragmented_routes_and_memory_png(self):
        transport = Transport(); transport.extras = [{'url': ASSET}]
        original = request(ready_selector='main'); before = copy.deepcopy(original)
        result = transport.capture(original, executable_path='/usr/bin/chromium')
        self.assertEqual(result['png'], b'png-test-bytes')
        self.assertFalse(result['runtime_verified']); self.assertEqual(result['blocked_count'], 0)
        self.assertIs(type(result['elapsed_ms']), int)
        self.assertEqual(original, before)
        options = transport.runtime.chromium.launch.call_args.kwargs
        self.assertTrue(options['headless']); self.assertTrue(options['chromium_sandbox'])
        self.assertEqual(options['args'], ['--disable-gpu'])
        self.assertEqual(options['executable_path'], '/usr/bin/chromium')
        self.assertEqual(transport.browser.new_context.call_args.kwargs,
                         {'viewport': {'width': 1200, 'height': 800}, 'accept_downloads': False,
                          'service_workers': 'block'})
        self.assertEqual(transport.context.route.call_args.args[0], '**/*')
        for route in transport.routes:
            self.assertEqual(route.fetch.call_args.kwargs['max_redirects'], 0)
            self.assertEqual(route.fetch.call_args.kwargs['max_retries'], 0)
            self.assertGreater(route.fetch.call_args.kwargs['timeout'], 0)
            route.fulfill.assert_called_once(); route.continue_.assert_not_called()
        screenshot = transport.page.screenshot.call_args.kwargs
        self.assertEqual(screenshot['type'], 'png'); self.assertEqual(screenshot['animations'], 'disabled')
        self.assertNotIn('path', screenshot)
        transport.page.wait_for_selector.assert_called_once()
        transport.context.close.assert_called_once(); transport.browser.close.assert_called_once()
        transport.page.wait_for_function.assert_not_called()

    def test_non_get_external_unlisted_api_and_credential_requests_are_denied(self):
        extras = [{'url': ASSET, 'method': 'POST'}, {'url': 'https://external.test/image.png'},
                  {'url': 'http://127.0.0.1:9417/api/private'},
                  {'url': ASSET, 'request_headers': {'Authorization': 'opaque'}},
                  {'url': 'http://127.0.0.1:9418/image.png', 'request_headers': {'cookie': 'opaque'}}]
        for extra in extras:
            transport = Transport(); transport.extras = [extra]
            with self.subTest(extra=extra):
                self.error('CAPTURE_RESOURCE_DENIED', transport.capture)
                transport.routes[-1].fetch.assert_not_called()
                transport.routes[-1].abort.assert_called_once()
                transport.page.screenshot.assert_not_called()
                transport.context.close.assert_called_once(); transport.browser.close.assert_called_once()

    def test_fresh_same_origin_anonymous_cookie_is_not_imported_owner_authentication(self):
        transport = Transport(); transport.extras = [{'url': ASSET, 'request_headers': {'Cookie': 'new-anonymous-only'}}]
        self.assertFalse(transport.capture()['runtime_verified'])
        transport.routes[-1].fetch.assert_called_once()

    def test_redirect_and_error_status_is_denied_before_follow_or_fulfill(self):
        for status in (301, 302, 303, 304, 307, 308, 401, 404, 500):
            transport = Transport(); transport.extras = [{'url': ASSET, 'status': status,
                                                         'headers': {'location': URL}}]
            with self.subTest(status=status):
                self.error('CAPTURE_RESOURCE_DENIED', transport.capture)
                transport.routes[-1].fetch.assert_called_once()
                transport.routes[-1].fulfill.assert_not_called()
                transport.page.screenshot.assert_not_called()

    def test_websocket_is_closed_without_server_connection(self):
        transport = Transport(); socket = MagicMock()
        transport.goto_hook = lambda: transport.websocket(socket)
        self.error('CAPTURE_RESOURCE_DENIED', transport.capture)
        socket.close.assert_called_once_with(code=1008, reason='Capture policy denied')
        socket.connect_to_server.assert_not_called(); transport.page.screenshot.assert_not_called()

    def test_popup_and_download_are_closed_and_prevent_image(self):
        for kind in ('popup', 'download', 'attachment'):
            transport = Transport(); unwanted = MagicMock()
            if kind == 'popup':
                transport.goto_hook = lambda: transport.context_events['page'](unwanted)
            elif kind == 'download':
                transport.goto_hook = lambda: transport.page_events['download'](unwanted)
            else:
                transport.extras = [{'url': ASSET, 'headers': {'content-disposition': 'attachment; filename=image.png'}}]
            with self.subTest(kind=kind):
                self.error('CAPTURE_RESOURCE_DENIED', transport.capture)
                transport.page.screenshot.assert_not_called()
                if kind == 'popup': unwanted.close.assert_called_once()
                if kind == 'download': unwanted.cancel.assert_called_once()

    def test_route_from_another_page_never_fetches(self):
        transport = Transport(); transport.extras = [{'url': ASSET, 'page': MagicMock()}]
        self.error('CAPTURE_RESOURCE_DENIED', transport.capture)
        transport.routes[-1].fetch.assert_not_called()

    def test_literal_final_spa_url_must_match(self):
        transport = Transport(); transport.goto_hook = lambda: setattr(transport.page, 'url', URL + '-changed')
        self.error('CAPTURE_RESOURCE_DENIED', transport.capture)
        transport.page.screenshot.assert_not_called()

    def test_version_before_and_after_matches_request_and_disposes_handles(self):
        transport = Transport(); handles = transport.version(self.version(), self.version())
        result = transport.capture(request(version_url=VERSION))
        self.assertTrue(result['runtime_verified']); self.assertEqual(transport.page.wait_for_function.call_count, 2)
        for call in transport.page.wait_for_function.call_args_list:
            self.assertEqual(call.kwargs['arg']['url'], VERSION)
            self.assertGreater(call.kwargs['timeout'], 0)
            self.assertIn("redirect:'error'", call.args[0]); self.assertIn('4096', call.args[0])
            self.assertIn("credentials:'omit'", call.args[0])
        for handle in handles: handle.dispose.assert_called_once()

    def test_mismatch_unknown_keys_duplicate_keys_and_oversize_version_fail(self):
        invalid = [self.version(source_sha256='3' * 64), self.version(project_id='wrong'),
                   self.version(build_sha256='4' * 64), self.version(extra='private'),
                   '{"schema_version":"1.0","schema_version":"1.0"}', '[]', 'x' * 4097]
        for raw in invalid:
            transport = Transport(); transport.version(raw)
            with self.subTest(raw=raw[:80]):
                self.error('CAPTURE_VERSION_CHANGED', lambda: transport.capture(request(version_url=VERSION)))
                transport.page.screenshot.assert_not_called()

    def test_post_screenshot_version_change_returns_no_success(self):
        transport = Transport(); transport.version(self.version(), self.version(source_sha256='3' * 64))
        self.error('CAPTURE_VERSION_CHANGED', lambda: transport.capture(request(version_url=VERSION)))
        transport.page.screenshot.assert_called_once(); transport.context.close.assert_called_once()

    def test_timeout_is_fixed_and_cleanup_always_runs(self):
        transport = Transport(); transport.page.goto.side_effect = FakeTimeout('private URL exception')
        self.error('CAPTURE_TIMEOUT', transport.capture)
        transport.context.close.assert_called_once(); transport.browser.close.assert_called_once()
        transport.page.screenshot.assert_not_called()

    def test_total_deadline_not_reset_between_steps(self):
        transport = Transport(); elapsed = [0.0]
        transport.goto_hook = lambda: elapsed.__setitem__(0, 31.0)
        with patch('hub.capture_browser.time.monotonic', side_effect=lambda: elapsed[0]):
            self.error('CAPTURE_TIMEOUT', transport.capture)
        transport.page.screenshot.assert_not_called()

    def test_browser_errors_are_redacted_and_cleanup_attempts_both(self):
        transport = Transport(); transport.page.screenshot.side_effect = RuntimeError('secret URL data')
        self.error('CAPTURE_BROWSER_FAILED', transport.capture)
        transport.context.close.assert_called_once(); transport.browser.close.assert_called_once()
        transport = Transport(); transport.context.close.side_effect = RuntimeError('secret close')
        self.error('CAPTURE_BROWSER_FAILED', transport.capture)
        transport.browser.close.assert_called_once()

    def test_empty_or_oversize_png_cannot_succeed(self):
        for data in (b'', b'12345'):
            transport = Transport(); transport.page.screenshot.return_value = data
            with self.subTest(data=data), patch('hub.capture_browser.MAX_ARTIFACT_BYTES', 4):
                self.error('CAPTURE_BROWSER_FAILED', transport.capture)

    def test_resource_denial_during_screenshot_discards_returned_bytes(self):
        transport = Transport()
        def screenshot(**kwargs):
            transport.event('http://127.0.0.1:9417/not-authorized')
            return b'png-that-must-not-be-returned'
        transport.page.screenshot.side_effect = screenshot
        self.error('CAPTURE_RESOURCE_DENIED', transport.capture)
        transport.context.close.assert_called_once(); transport.browser.close.assert_called_once()

    def test_known_build_version_can_be_verified(self):
        transport = Transport()
        transport.version(self.version(build_sha256='4' * 64), self.version(build_sha256='4' * 64))
        self.assertTrue(transport.capture(request(version_url=VERSION, build_sha256='4' * 64))['runtime_verified'])

    def test_launch_failure_never_leaks_exception_or_attempts_context(self):
        transport = Transport()
        transport.runtime.chromium.launch.side_effect = RuntimeError('private executable path')
        self.error('CAPTURE_BROWSER_FAILED', transport.capture)
        transport.browser.new_context.assert_not_called()
        transport.enter.__exit__.assert_called_once()

    def callback_dispatch(self, callback, value):
        # Model Playwright's listener diagnostic sink. An escaped callback error
        # would expose its original call log here before capture's outer catch.
        try:
            callback(value)
        except Exception as error:
            print(str(error), file=sys.stderr)
            raise

    def assert_private_callback_failure(self, transport, code):
        stdout = io.StringIO(); stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            error = self.error(code, transport.capture)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(stderr.getvalue(), '')
        self.assertIsNone(error.__cause__)
        self.assertTrue(error.__suppress_context__)
        transport.context.close.assert_called_once(); transport.browser.close.assert_called_once()
        transport.page.screenshot.assert_not_called()

    def test_callback_fetch_timeout_is_contained_without_header_diagnostics(self):
        transport = Transport(); route = MagicMock()
        route.request = SimpleNamespace(url=ASSET, method='GET', headers={'Cookie': 'fresh-anonymous-secret'},
                                        frame=SimpleNamespace(page=transport.page))
        route.fetch.side_effect = FakeTimeout('route.fetch Timeout; Cookie: fresh-anonymous-secret')
        route.abort.side_effect = RuntimeError('abort Cookie: private-cleanup-secret')
        completed = []
        def dispatch():
            self.callback_dispatch(transport.route, route)
            completed.append(True)
        transport.goto_hook = dispatch
        self.assert_private_callback_failure(transport, 'CAPTURE_TIMEOUT')
        self.assertEqual(completed, [True])
        route.abort.assert_called_once(); route.fulfill.assert_not_called()

    def test_callback_generic_fetch_failure_retains_fixed_resource_failure(self):
        transport = Transport(); route = MagicMock()
        route.request = SimpleNamespace(url=ASSET, method='GET', headers={},
                                        frame=SimpleNamespace(page=transport.page))
        route.fetch.side_effect = RuntimeError('Authorization: private-header-value')
        transport.goto_hook = lambda: self.callback_dispatch(transport.route, route)
        # Even an unrelated cleanup exception cannot replace the callback code.
        transport.context.close.side_effect = RuntimeError('private cleanup detail')
        self.assert_private_callback_failure(transport, 'CAPTURE_RESOURCE_FAILED')
        route.abort.assert_called_once()

    def test_callback_header_access_and_fulfill_failures_do_not_escape(self):
        class BrokenHeaders:
            @property
            def headers(self):
                raise RuntimeError('Cookie: hidden header exception')
        for stage in ('headers', 'fulfill'):
            transport = Transport(); route = MagicMock()
            route.request = (BrokenHeaders() if stage == 'headers' else
                             SimpleNamespace(url=ASSET, method='GET', headers={},
                                             frame=SimpleNamespace(page=transport.page)))
            route.fetch.return_value = SimpleNamespace(status=200, headers={})
            route.fulfill.side_effect = RuntimeError('Set-Cookie: hidden response exception')
            transport.goto_hook = lambda: self.callback_dispatch(transport.route, route)
            with self.subTest(stage=stage):
                self.assert_private_callback_failure(transport, 'CAPTURE_RESOURCE_FAILED')
                route.abort.assert_called_once()

    def test_late_callback_during_context_close_cannot_return_screenshot(self):
        transport = Transport(); socket = MagicMock()
        socket.close.side_effect = RuntimeError('Cookie: late callback secret')
        transport.context.close.side_effect = lambda: self.callback_dispatch(transport.websocket, socket)
        stdout = io.StringIO(); stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.error('CAPTURE_RESOURCE_DENIED', transport.capture)
        self.assertEqual(stdout.getvalue(), ''); self.assertEqual(stderr.getvalue(), '')
        transport.page.screenshot.assert_called_once(); transport.browser.close.assert_called_once()

    def test_all_denial_callback_cleanup_exceptions_stay_private(self):
        for kind in ('websocket', 'popup', 'download', 'route_abort'):
            transport = Transport(); unwanted = MagicMock()
            if kind == 'websocket':
                unwanted.close.side_effect = FakeTimeout('Cookie: websocket secret')
                transport.goto_hook = lambda: self.callback_dispatch(transport.websocket, unwanted)
            elif kind == 'popup':
                unwanted.close.side_effect = RuntimeError('Cookie: popup secret')
                transport.goto_hook = lambda: self.callback_dispatch(transport.context_events['page'], unwanted)
            elif kind == 'download':
                unwanted.cancel.side_effect = RuntimeError('Cookie: download secret')
                transport.goto_hook = lambda: self.callback_dispatch(transport.page_events['download'], unwanted)
            else:
                unwanted.request = SimpleNamespace(url='https://external.test/', method='GET', headers={})
                unwanted.abort.side_effect = RuntimeError('Cookie: abort secret')
                transport.goto_hook = lambda: self.callback_dispatch(transport.route, unwanted)
            with self.subTest(kind=kind):
                self.assert_private_callback_failure(transport, 'CAPTURE_RESOURCE_DENIED')
                if kind in ('websocket', 'popup'): unwanted.close.assert_called_once()
                elif kind == 'download': unwanted.cancel.assert_called_once()
                else: unwanted.abort.assert_called_once()

    def test_context_without_websocket_guard_fails_closed(self):
        transport = Transport(); del transport.context.route_web_socket
        self.error('CAPTURE_BROWSER_UNAVAILABLE', transport.capture)
        transport.context.new_page.assert_not_called()
        transport.context.close.assert_called_once(); transport.browser.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
