#!/usr/bin/env python3
"""Continuous auto-advance entry runner for personal-control-hub."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


ROOT = Path(__file__).resolve().parents[1]
SENSITIVE_PATTERNS = [
    re.compile(r"^\.env$"),
    re.compile(r"^\.env\."),
    re.compile(r"\.pem$"),
    re.compile(r"\.key$"),
    re.compile(r"^id_rsa$"),
    re.compile(r"^id_ed25519$"),
    re.compile(r"^secrets\."),
]

SENSITIVE_CONTENT_MARKERS = [
    "OPENAI_API_KEY" + "=",
    "FEISHU_APP_SECRET" + "=",
    "GITHUB_TOKEN" + "=",
]


def _load_yaml(relative: str) -> Any:
    path = ROOT / relative
    if not path.is_file() or yaml is None:
        return None
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _run_script(relative: str, extra_args: list[str] | None = None) -> tuple[int, str]:
    script = ROOT / relative
    if not script.is_file():
        return 127, f"脚本不存在：{relative}"
    cmd = [sys.executable, str(script)] + (extra_args or [])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode, output.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def _parse_json_from_output(output: str) -> dict[str, Any] | None:
    start = output.find("{")
    if start < 0:
        return None
    try:
        return json.loads(output[start:])
    except json.JSONDecodeError:
        return None


def _get_round_context() -> dict[str, Any]:
    round_state = _load_yaml("governance/round_state.yaml") or {}
    return {
        "current_round": round_state.get("current_round"),
        "current_round_name": round_state.get("current_round_name"),
        "next_round": round_state.get("next_round"),
        "current_phase": round_state.get("current_phase"),
    }


def _find_round_task(round_id: str | None) -> dict[str, Any] | None:
    if not round_id:
        return None
    data = _load_yaml("data/roadmap/round_tasks.yaml") or {}
    rounds = data.get("rounds", [])
    if not isinstance(rounds, list):
        return None
    for item in rounds:
        if isinstance(item, dict) and item.get("id") == round_id:
            return item
    return None


def _collect_hard_and_soft(
    env_result: dict[str, Any] | None,
    gate_result: dict[str, Any] | None,
    consistency_result: dict[str, Any] | None,
    extra_hard: list[str] | None = None,
    extra_soft: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    hard: list[str] = list(extra_hard or [])
    soft: list[str] = list(extra_soft or [])

    if env_result:
        hard.extend(env_result.get("hard_blockers", []))
        soft.extend(env_result.get("warnings", []))

    if gate_result:
        if gate_result.get("decision") == "stop":
            hard.extend(gate_result.get("hard_blockers", []))
        soft.extend(gate_result.get("soft_warnings", []))

    if consistency_result:
        hard.extend(consistency_result.get("hard_blockers", []))
        soft.extend(consistency_result.get("warnings", []))

    return hard, soft


def _decide(hard: list[str], soft: list[str]) -> str:
    if hard:
        return "stop"
    if soft:
        return "warn_and_continue"
    return "continue"


def _run_checks() -> dict[str, Any]:
    hard_blockers: list[str] = []
    soft_warnings: list[str] = []

    code, env_out = _run_script("scripts/check_environment.py", ["--json"])
    env_result = _parse_json_from_output(env_out)
    if env_result is None:
        hard_blockers.append("check_environment.py 未能输出有效 JSON")

    code, gate_out = _run_script("scripts/agent_gate.py", ["--json"])
    gate_result = _parse_json_from_output(gate_out)
    if gate_result is None:
        hard_blockers.append("agent_gate.py 未能输出有效 JSON")

    code, consistency_out = _run_script("scripts/round_consistency_check.py", ["--json"])
    consistency_result = _parse_json_from_output(consistency_out)
    if consistency_result is None:
        hard_blockers.append("round_consistency_check.py 未能输出有效 JSON")

    hard, soft = _collect_hard_and_soft(
        env_result,
        gate_result,
        consistency_result,
        extra_hard=hard_blockers,
        extra_soft=soft_warnings,
    )
    context = _get_round_context()
    decision = _decide(hard, soft)

    return {
        "decision": decision,
        "hard_blockers": hard,
        "soft_warnings": soft,
        "env_result": env_result,
        "gate_result": gate_result,
        "consistency_result": consistency_result,
        "context": context,
        "checks_passed": decision in {"continue", "warn_and_continue"},
        "can_continue": False,
        "authority_granted": False,
    }


def _print_check_summary(result: dict[str, Any]) -> None:
    ctx = result["context"]
    print("=== Auto Advance Runner: check ===")
    print(f"决策：{result['decision']}")
    print(f"当前轮次：{ctx.get('current_round')}")
    print(f"下一轮次：{ctx.get('next_round')}")
    print(f"硬阻塞：{len(result['hard_blockers'])}")
    print(f"软警告：{len(result['soft_warnings'])}")
    print(f"检查通过：{'是' if result['checks_passed'] else '否'}")
    print("动作授权：未授予（runner 只报告检查结果）")
    if result["hard_blockers"]:
        print("\n硬阻塞：")
        for item in result["hard_blockers"]:
            print(f"  - {item}")
    if result["soft_warnings"]:
        print("\n软警告：")
        for item in result["soft_warnings"][:10]:
            print(f"  - {item}")


def mode_check() -> dict[str, Any]:
    result = _run_checks()
    _print_check_summary(result)
    return result


def _generate_prompt(round_item: dict[str, Any], executor: str) -> str:
    round_id = round_item.get("id", "UNKNOWN")
    name = round_item.get("name", "")
    goal = round_item.get("goal", "")
    outputs = round_item.get("outputs", [])
    acceptance = round_item.get("acceptance_criteria", [])
    hard = round_item.get("hard_blockers", [])
    soft = round_item.get("soft_blockers", [])

    lines = [
        f"# 下一轮任务草案：{round_id}",
        "",
        f"**名称**：{name}",
        f"**执行器**：{executor}",
        "",
        "## 开始前",
        "",
        "```bash",
        "python scripts/auto_advance_runner.py --mode check",
        "python scripts/agent_gate.py",
        "```",
        "",
        "## 目标",
        "",
        goal or "（见 round_tasks.yaml）",
        "",
        "## 预期输出",
        "",
    ]
    for item in outputs:
        lines.append(f"- {item}")
    lines.extend(["", "## 验收标准", ""])
    for item in acceptance:
        lines.append(f"- {item}")
    lines.extend(["", "## Hard Blockers", ""])
    for item in hard:
        lines.append(f"- {item}")
    lines.extend(["", "## Soft Blockers", ""])
    for item in soft:
        lines.append(f"- {item}")
    lines.extend([
        "",
        "## 完成后",
        "",
        "```bash",
        "python scripts/auto_advance_runner.py --mode finalize-round",
        "```",
        "",
        "该命令只做验证，不提交、不推送。是否继续及任何写操作仍取决于当前上级授权。",
    ])
    return "\n".join(lines) + "\n"


def mode_prepare_next() -> dict[str, Any]:
    check_result = _run_checks()
    context = check_result["context"]
    next_round_id = context.get("next_round")

    if not check_result["checks_passed"]:
        print("=== Auto Advance Runner: prepare-next ===")
        print("存在硬阻塞，无法预览下一轮 prompt。")
        return {"decision": "stop", "prompts_written": False}

    round_item = _find_round_task(next_round_id)
    if not round_item:
        print(f"未找到 next_round 任务：{next_round_id}")
        return {"decision": "stop", "prompts_written": False}

    print("=== Auto Advance Runner: prepare-next ===")
    print("只读预览；未写入队列文件。")
    print(f"下一轮：{next_round_id} — {round_item.get('name')}")
    print("\n--- Codex prompt preview ---\n")
    print(_generate_prompt(round_item, "Codex"), end="")
    print("\n--- Cursor prompt preview ---\n")
    print(_generate_prompt(round_item, "Cursor"), end="")
    return {"decision": check_result["decision"], "prompts_written": False, "previewed": True}


def _git_status_porcelain() -> tuple[int, str]:
    return _run_script_command(["git", "status", "--porcelain"])


def _run_script_command(cmd: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return result.returncode, ((result.stdout or "") + (result.stderr or "")).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def _is_sensitive_path(path: str) -> bool:
    name = Path(path).name
    for pattern in SENSITIVE_PATTERNS:
        if pattern.search(name) or pattern.search(path):
            return True
    return False


def _scan_staged_sensitive(hard_blockers: list[str]) -> None:
    code, output = _run_script_command(["git", "diff", "--cached", "--name-only"])
    if code != 0:
        return
    for line in output.splitlines():
        rel = line.strip()
        if not rel:
            continue
        if _is_sensitive_path(rel):
            hard_blockers.append(f"疑似敏感文件准备提交：{rel}")
            continue
        file_path = ROOT / rel
        if file_path.is_file():
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for marker in SENSITIVE_CONTENT_MARKERS:
                if marker in text:
                    hard_blockers.append(f"{rel}: 包含敏感内容标记 {marker}")


def _scan_unstaged_sensitive(hard_blockers: list[str]) -> None:
    code, output = _run_script_command(["git", "status", "--porcelain"])
    if code != 0:
        return
    for line in output.splitlines():
        if len(line) < 4:
            continue
        rel = line[3:].strip()
        if _is_sensitive_path(rel):
            hard_blockers.append(f"工作区存在疑似敏感文件：{rel}")


def mode_finalize_round() -> dict[str, Any]:
    hard_blockers: list[str] = []
    soft_warnings: list[str] = []
    context = _get_round_context()

    check_result = _run_checks()
    hard_blockers.extend(check_result["hard_blockers"])
    soft_warnings.extend(check_result["soft_warnings"])

    if (ROOT / "scripts/check_repo.py").is_file():
        code, out = _run_script("scripts/check_repo.py")
        if code != 0:
            hard_blockers.append("check_repo.py 验证失败")

    in_repo_code, _ = _run_script_command(["git", "rev-parse", "--is-inside-work-tree"])
    if in_repo_code != 0:
        hard_blockers.append("当前目录不在 git 仓库中，无法完成 Git 状态验证")

    merge_code, merge_out = _run_script_command(["git", "diff", "--name-only", "--diff-filter=U"])
    if merge_code == 0 and merge_out.strip():
        hard_blockers.append("存在 merge conflict，必须停止")

    _scan_unstaged_sensitive(hard_blockers)
    _scan_staged_sensitive(hard_blockers)

    decision = _decide(hard_blockers, soft_warnings)

    print("=== Auto Advance Runner: finalize-round ===")
    print(f"决策：{decision}")

    if decision == "stop":
        print("存在硬阻塞。该模式始终不 commit、不 push。")
        if hard_blockers:
            for item in hard_blockers:
                print(f"  - {item}")
    else:
        status_code, status_out = _run_script_command(["git", "status", "--porcelain"])
        if status_code != 0:
            hard_blockers.append(f"git status 失败：{status_out}")
            decision = "stop"
        elif not status_out.strip():
            soft_warnings.append("工作区无变更")
        else:
            print("检测到工作区变更；仅报告，不暂存、不提交、不推送。")
    print("git_commit: not_attempted")
    print("git_push_status: not_attempted_runner_is_read_only")
    return {
        "decision": decision,
        "git_commit": None,
        "git_push_status": "not_attempted_runner_is_read_only",
        "hard_blockers": hard_blockers,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="personal-control-hub auto advance runner")
    parser.add_argument(
        "--mode",
        choices=["check", "prepare-next", "finalize-round"],
        default="check",
        help="运行模式",
    )
    args = parser.parse_args(argv)

    if args.mode == "check":
        result = mode_check()
        return 0 if result["checks_passed"] else 1
    if args.mode == "prepare-next":
        result = mode_prepare_next()
        return 0 if result.get("decision") != "stop" else 1
    result = mode_finalize_round()
    return 0 if result["decision"] != "stop" else 1


if __name__ == "__main__":
    sys.exit(main())
