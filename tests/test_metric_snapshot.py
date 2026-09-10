import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

from hub.connection_records import RecordError, content_hash, record_schema
from hub.connection_refresh import PrecommitFaultError
from hub.metric_export import export_metric_snapshot
from hub.metric_import import (
    import_metric_snapshot,
    import_metric_snapshot_file,
    import_metric_snapshots,
    read_metric_snapshot,
)
from hub.metric_snapshot import metric_snapshot_contract_schema, validate_metric_snapshot
from hub.metric_store import MetricStore
from hub.metrics import metric


NOW = "2026-09-10T00:00:00Z"
LATER = "2026-09-10T00:01:00Z"


class MetricSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project_root = self.root / "project"
        self.project_root.mkdir()
        self.raw_source = self.project_root / "facts.json"
        self.raw_source.write_text('{"done":3}')

    @staticmethod
    def collect_business(root, project_id, observed_at):
        data = json.loads((root / "facts.json").read_text())
        version = content_hash(data)
        return {
            "metrics": [
                metric(
                    project_id,
                    "work.done",
                    data["done"],
                    "items",
                    "facts.json#done",
                    version,
                    observed_at,
                    counting_basis="Unique completed fixture item IDs",
                )
            ],
            "issues": [],
            "disposition": "resolved",
            "source_version": version,
        }

    def export(self, observed_at=NOW):
        return export_metric_snapshot(
            self.project_root,
            "p",
            {"business": self.collect_business},
            exporter_id="fixture-export",
            exporter_version="1.0",
            clock=lambda: observed_at,
        )

    def exported_project(self, project_id, value):
        root = self.root / project_id
        root.mkdir()
        (root / "facts.json").write_text(json.dumps({"done": value}))

        def collect(project_root, pid, observed_at):
            data = json.loads((project_root / "facts.json").read_text())
            version = content_hash(data)
            return {
                "metrics": [
                    metric(
                        pid,
                        "work.done",
                        data["done"],
                        "items",
                        "facts.json#done",
                        version,
                        observed_at,
                        counting_basis="Unique completed fixture item IDs",
                    )
                ],
                "issues": [],
                "disposition": "resolved",
                "source_version": version,
            }

        export_metric_snapshot(
            root,
            project_id,
            {"business": collect},
            exporter_id="fixture-export",
            exporter_version="1.0",
            clock=lambda: NOW,
        )
        return root

    def test_plain_python_export_then_snapshot_only_import(self):
        snapshot = self.export()
        snapshot_path = self.project_root / ".hub/status.json"
        self.assertTrue(snapshot_path.is_file())

        # A Hub importer must still succeed when the business source is gone and
        # must open only the one canonical snapshot file in the project.
        self.raw_source.unlink()
        hub_root = self.root / "hub"
        hub_root.mkdir()
        store = MetricStore(hub_root)
        real_open = Path.open
        opened = []

        def snapshot_only(path, *args, **kwargs):
            opened.append(path)
            if path.resolve() != snapshot_path.resolve():
                raise AssertionError("importer opened a non-snapshot project file")
            return real_open(path, *args, **kwargs)

        with patch.object(Path, "open", snapshot_only):
            receipt = import_metric_snapshot_file(
                store,
                "snapshot-request",
                self.project_root,
                expected_project={"id": "p", "name": "Fixture"},
            )

        self.assertEqual(opened, [snapshot_path])
        self.assertEqual(receipt["changed_metrics"], 1)
        saved = store.page(project_id="p")["items"][0]["metric"]
        self.assertEqual(saved["value"], 3)
        self.assertEqual(read_metric_snapshot(self.project_root), snapshot)

    def test_contract_is_shared_and_atomic_replacement_leaves_no_temp(self):
        first = self.export()
        self.raw_source.write_text('{"done":4}')
        second = self.export(LATER)

        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual(read_metric_snapshot(self.project_root)["metrics"][0]["value"], 4)
        self.assertEqual(list((self.project_root / ".hub").glob(".status.*.tmp")), [])
        self.assertEqual(
            record_schema()["metric_snapshot_contract"],
            metric_snapshot_contract_schema(),
        )

        unknown = copy.deepcopy(second)
        unknown["raw_source"] = "facts.json"
        with self.assertRaises(RecordError):
            validate_metric_snapshot(unknown)
        forged = copy.deepcopy(second)
        forged["metric_definitions"][0]["display_name"] = "forged"
        with self.assertRaises(RecordError):
            validate_metric_snapshot(forged)

    def test_exact_byte_limit_round_trips_without_writer_overhead(self):
        snapshot = self.export()
        payload = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        with patch("hub.metric_snapshot.MAX_SNAPSHOT_BYTES", len(payload)), patch(
            "hub.metric_import.MAX_SNAPSHOT_BYTES", len(payload)
        ):
            self.export()
            self.assertEqual(
                (self.project_root / ".hub/status.json").stat().st_size,
                len(payload),
            )
            self.assertEqual(read_metric_snapshot(self.project_root), snapshot)

    def test_import_rejects_snapshot_symlink(self):
        hub_dir = self.project_root / ".hub"
        hub_dir.mkdir()
        (self.project_root / "other.json").write_text("{}")
        (hub_dir / "status.json").symlink_to("../other.json")
        with self.assertRaises(RecordError):
            read_metric_snapshot(self.project_root)

    def test_export_isolates_one_expected_collector_failure(self):
        def unavailable(root, project_id, observed_at):
            raise OSError("fixture source is unavailable")

        snapshot = export_metric_snapshot(
            self.project_root,
            "p",
            {"business": self.collect_business, "validation": unavailable},
            exporter_id="fixture-export",
            exporter_version="1.0",
            clock=lambda: NOW,
        )

        self.assertEqual(snapshot["disposition"], "partial")
        self.assertEqual(snapshot["metrics"][0]["value"], 3)
        self.assertEqual(snapshot["issues"][0]["code"], "collector_failure")
        self.assertEqual(set(snapshot["source_versions"]), {"business", "validation"})

    def test_repeated_snapshot_never_duplicates_metric_history(self):
        snapshot = self.export()
        hub_root = self.root / "hub"
        hub_root.mkdir()
        store = MetricStore(hub_root)
        project = {"id": "p"}

        first = import_metric_snapshot(
            store, "same-request", snapshot, expected_project=project
        )
        repeated = import_metric_snapshot(
            store, "same-request", snapshot, expected_project=project
        )
        another_request = import_metric_snapshot(
            store, "new-request", snapshot, expected_project=project
        )

        self.assertEqual(repeated, first)
        self.assertEqual(another_request["changed_metrics"], 0)
        self.assertEqual(store.validate()["metric_versions"], 1)

    def test_file_and_metric_transaction_half_writes_roll_back(self):
        original = self.export()
        snapshot_path = self.project_root / ".hub/status.json"
        original_bytes = snapshot_path.read_bytes()
        self.raw_source.write_text('{"done":4}')
        with patch("hub.metric_export.os.replace", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                self.export(LATER)
        self.assertEqual(snapshot_path.read_bytes(), original_bytes)
        self.assertEqual(list((self.project_root / ".hub").glob(".status.*.tmp")), [])

        hub_root = self.root / "hub"
        hub_root.mkdir()

        def fail(operation, context):
            if operation == "save_metric_result":
                raise RuntimeError("injected")

        faulty = MetricStore(hub_root, precommit_fault=fail)
        with self.assertRaises(PrecommitFaultError):
            import_metric_snapshot(
                faulty, "transaction-fault", original, expected_project={"id": "p"}
            )
        reopened = MetricStore(hub_root)
        self.assertIsNone(reopened.receipt("transaction-fault", "p"))
        self.assertEqual(reopened.validate()["metric_versions"], 0)
        receipt = import_metric_snapshot(
            reopened, "transaction-fault", original, expected_project={"id": "p"}
        )
        self.assertEqual(receipt["changed_metrics"], 1)

    def test_incompatible_snapshot_version_never_reaches_ledger(self):
        snapshot = self.export()
        incompatible = copy.deepcopy(snapshot)
        incompatible["schema_version"] = "2.0"
        incompatible["snapshot_id"] = content_hash(
            {
                key: value
                for key, value in incompatible.items()
                if key != "snapshot_id"
            }
        )
        hub_root = self.root / "hub"
        hub_root.mkdir()
        store = MetricStore(hub_root)

        with self.assertRaises(RecordError):
            import_metric_snapshot(
                store,
                "incompatible",
                incompatible,
                expected_project={"id": "p"},
            )
        self.assertIsNone(store.receipt("incompatible", "p"))
        self.assertEqual(store.validate()["metric_versions"], 0)

    def test_batch_timeout_does_not_block_and_retry_reads_only_failed_unit(self):
        roots = {
            "p-a": self.exported_project("p-a", 1),
            "p-b": self.exported_project("p-b", 2),
        }
        projects = {project_id: {"id": project_id} for project_id in roots}
        hub_root = self.root / "hub"
        hub_root.mkdir()
        store = MetricStore(hub_root)
        real_read = read_metric_snapshot
        calls = []

        def slow_first(root):
            calls.append(Path(root).name)
            if Path(root).name == "p-a":
                time.sleep(0.2)
            return real_read(root)

        with patch("hub.metric_import.read_metric_snapshot", side_effect=slow_first):
            partial = import_metric_snapshots(
                store,
                "timeout-batch",
                roots,
                expected_projects=projects,
                timeout_seconds=0.02,
            )
        self.assertEqual(partial["errors"], {"p-a": "snapshot_timeout"})
        self.assertEqual(set(partial["receipts"]), {"p-b"})
        self.assertFalse(partial["complete"])

        calls.clear()
        with patch(
            "hub.metric_import.read_metric_snapshot", wraps=real_read
        ) as resumed_read:
            completed = import_metric_snapshots(
                store,
                "timeout-batch",
                roots,
                expected_projects=projects,
                timeout_seconds=0.02,
            )
        self.assertTrue(completed["complete"])
        self.assertEqual(
            [Path(call.args[0]).name for call in resumed_read.call_args_list],
            ["p-a"],
        )

    def test_batch_interruption_commits_prior_unit_and_resume_skips_it(self):
        roots = {
            "p-a": self.exported_project("p-a", 1),
            "p-b": self.exported_project("p-b", 2),
        }
        projects = {project_id: {"id": project_id} for project_id in roots}
        hub_root = self.root / "hub"
        hub_root.mkdir()
        store = MetricStore(hub_root)
        real_read = read_metric_snapshot

        def interrupt_second(root):
            if Path(root).name == "p-b":
                raise RuntimeError("injected interruption")
            return real_read(root)

        with patch(
            "hub.metric_import.read_metric_snapshot", side_effect=interrupt_second
        ):
            with self.assertRaises(RuntimeError):
                import_metric_snapshots(
                    store,
                    "interrupted-batch",
                    roots,
                    expected_projects=projects,
                )
        self.assertIsNotNone(store.receipt("interrupted-batch", "p-a"))
        self.assertIsNone(store.receipt("interrupted-batch", "p-b"))

        with patch(
            "hub.metric_import.read_metric_snapshot", wraps=real_read
        ) as resumed_read:
            completed = import_metric_snapshots(
                store,
                "interrupted-batch",
                roots,
                expected_projects=projects,
            )
        self.assertTrue(completed["complete"])
        self.assertEqual(
            [Path(call.args[0]).name for call in resumed_read.call_args_list],
            ["p-b"],
        )

    def test_batch_uses_frozen_roots_and_project_bindings_after_begin(self):
        original_root = self.exported_project("p-a", 1)
        alternate_root = self.root / "alternate"
        alternate_root.mkdir()
        (alternate_root / "facts.json").write_text('{"done":99}')
        export_metric_snapshot(
            alternate_root,
            "p-a",
            {"business": self.collect_business},
            exporter_id="fixture-export",
            exporter_version="1.0",
            clock=lambda: NOW,
        )
        roots = {"p-a": original_root}
        original_project = {"id": "p-a", "name": "Original"}
        projects = {"p-a": copy.deepcopy(original_project)}
        hub_root = self.root / "hub"
        hub_root.mkdir()
        store = MetricStore(hub_root)
        real_begin = store.begin

        def mutate_after_begin(*args, **kwargs):
            result = real_begin(*args, **kwargs)
            roots["p-a"] = alternate_root
            projects["p-a"]["name"] = "Mutated"
            return result

        with patch.object(store, "begin", side_effect=mutate_after_begin):
            result = import_metric_snapshots(
                store,
                "frozen-inputs",
                roots,
                expected_projects=projects,
            )

        self.assertTrue(result["complete"])
        self.assertEqual(
            store.page(project_id="p-a")["items"][0]["metric"]["value"],
            1,
        )
        coverage = store.coverage({"projects": [original_project]}, now=NOW)
        self.assertEqual(coverage["projects"][0]["freshness"], "fresh")

    def test_batch_uses_frozen_project_set_after_begin(self):
        roots = {"p-a": self.exported_project("p-a", 1)}
        projects = {"p-a": {"id": "p-a"}}
        hub_root = self.root / "hub"
        hub_root.mkdir()
        store = MetricStore(hub_root)
        real_begin = store.begin

        def clear_after_begin(*args, **kwargs):
            result = real_begin(*args, **kwargs)
            roots.clear()
            projects.clear()
            return result

        with patch.object(store, "begin", side_effect=clear_after_begin):
            result = import_metric_snapshots(
                store,
                "frozen-project-set",
                roots,
                expected_projects=projects,
            )

        self.assertEqual(result["requested"], 1)
        self.assertEqual(result["completed"], 1)
        self.assertTrue(result["complete"])
        self.assertEqual(set(result["receipts"]), {"p-a"})

    def test_fresh_plain_python_process_needs_no_agent_mcp_or_site_package(self):
        hub_root = self.root / "hub"
        hub_root.mkdir()
        source_root = Path(__file__).resolve().parents[1] / "src"
        program = textwrap.dedent(
            """
            import json
            from pathlib import Path

            from hub.connection_records import content_hash
            from hub.metric_export import export_metric_snapshot
            from hub.metric_import import import_metric_snapshot_file
            from hub.metric_store import MetricStore
            from hub.metrics import metric

            project_root = Path({project_root!r})
            hub_root = Path({hub_root!r})

            def collect(root, project_id, observed_at):
                data = json.loads((root / "facts.json").read_text())
                version = content_hash(data)
                row = metric(
                    project_id, "work.done", data["done"], "items",
                    "facts.json#done", version, observed_at,
                    counting_basis="Unique completed fixture item IDs",
                )
                return dict(
                    metrics=[row], issues=[], disposition="resolved",
                    source_version=version,
                )

            export_metric_snapshot(
                project_root, "p", {{"business": collect}},
                exporter_id="fixture-export", exporter_version="1.0",
                clock=lambda: {observed_at!r},
            )
            (project_root / "facts.json").unlink()
            receipt = import_metric_snapshot_file(
                MetricStore(hub_root), "plain-python", project_root,
                expected_project={{"id": "p"}},
            )
            print(json.dumps(receipt, sort_keys=True))
            """
        ).format(
            project_root=str(self.project_root),
            hub_root=str(hub_root),
            observed_at=NOW,
        )
        environment = {
            "PATH": os.environ["PATH"],
            "PYTHONPATH": str(source_root),
            "PYTHONSAFEPATH": "1",
        }
        result = subprocess.run(
            [sys.executable, "-S", "-c", program],
            cwd=self.root,
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["changed_metrics"], 1)


if __name__ == "__main__":
    unittest.main()
