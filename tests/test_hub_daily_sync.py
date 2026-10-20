"""Finite metadata discovery and resumable daily refresh use one real ledger."""
import contextlib
import io
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import yaml

from connection_fixtures import Fixture
from hub.connection_cli import main
from hub.connection_records import RecordError, validate_result
from hub.connection_refresh import RefreshLedger, RequestConflictError
from hub.connection_sources import now
from hub.connection_sync import daily_request_id, scope_plan, sync_request


class DailySyncTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.a, _ = self.fixture.add("a")
        self.b, _ = self.fixture.add("b")
        (self.fixture.hub / "data/connections").mkdir()
        self.scope = {"schema_version": "1.0", "scope_id": "test-scope", "baseline_total": 2,
                      "project_ids": ["a", "b"], "excluded_project_ids": {}}
        self.save_scope()
        self.resolver = self.fixture.resolver()
        self.resolver.clock = now
        self.ledger = RefreshLedger(self.fixture.hub, result_validator=validate_result)

    def tearDown(self):
        self.fixture.close()

    def save_scope(self):
        (self.fixture.hub / "data/connections/sync_scope.yaml").write_text(yaml.safe_dump(self.scope))

    def run_sync(self, request_id, **kwargs):
        return sync_request(self.fixture.hub, self.resolver, self.ledger, request_id, **kwargs)

    def cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--root", str(self.fixture.hub), *args])
        return code, json.loads(output.getvalue())

    def test_cli_sync_still_observes_real_selected_roots(self):
        original = Path.resolve
        observed = []
        def resolving(path, *args, **kwargs):
            if path in (self.a, self.b):
                observed.append(str(path))
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "resolve", resolving):
            code, result = self.cli("sync", "--request-id", "observing-sync")
        self.assertEqual(0, code, result)
        self.assertTrue({str(self.a), str(self.b)} <= set(observed))
        self.assertEqual(2, result["appended_count"])

    def test_same_request_skips_committed_sources_and_new_id_reads_again(self):
        first, code = self.run_sync("one")
        self.assertEqual(0, code)
        self.assertEqual(2, first["appended_count"])
        with mock.patch.object(self.resolver, "refresh", side_effect=AssertionError("reread")):
            repeat, code = self.run_sync("one")
        self.assertEqual(0, code)
        self.assertEqual(0, repeat["appended_count"])
        self.assertEqual(first["head"], repeat["head"])
        with mock.patch.object(self.resolver, "refresh", wraps=self.resolver.refresh) as read:
            fresh, code = self.run_sync("two")
        self.assertEqual(2, read.call_count)
        self.assertEqual(2, fresh["appended_count"])
        self.assertGreater(fresh["head"]["sequence"], first["head"]["sequence"])

    def test_interrupted_request_resumes_only_missing_project(self):
        original = self.resolver.refresh
        def interrupt(project_id):
            if project_id == "b":
                raise KeyboardInterrupt()
            return original(project_id)
        with mock.patch.object(self.resolver, "refresh", interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_sync("resume")
        history = self.ledger.history("resume")
        self.assertEqual(["a"], history["requests"][0]["completed_project_ids"])
        with mock.patch.object(self.resolver, "refresh", wraps=original) as read:
            result, code = self.run_sync("resume")
        self.assertEqual(0, code)
        read.assert_called_once_with("b")
        self.assertEqual(1, result["appended_count"])

    def test_failed_source_isolated_and_durable_failure_is_not_reread(self):
        (self.b / "STATE.yaml").write_text("broken: [yaml")
        result, code = self.run_sync("failed")
        self.assertEqual(2, code)
        self.assertEqual("FINISHED", result["status"])
        self.assertEqual({"invalid": 1}, result["failure_code_counts"])
        history = self.ledger.history("failed")
        self.assertEqual({"a": True, "b": False}, {row["project_id"]: row["success"] for row in history["results"]})
        with mock.patch.object(self.resolver, "refresh", side_effect=AssertionError("reread")):
            again, code = self.run_sync("failed")
        self.assertEqual(2, code)
        self.assertEqual(0, again["appended_count"])
        self.assertEqual({"invalid": 1}, again["failure_code_counts"])

    def test_partial_business_quality_is_successful_source_read(self):
        for root in (self.a, self.b):
            declaration = yaml.safe_load((root / "hub.connection.yaml").read_text())
            declaration["mapping"] = {}
            (root / "hub.connection.yaml").write_text(yaml.safe_dump(declaration))
        # Freeze the revised declaration before this new explicit request.
        self.resolver = self.fixture.resolver()
        self.resolver.clock = now
        result, code = self.run_sync("partial")
        self.assertEqual(0, code)
        self.assertEqual({}, result["failure_code_counts"])
        self.assertEqual(2, result["summary"]["partial"])

    def test_authority_change_reusing_request_conflicts_without_new_results(self):
        first, _ = self.run_sync("authority")
        self.fixture.projects[0]["name"] = "Changed metadata"
        self.fixture.save_registry()
        self.resolver = self.fixture.resolver()
        with self.assertRaises(RequestConflictError):
            self.run_sync("authority")
        self.assertEqual(first["head"], self.ledger.head)

    def test_scope_metadata_qualification_never_probes_projects(self):
        self.fixture.add("outside")
        self.fixture.projects[0]["connection_read_allowed"] = False
        self.fixture.projects[1]["local_presence"] = {"status": "removed_local"}
        self.fixture.save_registry()
        self.scope["project_ids"].append("missing")
        self.scope["baseline_total"] = 3
        self.save_scope()
        resolver = self.fixture.resolver()
        with mock.patch.object(Path, "resolve", side_effect=AssertionError("root resolved")), \
             mock.patch.object(Path, "stat", side_effect=AssertionError("root inspected")), \
             mock.patch.object(resolver, "refresh", side_effect=AssertionError("source refreshed")):
            plan = scope_plan(self.fixture.hub, resolver)
        self.assertEqual(3, plan["scope_total"])
        self.assertEqual(3, plan["registered_count"])
        self.assertEqual(1, plan["outside_scope_count"])
        self.assertEqual([], plan["eligible_project_ids"])
        self.assertEqual({"a": "connection_read_disabled", "b": "removed_local", "missing": "missing_registration",
                          "outside": "outside_frozen_scope"},
                         {row["project_id"]: row["reason"] for row in plan["excluded"]})

    def test_disabled_summary_and_explicit_exclusion_remain_in_denominator(self):
        for setting, reason in (({"enabled": False, "connection_read_allowed": True}, "disabled"),
                                ({"summary_enabled": False}, "summary_disabled")):
            with self.subTest(setting=setting):
                self.fixture.projects[0].update(enabled=True, summary_enabled=True)
                self.fixture.projects[0].update(setting)
                self.fixture.save_registry()
                self.scope["excluded_project_ids"] = {"b": "outside current task"}
                self.save_scope()
                plan = scope_plan(self.fixture.hub, self.fixture.resolver())
                self.assertEqual(2, plan["scope_total"])
                self.assertEqual([], plan["eligible_project_ids"])
                self.assertEqual(reason, plan["excluded"][0]["reason"])

    def test_scope_rejects_duplicate_count_unknown_and_unsafe_yaml(self):
        original = dict(self.scope)
        for change in ({"baseline_total": 3}, {"project_ids": ["a", "a"]},
                       {"excluded_project_ids": {"../outside": "no"}}, {"extra": "invalid"},
                       {"scope_id": "../invalid"}, {"excluded_project_ids": {"a": ""}}):
            self.scope = {**original, **change}
            self.save_scope()
            with self.assertRaises(RecordError):
                scope_plan(self.fixture.hub, self.resolver)
        (self.fixture.hub / "data/connections/sync_scope.yaml").write_text("!!python/object:unsafe {}")
        with self.assertRaises(RecordError):
            scope_plan(self.fixture.hub, self.resolver)

    def test_discover_is_metadata_only_and_cli_output_has_no_business_payload(self):
        with mock.patch("hub.connection_sources.SourceResolver.refresh", side_effect=AssertionError("source read")):
            code, plan = self.cli("discover")
        self.assertEqual(0, code)
        self.assertEqual(["a", "b"], plan["eligible_project_ids"])
        code, result = self.cli("sync", "--request-id", "cli", "--project-id", "a")
        self.assertEqual(0, code)
        self.assertEqual({"request_id", "status", "appended_count", "failure_code_counts", "head", "summary"}, set(result))
        self.assertNotIn(str(self.a), json.dumps(result))
        self.assertNotIn("Repair current candidate", json.dumps(result))
        self.assertFalse((self.fixture.hub / "data/connections/preview").exists())

    def test_cli_rejects_daily_subset_and_outside_scope_before_refresh(self):
        with mock.patch("hub.connection_cli.RefreshLedger", side_effect=AssertionError("ledger opened")):
            for args in (("sync", "--daily", "--project-id", "a"),
                         ("sync", "--request-id", "bad", "--project-id", "outside")):
                code, result = self.cli(*args)
                self.assertEqual(2, code)
                self.assertEqual("INPUT_INVALID", result["error"])

    def test_daily_id_uses_tokyo_date_boundary_and_is_stable(self):
        before = datetime(2026, 10, 14, 14, 59, tzinfo=timezone.utc)
        after = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)
        self.assertEqual("scope.daily.2026-10-14", daily_request_id("scope", instant=before))
        self.assertEqual("scope.daily.2026-10-15", daily_request_id("scope", instant=after))
        self.assertEqual(daily_request_id("scope", instant=after), daily_request_id("scope", instant=after))

    def test_cli_daily_reuses_scope_day_and_cannot_rebind_subset(self):
        with mock.patch("hub.connection_sync.daily_request_id", return_value="test-scope.daily.2026-10-14"):
            code, first = self.cli("sync", "--daily")
            self.assertEqual(0, code)
            with mock.patch("hub.connection_sources.SourceResolver.refresh", side_effect=AssertionError("reread")):
                code, second = self.cli("sync", "--daily")
        self.assertEqual(0, code)
        self.assertEqual(0, second["appended_count"])
        self.assertEqual(first["head"], second["head"])
        code, conflict = self.cli("sync", "--request-id", first["request_id"], "--project-id", "a")
        self.assertEqual(2, code)
        self.assertEqual("REQUEST_IDENTITY_CONFLICT", conflict["error"])
        self.assertEqual(first["head"], self.ledger.head)

    def test_outside_scope_never_refreshes_and_scope_symlink_is_rejected(self):
        self.fixture.add("outside")
        self.resolver = self.fixture.resolver()
        with mock.patch.object(self.resolver, "refresh", wraps=self.resolver.refresh) as read:
            _, code = self.run_sync("bounded")
        self.assertEqual(0, code)
        self.assertEqual([mock.call("a"), mock.call("b")], read.call_args_list)
        scope_path = self.fixture.hub / "data/connections/sync_scope.yaml"
        target = self.fixture.base / "scope.yaml"
        target.write_bytes(scope_path.read_bytes())
        scope_path.unlink()
        scope_path.symlink_to(target)
        with self.assertRaises(OSError):
            scope_plan(self.fixture.hub, self.resolver)

    def test_no_eligible_sources_fails_without_root_probe_and_cannot_rebind_request(self):
        first, _ = self.run_sync("old")
        for row in self.fixture.projects:
            row["summary_enabled"] = False
        self.fixture.save_registry()
        self.resolver = self.fixture.resolver()
        with mock.patch.object(self.resolver, "refresh", side_effect=AssertionError("source read")):
            result, code = self.run_sync("empty")
            with self.assertRaises(RequestConflictError):
                self.run_sync("old")
        self.assertEqual(2, code)
        self.assertEqual("NO_ELIGIBLE_PROJECTS", result["status"])
        self.assertEqual({"NO_ELIGIBLE_PROJECTS": 1}, result["failure_code_counts"])
        self.assertEqual(first["head"], self.ledger.head)


if __name__ == "__main__":
    unittest.main()
