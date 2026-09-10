import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hub.connection_records import RecordError, record_schema
from hub.metric_cli import main
from hub.metric_collect import MetricCollector
from hub.metric_store import MetricStore
from hub.metrics import bounded_json, issue, validate_metric

NOW = "2026-09-09T08:00:00Z"


class MetricDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = MetricStore(self.root)
        repo = self.root / "project"
        repo.mkdir()
        (repo / "facts.json").write_text('{"done":3}')
        (self.root / "data/registry").mkdir(parents=True)
        projects = [dict(id="p", name="Project", enabled=True, connection_read_allowed=True, root_path=str(repo)),
                    dict(id="removed", name="Removed", enabled=True, connection_read_allowed=True,
                         current_state_status="removed_local", root_path="/must-not-probe-removed"),
                    dict(id="disabled", name="Disabled", enabled=False, connection_read_allowed=False,
                         root_path="/must-not-probe-disabled")]
        (self.root / "data/registry/external_projects.yaml").write_text(json.dumps({"projects": projects, "cloud_excluded_count": 17}))
        spec = {"adapter": "declared", "github_repository": "fixture/repo", "sources": [{"path": "facts.json", "metrics": [
            {"id": "work.done", "unit": "chapters", "selector": ["done"], "counting_basis": "Declared completed chapters"}]}]}
        (self.root / "data/connections/metric_sources.yaml").write_text(json.dumps({"schema_version": "1.0", "projects": {"p": spec}}))
        self.git = patch("hub.metric_git.collect_git", return_value=dict(metrics=[], issues=[], disposition="resolved", source_version="git-fixture"))
        self.git.start()
        self.addCleanup(self.git.stop)

    def test_default_is_local_and_option_changes_request_identity(self):
        local = MetricCollector(self.root, clock=lambda: NOW)
        with patch("hub.metric_remote.collect_remote", side_effect=AssertionError("network collector invoked")):
            local.refresh(self.store, "local", ["p"])
        remote = MetricCollector(self.root, clock=lambda: NOW, remote_git=True)
        self.assertNotEqual(local.identity, remote.identity)
        with self.assertRaises(RecordError):
            remote.refresh(self.store, "local", ["p"])

    def test_removed_disabled_skip_roots_and_remote(self):
        collector = MetricCollector(self.root, clock=lambda: NOW, remote_git=True, github_ci=True)
        with patch("hub.metric_collect.Path", side_effect=AssertionError("root inspected")), \
             patch("hub.metric_remote.collect_remote", side_effect=AssertionError("remote queried")):
            self.assertEqual(collector.collect("removed")["disposition"], "removed_local")
            self.assertEqual(collector.collect("disabled")["disposition"], "disabled")

    def test_remote_error_preserves_business_values_and_disposition(self):
        collector = MetricCollector(self.root, clock=lambda: NOW, github_ci=True)
        failed = dict(metrics=[], issues=[issue("p", "github_request_failed", "github:github_ci")],
                      disposition="partial", source_version="denied")
        with patch("hub.metric_remote.collect_remote", return_value=failed) as remote:
            result = collector.collect("p")
        self.assertTrue(remote.call_args.kwargs["github_ci"])
        self.assertFalse(remote.call_args.kwargs["remote_git"])
        self.assertEqual(result["metrics"][0]["value"], 3)
        self.assertEqual(result["disposition"], "partial")

    def test_collector_exports_one_bound_catalog_not_row_copies(self):
        collector = MetricCollector(self.root, clock=lambda: NOW)
        result = collector.collect("p")
        self.assertEqual(len(result["metrics"]), 1)
        self.assertEqual(len(result["metric_definitions"]), 1)
        definition = result["metric_definitions"][0]
        self.assertNotIn("value", definition)
        self.assertNotIn("observed_at", definition)
        self.assertNotIn("metric_kind", result["metrics"][0])

        self.store.begin("catalog", ["p"], collector.identity)
        tampered = copy.deepcopy(result)
        tampered["metric_definitions"][0]["display_name"] = "forged"
        with self.assertRaises(RecordError):
            self.store.save("catalog", tampered)
        missing = copy.deepcopy(result)
        del missing["metric_definitions"]
        with self.assertRaises(RecordError):
            self.store.save("catalog", missing)
        self.assertEqual(self.store.save("catalog", result)["metric_count"], 1)

    def test_feishu_mapping_and_example_are_local_disabled_bounded(self):
        output = io.StringIO()
        with patch("subprocess.Popen", side_effect=AssertionError("external process")), redirect_stdout(output):
            code = main(["--root", str(self.root), "feishu"])
        self.assertEqual(code, 0)
        data = json.loads(output.getvalue())
        self.assertFalse(data["enabled"])
        self.assertFalse(data["write_back_allowed"])
        self.assertEqual(data["network_calls"], 0)
        validate_metric(data["sample"])
        self.assertIsNone(data["sample"]["value"])
        self.assertEqual(data["coverage"]["metrics"], 0)
        self.assertEqual(data["coverage"]["registered"], 3)
        self.assertLessEqual(len(bounded_json(data).encode()), 8192)

    def test_metric_cli_schema_is_the_shared_executable_contract(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["schema"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), record_schema()["metric_contract"])
