#!/usr/bin/env python3
"""V3-11 closure: remote SHAs, dispositions, dead paths. No new receipt files."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hub.metrics import bounded_json  # noqa: E402

ABSENT_PATHS = (
    "src/hub/connection_manager_cli.py",
    "src/hub/connection_manager.py",
    "data/design_governance/connection_refresh.sqlite3",
)
PROTECTED_LOCAL_IO = (
    "youtube-hq-downloader",
    "computer-study-plan",
    "novel-continuation-agent",
    "pixel-world-asset-forge",
)
REMAINING_CRITERIA = ("V3-11-A1", "V3-11-A2", "V3-11-A3")
SHA = __import__("re").compile(r"^[0-9a-f]{40}$")


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_project_data_v3",
        ROOT / "scripts/check_project_data_v3.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(*args: str, cwd: Path = ROOT) -> str:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    completed = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(cwd), *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("git_command_failed")
    return completed.stdout.strip()


def ls_remote_main(repository: str) -> str:
    if not isinstance(repository, str) or "/" not in repository or ".." in repository:
        raise ValueError("invalid_github_repository")
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    completed = subprocess.run(
        ["git", "ls-remote", f"https://github.com/{repository}.git", "refs/heads/main"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("remote_main_unavailable")
    line = completed.stdout.splitlines()[0] if completed.stdout.splitlines() else ""
    sha = line.split()[0] if line else ""
    if not SHA.match(sha):
        raise ValueError("remote_main_unverified")
    return sha


def _v3_08_remaining(unresolved: list[str]) -> dict[str, list[str]]:
    skipped: list[str] = []
    blocked: list[str] = []
    for item in unresolved:
        if (
            ("续写" in item or "governed-wip" in item)
            and ("禁commit" in item or "条件变化前不重试" in item)
            and "novel-continuation-agent" not in blocked
        ):
            blocked.append("novel-continuation-agent")
    return {"skipped_condition_unchanged": skipped, "blocked": blocked}


def _dead_paths() -> dict[str, Any]:
    present = [path for path in ABSENT_PATHS if (ROOT / path).exists()]
    status = yaml.safe_load((ROOT / "data/state/current_status.yaml").read_text(encoding="utf-8"))
    return {
        "absent": list(ABSENT_PATHS),
        "unexpectedly_present": present,
        "current_status_authority": status.get("metadata", {}).get("authority"),
        "canonical_pointer": status.get("metadata", {}).get("canonical_current_state"),
        "duplicate_current_state": status.get("metadata", {}).get("authority") != "non_canonical"
        or status.get("metadata", {}).get("canonical_current_state") != "STATE.yaml",
    }


def run_check(
    *,
    verify_remote: bool = False,
    remote_lookup: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    checker = _load_checker()
    coverage = checker.run_check(ROOT)
    if not coverage["valid"]:
        raise ValueError("coverage_invalid")
    sources = yaml.safe_load((ROOT / "data/connections/metric_sources.yaml").read_text(encoding="utf-8"))
    governance = yaml.safe_load((ROOT / "STATE.yaml").read_text(encoding="utf-8"))["all_projects_governance"]
    unresolved = [item for item in governance.get("unresolved", []) if isinstance(item, str)]
    remaining = _v3_08_remaining(unresolved)
    lookup = remote_lookup or ls_remote_main
    remotes: dict[str, dict[str, str]] = {}
    if verify_remote:
        seen: dict[str, str] = {}
        for project_id, spec in (sources.get("projects") or {}).items():
            if not isinstance(spec, dict):
                continue
            repository = spec.get("github_repository")
            if not isinstance(repository, str):
                continue
            if repository not in seen:
                try:
                    seen[repository] = lookup(repository)
                except ValueError as exc:
                    raise ValueError(f"{exc.args[0]}:{repository}") from exc
            remotes[project_id] = {"repository": repository, "main": seen[repository]}
        remotes["personal-control-hub-local"] = {
            "repository": "alalapi-0/personal-control-hub",
            "head": _git("rev-parse", "HEAD"),
            "origin_main": _git("rev-parse", "origin/main"),
            "main": remotes.get("personal-control-hub", {}).get("main") or lookup("alalapi-0/personal-control-hub"),
        }
        hub = remotes["personal-control-hub-local"]
        if hub["head"] != hub["origin_main"] or hub["head"] != hub["main"]:
            raise ValueError("hub_remote_mismatch")
    dead = _dead_paths()
    goal_complete = (
        not remaining["skipped_condition_unchanged"]
        and not remaining["blocked"]
        and not dead["unexpectedly_present"]
        and not dead["duplicate_current_state"]
        and coverage["counts"]["rollout_project_units"] == 22
    )
    result = {
        "schema_version": "1.0",
        "kind": "v3_closure_check",
        "agent_required": False,
        "probed_protected_roots": False,
        "stale_inventory_not_authority": True,
        "coverage": {
            "valid": coverage["valid"],
            "counts": coverage["counts"],
            "active_source_gaps": coverage.get("active_source_gaps"),
            "no_git": coverage.get("no_git"),
            "removed_local": coverage.get("removed_local"),
        },
        "dead_paths": dead,
        "protected_local_io": list(PROTECTED_LOCAL_IO),
        "handoff": {
            "canonical_state": "STATE.yaml#all_projects_governance",
            "unit": governance.get("unit"),
            "accepted_count": governance["v3"]["acceptance"]["accepted_count"],
            "latest_accepted_unit": governance["v3"]["acceptance"]["latest"].get("unit"),
            "v3_08_remaining": remaining,
            "unresolved_count": len(unresolved),
        },
        "remaining_criteria": list(REMAINING_CRITERIA),
        "unresolved": unresolved,
        "remotes": remotes,
        "remote_verified": verify_remote,
        "goal_complete": goal_complete,
        "recovery": {
            "wait_for_condition_change": remaining,
            "reason": "V3-08 remaining items stay in the denominator until their conditions change",
        },
    }
    if (
        dead["unexpectedly_present"]
        or dead["duplicate_current_state"]
        or remaining["blocked"] != []
        or remaining["skipped_condition_unchanged"] != []
        or len(unresolved) != 11
        or not result["goal_complete"]
    ):
        raise ValueError("closure_assertions_failed")
    bounded_json(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PROJECT-DATA-PUBLISH-SYNC-V3 closure check")
    parser.add_argument("--verify-remote", action="store_true", help="ls-remote GitHub main; never visit project roots")
    args = parser.parse_args(argv)
    try:
        print(bounded_json(run_check(verify_remote=args.verify_remote)))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(bounded_json({
            "schema_version": "1.0",
            "kind": "v3_closure_check",
            "valid": False,
            "error": "v3_closure_failed",
            "reason": exc.args[0] if isinstance(exc, ValueError) and exc.args else exc.__class__.__name__,
        }))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
