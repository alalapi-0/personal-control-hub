"""Optional v1.0 semantic extensions, independent of source2.0 and DTO1.0.

This module performs no I/O, scheduling, inference, or authority promotion. The
caller supplies observed source context; validation does not acquire evidence.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .connections import select_value
from .connection_records import (RecordError, content_hash, exact, fingerprint, identifier,
                                 require, string, timestamp, validate_selector)

VERSION = "1.0"
DOMAINS = {"fact", "managed", "derived", "proposed"}
FACT_FIELDS = {"task_nodes", "usable_versions", "core_capabilities", "fact_updated_at",
               "has_ui", "plan_version", "unit", "counting_basis", "progress_counts"}
MANAGED_FIELDS = {"planned_start", "planned_end", "priority", "watch", "order", "layout"}
COUNTS = {"total", "implemented", "verified", "human", "not_started", "active", "blocked"}
METADATA = {"unit", "plan_version", "counting_basis", "baseline_denominator"}


def _integer(value: Any) -> None:
    require(type(value) is int and value >= 0, "count: nonnegative integer required")


def _array(value: Any) -> None:
    require(type(value) is list and len(value) <= 10000, "extension: bounded list required")


def validate_task_nodes(nodes: Any) -> list:
    _array(nodes)
    by_id = {}
    for node in nodes:
        exact(node, {"id", "parent", "kind", "dependencies", "status", "condition", "evidence"}, "task node")
        identifier(node["id"], "task")
        require(node["id"] not in by_id, "task nodes: duplicate identity")
        by_id[node["id"]] = node
        require(type(node["kind"]) is str and node["kind"] in {"project", "stage", "round", "leaf"}, "task: invalid kind")
        require(type(node["status"]) is str and node["status"] in {"not_started", "active", "paused", "blocked", "complete", "unknown"}, "task: invalid status")
        if node["parent"] is not None:
            identifier(node["parent"], "parent")
        _array(node["dependencies"])
        for dependency in node["dependencies"]:
            identifier(dependency, "dependency")
        require(len(set(node["dependencies"])) == len(node["dependencies"]), "task: duplicate dependency")
        require(node["condition"] is None or type(node["condition"]) is str, "task: invalid condition")
        if node["condition"] is not None:
            string(node["condition"], "condition")
        _array(node["evidence"])
        for ref in node["evidence"]:
            string(ref, "task evidence")
    for node in nodes:
        for ref in [node["parent"], *node["dependencies"]]:
            require(ref is None or ref in by_id, "task: missing reference")
            require(ref != node["id"], "task: self reference")
        require(node["parent"] is None or by_id[node["parent"]]["kind"] != "leaf", "task: leaf cannot be parent")
    # Parent and dependency graphs are separate directed relations. A task may
    # depend on its parent without turning an ordinary hierarchy into a cycle.
    for relation in (lambda n: [n["parent"]] if n["parent"] else [], lambda n: n["dependencies"]):
        done, active = set(), set()
        for node_id in by_id:
            stack = [(node_id, False)]
            while stack:
                current, leaving = stack.pop()
                if leaving:
                    active.remove(current)
                    done.add(current)
                    continue
                require(current not in active, "task: cycle")
                if current in done:
                    continue
                active.add(current)
                stack.append((current, True))
                stack.extend((ref, False) for ref in relation(by_id[current]))
    return nodes


def _metric(metric: Any) -> None:
    exact(metric, METADATA | {"value", "unknown_reason"}, "count metric")
    for key in ("unit", "plan_version", "counting_basis"):
        string(metric[key], "count " + key)
    _integer(metric["baseline_denominator"])
    if metric["value"] is None:
        string(metric["unknown_reason"], "count unknown reason")
    else:
        _integer(metric["value"])
        require(metric["unknown_reason"] is None, "known count cannot have unknown reason")


def derive_progress(numerator: dict, denominator: dict) -> dict:
    """Derive a ratio only from counts sharing the complete source basis.

    Unknown counts remain None; a zero denominator is not applicable. Baseline
    is a versioned input, not a substitute for the current denominator.
    """
    _metric(numerator)
    _metric(denominator)
    require(all(numerator[k] == denominator[k] for k in METADATA), "progress: incompatible counting metadata")
    n, total = numerator["value"], denominator["value"]
    require(n is None or total is None or n <= total, "progress: numerator exceeds denominator")
    reason = ("unknown_count" if n is None or total is None else "not_applicable" if total == 0 else None)
    return {**{k: denominator[k] for k in METADATA}, "numerator": n, "denominator": total,
            "ratio": n / total if reason is None else None, "unknown_reason": reason}


def _versions(value: Any) -> None:
    _array(value)
    seen = set()
    for entry in value:
        exact(entry, {"version", "scope", "verified", "evidence"}, "usable version")
        string(entry["version"], "usable version")
        string(entry["scope"], "usable scope")
        require(type(entry["verified"]) is bool, "usable version: boolean verified required")
        require((entry["version"], entry["scope"]) not in seen, "usable version: duplicate scope/version")
        seen.add((entry["version"], entry["scope"]))
        _array(entry["evidence"])
        require(not entry["verified"] or bool(entry["evidence"]), "verified version needs evidence")
        for proof in entry["evidence"]:
            exact(proof, {"version", "scope", "status", "ref"}, "version evidence")
            require(proof["version"] == entry["version"] and proof["scope"] == entry["scope"], "version evidence: scope/version mismatch")
            require(type(proof["status"]) is str and proof["status"] in {"passed", "failed", "not_run", "pending"}, "version evidence: invalid status")
            require(not entry["verified"] or proof["status"] == "passed", "verified version requires passed evidence")
            string(proof["ref"], "version evidence ref")


def _fact_value(key: str, value: Any) -> None:
    if key == "task_nodes":
        validate_task_nodes(value)
    elif key in {"usable_versions", "core_capabilities"}:
        _versions(value)
    elif key == "fact_updated_at":
        timestamp(value, key)
    elif key == "has_ui":
        require(type(value) is bool, "has_ui: boolean required")
    elif key == "progress_counts":
        exact(value, {"counts", "counted_node_ids"}, "progress counts")
        require(type(value["counts"]) is dict and set(value["counts"]) == COUNTS, "progress: exhaustive counts required")
        for metric in value["counts"].values():
            _metric(metric)
        for name in COUNTS - {"total"}:
            derive_progress(value["counts"][name], value["counts"]["total"])
        _array(value["counted_node_ids"])
        for node_id in value["counted_node_ids"]:
            identifier(node_id, "counted task")
        require(len(set(value["counted_node_ids"])) == len(value["counted_node_ids"]), "progress: duplicate counted task")
    else:
        string(value, key)


def _managed_value(key: str, value: Any) -> None:
    if key in {"planned_start", "planned_end"}:
        if value is not None:
            timestamp(value, key)
    elif key == "watch":
        require(type(value) is bool, "watch: boolean required")
    elif key in {"priority", "order"}:
        _integer(value)
    elif key == "layout":
        require(type(value) is dict, "layout: object required")


def validate_extensions(envelope: Any, *, source_context: dict | None = None, domain: str = "source") -> dict | None:
    """Validate optional domain-separated data and return a detached copy.

    None means no extensions. Source context maps source_ref to
    {sha256, observed_at, format, data}; data is the parsed source from the
    caller's actual read, never reconstructed from the proposed fact envelope.
    ``domain='hub'`` permits managed/derived/proposed fields; no import may use
    it to promote source data. This function does not merge or store domains.
    """
    require(domain in {"source", "hub"}, "extension: unsupported ingestion domain")
    if envelope is None:
        return None
    exact(envelope, {"schema_version", *DOMAINS}, "extensions")
    require(envelope["schema_version"] == VERSION, "unsupported extension schema")
    for name in DOMAINS:
        require(type(envelope[name]) is dict, "extension domain: object required")
    require(domain != "source" or not any(envelope[x] for x in DOMAINS - {"fact"}), "source cannot supply managed/derived/proposed authority")
    require(set(envelope["fact"]) <= FACT_FIELDS, "extension: unknown fact field")
    for key, field in envelope["fact"].items():
        exact(field, {"value", "proof", "unknown_reason"}, "fact field")
        if field["value"] is None:
            string(field["unknown_reason"], "unknown reason")
            require(field["proof"] is None, "unknown fact cannot have value proof")
            continue
        require(field["unknown_reason"] is None, "known fact cannot have unknown reason")
        _fact_value(key, field["value"])
        proof = field["proof"]
        exact(proof, {"source_ref", "selector", "sha256", "observed_at"}, "fact proof")
        identifier(proof["source_ref"], "source")
        fingerprint(proof["sha256"], "source hash")
        timestamp(proof["observed_at"], "observed_at")
        validate_selector(proof["selector"])
        context = (source_context or {}).get(proof["source_ref"])
        require(type(context) is dict and context.get("sha256") == proof["sha256"] and context.get("observed_at") == proof["observed_at"], "fact proof: observed source mismatch")
        require(context.get("format") in {"yaml", "json", "markdown"}, "fact proof: source format required")
        require(("path" in proof["selector"]) == (context["format"] != "markdown"), "fact proof: selector format mismatch")
        require("data" in context, "fact proof: parsed observed source required")
        observed_value = select_value(context["data"], proof["selector"])
        try:
            matches = content_hash(observed_value) == content_hash(field["value"])
        except (TypeError, ValueError, RecursionError) as exc:
            raise RecordError("fact proof: source value is not canonical JSON") from exc
        require(matches, "fact proof: selected source value mismatch")
    facts = envelope["fact"]
    if "progress_counts" in facts and facts["progress_counts"]["value"] is not None:
        counted = facts["progress_counts"]["value"]["counted_node_ids"]
        if counted:
            require("task_nodes" in facts and facts["task_nodes"]["value"] is not None, "counted tasks need task tree")
            nodes = {n["id"]: n for n in facts["task_nodes"]["value"]}
            require(all(n in nodes and nodes[n]["kind"] == "leaf" for n in counted), "progress: only existing leaves may be counted")
            total = facts["progress_counts"]["value"]["counts"]["total"]["value"]
            require(total is None or len(counted) == total, "progress: leaf denominator mismatch")
    require(set(envelope["managed"]) <= MANAGED_FIELDS, "extension: unknown managed field")
    for key, value in envelope["managed"].items():
        _managed_value(key, value)
    require(set(envelope["derived"]) <= {"progress"}, "extension: unknown derived field")
    if "progress" in envelope["derived"]:
        counts = facts.get("progress_counts", {}).get("value")
        require(counts is not None, "derived progress needs fact counts")
        expected = derive_progress(counts["counts"]["verified"], counts["counts"]["total"])
        require(type(envelope["derived"]["progress"]) is dict and envelope["derived"]["progress"] == expected, "derived progress does not match facts")
    # Proposals use the same management semantics but never become management.
    if envelope["proposed"]:
        for key, proposal in envelope["proposed"].items():
            require(key in MANAGED_FIELDS, "extension: unknown proposal")
            exact(proposal, {"value", "status", "reason"}, "proposal")
            require(proposal["status"] == "proposed", "proposal cannot claim acceptance")
            string(proposal["reason"], "proposal reason")
            _managed_value(key, proposal["value"])
    return deepcopy(envelope)
