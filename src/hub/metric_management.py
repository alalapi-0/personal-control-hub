"""Hub management config and separate review-axis queries. No charts or UI."""
from __future__ import annotations

from pathlib import Path

from hub.connection_records import RecordError, content_hash, exact, identifier, require
from hub.metrics import MAX_PAGE_SIZE, bounded_json
from hub.review_contract import REVIEW_STAGES, validate_review_case

CONFIG_RELATIVE = "data/connections/management_config.yaml"
REVIEW_AXES = ("review_stage", "rework_count", "submission_attempt", "candidate_revision")
REVIEW_VIEWS = ("current", "history")
DIMENSIONS = (
    "identity_and_goal", "scope_and_progress", "workflow", "artifacts",
    "time_and_backlog", "window_flow", "quality_and_rework", "issues_and_dependencies",
    "development_and_delivery", "operation", "resource_and_cost", "data_quality",
)
CONFIG_FIELDS = {
    "schema_version", "kind", "write_back_allowed", "freshness", "axes",
    "dimensions", "watch", "projects",
}
AXIS_FIELDS = {"display_name", "unit"}
WATCH_FIELDS = {"id", "display_name", "metric_id", "threshold", "threshold_reason", "recovery"}
FRESHNESS_FIELDS = {"max_age_seconds", "labels"}


def management_contract_schema():
    return {
        "schema_version": "1.0",
        "config_path": CONFIG_RELATIVE,
        "config_validator": "hub.metric_management.validate_management_config",
        "review_query": "hub.metric_management.query_review_records",
        "axes": list(REVIEW_AXES),
        "views": list(REVIEW_VIEWS),
        "dimensions": list(DIMENSIONS),
        "history_rule": "current and historical candidates are queried separately; missing history is an empty page",
        "write_back_allowed": False,
        "ui_rule": "machine-readable pages only; no chart or UI implementation",
    }


def load_management_config(root):
    from hub.metric_sources import read_structured

    data, metadata = read_structured(Path(root), CONFIG_RELATIVE)
    return validate_management_config(data), metadata


def validate_management_config(value):
    exact(value, CONFIG_FIELDS, "management config")
    require(value["schema_version"] == "1.0" and value["kind"] == "hub_management_config",
            "unsupported management config")
    require(value["write_back_allowed"] is False, "management config write-back is disabled")
    freshness = value["freshness"]
    exact(freshness, FRESHNESS_FIELDS, "management freshness")
    require(type(freshness["max_age_seconds"]) is int and 60 <= freshness["max_age_seconds"] <= 604800,
            "invalid management freshness")
    exact(freshness["labels"], {"fresh", "stale", "unknown"}, "management freshness labels")
    for label in freshness["labels"].values():
        require(type(label) is str and 0 < len(label) <= 40, "invalid freshness label")
    axes = value["axes"]
    require(type(axes) is dict and set(axes) == set(REVIEW_AXES), "management axes must be the four review axes")
    for name, axis in axes.items():
        exact(axis, AXIS_FIELDS, "management axis")
        require(type(axis["display_name"]) is str and 0 < len(axis["display_name"]) <= 40, "invalid axis name")
        require(type(axis["unit"]) is str and 0 < len(axis["unit"]) <= 40, "invalid axis unit")
    dimensions = value["dimensions"]
    require(type(dimensions) is dict and list(dimensions) == list(DIMENSIONS),
            "management dimensions must follow the shared catalog order")
    for name in DIMENSIONS:
        require(type(dimensions[name]) is str and 0 < len(dimensions[name]) <= 40, "invalid dimension label")
    watch = value["watch"]
    require(type(watch) is list and 1 <= len(watch) <= 32, "management watch list bounds")
    seen = set()
    for item in watch:
        exact(item, WATCH_FIELDS, "management watch")
        identifier(item["id"], "watch")
        require(item["id"] not in seen, "duplicate watch id")
        seen.add(item["id"])
        for field in ("display_name", "metric_id", "threshold_reason", "recovery"):
            require(type(item[field]) is str and 0 < len(item[field]) <= 200, "invalid watch " + field)
        threshold = item["threshold"]
        if threshold is not None:
            exact(threshold, {"kind", "value"}, "watch threshold")
            require(threshold["kind"] == "max_age_seconds" and type(threshold["value"]) is int
                    and threshold["value"] > 0, "unsupported watch threshold")
    require(type(value["projects"]) is dict and value["projects"] == {},
            "project overlays stay empty until an explicit source is registered")
    return value


