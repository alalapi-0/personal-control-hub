"""Versioned Hub record validation; no writes or external content reads.

This small schema covers the first connection unit. Unsupported versions and
record kinds fail closed. Design-decision records will use a separate contract.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

VERSION = "1.0"
SUPPORTED_ADAPTER_VERSIONS = {"1.0"}
FORBIDDEN_PARTS = {".git", "node_modules", "dist", "build", "target", "__pycache__", ".venv",
                   "venv", ".idea", ".cache", "cache", "logs", "outputs", "tmp"}
SOURCE_SUFFIXES = {".yaml", ".yml", ".json", ".md", ".txt"}
ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
DISPOSITIONS = {"in_scope", "no_ui", "deferred", "protected", "unresolved"}
CONNECTION_STATES = {"PENDING", "BLOCKED_BY_AUTHORITY", "SOURCE_UNAVAILABLE",
                     "VALIDATION_FAILED", "CONNECTED_AND_VERIFIED", "AUTHORIZED_EXCEPTION"}
AVAILABILITY = {"fresh", "missing", "unavailable", "unreadable", "invalid",
                "blocked_by_authority", "source_not_declared"}
NORMALIZED_STATES = {"active", "complete", "paused", "blocked", "unknown"}
KINDS = {"scope", "connection_manifest", "project_snapshot", "connection_evidence"}
REQUIRED_FIELDS = {
    "scope": {"disposition", "reason", "evidence_refs", "authority_ref"},
    "connection_manifest": {"revision", "registry_ref", "entries", "content_hash"},
    "project_snapshot": {"manifest_id", "manifest_hash", "adapter_version", "adapter_hash", "raw_status",
                         "normalized_status", "next_action", "next_action_kind", "unknown_fields",
                         "blockers", "availability", "sources", "observed_at", "last_success_at",
                         "refresh_error", "relations", "designs", "source_role"},
    "connection_evidence": {"manifest_id", "manifest_hash", "adapter_version", "adapter_hash", "snapshot_ref",
                            "snapshot_hash", "status", "command", "exit_code", "observed_at",
                            "ui_verification", "authority_ref", "reason"},
}
NESTED_FIELDS = {
    "registry_ref": {"path", "schema_version", "sha256"},
    "manifest_entry": {"project_id", "scope", "permission", "allowed_entries", "expected_capabilities",
                       "source_absence_reason", "evidence_refs"},
    "permission": {"mode", "basis", "access_profile"},
    "source": {"ref", "path", "sha256", "bytes"},
}
CAPABILITIES = {"identity", "source", "availability", "status_or_explicit_unknown",
                "next_action_or_explicit_unknown", "bounded_refresh"}
STATUS_MAP = {"active": "active", "in_progress": "active", "running": "active",
              "complete": "complete", "completed": "complete", "accepted": "complete",
              "paused": "paused", "blocked": "blocked"}


def record_schema() -> dict:
    """Machine-readable schema index, derived from the validator's declarations."""
    return {"schema_version": VERSION, "schema_language": "hub-record-validator-v1",
            "validator": "hub.connection_records.validate_record",
            "collection_validator": "hub.connection_records.validate_collection",
            "common_required": ["schema_version", "record_type", "id", "created_at"],
            "project_required_except_manifest": ["project_id"],
            "record_required": {key: sorted(value) for key, value in REQUIRED_FIELDS.items()},
            "nested_exact_fields": {key: sorted(value) for key, value in NESTED_FIELDS.items()},
            "enums": {"ui_disposition": sorted(DISPOSITIONS), "connection_status": sorted(CONNECTION_STATES),
                      "availability": sorted(AVAILABILITY), "normalized_status": sorted(NORMALIZED_STATES)},
            "constraints": {"id_pattern": ID.pattern, "sha256_pattern": SHA.pattern,
                            "timestamps": "ISO 8601 with timezone", "unknown_fields": "reason required for null status/next",
                            "manifest": "unique complete registry IDs; hash binding; no root copies",
                            "references": "manifest binds authoritative registry; exact named path; snapshot/evidence identity",
                            "adapter_versions": sorted(SUPPORTED_ADAPTER_VERSIONS),
                            "acceptance": "successful read plus UI PASS; authorized exception requires owner/policy ref"}}


