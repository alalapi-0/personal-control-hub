"""Loopback-only HTTP transport for the Hub's local application contract.

No project or design facts are duplicated here. GETs do not refresh sources or
initialize stores. There is intentionally no UI before the owner's Figma choice.
"""
from __future__ import annotations

import hmac
import json
import re
import secrets
import socket
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

from .service_contract import ArtifactResponse, OwnerAction, ServiceError

API_VERSION = "1.0"
MAX_BODY_BYTES = 64 * 1024
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,160}\Z")


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
    def __init__(self, *, clock=time.monotonic, ttl=3600, capacity=32):
        self.clock, self.ttl, self.capacity = clock, ttl, capacity
        self.values = {}
        self.lock = threading.Lock()
        self.cookie_name = "hub_" + secrets.token_hex(8)

    def issue(self):
        with self.lock:
            now = self.clock()
            self.values = {key: value for key, value in self.values.items() if value[1] > now}
            while len(self.values) >= self.capacity:
                del self.values[next(iter(self.values))]
            session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.values[session] = (csrf, now + self.ttl)
            return session, csrf

    def validate(self, cookie, csrf=None):
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
        with self.lock:
            saved = self.values.get(session)
            if saved is None or saved[1] <= self.clock():
                self.values.pop(session, None)
                raise ServiceError("SESSION_REQUIRED", status=401)
            if csrf is not None and (not isinstance(csrf, str) or not csrf.isascii() or
                                     not hmac.compare_digest(saved[0], csrf)):
                raise ServiceError("CSRF_REJECTED", status=403)


class HubHTTPServer(ThreadingHTTPServer):
    """Explicit loopback binding and bounded, joined request workers."""
    daemon_threads = False
    allow_reuse_address = False
    request_queue_size = 8

    def __init__(self, projects, designs, *, host="127.0.0.1", port=0):
        if host != "127.0.0.1":
            raise ServiceError("NON_LOOPBACK_BIND_REJECTED")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ServiceError("INVALID_PORT")
        self.projects, self.designs = projects, designs
        self.sessions = _Sessions()
        self._slots = threading.BoundedSemaphore(8)
        super().__init__((host, port), HubRequestHandler)
        self.host_header = f"127.0.0.1:{self.server_address[1]}"
        self.origin = "http://" + self.host_header

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(5)
        return request, address

    def verify_request(self, request, client_address):
        return client_address[0] == "127.0.0.1"

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
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

    def _send(self, data, *, status=200, content_type="application/json; charset=utf-8", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; sandbox; frame-ancestors 'none'")
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
        if isinstance(value, ArtifactResponse):
            if not FILENAME.fullmatch(value.filename) or value.disposition not in {"inline", "attachment"}:
                raise ServiceError("INVALID_ARTIFACT_RESPONSE", status=500)
            if value.content_type not in {"application/octet-stream", "application/zip", "image/png",
                                          "image/jpeg", "image/webp", "image/gif", "text/plain; charset=utf-8"}:
                raise ServiceError("INVALID_ARTIFACT_RESPONSE", status=500)
            self._send(value.data, content_type=value.content_type,
                       headers={"Content-Disposition": f'{value.disposition}; filename="{value.filename}"'})
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
        if path in {"/api/health", "/api/session"}:
            if query:
                raise ServiceError("INVALID_QUERY")
            if path == "/api/health":
                self._success({"service": "hub-local", "ui_implemented": False})
            else:
                session, csrf = self.server.sessions.issue()
                cookie = f"{self.server.sessions.cookie_name}={session}; HttpOnly; SameSite=Strict; Path=/api"
                self._json({"api_version": API_VERSION, "ok": True, "data": {"csrf_token": csrf}},
                           headers={"Set-Cookie": cookie})
            return
        self.server.sessions.validate(self._one_header("Cookie"))
        if path == "/api/projects":
            snapshot = self._design_snapshot()
            result = self.server.projects.list_projects(query=query, design_snapshot=snapshot)
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
            path, query = self._route()
            if not mutation:
                self._dispatch_get(path, query)
                return
            if query:
                raise ServiceError("INVALID_QUERY")
            token = self._one_header("X-Hub-CSRF")
            if not token:
                raise ServiceError("CSRF_REJECTED", status=403)
            self.server.sessions.validate(self._one_header("Cookie"), token)
            command = self._body()
            if path not in {"/api/refresh", "/api/designs/decisions", "/api/designs/exports"}:
                raise ServiceError("NOT_FOUND", status=404)
            operation_started = True
            if path == "/api/refresh":
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
