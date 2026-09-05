#!/usr/bin/env python3
"""Minimal repository skeleton checker for personal-control-hub."""

from __future__ import annotations

from pathlib import Path
import re
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


ROOT = Path(__file__).resolve().parents[1]
SECRET_SCAN_EXCLUDE = {
    "scripts/agent_gate.py",
}

REQUIRED_FILES = [
    "README.md",
    "AGENTS.md",
    "STATE.yaml",
    "NORTH_STAR.md",
    "project.yaml",
    "governance/round_state.yaml",
    "governance/agent_policy.yaml",
    "governance/file_role_map.yaml",
    "governance/repo_protocol_standard.yaml",
    "governance/adapters/storage_governance.yaml",
    "docs/00_start_here.md",
    "docs/01_project_ultimate_goal.md",
    "docs/02_master_roadmap.md",
    "docs/03_architecture.md",
    "docs/04_data_model.md",
    "docs/05_external_project_protocol.md",
    "docs/06_scheduler_design.md",
    "docs/07_integration_strategy.md",
    "docs/08_codex_cursor_workflow.md",
    "docs/09_feishu_lark_strategy.md",
    "docs/10_decision_log.md",
    "docs/11_mcp_infrastructure_strategy.md",
    "docs/12_external_tool_approval_model.md",
    "docs/13_cursor_mcp_workspace_setup.md",
    "docs/14_ui_console_plan.md",
    "docs/15_auto_advance_gate.md",
    "docs/16_runtime_environment.md",
    "docs/17_continuous_auto_advance_runner.md",
    "docs/reports/restart_audit_report.md",
    "data/registry/external_projects.yaml",
    "data/programs/active_programs.yaml",
    "data/programs/program_project_links.yaml",
    "data/programs/storage_governance_goal.yaml",
    "data/tasks/inbox.yaml",
    "data/tasks/next_actions.yaml",
    "data/scheduler/scheduled_tasks.yaml",
    "data/integrations/integration_targets.yaml",
    "data/state/current_status.yaml",
    "data/logs/automation_log.jsonl",
    "data/logs/project_decision_log.jsonl",
    "data/mcp/mcp_capability_registry.yaml",
    "data/mcp/mcp_approval_policy.yaml",
    "data/mcp/mcp_integration_roadmap.yaml",
    "data/mcp/mcp_servers.example.yaml",
    "data/roadmap/round_tasks.yaml",
    "data/roadmap/round_dependencies.yaml",
    "data/gates/auto_advance_policy.yaml",
    "data/gates/gate_checklist.yaml",
    "prompts/codex_project_driver.md",
    "prompts/cursor_project_driver.md",
    "prompts/auto_advance_agent_prompt.md",
    "prompts/storage_governance_goal_mode.md",
    "prompts/mcp_audit_prompt.md",
    "prompts/cursor_mcp_usage_prompt.md",
    "scripts/bootstrap.py",
    "scripts/check_repo.py",
    "scripts/agent_gate.py",
    "scripts/check_environment.py",
    "scripts/round_consistency_check.py",
    "scripts/auto_advance_runner.py",
    "data/runtime/environment_requirements.yaml",
    "data/runtime/toolchain_status.yaml",
    "data/runtime/validation_commands.yaml",
    "prompts/continuous_auto_advance_prompt.md",
    ".cursor/README.md",
    ".cursor/mcp.example.json",
]

REQUIRED_DIRS = [
    "docs/archive",
    "docs/reports",
    "data/mcp",
    "data/roadmap",
    "data/gates",
    "data/runtime",
    "data/project_profiles",
    "data/project_snapshots",
    "data/project_scans",
    "data/tasks",
    "data/scheduler",
    "data/integrations",
    "data/state",
    "data/logs",
    "prompts/templates",
    "src/hub/services",
    "tests",
    ".cursor",
]

REQUIRED_MCP_IDS = [
    "chrome-devtools",
    "context7",
    "filesystem",
    "github",
    "playwright",
    "stitch",
]

REQUIRED_CAPABILITY_FIELDS = [
    "id",
    "name",
    "category",
    "status",
    "enabled_in_project",
    "recommended_for_project",
    "approval_level",
    "purpose",
    "allowed_scope",
    "forbidden_scope",
    "planned_round",
    "notes",
]

L2_L3_LEVELS = {"L2", "L3"}

FORBIDDEN_MARKERS = [
    "FEISHU_APP_SECRET" + "=",
    "FEISHU_WEBHOOK_URL" + "=http",
    "sk" + "-",
    "xoxb" + "-",
    "BEGIN PRIVATE " + "KEY",
]

FORBIDDEN_REGEX = [
    re.compile(r"ghp_[0-9A-Za-z]{20,}"),
    re.compile(r"github_pat_[0-9A-Za-z_]{20,}"),
]

