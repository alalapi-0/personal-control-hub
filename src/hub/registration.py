"""Explicit metadata registration in the sole Hub registry; no project content I/O."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import os
import stat
import unicodedata
import uuid
from contextlib import contextmanager
from pathlib import Path

import yaml

from hub.connection_records import (FORBIDDEN_PARTS, RecordError, credential_path_component,
                                    fingerprint, identifier, relative_path, require, string)
from hub.connection_sources import MAX_SOURCE_BYTES, _root_path_allowed
from hub.connections import parse_source, validate_registry_value

REGISTRY_NAME = "external_projects.yaml"


class RegistrationError(RecordError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _single_line(value: str) -> str:
    string(value, "path")
    require(not any(unicodedata.category(char).startswith("C") or char in "\u2028\u2029"
                    for char in value), "path contains control characters")
    return value


def _normalized_root(value: str) -> Path:
    value = _single_line(value)
    path = Path(value)
    require(path.is_absolute(), "root_path must be absolute")
    require(".." not in path.parts, "root_path cannot contain parent traversal")
    # Linux treats repeated leading slashes as the same absolute root.
    normalized = Path("/" + os.path.normpath(value).lstrip("/"))
    _root_path_allowed(normalized)
    return normalized


def _eligible(row: dict) -> bool:
    presence = row.get("local_presence", {}).get("status", "")
    labels = (presence, row.get("current_state_status", ""), row.get("project_type", ""),
              row.get("access_profile", ""))
    legacy = (row.get("legacy") is True or "legacy_hub_connection_exception" in row
              or any("legacy" in str(label).casefold() for label in labels))
    return (not legacy and "removed_local" not in labels
            and row.get("enabled") is True and row.get("summary_enabled", True) is True
            and row.get("connection_read_allowed") is True
            and row.get("access_profile") != "no_current_goal_access")


def _routes(entry: dict, root: Path) -> None:
    for key in ("current_state_paths", "rules_paths", "supporting_authority_paths", "watch_paths"):
        if key not in entry:
            continue
        values = entry[key]
        require(type(values) is list and (key != "current_state_paths" or bool(values)),
                "registered paths must be explicit lists")
        for raw in values:
            path = Path(_single_line(raw))
            require(".." not in path.parts, "registered route cannot contain parent traversal")
            if path.is_absolute():
                try:
                    relative = str(path.relative_to(root))
                except ValueError as exc:
                    raise RegistrationError("ROUTE_OUTSIDE_PROJECT") from exc
            else:
                relative = raw
            if key == "watch_paths":
                # Watch directories are metadata, not source-file routes; no
                # scanner is enabled by registering them.
                require(relative == "." or (str(Path(relative)) == relative
                        and not Path(relative).is_absolute()
                        and all(part.lower() not in FORBIDDEN_PARTS
                                and not credential_path_component(part)
                                for part in Path(relative).parts)), "unsafe watch route")
            else:
                relative_path(relative)


@contextmanager
def _registry_directory(root: Path):
    root = Path(root).absolute()
    require(".." not in root.parts, "Hub root cannot contain parent traversal")
    descriptor = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
    opened = [descriptor]
    try:
        for part in (*root.parts[1:], "data", "registry"):
            descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            opened.append(descriptor)
        yield descriptor
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def _identity(info) -> tuple:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_registry(directory: int) -> tuple[bytes, os.stat_result]:
    descriptor = os.open(REGISTRY_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and before.st_size <= MAX_SOURCE_BYTES, "registry must be bounded ordinary singly-linked file")
        data = stream.read(MAX_SOURCE_BYTES + 1)
        after = os.fstat(stream.fileno())
    require(len(data) <= MAX_SOURCE_BYTES and _identity(before) == _identity(after),
            "registry changed while reading")
    return data, before


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _result(entry: dict, old: dict | None, before: bytes, candidate: bytes, warnings: list,
            *, applied: bool = False) -> dict:
    return {"applied": applied, "change_kind": ("added" if old is None else
            "unchanged" if old == entry else "migrated" if old["root_path"] != entry["root_path"] else "updated"),
            "id_mapping": {"old_id": old["id"] if old else None, "new_id": entry["id"]},
            "old_registry_hash": _digest(before), "new_registry_hash": _digest(candidate),
            "registry_preimage": before.decode("utf-8"), "warnings": warnings}


def register_project(root: Path, entry: dict, expected_registry_hash: str, *, apply: bool = False) -> dict:
    """Check or atomically replace one authorized metadata row, preserving all other data."""
    require(type(entry) is dict and type(apply) is bool, "invalid registration arguments")
    entry = copy.deepcopy(entry)
    require(type(entry.get("local_presence", {})) is dict, "local presence must be metadata object")
    fingerprint(expected_registry_hash, "expected registry hash")
    identifier(entry.get("id"), "project")
    with _registry_directory(root) as directory:
        # Lock the stable containing directory, not the inode replaced by commit.
        if apply:
            try:
                fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RegistrationError("REGISTRY_BUSY") from exc
        before, info = _read_registry(directory)
        if _digest(before) != expected_registry_hash:
            raise RegistrationError("REGISTRY_CONFLICT")
        registry = validate_registry_value(parse_source(before, "yaml"))
        rows = registry["projects"]
        old = next((row for row in rows if row["id"] == entry["id"]), None)
        if old is not None and not _eligible(old):
            if entry != old:
                raise RegistrationError("PROTECTED_REGISTRATION")
            return _result(entry, old, before, before,
                           [{"code": "physical_identity_unverified", "project_id": entry["id"]}])
        require({"id", "name", "root_path", "enabled", "connection_read_allowed", "current_state_paths"}
                <= set(entry), "complete registration metadata required")
        for key in registry.get("required_project_fields", []):
            require(key in entry, "registry-required metadata field missing")
        for key in ("scan_enabled", "external_write_allowed"):
            if entry.get(key) is True and (old is None or old.get(key) is not True):
                raise RegistrationError("PERMISSION_EXPANSION_FORBIDDEN")
        require(type(entry["connection_read_allowed"]) is bool, "explicit read permission required")
        candidate_root = _normalized_root(entry["root_path"])
        _routes(entry, candidate_root)
        candidate_registry = copy.deepcopy(registry)
        if old is None:
            candidate_registry["projects"].append(copy.deepcopy(entry))
        else:
            candidate_registry["projects"] = [copy.deepcopy(entry) if row["id"] == entry["id"] else row
                                              for row in candidate_registry["projects"]]
        validate_registry_value(candidate_registry)
        if "required_project_fields" in registry:
            from hub.services.project_registry_service import validate_registry
            require(validate_registry(candidate_registry, check_paths=False)["valid"],
                    "registration fails the existing registry metadata contract")
        warnings = []
        identities = {}
        normalized = {}
        for row in [*rows, entry]:
            if row["id"] == entry["id"] and row is not entry:
                continue
            # Lexical comparison is metadata only, even for protected identities.
            raw = row.get("root_path")
            if type(raw) is str and Path(raw).is_absolute():
                lexical = Path("/" + os.path.normpath(raw).lstrip("/"))
                if row["id"] != entry["id"]:
                    if lexical == candidate_root:
                        raise RegistrationError("ROOT_ALREADY_REGISTERED")
                    if lexical in candidate_root.parents or candidate_root in lexical.parents:
                        warnings.append({"code": "nested_root_review", "project_id": row["id"]})
                normalized[row["id"]] = lexical
            if not _eligible(row):
                warnings.append({"code": "physical_identity_unverified", "project_id": row["id"]})
                continue
            try:
                registered = _normalized_root(row["root_path"])
                resolved = registered.resolve(strict=True)
                _root_path_allowed(resolved)
                metadata = resolved.stat()
                require(stat.S_ISDIR(metadata.st_mode), "registered root must be directory")
                identities[row["id"]] = (metadata.st_dev, metadata.st_ino)
                normalized[row["id"]] = resolved
            except RecordError:
                if row is entry:
                    raise
                warnings.append({"code": "physical_identity_unverified", "project_id": row["id"]})
            except OSError:
                warnings.append({"code": "physical_identity_unverified", "project_id": row["id"]})
        candidate_identity = identities.get(entry["id"])
        if candidate_identity is not None:
            for project_id, identity in identities.items():
                if project_id != entry["id"] and identity == candidate_identity:
                    raise RegistrationError("ROOT_IDENTITY_ALREADY_REGISTERED")
            resolved = normalized[entry["id"]]
            for project_id, path in normalized.items():
                if project_id != entry["id"] and (path in resolved.parents or resolved in path.parents):
                    warning = {"code": "nested_root_review", "project_id": project_id}
                    if warning not in warnings:
                        warnings.append(warning)
        candidate = (before if old == entry else
                     yaml.safe_dump(candidate_registry, allow_unicode=True, sort_keys=False).encode("utf-8"))
        result = _result(entry, old, before, candidate, warnings)
        if not apply or candidate == before:
            return result
        if any(warning["code"] == "nested_root_review" for warning in warnings):
            raise RegistrationError("NESTED_ROOT_REVIEW_REQUIRED")
        temporary = f".registration-{uuid.uuid4().hex}.tmp"
        created = False
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 stat.S_IMODE(info.st_mode), dir_fd=directory)
            created = True
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
                stream.write(candidate)
                stream.flush()
                os.fsync(stream.fileno())
            current, current_info = _read_registry(directory)
            if current != before or _identity(current_info) != _identity(info):
                raise RegistrationError("REGISTRY_CONFLICT")
            os.replace(temporary, REGISTRY_NAME, src_dir_fd=directory, dst_dir_fd=directory)
            created = False
            os.fsync(directory)
        finally:
            if created:
                os.unlink(temporary, dir_fd=directory)
        result["applied"] = True
        return result
