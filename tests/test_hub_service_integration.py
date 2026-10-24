from __future__ import annotations

import copy
import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from connection_fixtures import Fixture
import test_hub_local_service as transport_fixtures
from hub.connection_records import content_hash
from hub.connection_refresh import GENESIS_HASH
from hub.connection_sources import SourceResolver
from hub.design_cli import _artifact, _baseline, _candidate, FIXTURE_TIME
from hub.design_service import DesignService
from hub.design_store import DesignStore
from hub.local_service import HubHTTPServer
from hub.owner_auth import OwnerAuth
import secrets
from hub.project_service import ProjectService



def project_facts(dto):
    value = copy.deepcopy(dto)
    for key in ('read_age_seconds', 'source_mtime_age_seconds', 'projected_at'):
        value['freshness'].pop(key, None)
    for key in ('age_seconds', 'source_mtime_age_seconds'):
        value['last_success_snapshot'].pop(key, None)
    return value

class LocalServiceIntegrationTests(unittest.TestCase):
    """Real libraries and HTTP lifecycle, entirely inside a temporary Hub."""
    call = transport_fixtures.LocalHTTPTests.call
    session = transport_fixtures.LocalHTTPTests.session
    stop = transport_fixtures.LocalHTTPTests.stop

    def setUp(self):
        self.source_fixture = Fixture()
        self.addCleanup(self.source_fixture.close)
        self.root = self.source_fixture.hub
        self.source_fixture.business["current_work"]["status"] = "active"
        self.source_fixture.business["current_work"]["completed"] = False
        self.source_fixture.add("fixture-project")
        self.source_fixture.add("manga-localizer")
        # A synthetic explicit denial; no real project root is used.
        self.source_fixture.projects[1]["connection_read_allowed"] = False
        self.source_fixture.save_registry()
        self.material = self.root / "docs/reports/ui_design_governance/unit-04/integration"
        self.material.mkdir(parents=True)
        artifacts = []
        for name in ("baseline", "candidate"):
            path = self.material / (name + ".txt")
            payload = ("Synthetic " + name + " material\n").encode()
            path.write_bytes(payload)
            artifacts.append(_artifact("fixture-" + name + "-material", str(path.relative_to(self.root)),
                                       hashlib.sha256(payload).hexdigest()))
        self.baseline = _baseline(1, artifacts[0])
        self.candidate = _candidate(1, self.baseline, artifacts[1])
        self.store = DesignStore(self.root, self.material / "fixture-store.json", fixture=True)
        self.store.initialize()
        for i, fact in enumerate([*artifacts, self.baseline, self.candidate]):
            self.store.append_fact(fact, expected_revision=self.store.read()["revision"],
                                   request_id=f"fixture-fact-{i}")
        self.start()
        self.addCleanup(self.stop)

    def start(self):
        self.projects = ProjectService(self.root)
        self.designs = DesignService(DesignStore(self.root, self.store.path, fixture=True))
        self.owner_proof = secrets.token_urlsafe(40)
        self.server = HubHTTPServer(self.projects, self.designs,
                                    owner_auth=OwnerAuth(provider=lambda: self.owner_proof))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.cookie, self.csrf = None, None

    def command(self, request_id="fixture-choice"):
        return {"request_id": request_id, "event_id": request_id, "created_at": FIXTURE_TIME,
                "expected_revision": self.store.read()["revision"], "action": "select",
                "candidate": {key: self.candidate[key] for key in ("id", "revision", "content_hash")},
                "scope": self.candidate["scope"], "feedback": "Synthetic owner action", "supersedes": None}

    def test_committed_response_failure_restart_retry_history_original_and_export(self):
        self.session()
        command = self.command()
        original_decide = self.designs.decide
        def committed_but_bad_response(*args, **kwargs):
            result = original_decide(*args, **kwargs)
            return {**result, "invalid_response": object()}
        with mock.patch.object(self.designs, "decide", side_effect=committed_but_bad_response):
            status, _, failure = self.call("POST", "/api/designs/decisions", command)
        self.assertEqual(status, 500)
        self.assertEqual(failure["error"]["outcome"], "UNKNOWN")
        committed = self.store.read()
        self.assertEqual(len(committed["events"]), 1)
        store_digest = hashlib.sha256(self.store.path.read_bytes()).hexdigest()
        old_cookie = self.cookie
        self.stop()
        self.start()
        status, _, _ = self.call(headers={"Cookie": old_cookie})
        self.assertEqual(status, 401)
        self.session()
        status, _, retried = self.call("POST", "/api/designs/decisions", command)
        self.assertEqual(status, 200)
        self.assertEqual(retried["data"]["outcome"], "COMMITTED")
        self.assertEqual(hashlib.sha256(self.store.path.read_bytes()).hexdigest(), store_digest)
        status, _, history = self.call(path="/api/designs")
        self.assertEqual(status, 200)
        self.assertEqual(history["data"]["history"][0]["event"]["source"]["type"], "synthetic_fixture")
        self.assertEqual(self.store.projection()["real_selection_count"], 0)

        for artifact_id in ("fixture-baseline-material", "fixture-candidate-material"):
            status, headers, payload = self.call(path=f"/api/artifacts/{artifact_id}?candidate_id=fixture-candidate&candidate_revision=1")
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "application/octet-stream")
            self.assertTrue(payload.startswith(b"Synthetic"))

        export = {"request_id": "fixture-export", "expected_revision": committed["revision"],
                  "candidate": command["candidate"]}
        status, _, result = self.call("POST", "/api/designs/exports", export)
        self.assertEqual(status, 200)
        self.assertEqual(result["data"]["outcome"], "COMMITTED")
        candidate_hash = self.candidate["content_hash"]
        url = (f"/api/exports/fixture-export?candidate_id=fixture-candidate&candidate_revision=1"
               f"&candidate_hash={candidate_hash}&store_revision={committed['revision']}")
        status, headers, data = self.call(path=url)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/zip")
        self.assertEqual(hashlib.sha256(data).hexdigest(), result["data"]["sha256"])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        self.assertFalse(manifest["authority"]["implementation_authority"])
        self.assertFalse(manifest["authority"]["real_selection"])
        self.assertEqual(manifest["candidate_identity"]["content_hash"], candidate_hash)

        baseline2 = _baseline(2)
        self.store.append_fact(baseline2, expected_revision=committed["revision"], request_id="fixture-new-baseline")
        status, _, after = self.call(path="/api/designs")
        self.assertEqual(status, 200)
        self.assertTrue(after["data"]["history"][0]["stale"])
        status, _, _ = self.call(path="/api/artifacts/fixture-baseline-material?candidate_id=fixture-candidate&candidate_revision=1")
        self.assertEqual(status, 200)
        status, _, old_export = self.call(path=url)
        self.assertEqual(status, 200)
        self.assertEqual(old_export, data)
        new_action = self.command("fixture-stale-choice")
        new_action["supersedes"] = command["event_id"]
        status, _, rejected = self.call("POST", "/api/designs/decisions", new_action)
        self.assertEqual((status, rejected["error"]["code"]), (409, "BASELINE_STALE"))

    def test_project_gets_are_offline_parity_and_refresh_retry_is_durable(self):
        self.session()
        status, _, before = self.call()
        self.assertEqual(status, 200)
        self.assertEqual(before["data"]["total"], 2)
        self.assertFalse((self.root / "data/connections/connection_refresh.sqlite3").exists())
        command = {"request_id": "fixture-refresh", "project_ids": ["fixture-project", "manga-localizer"],
                   "expected_head": {"sequence": 0, "hash": GENESIS_HASH}}
        status, _, result = self.call("POST", "/api/refresh", command)
        self.assertEqual(status, 200)
        self.assertEqual(result["data"]["request"]["status"], "FINISHED")
        ledger = self.root / "data/connections/connection_refresh.sqlite3"
        ledger_digest = hashlib.sha256(ledger.read_bytes()).hexdigest()
        with mock.patch.object(SourceResolver, "refresh", side_effect=AssertionError("GET/retry must remain offline")):
            status, _, listing = self.call()
            self.assertEqual(status, 200)
            row = next(item for item in listing["data"]["projects"] if item["project_id"] == "fixture-project")
            status, _, detail = self.call(path="/api/projects/fixture-project")
            self.assertEqual(status, 200)
            self.assertGreaterEqual(detail["data"]["last_success_snapshot"]["age_seconds"],
                                    row["last_success_snapshot"]["age_seconds"])
            self.assertEqual(project_facts(row), project_facts(detail["data"]))
            self.assertEqual(row["business"]["normalized_status"], "active")
            self.assertTrue(any(f["id"] == "fixture-candidate" for f in row["design"]["references"]))
            status, _, retried = self.call("POST", "/api/refresh", command)
            self.assertEqual(status, 200)
            self.assertEqual(retried["data"]["appended_project_ids"], [])
        self.assertEqual(hashlib.sha256(ledger.read_bytes()).hexdigest(), ledger_digest)
        blocked = next(item for item in listing["data"]["projects"] if item["project_id"] == "manga-localizer")
        self.assertEqual(blocked["business"]["normalized_status"], "unknown")
        self.assertEqual(blocked["operational"]["latest_attempt"]["disposition"], "disabled")

    def test_confirmed_relation_cannot_restore_revoked_source_or_task_authority(self):
        self.stop()
        related_root, _ = self.source_fixture.add("related-project")
        denied_root = self.source_fixture.base / "manga-localizer"
        protected_sources = {path: path.read_bytes() for root in (related_root, denied_root)
                             for path in (root / "STATE.yaml", root / "hub.connection.yaml")}
        config = self.root / "data/connections/management_config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        base = yaml.safe_load((Path(__file__).resolve().parents[1] /
                               "data/connections/management_config.yaml").read_text())
        base.update(schema_version="1.0", projects={})
        base.pop("revision", None)
        base.pop("history", None)
        config.write_text(yaml.safe_dump(base, sort_keys=False))
        spec = importlib.util.spec_from_file_location(
            "relation_integration_entry", Path(__file__).resolve().parents[1] / "scripts/hub_server.py")
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        from hub import connection_sources
        from hub.codex_adapter import AppServerAdapter, ReadonlyAppServerAdapter
        from hub.management_store import ManagementStore
        from hub.ordinary_runtime import OrdinaryRuntime
        from hub.task_service import TaskService
        from hub.task_store import TaskStore
        from hub.task_worker import TaskWorker

        class InertObserver:
            pass

        with contextlib.ExitStack() as stack:
            forbidden = []
            for target, methods in ((AppServerAdapter, ("__init__", "open")),
                                    (ReadonlyAppServerAdapter, ("__init__", "open")),
                                    (OrdinaryRuntime, ("__init__", "start")),
                                    (TaskWorker, ("__init__", "run_one")),
                                    (entry, ("ordinary_provider_status",))):
                for method in methods:
                    forbidden.append(stack.enter_context(mock.patch.object(
                        target, method, side_effect=AssertionError("relation conferred execution authority"))))
            input_read = stack.enter_context(mock.patch(
                "hub.ordinary_tasks._read_input", side_effect=AssertionError("ungranted task input read")))
            source_reads = []
            real_read = connection_sources._safe_relative_read

            def allowed_source_read(root, relative, **kwargs):
                source_reads.append((root, relative))
                self.assertEqual(root, self.source_fixture.base / "fixture-project")
                return real_read(root, relative, **kwargs)

            stack.enter_context(mock.patch.object(connection_sources, "_safe_relative_read",
                                                  side_effect=allowed_source_read))
            self.server, _, _ = entry.assemble_server(self.root, port=0, services={
                "designs": self.designs, "host_observer": InertObserver(),
                "owner_auth": OwnerAuth(provider=lambda: self.owner_proof)})
            thread = None
            try:
                self.projects = self.server.projects
                self.assertIs(type(self.projects), ProjectService)
                self.assertIs(type(self.server.tasks), TaskService)
                self.assertIs(type(self.server.tasks.store), TaskStore)
                # Register only disposable storage, without any grants or runtime.
                self.server.tasks.store.register_storage()
                thread = self.thread = threading.Thread(target=self.server.serve_forever,
                                                         kwargs={"poll_interval": 0.01})
                thread.start()
                self.cookie, self.csrf = None, None
                value = {"priority": None, "start_date": None, "end_date": None,
                         "watch": None, "order": None, "layout": None,
                         "relations": {"belongs_to": None, "depends_on": [],
                                       "related_to": ["related-project"], "independent": None}}

                def management_command(ident, project="fixture-project"):
                    status, _, state = self.call(path="/api/management?project_id=" + project)
                    self.assertEqual(status, 200)
                    return {"request_id": ident, "expected_revision": state["data"]["revision"],
                            "expected_hash": state["data"]["config_hash"], "project_id": project,
                            "operation": "set", "value": value}

                def ordinary_command(project, ident):
                    return {"kind": "ordinary_project", "request_id": ident, "project_id": project,
                            "objective": "Read synthetic registered inputs", "scope": "Return text only",
                            "effect": "read_only", "input_version": "0" * 64,
                            "mode": "new", "parent_task_id": None}

                def boundaries(label, revoked):
                    before_config = config.read_bytes()
                    before_tasks = self.server.tasks.store.path.read_bytes()
                    self.cookie, self.csrf = None, None
                    status, _, denied = self.call(authenticated=False)
                    self.assertEqual((status, denied["error"]["code"]), (401, "SESSION_REQUIRED"))
                    status, headers, guest = self.call(path="/api/session", authenticated=False)
                    self.assertEqual(status, 200)
                    self.cookie = headers["Set-Cookie"].split(";", 1)[0]
                    self.csrf = guest["data"]["csrf_token"]
                    command = management_command(label + "-guest")
                    task = ordinary_command("fixture-project", label + "-task")
                    for path, body in (("/api/management/project", command), ("/api/tasks", task)):
                        status, _, denied = self.call("POST", path, body)
                        self.assertEqual((status, denied["error"]["code"]), (401, "OWNER_AUTH_REQUIRED"))
                    self.session()
                    for path, body in (("/api/management/project", command), ("/api/tasks", task)):
                        status, _, denied = self.call("POST", path, body, {"X-Hub-CSRF": "wrong"})
                        self.assertEqual((status, denied["error"]["code"]), (403, "CSRF_REJECTED"))
                    for project in ("fixture-project", "related-project", "manga-localizer"):
                        status, _, denied = self.call("POST", "/api/tasks",
                                                     ordinary_command(project, label + "-" + project))
                        expected = ((403, "TASK_PROJECT_REJECTED") if project == "manga-localizer"
                                    or (project == "related-project" and revoked)
                                    else (503, "ORDINARY_EXECUTION_DISABLED"))
                        self.assertEqual((status, denied["error"]["code"]), expected)
                    for project in (["manga-localizer", "related-project"] if revoked else ["manga-localizer"]):
                        command = management_command(label + "-denied-" + project, project)
                        command["value"] = {**value, "relations": None}
                        status, _, denied = self.call("POST", "/api/management/project", command)
                        self.assertEqual((status, denied["error"]["code"]), (400, "MANAGEMENT_INVALID_COMMAND"))
                    self.assertEqual(config.read_bytes(), before_config)
                    self.assertEqual(self.server.tasks.store.path.read_bytes(), before_tasks)
                    self.assertEqual(self.server.tasks.store.tasks(), [])
                    self.assertEqual(self.server.tasks.store.grants(), [])

                boundaries("before-relation", revoked=False)
                command = management_command("confirm-related-project")
                status, _, saved = self.call("POST", "/api/management/project", command)
                self.assertEqual(status, 200)
                self.assertEqual(saved["data"]["view"]["relation_state"], "owner_confirmed")
                self.assertEqual(ManagementStore(self.root).snapshot()["projects"]["fixture-project"], value)
                status, _, detail = self.call(path="/api/projects/fixture-project")
                self.assertEqual(status, 200)
                edge = detail["data"]["management"]["relation_view"]["outgoing"][0]
                self.assertEqual((edge["project_id"], edge["relation"], edge["eligible"]),
                                 ("related-project", "related_to", True))
                self.assertEqual(source_reads, [])
                self.assertFalse((self.root / "data/connections/connection_refresh.sqlite3").exists())

                self.source_fixture.projects[2]["connection_read_allowed"] = False
                self.source_fixture.save_registry()
                registry = self.root / "data/registry/external_projects.yaml"
                registry_bytes = registry.read_bytes()
                boundaries("after-revocation", revoked=True)
                status, _, detail = self.call(path="/api/projects/fixture-project")
                self.assertEqual(status, 200)
                management = detail["data"]["management"]
                self.assertEqual(management["relation_state"], "owner_confirmed")
                edge = management["relation_view"]["outgoing"][0]
                self.assertEqual((edge["project_id"], edge["eligible"]), ("related-project", False))
                self.assertIn("MANAGEMENT_ORPHAN_REFERENCE", management["warnings"])
                self.assertEqual(source_reads, [])
                self.assertFalse((self.root / "data/connections/connection_refresh.sqlite3").exists())
                status, _, refreshed = self.call("POST", "/api/refresh", {
                    "request_id": "related-denied-refresh", "project_ids": ["fixture-project", "related-project", "manga-localizer"],
                    "expected_head": {"sequence": 0, "hash": GENESIS_HASH}})
                self.assertEqual(status, 200)
                self.assertEqual(refreshed["data"]["request"]["status"], "FINISHED")
                self.assertEqual(refreshed["data"]["resolver_errors"], {})
                self.assertTrue(source_reads)
                status, _, active = self.call(path="/api/projects/fixture-project")
                self.assertEqual(status, 200)
                self.assertEqual(active["data"]["business"]["normalized_status"], "active")
                for project in ("related-project", "manga-localizer"):
                    status, _, denied = self.call(path="/api/projects/" + project)
                    self.assertEqual(status, 200)
                    self.assertEqual(denied["data"]["business"]["normalized_status"], "unknown")
                    self.assertEqual(denied["data"]["operational"]["latest_attempt"]["disposition"], "disabled")
                    self.assertIsNone(denied["data"]["operational"]["last_success"])
                self.assertEqual(registry.read_bytes(), registry_bytes)
                for path, original in protected_sources.items():
                    self.assertEqual(path.read_bytes(), original)
                self.assertEqual(self.server.tasks.store.tasks(), [])
                self.assertEqual(self.server.tasks.store.grants(), [])
                input_read.assert_not_called()
            finally:
                try:
                    if thread is not None and thread.is_alive():
                        self.server.shutdown()
                finally:
                    try:
                        self.server.server_close()
                    finally:
                        if thread is not None and thread.ident is not None:
                            thread.join(2)
                            self.assertFalse(thread.is_alive())
                        for method in forbidden:
                            method.assert_not_called()

    def test_default_assembly_browses_and_refreshes_without_model_execution(self):
        # Replace only the disposable server from setUp; retain its real fixtures.
        self.stop()
        spec = importlib.util.spec_from_file_location(
            "integration_server_entry", Path(__file__).resolve().parents[1] / "scripts/hub_server.py")
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        from hub.codex_adapter import AppServerAdapter, ReadonlyAppServerAdapter
        from hub.ordinary_runtime import OrdinaryRuntime
        from hub.task_service import TaskService
        from hub.task_store import TaskStore
        from hub.task_worker import TaskWorker

        class InertObserver:
            def start(self):
                pass

            def stop(self):
                pass

        with contextlib.ExitStack() as stack:
            forbidden = []
            for target, methods in (
                    (AppServerAdapter, ("__init__", "open")),
                    (ReadonlyAppServerAdapter, ("__init__", "open")),
                    (OrdinaryRuntime, ("__init__", "start")),
                    (TaskWorker, ("__init__", "run_one")),
                    (entry, ("ordinary_provider_status",))):
                for method in methods:
                    forbidden.append(stack.enter_context(mock.patch.object(
                        target, method, side_effect=AssertionError("implicit model/worker startup"))))

            # No ordinary grant/runtime is the supported unavailable-model setup.
            self.server, _, preview = entry.assemble_server(
                self.root, port=0, services={
                    "designs": self.designs, "host_observer": InertObserver(),
                    "owner_auth": OwnerAuth(provider=lambda: self.owner_proof)})
            thread = None
            try:
                self.projects = self.server.projects
                self.assertIs(type(self.projects), ProjectService)
                self.assertIs(type(self.server.tasks), TaskService)
                self.assertIs(type(self.server.tasks.store), TaskStore)
                self.assertEqual(self.server.tasks.store.root, self.root)
                self.assertIsNone(preview)
                thread = self.thread = threading.Thread(
                    target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
                thread.start()
                self.cookie, self.csrf = None, None

                status, headers, guest = self.call(path="/api/session", authenticated=False)
                self.assertEqual(status, 200)
                self.cookie = headers["Set-Cookie"].split(";", 1)[0]
                self.csrf = guest["data"]["csrf_token"]
                status, _, identity = self.call(path="/api/identity")
                self.assertEqual(status, 200)
                self.assertFalse(identity["data"]["owner_authenticated"])
                self.assertEqual(identity["data"]["capabilities"]["owner_session"]["status"], "guest")
                self.session()
                status, _, identity = self.call(path="/api/identity")
                self.assertEqual(status, 200)
                self.assertTrue(identity["data"]["owner_authenticated"])
                self.assertFalse(identity["data"]["execution_enabled"])
                for capability in ("ordinary_execution", "provider", "worker_runtime"):
                    self.assertFalse(identity["data"]["capabilities"][capability]["available"])
                self.assertEqual(identity["data"]["capabilities"]["ordinary_execution"]["reason_code"],
                                 "ORDINARY_EXECUTION_DISABLED")

                ledger = self.root / "data/connections/connection_refresh.sqlite3"
                status, _, before = self.call()
                self.assertEqual(status, 200)
                self.assertEqual(before["data"]["total"], 2)
                status, _, detail = self.call(path="/api/projects/fixture-project")
                self.assertEqual(status, 200)
                self.assertEqual(detail["data"]["business"]["normalized_status"], "unknown")
                self.assertFalse(ledger.exists())
                business = copy.deepcopy(self.source_fixture.business)
                business["current_work"]["next_action"] = "Review freshly collected synthetic source"
                (self.source_fixture.base / "fixture-project/STATE.yaml").write_text(
                    yaml.safe_dump(business, sort_keys=False))
                command = {"request_id": "fixture-no-model-refresh", "project_ids": ["fixture-project"],
                           "expected_head": {"sequence": 0, "hash": GENESIS_HASH}}
                status, _, result = self.call("POST", "/api/refresh", command)
                self.assertEqual(status, 200)
                self.assertEqual(result["data"]["request"]["status"], "FINISHED")
                self.assertEqual(result["data"]["appended_project_ids"], ["fixture-project"])
                self.assertEqual(result["data"]["resolver_errors"], {})
                ledger_digest = hashlib.sha256(ledger.read_bytes()).hexdigest()
                with mock.patch.object(SourceResolver, "refresh", side_effect=AssertionError(
                        "GET/retry must use the committed source result")) as refresh:
                    status, _, listing = self.call()
                    self.assertEqual(status, 200)
                    row = next(item for item in listing["data"]["projects"]
                               if item["project_id"] == "fixture-project")
                    status, _, detail = self.call(path="/api/projects/fixture-project")
                    self.assertEqual(status, 200)
                    self.assertEqual(project_facts(row), project_facts(detail["data"]))
                    self.assertEqual(row["business"]["normalized_status"], "active")
                    self.assertEqual(row["business"]["next_action"], business["current_work"]["next_action"])
                    self.assertTrue(row["business"]["source_record"]["success"])
                    self.assertEqual(row["business"]["source_record"]["business"], business)
                    status, _, retried = self.call("POST", "/api/refresh", command)
                    self.assertEqual(status, 200)
                    self.assertEqual(retried["data"]["appended_project_ids"], [])
                    refresh.assert_not_called()
                self.assertEqual(hashlib.sha256(ledger.read_bytes()).hexdigest(), ledger_digest)
                status, _, identity = self.call(path="/api/identity")
                self.assertEqual(status, 200)
                self.assertFalse(identity["data"]["execution_enabled"])
                self.assertFalse(identity["data"]["capabilities"]["ordinary_execution"]["available"])
                self.assertFalse(self.server.tasks.store.path.exists())
            finally:
                try:
                    if thread is not None and thread.is_alive():
                        self.server.shutdown()
                finally:
                    try:
                        self.server.server_close()
                    finally:
                        if thread is not None and thread.ident is not None:
                            thread.join(2)
                            self.assertFalse(thread.is_alive())
                        for method in forbidden:
                            method.assert_not_called()



    def test_pch_domain_actual_routes_scope_replay_and_same_head(self):
        self.source_fixture.add('personal-control-hub')
        self.session()
        command={'request_id':'http-pch-domain','project_ids':['personal-control-hub'],
                 'expected_head':{'sequence':0,'hash':'0'*64}}
        status,_,response=self.call('POST','/api/projects/personal-control-hub/domain/refresh',command)
        self.assertEqual(status,200,response)
        result=response['data'];self.assertEqual(result['catalog']['head'],result['request_result']['head'])
        self.assertEqual(result['catalog']['graph']['project_id'],'personal-control-hub')
        before=self.projects._read_ledger().history()
        status,_,replay=self.call('POST','/api/projects/personal-control-hub/domain/refresh',command)
        self.assertEqual(status,200,replay);self.assertEqual(before,self.projects._read_ledger().history())
        from urllib.parse import urlencode
        status,_,receipt=self.call(path='/api/projects/personal-control-hub/domain/request?'+urlencode({'command':json.dumps(command)}))
        self.assertEqual(status,200);self.assertEqual(receipt['data']['request_id'],command['request_id'])
        bad=copy.deepcopy(command);bad['project_ids']=['fixture-project']
        status,_,failure=self.call('POST','/api/projects/personal-control-hub/domain/refresh',bad)
        self.assertEqual(status,400);self.assertEqual(failure['error']['code'],'PCH_REFRESH_SCOPE_INVALID')
        self.assertEqual(before,self.projects._read_ledger().history())

    def test_pch_domain_unauthorized_and_csrf_have_zero_effects(self):
        self.source_fixture.add('personal-control-hub')
        command={'request_id':'http-pch-denied','project_ids':['personal-control-hub'],
                 'expected_head':{'sequence':0,'hash':'0'*64}}
        status,_,_=self.call('POST','/api/projects/personal-control-hub/domain/refresh',command,authenticated=False)
        self.assertEqual(status,401)
        self.assertIsNone(self.projects.ledger_head())
        self.session()
        status,_,_=self.call('POST','/api/projects/personal-control-hub/domain/refresh',command,headers={'X-Hub-CSRF':'wrong'})
        self.assertEqual(status,403);self.assertIsNone(self.projects.ledger_head())

if __name__ == "__main__":
    unittest.main()
