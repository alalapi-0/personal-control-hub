#!/usr/bin/env python3
"""Ordinary no-Agent chain: export, sync, query, validate, and read-only handoff."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hub.connection_records import FIELDS  # noqa: E402
from hub.metric_management import query_review_records  # noqa: E402
from hub.metrics import bounded_json  # noqa: E402
from hub.review_contract import FIRST_REVIEW, apply_review_event, new_review_case  # noqa: E402

ONLINE = "chain-online"
PEER = "chain-peer"
OFFLINE = "chain-offline"
EXPORT_ENTRY = "scripts/export_hub_metric_snapshot.py"
UNVERIFIED_HOST_EVENTS = (
    "cursor_editor_runtime",
    "account_runtime",
    "mcp_host_events",
    "feishu_live_send",
    "youtube_credentialed_network",
    "manga_unauthorized_real_rework",
    "novel_governed_wip_branch",
    "computer_study_plan_protected_wip",
    "pixel_unrelated_historical_ci",
)
ISOLATED_ENV = {
    "PATH": "",
    "PYTHONNOUSERSITE": "1",
    "PYTHONSAFEPATH": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUTF8": "1",
}
EXPORT_SCRIPT = """\
from __future__ import annotations
import argparse
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--hub-root", type=Path, required=True)
parser.add_argument("--project-root", type=Path, required=True)
args = parser.parse_args()
sys.path.insert(0, str(args.hub_root / "src"))

from hub.connection_sources import SourceResolver
from hub.metric_export import export_metric_snapshot
from hub.metrics import metric

observed = "2026-09-12T01:00:00+00:00"
management = SourceResolver(args.hub_root, clock=lambda: observed).refresh({project_id!r})
if not management["success"]:
    raise SystemExit(2)

def collect(project_root, project_id, observed_at):
    source = management["sources"][0]
    return {{
        "metrics": [metric(
            project_id, "fixture.completed", 1, "items",
            "STATE.yaml#current_work.completed", source["sha256"], observed_at,
            counting_basis="One synthetic end-to-end fixture.",
        )],
        "issues": [],
        "disposition": "resolved",
        "source_version": source["sha256"],
    }}

