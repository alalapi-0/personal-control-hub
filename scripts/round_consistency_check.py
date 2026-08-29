#!/usr/bin/env python3
"""Lightweight round consistency checker for personal-control-hub."""

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

FILES = {
    "state": "STATE.yaml",
    "roadmap": "docs/02_master_roadmap.md",
    "round_tasks": "data/roadmap/round_tasks.yaml",
    "round_dependencies": "data/roadmap/round_dependencies.yaml",
}


def _load_yaml(relative: str, hard_blockers: list[str]) -> Any:
    path = ROOT / relative
    if not path.is_file():
        hard_blockers.append(f"{relative}: 文件不存在")
        return None
    if yaml is None:
        hard_blockers.append("PyYAML 未安装，无法解析 YAML")
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except Exception as exc:  # pragma: no cover
        hard_blockers.append(f"{relative}: YAML 解析失败：{exc}")
        return None


def _get_rounds(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    rounds = data.get("rounds", [])
    if isinstance(rounds, list):
        return [item for item in rounds if isinstance(item, dict)]
    return []


def _find_round(rounds: list[dict[str, Any]], round_id: str) -> dict[str, Any] | None:
    for item in rounds:
        if item.get("id") == round_id:
            return item
    return None


def _roadmap_mentions_round(text: str, round_id: str, round_name: str | None) -> bool:
    if round_id in text:
        return True
    if round_name and round_name in text:
        return True
    compact = round_id.replace("ROUND-", "Round ")
    if compact in text:
        return True
    dotted = round_id.replace("ROUND-", "").replace("-", ".")
    if f"Round {dotted}" in text or f"{dotted}" in text:
        return True
    return False


def run_check() -> dict[str, Any]:
    hard_blockers: list[str] = []
    warnings: list[str] = []

    state = _load_yaml(FILES["state"], hard_blockers) or {}
    round_tasks_data = _load_yaml(FILES["round_tasks"], hard_blockers) or {}
    round_deps_data = _load_yaml(FILES["round_dependencies"], hard_blockers) or {}

    state_meta = state.get("metadata", {}) if isinstance(state, dict) else {}
    state_project = state.get("project", {}) if isinstance(state, dict) else {}
    state_round = state.get("current_round", {}) if isinstance(state, dict) else {}
    if not isinstance(state_meta, dict) or state_meta.get("authority") != "canonical":
        hard_blockers.append("STATE.yaml 必须声明 metadata.authority=canonical")
    if not isinstance(state_project, dict):
        state_project = {}
    if not isinstance(state_round, dict):
        state_round = {}

    current_round = state_round.get("id")
    next_round = state_round.get("next_round")
    current_phase = state_project.get("phase")
    last_completed = state_round.get("last_completed_round")

    rounds = _get_rounds(round_tasks_data)
    round_ids = {str(item.get("id")) for item in rounds if item.get("id")}
    completed_rounds = {
        str(item.get("id"))
        for item in rounds
        if item.get("id") and str(item.get("status", "")).startswith("completed")
    }
    active_rounds = {
        str(item.get("id")) for item in rounds if item.get("id") and item.get("status") == "active"
    }

    if current_round and current_round not in round_ids:
        hard_blockers.append(f"current_round {current_round} 不在 round_tasks 中")
    if next_round and next_round not in round_ids:
        hard_blockers.append(f"next_round {next_round} 不在 round_tasks 中")

    if current_round and current_round not in active_rounds:
        hard_blockers.append(f"round_tasks 中当前轮次 {current_round} 不是 active")

    if last_completed and last_completed not in completed_rounds:
        hard_blockers.append(f"last_completed_round {last_completed} 在 round_tasks 中不是 completed")

    current_round_item = _find_round(rounds, current_round) if current_round else None
    if current_round_item and current_round_item.get("phase") and current_phase:
        if current_round_item.get("phase") != current_phase:
            warnings.append(
                f"round_tasks 中 {current_round} phase 与 round_state 不一致"
            )

    deps = round_deps_data.get("dependencies") or round_deps_data.get("rounds") or {}
    if isinstance(deps, dict) and current_round and current_round in deps:
        dep_entry = deps[current_round]
        if isinstance(dep_entry, dict):
            requires = dep_entry.get("requires", [])
            if isinstance(requires, list):
                for req in requires:
                    if req not in completed_rounds and req not in active_rounds:
                        if req != current_round:
                            warnings.append(f"{current_round} 依赖 {req} 尚未在 completed/active 中")

    roadmap_path = ROOT / FILES["roadmap"]
    if roadmap_path.is_file() and current_round:
        roadmap_text = roadmap_path.read_text(encoding="utf-8")
        round_name = state_round.get("name")
        if not _roadmap_mentions_round(roadmap_text, str(current_round), round_name):
            hard_blockers.append(f"master roadmap 未提及当前轮次 {current_round}")

    result_status = "ok" if not hard_blockers else "fail"
    if result_status == "ok" and warnings:
        result_status = "ok_with_warnings"

    return {
        "result": result_status,
        "current_round": current_round,
        "next_round": next_round,
        "current_phase": current_phase,
        "warnings": warnings,
        "hard_blockers": hard_blockers,
    }


def _print_text(result: dict[str, Any]) -> None:
    print("=== 轮次一致性检查 ===")
    print(f"结果：{result['result']}")
    print(f"当前轮次：{result['current_round']}")
    print(f"下一轮次：{result['next_round']}")
    print(f"警告：{len(result['warnings'])}")
    print(f"硬阻塞：{len(result['hard_blockers'])}")
    if result["warnings"]:
        print("\n警告明细：")
        for item in result["warnings"]:
            print(f"  - {item}")
    if result["hard_blockers"]:
        print("\n硬阻塞明细：")
        for item in result["hard_blockers"]:
            print(f"  - {item}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="personal-control-hub round consistency check")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    result = run_check()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_text(result)

    return 1 if result["hard_blockers"] else 0


if __name__ == "__main__":
    sys.exit(main())
