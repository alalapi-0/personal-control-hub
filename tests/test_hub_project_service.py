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
from hub.connection_records import RecordError, validate_result
from hub.connection_refresh import RefreshLedger, refresh
from hub.project_service import project_snapshot, row_update_key, validate_snapshot
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
