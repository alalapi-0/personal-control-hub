"""CLI entrypoint for personal-control-hub."""

from __future__ import annotations

import argparse
from pathlib import Path
import secrets
import sqlite3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="personal-control-hub CLI")
    subparsers = parser.add_subparsers(dest="command")

    mcp_parser = subparsers.add_parser("mcp", help="Read-only MCP registry and policy")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_command", required=True)
    mcp_sub.add_parser("list", help="List registered MCP capabilities")
    mcp_sub.add_parser("policy", help="List MCP approval levels L0-L3")

    registry_parser = subparsers.add_parser("registry", help="Read-only external project registry")
    registry_sub = registry_parser.add_subparsers(dest="registry_command", required=True)
    registry_sub.add_parser("list", help="List registered external projects")

    for command, help_text in (
        ("start", "Start the Hub data runtime from cache, then synchronize"),
        ("sync", "Manually synchronize declared project metric snapshots"),
    ):
        sync_parser = subparsers.add_parser(command, help=help_text)
        sync_parser.add_argument("--root", type=Path, default=None)
        sync_parser.add_argument("--db", default=None)
        sync_parser.add_argument("--request-id", required=command == "sync")
        sync_parser.add_argument("--project-id", action="append", dest="project_ids")
        sync_parser.add_argument("--timeout-seconds", type=float, default=15.0)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "mcp":
        from hub.services.mcp_registry_service import print_mcp_list, print_mcp_policy

        if args.mcp_command == "list":
            return print_mcp_list()
        if args.mcp_command == "policy":
            return print_mcp_policy()
        parser.error(f"unknown mcp subcommand: {args.mcp_command}")

    if args.command == "registry":
        from hub.services.project_registry_service import print_registry_list

        if args.registry_command == "list":
            return print_registry_list()
        parser.error(f"unknown registry subcommand: {args.registry_command}")

    if args.command in {"start", "sync"}:
        from hub.connection_records import RecordError
        from hub.connection_refresh import RefreshLedgerError
        from hub.metric_sync import (
            SYNC_SCHEMA_VERSION,
            MetricSyncCoordinator,
            cache_then_sync,
        )
        from hub.metrics import bounded_json
        from hub.paths import PROJECT_ROOT

        mode = "startup" if args.command == "start" else "manual"
        request_id = args.request_id or "startup-" + secrets.token_hex(12)

        def emit_cache(event):
            print(bounded_json(event), flush=True)

        try:
            result = cache_then_sync(
                MetricSyncCoordinator(
                    args.root or PROJECT_ROOT,
                    db=args.db,
                ),
                request_id,
                mode=mode,
                emit_cache=emit_cache,
                project_ids=args.project_ids,
                timeout_seconds=args.timeout_seconds,
            )
            print(
                bounded_json(
                    {
                        "schema_version": SYNC_SCHEMA_VERSION,
                        "kind": "metric_sync_event",
                        "mode": mode,
                        "phase": "sync",
                        "data": result,
                    }
                ),
                flush=True,
            )
            return 0 if result["complete"] else 2
        except (
            OSError,
            RecordError,
            RefreshLedgerError,
            sqlite3.Error,
            ValueError,
            TypeError,
            KeyError,
        ):
            print(
                bounded_json(
                    {
                        "schema_version": SYNC_SCHEMA_VERSION,
                        "kind": "metric_sync_event",
                        "mode": mode,
                        "phase": "sync",
                        "data": {
                            "complete": False,
                            "error": "metric_sync_unavailable",
                        },
                    }
                ),
                flush=True,
            )
            return 2

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
