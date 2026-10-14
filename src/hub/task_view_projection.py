"""Rebuild only the PCH refactor's display projection from its existing authority.

No I/O or writer lives here. The owner of STATE.pch_refactor supplies the frozen
catalog and task_results and includes this view in the same material update.
SourceResolver only checks the published view against already read State inputs.
"""
from __future__ import annotations

from copy import deepcopy

from .connection_records import content_hash, require
from .business_tasks import validate_task_plan

PROJECT = "personal-control-hub"


def refactor_view_input_version(task: dict, catalog_sha256: str) -> str:
    return content_hash({"catalog_sha256": catalog_sha256,
                         "task_id": task["task_id"], "plan_version": task["plan_version"],
                         "task_results": task["task_results"],
                         "active_task_id": task.get("active_task_id"),
                         "progress": {key: task["progress"][key] for key in
                                      ("current_total", "baseline_total", "verified_total", "basis")}})


def _refs(result: dict) -> list[str]:
    value = result.get("evidence_ref", [])
    return [value] if type(value) is str else deepcopy(value)


def _canonical_leaf(task: dict, task_id: str, condition: str) -> tuple[str, list[str], dict]:
    result = task["task_results"].get(task_id)
    verified = bool(result and result.get("verification") == "passed")
    require(not (result and result.get("cancelled") is True and verified),
            "cancelled canonical result cannot remain verified")
    status = ("complete" if verified else "not_started" if result is None else
              "cancelled" if result.get("cancelled") is True else
              "blocked" if result.get("blocker") else "active")
    implemented = (True if verified else False if result is None else
                   True if result.get("implementation") == "complete" else
                   False if result.get("implementation") in {"not_started", "in_progress"} else None)
    human = result.get("human_acceptance") if result else None
    human = human if type(human) is bool else None
    refs = _refs(result) if result else []
    version = (result.get("main_commit") or result.get("candidate_id")
               or content_hash(result)) if result else None
    reasons = (["The canonical task result does not publish boolean implementation/human acceptance."]
               if implemented is None or human is None else [])
    if result and result.get("blocker"):
        require(type(result["blocker"]) is str, "canonical blocker reason must be explicit text")
        reasons.append("未通过原因：" + result["blocker"])
    row = {"task_id": task_id, "plan_version": task["plan_version"],
           "condition_version": content_hash(condition), "implemented": implemented,
           "verified": verified, "human": human, "evidence_version": version, "evidence_refs": refs,
           "unknown_reason": " ".join(reasons) if reasons else None}
    return status, refs, row


def build_refactor_task_view(task: dict, catalog: dict, catalog_sha256: str) -> dict:
    """Mechanical projection; prose implementation/human claims stay unknown."""
    require(task["task_id"] == catalog["task_id"] == "PCH-REFACTOR-20261014", "wrong refactor identity")
    require(task["plan_version"] == catalog["plan_version"], "refactor plan version differs")
    require(task["artifact_sha256"][task["task_catalog"]] == catalog_sha256,
            "frozen refactor catalog changed; explicit version/history decision required")
    definitions = catalog["tasks"]
    ids = [entry["id"] for entry in definitions]
    require(len(ids) == len(set(ids)) == catalog["baseline"]["current_task_count"]
            == task["progress"]["current_total"], "refactor denominator differs from catalog")
    require(catalog["baseline"]["task_count"] == task["progress"]["baseline_total"],
            "refactor baseline differs")
    # This is the actual unchanged baseline. New versions must supply explicit
    # identity history rather than silently regenerating a smaller baseline.
    require(task["plan_version"] == "1.0.0-20261014" and len(ids) == 111,
            "new refactor version requires explicit denominator history")
    nodes = [{"id": PROJECT, "parent": None, "kind": "project", "dependencies": [],
              "status": "active", "condition": None, "evidence": []}]
    labels = {PROJECT: "PCH 重构计划"}
    stages, rounds, acceptance = set(), set(), []
    round_names = {row["id"]: row["title"] for row in catalog["rounds"]}
    for entry in definitions:
        stage, round_id = "stage-" + entry["phase"], "round-" + entry["round"]
        if stage not in stages:
            stages.add(stage)
            nodes.append({"id": stage, "parent": PROJECT, "kind": "stage", "dependencies": [],
                          "status": "unknown", "condition": None, "evidence": []})
            labels[stage] = "阶段 " + entry["phase"]
        if round_id not in rounds:
            rounds.add(round_id)
            nodes.append({"id": round_id, "parent": stage, "kind": "round", "dependencies": [],
                          "status": "unknown", "condition": None, "evidence": []})
            labels[round_id] = entry["round"] + " · " + round_names[entry["round"]]
        condition = entry["expected_result"] + "\n验收方法：" + entry["test_method"]
        status, refs, row = _canonical_leaf(task, entry["id"], condition)
        nodes.append({"id": entry["id"], "parent": round_id, "kind": "leaf",
                      "dependencies": deepcopy(entry["dependencies"]), "status": status,
                      "condition": condition, "evidence": deepcopy(refs)})
        labels[entry["id"]] = entry["title"]
        acceptance.append(row)
    leaves = [node for node in nodes if node["kind"] == "leaf"]
    counts = {"total": len(leaves)}
    for field in ("implemented", "verified", "human"):
        values = [row[field] for row in acceptance]
        counts[field] = None if None in values else sum(value is True for value in values)
    counts.update({status: sum(node["status"] == status for node in leaves)
                   for status in ("not_started", "active", "blocked", "cancelled")})
    require(counts["verified"] == task["progress"]["verified_total"], "refactor V differs from accepted task_results")
    view = {"schema_version": "1.0", "project_id": PROJECT, "plan_version": task["plan_version"],
            "unit": "refactor_leaf_task", "counting_basis": task["progress"]["basis"],
            "catalog_sha256": catalog_sha256, "input_version": refactor_view_input_version(task, catalog_sha256),
            "nodes": nodes, "labels": labels, "acceptance": acceptance, "counts": counts,
            "baseline_denominator": task["progress"]["baseline_total"],
            "history": {"baseline_plan_version": task["plan_version"],
                        "baseline_leaf_ids": ids, "changes": []}, "history_unknown_reason": None}
    return validate_task_plan(view)


def validate_refactor_task_view(task: dict, view: dict) -> None:
    """Reject stale views using only the already read State, never the catalog."""
    validate_task_plan(view)
    require(view["catalog_sha256"] == task["artifact_sha256"][task["task_catalog"]]
            and view["input_version"] == refactor_view_input_version(task, view["catalog_sha256"]),
            "refactor task projection is stale relative to canonical inputs")
    require(view["plan_version"] == task["plan_version"]
            and view["counts"]["verified"] == task["progress"]["verified_total"]
            and view["counts"]["total"] == task["progress"]["current_total"],
            "refactor task projection differs from current State")
    leaves = {node["id"]: node for node in view["nodes"] if node["kind"] == "leaf"}
    require(set(task["task_results"]) <= set(leaves), "canonical result is missing from task projection")
    acceptance = {row["task_id"]: row for row in view["acceptance"]}
    for task_id, node in leaves.items():
        status, refs, row = _canonical_leaf(task, task_id, node["condition"])
        require(node["status"] == status and node["evidence"] == refs and acceptance[task_id] == row,
                "task projection leaf differs from canonical result")
