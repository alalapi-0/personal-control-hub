"""Read-only MCP registry and approval policy helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from hub.paths import PROJECT_ROOT

MCP_DIR = PROJECT_ROOT / "data" / "mcp"
REGISTRY_PATH = MCP_DIR / "mcp_capability_registry.yaml"
POLICY_PATH = MCP_DIR / "mcp_approval_policy.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data if isinstance(data, dict) else {}


def load_registry() -> dict[str, Any]:
    return _load_yaml(REGISTRY_PATH)


def load_policy() -> dict[str, Any]:
    return _load_yaml(POLICY_PATH)


def list_capabilities_table() -> list[dict[str, Any]]:
    registry = load_registry()
    capabilities = registry.get("capabilities", [])
    if not isinstance(capabilities, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "id": item.get("id", ""),
                "name": item.get("name", ""),
                "category": item.get("category", ""),
                "status": item.get("status", ""),
                "enabled": item.get("enabled_in_project", False),
                "approval_level": item.get("approval_level", ""),
                "planned_round": item.get("planned_round", ""),
            }
        )
    return rows


def list_policy_levels_table() -> list[dict[str, Any]]:
    policy = load_policy()
    levels = policy.get("levels", {})
    if not isinstance(levels, dict):
        return []
    rows: list[dict[str, Any]] = []
    for level_id in ("L0", "L1", "L2", "L3"):
        item = levels.get(level_id, {})
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "level": level_id,
                "name": item.get("name", ""),
                "confirmation_required": item.get("confirmation_required", False),
                "logging_required": item.get("logging_required", False),
                "default_forbidden": item.get("default_forbidden", False),
            }
        )
    return rows


def print_mcp_list() -> int:
    if not REGISTRY_PATH.is_file():
        print(f"Missing registry: {REGISTRY_PATH}")
        return 1
    print("MCP capability registry (read-only)")
    print(f"source: {REGISTRY_PATH.relative_to(PROJECT_ROOT)}")
    print()
    print(f"{'id':<18} {'category':<22} {'level':<6} {'enabled':<8} status")
    print("-" * 72)
    for row in list_capabilities_table():
        enabled = "yes" if row["enabled"] else "no"
        print(
            f"{row['id']:<18} {row['category']:<22} {row['approval_level']:<6} "
            f"{enabled:<8} {row['status']}"
        )
    return 0


def print_mcp_policy() -> int:
    if not POLICY_PATH.is_file():
        print(f"Missing policy: {POLICY_PATH}")
        return 1
    print("MCP approval policy (read-only)")
    print(f"source: {POLICY_PATH.relative_to(PROJECT_ROOT)}")
    print()
    print(f"{'level':<6} {'name':<28} {'confirm':<8} {'log':<6} forbidden")
    print("-" * 72)
    for row in list_policy_levels_table():
        confirm = "yes" if row["confirmation_required"] else "no"
        log = "yes" if row["logging_required"] else "no"
        forbidden = "yes" if row["default_forbidden"] else "no"
        print(
            f"{row['level']:<6} {row['name']:<28} {confirm:<8} {log:<6} {forbidden}"
        )
    return 0
