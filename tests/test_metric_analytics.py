import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from hub.metric_analytics import aggregate_current, observation_change
from hub.metric_cli import main
from hub.metric_store import MetricStore
from hub.metrics import AGGREGATE_RULES, metric, metric_catalog, metric_contract_schema, metric_definition


AT = "2026-09-09T00:00:00Z"
LATER = "2026-09-10T00:00:01Z"


class MetricAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = MetricStore(Path(self.tmp.name))

    def row(self, value=2, unit="pages", name="work.done", project="p", **extra):
        return metric(project, name, value, unit, "facts.json#done", str(value), AT,
                      counting_basis="Current unique item IDs",
                      reason="Missing" if value is None else None, **extra)

    def save(self, request, rows, project="p"):
        self.store.begin(request, [project], "code/input-v1")
        return self.store.save(request, dict(
            project_id=project, observed_at=AT, disposition="partial",
            metrics=rows, metric_definitions=metric_catalog(rows),
            issues=[], source_versions={}))

    def test_net_delta_is_never_throughput(self):
        change = observation_change(
            dict(self.row(4), observed_at=LATER),
            dict(self.row(2), observed_at=AT),
            changed_at=LATER,
        )
        self.assertEqual(change["delta"], 2)
        self.assertEqual(change["change_kind"], "net_delta")
        self.assertIsNone(change["change_per_second"])
        self.assertIsNone(change["throughput"])
        self.assertFalse(change["throughput_claimed"])
        self.assertGreater(change["observation_interval_seconds"], 0)

    def test_pages_and_chapters_stay_separate(self):
        pages = self.row(2, "pages")
        chapters = self.row(10, "chapters")
        result = aggregate_current([pages, chapters])
        units = {group["unit"] for group in result["groups"]}
        self.assertEqual(units, {"pages", "chapters"})
        self.assertEqual(sum(group["value"] for group in result["groups"]), 12)
        self.assertTrue(all(group["throughput"] is None for group in result["groups"]))

    def test_unknown_is_not_rewritten_to_zero(self):
        result = aggregate_current([self.row(None)])
        self.assertIsNone(result["groups"][0]["value"])
        self.assertEqual(result["groups"][0]["quality"], "unknown")
        self.assertIn("not rewritten to zero", result["groups"][0]["reason"])

    def test_ratio_keeps_numerator_and_denominator(self):
        bare = self.row(50, "percent", name="work.pass_rate")
        result = aggregate_current([bare])
        self.assertIn("ratios_keep_numerator_denominator", {item["rule"] for item in result["rejected"]})
        self.assertIsNone(result["groups"][0]["value"])
        paired = self.row(50, "percent", name="work.pass_rate",
                          dimensions={"numerator": 1, "denominator": 2})
        kept = aggregate_current([paired])
        self.assertEqual(kept["groups"][0]["value"], 50)
        self.assertEqual(kept["groups"][0]["dimensions"]["numerator"], 1)

    def test_percentiles_are_not_averaged(self):
        first = self.row(10, "p95_ms", name="work.latency_p95", project="a")
        second = self.row(20, "p95_ms", name="work.latency_p95", project="b")
        result = aggregate_current([first, second])
        # Different projects share the same metric key identity only when project_id matches.
        # Cross-project same unit still must not be averaged by this helper: keys differ.
        self.assertTrue(all(group["value"] in {10, 20} for group in result["groups"]))
        same = aggregate_current([
            metric("a", "work.latency_p95", 10, "p95_ms", "a", "1", AT,
                   counting_basis="Window p95", dimensions={"scope": "api"}),
            metric("a", "work.latency_p95", 20, "p95_ms", "b", "2", AT,
                   counting_basis="Window p95", dimensions={"scope": "api"}),
        ])
        self.assertIn("no_mean_of_percentiles", {item["rule"] for item in same["rejected"]})
        self.assertIsNone(same["groups"][0]["value"])

    def test_cost_unknown_is_not_estimated(self):
        result = aggregate_current([self.row(None, "usd", name="work.spend")])
        self.assertIsNone(result["groups"][0]["value"])
        self.assertIn("cost_from_telemetry_only", {item["rule"] for item in result["rejected"]})

    def test_store_aggregate_and_cli_are_bounded(self):
        self.save("one", [self.row(2), self.row(3, "chapters")])
        page = self.store.aggregate()
        self.assertEqual(page["rules"], list(AGGREGATE_RULES))
        self.assertEqual(page["distribution"]["groups"], 2)
        self.assertTrue(all(group["throughput"] is None for group in page["groups"]))
        self.assertEqual(metric_contract_schema()["aggregate_rules"], list(AGGREGATE_RULES))
        output = io.StringIO()
        with patch("hub.metric_cli.PROJECT_ROOT", Path(self.tmp.name)), redirect_stdout(output):
            code = main(["--root", self.tmp.name, "aggregate"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["kind"], "metric_aggregate_page")
        self.assertLessEqual(len(output.getvalue().encode()), 8192)


if __name__ == "__main__":
    unittest.main()
