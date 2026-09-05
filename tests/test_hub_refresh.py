from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hub.connection_refresh import (
    GENESIS_HASH,
    HeadConflictError,
    IncompleteRequestError,
    LedgerBusyError,
    LedgerCorruptionError,
    LedgerPathError,
    LedgerSchemaError,
    PrecommitFaultError,
    ReadOnlyLedgerError,
    RefreshLedger,
    RequestConflictError,
    ResultConflictError,
    ResultValidationError,
    refresh,
)


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def authority(seed: str = "a") -> dict[str, str]:
    fingerprint = hashlib.sha256(seed.encode()).hexdigest()
    return {
        "source_plan_id": f"source-plan-{seed}",
        "source_plan_hash": fingerprint,
        "manifest_id": f"manifest-{seed}",
        "manifest_hash": fingerprint,
        "registry_hash": fingerprint,
        "adapter_version": "1.0",
        "adapter_hash": fingerprint,
        "accepted_inventory_hash": fingerprint,
        "accepted_candidate": fingerprint,
    }


def make_result(project_id: str, capsule: dict[str, str], *, success: bool,
                disposition: str | None = None, marker: str = "one") -> dict:
    row = {
        "schema_version": "1.0",
        "kind": "source_resolution",
        "project_id": project_id,
        "observed_at": "2026-09-05T12:00:00Z",
        "disposition": disposition or ("SOURCE_RESOLVED" if success else "SOURCE_UNAVAILABLE"),
        "success": success,
        "authority": capsule,
        "business_snapshot": {"state": "unknown", "snapshot": None, "reason": "fixture"},
        "sources": [{"sha256": hashlib.sha256(marker.encode()).hexdigest()}] if success else [],
        "evidence": [],
        "operational_facts": [],
        "errors": [] if success else [{"code": marker, "message": "fixture failure"}],
        "ui_verification": "UNVERIFIED",
    }
    row["result_hash"] = digest(row)
    return row


def validate_result(row: dict) -> dict:
    required = {
        "schema_version", "kind", "project_id", "observed_at", "disposition",
        "success", "authority", "business_snapshot", "sources", "evidence",
        "operational_facts", "errors", "ui_verification", "result_hash",
    }
    if not isinstance(row, dict) or set(row) != required:
        raise ValueError("strict result fields")
    if row["result_hash"] != digest({key: value for key, value in row.items()
                                     if key != "result_hash"}):
        raise ValueError("result hash")
    if row["success"] != (row["disposition"] in {
            "SOURCE_RESOLVED", "EXPLICIT_NO_CURRENT_SOURCE_VERIFIED"}):
        raise ValueError("success semantics")
    return row


class FakeResolver:
    def __init__(self, capsule: dict[str, str], outcomes: dict[str, object]) -> None:
        self.authority = capsule
        self.outcomes = outcomes
        self.calls: list[str] = []

    def refresh(self, project_id: str) -> dict:
        self.calls.append(project_id)
        outcome = self.outcomes[project_id]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome  # type: ignore[return-value]

    @staticmethod
    def validate_result(row: dict) -> dict:
        return validate_result(row)


class RefreshLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "hub"
        self.root.mkdir()
        self.capsule = authority()
        self.path = self.root / "data/design_governance/refresh.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def ledger(self, **kwargs: object) -> RefreshLedger:
        return RefreshLedger(self.root, self.path, result_validator=validate_result, **kwargs)

    def append(self, ledger: RefreshLedger, request_id: str, project_id: str,
               *, success: bool, marker: str = "one") -> dict:
        return ledger.append_project_result(
            request_id, project_id,
            make_result(project_id, self.capsule, success=success, marker=marker))

    def enable_wal(self) -> None:
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual("wal", connection.execute("PRAGMA journal_mode=WAL").fetchone()[0])

    def test_per_project_commits_survive_restart_and_another_failure(self) -> None:
        ledger = self.ledger()
        ledger.begin_request("batch", ["a", "b", "c"], self.capsule,
                             expected_head={"sequence": 0, "hash": GENESIS_HASH})
        first = self.append(ledger, "batch", "a", success=True)
        with self.assertRaises(ResultValidationError):
            ledger.append_project_result("batch", "b", {"invalid": True})
        reopened = self.ledger()
        history = reopened.history("batch")
        self.assertEqual(["a"], history["requests"][0]["completed_project_ids"])
        self.assertEqual(first["result_ref"], history["results"][0]["result_ref"])
        with self.assertRaises(IncompleteRequestError):
            reopened.finish_request("batch")
        self.append(reopened, "batch", "b", success=False)
        self.append(reopened, "batch", "c", success=True)
        self.assertEqual("FINISHED", reopened.finish_request("batch")["status"])

    def test_same_request_and_result_retry_are_idempotent_but_changes_reject(self) -> None:
        ledger = self.ledger()
        original_request = ledger.begin_request("same", ["b", "a"], self.capsule)
        retry = ledger.begin_request("same", ["a", "b"], self.capsule,
                                     expected_head={"sequence": 999, "hash": "f" * 64})
        self.assertEqual(original_request, retry)
        original = self.append(ledger, "same", "a", success=True)
        repeated = self.append(ledger, "same", "a", success=True)
        self.assertEqual(original, repeated)
        with self.assertRaises(ResultConflictError):
            self.append(ledger, "same", "a", success=True, marker="changed")
        with self.assertRaises(RequestConflictError):
            ledger.begin_request("same", ["a"], self.capsule)

    def test_interrupted_coordinator_resumes_without_rereading_completed_project(self) -> None:
        outcomes = {
            "a": make_result("a", self.capsule, success=True),
            "b": RuntimeError("offline"),
        }
        resolver = FakeResolver(self.capsule, outcomes)
        first = refresh(self.ledger(), resolver, "resume", ["a", "b"])
        self.assertEqual(["a"], first["request"]["completed_project_ids"])
        self.assertEqual({"b": "RuntimeError"}, first["resolver_errors"])
        resolver2 = FakeResolver(self.capsule, {
            "a": AssertionError("must not reread"),
            "b": make_result("b", self.capsule, success=False),
        })
        second = refresh(self.ledger(), resolver2, "resume", ["a", "b"])
        self.assertEqual(["b"], resolver2.calls)
        self.assertEqual("FINISHED", second["request"]["status"])

    def test_coordinator_uses_ledger_dispatcher_to_rebuild_older_authorities(self) -> None:
        ledger = self.ledger()
        ledger.begin_request("old", ["a"], self.capsule)
        self.append(ledger, "old", "a", success=True)
        ledger.finish_request("old")
        newer = authority("new")

        class CurrentOnlyResolver(FakeResolver):
            def validate_result(self, row: dict) -> dict:
                validated = validate_result(row)
                if validated["authority"] != self.authority:
                    raise ValueError("not the active source plan")
                return validated

        resolver = CurrentOnlyResolver(
            newer, {"b": make_result("b", newer, success=True)})
        outcome = refresh(ledger, resolver, "new", ["b"])
        self.assertEqual({"a", "b"}, set(outcome["projection"]["projects"]))

    def test_latest_failure_retains_prior_success_as_explicitly_stale(self) -> None:
        ledger = self.ledger()
        ledger.begin_request("success", ["a", "other"], self.capsule)
        successful = self.append(ledger, "success", "a", success=True)
        self.append(ledger, "success", "other", success=True)
        ledger.finish_request("success")
        ledger.begin_request("failure", ["a"], self.capsule)
        failed = self.append(ledger, "failure", "a", success=False, marker="disk-offline")
        ledger.finish_request("failure")
        view = ledger.rebuild()["projects"]
        self.assertEqual(failed["result_ref"], view["a"]["latest_attempt"]["result_ref"])
        self.assertEqual(successful["result_ref"], view["a"]["last_success"]["result_ref"])
        self.assertEqual("stale", view["a"]["freshness"])
        self.assertEqual("fresh", view["other"]["freshness"])

    def test_failed_first_attempt_is_unknown_and_empty_history_is_empty(self) -> None:
        empty = self.ledger().rebuild()
        self.assertEqual({}, empty["projects"])
        ledger = self.ledger()
        ledger.begin_request("unknown", ["a"], self.capsule)
        self.append(ledger, "unknown", "a", success=False)
        ledger.finish_request("unknown")
        row = ledger.rebuild()["projects"]["a"]
        self.assertIsNone(row["last_success"])
        self.assertEqual("unknown", row["freshness"])

    def test_authority_drift_is_rejected_for_resume_and_flagged_offline(self) -> None:
        ledger = self.ledger()
        ledger.begin_request("authority", ["a"], self.capsule)
        with self.assertRaises(RequestConflictError):
            ledger.begin_request("authority", ["a"], authority("new"))
        history = ledger.history(current_authority=authority("new"))
        self.assertTrue(history["requests"][0]["authority_drift"])
        self.assertTrue(ledger.rebuild(current_authority=authority("new"))["authority_drift"])

    def test_precommit_fault_rolls_back_event_result_and_head(self) -> None:
        base = self.ledger()
        base.begin_request("fault", ["a"], self.capsule)
        prior = base.head

        def fail(operation: str, _context: object) -> None:
            if operation == "append_project_result":
                raise RuntimeError("injected")

        faulty = self.ledger(precommit_fault=fail)
        with self.assertRaises(PrecommitFaultError):
            self.append(faulty, "fault", "a", success=True)
        reopened = self.ledger()
        self.assertEqual(prior, reopened.head)
        self.assertEqual([], reopened.history("fault")["results"])

    def test_global_hash_link_and_result_reference_corruption_fail_closed(self) -> None:
        ledger = self.ledger()
        ledger.begin_request("corrupt", ["a"], self.capsule)
        self.append(ledger, "corrupt", "a", success=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE refresh_events SET previous_hash=? WHERE sequence=2", ("f" * 64,))
            connection.commit()
        with self.assertRaisesRegex(LedgerCorruptionError, "hash link"):
            ledger.history()

        other_path = self.root / "data/design_governance/other.sqlite3"
        other = RefreshLedger(self.root, other_path, result_validator=validate_result)
        other.begin_request("forged", ["a"], self.capsule)
        self.append(other, "forged", "a", success=True)
        with closing(sqlite3.connect(other_path)) as connection:
            connection.execute("UPDATE refresh_results SET result_ref='forged-ref'")
            connection.commit()
        with self.assertRaisesRegex(LedgerCorruptionError, "identity mismatch"):
            other.rebuild()

    def test_unknown_schema_fails_closed(self) -> None:
        ledger = self.ledger()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE ledger_meta SET schema_version='99.0'")
            connection.commit()
        with self.assertRaises(LedgerSchemaError):
            ledger.history()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO requires POSIX")
    def test_path_rejects_escape_symlink_fifo_and_unknown_file(self) -> None:
        with self.assertRaises(LedgerPathError):
            RefreshLedger(self.root, self.root.parent / "outside.sqlite3")
        approved = self.root / "data/design_governance"
        approved.mkdir(parents=True)
        outside = self.root / "outside"
        outside.mkdir()
        linked = approved / "linked"
        linked.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(LedgerPathError):
            RefreshLedger(self.root, linked / "db.sqlite3")
        fifo = approved / "pipe.sqlite3"
        os.mkfifo(fifo)
        with self.assertRaises(LedgerPathError):
            RefreshLedger(self.root, fifo)
        unknown = approved / "notes.sqlite3"
        unknown.write_text("do not overwrite", encoding="utf-8")
        with self.assertRaises(LedgerPathError):
            RefreshLedger(self.root, unknown)
        self.assertEqual("do not overwrite", unknown.read_text(encoding="utf-8"))

    def test_rebuild_never_opens_paths_named_inside_result_content(self) -> None:
        external = self.root.parent / "external-secret.yaml"
        external.write_text("must not read", encoding="utf-8")
        ledger = self.ledger()
        ledger.begin_request("offline", ["a"], self.capsule)
        result = make_result("a", self.capsule, success=True)
        result["business_snapshot"]["reason"] = str(external)
        result["result_hash"] = digest({key: value for key, value in result.items()
                                        if key != "result_hash"})
        ledger.append_project_result("offline", "a", result)
        ledger.finish_request("offline")
        original_open = Path.open

        def guarded(path: Path, *args: object, **kwargs: object):
            if path == external:
                raise AssertionError("external result content was opened")
            return original_open(path, *args, **kwargs)

        with mock.patch.object(Path, "open", guarded):
            self.assertEqual("fresh", ledger.rebuild()["projects"]["a"]["freshness"])

    def test_real_cross_process_lock_and_expected_head_contention(self) -> None:
        ledger = self.ledger()
        lock = sqlite3.connect(self.path)
        lock.execute("BEGIN IMMEDIATE")
        try:
            contender = RefreshLedger(self.root, self.path, timeout=0.05,
                                      result_validator=validate_result)
            with self.assertRaises(LedgerBusyError):
                contender.begin_request("locked", ["a"], self.capsule)
        finally:
            lock.rollback()
            lock.close()

        head = ledger.head
        script = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hub.connection_refresh import RefreshLedger
root, path, request_id, capsule, head = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], json.loads(sys.argv[5]), json.loads(sys.argv[6])
try:
    RefreshLedger(root, path).begin_request(request_id, ['a'], capsule, expected_head=head)
    print('OK')
