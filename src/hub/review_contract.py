"""Pure, version-bound review and rework state transitions."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping

from hub.connection_records import RecordError, fingerprint, identifier

REVIEW_SCHEMA_VERSION = "review-flow.v1"
FIRST_REVIEW = "first_review"
SECOND_REVIEW = "second_review"
REVIEW_STAGES = (FIRST_REVIEW, SECOND_REVIEW)
REQUIRED_STAGE_SETS = ((FIRST_REVIEW,), (FIRST_REVIEW, SECOND_REVIEW))
REVIEW_STATES = (
    "awaiting_submission",
    "under_review",
    "pending_rework",
    "rework_in_progress",
    "awaiting_acceptance",
)
REVIEW_EVENT_TYPES = ("submit", "decide", "claim_rework", "complete_rework")
REVIEW_VERDICTS = ("approve", "reject")
MAX_EVENTS = 10_000

_CASE_FIELDS = {
    "schema_version",
    "project_id",
    "item_id",
    "case_binding",
    "required_review_stages",
    "review_stage",
    "review_state",
    "initial_candidate_revision",
    "initial_candidate_hash",
    "candidate_revision",
    "candidate_hash",
    "submission_attempts",
    "rework_count",
    "active_submission_id",
    "active_rework_task_id",
    "submissions",
    "decisions",
    "reworks",
    "events",
    "sequence",
}
_COMMON_EVENT_FIELDS = {"event_id", "event_type", "expected_sequence"}
_EVENT_FIELDS = {
    "submit": _COMMON_EVENT_FIELDS
    | {"submission_id", "stage", "candidate_revision", "candidate_hash"},
    "decide": _COMMON_EVENT_FIELDS
    | {
        "decision_id",
        "submission_id",
        "stage",
        "candidate_revision",
        "candidate_hash",
        "verdict",
        "rework_task_id",
    },
    "claim_rework": _COMMON_EVENT_FIELDS | {"rework_task_id"},
    "complete_rework": _COMMON_EVENT_FIELDS
    | {"rework_task_id", "new_candidate_revision", "new_candidate_hash"},
}


def review_contract_schema() -> dict[str, Any]:
    """Return the executable contract advertised by the Hub schema."""
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "case_factory": "hub.review_contract.new_review_case",
        "event_reducer": "hub.review_contract.apply_review_event",
        "record_validator": "hub.review_contract.validate_review_case",
        "required_review_stage_sets": [list(value) for value in REQUIRED_STAGE_SETS],
        "states": list(REVIEW_STATES),
        "event_types": list(REVIEW_EVENT_TYPES),
        "verdicts": list(REVIEW_VERDICTS),
        "version_binding": {
            "case_fields": [
                "project_id",
                "item_id",
                "required_review_stages",
                "initial_candidate_revision",
                "initial_candidate_hash",
            ],
            "submission_fields": ["candidate_revision", "candidate_hash"],
            "decision_fields": [
                "submission_id",
                "candidate_revision",
                "candidate_hash",
            ],
            "history_binding": "ordered_full_event_replay",
            "rework_restart_stage": FIRST_REVIEW,
            "acceptance_state": "awaiting_acceptance",
        },
        "concurrency": {
            "event_identity_field": "event_id",
            "compare_and_swap_field": "expected_sequence",
            "duplicate_policy": "same_event_is_idempotent",
            "conflict_policy": "reject",
        },
    }


def new_review_case(
    project_id: str,
    item_id: str,
    candidate_revision: int,
    candidate_hash: str,
    *,
    require_second_review: bool = False,
) -> dict[str, Any]:
    """Create a review case for one immutable candidate version."""
    stages = [FIRST_REVIEW]
    if require_second_review:
        stages.append(SECOND_REVIEW)
    case = _new_case(
        project_id, item_id, stages, candidate_revision, candidate_hash
    )
    validate_review_case(case)
    return case


def apply_review_event(
    case: Mapping[str, Any], event: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply one idempotent optimistic-concurrency transition."""
    current = copy.deepcopy(dict(case))
    validate_review_case(current)
    normalized = _validate_event(event)
    prior = next(
        (
            item
            for item in current["events"]
            if item["event_id"] == normalized["event_id"]
        ),
        None,
    )
    if prior is not None:
        if prior != normalized:
            raise RecordError(
                f"event_id {normalized['event_id']!r} was reused with different content"
            )
        return current
    if normalized["expected_sequence"] != current["sequence"]:
        raise RecordError(
            "stale expected_sequence: "
            f"expected {current['sequence']}, got {normalized['expected_sequence']}"
        )
    _apply_unchecked(current, normalized)
    validate_review_case(current)
    return current


