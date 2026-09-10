"""Metric changes in the existing connection ledger, with restartable requests.

One state per semantic change, no duplicate full snapshots. Observation timestamps
are refreshed separately. History is explicit (never invented) and can be pruned
by age while preserving current states and the predecessor needed for a delta.
"""
from __future__ import annotations

import json
from collections import Counter
from contextlib import closing

from hub.connection_records import content_hash, require
from hub.connection_refresh import RefreshLedger
from hub.metrics import (MAX_PAGE_SIZE, bounded_json, eligibility, freshness, metric_catalog, metric_key,
                         semantic_metric, utcnow, validate_metric)


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

    def begin(self, request_id, projects, identity):
        from hub.connection_records import identifier
        identifier(request_id, "request_id")
        projects = sorted(set(projects))
        digest = content_hash([projects, identity])
        with self._write() as db:
            old = db.execute("SELECT identity FROM metric_requests WHERE id=?", (request_id,)).fetchone()
            require(old is None or old[0] == digest, "request identity changed; use a new request ID")
            db.execute("INSERT OR IGNORE INTO metric_requests VALUES (?,?,?)", (request_id, digest, json.dumps(projects)))

    def receipt(self, request_id, project_id):
        with closing(self._connect()) as db:
            row = db.execute("SELECT result FROM metric_receipts WHERE request_id=? AND project_id=?", (request_id, project_id)).fetchone()
            return json.loads(row[0]) if row else None

    def save(self, request_id, result):
        rows = result["metrics"]
        keys = [metric_key(validate_metric(row)) for row in rows]
        require(len(keys) == len(set(keys)), "duplicate metric identities")
        require(result.get("metric_definitions") == metric_catalog(rows),
                "metric definition catalog does not match facts")
        pid, observed = result["project_id"], result["observed_at"]
        require(all(row["project_id"] == pid for row in rows), "metric project identity mismatch")
        with self._write() as db:
            request = db.execute("SELECT projects FROM metric_requests WHERE id=?", (request_id,)).fetchone()
            require(request is not None and pid in json.loads(request[0]), "unregistered collection result")
            previous = db.execute("SELECT result FROM metric_receipts WHERE request_id=? AND project_id=?", (request_id, pid)).fetchone()
            if previous:
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
                       observed_at=value["observed_at"] if value else None)
            row["freshness"] = freshness(row["observed_at"], now) if value and allowed == "eligible" else disposition
            if value and value.get("registry_binding") and value["registry_binding"] != content_hash(project):
                row["freshness"] = allowed if allowed != "eligible" else "authority_changed"
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
                delta = value["value"] - prior["value"] if prior and prior["value"] is not None and value["value"] is not None else None
                entry = dict(sequence=item["seq"], metric=value, delta=delta,
                             previous_observed_at=prior["observed_at"] if prior else None)
                from datetime import datetime
                elapsed = ((datetime.fromisoformat(item["changed_at"].replace("Z", "+00:00")) -
                            datetime.fromisoformat(prior["observed_at"].replace("Z", "+00:00"))).total_seconds()) if prior else None
                entry.update(changed_at=item["changed_at"], change_per_second=delta / elapsed if delta is not None and elapsed and elapsed > 0 else None)
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

    def validate(self):
        with closing(self._connect()) as db:
            require(db.execute("PRAGMA quick_check").fetchone()[0] == "ok", "metric ledger integrity failed")
            require(not db.execute("PRAGMA foreign_key_check").fetchall(), "metric references invalid")
            count = 0
            for row in db.execute("SELECT key,version,value FROM metric_changes"):
                value = validate_metric(json.loads(row["value"]))
                require(row["key"] == metric_key(value) and row["version"] == content_hash(semantic_metric(value)), "metric history identity invalid")
                count += 1
        return {"valid": True, "metric_versions": count}

    def prune(self, before):
        """Explicit local retention, preserving current and immediate predecessor."""
        from hub.connection_records import timestamp
        timestamp(before, "before")
        with self._write() as db:
            cursor = db.execute("DELETE FROM metric_changes WHERE julianday(observed_at)<julianday(?) AND seq NOT IN (SELECT seq FROM metric_current) AND seq NOT IN (SELECT max(h.seq) FROM metric_changes h JOIN metric_current c ON h.key=c.key AND h.seq<c.seq GROUP BY h.key)", (before,))
            return {"deleted_metric_versions": cursor.rowcount, "before": before}
