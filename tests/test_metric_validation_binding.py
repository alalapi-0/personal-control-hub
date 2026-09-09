import builtins
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hub.metric_validation import collect_validation


class SelectedCodeBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for name in ("tool.py", "producer.py"):
            (self.root / name).write_text("# fixture " + name + "\n")
        self.spec = {"id": "saved-functional", "format": "feature_report",
                     "root": str(self.root), "path": "report.json",
                     "source_files": ["tool.py", "producer.py"], "test_scope": "offline_fixture"}
        files = {name: hashlib.sha256((self.root / name).read_bytes()).hexdigest()
                 for name in self.spec["source_files"]}
        self.report = {"schema_version": "tool.functional-result.v1", "project_id": "fixture",
                       "test_scope": "offline_fixture", "generated_at": "2026-09-09T01:00:00Z",
                       "environment": {"platform": "fixture", "python": "3.11"},
                       "git_commit": None, "status": "PASS",
                       "feature_results": [{"id": "unit", "status": "PASS"},
                                           {"id": "integration", "status": "PASS"}],
                       "code": {"files": files, "sha256": self.digest(files), "stable": True}}

    @staticmethod
    def digest(files):
        return hashlib.sha256((json.dumps(files, sort_keys=True, separators=(",", ":"),
                                          ensure_ascii=False) + "\n").encode()).hexdigest()

    def collect(self, reports=None, observed_at="2026-09-09T02:00:00Z"):
        (self.root / "report.json").write_text(json.dumps(self.report))
        with patch("hub.metric_validation._git_state", return_value=(None, None)):
            return collect_validation(self.root, "fixture", observed_at, reports or [self.spec])

    def row(self, result, metric_id, status=None, report_id="saved-functional"):
        rows = [row for row in result["metrics"] if row["metric_id"] == metric_id
                and row["dimensions"].get("status") == status
                and row["dimensions"]["report_id"] == report_id]
        self.assertEqual(len(rows), 1)
        return rows[0]

    def binding(self, result, expected, passed):
        for metric_id, value in (("validation.report.bound_to_selected_code", expected),
                                 ("validation.report.current_selected_code_passed", passed)):
            row = self.row(result, metric_id)
            self.assertEqual(row["value"], value)
            self.assertEqual(row["unit"], "boolean")
            self.assertEqual(row["dimensions"]["binding_scope"], "selected_source_files")

    def test_report_directory_does_not_supply_project_source_files(self):
        artifact_root = self.root / 'reports'
        artifact_root.mkdir()
        self.spec['root'] = str(artifact_root)
        (artifact_root / 'report.json').write_text(json.dumps(self.report))
        for name in self.spec['source_files']:
            (artifact_root / name).write_text('unrelated artifact file')
        result = self.collect()
        selected = next(r for r in result['metrics'] if r['metric_id'] == 'validation.report.bound_to_selected_code')
        self.assertEqual(selected['value'], 1)
        (artifact_root / 'tool.py').write_text('changed unrelated artifact')
        after = self.collect()
        current = next(r for r in after['metrics'] if r['metric_id'] == selected['metric_id'])
        self.assertEqual(current['source_version'], selected['source_version'])

    def test_declared_source_scope_rejects_escaping_or_ambiguous_paths(self):
        from hub.metric_collect import MetricCollector
        from hub.connection_records import RecordError
        collector = object.__new__(MetricCollector)
        collector.projects = {'fixture': {}}
        collector.config = {'schema_version': '1.0', 'projects': {'fixture':
            {'adapter': 'validation_runs', 'validation_reports': [self.spec]}}}
        collector.validate_config()
        for selected in ([], ['../tool.py'], ['/tool.py'], ['*.py'], ['tool.py', 'tool.py']):
            with self.subTest(selected=selected):
                self.spec['source_files'] = selected
                with self.assertRaises(RecordError):
                    collector.validate_config()

    def test_no_git_pass_binds_only_the_selected_sources(self):
        result = self.collect()
        self.binding(result, 1, 1)
        self.assertIsNone(self.row(result, "validation.report.bound_to_current_code")["value"])
        self.assertIsNone(self.row(result, "validation.report.commit_matches_head")["value"])

    def test_failure_preserves_independent_pass_count_and_business_issue(self):
        self.report["status"] = "FAIL"
        self.report["feature_results"][1]["status"] = "FAIL"
        result = self.collect()
        self.binding(result, 1, 0)
        self.assertEqual(self.row(result, "validation.checks", "PASS")["value"], 1)
        self.assertEqual(self.row(result, "validation.checks", "FAIL")["value"], 1)
        failures = [item for item in result["issues"] if item["code"] == "validation_fail"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["kind"], "business_blocker")
        self.assertEqual(failures[0]["updated_at"], self.report["generated_at"])

    def test_code_drift_does_not_rewrite_historical_checks(self):
        before = self.collect()
        (self.root / "tool.py").write_text("# changed after validation\n")
        after = self.collect(observed_at="2026-09-09T03:00:00Z")
        self.binding(after, 0, None)
        for metric_id, status in (("validation.checks.total", None), ("validation.checks", "PASS")):
            for field in ("value", "source_version", "business_at"):
                self.assertEqual(self.row(before, metric_id, status)[field],
                                 self.row(after, metric_id, status)[field])

    def test_invalid_manifest_digest_keeps_historical_counts(self):
        self.report["code"]["sha256"] = "0" * 64
        result = self.collect()
        self.binding(result, None, None)
        self.assertEqual(self.row(result, "validation.checks", "PASS")["value"], 2)

    def test_missing_overall_status_does_not_approve_saved_passes(self):
        self.report.pop("status")
        result = self.collect()
        self.binding(result, 1, None)
        self.assertEqual(self.row(result, "validation.checks", "PASS")["value"], 2)

    def test_report_cannot_expand_source_read_authority(self):
        extra = self.root / "undeclared.py"
        extra.write_text("# must not be read\n")
        self.report["code"]["files"]["undeclared.py"] = "f" * 64
        self.report["code"]["sha256"] = self.digest(self.report["code"]["files"])
        builtin_open, io_open = builtins.open, io.open

        def guarded(original):
            def open_file(file, *args, **kwargs):
                if isinstance(file, (str, bytes, Path)):
                    self.assertNotEqual(Path(file).resolve(), extra)
                return original(file, *args, **kwargs)
            return open_file

        with patch("builtins.open", side_effect=guarded(builtin_open)), \
                patch("io.open", side_effect=guarded(io_open)):
            result = self.collect()
        self.binding(result, None, None)
        self.assertEqual(self.row(result, "validation.checks", "PASS")["value"], 2)

    def test_critical_report_identity_failure_is_isolated(self):
        independent = dict(self.spec, id="independent", path="independent.json")
        (self.root / "independent.json").write_text(json.dumps(self.report))
        original_report = json.dumps(self.report)
        for field in ("schema_version", "project_id", "test_scope"):
            with self.subTest(field=field):
                self.report = json.loads(original_report)
                self.report[field] = "wrong"
                result = self.collect([self.spec, independent])
                self.binding(result, None, None)
                self.assertTrue(all(row["value"] is None for row in result["metrics"]
                                    if row["dimensions"]["report_id"] == self.spec["id"]))
                self.assertEqual(self.row(result, "validation.checks", "PASS", "independent")["value"], 2)
                self.assertEqual(self.row(result, "validation.report.current_selected_code_passed",
                                          report_id="independent")["value"], 1)

    def test_unknown_status_duplicate_or_missing_identity_cannot_approve(self):
        for records in ([{"id": "unit", "status": "UNKNOWN"}],
                        [{"id": "unit", "status": "PASS"}] * 2,
                        [{"status": "PASS"}], [{"id": "", "status": "PASS"}]):
            with self.subTest(records=records):
                self.report["feature_results"] = records
                result = self.collect()
                self.assertNotEqual(self.row(result, "validation.report.current_selected_code_passed")["value"], 1)


if __name__ == "__main__":
    unittest.main()
