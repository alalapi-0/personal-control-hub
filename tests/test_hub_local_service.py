from __future__ import annotations

import contextlib
import http.client
import io
import json
import socket
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hub.local_service import HubHTTPServer, MAX_BODY_BYTES, _Sessions
from hub.service_contract import ArtifactResponse, OwnerAction, ServiceError


class ProjectSpy:
    def __init__(self):
        self.calls = []

    def list_projects(self, query=None, design_snapshot=None):
        self.calls.append(("list", query, design_snapshot))
        return {"projects": [{"project_id": "fixture-project", "status": "unknown"}]}

    def get_project(self, project_id, design_snapshot=None):
        self.calls.append(("detail", project_id, design_snapshot))
        return {"project_id": project_id, "status": "unknown"}

    def refresh(self, command):
        self.calls.append(("refresh", command))
        return {"outcome": "COMMITTED", "request_id": command["request_id"]}


class DesignSpy:
    store = SimpleNamespace(fixture=True)

    def __init__(self):
        self.calls = []

    def snapshot(self):
        self.calls.append(("snapshot",))
        return {"store_revision": 7, "store_classification": "synthetic_fixture"}

    def decide(self, command, *, owner_action):
        self.calls.append(("decide", command, owner_action))
        return {"outcome": "COMMITTED", "request_id": command["request_id"]}

    def export(self, command):
        self.calls.append(("export", command))
        return {"outcome": "COMMITTED"}

    def export_download(self, command):
        self.calls.append(("export_download", command))
        raise ServiceError("EXPORT_NOT_FOUND", status=404)

    def artifact(self, artifact_id, *, candidate_id, candidate_revision):
        self.calls.append(("artifact", artifact_id, candidate_id, candidate_revision))
        return ArtifactResponse(b"<script>never executes</script>", "application/octet-stream", "fixture-artifact.bin")


