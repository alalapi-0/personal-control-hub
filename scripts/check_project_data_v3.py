#!/usr/bin/env python3
"""Validate the V3 project map and its bounded storage-governance entry."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hub.services.project_registry_service import validate_registry  # noqa: E402

PLAN_ID = "PROJECT-DATA-PUBLISH-SYNC-V3"
REMOVED_DISPOSITION = "removed_local_no_io"
SOURCE_GAP_DISPOSITION = "source_gap_keep_denominator"
CANDIDATE_FORMAT = "project-data-v3-candidate-v1"
STORAGE_FILES = {
    "adapter": "governance/adapters/storage_governance.yaml",
    "parameters": "data/programs/storage_governance_goal.yaml",
    "router": "governance/programs/storage_governance/AGENTS.md",
    "state": "governance/programs/storage_governance/STATE.yaml",
    "norm": "governance/programs/storage_governance/STORAGE_GOVERNANCE.md",
    "activation_prompt": "prompts/storage_governance_goal_mode.md",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: YAML root must be an object")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return value


def _duplicates(values: list[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


def validate_documents(
    registry: dict[str, Any],
    roadmap: dict[str, Any],
    sources: dict[str, Any],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    """Validate metadata only; never resolve or probe registered project roots."""
    blockers = list(validate_registry(registry, check_paths=False)["hard_blockers"])
    projects = registry.get("projects", [])
    project_map = roadmap.get("project_map", [])
    source_map = sources.get("projects", {})
    if not isinstance(projects, list):
        projects = []
        blockers.append("registry.projects must be a list")
    if not isinstance(project_map, list):
        project_map = []
        blockers.append("roadmap.project_map must be a list")
    if not isinstance(source_map, dict):
        source_map = {}
        blockers.append("metric_sources.projects must be an object")

    registry_ids = [
        row.get("id") for row in projects
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    ]
    map_ids = [
        row.get("project_id") for row in project_map
        if isinstance(row, dict) and isinstance(row.get("project_id"), str)
    ]
    for label, values in (("registry", registry_ids), ("roadmap", map_ids)):
        duplicates = _duplicates(values)
        if duplicates:
            blockers.append(f"{label} duplicate IDs: {duplicates}")
    if set(registry_ids) != set(map_ids):
        blockers.append(
            "registry/roadmap ID mismatch: "
            f"registry_only={sorted(set(registry_ids) - set(map_ids))}, "
            f"roadmap_only={sorted(set(map_ids) - set(registry_ids))}"
        )

    if roadmap.get("plan_id") != PLAN_ID or roadmap.get("version") != 3:
        blockers.append("roadmap plan_id/version mismatch")
    activation = roadmap.get("activation", {})
    if not isinstance(activation, dict) or activation.get("canonical_state") != "STATE.yaml#all_projects_governance":
        blockers.append("roadmap canonical state pointer mismatch")

    registry_by_id = {
        row["id"]: row for row in projects
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    removed_ids = {
        project_id for project_id, row in registry_by_id.items()
        if row.get("current_state_status") == "removed_local"
    }
    active_source_gaps: list[str] = []
    for row in project_map:
        if not isinstance(row, dict) or not isinstance(row.get("project_id"), str):
            blockers.append("roadmap project row lacks project_id")
            continue
        project_id = row["project_id"]
        expected_registry_ref = (
            "data/registry/external_projects.yaml"
            f"#projects[id={project_id}]"
        )
        if row.get("registry_ref") != expected_registry_ref:
            blockers.append(f"{project_id}: registry_ref mismatch")

        source_ref = row.get("source_binding_ref")
        if project_id in removed_ids:
            if source_ref is not None:
                blockers.append(f"{project_id}: removed_local cannot have a source binding")
            if row.get("disposition_rule") != REMOVED_DISPOSITION:
                blockers.append(f"{project_id}: removed_local disposition mismatch")
            if any(row.get(key) is not None for key in ("stage_id", "unit_id", "migration_unit")):
                blockers.append(f"{project_id}: removed_local cannot have execution units")
            continue

        if source_ref is None:
            active_source_gaps.append(project_id)
            if row.get("disposition_rule") != SOURCE_GAP_DISPOSITION:
                blockers.append(f"{project_id}: unexplained source gap")
            continue

        expected_source_ref = f"data/connections/metric_sources.yaml#projects.{project_id}"
        if source_ref != expected_source_ref:
            blockers.append(f"{project_id}: source_binding_ref mismatch")
        source = source_map.get(project_id)
        if not isinstance(source, dict) or not isinstance(source.get("adapter"), str):
            blockers.append(f"{project_id}: source adapter missing")

    unknown_sources = sorted(set(source_map) - set(registry_ids))
    if unknown_sources:
        blockers.append(f"metric sources without registry identity: {unknown_sources}")

    coverage_rows = {
        row.get("project_id"): row for row in coverage.get("projects", [])
        if isinstance(row, dict) and isinstance(row.get("project_id"), str)
    }
    additional_rows = {
        row.get("project_id") or row.get("discovery_id"): row
        for row in coverage.get("additional_dispositions", [])
        if isinstance(row, dict)
        and isinstance(row.get("project_id") or row.get("discovery_id"), str)
    }
    no_git: list[str] = []
    for project_id in registry_ids:
        row = additional_rows.get(project_id) if project_id in removed_ids else coverage_rows.get(project_id)
        if not isinstance(row, dict):
            blockers.append(f"{project_id}: coverage/Git disposition missing")
            continue
        git = row.get("git")
        if not isinstance(git, dict) or not isinstance(git.get("status"), str):
            blockers.append(f"{project_id}: Git disposition missing")
        elif git["status"] == "no_git":
            no_git.append(project_id)

    stages = roadmap.get("stages", [])
    stage_ids = [
        row.get("id") for row in stages
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    ]
    if len(stage_ids) != roadmap.get("round_protocol", {}).get("static_stages"):
        blockers.append("roadmap static stage count mismatch")
    migration_units = sum(
        isinstance(row, dict) and row.get("migration_unit") is not None
        for row in project_map
    )
    if migration_units != roadmap.get("round_protocol", {}).get("rollout_project_units"):
        blockers.append("roadmap rollout project count mismatch")

    cloud_excluded = sum(
        isinstance(row, dict) and row.get("disposition") == "owner_excluded"
        for row in coverage.get("additional_dispositions", [])
    )
    return {
        "valid": not blockers,
        "hard_blockers": blockers,
        "counts": {
            "registry": len(registry_ids),
            "roadmap": len(map_ids),
            "source_bindings": len(source_map),
            "active_source_gaps": len(active_source_gaps),
            "removed_local": len(removed_ids),
            "no_git": len(no_git),
            "cloud_excluded": cloud_excluded,
            "stages": len(stage_ids),
            "rollout_project_units": migration_units,
        },
        "active_source_gaps": sorted(active_source_gaps),
        "removed_local": sorted(removed_ids),
        "no_git": sorted(no_git),
    }


def validate_storage_documents(
    adapter: dict[str, Any],
    parameters: dict[str, Any],
    state: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    expected_state = (
        "~/PycharmProjects/personal-control-hub/"
        "governance/programs/storage_governance/STATE.yaml"
    )
    if adapter.get("authority", {}).get("sole_execution_state") != expected_state:
        blockers.append("storage adapter sole state pointer mismatch")

    graph = parameters.get("authority_graph", {})
    expected_root = "/Users/alalapi/PycharmProjects/personal-control-hub/"
    expected_graph = {
        "storage_router": expected_root + STORAGE_FILES["router"],
        "storage_current_state": expected_root + STORAGE_FILES["state"],
        "storage_norm": expected_root + STORAGE_FILES["norm"],
        "activation_prompt": expected_root + STORAGE_FILES["activation_prompt"],
        "parameters": expected_root + STORAGE_FILES["parameters"],
        "hub_adapter": expected_root + STORAGE_FILES["adapter"],
    }
    for key, expected in expected_graph.items():
        if graph.get(key) != expected:
            blockers.append(f"storage parameters {key} pointer mismatch")

    metadata = state.get("metadata", {})
    execution = state.get("execution_control", {})
    current_project = state.get("current_project", {})
    current_batch = state.get("current_batch", {})
    closure = state.get("closure", {})
    authorities = state.get("authorities", {})
    if metadata.get("canonical") is not True or metadata.get("authority") != "sole_execution_current_state":
        blockers.append("storage state is not the sole canonical execution state")
    if execution.get("state") != "COMPLETE_WITH_OWNER_REMOVAL":
        blockers.append("storage execution is not closed at the accepted terminal state")
    for key in ("active_projects", "active_batches", "active_writers"):
        if execution.get(key) != 0:
            blockers.append(f"storage execution {key} must be zero")
    if current_project.get("state") != "closed" or current_project.get("id") is not None:
        blockers.append("storage current project must be closed")
    if current_batch.get("state") != "closed" or current_batch.get("active_writer") is not None:
        blockers.append("storage current batch must be closed")
    if not authorities or any(value != "closed" for value in authorities.values()):
        blockers.append("storage effect authorities must all be closed")
    if (
        closure.get("all_scoped_projects_terminal") is not True
        or closure.get("pending_projects") != 0
        or closure.get("active_writers") != 0
        or closure.get("unconsumed_effect_authority") != "none"
    ):
        blockers.append("storage closure invariants mismatch")
    return blockers


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidate_identity(root: Path = ROOT) -> dict[str, Any]:
    """Hash sorted UTF-8 path/NUL/content-hash/LF rows after a format header."""
    state = _load_yaml(root / "STATE.yaml")
    task = state.get("all_projects_governance", {})
    paths = task.get("candidate_paths") if isinstance(task, dict) else None
    if not isinstance(paths, list) or not paths or any(
        not isinstance(path, str) or not path for path in paths
    ):
        raise ValueError("STATE candidate_paths must be a nonempty string list")
    rows: list[dict[str, str]] = []
    manifest = bytearray(f"{CANDIDATE_FORMAT}\n".encode("utf-8"))
    for relative in sorted(set(paths)):
        path = root / relative
        if not path.is_file():
            raise ValueError(f"candidate path is not a file: {relative}")
        digest = _sha256(path)
        rows.append({"path": relative, "sha256": digest})
        manifest.extend(relative.encode("utf-8"))
        manifest.extend(b"\0")
        manifest.extend(digest.encode("ascii"))
        manifest.extend(b"\n")
    return {
        "format": CANDIDATE_FORMAT,
        "serialization": "UTF-8 format-header/LF, then sorted UTF-8 path/NUL/SHA-256-hex/LF",
        "sha256": hashlib.sha256(manifest).hexdigest(),
        "files": rows,
    }


def _runtime_pointer(
    path_value: Any,
    expected_hash: Any = None,
) -> tuple[dict[str, Any], str | None]:
    if not isinstance(path_value, str) or not path_value:
        return {
            "exists": False,
            "verification": "unavailable",
            "hash_match": None,
        }, "runtime pointer is missing"
    path = Path(path_value).expanduser()
    if not path.is_file():
        return {
            "path": str(path),
            "exists": False,
            "verification": "unavailable",
            "hash_match": None,
        }, f"{path}: runtime pointer missing"
    actual = _sha256(path)
    hash_verified = isinstance(expected_hash, str) and bool(expected_hash)
    match = actual == expected_hash if hash_verified else None
    error = (
        f"{path}: runtime pointer hash mismatch"
        if hash_verified and not match
        else None
    )
    return {
        "path": str(path),
        "exists": True,
        "bytes": path.stat().st_size,
        "sha256": actual,
        "verification": "hash" if hash_verified else "existence_only",
        "hash_match": match,
    }, error


def run_check(root: Path = ROOT, *, runtime: bool = False) -> dict[str, Any]:
    blockers: list[str] = []
    identity: dict[str, Any] = {}
    try:
        registry = _load_yaml(root / "data/registry/external_projects.yaml")
        roadmap = _load_yaml(root / "data/roadmap/project_data_v3.yaml")
        sources = _load_yaml(root / "data/connections/metric_sources.yaml")
        coverage = _load_json(root / "docs/reports/all-projects-governance/bootstrap/coverage.json")
        result = validate_documents(registry, roadmap, sources, coverage)
        blockers.extend(result["hard_blockers"])
        identity = candidate_identity(root)
    except (OSError, ValueError, yaml.YAMLError, json.JSONDecodeError) as exc:
        return {"valid": False, "hard_blockers": [str(exc)], "runtime_checked": runtime}

    storage_paths = {key: root / relative for key, relative in STORAGE_FILES.items()}
    missing_storage = [
        relative for key, relative in STORAGE_FILES.items()
        if not storage_paths[key].is_file()
    ]
    blockers.extend(f"required storage entry missing: {path}" for path in missing_storage)
    storage_hashes: dict[str, str] = {}
    state: dict[str, Any] = {}
    if not missing_storage:
        try:
            adapter = _load_yaml(storage_paths["adapter"])
            parameters = _load_yaml(storage_paths["parameters"])
            state = _load_yaml(storage_paths["state"])
            blockers.extend(validate_storage_documents(adapter, parameters, state))
            storage_hashes = {
                key: _sha256(path) for key, path in storage_paths.items()
            }
        except (OSError, ValueError, yaml.YAMLError) as exc:
            blockers.append(str(exc))

    runtime_pointers: dict[str, Any] = {}
    if runtime and state:
        pointer_specs = {
            "contract": (
                state.get("task_contract", {}).get("path"),
                state.get("task_contract", {}).get("sha256"),
            ),
            "manifest": (
                state.get("inventory_epoch", {}).get("project_manifest"),
                state.get("inventory_epoch", {}).get("project_manifest_sha256"),
            ),
            "final_report": (state.get("closure", {}).get("final_report"), None),
            "authority_pointer_audit": (
                state.get("inventory_epoch", {}).get("authority_pointer_audit"),
                None,
            ),
            "identity_guard": (
                state.get("disk_identity", {}).get("identity_guard"),
                state.get("disk_identity", {}).get("guard_sha256_at_activation"),
            ),
            "storage_map": (
                state.get("disk_identity", {}).get("storage_map"),
                state.get("disk_identity", {}).get("storage_map_sha256_current"),
            ),
        }
        for name, (path_value, expected_hash) in pointer_specs.items():
            row, error = _runtime_pointer(path_value, expected_hash)
            runtime_pointers[name] = row
            if error:
                blockers.append(error)

    return {
        **result,
        "valid": not blockers,
        "hard_blockers": blockers,
        "storage_entry": {
            "files": len(storage_hashes),
            "hashes": storage_hashes,
            "terminal_state": state.get("execution_control", {}).get("state"),
            "active_writers": state.get("execution_control", {}).get("active_writers"),
        },
        "runtime_checked": runtime,
        "runtime_pointers": runtime_pointers,
        "candidate_identity": identity,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PROJECT-DATA-PUBLISH-SYNC-V3 baseline check")
    parser.add_argument("--runtime", action="store_true",
                        help="Verify only the explicitly declared storage evidence/guard pointers")
    parser.add_argument("--json", action="store_true", help="Print deterministic JSON")
    args = parser.parse_args(argv)
    result = run_check(runtime=args.runtime)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("=== Project Data V3 Baseline Check ===")
        print(f"结果：{'ok' if result['valid'] else 'fail'}")
        print(f"计数：{json.dumps(result.get('counts', {}), ensure_ascii=False, sort_keys=True)}")
        print(f"存储入口文件：{result.get('storage_entry', {}).get('files', 0)}")
        print(f"运行时指针检查：{result['runtime_checked']}")
        for blocker in result["hard_blockers"]:
            print(f"  - {blocker}")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
