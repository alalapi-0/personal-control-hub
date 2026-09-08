from __future__ import annotations

import contextlib
import io
import json
import socket
import sqlite3
import subprocess
import sys
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from connection_fixtures import Fixture
from hub.connection_cli import main
from hub.connection_refresh import GENESIS_HASH, RefreshLedger
from hub.connection_records import validate_result
from hub.connection_sources import SourceResolver


class HubRefreshCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()
        self.a, _ = self.fixture.add("a")
        self.b, _ = self.fixture.add("b")
        self.addCleanup(self.fixture.close)
        self.ledger_path = self.fixture.hub / "data/connections/connection_refresh.sqlite3"

    def invoke(self, *arguments: str) -> tuple[int, dict]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--root", str(self.fixture.hub), *arguments])
        return code, json.loads(output.getvalue())

    def test_refresh_retry_history_and_offline_rebuild_keep_public_receipt(self) -> None:
        code, first = self.invoke("refresh", "--request-id", "all")
        self.assertEqual(0, code, first)
        self.assertEqual("FINISHED", first["request"]["status"])
        self.assertEqual(["a", "b"], first["appended_project_ids"])
        self.assertEqual({"a", "b"}, set(first["projection"]["projects"]))

        with mock.patch.object(SourceResolver, "refresh",
                               side_effect=AssertionError("retry must not re-read")):
            code, retry = self.invoke("refresh", "--request-id", "all")
        self.assertEqual(0, code, retry)
        self.assertEqual([], retry["appended_project_ids"])
        self.assertEqual(first["projection"], retry["projection"])

        code, history = self.invoke("history")
        self.assertEqual(0, code, history)
        self.assertEqual("historical_ledger", history["view_role"])
        self.assertEqual("available", history["current_authority"]["state"])
        self.assertEqual(2, len(history["results"]))
        code, rebuilt = self.invoke("rebuild")
        self.assertEqual(0, code, rebuilt)
        self.assertEqual(first["projection"]["projects"], rebuilt["projects"])

    def test_history_and_rebuild_survive_corrupt_or_missing_registry_without_external_effects(self) -> None:
        code, _ = self.invoke("refresh", "--request-id", "durable", "--project-id", "a")
        self.assertEqual(0, code)
        registry = self.fixture.hub / "data/registry/external_projects.yaml"
        original = registry.read_bytes()

        for label in ("corrupt", "missing"):
            with self.subTest(label=label):
                if label == "corrupt":
                    registry.write_text("projects: [", encoding="utf-8")
                else:
                    registry.unlink()
                with mock.patch("hub.connection_sources._safe_relative_read",
                                side_effect=AssertionError("project roots must not be read")), \
                        mock.patch.object(subprocess, "run",
                                          side_effect=AssertionError("processes must not run")), \
                        mock.patch.object(subprocess, "Popen",
                                          side_effect=AssertionError("processes must not run")), \
                        mock.patch.object(socket, "create_connection",
                                          side_effect=AssertionError("network must not be used")):
                    code, history = self.invoke("history")
                    self.assertEqual(0, code, history)
                    self.assertEqual("unavailable", history["current_authority"]["state"])
                    self.assertEqual("historical_ledger", history["view_role"])
                    self.assertIsNone(history["requests"][0]["authority_drift"])
                    code, rebuilt = self.invoke("rebuild")
                    self.assertEqual(0, code, rebuilt)
                    self.assertEqual("stale", rebuilt["projects"]["a"]["freshness"])
                    self.assertIsNone(rebuilt["projects"]["a"]["authority_drift"])
                registry.write_bytes(original)

    def test_missing_and_corrupt_ledger_fail_with_fixed_errors_without_creation(self) -> None:
        for command in ("history", "rebuild"):
            with self.subTest(command=command):
                code, result = self.invoke(command)
                self.assertEqual(2, code, result)
                self.assertEqual("UNSAFE_LEDGER_PATH", result["error"])
                self.assertEqual("Hub refresh ledger is missing or unavailable at the configured Hub-local path.",
                                 result["message"])
                self.assertFalse(self.ledger_path.exists())

        ledger = RefreshLedger(self.fixture.hub, self.ledger_path,
                               result_validator=validate_result)
        ledger.begin_request("corrupt", ["a"], self.fixture.resolver().authority)
        with closing(sqlite3.connect(self.ledger_path)) as connection:
            connection.execute("UPDATE ledger_meta SET head_hash=?", ("f" * 64,))
            connection.commit()
        code, result = self.invoke("history")
        self.assertEqual(2, code, result)
        self.assertEqual("LEDGER_CORRUPT", result["error"])

    def test_current_authority_change_marks_historical_projection_stale(self) -> None:
        code, _ = self.invoke("refresh", "--request-id", "before", "--project-id", "a")
        self.assertEqual(0, code)
        self.fixture.projects[0]["name"] = "renamed"
        self.fixture.save_registry()

        code, history = self.invoke("history")
        self.assertEqual(0, code, history)
        self.assertTrue(history["requests"][0]["authority_drift"])
        code, rebuilt = self.invoke("rebuild")
        self.assertEqual(0, code, rebuilt)
        self.assertTrue(rebuilt["projects"]["a"]["authority_drift"])
        self.assertEqual("stale", rebuilt["projects"]["a"]["freshness"])

    def test_unavailable_authority_preserves_unknown_and_latest_failure_causes(self) -> None:
        offline = self.a.with_name("a-offline")
        self.a.rename(offline)
        code, _ = self.invoke("refresh", "--request-id", "a-first-failure", "--project", "a")
        self.assertEqual(2, code)
        offline.rename(self.a)
        code, _ = self.invoke("refresh", "--request-id", "b-success", "--project", "b")
        self.assertEqual(0, code)
        (self.b / "STATE.yaml").unlink()
        code, _ = self.invoke("refresh", "--request-id", "b-newer-failure", "--project", "b")
        self.assertEqual(2, code)
        (self.fixture.hub / "data/registry/external_projects.yaml").write_text(
            "projects: [", encoding="utf-8")

        code, rebuilt = self.invoke("rebuild")
        self.assertEqual(0, code, rebuilt)
        self.assertEqual("unknown", rebuilt["projects"]["a"]["freshness"])
        self.assertIn("Registered root is unavailable", rebuilt["projects"]["a"]["stale_reason"])
        self.assertIn("current Hub authority could not be verified", rebuilt["projects"]["a"]["stale_reason"])
        self.assertEqual("stale", rebuilt["projects"]["b"]["freshness"])
        self.assertIn("Declared current-state source is missing", rebuilt["projects"]["b"]["stale_reason"])
        self.assertIn("current Hub authority could not be verified", rebuilt["projects"]["b"]["stale_reason"])

    def test_partial_failure_restarts_without_rereading_committed_project(self) -> None:
        original = SourceResolver.refresh

        def interrupt(resolver: SourceResolver, project_id: str) -> dict:
            if project_id == "b":
                raise RuntimeError("fixture interruption")
            return original(resolver, project_id)

        with mock.patch.object(SourceResolver, "refresh", interrupt):
            code, partial = self.invoke("refresh", "--request-id", "partial")
        self.assertEqual(2, code, partial)
        self.assertEqual("OPEN", partial["request"]["status"])
        self.assertEqual(["a"], partial["appended_project_ids"])
        self.assertEqual({"b": "RuntimeError"}, partial["resolver_errors"])

        calls: list[str] = []

        def resume(resolver: SourceResolver, project_id: str) -> dict:
            calls.append(project_id)
            return original(resolver, project_id)

        with mock.patch.object(SourceResolver, "refresh", resume):
            code, completed = self.invoke("refresh", "--request-id", "partial")
        self.assertEqual(0, code, completed)
        self.assertEqual(["b"], calls)
        self.assertEqual(["b"], completed["appended_project_ids"])
        self.assertEqual("FINISHED", completed["request"]["status"])

    def test_failed_request_replay_stays_failed_and_removed_only_is_nonretrying(self) -> None:
        offline = self.a.with_name("a-offline")
        self.a.rename(offline)
        code, failed = self.invoke("refresh", "--request-id", "failed-a", "--project", "a")
        self.assertEqual(2, code, failed)
        offline.rename(self.a)
        code, succeeded = self.invoke("refresh", "--request-id", "success-b", "--project", "a")
        self.assertEqual(0, code, succeeded)
        with mock.patch.object(SourceResolver, "refresh",
                               side_effect=AssertionError("replay must not re-read")):
            code, replay = self.invoke("refresh", "--request-id", "failed-a", "--project", "a")
        self.assertEqual(2, code, replay)
        self.assertEqual([], replay["appended_project_ids"])

        self.fixture.projects[1].update(
            root_path="/definitely/not/read",
            local_presence={"status": "removed_local"},
        )
        self.fixture.save_registry()
        code, removed = self.invoke("refresh", "--request-id", "removed-only", "--project", "b")
        self.assertEqual(0, code, removed)
        self.assertEqual("removed_local",
                         removed["projection"]["projects"]["b"]["latest_attempt"]["disposition"])
        with mock.patch.object(SourceResolver, "refresh",
                               side_effect=AssertionError("removed replay must not re-read")):
            code, replay = self.invoke("refresh", "--request-id", "removed-only", "--project", "b")
        self.assertEqual(0, code, replay)
        self.assertEqual([], replay["appended_project_ids"])

    def test_refresh_preserves_expected_head_cas_and_legacy_option_aliases(self) -> None:
        code, first = self.invoke("--ledger", "data/connections/connection_refresh.sqlite3",
                                  "refresh", "--request-id", "cas-one", "--project", "a",
                                  "--expected-sequence", "0", "--expected-hash", GENESIS_HASH)
        self.assertEqual(0, code, first)
        self.assertEqual(["a"], first["appended_project_ids"])

        code, conflict = self.invoke("refresh", "--request-id", "cas-two", "--project-id", "b",
                                     "--expected-sequence", "0", "--expected-hash", GENESIS_HASH)
        self.assertEqual(2, code, conflict)
        self.assertEqual("EXPECTED_HEAD_CONFLICT", conflict["error"])

        code, incomplete = self.invoke("refresh", "--request-id", "cas-three",
                                       "--expected-sequence", "0")
        self.assertEqual(2, code, incomplete)
        self.assertEqual("INPUT_INVALID", incomplete["error"])

    def test_mid_request_authority_drift_rechecks_projection_before_preview(self) -> None:
        original = SourceResolver.refresh

        def drift_after_first(resolver: SourceResolver, project_id: str) -> dict:
            result = original(resolver, project_id)
            if project_id == "a":
                self.fixture.projects[1]["name"] = "renamed-b"
                self.fixture.save_registry()
            return result

        with mock.patch.object(SourceResolver, "refresh", drift_after_first):
            code, result = self.invoke("refresh", "--request-id", "mid-drift")
        self.assertEqual(2, code, result)
        self.assertEqual("stale", result["projection"]["projects"]["a"]["freshness"])
        self.assertTrue(result["projection"]["projects"]["a"]["authority_drift"])
        preview = json.loads((self.fixture.hub / result["previews"]["projects.json"]).read_text())
        rows = {row["project_id"]: row for row in preview["projects"]}
        self.assertEqual("stale", rows["a"]["freshness"]["state"])
        self.assertEqual("renamed-b", rows["b"]["name"])

    def test_unavailable_post_refresh_authority_returns_receipt_without_preview(self) -> None:
        original = SourceResolver.refresh

        def corrupt_after_first(resolver: SourceResolver, project_id: str) -> dict:
            result = original(resolver, project_id)
            if project_id == "a":
                (self.fixture.hub / "data/registry/external_projects.yaml").write_text(
                    "projects: [", encoding="utf-8")
            return result

        with mock.patch.object(SourceResolver, "refresh", corrupt_after_first):
            code, result = self.invoke("refresh", "--request-id", "authority-unavailable")
        self.assertEqual(2, code, result)
        self.assertEqual("CURRENT_AUTHORITY_UNAVAILABLE", result["error"])
        self.assertEqual("FINISHED", result["receipt"]["request"]["status"])
        self.assertEqual(["a", "b"], result["receipt"]["appended_project_ids"])
        self.assertEqual({"available": False, "error": "CURRENT_AUTHORITY_UNAVAILABLE"},
                         result["previews"])
        self.assertFalse((self.fixture.hub / "data/connections/preview").exists())

    def test_post_commit_error_is_sanitized_and_retains_receipt(self) -> None:
        marker = "sensitive-marker-from-os"
        with mock.patch("hub.connection_cli.write_previews", side_effect=OSError(marker)):
            code, result = self.invoke("refresh", "--request-id", "preview-failure", "--project", "a")
        self.assertEqual(2, code, result)
        self.assertEqual("PREVIEW_UNAVAILABLE", result["error"])
        self.assertEqual("FINISHED", result["receipt"]["request"]["status"])
        self.assertEqual(["a"], result["receipt"]["appended_project_ids"])
        self.assertNotIn(marker, json.dumps(result))

    def test_offline_root_and_missing_named_source_remain_distinct_failures(self) -> None:
        self.a.rename(self.a.with_name("a-offline"))
        (self.b / "STATE.yaml").unlink()
        code, refreshed = self.invoke("refresh", "--request-id", "source-failures")
        self.assertEqual(2, code, refreshed)
        self.assertEqual(["a", "b"], refreshed["appended_project_ids"])

        code, history = self.invoke("history", "--request-id", "source-failures")
        self.assertEqual(0, code, history)
        failures = {row["project_id"]: row["result"] for row in history["results"]}
        self.assertEqual("offline", failures["a"]["disposition"])
        self.assertEqual("missing_source", failures["b"]["disposition"])
        self.assertEqual([], failures["a"]["sources"])
        self.assertEqual([], failures["b"]["sources"])


if __name__ == "__main__":
    unittest.main()
