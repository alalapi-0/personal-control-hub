import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hub.connection_records import METRIC_QUALITY_SEMANTICS, RecordError
from hub.metric_collect import (
    MetricCollector,
    collect_declared,
    metric_source_contract_schema,
    validate_metric_source_config,
)
from hub.metric_store import MetricStore
from hub.metric_sources import metadata_path
from hub.metrics import (
    METRIC_DEFINITION_FIELDS,
    METRIC_FIELDS,
    bounded_json,
    metric,
    metric_catalog,
    metric_contract_schema,
    metric_definition,
    metric_key,
    validate_metric_definition,
    validate_metric,
)

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
                  metrics=rows, metric_definitions=metric_catalog(rows), issues=[], source_versions={}))

    def test_history_delta_idempotency_and_restart(self):
        first = self.save("first", [self.row()])
        self.assertEqual(first["changed_metrics"], 1)
        self.store = MetricStore(self.root)
        self.assertEqual(self.store.receipt("first", "p"), first)
        self.assertEqual(self.save("repeat", [self.row(observed=LATER)], LATER)["changed_metrics"], 0)
        self.save("change", [self.row(4, observed=LATER)], LATER)
        result = self.store.page(project_id="p")
        self.assertEqual(result["items"][0]["delta"], 2)
        self.assertIsNone(result["items"][0]["change_per_second"])
        self.assertIsNone(result["items"][0]["throughput"])
        self.assertFalse(result["items"][0]["throughput_claimed"])
        self.assertEqual(result["items"][0]["change_kind"], "net_delta")
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

    def test_quality_contract_distinguishes_five_outcomes(self):
        self.assertEqual(
            METRIC_QUALITY_SEMANTICS,
            {
                "good": "valid",
                "missing": "missing",
                "unknown": "unknown",
                "not_applicable": "not_applicable",
                "invalid": "error",
            },
        )
        valid = self.row()
        self.assertEqual(validate_metric(valid)["quality"], "good")
        for quality in ("missing", "unknown", "not_applicable", "invalid"):
            with self.subTest(quality=quality):
                row = dict(valid, value=None, quality=quality, reason=f"{quality} evidence")
                self.assertEqual(validate_metric(row)["quality"], quality)
        for row in (
            dict(valid, quality="missing"),
            dict(valid, value=None, quality="good", reason="missing"),
            dict(valid, value=None, quality="unknown", reason=None),
            dict(valid, value=None, quality="invented", reason="invalid enum"),
        ):
            with self.assertRaises(RecordError):
                validate_metric(row)

    def test_metric_definition_fields_are_separate_from_dynamic_facts(self):
        row = self.row()
        self.assertEqual(set(row), METRIC_FIELDS)
        definition = metric_definition(row)
        changed = dict(row, value=9, observed_at=LATER, source_version="changed")
        self.assertEqual(metric_definition(changed), definition)
        for invalid_row in (
            dict(row, dimensions={"scope": 1}),
            dict(row, dimensions={"bad key": "value"}),
        ):
            with self.assertRaises(RecordError):
                validate_metric(invalid_row)
        contract = metric_contract_schema()
        self.assertEqual(set(contract["record_fields"]), METRIC_FIELDS)
        self.assertEqual(set(contract["catalog_definition_fields"]), METRIC_DEFINITION_FIELDS)
        self.assertEqual(len(metric_catalog([row, changed])), 1)
        conflict = dict(changed, counting_basis="A different stable denominator")
        self.assertEqual(len(metric_catalog([row, conflict])), 2)

        counter = metric_definition(
            row,
            metric_kind="counter",
            counter_reset="window",
            aggregation="sum",
            window={"kind": "rolling", "timezone": "UTC", "size_seconds": 86400},
        )
        self.assertEqual(validate_metric_definition(counter)["metric_kind"], "counter")
        for invalid in (
            dict(definition, counter_reset="never"),
            dict(counter, counter_reset="not_applicable"),
            dict(definition, aggregation="weighted_ratio"),
            dict(definition, window={"kind": "rolling", "timezone": "UTC", "size_seconds": None}),
            dict(definition, metric_kind=[]),
            dict(definition, dimension_keys=[[]]),
        ):
            with self.assertRaises(RecordError):
                validate_metric_definition(invalid)

    def test_metric_source_declarations_reject_dynamic_or_unknown_facts(self):
        source = {
            "path": "facts.json",
            "business_time": ["observed_at"],
            "metrics": [
                {
                    "id": "work.done",
                    "unit": "items",
                    "selector": ["done"],
                    "counting_basis": "Unique completed item IDs",
                    "dimensions": {"scope": "current"},
                }
            ],
        }
        config = {
            "schema_version": "1.0",
            "projects": {"p": {"adapter": "declared", "sources": [source]}},
        }
        self.assertIs(validate_metric_source_config(config, {"p": {}}), config)
        mutations = (
            lambda value: value.update(observed_at=AT),
            lambda value: value["projects"]["p"].update(value=99),
            lambda value: value["projects"]["p"].update(adapter=[]),
            lambda value: value["projects"]["p"]["sources"][0].update(status="complete"),
            lambda value: value["projects"]["p"]["sources"][0]["metrics"][0].update(value=99),
            lambda value: value["projects"]["p"]["sources"][0]["metrics"][0].update(operation=[]),
            lambda value: value["projects"]["p"]["sources"][0]["metrics"][0]["dimensions"].update(status="complete"),
            lambda value: value["projects"]["p"]["sources"][0]["metrics"][0]["dimensions"].update(scope="not a stable id"),
        )
        for mutate in mutations:
            candidate = copy.deepcopy(config)
            mutate(candidate)
            with self.assertRaises(RecordError):
                validate_metric_source_config(candidate, {"p": {}})
        for field in ("value", "status", "time", "version", "source_version"):
            candidate = copy.deepcopy(config)
            candidate["projects"]["p"]["sources"][0]["metrics"][0]["dimensions"][field] = "dynamic"
            with self.subTest(dimension=field), self.assertRaises(RecordError):
                validate_metric_source_config(candidate, {"p": {}})
        protected_paths = (
            "auth.yaml",
            "api_key.json",
            "passwords.yaml",
            "client-secret.json",
            "auth/data.json",
            "api_keys/data.json",
            "private_keys/data.json",
            "nested/password/store.json",
            "api key/data.json",
            "private key/data.json",
            "service account/data.json",
            "client secret/data.json",
            "access token/data.json",
        )
        for path in protected_paths:
            candidate = copy.deepcopy(config)
            candidate["projects"]["p"]["sources"][0]["path"] = path
            with self.subTest(path=path), self.assertRaises(RecordError):
                validate_metric_source_config(candidate, {"p": {}})
            with self.subTest(runtime_path=path), self.assertRaises(RecordError):
                metadata_path(self.root, path)
        for component in (
            "auth", "api_keys", "private_keys", "password", "api key",
            "private key", "service account", "client secret", "access token",
        ):
            root_config = {
                "schema_version": "1.0",
                "projects": {
                    "p": {
                        "adapter": "jav_batch",
                        "data_root": str(self.root / component),
                    }
                },
            }
            with self.subTest(root=component), self.assertRaises(RecordError):
                validate_metric_source_config(root_config, {"p": {}})
            with self.subTest(runtime_root=component), self.assertRaises(RecordError):
                metadata_path(self.root / component, "data.json")

        report = {
            "schema_version": "1.0",
            "projects": {
                "p": {
                    "adapter": "validation_runs",
                    "validation_reports": [
                        {"id": "saved", "path": "report.json", "format": "feature_report"}
                    ],
                }
            },
        }
        validate_metric_source_config(report, {"p": {}})
        report["projects"]["p"]["validation_reports"][0]["status"] = "PASS"
        with self.assertRaises(RecordError):
            validate_metric_source_config(report, {"p": {}})
        report["projects"]["p"]["validation_reports"][0].pop("status")
        report["projects"]["p"]["validation_reports"][0]["format"] = []
        with self.assertRaises(RecordError):
            validate_metric_source_config(report, {"p": {}})
        self.assertIn("value", metric_source_contract_schema()["forbidden_dynamic_fact_fields"])

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
