#!/usr/bin/env python3
"""Lightweight runtime environment checker for personal-control-hub."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


ROOT = Path(__file__).resolve().parents[1]
STATUS_FILE = ROOT / "data/runtime/toolchain_status.yaml"
LOG_FILE = ROOT / "data/logs/environment_check_log.jsonl"

REQUIRED_DIRS = [
    "data/runtime",
    "data/logs",
    "data/gates",
    "data/roadmap",
    "scripts",
    "governance",
]

REGISTERED_MCP_CANDIDATES = [
    "chrome-devtools",
    "context7",
    "filesystem",
    "github",
    "playwright",
    "stitch",
]


def _run_command(command: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        output = (result.stdout or result.stderr or "").strip()
        return result.returncode == 0, output
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def _parse_python_version(text: str) -> tuple[bool, str | None]:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        return False, None
    major, minor = int(match.group(1)), int(match.group(2))
    version = match.group(0)
    ok = major > 3 or (major == 3 and minor >= 10)
    return ok, version


def _check_python() -> dict[str, Any]:
    ok, output = _run_command([sys.executable, "--version"])
    if not ok:
        return {"status": "missing", "version": None, "notes": output}
    version_ok, version = _parse_python_version(output)
    if not version_ok:
        return {
            "status": "version_too_low",
            "version": version,
            "notes": "需要 Python 3.10+",
        }
    return {"status": "ok", "version": version, "notes": ""}


def _check_git() -> dict[str, Any]:
    ok, output = _run_command(["git", "--version"])
    if not ok:
        return {"status": "missing", "version": None, "notes": output}
    in_repo, _ = _run_command(["git", "rev-parse", "--is-inside-work-tree"])
    notes = "" if in_repo else "当前目录不在 git 仓库中"
    status = "ok" if in_repo else "warning_not_in_repo"
    return {"status": status, "version": output.split()[-1] if output else None, "notes": notes}


def _check_optional_tool(command: list[str]) -> dict[str, Any]:
    ok, output = _run_command(command)
    if not ok:
        return {"status": "optional_missing", "version": None, "notes": ""}
    version = output.split()[-1] if output else output
    return {"status": "ok", "version": version, "notes": ""}


def _check_directories(hard_blockers: list[str], warnings: list[str]) -> None:
    for relative in REQUIRED_DIRS:
        path = ROOT / relative
        if not path.is_dir():
            hard_blockers.append(f"必要目录缺失：{relative}")


def _read_mcp_server_names(relative: str, warnings: list[str]) -> list[str]:
    path = ROOT / relative
    if not path.is_file():
        warnings.append(f"未找到 {relative}")
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"{relative} 无法解析：{exc}")
        return []
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        warnings.append(f"{relative} 缺少 mcpServers 对象")
        return []
    return sorted(str(name) for name in servers)


def _check_mcp_config(warnings: list[str]) -> dict[str, Any]:
    configured = _read_mcp_server_names(".cursor/mcp.json", warnings)
    example = _read_mcp_server_names(".cursor/mcp.example.json", warnings)
    if "filesystem" not in configured:
        warnings.append("当前项目配置未包含最小本地 filesystem server")
    return {
        "status": "runtime_unverified",
        "registered_candidates": REGISTERED_MCP_CANDIDATES,
        "example_candidates": example,
        "project_configured": configured,
        "runtime_available": None,
        "notes": "登记、项目配置、运行时可用和动作授权是四种不同状态；本检查不启用 MCP，也不授予调用权限",
    }


def run_check() -> dict[str, Any]:
    hard_blockers: list[str] = []
    warnings: list[str] = []

    python_info = _check_python()
    git_info = _check_git()
    node_info = _check_optional_tool(["node", "--version"])
    npm_info = _check_optional_tool(["npm", "--version"])

    if python_info["status"] in {"missing", "version_too_low"}:
        hard_blockers.append(f"Python: {python_info['status']}")
    if git_info["status"] == "missing":
        hard_blockers.append("Git 不可用")
    elif git_info["status"] == "warning_not_in_repo":
        warnings.append("当前目录不在 git 仓库中；只能执行非 Git 的只读检查")

    if node_info["status"] == "optional_missing":
        warnings.append("Node 未安装（可选，未来 UI/Playwright 可能需要）")
    if npm_info["status"] == "optional_missing":
        warnings.append("npm 未安装（可选）")

    _check_directories(hard_blockers, warnings)
    mcp_info = _check_mcp_config(warnings)

    tools = {
        "python": python_info,
        "git": git_info,
        "node": node_info,
        "npm": npm_info,
        "cursor": {
            "status": "manual_check_required",
            "version": None,
            "notes": "Cursor MCP 状态需要在 Cursor Workspace MCP Servers 中确认",
        },
        "codex": {
            "status": "manual_check_required",
            "version": None,
            "notes": "Codex 是否可用需要用户确认",
        },
        "mcp_servers": mcp_info,
    }

    if hard_blockers:
        overall = "fail"
    elif warnings:
        overall = "ok_with_warnings"
    else:
        overall = "ok"

    return {
        "overall_status": overall,
        "tools": tools,
        "warnings": warnings,
        "hard_blockers": hard_blockers,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _write_status(result: dict[str, Any]) -> None:
    if yaml is None:
        return
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_checked_at": result["checked_at"],
        "overall_status": result["overall_status"],
        "tools": result["tools"],
    }
    with STATUS_FILE.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)


def _append_log(result: dict[str, Any]) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": result["checked_at"],
        "overall_status": result["overall_status"],
        "hard_blockers": result["hard_blockers"],
        "warnings": result["warnings"],
        "python_version": result["tools"]["python"].get("version"),
        "git_version": result["tools"]["git"].get("version"),
    }
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _print_text(result: dict[str, Any]) -> None:
    tools = result["tools"]
    print("=== 运行环境检查 ===")
    print(f"Python: {tools['python']['status']}, {tools['python'].get('version') or 'N/A'}")
    print(f"Git: {tools['git']['status']}")
    print(f"Node: {tools['node']['status']}")
    print(f"npm: {tools['npm']['status']}")
    print("Cursor MCP: manual_check_required")
    print("Codex: manual_check_required")
    print(f"总体状态: {result['overall_status']}")
    if result["warnings"]:
        print("\n警告：")
        for item in result["warnings"]:
            print(f"  - {item}")
    if result["hard_blockers"]:
        print("\n硬阻塞：")
        for item in result["hard_blockers"]:
            print(f"  - {item}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="personal-control-hub environment check")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument(
        "--record",
        action="store_true",
        help="在当前任务已明确授权记录时，更新状态文件并追加日志；默认不写文件",
    )
    args = parser.parse_args(argv)

    result = run_check()
    if args.record:
        _write_status(result)
        _append_log(result)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_text(result)

    return 1 if result["hard_blockers"] else 0


if __name__ == "__main__":
    sys.exit(main())
