import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hub.connection_records import RecordError
from hub.review_contract import (
    FIRST_REVIEW,
    SECOND_REVIEW,
    apply_review_event,
    new_review_case,
    validate_review_case,
)


class ReviewContractTests(unittest.TestCase):
    HASH_1 = "1" * 64
    HASH_2 = "2" * 64
    HASH_3 = "3" * 64

    def case(self, *, second_review=False):
        return new_review_case(
            "project-a",
            "item-a",
            1,
            self.HASH_1,
            require_second_review=second_review,
        )

    def event(self, case, event_type, event_id, **fields):
        return {
            "event_id": event_id,
            "event_type": event_type,
            "expected_sequence": case["sequence"],
            **fields,
        }

    def submit(self, case, suffix, *, stage=None):
        return self.event(
            case,
            "submit",
            f"event-submit-{suffix}",
            submission_id=f"submission-{suffix}",
            stage=stage or case["review_stage"],
            candidate_revision=case["candidate_revision"],
            candidate_hash=case["candidate_hash"],
        )

    def decide(self, case, suffix, verdict, *, rework_task_id=None):
        submission = next(
            item
            for item in case["submissions"]
            if item["submission_id"] == case["active_submission_id"]
        )
        return self.event(
            case,
            "decide",
            f"event-decide-{suffix}",
            decision_id=f"decision-{suffix}",
            submission_id=submission["submission_id"],
            stage=submission["stage"],
            candidate_revision=submission["candidate_revision"],
            candidate_hash=submission["candidate_hash"],
            verdict=verdict,
            rework_task_id=rework_task_id,
        )

    def claim(self, case, task_id):
        return self.event(
            case,
            "claim_rework",
            f"event-claim-{task_id}",
            rework_task_id=task_id,
        )

    def complete(self, case, task_id, revision, candidate_hash):
        return self.event(
            case,
            "complete_rework",
            f"event-complete-{task_id}",
            rework_task_id=task_id,
            new_candidate_revision=revision,
            new_candidate_hash=candidate_hash,
        )

    def test_first_rejection_requires_claim_new_version_and_resubmission(self):
        case = self.case()
        initial_submission = self.submit(case, "v1-first")
        case = apply_review_event(case, initial_submission)
        case = apply_review_event(
            case,
            self.decide(case, "v1-reject", "reject", rework_task_id="rework-v1"),
        )
        self.assertEqual(case["review_state"], "pending_rework")
        with self.assertRaises(RecordError):
            apply_review_event(
                case, self.complete(case, "rework-v1", 2, self.HASH_2)
            )

        case = apply_review_event(case, self.claim(case, "rework-v1"))
        case = apply_review_event(
            case, self.complete(case, "rework-v1", 2, self.HASH_2)
        )
        self.assertEqual(
            (case["review_state"], case["review_stage"]),
            ("awaiting_submission", FIRST_REVIEW),
        )
        self.assertEqual(case["rework_count"], 1)

        late_approval = {
            **self.event(case, "decide", "event-late-approval"),
            "decision_id": "decision-late-approval",
            "submission_id": "submission-v1-first",
            "stage": FIRST_REVIEW,
            "candidate_revision": 1,
            "candidate_hash": self.HASH_1,
            "verdict": "approve",
            "rework_task_id": None,
        }
        with self.assertRaises(RecordError):
            apply_review_event(case, late_approval)

        case = apply_review_event(case, self.submit(case, "v2-first"))
        case = apply_review_event(case, self.decide(case, "v2-approve", "approve"))
        self.assertEqual(case["review_state"], "awaiting_acceptance")
        self.assertIsNone(case["review_stage"])
        self.assertEqual(case["candidate_revision"], 2)
        self.assertEqual(case["submission_attempts"][FIRST_REVIEW], 2)
        validate_review_case(case)

    def test_second_review_only_follows_current_version_first_approval(self):
        case = self.case(second_review=True)
        case = apply_review_event(case, self.submit(case, "v1-first"))
        case = apply_review_event(case, self.decide(case, "v1-first", "approve"))
        self.assertEqual(
            (case["review_state"], case["review_stage"]),
            ("awaiting_submission", SECOND_REVIEW),
        )
        case = apply_review_event(case, self.submit(case, "v1-second"))
        case = apply_review_event(
            case,
            self.decide(case, "v1-second", "reject", rework_task_id="rework-v1"),
        )
        case = apply_review_event(case, self.claim(case, "rework-v1"))
        case = apply_review_event(
            case, self.complete(case, "rework-v1", 2, self.HASH_2)
        )

        with self.assertRaises(RecordError):
            apply_review_event(
                case, self.submit(case, "v2-second-early", stage=SECOND_REVIEW)
            )
        case = apply_review_event(case, self.submit(case, "v2-first"))
        case = apply_review_event(case, self.decide(case, "v2-first", "approve"))
        case = apply_review_event(case, self.submit(case, "v2-second"))
        case = apply_review_event(case, self.decide(case, "v2-second", "approve"))
        self.assertEqual(case["review_state"], "awaiting_acceptance")
        self.assertEqual(case["submission_attempts"], {FIRST_REVIEW: 2, SECOND_REVIEW: 2})

    def test_duplicate_is_idempotent_but_id_collision_and_concurrency_conflict(self):
        case = self.case()
        first = self.submit(case, "winner")
        competing = self.submit(case, "competing")
        updated = apply_review_event(case, first)
        self.assertEqual(apply_review_event(updated, first), updated)
        with self.assertRaisesRegex(RecordError, "stale expected_sequence"):
            apply_review_event(updated, competing)

        collision = dict(first)
        collision["submission_id"] = "submission-other"
        with self.assertRaisesRegex(RecordError, "reused with different content"):
            apply_review_event(updated, collision)

    def test_rework_must_advance_exactly_one_changed_candidate(self):
        case = self.case()
        case = apply_review_event(case, self.submit(case, "v1"))
        case = apply_review_event(
            case,
            self.decide(case, "v1", "reject", rework_task_id="rework-v1"),
        )
        case = apply_review_event(case, self.claim(case, "rework-v1"))
        with self.assertRaises(RecordError):
            apply_review_event(
                case, self.complete(case, "rework-v1", 1, self.HASH_2)
            )
        with self.assertRaises(RecordError):
            apply_review_event(
                case, self.complete(case, "rework-v1", 2, self.HASH_1)
            )
        with self.assertRaises(RecordError):
            apply_review_event(
                case, self.complete(case, "rework-v1", 3, self.HASH_3)
            )

    def test_stored_state_rejects_tampered_candidate_and_history(self):
        case = self.case()
        case = apply_review_event(case, self.submit(case, "v1"))
        case = apply_review_event(case, self.decide(case, "v1", "approve"))
        tampered = copy.deepcopy(case)
        tampered["candidate_hash"] = self.HASH_2
        with self.assertRaises(RecordError):
            validate_review_case(tampered)
        tampered = copy.deepcopy(case)
        tampered["decisions"][0]["candidate_revision"] = 2
        with self.assertRaisesRegex(RecordError, "event replay"):
            validate_review_case(tampered)

    def test_case_configuration_and_operation_history_are_replay_bound(self):
        case = self.case(second_review=True)
        tampered = copy.deepcopy(case)
        tampered["required_review_stages"] = [FIRST_REVIEW]
        with self.assertRaisesRegex(RecordError, "case_binding"):
            validate_review_case(tampered)

        case = apply_review_event(case, self.submit(case, "v1-first"))
        tampered = copy.deepcopy(case)
        tampered["submissions"][0]["submission_id"] = "submission-rewritten"
        tampered["active_submission_id"] = "submission-rewritten"
        with self.assertRaisesRegex(RecordError, "event replay"):
            validate_review_case(tampered)

    def test_second_review_cannot_survive_loss_of_current_first_approval(self):
        case = self.case(second_review=True)
        case = apply_review_event(case, self.submit(case, "v1-first"))
        case = apply_review_event(case, self.decide(case, "v1-first", "approve"))
        case = apply_review_event(case, self.submit(case, "v1-second"))
        tampered = copy.deepcopy(case)
        tampered["decisions"][0]["verdict"] = "reject"
        tampered["decisions"][0]["rework_task_id"] = "forged-rework"
        with self.assertRaises(RecordError):
            validate_review_case(tampered)


if __name__ == "__main__":
    unittest.main()
