"""Versioned, fail-closed source planning and resolution for Hub refreshes.

The source plan grants no filesystem authority.  It binds a connection manifest
to registry declarations and to already accepted, exact static evidence.  The
resolver always rechecks those bindings before opening a project-owned file and
never imports or executes project code.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import yaml

from hub.connection_records import (RecordError, adapter_hash, content_hash, normalize_status, require,
                                    validate_record)
from hub.connections import load_registry_at, select_value

SOURCE_PLAN_VERSION = "1.0"
SOURCE_RESULT_VERSION = "1.0"
MAX_SOURCE_BYTES = 1024 * 1024
INVENTORY_PATH = "docs/reports/ui_design_governance/unit-02/ui-source-inventory.json"
DISCOVERY_PATH = "docs/reports/ui_design_governance/unit-03/source-discovery.json"
ADAPTER_PATH = "data/design_governance/connection_adapters.json"
ACCEPTED_INVENTORY_SHA256 = "5e7132ce05a5dc2a569826444ea15f67e9893c725c9ec24c884872bd93917f7c"
ACCEPTED_INVENTORY_CANDIDATE = "f0d2f820f5b3bed541cee64b617f4e23fe6b0342d1b2db1541368df31c573512"

PLAN_MODES = {"declared_source", "static_derived_operational", "accepted_inventory_absence",
              "blocked_by_authority"}
DISPOSITIONS = {"SOURCE_RESOLVED", "EXPLICIT_NO_CURRENT_SOURCE_VERIFIED", "BLOCKED_BY_AUTHORITY",
                "SOURCE_UNAVAILABLE", "VALIDATION_FAILED"}
SUCCESS_DISPOSITIONS = {"SOURCE_RESOLVED", "EXPLICIT_NO_CURRENT_SOURCE_VERIFIED"}
FORMATS = {"yaml", "json", "markdown", "text", "python"}
ROLES = {"business_state", "operational_state", "absence_evidence", "diagnostic"}
ERROR_CODES = {"SOURCE_MISSING", "SOURCE_UNAVAILABLE", "SOURCE_UNREADABLE", "SOURCE_INVALID",
               "EVIDENCE_DRIFT", "AUTHORITY_DRIFT", "STATIC_DECLARATION_INVALID"}
SENSITIVE_NAMES = {"credentials", "credential", "secrets", "secret", "cookies", "cookie",
                   "tokens", "token", ".ssh", ".aws", ".gnupg"}
FORBIDDEN_PARTS = {".git", "node_modules", "dist", "build", "target", "__pycache__", ".venv",
                   "venv", ".idea", ".cache", "cache", "logs", "outputs", "tmp"}
SHA = re.compile(r"^[0-9a-f]{64}$")
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

PLAN_FIELDS = {"schema_version", "kind", "id", "created_at", "authority", "entries", "content_hash"}
PLAN_AUTHORITY_FIELDS = {"manifest_id", "manifest_hash", "registry_path", "registry_hash",
                         "adapter_version", "adapter_hash", "accepted_inventory_path",
                         "accepted_inventory_hash", "accepted_candidate", "discovery_path",
                         "discovery_hash"}
ENTRY_FIELDS = {"project_id", "mode", "access_profile", "permission_basis", "declared_sources",
                "static_evidence", "derived_sources", "absence_files"}
DECLARED_FIELDS = {"ref", "format", "role"}
EVIDENCE_FIELDS = {"path", "sha256", "bytes"}
DERIVED_FIELDS = {"ref", "permission_ref", "evidence_path", "evidence_sha256", "declaration_name",
                  "declaration", "relative_path", "format", "field_meanings"}
ABSENCE_FIELDS = {"path", "sha256", "bytes", "content_proof"}

RESULT_FIELDS = {"schema_version", "kind", "project_id", "observed_at", "disposition", "success",
                 "authority", "business_snapshot", "sources", "evidence", "operational_facts",
                 "errors", "ui_verification", "result_hash"}
CAPSULE_FIELDS = {"source_plan_id", "source_plan_hash", "manifest_id", "manifest_hash",
                  "registry_hash", "adapter_version", "adapter_hash", "accepted_inventory_hash",
                  "accepted_candidate"}
BUSINESS_FIELDS = {"state", "snapshot", "reason"}
SOURCE_FIELDS = {"ref", "role", "format", "sha256", "bytes"}
RESULT_EVIDENCE_FIELDS = {"ref", "path", "sha256", "bytes", "meaning"}
FACT_FIELDS = {"kind", "value", "source_ref", "observed_at"}
ERROR_FIELDS = {"code", "message", "source_ref", "retryable"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _exact(value: Any, fields: set[str], label: str) -> dict:
    require(isinstance(value, dict), f"{label}: object required")
    require(set(value) == fields,
            f"{label}: exact fields required; missing={sorted(fields-set(value))}, "
            f"unknown={sorted(set(value)-fields)}")
    return value


def _fingerprint(value: Any, label: str) -> None:
    require(isinstance(value, str) and bool(SHA.fullmatch(value)), f"{label}: invalid sha256")


def _timestamp(value: Any, label: str) -> None:
    require(isinstance(value, str), f"{label}: timezone timestamp required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
                f"{label}: timezone required")
    except ValueError as exc:
        raise RecordError(f"{label}: invalid timestamp") from exc


def _string(value: Any, label: str) -> None:
    require(isinstance(value, str) and bool(value.strip()), f"{label}: nonempty string required")


def _relative_path(value: Any, label: str, suffixes: set[str] | None = None) -> str:
    _string(value, label)
    require("\\" not in value and "\x00" not in value, f"{label}: invalid separator")
    path = PurePosixPath(value)
    require(not path.is_absolute() and value == path.as_posix() and value not in {"", "."},
            f"{label}: exact relative path required")
    require(all(part not in {"", ".", ".."} for part in path.parts), f"{label}: traversal forbidden")
    lowered = {part.lower() for part in path.parts}
    require(not (lowered & SENSITIVE_NAMES), f"{label}: sensitive path forbidden")
    require(not any(part.startswith(".env") or part.endswith((".pem", ".key", ".p12", ".pfx"))
                    for part in lowered), f"{label}: sensitive path forbidden")
    require(not (set(path.parts) & FORBIDDEN_PARTS), f"{label}: forbidden path")
    if suffixes is not None:
        require(path.suffix.lower() in suffixes, f"{label}: file type forbidden")
    return value


def _inventory_ref_path(ref: str) -> str:
    """Remove a final line-number annotation without changing a real colon."""
    return re.sub(r":\d+(?:[-–]\d+)?(?:\s.*)?$", "", ref)


def _safe_relative_read(root: Path, relative: str, *, suffixes: set[str], max_bytes: int = MAX_SOURCE_BYTES,
                        authority_check: Callable[[], Any] | None = None) -> bytes:
    """Open one validated relative file through no-follow directory descriptors."""
    _relative_path(relative, "source path", suffixes)
    require(type(max_bytes) is int and 0 < max_bytes <= MAX_SOURCE_BYTES, "invalid read budget")
    if authority_check:
        authority_check()
    root_path = root.expanduser().resolve()
    require(root_path.is_absolute(), "authorized root must be absolute")
    # Opening each component with O_NOFOLLOW prevents root and descendant aliases.
    if authority_check:
        authority_check()
    descriptor = os.open(root_path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    opened: list[int] = [descriptor]
    file_descriptor: int | None = None
    try:
        for part in root_path.parts[1:]:
            if authority_check:
                authority_check()
            try:
                descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            except (FileNotFoundError, NotADirectoryError) as exc:
                if authority_check:
                    raise ConnectionError("Authorized project root is unavailable.") from exc
                raise
            opened.append(descriptor)
        parts = PurePosixPath(relative).parts
        for part in parts[:-1]:
            if authority_check:
                authority_check()
            descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            opened.append(descriptor)
        if authority_check:
            authority_check()
        file_descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                  dir_fd=descriptor)
        before = os.fstat(file_descriptor)
        require(stat.S_ISREG(before.st_mode), "source must be a regular file")
        require(before.st_size <= max_bytes, "source exceeds bounded read budget")
        if authority_check:
            authority_check()
        with os.fdopen(file_descriptor, "rb", closefd=True) as stream:
            file_descriptor = None
            data = stream.read(max_bytes + 1)
            after = os.fstat(stream.fileno())
        require(len(data) <= max_bytes, "source exceeds bounded read budget")
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
                "source changed during read")
        if authority_check:
            authority_check()
        return data
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for item in reversed(opened):
            os.close(item)


def extract_static_path_declarations(source: str) -> set[str]:
    """Return safe literal relative paths from Python AST without importing code.

    Only string literals and ``Path(literal) / literal`` chains are accepted.
    Calls, names, f-strings and computed values never become declarations.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise RecordError("static evidence Python syntax invalid") from exc

    def literal(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Path" and
                len(node.args) == 1 and not node.keywords):
            return literal(node.args[0])
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left, right = literal(node.left), literal(node.right)
            if left is not None and right is not None:
                return f"{left.rstrip('/')}/{right.lstrip('/')}"
        return None

    found: set[str] = set()
    for node in ast.walk(tree):
        value = literal(node)
        if value is None:
            continue
        try:
            found.add(_relative_path(value, "static declaration"))
        except RecordError:
            continue
    return found