def validate_review_case(case: Mapping[str, Any]) -> None:
    """Replay stored events and reject any state or history drift."""
    if not isinstance(case, Mapping) or set(case) != _CASE_FIELDS:
        raise RecordError("review case has missing or unknown fields")
    value = copy.deepcopy(dict(case))
    if value["schema_version"] != REVIEW_SCHEMA_VERSION:
        raise RecordError("unsupported review schema_version")
    project_id = _identifier(value["project_id"], "project_id")
    item_id = _identifier(value["item_id"], "item_id")
    stages = value["required_review_stages"]
    if not isinstance(stages, list) or tuple(stages) not in REQUIRED_STAGE_SETS:
        raise RecordError("required_review_stages must be first review, optionally second")
    initial_revision = _positive_int(
        value["initial_candidate_revision"], "initial_candidate_revision"
    )
    initial_hash = _fingerprint(
        value["initial_candidate_hash"], "initial_candidate_hash"
    )
    _fingerprint(value["case_binding"], "case_binding")
    if value["case_binding"] != _case_binding(
        project_id, item_id, stages, initial_revision, initial_hash
    ):
        raise RecordError("case configuration does not match case_binding")
    events = value["events"]
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise RecordError("events must be a bounded list")

    expected = _new_case(
        project_id, item_id, stages, initial_revision, initial_hash
    )
    seen: set[str] = set()
    for raw_event in events:
        event = _validate_event(raw_event)
        event_id = event["event_id"]
        if event_id in seen:
            raise RecordError("stored events contain a duplicate event_id")
        if event["expected_sequence"] != expected["sequence"]:
            raise RecordError("stored event sequence is not contiguous")
        seen.add(event_id)
        _apply_unchecked(expected, event)
    if expected != value:
        raise RecordError("stored review state does not match event replay")


def _new_case(
    project_id: str,
    item_id: str,
    stages: list[str],
    candidate_revision: int,
    candidate_hash: str,
) -> dict[str, Any]:
    project_id = _identifier(project_id, "project_id")
    item_id = _identifier(item_id, "item_id")
    revision = _positive_int(candidate_revision, "candidate_revision")
    content_hash = _fingerprint(candidate_hash, "candidate_hash")
    configured_stages = list(stages)
    case = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "project_id": project_id,
        "item_id": item_id,
        "case_binding": "",
        "required_review_stages": configured_stages,
        "review_stage": FIRST_REVIEW,
        "review_state": "awaiting_submission",
        "initial_candidate_revision": revision,
        "initial_candidate_hash": content_hash,
        "candidate_revision": revision,
        "candidate_hash": content_hash,
        "submission_attempts": {FIRST_REVIEW: 0, SECOND_REVIEW: 0},
        "rework_count": 0,
        "active_submission_id": None,
        "active_rework_task_id": None,
        "submissions": [],
        "decisions": [],
        "reworks": [],
        "events": [],
        "sequence": 0,
    }
    case["case_binding"] = _case_binding(
        project_id, item_id, configured_stages, revision, content_hash
    )
    return case


