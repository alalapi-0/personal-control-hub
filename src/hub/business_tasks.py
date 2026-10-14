"""Pure source business-plan validation; no provider tasks, I/O or inferred facts."""
from __future__ import annotations

from copy import deepcopy

from .connection_records import content_hash, exact, fingerprint, identifier, require, string

LIMIT = 10000
COUNT_FIELDS = {"total", "implemented", "verified", "human", "not_started", "active", "blocked", "cancelled"}
PLAN_FIELDS = {"schema_version", "project_id", "plan_version", "unit", "counting_basis",
               "catalog_sha256", "input_version", "nodes", "labels", "acceptance", "counts",
               "baseline_denominator", "history", "history_unknown_reason"}
ACCEPTANCE_FIELDS = {"task_id", "plan_version", "condition_version", "implemented", "verified",
                     "human", "evidence_version", "evidence_refs", "unknown_reason"}
CHANGE_FIELDS = {"from_plan_version", "to_plan_version", "kind", "reason", "before_leaf_ids",
                 "after_leaf_ids", "mapping", "evidence_refs"}


def _array(value, label):
    require(type(value) is list and len(value) <= LIMIT, f"{label}: bounded list required")


def _ids(value, label):
    _array(value, label)
    for item in value:
        identifier(item, label)
    require(len(value) == len(set(value)), f"{label}: duplicate identity")
    return set(value)


def _refs(value, label, *, nonempty=False):
    _array(value, label)
    require(not nonempty or bool(value), f"{label}: evidence required")
    for ref in value:
        string(ref, label)


def _count(value):
    require(value is None or type(value) is int and value >= 0, "count: nonnegative integer or null required")


def _history(value, leaves):
    history = value["history"]
    if history is None:
        string(value["history_unknown_reason"], "history unknown reason")
        return
    require(value["history_unknown_reason"] is None, "known history cannot have unknown reason")
    exact(history, {"baseline_plan_version", "baseline_leaf_ids", "changes"}, "plan history")
    current_version = history["baseline_plan_version"]
    string(current_version, "baseline plan version")
    current_ids = _ids(history["baseline_leaf_ids"], "baseline leaf")
    require(value["baseline_denominator"] == len(current_ids), "baseline denominator mismatch")
    seen_versions = {current_version}
    cancelled_ids = set()
    _array(history["changes"], "history changes")
    for change in history["changes"]:
        exact(change, CHANGE_FIELDS, "history change")
        for field in ("from_plan_version", "to_plan_version", "reason"):
            string(change[field], field)
        require(change["from_plan_version"] == current_version, "history version chain gap")
        require(change["to_plan_version"] not in seen_versions, "history version must advance without reuse")
        before = _ids(change["before_leaf_ids"], "before leaf")
        after = _ids(change["after_leaf_ids"], "after leaf")
        require(before == current_ids, "history leaf chain gap")
        kind = change["kind"]
        require(type(kind) is str and kind in {"add", "remove", "split", "cancel", "revise"}, "invalid history kind")
        removed, added = before - after, after - before
        if kind == "add":
            require(not removed and bool(added), "add must only add identities")
        elif kind == "remove":
            require(bool(removed) and not added, "remove must only remove identities")
        elif kind == "split":
            require(bool(removed) and len(added) >= 2, "split needs old and multiple new identities")
        else:
            require(before == after, "cancel/revise must retain identities")
        _array(change["mapping"], "history mapping")
        require(bool(change["mapping"]), "history mapping required")
        mapped_old, mapped_new = set(), set()
        for mapping in change["mapping"]:
            exact(mapping, {"old_ids", "new_ids"}, "history identity mapping")
            old_ids = _ids(mapping["old_ids"], "old mapped leaf")
            new_ids = _ids(mapping["new_ids"], "new mapped leaf")
            require(old_ids <= before and new_ids <= after, "mapped identities outside change")
            require(not old_ids & mapped_old and not new_ids & mapped_new, "mapping repeats identity")
            mapped_old.update(old_ids)
            mapped_new.update(new_ids)
            if kind == "add":
                require(not old_ids and bool(new_ids), "add mapping must identify new leaves")
            elif kind == "remove":
                require(bool(old_ids) and not new_ids, "remove mapping must identify old leaves")
            elif kind == "split":
                require(bool(old_ids) and len(new_ids) >= 2, "split mapping needs multiple new leaves")
            else:
                require(bool(old_ids) and old_ids == new_ids, "cancel/revise mapping must preserve identities")
        if kind in {"add", "remove", "split"}:
            require(mapped_old == removed and mapped_new == added, "mapping must cover changed identities exactly")
        if kind == "cancel":
            cancelled_ids.update(mapped_old)
        elif kind == "revise":
            # Only a declared revision of those same IDs releases cancellation.
            cancelled_ids.difference_update(mapped_old)
        cancelled_ids.intersection_update(after)
        _refs(change["evidence_refs"], "change evidence", nonempty=True)
        current_version, current_ids = change["to_plan_version"], after
        seen_versions.add(current_version)
    require(current_version == value["plan_version"] and current_ids == set(leaves),
            "history does not reach current plan and leaf set")
    require(all(leaves[task_id]["status"] == "cancelled" for task_id in cancelled_ids),
            "effective history cancellation requires current cancelled status")


