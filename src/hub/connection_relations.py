"""Evidence-bound relation proposals; never program links or design approvals."""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from .connection_records import ID, SHA, RecordError, content_hash, require


def relation_hash(value: dict) -> str:
    return content_hash({key: item for key, item in value.items() if key != "content_hash"})


def _fields(value: Any, keys: set[str], label: str) -> dict:
    require(isinstance(value, dict) and set(value) == keys, f"{label}: exact fields required")
    return value


def _strings(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    require(isinstance(value, list) and (bool(value) or not nonempty), f"{label}: list required")
    require(all(isinstance(v, str) and v.strip() for v in value), f"{label}: nonempty strings required")
    require(len(value) == len(set(value)), f"{label}: duplicate value")
    return value


def validate_relations(value: Any, *, project_ids: set[str], registry_hash: str,
                       inventory: dict, inventory_ref: dict) -> dict:
    """Validate against separately supplied authoritative registry/inventory facts."""
    value = _fields(value, {"schema_version", "kind", "id", "revision", "created_at", "registry_ref",
                            "inventory_ref", "relations", "content_hash"}, "relations")
    require(value["schema_version"] == "1.0" and value["kind"] == "connection_relation_proposals", "unsupported relation schema")
    require(isinstance(value["id"], str) and ID.fullmatch(value["id"]), "invalid collection ID")
    require(type(value["revision"]) is int and value["revision"] > 0, "invalid collection revision")
    try:
        timestamp = datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
        require(timestamp.tzinfo is not None and timestamp.utcoffset() is not None, "timestamp requires timezone")
    except (AttributeError, TypeError, ValueError) as exc:
        raise RecordError("invalid relation timestamp") from exc
    registry_ref = _fields(value["registry_ref"], {"path", "sha256"}, "registry reference")
    require(registry_ref == {"path": "data/registry/external_projects.yaml", "sha256": registry_hash}, "relation registry drift")
    ref = _fields(value["inventory_ref"], {"path", "sha256", "accepted_candidate_hash"}, "inventory reference")
    require(ref == inventory_ref, "relation inventory authority mismatch")
    require(all(isinstance(ref[k], str) and SHA.fullmatch(ref[k]) for k in ("sha256", "accepted_candidate_hash")), "invalid inventory digest")
    rows = inventory.get("relations")
    require(isinstance(rows, list), "inventory relations unavailable")
    require(isinstance(value["relations"], list), "relations must be a list")
    seen: set[str] = set()
    for relation in value["relations"]:
        relation = _fields(relation, {"id", "project_ids", "kind", "status", "shared_tasks", "differences",
                                     "not_shared", "inventory_index", "evidence_refs"}, "relation")
        identity = relation["id"]
        require(isinstance(identity, str) and ID.fullmatch(identity) and identity not in seen, "invalid or duplicate relation ID")
        seen.add(identity)
        members = _strings(relation["project_ids"], "relation projects", nonempty=True)
        require(len(members) >= 2 and members == sorted(members) and set(members) <= project_ids, "invalid relation project scope")
        require(relation["status"] == "proposed", "relations cannot grant confirmation or implementation authority")
        require(isinstance(relation["kind"], str) and relation["kind"] in {"pipeline", "shared_review_pattern", "shared_visual_language"}, "invalid relation kind")
        for key in ("shared_tasks", "differences", "not_shared"):
            _strings(relation[key], key, nonempty=True)
        index = relation["inventory_index"]
        require(type(index) is int and 0 <= index < len(rows), "invalid inventory relation index")
        evidence = rows[index]
        require(isinstance(evidence, dict) and isinstance(evidence.get("project_ids"), list)
                and all(isinstance(p, str) for p in evidence["project_ids"])
                and set(evidence["project_ids"]) == set(members)
                and evidence.get("kind") == relation["kind"], "relation does not match its observed evidence")
        refs = _strings(relation["evidence_refs"], "relation evidence", nonempty=True)
        require(refs == sorted(refs) and refs == sorted(evidence.get("source_refs", [])), "relation evidence references mismatch")
    require(isinstance(value["content_hash"], str) and SHA.fullmatch(value["content_hash"])
            and value["content_hash"] == relation_hash(value), "relation content hash mismatch")
    return value


def project_relations(value: dict, project_ids: set[str]) -> list[dict]:
    """Build a non-authoritative view from an already validated collection."""
    rows = []
    for project_id in sorted(project_ids):
        relations = [copy.deepcopy(r) for r in value["relations"] if project_id in r["project_ids"]]
        rows.append({"project_id": project_id, "status": "proposed" if relations else "unknown",
                     "reason": None if relations else "No evidence-bound relation proposal is registered.",
                     "relations": relations, "program_link_authority": False, "design_selection_authority": False})
    return rows
