"""Loopback-only HTTP transport for the Hub's local application contract.

No project or design facts are duplicated here. GETs do not refresh sources or
initialize stores. The selected Hub UI uses the same local application service.
"""
from __future__ import annotations

import hmac
import hashlib
import json
import re
import secrets
import socket
import threading
import time
from pathlib import Path
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

from .service_contract import ArtifactResponse, OwnerAction, ServiceError
from .owner_auth import OwnerAuth
from .material_service import MaterialResponse

API_VERSION = "1.0"
MAX_BODY_BYTES = 64 * 1024
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,160}\Z")
WEB_ROOT = Path(__file__).with_name("web")
WEB_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/hub.css": ("hub.css", "text/css; charset=utf-8"),
    "/assets/hub.js": ("hub.js", "text/javascript; charset=utf-8"),
    "/assets/dom.mjs": ("dom.mjs", "text/javascript; charset=utf-8"),
    "/assets/common.js": ("common.js", "text/javascript; charset=utf-8"),
    "/assets/connection_view.mjs": ("connection_view.mjs", "text/javascript; charset=utf-8"),
    "/assets/directory.mjs": ("directory.mjs", "text/javascript; charset=utf-8"),
    "/assets/graph.mjs": ("graph.mjs", "text/javascript; charset=utf-8"),
    "/assets/actions.mjs": ("actions.mjs", "text/javascript; charset=utf-8"),
    "/assets/ledger_watch.mjs": ("ledger_watch.mjs", "text/javascript; charset=utf-8"),
    "/assets/captures.mjs": ("captures.mjs", "text/javascript; charset=utf-8"),
    "/assets/page_inventory.mjs": ("page_inventory.mjs", "text/javascript; charset=utf-8"),
    "/assets/business_tasks.mjs": ("business_tasks.mjs", "text/javascript; charset=utf-8"),
    "/assets/business_preview.mjs": ("business_preview.mjs", "text/javascript; charset=utf-8"),
    "/assets/management.mjs": ("management.mjs", "text/javascript; charset=utf-8"),
    "/assets/relations.mjs": ("relations.mjs", "text/javascript; charset=utf-8"),
    "/assets/ordinary_tasks.mjs": ("ordinary_tasks.mjs", "text/javascript; charset=utf-8"),
    "/assets/task_history.mjs": ("task_history.mjs", "text/javascript; charset=utf-8"),
    "/assets/timeline.mjs": ("timeline.mjs", "text/javascript; charset=utf-8"),
    "/assets/designs.js": ("designs.js", "text/javascript; charset=utf-8"),
    "/assets/host.js": ("host.js", "text/javascript; charset=utf-8"),
    "/assets/workbench.js": ("workbench.js", "text/javascript; charset=utf-8"),
    "/assets/tasks.js": ("tasks.js", "text/javascript; charset=utf-8"),
    "/assets/materials.js": ("materials.js", "text/javascript; charset=utf-8"),
    "/assets/sessions.js": ("sessions.js", "text/javascript; charset=utf-8"),
    "/assets/preview_bridge.js": ("preview_bridge.js", "text/javascript; charset=utf-8"),
    "/assets/live_preview.js": ("live_preview.js", "text/javascript; charset=utf-8"),
    "/assets/icon.svg": ("icon.svg", "image/svg+xml"),
}
UI_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
          "connect-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none'; "
          "media-src 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'none'")


def _object_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _reject_constant(_):
    raise ValueError("non-finite JSON")


