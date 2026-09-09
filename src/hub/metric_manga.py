"""Bounded, read-only manga metadata projection; no catalog or workflow startup."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from hub.connection_records import content_hash, require
from hub.metric_sources import metadata_path
from hub.metrics import issue, metric

MAX_ROWS = 10000


@contextmanager
def _snapshot(base, relative):
    path = metadata_path(base, relative)
    require(path.is_file(), "selected database missing")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    steps = 0
    def budget():
        nonlocal steps
        steps += 1
        return int(steps > 2000)
    connection.set_progress_handler(budget, 1000)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _rows(connection, query, args=()):
    rows = connection.execute(query, args).fetchmany(MAX_ROWS + 1)
    require(len(rows) <= MAX_ROWS, "metadata row limit exceeded")
    return rows


def collect_manga(root: Path, project_id, observed_at, spec: dict) -> dict:
    metrics, issues = [], []
    def emit(name, value, unit, relative, query, *, dimensions=None, reason=None, schema=None):
        source = str(base / relative) + "#" + query
        version = content_hash([query, schema, dimensions or {}, value, reason])
        metrics.append(metric(project_id, name, value, unit, source, version, observed_at,
            dimensions=dimensions, quality="unknown" if reason else None, reason=reason,
            counting_basis=query))
    def failure(relative, group, source_id=None):
        source = str(base / relative)
        issues.append(issue(project_id, "MANGA_" + group.upper() + "_UNAVAILABLE", source,
            recovery_condition="Restore the selected metadata schema and identity; retry read-only collection."))
        emit(group + "_available", None, "boolean", relative, "selected source identity and schema",
             reason="Selected metadata could not be read or its identity/schema is invalid.",
             dimensions={"source_project_id": source_id} if source_id else None)
    try:
        base = Path(spec["data_root"])
        require(base.is_absolute() and not base.is_symlink(), "explicit absolute data root required")
        # Validate root even when all selected databases are absent.
        metadata_path(base, "metadata-probe")
    except (KeyError, ValueError, OSError):
        return {"metrics": [], "issues": [issue(project_id, "MANGA_DATA_ROOT_INVALID", str(root))],
                "disposition": "unavailable", "source_version": content_hash("invalid data root")}
    review = spec.get("review_db", "missing.sqlite3")
    try:
        with _snapshot(base, review) as c:
            batches = _rows(c, "SELECT id,item_count,revision,format_version FROM batches")
            require(len(batches) == 1 and batches[0][0] == spec.get("batch_id"), "batch identity mismatch")
            batch_id, expected, _, format_version = batches[0]
            require(format_version in (1, 2), "unsupported batch format")
            rows = _rows(c, "SELECT id,batch_id,source_project_id,verdict FROM items")
            require(len(rows) == expected and len({r[0] for r in rows}) == len(rows), "item identity/count mismatch")
            require(all(r[1] == batch_id and r[2] in spec.get("project_dbs", {}) for r in rows), "item source identity mismatch")
            schema = ["items:id,batch_id,source_project_id,verdict", format_version]
            for item_id, _, source_id, verdict in rows:
                if verdict == "issues":
                    problem = issue(project_id, "review_redo_required", str(base / review) + "#items/" + item_id,
                        affected_items=1, kind="business_blocker",
                        recovery_condition="Complete an authorized repair candidate and submit its new version for review; old approval cannot release it.")
                    problem.update(item_id=item_id, source_project_id=source_id, batch_id=batch_id,
                                   business_time_reason="Continuous failure start is not established by current metadata.")
                    issues.append(problem)
            try:
                version_rows = dict(_rows(c, "SELECT id,artifact_revision FROM items"))
            except sqlite3.Error:
                version_rows = {}
            emit("review_total", len(rows), "pages", review, "count current items", schema=schema)
            for source_id in sorted({r[2] for r in rows}):
                selected = [r for r in rows if r[2] == source_id]
                for status in ("approved", "issues", "pending", "unknown"):
                    n = sum((r[3] not in ("approved", "issues", "pending")) if status == "unknown" else r[3] == status for r in selected)
                    emit("review_count", n, "pages", review, "count current items by source_project_id and verdict; unknown groups unrecognized verdicts",
                         dimensions={"source_project_id": source_id, "verdict": status}, schema=schema)
                versions = [version_rows.get(r[0]) for r in selected]
                valid = all(type(v) is int and v >= 1 for v in versions)
                emit("review_current_version_max", max(versions) if valid else None, "revision", review,
                     "max artifact_revision of current items; excludes artifact_revisions history",
                     dimensions={"source_project_id": source_id}, schema=schema,
                     reason=None if valid else "Invalid current artifact revision.")
            if any(r[3] not in ("approved", "issues", "pending") for r in rows):
                issues.append(issue(project_id, "MANGA_UNKNOWN_VERDICT", str(base / review), kind="invalid_enum"))
            emit("blocker_oldest_age", None, "seconds", review, "age requires evidenced start of continuous current issues episode",
                 reason="Current verdict timestamps and historical revisions do not establish a verified continuous blocker start.")
    except (sqlite3.Error, ValueError, OSError, TypeError):
        failure(review, "review")
    for source_id, relative in sorted(spec.get("project_dbs", {}).items()):
        try:
            with _snapshot(base, relative) as c:
                identities = _rows(c, "SELECT id FROM projects")
                require(identities == [(source_id,)], "project identity mismatch")
                for table, field, statuses, name, unit in (
                    ("jobs", "status", ("queued", "running", "completed", "failed", "cancelled"), "job_count", "jobs"),
                    ("page_generations", "state", ("active", "superseded"), "generation_count", "generations"),
                ):
                    try:
                        groups = _rows(c, f"SELECT {field},count(*) FROM {table} WHERE project_id=? GROUP BY {field}", (source_id,))
                        require(c.execute(f"SELECT count(*) FROM {table} WHERE project_id IS NULL OR project_id!=?", (source_id,)).fetchone()[0] == 0, "row project mismatch")
                        counts = dict(groups)
                        for status in (*statuses, "unknown"):
                            value = sum(n for s, n in groups if s not in statuses) if status == "unknown" else counts.get(status, 0)
                            emit(name, value, unit, relative, f"count {table} by {field}; " + ("cumulative records; queued/running current queue states" if table == "jobs" else "chain metadata only; not running workers"),
                                 dimensions={"source_project_id": source_id, field: status}, schema=[table, field, "project_id"])
                        if any(s not in statuses for s, _ in groups):
                            issues.append(issue(project_id, "MANGA_UNKNOWN_" + field.upper(), str(base / relative) + "#" + table, kind="invalid_enum"))
                    except (sqlite3.Error, ValueError, TypeError):
                        failure(relative, table, source_id)
        except (sqlite3.Error, ValueError, OSError, TypeError):
            failure(relative, "project", source_id)
    return {"metrics": metrics, "issues": issues, "disposition": "partial" if issues or any(m["value"] is None for m in metrics) else "resolved",
            "source_version": content_hash(sorted(m["source_version"] for m in metrics))}
