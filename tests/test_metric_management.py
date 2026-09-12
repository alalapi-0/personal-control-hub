import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from hub.connection_records import RecordError, record_schema
from hub.metric_cli import main
from hub.metric_management import (
    DIMENSIONS,
    REVIEW_AXES,
    load_management_config,
    management_summary,
    query_review_records,
    validate_management_config,
)
from hub.metrics import bounded_json
from hub.review_contract import FIRST_REVIEW, apply_review_event, new_review_case
from hub.paths import PROJECT_ROOT


class MetricManagementTests(unittest.TestCase):
    HASH_1 = "1" * 64
    HASH_2 = "2" * 64

    def case(self):
        case = new_review_case("project-a", "item-a", 1, self.HASH_1)
        case = apply_review_event(case, {
            "event_id": "submit-1", "event_type": "submit", "expected_sequence": 0,
            "submission_id": "sub-1", "stage": FIRST_REVIEW,
            "candidate_revision": 1, "candidate_hash": self.HASH_1,
        })
        case = apply_review_event(case, {
            "event_id": "reject-1", "event_type": "decide", "expected_sequence": 1,
            "decision_id": "dec-1", "submission_id": "sub-1", "stage": FIRST_REVIEW,
            "candidate_revision": 1, "candidate_hash": self.HASH_1,
            "verdict": "reject", "rework_task_id": "rework-1",
        })
        case = apply_review_event(case, {
            "event_id": "claim-1", "event_type": "claim_rework", "expected_sequence": 2,
            "rework_task_id": "rework-1",
        })
        return apply_review_event(case, {
            "event_id": "done-1", "event_type": "complete_rework", "expected_sequence": 3,
            "rework_task_id": "rework-1",
            "new_candidate_revision": 2, "new_candidate_hash": self.HASH_2,
        })

    def test_shared_config_has_chinese_labels_and_no_write_back(self):
        config, _ = load_management_config(PROJECT_ROOT)
        self.assertEqual(list(config["axes"]), list(REVIEW_AXES))
        self.assertEqual(config["axes"]["review_stage"]["display_name"], "审核阶段")
        self.assertEqual(list(config["dimensions"]), list(DIMENSIONS))
        self.assertFalse(config["write_back_allowed"])
        self.assertIsNone(config["watch"][0]["threshold"])
        page = management_summary(config, after=10, limit=4)
        self.assertEqual(page["coverage"]["dimensions"], 12)
        self.assertEqual(page["returned"], 2)
        self.assertEqual(page["coverage"]["projects"], 0)
        self.assertLessEqual(len(bounded_json(page).encode()), 8192)
        broken = dict(config, write_back_allowed=True)
        with self.assertRaises(RecordError):
            validate_management_config(broken)

    def test_current_and_history_candidates_are_not_mixed(self):
        cases = [self.case()]
        current = query_review_records(cases, axis="candidate_revision", view="current")
        history = query_review_records(cases, axis="candidate_revision", view="history")
        self.assertEqual(current["items"][0]["candidate_revision"], 2)
        self.assertEqual(current["items"][0]["rework_count"], 1)
        self.assertEqual(current["coverage"]["matched"], 1)
        self.assertEqual([row["candidate_revision"] for row in history["items"]], [1])
        self.assertIsNone(history["items"][0]["rework_count"])
        self.assertNotEqual(current["items"][0]["candidate_revision"], history["items"][0]["candidate_revision"])

    def test_axes_are_queried_separately(self):
        cases = [self.case()]
        stage = query_review_records(cases, axis="review_stage", value="first_review", view="current")
        rework = query_review_records(cases, axis="rework_count", value=1, view="current")
        missing = query_review_records(cases, axis="rework_count", value=0, view="current")
        revision = query_review_records(cases, axis="candidate_revision", value=2, view="current")
        attempt = query_review_records(cases, axis="submission_attempt", value=1, view="history")
        self.assertEqual(stage["coverage"]["matched"], 1)
        self.assertEqual(rework["items"][0]["rework_count"], 1)
        self.assertEqual(missing["coverage"]["matched"], 0)
        self.assertEqual(missing["items"], [])
        self.assertEqual(revision["items"][0]["candidate_revision"], 2)
        self.assertEqual(attempt["items"][0]["submission_attempt"], 1)

    def test_missing_history_is_explicitly_empty(self):
        fresh = new_review_case("project-a", "item-b", 1, self.HASH_1)
        page = query_review_records([fresh], axis="review_stage", view="history")
        self.assertEqual(page["items"], [])
        self.assertEqual(page["coverage"]["matched"], 0)
        self.assertEqual(page["coverage"]["history_status"], "empty")
        self.assertEqual(record_schema()["management_contract"]["history_rule"].count("empty"), 1)

    def test_cli_management_and_empty_review_store_stay_bounded(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--root", str(PROJECT_ROOT), "management"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["kind"], "management_config_page")
        self.assertEqual(payload["coverage"]["axes"], 4)
        self.assertLessEqual(len(output.getvalue().encode()), 8192)
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--root", str(PROJECT_ROOT), "reviews", "--axis", "review_stage", "--view", "history"])
        self.assertEqual(code, 0)
        empty = json.loads(output.getvalue())
        self.assertEqual(empty["items"], [])
        self.assertEqual(empty["coverage"]["history_status"], "empty")
        self.assertEqual(empty["coverage"]["matched"], 0)


if __name__ == "__main__":
    unittest.main()