def extract_static_path_assignments(source: str) -> dict[str, str]:
    """Extract only exact, module-level name assignments to safe path literals."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise RecordError("static evidence Python syntax invalid") from exc

    def literal(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Path" and
                len(node.args) == 1 and not node.keywords):
            return literal(node.args[0])
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left, right = literal(node.left), literal(node.right)
            return f"{left.rstrip('/')}/{right.lstrip('/')}" if left is not None and right is not None else None
        return None

    assignments: dict[str, str] = {}
    for node in tree.body:
        target: ast.AST | None = None
        value_node: ast.AST | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value_node = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value_node = node.target, node.value
        if not isinstance(target, ast.Name) or value_node is None:
            continue
        value = literal(value_node)
        if value is None:
            continue
        try:
            assignments[target.id] = _relative_path(value, "static assignment")
        except RecordError:
            continue
    return assignments


def extract_static_imports(source: str) -> set[tuple[str, tuple[str, ...]]]:
    """Extract module/name tuples from top-level imports without loading modules."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise RecordError("static evidence Python syntax invalid") from exc
    imports: set[tuple[str, tuple[str, ...]]] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add((node.module, tuple(alias.name for alias in node.names)))
        elif isinstance(node, ast.Import):
            imports.update((alias.name, ()) for alias in node.names)
    return imports


def _load_json_bytes(data: bytes, label: str) -> dict:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RecordError(f"{label}: invalid JSON") from exc
    require(isinstance(value, dict), f"{label}: object required")
    return value


class AuthorityDriftError(RecordError):
    """Current Hub authority no longer matches the frozen refresh request."""


