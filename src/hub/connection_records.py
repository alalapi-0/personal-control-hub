"""Versioned connection declarations and normalized records, without I/O.

Reuses the prior record module's strict assertions, canonical hashing and typed
timestamps. Business acceptance is source data; a successful read never grants it.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

VERSION = "2.0"
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_PARTS = {".git", ".cursor", "cursor", "node_modules", ".venv", "venv",
                   "logs", "outputs", "cache", ".cache", "backups", "credentials",
                   "secrets", "cookies", ".ssh", ".codex"}
SOURCE_SUFFIXES = {".yaml", ".yml", ".json", ".md", ".txt"}
FIELDS = {
    "current_work.objective": "string", "current_work.phase": "string",
    "current_work.round": "string", "current_work.status": "status",
    "current_work.completed": "boolean", "current_work.accepted": "boolean",
    "current_work.next_action": "string", "current_work.owner_role": "string",
    "progress.completed": "integer", "progress.total": "integer",
    "progress.counting_basis": "string", "progress.milestones": "milestones",
    "blockers": "blockers", "verification.status": "string",
    "verification.evidence_refs": "strings", "verification.accepted_basis": "string",
    "delivery.status": "delivery", "delivery.remote": "string",
    "delivery.branch": "string", "delivery.commit": "commit", "delivery.receipt_ref": "string",
}
ENUMS = {"status": {"active", "paused", "blocked", "complete", "unknown"},
         "delivery": {"local", "pending_delivery", "delivered", "unknown"}}
READ_STATUSES = {"resolved", "removed_local", "disabled", "missing_declaration", "missing_source",
                 "offline", "permission_denied", "invalid", "unsafe_path", "authority_drift"}
SECRET = re.compile(r"-----BEGIN (?:[A-Z ]*PRIVATE KEY)-----|\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,})|"
                    r"(?i:\b(?:password|api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*[\"']?[^\s\"']{8,})")


class RecordError(ValueError):
    """Safe structural diagnostic, never includes source values."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RecordError(message)


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def exact(value: Any, fields: set[str], label: str) -> None:
    require(type(value) is dict and set(value) == fields, f"{label}: missing or unknown fields")


def identifier(value: Any, label: str) -> None:
    require(type(value) is str and bool(ID.fullmatch(value)), f"{label}: invalid identity")


def fingerprint(value: Any, label: str) -> None:
    require(type(value) is str and bool(SHA.fullmatch(value)), f"{label}: invalid sha256")


def validate_authority(value: Any) -> dict:
    exact(value, {"registry_hash", "schema_hash", "adapter_version"}, "authority")
    require(value["adapter_version"] == VERSION, "unsupported authority adapter")
    for name in ("registry_hash", "schema_hash"):
        fingerprint(value[name], "authority " + name)
    return value


def string(value: Any, label: str) -> None:
    require(type(value) is str and 0 < len(value.strip()) <= 16000, f"{label}: bounded nonempty string required")
    require(not SECRET.search(value), f"{label}: sensitive value rejected")


def timestamp(value: Any, label: str) -> None:
    string(value, label)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(parsed.tzinfo is not None and parsed.utcoffset() is not None, f"{label}: timezone required")
    except ValueError as exc:
        raise RecordError(f"{label}: invalid timestamp") from exc


def relative_path(value: Any) -> str:
    string(value, "source path")
    path = PurePosixPath(value)
    require(not path.is_absolute() and str(path) == value and "\\" not in value and ":" not in value,
            "source path: canonical relative path required")
    require(all(part not in {".", "..", "~"} and part.lower() not in FORBIDDEN_PARTS
                and not part.lower().startswith(".env") for part in path.parts), "source path: forbidden component")
    require(path.suffix.lower() in SOURCE_SUFFIXES, "source path: unsupported suffix")
    require(not any(word in path.name.lower() for word in ("secret", "credential", "cookie", "private_key")),
            "source path: sensitive filename")
    require(not set(re.split(r"[._-]+", path.stem.lower())) & {"auth", "token", "tokens", "password", "passwords", "apikey"},
            "source path: credential/token filename")
    canonical_name = re.sub(r"[._-]+", "", path.stem.casefold())
    require(not any(word in canonical_name for word in ("serviceaccount", "privatekey", "clientsecret", "clientcredential")),
            "source path: credential/key filename")
    return value