class LocalHTTPTests(unittest.TestCase):
    def setUp(self):
        self.projects, self.designs = ProjectSpy(), DesignSpy()
        self.server = HubHTTPServer(self.projects, self.designs)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.addCleanup(self.stop)
        self.cookie, self.csrf = None, None

    def stop(self):
        if self.thread.is_alive():
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(2)
        self.assertFalse(self.thread.is_alive())

    def call(self, method="GET", path="/api/projects", body=None, headers=None, *, authenticated=True):
        supplied = {}
        if authenticated and self.cookie:
            supplied["Cookie"] = self.cookie
        if method == "POST":
            supplied.update({"Origin": self.server.origin, "X-Hub-CSRF": self.csrf or "missing",
                             "Content-Type": "application/json"})
        supplied.update(headers or {})
        supplied = {key: value for key, value in supplied.items() if value is not None}
        encoded = json.dumps(body).encode() if isinstance(body, dict) else body
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=3)
        try:
            conn.request(method, path, body=encoded, headers=supplied)
            response = conn.getresponse()
            data = response.read()
            result_headers = dict(response.getheaders())
            if result_headers.get("Content-Type", "").startswith("application/json"):
                data = json.loads(data)
            return response.status, result_headers, data
        finally:
            conn.close()

    def session(self):
        status, headers, data = self.call(path="/api/session", authenticated=False)
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.csrf = data["data"]["csrf_token"]
        return headers

    def test_bind_rejects_every_nonliteral_loopback_form(self):
        for host in ("0.0.0.0", "::", "::1", "localhost", "127.0.0.2", "127.1", "example.com"):
            with self.subTest(host=host), self.assertRaises(ServiceError) as caught:
                HubHTTPServer(self.projects, self.designs, host=host)
            self.assertEqual(caught.exception.code, "NON_LOOPBACK_BIND_REJECTED")

    def test_start_query_stop_is_joined_and_port_is_closed(self):
        headers = self.session()
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        status, _, listing = self.call()
        self.assertEqual(status, 200)
        self.assertEqual(listing["data"]["projects"][0]["status"], "unknown")
        self.assertEqual(self.projects.calls[0][2]["store_revision"], 7)
        self.assertEqual(self.designs.calls, [("snapshot",)])
        address = self.server.server_address
        self.stop()
        with socket.socket() as probe:
            probe.settimeout(0.3)
            self.assertNotEqual(probe.connect_ex(address), 0)

    def test_unauthenticated_reads_reject_before_data_access(self):
        status, _, data = self.call()
        self.assertEqual((status, data["error"]["code"]), (401, "SESSION_REQUIRED"))
        self.assertEqual(self.projects.calls + self.designs.calls, [])

    def test_hostile_origins_and_fetch_metadata_cannot_bootstrap_or_read(self):
        self.session()
        for path in ("/api/session", "/api/projects", "/api/designs"):
            for attack in ({"Origin": "https://evil.invalid"}, {"Origin": "null"},
                           {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                           {"Host": "evil.invalid"}, {"Host": "localhost:" + str(self.server.server_address[1])}):
                with self.subTest(path=path, attack=attack):
                    status, _, data = self.call(path=path, headers=attack)
                    self.assertEqual(status, 403)
                    self.assertFalse(data["ok"])
        self.assertEqual(self.projects.calls + self.designs.calls, [])

    def test_mutations_require_same_origin_cookie_and_bound_token(self):
        self.session()
        for attack in ({"Origin": None}, {"Origin": "http://evil.invalid"}, {"Origin": "null"},
                       {"X-Hub-CSRF": None}, {"X-Hub-CSRF": "wrong"}, {"Cookie": None},
                       {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "none"}):
            with self.subTest(attack=attack):
                status, _, _ = self.call("POST", "/api/refresh", {"request_id": "fixture-one"}, attack)
                self.assertIn(status, {401, 403})
        self.assertEqual(self.projects.calls + self.designs.calls, [])

    def test_csrf_token_is_session_bound(self):
        self.session()
        first_token = self.csrf
        self.session()
        status, _, result = self.call("POST", "/api/refresh", {"request_id": "fixture-one"},
                                     {"X-Hub-CSRF": first_token})
        self.assertEqual((status, result["error"]["code"]), (403, "CSRF_REJECTED"))
        self.assertEqual(self.projects.calls, [])

    def test_duplicate_host_origin_cookie_and_framing_rejected(self):
        self.session()
        for key, value in (("Host", self.server.host_header), ("Origin", self.server.origin),
                           ("Cookie", self.cookie), ("Content-Length", "0")):
            with self.subTest(key=key):
                conn = http.client.HTTPConnection(*self.server.server_address, timeout=3)
                try:
                    conn.putrequest("GET", "/api/projects", skip_host=True)
                    conn.putheader("Host", self.server.host_header)
                    if key != "Host":
                        conn.putheader(key, value)
                    if key != "Cookie":
                        conn.putheader("Cookie", self.cookie)
                    conn.putheader(key, value)
                    conn.endheaders()
                    response = conn.getresponse()
                    self.assertEqual(response.status, 400)
                    self.assertEqual(json.loads(response.read())["error"]["code"], "AMBIGUOUS_HEADERS")
                finally:
                    conn.close()
        self.assertEqual(self.projects.calls + self.designs.calls, [])

    def test_strict_bounded_json_and_transport_framing(self):
        self.session()
        for body, headers, expected in (
                (b'{"request_id":"one","request_id":"two"}', {}, 400),
                (b'{"value":NaN}', {}, 400),
                (b'[]', {}, 400), (b'\xff', {}, 400),
                (b'{}', {"Content-Type": "text/plain"}, 415),
                (b'{}', {"Content-Type": "application/x-www-form-urlencoded"}, 415),
                (b'{}', {"Transfer-Encoding": "chunked"}, 400),
                (b'{}', {"Content-Length": str(MAX_BODY_BYTES + 1)}, 413)):
            with self.subTest(expected=expected, headers=headers):
                status, _, _ = self.call("POST", "/api/refresh", body, headers)
                self.assertEqual(status, expected)
        self.assertEqual(self.projects.calls + self.designs.calls, [])

    def test_routes_queries_and_methods_fail_closed(self):
        self.session()
        for path in ("/api/projects/foo/bar", "/api/projects/%2e%2e", "/api/projects/../secret",
                     "/api/projects?q=a&q=b", "/api/designs?unexpected=true", "/api/unknown",
                     "/etc/passwd", "http://evil.invalid/api/projects", "/api/artifacts/../secret",
                     "/api/artifacts/a?candidate_id=b&candidate_revision=0"):
            with self.subTest(path=path):
                status, _, result = self.call(path=path)
                self.assertIn(status, {400, 403, 404})
                self.assertFalse(result["ok"])
        for method in ("OPTIONS", "PUT", "DELETE", "PATCH"):
            status, headers, _ = self.call(method, "/api/designs")
            self.assertIn(status, {403, 405})
            self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self.projects.calls + self.designs.calls, [])

    def test_explicit_command_dispatch_and_internal_owner_source(self):
        self.session()
        command = {"request_id": "fixture-one", "action": "select"}
        status, _, _ = self.call("POST", "/api/designs/decisions", command)
        self.assertEqual(status, 200)
        _, received, capability = self.designs.calls[-1]
        self.assertEqual(received, command)
        self.assertIs(type(capability), OwnerAction)
        self.assertTrue(capability.fixture)
        self.assertEqual(capability.source("fixture-one")["type"], "synthetic_fixture")

    def test_inert_artifact_download_and_security_headers(self):
        self.session()
        status, headers, data = self.call(path="/api/artifacts/fixture-artifact?candidate_id=fixture-candidate&candidate_revision=1")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))
        self.assertIn("sandbox", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(data, b"<script>never executes</script>")

    def test_errors_preserve_postcommit_outcome_without_paths_or_logs(self):
        self.session()
        with mock.patch.object(self.designs, "decide", side_effect=ServiceError(
                "COMMITTED_VERIFICATION_UNCONFIRMED", status=503, outcome="COMMITTED_UNCONFIRMED",
                details={"request_id": "fixture-one", "revision": 8})):
            status, _, data = self.call("POST", "/api/designs/decisions", {"request_id": "fixture-one"})
        self.assertEqual(status, 503)
        self.assertEqual(data["error"]["outcome"], "COMMITTED_UNCONFIRMED")
        logs = io.StringIO()
        with contextlib.redirect_stderr(logs), mock.patch.object(self.projects, "refresh", side_effect=RuntimeError(
                "/private/secret-token=never-log-this")):
            status, _, data = self.call("POST", "/api/refresh", {"request_id": "fixture-one"})
        self.assertEqual(status, 500)
        self.assertEqual(data["error"]["outcome"], "UNKNOWN")
        self.assertNotIn("private", json.dumps(data))
        self.assertNotIn("secret", logs.getvalue())

    def test_response_serialization_failure_is_not_reported_uncommitted(self):
        self.session()
        with mock.patch.object(self.projects, "refresh", return_value={"committed": True, "unserializable": object()}):
            status, _, data = self.call("POST", "/api/refresh", {"request_id": "fixture-one"})
        self.assertEqual(status, 500)
        self.assertEqual(data["error"]["outcome"], "UNKNOWN")

    def test_design_failure_is_explicit_without_hiding_independent_projects(self):
        self.session()
        with mock.patch.object(self.designs, "snapshot", side_effect=ServiceError("DESIGN_STORE_CORRUPT", status=500)):
            status, _, data = self.call()
            self.assertEqual(status, 200)
            self.assertTrue(data["ok"])
            snapshot = self.projects.calls[-1][2]
            self.assertFalse(snapshot["available"])
            self.assertEqual(snapshot["reason"], "DESIGN_STORE_CORRUPT")
            self.assertIsNone(snapshot["store_revision"])
            status, _, data = self.call(path="/api/designs")
            self.assertEqual((status, data["error"]["code"]), (500, "DESIGN_STORE_CORRUPT"))

    def test_missing_export_download_is_read_only_and_exactly_bound(self):
        self.session()
        path = ("/api/exports/fixture-request?candidate_id=fixture-candidate&candidate_revision=2"
                "&candidate_hash=" + "a" * 64 + "&store_revision=7")
        status, _, data = self.call(path=path)
        self.assertEqual((status, data["error"]["code"]), (404, "EXPORT_NOT_FOUND"))
        self.assertEqual(self.designs.calls, [("export_download", {
            "request_id": "fixture-request", "expected_revision": 7,
            "candidate": {"id": "fixture-candidate", "revision": 2, "content_hash": "a" * 64}})])

    def test_session_expiry_capacity_and_restarted_server_reject_old_session(self):
        clock = [0]
        sessions = _Sessions(clock=lambda: clock[0], ttl=10, capacity=2)
        key, token = sessions.issue()
        cookie = sessions.cookie_name + "=" + key
        sessions.validate(cookie, token)
        clock[0] = 11
        with self.assertRaises(ServiceError):
            sessions.validate(cookie, token)
        for _ in range(3):
            sessions.issue()
        self.assertEqual(len(sessions.values), 2)
        other = _Sessions()
        with self.assertRaises(ServiceError):
            other.validate(cookie, token)


if __name__ == "__main__":
    unittest.main()
