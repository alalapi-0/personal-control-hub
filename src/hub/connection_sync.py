"""Finite, registry-qualified source refreshes using the existing durable ledger."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from hub.connection_records import identifier, require, string
from hub.connection_refresh import RequestConflictError, refresh
from hub.connection_sources import _safe_relative_read
from hub.connections import parse_source
from hub.service_contract import ServiceError

SCOPE_PATH = "data/connections/sync_scope.yaml"


def scope_plan(root: Path, resolver) -> dict:
    """Qualify a frozen identity list without probing any registered project root."""
    data, _ = _safe_relative_read(Path(root), SCOPE_PATH, authority_check=resolver._check_registry)
    scope = parse_source(data, "yaml")
    require(type(scope) is dict and set(scope) == {
        "schema_version", "scope_id", "baseline_total", "project_ids", "excluded_project_ids"},
        "invalid sync scope fields")
    require(scope["schema_version"] == "1.0", "unsupported sync scope")
    identifier(scope["scope_id"], "scope")
    ids, exclusions = scope["project_ids"], scope["excluded_project_ids"]
    require(type(ids) is list and bool(ids), "sync scope identities required")
    for project_id in ids:
        identifier(project_id, "project")
    require(len(ids) == len(set(ids)), "duplicate sync scope identity")
    require(type(scope["baseline_total"]) is int and scope["baseline_total"] == len(ids),
            "sync denominator must match frozen identities")
    require(type(exclusions) is dict, "invalid sync exclusions")
    for project_id, reason in exclusions.items():
        identifier(project_id, "excluded project")
        string(reason, "exclusion reason")
    eligible, excluded = [], []
    for project_id in ids:
        row = resolver.projects.get(project_id)
        reason = exclusions.get(project_id)
        if row is None:
            reason = "missing_registration"
        elif (row.get("local_presence", {}).get("status") == "removed_local"
              or row.get("current_state_status") == "removed_local"):
            reason = "removed_local"
        elif row.get("enabled") is not True:
            reason = "disabled"
        elif row.get("summary_enabled", row.get("enabled")) is not True:
            reason = "summary_disabled"
        elif (row.get("connection_read_allowed", row.get("enabled")) is not True
              or row.get("access_profile") == "no_current_goal_access"):
            reason = "connection_read_disabled"
        if reason is None:
            eligible.append(project_id)
        else:
            excluded.append({"project_id": project_id, "reason": reason})
    for project_id in resolver.projects:
        if project_id not in ids:
            excluded.append({"project_id": project_id,
                             "reason": exclusions.get(project_id, "outside_frozen_scope")})
    return {"scope_id": scope["scope_id"], "scope_total": scope["baseline_total"],
            "project_ids": list(ids), "eligible_project_ids": eligible, "excluded": excluded,
            "registered_count": len(resolver.projects),
            "outside_scope_count": len(set(resolver.projects) - set(ids))}


def daily_request_id(scope_id: str, *, instant: datetime | None = None) -> str:
    instant = instant or datetime.now(ZoneInfo("Asia/Tokyo"))
    require(instant.tzinfo is not None, "daily instant needs timezone")
    return f"{scope_id}.daily.{instant.astimezone(ZoneInfo('Asia/Tokyo')).date().isoformat()}"


def sync_request(root: Path, resolver, ledger, request_id: str, *,
                 project_ids: list[str] | None = None, plan: dict | None = None) -> tuple[dict, int]:
    """Same request resumes committed results; a new request reads sources again."""
    from hub.project_service import ProjectService

    plan = plan if plan is not None else scope_plan(root, resolver)
    identifier(request_id, "request")
    selected = plan["eligible_project_ids"] if project_ids is None else project_ids
    require(type(selected) is list and len(selected) == len(set(selected))
            and set(selected) <= set(plan["eligible_project_ids"]), "sync projects must be eligible scope members")
    if project_ids is not None:
        require(bool(selected), "sync subset must not be empty")
    failures = Counter()
    if selected:
        outcome = refresh(ledger, resolver, request_id, selected)
        for code in outcome["resolver_errors"].values():
            failures[code] += 1
        for stored in ledger.history(request_id)["results"]:
            if not stored["success"]:
                codes = {error["code"] for error in stored["result"]["errors"]}
                for code in codes or {stored["result"]["disposition"]}:
                    failures[code] += 1
        status = outcome["request"]["status"]
        appended = len(outcome["appended_project_ids"])
        head = outcome["projection"]["head"]
    else:
        if any(row["request_id"] == request_id for row in ledger.history()["requests"]):
            raise RequestConflictError("request ID is already bound to a different eligible scope")
        status, appended, head = "NO_ELIGIBLE_PROJECTS", 0, ledger.head
        failures["NO_ELIGIBLE_PROJECTS"] += 1
    payload = {"request_id": request_id, "status": status, "appended_count": appended,
               "failure_code_counts": dict(sorted(failures.items())), "head": head}
    try:
        summary = ProjectService(root, ledger_path=ledger.path).sync_status()
    except ServiceError as exc:
        # Preserve the committed request receipt even if a concurrent authority
        # change makes the current summary unavailable. Never print source data
        # or a traceback to the user journal after a successful commit.
        payload.update(summary=None, summary_error=exc.code)
        return payload, 2
    if summary["head"] != head:
        payload.update(summary=None, summary_error="SYNC_HEAD_CHANGED")
        return payload, 2
    payload["summary"] = summary["counts"]
    return payload, 2 if failures else 0
