"""Bounded, read-only source resolution. Removed identities never touch a root."""
from __future__ import annotations

import errno
import hashlib
import os
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

from hub.connection_records import (FIELDS, VERSION, RecordError, content_hash,
                                    credential_path_component, empty_business, put_field,
                                    record_schema, relative_path, require, update_key,
                                    validate_declaration, validate_result)
from hub.connections import parse_source, select_value, validate_registry_value

MAX_SOURCE_BYTES = 1024 * 1024
DECLARATION_PATH = "hub.connection.yaml"


class SourceFailure(RecordError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _root_path_allowed(path: Path) -> None:
    require(path.is_absolute(), "registered root must be absolute")
    forbidden = {".cursor", "cursor", ".codex", ".ssh", "credentials", "secrets", "cookies"}
    if any(part.lower() in forbidden or part.lower().startswith(".env")
           or credential_path_component(part) for part in path.parts):
        raise SourceFailure("unsafe_path", "Protected root or authority alias is outside connection scope.")


def _safe_relative_read(root: Path, relative: str, *, authority_check: Callable[[], None],
                        max_bytes: int = MAX_SOURCE_BYTES) -> tuple[bytes, dict]:
    """Reused no-follow descriptor walk; fingerprint and mtime share one open."""
    relative_path(relative)
    require(type(max_bytes) is int and 0 < max_bytes <= MAX_SOURCE_BYTES, "invalid read budget")
    authority_check()
    descriptor = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
    opened = [descriptor]
    file_descriptor = None
    try:
        for part in (*root.parts[1:], *PurePosixPath(relative).parts[:-1]):
            authority_check()
            descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            opened.append(descriptor)
        authority_check()
        file_descriptor = os.open(PurePosixPath(relative).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                  dir_fd=descriptor)
        before = os.fstat(file_descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "source must be ordinary singly-linked file")
        require(before.st_size <= max_bytes, "source exceeds read budget")
        with os.fdopen(file_descriptor, "rb", closefd=True) as stream:
            file_descriptor = None
            data = stream.read(max_bytes + 1)
            after = os.fstat(stream.fileno())
        require(len(data) <= max_bytes and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "source changed during read")
        authority_check()
        return data, {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                      "modified_at": datetime.fromtimestamp(before.st_mtime_ns / 1e9, timezone.utc).isoformat()}
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for item in reversed(opened):
            os.close(item)


class SourceResolver:
    def __init__(self, root: Path, *, registry_path: Path | None = None, clock: Callable[[], str] = now):
        self.root = Path(root)
        self.registry_path = registry_path or self.root / "data/registry/external_projects.yaml"
        self.registry_bytes = self.registry_path.read_bytes()
        self.registry = validate_registry_value(parse_source(self.registry_bytes, "yaml"))
        require(self.registry_bytes == self.registry_path.read_bytes(), "registry changed during load")
        self.projects = {row["id"]: row for row in self.registry["projects"]}
        self.authority = {"registry_hash": hashlib.sha256(self.registry_bytes).hexdigest(),
                          "schema_hash": content_hash(record_schema()), "adapter_version": VERSION}
        self.clock = clock

    @staticmethod
    def validate_result(value: dict) -> dict:
        return validate_result(value)

    def _check_registry(self) -> None:
        if self.registry_path.read_bytes() != self.registry_bytes:
            raise SourceFailure("authority_drift", "Registry changed during refresh; start a new request.")

    def refresh(self, project_id: str) -> dict:
        project = self.projects[project_id]
        result = {"schema_version": VERSION, "kind": "source_resolution", "project_id": project_id,
                  "name": project["name"], "observed_at": self.clock(), "success": False,
                  "disposition": "invalid", "authority": dict(self.authority), "business": empty_business(),
                  "sources": [], "declaration": None, "field_provenance": {},
                  "unknown_fields": {key: "No current source result." for key in FIELDS},
                  "validation_entry": [], "errors": [], "update_key": "",
                  "freshness": {"read_status": "invalid", "stale": True, "reason": "No successful read.",
                                "max_read_age_seconds": 86400, "root_binding": None}}
        stage = "root"
        try:
            # Registry-only disposition before expanduser, resolve, stat, or open.
            if (project.get("local_presence", {}).get("status") == "removed_local"
                    or project.get("current_state_status") == "removed_local"):
                raise SourceFailure("removed_local", "已从本地移除；保留登记身份，不扫描、不写入、不重试。")
            if (project.get("connection_read_allowed", project.get("enabled")) is not True
                    or project.get("access_profile") == "no_current_goal_access"):
                raise SourceFailure("disabled", "Registry currently disables this source route.")
            self._check_registry()
            registered = Path(project["root_path"]).expanduser()
            _root_path_allowed(registered)
            root = registered.resolve(strict=True)
            _root_path_allowed(root)
            root_stat = root.stat()
            require(root.is_dir(), "project root must be a directory")
            binding = (str(root), root_stat.st_dev, root_stat.st_ino)
            route_bindings: list[tuple[Path, Path]] = []
            result["freshness"]["root_binding"] = content_hash(binding)

            def check() -> None:
                self._check_registry()
                try:
                    resolved = registered.resolve(strict=True)
                    current_stat = resolved.stat()
                except OSError as exc:
                    raise SourceFailure("authority_drift", "Authorized root disappeared during read.") from exc
                if (str(resolved), current_stat.st_dev, current_stat.st_ino) != binding:
                    raise SourceFailure("authority_drift", "Authorized root binding changed during read.")
                for route, expected in route_bindings:
                    if route.resolve(strict=False) != expected:
                        raise SourceFailure("authority_drift", "Registered source alias changed during read.")

            stage = "declaration"
            data, metadata = _safe_relative_read(root, DECLARATION_PATH, authority_check=check)
            declaration = validate_declaration(parse_source(data, "yaml"), project_id)
            result["declaration"] = {"path": DECLARATION_PATH, "sha256": metadata["sha256"], "schema_version": VERSION}
            result["validation_entry"] = declaration["validation_entry"]
            result["freshness"]["max_read_age_seconds"] = declaration["max_read_age_seconds"]
            source = declaration["source_refs"][0]
            # The project cannot widen its registered sole-state route through a declaration.
            allowed = set()
            for raw in project.get("current_state_paths", []):
                path = Path(raw).expanduser()
                if not path.is_absolute():
                    relative_path(raw)
                    allowed.add(raw)
                else:
                    # Existing registry aliases may point at the same authorized root.
                    _root_path_allowed(path)
                    resolved = path.resolve(strict=False)
                    _root_path_allowed(resolved)
                    try:
                        relative = str(resolved.relative_to(root))
                    except ValueError:
                        continue
                    relative_path(relative)
                    allowed.add(relative)
                    if relative == source["path"]:
                        route_bindings.append((path, resolved))
            require(source["path"] in allowed, "source is not a registered current-state route")
            result["freshness"]["root_binding"] = content_hash({"root": binding, "routes": [(str(a), str(b)) for a, b in route_bindings]})
            stage = "source"
            data, metadata = _safe_relative_read(root, source["path"], authority_check=check)
            result["sources"] = [{**source, **metadata}]
            parsed = parse_source(data, source["format"])
            result["unknown_fields"] = dict(declaration["unknown_fields"])
            for field, rule in declaration["mapping"].items():
                raw = select_value(parsed, rule["selector"])
                if raw is None:
                    continue
                value = raw
                if rule["value_map"] is not None:
                    require(type(raw) is str and raw in rule["value_map"], "unmapped source enum")
                    value = rule["value_map"][raw]
                put_field(result["business"], field, value)
                result["field_provenance"][field] = {"source_ref": source["id"], "sha256": metadata["sha256"],
                                                     "selector": rule["selector"], "value_map": rule["value_map"], "raw_value": raw}
                del result["unknown_fields"][field]
            # A mapping read does not stand after its declaration changes.
            recheck, _ = _safe_relative_read(root, DECLARATION_PATH, authority_check=check)
            require(hashlib.sha256(recheck).hexdigest() == result["declaration"]["sha256"], "declaration changed during read")
            result["success"] = True
            result["disposition"] = "resolved"
            result["freshness"].update(read_status="resolved", stale=False, reason=None)
            result["update_key"] = update_key(result)
            return validate_result(result)
        except SourceFailure as exc:
            code, message = exc.code, str(exc)
        except PermissionError:
            code, message = "permission_denied", "Source access denied; previous success is not current evidence."
        except FileNotFoundError:
            code = {"root": "offline", "declaration": "missing_declaration", "source": "missing_source"}[stage]
            message = {"root": "Registered root is unavailable.", "declaration": "Project has no hub.connection.yaml declaration.",
                       "source": "Declared current-state source is missing."}[stage]
        except OSError as exc:
            code = "unsafe_path" if exc.errno in {errno.ELOOP, errno.ENOTDIR} else "offline"
            message = "Source path cannot be read within its authorized root."
        except (RecordError, TypeError, ValueError, KeyError):
            code, message = "invalid", "Declaration, source or typed mapping failed validation; inspect the registered source."
        # Never retain partial business data from an invalid read in a success-shaped row.
        result.update(success=False, disposition=code, business=empty_business(), field_provenance={},
                      unknown_fields={key: message for key in FIELDS}, errors=[{"code": code, "message": message}])
        result["freshness"].update(read_status=code, stale=True, reason=message)
        result["update_key"] = update_key(result)
        return validate_result(result)
