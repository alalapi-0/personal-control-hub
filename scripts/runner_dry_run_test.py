#!/usr/bin/env python3
"""ROUND-0-8: dry-run tests for auto_advance_runner decision and sensitive-file guards."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from auto_advance_runner import (  # noqa: E402
    SENSITIVE_CONTENT_MARKERS,
    _decide,
    _is_sensitive_path,
)


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
    text = "GITHUB_TOKEN" + "=synthetic-test-value\n"
    detected = any(marker in text for marker in SENSITIVE_CONTENT_MARKERS)
    return {"name": "sensitive_content_marker", "detected": detected, "pass": detected}


def test_runner_modes() -> dict:
    _, before = _run_cmd(["git", "status", "--porcelain=v1", "-z"])
    check_code, check_out = _run_cmd([sys.executable, "scripts/auto_advance_runner.py", "--mode", "check"])
    prep_code, prep_out = _run_cmd([sys.executable, "scripts/auto_advance_runner.py", "--mode", "prepare-next"])
    final_code, final_out = _run_cmd([sys.executable, "scripts/auto_advance_runner.py", "--mode", "finalize-round"])
    _, after = _run_cmd(["git", "status", "--porcelain=v1", "-z"])
    return {
        "name": "runner_modes",
        "check_exit_code": check_code,
        "prepare_exit_code": prep_code,
        "check_has_decision": "决策：" in check_out,
        "finalize_exit_code": final_code,
        "prepare_is_preview": "只读预览；未写入队列文件。" in prep_out,
        "finalize_is_read_only": "not_attempted_runner_is_read_only" in final_out,
        "worktree_unchanged": before == after,
        "pass": (
            check_code == 0
            and prep_code == 0
            and final_code == 0
            and "只读预览；未写入队列文件。" in prep_out
            and "not_attempted_runner_is_read_only" in final_out
            and before == after
        ),
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

    print(f"\n总体结果: {'PASS' if all_pass else 'FAIL'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
