"""Bounded, read-only source resolution. Removed identities never touch a root."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

from hub.connection_records import (FIELDS, VERSION, RecordError, content_hash,
                                    credential_path_component, empty_business,
                                    record_schema, relative_path, require, update_key,
                                    validate_declaration, validate_result)
from hub.connection_records import authority_matches
from hub.connection_records import FORBIDDEN_PARTS, CREDENTIAL_PATH_TOKENS, CREDENTIAL_PATH_NAMES
from hub.connections import parse_owned_hub_state, parse_source, validate_registry_value
from hub.connection_normalize import normalize_source

MAX_SOURCE_BYTES = 1024 * 1024
MAX_OWNED_HUB_STATE_BYTES = 2 * 1024 * 1024
# Only the registered light-novel current-state YAML receives this budget.
# The child enforces 512 MiB address space, 30 CPU seconds and 4 MiB output;
# a 45-second wall timeout and one child per process bound concurrent work.
MAX_LIGHT_NOVEL_STATE_BYTES = 8 * 1024 * 1024
_large_source_slot = threading.BoundedSemaphore(1)
_light_novel_read_grant = object()
DECLARATION_PATH = "hub.connection.yaml"


def _project_binding(project: dict) -> str:
    """Permission/root/declaration identity, not mutable source fact contents.

    Only metadata is observed here. Actual declaration bytes, source aliases and
    descriptor identities are still checked by the bounded reader below.
    Excluded identities never resolve/stat their roots.
    """
    context = {'project': project, 'root': None, 'declaration': None, 'routes': []}
    if (project.get('local_presence', {}).get('status') == 'removed_local'
            or project.get('current_state_status') == 'removed_local'
            or project.get('connection_read_allowed', project.get('enabled')) is not True
            or project.get('access_profile') == 'no_current_goal_access'
            or project.get('project_type') in {'cloud', 'cloud_project'}
            or project.get('root_kind') == 'cloud'):
        return content_hash(context)
    try:
        registered = Path(project['root_path']).expanduser()
        _root_path_allowed(registered)
        root = registered.resolve(strict=True)
        _root_path_allowed(root)
        info = root.stat()
        context['root'] = [str(root), info.st_dev, info.st_ino]
        declaration = (root / DECLARATION_PATH).lstat()
        context['declaration'] = [declaration.st_dev, declaration.st_ino, declaration.st_mode,
                                  declaration.st_size, declaration.st_mtime_ns, declaration.st_ctime_ns]
        for raw in project.get('current_state_paths', []):
            path = Path(raw).expanduser()
            if path.is_absolute():
                _root_path_allowed(path)
                resolved = path.resolve(strict=False)
                _root_path_allowed(resolved)
                context['routes'].append([raw, str(resolved)])
    except (OSError, RecordError, ValueError, KeyError) as exc:
        context['unavailable'] = type(exc).__name__
    return content_hash(context)


def _authority(registry: dict, raw: bytes, project_ids=None, *, observe_sources=True) -> dict:
    return {'registry_hash': hashlib.sha256(raw).hexdigest(),
            'schema_hash': content_hash({'record': record_schema(),
                'binding_contract': {'version': '1.1', 'forbidden_parts': sorted(FORBIDDEN_PARTS),
                    'credential_tokens': sorted(CREDENTIAL_PATH_TOKENS),
                    'credential_names': sorted(CREDENTIAL_PATH_NAMES)},
                'shared_registry_contract': {k: v for k, v in registry.items() if k != 'projects'}}),
            'adapter_version': VERSION, 'binding_version': '1.1',
            'registration_bindings': {p['id']: content_hash(p) for p in registry['projects']
                                      if project_ids is None or p['id'] in project_ids},
            'project_bindings': {p['id']: _project_binding(p) for p in registry['projects']
                                 if observe_sources and (project_ids is None or p['id'] in project_ids)}}


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
    """No-follow read with stable file metadata and descriptor-to-path bindings."""
    require(type(max_bytes) is int and 0 < max_bytes <= MAX_OWNED_HUB_STATE_BYTES, "invalid read budget")
    return _read_pinned(root, relative, authority_check=authority_check, max_bytes=max_bytes)


def _safe_light_novel_read(root: Path, source: dict, *, project_id: str,
                           authority_check: Callable[[], None]) -> tuple[bytes, dict]:
    """The larger budget is available only to the registered, exact source route."""
    require(project_id == "light-novel" and source.get("id") == "state"
            and source.get("role") == "current_state" and source.get("path") == "governance/round_state.yaml"
            and source.get("format") == "yaml", "invalid large source identity")
    return _read_pinned(root, source["path"], authority_check=authority_check,
                        max_bytes=MAX_LIGHT_NOVEL_STATE_BYTES, grant=_light_novel_read_grant)


def _read_pinned(root: Path, relative: str, *, authority_check: Callable[[], None],
                 max_bytes: int, grant=None) -> tuple[bytes, dict]:
    relative_path(relative)
    require(type(max_bytes) is int and (0 < max_bytes <= MAX_OWNED_HUB_STATE_BYTES
            or (grant is _light_novel_read_grant and relative == "governance/round_state.yaml"
                and max_bytes == MAX_LIGHT_NOVEL_STATE_BYTES)), "invalid read budget")
    authority_check()
    descriptor = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
    opened = [descriptor]
    file_descriptor = None
    try:
        directories = (*root.parts[1:], *PurePosixPath(relative).parts[:-1])
        for part in directories:
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
        def fingerprint(info):
            return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                    info.st_size, info.st_mtime_ns, info.st_ctime_ns)

        require(len(data) <= max_bytes and fingerprint(before) == fingerprint(after),
                "source changed during read")
        # A still-open descriptor can outlive an atomic file/directory replace.
        # Check each name against its pinned parent without following aliases.
        for index, part in enumerate(directories):
            current = os.stat(part, dir_fd=opened[index], follow_symlinks=False)
            pinned = os.fstat(opened[index + 1])
            require(stat.S_ISDIR(current.st_mode)
                    and (current.st_dev, current.st_ino) == (pinned.st_dev, pinned.st_ino),
                    "source parent binding changed during read")
        current = os.stat(PurePosixPath(relative).name, dir_fd=descriptor, follow_symlinks=False)
        require(fingerprint(current) == fingerprint(after), "source path binding changed during read")
        authority_check()
        return data, {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                      "modified_at": datetime.fromtimestamp(before.st_mtime_ns / 1e9, timezone.utc).isoformat()}
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for item in reversed(opened):
            os.close(item)


def _normalize_large_light_novel(data, declaration, source, metadata, observed_at):
    """Parse the complete document in a resource-limited child, never a truncated view."""
    if not _large_source_slot.acquire(blocking=False):
        raise SourceFailure("invalid", "Large source reader is busy; retry this project later.")
    try:
        header = json.dumps({"declaration": declaration, "source": source, "metadata": metadata,
                             "observed_at": observed_at}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        require(len(header) <= MAX_SOURCE_BYTES, "large source request exceeds header budget")
        packet = len(header).to_bytes(4, "big") + header + data
        try:
            child = subprocess.run([sys.executable, "-I", str(Path(__file__).with_name("connection_large_worker.py"))],
                                   input=packet, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   timeout=45, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SourceFailure("invalid", "Large source reader failed or timed out; retry after resolving the limit.") from exc
        if child.returncode != 0 or len(child.stdout) > 4 * 1024 * 1024:
            raise SourceFailure("invalid", "Large source reader failed or exceeded its resource limit.")
        try:
            output = json.loads(child.stdout)
        except (ValueError, UnicodeError) as exc:
            raise SourceFailure("invalid", "Large source reader returned an invalid result.") from exc
        if type(output) is not dict or output.get("ok") is not True:
            raise SourceFailure("invalid", "Large source is invalid or exceeded its resource limit; fix it and retry.")
        require(set(output) == {"ok", "value", "peak_rss_kib", "limits"} and type(output["value"]) is dict,
                "large source reader returned an invalid result")
        require(set(output["value"]) in ({"business", "field_provenance", "unknown_fields"},
                                         {"business", "field_provenance", "unknown_fields", "extensions"}),
                "large source reader returned unexpected fields")
        return output["value"]
    finally:
        _large_source_slot.release()


class SourceResolver:
    def __init__(self, root: Path, *, registry_path: Path | None = None, clock: Callable[[], str] = now,
                 observe_sources: bool = True):
        self.root = Path(root)
        self.registry_path = registry_path or self.root / "data/registry/external_projects.yaml"
        self.registry_bytes = self.registry_path.read_bytes()
        self.registry = validate_registry_value(parse_source(self.registry_bytes, "yaml"))
        require(self.registry_bytes == self.registry_path.read_bytes(), "registry changed during load")
        self.projects = {row["id"]: row for row in self.registry["projects"]}
        self.observe_sources = observe_sources
        self.authority = _authority(self.registry, self.registry_bytes, observe_sources=observe_sources)
        self.clock = clock

    @staticmethod
    def validate_result(value: dict) -> dict:
        return validate_result(value)

    def _check_registry(self, project_id=None, *, registration_only=None) -> None:
        data = self.registry_path.read_bytes()
        registry = validate_registry_value(parse_source(data, 'yaml'))
        ids = (self.projects if project_id is None else
               [project_id] if isinstance(project_id, str) else project_id)
        registration_only = not self.observe_sources if registration_only is None else registration_only
        current = _authority(registry, data, ids, observe_sources=not registration_only)
        if (data != self.registry_path.read_bytes()
                or not authority_matches(self.authority, current, ids, registration_only=registration_only)):
            raise SourceFailure("authority_drift", "Effective project authority changed during refresh; start a new request.")

    def refresh(self, project_id: str) -> dict:
        require(self.observe_sources, "ledger-only resolver cannot refresh; construct an observing resolver")
        project = self.projects[project_id]
        result = {"schema_version": VERSION, "kind": "source_resolution", "project_id": project_id,
                  "name": project["name"], "observed_at": self.clock(), "success": False,
                  "disposition": "invalid", "authority": dict(self.authority), "business": empty_business(),
                  "binding_hash": self.authority['project_bindings'][project_id],
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
                    or project.get("access_profile") == "no_current_goal_access"
                    or project.get("project_type") in {"cloud", "cloud_project"}
                    or project.get("root_kind") == "cloud"):
                raise SourceFailure("disabled", "Registry currently disables this source route.")
            self._check_registry(project_id, registration_only=True)
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
                self._check_registry(project_id, registration_only=True)
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
            if "hub_field_mapping" in project or "hub_source_ref" in project:
                # Registry authority may bind a registered route and replace field
                # selection, but cannot change the declaration's source identity.
                effective = dict(declaration)
                if "hub_source_ref" in project:
                    bound_source = project["hub_source_ref"]
                    original_source = declaration["source_refs"][0]
                    require(type(bound_source) is dict
                            and bound_source.get("id") == original_source["id"]
                            and bound_source.get("role") == original_source["role"],
                            "registry source binding must preserve source identity and role")
                    effective["source_refs"] = [bound_source]
                if "hub_field_mapping" in project:
                    effective["mapping"] = project["hub_field_mapping"]
                # Keep malformed overlays local to this project's refresh.
                declaration = validate_declaration(effective, project_id)
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
            owned_hub_state = (project_id == "personal-control-hub"
                               and root == self.root.resolve(strict=True)
                               and source["path"] == "STATE.yaml" and source["format"] == "yaml")
            large_light_novel = (project_id == "light-novel" and source["id"] == "state"
                                 and source["role"] == "current_state"
                                 and source["path"] == "governance/round_state.yaml"
                                 and source["format"] == "yaml")
            source_budget = MAX_OWNED_HUB_STATE_BYTES if owned_hub_state else MAX_SOURCE_BYTES

            def read_source():
                if large_light_novel:
                    return _safe_light_novel_read(root, source, project_id=project_id, authority_check=check)
                return _safe_relative_read(root, source["path"], authority_check=check, max_bytes=source_budget)

            data, metadata = read_source()
            result["sources"] = [{**source, **metadata}]
            if large_light_novel:
                normalized = _normalize_large_light_novel(data, declaration, source, metadata, result["observed_at"])
            else:
                parsed = parse_owned_hub_state(data) if owned_hub_state else parse_source(data, source["format"])
                normalized = normalize_source(parsed, declaration, source, metadata, result["observed_at"],
                                              owned_hub_state=owned_hub_state)
            result.update(normalized)
            # Keep normalization bound to a stable source version, then verify
            # that the declaration used for its mapping still agrees.
            _, source_metadata = read_source()
            require(source_metadata == metadata, "source changed during normalization")
            recheck, _ = _safe_relative_read(root, DECLARATION_PATH, authority_check=check)
            require(hashlib.sha256(recheck).hexdigest() == result["declaration"]["sha256"], "declaration changed during read")
            # Failed reads retain their precise failure disposition. Only a
            # complete successful candidate can reach the final physical binding
            # comparison; no changed root/declaration can publish current facts.
            self._check_registry(project_id)
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
        result.pop("extensions", None)
        result.update(success=False, disposition=code, business=empty_business(), field_provenance={},
                      unknown_fields={key: message for key in FIELDS}, errors=[{"code": code, "message": message}])
        result["freshness"].update(read_status=code, stale=True, reason=message)
        result["update_key"] = update_key(result)
        return validate_result(result)