class RecordError(ValueError):
    """A precise record/identity error safe to show in diagnostics."""


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RecordError(message)


def timestamp(value: Any, path: str, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    require(isinstance(value, str), f"{path}: expected timezone timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
                f"{path}: timezone required")
    except ValueError as exc:
        raise RecordError(f"{path}: invalid timestamp") from exc


def string(value: Any, path: str, nullable: bool = False) -> None:
    require((value is None and nullable) or (isinstance(value, str) and bool(value.strip())),
            f"{path}: nonempty string required")


def strings(value: Any, path: str) -> None:
    require(isinstance(value, list), f"{path}: list required")
    for item in value:
        string(item, path)


def fingerprint(value: Any, path: str) -> None:
    require(isinstance(value, str) and bool(SHA.fullmatch(value)), f"{path}: invalid sha256")


def enum(value: Any, choices: set[str], path: str) -> None:
    require(isinstance(value, str) and value in choices, f"{path}: invalid enum")


def registry_permission(project: dict) -> str:
    if (not project["enabled"] or not project["summary_enabled"] or
            project.get("access_profile") == "no_current_goal_access"):
        return "no_access"
    return "named_sources" if project["current_state_paths"] else "no_source"


def resolve_named_source(project: dict, index: int, forbidden_parts: set[str]) -> tuple[Path, Path, Path]:
    # Never expand/resolve/stat an excluded root, including during evidence validation.
    require(registry_permission(project) == "named_sources", "source access denied")
    require(type(index) is int and 0 <= index < len(project["current_state_paths"]), "source index out of bounds")
    raw = Path(project["current_state_paths"][index]).expanduser()
    root = Path(project["root_path"]).expanduser().resolve()
    require(raw.is_absolute(), "source path must be absolute")
    path = raw.resolve()
    require(path.is_relative_to(root) and path != root, "source escapes authorized root")
    parts = set(path.relative_to(root).parts) | {raw.name}
    require(not (parts & forbidden_parts), "source uses forbidden directory")
    require(not any(part.lower().startswith(".env") or part.lower().endswith((".pem", ".key"))
                    or part.lower() in {"credentials", "secrets", "cookies"} for part in parts),
            "secret-bearing source forbidden")
    require(path.suffix.lower() in SOURCE_SUFFIXES, "source file type not allowed")
    return root, path, raw


def validate_manifest_authority(manifest: dict, registry: dict, registry_sha256: str) -> dict[str, dict]:
    require(isinstance(registry, dict) and registry.get("schema_version") == "1.0", "registry authority required")
    policy = registry.get("policy")
    require(isinstance(policy, dict) and policy.get("read_only") is True and
            policy.get("write_external_forbidden") is True, "registry permission boundary missing")
    rows = registry.get("projects")
    require(isinstance(rows, list) and bool(rows), "registry projects required")
    projects = {}
    for project in rows:
        require(isinstance(project, dict) and isinstance(project.get("id"), str), "invalid registry entry")
        require(project["id"] not in projects, "duplicate registry ID")
        require(all(type(project.get(k)) is bool for k in ("enabled", "summary_enabled")), "registry flags invalid")
        require(isinstance(project.get("current_state_paths"), list), "registry state paths invalid")
        require(project.get("external_write_allowed", False) is False, "external writes not supported")
        projects[project["id"]] = project
    validate_record(manifest, set(projects))
    fingerprint(registry_sha256, "authoritative_registry_sha256")
    require(manifest["registry_ref"]["sha256"] == registry_sha256, "registry authority hash mismatch")
    for entry in manifest["entries"]:
        project = projects[entry["project_id"]]
        mode = registry_permission(project)
        require(entry["permission"]["mode"] == mode, f"{project['id']}: permission mismatch")
        require(entry["permission"]["access_profile"] == project.get("access_profile", "registered_project_read"),
                f"{project['id']}: access_profile mismatch")
        expected = [f"current_state_paths[{i}]" for i in range(len(project["current_state_paths"]))]
        require(entry["allowed_entries"] == (expected if mode == "named_sources" else []),
                f"{project['id']}: allowed entries mismatch")
    return projects


def validate_adapters(adapters: dict, project_ids: set[str]) -> None:
    require(isinstance(adapters, dict) and adapters.get("schema_version") == VERSION, "unsupported adapter version")
    enum(adapters.get("adapter_version"), SUPPORTED_ADAPTER_VERSIONS, "unsupported adapter version")
    require(not (set(adapters) - {"schema_version", "adapter_version", "projects", "metadata"}), "unknown adapter fields")
    projects = adapters.get("projects")
    require(isinstance(projects, dict) and set(projects) == project_ids, "adapter coverage mismatch")
    for pid, adapter in projects.items():
        require(isinstance(adapter, dict) and not (set(adapter) - {"role", "format", "status", "next", "unknown", "next_kind"}),
                f"{pid}: unknown adapter fields")
        string(adapter.get("role"), f"{pid}.role")
        if adapter.get("format") is not None:
            enum(adapter["format"], {"yaml", "json", "markdown"}, f"{pid}.format")
        enum(adapter.get("next_kind", "explicit"), {"explicit", "track_milestone", "recommendations", "backlog", "unknown"},
             f"{pid}.next_kind")
        if adapter.get("unknown") is not None:
            string(adapter["unknown"], f"{pid}.unknown")
        for field in ("status", "next"):
            selector = adapter.get(field)
            if selector is None:
                continue
            require(isinstance(selector, dict), f"{pid}: selector must be an object")
            methods = set(selector) & {"jsonpath", "label", "section", "heading_regex"}
            require(len(methods) == 1, f"{pid}: selector must have exactly one method")
            method = next(iter(methods))
            optional = {"first_paragraph", "table_key"} if method == "section" else set()
            require(not (set(selector) - methods - optional), f"{pid}: unknown selector fields")
            require(isinstance(selector[method], str) and 0 < len(selector[method]) <= 160,
                    f"{pid}: invalid selector value")
            if "first_paragraph" in selector:
                require(type(selector["first_paragraph"]) is bool, "first_paragraph must be boolean")
            if "table_key" in selector:
                string(selector["table_key"], "table_key")
            if method == "heading_regex":
                try:
                    re.compile(selector[method])
                except re.error as exc:
                    raise RecordError(f"{pid}: invalid heading expression") from exc


def adapter_hash(adapters: dict, project_id: str) -> str:
    return content_hash({"version": adapters["adapter_version"], "project": adapters["projects"][project_id]})


def record_fields(record: dict, required: set[str]) -> None:
    missing = required - record.keys()
    require(not missing, f"{record.get('record_type')}: missing fields {sorted(missing)}")


def exact_fields(value: Any, fields: set[str], label: str) -> None:
    require(isinstance(value, dict), f"{label}: object required")
    require(set(value) == fields, f"{label}: exact fields required; missing={sorted(fields-set(value))}, unknown={sorted(set(value)-fields)}")


def registry_evidence_refs(value: Any, project_id: str) -> None:
    strings(value, "evidence_refs")
    require(value == [f"registry:projects/{project_id}"], "evidence reference must target this registry project")


def normalize_status(value: str | None) -> str:
    return STATUS_MAP.get(value.strip().lower(), "unknown") if value else "unknown"


def validate_snapshot_truth(record: dict) -> None:
    """The first reader has no last-known-good cache: failure cannot carry business state."""
    availability = record["availability"]
    unknown = record["unknown_fields"]
    expected_unknown = set()
    for field in ("raw_status", "next_action", "blockers"):
        if record[field] is None:
            expected_unknown.add(field)
    if record["normalized_status"] == "unknown":
        expected_unknown.add("normalized_status")
    require(set(unknown) == expected_unknown, "unknown_fields must exactly explain unknown values")
    for field in expected_unknown:
        string(unknown[field], f"unknown_fields.{field}")
    require((record["next_action"] is None) == (record["next_action_kind"] == "unknown"),
            "next_action and next_action_kind contradict each other")
    require(record["normalized_status"] == normalize_status(record["raw_status"]),
            "normalized status contradicts raw status")
    if availability == "fresh":
        require(record["last_success_at"] == record["observed_at"] and record["last_success_at"] is not None,
                "fresh source requires matching success and observation times")
        require(record["refresh_error"] is None, "fresh source cannot have refresh_error")
        require(bool(record["sources"]), "fresh snapshot requires a source fingerprint")
    else:
        require(record["raw_status"] is None and record["next_action"] is None and
                record["blockers"] is None and record["normalized_status"] == "unknown",
                "non-fresh source cannot claim business state")
        require(record["last_success_at"] is None, "non-fresh source has no success in this schema")
        string(record["refresh_error"], "refresh_error")
        if availability != "invalid":
            require(not record["sources"], "unread source cannot carry source fingerprints")


def validate_record(record: Any, project_ids: set[str] | None = None) -> dict:
    require(isinstance(record, dict), "record must be an object")
    require(record.get("schema_version") == VERSION, "unsupported schema_version")
    enum(record.get("record_type"), KINDS, "record_type")
    require(isinstance(record.get("id"), str) and bool(ID.fullmatch(record["id"])), "invalid id")
    timestamp(record.get("created_at"), "created_at")
    kind = record["record_type"]
    record_fields(record, REQUIRED_FIELDS[kind])
    allowed = REQUIRED_FIELDS[kind] | {"schema_version", "record_type", "id", "created_at"}
    if kind != "connection_manifest":
        allowed.add("project_id")
    require(not (record.keys() - allowed), "unknown record fields; schema revision required")
    if kind != "connection_manifest":
        pid = record.get("project_id")
        require(isinstance(pid, str) and bool(ID.fullmatch(pid)), "invalid project_id")
        if project_ids is not None:
            require(pid in project_ids, f"unknown project_id: {pid}")
    if kind == "scope":
        require(isinstance(record["disposition"], str) and record["disposition"] in DISPOSITIONS,
                "invalid UI disposition")
        for field in ("reason", "authority_ref"):
            string(record[field], field)
        registry_evidence_refs(record["evidence_refs"], record["project_id"])
        require(bool(re.fullmatch(r"owner-goal-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", record["authority_ref"])),
                "invalid scope authority reference")
    elif kind == "connection_manifest":
        require(type(record["revision"]) is int and record["revision"] > 0, "invalid revision")
        ref = record["registry_ref"]
        exact_fields(ref, NESTED_FIELDS["registry_ref"], "registry_ref")
        require(ref.get("path") == "data/registry/external_projects.yaml",
                "manifest must reference the sole registry")
        require(ref.get("schema_version") == "1.0", "unsupported registry version")
        fingerprint(ref.get("sha256"), "registry_ref.sha256")
        entries = record["entries"]
        require(isinstance(entries, list) and bool(entries), "entries must be nonempty")
        ids = []
        for entry in entries:
            exact_fields(entry, NESTED_FIELDS["manifest_entry"], "manifest entry")
            validate_record(entry["scope"], project_ids)
            require(entry["project_id"] == entry["scope"]["project_id"], "scope project mismatch")
            ids.append(entry["project_id"])
            permission = entry["permission"]
            exact_fields(permission, NESTED_FIELDS["permission"], "permission")
            enum(permission.get("mode"), {"named_sources", "no_access", "no_source"}, "permission.mode")
            string(permission.get("basis"), "permission.basis")
            string(permission.get("access_profile"), "permission.access_profile")
            for key in ("allowed_entries", "expected_capabilities", "evidence_refs"):
                strings(entry[key], key)
            require(set(entry["expected_capabilities"]) == CAPABILITIES and
                    len(entry["expected_capabilities"]) == len(CAPABILITIES), "invalid expected capabilities")
            registry_evidence_refs(entry["evidence_refs"], entry["project_id"])
            require(all(re.fullmatch(r"current_state_paths\[\d+\]", item)
                        for item in entry["allowed_entries"]), "unsupported entry reference")
            require(len(set(entry["allowed_entries"])) == len(entry["allowed_entries"]),
                    "duplicate source reference")
            if permission["mode"] != "named_sources":
                require(not entry["allowed_entries"], "non-readable entry cannot grant paths")
                string(entry["source_absence_reason"], "source_absence_reason")
            else:
                require(entry["source_absence_reason"] is None, "named source cannot have absence reason")
        require(len(ids) == len(set(ids)), "duplicate manifest project ID")
        if project_ids is not None:
            require(set(ids) == project_ids, "manifest coverage mismatch")
        fingerprint(record["content_hash"], "content_hash")
        require(record["content_hash"] == content_hash({k: v for k, v in record.items()
                                                       if k != "content_hash"}), "manifest hash mismatch")
    elif kind == "project_snapshot":
        string(record["manifest_id"], "manifest_id")
        fingerprint(record["manifest_hash"], "manifest_hash")
        enum(record["adapter_version"], SUPPORTED_ADAPTER_VERSIONS, "adapter_version")
        fingerprint(record["adapter_hash"], "adapter_hash")
        enum(record["availability"], AVAILABILITY, "availability")
        enum(record["normalized_status"], NORMALIZED_STATES, "normalized_status")
        enum(record["next_action_kind"], {"explicit", "track_milestone", "recommendations",
                                        "backlog", "unknown"}, "next_action_kind")
        string(record["source_role"], "source_role")
        string(record["raw_status"], "raw_status", nullable=True)
        string(record["next_action"], "next_action", nullable=True)
        require(isinstance(record["unknown_fields"], dict), "unknown_fields must be an object")
        for field in ("raw_status", "next_action"):
            if record[field] is None:
                string(record["unknown_fields"].get(field), f"unknown_fields.{field}")
        if record["blockers"] is not None:
            strings(record["blockers"], "blockers")
        for field in ("relations", "designs"):
            strings(record[field], field)
            require(not record[field], f"{field}: schema 1.0 requires an integrated design-reference validator before use")
        timestamp(record["observed_at"], "observed_at")
        timestamp(record["last_success_at"], "last_success_at", nullable=True)
        require(isinstance(record["sources"], list), "sources must be a list")
        refs = []
        for source in record["sources"]:
            exact_fields(source, NESTED_FIELDS["source"], "source")
            for field in ("ref", "path"):
                string(source.get(field), f"source.{field}")
            require(bool(re.fullmatch(r"current_state_paths\[\d+\]", source["ref"])), "invalid source reference")
            require(Path(source["path"]).is_absolute(), "source path must be absolute")
            refs.append(source["ref"])
            fingerprint(source.get("sha256"), "source.sha256")
            require(type(source.get("bytes")) is int and 0 <= source["bytes"] <= 1048576,
                    "invalid source bytes")
        require(len(refs) == len(set(refs)), "duplicate snapshot source reference")
        validate_snapshot_truth(record)
    elif kind == "connection_evidence":
        for field in ("manifest_id", "adapter_version", "snapshot_ref", "command", "reason"):
            string(record[field], field)
        enum(record["adapter_version"], SUPPORTED_ADAPTER_VERSIONS, "adapter_version")
        fingerprint(record["adapter_hash"], "adapter_hash")
        for field in ("manifest_hash", "snapshot_hash"):
            fingerprint(record[field], field)
        enum(record["status"], CONNECTION_STATES, "status")
        require(type(record["exit_code"]) is int, "exit_code must be integer")
        timestamp(record["observed_at"], "observed_at")
        enum(record["ui_verification"], {"PASS", "FAIL", "UNVERIFIED", "NOT_APPLICABLE"}, "ui_verification")
        require(bool(re.fullmatch(r"#[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", record["snapshot_ref"])), "invalid snapshot reference")
        if record["status"] == "CONNECTED_AND_VERIFIED":
            require(record["exit_code"] == 0 and record["ui_verification"] == "PASS",
                    "connection acceptance requires successful read and UI evidence")
        if record["status"] == "AUTHORIZED_EXCEPTION":
            string(record["authority_ref"], "authority_ref")
        else:
            require(record["authority_ref"] is None, "non-exception evidence cannot claim exception authority")
    return record


def validate_collection(records: list[dict], registry: dict, registry_sha256: str, adapters: dict) -> dict:
    """Resolve references against caller-loaded canonical registry, never record-declared authority.

    Callers must load registry bytes from the owned Hub, not from request payloads.
    This validator resolves authorized named paths but never opens source content.
    """
    require(isinstance(records, list) and bool(records), "records must be nonempty")
    require(isinstance(registry, dict) and isinstance(registry.get("projects"), list), "registry authority required")
    project_ids = {p["id"] for p in registry["projects"]}
    validate_adapters(adapters, project_ids)
    projects = {p["id"]: p for p in registry["projects"]}
    forbidden = FORBIDDEN_PARTS | set(registry.get("policy", {}).get("forbidden_scan_dirs", []))
    indexed = {}
    for record in records:
        validate_record(record, project_ids)
        if record["record_type"] == "connection_manifest":
            validate_manifest_authority(record, registry, registry_sha256)
        key = (record["record_type"], record["id"])
        require(key not in indexed, f"duplicate record identity: {key}")
        indexed[key] = record
    for record in records:
        kind = record["record_type"]
        if kind not in {"project_snapshot", "connection_evidence"}:
            continue
        manifest = indexed.get(("connection_manifest", record["manifest_id"]))
        require(manifest is not None, f"missing manifest reference: {record['manifest_id']}")
        require(record["manifest_hash"] == manifest["content_hash"], "manifest reference hash mismatch")
        entry = next(e for e in manifest["entries"] if e["project_id"] == record["project_id"])
        require(record["adapter_version"] == adapters["adapter_version"] and
                record["adapter_hash"] == adapter_hash(adapters, record["project_id"]),
                "record does not match adapter authority")
        if kind == "project_snapshot":
            require(record["source_role"] == adapters["projects"][record["project_id"]]["role"],
                    "source role does not match adapter authority")
            if entry["permission"]["mode"] != "named_sources":
                require(not record["sources"] and record["last_success_at"] is None,
                        "unauthorized/undeclared source cannot have read evidence")
                expected_availability = "blocked_by_authority" if entry["permission"]["mode"] == "no_access" else "source_not_declared"
                require(record["availability"] == expected_availability, "snapshot availability contradicts registry permission")
            else:
                require(record["availability"] not in {"blocked_by_authority", "source_not_declared"},
                        "snapshot availability contradicts named-source permission")
                for source in record["sources"]:
                    require(source["ref"] in entry["allowed_entries"], "snapshot source reference outside manifest")
                    index = int(re.fullmatch(r"current_state_paths\[(\d+)\]", source["ref"])[1])
                    _, expected_path, _ = resolve_named_source(projects[record["project_id"]], index, forbidden)
                    require(source["path"] == str(expected_path), "snapshot path does not match authorized named source")
        else:
            require(record["snapshot_ref"].startswith("#"), "snapshot_ref must name an in-collection snapshot")
            snapshot = indexed.get(("project_snapshot", record["snapshot_ref"][1:]))
            require(snapshot is not None, "missing snapshot reference")
            require(snapshot["project_id"] == record["project_id"] and
                    snapshot["manifest_hash"] == record["manifest_hash"], "snapshot reference identity mismatch")
            require(record["snapshot_hash"] == content_hash(snapshot), "snapshot reference hash mismatch")
            require(record["adapter_version"] == snapshot["adapter_version"], "evidence adapter version mismatch")
            require(record["adapter_hash"] == snapshot["adapter_hash"], "evidence adapter hash mismatch")
            require(record["observed_at"] == snapshot["observed_at"], "evidence observation time mismatch")
            availability = snapshot["availability"]
            require(record["exit_code"] == (0 if availability == "fresh" else 2), "evidence exit code contradicts availability")
            if record["status"] == "AUTHORIZED_EXCEPTION":
                exception = projects[record["project_id"]].get("hub_connection_exception", {})
                require(isinstance(exception, dict) and exception.get("authority_ref") == record["authority_ref"]
                        and bool(exception.get("reason")), "exception lacks registry authority")
            elif availability == "fresh":
                expected_status = {"PASS": "CONNECTED_AND_VERIFIED", "FAIL": "VALIDATION_FAILED",
                                   "UNVERIFIED": "PENDING"}.get(record["ui_verification"])
                require(record["status"] == expected_status, "evidence status contradicts fresh source/UI lane")
            else:
                expected_status = {"blocked_by_authority": "BLOCKED_BY_AUTHORITY", "invalid": "VALIDATION_FAILED",
                                   "source_not_declared": "PENDING", "missing": "SOURCE_UNAVAILABLE",
                                   "unavailable": "SOURCE_UNAVAILABLE", "unreadable": "SOURCE_UNAVAILABLE"}[availability]
                require(record["status"] == expected_status and record["ui_verification"] == "UNVERIFIED",
                        "evidence status/UI lane contradicts source availability")
    return {"valid": True, "records": len(records), "registry_projects": len(project_ids)}