def typed(value: Any, kind: str) -> None:
    if kind == "string":
        string(value, "mapped field")
    elif kind == "boolean":
        require(type(value) is bool, "mapped field: boolean required")
    elif kind == "integer":
        require(type(value) is int and value >= 0, "mapped field: nonnegative integer required")
    elif kind == "strings":
        require(type(value) is list and len(value) <= 1000, "mapped field: bounded array required")
        for entry in value:
            string(entry, "array entry")
    elif kind in ENUMS:
        require(type(value) is str and value in ENUMS[kind], "mapped field: unsupported lifecycle state")
    elif kind == "commit":
        require(type(value) is str and bool(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value)), "mapped field: full commit required")
    elif kind in {"blockers", "milestones"}:
        require(type(value) is list and len(value) <= 1000, "mapped field: bounded array required")
        shape = ({"code": "string", "reason": "string", "affected_items": "strings",
                  "recovery_condition": "string", "retry_entry": "string", "waiting_for_user": "boolean"}
                 if kind == "blockers" else {"id": "string", "label": "string", "completed": "boolean", "accepted": "boolean"})
        for entry in value:
            exact(entry, set(shape), kind)
            for key, field_kind in shape.items():
                typed(entry[key], field_kind)
    else:
        raise RecordError("unknown field type")


def validate_selector(value: Any) -> None:
    require(type(value) is dict, "selector: object required")
    if set(value) == {"path"}:
        require(type(value["path"]) is list and 0 < len(value["path"]) <= 20, "selector: bounded path required")
        require(all((type(x) is str and 0 < len(x) <= 160) or (type(x) is int and x >= 0)
                    for x in value["path"]), "selector: typed key/index required")
    else:
        exact(value, {"heading", "label"}, "markdown selector")
        string(value["heading"], "heading")
        string(value["label"], "label")


def validate_declaration(value: Any, project_id: str | None = None) -> dict:
    exact(value, {"schema_version", "project_id", "source_refs", "mapping", "unknown_fields",
                  "validation_entry", "max_read_age_seconds"}, "declaration")
    require(value["schema_version"] == VERSION, "unsupported declaration schema")
    identifier(value["project_id"], "project")
    require(project_id is None or value["project_id"] == project_id, "project identity mismatch")
    sources = value["source_refs"]
    require(type(sources) is list and len(sources) == 1, "one sole current-state source required")
    for source in sources:
        exact(source, {"id", "path", "format", "role"}, "source reference")
        identifier(source["id"], "source")
        relative_path(source["path"])
        require(type(source["format"]) is str and source["format"] in {"yaml", "json", "markdown"}, "unsupported source format")
        require(source["role"] == "current_state", "unsupported source role")
        require(PurePosixPath(source["path"]).suffix.lower() in
                {"yaml": {".yaml", ".yml"}, "json": {".json"}, "markdown": {".md", ".txt"}}[source["format"]],
                "format/suffix mismatch")
    mapping, unknown = value["mapping"], value["unknown_fields"]
    require(type(mapping) is dict and type(unknown) is dict, "mapping and unknown_fields must be objects")
    require(set(mapping) <= set(FIELDS) and set(unknown) == set(FIELDS), "exhaustive field unknown reasons required")
    for reason in unknown.values():
        string(reason, "unknown reason")
    for key, rule in mapping.items():
        exact(rule, {"source_ref", "selector", "value_map"}, "mapping")
        require(rule["source_ref"] == sources[0]["id"], "unknown source reference")
        validate_selector(rule["selector"])
        require(("path" in rule["selector"]) == (sources[0]["format"] != "markdown"), "selector/format mismatch")
        require(rule["value_map"] is None or type(rule["value_map"]) is dict, "value_map must be object or null")
        if rule["value_map"] is not None:
            for raw, target in rule["value_map"].items():
                string(raw, "value_map key")
                typed(target, FIELDS[key])
    typed(value["validation_entry"], "strings")
    require(type(value["max_read_age_seconds"]) is int and 60 <= value["max_read_age_seconds"] <= 604800,
            "read age must be 60..604800 seconds")
    return value


