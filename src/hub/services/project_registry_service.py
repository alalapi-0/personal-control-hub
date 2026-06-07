"""External project registry: read-only load and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from hub.paths import PROJECT_ROOT

REGISTRY_PATH = PROJECT_ROOT / "data" / "registry" / "external_projects.yaml"

REQUIRED_FIELDS = {
    "id",
    "name",
    "root_path",
    "enabled",
    "scan_enabled",
    "profile_enabled",
    "summary_enabled",
    "project_type",
    "priority_source",
    "watch_paths",
}

ALLOWED_PRIORITY_SOURCES = {"user", "rule", "proposal"}


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data if isinstance(data, dict) else {}


def load_registry() -> dict[str, Any]:
    return _load_yaml(REGISTRY_PATH)


def list_projects() -> list[dict[str, Any]]:
    registry = load_registry()
    projects = registry.get("projects", [])
    if not isinstance(projects, list):
        return []
    return [item for item in projects if isinstance(item, dict)]


def validate_registry(registry: dict[str, Any] | None = None) -> dict[str, Any]:
    data = registry if registry is not None else load_registry()
    hard_blockers: list[str] = []
    warnings: list[str] = []

    if data.get("schema_version") != "1.0":
        hard_blockers.append("schema_version 必须为 1.0")

    policy = data.get("policy", {})
    if not isinstance(policy, dict):
        hard_blockers.append("policy 必须为对象")
        policy = {}

    if policy.get("read_only") is not True:
        hard_blockers.append("policy.read_only 必须为 true")
    if policy.get("write_external_forbidden") is not True:
        hard_blockers.append("policy.write_external_forbidden 必须为 true")

    forbidden_dirs = policy.get("forbidden_scan_dirs", [])
    if not isinstance(forbidden_dirs, list) or not forbidden_dirs:
        hard_blockers.append("policy.forbidden_scan_dirs 必须为非空列表")

    projects = data.get("projects", [])
    if not isinstance(projects, list):
        hard_blockers.append("projects 必须为列表")
        projects = []

    seen_ids: set[str] = set()
    for index, project in enumerate(projects):
        if not isinstance(project, dict):
            hard_blockers.append(f"projects[{index}] 必须为对象")
            continue

        project_id = project.get("id")
        if not project_id or not isinstance(project_id, str):
            hard_blockers.append(f"projects[{index}].id 无效")
            continue
        if project_id in seen_ids:
            hard_blockers.append(f"重复项目 id：{project_id}")
        seen_ids.add(project_id)

        missing = REQUIRED_FIELDS - set(project.keys())
        if missing:
            hard_blockers.append(f"{project_id}: 缺少字段 {sorted(missing)}")

        priority_source = project.get("priority_source")
        if priority_source not in ALLOWED_PRIORITY_SOURCES:
            hard_blockers.append(f"{project_id}: priority_source 无效")

        root_path = project.get("root_path")
        if isinstance(root_path, str) and root_path.strip():
            path = Path(root_path).expanduser()
            if not path.exists():
                warnings.append(f"{project_id}: root_path 不存在或不可读：{root_path}")
        elif project.get("enabled"):
            warnings.append(f"{project_id}: enabled=true 但 root_path 为空")

        watch_paths = project.get("watch_paths")
        if not isinstance(watch_paths, list) or not watch_paths:
            warnings.append(f"{project_id}: watch_paths 为空")

    return {
        "valid": not hard_blockers,
        "hard_blockers": hard_blockers,
        "warnings": warnings,
        "project_count": len(projects),
        "enabled_count": sum(1 for p in projects if isinstance(p, dict) and p.get("enabled")),
    }


def print_registry_list() -> int:
    registry = load_registry()
    projects = list_projects()
    policy = registry.get("policy", {})

    print("=== External Project Registry ===")
    print(f"schema_version: {registry.get('schema_version', 'unknown')}")
    print(f"read_only: {policy.get('read_only')}")
    print(f"write_external_forbidden: {policy.get('write_external_forbidden')}")
    print(f"projects: {len(projects)}")
    if not projects:
        print("（暂无登记项目；参见 data/registry/external_projects.yaml 注释示例）")
        return 0

    for project in projects:
        status = "enabled" if project.get("enabled") else "disabled"
        print(
            f"- {project.get('id')}: {project.get('name')} "
            f"[{status}] scan={project.get('scan_enabled')} "
            f"profile={project.get('profile_enabled')}"
        )
    return 0