def query_review_records(cases, *, axis, value=None, view="current", after=0, limit=10, project_id=None):
    require(axis in REVIEW_AXES, "review axis must be one of review_stage, rework_count, submission_attempt, candidate_revision")
    require(view in REVIEW_VIEWS, "review view must be current or history")
    require(type(limit) is int and 1 <= limit <= MAX_PAGE_SIZE and after >= 0, "invalid page bounds")
    if project_id is not None:
        identifier(project_id, "project")
    rows = []
    for case in cases:
        validate_review_case(case)
        if project_id is not None and case["project_id"] != project_id:
            continue
        rows.extend(_rows_for_view(case, view))
    selected = [row for row in rows if value is None or row[axis] == _axis_value(axis, value)]
    page = []
    for row in selected[after:]:
        candidate = _page(axis, view, page + [row], selected, after)
        try:
            bounded_json(candidate)
        except RecordError:
            break
        page.append(row)
    return _page(axis, view, page, selected, after)


def _axis_value(axis, value):
    if axis in {"rework_count", "submission_attempt", "candidate_revision"}:
        require(type(value) is int or (type(value) is str and value.isdigit()), "review axis value must be an integer")
        number = int(value)
        require(number >= 0, "review axis value must be nonnegative")
        return number
    require(type(value) is str and (value in REVIEW_STAGES or value == "none"), "invalid review_stage filter")
    return None if value == "none" else value


def _rows_for_view(case, view):
    current = case["candidate_revision"]
    if view == "current":
        stage = case["review_stage"]
        attempt = len([
            item for item in case["submissions"]
            if item["candidate_revision"] == current
            and (stage is None or item["stage"] == stage)
        ])
        return [{
            "project_id": case["project_id"],
            "item_id": case["item_id"],
            "view": "current",
            "review_stage": stage,
            "rework_count": case["rework_count"],
            "submission_attempt": attempt,
            "candidate_revision": current,
        }]
    rows = []
    for submission in case["submissions"]:
        if submission["candidate_revision"] == current:
            continue
        rows.append({
            "project_id": case["project_id"],
            "item_id": case["item_id"],
            "view": "history",
            "review_stage": submission["stage"],
            "rework_count": None,
            "submission_attempt": submission["attempt"],
            "candidate_revision": submission["candidate_revision"],
        })
    return rows


def _page(axis, view, items, selected, after):
    end = after + len(items)
    return {
        "kind": "review_axis_page",
        "axis": axis,
        "view": view,
        "items": items,
        "returned": len(items),
        "total_remaining": len(selected) - after,
        "next_cursor": end if end < len(selected) else None,
        "coverage": {
            "matched": len(selected),
            "returned": len(items),
            "omitted": max(0, len(selected) - after - len(items)),
            "history_status": "empty" if view == "history" and not selected else view,
        },
    }


def management_summary(config, *, after=0, limit=10):
    require(type(limit) is int and 1 <= limit <= MAX_PAGE_SIZE and after >= 0, "invalid page bounds")
    names = list(config["dimensions"])
    page = names[after:after + limit]
    end = after + len(page)
    return {
        "kind": "management_config_page",
        "schema_version": config["schema_version"],
        "config_hash": content_hash(config),
        "write_back_allowed": False,
        "freshness": config["freshness"],
        "axes": config["axes"],
        "watch": config["watch"],
        "dimensions": {name: config["dimensions"][name] for name in page},
        "returned": len(page),
        "total_remaining": len(names) - after,
        "next_cursor": end if end < len(names) else None,
        "coverage": {
            "axes": len(config["axes"]),
            "dimensions": len(names),
            "watch": len(config["watch"]),
            "projects": 0,
        },
    }