def empty_business() -> dict:
    result: dict = {}
    for field in FIELDS:
        put_field(result, field, None)
    return result


def put_field(business: dict, field: str, value: Any) -> None:
    parts = field.split(".")
    target = business
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = value


def get_field(business: dict, field: str) -> Any:
    target = business
    for part in field.split("."):
        target = target[part]
    return target


def record_schema() -> dict:
    """Executable-schema index; validation is shared by CLI, reader and ledger."""
    return {"schema_version": VERSION, "schema_language": "hub-typed-validator-v2",
            "declaration_validator": "hub.connection_records.validate_declaration",
            "record_validator": "hub.connection_records.validate_result",
            "business_fields": FIELDS, "nullable": "all fields; each null requires a reason",
            "enums": {k: sorted(v) for k, v in ENUMS.items()}, "read_statuses": sorted(READ_STATUSES),
            "source_contract": "one relative current_state source; typed path or exact Markdown heading/label",
            "permission_rule": "explicit connection_read_allowed=false denies before root access; absent field falls back to enabled",
            "protected_sources": "no .codex/.cursor/.ssh roots, unsafe aliases, environment or credential/token source filenames",
            "output_shell": "exact snapshot/row/authority/head/coverage/freshness fields; valid project IDs, nonempty names, strict numeric/boolean types",
            "value_maps": "all declared and provenance targets must match the field type, including unselected entries",
            "lifecycle": "completed, accepted and delivery.status are independent source facts"}


