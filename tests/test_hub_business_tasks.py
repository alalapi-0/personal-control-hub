"""Business leaf evidence and explicit denominator history stay source-grounded."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hub.business_tasks import task_plan_progress, validate_task_plan
from hub.connection_records import RecordError, content_hash
from hub.connection_extensions import validate_task_nodes


def plan(leaf_ids=("a", "b"), version="P1"):
    def node(node_id, kind, parent, status="active"):
        return {"id": node_id, "kind": kind, "parent": parent, "dependencies": [],
                "status": status, "condition": "Check " + node_id, "evidence": []}
    nodes = [node("business", "project", None), node("s", "stage", "business"), node("r", "round", "s")]
    nodes.extend(node(leaf_id, "leaf", "r", "not_started") for leaf_id in leaf_ids)
    rows = [{"task_id": n["id"], "plan_version": version, "condition_version": content_hash(n["condition"]),
             "implemented": False, "verified": False, "human": False, "evidence_version": None,
             "evidence_refs": [], "unknown_reason": None} for n in nodes if n["kind"] == "leaf"]
    return {"schema_version": "1.0", "project_id": "business", "plan_version": version,
            "unit": "business_leaf", "counting_basis": "Current version declared business leaves only",
            "catalog_sha256": "a" * 64, "input_version": "b" * 64,
            "nodes": nodes, "labels": {n["id"]: "Label " + n["id"] for n in nodes}, "acceptance": rows,
            "counts": {"total": len(leaf_ids), "implemented": 0, "verified": 0, "human": 0,
                       "not_started": len(leaf_ids), "active": 0, "blocked": 0, "cancelled": 0},
            "baseline_denominator": None, "history": None,
            "history_unknown_reason": "Source has not declared denominator history"}


def change(before, after, from_version, to_version, kind, mapping):
    return {"from_plan_version": from_version, "to_plan_version": to_version, "kind": kind,
            "reason": "Source explicitly changed the plan", "before_leaf_ids": list(before),
            "after_leaf_ids": list(after), "mapping": mapping, "evidence_refs": ["evidence/change-record"]}


class BusinessTaskPlanTests(unittest.TestCase):
    def assert_invalid(self, value):
        with self.assertRaises(RecordError):
            validate_task_plan(value)

    def test_detached_pure_plan_and_order_independent_graph(self):
        value = plan()
        value["nodes"].reverse()
        value["acceptance"].reverse()
        original = copy.deepcopy(value)
        validated = validate_task_plan(value)
        self.assertEqual(original, validated)
        validated["nodes"][0]["condition"] = "Changed detached output"
        self.assertEqual(original, value)
        self.assertEqual(0, task_plan_progress(value)["ratio"])

    def test_cycles_duplicates_missing_references_and_skipped_hierarchy_fail(self):
        cases = []
        duplicate = plan()
        duplicate["nodes"].append(copy.deepcopy(duplicate["nodes"][-1]))
        cases.append(duplicate)
        cycle = plan()
        cycle["nodes"][-2]["dependencies"] = ["b"]
        cycle["nodes"][-1]["dependencies"] = ["a"]
        cases.append(cycle)
        parent_cycle = plan()
        parent_cycle["nodes"][0]["parent"] = "r"
        cases.append(parent_cycle)
        missing = plan()
        missing["nodes"][-1]["dependencies"] = ["absent"]
        cases.append(missing)
        skipped = plan()
        skipped["nodes"][-1]["parent"] = "s"
        cases.append(skipped)
        extra_root = plan()
        extra_root["nodes"][1].update(kind="project", parent=None)
        cases.append(extra_root)
        no_root = plan()
        no_root["nodes"][0].update(kind="stage", parent="s")
        cases.append(no_root)
        for index, value in enumerate(cases):
            with self.subTest(index=index):
                self.assert_invalid(value)

    def test_stage_and_round_are_never_acceptance_or_count_units(self):
        value = plan()
        value["acceptance"][0]["task_id"] = "r"
        self.assert_invalid(value)
        value = plan()
        value["counts"]["total"] = len(value["nodes"])
        self.assert_invalid(value)
        value = plan()
        value["counts"]["active"] = 3  # Three non-leaf nodes are active.
        self.assert_invalid(value)
        value = plan()
        value["nodes"][-1]["kind"] = "provider"
        self.assert_invalid(value)

    def test_unknown_total_is_preserved_without_inference_and_zero_is_not_applicable(self):
        value = plan()
        value["counts"]["total"] = None
        result = task_plan_progress(value)
        self.assertIsNone(result["denominator"])
        self.assertIsNone(result["ratio"])
        self.assertEqual("unknown_count", result["unknown_reason"])
        empty = plan(())
        empty.update(nodes=[], labels={}, acceptance=[])
        result = task_plan_progress(empty)
        self.assertEqual(0, result["denominator"])
        self.assertIsNone(result["ratio"])
        self.assertEqual("not_applicable", result["unknown_reason"])
        empty["counts"]["total"] = None
        self.assertIsNone(task_plan_progress(empty)["denominator"])

    def test_known_verified_evidence_and_human_are_current_condition_specific(self):
        value = plan()
        row = value["acceptance"][0]
        row.update(implemented=True, verified=True, human=True, evidence_version="evidence-v1",
                   evidence_refs=["source-declared/test-result"])
        value["counts"].update(implemented=1, verified=1, human=1)
        progress = task_plan_progress(value)
        self.assertEqual(.5, progress["ratio"])
        self.assertEqual("business_leaf", progress["unit"])
        for mutation in ({"implemented": False}, {"evidence_version": None}, {"evidence_refs": []},
                         {"plan_version": "old-plan"}, {"condition_version": "0" * 64}):
            invalid = copy.deepcopy(value)
            invalid["acceptance"][0].update(mutation)
            with self.subTest(mutation=mutation):
                self.assert_invalid(invalid)
        invalid = plan()
        invalid["acceptance"][0]["human"] = True
        invalid["counts"]["human"] = 1
        self.assert_invalid(invalid)

    def test_each_null_acceptance_dimension_requires_unknown_aggregate_and_reason(self):
        for field in ("implemented", "verified", "human"):
            value = plan()
            value["acceptance"][0].update({field: None, "unknown_reason": "Source evidence pending"})
            value["counts"][field] = None
            with self.subTest(field=field):
                validate_task_plan(value)
                incorrect = copy.deepcopy(value)
                incorrect["counts"][field] = 0
                self.assert_invalid(incorrect)
                missing_reason = copy.deepcopy(value)
                missing_reason["acceptance"][0]["unknown_reason"] = None
                self.assert_invalid(missing_reason)
        value = plan()
        value["acceptance"][0].update(verified=None, unknown_reason="Verification not known")
        value["counts"]["verified"] = None
        self.assertIsNone(task_plan_progress(value)["ratio"])

    def test_cancelled_is_supported_only_here_and_cannot_contribute_verified_or_human(self):
        value = plan()
        value["nodes"][-1]["status"] = "cancelled"
        value["counts"].update(not_started=1, cancelled=1)
        validated = validate_task_plan(value)
        self.assertEqual("cancelled", validated["nodes"][-1]["status"])
        with self.assertRaises(RecordError):
            validate_task_nodes(value["nodes"])
        row = value["acceptance"][-1]
        row.update(implemented=True, verified=True, evidence_refs=["record"], evidence_version="current")
        value["counts"].update(implemented=1, verified=1)
        self.assert_invalid(value)

    def test_unknown_leaf_status_nulls_all_status_buckets_paused_and_complete_count_zero(self):
        value = plan()
        value["nodes"][-1]["status"] = "unknown"
        for status in ("not_started", "active", "blocked", "cancelled"):
            value["counts"][status] = None
        validate_task_plan(value)
        value["counts"]["active"] = 0
        self.assert_invalid(value)
        value = plan()
        value["nodes"][-1]["status"] = "paused"
        value["nodes"][-2]["status"] = "complete"
        value["counts"]["not_started"] = 0
        validate_task_plan(value)

    def history_plan(self):
        value = plan(("c", "d"), "P6")
        value["baseline_denominator"] = 1
        value["history_unknown_reason"] = None
        value["history"] = {"baseline_plan_version": "P1", "baseline_leaf_ids": ["a"], "changes": [
            change(["a"], ["a", "b"], "P1", "P2", "add", [{"old_ids": [], "new_ids": ["b"]}]),
            change(["a", "b"], ["a", "c", "d"], "P2", "P3", "split", [{"old_ids": ["b"], "new_ids": ["c", "d"]}]),
            change(["a", "c", "d"], ["c", "d"], "P3", "P4", "remove", [{"old_ids": ["a"], "new_ids": []}]),
            change(["c", "d"], ["c", "d"], "P4", "P5", "cancel", [{"old_ids": ["c"], "new_ids": ["c"]}]),
            change(["c", "d"], ["c", "d"], "P5", "P6", "revise", [{"old_ids": ["d"], "new_ids": ["d"]}])]
        }
        value["nodes"][-2]["status"] = "cancelled"
        value["counts"].update(not_started=1, cancelled=1)
        return value

    def test_complete_explicit_history_keeps_baseline_separate_from_current_denominator(self):
        value = self.history_plan()
        validate_task_plan(value)
        progress = task_plan_progress(value)
        self.assertEqual(1, progress["baseline_denominator"])
        self.assertEqual(2, progress["denominator"])
        self.assertEqual("P6", progress["plan_version"])

    def test_history_rejects_version_and_identity_chain_gaps_and_incomplete_mappings(self):
        cases = []
        value = self.history_plan()
        value["baseline_denominator"] = 2
        cases.append(value)
        value = self.history_plan()
        value["history"]["changes"][1]["from_plan_version"] = "not-previous"
        cases.append(value)
        value = self.history_plan()
        value["history"]["changes"][-1]["to_plan_version"] = "P1"
        cases.append(value)
        value = self.history_plan()
        value["history"]["changes"][1]["before_leaf_ids"] = ["b"]
        cases.append(value)
        value = self.history_plan()
        value["history"]["changes"][1]["mapping"][0]["new_ids"] = ["c"]
        cases.append(value)
        value = self.history_plan()
        value["history"]["changes"][0]["mapping"][0]["new_ids"] = ["a", "b"]
        cases.append(value)
        value = self.history_plan()
        value["history"]["changes"][-1]["evidence_refs"] = []
        cases.append(value)
        value = self.history_plan()
        value["history"]["changes"][-1]["reason"] = ""
        cases.append(value)
        for index, value in enumerate(cases):
            with self.subTest(index=index):
                self.assert_invalid(value)

    def test_cancellation_history_cannot_delete_ids_or_remap_to_different_leaf(self):
        value = self.history_plan()
        value["history"]["changes"][-2]["after_leaf_ids"] = ["d"]
        self.assert_invalid(value)
        value = self.history_plan()
        value["history"]["changes"][-2]["mapping"][0]["new_ids"] = ["d"]
        self.assert_invalid(value)

    def test_old_verified_acceptance_is_not_reusable_after_history_version_change(self):
        value = self.history_plan()
        row = value["acceptance"][-1]
        row.update(plan_version="P5", implemented=True, verified=True,
                   evidence_version="P5-evidence", evidence_refs=["old-evidence"])
        value["counts"].update(implemented=1, verified=1)
        self.assert_invalid(value)

    def test_strict_shells_types_bounds_and_unknown_history_reason(self):
        for mutation in ({"counts": {**plan()["counts"], "total": True}},
                         {"counts": {**plan()["counts"], "verified": -1}},
                         {"counts": {**plan()["counts"], "implemented": {"unit": "different", "value": 0}}},
                         {"counts": {**plan()["counts"], "other_unit": 0}},
                         {"history_unknown_reason": None}, {"catalog_sha256": "not-a-hash"},
                         {"labels": {"a": "missing labels"}}, {"unit": ""}, {"extra": "invalid"}):
            with self.subTest(mutation=mutation):
                self.assert_invalid({**plan(), **mutation})
        value = plan()
        value["nodes"] = [value["nodes"][0]] * 10001
        self.assert_invalid(value)
        value = plan()
        value["acceptance"].append(copy.deepcopy(value["acceptance"][0]))
        self.assert_invalid(value)
        value = plan()
        value["acceptance"] = []
        self.assert_invalid(value)

    def test_unchanged_known_history_and_explicit_unknown_baseline(self):
        value = plan()
        value.update(baseline_denominator=2, history_unknown_reason=None,
                     history={"baseline_plan_version": "P1", "baseline_leaf_ids": ["b", "a"], "changes": []})
        validate_task_plan(value)
        value["history"]["baseline_plan_version"] = "P0"
        self.assert_invalid(value)
        value = plan()
        value["baseline_denominator"] = 50  # Published baseline; history is explicitly unknown.
        self.assertEqual(50, task_plan_progress(value)["baseline_denominator"])

    def test_project_root_identity_cannot_claim_another_projects_plan(self):
        value = plan()
        value["nodes"][0]["id"] = "other-project"
        value["nodes"][1]["parent"] = "other-project"
        value["labels"]["other-project"] = value["labels"].pop("business")
        self.assert_invalid(value)

    def test_verified_leaf_cannot_use_null_condition_hash_as_acceptance(self):
        value = plan()
        value["nodes"][-2]["condition"] = None
        value["acceptance"][0].update(condition_version=content_hash(None), implemented=True,
                                       verified=True, evidence_version="current", evidence_refs=["record"])
        value["counts"].update(implemented=1, verified=1)
        self.assert_invalid(value)

    def test_effective_history_cancellation_cannot_implicitly_revive_verified_leaf(self):
        value = self.history_plan()
        value["nodes"][-2]["status"] = "complete"
        value["acceptance"][0].update(implemented=True, verified=True, evidence_version="P6-evidence",
                                       evidence_refs=["current-record"])
        value["counts"].update(cancelled=0, implemented=1, verified=1)
        self.assert_invalid(value)

    def test_explicit_current_revision_can_release_cancellation_for_mapped_id_only(self):
        value = self.history_plan()
        value["history"]["changes"].append(change(["c", "d"], ["c", "d"], "P6", "P7", "revise",
                                                 [{"old_ids": ["c"], "new_ids": ["c"]}]))
        value["plan_version"] = "P7"
        for row in value["acceptance"]:
            row["plan_version"] = "P7"
        value["nodes"][-2]["status"] = "complete"
        value["acceptance"][0].update(implemented=True, verified=True, evidence_version="P7-evidence",
                                       evidence_refs=["current-record"])
        value["counts"].update(cancelled=0, implemented=1, verified=1)
        validate_task_plan(value)
        self.assertEqual(.5, task_plan_progress(value)["ratio"])
        # A revision of the other ID must not release c's cancellation.
        value["history"]["changes"][-1]["mapping"] = [{"old_ids": ["d"], "new_ids": ["d"]}]
        self.assert_invalid(value)

    def test_removal_and_split_can_retire_cancelled_ids_without_reusing_old_acceptance(self):
        for kind, after, mapping in (
            ("remove", ["d"], [{"old_ids": ["c"], "new_ids": []}]),
            ("split", ["d", "e", "f"], [{"old_ids": ["c"], "new_ids": ["e", "f"]}]),
        ):
            with self.subTest(kind=kind):
                previous = self.history_plan()
                value = plan(after, "P7")
                value.update(baseline_denominator=1, history_unknown_reason=None,
                             history=previous["history"])
                value["history"]["changes"].append(change(["c", "d"], after, "P6", "P7", kind, mapping))
                validate_task_plan(value)


if __name__ == "__main__":
    unittest.main()