def freeze_source_plan(manifest: dict, registry: dict, registry_sha256: str, adapters: dict,
                       discovery: dict, discovery_sha256: str, *, plan_id: str = "hub-source-plan-v1",
                       created_at: str | None = None) -> dict:
    """Build the complete plan from already collected static discovery.

    This helper is pure and performs no project reads.  Root supplies the exact
    bytes' digests it registered; ``SourceResolver`` later rechecks live Hub
    authority files and every permitted external evidence file.
    """
    validate_record(manifest, {p["id"] for p in registry.get("projects", [])})
    _fingerprint(registry_sha256, "registry hash")
    require(manifest["registry_ref"]["sha256"] == registry_sha256, "manifest/registry hash mismatch")
    _fingerprint(discovery_sha256, "discovery hash")
    require(isinstance(adapters, dict) and adapters.get("schema_version") == "1.0" and
            adapters.get("adapter_version") == "1.0", "adapter authority invalid")
    projects = registry.get("projects")
    require(isinstance(projects, list) and len(projects) == 24, "source plan requires all 24 registry IDs")
    project_map = {p.get("id"): p for p in projects if isinstance(p, dict)}
    require(len(project_map) == 24 and None not in project_map, "duplicate or invalid registry IDs")
    require(set(adapters.get("projects", {})) == set(project_map), "adapter coverage mismatch")

    _exact(discovery, {"schema_version", "kind", "authority", "source_inventory", "light_novel",
                       "undeclared_sources", "light_novel_findings", "external_writes", "manga_probes"},
           "static discovery")
    inventory = discovery["source_inventory"]
    require(inventory == {"path": INVENTORY_PATH, "sha256": ACCEPTED_INVENTORY_SHA256,
                          "accepted_candidate": ACCEPTED_INVENTORY_CANDIDATE},
            "accepted inventory identity mismatch")
    absence_rows = {row["project_id"]: row for row in discovery["undeclared_sources"]["results"]}
    require(set(absence_rows) == {"desktop-magnet", "pycharm-misc-project", "desktop-downloads-scripts"},
            "absence discovery coverage mismatch")
    light_sources = {row["path"]: row for row in discovery["light_novel"]["sources"]}
    anchor_path = "scripts/local_scheduler_status.py"
    tick_proof_path = "src/scheduler/status.py"
    pause_proof_path = "src/scheduler/control.py"
    require({anchor_path, tick_proof_path, pause_proof_path} <= light_sources.keys(),
            "Light Novel static declaration proof missing")
    known_targets = discovery["light_novel_findings"]["known_static_control_paths"]
    require(known_targets == ["workspace/control/scheduler_tick_state.json",
                              "workspace/control/scheduler_paused.json"],
            "Light Novel target declarations changed")

    entries = []
    for project_id, project in project_map.items():
        state_paths = project.get("current_state_paths")
        require(isinstance(state_paths, list), f"{project_id}: state declarations invalid")
        access_profile = project.get("access_profile", "registered_project_read")
        declared = []
        static_evidence = []
        derived = []
        absence = []
        if access_profile == "no_current_goal_access":
            require(project_id == "manga-localizer", "unexpected no-access project")
            mode = "blocked_by_authority"
            basis = "registry access_profile no_current_goal_access; zero filesystem probes"
        elif project_id in absence_rows:
            require(not state_paths, f"{project_id}: absence mode contradicts registry declaration")
            mode = "accepted_inventory_absence"
            basis = "accepted inventory and exact static source-discovery content proof"
            for item in absence_rows[project_id]["files"]:
                absence.append({"path": item["path"], "sha256": item["sha256"], "bytes": item["bytes"],
                                "content_proof": "readme_per_item_output_not_project_state" if
                                project_id == "desktop-magnet" else "python_ast_no_state_source_declaration"})
        elif project_id == "light-novel":
            require(len(state_paths) == 1, "Light Novel legacy diagnostic declaration required")
            mode = "static_derived_operational"
            basis = "registry declaration plus hash-bound static Python literal; no probe execution"
            declared.append({"ref": "current_state_paths[0]", "format": "yaml", "role": "diagnostic"})
            anchor_proof = light_sources[anchor_path]
            tick_proof = light_sources[tick_proof_path]
            pause_proof = light_sources[pause_proof_path]
            static_evidence.extend({key: proof[key] for key in ("path", "sha256", "bytes")}
                                   for proof in (anchor_proof, tick_proof, pause_proof))
            derived = [
                {"ref": "derived:scheduler_tick", "permission_ref": "supporting_authority_paths[5]",
                 "evidence_path": tick_proof_path,
                 "evidence_sha256": tick_proof["sha256"], "declaration_name": "TICK_STATE_REL",
                 "declaration": known_targets[0],
                 "relative_path": known_targets[0], "format": "json",
                 "field_meanings": ["last_tick_status", "last_successful_tick", "last_blocked_reason"]},
                {"ref": "derived:scheduler_pause", "permission_ref": "supporting_authority_paths[5]",
                 "evidence_path": pause_proof_path,
                 "evidence_sha256": pause_proof["sha256"], "declaration_name": "PAUSE_REL",
                 "declaration": known_targets[1],
                 "relative_path": known_targets[1], "format": "json",
                 "field_meanings": ["paused", "reason", "requested_at"]},
            ]
        else:
            require(state_paths, f"{project_id}: no declared source or accepted absence evidence")
            mode = "declared_source"
            basis = "exact registry current_state_paths declaration"
            adapter = adapters["projects"][project_id]
            require(len(state_paths) == 1, f"{project_id}: v1 supports exactly one declared source")
            declared.append({"ref": "current_state_paths[0]", "format": adapter["format"],
                             "role": "business_state"})
        entries.append({"project_id": project_id, "mode": mode, "access_profile": access_profile,
                        "permission_basis": basis, "declared_sources": declared,
                        "static_evidence": static_evidence, "derived_sources": derived,
                        "absence_files": absence})

    authority = {"manifest_id": manifest["id"], "manifest_hash": manifest["content_hash"],
                 "registry_path": "data/registry/external_projects.yaml", "registry_hash": registry_sha256,
                 "adapter_version": adapters["adapter_version"], "adapter_hash": content_hash(adapters),
                 "accepted_inventory_path": INVENTORY_PATH,
                 "accepted_inventory_hash": ACCEPTED_INVENTORY_SHA256,
                 "accepted_candidate": ACCEPTED_INVENTORY_CANDIDATE,
                 "discovery_path": DISCOVERY_PATH, "discovery_hash": discovery_sha256}
    plan = {"schema_version": SOURCE_PLAN_VERSION, "kind": "source_plan", "id": plan_id,
            "created_at": created_at or _now(), "authority": authority, "entries": entries}
    plan["content_hash"] = content_hash(plan)
    return validate_source_plan(plan)


def validate_source_plan(plan: Any) -> dict:
    _exact(plan, PLAN_FIELDS, "source plan")
    require(plan["schema_version"] == SOURCE_PLAN_VERSION and plan["kind"] == "source_plan",
            "unsupported source plan schema")
    require(isinstance(plan["id"], str) and bool(ID.fullmatch(plan["id"])), "invalid source plan ID")
    _timestamp(plan["created_at"], "source plan created_at")
    authority = _exact(plan["authority"], PLAN_AUTHORITY_FIELDS, "source plan authority")
    _string(authority["manifest_id"], "manifest ID")
    for field in ("manifest_hash", "registry_hash", "adapter_hash", "accepted_inventory_hash", "discovery_hash"):
        _fingerprint(authority[field], field)
    require(authority["registry_path"] == "data/registry/external_projects.yaml", "invalid registry reference")
    require(authority["adapter_version"] == "1.0", "unsupported adapter version")
    require(authority["accepted_inventory_path"] == INVENTORY_PATH and
            authority["accepted_inventory_hash"] == ACCEPTED_INVENTORY_SHA256 and
            authority["accepted_candidate"] == ACCEPTED_INVENTORY_CANDIDATE,
            "unaccepted source inventory")
    require(authority["discovery_path"] == DISCOVERY_PATH, "invalid static discovery reference")
    require(isinstance(plan["entries"], list) and len(plan["entries"]) == 24,
            "source plan must cover exactly 24 projects")
    ids = []
    for entry in plan["entries"]:
        _exact(entry, ENTRY_FIELDS, "source plan entry")
        _string(entry["project_id"], "project_id")
        require(entry["mode"] in PLAN_MODES, f"{entry['project_id']}: invalid source mode")
        _string(entry["access_profile"], "access_profile")
        _string(entry["permission_basis"], "permission_basis")
        ids.append(entry["project_id"])
        for item in entry["declared_sources"]:
            _exact(item, DECLARED_FIELDS, "declared source")
            require(bool(re.fullmatch(r"current_state_paths\[\d+\]", item["ref"])),
                    "invalid registry declaration reference")
            require(item["format"] in FORMATS and item["role"] in ROLES, "invalid declared source")
        for item in entry["static_evidence"]:
            _exact(item, EVIDENCE_FIELDS, "static evidence")
            _relative_path(item["path"], "static evidence path", {".py", ".md", ".yaml", ".yml", ".json"})
            _fingerprint(item["sha256"], "static evidence hash")
            require(type(item["bytes"]) is int and 0 <= item["bytes"] <= MAX_SOURCE_BYTES,
                    "invalid static evidence size")
        evidence_index = {(item["path"], item["sha256"]) for item in entry["static_evidence"]}
        for item in entry["derived_sources"]:
            _exact(item, DERIVED_FIELDS, "derived source")
            require(item["ref"] in {"derived:scheduler_tick", "derived:scheduler_pause"},
                    "invalid derived source reference")
            require(item["declaration_name"] in {"TICK_STATE_REL", "PAUSE_REL"},
                    "invalid derived declaration name")
            require(item["permission_ref"] == "supporting_authority_paths[5]",
                    "derived source lacks exact registry permission anchor")
            _relative_path(item["evidence_path"], "derived evidence", {".py"})
            _fingerprint(item["evidence_sha256"], "derived evidence hash")
            require((item["evidence_path"], item["evidence_sha256"]) in evidence_index,
                    "derived source lacks bound evidence")
            _relative_path(item["declaration"], "derived declaration")
            require(item["relative_path"] == item["declaration"], "derived target/declaration mismatch")
            _relative_path(item["relative_path"], "derived target", {".json"})
            require(item["format"] == "json", "unsupported derived source format")
            require(isinstance(item["field_meanings"], list) and item["field_meanings"] and
                    len(item["field_meanings"]) == len(set(item["field_meanings"])) and
                    all(isinstance(v, str) and v for v in item["field_meanings"]),
                    "invalid derived field meanings")
        for item in entry["absence_files"]:
            _exact(item, ABSENCE_FIELDS, "absence file")
            _relative_path(item["path"], "absence file", {".py", ".md"})
            _fingerprint(item["sha256"], "absence file hash")
            require(type(item["bytes"]) is int and 0 <= item["bytes"] <= MAX_SOURCE_BYTES,
                    "invalid absence evidence size")
            require(item["content_proof"] in {"readme_per_item_output_not_project_state",
                                               "python_ast_no_state_source_declaration"},
                    "invalid absence proof")
        expected_counts = {
            "declared_source": (1, 0, 0, 0),
            "static_derived_operational": (1, 3, 2, 0),
            "accepted_inventory_absence": (0, 0, 0, len(entry["absence_files"])),
            "blocked_by_authority": (0, 0, 0, 0),
        }[entry["mode"]]
        actual = (len(entry["declared_sources"]), len(entry["static_evidence"]),
                  len(entry["derived_sources"]), len(entry["absence_files"]))
        require(actual == expected_counts and
                (entry["mode"] != "accepted_inventory_absence" or actual[3] > 0),
                f"{entry['project_id']}: mode/source shape mismatch")
        if entry["mode"] == "blocked_by_authority":
            require(entry["access_profile"] == "no_current_goal_access", "blocked mode lacks authority basis")
    require(len(ids) == len(set(ids)), "duplicate source-plan project ID")
    _fingerprint(plan["content_hash"], "source plan hash")
    require(plan["content_hash"] == content_hash({k: v for k, v in plan.items() if k != "content_hash"}),
            "source plan hash mismatch")
    return plan


