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
    "rules_paths",
    "current_state_paths",
    "current_state_status",
    "supporting_authority_paths",
}

AUTHORITY_PATH_FIELDS = (
    "rules_paths",
    "current_state_paths",
    "supporting_authority_paths",
)

ALLOWED_PRIORITY_SOURCES = {"user", "rule", "proposal"}

ALLOWED_STORAGE_SCOPES = {
    "project_candidate",
    "project_candidate_with_protected_associated_data",
    "project_candidate_with_protected_private_content",
    "project_candidate_with_credential_session_boundary",
    "project_candidate_with_credential_boundary",
    "project_candidate_with_protected_control_config",
    "accepted_exclusion_record_only",
    "protected_internal_control_plane",
    "protected_governance_program",
    "owner_named_existing_external_project",
}

SPECIAL_STORAGE_PROJECTS = {
    "manga-localizer": "accepted_exclusion_record_only",
    "personal-control-hub": "protected_internal_control_plane",
    "storage_governance": "protected_governance_program",
}


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
    if policy.get("project_registry_is_effect_authority") is not False:
        hard_blockers.append("policy.project_registry_is_effect_authority 必须为 false")

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
        root: Path | None = None
        if isinstance(root_path, str) and root_path.strip():
            root = Path(root_path).expanduser()
            # manga-localizer is an accepted no-access record. Even an exists()
            # probe would violate the current Goal's inspection boundary.
            if project_id != "manga-localizer" and not root.exists():
                warnings.append(f"{project_id}: root_path 不存在或不可读：{root_path}")
        elif project.get("enabled"):
            warnings.append(f"{project_id}: enabled=true 但 root_path 为空")

        watch_paths = project.get("watch_paths")
        if not isinstance(watch_paths, list):
            hard_blockers.append(f"{project_id}: watch_paths 必须为列表")
        elif project.get("scan_enabled") and not watch_paths:
            warnings.append(f"{project_id}: scan_enabled=true 但 watch_paths 为空")

        for field in AUTHORITY_PATH_FIELDS:
            authority_paths = project.get(field)
            if not isinstance(authority_paths, list):
                hard_blockers.append(f"{project_id}: {field} 必须为列表")
                continue
            for authority_path in authority_paths:
                if not isinstance(authority_path, str) or not authority_path.strip():
                    hard_blockers.append(f"{project_id}: {field} 含无效路径")
                    continue
                expanded_authority = Path(authority_path).expanduser()
                if project_id != "manga-localizer" and not expanded_authority.exists():
                    hard_blockers.append(f"{project_id}: 登记 authority 不存在：{authority_path}")
                if root is not None and isinstance(watch_paths, list):
                    try:
                        relative_authority = expanded_authority.relative_to(root)
                    except ValueError:
                        continue
                    relative_text = relative_authority.as_posix()
                    authority_is_watched = any(
                        isinstance(watch_path, str)
                        and (
                            watch_path == "."
                            or relative_text == watch_path.rstrip("/")
                            or relative_text.startswith(f"{watch_path.rstrip('/')}/")
                        )
                        for watch_path in watch_paths
                        if watch_path
                    )
                    if not authority_is_watched:
                        hard_blockers.append(
                            f"{project_id}: 项目内 authority 未进入 watch_paths：{relative_text}"
                        )

        current_state_status = project.get("current_state_status")
        if not isinstance(current_state_status, str) or not current_state_status.strip():
            hard_blockers.append(f"{project_id}: current_state_status 必须为非空字符串")

        storage = project.get("storage_governance")
        if not isinstance(storage, dict):
            hard_blockers.append(f"{project_id}: storage_governance 必须为对象")
            continue

        storage_scope = storage.get("scope")
        if storage_scope not in ALLOWED_STORAGE_SCOPES:
            hard_blockers.append(f"{project_id}: storage_governance.scope 无效")

        expected_special_scope = SPECIAL_STORAGE_PROJECTS.get(project_id)
        if expected_special_scope and storage_scope != expected_special_scope:
            hard_blockers.append(f"{project_id}: 特殊存储治理 scope 必须为 {expected_special_scope}")

    project_by_id = {
        project.get("id"): project
        for project in projects
        if isinstance(project, dict) and isinstance(project.get("id"), str)
    }
    for project_id in SPECIAL_STORAGE_PROJECTS:
        if project_id not in project_by_id:
            hard_blockers.append(f"缺少特殊项目记录：{project_id}")

    manga = project_by_id.get("manga-localizer", {})
    manga_storage = manga.get("storage_governance", {}) if isinstance(manga, dict) else {}
    if any(manga.get(key) is not False for key in ("enabled", "scan_enabled", "profile_enabled")):
        hard_blockers.append("manga-localizer 必须禁用扫描与 profile，仅保留排除记录")
    if any(
        manga_storage.get(key) is not False
        for key in ("inventory_allowed", "inspection_allowed", "validation_allowed", "mutation_allowed")
    ):
        hard_blockers.append("manga-localizer 当前 Goal 的 inventory/inspection/validation/mutation 必须全部为 false")
    if any(manga.get(field) for field in AUTHORITY_PATH_FIELDS):
        hard_blockers.append("manga-localizer authority 指针必须为空，禁止为本 Goal 访问项目树")

    hub = project_by_id.get("personal-control-hub", {})
    hub_storage = hub.get("storage_governance", {}) if isinstance(hub, dict) else {}
    if hub.get("scan_enabled") is not False:
        hard_blockers.append("personal-control-hub 必须禁止递归 registry 扫描")
    if hub_storage.get("migration_allowed") is not False or hub_storage.get("cleanup_allowed") is not False:
        hard_blockers.append("personal-control-hub 必须永久禁止迁移与 cleanup")

    storage_program = project_by_id.get("storage_governance", {})
    storage_program_policy = (
        storage_program.get("storage_governance", {}) if isinstance(storage_program, dict) else {}
    )
    if (
        storage_program_policy.get("migration_allowed") is not False
        or storage_program_policy.get("cleanup_allowed") is not False
    ):
        hard_blockers.append("storage_governance 控制面必须禁止迁移与 cleanup")

    storage_contract = data.get("storage_governance_contract", {})
    if not isinstance(storage_contract, dict):
        hard_blockers.append("storage_governance_contract 必须为对象")
    else:
        if storage_contract.get("sole_execution_state") != "~/PycharmProjects/personal-control-hub/governance/programs/storage_governance/STATE.yaml":
            hard_blockers.append("storage_governance_contract.sole_execution_state 指针无效")
        manifest_path = storage_contract.get("frozen_manifest")
        manifest_hash = storage_contract.get("frozen_manifest_sha256")
        if not isinstance(manifest_path, str) or not manifest_path.endswith("PROJECT_MANIFEST_v2.yaml"):
            hard_blockers.append("storage_governance_contract.frozen_manifest 指针无效")
        if not isinstance(manifest_hash, str) or len(manifest_hash) != 64:
            hard_blockers.append("storage_governance_contract.frozen_manifest_sha256 无效")

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
