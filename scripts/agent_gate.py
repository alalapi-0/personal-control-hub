#!/usr/bin/env python3
"""Local read-only gate checker for personal-control-hub auto-advance."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


ROOT = Path(__file__).resolve().parents[1]

CORE_FILES = [
    "README.md",
    "AGENTS.md",
    "project.yaml",
    "docs/00_start_here.md",
    "docs/01_project_ultimate_goal.md",
    "docs/02_master_roadmap.md",
    "governance/agent_policy.yaml",
    "governance/round_state.yaml",
    "data/mcp/mcp_capability_registry.yaml",
    "data/mcp/mcp_approval_policy.yaml",
    "data/mcp/mcp_integration_roadmap.yaml",
    "prompts/codex_project_driver.md",
    "prompts/cursor_project_driver.md",
]

REQUIRED_ROUND_FIELDS = [
    "id",
    "name",
    "status",
    "goal",
    "acceptance_criteria",
    "can_auto_advance",
    "hard_blockers",
]

REQUIRED_MCP_IDS = {
    "chrome-devtools",
    "context7",
    "filesystem",
    "github",
    "playwright",
    "stitch",
}

SCAN_ROOTS = [
    "README.md",
    "AGENTS.md",
    "project.yaml",
    "docs",
    "governance",
    "data/roadmap",
    "data/gates",
    "data/mcp",
    "data/scheduler",
    "data/state",
    "prompts",
    "scripts",
]

SELF_PATH = Path(__file__).resolve()

SKIP_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    "target",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
}

SCAN_SUFFIXES = {".yaml", ".yml", ".md", ".json", ".jsonl", ".py"}

FORBIDDEN_MARKERS = [
    "FEISHU_APP_SECRET" + "=",
    "FEISHU_WEBHOOK_URL" + "=http",
    "BEGIN " + "PRIVATE KEY",
    "BEGIN " + "RSA PRIVATE KEY",
    "xoxb" + "-",
]

FORBIDDEN_REGEX = [
    re.compile(r"sk-[0-9A-Za-z]{20,}"),
    re.compile(r"ghp_[0-9A-Za-z]{20,}"),
    re.compile(r"github_pat_[0-9A-Za-z_]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
]


def _load_yaml(relative: str, hard_blockers: list[str]) -> Any:
    path = ROOT / relative
    if not path.is_file():
        hard_blockers.append(f"{relative}: 文件不存在")
        return None
    if yaml is None:
        hard_blockers.append("PyYAML 未安装，无法解析 YAML；请安装依赖或改用 JSON/简化检查。")
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except Exception as exc:  # pragma: no cover - defensive
        hard_blockers.append(f"{relative}: YAML 解析失败：{exc}")
        return None


def _iter_scan_files() -> list[Path]:
    files: list[Path] = []
    for item in SCAN_ROOTS:
        root = ROOT / item
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix in SCAN_SUFFIXES and root.name != ".env":
                files.append(root)
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
                continue
            if path.name == ".env" or path.suffix not in SCAN_SUFFIXES:
                continue
            files.append(path)
    return sorted(set(files))


def _scan_for_secrets() -> list[str]:
    hits: list[str] = []
    for path in _iter_scan_files():
        if path.resolve() == SELF_PATH:
            continue
        relative = path.relative_to(ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for marker in FORBIDDEN_MARKERS:
            if marker in text:
                hits.append(f"{relative}: 疑似真实密钥标记 {marker!r}")
        for pattern in FORBIDDEN_REGEX:
            if pattern.search(text):
                hits.append(f"{relative}: 疑似真实 token 正则 {pattern.pattern!r}")
    return hits


def _check_core_files(hard_blockers: list[str]) -> None:
    missing = [relative for relative in CORE_FILES if not (ROOT / relative).is_file()]
    if missing:
        hard_blockers.extend(f"核心文件缺失：{relative}" for relative in missing)


def _get_rounds(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    rounds = data.get("rounds", [])
    if isinstance(rounds, list):
        return [item for item in rounds if isinstance(item, dict)]
    return []


def _check_round_tasks(hard_blockers: list[str], soft_warnings: list[str]) -> list[dict[str, Any]]:
    data = _load_yaml("data/roadmap/round_tasks.yaml", hard_blockers)
    rounds = _get_rounds(data)
    if not rounds:
        hard_blockers.append("data/roadmap/round_tasks.yaml: rounds 为空或结构错误")
        return []

    seen: set[str] = set()
    for item in rounds:
        round_id = str(item.get("id", "<unknown>"))
        if round_id in seen:
            hard_blockers.append(f"round_tasks: round id 重复：{round_id}")
        seen.add(round_id)

        status = item.get("status")
        if status in {"active", "planned"}:
            for field in REQUIRED_ROUND_FIELDS:
                if field not in item:
                    hard_blockers.append(f"round_tasks: {round_id} 缺少字段 {field}")
            if not item.get("acceptance_criteria"):
                hard_blockers.append(f"round_tasks: {round_id} acceptance_criteria 为空")
            if not item.get("hard_blockers"):
                hard_blockers.append(f"round_tasks: {round_id} hard_blockers 为空")
            if item.get("can_auto_advance") is not True and status == "planned":
                soft_warnings.append(f"{round_id}: planned 但 can_auto_advance 不是 true，可能需要人工确认")

    required_ui_rounds = {"ROUND-9", "ROUND-9-5", "ROUND-10", "ROUND-10-5", "ROUND-11", "ROUND-11-5"}
    missing_ui = sorted(required_ui_rounds - seen)
    if missing_ui:
        hard_blockers.append(f"UI 相关轮次缺失：{', '.join(missing_ui)}")

    return rounds


def _check_policy(hard_blockers: list[str]) -> None:
    data = _load_yaml("data/gates/auto_advance_policy.yaml", hard_blockers)
    if not isinstance(data, dict):
        return
    default_behavior = data.get("default_behavior", {})
    required = {
        "no_hard_blocker": "continue",
        "soft_blocker_only": "warn_and_continue",
        "missing_required_secret": "stop",
        "destructive_action_required": "stop",
        "external_write_required": "stop",
    }
    for key, expected in required.items():
        if default_behavior.get(key) != expected:
            hard_blockers.append(f"auto_advance_policy: default_behavior.{key} 应为 {expected}")


def _check_mcp_registry(hard_blockers: list[str]) -> None:
    data = _load_yaml("data/mcp/mcp_capability_registry.yaml", hard_blockers)
    if not isinstance(data, dict):
        return
    capabilities = data.get("capabilities")
    if not isinstance(capabilities, list):
        hard_blockers.append("mcp_capability_registry: capabilities 必须是列表")
        return

    default_enabled = bool(data.get("registry", {}).get("default_enabled", False))
    found: set[str] = set()
    for index, item in enumerate(capabilities):
        if not isinstance(item, dict):
            hard_blockers.append(f"mcp_capability_registry: capabilities[{index}] 不是 mapping")
            continue
        cap_id = item.get("id")
        if cap_id:
            found.add(str(cap_id))
        for field in ["id", "name", "approval_level", "enabled_in_project"]:
            if field not in item:
                hard_blockers.append(f"mcp_capability_registry: {cap_id or index} 缺少字段 {field}")
        if cap_id in REQUIRED_MCP_IDS and item.get("enabled_in_project") is not default_enabled:
            hard_blockers.append(
                f"mcp_capability_registry: {cap_id} enabled_in_project 应与 default_enabled={default_enabled} 一致"
            )

    missing = sorted(REQUIRED_MCP_IDS - found)
    if missing:
        hard_blockers.append(f"mcp_capability_registry: 缺少 MCP：{', '.join(missing)}")

    policy = _load_yaml("data/mcp/mcp_approval_policy.yaml", hard_blockers)
    levels = policy.get("levels", {}) if isinstance(policy, dict) else {}
    if not isinstance(levels, dict):
        hard_blockers.append("mcp_approval_policy: levels 必须是 mapping")
        return
    for level_id in ("L2", "L3"):
        level = levels.get(level_id, {})
        if not isinstance(level, dict) or level.get("confirmation_required") is not True:
            hard_blockers.append(f"mcp_approval_policy: {level_id} 必须要求人工确认")
    l3 = levels.get("L3", {})
    if not isinstance(l3, dict) or l3.get("default_forbidden") is not True:
        hard_blockers.append("mcp_approval_policy: L3 具体高风险动作必须保持默认禁止")


def _find_round(rounds: list[dict[str, Any]], round_id: str) -> dict[str, Any] | None:
    for item in rounds:
        if item.get("id") == round_id:
            return item
    return None


def _determine_next_round(rounds: list[dict[str, Any]], requested_round: str | None) -> str | None:
    if requested_round:
        item = _find_round(rounds, requested_round)
        if item:
            return item.get("next_round")
        return None

    active = [item for item in rounds if item.get("status") == "active"]
    if active:
        return active[0].get("next_round")

    for item in rounds:
        if item.get("status") == "planned":
            return item.get("id")
    return None


def run_gate(requested_round: str | None = None) -> dict[str, Any]:
    hard_blockers: list[str] = []
    soft_warnings: list[str] = []

    _check_core_files(hard_blockers)
    rounds = _check_round_tasks(hard_blockers, soft_warnings)
    _check_policy(hard_blockers)
    _check_mcp_registry(hard_blockers)

    if not (ROOT / "docs/14_ui_console_plan.md").is_file():
        soft_warnings.append("docs/14_ui_console_plan.md 不存在，UI 计划尚未落地")

    secret_hits = _scan_for_secrets()
    hard_blockers.extend(secret_hits)

    requested = _find_round(rounds, requested_round) if requested_round else None
    requested_can_auto_advance = None
    if requested_round:
        if requested is None:
            hard_blockers.append(f"未找到指定 round：{requested_round}")
        else:
            requested_can_auto_advance = bool(requested.get("can_auto_advance"))
            if not requested_can_auto_advance:
                soft_warnings.append(f"{requested_round}: can_auto_advance=false，需要人工确认或手动推进")

    if hard_blockers:
        decision = "stop"
    elif soft_warnings:
        decision = "warn_and_continue"
    else:
        decision = "continue"

    next_round = _determine_next_round(rounds, requested_round)
    if requested_round and requested_can_auto_advance is False and decision == "continue":
        decision = "warn_and_continue"

    checks_passed = decision in {"continue", "warn_and_continue"}

    return {
        "decision": decision,
        "hard_blockers": hard_blockers,
        "soft_warnings": soft_warnings,
        "next_round": next_round,
        "checks_passed": checks_passed,
        "can_auto_advance": False,
        "authority_granted": False,
    }


def _print_text(result: dict[str, Any]) -> None:
    print("=== Agent Gate 检查结果 ===")
    print(f"决策：{result['decision']}")
    print(f"硬阻塞：{len(result['hard_blockers'])}")
    print(f"软警告：{len(result['soft_warnings'])}")
    print(f"下一轮候选：{result['next_round'] or '无'}")
    print("动作授权：未授予（gate 只报告检查结果）")

    if result["hard_blockers"]:
        print("\n硬阻塞明细：")
        for item in result["hard_blockers"]:
            print(f"  - {item}")

    if result["soft_warnings"]:
        print("\n软警告明细：")
        for item in result["soft_warnings"]:
            print(f"  - {item}")

    if result["decision"] == "stop":
        suggestion = "存在硬阻塞；停止触发阻塞的动作，并按当前上级规则处理。"
    elif result["decision"] == "warn_and_continue":
        suggestion = "检查通过但有 warning；仅在当前已有授权范围内继续，默认不写日志。"
    else:
        suggestion = "检查通过；仅在当前已有授权范围内继续。"
    print(f"建议：{suggestion}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="personal-control-hub local agent gate")
    parser.add_argument("--round", dest="round_id", help="检查指定 round 是否可自动推进")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    result = run_gate(args.round_id)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_text(result)
    return 1 if result["decision"] == "stop" else 0


if __name__ == "__main__":
    sys.exit(main())
