"""Explicit local refresh and preview commands; no project command execution."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from hub.connection_records import RecordError, record_schema, require, validate_declaration, validate_result
from hub.connection_refresh import RefreshLedger, RefreshLedgerError, refresh
from hub.connection_sources import SourceResolver
from hub.connections import parse_source
from hub.paths import PROJECT_ROOT
from hub.project_service import project_snapshot
from hub.services.integration_service import feishu_preview, markdown_preview
from hub.service_contract import ServiceError


PUBLIC_ERROR_MESSAGES = {
    "CURRENT_AUTHORITY_UNAVAILABLE": "Current Hub authority could not be verified; committed refresh history remains available.",
    "EXPECTED_HEAD_CONFLICT": "Refresh ledger head changed; retry with the current head.",
    "INPUT_INVALID": "CLI input or Hub-local data failed validation.",
    "IO_UNAVAILABLE": "Required Hub-local data is unavailable.",
    "LEDGER_CORRUPT": "Hub refresh ledger failed integrity validation.",
    "LEDGER_LOCKED": "Hub refresh ledger is busy; retry after the current writer finishes.",
    "PROJECT_RESULT_CONFLICT": "A different result is already recorded for this request and project.",
    "PREVIEW_UNAVAILABLE": "Refresh committed, but Hub-local previews could not be generated.",
    "REQUEST_IDENTITY_CONFLICT": "The request ID is already bound to different projects or authority.",
    "REQUEST_INCOMPLETE": "The refresh request still has projects without durable results.",
    "RESULT_VALIDATION_FAILED": "A refresh result failed the Hub record contract.",
    "UNSUPPORTED_LEDGER_SCHEMA": "Hub refresh ledger schema is unsupported or incomplete.",
    "UNSAFE_LEDGER_PATH": "Hub refresh ledger is missing or unavailable at the configured Hub-local path.",
    "REGISTRY_CONFLICT": "Registry version changed; check the current metadata before retrying.",
    "REGISTRY_BUSY": "Another registry writer is active; retry after it finishes.",
    "PROTECTED_REGISTRATION": "This excluded or unqualified registration is protected from this entry point.",
    "ROOT_ALREADY_REGISTERED": "The project root already has a stable project identity.",
    "ROOT_IDENTITY_ALREADY_REGISTERED": "This directory alias already belongs to a registered project.",
    "PERMISSION_EXPANSION_FORBIDDEN": "Registration cannot enable scanning or external project writes.",
    "NESTED_ROOT_REVIEW_REQUIRED": "Nested project identities need an explicit owner decision before registration.",
}


def _error_payload(exc: Exception | None = None, *, code: str | None = None,
                   receipt: dict | None = None) -> dict:
    public_code = code or getattr(exc, "code", None)
    if public_code is None:
        public_code = "INPUT_INVALID" if isinstance(exc, RecordError) else "IO_UNAVAILABLE"
    payload = {"valid": False, "error": public_code,
               "message": PUBLIC_ERROR_MESSAGES.get(public_code, "Hub connection command failed safely.")}
    if receipt is not None:
        payload["receipt"] = receipt
    return payload


def _ledger_path(root: Path, configured: str | None) -> Path:
    path = Path(configured) if configured else root / "data/connections/connection_refresh.sqlite3"
    return path if path.is_absolute() else root / path


def _current_authority(root: Path) -> tuple[dict | None, dict]:
    """Current registration applicability only; no external physical observation."""
    try:
        authority = SourceResolver(root, observe_sources=False).authority
        return authority, {"state": "available", "reason": None,
                           "comparison_basis": "registration_only", "physical_sources_rechecked": False}
    except (OSError, RecordError, TypeError, ValueError, KeyError):
        return None, {
            "state": "unavailable",
            "reason": "Current Hub authority could not be verified.",
        }


def _historical_view(root: Path, path: Path, command: str,
                     request_id: str | None = None) -> dict:
    """Return a verified Hub-ledger view without visiting any project root."""
    ledger = RefreshLedger(root, path, result_validator=validate_result, read_only=True)
    authority, authority_status = _current_authority(root)
    if command == "history":
        result = ledger.history(request_id, current_authority=authority, registration_only=True)
        for stored in result["results"]:
            validate_result(stored["result"])
        if authority is None:
            for request in result["requests"]:
                request["authority_drift"] = None
    else:
        result = ledger.rebuild(current_authority=authority, registration_only=True)
        if authority is None:
            result["authority_drift"] = None
            for project in result["projects"].values():
                project["authority_drift"] = None
                reason = "current Hub authority could not be verified"
                if project["stale_reason"]:
                    reason = f"{project['stale_reason']}; {reason}"
                if project["freshness"] == "fresh":
                    project["freshness"] = "stale"
                project["stale_reason"] = reason
    result["view_role"] = "historical_ledger"
    result["current_authority"] = authority_status
    return result


def _service_receipt(payload, data_exit, ledger, plan):
    """Operational success is ledger-derived, never a payload status/exit allowlist."""
    from .connection_refresh import IncompleteRequestError
    from .connection_sync import project_recovery
    from datetime import datetime
    history = ledger.history()
    require(history['head'] == payload['head'], 'service verification head changed')
    for row in history['results'] + history['observation_results']:
        validate_result(row['result'])
    requests = {row['request_id']: row for row in history['requests']}
    ids = [payload['request_id'], *payload.get('recovery_request_ids', [])]
    verified = []
    for ident in ids:
        request = requests.get(ident)
        if request is None:
            if ident != payload['request_id']: raise IncompleteRequestError('missing recovery request')
            continue
        results = [row for row in history['results'] if row['request_id'] == ident]
        if (request['status'] != 'FINISHED' or request['remaining_project_ids'] or
                set(request['project_ids']) != {row['project_id'] for row in results}):
            raise IncompleteRequestError('service request lacks complete durable results')
        verified.append(ident)
    # A legitimate wait is derived from each project's durable failure chain;
    # no unfinished request or arbitrary DEFERRED payload can pass as no-work.
    eligible = set(plan['eligible_project_ids'])
    for request in history['requests']:
        if request['status'] != 'FINISHED' and eligible.intersection(request['project_ids']):
            raise IncompleteRequestError('unfinished scoped request requires recovery')
    if not verified:
        instant = datetime.fromisoformat(payload['scheduled_at'])
        require(payload['status'] == 'DEFERRED' and payload['appended_count'] == 0,
                'unverified service receipt')
        deferred = payload.get('deferred_projects', {})
        require(set(deferred) == eligible, 'service no-work scope mismatch')
        for ident in eligible:
            state = project_recovery(history, ident, instant)
            require(state['state'] != 'ready' and deferred[ident] == state,
                    'service no-work lacks durable recovery evidence')
    return {'interface': 'pch-refresh-service-v1', 'operational_success': True,
            'classification': 'completed_degraded' if verified and data_exit else
                              'completed' if verified else 'no_work',
            'data_exit_code': data_exit, 'verified_request_ids': verified,
            'scope_total': plan['scope_total'], 'eligible_total': len(eligible),
            'verified_head': history['head']}


def write_previews(root: Path, directory: str, snapshot: dict) -> dict:
    destination = Path(directory)
    if not destination.is_absolute():
        destination = root / destination
    try:
        relative = destination.relative_to(root)
    except ValueError as exc:
        raise RecordError("preview directory must be inside this Hub") from exc
    require(".." not in relative.parts and
            (relative.parts[:2] == ("data", "connections") or relative.parts[:3] == ("docs", "reports", "all-projects-governance")),
            "preview directory must be under approved Hub data/reports")
    current = root
    for part in relative.parts:
        current /= part
        require(not current.is_symlink(), "preview parent cannot be a symlink")
        current.mkdir(exist_ok=True)
    payloads = {"projects.json": json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
                "projects.md": markdown_preview(snapshot),
                "feishu.json": json.dumps(feishu_preview(snapshot), ensure_ascii=False, indent=2) + "\n"}
    for name in payloads:
        target = destination / name
        require(not target.is_symlink() and (not target.exists() or target.is_file() and target.stat().st_nlink == 1),
                "preview target must be an ordinary singly-linked file")
    for name, text in payloads.items():
        (destination / name).write_text(text, encoding="utf-8")
    return {name: str((destination / name).relative_to(root)) for name in payloads}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hub connection schema, explicit refresh and disabled local previews")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--db", "--ledger", dest="db", default=None,
                        help="Hub-local ledger path; default data/connections/connection_refresh.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema")
    declaration = commands.add_parser("validate-declaration")
    declaration.add_argument("path", help="Relative Hub template path (does not visit project roots)")
    commands.add_parser("validate")
    commands.add_parser("discover")
    commands.add_parser("status", help="Read the same scope, quality, recovery and fixed-timer status as the local page")
    registration = commands.add_parser("register", help="Check one explicit Hub-local registry entry; --apply writes only this Hub registry")
    registration.add_argument("--entry", required=True, help="Entry YAML under Hub data/registry")
    registration.add_argument("--expected-registry-sha256", required=True)
    registration.add_argument("--apply", action="store_true", help="Apply the checked single-entry add or same-ID migration")
    sync = commands.add_parser("sync")
    identity = sync.add_mutually_exclusive_group(required=True)
    identity.add_argument("--request-id", help="Reuse to resume; choose a fresh ID to read again")
    identity.add_argument("--daily", action="store_true", help="One scope request per Asia/Tokyo day")
    identity.add_argument("--scheduled", action="store_true", help="One finite snapshot for the latest 12/24-hour due slot")
    identity.add_argument("--incremental", action="store_true", help="One bounded incremental check")
    sync.add_argument("--project-id", action="append", dest="projects")
    for command in ('service-scheduled-v1', 'service-precheck-v1'):
        service = commands.add_parser(command, help='Pinned managed-service interface version 1')
        service.add_argument('--program-id', required=True)
        service.add_argument('--runtime-manifest', type=Path)
    history = commands.add_parser("history")
    history.add_argument("--request-id")
    commands.add_parser("rebuild")
    current = commands.add_parser("current")
    current.add_argument("--format", choices=("json", "markdown", "feishu"), default="json")
    run = commands.add_parser("refresh")
    run.add_argument("--request-id", required=True, help="Reuse after interruption; use a new ID to re-read sources")
    run.add_argument("--project-id", "--project", action="append", dest="projects")
    run.add_argument("--expected-sequence", type=int)
    run.add_argument("--expected-hash")
    run.add_argument("--preview-dir", default="data/connections/preview")
    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            print(json.dumps(record_schema(), ensure_ascii=False, indent=2))
            return 0
        root = args.root.absolute()
        if args.command in {'service-scheduled-v1', 'service-precheck-v1'}:
            from hub.systemd_refresh import preflight_runtime
            program = preflight_runtime(args.program_id, args.runtime_manifest)
            if args.command == 'service-precheck-v1':
                print(json.dumps({'valid': True, 'preflight': program}, ensure_ascii=False))
                return 0
            from hub.connection_sync import scope_plan, scheduled_request
            resolver = SourceResolver(root)
            plan = scope_plan(root, resolver)
            ledger = RefreshLedger(root, _ledger_path(root, args.db), result_validator=validate_result)
            payload, data_exit = scheduled_request(root, resolver, ledger, plan=plan)
            receipt = _service_receipt(payload, data_exit, ledger, plan)
            from hub.project_service import ProjectService
            quality = ProjectService(root, ledger_path=ledger.path).sync_status()
            require(quality['head'] == receipt['verified_head'] and
                    quality['scope_total'] == plan['scope_total'] and
                    set(quality['eligible_project_ids']) == set(plan['eligible_project_ids']),
                    'service quality projection changed during verification')
            payload['service_execution'] = {**receipt, 'program': program,
                'quality_counts': quality['counts'], 'current_failure_codes': quality['failure_codes']}
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if args.command == "register":
            from hub.connection_sources import _safe_relative_read
            from hub.registration import register_project
            entry_path = Path(args.entry)
            require(not entry_path.is_absolute() and ".." not in entry_path.parts
                    and entry_path.parts[:2] == ("data", "registry"),
                    "registration entry must be under Hub data/registry")
            data, _ = _safe_relative_read(root, str(entry_path), authority_check=lambda: None)
            result = register_project(root, parse_source(data, "yaml"),
                                      args.expected_registry_sha256, apply=args.apply)
            # The exact preimage supports Root's local recovery; never emit the
            # registry body or private paths to stdout or a service journal.
            public = {key: result[key] for key in ("applied", "change_kind", "id_mapping",
                                                   "old_registry_hash", "new_registry_hash", "warnings")}
            print(json.dumps(public, ensure_ascii=False, indent=2))
            return 0
        if args.command == "validate-declaration":
            path = Path(args.path)
            require(not path.is_absolute() and ".." not in path.parts and path.parts[:2] == ("data", "connections"),
                    "validate-declaration accepts a Hub data/connections template path")
            from hub.connection_sources import _safe_relative_read
            data, _ = _safe_relative_read(root, str(path), authority_check=lambda: None)
            validate_declaration(parse_source(data, "yaml"))
            print(json.dumps({"valid": True, "schema_version": "2.0"}))
            return 0
        path = _ledger_path(root, args.db)
        if args.command == "status":
            from hub.project_service import ProjectService
            from hub.refresh_schedule import refresh_schedule_status
            result = ProjectService(root, ledger_path=path).sync_status()
            from hub.connection_sync import scope_plan
            policy = scope_plan(root, SourceResolver(root, observe_sources=False))["schedule"]
            result["schedule"] = refresh_schedule_status(interval_hours=policy["interval_hours"])
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command in {"history", "rebuild"}:
            result = _historical_view(root, path, args.command,
                                      getattr(args, "request_id", None))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        resolver = SourceResolver(root, observe_sources=args.command in {"sync", "refresh"})
        if args.command in {"discover", "sync"}:
            from hub.connection_sync import daily_request_id, scope_plan, sync_request, scheduled_request, sync_mutex
            plan = scope_plan(root, resolver)
            if args.command == "discover":
                print(json.dumps(plan, ensure_ascii=False, indent=2))
                return 0
            require(not args.daily or args.projects is None, "daily sync cannot select a subset")
            require(not (args.scheduled or args.incremental) or args.projects is None,
                    "scheduled sync cannot select a subset")
            request_id = daily_request_id(plan["scope_id"]) if args.daily else args.request_id
            # Validate selection before creating or opening a writable ledger.
            if args.projects is not None:
                require(len(args.projects) == len(set(args.projects))
                        and set(args.projects) <= set(plan["eligible_project_ids"]),
                        "sync projects must be eligible scope members")
            ledger = RefreshLedger(root, path, result_validator=validate_result)
            if args.scheduled or args.incremental:
                payload,code=scheduled_request(root,resolver,ledger,plan=plan,incremental_only=args.incremental)
                print(json.dumps(payload,ensure_ascii=False,indent=2))
                return code
            with sync_mutex(ledger):
                payload, code = sync_request(root, resolver, ledger, request_id,
                                             project_ids=args.projects, plan=plan)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return code
        if args.command == "refresh":
            ids = args.projects or list(resolver.projects)
            require(set(ids) <= set(resolver.projects), "unknown project ID")
            require((args.expected_sequence is None) == (args.expected_hash is None),
                    "both expected head fields are required")
            expected = (None if args.expected_sequence is None else
                        {"sequence": args.expected_sequence, "hash": args.expected_hash})
            ledger = RefreshLedger(root, path, result_validator=validate_result)
            from hub.connection_sync import sync_mutex
            with sync_mutex(ledger):
                outcome = refresh(ledger, resolver, args.request_id, ids,
                                  expected_head=expected)
            receipt = {"request": outcome["request"],
                       "appended_project_ids": outcome["appended_project_ids"],
                       "resolver_errors": outcome["resolver_errors"]}
            try:
                current_resolver = SourceResolver(root)
            except (OSError, RecordError, TypeError, ValueError, KeyError):
                authority_status = {"state": "unavailable",
                                    "reason": "Current Hub authority could not be verified."}
                payload = _error_payload(code="CURRENT_AUTHORITY_UNAVAILABLE", receipt=receipt)
                payload.update(current_authority=authority_status,
                               previews={"available": False, "error": "CURRENT_AUTHORITY_UNAVAILABLE"})
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 2
            try:
                outcome["projection"] = ledger.rebuild(current_authority=current_resolver.authority)
                snapshot = project_snapshot(current_resolver.registry, current_resolver.authority, ledger)
                files = write_previews(root, args.preview_dir, snapshot)
            except (RecordError, RefreshLedgerError, OSError) as exc:
                payload = _error_payload(exc, code="PREVIEW_UNAVAILABLE", receipt=receipt)
                payload.update(current_authority={"state": "available", "reason": None},
                               previews={"available": False, "error": "PREVIEW_UNAVAILABLE"})
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 2
            request_results = ledger.history(args.request_id)["results"]
            failed_results = [row for row in request_results
                              if not row["success"] and row["result"].get("disposition") != "removed_local"]
            payload = {**receipt, "projection": outcome["projection"],
                       "coverage": snapshot["coverage"], "previews": files}
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0 if not outcome["resolver_errors"] and not failed_results else 2
        ledger = (RefreshLedger(root, path, result_validator=validate_result, read_only=True)
                  if path.exists() else None)
        if args.command == "validate":
            outcome = ledger.rebuild(current_authority=resolver.authority, registration_only=True) if ledger else None
            print(json.dumps({"valid": True, "schema_version": "2.0", "ledger_initialized": ledger is not None,
                              "projection": outcome}, ensure_ascii=False, indent=2))
            return 0
        snapshot = project_snapshot(resolver.registry, resolver.authority, ledger)
        print(markdown_preview(snapshot) if args.format == "markdown" else
              json.dumps(feishu_preview(snapshot) if args.format == "feishu" else snapshot, ensure_ascii=False, indent=2))
        return 0
    except (RecordError, RefreshLedgerError, ServiceError, OSError) as exc:
        print(json.dumps(_error_payload(exc), ensure_ascii=False))
        return 2