def validate_result(value: Any) -> dict:
    exact(value, {"schema_version", "kind", "project_id", "name", "observed_at", "success", "disposition",
                  "authority", "business", "sources", "declaration", "field_provenance", "unknown_fields",
                  "validation_entry", "freshness", "errors", "update_key"}, "normalized result")
    require(value["schema_version"] == VERSION and value["kind"] == "source_resolution", "unsupported result schema/kind")
    identifier(value["project_id"], "result project")
    string(value["name"], "project name")
    timestamp(value["observed_at"], "observed_at")
    require(type(value["success"]) is bool and type(value["disposition"]) is str and value["disposition"] in READ_STATUSES, "invalid read disposition")
    require(value["success"] == (value["disposition"] == "resolved"), "read success mismatch")
    validate_authority(value["authority"])
    expected = empty_business()
    exact(value["business"], set(expected), "business")
    for group, fields in expected.items():
        if type(fields) is dict:
            exact(value["business"][group], set(fields), group)
    require(type(value["field_provenance"]) is dict and type(value["unknown_fields"]) is dict,
            "provenance/reasons required")
    require(set(value["field_provenance"]) | set(value["unknown_fields"]) == set(FIELDS)
            and not set(value["field_provenance"]) & set(value["unknown_fields"]), "exhaustive disjoint field evidence required")
    require(type(value["sources"]) is list and len(value["sources"]) <= 1, "invalid source list")
    for source in value["sources"]:
        exact(source, {"id", "path", "format", "role", "sha256", "bytes", "modified_at"}, "source evidence")
        require(type(source["id"]) is str and bool(ID.fullmatch(source["id"])) and source["role"] == "current_state",
                "invalid source identity/role")
        require(type(source["format"]) is str and source["format"] in {"yaml", "json", "markdown"}, "invalid source format")
        relative_path(source["path"])
        require(PurePosixPath(source["path"]).suffix.lower() in
                {"yaml": {".yaml", ".yml"}, "json": {".json"}, "markdown": {".md", ".txt"}}[source["format"]],
                "source evidence format/suffix mismatch")
        require(type(source["sha256"]) is str and bool(SHA.fullmatch(source["sha256"])), "invalid source fingerprint")
        typed(source["bytes"], "integer")
        timestamp(source["modified_at"], "source modified_at")
    if value["declaration"] is not None:
        exact(value["declaration"], {"path", "sha256", "schema_version"}, "declaration evidence")
        require(value["declaration"]["path"] == "hub.connection.yaml" and value["declaration"]["schema_version"] == VERSION,
                "invalid declaration evidence")
        require(type(value["declaration"]["sha256"]) is str and bool(SHA.fullmatch(value["declaration"]["sha256"])), "invalid declaration hash")
    if value["success"]:
        require(value["declaration"] is not None and len(value["sources"]) == 1, "successful read needs declaration/source evidence")
    else:
        require(not value["field_provenance"] and value["business"] == empty_business(), "failed read cannot publish business facts")
    for field, kind in FIELDS.items():
        actual = get_field(value["business"], field)
        if actual is None:
            require(field in value["unknown_fields"], "null field needs unknown reason")
            string(value["unknown_fields"][field], "unknown reason")
        else:
            typed(actual, kind)
            require(field in value["field_provenance"], "business field needs source evidence")
            proof = value["field_provenance"][field]
            exact(proof, {"source_ref", "sha256", "selector", "value_map", "raw_value"}, "field proof")
            require(any(s["id"] == proof["source_ref"] and s["sha256"] == proof["sha256"] for s in value["sources"]),
                    "field evidence/source mismatch")
            validate_selector(proof["selector"])
            source_format = next(s["format"] for s in value["sources"] if s["id"] == proof["source_ref"])
            require(("path" in proof["selector"]) == (source_format != "markdown"), "field selector/source format mismatch")
            require(not SECRET.search(json.dumps(proof, ensure_ascii=False)), "sensitive field evidence")
            mapped = proof["raw_value"]
            if proof["value_map"] is not None:
                require(type(mapped) is str and type(proof["value_map"]) is dict and mapped in proof["value_map"], "invalid mapped proof")
                for raw, target in proof["value_map"].items():
                    string(raw, "value_map key")
                    typed(target, kind)
                mapped = proof["value_map"][mapped]
            typed(mapped, kind)
            require(type(mapped) is type(actual) and mapped == actual, "field value/evidence mismatch")
    progress = value["business"]["progress"]
    if progress["completed"] is not None or progress["total"] is not None:
        require(progress["counting_basis"] is not None, "counts need source counting basis")
    if progress["completed"] is not None and progress["total"] is not None:
        require(progress["completed"] <= progress["total"], "completed count exceeds denominator")
    typed(value["validation_entry"], "strings")
    exact(value["freshness"], {"read_status", "stale", "reason", "max_read_age_seconds", "root_binding"}, "freshness")
    require(value["freshness"]["read_status"] == value["disposition"] and type(value["freshness"]["stale"]) is bool,
            "freshness mismatch")
    require(value["freshness"]["stale"] == (not value["success"]), "stored result freshness must match read outcome")
    if value["success"]:
        require(value["freshness"]["reason"] is None, "successful read has no failure reason")
    else:
        string(value["freshness"]["reason"], "freshness reason")
    age = value["freshness"]["max_read_age_seconds"]
    require(type(age) is int and 60 <= age <= 604800, "invalid read freshness limit")
    binding = value["freshness"]["root_binding"]
    require(binding is None or (type(binding) is str and bool(SHA.fullmatch(binding))), "invalid root binding")
    require(not value["success"] or binding is not None, "successful read requires root binding")
    require(type(value["errors"]) is list and bool(value["errors"]) != value["success"], "error/success mismatch")
    for error in value["errors"]:
        exact(error, {"code", "message"}, "error")
        string(error["code"], "error code")
        string(error["message"], "error message")
    require(type(value["update_key"]) is str and bool(SHA.fullmatch(value["update_key"])), "invalid update identity")
    require(value["update_key"] == update_key(value), "update identity mismatch")
    return value


def update_key(value: dict) -> str:
    """Stable business update identity; read time and other projects cannot affect it."""
    return content_hash({**{key: value[key] for key in ("project_id", "name", "business", "sources", "declaration",
                                                       "disposition", "errors", "unknown_fields", "field_provenance")},
                         "root_binding": value["freshness"]["root_binding"]})
