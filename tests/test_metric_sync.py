"""Isolated startup/manual synchronization checks; no real project is executed."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from unittest import mock
import unittest

import yaml

from connection_fixtures import Fixture
from hub.connection_records import RecordError, content_hash
from hub.connection_refresh import RefreshLedgerError
from hub.metric_import import import_metric_snapshot_file as real_import_snapshot
from hub.metric_store import MetricStore
from hub.metric_sync import MetricSyncCoordinator, cache_then_sync


ROOT = Path(__file__).resolve().parents[1]
EXPORT_ENTRY = "scripts/export_hub_metric_snapshot.py"
EXPORT_SCRIPT = """\
from __future__ import annotations
import argparse
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--hub-root", type=Path, required=True)
parser.add_argument("--project-root", type=Path, required=True)
args = parser.parse_args()
sys.path.insert(0, str(args.hub_root / "src"))

from hub.connection_sources import SourceResolver
from hub.metric_export import export_metric_snapshot
from hub.metrics import metric

observed = "2026-09-11T04:00:00+00:00"
management = SourceResolver(
    args.hub_root,
    clock=lambda: observed,
).refresh("fixture-project")
if not management["success"]:
    raise SystemExit(2)

def collect(project_root, project_id, observed_at):
    source = management["sources"][0]
    return {
        "metrics": [metric(
            project_id,
            "fixture.completed",
            1,
            "items",
            "STATE.yaml#current_work.completed",
            source["sha256"],
            observed_at,
            counting_basis="One synthetic synchronization fixture.",
        )],
        "issues": [],
        "disposition": "resolved",
        "source_version": source["sha256"],
    }

