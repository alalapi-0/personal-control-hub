"""Metric changes in the existing connection ledger, with restartable requests.

One state per semantic change, no duplicate full snapshots. Observation timestamps
are refreshed separately. History is explicit (never invented) and can be pruned
by age while preserving current states and the predecessor needed for a delta.
"""
from __future__ import annotations

import json
from collections import Counter
from contextlib import closing
from datetime import datetime

from hub.connection_records import (
    RecordError,
    content_hash,
    exact,
    fingerprint,
    identifier,
    require,
    timestamp,
)
from hub.connection_refresh import RefreshLedger
from hub.metric_analytics import aggregate_current, observation_change
from hub.metrics import (MAX_PAGE_SIZE, bounded_json, eligibility, freshness, metric_catalog, metric_key,
                         semantic_metric, utcnow, validate_metric)

SYNC_ATTEMPT_FIELDS = {
    "sequence",
    "project_id",
    "request_id",
    "status",
    "started_at",
    "finished_at",
    "error",
    "snapshot",
}
SYNC_SNAPSHOT_FIELDS = {
    "id",
    "schema_version",
    "observed_at",
    "exporter",
    "source_versions",
    "disposition",
}
SYNC_ATTEMPT_STATUSES = {"running", "success", "failed", "skipped"}
MAX_SYNC_STATUS_PROJECTS = 1000


def _parsed_timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _compact_timestamp(value):
    if value is None or len(value) <= 64:
        return value
    return _parsed_timestamp(value).isoformat(timespec="microseconds")


def validate_sync_attempt(value):
    """Validate one durable synchronization attempt without source access."""
    exact(value, SYNC_ATTEMPT_FIELDS, "metric synchronization attempt")
    require(type(value["sequence"]) is int and value["sequence"] > 0,
            "metric synchronization attempt sequence invalid")
    identifier(value["project_id"], "metric synchronization project")
    identifier(value["request_id"], "metric synchronization request")
    require(
        type(value["status"]) is str
        and value["status"] in SYNC_ATTEMPT_STATUSES,
        "metric synchronization attempt status invalid",
    )
    timestamp(value["started_at"], "metric synchronization start")
    if value["finished_at"] is not None:
        timestamp(value["finished_at"], "metric synchronization finish")
        require(
            _parsed_timestamp(value["finished_at"]) >=
            _parsed_timestamp(value["started_at"]),
            "metric synchronization finish precedes start",
        )

    status = value["status"]
    if status == "running":
        require(
            value["finished_at"] is None
            and value["error"] is None
            and value["snapshot"] is None,
            "running synchronization attempt has terminal data",
        )
    elif status == "success":
        require(
            value["finished_at"] is not None
            and value["error"] is None
            and type(value["snapshot"]) is dict,
            "successful synchronization attempt is incomplete",
        )
    else:
        require(
            value["finished_at"] is not None
            and value["snapshot"] is None,
            "unsuccessful synchronization attempt has snapshot data",
        )
        identifier(value["error"], "metric synchronization error")

    snapshot = value["snapshot"]
    if snapshot is not None:
        exact(snapshot, SYNC_SNAPSHOT_FIELDS, "metric synchronization snapshot")
        fingerprint(snapshot["id"], "metric synchronization snapshot identity")
        identifier(
            snapshot["schema_version"],
            "metric synchronization snapshot schema",
        )
        timestamp(
            snapshot["observed_at"],
            "metric synchronization snapshot observation",
        )
        exact(snapshot["exporter"], {"id", "version"},
              "metric synchronization exporter")
        identifier(snapshot["exporter"]["id"], "metric synchronization exporter")
        identifier(
            snapshot["exporter"]["version"],
            "metric synchronization exporter version",
        )
        require(
            type(snapshot["source_versions"]) is dict
            and len(snapshot["source_versions"]) <= 32,
            "metric synchronization source versions invalid",
        )
        for group, version in snapshot["source_versions"].items():
            identifier(group, "metric synchronization source group")
            require(
                type(version) is str and 0 < len(version) <= 1000,
                "metric synchronization source version invalid",
            )
        identifier(snapshot["disposition"], "metric synchronization disposition")
    return value


