"""Finite, registry-qualified source refreshes using the existing durable ledger."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import fcntl
import os
from pathlib import Path
import stat
import time
from zoneinfo import ZoneInfo

from hub.connection_records import identifier, require, string
from hub.connection_refresh import RequestConflictError, refresh
from hub.connection_sources import _safe_relative_read
from hub.connections import parse_source
from hub.service_contract import ServiceError

from hub.refresh_schedule import schedule_policy, latest_due_slot


@contextmanager
def sync_mutex(ledger):
    """One nonblocking batch lock; SQLite CAS still fences other writers."""
    path=ledger.path.with_name('.sync.lock')
    descriptor=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==os.getuid(),
                'unsafe sync mutex')
        try:fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise ServiceError('SYNC_BATCH_BUSY',status=409,retryable=True) from None
        yield
    finally:os.close(descriptor)


def _version(result):
    # A metadata-only touch is not a changed source or business fact.
    value=deepcopy(result)
    value.pop('observed_at',None);value.pop('update_key',None)
    if 'binding_version' in value.get('authority', {}):
        authority = value['authority']
        value['authority'] = {k: authority[k] for k in ('schema_hash', 'adapter_version', 'binding_version')}
        value['authority']['binding_hash'] = authority['project_bindings'].get(value['project_id'])
    for source in value.get('sources',[]):source.pop('modified_at',None)
    for field in value.get('extensions',{}).get('fact',{}).values():
        if field.get('proof'):field['proof'].pop('observed_at',None)
    return value


def incremental_request(root, resolver, ledger, request_id, *, plan=None):
    """Bounded safe reads; append only changed/expired evidence, never on GET."""
    plan=plan if plan is not None else scope_plan(root,resolver)
    identifier(request_id,'request')
    if not plan['eligible_project_ids']:
        return {'request_id':request_id,'status':'NO_ELIGIBLE_PROJECTS','appended_count':0,
                'checked_count':0,'unchanged_count':0,
                'failure_code_counts':{'NO_ELIGIBLE_PROJECTS':1},'head':ledger.head},2
    history=ledger.history(current_authority=resolver.authority)
    existing=next((row for row in history['requests'] if row['request_id']==request_id),None)
    if existing:
        return sync_request(root,resolver,ledger,request_id,project_ids=existing['project_ids'],plan=plan)
    previous={row['project_id']:row['result'] for row in history['results']}
    scanned={};unchanged=[];errors=Counter()
    for ident in plan['eligible_project_ids']:
        result=resolver.refresh(ident)
        old=previous.get(ident)
        expired=False
        if old is not None and result['success']:
            observed=datetime.fromisoformat(result['observed_at'].replace('Z','+00:00'))
            prior=datetime.fromisoformat(old['observed_at'].replace('Z','+00:00'))
            age=(observed-prior).total_seconds()
            expired=age<0 or age>=old['freshness']['max_read_age_seconds']
        if old is None or _version(old)!=_version(result) or expired:
            scanned[ident]=result
        else:unchanged.append(ident)
        if not result['success']:
            errors.update(error['code'] for error in result['errors'])
    resolver._check_registry(plan['eligible_project_ids'])
    if not scanned:
        return {'request_id':request_id,'status':'UNCHANGED','appended_count':0,
                'checked_count':len(unchanged),'unchanged_count':len(unchanged),
                'failure_code_counts':dict(errors),'head':ledger.head},2 if errors else 0
    class ScannedResolver:
        authority=resolver.authority
        validate_result=staticmethod(resolver.validate_result)
        def refresh(self,ident):
            resolver._check_registry(ident)
            return deepcopy(scanned[ident])
    outcome=refresh(ledger,ScannedResolver(),request_id,list(scanned),expected_head=history['head'])
    return {'request_id':request_id,'status':outcome['request']['status'],
            'appended_count':len(outcome['appended_project_ids']),
            'checked_count':len(scanned)+len(unchanged),'unchanged_count':len(unchanged),
            'failure_code_counts':dict(errors),'head':outcome['projection']['head']},2 if errors else 0


def scheduled_request(root,resolver,ledger,*,instant=None,plan=None,incremental_only=False):
    """One finite latest-due snapshot, or an explicit manual incremental read."""
    instant=instant or datetime.now(ZoneInfo('Asia/Tokyo'))
    require(instant.tzinfo is not None,'scheduled instant needs timezone')
    plan=plan if plan is not None else scope_plan(root,resolver)
    policy=plan['schedule']
    started=time.monotonic()
    with sync_mutex(ledger):
        if incremental_only:
            # Manual runs are distinct recoveries, never a generated timer policy.
            import uuid
            ident=f"{plan['scope_id']}.manual.{uuid.uuid4().hex}"
            payload,code=incremental_request(root,resolver,ledger,ident,plan=plan)
            slot=None
            mode='manual_incremental'
        else:
            slot=latest_due_slot(instant,policy['interval_hours'])
            ident=f"{plan['scope_id']}.snapshot.{policy['interval_hours']}h.{slot.strftime('%Y-%m-%dT%H-%M')}"
            payload,code=sync_request(root,resolver,ledger,ident,plan=plan)
            mode='snapshot'
        elapsed=time.monotonic()-started
        payload.update(mode=mode,scheduled_at=instant.isoformat(),elapsed_seconds=elapsed,
                       due_slot=slot.isoformat() if slot else None,
                       interval_hours=policy['interval_hours'],
                       overloaded=elapsed>policy['expected_interval_seconds'])
        return payload,code

SCOPE_PATH = "data/connections/sync_scope.yaml"


def scope_plan(root: Path, resolver) -> dict:
    """Qualify a frozen identity list without probing any registered project root."""
    data, meta = _safe_relative_read(Path(root), SCOPE_PATH, authority_check=lambda: resolver._check_registry(registration_only=True))
    scope = parse_source(data, "yaml")
    require(type(scope) is dict and set(scope) in ({
        "schema_version", "scope_id", "baseline_total", "project_ids", "excluded_project_ids"}, {
        "schema_version", "scope_id", "baseline_total", "project_ids", "excluded_project_ids", "schedule"}),
        "invalid sync scope fields")
    require(scope["schema_version"] == "1.0", "unsupported sync scope")
    try:
        policy = schedule_policy(scope["schedule"] if "schedule" in scope else {"interval_hours":12})
        require("schedule" not in scope or type(scope["schedule"]) is dict, "invalid sync schedule")
    except ValueError:
        require(False, "invalid sync schedule")
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
        elif row.get('project_type') in {'cloud','cloud_project'} or row.get('root_kind')=='cloud':
            reason = 'cloud'
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
            "schedule": policy,
            "scope_sha256": meta["sha256"],
            "project_ids": list(ids), "eligible_project_ids": eligible, "excluded": excluded,
            "registered_count": len(resolver.projects),
            "outside_scope_count": len(set(resolver.projects) - set(ids))}


def recovery_summary(history: dict, eligible: set[str]) -> dict:
    """Derive finite request recovery from the existing immutable ledger only.

    A successful source read says nothing about business or human acceptance.
    Historical request selections are not historical permission snapshots.
    """
    by_request = {}
    for result in history["results"]:
        by_request.setdefault(result["request_id"], []).append(result)
    summaries, versions = [], []
    for request in history["requests"]:
        ids = set(request["project_ids"])
        versions.append({"request_id": request["request_id"],
                         "project_ids": request["project_ids"],
                         "registry_sha256": request["authority"]["registry_hash"],
                         "started_at": request["started_at"],
                         "authority_drift": request["authority_drift"]})
        if not ids or not ids <= eligible:
            continue
        results = by_request.get(request["request_id"], [])
        codes = Counter(error["code"] for row in results if not row["success"]
                        for error in row["result"]["errors"])
        failed = sum(not row["success"] for row in results)
        summaries.append({"request_id": request["request_id"],
                          "status": request["status"],
                          "started_at": request["started_at"],
                          "finished_at": request["finished_at"],
                          "project_ids": request["project_ids"],
                          "completed": len(request["completed_project_ids"]),
                          "remaining_project_ids": request["remaining_project_ids"],
                          "failed": failed, "failure_codes": dict(sorted(codes.items())),
                          "authority_drift": request["authority_drift"],
                          "can_resume": request["status"] == "OPEN" and not request["authority_drift"]})
    success = [row for row in summaries if row["status"] == "FINISHED" and not row["failed"]]
    failed = [row for row in summaries if row["failed"]]
    return {"last_success": success[-1] if success else None,
            "last_failure": failed[-1] if failed else None,
            "open_requests": [row for row in summaries if row["status"] == "OPEN"],
            "request_versions": versions,
            "history_basis": "Recorded request selections and authority hashes; not permission snapshots or timer triggers."}


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
