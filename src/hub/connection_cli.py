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
    """Read only current Hub authority; historical ledger reads do not depend on it."""
    try:
        authority = SourceResolver(root).authority
        return authority, {"state": "available", "reason": None}
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
        result = ledger.history(request_id, current_authority=authority)
        for stored in result["results"]:
            validate_result(stored["result"])
        if authority is None:
            for request in result["requests"]:
                request["authority_drift"] = None
    else:
        result = ledger.rebuild(current_authority=authority)
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
        if args.command in {"history", "rebuild"}:
            result = _historical_view(root, path, args.command,
                                      getattr(args, "request_id", None))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        resolver = SourceResolver(root)
        if args.command == "refresh":
            ids = args.projects or list(resolver.projects)
            require(set(ids) <= set(resolver.projects), "unknown project ID")
            require((args.expected_sequence is None) == (args.expected_hash is None),
                    "both expected head fields are required")
            expected = (None if args.expected_sequence is None else
                        {"sequence": args.expected_sequence, "hash": args.expected_hash})
            ledger = RefreshLedger(root, path, result_validator=validate_result)
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
            outcome = ledger.rebuild(current_authority=resolver.authority) if ledger else None
            print(json.dumps({"valid": True, "schema_version": "2.0", "ledger_initialized": ledger is not None,
                              "projection": outcome}, ensure_ascii=False, indent=2))
            return 0
        snapshot = project_snapshot(resolver.registry, resolver.authority, ledger)
        print(markdown_preview(snapshot) if args.format == "markdown" else
              json.dumps(feishu_preview(snapshot) if args.format == "feishu" else snapshot, ensure_ascii=False, indent=2))
        return 0
    except (RecordError, RefreshLedgerError, OSError) as exc:
        print(json.dumps(_error_payload(exc), ensure_ascii=False))
        return 2
