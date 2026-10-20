"""Isolated PCH API evidence: no real project roots, services or databases."""
import copy
import hashlib
import json
import subprocess
import sys
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import yaml

from connection_fixtures import Fixture
from hub.connection_records import FIELDS, validate_result
from hub.connection_extensions import validate_extensions
from hub.connection_refresh import RefreshLedger, refresh
from hub.connection_sources import SourceResolver
from hub.local_service import HubHTTPServer
from hub.metric_collect import MetricCollector
from hub.project_service import ProjectService, project_snapshot
from hub.service_contract import ServiceError
import test_hub_local_service as transport_fixtures


class UnavailableDesign:
    def snapshot(self):
        raise ServiceError("DESIGN_STORE_UNAVAILABLE", status=503)


class ProjectAPITests(unittest.TestCase):
    call = transport_fixtures.LocalHTTPTests.call
    stop = transport_fixtures.LocalHTTPTests.stop

    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)
        self.root, self.declaration = self.fixture.add("a")
        self.service = ProjectService(self.fixture.hub)
        self.time = datetime.now(timezone.utc).isoformat()
        self.resolver = SourceResolver(self.fixture.hub, clock=lambda: self.time)
        self.cookie = None

    def commit(self, request="good"):
        self.ledger = RefreshLedger(self.fixture.hub, result_validator=validate_result)
        result = refresh(self.ledger, self.resolver, request, ["a"], expected_head=self.ledger.head)
        self.assertEqual(result["resolver_errors"], {})
        return self.ledger.head

    def start_http(self):
        self.server = HubHTTPServer(self.service, UnavailableDesign())
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01})
        self.thread.start()
        self.addCleanup(self.stop)
        status, headers, _ = self.call(path="/api/session", authenticated=False)
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]

    def test_missing_db_gets_do_not_create_database_or_collect(self):
        self.start_http()
        path = self.fixture.hub / "data/connections/connection_refresh.sqlite3"
        with mock.patch.object(SourceResolver, "refresh", side_effect=AssertionError("GET source refresh")), \
             mock.patch.object(MetricCollector, "collect", side_effect=AssertionError("GET collect")):
            for endpoint in ("/api/identity", "/api/projects", "/api/projects/a"):
                status, _, body = self.call(path=endpoint)
                self.assertEqual(status, 200, body)
            self.assertIsNone(self.service.ledger_head())
        self.assertFalse(path.exists())

    def test_http_identity_projection_cli_and_database_head_agree_offline(self):
        expected = self.commit()
        db = self.ledger.path
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        self.start_http()
        with mock.patch.object(SourceResolver, "refresh", side_effect=AssertionError("GET source refresh")), \
             mock.patch.object(MetricCollector, "collect", side_effect=AssertionError("GET collect")):
            status, _, identity = self.call(path="/api/identity")
            self.assertEqual(status, 200, identity)
            self.assertEqual(identity["data"]["ledger_head"], expected)
            status, _, listing = self.call()
            self.assertEqual(status, 200, listing)
            self.assertEqual(listing["data"]["head"], expected)
            status, _, detail = self.call(path="/api/projects/a")
            self.assertEqual(status, 200, detail)
            self.assertEqual(detail["data"]["provenance"]["ledger_head"], expected)
            self.assertFalse(detail["data"]["design"]["available"])
            self.assertEqual(detail["data"]["design"]["reason"], "DESIGN_STORE_UNAVAILABLE")
        cli = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/hub_connections.py"),
                              "--root", str(self.fixture.hub), "current"], capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(json.loads(cli.stdout)["ledger_head"], expected)
        snapshot = project_snapshot(self.resolver.registry, self.resolver.authority,
                                    RefreshLedger(self.fixture.hub, read_only=True, result_validator=validate_result))
        self.assertEqual(snapshot["ledger_head"], expected)
        self.assertEqual(self.service.ledger_head(), expected)
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)

    def test_quality_false_zero_empty_are_known_and_core_is_explicit(self):
        business = copy.deepcopy(self.fixture.business)
        business["current_work"]["completed"] = False
        business["progress"]["completed"] = 0
        business["blockers"] = []
        business["verification"]["evidence_refs"] = []
        (self.root / "STATE.yaml").write_text(yaml.safe_dump(business))
        self.commit()
        dto = self.service.get_project("a")
        self.assertEqual(set(dto), {"schema_version", "project_id", "name", "declared", "business",
                                    "operational", "freshness", "source", "errors", "relations", "design", "provenance", "management", "definition", "last_success_snapshot", "field_explanations", "semantics"})
        self.assertEqual(dto["field_explanations"], {"schema_version": "1.0", "project_id": "a", "fields": {}})
        semantics = dto["semantics"]
        self.assertEqual("1.0", semantics["schema_version"])
        self.assertEqual({"purpose", "outputs", "current_work", "next_action", "actor"},
                         set(semantics["explanations"]))
        self.assertIs(semantics["progress"]["current_work_completed"]["value"], False)
        self.assertEqual(0, semantics["progress"]["completed"]["value"])
        self.assertEqual("known", semantics["blockers"]["state"])
        self.assertEqual([], semantics["blockers"]["value"])
        self.assertFalse(semantics["progress"]["project_completion_inferred"])
        success = dto["last_success_snapshot"]
        self.assertEqual(set(success), {"available", "state", "observed_at", "age_seconds",
                                        "source_mtime_age_seconds", "fields", "reason", "recovery_action"})
        self.assertTrue(success["available"])
        self.assertEqual(success["state"], "current")
        self.assertEqual(success["observed_at"], self.time)
        self.assertGreaterEqual(success["age_seconds"], 0)
        self.assertEqual(success["fields"], dto["business"]["fields"])
        self.assertFalse(dto["management"]["available"])
        self.assertIsNone(dto["management"]["overlay"])
        quality = dto["business"]["quality"]
        self.assertEqual(quality["schema_version"], "1.0")
        for key in ("structure_coverage", "value_coverage", "evidence_coverage", "current_coverage"):
            self.assertEqual(quality[key]["total"], len(FIELDS))
            self.assertEqual(quality[key]["count"], len(FIELDS))
            self.assertEqual(quality[key]["fields"], list(FIELDS))
            self.assertEqual(quality[key]["missing"], [])
        self.assertTrue(quality["core_complete"])
        self.assertEqual(quality["core_missing"], [])
        freshness = dto["freshness"]
        self.assertEqual(freshness["observed_at"], self.time)
        self.assertIsNotNone(freshness["source_mtime_age_seconds"])
        self.assertIsNone(freshness["fact_updated_at"])
        self.assertEqual(freshness["fact_time_state"], "unknown")
        self.assertIsNotNone(freshness["fact_time_unknown_reason"])

    def test_fact_time_uses_only_fresh_validated_fact_and_not_source_mtime(self):
        self.commit()
        dto = self.service.get_project("a")
        record = dto["operational"]["latest_attempt"]
        source = record["sources"][0]
        fact_time = "2026-01-01T00:00:00+00:00"
        proof = {"source_ref": source["id"], "sha256": source["sha256"],
                 "observed_at": self.time, "selector": {"path": ["fact_updated_at"]}}
        record["extensions"] = validate_extensions({"schema_version": "1.0",
            "fact": {"fact_updated_at": {"value": fact_time, "proof": proof, "unknown_reason": None}},
            "managed": {}, "derived": {}, "proposed": {}}, source_context={source["id"]: {
                "sha256": source["sha256"], "observed_at": self.time, "format": "yaml",
                "data": {"fact_updated_at": fact_time}}})
        self.assertEqual(self.service._fact_time(record, {"state": "fresh"})["fact_updated_at"], fact_time)
        self.assertNotEqual(fact_time, source["modified_at"])
        for state in ("stale", "unknown", "removed_local"):
            self.assertEqual(self.service._fact_time(record, {"state": state})["fact_time_state"], "unknown")
        record["success"] = False
        self.assertIsNone(self.service._fact_time(record, {"state": "fresh"})["fact_updated_at"])

    def test_initialized_genesis_head_is_visible_and_corruption_is_explicit(self):
        self.ledger = RefreshLedger(self.fixture.hub, result_validator=validate_result)
        self.start_http()
        status, _, body = self.call(path="/api/identity")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data"]["ledger_head"], self.ledger.head)
        self.ledger.path.write_bytes(b"disposable corrupt synthetic SQLite")
        status, _, body = self.call(path="/api/identity")
        self.assertEqual(status, 503, body)
        self.assertIn(body["error"]["code"], {"LEDGER_CORRUPT", "LEDGER_UNAVAILABLE"})

    def test_success_is_never_current_after_failure_expiry_or_authority_drift(self):
        self.commit()
        success = self.service.get_project("a")["operational"]["last_success"]
        (self.root / "STATE.yaml").unlink()
        self.commit("missing")
        dto = self.service.get_project("a")
        self.assertEqual(dto["operational"]["last_success"], success)
        self.assertEqual(dto["business"]["quality"]["current_coverage"]["count"], 0)
        self.assertFalse(dto["business"]["quality"]["core_complete"])
        self.assertEqual(dto["business"]["quality"]["integration_quality"], "error")
        (self.root / "STATE.yaml").write_text(yaml.safe_dump(self.fixture.business))
        self.commit("recovered")
        expired = project_snapshot(self.resolver.registry, self.resolver.authority, self.ledger,
                                   observed_at="2099-01-01T00:00:00+00:00")
        row = expired["projects"][0]
        self.assertEqual(self.service._quality(row["latest_attempt"], row["freshness"], True)
                         ["current_coverage"]["count"], 0)
        self.fixture.projects[0]["name"] = "renamed"
        self.fixture.save_registry()
        dto = self.service.get_project("a")
        self.assertTrue(dto["freshness"]["authority_drift"])
        self.assertEqual(dto["business"]["quality"]["current_coverage"]["count"], 0)

    def test_unknown_core_is_partial_despite_valid_normalized_structure(self):
        business = copy.deepcopy(self.fixture.business)
        business["current_work"]["status"] = None
        (self.root / "STATE.yaml").write_text(yaml.safe_dump(business))
        self.commit()
        quality = self.service.get_project("a")["business"]["quality"]
        self.assertEqual(quality["structure_coverage"]["count"], 21)
        self.assertIn("current_work.status", quality["core_missing"])
        self.assertFalse(quality["core_complete"])
        self.assertEqual(quality["integration_quality"], "partial")

    def test_pagination_over_100_has_unique_pages_true_filtered_total_and_stable_head(self):
        self.commit()
        # Only a synthetic registry is expanded; no sibling source directories.
        template = copy.deepcopy(self.fixture.projects[0])
        for number in range(1, 206):
            project = dict(copy.deepcopy(template), id=f"p{number:03d}", name=f"Group {number % 2}")
            self.fixture.projects.append(project)
        self.fixture.save_registry()
        expected = self.service.ledger_head()
        before = self.ledger.path.read_bytes()
        self.start_http()
        pages = []
        with mock.patch.object(SourceResolver, "refresh", side_effect=AssertionError("GET source refresh")), \
             mock.patch.object(MetricCollector, "collect", side_effect=AssertionError("GET collect")):
            for offset in (0, 100, 200):
                status, _, body = self.call(path=f"/api/projects?limit=100&offset={offset}")
                self.assertEqual(status, 200, body)
                page = body["data"]
                self.assertEqual(page["total"], 206)
                self.assertEqual(page["head"], expected)
                pages.extend(row["project_id"] for row in page["projects"])
            status, _, body = self.call(path="/api/projects?q=Group%201&limit=100")
            self.assertEqual(status, 200, body)
            self.assertEqual(body["data"]["total"], 103)
            self.assertEqual(len(body["data"]["projects"]), 100)
            self.assertEqual(self.call(path="/api/projects?limit=101")[0], 400)
        self.assertEqual(len(pages), len(set(pages)), 206)
        self.assertEqual(pages, sorted(pages))
        self.assertEqual(self.ledger.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
