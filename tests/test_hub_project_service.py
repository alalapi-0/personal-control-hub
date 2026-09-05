from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_hub_sources as source_fixtures
from hub.connection_records import content_hash
from hub.connection_refresh import GENESIS_HASH, LedgerBusyError, PrecommitFaultError, RefreshLedger
from hub.connection_relations import relation_hash
from hub.project_service import ProjectService
from hub.service_contract import ServiceError


class ProjectServiceTests(unittest.TestCase):
    """Reusable temporary Hub fixture for service and lifecycle integration tests."""

    def setUp(self) -> None:
        self.fixture = source_fixtures.SourceResolverTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        self.data = self.root / "data/design_governance"
        self.bundle = {
            "schema_version": "1.0", "kind": "connection_authority_bundle",
            "manifest": self.fixture.manifest, "adapters": self.fixture.adapters,
            "source_plan": self.fixture.plan,
        }
        self.bundle["content_hash"] = content_hash(self.bundle)
        (self.data / "authority-bundle-v1.json").write_text(
            json.dumps(self.bundle), encoding="utf-8")
        relations = {
            "schema_version": "1.0", "kind": "connection_relation_proposals",
            "id": "fixture-relations", "revision": 1,
            "created_at": "2026-09-05T12:00:00+08:00",
            "registry_ref": {"path": "data/registry/external_projects.yaml",
                             "sha256": self.fixture.manifest["registry_ref"]["sha256"]},
            "inventory_ref": {
                "path": "docs/reports/ui_design_governance/unit-02/ui-source-inventory.json",
                "sha256": "5e7132ce05a5dc2a569826444ea15f67e9893c725c9ec24c884872bd93917f7c",
                "accepted_candidate_hash": "f0d2f820f5b3bed541cee64b617f4e23fe6b0342d1b2db1541368df31c573512",
            },
            "relations": [], "content_hash": "",
        }
        relations["content_hash"] = relation_hash(relations)
        (self.data / "relation-proposals-v1.json").write_text(
            json.dumps(relations), encoding="utf-8")
        self.service = ProjectService(self.root)
        self.design_snapshot = {
            "available": True, "store_revision": 3, "store_classification": "mock",
            "facts": [{"kind": "candidate", "id": "fixture-candidate", "revision": 1,
                       "scope": {"family_id": None, "members": [
                           {"project_id": "declared-00", "pages": ["review"]}]}}],
            "history": [], "effective": {}, "queues": {}, "reason": None,
        }

    def test_initial_list_represents_all_24_without_refresh_or_store_creation(self) -> None:
        ledger_path = self.data / "connection_refresh.sqlite3"
        with mock.patch("hub.project_service.SourceResolver.refresh",
                        side_effect=AssertionError("query must not read project sources")):
            result = self.service.list_projects(design_snapshot=None)
        self.assertEqual(24, result["total"])
        self.assertEqual(sorted(p["project_id"] for p in result["projects"]),
                         [p["project_id"] for p in result["projects"]])
        self.assertFalse(ledger_path.exists())
        manga = next(p for p in result["projects"] if p["project_id"] == "manga-localizer")
        self.assertEqual("unknown", manga["business"]["normalized_status"])
        self.assertFalse(manga["design"]["available"])

    def test_refresh_is_explicit_cas_partial_success_and_idempotent(self) -> None:
        command = {"request_id": "service-refresh", "project_ids": [
            "declared-00", "manga-localizer"],
            "expected_head": {"sequence": 0, "hash": GENESIS_HASH}}
        first = self.service.refresh(command)
        self.assertEqual("FINISHED", first["request"]["status"])
        self.assertEqual(["declared-00", "manga-localizer"],
                         first["appended_project_ids"])
        before = RefreshLedger(self.root, read_only=True,
                               result_validator=self.fixture.resolver.validate_result).head
        with mock.patch("hub.project_service.SourceResolver.refresh",
                        side_effect=AssertionError("durable retry must not reread")):
            retry = self.service.refresh(command)
        self.assertEqual([], retry["appended_project_ids"])
        self.assertEqual(before, retry["projection"]["head"])
        row = self.service.get_project("declared-00", self.design_snapshot)
        self.assertEqual("active", row["business"]["normalized_status"])
        self.assertEqual("fresh", row["freshness"]["state"])
        self.assertEqual("fixture-candidate", row["design"]["references"][0]["id"])

    def test_query_filter_order_pagination_and_list_detail_parity(self) -> None:
        self.service.refresh({"request_id": "query", "project_ids": ["declared-00", "declared-01"],
                              "expected_head": {"sequence": 0, "hash": GENESIS_HASH}})
        result = self.service.list_projects({"q": "DECLARED", "status": "active",
                                             "freshness": "fresh", "order": "-name",
                                             "offset": "0", "limit": "2"},
                                            self.design_snapshot)
        self.assertEqual(2, len(result["projects"]))
        self.assertGreater(result["projects"][0]["name"], result["projects"][1]["name"])
        detail = self.service.get_project(result["projects"][0]["project_id"], self.design_snapshot)
        self.assertEqual(result["projects"][0], detail)

    def test_invalid_shapes_and_conflicts_have_fixed_sanitized_codes(self) -> None:
        cases = [
            lambda: self.service.list_projects({"path": "/private/value"}),
            lambda: self.service.list_projects({"limit": "01"}),
            lambda: self.service.get_project("missing"),
            lambda: self.service.refresh({"request_id": "x", "project_ids": ["declared-00"],
                                          "expected_head": {"sequence": 0}}),
        ]
        expected = ["QUERY_INVALID", "QUERY_INVALID", "PROJECT_NOT_FOUND",
                    "REFRESH_COMMAND_INVALID"]
        for call, code in zip(cases, expected):
            with self.subTest(code=code), self.assertRaises(ServiceError) as raised:
                call()
            self.assertEqual(code, raised.exception.code)
            self.assertNotIn(str(self.root), json.dumps(raised.exception.as_dict()))

        self.service.refresh({"request_id": "head-a", "project_ids": ["declared-00"],
                              "expected_head": {"sequence": 0, "hash": GENESIS_HASH}})
        with self.assertRaises(ServiceError) as raised:
            self.service.refresh({"request_id": "head-b", "project_ids": ["declared-01"],
                                  "expected_head": {"sequence": 0, "hash": GENESIS_HASH}})
        self.assertEqual("REFRESH_HEAD_CONFLICT", raised.exception.code)

    def test_operational_only_result_does_not_promote_business_state(self) -> None:
        self.service.refresh({"request_id": "operational", "project_ids": ["light-novel"],
                              "expected_head": {"sequence": 0, "hash": GENESIS_HASH}})
        row = self.service.get_project("light-novel")
        self.assertEqual("unknown", row["business"]["normalized_status"])
        self.assertTrue(row["operational"]["facts"])

    def test_design_unavailable_keeps_project_facts(self) -> None:
        unavailable = copy.deepcopy(self.design_snapshot)
        unavailable.update(available=False, store_revision=None, facts=[],
                           reason="DESIGN_STORE_CORRUPT")
        row = self.service.get_project("declared-00", unavailable)
        self.assertEqual("unknown", row["business"]["normalized_status"])
        self.assertEqual("DESIGN_STORE_CORRUPT", row["design"]["reason"])

    def test_current_authority_drift_marks_retained_business_stale(self) -> None:
        first = self.service.refresh({"request_id": "before-drift",
                                      "project_ids": ["declared-00"],
                                      "expected_head": {"sequence": 0, "hash": GENESIS_HASH}})
        self.assertEqual("fresh", first["projection"]["projects"]["declared-00"]["freshness"])
        registry = copy.deepcopy(self.fixture.registry)
        next(row for row in registry["projects"] if row["id"] == "declared-00")["name"] = "drifted-name"
        (self.root / "data/registry/external_projects.yaml").write_text(
            yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
        with mock.patch("hub.project_service.SourceResolver.refresh",
                        side_effect=AssertionError("GET must remain offline")):
            row = self.service.get_project("declared-00")
        self.assertEqual("stale", row["freshness"]["state"])
        self.assertTrue(row["freshness"]["authority_drift"])
        self.assertEqual("active", row["business"]["normalized_status"])
        self.assertEqual("RELATION_STORE_UNAVAILABLE", row["relations"]["reason"])

    def test_ledger_head_join_retries_and_fails_after_bound(self) -> None:
        bundles, validators = self.service._authorities()
        authority = validators[bundles[-1]["source_plan"]["content_hash"]].authority
        empty = {"schema_version": "1.0", "requests": [], "events": [], "results": []}
        fake = SimpleNamespace(
            history=mock.Mock(side_effect=[dict(empty, head={"sequence": 1, "hash": "1" * 64}),
                                           dict(empty, head={"sequence": 2, "hash": "2" * 64})]),
            rebuild=mock.Mock(side_effect=[{"schema_version": "1.0", "head": {"sequence": 0, "hash": GENESIS_HASH},
                                            "authority_drift": False, "projects": {}},
                                           {"schema_version": "1.0", "head": {"sequence": 2, "hash": "2" * 64},
                                            "authority_drift": False, "projects": {}}]))
        with mock.patch("hub.project_service.RefreshLedger", return_value=fake):
            history, projection = self.service._ledger_snapshot(validators, authority, "matched")
        self.assertEqual(history["head"], projection["head"])
        mismatch = SimpleNamespace(
            history=mock.Mock(return_value=dict(empty, head={"sequence": 1, "hash": "1" * 64})),
            rebuild=mock.Mock(return_value={"schema_version": "1.0",
                                            "head": {"sequence": 2, "hash": "2" * 64},
                                            "authority_drift": False, "projects": {}}))
        with mock.patch("hub.project_service.RefreshLedger", return_value=mismatch), \
                self.assertRaises(ServiceError) as raised:
            self.service._ledger_snapshot(validators, authority, "matched")
        self.assertEqual("LEDGER_SNAPSHOT_CONFLICT", raised.exception.code)
        self.assertEqual(3, mismatch.history.call_count)

    def test_filter_with_no_rows_retains_ledger_head(self) -> None:
        refreshed = self.service.refresh({"request_id": "head-visible",
                                          "project_ids": ["declared-00"],
                                          "expected_head": {"sequence": 0, "hash": GENESIS_HASH}})
        result = self.service.list_projects({"q": "does-not-exist"})
        self.assertEqual([], result["projects"])
        self.assertEqual(refreshed["projection"]["head"], result["head"])

    def test_precommit_failure_reports_durable_partial_receipt(self) -> None:
        def commit_one_then_fail(ledger, resolver, request_id, project_ids, *, expected_head):
            ledger.begin_request(request_id, project_ids, resolver.authority,
                                 expected_head=expected_head)
            result = resolver.refresh(project_ids[0])
            ledger.append_project_result(request_id, project_ids[0], result,
                                         validator=resolver.validate_result)
            raise PrecommitFaultError("fixture")

        command = {"request_id": "partial", "project_ids": ["declared-00", "declared-01"],
                   "expected_head": {"sequence": 0, "hash": GENESIS_HASH}}
        with mock.patch("hub.project_service.refresh_projects", side_effect=commit_one_then_fail), \
                self.assertRaises(ServiceError) as raised:
            self.service.refresh(command)
        error = raised.exception
        self.assertEqual("REFRESH_PRECOMMIT_FAILED", error.code)
        self.assertEqual("PARTIALLY_COMMITTED", error.outcome)
        self.assertEqual(["declared-00"], error.details["completed_project_ids"])
        self.assertEqual(["declared-01"], error.details["remaining_project_ids"])

    def test_postbegin_busy_reports_durable_partial_receipt(self) -> None:
        def commit_one_then_busy(ledger, resolver, request_id, project_ids, *, expected_head):
            ledger.begin_request(request_id, project_ids, resolver.authority,
                                 expected_head=expected_head)
            ledger.append_project_result(request_id, project_ids[0], resolver.refresh(project_ids[0]),
                                         validator=resolver.validate_result)
            raise LedgerBusyError("fixture")

        command = {"request_id": "partial-busy",
                   "project_ids": ["declared-00", "declared-01"],
                   "expected_head": {"sequence": 0, "hash": GENESIS_HASH}}
        with mock.patch("hub.project_service.refresh_projects", side_effect=commit_one_then_busy), \
                self.assertRaises(ServiceError) as raised:
            self.service.refresh(command)
        self.assertEqual("REFRESH_CONCURRENCY_BUSY", raised.exception.code)
        self.assertEqual("PARTIALLY_COMMITTED", raised.exception.outcome)
        self.assertEqual(["declared-00"], raised.exception.details["completed_project_ids"])

    def test_midrequest_authority_drift_marks_refresh_projection_stale(self) -> None:
        original = self.fixture.resolver.__class__.refresh

        def drift_after_first(resolver, project_id):
            result = original(resolver, project_id)
            if project_id == "declared-00":
                registry = copy.deepcopy(self.fixture.registry)
                next(row for row in registry["projects"] if row["id"] == "declared-01")["name"] = "drift"
                (self.root / "data/registry/external_projects.yaml").write_text(
                    yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
            return result

        command = {"request_id": "midrequest-drift",
                   "project_ids": ["declared-00", "declared-01"],
                   "expected_head": {"sequence": 0, "hash": GENESIS_HASH}}
        with mock.patch("hub.project_service.SourceResolver.refresh", drift_after_first):
            result = self.service.refresh(command)
        self.assertEqual("drifted", result["current_authority"]["state"])
        self.assertTrue(result["projection"]["authority_drift"])
        self.assertEqual("stale", result["projection"]["projects"]["declared-00"]["freshness"])


if __name__ == "__main__":
    unittest.main()
