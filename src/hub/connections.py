"""Bounded, read-only project connection adapters. No external writes or subprocesses."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from hub.connection_records import (VERSION, FORBIDDEN_PARTS, RecordError, adapter_hash, content_hash,
                                    normalize_status, registry_permission, require, resolve_named_source,
                                    validate_adapters, validate_manifest_authority, validate_record)

ADAPTER_VERSION = "1.0"
MAX_BYTES = 1024 * 1024


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_registry_at(root: Path) -> tuple[dict, str]:
    raw = (root / "data/registry/external_projects.yaml").read_bytes()
    try:
        registry = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RecordError("registry syntax invalid") from exc
    require(isinstance(registry, dict) and registry.get("schema_version") == "1.0",
            "unsupported registry version")
    policy = registry.get("policy", {})
    require(isinstance(policy, dict), "registry policy must be an object")
    require(policy.get("read_only") is True and policy.get("write_external_forbidden") is True,
            "registry read-only boundary missing")
    projects = registry.get("projects")
    require(isinstance(projects, list) and bool(projects), "registry projects missing")
    ids = []
    for p in projects:
        require(isinstance(p, dict) and isinstance(p.get("id"), str), "invalid registry entry")
        for field in ("enabled", "summary_enabled"):
            require(type(p.get(field)) is bool, f"{p['id']}: invalid {field}")
        require(isinstance(p.get("root_path"), str) and bool(p["root_path"]), "missing root")
        require(isinstance(p.get("current_state_paths"), list) and
                all(isinstance(s, str) and s for s in p["current_state_paths"]), "invalid state paths")
        require(p.get("external_write_allowed", False) is False, "external writes not supported")
        ids.append(p["id"])
    require(len(ids) == len(set(ids)), "duplicate registry project ID")
    return registry, file_hash(raw)


def permission(project: dict) -> tuple[str, str]:
    mode = registry_permission(project)
    if mode == "no_access":
        return "no_access", "登记明确禁止当前访问；须所有者决定，不探测项目文件。"
    if mode == "no_source":
        return "no_source", "登记未声明当前状态来源；不能把路径存在或项目介绍当作业务状态。"
    return "named_sources", "registry 当前启用并允许摘要；仅读取命名 current_state_paths。"


def freeze_manifest(root: Path, revision: int = 1) -> dict:
    registry, digest = load_registry_at(root)
    created = now()
    entries = []
    for project in registry["projects"]:
        pid = project["id"]
        mode, reason = permission(project)
        scope = {"schema_version": VERSION, "record_type": "scope", "id": f"scope-{pid}-v{revision}",
                 "created_at": created, "project_id": pid,
                 "disposition": "protected" if mode == "no_access" else "unresolved",
                 "reason": "尚待允许范围内的 UI 勘察；管理接入与 UI 范围分别判定。" if mode != "no_access" else reason,
                 "evidence_refs": [f"registry:projects/{pid}"],
                 "authority_ref": "owner-goal-01a06fae-2112-7cd2-8c83-d34d323fecaa"}
        entries.append({"project_id": pid, "scope": scope,
                        "permission": {"mode": mode, "basis": reason,
                                       "access_profile": project.get("access_profile", "registered_project_read")},
                        "allowed_entries": [f"current_state_paths[{i}]" for i in range(len(project["current_state_paths"]))]
                        if mode == "named_sources" else [],
                        "expected_capabilities": ["identity", "source", "availability", "status_or_explicit_unknown",
                                                  "next_action_or_explicit_unknown", "bounded_refresh"],
                        "source_absence_reason": reason if mode != "named_sources" else None,
                        "evidence_refs": [f"registry:projects/{pid}"]})
    manifest = {"schema_version": VERSION, "record_type": "connection_manifest",
                "id": f"hub-connections-v{revision}", "revision": revision, "created_at": created,
                "registry_ref": {"path": "data/registry/external_projects.yaml",
                                 "schema_version": registry["schema_version"], "sha256": digest},
                "entries": entries}
    manifest["content_hash"] = content_hash(manifest)
    return validate_record(manifest, {p["id"] for p in registry["projects"]})


def bounded_read(project: dict, source_index: int, forbidden_parts: set[str]) -> tuple[str, dict]:
    # Permission must be checked before expanduser/resolve/exists, including root probes.
    root, path, raw = resolve_named_source(project, source_index, forbidden_parts)
    if not root.is_dir():
        raise ConnectionError("authorized project root unavailable")
    # Walk the resolved absolute path with no-follow descriptors. A concurrent
    # symlink replacement at any level must fail rather than escape the boundary.
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    descriptor = None
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode), "source must be a regular file")
        require(before.st_size <= MAX_BYTES, "source exceeds 1 MiB limit")
        raw_bytes = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
    require(len(raw_bytes) <= MAX_BYTES, "source exceeds 1 MiB limit")
    require((before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_ino, after.st_size, after.st_mtime_ns), "source changed during read; retry")
    require(path.resolve() == path and raw.resolve() == path, "source alias changed during read")
    return raw_bytes.decode("utf-8"), {"ref": f"current_state_paths[{source_index}]", "path": str(path),
                                      "sha256": file_hash(raw_bytes), "bytes": len(raw_bytes)}


def section(text: str, heading: str) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match and match.group(2) == heading:
            level = len(match.group(1))
            end = index + 1
            while end < len(lines):
                boundary = re.match(r"^(#{1,6})\s", lines[end])
                if boundary and len(boundary.group(1)) <= level:
                    break
                end += 1
            return "\n".join(lines[index + 1:end]).strip() or None
    return None


def select_value(parsed: Any, text: str, selector: dict | None) -> str | None:
    if not selector:
        return None
    value = None
    if "jsonpath" in selector:
        value = parsed
        for key in selector["jsonpath"].split("."):
            if not isinstance(value, dict) or key not in value:
                return None
            value = value[key]
    elif "label" in selector:
        matches = [line.strip()[len(selector["label"]):].strip()
                   for line in text.splitlines() if line.strip().startswith(selector["label"])]
        if len(matches) == 1:
            value = matches[0]
    elif "section" in selector:
        value = section(text, selector["section"])
        if value and "table_key" in selector:
            cells = [line.strip().strip("|").split("|") for line in value.splitlines()]
            values = [row[1].strip() for row in cells if len(row) >= 2 and
                      row[0].strip() == selector["table_key"]]
            value = values[0] if len(values) == 1 else None
        elif value and selector.get("first_paragraph"):
            value = re.split(r"\n\s*\n", value)[0]
    elif "heading_regex" in selector:
        matches = re.findall(selector["heading_regex"], text, flags=re.MULTILINE)
        value = matches[0] if len(matches) == 1 else None
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, list):
        value = "\n".join(str(v) for v in value[:8] if isinstance(v, (str, int, float, bool)))
    if not isinstance(value, (str, int, float, bool)):
        return None
    return str(value).strip()[:2400] or None


class Connections:
    def __init__(self, root: Path, manifest: dict, adapters: dict):
        self.root = root
        self.registry, digest = load_registry_at(root)
        self.registry_sha256 = digest
        self.projects = {p["id"]: p for p in self.registry["projects"]}
        validate_record(manifest, set(self.projects))
        require(manifest["registry_ref"]["sha256"] == digest, "registry changed; freeze a new manifest revision")
        validate_manifest_authority(manifest, self.registry, digest)
        validate_adapters(adapters, set(self.projects))
        self.adapter_authority = adapters
        self.adapters = adapters["projects"]
        self.manifest = manifest
        self.entries = {e["project_id"]: e for e in manifest["entries"]}
        self.forbidden_parts = FORBIDDEN_PARTS | set(self.registry["policy"].get("forbidden_scan_dirs", []))

    def refresh(self, project_id: str) -> dict:
        _, current_digest = load_registry_at(self.root)
        require(current_digest == self.manifest["registry_ref"]["sha256"],
                "registry changed; refresh refused until a new manifest is frozen")
        require(project_id in self.projects, "unknown project ID")
        project = self.projects[project_id]
        adapter = self.adapters[project_id]
        mode, reason = permission(project)
        observed = now()
        snapshot = {"schema_version": VERSION, "record_type": "project_snapshot",
                    "id": f"snapshot-{project_id}", "created_at": observed, "project_id": project_id,
                    "manifest_id": self.manifest["id"], "manifest_hash": self.manifest["content_hash"],
                    "adapter_version": ADAPTER_VERSION, "adapter_hash": adapter_hash(self.adapter_authority, project_id),
                    "source_role": adapter["role"],
                    "raw_status": None, "normalized_status": "unknown", "next_action": None,
                    "next_action_kind": "unknown", "unknown_fields": {}, "blockers": None,
                    "availability": "fresh", "sources": [], "observed_at": observed,
                    "last_success_at": None, "refresh_error": None, "relations": [], "designs": []}
        if mode != "named_sources":
            snapshot["availability"] = "blocked_by_authority" if mode == "no_access" else "source_not_declared"
            snapshot["refresh_error"] = reason
        else:
            try:
                require(len(project["current_state_paths"]) == 1, "multi-source adapter not yet supported")
                text, source = bounded_read(project, 0, self.forbidden_parts)
                snapshot["sources"].append(source)
                fmt = adapter["format"]
                require(fmt in {"yaml", "json", "markdown"}, "unsupported source format")
                parsed = yaml.safe_load(text) if fmt == "yaml" else json.loads(text) if fmt == "json" else None
                if fmt in {"yaml", "json"}:
                    require(isinstance(parsed, dict), "structured source must be an object")
                snapshot["raw_status"] = select_value(parsed, text, adapter.get("status"))
                snapshot["next_action"] = select_value(parsed, text, adapter.get("next"))
                snapshot["normalized_status"] = normalize_status(snapshot["raw_status"])
                snapshot["next_action_kind"] = adapter.get("next_kind", "explicit") if snapshot["next_action"] else "unknown"
                snapshot["last_success_at"] = observed
            except FileNotFoundError:
                snapshot.update(availability="missing", refresh_error="命名来源不存在。")
            except PermissionError:
                snapshot.update(availability="unreadable", refresh_error="命名来源不可读：系统拒绝权限。")
            except ConnectionError:
                snapshot.update(availability="unavailable", refresh_error="项目所在磁盘或根目录不可用。")
            except (yaml.YAMLError, json.JSONDecodeError, UnicodeError):
                # Parser exception messages can contain source lines; never persist them.
                snapshot.update(availability="invalid", refresh_error="来源语法或编码无效；保留指纹，未推断状态。")
            except (RecordError, OSError) as exc:
                snapshot.update(availability="invalid", refresh_error=str(exc) if isinstance(exc, RecordError)
                                else "读取来源失败；未覆盖任何外部数据。")
        for field in ("raw_status", "next_action"):
            if snapshot[field] is None:
                snapshot["unknown_fields"][field] = snapshot["refresh_error"] or adapter.get("unknown") or "来源未提供该字段，或选择器未唯一匹配。"
        snapshot["unknown_fields"]["blockers"] = snapshot["refresh_error"] or "此适配器尚未提取阻塞信息；不能将未知解释为没有阻塞。"
        if snapshot["normalized_status"] == "unknown":
            snapshot["unknown_fields"]["normalized_status"] = snapshot["refresh_error"] or "来源状态未映射到通用状态，保留原始声明。"
        return validate_record(snapshot, set(self.projects))

    def refresh_all(self) -> list[dict]:
        return [self.refresh(pid) for pid in self.projects]


def connection_evidence(snapshot: dict, command: str, snapshot_ref: str) -> dict:
    availability = snapshot["availability"]
    status = {"blocked_by_authority": "BLOCKED_BY_AUTHORITY", "invalid": "VALIDATION_FAILED",
              "missing": "SOURCE_UNAVAILABLE", "unavailable": "SOURCE_UNAVAILABLE",
              "unreadable": "SOURCE_UNAVAILABLE"}.get(availability, "PENDING")
    return validate_record({"schema_version": VERSION, "record_type": "connection_evidence",
                            "id": f"read-{snapshot['project_id']}", "created_at": now(),
                            "project_id": snapshot["project_id"], "manifest_id": snapshot["manifest_id"],
                            "manifest_hash": snapshot["manifest_hash"], "adapter_version": ADAPTER_VERSION,
                            "adapter_hash": snapshot["adapter_hash"],
                            "snapshot_ref": snapshot_ref, "snapshot_hash": content_hash(snapshot),
                            "status": status, "command": command, "exit_code": 0 if availability == "fresh" else 2,
                            "observed_at": snapshot["observed_at"], "ui_verification": "UNVERIFIED",
                            "authority_ref": None,
                            "reason": "只读读取成功；尚未完成界面及逐项最终验收。" if availability == "fresh"
                            else snapshot["refresh_error"]})
