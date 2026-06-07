#!/usr/bin/env python3
"""ROUND-0-8: dry-run tests for auto_advance_runner decision and sensitive-file guards."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_FILE = ROOT / "data/logs/auto_advance_log.jsonl"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from auto_advance_runner import (  # noqa: E402
    SENSITIVE_CONTENT_MARKERS,
    _decide,
    _is_sensitive_path,
)


def _append_log(entry: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _run_cmd(cmd: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode, output.strip()


def test_decision_logic() -> dict:
    cases = [
        {"hard": ["需要真实密钥"], "soft": [], "expected": "stop"},
        {"hard": [], "soft": ["Node 缺失"], "expected": "warn_and_continue"},
        {"hard": [], "soft": [], "expected": "continue"},
    ]
    results = []
    for case in cases:
        actual = _decide(case["hard"], case["soft"])
        ok = actual == case["expected"]
        results.append({**case, "actual": actual, "pass": ok})
    return {"name": "decision_logic", "cases": results, "pass": all(r["pass"] for r in results)}


def test_sensitive_path_patterns() -> dict:
    samples = [
        (".env", True),
        (".env.local", True),
        ("config.pem", True),
        ("deploy.key", True),
        ("id_rsa", True),
        ("secrets.yaml", True),
        ("README.md", False),
        ("data/state/current_status.yaml", False),
    ]
    results = []
    for path, expected in samples:
        actual = _is_sensitive_path(path)
        results.append({"path": path, "expected": expected, "actual": actual, "pass": actual == expected})
    marker_hits = [m for m in SENSITIVE_CONTENT_MARKERS if "=" in m]
    return {
        "name": "sensitive_path_patterns",
        "cases": results,
        "content_markers_defined": len(marker_hits) >= 3,
        "pass": all(r["pass"] for r in results) and len(marker_hits) >= 3,
    }


def test_sensitive_content_marker() -> dict:
    fixture = ROOT / "data/tmp_runner_test"
    fixture.mkdir(parents=True, exist_ok=True)
    fake_env = fixture / "fake_secrets.txt"
    fake_env.write_text("GITHUB_TOKEN" + "=should-not-commit\n", encoding="utf-8")
    text = fake_env.read_text(encoding="utf-8")
    detected = any(marker in text for marker in SENSITIVE_CONTENT_MARKERS)
    fake_env.unlink(missing_ok=True)
    try:
        fixture.rmdir()
    except OSError:
        pass
    return {"name": "sensitive_content_marker", "detected": detected, "pass": detected}


def test_runner_modes() -> dict:
    check_code, check_out = _run_cmd([sys.executable, "scripts/auto_advance_runner.py", "--mode", "check"])
    prep_code, prep_out = _run_cmd([sys.executable, "scripts/auto_advance_runner.py", "--mode", "prepare-next"])
    codex_prompt = ROOT / "data/codex_queue/next_round_prompt.md"
    cursor_prompt = ROOT / "data/codex_queue/next_cursor_prompt.md"
    return {
        "name": "runner_modes",
        "check_exit_code": check_code,
        "prepare_exit_code": prep_code,
        "check_has_decision": "决策：" in check_out,
        "prepare_wrote_codex": codex_prompt.is_file(),
        "prepare_wrote_cursor": cursor_prompt.is_file(),
        "pass": check_code == 0 and prep_code == 0 and codex_prompt.is_file() and cursor_prompt.is_file(),
    }


def main() -> int:
    print("=== Runner Dry Run Test (ROUND-0-8) ===")
    suites = [
        test_decision_logic(),
        test_sensitive_path_patterns(),
        test_sensitive_content_marker(),
        test_runner_modes(),
    ]
    all_pass = True
    for suite in suites:
        status = "PASS" if suite.get("pass") else "FAIL"
        print(f"{suite['name']}: {status}")
        if not suite.get("pass"):
            all_pass = False

    simulated_hard = ["模拟硬阻塞：检测到敏感文件准备提交"]
    simulated_soft = ["模拟软警告：remote 未配置"]
    _append_log({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "dry-run-test",
        "decision": _decide(simulated_hard, []),
        "hard_blockers": simulated_hard,
        "soft_warnings": [],
        "current_round": "ROUND-0-8",
        "next_round": "ROUND-0-9",
        "git_commit": None,
        "git_push_status": "skipped_no_real_push",
        "simulation": "hard_blocker",
    })
    _append_log({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "dry-run-test",
        "decision": _decide([], simulated_soft),
        "hard_blockers": [],
        "soft_warnings": simulated_soft,
        "current_round": "ROUND-0-8",
        "next_round": "ROUND-0-9",
        "git_commit": None,
        "git_push_status": "skipped_no_real_push",
        "simulation": "soft_warning",
    })
    _append_log({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "dry-run-test",
        "decision": "pass" if all_pass else "stop",
        "hard_blockers": [] if all_pass else ["dry-run test suite failed"],
        "soft_warnings": ["本轮不真实 push"],
        "current_round": "ROUND-0-8",
        "next_round": "ROUND-0-9",
        "git_commit": None,
        "git_push_status": "skipped_no_real_push",
        "simulation": "suite_summary",
        "suites": [s["name"] for s in suites],
    })

    print(f"\n总体结果: {'PASS' if all_pass else 'FAIL'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
