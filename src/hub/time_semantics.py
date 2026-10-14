"""Literal source time contracts; derived calendar views never change evidence."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .connection_records import RecordError, exact, identifier, require, string

_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_TIMESTAMP = re.compile(
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?)"
    r"(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})\Z")
_TIMELINE = {"schema_version", "project_id", "plan_version", "actual_start", "actual_end", "milestones", "task_dates"}


def validate_calendar_date(value):
    """Accept only a nullable, literal Gregorian calendar date."""
    if value is None:
        return None
    require(type(value) is str and bool(_DATE.fullmatch(value)), "calendar date: YYYY-MM-DD required")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise RecordError("calendar date: invalid day") from None
    return value


def validate_timezone(value):
    """Validate an explicit nullable IANA key; never use the host zone."""
    if value is None:
        return None
    require(type(value) is str and 0 < len(value) <= 128
            and bool(re.fullmatch(r"[A-Za-z0-9_+./-]+", value))
            and all(part not in {"", ".", ".."} for part in value.split("/")),
            "timezone: named IANA key required")
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise RecordError("timezone: unknown IANA key") from None
    return value


def _timestamp(value):
    require(type(value) is str and len(value) <= 16000, "timestamp: aware ISO value required")
    match = _TIMESTAMP.fullmatch(value)
    require(match is not None, "timestamp: aware ISO value required")
    base, fraction, offset = match.groups()
    require(fraction is None or len(base) == 19, "timestamp: fraction requires seconds")
    if offset != "Z":
        require(int(offset[1:3]) <= 23 and int(offset[4:]) <= 59, "timestamp: invalid offset")
    try:
        instant = datetime.fromisoformat(base + ("+00:00" if offset == "Z" else offset))
        utc = instant.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise RecordError("timestamp: invalid or unrepresentable instant") from None
    return instant, utc, fraction or ""


def validate_time_point(value):
    if value is None:
        return None
    exact(value, {"kind", "value", "timezone"}, "time point")
    require(type(value["kind"]) is str and value["kind"] in {"date", "timestamp"}, "time point: invalid kind")
    validate_timezone(value["timezone"])
    if value["kind"] == "date":
        require(value["value"] is not None, "time point: date value required")
        validate_calendar_date(value["value"])
    else:
        instant, _, _ = _timestamp(value["value"])
        if value["timezone"] is not None:
            try:
                instant.astimezone(ZoneInfo(value["timezone"]))
            except (ValueError, OverflowError):
                raise RecordError("timestamp: unrepresentable named calendar") from None
    return deepcopy(value)


def validate_time_pair(start, end):
    """Reject reversed comparable ranges; preserve independently based markers."""
    start, end = validate_time_point(start), validate_time_point(end)
    if start is not None and end is not None and start["kind"] == end["kind"]:
        if start["kind"] == "timestamp":
            _, left, left_fraction = _timestamp(start["value"])
            _, right, right_fraction = _timestamp(end["value"])
            require((left, Decimal("0" + left_fraction)) <= (right, Decimal("0" + right_fraction)),
                    "time pair: reversed instant range")
        elif start["timezone"] == end["timezone"]:
            require(start["value"] <= end["value"], "time pair: reversed calendar range")
    return start, end


def _milestones(value):
    require(type(value) is list and len(value) <= 1000, "milestones: bounded list required")
    seen = set()
    for row in value:
        exact(row, {"id", "label", "at"}, "milestone")
        identifier(row["id"], "milestone identity")
        require(row["id"] not in seen, "milestone: duplicate identity")
        seen.add(row["id"])
        string(row["label"], "milestone label")
        validate_time_point(row["at"])


def validate_task_timeline(value, *, project_id=None, task_plan=None):
    """Validate current-plan references as well as literal actual-only facts."""
    exact(value, _TIMELINE, "task timeline")
    require(value["schema_version"] == "1.0", "unsupported task timeline schema")
    identifier(value["project_id"], "timeline project")
    if project_id is not None:
        identifier(project_id, "expected timeline project")
        require(value["project_id"] == project_id, "timeline project mismatch")
    if value["plan_version"] is not None:
        string(value["plan_version"], "timeline plan version")
    validate_time_pair(value["actual_start"], value["actual_end"])
    _milestones(value["milestones"])
    rows = value["task_dates"]
    require(type(rows) is list and len(rows) <= 10000, "task dates: bounded list required")
    seen = set()
    for row in rows:
        exact(row, {"task_id", "actual_start", "actual_end", "milestones"}, "task dates")
        identifier(row["task_id"], "dated task identity")
        require(row["task_id"] not in seen, "task dates: duplicate identity")
        seen.add(row["task_id"])
        validate_time_pair(row["actual_start"], row["actual_end"])
        _milestones(row["milestones"])
    if task_plan is None:
        require(value["plan_version"] is None and not rows, "timeline: current task plan required")
    else:
        from .business_tasks import validate_task_plan
        plan = validate_task_plan(task_plan)
        require(plan["project_id"] == value["project_id"], "timeline task plan project mismatch")
        require(value["plan_version"] == plan["plan_version"], "timeline current plan version mismatch")
        require(seen <= {node["id"] for node in plan["nodes"]}, "timeline: unknown current task reference")
    return deepcopy(value)


def calendar_projection(point):
    """Return a derived display basis alongside unchanged literal fields."""
    point = validate_time_point(point)
    if point is None:
        return None
    if point["kind"] == "date":
        return {**point, "calendar_date": point["value"], "axis_date": point["value"],
                "basis": "calendar_date", "instant_utc": None}
    instant, utc, fraction = _timestamp(point["value"])
    calendar = instant if point["timezone"] is None else instant.astimezone(ZoneInfo(point["timezone"]))
    # strftime does not consistently zero-pad years before 1000 across hosts.
    utc_text = f"{utc.year:04d}-{utc.month:02d}-{utc.day:02d}T{utc.hour:02d}:{utc.minute:02d}:{utc.second:02d}{fraction}Z"
    return {**point, "calendar_date": calendar.date().isoformat(), "axis_date": utc.date().isoformat(),
            "basis": "utc_instant", "instant_utc": utc_text}
