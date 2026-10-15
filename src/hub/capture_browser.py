"""Optional finite web capture transport; importing this module starts nothing."""
from __future__ import annotations

import json
import time
from urllib.parse import urldefrag, urlsplit

from .connection_records import SECRET, exact, fingerprint, identifier, require
from .preview_policy import MAX_ARTIFACT_BYTES, authorize_capture_url, validate_capture_policy


class CaptureBrowserError(ValueError):
    """Fixed public diagnostic with no browser exception or URL content."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


REQUEST_FIELDS = {'schema_version', 'request_id', 'project_id', 'page_id', 'url', 'policy',
                  'source_sha256', 'build_sha256', 'source_proof_sha256', 'version_url',
                  'viewport', 'timeout_ms', 'ready_selector', 'refresh_reason', 'expires_after_seconds'}
VERSION_FIELDS = {'schema_version', 'project_id', 'source_sha256', 'build_sha256'}
# Fetch executes in the isolated page so the same context route gates every byte.
# Streaming avoids retaining an unbounded response before its JSON size check.
VERSION_FETCH = """async ({url, timeout}) => {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), timeout);
  try {
    const response = await fetch(url, {method:'GET', redirect:'error',
      credentials:'omit', cache:'no-store', signal:abort.signal});
    if (!response.ok) throw new Error('version unavailable');
    const reader = response.body.getReader(); const chunks = []; let size = 0;
    while (true) {
      const {done,value} = await reader.read(); if (done) break;
      size += value.byteLength;
      if (size > 4096) { await reader.cancel(); throw new Error('version too large'); }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk,offset); offset += chunk.byteLength; }
    return {raw: new TextDecoder('utf-8', {fatal:true}).decode(bytes)};
  } finally { clearTimeout(timer); }
}"""


def _request(value):
    exact(value, REQUEST_FIELDS, 'capture request')
    require(value['schema_version'] == '1.0', 'invalid capture version')
    for name in ('request_id', 'project_id', 'page_id'):
        identifier(value[name], 'capture identity')
    for name in ('source_sha256', 'source_proof_sha256'):
        fingerprint(value[name], 'capture version')
    if value['build_sha256'] is not None:
        fingerprint(value['build_sha256'], 'capture version')
    policy = validate_capture_policy(value['policy'])
    authorize_capture_url(policy, project_id=value['project_id'], url=value['url'])
    if value['version_url'] is not None:
        authorize_capture_url(policy, project_id=value['project_id'], url=value['version_url'])
    exact(value['viewport'], {'width', 'height'}, 'capture viewport')
    for name, lower, upper in (('width', 320, 1920), ('height', 240, 1200)):
        require(type(value['viewport'][name]) is int and lower <= value['viewport'][name] <= upper,
                'invalid capture viewport')
    require(type(value['timeout_ms']) is int and 1 <= value['timeout_ms'] <= 30000, 'invalid capture timeout')
    selector = value['ready_selector']
    require(selector is None or (type(selector) is str and 0 < len(selector) <= 500 and not SECRET.search(selector)),
            'invalid capture ready selector')
    require(value['refresh_reason'] in ('version', 'expired', 'manual'), 'invalid capture refresh')
    require(type(value['expires_after_seconds']) is int and value['expires_after_seconds'] > 0,
            'invalid capture retention')
    return policy


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'duplicate runtime version field')
        value[key] = item
    return value


def capture_web(request, *, executable_path=None):
    """Capture one owner-authorized local UI; no retries or persistent profile.

    The route boundary applies to an owner-trusted local app. It does not claim
    hostile-browser isolation or operating-system network namespace containment.
    """
    try:
        policy = _request(request)
        require(executable_path is None or (type(executable_path) is str and executable_path.startswith('/')
                and '\x00' not in executable_path), 'invalid browser executable')
    except (ValueError, TypeError, KeyError):
        raise CaptureBrowserError('CAPTURE_INVALID_REQUEST') from None
    try:
        from playwright.sync_api import TimeoutError as BrowserTimeout, sync_playwright
    except ImportError:
        raise CaptureBrowserError('CAPTURE_BROWSER_UNAVAILABLE') from None

    started = time.monotonic()
    deadline = started + request['timeout_ms'] / 1000
    blocked = [0]
    callback_error = [None]
    browser = context = None
    allowed = {urldefrag(url)[0] for url in policy['allowed_urls']}
    primary = [None]
    owner_origin = urlsplit(request['url'])[:2]

    def remaining():
        left = int((deadline - time.monotonic()) * 1000)
        if left <= 0:
            raise CaptureBrowserError('CAPTURE_TIMEOUT')
        return left

    def failure_code(default):
        if blocked[0]:
            return 'CAPTURE_RESOURCE_DENIED'
        return callback_error[0] or default

    def healthy():
        if blocked[0] or callback_error[0]:
            raise CaptureBrowserError(failure_code('CAPTURE_RESOURCE_FAILED'))
        remaining()

    def callback_failed(error):
        # Never retain/stringify the original Playwright exception: its call log
        # may contain response/request headers, including fresh-context cookies.
        if callback_error[0] is None:
            callback_error[0] = ('CAPTURE_TIMEOUT' if isinstance(error, BrowserTimeout)
                                 or (isinstance(error, CaptureBrowserError) and error.code == 'CAPTURE_TIMEOUT')
                                 else 'CAPTURE_RESOURCE_FAILED')

    def quiet_abort(route):
        try:
            route.abort('blockedbyclient')
        except Exception as error:
            callback_failed(error)

    def deny(route):
        blocked[0] += 1
        quiet_abort(route)

    def intercept(route):
        # Event listener exceptions are printed by Playwright before reaching the
        # outer capture exception handler. Contain every access and transport
        # operation here, then surface only a fixed diagnostic at the checkpoint.
        try:
            incoming = route.request
            headers = incoming.headers
            if (incoming.method != 'GET' or incoming.url not in allowed
                    or any(name.lower() in ('authorization', 'proxy-authorization') for name in headers)
                    or ('cookie' in {name.lower() for name in headers} and urlsplit(incoming.url)[:2] != owner_origin)
                    or (primary[0] is not None and incoming.frame.page is not primary[0])):
                deny(route)
                return
            response = route.fetch(max_redirects=0, max_retries=0, timeout=remaining())
            response_headers = response.headers
            if not 200 <= response.status < 300 or 'attachment' in response_headers.get('content-disposition', '').lower():
                deny(route)
                return
            # Only this fresh context's same-origin anonymous GET bootstrap can
            # establish cookies. No imported storage, credentials or login POSTs.
            route.fulfill(response=response, headers={key: value for key, value in response_headers.items()
                            if key.lower() != 'set-cookie' or urlsplit(incoming.url)[:2] == owner_origin})
        except Exception as error:
            callback_failed(error)
            quiet_abort(route)

    def websocket(route):
        blocked[0] += 1
        try:
            route.close(code=1008, reason='Capture policy denied')
        except Exception as error:
            callback_failed(error)

    def popup(page):
        blocked[0] += 1
        try:
            page.close()
        except Exception as error:
            callback_failed(error)

    def download(item):
        blocked[0] += 1
        try:
            item.cancel()
        except Exception as error:
            callback_failed(error)

    def version(page):
        if request['version_url'] is None:
            return False
        handle = page.wait_for_function(VERSION_FETCH,
                                        arg={'url': request['version_url'], 'timeout': remaining()},
                                        timeout=remaining(), polling=100)
        try:
            raw = handle.json_value()['raw']
        finally:
            handle.dispose()
        healthy()
        try:
            require(type(raw) is str and len(raw.encode('utf-8')) <= 4096, 'invalid runtime version')
            value = json.loads(raw, object_pairs_hook=_json_object)
            exact(value, VERSION_FIELDS, 'runtime version')
            require(value == {name: request[name] for name in VERSION_FIELDS}, 'runtime version changed')
        except (ValueError, TypeError, KeyError):
            raise CaptureBrowserError('CAPTURE_VERSION_CHANGED') from None
        return True

    try:
        with sync_playwright() as runtime:
            try:
                options = {'headless': True, 'chromium_sandbox': True,
                           'args': ['--disable-gpu'], 'timeout': remaining()}
                if executable_path is not None:
                    options['executable_path'] = executable_path
                browser = runtime.chromium.launch(**options)
                healthy()
                context = browser.new_context(viewport=dict(request['viewport']), accept_downloads=False,
                                              service_workers='block')
                context.set_default_timeout(remaining())
                context.route('**/*', intercept)
                # Missing optional runtime capability fails closed, never falls
                # back to permitting WebSockets on older Playwright versions.
                if not hasattr(context, 'route_web_socket'):
                    raise CaptureBrowserError('CAPTURE_BROWSER_UNAVAILABLE')
                context.route_web_socket('**/*', websocket)
                page = context.new_page()
                primary[0] = page
                context.on('page', popup)
                page.on('download', download)
                page.goto(request['url'], wait_until='domcontentloaded', timeout=remaining())
                healthy()
                if request['ready_selector'] is not None:
                    page.wait_for_selector(request['ready_selector'], state='visible', timeout=remaining())
                healthy()
                if page.url != request['url']:
                    raise CaptureBrowserError('CAPTURE_RESOURCE_DENIED')
                verified = version(page)
                png = page.screenshot(type='png', animations='disabled', timeout=remaining())
                healthy()
                require(type(png) is bytes and 0 < len(png) <= MAX_ARTIFACT_BYTES, 'invalid capture image bytes')
                if page.url != request['url']:
                    raise CaptureBrowserError('CAPTURE_RESOURCE_DENIED')
                if verified:
                    version(page)
                healthy()
                result = {'png': png, 'runtime_verified': verified,
                          'elapsed_ms': 0, 'blocked_count': blocked[0]}
            finally:
                try:
                    if context is not None:
                        context.close()
                finally:
                    if browser is not None:
                        browser.close()
        # Closing a context can dispatch its final in-flight resource events.
        # Check their fixed errors before returning any screenshot bytes.
        healthy()
        result['elapsed_ms'] = max(0, int((time.monotonic() - started) * 1000))
        return result
    except CaptureBrowserError as error:
        raise CaptureBrowserError(failure_code(error.code)) from None
    except BrowserTimeout:
        raise CaptureBrowserError(failure_code('CAPTURE_TIMEOUT')) from None
    except Exception:
        code = 'CAPTURE_TIMEOUT' if time.monotonic() >= deadline else 'CAPTURE_BROWSER_FAILED'
        raise CaptureBrowserError(failure_code(code)) from None
