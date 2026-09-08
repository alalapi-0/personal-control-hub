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
    parser.add_argument("--db", default=None, help="Hub-local ledger path; default data/connections/connection_refresh.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema")
    declaration = commands.add_parser("validate-declaration")
    declaration.add_argument("path", help="Relative Hub template path (does not visit project roots)")
    commands.add_parser("validate")
    current = commands.add_parser("current")
    current.add_argument("--format", choices=("json", "markdown", "feishu"), default="json")
    run = commands.add_parser("refresh")
    run.add_argument("--request-id", required=True, help="Reuse after interruption; use a new ID to re-read sources")
    run.add_argument("--project-id", action="append", dest="projects")
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
        resolver = SourceResolver(root)
        path = Path(args.db) if args.db else root / "data/connections/connection_refresh.sqlite3"
        if not path.is_absolute():
            path = root / path
        if args.command == "refresh":
            ids = args.projects or list(resolver.projects)
            require(set(ids) <= set(resolver.projects), "unknown project ID")
            ledger = RefreshLedger(root, path, result_validator=validate_result)
            outcome = refresh(ledger, resolver, args.request_id, ids)
            snapshot = project_snapshot(resolver.registry, resolver.authority, ledger)
            files = write_previews(root, args.preview_dir, snapshot)
            print(json.dumps({"request": outcome["request"], "resolver_errors": outcome["resolver_errors"],
                              "coverage": snapshot["coverage"], "previews": files}, ensure_ascii=False, indent=2))
            return 0 if not outcome["resolver_errors"] else 2
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
        print(json.dumps({"valid": False, "error": getattr(exc, "code", type(exc).__name__)}, ensure_ascii=False))
        return 2