def validate_task_plan(value) -> dict:
    """Validate source-declared plans and return a detached, unchanged value."""
    exact(value, PLAN_FIELDS, "task plan")
    require(value["schema_version"] == "1.0", "unsupported task plan schema")
    identifier(value["project_id"], "plan project")
    for field in ("plan_version", "unit", "counting_basis"):
        string(value[field], field)
    for field in ("catalog_sha256", "input_version"):
        fingerprint(value[field], field)
    _array(value["nodes"], "plan nodes")
    # Import lazily: connection_extensions dispatches optional task-plan facts.
    from .connection_extensions import validate_task_nodes
    normalized = deepcopy(value["nodes"])
    for node in normalized:
        if type(node) is dict and node.get("status") == "cancelled":
            node["status"] = "paused"
    validate_task_nodes(normalized)
    nodes = {node["id"]: node for node in value["nodes"]}
    roots = [node for node in nodes.values() if node["kind"] == "project"]
    require(not nodes or len(roots) == 1 and roots[0]["parent"] is None,
            "plan needs exactly one project root")
    require(not nodes or roots[0]["id"] == value["project_id"], "project root identity mismatch")
    parent_kinds = {"stage": "project", "round": "stage", "leaf": "round"}
    for node in nodes.values():
        if node["kind"] == "project":
            require(node["parent"] is None, "project root cannot have parent")
        else:
            require(node["parent"] in nodes and nodes[node["parent"]]["kind"] == parent_kinds[node["kind"]],
                    "plan hierarchy must be project/stage/round/leaf")
    leaves = {key: node for key, node in nodes.items() if node["kind"] == "leaf"}
    require(type(value["labels"]) is dict and set(value["labels"]) == set(nodes), "exhaustive plan labels required")
    for label in value["labels"].values():
        string(label, "task label")
    exact(value["counts"], COUNT_FIELDS, "plan counts")
    for count in [*value["counts"].values(), value["baseline_denominator"]]:
        _count(count)
    total = value["counts"]["total"]
    require(total is None or total == len(leaves), "known denominator must count leaves")
    for key in COUNT_FIELDS - {"total"}:
        count = value["counts"][key]
        require(total is None or count is None or count <= total, "count exceeds known denominator")
    _array(value["acceptance"], "leaf acceptance")
    acceptance = {}
    for row in value["acceptance"]:
        exact(row, ACCEPTANCE_FIELDS, "leaf acceptance")
        identifier(row["task_id"], "acceptance task")
        require(row["task_id"] in leaves and row["task_id"] not in acceptance, "acceptance must identify unique current leaf")
        acceptance[row["task_id"]] = row
        string(row["plan_version"], "acceptance plan version")
        fingerprint(row["condition_version"], "acceptance condition version")
        for field in ("implemented", "verified", "human"):
            require(row[field] is None or type(row[field]) is bool, "acceptance must be boolean or null")
        if any(row[field] is None for field in ("implemented", "verified", "human")):
            string(row["unknown_reason"], "acceptance unknown reason")
        elif row["unknown_reason"] is not None:
            string(row["unknown_reason"], "acceptance unknown reason")
        _refs(row["evidence_refs"], "leaf evidence")
        if row["evidence_version"] is not None:
            string(row["evidence_version"], "evidence version")
        if any(row[field] is True for field in ("implemented", "verified", "human")):
            require(row["plan_version"] == value["plan_version"]
                    and row["condition_version"] == content_hash(leaves[row["task_id"]]["condition"]),
                    "acceptance evidence is not applicable to current plan/condition")
        if row["verified"] is True:
            string(leaves[row["task_id"]]["condition"], "verified leaf acceptance condition")
            require(row["implemented"] is True and bool(row["evidence_refs"])
                    and row["evidence_version"] is not None, "verified leaf requires implementation and versioned evidence")
        require(row["human"] is not True or row["verified"] is True, "human acceptance requires verification")
        if leaves[row["task_id"]]["status"] == "cancelled":
            require(row["verified"] is not True and row["human"] is not True, "cancelled leaf cannot count as accepted")
    require(set(acceptance) == set(leaves), "every current leaf needs acceptance")
    for field in ("implemented", "verified", "human"):
        values = [row[field] for row in acceptance.values()]
        expected = None if None in values else sum(item is True for item in values)
        require(value["counts"][field] == expected, "acceptance aggregate mismatch")
    for status in ("not_started", "active", "blocked", "cancelled"):
        expected = (None if any(node["status"] == "unknown" for node in leaves.values())
                    else sum(node["status"] == status for node in leaves.values()))
        require(value["counts"][status] == expected, "leaf status aggregate mismatch")
    _history(value, leaves)
    return deepcopy(value)


def task_plan_progress(value) -> dict:
    plan = validate_task_plan(value)
    numerator, denominator = plan["counts"]["verified"], plan["counts"]["total"]
    reason = ("unknown_count" if numerator is None or denominator is None
              else "not_applicable" if denominator == 0 else None)
    return {"numerator": numerator, "denominator": denominator,
            "ratio": numerator / denominator if reason is None else None, "unknown_reason": reason,
            **{key: plan[key] for key in ("unit", "plan_version", "counting_basis", "baseline_denominator")}}