export_metric_snapshot(
    args.project_root, {project_id!r}, {{"business": collect}},
    exporter_id="v3-10-fixture-export", exporter_version="1.0",
    management=management, clock=lambda: observed,
)
"""
INVALID_EXPORT = """\
from pathlib import Path
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--hub-root", type=Path, required=True)
parser.add_argument("--project-root", type=Path, required=True)
args = parser.parse_args()
target = args.project_root / ".hub/status.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text("{}", encoding="utf-8")
"""


def _run(command: list[str], *, cwd: Path, timeout: float = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=ISOLATED_ENV,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _json_line(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _json_payload(text: str) -> dict[str, Any]:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("command did not return a JSON object")
    return payload


def _declaration(project_id: str) -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "project_id": project_id,
        "source_refs": [{"id": "state", "path": "STATE.yaml", "format": "yaml", "role": "current_state"}],
        "metric_export": {"entry": EXPORT_ENTRY, "snapshot": ".hub/status.json"},
        "mapping": {
            field: {"source_ref": "state", "selector": {"path": field.split(".")}, "value_map": None}
            for field in FIELDS
        },
        "unknown_fields": {field: "Source does not publish this fact." for field in FIELDS},
        "validation_entry": ["python3 scripts/check_state.py"],
        "max_read_age_seconds": 3600,
    }


def _state() -> dict[str, Any]:
    return {
        "current_work": {
            "objective": "End-to-end fixture",
            "phase": "verification",
            "round": "R1",
            "status": "complete",
            "completed": True,
            "accepted": False,
            "next_action": "Keep fixture local",
            "owner_role": "Root",
        },
        "progress": {
            "completed": 1,
            "total": 1,
            "counting_basis": "One synthetic fixture item",
            "milestones": [{"id": "fixture", "label": "Fixture", "completed": True, "accepted": False}],
        },
        "blockers": [],
        "verification": {
            "status": "checks_passed",
            "evidence_refs": ["tests/result.json"],
            "accepted_basis": "Isolated fixture only",
        },
        "delivery": {
            "status": "pending_delivery",
            "remote": "https://github.com/example/example",
            "branch": "main",
            "commit": "a" * 40,
            "receipt_ref": "docs/delivery.json",
        },
    }


def _add_project(base: Path, registry: list[dict[str, Any]], project_id: str) -> Path:
    root = base / project_id
    root.mkdir()
    (root / "hub.connection.yaml").write_text(
        yaml.safe_dump(_declaration(project_id), sort_keys=False),
        encoding="utf-8",
    )
    (root / "STATE.yaml").write_text(yaml.safe_dump(_state(), sort_keys=False), encoding="utf-8")
    entry = root / EXPORT_ENTRY
    entry.parent.mkdir()
    entry.write_text(EXPORT_SCRIPT.format(project_id=project_id), encoding="utf-8")
    registry.append({
        "id": project_id,
        "name": project_id,
        "root_path": str(root),
        "enabled": True,
        "current_state_paths": ["STATE.yaml"],
    })
    return root


def _write_registry(hub: Path, registry: list[dict[str, Any]]) -> None:
    (hub / "data/registry/external_projects.yaml").write_text(
        yaml.safe_dump({"projects": registry}, sort_keys=False),
        encoding="utf-8",
    )


def _sync_event(stdout: str) -> dict[str, Any]:
    events = _json_line(stdout)
    if [event.get("phase") for event in events] != ["cache", "sync"]:
        raise ValueError("expected cache then sync")
    return {"phases": ["cache", "sync"], "cache": events[0], "sync": events[1]["data"]}


def _review_rework() -> dict[str, Any]:
    case = new_review_case("fixture-review", "item-a", 1, "1" * 64)
    case = apply_review_event(case, {
        "event_id": "submit-1", "event_type": "submit", "expected_sequence": 0,
        "submission_id": "sub-1", "stage": FIRST_REVIEW,
        "candidate_revision": 1, "candidate_hash": "1" * 64,
    })
    case = apply_review_event(case, {
        "event_id": "reject-1", "event_type": "decide", "expected_sequence": 1,
        "decision_id": "dec-1", "submission_id": "sub-1", "stage": FIRST_REVIEW,
        "candidate_revision": 1, "candidate_hash": "1" * 64,
        "verdict": "reject", "rework_task_id": "rework-1",
    })
    case = apply_review_event(case, {
        "event_id": "claim-1", "event_type": "claim_rework", "expected_sequence": 2,
        "rework_task_id": "rework-1",
    })
    case = apply_review_event(case, {
        "event_id": "done-1", "event_type": "complete_rework", "expected_sequence": 3,
        "rework_task_id": "rework-1",
        "new_candidate_revision": 2, "new_candidate_hash": "2" * 64,
    })
    current = query_review_records([case], axis="candidate_revision", view="current")
    history = query_review_records([case], axis="candidate_revision", view="history")
    return {
        "kind": "synthetic_first_review_rework",
        "replaces_real_business": False,
        "current_revision": current["items"][0]["candidate_revision"],
        "history_revision": history["items"][0]["candidate_revision"],
        "current_rework_count": current["items"][0]["rework_count"],
        "history_rework_not_copied": history["items"][0]["rework_count"] is None,
    }


def _v3_08_remaining(unresolved: list[str]) -> dict[str, list[str]]:
    skipped: list[str] = []
    blocked: list[str] = []
    for item in unresolved:
        if "学习计划" in item and "computer-study-plan" not in skipped:
            skipped.append("computer-study-plan")
        elif ("续写" in item or "governed-wip" in item) and "novel-continuation-agent" not in blocked:
            blocked.append("novel-continuation-agent")
    return {"skipped_condition_unchanged": skipped, "blocked": blocked}


def _roadmap_criteria() -> list[str]:
    roadmap = yaml.safe_load((ROOT / "data/roadmap/project_data_v3.yaml").read_text(encoding="utf-8"))
    return [
        item["id"]
        for stage in roadmap.get("stages", [])
        if isinstance(stage, dict)
        for item in stage.get("acceptance", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]


def _handoff() -> dict[str, Any]:
    governance = yaml.safe_load((ROOT / "STATE.yaml").read_text(encoding="utf-8"))["all_projects_governance"]
    v3 = governance["v3"]
    latest = v3["acceptance"]["latest"]
    unresolved = [item for item in governance.get("unresolved", []) if isinstance(item, str)]
    return {
        "canonical_state": "STATE.yaml#all_projects_governance",
        "unit": governance.get("unit"),
        "lane": governance.get("lane"),
        "accepted_count": v3["acceptance"]["accepted_count"],
        "latest_accepted_unit": latest.get("unit"),
        "rollout": {
            "accepted_count": v3["rollout"].get("accepted_count"),
            "blocked_count": v3["rollout"].get("blocked_count"),
            "remaining_count": v3["rollout"].get("remaining_count"),
        },
        "reuse_accepted_units": True,
        "v3_08_remaining": _v3_08_remaining(unresolved),
        "unresolved_count": len(unresolved),
    }


def _coverage() -> dict[str, Any]:
    completed = _run(
        [sys.executable, "-I", str(ROOT / "scripts/check_project_data_v3.py"), "--json"],
        cwd=ROOT,
    )
    if completed.returncode != 0:
        raise ValueError(completed.stderr or completed.stdout)
    payload = _json_payload(completed.stdout)
    return {
        "valid": payload["valid"],
        "counts": payload["counts"],
        "active_source_gaps": payload.get("active_source_gaps"),
        "runtime_checked": payload["runtime_checked"],
    }


def run_check() -> dict[str, Any]:
    temporary = tempfile.TemporaryDirectory()
    try:
        base = Path(temporary.name)
        hub = base / "hub"
        (hub / "data/registry").mkdir(parents=True)
        (hub / "data/connections").mkdir(parents=True)
        (hub / "src").symlink_to(SRC, target_is_directory=True)
        shutil.copy2(ROOT / "data/connections/management_config.yaml", hub / "data/connections/management_config.yaml")
        registry: list[dict[str, Any]] = []
        online = _add_project(base, registry, ONLINE)
        _add_project(base, registry, PEER)
        _write_registry(hub, registry)

        def hub_cmd(command: str, request_id: str, project_ids: list[str], timeout: float = 8.0):
            argv = [
                sys.executable, "-I", str(ROOT / "hub.py"), command,
                "--root", str(hub), "--request-id", request_id, "--timeout-seconds", "4",
            ]
            for project_id in project_ids:
                argv.extend(["--project-id", project_id])
            completed = _run(argv, cwd=ROOT, timeout=timeout)
            return completed, _sync_event(completed.stdout)

        def metrics(args: list[str]) -> dict[str, Any]:
            completed = _run(
                [sys.executable, "-I", str(ROOT / "scripts/hub_connections.py"), "metrics", "--root", str(hub), *args],
                cwd=ROOT,
            )
            if completed.returncode != 0:
                raise ValueError(completed.stderr or completed.stdout)
            return _json_payload(completed.stdout)

        start, started = hub_cmd("start", "v3-10-start", [ONLINE])
        if start.returncode != 0 or started["sync"]["results"][ONLINE]["status"] != "imported":
            raise ValueError(start.stderr or start.stdout)
        replay, replayed = hub_cmd("sync", "v3-10-start", [ONLINE])
        if replay.returncode != 0 or replayed["sync"]["results"][ONLINE]["status"] != "reused":
            raise ValueError(replay.stderr or replay.stdout)

        state = yaml.safe_load((online / "STATE.yaml").read_text(encoding="utf-8"))
        state["current_work"]["round"] = "R2"
        (online / "STATE.yaml").write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
        changed, source_changed = hub_cmd("sync", "v3-10-source-change", [ONLINE])
        if changed.returncode != 0 or source_changed["sync"]["results"][ONLINE]["status"] != "imported":
            raise ValueError(changed.stderr or changed.stdout)

        registry.append({
            "id": OFFLINE,
            "name": OFFLINE,
            "root_path": str(base / "missing-offline-root"),
            "enabled": True,
            "current_state_paths": ["STATE.yaml"],
        })
        _write_registry(hub, registry)
        offline, isolated = hub_cmd("sync", "v3-10-offline-peer", [ONLINE, PEER, OFFLINE])
        offline_errors = isolated["sync"].get("errors") or {}
        if isolated["sync"]["results"][PEER]["status"] != "imported":
            raise ValueError(offline.stderr or offline.stdout)
        if offline_errors.get(OFFLINE) != "project_export_unavailable":
            raise ValueError(f"offline was not isolated: {isolated['sync']}")

        (online / EXPORT_ENTRY).write_text(INVALID_EXPORT, encoding="utf-8")
        failed, failed_sync = hub_cmd("sync", "v3-10-invalid", [ONLINE])
        if (failed_sync["sync"].get("errors") or {}).get(ONLINE) != "snapshot_import_failed":
            raise ValueError(failed.stderr or failed.stdout)
        (online / EXPORT_ENTRY).write_text(EXPORT_SCRIPT.format(project_id=ONLINE), encoding="utf-8")
        recovered, recovered_sync = hub_cmd("sync", "v3-10-recover", [ONLINE])
        if recovered.returncode != 0 or recovered_sync["sync"]["results"][ONLINE]["status"] != "imported":
            raise ValueError(recovered.stderr or recovered.stdout)

        queried = metrics(["query", "--project-id", ONLINE, "--metric-id", "fixture.completed"])
        validated = metrics(["validate"])
        aggregated = metrics(["aggregate", "--project-id", ONLINE, "--metric-id", "fixture.completed"])
        managed = metrics(["management"])
        reviews = metrics(["reviews", "--axis", "review_stage", "--view", "history"])
        template = _run(
            [
                sys.executable, "-I", str(ROOT / "scripts/hub_connections.py"),
                "--root", str(ROOT), "validate-declaration",
                "data/connections/hub.connection.template.yaml",
            ],
            cwd=ROOT,
        )
        if template.returncode != 0:
            raise ValueError(template.stderr or template.stdout)

        result = {
            "schema_version": "1.0",
            "kind": "v3_end_to_end_check",
            "agent_required": False,
            "mcp_required": False,
            "model_required": False,
            "commands": {
                "start": {"exit": start.returncode, "status": started["sync"]["results"][ONLINE]["status"]},
                "sync_replay": {"exit": replay.returncode, "status": replayed["sync"]["results"][ONLINE]["status"]},
                "sync_source_change": {
                    "exit": changed.returncode,
                    "status": source_changed["sync"]["results"][ONLINE]["status"],
                },
                "offline_peer": {
                    "peer_status": isolated["sync"]["results"][PEER]["status"],
                    "offline_error": offline_errors.get(OFFLINE),
                    "online_not_blocked": ONLINE in isolated["sync"]["results"],
                },
                "fail_keeps_history": {"error": "snapshot_import_failed"},
                "recover": {"exit": recovered.returncode, "status": recovered_sync["sync"]["results"][ONLINE]["status"]},
                "query": {"exit": 0, "total_remaining": queried.get("total_remaining")},
                "validate": {"exit": 0, "metric_versions": validated.get("metric_versions")},
                "aggregate": {"exit": 0, "kind": aggregated.get("kind")},
                "management": {"exit": 0, "kind": managed.get("kind")},
                "reviews_empty_store": {
                    "exit": 0,
                    "history_status": reviews.get("coverage", {}).get("history_status"),
                    "matched": reviews.get("coverage", {}).get("matched"),
                },
                "template": _json_payload(template.stdout),
            },
            "review_rework_fixture": _review_rework(),
            "coverage": _coverage(),
            "handoff": _handoff(),
            "unverified_host_events": list(UNVERIFIED_HOST_EVENTS),
            "real_business_not_replaced_by_fixture": True,
        }
        if (
            result["commands"]["query"]["total_remaining"] < 1
            or result["commands"]["reviews_empty_store"]["history_status"] != "empty"
            or result["review_rework_fixture"]["current_revision"] != 2
            or not result["coverage"]["valid"]
        ):
            raise ValueError("end-to-end assertions failed")
        bounded_json(result)
        return result
    finally:
        temporary.cleanup()


def run_handoff() -> dict[str, Any]:
    criteria = _roadmap_criteria()
    remaining = ["V3-10-A2", "V3-10-A3", "V3-11-A1", "V3-11-A2", "V3-11-A3"]
    if [item for item in remaining if item not in criteria]:
        raise ValueError("remaining criteria missing from roadmap")
    handoff = _handoff()
    coverage = _coverage()
    unresolved = yaml.safe_load((ROOT / "STATE.yaml").read_text(encoding="utf-8"))[
        "all_projects_governance"
    ].get("unresolved", [])
    result = {
        "schema_version": "1.0",
        "kind": "v3_handoff_check",
        "agent_required": False,
        "mcp_required": False,
        "model_required": False,
        "reran_accepted_units": False,
        "mapping": {
            "canonical_state": "STATE.yaml#all_projects_governance",
            "roadmap": "data/roadmap/project_data_v3.yaml",
            "registry": "data/registry/external_projects.yaml",
            "sources": "data/connections/metric_sources.yaml",
        },
        "applicable_criteria": criteria,
        "remaining_criteria": remaining,
        "handoff": handoff,
        "coverage": coverage,
        "unresolved": unresolved,
        "unverified_host_events": list(UNVERIFIED_HOST_EVENTS),
        "real_business_not_replaced_by_fixture": True,
    }
    if (
        not coverage["valid"]
        or handoff["v3_08_remaining"]["blocked"] != ["novel-continuation-agent"]
        or set(handoff["v3_08_remaining"]["skipped_condition_unchanged"])
        != {"computer-study-plan"}
        or handoff["unresolved_count"] != 11
        or len(unresolved) != 11
    ):
        raise ValueError("handoff assertions failed")
    bounded_json(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V3 no-Agent chain and read-only handoff")
    parser.add_argument("--handoff", action="store_true", help="Read STATE/roadmap only; do not rerun accepted units")
    args = parser.parse_args(argv)
    kind = "v3_handoff_check" if args.handoff else "v3_end_to_end_check"
    try:
        print(bounded_json(run_handoff() if args.handoff else run_check()))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(bounded_json({
            "schema_version": "1.0",
            "kind": kind,
            "valid": False,
            "error": "v3_end_to_end_failed" if not args.handoff else "v3_handoff_failed",
            "reason": str(exc.__class__.__name__),
        }))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