def compact_sync_attempt(attempt):
    """Keep cache rows bounded while retaining deterministic version identity."""
    if attempt is None:
        return None
    value = {
        key: json.loads(json.dumps(attempt[key]))
        for key in (
            "sequence",
            "request_id",
            "status",
            "error",
        )
    }
    value["started_at"] = _compact_timestamp(attempt["started_at"])
    value["finished_at"] = _compact_timestamp(attempt["finished_at"])
    snapshot = attempt["snapshot"]
    if snapshot is None:
        value["snapshot"] = None
    else:
        versions = snapshot["source_versions"]
        value["snapshot"] = {
            key: json.loads(json.dumps(snapshot[key]))
            for key in (
                "id",
                "schema_version",
                "exporter",
                "disposition",
            )
        }
        value["snapshot"]["observed_at"] = _compact_timestamp(
            snapshot["observed_at"]
        )
        value["snapshot"].update(
            source_versions_identity=content_hash(versions),
            source_versions_count=len(versions),
        )
    return value


def compact_metric_receipt(receipt):
    """Return the same receipt facts with a bounded equivalent observation time."""
    value = dict(receipt)
    value["observed_at"] = _compact_timestamp(receipt["observed_at"])
    return value


class MetricStore(RefreshLedger):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.read_only:
            with self._write() as db:
                db.execute("CREATE TABLE IF NOT EXISTS metric_requests (id TEXT PRIMARY KEY, identity TEXT NOT NULL, projects TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS metric_receipts (request_id TEXT, project_id TEXT, result TEXT NOT NULL, PRIMARY KEY(request_id,project_id))")
                db.execute("CREATE TABLE IF NOT EXISTS metric_projects (project_id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, result TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS metric_changes (seq INTEGER PRIMARY KEY, project_id TEXT NOT NULL, key TEXT NOT NULL, version TEXT NOT NULL, observed_at TEXT NOT NULL, value TEXT NOT NULL)")
                db.execute("CREATE INDEX IF NOT EXISTS metric_change_key ON metric_changes(key,seq)")
                db.execute("CREATE TABLE IF NOT EXISTS metric_current (key TEXT PRIMARY KEY, project_id TEXT NOT NULL, seq INTEGER NOT NULL REFERENCES metric_changes(seq), observed_at TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS metric_sync_attempts (sequence INTEGER PRIMARY KEY, project_id TEXT NOT NULL, request_id TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('running','success','failed','skipped')), started_at TEXT NOT NULL, finished_at TEXT, record TEXT NOT NULL)")
                db.execute("CREATE INDEX IF NOT EXISTS metric_sync_project ON metric_sync_attempts(project_id,sequence)")
                db.execute("CREATE INDEX IF NOT EXISTS metric_sync_status ON metric_sync_attempts(project_id,status,sequence)")
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS metric_sync_running_project ON metric_sync_attempts(project_id) WHERE status='running'")
        with closing(self._connect()) as db:
            for table, expected in {
                "metric_requests": {"id", "identity", "projects"},
                "metric_receipts": {"request_id", "project_id", "result"},
                "metric_projects": {"project_id", "observed_at", "result"},
                "metric_changes": {"seq", "project_id", "key", "version", "observed_at", "value"},
                "metric_current": {"key", "project_id", "seq", "observed_at"},
            }.items():
                require({r["name"] for r in db.execute("PRAGMA table_info(" + table + ")")} == expected,
                        "metric ledger uninitialized or unsupported schema")
            sync_table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='metric_sync_attempts'"
            ).fetchone()
            if sync_table is not None:
                require(
                    {
                        row["name"]
                        for row in db.execute(
                            "PRAGMA table_info(metric_sync_attempts)"
                        )
                    }
                    == {
                        "sequence",
                        "project_id",
                        "request_id",
                        "status",
                        "started_at",
                        "finished_at",
                        "record",
                    },
                    "metric synchronization ledger has unsupported schema",
                )

    @staticmethod
    def _begin_request(
        db,
        request_id,
        projects,
        identity,
        *,
        rebind_uncommitted=False,
    ):
        projects = sorted(set(projects))
        digest = content_hash([projects, identity])
        old = db.execute(
            "SELECT identity,projects FROM metric_requests WHERE id=?",
            (request_id,),
        ).fetchone()
        if old is not None and old["identity"] != digest:
            can_rebind = False
            if rebind_uncommitted:
                # Pre-atomic sync versions could leave only this registration.
                # Never rebind a different project set or any committed receipt.
                try:
                    old_projects = json.loads(old["projects"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise RecordError(
                        "metric request project binding is invalid"
                    ) from exc
                can_rebind = (
                    old_projects == projects
                    and db.execute(
                        "SELECT 1 FROM metric_receipts WHERE request_id=?",
                        (request_id,),
                    ).fetchone()
                    is None
                )
            require(
                can_rebind,
                "request identity changed; use a new request ID",
            )
            db.execute(
                "UPDATE metric_requests SET identity=?,projects=? WHERE id=?",
                (digest, json.dumps(projects), request_id),
            )
            return
        db.execute(
            "INSERT OR IGNORE INTO metric_requests VALUES (?,?,?)",
            (request_id, digest, json.dumps(projects)),
        )

    def begin(self, request_id, projects, identity):
        identifier(request_id, "request_id")
        with self._write() as db:
            self._begin_request(db, request_id, projects, identity)

    def receipt(self, request_id, project_id):
        with closing(self._connect()) as db:
            row = db.execute("SELECT result FROM metric_receipts WHERE request_id=? AND project_id=?", (request_id, project_id)).fetchone()
            return json.loads(row[0]) if row else None

    @staticmethod
    def _attempt_from_row(row):
        attempt = validate_sync_attempt(json.loads(row["record"]))
        require(
            attempt["sequence"] == row["sequence"]
            and attempt["project_id"] == row["project_id"]
            and attempt["request_id"] == row["request_id"]
            and attempt["status"] == row["status"]
            and attempt["started_at"] == row["started_at"]
            and attempt["finished_at"] == row["finished_at"],
            "metric synchronization attempt columns disagree",
        )
        return attempt

    @staticmethod
    def _attempt_json(attempt):
        validate_sync_attempt(attempt)
        return json.dumps(
            attempt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def begin_sync_attempt(self, request_id, project_id, started_at):
        """Persist an attempt before source access and retire an interrupted predecessor."""
        identifier(request_id, "metric synchronization request")
        identifier(project_id, "metric synchronization project")
        timestamp(started_at, "metric synchronization start")
        with self._write() as db:
            for row in db.execute(
                "SELECT * FROM metric_sync_attempts "
                "WHERE project_id=? AND status='running' ORDER BY sequence",
                (project_id,),
            ):
                interrupted = self._attempt_from_row(row)
                interrupted.update(
                    status="failed",
                    finished_at=started_at,
                    error="sync_interrupted",
                )
                db.execute(
                    "UPDATE metric_sync_attempts "
                    "SET status=?,finished_at=?,record=? WHERE sequence=?",
                    (
                        interrupted["status"],
                        interrupted["finished_at"],
                        self._attempt_json(interrupted),
                        interrupted["sequence"],
                    ),
                )
            sequence = db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM metric_sync_attempts"
            ).fetchone()[0]
            attempt = {
                "sequence": sequence,
                "project_id": project_id,
                "request_id": request_id,
                "status": "running",
                "started_at": started_at,
                "finished_at": None,
                "error": None,
                "snapshot": None,
            }
            db.execute(
                "INSERT INTO metric_sync_attempts VALUES(?,?,?,?,?,?,?)",
                (
                    sequence,
                    project_id,
                    request_id,
                    attempt["status"],
                    started_at,
                    None,
                    self._attempt_json(attempt),
                ),
            )
            return sequence

    def finish_sync_attempt(
        self,
        sequence,
        finished_at,
        error,
        *,
        skipped=False,
    ):
        """Finish one failed or intentionally skipped attempt without replacing success."""
        require(type(sequence) is int and sequence > 0,
                "metric synchronization attempt sequence invalid")
        timestamp(finished_at, "metric synchronization finish")
        identifier(error, "metric synchronization error")
        require(type(skipped) is bool, "metric synchronization skip flag invalid")
        with self._write() as db:
            row = db.execute(
                "SELECT * FROM metric_sync_attempts WHERE sequence=?",
                (sequence,),
            ).fetchone()
            require(row is not None, "metric synchronization attempt missing")
            attempt = self._attempt_from_row(row)
            require(
                attempt["status"] == "running",
                "metric synchronization attempt already finished",
            )
            attempt.update(
                status="skipped" if skipped else "failed",
                finished_at=finished_at,
                error=error,
            )
            db.execute(
                "UPDATE metric_sync_attempts "
                "SET status=?,finished_at=?,record=? WHERE sequence=?",
                (
                    attempt["status"],
                    finished_at,
                    self._attempt_json(attempt),
                    sequence,
                ),
            )
            return attempt

    def _finish_sync_success(self, db, sequence, finished_at, result):
        row = db.execute(
            "SELECT * FROM metric_sync_attempts WHERE sequence=?",
            (sequence,),
        ).fetchone()
        require(row is not None, "metric synchronization attempt missing")
        attempt = self._attempt_from_row(row)
        require(
            attempt["status"] == "running"
            and attempt["project_id"] == result["project_id"],
            "metric synchronization success binding invalid",
        )
        attempt.update(
            status="success",
            finished_at=finished_at,
            snapshot={
                "id": result["snapshot_id"],
                "schema_version": result["snapshot_schema_version"],
                "observed_at": result["observed_at"],
                "exporter": result["exporter"],
                "source_versions": result["source_versions"],
                "disposition": result["disposition"],
            },
        )
        db.execute(
            "UPDATE metric_sync_attempts "
            "SET status=?,finished_at=?,record=? WHERE sequence=?",
            (
                attempt["status"],
                finished_at,
                self._attempt_json(attempt),
                sequence,
            ),
        )
        return attempt

    def sync_status(self, project_ids):
        """Return latest attempt and durable last success for bounded projects."""
        require(
            type(project_ids) is list
            and len(project_ids) <= MAX_SYNC_STATUS_PROJECTS,
            "invalid synchronization status project set",
        )
        for project_id in project_ids:
            identifier(project_id, "metric synchronization project")
        require(
            len(project_ids) == len(set(project_ids)),
            "invalid synchronization status project set",
        )
        status = {
            project_id: {"latest_attempt": None, "last_success": None}
            for project_id in project_ids
        }
        if not project_ids:
            return status
        with closing(self._connect()) as db:
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='metric_sync_attempts'"
            ).fetchone()
            if exists is None:
                return status
            for project_id in project_ids:
                latest = db.execute(
                    "SELECT * FROM metric_sync_attempts "
                    "WHERE project_id=? ORDER BY sequence DESC LIMIT 1",
                    (project_id,),
                ).fetchone()
                successful = db.execute(
                    "SELECT * FROM metric_sync_attempts "
                    "WHERE project_id=? AND status='success' "
                    "ORDER BY sequence DESC LIMIT 1",
                    (project_id,),
                ).fetchone()
                if latest is not None:
                    status[project_id]["latest_attempt"] = (
                        self._attempt_from_row(latest)
                    )
                if successful is not None:
                    status[project_id]["last_success"] = (
                        self._attempt_from_row(successful)
                    )
        return status

    def sync_attempts(self, project_id, *, after=0, limit=10):
        """Read bounded attempt history in durable sequence order."""
        identifier(project_id, "metric synchronization project")
        require(
            type(after) is int
            and after >= 0
            and type(limit) is int
            and 1 <= limit <= MAX_PAGE_SIZE,
            "invalid synchronization attempt page",
        )
        with closing(self._connect()) as db:
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='metric_sync_attempts'"
            ).fetchone()
            if exists is None:
                return {
                    "total_remaining": 0,
                    "items": [],
                    "returned": 0,
                    "next_cursor": None,
                }
            total = db.execute(
                "SELECT count(*) FROM metric_sync_attempts "
                "WHERE project_id=? AND sequence>?",
                (project_id, after),
            ).fetchone()[0]
            rows = []
            for row in db.execute(
                "SELECT * FROM metric_sync_attempts "
                "WHERE project_id=? AND sequence>? "
                "ORDER BY sequence LIMIT ?",
                (project_id, after, limit),
            ):
                rows.append(self._attempt_from_row(row))
        return {
            "total_remaining": total,
            "items": rows,
            "returned": len(rows),
            "next_cursor": (
                rows[-1]["sequence"]
                if len(rows) < total and rows
                else None
            ),
        }

    def save(
        self,
        request_id,
        result,
        *,
        sync_attempt=None,
        request_identity=None,
        rebind_uncommitted_request=False,
    ):
        identifier(request_id, "request_id")
        require(
            type(rebind_uncommitted_request) is bool,
            "metric request rebind flag invalid",
        )
        rows = result["metrics"]
        keys = [metric_key(validate_metric(row)) for row in rows]
        require(len(keys) == len(set(keys)), "duplicate metric identities")
        require(result.get("metric_definitions") == metric_catalog(rows),
                "metric definition catalog does not match facts")
        pid, observed = result["project_id"], result["observed_at"]
        require(all(row["project_id"] == pid for row in rows), "metric project identity mismatch")
        if sync_attempt is not None:
            exact(
                sync_attempt,
                {"sequence", "finished_at"},
                "metric synchronization success",
            )
            require(
                type(sync_attempt["sequence"]) is int
                and sync_attempt["sequence"] > 0,
                "metric synchronization attempt sequence invalid",
            )
            timestamp(
                sync_attempt["finished_at"],
                "metric synchronization finish",
            )
        if request_identity is not None:
            fingerprint(request_identity, "metric request identity")
        with self._write() as db:
            if request_identity is not None:
                self._begin_request(
                    db,
                    request_id,
                    [pid],
                    request_identity,
                    rebind_uncommitted=rebind_uncommitted_request,
                )
            request = db.execute("SELECT projects FROM metric_requests WHERE id=?", (request_id,)).fetchone()
            require(request is not None and pid in json.loads(request[0]), "unregistered collection result")
            previous = db.execute("SELECT result FROM metric_receipts WHERE request_id=? AND project_id=?", (request_id, pid)).fetchone()
            if previous:
                if sync_attempt is not None:
                    self._finish_sync_success(
                        db,
                        sync_attempt["sequence"],
                        sync_attempt["finished_at"],
                        result,
                    )
                return json.loads(previous[0])
            changed = 0
            # Current membership is replaced; historical success remains queryable after failures.
            current = {r["key"]: r for r in db.execute("SELECT c.key,c.seq,h.version FROM metric_current c JOIN metric_changes h ON h.seq=c.seq WHERE c.project_id=?", (pid,))}
            db.execute("DELETE FROM metric_current WHERE project_id=?", (pid,))
            for key, row in zip(keys, rows):
                version = content_hash(semantic_metric(row))
                old = current.get(key)
                if old and old["version"] == version:
                    seq = old["seq"]
                else:
                    cursor = db.execute("INSERT INTO metric_changes(project_id,key,version,observed_at,value) VALUES(?,?,?,?,?)", (pid, key, version, observed, json.dumps(row)))
                    seq = cursor.lastrowid
                    changed += 1
                db.execute("INSERT INTO metric_current VALUES(?,?,?,?)", (key, pid, seq, row["observed_at"]))
            summary = {k: v for k, v in result.items() if k != "metrics"}
            summary.update(metric_count=len(rows), numeric_count=sum(r["quality"] == "good" for r in rows), changed_metrics=changed)
            db.execute("INSERT OR REPLACE INTO metric_projects VALUES(?,?,?)", (pid, observed, json.dumps(summary)))
            receipt = {k: summary[k] for k in ("project_id", "observed_at", "disposition", "metric_count", "numeric_count", "changed_metrics")}
            receipt["issue_count"] = len(summary["issues"])
            db.execute("INSERT INTO metric_receipts VALUES(?,?,?)", (request_id, pid, json.dumps(receipt)))
            if sync_attempt is not None:
                self._finish_sync_success(
                    db,
                    sync_attempt["sequence"],
                    sync_attempt["finished_at"],
                    result,
                )
            self._fault("save_metric_result", {
                "request_id": request_id,
                "project_id": pid,
                "metric_count": len(rows),
                "changed_metrics": changed,
            })
            return receipt

    def coverage(self, registry, now=None, *, after=0, limit=10):
        require(type(limit) is int and 1 <= limit <= MAX_PAGE_SIZE and after >= 0, "invalid project page")
        now = now or utcnow()
        with closing(self._connect()) as db:
            stored = {r[0]: json.loads(r[1]) for r in db.execute("SELECT project_id,result FROM metric_projects")}
        project_ids = [project["id"] for project in registry["projects"]]
        synchronization = {}
        for start in range(0, len(project_ids), MAX_SYNC_STATUS_PROJECTS):
            synchronization.update(
                self.sync_status(
                    project_ids[start:start + MAX_SYNC_STATUS_PROJECTS]
                )
            )
        counts, quality = Counter(), Counter()
        local = removed = 0
        rows = []
        for project in registry["projects"]:
            pid = project["id"]
            is_removed = project.get("local_presence", {}).get("status") == "removed_local" or project.get("current_state_status") == "removed_local"
            removed += is_removed
            local += not is_removed
            value = stored.get(pid)
            allowed = eligibility(project)
            disposition = allowed if allowed != "eligible" else value["disposition"] if value else "not_collected"
            counts[disposition] += 1
            row = dict(project_id=pid, disposition=disposition, metrics=value["metric_count"] if value else 0,
                       numeric=value["numeric_count"] if value else 0,
                       issues=len(value["issues"]) if value else 0,
                       observed_at=_compact_timestamp(value["observed_at"]) if value else None)
            row["freshness"] = freshness(row["observed_at"], now) if value and allowed == "eligible" else disposition
            if value and value.get("registry_binding") and value["registry_binding"] != content_hash(project):
                row["freshness"] = allowed if allowed != "eligible" else "authority_changed"
            project_status = synchronization[pid]
            latest = compact_sync_attempt(project_status["latest_attempt"])
            last_success = compact_sync_attempt(project_status["last_success"])
            row["latest_attempt"] = latest
            row["last_success"] = last_success
            if latest is None:
                if value is None:
                    row["view_role"] = "unavailable"
                elif row["freshness"] == "fresh":
                    row["view_role"] = "current_untracked"
                else:
                    row["view_role"] = "historical_untracked"
            elif latest["status"] == "success":
                current_snapshot = (
                    value is not None
                    and latest["snapshot"] is not None
                    and value.get("snapshot_id") == latest["snapshot"]["id"]
                )
                row["view_role"] = (
                    "current"
                    if current_snapshot and row["freshness"] == "fresh"
                    else "historical"
                )
                if not current_snapshot and allowed == "eligible":
                    row["freshness"] = "sync_state_mismatch"
            else:
                if row["freshness"] not in {
                    "removed_local",
                    "disabled",
                    "authority_changed",
                }:
                    row["freshness"] = "sync_" + latest["status"]
                row["view_role"] = (
                    "historical"
                    if last_success is not None
                    else "unavailable"
                )
            quality[row["freshness"]] += 1
            rows.append(row)
        result = {"coverage": {"registered": len(rows), "local": local, "removed_local": removed,
                             "cloud_excluded": registry.get("cloud_excluded_count", 17),
                             "dispositions": dict(counts), "freshness": dict(quality),
                             "metrics": sum(r["metrics"] for r in rows), "numeric": sum(r["numeric"] for r in rows),
                             "unknown": sum(r["metrics"] - r["numeric"] for r in rows),
                             "read_errors": sum(i.get("kind", "read_failure") in {"read_failure", "invalid_enum"} for p in registry["projects"] for i in stored.get(p["id"], {}).get("issues", [])),
                             "data_gaps": sum(i.get("kind") == "data_gap" for p in registry["projects"] for i in stored.get(p["id"], {}).get("issues", [])),
                             "business_blockers": sum(i.get("kind") == "business_blocker" for p in registry["projects"] for i in stored.get(p["id"], {}).get("issues", [])),
                             "issues": sum(r["issues"] for r in rows)}, "projects": [], "projects_total": len(rows), "next_cursor": None}
        for row in rows[after:after + limit]:
            try:
                bounded_json({**result, "projects": result["projects"] + [row]})
            except ValueError:
                break
            result["projects"].append(row)
        end = after + len(result["projects"])
        result["next_cursor"] = end if end < len(rows) else None
        return result

    def project_snapshot(self, project_id, *, after=0, limit=10):
        """Read management and business facts from one imported snapshot transaction."""
        from hub.metric_snapshot import (
            SNAPSHOT_SCHEMA_VERSION,
            validate_metric_snapshot,
        )

        identifier(project_id, "snapshot view project")
        require(
            type(limit) is int and 1 <= limit <= MAX_PAGE_SIZE and after >= 0,
            "invalid snapshot view page",
        )
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            stored = db.execute(
                "SELECT observed_at,result FROM metric_projects WHERE project_id=?",
                (project_id,),
            ).fetchone()
            require(stored is not None, "project has no imported metric snapshot")
            summary = json.loads(stored["result"])
            required = {
                "snapshot_id",
                "snapshot_kind",
                "snapshot_schema_version",
                "exporter",
                "management",
                "source_versions",
                "metric_definitions",
                "issues",
                "disposition",
                "observed_at",
                "metric_count",
                "numeric_count",
            }
            require(
                required <= set(summary)
                and summary["snapshot_schema_version"] == SNAPSHOT_SCHEMA_VERSION,
                "project was not imported from the current standard snapshot",
            )
            current = db.execute(
                """SELECT h.seq,h.key,h.value,c.observed_at
                   FROM metric_current c
                   JOIN metric_changes h ON h.seq=c.seq
                   WHERE c.project_id=?""",
                (project_id,),
            ).fetchall()
            entries = []
            for item in current:
                row = json.loads(item["value"])
                row["observed_at"] = item["observed_at"]
                validate_metric(row)
                require(
                    row["project_id"] == project_id
                    and row["observed_at"] == stored["observed_at"]
                    and item["key"] == metric_key(row),
                    "stored business metric is outside its snapshot",
                )
                entries.append({"sequence": item["seq"], "metric": row})
            total = len(entries)
            require(total == summary["metric_count"], "stored snapshot metric count diverged")
            snapshot = validate_metric_snapshot({
                "schema_version": summary["snapshot_schema_version"],
                "kind": summary["snapshot_kind"],
                "project_id": project_id,
                "observed_at": stored["observed_at"],
                "exporter": summary["exporter"],
                "management": summary["management"],
                "disposition": summary["disposition"],
                "metrics": sorted(
                    (entry["metric"] for entry in entries),
                    key=metric_key,
                ),
                "metric_definitions": summary["metric_definitions"],
                "issues": summary["issues"],
                "source_versions": summary["source_versions"],
                "snapshot_id": summary["snapshot_id"],
            })
            eligible = sorted(
                (entry for entry in entries if entry["sequence"] > after),
                key=lambda entry: entry["sequence"],
            )
            remaining = len(eligible)
            items = eligible[:limit]
            return {
                "schema_version": "1.0",
                "kind": "metric_project_snapshot_view",
                "project_id": project_id,
                "snapshot_id": summary["snapshot_id"],
                "snapshot_schema_version": summary["snapshot_schema_version"],
                "source_versions": snapshot["source_versions"],
                "management": snapshot["management"],
                "business": {
                    "metric_count": total,
                    "numeric_count": summary["numeric_count"],
                    "issue_count": len(summary["issues"]),
                    "disposition": summary["disposition"],
                    "items": items,
                    "returned": len(items),
                    "remaining": remaining,
                    "next_cursor": (
                        items[-1]["sequence"]
                        if items and len(items) < remaining
                        else None
                    ),
                },
            }

    def page(self, *, project_id=None, metric_id=None, stage=None, after=0, limit=10, history=False, since=None,
             current_projects=None, now=None):
        now = now or utcnow()
        require(type(limit) is int and 1 <= limit <= MAX_PAGE_SIZE and after >= 0, "invalid page bounds")
        sql = ("SELECT h.seq,h.value,h.observed_at,h.observed_at AS changed_at FROM metric_changes h" if history else
               "SELECT h.seq,h.value,c.observed_at,h.observed_at AS changed_at FROM metric_current c JOIN metric_changes h ON h.seq=c.seq")
        clauses, args = ["h.seq > ?"], [after]
        for column, value in (("h.project_id", project_id), ("json_extract(h.value,'$.metric_id')", metric_id),
                              ("coalesce(json_extract(h.value,'$.dimensions.stage'),json_extract(h.value,'$.dimensions.verdict'),json_extract(h.value,'$.dimensions.status'))", stage)):
            if value is not None:
                clauses.append(column + "=?")
                args.append(value)
        if since:
            from hub.connection_records import timestamp
            timestamp(since, "since")
            clauses.append("julianday(" + ("h.observed_at" if history else "c.observed_at") + ")>=julianday(?)")
            args.append(since)
        with closing(self._connect()) as db:
            where = " WHERE " + " AND ".join(clauses)
            total = db.execute("SELECT count(*) FROM (" + sql + where + ")", args).fetchone()[0]
            values = db.execute(sql + where + " ORDER BY h.seq LIMIT ?", [*args, limit]).fetchall()
            rows = []
            for item in values:
                value = json.loads(item["value"])
                value["observed_at"] = item["observed_at"]
                previous = db.execute("SELECT value FROM metric_changes WHERE key=? AND seq<? ORDER BY seq DESC LIMIT 1", (metric_key(value), item["seq"])).fetchone()
                prior = json.loads(previous[0]) if previous else None
                entry = dict(sequence=item["seq"], metric=value,
                             previous_observed_at=prior["observed_at"] if prior else None)
                entry.update(observation_change(value, prior, changed_at=item["changed_at"]))
                entry.update(changed_at=item["changed_at"])
                project = current_projects.get(value["project_id"]) if current_projects is not None else None
                allowed = eligibility(project) if project is not None else "authority_unknown"
                status = freshness(value["observed_at"], now) if allowed == "eligible" else allowed
                binding = db.execute("SELECT result FROM metric_projects WHERE project_id=?", (value["project_id"],)).fetchone()
                expected = json.loads(binding[0]).get("registry_binding") if binding else None
                if allowed == "eligible" and expected and expected != content_hash(project):
                    status = "authority_changed"
                entry.update(freshness=status, view_role="historical" if history or status != "fresh" else "current")
                candidate = {"total_remaining": total, "items": [*rows, entry], "next_cursor": item["seq"], "returned": len(rows)+1}
                try:
                    bounded_json(candidate)
                except ValueError:
                    break
                rows.append(entry)
        return {"total_remaining": total, "items": rows, "returned": len(rows),
                "next_cursor": rows[-1]["sequence"] if len(rows) < total and rows else None}

    def aggregate(self, *, project_id=None, metric_id=None, after=0, limit=10):
        require(type(limit) is int and 1 <= limit <= MAX_PAGE_SIZE and after >= 0, "invalid page bounds")
        sql = ("SELECT h.value,c.observed_at FROM metric_current c "
               "JOIN metric_changes h ON h.seq=c.seq")
        clauses, args = [], []
        for column, value in (("h.project_id", project_id), ("json_extract(h.value,'$.metric_id')", metric_id)):
            if value is not None:
                clauses.append(column + "=?")
                args.append(value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with closing(self._connect()) as db:
            values = db.execute(sql + where + " ORDER BY h.project_id,h.seq", args).fetchall()
            catalogs = {}
            for stored in db.execute("SELECT project_id,result FROM metric_projects"):
                payload = json.loads(stored["result"])
                catalogs[stored["project_id"]] = payload.get("metric_definitions") or []
        rows = []
        for item in values:
            value = json.loads(item["value"])
            value["observed_at"] = item["observed_at"]
            validate_metric(value)
            rows.append(value)
        computed = aggregate_current(rows, catalogs)
        groups = computed["groups"]
        page = []
        for group in groups[after:]:
            candidate = {**computed, "groups": page + [group], "returned": len(page) + 1,
                         "total_remaining": len(groups) - after, "next_cursor": after + len(page) + 1}
            try:
                bounded_json(candidate)
            except RecordError:
                break
            page.append(group)
        end = after + len(page)
        return {
            **computed,
            "groups": page,
            "returned": len(page),
            "total_remaining": len(groups) - after,
            "next_cursor": end if end < len(groups) else None,
            "distribution": {
                "groups": len(groups),
                "good": sum(1 for group in groups if group["quality"] == "good"),
                "unknown": sum(1 for group in groups if group["quality"] != "good"),
                "rejected": len(computed["rejected"]),
            },
        }

    def validate(self):
        from hub.metric_snapshot import SNAPSHOT_SCHEMA_VERSION

        current_snapshots = []
        synchronization_attempts = 0
        with closing(self._connect()) as db:
            require(db.execute("PRAGMA quick_check").fetchone()[0] == "ok", "metric ledger integrity failed")
            require(not db.execute("PRAGMA foreign_key_check").fetchall(), "metric references invalid")
            for stored in db.execute(
                "SELECT project_id,observed_at,result FROM metric_projects"
            ):
                summary = json.loads(stored["result"])
                if "snapshot_id" not in summary:
                    continue
                version = summary.get("snapshot_schema_version")
                if version == "1.0":
                    continue
                require(
                    version == SNAPSHOT_SCHEMA_VERSION,
                    "stored metric snapshot version unsupported",
                )
                current_snapshots.append(stored["project_id"])
            count = 0
            for row in db.execute("SELECT key,version,value FROM metric_changes"):
                value = validate_metric(json.loads(row["value"]))
                require(row["key"] == metric_key(value) and row["version"] == content_hash(semantic_metric(value)), "metric history identity invalid")
                count += 1
            sync_table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='metric_sync_attempts'"
            ).fetchone()
            running = set()
            if sync_table is not None:
                for row in db.execute(
                    "SELECT * FROM metric_sync_attempts ORDER BY sequence"
                ):
                    attempt = self._attempt_from_row(row)
                    if attempt["status"] == "running":
                        require(
                            attempt["project_id"] not in running,
                            "multiple running synchronization attempts",
                        )
                        running.add(attempt["project_id"])
                    synchronization_attempts += 1
        for project_id in current_snapshots:
            self.project_snapshot(project_id, limit=1)
        return {
            "valid": True,
            "metric_versions": count,
            "synchronization_attempts": synchronization_attempts,
        }

    def prune(self, before):
        """Explicit local retention, preserving current and immediate predecessor."""
        from hub.connection_records import timestamp
        timestamp(before, "before")
        with self._write() as db:
            cursor = db.execute("DELETE FROM metric_changes WHERE julianday(observed_at)<julianday(?) AND seq NOT IN (SELECT seq FROM metric_current) AND seq NOT IN (SELECT max(h.seq) FROM metric_changes h JOIN metric_current c ON h.key=c.key AND h.seq<c.seq GROUP BY h.key)", (before,))
            return {"deleted_metric_versions": cursor.rowcount, "before": before}
