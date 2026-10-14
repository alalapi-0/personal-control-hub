"""Literal dates, explicit display zones and current business references."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hub.connection_records import RecordError
from hub.time_semantics import (calendar_projection, validate_calendar_date, validate_timezone,
                                validate_time_point, validate_time_pair, validate_task_timeline)


def point(value="2026-10-14", kind="date", zone=None):
    return {"kind": kind, "value": value, "timezone": zone}


def timeline():
    return {"schema_version": "1.0", "project_id": "business", "plan_version": None,
            "actual_start": None, "actual_end": None, "milestones": [], "task_dates": []}


def plan():
    nodes = [{"id": node_id, "kind": kind, "parent": parent, "dependencies": [],
              "status": "active", "condition": "Check current task", "evidence": []}
             for node_id, kind, parent in [("business", "project", None), ("s", "stage", "business")]]
    return {"schema_version": "1.0", "project_id": "business", "plan_version": "P1",
            "unit": "business_leaf", "counting_basis": "Current leaves", "catalog_sha256": "a" * 64,
            "input_version": "b" * 64, "nodes": nodes, "labels": {n["id"]: n["id"] for n in nodes},
            "acceptance": [], "counts": {key: 0 for key in ["total", "implemented", "verified", "human",
            "not_started", "active", "blocked", "cancelled"]}, "baseline_denominator": None,
            "history": None, "history_unknown_reason": "Not declared"}


class TimeSemanticsTests(unittest.TestCase):
    def test_calendar_literal_bounds_leap_and_nullable(self):
        for value in (None, "0001-01-01", "9999-12-31", "2024-02-29"):
            self.assertEqual(value, validate_calendar_date(value))
        for value in ("0000-01-01", "2023-02-29", "2024-2-01", "2024-01-01T00:00:00Z", "20240101", True):
            with self.subTest(value=value), self.assertRaises(RecordError):
                validate_calendar_date(value)

    def test_zone_null_named_and_path_rejection(self):
        for value in (None, "UTC", "Asia/Tokyo", "America/New_York", "Etc/GMT+9"):
            self.assertEqual(value, validate_timezone(value))
        for value in ("", "local", "/etc/localtime", "../UTC", "Asia//Tokyo", "Asia/../Tokyo", 9, "x" * 129):
            with self.subTest(value=value), self.assertRaises(RecordError):
                validate_timezone(value)

    def test_date_point_never_sourced_midnight(self):
        raw = point(zone="Asia/Tokyo")
        result = calendar_projection(raw)
        self.assertEqual("2026-10-14", result["axis_date"])
        self.assertEqual("calendar_date", result["basis"])
        self.assertIsNone(result["instant_utc"])
        self.assertEqual(raw, validate_time_point(raw))

    def test_utc_value_named_calendar_conversion_preserves_literal(self):
        raw = point("2026-10-14T18:30:00Z", "timestamp", "Asia/Tokyo")
        result = calendar_projection(raw)
        self.assertEqual("2026-10-15", result["calendar_date"])
        self.assertEqual("2026-10-14", result["axis_date"])
        self.assertEqual("utc_instant", result["basis"])
        self.assertEqual(raw["value"], result["value"])
        self.assertEqual(raw["value"], result["instant_utc"])

    def test_legacy_offset_calendar_no_host_default(self):
        raw = point("2026-10-15T01:00:00+09:00", "timestamp")
        result = calendar_projection(raw)
        self.assertEqual("2026-10-15", result["calendar_date"])
        self.assertEqual("2026-10-14", result["axis_date"])
        self.assertEqual("2026-10-14T16:00:00Z", result["instant_utc"])
        self.assertIsNone(result["timezone"])

    def test_timestamp_pair_cross_zone_utc_order(self):
        start = point("2026-10-14T10:00:00+09:00", "timestamp", "Asia/Tokyo")
        end = point("2026-10-14T02:00:00Z", "timestamp", "America/New_York")
        self.assertEqual((start, end), validate_time_pair(start, end))
        with self.assertRaises(RecordError):
            validate_time_pair(end, start)

    def test_fraction_precision_is_not_truncated(self):
        start = point("2026-10-14T00:00:00.123456789Z", "timestamp")
        end = point("2026-10-14T00:00:00.123456788Z", "timestamp")
        with self.assertRaises(RecordError):
            validate_time_pair(start, end)
        self.assertEqual(start["value"], calendar_projection(start)["instant_utc"])

    def test_single_end_and_mixed_or_different_calendars_remain_independent(self):
        end = point()
        self.assertEqual((None, end), validate_time_pair(None, end))
        early = point("2020-01-01", zone="Asia/Tokyo")
        self.assertEqual((end, early), validate_time_pair(end, early))
        instant = point("2000-01-01T00:00:00Z", "timestamp")
        self.assertEqual((end, instant), validate_time_pair(end, instant))
        with self.assertRaises(RecordError):
            validate_time_pair(end, point("2020-01-01"))

    def test_invalid_timestamp_is_not_naive_or_lenient_calendar(self):
        values = ("2026-10-14", "2026-10-14T01:00:00", "2026-10-14 01:00:00Z",
                  "2026-02-30T01:00:00Z", "2026-10-14T25:00:00Z", "2026-10-14T01:00:00+09:99",
                  "2026-10-14T01:00:00+24:00", "0001-01-01T00:00:00+09:00")
        for value in values:
            with self.subTest(value=value), self.assertRaises(RecordError):
                validate_time_point(point(value, "timestamp"))

    def test_dst_aware_instant_is_not_reinterpreted_as_wall_time(self):
        first = point("2026-11-01T01:30:00-04:00", "timestamp", "America/New_York")
        second = point("2026-11-01T01:30:00-05:00", "timestamp", "America/New_York")
        self.assertEqual((first, second), validate_time_pair(first, second))
        self.assertNotEqual(calendar_projection(first)["instant_utc"], calendar_projection(second)["instant_utc"])

    def test_point_exact_shape_and_detached(self):
        raw = point()
        validated = validate_time_point(raw)
        validated["value"] = "2020-01-01"
        self.assertEqual("2026-10-14", raw["value"])
        for bad in ({**raw, "actual": True}, {"kind": "date", "value": "2026-10-14"}, point(None), point(kind="unknown")):
            with self.assertRaises(RecordError):
                validate_time_point(bad)
        self.assertIsNone(calendar_projection(None))

    def test_project_only_nullable_timeline_and_end_only(self):
        raw = timeline()
        raw["actual_end"] = point()
        raw["milestones"] = [{"id": "delivery", "label": "Actual delivery", "at": None}]
        result = validate_task_timeline(raw, project_id="business")
        self.assertEqual(raw, result)
        result["milestones"][0]["label"] = "Detached"
        self.assertEqual("Actual delivery", raw["milestones"][0]["label"])

    def test_task_plan_current_version_stage_reference_no_count_changes(self):
        current = plan()
        before = copy.deepcopy(current)
        raw = timeline()
        raw["plan_version"] = "P1"
        raw["task_dates"] = [{"task_id": "s", "actual_start": None, "actual_end": point(), "milestones": []}]
        result = validate_task_timeline(raw, task_plan=current)
        self.assertEqual(raw, result)
        self.assertEqual(before, current)
        self.assertEqual(0, current["counts"]["verified"])

    def test_task_dates_or_version_require_current_plan(self):
        for change in ({"plan_version": "P1"}, {"task_dates": [{"task_id": "s", "actual_start": None,
                         "actual_end": None, "milestones": []}]}):
            with self.assertRaises(RecordError):
                validate_task_timeline({**timeline(), **change})

    def test_version_project_and_task_reference_rejected(self):
        raw = {**timeline(), "plan_version": "P1"}
        for bad in ({**raw, "plan_version": "old"}, {**raw, "project_id": "other"},
                    {**raw, "task_dates": [{"task_id": "gone", "actual_start": None, "actual_end": None, "milestones": []}]}):
            with self.assertRaises(RecordError):
                validate_task_timeline(bad, task_plan=plan())
        with self.assertRaises(RecordError):
            validate_task_timeline(timeline(), project_id="other")

    def test_invalid_supplied_plan_cannot_authorize_reference(self):
        current = plan()
        current["counts"]["total"] = 3
        with self.assertRaises(RecordError):
            validate_task_timeline({**timeline(), "plan_version": "P1"}, task_plan=current)

    def test_duplicate_ids_and_extra_planned_fields_rejected(self):
        milestone = {"id": "stable", "label": "Milestone", "at": None}
        task = {"task_id": "s", "actual_start": None, "actual_end": None, "milestones": []}
        for bad in ({**timeline(), "planned_start": None}, {**timeline(), "milestones": [milestone, milestone]},
                    {**timeline(), "plan_version": "P1", "task_dates": [task, task]},
                    {**timeline(), "milestones": [{**milestone, "label": ""}]}):
            with self.assertRaises(RecordError):
                validate_task_timeline(bad)

    def test_list_bounds_and_nested_pair_validation(self):
        with self.assertRaises(RecordError):
            validate_task_timeline({**timeline(), "milestones": [None] * 1001})
        with self.assertRaises(RecordError):
            validate_task_timeline({**timeline(), "task_dates": [None] * 10001})
        raw = {**timeline(), "plan_version": "P1", "task_dates": [{"task_id": "s", "actual_start": point(),
               "actual_end": point("2020-01-01"), "milestones": []}]}
        with self.assertRaises(RecordError):
            validate_task_timeline(raw, task_plan=plan())


if __name__ == "__main__":
    unittest.main()