class _Sessions:
    OWNER_COOKIE_AGE = 400 * 24 * 60 * 60

    def __init__(self, *, clock=time.monotonic, ttl=3600, capacity=32,
                 owner_store=None, scope=None):
        self.clock, self.ttl, self.capacity = clock, ttl, capacity
        self.owner_store, self.scope = owner_store, scope
        self.values = {}
        self.lock = threading.Lock()
        self.cookie_name = (owner_store.cookie_name(scope) if owner_store is not None
                            else "hub_" + secrets.token_hex(8))

    def _owner_csrf(self, session):
        return hmac.new(session.encode('ascii'), b'PCH persistent Owner CSRF v1', hashlib.sha256).hexdigest()

    def cookie_header(self, session, *, owner=False, clear=False):
        value = f'{self.cookie_name}={session}; HttpOnly; SameSite=Strict; Path=/api'
        if clear:
            return value + '; Max-Age=0'
        if owner and self.owner_store is not None:
            value += f'; Max-Age={self.OWNER_COOKIE_AGE}'
        return value

    def issue(self, *, owner=False, credential_version=None):
        if owner and self.owner_store is not None:
            session = secrets.token_urlsafe(32)
            self.owner_store.issue(session, self.scope, credential_version)
            return session, self._owner_csrf(session)
        with self.lock:
            now = self.clock()
            self.values = {key: value for key, value in self.values.items() if value[1] > now}
            while len(self.values) >= self.capacity:
                del self.values[next(iter(self.values))]
            session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.values[session] = (csrf, now + self.ttl, owner, credential_version)
            return session, csrf

    def _session_id(self, cookie):
        if not cookie or len(cookie) > 8192:
            raise ServiceError("SESSION_REQUIRED", status=401)
        try:
            pairs = cookie.split(";")
            if sum(part.strip().split("=", 1)[0] == self.cookie_name for part in pairs) != 1:
                raise CookieError("ambiguous session")
            parsed = SimpleCookie()
            parsed.load(cookie)
            session = parsed[self.cookie_name].value
        except (CookieError, KeyError, ValueError):
            raise ServiceError("SESSION_REQUIRED", status=401) from None
        return session

    def validate(self, cookie, csrf=None, *, require_owner=False, credential_version=None):
        session = self._session_id(cookie)
        with self.lock:
            saved = self.values.get(session)
        if saved is None and self.owner_store is not None:
            if not self.owner_store.validate(session, self.scope, credential_version):
                raise ServiceError('SESSION_REQUIRED', status=401)
            if csrf is not None and (not isinstance(csrf, str) or not csrf.isascii()
                    or not hmac.compare_digest(self._owner_csrf(session), csrf)):
                raise ServiceError('CSRF_REJECTED', status=403)
            return True
        with self.lock:
            saved = self.values.get(session)
            if saved is None or saved[1] <= self.clock():
                self.values.pop(session, None)
                raise ServiceError("SESSION_REQUIRED", status=401)
            if csrf is not None and (not isinstance(csrf, str) or not csrf.isascii() or
                                     not hmac.compare_digest(saved[0], csrf)):
                raise ServiceError("CSRF_REJECTED", status=403)
            if saved[2] and saved[3] != credential_version:
                self.values.pop(session, None)
                raise ServiceError('SESSION_REQUIRED', status=401)
            if require_owner and not saved[2]:
                raise ServiceError('OWNER_AUTH_REQUIRED', status=401)
            return saved[2]

    def bootstrap(self, cookie, *, credential_version=None):
        if cookie:
            if len(cookie) > 8192:
                raise ServiceError('SESSION_REQUIRED', status=401)
            count = sum(part.strip().split('=', 1)[0] == self.cookie_name for part in cookie.split(';'))
            if count == 0:
                # Cookies share a host across ports; another Hub instance is not
                # this instance's proof and must not prevent a guest bootstrap.
                return self.issue()
            session = self._session_id(cookie)
            try:
                owner = self.validate(cookie, credential_version=credential_version)
                if owner and self.owner_store is not None:
                    return session, self._owner_csrf(session)
                with self.lock:
                    saved = self.values.get(session)
                    if saved and saved[1] > self.clock():
                        return session, saved[0]
            except ServiceError:
                pass
        return self.issue()

    def revoke(self, cookie):
        session = self._session_id(cookie)
        with self.lock:
            saved = self.values.pop(session, None)
        if saved is None and self.owner_store is not None:
            self.owner_store.revoke(session, self.scope)


class HubHTTPServer(ThreadingHTTPServer):
    """Explicit loopback binding and bounded, joined request workers."""
    daemon_threads = False
    # Rebind after a normal shutdown while old accepted sockets are in TIME_WAIT.
    # SO_REUSEPORT remains disabled: a second live listener is still rejected.
    allow_reuse_address = True
    request_queue_size = 8

    def __init__(self, projects, designs, *, host="127.0.0.1", port=0, owner_auth=None, host_observer=None, previews=None, tasks=None, materials=None, session_view=None, owner_session_store=None):
        if host != "127.0.0.1":
            raise ServiceError("NON_LOOPBACK_BIND_REJECTED")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ServiceError("INVALID_PORT")
        self.projects, self.designs = projects, designs
        self.owner_auth = owner_auth or OwnerAuth()
        self.host_observer = host_observer
        self.previews = previews
        self.tasks = tasks
        self.materials = materials
        self.session_view = session_view
        self.backend_instance = secrets.token_hex(16)
        self._slots = threading.BoundedSemaphore(8)
        super().__init__((host, port), HubRequestHandler)
        self.host_header = f"127.0.0.1:{self.server_address[1]}"
        self.origin = "http://" + self.host_header
        self.sessions = _Sessions(owner_store=owner_session_store, scope=self.origin)
        from .action_registry import ActionRegistry
        self.actions = ActionRegistry(projects, designs, host=self.origin, backend_instance=self.backend_instance)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(5)
        return request, address

    def verify_request(self, request, client_address):
        return client_address[0] == "127.0.0.1"

    def process_request(self, request, client_address):
        # A browser can briefly overlap asset loads with existing API requests.
        # Keep the same eight workers, allowing a short bounded slot handoff.
        if not self._slots.acquire(timeout=0.1):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        # No tracebacks, body values, cookies, tokens or filesystem paths in logs.
        pass


class HubRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "HubLocal"
    sys_version = ""

    def log_message(self, format, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        # Also sanitize errors raised by the standard-library HTTP parser.
        self._error(ServiceError("HTTP_REQUEST_REJECTED", status=code))

    def _one_header(self, key):
        values = self.headers.get_all(key, [])
        if len(values) > 1:
            raise ServiceError("AMBIGUOUS_HEADERS")
        return values[0] if values else None

    def _boundary(self, *, mutation=False):
        if self._one_header("Host") != self.server.host_header:
            raise ServiceError("HOST_REJECTED", status=403)
        origin = self._one_header("Origin")
        if (origin is not None and origin != self.server.origin) or (mutation and origin is None):
            raise ServiceError("ORIGIN_REJECTED", status=403)
        site = self._one_header("Sec-Fetch-Site")
        if site is not None and site not in {"same-origin", "none"}:
            raise ServiceError("FETCH_SITE_REJECTED", status=403)
        if mutation and site == "none":
            raise ServiceError("FETCH_SITE_REJECTED", status=403)
        if self._one_header("Transfer-Encoding") is not None:
            raise ServiceError("TRANSFER_ENCODING_REJECTED")
        if self._one_header('Authorization') is not None:
            raise ServiceError('AUTH_HEADER_REJECTED', status=403)
        # Check these even on GET so duplicate/framed requests cannot be smuggled.
        length = self._one_header("Content-Length")
        if not mutation and length not in {None, "0"}:
            raise ServiceError("UNEXPECTED_BODY")

    def _route(self):
        if len(self.path) > 4096 or any(ch in self.path for ch in ("%", "\\", "#")):
            # Percent encoding is allowed in query strings, never route identifiers.
            raw_path = self.path.split("?", 1)[0]
            if len(self.path) > 4096 or any(ch in raw_path for ch in ("%", "\\", "#")) or "#" in self.path:
                raise ServiceError("INVALID_ROUTE")
        parts = urlsplit(self.path)
        if parts.scheme or parts.netloc or not parts.path.startswith("/api/"):
            raise ServiceError("NOT_FOUND", status=404)
        try:
            pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True,
                              max_num_fields=8, encoding="utf-8", errors="strict")
        except (ValueError, UnicodeError):
            raise ServiceError("INVALID_QUERY") from None
        query = {}
        for key, value in pairs:
            if key in query:
                raise ServiceError("INVALID_QUERY")
            query[key] = value
        return parts.path, query

    def _body(self):
        content_type = self._one_header("Content-Type")
        if content_type is None or content_type.lower().replace(" ", "") not in {
                "application/json", "application/json;charset=utf-8"}:
            raise ServiceError("JSON_REQUIRED", status=415)
        raw_length = self._one_header("Content-Length")
        if raw_length is None or not re.fullmatch(r"[0-9]{1,8}", raw_length):
            raise ServiceError("CONTENT_LENGTH_REQUIRED", status=411)
        length = int(raw_length)
        if length > MAX_BODY_BYTES:
            raise ServiceError("BODY_TOO_LARGE", status=413)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ServiceError("INCOMPLETE_BODY")
        try:
            result = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_pairs,
                                parse_constant=_reject_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise ServiceError("INVALID_JSON") from None
        if not isinstance(result, dict):
            raise ServiceError("INVALID_JSON")
        return result

    def _send(self, data, *, status=200, content_type="application/json; charset=utf-8", headers=None, ui=False):
        headers = dict(headers or {})
        if (status < 400 and getattr(self, '_owner_response_session', None)
                and 'Set-Cookie' not in headers):
            headers['Set-Cookie'] = self.server.sessions.cookie_header(self._owner_response_session, owner=True)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        csp = UI_CSP
        bridge = getattr(self.server.previews, 'fixture_bridge', None)
        if ui and bridge is not None:
            csp += '; frame-src ' + bridge.origin
        live = getattr(self.server.previews, 'readonly_preview', None)
        if ui and live is not None and live.origin:
            csp += (' ' if bridge is not None else '; frame-src ') + live.origin
        self.send_header("Content-Security-Policy", csp if ui else "default-src 'none'; sandbox; frame-ancestors 'none'")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Connection", "close")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def _json(self, value, *, status=200, headers=None):
        data = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
        self._send(data, status=status, headers=headers)

    def _error(self, error):
        try:
            self._json({"api_version": API_VERSION, "ok": False, "error": error.as_dict()},
                       status=error.status)
        except (OSError, ValueError):
            self.close_connection = True

    def _success(self, value):
        from .action_registry import ControlledArtifactRead
        read_headers = {}
        if isinstance(value, ControlledArtifactRead):
            receipt = value.receipt
            read_headers = {'X-Hub-Read-'+name: str(receipt[key]) for name,key in (
                ('Schema','schema_version'),('Run-Id','run_id'),('Request-Id','request_id'),
                ('Input-SHA256','input_sha256'),('Parameter-SHA256','parameter_sha256'),
                ('Binding-SHA256','binding_sha256'),('Bytes','bytes_read'))}
            read_headers['X-Hub-Read-Durable-Receipt'] = 'false'
            value = value.response
        if isinstance(value, MaterialResponse):
            self._send(value.data, status=value.status, content_type=value.content_type, headers=value.headers)
        elif isinstance(value, ArtifactResponse):
            if not FILENAME.fullmatch(value.filename) or value.disposition not in {"inline", "attachment"}:
                raise ServiceError("INVALID_ARTIFACT_RESPONSE", status=500)
            if value.content_type not in {"application/octet-stream", "application/zip", "image/png",
                                          "image/jpeg", "image/webp", "image/gif", "text/plain; charset=utf-8"}:
                raise ServiceError("INVALID_ARTIFACT_RESPONSE", status=500)
            self._send(value.data, content_type=value.content_type,
                       headers={"Content-Disposition": f'{value.disposition}; filename="{value.filename}"', **read_headers})
        else:
            self._json({"api_version": API_VERSION, "ok": True, "data": value})

    def _design_snapshot(self):
        try:
            return self.server.designs.snapshot()
        except ServiceError as error:
            # Keep independent project sources readable while preserving the
            # design failure explicitly. Direct /api/designs still returns it.
            return {"available": False, "store_revision": None, "store_classification": None,
                    "facts": [], "history": [], "effective": {}, "queues": {}, "reason": error.code}

    def _dispatch_get(self, path, query):
        if path == '/api/preview-runtime':
            if query:
                raise ServiceError('INVALID_QUERY')
            files = {'src/hub/web/'+p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in WEB_ROOT.iterdir() if p.is_file()}
            code_sha = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            result = {'schema_version': '1.0', 'project_id': 'personal-control-hub',
                      'source_sha256': code_sha, 'build_sha256': None}
            self._json(result)
            return
        if path in {"/api/health", "/api/session", "/api/guest"}:
            if query:
                raise ServiceError("INVALID_QUERY")
            if path == "/api/health":
                self._success({"service": "hub-local", "ui_implemented": True})
            else:
                session, csrf = self.server.sessions.bootstrap(self._one_header('Cookie'), credential_version=self.server.owner_auth.version)
                owner = self.server.sessions.validate(f'{self.server.sessions.cookie_name}={session}',
                                                     credential_version=self.server.owner_auth.version)
                cookie = self.server.sessions.cookie_header(session, owner=owner)
                self._json({"api_version": API_VERSION, "ok": True, "data": {"csrf_token": csrf}},
                           headers={"Set-Cookie": cookie})
            return
        owner = self.server.sessions.validate(self._one_header("Cookie"), credential_version=self.server.owner_auth.version)
        if owner and self.server.sessions.owner_store is not None:
            self._owner_response_session = self.server.sessions._session_id(self._one_header('Cookie'))
        if path == '/api/ledger-head':
            if query:
                raise ServiceError('INVALID_QUERY')
            result = {'head': self.server.projects.ledger_head(),
                      'backend_instance': self.server.backend_instance}
        elif path == '/api/page-inventory':
            if query:
                raise ServiceError('INVALID_QUERY')
            from .preview_service import page_catalog
            result = page_catalog(self.server.projects)
        elif path == '/api/identity':
            if query:
                raise ServiceError('INVALID_QUERY')
            ledger = self.server.projects.ledger_head()
            snapshot = self._design_snapshot()
            capabilities = self.server.tasks.capabilities() if self.server.tasks is not None else {}
            authenticated = bool(owner and self.server.owner_auth.configured)
            for name in ('task_drafts', 'task_history', 'annotation_execution', 'ordinary_execution'):
                if name in capabilities:
                    capability = capabilities[name]
                    capability['service_available'] = capability['available']
                    if not authenticated:
                        capability.update(available=False, status='requires_owner',
                                          reason_code='OWNER_REQUIRED' if self.server.owner_auth.configured else 'OWNER_AUTH_UNAVAILABLE')
            capabilities['owner_auth'] = {'available': self.server.owner_auth.configured,
                                          'status': 'configured' if self.server.owner_auth.configured else 'unconfigured',
                                          'reason_code': None if self.server.owner_auth.configured else 'OWNER_AUTH_UNAVAILABLE'}
            capabilities['owner_session'] = {'available': authenticated,
                                             'status': 'authenticated' if authenticated else 'guest',
                                             'reason_code': None if authenticated else 'OWNER_REQUIRED'}
            execution = capabilities.get('annotation_execution', {}).get('available', False)
            result = {'backend_instance': self.server.backend_instance, 'owner_authenticated': authenticated,
                      'owner_configured': self.server.owner_auth.configured,
                      'execution_enabled': execution,
                      'capabilities': capabilities,
                      'ledger_head': ledger, 'design_revision': snapshot.get('store_revision'),
                      'data_classification': snapshot.get('store_classification', 'unavailable')}
        elif path == '/api/sessions':
            if query: raise ServiceError('INVALID_QUERY')
            def authorize_sessions():
                if not self.server.owner_auth.configured: raise ServiceError('OWNER_AUTH_UNAVAILABLE', status=503)
                cookie = self._one_header('Cookie')
                self.server.sessions.validate(cookie, require_owner=True, credential_version=self.server.owner_auth.version)
                return (self.server.owner_auth.version, self.server.sessions._session_id(cookie))
            authorize_sessions()
            if self.server.session_view is None: raise ServiceError('SESSION_VIEW_UNAVAILABLE', status=503)
            result = self.server.session_view.catalog(authorize_sessions)
            authorize_sessions()
        elif path == '/api/ordinary-context':
            if 'project_id'not in query or not set(query)<={'project_id','parent_task_id'}:
                raise ServiceError('INVALID_QUERY')
            version=self.server.owner_auth.version
            self.server.sessions.validate(self._one_header('Cookie'),require_owner=True,credential_version=version)
            if self.server.tasks is None:raise ServiceError('TASK_RUNTIME_UNAVAILABLE',status=503)
            owner_context=hashlib.sha256(b'hub-task-owner-v1'+version).hexdigest()
            result=self.server.tasks.ordinary_context(query['project_id'],owner_context=owner_context,
                parent_task_id=query.get('parent_task_id'))
            self.server.sessions.validate(self._one_header('Cookie'),require_owner=True,
                credential_version=self.server.owner_auth.version)
            if self.server.owner_auth.version!=version:raise ServiceError('OWNER_AUTH_FAILED',status=401)
        elif path == '/api/task-drafts' or path.startswith('/api/task-drafts/'):
            if query:raise ServiceError('INVALID_QUERY')
            self.server.sessions.validate(self._one_header('Cookie'),require_owner=True,credential_version=self.server.owner_auth.version)
            if self.server.tasks is None:raise ServiceError('TASK_RUNTIME_UNAVAILABLE',status=503)
            result=(self.server.tasks.drafts() if path=='/api/task-drafts' else
                    self.server.tasks.draft(path[len('/api/task-drafts/'):]))
            self.server.sessions.validate(self._one_header('Cookie'),require_owner=True,credential_version=self.server.owner_auth.version)
        elif path == '/api/tasks' or path.startswith('/api/tasks/'):
            self.server.sessions.validate(self._one_header('Cookie'),require_owner=True,credential_version=self.server.owner_auth.version)
            single_task = path.startswith('/api/tasks/') and ID.fullmatch(path[len('/api/tasks/'):])
            if query and (not single_task or set(query) != {'expected_result_version'}
                          or not query['expected_result_version']):
                raise ServiceError('INVALID_QUERY')
            if self.server.tasks is None:raise ServiceError('TASK_RUNTIME_UNAVAILABLE',status=503)
            if path=='/api/tasks':result=self.server.tasks.list()
            elif path.startswith('/api/tasks/options/'):result=self.server.tasks.options(path[len('/api/tasks/options/'):])
            elif path.endswith('/preview') and not single_task:result=self.server.tasks.business_preview(path[len('/api/tasks/'):-len('/preview')])
            elif query:result=self.server.tasks.get(path[len('/api/tasks/'):],expected_result_version=query['expected_result_version'])
            else:result=self.server.tasks.get(path[len('/api/tasks/'):])
            self.server.sessions.validate(self._one_header('Cookie'),require_owner=True,credential_version=self.server.owner_auth.version)
        elif path == '/api/materials' or path.startswith('/api/materials/'):
            def authorize():
                if not self.server.owner_auth.configured: raise ServiceError('OWNER_AUTH_UNAVAILABLE', status=503)
                return self.server.sessions.validate(self._one_header('Cookie'), require_owner=True,
                    credential_version=self.server.owner_auth.version)
            authorize()
            if self.server.materials is None: raise ServiceError('MATERIAL_VIEW_UNAVAILABLE', status=503)
            if path == '/api/materials':
                if query: raise ServiceError('INVALID_QUERY')
                result = self.server.materials.catalog(authorize)
            else:
                if set(query) != {'version'}: raise ServiceError('INVALID_QUERY')
                result = self.server.materials.read(path[len('/api/materials/'):], query['version'],
                    range_header=self._one_header('Range'), authorize=authorize)
            authorize()
        elif path.startswith('/api/live-preview/'):
            if query: raise ServiceError('INVALID_QUERY')
            ident = path[len('/api/live-preview/'):]
            if not ID.fullmatch(ident): raise ServiceError('INVALID_QUERY')
            self.server.sessions.validate(self._one_header('Cookie'), require_owner=True,
                                credential_version=self.server.owner_auth.version)
            live = getattr(self.server.previews, 'readonly_preview', None)
            if live is None: raise ServiceError('REAL_PREVIEW_UNAVAILABLE', status=503)
            selected = self.server.previews.resolve(ident)
            if selected['binding']['candidate_id'] != live.candidate_id:
                raise ServiceError('PREVIEW_UNAVAILABLE', status=404)
            result = live.describe()
            self.server.sessions.validate(self._one_header('Cookie'), require_owner=True,
                                credential_version=self.server.owner_auth.version)
        elif path.startswith('/api/preview-bridge/'):
            if query: raise ServiceError('INVALID_QUERY')
            ident = path[len('/api/preview-bridge/'):]
            if not ID.fullmatch(ident): raise ServiceError('INVALID_QUERY')
            if not self.server.owner_auth.configured: raise ServiceError('OWNER_AUTH_UNAVAILABLE', status=503)
            self.server.sessions.validate(self._one_header('Cookie'), require_owner=True,
                                credential_version=self.server.owner_auth.version)
            session_id = self.server.sessions._session_id(self._one_header('Cookie'))
            bridge = getattr(self.server.previews, 'fixture_bridge', None)
            if bridge is None:
                result = {'available': False, 'reason': 'BRIDGE_DISABLED', 'execution_allowed': False}
            elif bridge.binding['preview_id'] != ident:
                raise ServiceError('PREVIEW_UNAVAILABLE', status=404)
            else:
                result = bridge.issue((session_id, self.server.owner_auth.version))
            self.server.sessions.validate(self._one_header('Cookie'), require_owner=True,
                                credential_version=self.server.owner_auth.version)
        elif path in {'/api/previews', '/api/annotations'} or path.startswith('/api/previews/') or path.startswith('/api/annotations/requests/'):
            if query:
                raise ServiceError('INVALID_QUERY')
            if self.server.previews is None:
                raise ServiceError('WORKBENCH_UNAVAILABLE', status=503)
            if path == '/api/previews': result = self.server.previews.catalog()
            elif path == '/api/annotations': result = self.server.previews.annotations()
            elif path.startswith('/api/annotations/requests/'):
                request_id = path[len('/api/annotations/requests/'):]
                if not ID.fullmatch(request_id): raise ServiceError('INVALID_QUERY')
                result = self.server.previews.receipt(request_id)
            elif path.endswith('/image'):
                preview_id = path[len('/api/previews/'):-len('/image')]
                if not ID.fullmatch(preview_id): raise ServiceError('INVALID_QUERY')
                result = self.server.previews.resolve(preview_id, image=True)
            else: raise ServiceError('NOT_FOUND', status=404)
        elif path == '/api/host':
            if query:
                raise ServiceError('INVALID_QUERY')
            if self.server.host_observer:
                self.server.host_observer.demand()
            result = (self.server.host_observer.snapshot() if self.server.host_observer else
                      {'available': False, 'metrics': {}, 'reason': 'OBSERVER_NOT_RUNNING',
                       'capacity_recommendation': None})
        elif path == "/api/management":
            if set(query) != {"project_id"}:
                raise ServiceError("INVALID_QUERY")
            result = self.server.projects.get_management(query["project_id"])
        elif path == "/api/sync-status":
            if query:
                raise ServiceError("INVALID_QUERY")
            from .refresh_schedule import refresh_schedule_status
            status = self.server.projects.sync_status()
            interval = (status.get("schedule_policy") or {}).get("interval_hours", 12)
            result = {**status, "schedule": refresh_schedule_status(interval_hours=interval)}
        elif path == "/api/projects":
            snapshot = self._design_snapshot()
            result = self.server.projects.list_projects(query=query, design_snapshot=snapshot)
        elif re.fullmatch(r'/api/projects/[^/]+/captures(?:/.*)?', path):
            if query:
                raise ServiceError('INVALID_QUERY')
            parts = path[len('/api/projects/'):].split('/')
            if (len(parts) not in {2, 4} or not ID.fullmatch(parts[0]) or parts[1] != 'captures'
                    or len(parts) == 4 and (not ID.fullmatch(parts[2]) or parts[3] != 'image')):
                raise ServiceError('INVALID_QUERY')
            from .capture_store import CaptureStore
            from .capture_worker import capture_catalog, read_capture_image
            from .connection_records import RecordError
            try:
                result = (capture_catalog(self.server.projects.get_project(parts[0]), CaptureStore(self.server.projects.root))
                          if len(parts) == 2 else read_capture_image(self.server.projects, parts[0], parts[2]))
            except RecordError:
                raise ServiceError('CAPTURE_STORE_REJECTED', status=503) from None
        elif re.fullmatch(r'/api/projects/[^/]+/actions(?:/artifact)?', path):
            parts=path[len('/api/projects/'):].split('/')
            if not ID.fullmatch(parts[0]):raise ServiceError('INVALID_QUERY')
            def authorize_action_read():
                self.server.sessions.validate(self._one_header('Cookie'), credential_version=self.server.owner_auth.version)
            authorize_action_read()
            if len(parts)==2:
                if query:raise ServiceError('INVALID_QUERY')
                result=self.server.actions.catalog(parts[0])
            else:
                if set(query)!={'binding'}:raise ServiceError('INVALID_QUERY')
                try:binding=json.loads(query['binding'],object_pairs_hook=_object_pairs,parse_constant=_reject_constant)
                except (ValueError,TypeError):raise ServiceError('ACTION_BINDING_INVALID') from None
                if type(binding)is not dict or binding.get('project_id')!=parts[0]:raise ServiceError('ACTION_BINDING_INVALID')
                result=self.server.actions.read_with_receipt(binding,authorize=authorize_action_read)
            authorize_action_read()
        elif re.fullmatch(r'/api/projects/[^/]+/graph', path):
            project_id = path[len('/api/projects/'):-len('/graph')]
            if query or not ID.fullmatch(project_id):
                raise ServiceError('INVALID_QUERY')
            result = self.server.projects.get_graph(project_id, design_snapshot=self._design_snapshot())
        elif path.startswith("/api/projects/"):
            project_id = path[len("/api/projects/"):]
            if query or not ID.fullmatch(project_id):
                raise ServiceError("INVALID_QUERY")
            snapshot = self._design_snapshot()
            result = self.server.projects.get_project(project_id, design_snapshot=snapshot)
        elif path == "/api/designs":
            if query:
                raise ServiceError("INVALID_QUERY")
            result = self.server.designs.snapshot()
        elif path.startswith("/api/artifacts/"):
            artifact_id = path[len("/api/artifacts/"):]
            if (not ID.fullmatch(artifact_id) or set(query) != {"candidate_id", "candidate_revision"}
                    or not ID.fullmatch(query["candidate_id"])
                    or not re.fullmatch(r"[1-9][0-9]{0,8}", query["candidate_revision"])):
                raise ServiceError("INVALID_QUERY")
            result = self.server.designs.artifact(artifact_id, candidate_id=query["candidate_id"],
                                                 candidate_revision=int(query["candidate_revision"]))
        elif path.startswith("/api/exports/"):
            request_id = path[len("/api/exports/"):]
            if (not ID.fullmatch(request_id) or set(query) != {
                    "candidate_id", "candidate_revision", "candidate_hash", "store_revision"}
                    or not ID.fullmatch(query["candidate_id"])
                    or not re.fullmatch(r"[1-9][0-9]{0,8}", query["candidate_revision"])
                    or not re.fullmatch(r"[1-9][0-9]{0,8}", query["store_revision"])
                    or not re.fullmatch(r"[0-9a-f]{64}", query["candidate_hash"])):
                raise ServiceError("INVALID_QUERY")
            result = self.server.designs.export_download({
                "request_id": request_id, "expected_revision": int(query["store_revision"]),
                "candidate": {"id": query["candidate_id"], "revision": int(query["candidate_revision"]),
                              "content_hash": query["candidate_hash"]}})
        else:
            raise ServiceError("NOT_FOUND", status=404)
        self._success(result)

    def _handle(self, *, mutation=False):
        operation_started = False
        try:
            self._boundary(mutation=mutation)
            if not mutation and self.path in WEB_ASSETS:
                name, content_type = WEB_ASSETS[self.path]
                asset = WEB_ROOT / name
                if asset.is_symlink() or not asset.is_file():
                    raise ServiceError("UI_ASSET_UNAVAILABLE", status=503)
                self._send(asset.read_bytes(), content_type=content_type, ui=True)
                return
            path, query = self._route()
            if not mutation:
                self._dispatch_get(path, query)
                return
            if query:
                raise ServiceError("INVALID_QUERY")
            token = self._one_header("X-Hub-CSRF")
            if not token:
                raise ServiceError("CSRF_REJECTED", status=403)
            owner = self.server.sessions.validate(self._one_header("Cookie"), token, credential_version=self.server.owner_auth.version)
            if owner and self.server.sessions.owner_store is not None:
                self._owner_response_session = self.server.sessions._session_id(self._one_header('Cookie'))
            command = self._body()
            if path == '/api/owner/login':
                if set(command) != {'token'}:
                    raise ServiceError('INVALID_OWNER_PROOF')
                version = self.server.owner_auth.verify(command['token'])
                session, csrf = self.server.sessions.issue(owner=True, credential_version=version)
                self.server.sessions.revoke(self._one_header('Cookie'))
                cookie = self.server.sessions.cookie_header(session, owner=True)
                self._json({'api_version': API_VERSION, 'ok': True, 'data': {'csrf_token': csrf}},
                           headers={'Set-Cookie': cookie})
                return
            if path == '/api/owner/logout':
                if command:
                    raise ServiceError('INVALID_OWNER_PROOF')
                self.server.sessions.revoke(self._one_header('Cookie'))
                self._json({'api_version': API_VERSION, 'ok': True, 'data': {'owner_authenticated': False}},
                           headers={'Set-Cookie': self.server.sessions.cookie_header('', clear=True)})
                return
            if path not in {"/api/refresh", "/api/designs/decisions", "/api/designs/exports", '/api/annotations', '/api/task-drafts', '/api/ordinary-drafts', '/api/tasks', '/api/management/project', '/api/actions/verify'} and not (path.startswith('/api/tasks/')and path.endswith(('/cancel','/respond','/review'))):
                raise ServiceError("NOT_FOUND", status=404)
            if not self.server.owner_auth.configured:
                raise ServiceError('OWNER_AUTH_UNAVAILABLE', status=503)
            self.server.sessions.validate(self._one_header('Cookie'), token, require_owner=True, credential_version=self.server.owner_auth.version)
            operation_started = True
            if path == '/api/actions/verify':
                version=self.server.owner_auth.version
                def authorize_action():
                    self._boundary(mutation=True)
                    if self.server.owner_auth.version!=version:raise ServiceError('OWNER_AUTH_REQUIRED',status=401)
                    self.server.sessions.validate(self._one_header('Cookie'),token,require_owner=True,credential_version=version)
                result=self.server.actions.verify(command,authorize=authorize_action)
                authorize_action()
            elif path == '/api/tasks' or path.startswith('/api/tasks/') or path=='/api/ordinary-drafts':
                if self.server.tasks is None:raise ServiceError('TASK_RUNTIME_UNAVAILABLE',status=503)
                version=self.server.owner_auth.version
                # Stable across Hub restarts, rotated with the owner credential.
                owner_context=hashlib.sha256(b'hub-task-owner-v1'+version).hexdigest()
                def authorize_task():
                    self.server.sessions.validate(self._one_header('Cookie'),token,require_owner=True,credential_version=self.server.owner_auth.version)
                    if self.server.owner_auth.version!=version:raise ServiceError('OWNER_AUTH_FAILED',status=401)
                if path=='/api/ordinary-drafts':result=self.server.tasks.save_ordinary_draft(command,owner_context=owner_context,authorize=authorize_task)
                elif path=='/api/tasks':result=self.server.tasks.submit(command,owner_context=owner_context,authorize=authorize_task)
                elif path.endswith('/review'):result=self.server.tasks.review(path[len('/api/tasks/'):-len('/review')],command,owner_context=owner_context,authorize=authorize_task)
                elif path.endswith('/respond'):result=self.server.tasks.respond_control(path[len('/api/tasks/'):-len('/respond')],command,owner_context=owner_context,authorize=authorize_task)
                else:
                    if command:raise ServiceError('INVALID_TASK_REQUEST')
                    result=self.server.tasks.cancel(path[len('/api/tasks/'):-len('/cancel')],owner_context=owner_context,authorize=authorize_task)
            elif path == '/api/management/project':
                version = self.server.owner_auth.version
                def authorize_management():
                    self.server.sessions.validate(self._one_header('Cookie'), token, require_owner=True,
                                                  credential_version=self.server.owner_auth.version)
                    if self.server.owner_auth.version != version:
                        raise ServiceError('OWNER_AUTH_FAILED', status=401)
                result = self.server.projects.save_management(command, authorize=authorize_management)
            elif path == '/api/task-drafts':
                if self.server.tasks is None:raise ServiceError('TASK_RUNTIME_UNAVAILABLE',status=503)
                version=self.server.owner_auth.version
                def authorize_draft():
                    self.server.sessions.validate(self._one_header('Cookie'), token, require_owner=True,
                                                  credential_version=self.server.owner_auth.version)
                    if self.server.owner_auth.version!=version:raise ServiceError('OWNER_AUTH_FAILED',status=401)
                    return self.server.sessions._session_id(self._one_header('Cookie'))
                result=self.server.tasks.create_draft(command,backend_instance=self.server.backend_instance,
                    context=(authorize_draft(),version),authorize=authorize_draft)
            elif path == '/api/annotations':
                if self.server.previews is None: raise ServiceError('WORKBENCH_UNAVAILABLE', status=503)
                def authorize_annotation():
                    self.server.sessions.validate(self._one_header('Cookie'), token, require_owner=True,
                                                  credential_version=self.server.owner_auth.version)
                    return self.server.sessions._session_id(self._one_header('Cookie'))
                result = self.server.previews.save(command, backend_instance=self.server.backend_instance,
                    context=(authorize_annotation(), self.server.owner_auth.version), authorize=authorize_annotation)
            elif path == "/api/refresh":
                result = self.server.projects.refresh(command)
            elif path == "/api/designs/decisions":
                result = self.server.designs.decide(command, owner_action=OwnerAction(
                    fixture=self.server.designs.store.fixture))
            else:
                result = self.server.designs.export(command)
            self._success(result)
        except ServiceError as error:
            self._error(error)
        except (socket.timeout, TimeoutError):
            self._error(ServiceError("REQUEST_TIMEOUT", status=408,
                                     outcome="UNKNOWN" if operation_started else "NOT_COMMITTED"))
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self._error(ServiceError("INTERNAL_ERROR", status=500,
                                     outcome="UNKNOWN" if operation_started else "NOT_COMMITTED"))

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle(mutation=True)

    def do_OPTIONS(self):
        self._error(ServiceError("CORS_REJECTED", status=403))

    def do_HEAD(self):
        self._error(ServiceError("METHOD_REJECTED", status=405))

    do_PUT = do_DELETE = do_PATCH = do_HEAD
