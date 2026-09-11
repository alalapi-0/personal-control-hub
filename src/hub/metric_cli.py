"""Bounded machine-readable project metrics through the existing Hub CLI."""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from hub.connection_records import RecordError, require
from hub.connection_refresh import RefreshLedgerError
from hub.connections import load_registry_at
from hub.metric_collect import MetricCollector
from hub.metric_store import MetricStore
from hub.metrics import (MAX_PAGE_SIZE, METRIC_FIELDS, METRIC_KEY_FIELDS, bounded_json, metric,
                         metric_contract_schema)
from hub.paths import PROJECT_ROOT


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only project collection; local numeric ledger and bounded queries")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--db", default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect")
    collect.add_argument("--request-id", required=True)
    collect.add_argument("--project-id", action="append")
    collect.add_argument("--remote-git", action="store_true", help="Read GitHub actual default branch and HEAD containment; no fetch or ref writes")
    collect.add_argument("--github-ci", action="store_true", help="Read GitHub workflow runs for exact local HEAD; never run checks")
    summary = commands.add_parser("summary")
    summary.add_argument("--after", type=int, default=0)
    summary.add_argument("--limit", type=int, default=10)
    for name in ("validate", "schema", "feishu"):
        commands.add_parser(name)
    for name in ("query", "changes", "issues", "aggregate"):
        query = commands.add_parser(name)
        query.add_argument("--project-id")
        query.add_argument("--metric-id")
        query.add_argument("--stage")
        query.add_argument("--after", type=int, default=0)
        query.add_argument("--limit", type=int, default=10)
        query.add_argument("--since")
    prune = commands.add_parser("prune")
    prune.add_argument("--before", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            print(bounded_json(metric_contract_schema()))
            return 0
        if args.command == "collect":
            collector = MetricCollector(args.root, remote_git=args.remote_git, github_ci=args.github_ci)
            store = MetricStore(args.root, args.db)
            receipts = collector.refresh(store, args.request_id, args.project_id)
            result = store.coverage(collector.registry)
            result.update(request_id=args.request_id, requested=len(receipts),
                          changed_metrics=sum(r["changed_metrics"] for r in receipts))
        else:
            store = MetricStore(args.root, args.db, read_only=args.command != "prune")
            if args.command == "validate":
                result = store.validate()
            elif args.command == "prune":
                result = store.prune(args.before)
            elif args.command == "aggregate":
                result = store.aggregate(project_id=args.project_id, metric_id=args.metric_id,
                                         after=args.after, limit=args.limit)
            elif args.command in {"query", "changes"}:
                try:
                    registry = load_registry_at(args.root / "data/registry/external_projects.yaml")
                except (OSError, RecordError):
                    registry = {"projects": []}  # Historical values remain readable, explicitly authority_unknown.
                result = store.page(project_id=args.project_id, metric_id=args.metric_id,
                                    stage=args.stage, after=args.after, limit=args.limit,
                                    history=args.command == "changes", since=args.since,
                                    current_projects={p["id"]: p for p in registry["projects"]})
            elif args.command == "issues":
                require(1 <= args.limit <= MAX_PAGE_SIZE and args.after >= 0, "invalid page bounds")
                with closing(store._connect()) as db:
                    sql, params = "SELECT project_id,result FROM metric_projects", []
                    if args.project_id:
                        sql += " WHERE project_id=?"
                        params = [args.project_id]
                    rows = [item for row in db.execute(sql + " ORDER BY project_id", params)
                            for item in json.loads(row["result"])["issues"]]
                items = []
                for item in rows[args.after:args.after + args.limit]:
                    try:
                        bounded_json({"total": len(rows), "items": items + [item], "next_cursor": args.after + len(items) + 1})
                    except RecordError:
                        break
                    items.append(item)
                result = {"total": len(rows), "items": items, "next_cursor": args.after + len(items) if args.after + len(items) < len(rows) else None}
            else:
                registry = load_registry_at(args.root / "data/registry/external_projects.yaml")
                result = store.coverage(registry, after=getattr(args, "after", 0), limit=getattr(args, "limit", 10))
                if args.command == "feishu":
                    result = {"enabled": False, "write_back_allowed": False, "network_calls": 0,
                              "mapping": {field: field for field in sorted(METRIC_FIELDS)},
                              "record_key": list(METRIC_KEY_FIELDS),
                              "sample_kind": "synthetic_contract_example_not_project_data",
                              "sample": metric("example-project", "example.backlog", None, "items",
                                  "local:contract-example", "example-v1", "1970-01-01T00:00:00Z",
                                  dimensions={"stage": "pending"}, reason="Example has no business source.",
                                  counting_basis="Schema example only; never include in project coverage or totals."),
                              "coverage": result["coverage"]}
        print(bounded_json(result))
        return 0
    except (OSError, RecordError, RefreshLedgerError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(bounded_json({"valid": False, "error": getattr(exc, "code", "METRIC_INPUT_OR_STORE_INVALID"),
                            "reason": "Metadata, request identity, or local metric store unavailable; no project workflow executed."}))
        return 2
