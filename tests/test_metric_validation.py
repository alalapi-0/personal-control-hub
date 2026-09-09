import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hub.metric_validation import collect_validation
from hub.metric_collect import MetricCollector
from hub.connection_records import RecordError


class ValidationMetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.commit = "a" * 40
        self.report = {"generated_at": "2026-09-09T01:00:00Z", "git_commit": self.commit,
            "working_tree_status": "", "environment": {"python": "3.11", "platform": "test"},
            "status": "PASS", "overall_status": "PARTIAL", "feature_results": [
                {"id": "unit", "status": "PASS", "details": {"output": "private log"}},
                {"id": "journey", "status": "BLOCKED"}, {"id": "voice", "status": "HUMAN_ONLY"}]}
        self.spec = [{"id": "saved", "path": "verify.json", "format": "feature_report"}]

    def collect(self, *, write=True, clean=True):
        if write:
            (self.root / "verify.json").write_text(json.dumps(self.report))
        with patch("hub.metric_validation._git_state", return_value=(self.commit, clean)):
            return collect_validation(self.root, "audio", "2026-09-09T02:00:00Z", self.spec)

    def values(self, result):
        return {(r["metric_id"], r["dimensions"].get("status")): r["value"] for r in result["metrics"]}

    def test_actual_scope_not_overall_pass_or_private_output(self):
        result = self.collect()
        values = self.values(result)
        self.assertEqual(values["validation.checks.total", None], 3)
        self.assertEqual(values["validation.checks", "PASS"], 1)
        self.assertEqual(values["validation.checks", "HUMAN_ONLY"], 1)
        self.assertEqual(values["validation.report.bound_to_current_code", None], 1)
        self.assertEqual(result["disposition"], "partial")
        self.assertNotIn("private log", json.dumps(result))

    def test_unbound_dirty_and_missing_revision(self):
        self.report["working_tree_status"] = " M private.txt"
        self.assertEqual(self.values(self.collect())["validation.report.bound_to_current_code", None], 0)
        self.report.pop("git_commit")
        self.report.pop("working_tree_status")
        self.assertIsNone(self.values(self.collect())["validation.report.bound_to_current_code", None])
        self.assertEqual(self.values(self.collect())["validation.checks", "PASS"], 1)

    def test_duplicate_identity_does_not_discard_binding(self):
        self.report["feature_results"].append({"id": "unit", "status": "PASS"})
        values = self.values(self.collect())
        self.assertIsNone(values["validation.checks.total", None])
        self.assertEqual(values["validation.entries.total", None], 4)
        self.assertEqual(values["validation.entries.duplicate_identity_occurrences", None], 1)
        self.assertEqual(values["validation.report.bound_to_current_code", None], 1)

    def test_unknown_enum_keeps_independent_identity_count(self):
        self.report["feature_results"][0]["status"] = "mystery"
        values = self.values(self.collect())
        self.assertEqual(values["validation.checks.total", None], 3)
        self.assertEqual(values["validation.checks.invalid_status", None], 1)
        self.assertEqual(values["validation.checks", "BLOCKED"], 1)

    def test_semantic_versions_ignore_logs_and_only_related_status_changes(self):
        def versions(result):
            return {(r["metric_id"], r["dimensions"].get("status")): r["source_version"] for r in result["metrics"]}
        first = versions(self.collect())
        self.report["feature_results"][0]["details"]["output"] = "different private output"
        self.assertEqual(first, versions(self.collect()))
        self.report["feature_results"][0]["status"] = "FAIL"
        second = versions(self.collect())
        changed = {key for key in first if first[key] != second[key]}
        self.assertEqual(changed, {("validation.checks", "PASS"), ("validation.checks", "FAIL")})

    def test_missing_and_symlink_reports_remain_unknown(self):
        self.assertIsNone(self.values(self.collect(write=False))["validation.checks.total", None])
        outside = self.root / "other.json"
        outside.write_text(json.dumps(self.report))
        (self.root / "verify.json").symlink_to(outside)
        result = self.collect(write=False)
        self.assertIsNone(self.values(result)["validation.checks.total", None])

    def test_gate_arrays_explicit_zero_and_historical_binding(self):
        self.report = {"timestamp": "2026-06-18T14:52:44+00:00", "passed": ["one", "two"],
                       "failed": [], "blocked": [], "skipped": []}
        self.spec[0]["format"] = "gate_arrays"
        values = self.values(self.collect())
        self.assertEqual(values["validation.checks", "PASS"], 2)
        self.assertEqual(values["validation.checks", "FAIL"], 0)
        self.assertIsNone(values["validation.report.commit_matches_head", None])

    def test_declarations_reject_ambiguous_roots_and_duplicate_report_identity(self):
        collector = MetricCollector.__new__(MetricCollector)
        collector.projects = {"audio": {}}
        collector.config = {"schema_version": "1.0", "projects": {"audio": {
            "adapter": "unmapped", "validation_reports": self.spec}}}
        collector.validate_config()
        self.spec.append(dict(self.spec[0]))
        with self.assertRaises(RecordError):
            collector.validate_config()
        self.spec.pop()
        self.spec[0]["root"] = "../ambiguous"
        with self.assertRaises(RecordError):
            collector.validate_config()


if __name__ == "__main__":
    unittest.main()
