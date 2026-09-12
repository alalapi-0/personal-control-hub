import json
from pathlib import Path
import tempfile
import unittest

from hub.metric_novel import CHAPTERS, EXPORT, STAGE, collect_novel

AT = "2026-09-09T00:00:00Z"


class NovelMetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.chapter = {"schema_version": 1, "generated_at": AT, "total_expected_chapters": 2,
            "chapters": {"ch-001": {"chapter_id": "ch-001", "draft_status": "complete"},
                         "ch-002": {"chapter_id": "ch-002", "draft_status": "partial"}}}
        self.write(CHAPTERS, self.chapter)
        self.write(EXPORT, {"schema": "consistency_final_export_v2", "generated_at": AT,
            "chapters_discovered": 2, "chapters_exported": 1,
            "chapters_incomplete": ["ch-002"], "chapters_missing": []})
        self.write(STAGE, {"run_id": "run_sample", "status": "in_progress"})
        self.progress_path = "workspace/runs/run_sample/run_progress.json"
        self.progress = {"schema_version": 1, "run_id": "run_sample", "status": "completed",
            "total_segments": 4, "completed_segments": 4, "pending_segments": 0, "updated_at": AT}
        self.write(self.progress_path, self.progress)

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def collect(self):
        result = collect_novel(self.root, "light-novel", AT)
        return result, {r["metric_id"].removeprefix("novel."): r for r in result["metrics"]}

    def test_scope_and_conflict(self):
        result, rows = self.collect()
        self.assertEqual(rows["chapters_indexed"]["value"], 2)
        self.assertEqual(rows["chapters_draft_complete"]["value"], 1)
        self.assertEqual(rows["batch_completed_segments"]["value"], 4)
        self.assertIsNone(rows["review_pending"]["value"])
        self.assertTrue(rows["review_pending"]["reason"])
        self.assertIn("novel_stage_progress_conflict", [p["code"] for p in result["issues"]])

    def test_relevant_change_only(self):
        _, before = self.collect()
        self.progress.update(completed_segments=3, pending_segments=1)
        self.write(self.progress_path, self.progress)
        _, after = self.collect()
        changed = {k for k in before if before[k] != after[k]}
        self.assertEqual(changed, {"batch_completed_segments", "batch_pending_segments"})

    def test_failure_isolation_and_bad_count(self):
        (self.root / EXPORT).write_text("broken")
        self.progress["pending_segments"] = 8
        self.write(self.progress_path, self.progress)
        _, rows = self.collect()
        self.assertEqual(rows["chapters_indexed"]["value"], 2)
        self.assertIsNone(rows["export_chapters_exported"]["value"])
        self.assertIsNone(rows["batch_total_segments"]["value"])
        self.assertEqual(rows["batch_failed"]["value"], 0)

    def test_invalid_identity_and_timestamp(self):
        self.chapter["chapters"]["ch-002"]["chapter_id"] = "ch-001"
        self.chapter["generated_at"] = "not-time"
        self.write(CHAPTERS, self.chapter)
        _, rows = self.collect()
        self.assertIsNone(rows["chapters_indexed"]["value"])
        self.assertEqual(rows["chapters_expected"]["value"], 2)
        self.assertIsNone(rows["chapters_expected"]["business_at"])

    def test_run_path_traversal_rejected(self):
        self.write(STAGE, {"run_id": "../../secret", "status": "completed"})
        _, rows = self.collect()
        self.assertIsNone(rows["batch_total_segments"]["value"])
        self.assertNotIn("..", rows["batch_total_segments"]["source_ref"])


if __name__ == "__main__":
    unittest.main()
