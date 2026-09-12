import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hub.metric_guard_report import CODE_PATHS, FORMAT, collect_guard_report
from hub.metric_validation import collect_validation
from hub.metric_collect import MetricCollector
from hub.connection_records import RecordError


class GuardReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "repo"
        self.reports = self.base / "reports"
        self.root.mkdir()
        self.reports.mkdir()
        self.files = {}
        for name in CODE_PATHS:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = (name + " source\n").encode()
            path.write_bytes(payload)
            self.files[name] = hashlib.sha256(payload).hexdigest()
        self.data = {"schema_version": FORMAT, "suite": "core", "status": "PASS",
            "started_at": "2026-09-09T01:00:00Z", "finished_at": "2026-09-09T01:00:05Z",
            "summary": {"observed_cases": 2, "complete": True, "failed_suites": 0},
            "code": {"files": self.files, "sha256": self.digest(self.files), "stable": True},
            "environment": {"python": "3.9.6", "system": "Darwin", "machine": "arm64", "guard_sha256": "b" * 64},
            "results": [{"case": "one", "details": "private payload"}, {"case": "two"}]}
        self.spec = {"id": "guard-core", "root": str(self.reports), "path": "report.json",
                     "suite": "core", "format": FORMAT}
        self.write()

    def digest(self, obj):
        return hashlib.sha256((json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()

    def write(self, relative="report.json", data=None):
        p = self.reports / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.data if data is None else data))
        return p

    def collect(self):
        return collect_guard_report(self.root, "player", "2026-09-09T02:00:00Z", self.spec)

    def values(self, result):
        return {r["metric_id"].removeprefix("validation.guard."): r["value"] for r in result["metrics"]}

    def versions(self, result):
        return {r["metric_id"].removeprefix("validation.guard."): r["source_version"] for r in result["metrics"]}

    def test_real_schema_scope_counts_and_privacy(self):
        result = self.collect()
        v = self.values(result)
        self.assertEqual(v["entries_observed"], 2)
        self.assertEqual(v["cases_reported"], 2)
        self.assertEqual(v["bound_to_selected_code"], 1)
        self.assertEqual(v["current_selected_code_passed"], 1)
        self.assertEqual(v["duration"], 5)
        self.assertEqual(v["selected_code_files"], 11)
        self.assertEqual(result["disposition"], "resolved")
        self.assertNotIn("private payload", json.dumps(result))
        self.assertTrue(all(r["dimensions"]["binding_scope"] == "selected_source_files" for r in result["metrics"]))

    def test_code_change_invalidates_binding_only_not_historical_counts(self):
        first = self.versions(self.collect())
        (self.root / CODE_PATHS[0]).write_text("new source")
        result = self.collect()
        v = self.values(result)
        self.assertEqual(v["bound_to_selected_code"], 0)
        self.assertIsNone(v["current_selected_code_passed"])
        self.assertEqual(v["run_passed"], 1)
        self.assertEqual(v["cases_reported"], 2)
        second = self.versions(result)
        self.assertEqual({k for k in first if first[k] != second[k]},
                         {"bound_to_selected_code", "current_selected_code_passed"})

    def test_incorrect_aggregate_keeps_independent_counts(self):
        self.data["code"]["sha256"] = "0" * 64
        self.write()
        v = self.values(self.collect())
        self.assertIsNone(v["bound_to_selected_code"])
        self.assertEqual(v["cases_reported"], 2)

    def test_exact_source_set_required_and_unstable_run_not_current(self):
        self.data["code"]["stable"] = False
        self.write()
        self.assertEqual(self.values(self.collect())["bound_to_selected_code"], 0)
        self.data["code"]["files"].pop(CODE_PATHS[0])
        self.data["code"]["sha256"] = self.digest(self.data["code"]["files"])
        self.write()
        self.assertIsNone(self.values(self.collect())["bound_to_selected_code"])

    def test_bad_summary_and_status_do_not_erase_entry_count_or_binding(self):
        self.data["summary"]["observed_cases"] = 99
        self.data["status"] = "MYSTERY"
        self.write()
        v = self.values(self.collect())
        self.assertEqual(v["entries_observed"], 2)
        self.assertIsNone(v["cases_reported"])
        self.assertIsNone(v["run_passed"])
        self.assertEqual(v["bound_to_selected_code"], 1)

    def test_newest_failure_is_selected_without_merging_denominators(self):
        self.spec["path"] = "universal-player-guard-*/results/core-*.json"
        folder = "universal-player-guard-" + "a" * 32 + "/results/"
        first = self.write(folder + "core-" + "1" * 32 + ".json")
        self.data["status"] = "FAIL"
        self.data["summary"] = {"observed_cases": 1, "complete": False, "failed_suites": 1}
        self.data["results"] = [{"case": "first complete case before failure"}]
        second = self.write(folder + "core-" + "2" * 32 + ".json")
        os.utime(first, ns=(1000000000, 1000000000))
        os.utime(second, ns=(2000000000, 2000000000))
        result = self.collect()
        v = self.values(result)
        self.assertEqual(v["cases_reported"], 1)
        self.assertEqual(v["current_selected_code_passed"], 0)
        self.assertEqual(v["failed_suites"], 1)
        self.assertIn("validation_guard_failed", [r["code"] for r in result["issues"]])
        second.write_text("invalid json")
        self.assertIsNone(self.values(self.collect())["run_passed"])

    def test_non_metadata_report_details_do_not_change_semantic_versions(self):
        first = self.versions(self.collect())
        self.data["results"][0]["details"] = "different private payload"
        self.data["trace"] = ["do not project"]
        self.write()
        self.assertEqual(first, self.versions(self.collect()))

    def test_bad_business_time_is_unknown_not_filesystem_mtime(self):
        self.data["finished_at"] = "yesterday"
        self.write()
        result = self.collect()
        self.assertEqual(self.values(result)["cases_reported"], 2)
        self.assertIsNone(self.values(result)["age"])
        self.assertTrue(all(row["business_at"] is None for row in result["metrics"]))

    def test_bad_environment_hash_keeps_independent_source_binding(self):
        self.data["environment"]["guard_sha256"] = "unverified"
        self.write()
        v = self.values(self.collect())
        self.assertEqual(v["environment_recorded"], 0)
        self.assertEqual(v["bound_to_selected_code"], 1)
        self.assertEqual(v["cases_reported"], 2)

    def test_symlink_report_or_code_never_binds(self):
        (self.reports / "report.json").unlink()
        other = self.write("other.json")
        (self.reports / "report.json").symlink_to(other)
        self.assertIsNone(self.values(self.collect())["entries_observed"])
        (self.reports / "report.json").unlink()
        self.write()
        source = self.root / CODE_PATHS[0]
        source.unlink()
        source.symlink_to(other)
        self.assertIsNone(self.values(self.collect())["bound_to_selected_code"])

    def test_source_set_race_invalidates_binding(self):
        from hub.metric_sources import read_metadata
        def mutate(root, name):
            result = read_metadata(root, name)
            if name == CODE_PATHS[-1]:
                (self.root / CODE_PATHS[0]).write_text("concurrent change")
            return result
        with patch("hub.metric_guard_report.read_metadata", side_effect=mutate):
            v = self.values(self.collect())
        self.assertIsNone(v["bound_to_selected_code"])
        self.assertEqual(v["cases_reported"], 2)

    def test_discovery_budget_and_unknown_schema_do_not_fall_back(self):
        self.spec["path"] = "results/core-*.json"
        self.write("results/core-" + "1" * 32 + ".json")
        with patch("hub.metric_guard_report.MAX_DISCOVERY", 0):
            self.assertIsNone(self.values(self.collect())["run_passed"])
        self.data["schema_version"] = "other"
        self.write("results/core-" + "1" * 32 + ".json")
        self.assertIsNone(self.values(self.collect())["run_passed"])

    def test_shared_validation_dispatch_does_not_require_git_binding(self):
        with patch("hub.metric_validation._git_state", side_effect=AssertionError("not a whole HEAD binding")):
            result = collect_validation(self.root, "player", "2026-09-09T02:00:00Z", [self.spec])
        self.assertEqual(self.values(result)["bound_to_selected_code"], 1)

    def test_functional_adapter_requires_actual_report_declaration_and_suite(self):
        c = MetricCollector.__new__(MetricCollector)
        c.projects = {"player": {}}
        c.config = {"schema_version": "1.0", "projects": {"player": {
            "adapter": "validation_runs", "validation_reports": [self.spec]}}}
        c.validate_config()
        self.spec["suite"] = []
        with self.assertRaises(RecordError):
            c.validate_config()
        c.config["projects"]["player"]["validation_reports"] = []
        with self.assertRaises(RecordError):
            c.validate_config()


if __name__ == "__main__":
    unittest.main()
