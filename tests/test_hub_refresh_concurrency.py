"""Independent processes exercise real SQLite refresh contention in synthetic roots."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path

from connection_fixtures import Fixture
from hub.connection_records import validate_result
from hub.connection_refresh import GENESIS_HASH, LedgerBusyError, RefreshLedger, refresh


# The harness gates only process entry. No product hooks or project commands run.
REFRESH_CHILD = r'''
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hub.connection_records import validate_result
from hub.connection_refresh import RefreshLedger, RefreshLedgerError, refresh
from hub.connection_sources import SourceResolver
root, ready, go = map(Path, sys.argv[2:5])
request_id, expected = sys.argv[5], json.loads(sys.argv[6])
resolver = SourceResolver(root)
ledger = RefreshLedger(root, timeout=5, result_validator=validate_result)
ready.write_text("ready")
deadline = time.monotonic() + 15
while not go.exists():
    if time.monotonic() > deadline:
        raise TimeoutError("refresh gate timed out")
    time.sleep(.01)
try:
    outcome = refresh(ledger, resolver, request_id, ["a", "b"], expected_head=expected)
    print(json.dumps({"status": outcome["request"]["status"],
                      "remaining": outcome["request"]["remaining_project_ids"],
                      "resolver_errors": outcome["resolver_errors"],
                      "head": outcome["projection"]["head"]}), flush=True)
except RefreshLedgerError as exc:
    print(json.dumps({"error": exc.code}), flush=True)
    sys.exit(2)
'''

LOCK_CHILD = r'''
import sqlite3, sys, time
from pathlib import Path
database, ready, release = map(Path, sys.argv[1:])
connection = sqlite3.connect(database, timeout=5)
try:
    connection.execute("BEGIN IMMEDIATE")
    ready.write_text("locked")
    deadline = time.monotonic() + 15
    while not release.exists():
        if time.monotonic() > deadline:
            raise TimeoutError("lock release timed out")
        time.sleep(.01)
    connection.commit()
    print("released", flush=True)
finally:
    connection.close()
'''


class RefreshConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.fixture.add("a")
        self.fixture.add("b")
        self.addCleanup(self.fixture.close)
        self.ledger = RefreshLedger(self.fixture.hub, timeout=.15, result_validator=validate_result)
        self.initial_head = self.ledger.head
        self.children = []
        # LIFO cleanups collect every owned process before removing its fixture.
        self.addCleanup(self.collect_children)

    def collect_children(self):
        for process in self.children:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)

    def spawn(self, code, *args):
        process = subprocess.Popen([sys.executable, "-c", code, *map(str, args)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        self.children.append(process)
        return process

    def wait_ready(self, ready, process):
        deadline = time.monotonic() + 10
        while not ready.exists():
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=1)
                self.fail(f"child exited before ready: {process.returncode}; {stdout}; {stderr}")
            if time.monotonic() > deadline:
                self.fail("owned child did not reach ready gate within 10 seconds")
            time.sleep(.01)
        self.assertIsNone(process.poll(), "child exited at the ready gate")

    def release_refresh_pair(self, request_ids):
        go = self.fixture.base / "refresh-go"
        processes = []
        for index, request_id in enumerate(request_ids):
            ready = self.fixture.base / f"refresh-ready-{index}"
            process = self.spawn(REFRESH_CHILD, Path(__file__).resolve().parents[1] / "src",
                                 self.fixture.hub, ready, go, request_id, json.dumps(self.initial_head))
            processes.append((process, ready))
        for process, ready in processes:
            self.wait_ready(ready, process)
        go.write_text("go")
        outcomes = []
        for process, _ in processes:
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual("", stderr, stderr)
            self.assertIn(process.returncode, (0, 2), stdout)
            outcomes.append((process.returncode, json.loads(stdout)))
        return outcomes

    def assert_complete_chain(self, request_id):
        history = self.ledger.history()
        self.assertEqual([request_id], [row["request_id"] for row in history["requests"]])
        request = history["requests"][0]
        self.assertEqual("FINISHED", request["status"])
        self.assertEqual(["a", "b"], request["completed_project_ids"])
        self.assertEqual([], request["remaining_project_ids"])
        self.assertEqual({("a", request_id), ("b", request_id)},
                         {(row["project_id"], row["request_id"]) for row in history["results"]})
        self.assertEqual(2, len(history["results"]))
        self.assertTrue(all(row["success"] for row in history["results"]))
        previous = GENESIS_HASH
        self.assertEqual(4, len(history["events"]))
        for sequence, event in enumerate(history["events"], 1):
            self.assertEqual(sequence, event["sequence"])
            self.assertEqual(previous, event["previous_hash"])
            canonical = json.dumps(event["payload"], ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), allow_nan=False).encode()
            core = {key: event[key] for key in ("sequence", "event_id", "event_type", "request_id",
                                                "project_id", "created_at", "previous_hash")}
            core.update(schema_version="2.0", payload_hash=hashlib.sha256(canonical).hexdigest())
            encoded = json.dumps(core, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False).encode()
            self.assertEqual(hashlib.sha256(encoded).hexdigest(), event["event_hash"])
            previous = event["event_hash"]
        self.assertEqual({"sequence": 4, "hash": previous}, history["head"])
        self.assertEqual(history["head"], self.ledger.head)
        return history["head"]

    def test_two_processes_same_initial_head_have_one_complete_winner_and_explicit_cas_loser(self):
        ids = ["process-one", "process-two"]
        outcomes = self.release_refresh_pair(ids)
        self.assertEqual([0, 2], sorted(code for code, _ in outcomes))
        winner = next(index for index, (code, _) in enumerate(outcomes) if code == 0)
        success = outcomes[winner][1]
        loser = outcomes[1 - winner][1]
        self.assertEqual({"error": "EXPECTED_HEAD_CONFLICT"}, loser)
        self.assertEqual("FINISHED", success["status"])
        self.assertEqual([], success["remaining"])
        self.assertEqual({}, success["resolver_errors"])
        self.assertEqual(success["head"], self.assert_complete_chain(ids[winner]))

    def test_independent_sqlite_writer_lock_is_busy_then_same_request_recovers(self):
        ready, release = self.fixture.base / "lock-ready", self.fixture.base / "lock-release"
        child = self.spawn(LOCK_CHILD, self.ledger.path, ready, release)
        self.wait_ready(ready, child)
        resolver = self.fixture.resolver()
        with self.assertRaises(LedgerBusyError) as failure:
            refresh(self.ledger, resolver, "lock-resume", ["a", "b"], expected_head=self.initial_head)
        self.assertEqual("LEDGER_LOCKED", failure.exception.code)
        self.assertIsNone(child.poll(), "independent lock holder exited before the contention check")
        self.assertEqual(self.initial_head, self.ledger.head)
        self.assertEqual([], self.ledger.history()["requests"])
        release.write_text("release")
        stdout, stderr = child.communicate(timeout=10)
        self.assertEqual(0, child.returncode, stderr)
        self.assertEqual("released", stdout.strip())
        self.assertEqual("", stderr)
        result = refresh(self.ledger, resolver, "lock-resume", ["a", "b"], expected_head=self.initial_head)
        self.assertEqual(["a", "b"], result["appended_project_ids"])
        self.assertEqual({}, result["resolver_errors"])
        self.assert_complete_chain("lock-resume")

    def test_overlapping_same_request_keeps_identity_and_no_duplicate_durable_results(self):
        outcomes = self.release_refresh_pair(["shared", "shared"])
        self.assertTrue(any(code == 0 for code, _ in outcomes), outcomes)
        for code, outcome in outcomes:
            if code == 0:
                self.assertEqual("FINISHED", outcome["status"])
                self.assertEqual({}, outcome["resolver_errors"])
            else:
                self.assertIn(outcome.get("error"), {"LEDGER_LOCKED", "PROJECT_RESULT_CONFLICT"})
        # Resume via the public dispatcher, retaining the original request capsule.
        recovered = refresh(self.ledger, self.fixture.resolver(), "shared", ["a", "b"],
                            expected_head=self.initial_head)
        self.assertEqual([], recovered["appended_project_ids"])
        self.assertEqual({}, recovered["resolver_errors"])
        self.assert_complete_chain("shared")


if __name__ == "__main__":
    unittest.main()
