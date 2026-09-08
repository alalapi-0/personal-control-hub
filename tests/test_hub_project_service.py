import copy
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

from connection_fixtures import Fixture, TIME
from hub.connection_cli import write_previews
from hub.connection_records import FIELDS, RecordError, empty_business, validate_result
from hub.connection_refresh import (GENESIS_HASH, LedgerBusyError, PrecommitFaultError,
                                    RefreshLedger, refresh)
import hub.project_service as project_module
from hub.project_service import ProjectService, project_snapshot, row_update_key, validate_snapshot
from hub.service_contract import ServiceError
from hub.services.integration_service import feishu_preview, markdown_preview


class SharedProjectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.a, _ = self.fixture.add("a")
        self.b, _ = self.fixture.add("b")
        self.resolver = self.fixture.resolver()
        self.ledger = RefreshLedger(self.fixture.hub, result_validator=validate_result)

    def tearDown(self):
        self.fixture.close()

    def view(self, observed_at=TIME):
        return project_snapshot(self.resolver.registry, self.resolver.authority, self.ledger, observed_at=observed_at)

    def run_refresh(self, request_id):
        result = refresh(self.ledger, self.resolver, request_id, ["a", "b"])
        self.assertEqual({}, result["resolver_errors"])
        self.assertEqual("FINISHED", result["request"]["status"])

    def test_source_change_only_changes_its_own_project_and_repeated_read_is_stable(self):
        self.run_refresh("first")
        first = self.view()
        self.run_refresh("same-sources")
        second = self.view()
        self.assertEqual([x["update_key"] for x in first["projects"]], [x["update_key"] for x in second["projects"]])
        state = copy.deepcopy(self.fixture.business)
        state["current_work"]["next_action"] = "Review candidate C"
        (self.a / "STATE.yaml").write_text(yaml.safe_dump(state))
        self.run_refresh("a-changed")
        changed = self.view()
        self.assertNotEqual(first["projects"][0]["update_key"], changed["projects"][0]["update_key"])
        self.assertEqual(first["projects"][1]["update_key"], changed["projects"][1]["update_key"])
        self.assertEqual(2, changed["coverage"]["registry_count"])

    def test_new_failure_retains_last_success_explicitly_stale_after_restart(self):
        self.run_refresh("good")
        original = self.view()["projects"][0]["last_success"]
        (self.a / "STATE.yaml").unlink()
        self.run_refresh("missing")
        self.ledger = RefreshLedger(self.fixture.hub, result_validator=validate_result, read_only=True)
        row = self.view()["projects"][0]
        self.assertEqual("missing_source", row["latest_attempt"]["disposition"])
        self.assertEqual("stale", row["freshness"]["state"])
        self.assertEqual(original, row["last_success"])
        text = markdown_preview(self.view())
        self.assertIn("历史快照", text)
        self.assertIn("missing_source", text)

    def test_read_ttl_and_registry_drift_cannot_promote_to_current(self):
        self.run_refresh("good")
        expired = self.view("2026-09-09T10:00:00+00:00")
        self.assertEqual(0, expired["coverage"]["resolved_count"])
        self.assertTrue(all(row["freshness"]["state"] == "stale" for row in expired["projects"]))
        self.fixture.projects[0]["name"] = "renamed"
        self.fixture.save_registry()
        self.resolver = self.fixture.resolver()
        self.assertTrue(all(row["freshness"]["authority_drift"] for row in self.view()["projects"]))

    def test_removed_after_success_hides_business_history_without_root_reads(self):
        self.run_refresh("good")
        self.fixture.projects[0]["local_presence"] = {"status": "removed_local"}
        self.fixture.save_registry()
        self.resolver = self.fixture.resolver()
        with mock.patch.object(Path, "resolve", side_effect=AssertionError("project root read")):
            row = self.view()["projects"][0]
        self.assertEqual("removed_local", row["freshness"]["state"])
        self.assertIsNone(row["last_success"])
        self.assertIsNone(row["latest_attempt"])

    def test_json_markdown_and_feishu_share_records_and_have_no_external_effects(self):
        self.run_refresh("good")
        snapshot = self.view()
        with mock.patch("socket.socket", side_effect=AssertionError("network")), \
             mock.patch("subprocess.Popen", side_effect=AssertionError("process")), \
             mock.patch("builtins.open", side_effect=AssertionError("file read/write")), \
             mock.patch("os.getenv", side_effect=AssertionError("credential lookup")), \
             mock.patch.object(type(os.environ), "__getitem__", side_effect=AssertionError("environment lookup")):
            card = feishu_preview(snapshot)
            markdown = markdown_preview(snapshot)
        self.assertFalse(card["config"]["enabled"])
        self.assertFalse(card["config"]["write_back_allowed"])
        self.assertEqual(snapshot["projects"], [r["source_record"] for r in card["updates"]])
        for row in snapshot["projects"]:
            self.assertIn("## " + row["project_id"] + "\n", markdown)
            self.assertIn(row["update_key"], markdown)
        self.assertEqual(2, len(card["updates"]))

    def test_all_normalized_fields_roundtrip_across_every_preview_disposition(self):
        def assert_parity(snapshot):
            markdown = markdown_preview(snapshot)
            rows = [json.loads(block) for block in re.findall(r"^```json\n(.*?)\n```$", markdown, re.MULTILINE | re.DOTALL)]
            self.assertEqual(snapshot["projects"], rows)
            self.assertEqual(rows, [entry["source_record"] for entry in feishu_preview(snapshot)["updates"]])
            return markdown

        # First failure retains declaration and source identity even before a success.
        state = copy.deepcopy(self.fixture.business)
        state["current_work"]["accepted"] = "not-a-boolean"
        (self.a / "STATE.yaml").write_text(yaml.safe_dump(state))
        self.run_refresh("first-failure")
        snapshot = self.view()
        first = snapshot["projects"][0]
        self.assertFalse(first["latest_attempt"]["success"])
        self.assertIsNone(first["last_success"])
        markdown = assert_parity(snapshot)
        for fingerprint in (first["latest_attempt"]["declaration"]["sha256"], first["latest_attempt"]["sources"][0]["sha256"]):
            self.assertIn(fingerprint, markdown)

        # Current success includes independent completed/accepted/delivery facts.
        (self.a / "STATE.yaml").write_text(yaml.safe_dump(self.fixture.business))
        self.run_refresh("current-success")
        success = self.view()
        assert_parity(success)
        prior_hash = success["projects"][0]["latest_attempt"]["sources"][0]["sha256"]

        # A newer corrupt source must expose its own fingerprint, not just the old one.
        (self.a / "STATE.yaml").write_text("status: [corrupt")
        self.run_refresh("newer-failure")
        failure = self.view()
        failed = failure["projects"][0]
        new_hash = failed["latest_attempt"]["sources"][0]["sha256"]
        self.assertNotEqual(prior_hash, new_hash)
        self.assertEqual(prior_hash, failed["last_success"]["sources"][0]["sha256"])
        markdown = assert_parity(failure)
        self.assertIn(new_hash, markdown)
        self.assertIn("不能替代上面的最新失败证据", markdown)

        # Stale and removed rows preserve their full, different dispositions.
        assert_parity(self.view("2026-09-09T10:00:00+00:00"))
        self.fixture.projects[0]["local_presence"] = {"status": "removed_local"}
        self.fixture.save_registry()
        self.resolver = self.fixture.resolver()
        self.run_refresh("removed")
        removed = self.view()
        self.assertEqual("removed_local", removed["projects"][0]["latest_attempt"]["disposition"])
        self.assertIsNone(removed["projects"][0]["last_success"])
        assert_parity(removed)

    def test_preview_paths_cannot_escape_to_external_project_or_follow_alias(self):
        self.run_refresh("good")
        snapshot = self.view()
        with self.assertRaises(RecordError):
            write_previews(self.fixture.hub, str(self.a), snapshot)
        alias = self.fixture.hub / "data/connections/alias"
        alias.symlink_to(self.a, target_is_directory=True)
        with self.assertRaises(RecordError):
            write_previews(self.fixture.hub, "data/connections/alias", snapshot)
        self.assertFalse((self.a / "projects.json").exists())

    def test_forged_freshness_and_omitted_project_fail_validation(self):
        self.run_refresh("good")
        snapshot = self.view("2026-09-09T10:00:00+00:00")
        snapshot["projects"][0]["freshness"]["state"] = "fresh"
        snapshot["projects"][0]["update_key"] = row_update_key(snapshot["projects"][0])
        with self.assertRaises(RecordError):
            validate_snapshot(snapshot)

    def test_invalid_projection_shell_never_passes_without_source_records(self):
        snapshot = self.view()
        mutations = [lambda v: v["projects"][0].update(project_id="../bad"),
                     lambda v: v["projects"][0].update(name=""),
                     lambda v: v.update(authority={"unexpected": "field"}),
                     lambda v: v["authority"].update(adapter_version="1.0"),
                     lambda v: v["authority"].update(schema_hash="bad"),
                     lambda v: v.update(ledger_head="invalid"),
                     lambda v: v["ledger_head"].update(sequence=True),
                     lambda v: v["ledger_head"].update(hash="bad"),
                     lambda v: v["ledger_head"].update(unexpected=True),
                     lambda v: v["coverage"].update(resolved_count=False),
                     lambda v: v["projects"][0]["freshness"].update(authority_drift=0),
                     lambda v: v["projects"][0]["freshness"].update(read_age_seconds=False),
                     lambda v: v["projects"][0]["freshness"].update(reason=None),
                     lambda v: v["projects"][0].update(local_presence=[]),
                     lambda v: v.update(unexpected=True)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                bad = copy.deepcopy(snapshot)
                mutate(bad)
                # Do not let an outdated update hash conceal the structural defect.
                for row in bad["projects"]:
                    row["update_key"] = row_update_key(row)
                with self.assertRaises(RecordError):
                    validate_snapshot(bad)

    def test_stored_results_need_ledger_head_and_latest_success_consistency(self):
        self.run_refresh("good")
        snapshot = self.view()
        snapshot["ledger_head"] = None
        with self.assertRaises(RecordError):
            validate_snapshot(snapshot)
        for sequence in (1, 2):
            snapshot = self.view()
            snapshot["ledger_head"] = {"sequence": sequence, "hash": "a" * 64}
            with self.assertRaises(RecordError):
                validate_snapshot(snapshot)
        snapshot = self.view()
        snapshot["projects"][0]["last_success"] = None
        snapshot["projects"][0]["update_key"] = row_update_key(snapshot["projects"][0])
        with self.assertRaises(RecordError):
            validate_snapshot(snapshot)
        snapshot = self.view()
        snapshot["projects"].pop()
        with self.assertRaises(RecordError):
            validate_snapshot(snapshot)

    def test_cli_roundtrip_refresh_resume_and_read_only_validation(self):
        script = Path(__file__).resolve().parents[1] / "scripts/hub_refresh.py"
        def call(*args):
            completed = subprocess.run([sys.executable, str(script), "--root", str(self.fixture.hub), *args],
                                       text=True, capture_output=True)
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            return json.loads(completed.stdout)
        first = call("refresh", "--request-id", "cli")
        self.assertEqual(2, first["coverage"]["registry_count"])
        before = self.ledger.head
        call("refresh", "--request-id", "cli")
        self.assertEqual(before, self.ledger.head)
        self.assertTrue(call("validate")["valid"])
        self.assertEqual(before, self.ledger.head)
        artifact = json.loads((self.fixture.hub / "data/connections/preview/projects.json").read_text())
        self.assertEqual(["a", "b"], [r["project_id"] for r in artifact["projects"]])


class ProjectServiceFacadeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.a, _ = self.fixture.add("a")
        self.b, _ = self.fixture.add("b")
        self.service = ProjectService(self.fixture.hub)
        self.design = {"available": True, "store_revision": 3,
                       "store_classification": "synthetic_fixture",
                       "facts": [{"kind": "candidate", "id": "candidate-a", "revision": 1,
                                  "scope": {"members": [{"project_id": "a"}]}}],
                       "history": [], "effective": {}, "queues": {}, "reason": None}

    def tearDown(self):
        self.fixture.close()

    def command(self, request_id, project_ids=("a",), head=None):
        return {"request_id": request_id, "project_ids": list(project_ids),
                "expected_head": head or {"sequence": 0, "hash": GENESIS_HASH}}

    def test_initial_list_keeps_legacy_exception_historical_and_permission_current(self):
        self.fixture.projects[0]["legacy_hub_connection_exception"] = {
            "status": "AUTHORIZED_EXCEPTION", "reason": "visible exception"}
        self.fixture.save_registry()
        ledger = self.fixture.hub / "data/connections/connection_refresh.sqlite3"
        with mock.patch("hub.project_service.SourceResolver.refresh",
                        side_effect=AssertionError("list/detail must not resolve sources")):
            listing = self.service.list_projects()
            detail = self.service.get_project("a")
        self.assertEqual(2, listing["total"])
        self.assertEqual(detail, listing["projects"][0])
        self.assertEqual({"schema_version", "project_id", "name", "declared", "business",
                          "operational", "freshness", "source", "errors", "relations",
                          "design", "provenance"}, set(detail))
        self.assertIsNone(detail["declared"]["hub_connection_exception"])
        self.assertEqual("visible exception",
                         detail["declared"]["legacy_hub_connection_exception"]["reason"])
        self.assertTrue(detail["declared"]["connection_read_allowed"])
        self.assertEqual("unknown", detail["business"]["normalized_status"])
        self.assertFalse(ledger.exists())

    def test_actual_registry_shaped_manga_permission_exposes_missing_declaration(self):
        manga = self.fixture.projects[0]
        manga.update(name="Manga Localizer", enabled=False, summary_enabled=True,
                     access_profile="bounded_named_business_state_read",
                     connection_read_allowed=True,
                     current_state_paths=[".agent/STATE.yaml"],
                     current_state_status="owner_authorized_sole_business_state",
                     legacy_hub_connection_exception={
                         "status": "AUTHORIZED_EXCEPTION",
                         "reason": "historical restriction"})
        (self.a / "hub.connection.yaml").unlink()
        self.fixture.save_registry()

        outcome = self.service.refresh(self.command("manga-missing"))
        self.assertEqual(["a"], outcome["appended_project_ids"])
        row = self.service.get_project("a")
        self.assertFalse(row["declared"]["enabled"])
        self.assertTrue(row["declared"]["connection_read_allowed"])
        self.assertIsNone(row["declared"]["hub_connection_exception"])
        self.assertEqual("historical restriction",
                         row["declared"]["legacy_hub_connection_exception"]["reason"])
        self.assertEqual("missing_declaration",
                         row["operational"]["latest_attempt"]["disposition"])
        self.assertEqual("missing_declaration", row["errors"][0]["code"])
        self.assertEqual("unknown", row["freshness"]["state"])

    def test_effective_permission_matches_resolver_denials_without_root_reads(self):
        cases = [
            ({"enabled": True, "connection_read_allowed": False}, "disabled"),
            ({"enabled": True, "connection_read_allowed": True,
              "access_profile": "no_current_goal_access"}, "disabled"),
        ]
        for index, (changes, disposition) in enumerate(cases):
            with self.subTest(changes=changes):
                fixture = Fixture()
                try:
                    fixture.add("denied")
                    fixture.projects[0].update(root_path="/definitely/not/read", **changes)
                    fixture.save_registry()
                    service = ProjectService(fixture.hub)
                    outcome = service.refresh({
                        "request_id": f"denied-{index}", "project_ids": ["denied"],
                        "expected_head": {"sequence": 0, "hash": GENESIS_HASH}})
                    self.assertEqual(["denied"], outcome["appended_project_ids"])
                    row = service.get_project("denied")
                    self.assertFalse(row["declared"]["connection_read_allowed"])
                    self.assertEqual(disposition,
                                     row["operational"]["latest_attempt"]["disposition"])
                finally:
                    fixture.close()

    def test_removed_identity_never_resolves_or_reads_removed_root(self):
        self.fixture.projects.append({"id": "removed", "name": "removed",
                                      "root_path": "/definitely/not/read",
                                      "enabled": True, "connection_read_allowed": True,
                                      "current_state_paths": ["STATE.yaml"],
                                      "local_presence": {"status": "removed_local"}})
        self.fixture.save_registry()
        with mock.patch("hub.project_service.SourceResolver.refresh",
                        side_effect=AssertionError("read facade must not refresh")), \
                mock.patch.object(Path, "resolve",
                                  side_effect=AssertionError("removed root must not be probed")):
            row = self.service.get_project("removed")
        self.assertEqual("removed_local", row["freshness"]["state"])
        self.assertEqual("removed_local", row["freshness"]["local_presence"])
        self.assertFalse(row["declared"]["connection_read_allowed"])
        self.assertIsNone(row["operational"]["latest_attempt"])
        self.assertEqual([], row["source"]["entrypoints"])

    def test_explicit_refresh_is_cas_partial_success_and_idempotent(self):
        command = self.command("facade-refresh", ("a", "b"))
        first = self.service.refresh(command)
        self.assertEqual("FINISHED", first["request"]["status"])
        self.assertEqual(["a", "b"], first["appended_project_ids"])
        head = first["projection"]["head"]
        with mock.patch("hub.project_service.SourceResolver.refresh",
                        side_effect=AssertionError("durable retry must not reread")):
            retry = self.service.refresh(command)
        self.assertEqual([], retry["appended_project_ids"])
        self.assertEqual(head, retry["projection"]["head"])

    def test_query_order_pagination_detail_and_empty_filter_keep_real_head(self):
        refreshed = self.service.refresh(self.command("query", ("a", "b")))
        listing = self.service.list_projects({"q": "A", "status": "complete",
                                              "freshness": "fresh", "order": "-name",
                                              "offset": "0", "limit": "1"}, self.design)
        self.assertEqual(1, listing["total"])
        self.assertEqual(listing["projects"][0],
                         self.service.get_project("a", self.design))
        self.assertEqual(self.fixture.business, listing["projects"][0]["business"]["fields"])
        self.assertEqual(self.fixture.business,
                         listing["projects"][0]["source"]["source_record"]["business"])
        self.assertEqual("candidate-a", listing["projects"][0]["design"]["references"][0]["id"])
        empty = self.service.list_projects({"q": "does-not-exist"})
        self.assertEqual([], empty["projects"])
        self.assertEqual(refreshed["projection"]["head"], empty["head"])

    def test_invalid_shapes_unknown_ids_and_conflicts_use_sanitized_codes(self):
        cases = [(lambda: self.service.list_projects({"path": "/private/value"}), "QUERY_INVALID"),
                 (lambda: self.service.list_projects({"limit": "01"}), "QUERY_INVALID"),
                 (lambda: self.service.get_project("bad:id"), "PROJECT_ID_INVALID"),
                 (lambda: self.service.get_project("missing"), "PROJECT_NOT_FOUND"),
                 (lambda: self.service.refresh({"request_id": "x", "project_ids": ["a"],
                                                "expected_head": {"sequence": True,
                                                                  "hash": GENESIS_HASH}}),
                  "REFRESH_COMMAND_INVALID")]
        for call, code in cases:
            with self.subTest(code=code), self.assertRaises(ServiceError) as caught:
                call()
            self.assertEqual(code, caught.exception.code)
            self.assertNotIn(str(self.fixture.hub), json.dumps(caught.exception.as_dict()))
        self.service.refresh(self.command("head-a"))
        with self.assertRaises(ServiceError) as caught:
            self.service.refresh(self.command("head-b", ("b",)))
        self.assertEqual("REFRESH_HEAD_CONFLICT", caught.exception.code)

    def test_latest_failure_is_current_and_last_success_is_only_history(self):
        self.service.refresh(self.command("good"))
        good = self.service.get_project("a")
        source_hash = good["source"]["source_record"]["sources"][0]["sha256"]
        (self.a / "STATE.yaml").write_text("status: [corrupt")
        head = RefreshLedger(self.fixture.hub, result_validator=self.fixture.resolver().validate_result,
                             read_only=True).head
        self.service.refresh(self.command("bad", head=head))
        row = self.service.get_project("a")
        self.assertFalse(row["source"]["source_record"]["success"])
        self.assertEqual("invalid", row["source"]["source_record"]["disposition"])
        self.assertEqual("unknown", row["business"]["normalized_status"])
        self.assertEqual(empty_business(), row["business"]["fields"])
        self.assertEqual(source_hash,
                         row["operational"]["last_success"]["sources"][0]["sha256"])
        self.assertNotEqual(row["operational"]["latest_attempt"],
                            row["operational"]["last_success"])
        self.assertEqual(row["source"]["source_record"],
                         row["business"]["source_record"])
        self.assertEqual(row["source"]["source_record"],
                         row["operational"]["latest_attempt"])
        for field in FIELDS:
            value = row["business"]["fields"]
            for part in field.split("."):
                self.assertIn(part, value)
                value = value[part]

    def test_registry_operational_metadata_cannot_promote_business(self):
        self.fixture.projects[0]["current_state_status"] = "complete"
        self.fixture.projects[0]["priority_source"] = "user"
        self.fixture.save_registry()
        row = self.service.get_project("a")
        self.assertEqual("unknown", row["business"]["normalized_status"])
        self.assertEqual(empty_business(), row["business"]["fields"])
        self.assertIsNone(row["business"]["source_record"])

    def test_facade_projection_uses_one_coherent_ledger_snapshot(self):
        ledger = mock.Mock()
        ledger.history.return_value = {"head": None, "results": []}
        with mock.patch.object(self.service, "_read_ledger", return_value=ledger):
            listing = self.service.list_projects()
        self.assertEqual(2, listing["total"])
        ledger.history.assert_called_once_with(current_authority=mock.ANY)

    def test_design_unavailable_and_missing_relations_preserve_project_facts(self):
        unavailable = copy.deepcopy(self.design)
        unavailable.update(available=False, store_revision=None, facts=[],
                           reason="DESIGN_STORE_CORRUPT")
        row = self.service.get_project("a", unavailable)
        self.assertEqual("DESIGN_STORE_CORRUPT", row["design"]["reason"])
        self.assertEqual("RELATION_STORE_UNAVAILABLE", row["relations"]["reason"])

    def test_relation_store_rejects_escape_alias_hardlink_protected_name_and_nonobject(self):
        outside = self.fixture.base / "outside.json"
        outside.write_text('{"projects": []}')
        for unsafe in (outside, "data/connections/../../outside.json",
                       "data/connections/secrets/relations.json",
                       "data/connections/api_tokens/relations.json"):
            service = ProjectService(self.fixture.hub, relations_path=unsafe)
            with self.subTest(path=str(unsafe)), \
                    mock.patch("hub.project_service.os.open",
                               side_effect=AssertionError("unsafe route must not open")):
                relation = service._relations({"a"})["a"]
            self.assertEqual("RELATION_STORE_UNAVAILABLE", relation["reason"])

        data = self.fixture.hub / "data/connections"
        data.mkdir()
        source = data / "source.json"
        source.write_text('{"projects": []}')
        alias = data / "alias.json"
        alias.symlink_to(source)
        relation = ProjectService(self.fixture.hub, relations_path=alias)._relations({"a"})["a"]
        self.assertEqual("RELATION_STORE_UNAVAILABLE", relation["reason"])
        alias.unlink()
        hardlink = data / "hardlink.json"
        os.link(source, hardlink)
        relation = ProjectService(self.fixture.hub, relations_path=hardlink)._relations({"a"})["a"]
        self.assertEqual("RELATION_STORE_UNAVAILABLE", relation["reason"])
        list_payload = data / "list.json"
        list_payload.write_text("[]")
        relation = ProjectService(self.fixture.hub, relations_path=list_payload)._relations({"a"})["a"]
        self.assertEqual("RELATION_STORE_UNAVAILABLE", relation["reason"])

    def test_absent_ledger_fallback_rejects_escape_alias_and_nondirectory(self):
        for unsafe in (self.fixture.base / "outside.sqlite3",
                       "data/connections/../../outside.sqlite3"):
            service = ProjectService(self.fixture.hub, ledger_path=unsafe)
            with self.subTest(path=str(unsafe)), \
                    mock.patch("hub.project_service.RefreshLedger",
                               side_effect=AssertionError("unsafe ledger path must fail first")), \
                    mock.patch.object(Path, "exists",
                                      side_effect=AssertionError("unsafe ledger path must not be probed")):
                with self.assertRaises(ServiceError) as caught:
                    service._read_ledger()
            self.assertEqual("LEDGER_UNAVAILABLE", caught.exception.code)

        outside_dir = self.fixture.base / "outside-ledger"
        outside_dir.mkdir()
        connections = self.fixture.hub / "data/connections"
        connections.symlink_to(outside_dir, target_is_directory=True)
        with self.assertRaises(ServiceError) as caught:
            ProjectService(self.fixture.hub)._read_ledger()
        self.assertEqual("LEDGER_UNAVAILABLE", caught.exception.code)
        connections.unlink()
        connections.write_text("not a directory")
        with self.assertRaises(ServiceError) as caught:
            ProjectService(self.fixture.hub)._read_ledger()
        self.assertEqual("LEDGER_UNAVAILABLE", caught.exception.code)

    def test_registry_authority_drift_marks_retained_result_stale(self):
        self.service.refresh(self.command("before-drift"))
        before = self.service.get_project("a")
        self.fixture.projects[0]["name"] = "renamed"
        self.fixture.save_registry()
        row = self.service.get_project("a")
        self.assertEqual("stale", row["freshness"]["state"])
        self.assertTrue(row["freshness"]["authority_drift"])
        self.assertEqual("unknown", row["business"]["normalized_status"])
        self.assertEqual(empty_business(), row["business"]["fields"])
        self.assertEqual(set(FIELDS), set(row["business"]["unknown_fields"]))
        self.assertTrue(all(row["business"]["unknown_fields"].values()))
        self.assertEqual(before["source"]["source_record"], row["source"]["source_record"])
        self.assertEqual(before["operational"]["last_success"],
                         row["operational"]["last_success"])
        self.assertEqual(before["provenance"]["latest_result_hash"],
                         row["provenance"]["latest_result_hash"])

    def test_precommit_before_durable_begin_reports_unknown_receipt(self):
        def fail_before_commit(*_args, **_kwargs):
            raise PrecommitFaultError("fixture")
        with mock.patch("hub.project_service.refresh_projects", side_effect=fail_before_commit), \
                self.assertRaises(ServiceError) as caught:
            self.service.refresh(self.command("precommit"))
        self.assertEqual("REFRESH_PRECOMMIT_FAILED", caught.exception.code)
        self.assertEqual("UNKNOWN", caught.exception.outcome)
        self.assertEqual({"request_id": "precommit"}, caught.exception.details)

    def test_postbegin_busy_reports_durable_partial_receipt(self):
        def commit_one_then_busy(ledger, resolver, request_id, project_ids, *, expected_head):
            ledger.begin_request(request_id, project_ids, resolver.authority,
                                 expected_head=expected_head)
            ledger.append_project_result(request_id, project_ids[0],
                                         resolver.refresh(project_ids[0]),
                                         validator=resolver.validate_result)
            raise LedgerBusyError("fixture")
        with mock.patch("hub.project_service.refresh_projects",
                        side_effect=commit_one_then_busy), self.assertRaises(ServiceError) as caught:
            self.service.refresh(self.command("partial-busy", ("a", "b")))
        self.assertEqual("REFRESH_CONCURRENCY_BUSY", caught.exception.code)
        self.assertEqual("PARTIALLY_COMMITTED", caught.exception.outcome)
        self.assertEqual(["a"], caught.exception.details["completed_project_ids"])
        self.assertEqual(["b"], caught.exception.details["remaining_project_ids"])

    def test_midrequest_authority_drift_is_explicit(self):
        original = self.fixture.resolver().__class__.refresh

        def drift_after_first(resolver, project_id):
            result = original(resolver, project_id)
            if project_id == "a":
                self.fixture.projects[1]["name"] = "drifted"
                self.fixture.save_registry()
            return result

        with mock.patch("hub.project_service.SourceResolver.refresh", drift_after_first):
            outcome = self.service.refresh(self.command("mid-drift", ("a", "b")))
        self.assertEqual("drifted", outcome["current_authority"]["state"])
        self.assertTrue(outcome["projection"]["authority_drift"])
        self.assertEqual("stale", outcome["projection"]["projects"]["a"]["freshness"])

    def test_facade_has_no_legacy_bundle_or_manager_dependency(self):
        source = Path(project_module.__file__).read_text()
        self.assertNotIn("connection_manager_cli", source)
        self.assertNotIn("authority-bundle", source)
        self.assertNotIn("design_governance/connection_refresh", source)