def _apply_unchecked(case: dict[str, Any], event: dict[str, Any]) -> None:
    sequence = case["sequence"] + 1
    event_type = event["event_type"]
    if event_type == "submit":
        _submit(case, event, sequence)
    elif event_type == "decide":
        _decide(case, event, sequence)
    elif event_type == "claim_rework":
        _claim_rework(case, event, sequence)
    else:
        _complete_rework(case, event, sequence)
    case["sequence"] = sequence
    case["events"].append(copy.deepcopy(event))


def _submit(case: dict[str, Any], event: dict[str, Any], sequence: int) -> None:
    if case["review_state"] != "awaiting_submission":
        raise RecordError("submission is not allowed in the current review_state")
    if event["stage"] != case["review_stage"]:
        raise RecordError("submission stage does not match the active review_stage")
    _require_current_candidate(case, event)
    if any(
        item["submission_id"] == event["submission_id"] for item in case["submissions"]
    ):
        raise RecordError("submission_id already exists")
    stage = event["stage"]
    case["submission_attempts"][stage] += 1
    case["submissions"].append(
        {
            "submission_id": event["submission_id"],
            "stage": stage,
            "attempt": case["submission_attempts"][stage],
            "candidate_revision": event["candidate_revision"],
            "candidate_hash": event["candidate_hash"],
            "sequence": sequence,
        }
    )
    case["review_state"] = "under_review"
    case["active_submission_id"] = event["submission_id"]


def _decide(case: dict[str, Any], event: dict[str, Any], sequence: int) -> None:
    if (
        case["review_state"] != "under_review"
        or event["submission_id"] != case["active_submission_id"]
    ):
        raise RecordError("decision is late or targets a non-active submission")
    submission = next(
        item
        for item in case["submissions"]
        if item["submission_id"] == case["active_submission_id"]
    )
    for field in ("stage", "candidate_revision", "candidate_hash"):
        if event[field] != submission[field]:
            raise RecordError(f"decision {field} does not match its submission")
    if any(item["decision_id"] == event["decision_id"] for item in case["decisions"]):
        raise RecordError("decision_id already exists")
    verdict = event["verdict"]
    task_id = event["rework_task_id"]
    if verdict == "reject":
        _identifier(task_id, "rework_task_id")
        if any(item["rework_task_id"] == task_id for item in case["reworks"]):
            raise RecordError("rework_task_id already exists")
    elif task_id is not None:
        raise RecordError("approve decision cannot create a rework task")
    case["decisions"].append(
        {
            "decision_id": event["decision_id"],
            "submission_id": event["submission_id"],
            "stage": event["stage"],
            "candidate_revision": event["candidate_revision"],
            "candidate_hash": event["candidate_hash"],
            "verdict": verdict,
            "sequence": sequence,
        }
    )
    case["active_submission_id"] = None
    if verdict == "reject":
        case["reworks"].append(
            {
                "rework_task_id": task_id,
                "decision_id": event["decision_id"],
                "rejected_submission_id": event["submission_id"],
                "rejected_stage": event["stage"],
                "base_revision": event["candidate_revision"],
                "base_hash": event["candidate_hash"],
                "status": "pending",
                "created_sequence": sequence,
                "claimed_sequence": None,
                "completed_sequence": None,
                "new_candidate_revision": None,
                "new_candidate_hash": None,
            }
        )
        case["review_state"] = "pending_rework"
        case["active_rework_task_id"] = task_id
    elif event["stage"] == FIRST_REVIEW and SECOND_REVIEW in case[
        "required_review_stages"
    ]:
        case["review_stage"] = SECOND_REVIEW
        case["review_state"] = "awaiting_submission"
    else:
        case["review_stage"] = None
        case["review_state"] = "awaiting_acceptance"


def _claim_rework(case: dict[str, Any], event: dict[str, Any], sequence: int) -> None:
    if (
        case["review_state"] != "pending_rework"
        or event["rework_task_id"] != case["active_rework_task_id"]
    ):
        raise RecordError("rework claim does not target the active pending task")
    rework = _find_rework(case, event["rework_task_id"])
    rework["status"] = "in_progress"
    rework["claimed_sequence"] = sequence
    case["review_state"] = "rework_in_progress"


