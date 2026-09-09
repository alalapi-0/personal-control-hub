"""Numeric project facts. No model, workflow execution, or UI dependency."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

from hub.connection_records import content_hash, identifier, require, timestamp

VERSION = "1.0"
MAX_SUMMARY_BYTES = 8192
MAX_PAGE_SIZE = 50
METRIC_FIELDS = {"project_id", "metric_id", "value", "unit", "dimensions", "observed_at",
                 "business_at", "source_ref", "source_version", "quality", "reason", "counting_basis"}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def metric(project_id, metric_id, value, unit, source_ref, source_version, observed_at,
           *, dimensions=None, business_at=None, quality=None, reason=None, counting_basis):
    return validate_metric(dict(project_id=project_id, metric_id=metric_id, value=value, unit=unit,
        dimensions=dimensions or {}, observed_at=observed_at, business_at=business_at,
        source_ref=source_ref, source_version=source_version, quality=quality or ("good" if value is not None else "unknown"),
        reason=reason, counting_basis=counting_basis))


def validate_metric(row):
    require(type(row) is dict and set(row) == METRIC_FIELDS, "metric fields do not match contract")
    for field in ("project_id", "metric_id"):
        identifier(row[field], field)
    for field in ("unit", "source_ref", "source_version", "counting_basis"):
        require(type(row[field]) is str and 0 < len(row[field]) <= 1000, "invalid metric " + field)
    require(type(row["dimensions"]) is dict and len(row["dimensions"]) <= 12, "invalid dimensions")
    for key, value in row["dimensions"].items():
        require(type(key) is str and len(key) <= 80 and type(value) in (str, int, bool), "invalid dimension")
        require(len(str(value)) <= 200, "dimension too long")
    timestamp(row["observed_at"], "observed_at")
    if row["business_at"] is not None:
        timestamp(row["business_at"], "business_at")
    value = row["value"]
    require(value is None or type(value) in (int, float) and math.isfinite(value), "metric needs finite number or null")
    require(row["quality"] in {"good", "unknown", "invalid"}, "invalid metric quality")
    require((value is not None) == (row["quality"] == "good"), "invalid metric value/quality")
    require(row["reason"] is None and row["quality"] == "good" or
            type(row["reason"]) is str and 0 < len(row["reason"]) <= 500, "unknown/invalid metric needs reason")
    require(len(json.dumps(row, ensure_ascii=False).encode()) <= 6000, "metric too large for bounded detail page")
    return row


def metric_key(row):
    """No summing across units, dimensions, projects, or changed counting bases."""
    return content_hash([row[x] for x in ("project_id", "metric_id", "unit", "dimensions", "counting_basis")])


def semantic_metric(row):
    return {k: v for k, v in row.items() if k != "observed_at"}


def issue(project_id, code, source_ref, *, affected_items=None, started_at=None,
          updated_at=None, recovery_condition=None, retry_entry=None, kind="read_failure"):
    return dict(issue_id=content_hash([project_id, code, source_ref]), project_id=project_id,
                code=code, source_ref=source_ref, kind=kind, affected_items=affected_items,
                started_at=started_at, updated_at=updated_at, recovery_condition=recovery_condition,
                retry_entry=retry_entry)


def bounded_json(value, limit=MAX_SUMMARY_BYTES):
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    require(len(text.encode()) <= limit, "output exceeds byte limit; select a smaller page")
    return text


def freshness(observed_at, now, max_age_seconds=86400):
    age = (datetime.fromisoformat(now.replace("Z", "+00:00")) -
           datetime.fromisoformat(observed_at.replace("Z", "+00:00"))).total_seconds()
    return "stale" if age > max_age_seconds else "fresh"


def eligibility(project):
    if project.get("local_presence", {}).get("status") == "removed_local" or project.get("current_state_status") == "removed_local":
        return "removed_local"
    if project.get("connection_read_allowed", project.get("enabled", True)) is not True or project.get("access_profile") == "no_current_goal_access":
        return "disabled"
    return "eligible"
