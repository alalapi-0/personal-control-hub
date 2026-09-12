"""Strict, versioned records for the local design-governance fact store."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

SCHEMA_VERSION = "1.0"
ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class DesignRecordError(ValueError):
    """A record cannot be represented by this exact schema."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _obj(value: Any, path: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DesignRecordError(f"{path}: expected object")
    actual = set(value)
    if actual != keys:
        missing, extra = sorted(keys - actual), sorted(actual - keys)
        raise DesignRecordError(f"{path}: fields mismatch missing={missing} extra={extra}")
    return value


def _list(value: Any, path: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list) or (nonempty and not value):
        raise DesignRecordError(f"{path}: expected {'non-empty ' if nonempty else ''}list")
    return value


def _text(value: Any, path: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise DesignRecordError(f"{path}: expected non-empty text")
    return value


def _id(value: Any, path: str) -> str:
    text = _text(value, path)
    assert text is not None
    if not ID_RE.fullmatch(text):
        raise DesignRecordError(f"{path}: invalid stable ID")
    return text


def _hash(value: Any, path: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not HASH_RE.fullmatch(value):
        raise DesignRecordError(f"{path}: invalid sha256")
    return value


def _time(value: Any, path: str) -> str:
    text = _text(value, path)
    assert text is not None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DesignRecordError(f"{path}: invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DesignRecordError(f"{path}: timestamp must include timezone")
    return text


def _enum(value: Any, path: str, allowed: set[str]) -> str:
    if value not in allowed:
        raise DesignRecordError(f"{path}: expected one of {sorted(allowed)}")
    return value


def _strings(value: Any, path: str, *, nonempty: bool = False) -> list[str]:
    result = _list(value, path, nonempty=nonempty)
    for index, item in enumerate(result):
        _text(item, f"{path}[{index}]")
    if len(result) != len(set(result)):
        raise DesignRecordError(f"{path}: duplicate values")
    return result


def _version(record: dict[str, Any], path: str) -> None:
    if record["schema_version"] != SCHEMA_VERSION:
        raise DesignRecordError(f"{path}: unsupported schema_version {record['schema_version']!r}")


def validate_scope(scope: Any, path: str = "scope") -> dict[str, Any]:
    scope = _obj(scope, path, {"family_id", "members"})
    if scope["family_id"] is not None:
        _id(scope["family_id"], f"{path}.family_id")
    members = _list(scope["members"], f"{path}.members", nonempty=True)
    seen: set[str] = set()
    for index, member in enumerate(members):
        p = f"{path}.members[{index}]"
        member = _obj(member, p, {"project_id", "pages"})
        project_id = _id(member["project_id"], f"{p}.project_id")
        if project_id in seen:
            raise DesignRecordError(f"{path}: duplicate member {project_id}")
        seen.add(project_id)
        pages = _strings(member["pages"], f"{p}.pages", nonempty=True)
        if pages != sorted(pages):
            raise DesignRecordError(f"{p}.pages: must use canonical lexicographic order")
    if [member["project_id"] for member in members] != sorted(seen):
        raise DesignRecordError(f"{path}.members: must use canonical project_id order")
    return scope


def validate_artifact_ref(record: Any) -> dict[str, Any]:
    base_keys = {"schema_version", "kind", "id", "created_at", "classification", "location", "sha256", "provenance", "scope"}
    if not isinstance(record, dict):
        raise DesignRecordError("artifact_ref: expected object")
    scope_value = record.get("scope")
    family_scoped = isinstance(scope_value, dict) and scope_value.get("family_id") is not None
    record = _obj(record, "artifact_ref", base_keys | ({"family_binding"} if family_scoped else set()))
    _version(record, "artifact_ref")
    _enum(record["kind"], "artifact_ref.kind", {"artifact_ref"})
    _id(record["id"], "artifact_ref.id")
    _time(record["created_at"], "artifact_ref.created_at")
    _enum(record["classification"], "artifact_ref.classification", {"real", "mock", "dry-run", "imported"})
    location = _obj(record["location"], "artifact_ref.location", {"kind", "value"})
    location_kind = _enum(location["kind"], "artifact_ref.location.kind", {"hub_relative", "figma"})
    _text(location["value"], "artifact_ref.location.value")
    _hash(record["sha256"], "artifact_ref.sha256", nullable=location_kind == "figma")
    provenance = _obj(record["provenance"], "artifact_ref.provenance", {"method", "source_refs"})
    _text(provenance["method"], "artifact_ref.provenance.method")
    _strings(provenance["source_refs"], "artifact_ref.provenance.source_refs")
    scope = validate_scope(record["scope"], "artifact_ref.scope")
    if family_scoped:
        binding = validate_family_ref(record["family_binding"], "artifact_ref.family_binding")
        if binding["id"] != scope["family_id"]:
            raise DesignRecordError("artifact_ref.family_binding: ID must match scope family_id")
    return record


def validate_artifact_bindings(value: Any, path: str) -> None:
    artifact_ids = set()
    for index, binding in enumerate(_list(value, path)):
        p = f"{path}[{index}]"
        binding = _obj(binding, p, {"artifact_id", "sha256"})
        artifact_id = _id(binding["artifact_id"], f"{p}.artifact_id")
        if artifact_id in artifact_ids:
            raise DesignRecordError(f"{path}: duplicate artifact")
        artifact_ids.add(artifact_id)
        _hash(binding["sha256"], f"{p}.sha256")


def validate_baseline(record: Any) -> dict[str, Any]:
    keys = {"schema_version", "kind", "id", "created_at", "classification", "project_id", "revision", "content_hash", "source", "scope", "behaviors", "data_contract_refs", "unverified", "artifact_bindings"}
    record = _obj(record, "baseline", keys)
    _version(record, "baseline")
    _enum(record["kind"], "baseline.kind", {"baseline"})
    _id(record["id"], "baseline.id"); _time(record["created_at"], "baseline.created_at")
    _enum(record["classification"], "baseline.classification", {"real", "mock", "dry-run", "imported"})
    _id(record["project_id"], "baseline.project_id")
    if not isinstance(record["revision"], int) or isinstance(record["revision"], bool) or record["revision"] < 1:
        raise DesignRecordError("baseline.revision: expected positive integer")
    source = _obj(record["source"], "baseline.source", {"kind", "reference", "commit", "dirty_fingerprint", "observed_at"})
    _enum(source["kind"], "baseline.source.kind", {"repository", "new_surface_spec", "imported"})
    _text(source["reference"], "baseline.source.reference")
    _text(source["commit"], "baseline.source.commit", nullable=True)
    _hash(source["dirty_fingerprint"], "baseline.source.dirty_fingerprint", nullable=True)
    _time(source["observed_at"], "baseline.source.observed_at")
    scope = _obj(record["scope"], "baseline.scope", {"pages", "flows", "viewport"})
    _strings(scope["pages"], "baseline.scope.pages", nonempty=True); _strings(scope["flows"], "baseline.scope.flows")
    viewport = _obj(scope["viewport"], "baseline.scope.viewport", {"width", "height", "platform"})
    if any(not isinstance(viewport[k], int) or isinstance(viewport[k], bool) or viewport[k] < 1 for k in ("width", "height")):
        raise DesignRecordError("baseline.scope.viewport: dimensions must be positive integers")
    _enum(viewport["platform"], "baseline.scope.viewport.platform", {"desktop", "mobile", "tablet", "other"})
    action_ids: set[str] = set()
    for index, behavior in enumerate(_list(record["behaviors"], "baseline.behaviors")):
        p = f"baseline.behaviors[{index}]"
        behavior = _obj(behavior, p, {"action_id", "entry", "preconditions", "input", "output", "transition", "storage_effects", "external_effects", "recovery", "test_refs"})
        action_id = _id(behavior["action_id"], f"{p}.action_id")
        if action_id in action_ids:
            raise DesignRecordError("baseline.behaviors: duplicate action_id")
        action_ids.add(action_id)
        for field in ("entry", "input", "output", "transition", "storage_effects", "external_effects", "recovery"):
            _text(behavior[field], f"{p}.{field}")
        _strings(behavior["preconditions"], f"{p}.preconditions"); _strings(behavior["test_refs"], f"{p}.test_refs")
    _strings(record["data_contract_refs"], "baseline.data_contract_refs")
    _strings(record["unverified"], "baseline.unverified")
    validate_artifact_bindings(record["artifact_bindings"], "baseline.artifact_bindings")
    expected = content_hash({k: record[k] for k in sorted(keys - {"content_hash", "created_at"})})
    if _hash(record["content_hash"], "baseline.content_hash") != expected:
        raise DesignRecordError("baseline.content_hash: content hash mismatch")
    return record


def validate_candidate(record: Any) -> dict[str, Any]:
    base_keys = {"schema_version", "kind", "id", "created_at", "classification", "revision", "content_hash", "scope", "baseline_bindings", "visual", "figma_ref", "artifact_bindings", "evidence_refs"}
    if not isinstance(record, dict):
        raise DesignRecordError("candidate: expected object")
    scope_value = record.get("scope")
    family_scoped = isinstance(scope_value, dict) and scope_value.get("family_id") is not None
    keys = base_keys | ({"family_binding"} if family_scoped else set())
    record = _obj(record, "candidate", keys); _version(record, "candidate")
    _enum(record["kind"], "candidate.kind", {"candidate"}); _id(record["id"], "candidate.id"); _time(record["created_at"], "candidate.created_at")
    _enum(record["classification"], "candidate.classification", {"real", "mock", "dry-run", "imported"})
    if not isinstance(record["revision"], int) or isinstance(record["revision"], bool) or record["revision"] < 1:
        raise DesignRecordError("candidate.revision: expected positive integer")
    scope = validate_scope(record["scope"], "candidate.scope")
    if family_scoped:
        binding = validate_family_ref(record["family_binding"], "candidate.family_binding")
        if binding["id"] != scope["family_id"]:
            raise DesignRecordError("candidate.family_binding: ID must match scope family_id")
    bindings = _list(record["baseline_bindings"], "candidate.baseline_bindings", nonempty=True)
    bound: dict[str, set[str]] = {}
    for index, binding in enumerate(bindings):
        p = f"candidate.baseline_bindings[{index}]"
        binding = _obj(binding, p, {"project_id", "baseline_id", "baseline_revision", "baseline_hash", "pages"})
        project_id = _id(binding["project_id"], f"{p}.project_id"); _id(binding["baseline_id"], f"{p}.baseline_id")
        if project_id in bound: raise DesignRecordError("candidate.baseline_bindings: duplicate project")
        if not isinstance(binding["baseline_revision"], int) or isinstance(binding["baseline_revision"], bool) or binding["baseline_revision"] < 1:
            raise DesignRecordError(f"{p}.baseline_revision: expected positive integer")
        _hash(binding["baseline_hash"], f"{p}.baseline_hash")
        bound[project_id] = set(_strings(binding["pages"], f"{p}.pages", nonempty=True))
    scoped = {m["project_id"]: set(m["pages"]) for m in scope["members"]}
    if bound != scoped:
        raise DesignRecordError("candidate.baseline_bindings: must exactly match member/page scope")
    visual = _obj(record["visual"], "candidate.visual", {"tokens", "components", "structure", "differences"})
    for field in visual: _strings(visual[field], f"candidate.visual.{field}")
    figma = _obj(record["figma_ref"], "candidate.figma_ref", {"file_key", "node_id", "version", "offline"})
    for field in ("file_key", "node_id", "version"): _text(figma[field], f"candidate.figma_ref.{field}", nullable=True)
    if not isinstance(figma["offline"], bool): raise DesignRecordError("candidate.figma_ref.offline: expected boolean")
    validate_artifact_bindings(record["artifact_bindings"], "candidate.artifact_bindings")
    for index, value in enumerate(_strings(record["evidence_refs"], "candidate.evidence_refs")):
        _id(value, f"candidate.evidence_refs[{index}]")
    expected = content_hash({k: record[k] for k in sorted(keys - {"content_hash", "created_at"})})
    if _hash(record["content_hash"], "candidate.content_hash") != expected:
        raise DesignRecordError("candidate.content_hash: content hash mismatch")
    return record


def validate_review(record: Any) -> dict[str, Any]:
    record = _obj(record, "review", {"schema_version", "kind", "id", "created_at", "candidate", "functional_invariants", "lanes", "tool_limits", "reviewer", "evidence_refs"})
    _version(record, "review"); _enum(record["kind"], "review.kind", {"review"}); _id(record["id"], "review.id"); _time(record["created_at"], "review.created_at")
    validate_candidate_ref(record["candidate"], "review.candidate")
    _strings(record["functional_invariants"], "review.functional_invariants")
    lane_ids: set[str] = set()
    for index, lane in enumerate(_list(record["lanes"], "review.lanes", nonempty=True)):
        p = f"review.lanes[{index}]"; lane = _obj(lane, p, {"name", "result", "reason", "evidence_refs"})
        lane_id = _id(lane["name"], f"{p}.name")
        if lane_id in lane_ids:
            raise DesignRecordError("review.lanes: duplicate lane name")
        lane_ids.add(lane_id); _enum(lane["result"], f"{p}.result", {"PASS", "FAIL", "UNVERIFIED", "NOT_APPLICABLE"})
        _text(lane["reason"], f"{p}.reason"); _strings(lane["evidence_refs"], f"{p}.evidence_refs")
    _strings(record["tool_limits"], "review.tool_limits")
    reviewer = _obj(record["reviewer"], "review.reviewer", {"type", "reference"})
    _enum(reviewer["type"], "review.reviewer.type", {"agent", "owner", "tool"}); _text(reviewer["reference"], "review.reviewer.reference")
    _strings(record["evidence_refs"], "review.evidence_refs")
    return record


def validate_candidate_ref(value: Any, path: str = "candidate") -> dict[str, Any]:
    value = _obj(value, path, {"id", "revision", "content_hash"}); _id(value["id"], f"{path}.id")
    if not isinstance(value["revision"], int) or isinstance(value["revision"], bool) or value["revision"] < 1: raise DesignRecordError(f"{path}.revision: expected positive integer")
    _hash(value["content_hash"], f"{path}.content_hash"); return value


def validate_family_ref(value: Any, path: str = "family") -> dict[str, Any]:
    value = _obj(value, path, {"id", "revision", "content_hash"})
    _id(value["id"], f"{path}.id")
    if not isinstance(value["revision"], int) or isinstance(value["revision"], bool) or value["revision"] < 1:
        raise DesignRecordError(f"{path}.revision: expected positive integer")
    _hash(value["content_hash"], f"{path}.content_hash")
    return value


def validate_design_family(record: Any) -> dict[str, Any]:
    keys = {"schema_version", "kind", "id", "created_at", "classification", "revision", "content_hash", "scope", "source", "shared_visual_semantics", "component_mappings", "member_exceptions"}
    record = _obj(record, "design_family", keys); _version(record, "design_family")
    _enum(record["kind"], "design_family.kind", {"design_family"}); family_id = _id(record["id"], "design_family.id")
    _time(record["created_at"], "design_family.created_at")
    _enum(record["classification"], "design_family.classification", {"real", "mock", "dry-run", "imported"})
    if not isinstance(record["revision"], int) or isinstance(record["revision"], bool) or record["revision"] < 1:
        raise DesignRecordError("design_family.revision: expected positive integer")
    scope = validate_scope(record["scope"], "design_family.scope")
    if scope["family_id"] != family_id:
        raise DesignRecordError("design_family.scope.family_id: must equal family ID")
    source = _obj(record["source"], "design_family.source", {"reference", "evidence_refs"})
    _text(source["reference"], "design_family.source.reference")
    for index, value in enumerate(_strings(source["evidence_refs"], "design_family.source.evidence_refs")):
        _id(value, f"design_family.source.evidence_refs[{index}]")
    _strings(record["shared_visual_semantics"], "design_family.shared_visual_semantics", nonempty=True)
    member_ids = {member["project_id"] for member in scope["members"]}
    component_ids: set[str] = set()
    for index, mapping in enumerate(_list(record["component_mappings"], "design_family.component_mappings")):
        path = f"design_family.component_mappings[{index}]"
        mapping = _obj(mapping, path, {"component_id", "members"})
        component_id = _id(mapping["component_id"], f"{path}.component_id")
        if component_id in component_ids:
            raise DesignRecordError("design_family.component_mappings: duplicate component_id")
        component_ids.add(component_id)
        mapped = set(_strings(mapping["members"], f"{path}.members", nonempty=True))
        if not mapped.issubset(member_ids):
            raise DesignRecordError("design_family.component_mappings: unknown member")
    exception_members: set[str] = set()
    for index, exception in enumerate(_list(record["member_exceptions"], "design_family.member_exceptions")):
        path = f"design_family.member_exceptions[{index}]"
        exception = _obj(exception, path, {"project_id", "reason"})
        project_id = _id(exception["project_id"], f"{path}.project_id")
        if project_id not in member_ids or project_id in exception_members:
            raise DesignRecordError("design_family.member_exceptions: unknown or duplicate member")
        exception_members.add(project_id); _text(exception["reason"], f"{path}.reason")
    expected = content_hash({k: record[k] for k in sorted(keys - {"content_hash", "created_at"})})
    if _hash(record["content_hash"], "design_family.content_hash") != expected:
        raise DesignRecordError("design_family.content_hash: content hash mismatch")
    return record


def validate_decision_event(record: Any, *, fixture_store: bool) -> dict[str, Any]:
    record = _obj(record, "decision_event", {"schema_version", "kind", "id", "request_id", "created_at", "source", "action", "candidate", "scope", "feedback", "supersedes"})
    _version(record, "decision_event"); _enum(record["kind"], "decision_event.kind", {"decision_event"})
    _id(record["id"], "decision_event.id"); _id(record["request_id"], "decision_event.request_id"); _time(record["created_at"], "decision_event.created_at")
    source = _obj(record["source"], "decision_event.source", {"type", "reference", "trusted_owner", "fixture"})
    source_type = _enum(source["type"], "decision_event.source.type", {"synthetic_fixture", "trusted_owner_reference"})
    _text(source["reference"], "decision_event.source.reference")
    if not isinstance(source["trusted_owner"], bool) or not isinstance(source["fixture"], bool): raise DesignRecordError("decision_event.source: flags must be boolean")
    if fixture_store:
        if source_type != "synthetic_fixture" or source["trusted_owner"] or not source["fixture"]:
            raise DesignRecordError("decision_event.source: fixture stores require unmistakably synthetic sources")
    elif source_type != "trusted_owner_reference" or not source["trusted_owner"] or source["fixture"]:
        raise DesignRecordError("decision_event.source: real events require an explicit trusted owner reference")
    _enum(record["action"], "decision_event.action", {"select", "request_changes", "defer", "withdraw"})
    validate_candidate_ref(record["candidate"], "decision_event.candidate"); validate_scope(record["scope"], "decision_event.scope")
    _text(record["feedback"], "decision_event.feedback", nullable=True)
    if record["supersedes"] is not None: _id(record["supersedes"], "decision_event.supersedes")
    return record


VALIDATORS = {"baseline": validate_baseline, "candidate": validate_candidate, "review": validate_review, "artifact_ref": validate_artifact_ref, "design_family": validate_design_family}


def validate_fact(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict) or record.get("kind") not in VALIDATORS:
        raise DesignRecordError("fact: unknown record kind")
    return VALIDATORS[record["kind"]](record)


def with_content_hash(record: dict[str, Any]) -> dict[str, Any]:
    """Set the hash on a baseline/candidate using its schema-defined identity content."""
    result = json.loads(json.dumps(record))
    result["content_hash"] = content_hash({k: result[k] for k in sorted(set(result) - {"content_hash", "created_at"})})
    return result