def _complete_rework(
    case: dict[str, Any], event: dict[str, Any], sequence: int
) -> None:
    if (
        case["review_state"] != "rework_in_progress"
        or event["rework_task_id"] != case["active_rework_task_id"]
    ):
        raise RecordError("rework completion does not target the active claimed task")
    revision = _positive_int(
        event["new_candidate_revision"], "new_candidate_revision"
    )
    content_hash = _fingerprint(
        event["new_candidate_hash"], "new_candidate_hash"
    )
    if revision != case["candidate_revision"] + 1:
        raise RecordError("rework must create the next candidate revision")
    if content_hash == case["candidate_hash"]:
        raise RecordError("rework must create a new candidate hash")
    _find_rework(case, event["rework_task_id"]).update(
        {
            "status": "completed",
            "completed_sequence": sequence,
            "new_candidate_revision": revision,
            "new_candidate_hash": content_hash,
        }
    )
    case["candidate_revision"] = revision
    case["candidate_hash"] = content_hash
    case["rework_count"] += 1
    case["review_stage"] = FIRST_REVIEW
    case["review_state"] = "awaiting_submission"
    case["active_rework_task_id"] = None


def _validate_event(event: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise RecordError("review event must be an object")
    event_type = event.get("event_type")
    if event_type not in REVIEW_EVENT_TYPES:
        raise RecordError("invalid review event_type")
    value = dict(event)
    if set(value) != _EVENT_FIELDS[event_type]:
        raise RecordError(f"{event_type} event has missing or unknown fields")
    _identifier(value["event_id"], "event_id")
    _nonnegative_int(value["expected_sequence"], "expected_sequence")
    if event_type == "submit":
        _identifier(value["submission_id"], "submission_id")
        _review_stage(value["stage"])
        _positive_int(value["candidate_revision"], "candidate_revision")
        _fingerprint(value["candidate_hash"], "candidate_hash")
    elif event_type == "decide":
        _identifier(value["decision_id"], "decision_id")
        _identifier(value["submission_id"], "submission_id")
        _review_stage(value["stage"])
        _positive_int(value["candidate_revision"], "candidate_revision")
        _fingerprint(value["candidate_hash"], "candidate_hash")
        if value["verdict"] not in REVIEW_VERDICTS:
            raise RecordError("invalid review verdict")
        if value["rework_task_id"] is not None:
            _identifier(value["rework_task_id"], "rework_task_id")
    else:
        _identifier(value["rework_task_id"], "rework_task_id")
        if event_type == "complete_rework":
            _positive_int(value["new_candidate_revision"], "new_candidate_revision")
            _fingerprint(value["new_candidate_hash"], "new_candidate_hash")
    return value


def _require_current_candidate(
    case: Mapping[str, Any], value: Mapping[str, Any]
) -> None:
    if (
        value["candidate_revision"] != case["candidate_revision"]
        or value["candidate_hash"] != case["candidate_hash"]
    ):
        raise RecordError("operation does not match the current candidate version")


def _find_rework(case: dict[str, Any], task_id: str) -> dict[str, Any]:
    return next(item for item in case["reworks"] if item["rework_task_id"] == task_id)


def _review_stage(value: Any) -> str:
    if value not in REVIEW_STAGES:
        raise RecordError("invalid review stage")
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RecordError(f"{field} must be a positive integer")
    return value


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RecordError(f"{field} must be a non-negative integer")
    return value


def _identifier(value: Any, field: str) -> str:
    identifier(value, field)
    return value


def _fingerprint(value: Any, field: str) -> str:
    fingerprint(value, field)
    return value


def _case_binding(
    project_id: str,
    item_id: str,
    stages: list[str],
    candidate_revision: int,
    candidate_hash: str,
) -> str:
    payload = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "project_id": project_id,
        "item_id": item_id,
        "required_review_stages": stages,
        "initial_candidate_revision": candidate_revision,
        "initial_candidate_hash": candidate_hash,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
