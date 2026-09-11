"""Isolated startup/manual synchronization checks; no real project is executed."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest import mock
import unittest

import yaml

from connection_fixtures import Fixture
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


class MetricSyncTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)
        (self.fixture.hub / "src").symlink_to(ROOT / "src", target_is_directory=True)
        self.project, declaration = self.fixture.add("fixture-project")
        declaration["metric_export"] = {
            "entry": EXPORT_ENTRY,
            "snapshot": ".hub/status.json",
        }
        (self.project / "hub.connection.yaml").write_text(
            yaml.safe_dump(declaration, sort_keys=False),
            encoding="utf-8",
        )
        entry = self.project / EXPORT_ENTRY
        entry.parent.mkdir()
        entry.write_text(EXPORT_SCRIPT, encoding="utf-8")
        self.coordinator = MetricSyncCoordinator(self.fixture.hub)

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

        def guarded_import(store, request_id, project_root, *, expected_project):
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