class SourceResolver:
    """Resolve one project under a frozen source plan."""

    def __init__(self, root: Path, manifest: dict, adapters: dict, plan: dict):
        self.root = Path(root)
        self.plan = copy.deepcopy(validate_source_plan(plan))
        self.manifest = copy.deepcopy(manifest)
        self.adapters = copy.deepcopy(adapters)
        self.registry, digest = load_registry_at(self.root)
        project_ids = {p["id"] for p in self.registry["projects"]}
        validate_record(self.manifest, project_ids)
        authority = self.plan["authority"]
        require(authority["manifest_id"] == manifest["id"] and
                authority["manifest_hash"] == manifest["content_hash"], "source plan manifest drift")
        require(authority["registry_hash"] == digest == manifest["registry_ref"]["sha256"],
                "source plan registry drift")
        require(authority["adapter_version"] == adapters.get("adapter_version") and
                authority["adapter_hash"] == content_hash(adapters), "source plan adapter drift")
        require({entry["project_id"] for entry in self.plan["entries"]} == project_ids,
                "source plan registry coverage mismatch")
        self.projects = {p["id"]: p for p in self.registry["projects"]}
        self.entries = {e["project_id"]: e for e in self.plan["entries"]}
        manifest_entries = {e["project_id"]: e for e in self.manifest["entries"]}
        for project_id, entry in self.entries.items():
            project = self.projects[project_id]
            require(entry["access_profile"] == project.get("access_profile", "registered_project_read"),
                    f"{project_id}: source-plan access profile drift")
            manifest_mode = manifest_entries[project_id]["permission"]["mode"]
            expected_mode = ("blocked_by_authority" if manifest_mode == "no_access" else
                             "accepted_inventory_absence" if manifest_mode == "no_source" else
                             "static_derived_operational" if project_id == "light-novel" else
                             "declared_source")
            require(entry["mode"] == expected_mode, f"{project_id}: source-plan permission mismatch")
        self._validate_hub_evidence(self.registry, digest)

    @classmethod
    def from_frozen(cls, manifest: dict, adapters: dict, plan: dict) -> "SourceResultValidator":
        """Construct a pure historical validator without reading current Hub or project files."""
        return SourceResultValidator(manifest, adapters, plan)

    @property
    def authority(self) -> dict:
        value = self.plan["authority"]
        return {"source_plan_id": self.plan["id"], "source_plan_hash": self.plan["content_hash"],
                "manifest_id": value["manifest_id"], "manifest_hash": value["manifest_hash"],
                "registry_hash": value["registry_hash"], "adapter_version": value["adapter_version"],
                "adapter_hash": value["adapter_hash"],
                "accepted_inventory_hash": value["accepted_inventory_hash"],
                "accepted_candidate": value["accepted_candidate"]}

    def _validate_hub_evidence(self, registry: dict, registry_digest: str) -> None:
        authority = self.plan["authority"]
        require(registry_digest == authority["registry_hash"], "live registry authority drift")
        adapter_raw = _safe_relative_read(self.root, ADAPTER_PATH, suffixes={".json"})
        inventory_raw = _safe_relative_read(self.root, authority["accepted_inventory_path"], suffixes={".json"})
        discovery_raw = _safe_relative_read(self.root, authority["discovery_path"], suffixes={".json"})
        current_adapters = _load_json_bytes(adapter_raw, "current adapter authority")
        require(current_adapters == self.adapters and content_hash(current_adapters) == authority["adapter_hash"],
                "live adapter authority drift")
        require(_hash(inventory_raw) == authority["accepted_inventory_hash"], "accepted inventory drift")
        require(_hash(discovery_raw) == authority["discovery_hash"], "static discovery drift")
        inventory = _load_json_bytes(inventory_raw, "accepted inventory")
        discovery = _load_json_bytes(discovery_raw, "static discovery")
        require(discovery.get("source_inventory") == {
            "path": INVENTORY_PATH, "sha256": authority["accepted_inventory_hash"],
            "accepted_candidate": authority["accepted_candidate"]}, "discovery inventory binding drift")
        expected_plan = freeze_source_plan(
            self.manifest, registry, authority["registry_hash"], current_adapters, discovery,
            authority["discovery_hash"], plan_id=self.plan["id"], created_at=self.plan["created_at"])
        require(expected_plan == self.plan,
                "source plan differs from registry and hash-bound static discovery")
        inventory_rows = {row.get("project_id"): row for row in inventory.get("projects", [])
                          if isinstance(row, dict)}
        discovery_rows = {row.get("project_id"): row for row in
                          discovery.get("undeclared_sources", {}).get("results", []) if isinstance(row, dict)}
        for project_id in ("desktop-magnet", "pycharm-misc-project", "desktop-downloads-scripts"):
            planned = self.entries[project_id]["absence_files"]
            source_refs = {_inventory_ref_path(ref) for ref in inventory_rows.get(project_id, {}).get("source_refs", [])}
            observed = {(item.get("path"), item.get("sha256"), item.get("bytes")) for item in
                        discovery_rows.get(project_id, {}).get("files", [])}
            require(all(item["path"] in source_refs for item in planned),
                    f"{project_id}: absence path is not accepted inventory evidence")
            require(all((item["path"], item["sha256"], item["bytes"]) in observed for item in planned),
                    f"{project_id}: absence evidence discovery drift")

    def _check_live_authority(self, project_id: str) -> dict:
        """Re-read all Hub authority before one external path operation."""
        try:
            registry, digest = load_registry_at(self.root)
            self._validate_hub_evidence(registry, digest)
            projects = {project["id"]: project for project in registry["projects"]}
            require(project_id in projects, "project removed from live registry")
            project = projects[project_id]
            entry = self.entries[project_id]
            require(project.get("access_profile", "registered_project_read") == entry["access_profile"],
                    "live project access profile drift")
            require(project.get("enabled") is True and project.get("summary_enabled") is True,
                    "live project read permission revoked")
            return project
        except AuthorityDriftError:
            raise
        except (RecordError, OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
            raise AuthorityDriftError("live Hub source authority changed or became unavailable") from exc

    def _empty_result(self, project_id: str, observed_at: str) -> dict:
        return {"schema_version": SOURCE_RESULT_VERSION, "kind": "source_resolution",
                "project_id": project_id, "observed_at": observed_at, "disposition": "VALIDATION_FAILED",
                "success": False, "authority": self.authority,
                "business_snapshot": {"state": "unknown", "snapshot": None,
                                      "reason": "No verified business snapshot is available."},
                "sources": [], "evidence": [], "operational_facts": [], "errors": [],
                "ui_verification": "UNVERIFIED", "result_hash": "0" * 64}

    @staticmethod
    def _error(code: str, message: str, source_ref: str | None, retryable: bool) -> dict:
        return {"code": code, "message": message, "source_ref": source_ref, "retryable": retryable}

    @staticmethod
    def _finish(result: dict) -> dict:
        result["result_hash"] = content_hash({k: v for k, v in result.items() if k != "result_hash"})
        return result

    def _failure(self, result: dict, exc: Exception, source_ref: str | None = None) -> dict:
        if isinstance(exc, AuthorityDriftError):
            code, disposition, message, retryable = (
                "AUTHORITY_DRIFT", "VALIDATION_FAILED", "Live Hub source authority no longer matches this request.", False)
        elif isinstance(exc, FileNotFoundError):
            code, disposition, message, retryable = "SOURCE_MISSING", "SOURCE_UNAVAILABLE", "Named source is missing.", True
        elif isinstance(exc, PermissionError):
            code, disposition, message, retryable = "SOURCE_UNREADABLE", "SOURCE_UNAVAILABLE", "Named source is unreadable.", True
        elif isinstance(exc, (ConnectionError, OSError)) and not isinstance(exc, RecordError):
            code, disposition, message, retryable = "SOURCE_UNAVAILABLE", "SOURCE_UNAVAILABLE", "Authorized source is unavailable.", True
        else:
            code, disposition, retryable = "SOURCE_INVALID", "VALIDATION_FAILED", False
            message = str(exc) if isinstance(exc, RecordError) else "Source validation failed."
        result["disposition"] = disposition
        result["success"] = False
        result["business_snapshot"] = {"state": "unknown", "snapshot": None, "reason": message}
        # Partial reads are not a resolution.  Keeping them here would let a
        # failed envelope masquerade as durable evidence on ledger readback.
        result["sources"] = []
        result["evidence"] = []
        result["operational_facts"] = []
        result["errors"] = []
        result["errors"].append(self._error(code, message, source_ref, retryable))
        return self._finish(result)

    def refresh(self, project_id: str) -> dict:
        require(project_id in self.projects, "unknown project ID")
        # No-access rejection precedes root expansion, path resolution and evidence reads.
        entry = self.entries[project_id]
        observed = _now()
        result = self._empty_result(project_id, observed)
        if entry["mode"] == "blocked_by_authority":
            result["disposition"] = "BLOCKED_BY_AUTHORITY"
            result["business_snapshot"]["reason"] = "Registry denies current-goal filesystem access."
            return self.validate_result(self._finish(result))
        try:
            self._check_live_authority(project_id)
            if entry["mode"] == "declared_source":
                self._refresh_declared(result, project_id)
            elif entry["mode"] == "accepted_inventory_absence":
                self._refresh_absence(result, project_id, entry)
            elif entry["mode"] == "static_derived_operational":
                self._refresh_operational(result, project_id, entry)
            else:  # validated plans make this unreachable; keep runtime fail-closed.
                raise RecordError("unsupported source-plan mode")
            return self.validate_result(self._finish(result))
        except Exception as exc:  # normalize I/O/parser errors into nonterminal result records
            return self.validate_result(self._failure(result, exc))

    def _refresh_declared(self, result: dict, project_id: str) -> None:
        snapshot = self._read_declared_snapshot(project_id, result["observed_at"])
        if snapshot["availability"] != "fresh":
            result["business_snapshot"]["reason"] = snapshot["refresh_error"]
            if snapshot["availability"] == "invalid":
                raise RecordError(snapshot["refresh_error"])
            raise ConnectionError(snapshot["refresh_error"])
        result["disposition"] = "SOURCE_RESOLVED"
        result["success"] = True
        result["business_snapshot"] = {"state": "v1_snapshot", "snapshot": copy.deepcopy(snapshot), "reason": None}
        adapter = self.adapters["projects"][project_id]
        for source in snapshot["sources"]:
            result["sources"].append({"ref": source["ref"], "role": "business_state",
                                      "format": adapter["format"], "sha256": source["sha256"],
                                      "bytes": source["bytes"]})

    def _read_declared_snapshot(self, project_id: str, observed: str) -> dict:
        """Build the immutable v1 snapshot with live checks around every file operation."""
        project = self._check_live_authority(project_id)
        adapter = self.adapters["projects"][project_id]
        require(len(project["current_state_paths"]) == 1, "v1 declared source count changed")
        raw = Path(project["current_state_paths"][0]).expanduser()
        root = Path(project["root_path"]).expanduser()
        self._check_live_authority(project_id)
        resolved_root = root.resolve()
        self._check_live_authority(project_id)
        resolved_path = raw.resolve()
        require(resolved_path.is_relative_to(resolved_root) and resolved_path != resolved_root,
                "declared source escapes authorized root")
        relative = resolved_path.relative_to(resolved_root).as_posix()
        suffixes = {".yaml", ".yml", ".json", ".md", ".txt"}
        data = _safe_relative_read(
            resolved_root, relative, suffixes=suffixes,
            authority_check=lambda: self._check_live_authority(project_id))
        source = {"ref": "current_state_paths[0]", "path": str(resolved_path),
                  "sha256": _hash(data), "bytes": len(data)}
        snapshot = {"schema_version": "1.0", "record_type": "project_snapshot",
                    "id": f"snapshot-{project_id}", "created_at": observed, "project_id": project_id,
                    "manifest_id": self.manifest["id"], "manifest_hash": self.manifest["content_hash"],
                    "adapter_version": self.adapters["adapter_version"],
                    "adapter_hash": adapter_hash(self.adapters, project_id), "source_role": adapter["role"],
                    "raw_status": None, "normalized_status": "unknown", "next_action": None,
                    "next_action_kind": "unknown", "unknown_fields": {}, "blockers": None,
                    "availability": "fresh", "sources": [source], "observed_at": observed,
                    "last_success_at": observed, "refresh_error": None, "relations": [], "designs": []}
        try:
            text = data.decode("utf-8")
            fmt = adapter["format"]
            require(fmt in {"yaml", "json", "markdown"}, "unsupported declared source format")
            parsed = yaml.safe_load(text) if fmt == "yaml" else json.loads(text) if fmt == "json" else None
            if fmt in {"yaml", "json"}:
                require(isinstance(parsed, dict), "structured source must be an object")
            snapshot["raw_status"] = select_value(parsed, text, adapter.get("status"))
            snapshot["next_action"] = select_value(parsed, text, adapter.get("next"))
            snapshot["normalized_status"] = normalize_status(snapshot["raw_status"])
            snapshot["next_action_kind"] = adapter.get("next_kind", "explicit") if snapshot["next_action"] else "unknown"
        except (yaml.YAMLError, json.JSONDecodeError, UnicodeError):
            snapshot.update(availability="invalid", last_success_at=None,
                            refresh_error="Source syntax or encoding is invalid; no business state inferred.")
        for field in ("raw_status", "next_action"):
            if snapshot[field] is None:
                snapshot["unknown_fields"][field] = snapshot["refresh_error"] or adapter.get("unknown") or "Field absent."
        snapshot["unknown_fields"]["blockers"] = snapshot["refresh_error"] or "Blockers are not extracted by this adapter."
        if snapshot["normalized_status"] == "unknown":
            snapshot["unknown_fields"]["normalized_status"] = snapshot["refresh_error"] or "Status is not mapped."
        return validate_record(snapshot, set(self.entries))

    def _refresh_absence(self, result: dict, project_id: str, entry: dict) -> None:
        for item in entry["absence_files"]:
            project = self._check_live_authority(project_id)
            project_root = Path(project["root_path"])
            data = _safe_relative_read(
                project_root, item["path"], suffixes={".md", ".py"},
                authority_check=lambda: self._check_live_authority(project_id))
            require(len(data) == item["bytes"] and _hash(data) == item["sha256"],
                    "accepted absence evidence content drift")
            if item["content_proof"] == "python_ast_no_state_source_declaration":
                declarations = extract_static_path_declarations(data.decode("utf-8"))
                require(not any(PurePosixPath(value).name.lower() in
                                {"state.yaml", "state.yml", "state.json", "project_status.md"}
                                for value in declarations), "static source now declares a state path")
            result["evidence"].append({"ref": f"accepted_inventory:{project_id}:{item['path']}",
                                       "path": item["path"], "sha256": item["sha256"],
                                       "bytes": item["bytes"], "meaning": item["content_proof"]})
        result["disposition"] = "EXPLICIT_NO_CURRENT_SOURCE_VERIFIED"
        result["success"] = True
        result["business_snapshot"]["reason"] = (
            "Exact accepted entry content contains no current project-state source; business state is unknown.")

    def _refresh_operational(self, result: dict, project_id: str, entry: dict) -> None:
        project = self._check_live_authority(project_id)
        project_root = Path(project["root_path"])
        permission_ref = entry["derived_sources"][0]["permission_ref"]
        match = re.fullmatch(r"supporting_authority_paths\[(\d+)\]", permission_ref)
        require(match is not None, "invalid derived permission reference")
        declared_anchor = Path(project["supporting_authority_paths"][int(match.group(1))])
        project = self._check_live_authority(project_id)
        require(project["root_path"] == str(project_root), "live Light Novel root changed")
        resolved_root = project_root.expanduser().resolve()
        project = self._check_live_authority(project_id)
        require(project["root_path"] == str(project_root) and
                project["supporting_authority_paths"][int(match.group(1))] == str(declared_anchor),
                "live Light Novel root or anchor changed")
        resolved_anchor = declared_anchor.expanduser().resolve()
        require(resolved_anchor.is_relative_to(resolved_root) and
                resolved_anchor.relative_to(resolved_root).as_posix() == "scripts/local_scheduler_status.py",
                "derived permission anchor escapes registry root or names a different file")
        self._check_live_authority(project_id)
        legacy = self._read_declared_snapshot(project_id, result["observed_at"])
        for source in legacy["sources"]:
            result["sources"].append({"ref": source["ref"], "role": "diagnostic", "format": "yaml",
                                      "sha256": source["sha256"], "bytes": source["bytes"]})
        legacy_message = ("Historical governance YAML parsed but is not current business-state authority."
                          if legacy["availability"] == "fresh" else
                          "Historical governance YAML is invalid or unavailable and remains diagnostic only.")
        result["errors"].append(self._error("SOURCE_INVALID", legacy_message,
                                             "current_state_paths[0]", False))
        declarations: dict[str, dict[str, str]] = {}
        imports: dict[str, set[tuple[str, tuple[str, ...]]]] = {}
        for evidence in entry["static_evidence"]:
            project = self._check_live_authority(project_id)
            project_root = Path(project["root_path"])
            data = _safe_relative_read(
                project_root, evidence["path"], suffixes={".py"},
                authority_check=lambda: self._check_live_authority(project_id))
            require(len(data) == evidence["bytes"] and _hash(data) == evidence["sha256"],
                    "static derivation evidence content drift")
            declarations[evidence["path"]] = extract_static_path_assignments(data.decode("utf-8"))
            imports[evidence["path"]] = extract_static_imports(data.decode("utf-8"))
            result["evidence"].append({"ref": f"static:{project_id}:{evidence['path']}",
                                       "path": evidence["path"], "sha256": evidence["sha256"],
                                       "bytes": evidence["bytes"], "meaning": "derived_path_declaration"})
        require(any(module == "scheduler.status" and "collect_status" in names
                    for module, names in imports["scripts/local_scheduler_status.py"]),
                "registry-named scheduler diagnostic lacks approved status import")
        require(any(module == "scheduler.control" and {"is_paused", "lock_status"} <= set(names)
                    for module, names in imports["src/scheduler/status.py"]),
                "scheduler status lacks approved control import")
        for derived in entry["derived_sources"]:
            require(declarations[derived["evidence_path"]].get(derived["declaration_name"]) ==
                    derived["declaration"], "derived target lacks exact named safe static declaration")
            project = self._check_live_authority(project_id)
            project_root = Path(project["root_path"])
            data = _safe_relative_read(
                project_root, derived["relative_path"], suffixes={".json"},
                authority_check=lambda: self._check_live_authority(project_id))
            parsed = _load_json_bytes(data, derived["ref"])
            source = {"ref": derived["ref"], "role": "operational_state", "format": "json",
                      "sha256": _hash(data), "bytes": len(data)}
            result["sources"].append(source)
            if derived["ref"] == "derived:scheduler_tick":
                allowed = {"last_tick_status", "last_successful_tick", "last_blocked_reason"}
                require(allowed <= parsed.keys(), "scheduler tick fields missing")
                value = {key: parsed[key] for key in sorted(allowed)}
                require(all(v is None or isinstance(v, str) for v in value.values()),
                        "scheduler tick field type invalid")
                kind = "scheduler_tick"
            else:
                allowed = {"paused", "reason", "requested_at"}
                require(allowed <= parsed.keys() and type(parsed["paused"]) is bool and
                        all(parsed[key] is None or isinstance(parsed[key], str) for key in ("reason", "requested_at")),
                        "scheduler pause field type invalid")
                value = {key: parsed[key] for key in sorted(allowed)}
                kind = "scheduler_pause"
            result["operational_facts"].append({"kind": kind, "value": value,
                                                "source_ref": derived["ref"],
                                                "observed_at": result["observed_at"]})
        result["disposition"] = "SOURCE_RESOLVED"
        result["success"] = True
        result["business_snapshot"]["reason"] = (
            "Scheduler operational facts resolved; historical governance YAML is diagnostic and business state remains unknown.")

    def validate_result(self, result: Any) -> dict:
        """Pure validation for ledger readback; this method performs no filesystem access."""
        _exact(result, RESULT_FIELDS, "source resolution")
        require(result["schema_version"] == SOURCE_RESULT_VERSION and result["kind"] == "source_resolution",
                "unsupported source-resolution schema")
        require(result["project_id"] in self.entries, "unknown source-resolution project")
        _timestamp(result["observed_at"], "source resolution observed_at")
        require(result["disposition"] in DISPOSITIONS, "invalid source disposition")
        require(type(result["success"]) is bool and
                result["success"] == (result["disposition"] in SUCCESS_DISPOSITIONS),
                "source success/disposition mismatch")
        _exact(result["authority"], CAPSULE_FIELDS, "source authority capsule")
        require(result["authority"] == self.authority, "source authority capsule mismatch")
        business = _exact(result["business_snapshot"], BUSINESS_FIELDS, "business snapshot")
        require(business["state"] in {"v1_snapshot", "unknown"}, "invalid business snapshot state")
        if business["state"] == "v1_snapshot":
            require(result["disposition"] == "SOURCE_RESOLVED" and business["reason"] is None,
                    "v1 snapshot semantic mismatch")
            validate_record(business["snapshot"], set(self.entries))
            snapshot = business["snapshot"]
            require(snapshot["schema_version"] == "1.0" and snapshot["record_type"] == "project_snapshot" and
                    snapshot["project_id"] == result["project_id"] and
                    snapshot["manifest_id"] == self.authority["manifest_id"] and
                    snapshot["manifest_hash"] == self.authority["manifest_hash"] and
                    snapshot["adapter_version"] == self.authority["adapter_version"] and
                    snapshot["adapter_hash"] == adapter_hash(self.adapters, result["project_id"]),
                    "embedded v1 snapshot authority mismatch")
        else:
            require(business["snapshot"] is None, "unknown business state cannot contain a snapshot")
            _string(business["reason"], "unknown business reason")
        require(isinstance(result["sources"], list), "sources list required")
        refs = []
        for source in result["sources"]:
            _exact(source, SOURCE_FIELDS, "resolved source")
            _string(source["ref"], "source ref")
            require(source["role"] in ROLES and source["format"] in FORMATS, "invalid resolved source")
            _fingerprint(source["sha256"], "source hash")
            require(type(source["bytes"]) is int and 0 <= source["bytes"] <= MAX_SOURCE_BYTES,
                    "invalid source size")
            refs.append(source["ref"])
        require(len(refs) == len(set(refs)), "duplicate resolved source reference")
        require(isinstance(result["evidence"], list), "evidence list required")
        for evidence in result["evidence"]:
            _exact(evidence, RESULT_EVIDENCE_FIELDS, "resolution evidence")
            _string(evidence["ref"], "evidence ref")
            _relative_path(evidence["path"], "evidence path")
            _fingerprint(evidence["sha256"], "evidence hash")
            require(type(evidence["bytes"]) is int and 0 <= evidence["bytes"] <= MAX_SOURCE_BYTES,
                    "invalid evidence size")
            _string(evidence["meaning"], "evidence meaning")
        require(isinstance(result["operational_facts"], list), "operational facts list required")
        fact_kinds = []
        for fact in result["operational_facts"]:
            _exact(fact, FACT_FIELDS, "operational fact")
            require(fact["kind"] in {"scheduler_tick", "scheduler_pause"} and
                    isinstance(fact["value"], dict), "invalid operational fact")
            _string(fact["source_ref"], "operational source ref")
            require(fact["source_ref"] in refs, "operational fact source missing")
            _timestamp(fact["observed_at"], "operational fact observed_at")
            require(fact["observed_at"] == result["observed_at"], "operational observation mismatch")
            fact_kinds.append(fact["kind"])
        require(len(fact_kinds) == len(set(fact_kinds)), "duplicate operational fact")
        require(isinstance(result["errors"], list), "errors list required")
        for error in result["errors"]:
            _exact(error, ERROR_FIELDS, "source error")
            require(error["code"] in ERROR_CODES, "invalid source error code")
            _string(error["message"], "source error message")
            require(error["source_ref"] is None or isinstance(error["source_ref"], str),
                    "invalid error source ref")
            require(type(error["retryable"]) is bool, "invalid error retryability")
        require(result["ui_verification"] == "UNVERIFIED", "source resolution cannot claim UI verification")

        mode = self.entries[result["project_id"]]["mode"]
        entry = self.entries[result["project_id"]]
        allowed_by_mode = {
            "declared_source": {"SOURCE_RESOLVED", "SOURCE_UNAVAILABLE", "VALIDATION_FAILED"},
            "static_derived_operational": {"SOURCE_RESOLVED", "SOURCE_UNAVAILABLE", "VALIDATION_FAILED"},
            "accepted_inventory_absence": {"EXPLICIT_NO_CURRENT_SOURCE_VERIFIED", "SOURCE_UNAVAILABLE",
                                             "VALIDATION_FAILED"},
            "blocked_by_authority": {"BLOCKED_BY_AUTHORITY"},
        }
        require(result["disposition"] in allowed_by_mode[mode], "source disposition contradicts plan mode")
        if result["disposition"] == "EXPLICIT_NO_CURRENT_SOURCE_VERIFIED":
            expected_evidence = [{"ref": f"accepted_inventory:{result['project_id']}:{item['path']}",
                                  "path": item["path"], "sha256": item["sha256"],
                                  "bytes": item["bytes"], "meaning": item["content_proof"]}
                                 for item in entry["absence_files"]]
            require(mode == "accepted_inventory_absence" and result["evidence"] == expected_evidence and
                    business["state"] == "unknown" and not result["sources"] and
                    not result["operational_facts"] and not result["errors"],
                    "absence result lacks exact evidence")
        if result["disposition"] == "BLOCKED_BY_AUTHORITY":
            require(mode == "blocked_by_authority" and not result["sources"] and not result["evidence"] and
                    not result["operational_facts"] and not result["errors"],
                    "authority-blocked result contains forged observations")
        if mode == "static_derived_operational" and result["success"]:
            expected_evidence = [{"ref": f"static:{result['project_id']}:{item['path']}",
                                  "path": item["path"], "sha256": item["sha256"],
                                  "bytes": item["bytes"], "meaning": "derived_path_declaration"}
                                 for item in entry["static_evidence"]]
            derived_by_ref = {item["ref"]: item for item in entry["derived_sources"]}
            source_by_ref = {item["ref"]: item for item in result["sources"]}
            require(set(derived_by_ref) <= source_by_ref.keys() and
                    all(source_by_ref[ref]["role"] == "operational_state" and
                        source_by_ref[ref]["format"] == derived_by_ref[ref]["format"]
                        for ref in derived_by_ref), "derived sources do not match source plan")
            require(set(source_by_ref) <= set(derived_by_ref) | {"current_state_paths[0]"} and
                    ("current_state_paths[0]" not in source_by_ref or
                     (source_by_ref["current_state_paths[0]"]["role"] == "diagnostic" and
                      source_by_ref["current_state_paths[0]"]["format"] == "yaml")),
                    "unexpected Light Novel source")
            require(result["evidence"] == expected_evidence, "operational static evidence mismatch")
            fact_by_kind = {fact["kind"]: fact for fact in result["operational_facts"]}
            require(business["state"] == "unknown" and set(fact_by_kind) ==
                    {"scheduler_tick", "scheduler_pause"},
                    "Light Novel operational facts incomplete or forged as business state")
            require(fact_by_kind["scheduler_tick"]["source_ref"] == "derived:scheduler_tick" and
                    set(fact_by_kind["scheduler_tick"]["value"]) ==
                    {"last_tick_status", "last_successful_tick", "last_blocked_reason"} and
                    all(value is None or isinstance(value, str)
                        for value in fact_by_kind["scheduler_tick"]["value"].values()),
                    "scheduler tick fact schema mismatch")
            pause_value = fact_by_kind["scheduler_pause"]["value"]
            require(fact_by_kind["scheduler_pause"]["source_ref"] == "derived:scheduler_pause" and
                    set(pause_value) == {"paused", "reason", "requested_at"} and
                    type(pause_value["paused"]) is bool and
                    all(pause_value[key] is None or isinstance(pause_value[key], str)
                        for key in ("reason", "requested_at")), "scheduler pause fact schema mismatch")
            require(len(result["errors"]) == 1 and result["errors"][0]["code"] == "SOURCE_INVALID" and
                    result["errors"][0]["source_ref"] == "current_state_paths[0]" and
                    result["errors"][0]["retryable"] is False,
                    "Light Novel diagnostic error mismatch")
        if result["success"] and mode == "declared_source":
            declared = entry["declared_sources"]
            snapshot_sources = business["snapshot"]["sources"] if business["state"] == "v1_snapshot" else []
            require([source["ref"] for source in snapshot_sources] == [item["ref"] for item in declared],
                    "embedded v1 source refs differ from source plan")
            expected_sources = [{"ref": source["ref"], "role": declared[0]["role"],
                                 "format": declared[0]["format"], "sha256": source["sha256"],
                                 "bytes": source["bytes"]} for source in snapshot_sources]
            require(business["state"] == "v1_snapshot" and result["sources"] == expected_sources and
                    bool(result["sources"]) and not result["evidence"] and
                    not result["operational_facts"] and not result["errors"],
                    "declared-source success lacks v1 snapshot")
        if not result["success"] and result["disposition"] not in {"BLOCKED_BY_AUTHORITY"}:
            require(bool(result["errors"]) and business["state"] == "unknown" and
                    not result["sources"] and not result["evidence"] and
                    not result["operational_facts"],
                    "nonterminal source failure requires one clean unknown result")
        _fingerprint(result["result_hash"], "source result hash")
        require(result["result_hash"] == content_hash({k: v for k, v in result.items()
                                                       if k != "result_hash"}),
                "source result hash mismatch")
        return result


class SourceResultValidator:
    """Pure validator for frozen ledger history, independent of live authority drift."""

    def __init__(self, manifest: dict, adapters: dict, plan: dict):
        self.plan = copy.deepcopy(validate_source_plan(plan))
        self.manifest = copy.deepcopy(manifest)
        self.adapters = copy.deepcopy(adapters)
        project_ids = {entry["project_id"] for entry in self.plan["entries"]}
        validate_record(self.manifest, project_ids)
        authority = self.plan["authority"]
        require(authority["manifest_id"] == self.manifest["id"] and
                authority["manifest_hash"] == self.manifest["content_hash"],
                "frozen validator manifest mismatch")
        require(self.manifest["registry_ref"]["sha256"] == authority["registry_hash"],
                "frozen validator registry mismatch")
        require(authority["adapter_version"] == self.adapters.get("adapter_version") and
                authority["adapter_hash"] == content_hash(self.adapters) and
                set(self.adapters.get("projects", {})) == project_ids,
                "frozen validator adapter mismatch")
        self.entries = {entry["project_id"]: entry for entry in self.plan["entries"]}
        manifest_entries = {entry["project_id"]: entry for entry in self.manifest["entries"]}
        for project_id, entry in self.entries.items():
            permission = manifest_entries[project_id]["permission"]
            expected_mode = ("blocked_by_authority" if permission["mode"] == "no_access" else
                             "accepted_inventory_absence" if permission["mode"] == "no_source" else
                             "static_derived_operational" if project_id == "light-novel" else
                             "declared_source")
            require(entry["mode"] == expected_mode and entry["access_profile"] == permission["access_profile"],
                    f"{project_id}: frozen source-plan permission mismatch")
            require([item["ref"] for item in entry["declared_sources"]] ==
                    manifest_entries[project_id]["allowed_entries"],
                    f"{project_id}: frozen declared-source references mismatch")

    @property
    def authority(self) -> dict:
        return SourceResolver.authority.fget(self)  # type: ignore[union-attr]

    def validate_result(self, result: Any) -> dict:
        return SourceResolver.validate_result(self, result)