except Exception as exc:
    print(getattr(exc, 'code', type(exc).__name__))
"""
        arguments = [str(Path(__file__).resolve().parents[1] / "src"), str(self.root),
                     str(self.path)]
        processes = [subprocess.Popen(
            [sys.executable, "-c", script, *arguments, request_id,
             canonical(self.capsule), canonical(head)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            for request_id in ("process-a", "process-b")]
        outputs = [process.communicate(timeout=10) for process in processes]
        self.assertEqual([0, 0], [process.returncode for process in processes], outputs)
        labels = sorted(stdout.strip() for stdout, _stderr in outputs)
        self.assertEqual(["EXPECTED_HEAD_CONFLICT", "OK"], labels)

    def test_history_is_one_snapshot_across_concurrent_wal_commit(self) -> None:
        ledger = self.ledger()
        ledger.begin_request("original", ["a"], self.capsule)
        old_head = ledger.head
        self.enable_wal()
        writer = self.ledger()
        verified_rows = ledger._verified_rows

        def commit_after_verified_rows(connection: sqlite3.Connection):
            rows = verified_rows(connection)
            writer.begin_request("concurrent", ["b"], self.capsule)
            return rows

        with mock.patch.object(ledger, "_verified_rows", side_effect=commit_after_verified_rows):
            snapshot = ledger.history()
        self.assertEqual(old_head, snapshot["head"])
        self.assertEqual(["original"], [row["request_id"] for row in snapshot["requests"]])
        self.assertEqual(["original"], [row["request_id"] for row in snapshot["events"]])
        stable = ledger.history()
        self.assertEqual({"original", "concurrent"},
                         {row["request_id"] for row in stable["requests"]})
        self.assertEqual(old_head["sequence"] + 1, stable["head"]["sequence"])

    def test_rebuild_uses_one_history_snapshot_during_concurrent_wal_commit(self) -> None:
        ledger = self.ledger()
        ledger.begin_request("original", ["a"], self.capsule)
        self.append(ledger, "original", "a", success=True)
        ledger.finish_request("original")
        old_head = ledger.head
        self.enable_wal()
        writer = self.ledger()
        verified_rows = ledger._verified_rows

        def commit_after_verified_rows(connection: sqlite3.Connection):
            rows = verified_rows(connection)
            writer.begin_request("concurrent", ["b"], self.capsule)
            self.append(writer, "concurrent", "b", success=True)
            writer.finish_request("concurrent")
            return rows

        with mock.patch.object(ledger, "_verified_rows", side_effect=commit_after_verified_rows):
            projection = ledger.rebuild()
        self.assertEqual(old_head, projection["head"])
        self.assertEqual({"a"}, set(projection["projects"]))
        stable = ledger.rebuild()
        self.assertEqual({"a", "b"}, set(stable["projects"]))
        self.assertEqual(old_head["sequence"] + 3, stable["head"]["sequence"])

    def test_rebuild_requires_pure_offline_result_authority(self) -> None:
        ledger = RefreshLedger(self.root, self.path)
        with self.assertRaises(ResultValidationError):
            ledger.rebuild()

    def test_read_only_history_never_creates_or_mutates_a_ledger(self) -> None:
        absent = self.root / "docs/reports/ui_design_governance/absent.sqlite3"
        with self.assertRaises(LedgerPathError):
            RefreshLedger(self.root, absent, read_only=True)
        self.assertFalse(absent.exists())
        self.assertFalse(absent.parent.exists())

        writable = self.ledger()
        writable.begin_request("read-only", ["a"], self.capsule)
        self.append(writable, "read-only", "a", success=True)
        writable.finish_request("read-only")
        before = self.path.stat().st_mtime_ns
        offline = RefreshLedger(self.root, self.path, read_only=True,
                                result_validator=validate_result)
        self.assertEqual("fresh", offline.rebuild()["projects"]["a"]["freshness"])
        with self.assertRaises(ReadOnlyLedgerError):
            offline.begin_request("forbidden", ["a"], self.capsule)
        self.assertEqual(before, self.path.stat().st_mtime_ns)


if __name__ == "__main__":
    unittest.main()
