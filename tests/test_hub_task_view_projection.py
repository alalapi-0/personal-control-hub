"""Synthetic, portable acceptance coverage for the pure refactor projection."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hub.business_tasks import validate_task_plan
from hub.connection_records import RecordError, content_hash
from hub.connection_extensions import validate_task_nodes  # Load before the no-I/O guard.
from hub.task_view_projection import (
    build_refactor_task_view, refactor_view_input_version, validate_refactor_task_view,
)


def synthetic_inputs():
    catalog = {
        "task_id": "PCH-REFACTOR-20261014", "plan_version": "1.0.0-20261014",
        "baseline": {"task_count": 111, "current_task_count": 111},
        "rounds": [{"id": f"R{i}", "title": f"Synthetic round {i}"} for i in range(7)],
        "tasks": [{"id": f"leaf-{i:03d}", "phase": f"P{i % 7}", "round": f"R{i % 7}",
                   "title": f"Synthetic leaf {i}", "dependencies": [],
                   "expected_result": f"Synthetic result {i}", "test_method": f"Synthetic check {i}"}
                  for i in range(111)],
    }
    sha = content_hash(catalog)
    task = {"task_id": catalog["task_id"], "plan_version": catalog["plan_version"],
            "task_catalog": "synthetic-catalog.yaml", "artifact_sha256": {"synthetic-catalog.yaml": sha},
            "task_results": {}, "active_task_id": "leaf-000",
            "progress": {"current_total": 111, "baseline_total": 111, "verified_total": 0,
                         "basis": "Synthetic current leaf tasks only"},
            "p0_preservation": {"status": "preserved", "original": ["synthetic-P0-input"]}}
    return task, catalog, sha


class RefactorTaskViewProjectionTests(unittest.TestCase):
    def test_only_leaves_count_and_p0_inputs_are_preserved_and_detached(self):
        task, catalog, sha = synthetic_inputs()
        original_task, original_catalog = deepcopy(task), deepcopy(catalog)
        view = build_refactor_task_view(task, catalog, sha)
        self.assertEqual(111, view["counts"]["total"])
        self.assertEqual(111, view["counts"]["not_started"])
        self.assertEqual(111, view["baseline_denominator"])
        self.assertEqual(126, len(view["nodes"]))  # One project, seven stages, seven rounds.
        self.assertEqual(7, sum(n["kind"] == "stage" for n in view["nodes"]))
        self.assertEqual(7, sum(n["kind"] == "round" for n in view["nodes"]))
        self.assertEqual(111, len(view["acceptance"]))
        self.assertEqual([t["id"] for t in catalog["tasks"]], view["history"]["baseline_leaf_ids"])
        self.assertEqual([], view["history"]["changes"])
        self.assertEqual(original_task, task)
        self.assertEqual(original_catalog, catalog)
        self.assertEqual(16, sum(t["phase"] == "P0" for t in catalog["tasks"]))
        view["nodes"][-1]["dependencies"].append("detached-output")
        view["history"]["baseline_leaf_ids"].clear()
        self.assertEqual(original_task, task)
        self.assertEqual(original_catalog, catalog)

    def test_verification_requires_explicit_pass_and_unknown_i_h_do_not_promote(self):
        task, catalog, sha = synthetic_inputs()
        task["task_results"] = {
            "leaf-000": {"verification": "passed", "evidence_ref": ["synthetic/evidence-0"]},
            "leaf-001": {"verification": "PASS", "implementation": "prose says completed",
                         "human_acceptance": "accepted", "evidence_ref": ["synthetic/evidence-1"]},
            "leaf-002": {"verification": "pending", "implementation": "complete"},
            "leaf-003": {"verification": "failed", "blocker": "Synthetic blocker"},
            "leaf-004": {"cancelled": True},
        }
        task["progress"]["verified_total"] = 1
        view = build_refactor_task_view(task, catalog, sha)
        rows = {row["task_id"]: row for row in view["acceptance"]}
        self.assertEqual(1, view["counts"]["verified"])
        self.assertIsNone(view["counts"]["implemented"])
        self.assertIsNone(view["counts"]["human"])
        self.assertTrue(rows["leaf-000"]["verified"])
        self.assertTrue(rows["leaf-000"]["implemented"])
        self.assertIsNone(rows["leaf-000"]["human"])
        self.assertFalse(rows["leaf-001"]["verified"])
        self.assertIsNone(rows["leaf-001"]["implemented"])
        self.assertIsNone(rows["leaf-001"]["human"])
        self.assertTrue(rows["leaf-002"]["implemented"])
        self.assertFalse(rows["leaf-002"]["verified"])
        self.assertIn("Synthetic blocker", rows["leaf-003"]["unknown_reason"])
        self.assertEqual({"active": 2, "blocked": 1, "cancelled": 1, "not_started": 106},
                         {k: view["counts"][k] for k in ("active", "blocked", "cancelled", "not_started")})
        task["task_results"]["leaf-001"]["human_acceptance"] = True
        with self.assertRaises(RecordError):
            build_refactor_task_view(task, catalog, sha)

    def test_frozen_catalog_identity_denominators_and_versions_reject_conflicts(self):
        for case in ("sha", "current_total", "catalog_current", "baseline_total", "catalog_baseline",
                     "task_version", "both_versions", "task_identity", "catalog_identity", "duplicate_leaf", "v_total"):
            task, catalog, sha = synthetic_inputs()
            with self.subTest(case=case):
                if case == "sha":
                    sha = "b" * 64
                elif case == "current_total":
                    task["progress"]["current_total"] = 110
                elif case == "catalog_current":
                    catalog["baseline"]["current_task_count"] = 110
                elif case == "baseline_total":
                    task["progress"]["baseline_total"] = 110
                elif case == "catalog_baseline":
                    catalog["baseline"]["task_count"] = 110
                elif case == "task_version":
                    task["plan_version"] = "2.0"
                elif case == "both_versions":
                    task["plan_version"] = catalog["plan_version"] = "2.0"
                elif case == "task_identity":
                    task["task_id"] = "OTHER"
                elif case == "catalog_identity":
                    catalog["task_id"] = "OTHER"
                elif case == "duplicate_leaf":
                    catalog["tasks"][-1]["id"] = catalog["tasks"][0]["id"]
                else:
                    task["progress"]["verified_total"] = 1
                with self.assertRaises(RecordError):
                    build_refactor_task_view(task, catalog, sha)

    def test_each_canonical_input_change_makes_the_published_view_stale(self):
        task, catalog, sha = synthetic_inputs()
        view = build_refactor_task_view(task, catalog, sha)
        validate_refactor_task_view(task, view)
        for case in ("result", "active", "total", "baseline", "verified", "basis", "sha", "plan", "identity"):
            changed = deepcopy(task)
            with self.subTest(case=case):
                if case == "result":
                    changed["task_results"]["leaf-000"] = {"implementation": "in_progress"}
                elif case == "active":
                    changed["active_task_id"] = "leaf-001"
                elif case in ("total", "baseline", "verified"):
                    key = {"total": "current_total", "baseline": "baseline_total", "verified": "verified_total"}[case]
                    changed["progress"][key] += 1
                elif case == "basis":
                    changed["progress"]["basis"] = "Different declared basis"
                elif case == "sha":
                    changed["artifact_sha256"][changed["task_catalog"]] = "c" * 64
                elif case == "plan":
                    changed["plan_version"] = "2.0"
                else:
                    changed["task_id"] = "OTHER"
                with self.assertRaises(RecordError):
                    validate_refactor_task_view(changed, view)
        self.assertEqual(view["input_version"], refactor_view_input_version(task, sha))

    def test_conditions_and_evidence_versions_are_preserved_with_explicit_precedence(self):
        task, catalog, sha = synthetic_inputs()
        task["task_results"] = {
            "leaf-000": {"verification": "passed", "main_commit": "synthetic-main-version",
                         "candidate_id": "synthetic-candidate-version", "evidence_ref": "synthetic/evidence-0"},
            "leaf-001": {"verification": "passed", "candidate_id": "synthetic-candidate-version",
                         "human_acceptance": False, "evidence_ref": ["synthetic/evidence-1"]},
            "leaf-002": {"verification": "passed", "evidence_ref": ["synthetic/evidence-2"]},
        }
        task["progress"]["verified_total"] = 3
        view = build_refactor_task_view(task, catalog, sha)
        nodes = {n["id"]: n for n in view["nodes"]}
        rows = {r["task_id"]: r for r in view["acceptance"]}
        for i in range(3):
            leaf_id = f"leaf-{i:03d}"
            condition = f"Synthetic result {i}\n验收方法：Synthetic check {i}"
            self.assertEqual(condition, nodes[leaf_id]["condition"])
            self.assertEqual(content_hash(condition), rows[leaf_id]["condition_version"])
            self.assertEqual([f"synthetic/evidence-{i}"], rows[leaf_id]["evidence_refs"])
            self.assertEqual(rows[leaf_id]["evidence_refs"], nodes[leaf_id]["evidence"])
        self.assertEqual("synthetic-main-version", rows["leaf-000"]["evidence_version"])
        self.assertEqual("synthetic-candidate-version", rows["leaf-001"]["evidence_version"])
        self.assertEqual(content_hash(task["task_results"]["leaf-002"]), rows["leaf-002"]["evidence_version"])
        self.assertIs(rows["leaf-001"]["human"], False)
        with self.subTest(case="verified without evidence"):
            task["task_results"]["leaf-002"]["evidence_ref"] = []
            with self.assertRaises(RecordError):
                build_refactor_task_view(task, catalog, sha)

    def test_typed_valid_leaf_acceptance_swap_is_rejected_against_canonical_results(self):
        task, catalog, sha = synthetic_inputs()
        task["task_results"] = {
            "leaf-000": {"verification": "passed", "implementation": "complete",
                         "human_acceptance": False, "candidate_id": "synthetic-verified-version",
                         "evidence_ref": ["synthetic/verified-evidence"]},
            "leaf-001": {"verification": "pending", "implementation": "in_progress",
                         "human_acceptance": False, "candidate_id": "synthetic-pending-version",
                         "evidence_ref": ["synthetic/pending-evidence"]},
        }
        task["progress"]["verified_total"] = 1
        view = build_refactor_task_view(task, catalog, sha)
        swapped = deepcopy(view)
        rows = {row["task_id"]: row for row in swapped["acceptance"]}
        nodes = {node["id"]: node for node in swapped["nodes"]}
        for field in ("implemented", "verified", "human", "evidence_version", "evidence_refs"):
            rows["leaf-000"][field], rows["leaf-001"][field] = rows["leaf-001"][field], rows["leaf-000"][field]
        for field in ("status", "evidence"):
            nodes["leaf-000"][field], nodes["leaf-001"][field] = nodes["leaf-001"][field], nodes["leaf-000"][field]
        self.assertEqual(view["counts"], swapped["counts"])
        self.assertEqual(view["input_version"], swapped["input_version"])
        self.assertEqual(view["catalog_sha256"], swapped["catalog_sha256"])
        for leaf_id in ("leaf-000", "leaf-001"):
            original = next(row for row in view["acceptance"] if row["task_id"] == leaf_id)
            self.assertEqual(original["task_id"], rows[leaf_id]["task_id"])
            self.assertEqual(original["condition_version"], rows[leaf_id]["condition_version"])
        # The forged leaf mapping remains schema-valid, with unchanged V/N and
        # legitimate condition fingerprints. Only canonical per-leaf comparison
        # can distinguish it from the projection actually produced by State.
        self.assertEqual(swapped, validate_task_plan(swapped))
        with self.assertRaises(RecordError):
            validate_refactor_task_view(task, swapped)

    def test_projection_is_pure_deterministic_and_input_version_has_no_self_hash(self):
        task, catalog, sha = synthetic_inputs()
        before = deepcopy((task, catalog))
        with patch("builtins.open", side_effect=AssertionError("projection performed I/O")), \
             patch.object(Path, "open", side_effect=AssertionError("projection performed path I/O")):
            first = build_refactor_task_view(task, catalog, sha)
            second = build_refactor_task_view(task, catalog, sha)
            validate_refactor_task_view(task, first)
        self.assertEqual(first, second)
        self.assertEqual(before, (task, catalog))
        self.assertEqual(refactor_view_input_version(task, sha), first["input_version"])
        self.assertNotEqual(content_hash(first), first["input_version"])
        self.assertNotIn("sha256", first)
        self.assertNotIn("self_hash", first)
        first["labels"]["leaf-000"] = "Output-only presentation mutation"
        self.assertEqual(refactor_view_input_version(task, sha), second["input_version"])
        changed = deepcopy(task)
        changed["task_results"]["leaf-000"] = {"note": "New canonical input"}
        self.assertNotEqual(refactor_view_input_version(changed, sha), second["input_version"])


if __name__ == "__main__":
    unittest.main()
