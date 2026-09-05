from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import test_hub_sources as source_fixtures
from hub.connection_manager_cli import main, validate_bundle
from hub.connection_records import content_hash
from hub.connection_refresh import RefreshLedger
from hub.connection_sources import SourceResolver


class RefreshCliTests(unittest.TestCase):
    def setUp(self):
        self.fixture = source_fixtures.SourceResolverTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        self.bundle = {"schema_version": "1.0", "kind": "connection_authority_bundle",
                       "manifest": self.fixture.manifest, "adapters": self.fixture.adapters,
                       "source_plan": self.fixture.plan}
        self.bundle["content_hash"] = content_hash(self.bundle)
        self.data = self.root / "data/design_governance"
        self.data.mkdir(exist_ok=True)
        self.write("authority-bundle-v1.json", self.bundle)
        self.write("connection_adapters.json", self.fixture.adapters)

    def write(self, filename, value):
        (self.data / filename).write_text(json.dumps(value), encoding="utf-8")

    def call(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(list(args), root=self.root)
        return code, json.loads(output.getvalue())

    def test_full_refresh_retry_and_offline_rebuild(self):
        code, result = self.call("refresh", "--request-id", "fixture-all")
        self.assertEqual(code, 2)
        self.assertEqual(result["request"]["status"], "FINISHED")
        self.assertEqual(len(result["projection"]["projects"]), 24)
        self.assertEqual(sum(p["last_success"] is not None for p in result["projection"]["projects"].values()), 23)
        with mock.patch.object(SourceResolver, "refresh", side_effect=AssertionError("retry must not re-read")):
            code, retry = self.call("refresh", "--request-id", "fixture-all")
        self.assertEqual(code, 2)
        self.assertEqual(retry["appended_project_ids"], [])
        self.assertEqual(result["projection"], retry["projection"])
        code, rebuilt = self.call("rebuild")
        self.assertEqual(code, 0)
        self.assertEqual(rebuilt["projects"], result["projection"]["projects"])

    def test_history_survives_current_registry_and_adapter_unavailability(self):
        self.call("refresh", "--request-id", "fixture-one", "--project", "declared-00")
        _, before = self.call("history")
        (self.root / "data/registry/external_projects.yaml").write_text("not: [valid")
        (self.data / "connection_adapters.json").unlink()
        code, history = self.call("history")
        self.assertEqual(code, 0)
        self.assertEqual(history["results"], before["results"])
        self.assertEqual(history["current_authority"]["state"], "unavailable")
        code, rebuilt = self.call("rebuild")
        self.assertEqual(code, 0)
        self.assertEqual(rebuilt["projects"]["declared-00"]["freshness"], "stale")
        self.assertTrue(rebuilt["projects"]["declared-00"]["authority_drift"])
        code, failed = self.call("refresh", "--request-id", "must-not-begin")
        self.assertEqual(code, 1)
        self.assertIn("drift", failed["message"])
        _, after = self.call("history")
        self.assertEqual(before["head"], after["head"])

    def test_absent_history_does_not_create_database(self):
        code, result = self.call("history")
        self.assertEqual(code, 1)
        self.assertFalse((self.data / "connection_refresh.sqlite3").exists())

    def test_corrupt_bundle_rejected_before_database_creation(self):
        self.bundle["content_hash"] = "0" * 64
        self.write("authority-bundle-v1.json", self.bundle)
        code, result = self.call("refresh", "--request-id", "invalid")
        self.assertEqual(code, 1)
        self.assertIn("hash", result["message"])
        self.assertFalse((self.data / "connection_refresh.sqlite3").exists())

    def test_multiple_frozen_versions_rebuild_and_continue(self):
        self.call("refresh", "--request-id", "old", "--project", "declared-00")
        next_bundle = copy.deepcopy(self.bundle)
        next_bundle["source_plan"]["id"] = "hub-source-plan-v2"
        plan = next_bundle["source_plan"]
        plan["content_hash"] = content_hash({k: v for k, v in plan.items() if k != "content_hash"})
        next_bundle["content_hash"] = content_hash({k: v for k, v in next_bundle.items() if k != "content_hash"})
        validate_bundle(next_bundle)
        self.write("authority-bundle-v2.json", next_bundle)
        prefix = ["--bundle", "data/design_governance/authority-bundle-v1.json",
                  "--bundle", "data/design_governance/authority-bundle-v2.json"]
        code, result = self.call(*prefix, "refresh", "--request-id", "new", "--project", "declared-00")
        self.assertEqual(code, 0, result)
        code, history = self.call(*prefix, "history")
        self.assertEqual(code, 0)
        self.assertEqual(len(history["results"]), 2)
        self.assertTrue(history["requests"][0]["authority_drift"])
        self.assertFalse(history["requests"][1]["authority_drift"])
        code, rebuilt = self.call(*prefix, "rebuild")
        self.assertEqual(code, 0, rebuilt)
        self.assertEqual(rebuilt["projects"]["declared-00"]["last_success"]["request_id"], "new")

    def test_mid_request_permission_drift_preserves_committed_project(self):
        original = SourceResolver.refresh

        def change_after_first(resolver, project_id):
            result = original(resolver, project_id)
            if project_id == "declared-00":
                for project in self.fixture.registry["projects"]:
                    if project["id"] == "desktop-magnet":
                        project["access_profile"] = "no_current_goal_access"
                (self.root / "data/registry/external_projects.yaml").write_text(
                    yaml.safe_dump(self.fixture.registry, sort_keys=False))
            return result

        with mock.patch.object(SourceResolver, "refresh", change_after_first):
            code, result = self.call("refresh", "--request-id", "mid-request-drift",
                                     "--project", "declared-00", "--project", "desktop-magnet")
        self.assertEqual(code, 2)
        self.assertEqual(result["request"]["status"], "FINISHED")
        projects = result["projection"]["projects"]
        self.assertIsNotNone(projects["declared-00"]["last_success"])
        self.assertFalse(projects["desktop-magnet"]["latest_attempt"]["success"])
        self.assertIsNone(projects["desktop-magnet"]["last_success"])
        code, history = self.call("history")
        self.assertEqual(code, 0)
        self.assertEqual(len(history["results"]), 2)
        self.assertEqual(history["results"][1]["result"]["sources"], [])

    def test_offline_project_root_is_distinct_from_missing_named_file(self):
        project = next(p for p in self.fixture.registry["projects"] if p["id"] == "declared-00")
        path = Path(project["root_path"])
        path.rename(path.with_name("offline-fixture"))
        code, result = self.call("refresh", "--request-id", "offline-root", "--project", "declared-00")
        self.assertEqual(code, 2)
        code, history = self.call("history")
        self.assertEqual(code, 0)
        failure = history["results"][0]["result"]
        self.assertEqual(failure["disposition"], "SOURCE_UNAVAILABLE")
        self.assertEqual(failure["errors"][0]["code"], "SOURCE_UNAVAILABLE")
        self.assertEqual(failure["sources"], [])


if __name__ == "__main__":
    unittest.main()
