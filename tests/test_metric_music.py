import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hub.metric_music import collect_music
from hub.metrics import validate_metric

NOW = "2026-09-09T08:00:00Z"


class MusicMetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = dict(schema_version="1.0.0", project_id="demo", updated_at=NOW,
                            versions=["v1"], assets=[], runs=["r1"], exports=["exports/logic/v1"])
        self.run = dict(schema_version="1.0.0", run_id="r1", project_id=None,
                        provider_name="offline_vertical_slice", provider_version="0.1.0",
                        status="succeeded", started_at="2026-07-18T00:00:00Z",
                        completed_at="2026-07-18T00:00:01Z", prompt="PRIVATE PROMPT")
        self.save()

    def save(self):
        for relative, obj in (("demo/project.yaml", self.project), ("demo/runs/r1.json", self.run)):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(obj))

    def collect(self):
        result = collect_music(self.root, "music", NOW, {"data_root": str(self.root)})
        for row in result["metrics"]:
            validate_metric(row)
        return result, {m["metric_id"]: m for m in result["metrics"]}

    def test_registered_offline_run_counts_and_privacy(self):
        result, rows = self.collect()
        self.assertEqual(rows["music.runs_succeeded"]["value"], 1)
        self.assertEqual(rows["music.assets_registered"]["value"], 0)
        self.assertEqual(rows["music.run_duration_seconds"]["value"], 1)
        self.assertIsNone(rows["music.reviews_pending"]["value"])
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual(rows["music.run_succeeded"]["dimensions"]["provider"], "offline_vertical_slice")
        self.assertTrue(rows["music.runs_registered"]["source_ref"].startswith(str(self.root)))

    def test_unregistered_file_and_body_do_not_change_versions(self):
        first, _ = self.collect()
        self.run["prompt"] = "NEW PRIVATE PROMPT"
        self.save()
        (self.root / "demo/runs/unregistered.json").write_text("not JSON")
        second, _ = self.collect()
        self.assertEqual(first["source_version"], second["source_version"])

    def test_corrupt_run_preserves_manifest_counts(self):
        self.run["run_id"] = "wrong"
        self.save()
        _, rows = self.collect()
        self.assertEqual(rows["music.runs_registered"]["value"], 1)
        self.assertIsNone(rows["music.runs_succeeded"]["value"])
        self.assertEqual(rows["music.versions_registered"]["value"], 1)

    def test_status_and_time_fail_independently(self):
        self.run.update(status="failed", completed_at="invalid")
        self.save()
        result, rows = self.collect()
        self.assertEqual(rows["music.runs_failed"]["value"], 1)
        self.assertIsNone(rows["music.run_duration_seconds"]["value"])
        self.assertTrue(any(i["kind"] == "business_blocker" for i in result["issues"]))
        self.run["status"] = "unexpected"
        self.save()
        _, rows = self.collect()
        self.assertIsNone(rows["music.runs_failed"]["value"])
        self.assertEqual(rows["music.assets_registered"]["value"], 0)

    def test_duplicate_references_and_wrong_manifest_identity(self):
        self.project["runs"] = ["r1", "r1"]
        self.save()
        _, rows = self.collect()
        self.assertIsNone(rows["music.runs_registered"]["value"])
        self.assertEqual(rows["music.assets_registered"]["value"], 0)
        self.project["project_id"] = "another"
        self.save()
        _, rows = self.collect()
        self.assertIsNone(rows["music.projects_registered"]["value"])
        self.assertEqual(rows["music.catalog_complete"]["value"], 0)

    def test_missing_catalog_not_empty_and_symlink_not_followed(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        _, rows = self.collect()
        self.assertIsNone(rows["music.projects_registered"]["value"])
        result = collect_music(self.root, "music", NOW, {"data_root": str(self.root / "missing")})
        self.assertTrue(result["issues"])
        self.assertIsNone(next(m["value"] for m in result["metrics"] if m["metric_id"] == "music.projects_registered"))

    def test_budget_and_invalid_reference_do_not_open_payloads(self):
        self.project["runs"] = ["../private"]
        self.save()
        _, rows = self.collect()
        self.assertIsNone(rows["music.runs_registered"]["value"])
        with patch("hub.metric_music.MAX_BYTES", 1):
            _, rows = self.collect()
        self.assertIsNone(rows["music.projects_registered"]["value"])

    def test_changed_run_does_not_churn_independent_asset_metric(self):
        _, before = self.collect()
        self.run["status"] = "failed"
        self.save()
        _, after = self.collect()
        self.assertEqual(before["music.assets_registered"], after["music.assets_registered"])
        self.assertNotEqual(before["music.runs_failed"]["source_version"], after["music.runs_failed"]["source_version"])