MCP_SCAN_PATHS = [
    "data/mcp/mcp_capability_registry.yaml",
    "data/mcp/mcp_approval_policy.yaml",
    "data/mcp/mcp_integration_roadmap.yaml",
    "data/mcp/mcp_servers.example.yaml",
    ".cursor/mcp.example.json",
]


def _scan_secrets_in_text(relative: str, text: str) -> list[str]:
    hits: list[str] = []
    for marker in FORBIDDEN_MARKERS:
        if marker in text:
            hits.append(f"{relative}: contains forbidden marker {marker!r}")
    for pattern in FORBIDDEN_REGEX:
        if pattern.search(text):
            hits.append(f"{relative}: contains suspected token matching {pattern.pattern!r}")
    return hits


def _check_mcp_registry() -> list[str]:
    issues: list[str] = []
    registry_path = ROOT / "data/mcp/mcp_capability_registry.yaml"
    if not registry_path.is_file():
        return ["data/mcp/mcp_capability_registry.yaml: missing"]

    if yaml is None:
        issues.append("PyYAML not installed; cannot validate MCP registry structure")
        return issues

    with registry_path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    default_enabled = bool((data or {}).get("registry", {}).get("default_enabled", False))
    capabilities = (data or {}).get("capabilities", [])
    if not isinstance(capabilities, list):
        return ["mcp_capability_registry: capabilities must be a list"]

    found_ids = set()
    policy_path = ROOT / "data/mcp/mcp_approval_policy.yaml"
    policy_data: dict = {}
    if policy_path.is_file():
        with policy_path.open(encoding="utf-8") as handle:
            loaded_policy = yaml.safe_load(handle)
        policy_data = loaded_policy if isinstance(loaded_policy, dict) else {}
    else:
        issues.append("data/mcp/mcp_approval_policy.yaml: missing")

    policy_levels = policy_data.get("levels", {})
    if not isinstance(policy_levels, dict):
        policy_levels = {}

    for index, item in enumerate(capabilities):
        if not isinstance(item, dict):
            issues.append(f"mcp_capability_registry: capabilities[{index}] must be a mapping")
            continue
        cap_id = item.get("id")
        if cap_id:
            found_ids.add(cap_id)
        for field in REQUIRED_CAPABILITY_FIELDS:
            if field not in item:
                issues.append(
                    f"mcp_capability_registry: capability {cap_id or index} missing field {field!r}"
                )
        if cap_id in REQUIRED_MCP_IDS and item.get("enabled_in_project") is not default_enabled:
            issues.append(
                f"mcp_capability_registry: {cap_id} enabled_in_project must match default_enabled={default_enabled}"
            )
        approval_level = item.get("approval_level")
        if approval_level in L2_L3_LEVELS:
            level_policy = policy_levels.get(approval_level, {})
            if not isinstance(level_policy, dict):
                issues.append(
                    f"mcp_approval_policy: missing policy for approval level {approval_level}"
                )
            elif level_policy.get("confirmation_required") is not True:
                issues.append(
                    f"mcp_approval_policy: {approval_level} must require confirmation"
                )
            if approval_level == "L3" and level_policy.get("default_forbidden") is not True:
                issues.append("mcp_approval_policy: L3 must remain default_forbidden")

    for required_id in REQUIRED_MCP_IDS:
        if required_id not in found_ids:
            issues.append(f"mcp_capability_registry: missing required MCP id {required_id!r}")

    return issues


def main() -> int:
    missing_files = [path for path in REQUIRED_FILES if not (ROOT / path).is_file()]
    missing_dirs = [path for path in REQUIRED_DIRS if not (ROOT / path).is_dir()]
    secret_hits: list[str] = []
    mcp_issues: list[str] = []

    scan_paths = list(dict.fromkeys(REQUIRED_FILES + MCP_SCAN_PATHS))
    for relative in scan_paths:
        if relative in SECRET_SCAN_EXCLUDE:
            continue
        path = ROOT / relative
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        secret_hits.extend(_scan_secrets_in_text(relative, text))

    mcp_issues = _check_mcp_registry()

    print("personal-control-hub skeleton check")
    print(f"root: {ROOT}")

    if missing_files:
        print("\nMissing files:")
        for item in missing_files:
            print(f"  - {item}")
    else:
        print("\nFiles: OK")

    if missing_dirs:
        print("\nMissing directories:")
        for item in missing_dirs:
            print(f"  - {item}")
    else:
        print("Directories: OK")

    if mcp_issues:
        print("\nMCP registry issues:")
        for item in mcp_issues:
            print(f"  - {item}")
    else:
        print("MCP registry: OK")

    if secret_hits:
        print("\nPotential secret markers:")
        for item in secret_hits:
            print(f"  - {item}")
    else:
        print("Secret marker scan: OK")

    if missing_files or missing_dirs or secret_hits or mcp_issues:
        print("\nResult: FAIL")
        return 1

    print("\nResult: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
