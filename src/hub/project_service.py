"""Registry-complete projection, reusing latest-attempt/last-success separation.

Reads only the Hub ledger, never external roots. A previous successful read is
explicitly historical whenever a newer attempt fails or its authority expires.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from hub.connection_records import (VERSION, content_hash, exact, fingerprint, identifier, require,
                                    string, timestamp, typed, validate_authority, validate_result)
from hub.connection_refresh import GENESIS_HASH, RefreshLedger


def _age(observed: str, now: datetime) -> float:
    return (now - datetime.fromisoformat(observed.replace("Z", "+00:00"))).total_seconds()


def row_update_key(row: dict) -> str:
    return content_hash({"project_id": row["project_id"], "name": row["name"],
                         "latest": row["latest_attempt"]["update_key"] if row["latest_attempt"] else None,
                         "last_success": row["last_success"]["update_key"] if row["last_success"] else None,
                         "freshness": row["freshness"]["state"], "authority_drift": row["freshness"]["authority_drift"]})


def project_snapshot(registry: dict, authority: dict, ledger: RefreshLedger | None, *,
                     observed_at: str | None = None) -> dict:
    observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    current = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    require(current.tzinfo is not None, "projection timestamp needs timezone")
    # One verified SQLite snapshot prevents mixing refs across concurrent commits.
    history = ledger.history(current_authority=authority) if ledger else {"head": None, "results": []}
    attempts, successes = {}, {}
    for item in history["results"]:
        record = validate_result(item["result"])
        attempts[record["project_id"]] = record
        if record["success"]:
            successes[record["project_id"]] = record
    projects = []
    for project in sorted(registry["projects"], key=lambda row: row["id"]):
        project_id = project["id"]
        removed = (project.get("local_presence", {}).get("status") == "removed_local"
                   or project.get("current_state_status") == "removed_local")
        latest, previous = attempts.get(project_id), successes.get(project_id)
        if removed:
            previous = None
            if latest and latest["disposition"] != "removed_local":
                latest = None
        drift = bool(latest and latest["authority"] != authority)
        age = _age(latest["observed_at"], current) if latest else None
        source_age = (_age(latest["sources"][0]["modified_at"], current)
                      if latest and latest["sources"] else None)
        if removed:
            state, reason = "removed_local", "已从本地移除；不扫描、不写入、不重试。"
        elif not latest:
            state, reason = "unknown", "Not refreshed under this Hub ledger."
        elif drift:
            state, reason = "stale", "Recorded registry/schema authority differs from current Hub authority."
        elif not latest["success"]:
            state, reason = ("stale" if previous else "unknown"), latest["errors"][0]["message"]
        elif age < 0 or age > latest["freshness"]["max_read_age_seconds"]:
            state, reason = "stale", "Read evidence expired or clock moved backwards; refresh required."
        else:
            state, reason = "fresh", None
        row = {"project_id": project_id, "name": project["name"],
               "local_presence": "removed_local" if removed else "registered_local",
               "latest_attempt": latest, "last_success": previous,
               "freshness": {"state": state, "reason": reason, "authority_drift": drift,
                             "read_age_seconds": age, "source_age_seconds": source_age}}
        row["update_key"] = row_update_key(row)
        projects.append(row)
    snapshot = {"schema_version": VERSION, "kind": "project_connections", "observed_at": observed_at,
                "authority": authority, "ledger_head": history["head"],
                "coverage": {"registry_count": len(projects),
                             "local_count": sum(r["local_presence"] != "removed_local" for r in projects),
                             "removed_local_count": sum(r["local_presence"] == "removed_local" for r in projects),
                             "resolved_count": sum(bool(r["latest_attempt"] and r["latest_attempt"]["success"]
                                                        and r["freshness"]["state"] == "fresh") for r in projects)},
                "projects": projects}
    return validate_snapshot(snapshot)


def validate_snapshot(snapshot: dict) -> dict:
    exact(snapshot, {"schema_version", "kind", "observed_at", "authority", "ledger_head", "coverage", "projects"}, "snapshot")
    require(type(snapshot) is dict and snapshot.get("schema_version") == VERSION
            and snapshot.get("kind") == "project_connections", "invalid snapshot schema")
    require(type(snapshot.get("projects")) is list, "snapshot project list required")
    timestamp(snapshot["observed_at"], "snapshot time")
    validate_authority(snapshot["authority"])
    if snapshot["ledger_head"] is not None:
        exact(snapshot["ledger_head"], {"sequence", "hash"}, "ledger head")
        typed(snapshot["ledger_head"]["sequence"], "integer")
        fingerprint(snapshot["ledger_head"]["hash"], "ledger head hash")
        require((snapshot["ledger_head"]["sequence"] == 0) == (snapshot["ledger_head"]["hash"] == GENESIS_HASH),
                "ledger genesis identity mismatch")
    current = datetime.fromisoformat(snapshot["observed_at"].replace("Z", "+00:00"))
    exact(snapshot["coverage"], {"registry_count", "local_count", "removed_local_count", "resolved_count"}, "coverage")
    for count in snapshot["coverage"].values():
        typed(count, "integer")
    ids = []
    visible_results = set()
    for row in snapshot["projects"]:
        exact(row, {"project_id", "name", "local_presence", "latest_attempt", "last_success", "freshness", "update_key"}, "project view")
        exact(row["freshness"], {"state", "reason", "authority_drift", "read_age_seconds", "source_age_seconds"}, "project freshness")
        identifier(row["project_id"], "projection project")
        string(row["name"], "projection project name")
        require(type(row["freshness"]["state"]) is str and row["freshness"]["state"] in {"fresh", "stale", "unknown", "removed_local"},
                "invalid project freshness state")
        require(type(row["freshness"]["authority_drift"]) is bool, "authority drift must be boolean")
        if row["freshness"]["state"] == "fresh":
            require(row["freshness"]["reason"] is None, "fresh projection cannot carry a failure reason")
        else:
            string(row["freshness"]["reason"], "projection freshness reason")
        for key in ("read_age_seconds", "source_age_seconds"):
            age_value = row["freshness"][key]
            require(age_value is None or (type(age_value) in {int, float} and math.isfinite(age_value)), "invalid freshness age")
        ids.append(row["project_id"])
        for key in ("latest_attempt", "last_success"):
            if row[key] is not None:
                validate_result(row[key])
                visible_results.add(content_hash(row[key]))
                require(row[key]["project_id"] == row["project_id"], "project/result identity mismatch")
                require(snapshot["ledger_head"] is not None and snapshot["ledger_head"]["sequence"] >= 2,
                        "stored result requires a populated ledger head")
        require(row["last_success"] is None or row["last_success"]["success"], "last success must be successful")
        require(row["local_presence"] != "removed_local" or row["last_success"] is None, "removed record exposes business history")
        require(type(row["local_presence"]) is str and row["local_presence"] in {"removed_local", "registered_local"}, "invalid local presence")
        latest = row["latest_attempt"]
        if latest is None:
            require(row["last_success"] is None, "last success requires a latest attempt")
        elif latest["success"]:
            require(row["last_success"] == latest, "successful latest attempt must be the last success")
        age = _age(latest["observed_at"], current) if latest else None
        source_age = _age(latest["sources"][0]["modified_at"], current) if latest and latest["sources"] else None
        drift = bool(latest and latest["authority"] != snapshot["authority"])
        require(row["freshness"]["read_age_seconds"] == age and row["freshness"]["source_age_seconds"] == source_age
                and row["freshness"]["authority_drift"] is drift, "freshness evidence mismatch")
        if row["local_presence"] == "removed_local":
            expected = "removed_local"
            require(latest is None or latest["disposition"] == "removed_local", "removed view contains live facts")
        elif not latest:
            expected = "unknown"
        elif drift or (latest["success"] and (age < 0 or age > latest["freshness"]["max_read_age_seconds"])):
            expected = "stale"
        elif latest["success"]:
            expected = "fresh"
        else:
            expected = "stale" if row["last_success"] else "unknown"
        require(row["freshness"]["state"] == expected, "snapshot must not promote failed or expired evidence")
        require(row["update_key"] == row_update_key(row), "projection update identity mismatch")
    require(len(ids) == len(set(ids)) == snapshot["coverage"]["registry_count"], "snapshot coverage mismatch")
    if visible_results:
        require(snapshot["ledger_head"]["sequence"] >= 1 + len(visible_results),
                "ledger head cannot precede its visible result events and request begin")
    require(snapshot["coverage"]["local_count"] == sum(r["local_presence"] != "removed_local" for r in snapshot["projects"])
            and snapshot["coverage"]["removed_local_count"] == sum(r["local_presence"] == "removed_local" for r in snapshot["projects"]),
            "presence coverage mismatch")
    require(snapshot["coverage"]["resolved_count"] == sum(r["freshness"]["state"] == "fresh" for r in snapshot["projects"]),
            "resolved coverage mismatch")
    return snapshot
