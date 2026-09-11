"""Deterministic Hub aggregates. Net observation deltas are never throughput."""
from __future__ import annotations

from datetime import datetime

from hub.connection_records import require
from hub.metrics import AGGREGATE_RULES, METRIC_KEY_FIELDS, metric_definition, metric_key
THROUGHPUT_REASON = "Net observation delta is not throughput."
COST_UNITS = frozenset({"currency", "usd", "cny", "credits", "cost"})
PERCENTILE_MARKS = ("p50", "p90", "p95", "p99", "percentile")
RATIO_PARTS = frozenset({"numerator", "denominator"})


def _instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def observation_change(current, prior, *, changed_at):
    """Same-key net observation change. Division by elapsed time is forbidden."""
    delta = None
    interval = None
    if prior is not None and prior.get("value") is not None and current.get("value") is not None:
        if [current[field] for field in METRIC_KEY_FIELDS] == [prior[field] for field in METRIC_KEY_FIELDS]:
            delta = current["value"] - prior["value"]
            interval = (_instant(changed_at) - _instant(prior["observed_at"])).total_seconds()
            if interval <= 0:
                interval = None
    return {
        "delta": delta,
        "change_kind": "net_delta" if delta is not None else None,
        "change_per_second": None,
        "observation_interval_seconds": interval,
        "throughput": None,
        "throughput_claimed": False,
        "throughput_reason": THROUGHPUT_REASON,
    }


def _tokens(row):
    return (row["metric_id"].casefold() + " " + row["unit"].casefold()).replace(".", " ").replace("_", " ").replace("-", " ")


def _is_cost(row):
    text = _tokens(row)
    return row["unit"].casefold() in COST_UNITS or "cost" in text.split()


def _is_percentile(row, definition):
    text = _tokens(row)
    if any(mark in text.split() or mark in row["unit"].casefold() for mark in PERCENTILE_MARKS):
        return True
    return definition["aggregation"] == "none" and any(mark in definition["display_name"].casefold() for mark in PERCENTILE_MARKS)


def _definition_for(row, catalog):
    if catalog:
        keys = sorted(row["dimensions"])
        scope = row["dimensions"]["scope"] if type(row["dimensions"].get("scope")) is str else "project"
        for item in catalog:
            if (item["metric_id"] == row["metric_id"] and item["unit"] == row["unit"]
                    and item["counting_basis"] == row["counting_basis"]
                    and item["dimension_keys"] == keys and item["scope_id"] == scope):
                return item
    return metric_definition(row)


def _compatible(rows):
    first = rows[0]
    return all(
        row["metric_id"] == first["metric_id"]
        and row["unit"] == first["unit"]
        and row["counting_basis"] == first["counting_basis"]
        and row["dimensions"] == first["dimensions"]
        for row in rows
    )


def _ratio_parts(row):
    return RATIO_PARTS <= set(row["dimensions"])


def aggregate_current(rows, catalogs=None):
    """Group current observations. Different units, ratios, percentiles and costs stay isolated."""
    require(type(rows) is list, "aggregate rows must be a list")
    catalogs = catalogs or {}
    buckets = {}
    for row in rows:
        key = metric_key(row)
        buckets.setdefault(key, []).append(row)
    groups = []
    rejected = []
    for key, members in sorted(buckets.items()):
        sample = members[0]
        catalog = catalogs.get(sample["project_id"], [])
        definition = _definition_for(sample, catalog)
        quality_rows = [row for row in members if row["quality"] == "good"]
        unknown_rows = [row for row in members if row["quality"] != "good"]
        reason = None
        value = None
        quality = "unknown"
        if not _compatible(members):
            rejected.append({"rule": "scope_and_unit_compatible", "metric_id": sample["metric_id"]})
            continue
        if definition["metric_kind"] == "ratio" or sample["unit"] in {"ratio", "percent"}:
            if not _ratio_parts(sample):
                reason = "Ratio observations keep numerator and denominator; they are not averaged."
                rejected.append({"rule": "ratios_keep_numerator_denominator", "metric_id": sample["metric_id"]})
            elif unknown_rows:
                reason = "Unknown ratio parts stay unknown and are not rewritten to zero."
            else:
                value = sample["value"] if len(quality_rows) == 1 else None
                reason = None if value is not None else "Multiple ratio observations are not combined."
                quality = "good" if value is not None else "unknown"
        elif _is_percentile(sample, definition):
            if definition["aggregation"] in {"sum", "weighted_ratio"} or len(quality_rows) > 1:
                reason = "Percentile observations are not averaged or summed."
                rejected.append({"rule": "no_mean_of_percentiles", "metric_id": sample["metric_id"]})
            elif quality_rows:
                value = quality_rows[0]["value"]
                quality = "good"
            else:
                reason = "Percentile observation is unknown and is not rewritten to zero."
        elif _is_cost(sample):
            if unknown_rows and not quality_rows:
                reason = "Cost stays unknown without telemetry; it is not estimated."
                rejected.append({"rule": "cost_from_telemetry_only", "metric_id": sample["metric_id"]})
            elif unknown_rows:
                reason = "Cost stays unknown without complete telemetry; missing values are not zero."
            elif definition["aggregation"] == "sum":
                value = sum(row["value"] for row in quality_rows)
                quality = "good"
            elif len(quality_rows) == 1:
                value = quality_rows[0]["value"]
                quality = "good"
            else:
                reason = "Cost series with aggregation=none are not combined."
        elif unknown_rows:
            reason = "Unknown observations stay unknown and are not rewritten to zero."
        elif definition["aggregation"] == "sum":
            value = sum(row["value"] for row in quality_rows)
            quality = "good"
        elif definition["aggregation"] == "min":
            value = min(row["value"] for row in quality_rows)
            quality = "good"
        elif definition["aggregation"] == "max":
            value = max(row["value"] for row in quality_rows)
            quality = "good"
        elif len(quality_rows) == 1:
            value = quality_rows[0]["value"]
            quality = "good"
        else:
            reason = "Series with aggregation=none are not combined across observations."
        groups.append({
            "project_id": sample["project_id"] if len({row["project_id"] for row in members}) == 1 else None,
            "metric_id": sample["metric_id"],
            "unit": sample["unit"],
            "dimensions": sample["dimensions"],
            "counting_basis": sample["counting_basis"],
            "metric_kind": definition["metric_kind"],
            "aggregation": definition["aggregation"],
            "window": definition["window"],
            "value": value,
            "quality": quality,
            "reason": reason,
            "sample_count": len(members),
            "unknown_count": len(unknown_rows),
            "net_delta": None,
            "throughput": None,
            "throughput_claimed": False,
            "throughput_reason": THROUGHPUT_REASON,
        })
    return {
        "kind": "metric_aggregate_page",
        "rules": list(AGGREGATE_RULES),
        "groups": groups,
        "rejected": rejected,
    }
