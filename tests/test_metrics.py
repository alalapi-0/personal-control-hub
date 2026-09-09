import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hub.connection_records import RecordError
from hub.metric_collect import MetricCollector, collect_declared
from hub.metric_store import MetricStore
from hub.metrics import bounded_json, metric, metric_key, validate_metric

AT = "2026-09-09T00:00:00Z"
LATER = "2026-09-10T00:00:01Z"


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = MetricStore(self.root)

    def row(self, value=2, unit="pages", observed=AT, name="work.done"):
        return metric("p", name, value, unit, "run.json#done", str(value), observed,
                      counting_basis="Current unique item IDs", reason="Missing" if value is None else None)

    def save(self, request, rows, observed=AT):
        self.store.begin(request, ["p"], "code/input-v1")
        return self.store.save(request, dict(project_id="p", observed_at=observed, disposition="partial",
                  metrics=rows, issues=[], source_versions={}))

    def test_history_delta_idempotency_and_restart(self):
        first = self.save("first", [self.row()])
        self.assertEqual(first["changed_metrics"], 1)
        self.store = MetricStore(self.root)
        self.assertEqual(self.store.receipt("first", "p"), first)
        self.assertEqual(self.save("repeat", [self.row(observed=LATER)], LATER)["changed_metrics"], 0)
        self.save("change", [self.row(4, observed=LATER)], LATER)
        result = self.store.page(project_id="p")
        self.assertEqual(result["items"][0]["delta"], 2)
        self.assertEqual(result["items"][0]["metric"]["observed_at"], LATER)
        self.assertEqual(self.store.validate()["metric_versions"], 2)
        with self.assertRaises(RecordError):
            self.store.begin("first", ["p"], "changed-code")

    def test_units_never_mix_and_unknown_does_not_become_zero(self):
        self.save("initial", [self.row(2), self.row(10, "chapters")])
        self.save("partial", [self.row(None), self.row(11, "chapters")])
        items = self.store.page(project_id="p")["items"]
        rows = {r["metric"]["unit"]: r for r in items}
        self.assertIsNone(rows["pages"]["delta"])
        self.assertEqual(rows["chapters"]["delta"], 1)
        self.assertEqual(len(self.store.page(history=True)["items"]), 4)

    def test_field_failure_isolation_and_strict_numbers(self):
        (self.root / "facts.json").write_text(json.dumps({"a": 8, "b": "bad", "at": AT}))
        sources = [{"path": "facts.json", "business_time": ["at"], "metrics": [
            {"id": "work." + key, "unit": "pages", "selector": [key], "counting_basis": "Unique IDs"}
            for key in ("a", "b")]}]
        result = collect_declared(self.root, "p", AT, sources)
        self.assertEqual([r["value"] for r in result["metrics"]], [8, None])
        for value in (True, float("nan"), float("inf")):
            invalid = self.row()
            invalid["value"] = value
            with self.assertRaises(RecordError):
                validate_metric(invalid)

    def test_bounded_paging_coverage_and_retention(self):
        rows = [self.row(name="work." + str(i)) for i in range(60)]
        self.save("many", rows)
        cursor, found = 0, []
        while cursor is not None:
            page = self.store.page(after=cursor, limit=50)
            self.assertLessEqual(len(bounded_json(page).encode()), 8192)
            found.extend(r["sequence"] for r in page["items"])
            cursor = page["next_cursor"]
        self.assertEqual(len(set(found)), 60)
        self.assertEqual(len(found), 60)
        self.save("more", [self.row(5, name="work." + str(i)) for i in range(60)])
        self.save("last", [self.row(6, name="work." + str(i)) for i in range(60)])
        self.assertEqual(self.store.prune(LATER)["deleted_metric_versions"], 60)
        self.assertEqual(self.store.validate()["metric_versions"], 120)
        registry = {"projects": [{"id": "p"}, {"id": "missing"}, {"id": "removed", "current_state_status": "removed_local"}]}
        result = self.store.coverage(registry, now=LATER)
        self.assertEqual(result["coverage"]["registered"], 3)
        self.assertEqual(result["coverage"]["removed_local"], 1)
        self.assertEqual(result["projects"][0]["freshness"], "stale")

    def config(self):
        directory = self.root / "data/registry"
        directory.mkdir(parents=True)
        project = {"id": "removed", "name": "Removed", "enabled": True,
                   "connection_read_allowed": True, "current_state_status": "removed_local",
                   "root_path": "/must-never-be-probed"}
        (directory / "external_projects.yaml").write_text(json.dumps({"projects": [project]}))
        (self.root / "data/connections/metric_sources.yaml").write_text(json.dumps({"schema_version": "1.0", "projects": {}}))

    def test_removed_never_visits_root_and_restart_never_recollects(self):
        self.config()
        collector = MetricCollector(self.root, clock=lambda: AT)
        with patch("hub.metric_collect.Path", side_effect=AssertionError("root touched")):
            result = collector.collect("removed")
        self.assertEqual(result["disposition"], "removed_local")
        collector.refresh(self.store, "removed-1")
        with patch.object(collector, "collect", side_effect=AssertionError("recollected")):
            collector.refresh(self.store, "removed-1")


if __name__ == "__main__":
    unittest.main()
