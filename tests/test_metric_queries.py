"""Regression checks for query authority, observation time and bounded output."""
from pathlib import Path
from contextlib import redirect_stdout
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from hub.connection_records import RecordError
from hub.metric_store import MetricStore
from hub.metrics import bounded_json, metric, validate_metric

EARLY = "2026-09-08T00:00:00Z"
LATE = "2026-09-09T00:00:00Z"
BETWEEN = "2026-09-08T12:00:00Z"


class MetricQueryRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = MetricStore(Path(self.tmp.name))

    def save(self, request, observed):
        row = metric("p", "work.pages", 12, "pages", "facts.json#pages", "v1", observed,
                     counting_basis="Unique current page IDs")
        self.store.begin(request, ["p"], "test-identity")
        self.store.save(request, {"project_id": "p", "observed_at": observed,
            "disposition": "resolved", "metrics": [row], "issues": [], "source_versions": {}})
        return row

    def test_revoked_and_removed_reads_are_explicitly_historical(self):
        self.save("initial", EARLY)
        for changed, expected in (
            ({"connection_read_allowed": False}, "disabled"),
            ({"enabled": False}, "disabled"),
            ({"access_profile": "no_current_goal_access"}, "disabled"),
            ({"current_state_status": "removed_local"}, "removed_local"),
        ):
            project = {"id": "p", "enabled": True, "connection_read_allowed": True, **changed}
            if changed == {"enabled": False}:
                project.pop("connection_read_allowed")
            with self.subTest(changed=changed):
                page = self.store.page(current_projects={"p": project}, now=LATE)
                self.assertEqual(page["returned"], 1)
                entry = page["items"][0]
                self.assertEqual(entry["freshness"], expected)
                self.assertEqual(entry["metric"]["observed_at"], EARLY)
                self.assertEqual(entry["metric"]["value"], 12)
                coverage = self.store.coverage({"projects": [project]}, now=LATE)
                self.assertEqual(coverage["coverage"]["dispositions"][expected], 1)
        explicit_permission = {"id": "p", "enabled": False, "connection_read_allowed": True}
        entry = self.store.page(current_projects={"p": explicit_permission}, now=LATE)["items"][0]
        self.assertEqual(entry["freshness"], "fresh")
        self.assertEqual(entry["metric"]["value"], 12)

    def test_current_since_uses_latest_observation_without_fake_history(self):
        self.save("initial", EARLY)
        self.save("same-value-later", LATE)
        page = self.store.page(since=BETWEEN)
        self.assertEqual(page["returned"], 1)
        self.assertEqual(page["items"][0]["metric"]["observed_at"], LATE)
        self.assertEqual(self.store.page(history=True, since=BETWEEN)["returned"], 0)
        self.assertEqual(self.store.validate()["metric_versions"], 1)

    def test_oversize_single_metric_rejected_before_paging(self):
        row = self.save("initial", EARLY)
        row.update(source_ref="文" * 1000, source_version="版" * 1000,
                   counting_basis="章" * 1000)
        with self.assertRaises(RecordError):
            validate_metric(row)
        with self.assertRaises(RecordError):
            self.store.begin("oversize", ["p"], "test-identity")
            self.store.save("oversize", {"project_id": "p", "observed_at": LATE,
                "disposition": "resolved", "metrics": [row], "issues": [], "source_versions": {}})
        self.assertEqual(self.store.page()["returned"], 1)

    def test_large_registry_summary_is_bounded_and_paginated(self):
        self.save("initial", EARLY)
        registry = {"projects": [{"id": "p"}] + [{"id": "project-" + str(i)} for i in range(99)]}
        first = self.store.coverage(registry, now=LATE)
        self.assertLessEqual(len(bounded_json(first).encode()), 8192)
        self.assertEqual(first["projects_total"], 100)
        self.assertEqual(first["coverage"]["registered"], 100)
        self.assertEqual(len(first["projects"]), 10)
        found = [r["project_id"] for r in first["projects"]]
        cursor = first["next_cursor"]
        while cursor is not None:
            page = self.store.coverage(registry, now=LATE, after=cursor, limit=10)
            self.assertLessEqual(len(bounded_json(page).encode()), 8192)
            self.assertEqual(page["coverage"], first["coverage"])
            found.extend(r["project_id"] for r in page["projects"])
            next_cursor = page["next_cursor"]
            if next_cursor is not None:
                self.assertGreater(next_cursor, cursor)
            cursor = next_cursor
        self.assertEqual(len(found), 100)
        self.assertEqual(len(set(found)), 100)
        from hub.metric_cli import main
        output = io.StringIO()
        with patch("hub.metric_cli.load_registry_at", return_value=registry), redirect_stdout(output):
            exit_code = main(["--root", self.tmp.name, "summary", "--after", "10", "--limit", "7"])
        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["coverage"]["registered"], 100)
        self.assertEqual(len(payload["projects"]), 7)
        self.assertEqual(payload["next_cursor"], 17)
        self.assertLessEqual(len(output.getvalue().encode()), 8192)


if __name__ == "__main__":
    unittest.main()
