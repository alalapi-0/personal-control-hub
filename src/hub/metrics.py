"""Numeric project facts. No model, workflow execution, or UI dependency."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

from hub.connection_records import (
    METRIC_QUALITY_SEMANTICS,
    content_hash,
    identifier,
    require,
    timestamp,
)

VERSION = "1.0"
METRIC_CONTRACT_VERSION = "2.0"
MAX_SUMMARY_BYTES = 8192
MAX_PAGE_SIZE = 50
METRIC_FIELDS = {"project_id", "metric_id", "value", "unit", "dimensions", "observed_at",
                 "business_at", "source_ref", "source_version", "quality", "reason", "counting_basis"}
METRIC_KEY_FIELDS = ("project_id", "metric_id", "unit", "dimensions", "counting_basis")
METRIC_DEFINITION_FIELDS = {
    "definition_id", "metric_id", "unit", "dimension_keys", "counting_basis",
    "metric_kind", "window", "scope_id", "aggregation", "counter_reset",
    "display_name", "description",
}
METRIC_KINDS = {"gauge", "counter", "ratio", "duration", "timestamp"}
AGGREGATIONS = {"none", "sum", "min", "max", "latest", "weighted_ratio"}
COUNTER_RESETS = {"not_applicable", "never", "window", "source_defined"}
WINDOW_KINDS = {"instant", "lifetime", "rolling", "calendar", "event"}
AGGREGATE_RULES = (
    "scope_and_unit_compatible",
    "ratios_keep_numerator_denominator",
    "net_delta_is_not_throughput",
    "no_mean_of_percentiles",
    "unknown_not_zero",
    "cost_from_telemetry_only",
)


def metric_contract_schema():
    """Machine-readable authority for metric records and stable definitions."""
    return {
        "schema_version": METRIC_CONTRACT_VERSION,
        "record_validator": "hub.metrics.validate_metric",
        "definition_projector": "hub.metrics.metric_definition",
        "catalog_projector": "hub.metrics.metric_catalog",
        "record_fields": sorted(METRIC_FIELDS),
        "record_identity_fields": ["project_id", "metric_id"],
        "record_key_fields": list(METRIC_KEY_FIELDS),
        "record_definition_selectors": [
            "unit", "dimension keys", "dimensions.scope", "counting_basis",
        ],
        "record_dynamic_fact_fields": [
            "value", "dimension values except dimensions.scope", "observed_at",
            "business_at", "source_ref", "source_version", "quality", "reason",
        ],
        "catalog_definition_fields": sorted(METRIC_DEFINITION_FIELDS),
        "metric_kinds": sorted(METRIC_KINDS),
        "aggregations": sorted(AGGREGATIONS),
        "counter_resets": sorted(COUNTER_RESETS),
        "window_kinds": sorted(WINDOW_KINDS),
        "aggregate_rules": list(AGGREGATE_RULES),
        "quality_semantics": METRIC_QUALITY_SEMANTICS,
        "scope_rule": "dimensions.scope is an optional stable identifier; all dimension keys are identifiers",
        "catalog_rule": "one shared definition per metric_id/unit/scope_id/dimension-key/counting-basis identity; dynamic values never enter definitions",
    }


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
        identifier(key, "metric dimension key")
        require(len(str(value)) <= 200, "dimension too long")
        if key == "scope":
            identifier(value, "metric scope")
    timestamp(row["observed_at"], "observed_at")
    if row["business_at"] is not None:
        timestamp(row["business_at"], "business_at")
    value = row["value"]
    require(value is None or type(value) in (int, float) and math.isfinite(value), "metric needs finite number or null")
    require(type(row["quality"]) is str and row["quality"] in METRIC_QUALITY_SEMANTICS,
            "invalid metric quality")
    require((value is not None) == (row["quality"] == "good"), "invalid metric value/quality")
    require(row["reason"] is None and row["quality"] == "good" or
            type(row["reason"]) is str and 0 < len(row["reason"]) <= 500, "non-good metric needs reason")
    require(len(json.dumps(row, ensure_ascii=False).encode()) <= 6000, "metric too large for bounded detail page")
    return row


def metric_key(row):
    """No summing across units, dimensions, projects, or changed counting bases."""
    return content_hash([row[field] for field in METRIC_KEY_FIELDS])


def metric_definition(row, *, metric_kind="gauge", window=None, scope_id=None,
                      aggregation="none", counter_reset="not_applicable",
                      display_name=None, description=None):
    """Build one catalog definition without copying dynamic metric facts."""
    validate_metric(row)
    if scope_id is None:
        scope_id = (
            row["dimensions"].get("scope")
            if type(row["dimensions"].get("scope")) is str
            else "project"
        )
    result = {
        "metric_id": row["metric_id"],
        "unit": row["unit"],
        "dimension_keys": sorted(row["dimensions"]),
        "counting_basis": row["counting_basis"],
        "metric_kind": metric_kind,
        "window": dict(window) if type(window) is dict else window,
        "scope_id": scope_id,
        "aggregation": aggregation,
        "counter_reset": counter_reset,
        "display_name": display_name or row["metric_id"],
        "description": description or row["counting_basis"],
    }
    result["definition_id"] = content_hash(result)
    return validate_metric_definition(result)


def validate_metric_definition(value):
    require(type(value) is dict and set(value) == METRIC_DEFINITION_FIELDS,
            "metric definition fields do not match contract")
    identifier(value["metric_id"], "metric definition id")
    identifier(value["scope_id"], "scope_id")
    for field in ("unit", "counting_basis", "display_name", "description"):
        require(type(value[field]) is str and 0 < len(value[field]) <= 1000,
                "invalid metric definition " + field)
    keys = value["dimension_keys"]
    require(type(keys) is list and len(keys) <= 12
            and all(type(key) is str for key in keys)
            and keys == sorted(set(keys)),
            "invalid metric definition dimensions")
    for key in keys:
        identifier(key, "metric dimension key")
    require(type(value["metric_kind"]) is str and value["metric_kind"] in METRIC_KINDS,
            "invalid metric kind")
    require(type(value["aggregation"]) is str and value["aggregation"] in AGGREGATIONS,
            "invalid metric aggregation")
    require(type(value["counter_reset"]) is str and value["counter_reset"] in COUNTER_RESETS,
            "invalid counter reset")
    require((value["metric_kind"] == "counter") == (value["counter_reset"] != "not_applicable"),
            "counter reset only applies to counters")
    require(value["aggregation"] != "weighted_ratio" or value["metric_kind"] == "ratio",
            "weighted_ratio requires a ratio metric")
    _validate_window(value["window"])
    expected = content_hash({key: item for key, item in value.items() if key != "definition_id"})
    require(value["definition_id"] == expected, "metric definition identity mismatch")
    return value


def metric_catalog(rows):
    """Deduplicate definitions and reject conflicting shared series shapes."""
    definitions = {}
    for row in rows:
        definition = metric_definition(row)
        series = content_hash([
            definition["metric_id"],
            definition["unit"],
            definition["scope_id"],
            definition["dimension_keys"],
            definition["counting_basis"],
        ])
        require(series not in definitions or definitions[series] == definition,
                "conflicting metric definitions")
        definitions[series] = definition
    return sorted(definitions.values(), key=lambda item: item["definition_id"])


def semantic_metric(row):
    return {k: v for k, v in row.items() if k != "observed_at"}


def _validate_window(value):
    if value is None:
        return
    require(type(value) is dict and set(value) == {"kind", "timezone", "size_seconds"},
            "metric window fields do not match contract")
    require(type(value["kind"]) is str and value["kind"] in WINDOW_KINDS,
            "invalid metric window kind")
    require(type(value["timezone"]) is str and 0 < len(value["timezone"]) <= 80,
            "invalid metric window timezone")
    size = value["size_seconds"]
    require((value["kind"] == "rolling" and type(size) is int and size > 0)
            or (value["kind"] != "rolling" and size is None), "invalid metric window size")


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