export_metric_snapshot(
    args.project_root,
    "fixture-project",
    {"business": collect},
    exporter_id="fixture-export",
    exporter_version="1.0",
    management=management,
    clock=lambda: observed,
)
"""
INVALID_SNAPSHOT_SCRIPT = """\
from pathlib import Path
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--hub-root", type=Path, required=True)
parser.add_argument("--project-root", type=Path, required=True)
args = parser.parse_args()
target = args.project_root / ".hub/status.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text("{}", encoding="utf-8")
"""


class MetricSyncTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)
        (self.fixture.hub / "src").symlink_to(ROOT / "src", target_is_directory=True)
        self.project = self.add_export_project("fixture-project")
        self.coordinator = MetricSyncCoordinator(self.fixture.hub)

    def add_export_project(self, project_id, *, script=None):
        project, declaration = self.fixture.add(project_id)
        declaration["metric_export"] = {
            "entry": EXPORT_ENTRY,
            "snapshot": ".hub/status.json",
        }
        (project / "hub.connection.yaml").write_text(
            yaml.safe_dump(declaration, sort_keys=False),
            encoding="utf-8",
        )
        entry = project / EXPORT_ENTRY
        entry.parent.mkdir()
        entry.write_text(
            script or EXPORT_SCRIPT.replace("fixture-project", project_id),
            encoding="utf-8",
        )
        return project

    def project_files(self):
        return {
            str(path.relative_to(self.project)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.project.rglob("*")
            if path.is_file()
        }

    def test_startup_emits_cache_before_export_and_imports_snapshot_only(self):
        before = self.project_files()
        events = []
        opened = []
        snapshot = (self.project / ".hub/status.json").resolve()
        original_binding = self.coordinator._binding

        def emit(event):
            self.assertFalse(binding.called)
            self.assertFalse(snapshot.exists())
            events.append(event)

        def guarded_import(
            store,
            request_id,
            project_root,
            *,
            expected_project,
            sync_attempt_sequence=None,
            clock=None,
        ):
            real_open = Path.open

            def snapshot_only(path, *args, **kwargs):
                candidate = Path(path).resolve()
                opened.append(candidate)
                if candidate != snapshot:
                    raise AssertionError("importer opened a non-snapshot project file")
                return real_open(path, *args, **kwargs)

            with mock.patch.object(Path, "open", snapshot_only):
                return real_import_snapshot(
                    store,
                    request_id,
                    project_root,
                    expected_project=expected_project,
                    sync_attempt_sequence=sync_attempt_sequence,
                    clock=clock,
                )

        with mock.patch.object(
            self.coordinator,
            "_binding",
            wraps=original_binding,
        ) as binding, mock.patch(
            "hub.metric_sync.import_metric_snapshot_file",
            side_effect=guarded_import,
        ):
            result = cache_then_sync(
                self.coordinator,
                "startup-fixture",
                mode="startup",
                emit_cache=emit,
                project_ids=["fixture-project"],
                timeout_seconds=2,
            )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["phase"], "cache")
        self.assertFalse(events[0]["data"]["available"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["results"]["fixture-project"]["status"], "imported")
        self.assertEqual(opened, [snapshot])
        after = self.project_files()
        self.assertEqual(
            set(after) - set(before),
            {".hub/status.json"},
        )
        self.assertEqual(
            MetricStore(self.fixture.hub, read_only=True)
            .project_snapshot("fixture-project")["business"]["metric_count"],
            1,
        )

    def test_same_manual_request_reuses_receipt_without_export(self):
        first = self.coordinator.synchronize(
            "manual-fixture",
            project_ids=["fixture-project"],
            timeout_seconds=2,
        )
        self.assertTrue(first["complete"])
        with mock.patch.object(
            self.coordinator,
            "_run_export",
            side_effect=AssertionError("completed unit reran its exporter"),
        ):
            repeated = self.coordinator.synchronize(
                "manual-fixture",
                project_ids=["fixture-project"],
                timeout_seconds=2,
            )
        self.assertEqual(
            repeated["results"]["fixture-project"]["status"],
            "reused",
        )
        self.assertEqual(
            repeated["results"]["fixture-project"]["receipt"],
            first["results"]["fixture-project"]["receipt"],
        )
        attempts = MetricStore(
            self.fixture.hub,
            read_only=True,
        ).sync_attempts("fixture-project")
        self.assertEqual(
            [attempt["status"] for attempt in attempts["items"]],
            ["success"],
        )

    def test_failed_refresh_preserves_last_success_and_marks_cache_historical(self):
        times = iter(
            [
                "2026-09-11T05:00:00+00:00",
                "2026-09-11T05:00:01+00:00",
                "2026-09-11T06:00:00+00:00",
                "2026-09-11T06:00:02+00:00",
            ]
        )
        coordinator = MetricSyncCoordinator(
            self.fixture.hub,
            clock=lambda: next(times),
        )
        succeeded = coordinator.synchronize(
            "success-before-failure",
            project_ids=["fixture-project"],
            timeout_seconds=2,
        )
        self.assertTrue(succeeded["complete"])
        snapshot = json.loads(
            (self.project / ".hub/status.json").read_text(encoding="utf-8")
        )
        (self.project / EXPORT_ENTRY).write_text(
            INVALID_SNAPSHOT_SCRIPT,
            encoding="utf-8",
        )
        failed = coordinator.synchronize(
            "invalid-after-success",
            project_ids=["fixture-project"],
            timeout_seconds=2,
        )
        self.assertEqual(
            failed["errors"],
            {"fixture-project": "snapshot_import_failed"},
        )

        store = MetricStore(self.fixture.hub, read_only=True)
        status = store.sync_status(["fixture-project"])["fixture-project"]
        self.assertEqual(status["latest_attempt"]["status"], "failed")
        self.assertEqual(
            status["latest_attempt"]["request_id"],
            "invalid-after-success",
        )
        self.assertEqual(
            status["latest_attempt"]["started_at"],
            "2026-09-11T06:00:00+00:00",
        )
        self.assertEqual(
            status["latest_attempt"]["finished_at"],
            "2026-09-11T06:00:02+00:00",
        )
        self.assertEqual(
            status["latest_attempt"]["error"],
            "snapshot_import_failed",
        )
        success = status["last_success"]
        self.assertEqual(success["status"], "success")
        self.assertEqual(
            success["snapshot"],
            {
                "id": snapshot["snapshot_id"],
                "schema_version": snapshot["schema_version"],
                "observed_at": snapshot["observed_at"],
                "exporter": snapshot["exporter"],
                "source_versions": snapshot["source_versions"],
                "disposition": snapshot["disposition"],
            },
        )
        self.assertEqual(
            success["started_at"],
            "2026-09-11T05:00:00+00:00",
        )
        self.assertEqual(
            success["finished_at"],
            "2026-09-11T05:00:01+00:00",
        )
        self.assertEqual(
            store.project_snapshot("fixture-project")["snapshot_id"],
            snapshot["snapshot_id"],
        )

        cache = coordinator.read_cache()
        row = cache["projects"][0]
        self.assertEqual(row["view_role"], "historical")
        self.assertEqual(row["freshness"], "sync_failed")
        self.assertEqual(
            row["latest_attempt"]["error"],
            "snapshot_import_failed",
        )
        self.assertEqual(
            row["last_success"]["snapshot"]["id"],
            snapshot["snapshot_id"],
        )
        self.assertEqual(
            row["last_success"]["snapshot"]["source_versions_identity"],
            content_hash(snapshot["source_versions"]),
        )

    def test_success_becomes_historical_after_authority_change_or_removal(self):
        result = self.coordinator.synchronize(
            "authority-history",
            project_ids=["fixture-project"],
            timeout_seconds=2,
        )
        self.assertTrue(result["complete"])

        self.fixture.projects[0]["name"] = "changed-authority"
        self.fixture.save_registry()
        changed = MetricSyncCoordinator(self.fixture.hub).read_cache()
        self.assertEqual(
            changed["projects"][0]["freshness"],
            "authority_changed",
        )
        self.assertEqual(changed["projects"][0]["view_role"], "historical")
        self.assertIsNotNone(changed["projects"][0]["last_success"])

        self.fixture.projects[0]["current_state_status"] = "removed_local"
        self.fixture.save_registry()
        removed = MetricSyncCoordinator(self.fixture.hub).read_cache()
        self.assertEqual(
            removed["projects"][0]["freshness"],
            "removed_local",
        )
        self.assertEqual(removed["projects"][0]["view_role"], "historical")

    def test_cache_pagination_progresses_beyond_metric_page_limit(self):
        for index in range(50):
            project_id = f"fixture-extra-{index:02d}"
            self.fixture.projects.append(
                {
                    "id": project_id,
                    "name": project_id,
                    "root_path": str(self.fixture.base / project_id),
                    "enabled": True,
                    "current_state_paths": ["STATE.yaml"],
                }
            )
        self.fixture.save_registry()
        MetricStore(self.fixture.hub)

        page = MetricSyncCoordinator(self.fixture.hub).read_cache(
            after=50,
            limit=10,
        )
        self.assertTrue(page["available"])
        self.assertEqual(page["projects_total"], 51)
        self.assertEqual(len(page["projects"]), 1)
        self.assertEqual(
            page["projects"][0]["project_id"],
            "fixture-extra-49",
        )
        self.assertIsNone(page["next_cursor"])
        self.assertLessEqual(
            len(
                json.dumps(
                    page,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            ),
            8192,
        )

    def test_oversized_worst_case_batch_stops_before_store_or_source_access(self):
        project_ids = []
        projects = []
        for index in range(64):
            prefix = f"p{index:02d}-"
            project_id = prefix + ("x" * (128 - len(prefix)))
            project_ids.append(project_id)
            projects.append(
                {
                    "id": project_id,
                    "name": project_id,
                    "root_path": str(self.fixture.base / project_id),
                    "enabled": True,
                    "current_state_paths": ["STATE.yaml"],
                }
            )
        self.fixture.projects = projects
        self.fixture.save_registry()
        coordinator = MetricSyncCoordinator(self.fixture.hub)
        with mock.patch(
            "hub.metric_sync.MetricStore",
            side_effect=AssertionError("oversized batch opened the store"),
        ), mock.patch.object(
            coordinator,
            "_binding",
            side_effect=AssertionError("oversized batch visited a project"),
        ), self.assertRaisesRegex(
            RecordError,
            "result exceeds byte limit",
        ):
            coordinator.synchronize(
                "r" * 128,
                project_ids=project_ids,
                timeout_seconds=2,
            )
        self.assertFalse(
            (
                self.fixture.hub
                / "data/connections/connection_refresh.sqlite3"
            ).exists()
        )

    def test_long_valid_timestamps_stay_exact_in_store_and_fit_cache(self):
        observed_at = (
            "2026-09-11T04:00:00."
            + ("1" * 12000)
            + "+00:00"
        )
        (self.project / EXPORT_ENTRY).write_text(
            EXPORT_SCRIPT.replace(
                "2026-09-11T04:00:00+00:00",
                observed_at,
            ),
            encoding="utf-8",
        )
        result = self.coordinator.synchronize(
            "long-observation",
            project_ids=["fixture-project"],
            timeout_seconds=2,
        )
        self.assertTrue(result["complete"])
        compact_receipt_time = result["results"]["fixture-project"][
            "receipt"
        ]["observed_at"]
        self.assertLessEqual(len(compact_receipt_time), 64)
        self.assertEqual(
            datetime.fromisoformat(compact_receipt_time),
            datetime.fromisoformat(observed_at),
        )
        self.assertLessEqual(
            len(
                json.dumps(
                    {
                        "schema_version": "1.1",
                        "kind": "metric_sync_event",
                        "mode": "manual",
                        "phase": "sync",
                        "data": result,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            ),
            8192,
        )

        store = MetricStore(
            self.fixture.hub,
            read_only=True,
        )
        status = store.sync_status(["fixture-project"])["fixture-project"]
        self.assertEqual(
            status["latest_attempt"]["snapshot"]["observed_at"],
            observed_at,
        )
        self.assertEqual(
            store.receipt(
                self.coordinator._unit_request_id(
                    "long-observation",
                    "fixture-project",
                ),
                "fixture-project",
            )["observed_at"],
            observed_at,
        )
        cache = MetricSyncCoordinator(self.fixture.hub).read_cache()
        self.assertEqual(len(cache["projects"]), 1)
        self.assertIsNone(cache["next_cursor"])
        row = cache["projects"][0]
        self.assertIsNotNone(row["latest_attempt"])
        self.assertIsNotNone(row["last_success"])
        compact_observed = row["latest_attempt"]["snapshot"]["observed_at"]
        self.assertLessEqual(len(compact_observed), 64)
        self.assertEqual(
            datetime.fromisoformat(compact_observed),
            datetime.fromisoformat(observed_at),
        )
        self.assertLessEqual(
            len(
                json.dumps(
                    cache,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            ),
            8192,
        )
        self.assertLessEqual(
            len(
                json.dumps(
                    {
                        "schema_version": "1.1",
                        "kind": "metric_sync_event",
                        "mode": "startup",
                        "phase": "cache",
                        "data": cache,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            ),
            8192,
        )

    def test_export_command_is_fixed_and_does_not_inherit_environment(self):
        binding = self.coordinator._binding(
            self.coordinator._resolver(),
            "fixture-project",
        )
        captured = {}

        class Process:
            pid = 987654321
            returncode = 0

            def communicate(self, *, input=None, timeout=None):
                captured["input"] = input
                captured["timeout"] = timeout
                return b"", b""

            def kill(self):
                captured["killed"] = True

        with mock.patch.dict(
            os.environ,
            {"ACCESS_TOKEN": "must-not-reach-export"},
            clear=False,
        ), mock.patch(
            "hub.metric_sync.subprocess.Popen",
            return_value=Process(),
        ) as popen, mock.patch(
            "hub.metric_sync.os.killpg",
            side_effect=ProcessLookupError,
        ):
            self.coordinator._run_export(binding, 1.25)

        args, kwargs = popen.call_args
        self.assertEqual(
            args[0],
            [
                self.coordinator.python_executable,
                "-I",
                "-B",
                "-",
                "--hub-root",
                str(self.fixture.hub),
                "--project-root",
                str(binding["root"]),
            ],
        )
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["cwd"], binding["root"])
        self.assertEqual(kwargs["env"]["PATH"], "")
        self.assertNotIn("ACCESS_TOKEN", kwargs["env"])
        self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(captured["input"], EXPORT_SCRIPT.encode())
        self.assertEqual(captured["timeout"], 1.25)

    def test_timeout_kills_export_and_never_imports_stale_snapshot(self):
        (self.project / EXPORT_ENTRY).write_text(
            "import time\ntime.sleep(30)\n",
            encoding="utf-8",
        )
        started = time.monotonic()
        result = self.coordinator.synchronize(
            "timeout-fixture",
            project_ids=["fixture-project"],
            timeout_seconds=0.05,
        )
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(
            result["errors"],
            {"fixture-project": "export_timeout"},
        )
        self.assertFalse(result["complete"])
        self.assertFalse((self.project / ".hub/status.json").exists())
        status = MetricStore(
            self.fixture.hub,
            read_only=True,
        ).sync_status(["fixture-project"])["fixture-project"]
        self.assertEqual(status["latest_attempt"]["status"], "failed")
        self.assertEqual(status["latest_attempt"]["error"], "export_timeout")
        self.assertIsNone(status["last_success"])

    def test_offline_first_attempt_is_queryable_without_a_false_success(self):
        self.fixture.projects[0]["root_path"] = str(
            self.fixture.base / "offline-project"
        )
        self.fixture.save_registry()
        result = MetricSyncCoordinator(self.fixture.hub).synchronize(
            "offline-fixture",
            project_ids=["fixture-project"],
        )
        self.assertEqual(
            result["errors"],
            {"fixture-project": "project_export_unavailable"},
        )
        status = MetricStore(
            self.fixture.hub,
            read_only=True,
        ).sync_status(["fixture-project"])["fixture-project"]
        self.assertEqual(status["latest_attempt"]["status"], "failed")
        self.assertEqual(
            status["latest_attempt"]["error"],
            "project_export_unavailable",
        )
        self.assertIsNone(status["last_success"])
        cache = MetricSyncCoordinator(self.fixture.hub).read_cache()
        self.assertEqual(cache["projects"][0]["view_role"], "unavailable")
        self.assertEqual(cache["projects"][0]["freshness"], "sync_failed")

    def test_one_timed_out_project_does_not_block_another_project(self):
        second = self.add_export_project("fixture-second")
        (self.project / EXPORT_ENTRY).write_text(
            "import time\ntime.sleep(30)\n",
            encoding="utf-8",
        )
        result = MetricSyncCoordinator(self.fixture.hub).synchronize(
            "isolated-collection",
            project_ids=["fixture-project", "fixture-second"],
            timeout_seconds=0.5,
        )
        self.assertEqual(
            result["errors"],
            {"fixture-project": "export_timeout"},
        )
        self.assertEqual(
            result["results"]["fixture-second"]["status"],
            "imported",
        )
        self.assertTrue((second / ".hub/status.json").is_file())
        statuses = MetricStore(
            self.fixture.hub,
            read_only=True,
        ).sync_status(["fixture-project", "fixture-second"])
        self.assertEqual(
            statuses["fixture-project"]["latest_attempt"]["status"],
            "failed",
        )
        self.assertEqual(
            statuses["fixture-second"]["latest_attempt"]["status"],
            "success",
        )
        self.assertEqual(
            statuses["fixture-second"]["last_success"]["sequence"],
            statuses["fixture-second"]["latest_attempt"]["sequence"],
        )

    def test_interrupted_set_resumes_only_units_without_receipts(self):
        self.add_export_project("fixture-second")
        times = iter(
            [
                "2026-09-11T07:00:00+00:00",
                "2026-09-11T07:00:01+00:00",
                "2026-09-11T07:00:02+00:00",
                "2026-09-11T07:01:00+00:00",
                "2026-09-11T07:01:01+00:00",
            ]
        )
        interrupted = MetricSyncCoordinator(
            self.fixture.hub,
            clock=lambda: next(times),
        )
        original_export = interrupted._run_export

        def interrupt_second(binding, timeout_seconds):
            if binding["project"]["id"] == "fixture-second":
                raise KeyboardInterrupt
            return original_export(binding, timeout_seconds)

        with mock.patch.object(
            interrupted,
            "_run_export",
            side_effect=interrupt_second,
        ), self.assertRaises(KeyboardInterrupt):
            interrupted.synchronize(
                "interrupted-collection",
                project_ids=["fixture-project", "fixture-second"],
                timeout_seconds=2,
            )

        store = MetricStore(self.fixture.hub, read_only=True)
        before = store.sync_status(["fixture-project", "fixture-second"])
        self.assertEqual(
            before["fixture-project"]["latest_attempt"]["status"],
            "success",
        )
        self.assertEqual(
            before["fixture-second"]["latest_attempt"]["status"],
            "running",
        )

        resumed = MetricSyncCoordinator(
            self.fixture.hub,
            clock=lambda: next(times),
        )
        resumed_projects = []
        resumed_export = resumed._run_export

        def record_export(binding, timeout_seconds):
            resumed_projects.append(binding["project"]["id"])
            return resumed_export(binding, timeout_seconds)

        with mock.patch.object(
            resumed,
            "_run_export",
            side_effect=record_export,
        ):
            result = resumed.synchronize(
                "interrupted-collection",
                project_ids=["fixture-project", "fixture-second"],
                timeout_seconds=2,
            )
        self.assertTrue(result["complete"])
        self.assertEqual(
            result["results"]["fixture-project"]["status"],
            "reused",
        )
        self.assertEqual(resumed_projects, ["fixture-second"])

        reopened = MetricStore(self.fixture.hub, read_only=True)
        history = reopened.sync_attempts("fixture-second")
        self.assertEqual(
            [attempt["status"] for attempt in history["items"]],
            ["failed", "success"],
        )
        self.assertEqual(history["items"][0]["error"], "sync_interrupted")
        self.assertEqual(
            history["items"][0]["finished_at"],
            "2026-09-11T07:01:00+00:00",
        )
        self.assertEqual(
            reopened.sync_attempts("fixture-project")["returned"],
            1,
        )

    def test_interrupted_import_transaction_accepts_new_snapshot_on_same_request(self):
        times = iter(
            [
                "2026-09-11T08:00:00+00:00",
                "2026-09-11T08:00:01+00:00",
                "2026-09-11T08:01:00+00:00",
                "2026-09-11T08:01:01+00:00",
            ]
        )
        coordinator = MetricSyncCoordinator(
            self.fixture.hub,
            clock=lambda: next(times),
        )
        with mock.patch.object(
            MetricStore,
            "_fault",
            side_effect=KeyboardInterrupt,
        ), self.assertRaises(KeyboardInterrupt):
            coordinator.synchronize(
                "interrupted-import",
                project_ids=["fixture-project"],
                timeout_seconds=2,
            )

        unit_request_id = coordinator._unit_request_id(
            "interrupted-import",
            "fixture-project",
        )
        store = MetricStore(self.fixture.hub, read_only=True)
        self.assertIsNone(store.receipt(unit_request_id, "fixture-project"))
        connection = store._connect()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM metric_requests WHERE id=?",
                    (unit_request_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()
        self.assertEqual(
            store.sync_status(["fixture-project"])["fixture-project"][
                "latest_attempt"
            ]["status"],
            "running",
        )

        changed_script = EXPORT_SCRIPT.replace(
            "2026-09-11T04:00:00+00:00",
            "2026-09-11T04:01:00+00:00",
        )
        (self.project / EXPORT_ENTRY).write_text(
            changed_script,
            encoding="utf-8",
        )
        resumed = MetricSyncCoordinator(
            self.fixture.hub,
            clock=lambda: next(times),
        ).synchronize(
            "interrupted-import",
            project_ids=["fixture-project"],
            timeout_seconds=2,
        )
        self.assertTrue(resumed["complete"])
        self.assertEqual(
            resumed["results"]["fixture-project"]["status"],
            "imported",
        )
        reopened = MetricStore(self.fixture.hub, read_only=True)
        attempts = reopened.sync_attempts("fixture-project")["items"]
        self.assertEqual(
            [attempt["status"] for attempt in attempts],
            ["failed", "success"],
        )
        self.assertEqual(attempts[0]["error"], "sync_interrupted")
        self.assertEqual(
            attempts[1]["snapshot"]["observed_at"],
            "2026-09-11T04:01:00+00:00",
        )
        self.assertEqual(
            {attempt["request_id"] for attempt in attempts},
            {"interrupted-import"},
        )

    def test_pre_a2_orphan_request_rebinds_only_without_a_receipt(self):
        coordinator = MetricSyncCoordinator(self.fixture.hub)
        binding = coordinator._binding(
            coordinator._resolver(),
            "fixture-project",
        )
        coordinator._run_export(binding, 2)
        original = json.loads(
            (self.project / ".hub/status.json").read_text(encoding="utf-8")
        )
        unit_request_id = coordinator._unit_request_id(
            "legacy-orphan",
            "fixture-project",
        )
        old_identity = content_hash(
            [
                "project_metric_snapshot",
                original["schema_version"],
                original["snapshot_id"],
                original["exporter"],
            ]
        )
        store = MetricStore(self.fixture.hub)
        store.begin(
            unit_request_id,
            ["fixture-project"],
            old_identity,
        )
        self.assertIsNone(
            store.receipt(unit_request_id, "fixture-project")
        )

        (self.project / EXPORT_ENTRY).write_text(
            EXPORT_SCRIPT.replace(
                "2026-09-11T04:00:00+00:00",
                "2026-09-11T04:02:00+00:00",
            ),
            encoding="utf-8",
        )
        resumed = MetricSyncCoordinator(self.fixture.hub).synchronize(
            "legacy-orphan",
            project_ids=["fixture-project"],
            timeout_seconds=2,
        )
        self.assertTrue(resumed["complete"])
        self.assertEqual(
            resumed["results"]["fixture-project"]["status"],
            "imported",
        )
        current = json.loads(
            (self.project / ".hub/status.json").read_text(encoding="utf-8")
        )
        self.assertNotEqual(current["snapshot_id"], original["snapshot_id"])
        reopened = MetricStore(self.fixture.hub, read_only=True)
        self.assertIsNotNone(
            reopened.receipt(unit_request_id, "fixture-project")
        )
        self.assertEqual(
            [
                attempt["status"]
                for attempt in reopened.sync_attempts(
                    "fixture-project"
                )["items"]
            ],
            ["success"],
        )

    def test_persistence_failure_is_not_reported_as_snapshot_failure(self):
        with mock.patch.object(
            MetricStore,
            "_fault",
            side_effect=RefreshLedgerError("injected persistence failure"),
        ):
            result = self.coordinator.synchronize(
                "store-failure",
                project_ids=["fixture-project"],
                timeout_seconds=2,
            )
        self.assertEqual(
            result["errors"],
            {"fixture-project": "sync_attempt_store_failed"},
        )
        store = MetricStore(self.fixture.hub, read_only=True)
        status = store.sync_status(["fixture-project"])["fixture-project"]
        self.assertEqual(status["latest_attempt"]["status"], "failed")
        self.assertEqual(
            status["latest_attempt"]["error"],
            "sync_attempt_store_failed",
        )
        self.assertIsNone(status["last_success"])
        self.assertIsNone(
            store.receipt(
                self.coordinator._unit_request_id(
                    "store-failure",
                    "fixture-project",
                ),
                "fixture-project",
            )
        )

    def test_status_query_materializes_only_latest_and_last_success(self):
        first = self.coordinator.synchronize(
            "bounded-status-history",
            project_ids=["fixture-project"],
            timeout_seconds=2,
        )
        self.assertTrue(first["complete"])
        store = MetricStore(self.fixture.hub)
        start = datetime(2026, 9, 12, tzinfo=timezone.utc)
        for index in range(75):
            attempted_at = start + timedelta(seconds=index * 2)
            sequence = store.begin_sync_attempt(
                f"bounded-history-{index}",
                "fixture-project",
                attempted_at.isoformat(),
            )
            store.finish_sync_attempt(
                sequence,
                (attempted_at + timedelta(seconds=1)).isoformat(),
                "export_timeout",
            )

        with mock.patch.object(
            store,
            "_attempt_from_row",
            wraps=store._attempt_from_row,
        ) as deserialize:
            status = store.sync_status(["fixture-project"])[
                "fixture-project"
            ]
        self.assertLessEqual(deserialize.call_count, 2)
        self.assertEqual(status["latest_attempt"]["status"], "failed")
        self.assertEqual(status["last_success"]["status"], "success")
        self.assertEqual(
            store.sync_attempts("fixture-project", limit=50)[
                "total_remaining"
            ],
            76,
        )

    def test_removed_project_is_skipped_before_root_access(self):
        self.fixture.projects[0]["current_state_status"] = "removed_local"
        self.fixture.projects[0]["root_path"] = "/definitely/not/visited"
        self.fixture.save_registry()
        result = MetricSyncCoordinator(self.fixture.hub).synchronize(
            "removed-fixture",
            project_ids=["fixture-project"],
        )
        self.assertTrue(result["complete"])
        self.assertEqual(
            result["skipped"],
            {"fixture-project": "removed_local"},
        )
        status = MetricStore(
            self.fixture.hub,
            read_only=True,
        ).sync_status(["fixture-project"])["fixture-project"]
        self.assertEqual(status["latest_attempt"]["status"], "skipped")
        self.assertEqual(status["latest_attempt"]["error"], "removed_local")

    def test_missing_export_declaration_skips_without_running_source_or_validation(self):
        declaration = yaml.safe_load(
            (self.project / "hub.connection.yaml").read_text(encoding="utf-8")
        )
        del declaration["metric_export"]
        declaration["source_refs"][0]["path"] = "missing-state.yaml"
        declaration["validation_entry"] = ["python3 scripts/must-not-run.py"]
        (self.project / "hub.connection.yaml").write_text(
            yaml.safe_dump(declaration, sort_keys=False),
            encoding="utf-8",
        )
        with mock.patch.object(
            self.coordinator,
            "_run_export",
            side_effect=AssertionError("undeclared exporter ran"),
        ):
            result = self.coordinator.synchronize(
                "missing-export-fixture",
                project_ids=["fixture-project"],
            )
        self.assertTrue(result["complete"])
        self.assertEqual(
            result["skipped"],
            {"fixture-project": "metric_export_not_declared"},
        )

    def test_plain_python_start_and_manual_commands_are_cache_first(self):
        base = [
            sys.executable,
            "-I",
            str(ROOT / "hub.py"),
        ]

        def run(command, request):
            completed = subprocess.run(
                [
                    *base,
                    command,
                    "--root",
                    str(self.fixture.hub),
                    "--request-id",
                    request,
                    "--project-id",
                    "fixture-project",
                    "--timeout-seconds",
                    "2",
                ],
                cwd=ROOT,
                env={
                    "PATH": "",
                    "PYTHONNOUSERSITE": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            events = [json.loads(line) for line in completed.stdout.splitlines()]
            self.assertEqual([event["phase"] for event in events], ["cache", "sync"])
            return events

        startup = run("start", "plain-startup")
        manual = run("sync", "plain-manual")
        self.assertEqual(startup[0]["mode"], "startup")
        self.assertEqual(manual[0]["mode"], "manual")
        self.assertTrue(startup[1]["data"]["complete"])
        self.assertTrue(manual[0]["data"]["available"])
        self.assertEqual(
            manual[1]["data"]["results"]["fixture-project"]["receipt"][
                "changed_metrics"
            ],
            0,
        )


if __name__ == "__main__":
    unittest.main()
