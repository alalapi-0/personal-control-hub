from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hub.design_export import DesignExportError, export_bundle
from hub.design_records import SCHEMA_VERSION, content_hash, with_content_hash
from hub.design_store import DesignStore


NOW = "2026-09-05T12:00:00+08:00"


class DesignExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.material_dir = self.root / "data/design_governance/material"
        self.material_dir.mkdir(parents=True)
        self.store_path = self.root / "data/design_governance/fixture-store.json"
        self.output = self.root / "docs/reports/ui_design_governance/unit-02/exports/synthetic_fixture/candidate.zip"
        self.scope = {"family_id": None, "members": [{"project_id": "fixture-project", "pages": ["review"]}]}
        self.baseline_bytes = b"synthetic baseline material\n"
        self.candidate_bytes = b"synthetic candidate material\n"
        (self.material_dir / "baseline.txt").write_bytes(self.baseline_bytes)
        (self.material_dir / "candidate.txt").write_bytes(self.candidate_bytes)
        self.store, self.projection = self.make_snapshot(selected=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def artifact(self, artifact_id: str, filename: str, data: bytes, *, scope=None) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "artifact_ref",
            "id": artifact_id,
            "created_at": NOW,
            "classification": "mock",
            "location": {"kind": "hub_relative", "value": f"data/design_governance/material/{filename}"},
            "sha256": self.digest(data),
            "provenance": {"method": "synthetic test material", "source_refs": ["fixture://tc2-test"]},
            "scope": self.scope if scope is None else scope,
        }

    def make_snapshot(self, *, selected: bool) -> tuple[dict, dict]:
        baseline_artifact = self.artifact("baseline-material", "baseline.txt", self.baseline_bytes)
        candidate_artifact = self.artifact("candidate-material", "candidate.txt", self.candidate_bytes)
        figma_artifact = {
            "schema_version": SCHEMA_VERSION,
            "kind": "artifact_ref",
            "id": "figma-pointer",
            "created_at": NOW,
            "classification": "mock",
            "location": {"kind": "figma", "value": "figma://offline-file/node-1?v=7"},
            "sha256": None,
            "provenance": {"method": "offline synthetic pointer", "source_refs": ["fixture://tc2-test"]},
            "scope": self.scope,
        }
        baseline = with_content_hash({
            "schema_version": SCHEMA_VERSION,
            "kind": "baseline",
            "id": "fixture-baseline",
            "created_at": NOW,
            "classification": "mock",
            "project_id": "fixture-project",
            "revision": 1,
            "content_hash": "",
            "source": {"kind": "new_surface_spec", "reference": "fixture://baseline/1", "commit": None, "dirty_fingerprint": None, "observed_at": NOW},
            "scope": {"pages": ["review"], "flows": ["compare"], "viewport": {"width": 390, "height": 844, "platform": "mobile"}},
            "behaviors": [],
            "data_contract_refs": ["fixture://contract"],
            "unverified": ["synthetic only"],
            "artifact_bindings": [{"artifact_id": baseline_artifact["id"], "sha256": baseline_artifact["sha256"]}],
        })
        candidate = with_content_hash({
            "schema_version": SCHEMA_VERSION,
            "kind": "candidate",
            "id": "fixture-candidate",
            "created_at": NOW,
            "classification": "mock",
            "revision": 1,
            "content_hash": "",
            "scope": self.scope,
            "baseline_bindings": [{"project_id": "fixture-project", "baseline_id": baseline["id"], "baseline_revision": 1, "baseline_hash": baseline["content_hash"], "pages": ["review"]}],
            "visual": {"tokens": ["fixture-token"], "components": ["review-card"], "structure": ["single-page"], "differences": ["synthetic delta"]},
            "figma_ref": {"file_key": "offline-fixture", "node_id": "node-1", "version": "7", "offline": True},
            "artifact_bindings": [{"artifact_id": candidate_artifact["id"], "sha256": candidate_artifact["sha256"]}],
            "evidence_refs": ["figma-pointer"],
        })
        candidate_ref = {"id": candidate["id"], "revision": 1, "content_hash": candidate["content_hash"]}
        review = {
            "schema_version": SCHEMA_VERSION,
            "kind": "review",
            "id": "fixture-review",
            "created_at": NOW,
            "candidate": candidate_ref,
            "functional_invariants": ["no external effect"],
            "lanes": [{"name": "visual", "result": "PASS", "reason": "synthetic check", "evidence_refs": []}],
            "tool_limits": ["offline"],
            "reviewer": {"type": "agent", "reference": "fixture-reviewer"},
            "evidence_refs": [],
        }
        events = []
        history = []
        effective = {}
        if selected:
            feedback = self.event("fixture-feedback", "request_changes", candidate_ref, "Please increase synthetic spacing")
            selection = self.event("fixture-selection", "select", candidate_ref, "Synthetic choice", supersedes=feedback["id"])
            events = [feedback, selection]
            history = [
                {"sequence": 1, "event": feedback, "stale": False, "stale_reasons": [], "superseded": True},
                {"sequence": 2, "event": selection, "stale": False, "stale_reasons": [], "superseded": False},
            ]
            effective = {content_hash(self.scope): history[1]}
        facts = [baseline_artifact, candidate_artifact, figma_artifact, baseline, candidate, review]
        store = {"schema_version": "1.0", "kind": "design_store", "store_classification": "synthetic_fixture", "revision": 9, "facts": facts, "events": events, "requests": []}
        projection = {"store_revision": 9, "store_classification": "synthetic_fixture", "history": history, "effective": effective, "queues": {}}
        return store, projection

    def event(self, event_id: str, action: str, candidate_ref: dict, feedback: str, *, supersedes=None) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "decision_event",
            "id": event_id,
            "request_id": f"request-{event_id}",
            "created_at": NOW,
            "source": {"type": "synthetic_fixture", "reference": "fixture://tc2-test", "trusted_owner": False, "fixture": True},
            "action": action,
            "candidate": candidate_ref,
            "scope": self.scope,
            "feedback": feedback,
            "supersedes": supersedes,
        }

    def export(self, **overrides):
        arguments = dict(hub_root=self.root, store_path=self.store_path, fixture=True, store=self.store, projection=self.projection, candidate_id="fixture-candidate", candidate_revision=1, output_path=self.output)
        arguments.update(overrides)
        return export_bundle(**arguments)

    def append(self, design_store: DesignStore, fact: dict, request_id: str) -> None:
        revision = design_store.read()["revision"]
        design_store.append_fact(fact, expected_revision=revision, request_id=request_id)

    def make_family_store(self) -> tuple[DesignStore, Path]:
        family_scope = {
            "family_id": "fixture-family",
            "members": [
                {"project_id": "fixture-project", "pages": ["review"]},
                {"project_id": "fixture-project-b", "pages": ["detail"]},
            ],
        }
        material_dir = self.root / "docs/reports/ui_design_governance/unit-02/material"
        material_dir.mkdir(parents=True, exist_ok=True)
        payloads = {
            "family-source": b"synthetic family source\n",
            "baseline-a": b"synthetic project A baseline\n",
            "baseline-b": b"synthetic project B baseline\n",
            "family-candidate": b"synthetic family candidate\n",
        }
        for name, payload in payloads.items():
            (material_dir / f"{name}.txt").write_bytes(payload)

        def artifact(artifact_id: str, scope: dict, *, figma: bool = False, family_ref: dict | None = None) -> dict:
            result = {
                "schema_version": SCHEMA_VERSION,
                "kind": "artifact_ref",
                "id": artifact_id,
                "created_at": NOW,
                "classification": "mock",
                "location": {"kind": "figma" if figma else "hub_relative", "value": f"figma://fixture/{artifact_id}" if figma else f"docs/reports/ui_design_governance/unit-02/material/{artifact_id}.txt"},
                "sha256": None if figma else self.digest(payloads[artifact_id]),
                "provenance": {"method": "synthetic family fixture", "source_refs": [f"fixture://{artifact_id}"]},
                "scope": scope,
            }
            if family_ref is not None:
                result["family_binding"] = family_ref
            return result

        scope_a = {"family_id": None, "members": [{"project_id": "fixture-project", "pages": ["review"]}]}
        scope_b = {"family_id": None, "members": [{"project_id": "fixture-project-b", "pages": ["detail"]}]}
        family_source = artifact("family-source", scope_a)
        baseline_artifact_a = artifact("baseline-a", scope_a)
        baseline_artifact_b = artifact("baseline-b", scope_b)
        baseline_a = with_content_hash({
            "schema_version": SCHEMA_VERSION, "kind": "baseline", "id": "fixture-baseline-a", "created_at": NOW,
            "classification": "mock", "project_id": "fixture-project", "revision": 1, "content_hash": "",
            "source": {"kind": "new_surface_spec", "reference": "fixture://baseline/a", "commit": None, "dirty_fingerprint": None, "observed_at": NOW},
            "scope": {"pages": ["review"], "flows": [], "viewport": {"width": 390, "height": 844, "platform": "mobile"}},
            "behaviors": [], "data_contract_refs": [], "unverified": ["synthetic"],
            "artifact_bindings": [{"artifact_id": "baseline-a", "sha256": baseline_artifact_a["sha256"]}],
        })
        baseline_b = with_content_hash({
            "schema_version": SCHEMA_VERSION, "kind": "baseline", "id": "fixture-baseline-b", "created_at": NOW,
            "classification": "mock", "project_id": "fixture-project-b", "revision": 1, "content_hash": "",
            "source": {"kind": "new_surface_spec", "reference": "fixture://baseline/b", "commit": None, "dirty_fingerprint": None, "observed_at": NOW},
            "scope": {"pages": ["detail"], "flows": [], "viewport": {"width": 390, "height": 844, "platform": "mobile"}},
            "behaviors": [], "data_contract_refs": [], "unverified": ["synthetic"],
            "artifact_bindings": [{"artifact_id": "baseline-b", "sha256": baseline_artifact_b["sha256"]}],
        })
        family = with_content_hash({
            "schema_version": SCHEMA_VERSION, "kind": "design_family", "id": "fixture-family", "created_at": NOW,
            "classification": "mock", "revision": 1, "content_hash": "", "scope": family_scope,
            "source": {"reference": "fixture://family/source", "evidence_refs": ["family-source"]},
            "shared_visual_semantics": ["synthetic shared shell"],
            "component_mappings": [{"component_id": "review-card", "members": ["fixture-project", "fixture-project-b"]}],
            "member_exceptions": [],
        })
        family_ref = {"id": family["id"], "revision": family["revision"], "content_hash": family["content_hash"]}
        candidate_artifact = artifact("family-candidate", family_scope, family_ref=family_ref)
        bound_figma = artifact("bound-figma", family_scope, figma=True, family_ref=family_ref)
        unbound_figma = artifact("unbound-figma", family_scope, figma=True, family_ref=family_ref)
        candidate = with_content_hash({
            "schema_version": SCHEMA_VERSION, "kind": "candidate", "id": "fixture-family-candidate", "created_at": NOW,
            "classification": "mock", "revision": 1, "content_hash": "", "scope": family_scope, "family_binding": family_ref,
            "baseline_bindings": [
                {"project_id": "fixture-project", "baseline_id": baseline_a["id"], "baseline_revision": 1, "baseline_hash": baseline_a["content_hash"], "pages": ["review"]},
                {"project_id": "fixture-project-b", "baseline_id": baseline_b["id"], "baseline_revision": 1, "baseline_hash": baseline_b["content_hash"], "pages": ["detail"]},
            ],
            "visual": {"tokens": ["fixture-token"], "components": ["review-card"], "structure": ["two-member family"], "differences": []},
            "figma_ref": {"file_key": "offline-family", "node_id": "node-family", "version": "1", "offline": True},
            "artifact_bindings": [{"artifact_id": "family-candidate", "sha256": candidate_artifact["sha256"]}],
            "evidence_refs": ["bound-figma"],
        })
        candidate_ref = {"id": candidate["id"], "revision": 1, "content_hash": candidate["content_hash"]}
        review = {
            "schema_version": SCHEMA_VERSION, "kind": "review", "id": "fixture-family-review", "created_at": NOW,
            "candidate": candidate_ref, "functional_invariants": ["synthetic only"],
            "lanes": [{"name": "visual", "result": "PASS", "reason": "synthetic", "evidence_refs": ["bound-figma"]}],
            "tool_limits": ["offline"], "reviewer": {"type": "agent", "reference": "fixture-reviewer"}, "evidence_refs": [],
        }
        path = self.root / "docs/reports/ui_design_governance/unit-02/fixture-store.json"
        design_store = DesignStore(self.root, path, fixture=True, fixture_project_ids={"fixture-project", "fixture-project-b"})
        design_store.initialize()
        for index, fact in enumerate((family_source, baseline_artifact_a, baseline_artifact_b, baseline_a, baseline_b, family, candidate_artifact, bound_figma, unbound_figma, candidate, review), start=1):
            self.append(design_store, fact, f"append-{index}")
        event = self.event("fixture-family-selection", "select", candidate_ref, "Synthetic family choice")
        event["scope"] = family_scope
        design_store.append_decision(event, expected_revision=design_store.read()["revision"])
        return design_store, path

    def test_selected_bundle_contains_verified_material_history_and_offline_figma(self) -> None:
        result = self.export()
        self.assertEqual(result["manifest"]["selection_state"], "selected")
        self.assertFalse(result["manifest"]["authority"]["real_selection"])
        with zipfile.ZipFile(self.output) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
            files = {item["record"]["id"]: item for item in manifest["artifact_files"]}
            self.assertEqual(bundle.read(files["baseline-material"]["archive_path"]), self.baseline_bytes)
            self.assertEqual(bundle.read(files["candidate-material"]["archive_path"]), self.candidate_bytes)
            for item in files.values():
                self.assertEqual(hashlib.sha256(bundle.read(item["archive_path"])).hexdigest(), item["sha256"])
        self.assertEqual([item["event"]["id"] for item in manifest["decision_history"]], ["fixture-feedback", "fixture-selection"])
        self.assertEqual(manifest["figma_references"][0]["value"]["file_key"], "offline-fixture")
        self.assertTrue(manifest["figma_references"][0]["offline_only"])
        self.assertEqual(manifest["figma_references"][1]["value"]["id"], "figma-pointer")

    def test_current_unselected_candidate_exports_feedback_free_bundle_without_authority(self) -> None:
        self.store, self.projection = self.make_snapshot(selected=False)
        result = self.export()
        self.assertEqual(result["manifest"]["selection_state"], "unselected")
        self.assertIsNone(result["manifest"]["selection"])
        self.assertFalse(result["manifest"]["authority"]["real_selection"])

    def test_replacing_baseline_material_cannot_preserve_bound_candidate(self) -> None:
        artifact = next(f for f in self.store["facts"] if f["id"] == "baseline-material")
        replacement = b"different synthetic original\n"
        (self.material_dir / "baseline.txt").write_bytes(replacement)
        artifact["sha256"] = self.digest(replacement)
        with self.assertRaisesRegex(DesignExportError, "baseline artifact digest mismatch"):
            self.export()
        self.assertFalse(self.output.exists())

    def test_candidate_or_bound_baseline_drift_rejects_export(self) -> None:
        newer = dict(next(f for f in self.store["facts"] if f.get("kind") == "candidate"))
        newer["revision"] = 2
        newer = with_content_hash(newer)
        self.store["facts"].append(newer)
        with self.assertRaisesRegex(DesignExportError, "candidate revision is stale"):
            self.export()

    def test_later_committed_baseline_id_wins_even_with_lower_revision(self) -> None:
        baseline = next(f for f in self.store["facts"] if f.get("kind") == "baseline")
        baseline["revision"] = 99
        updated = with_content_hash(baseline)
        baseline.clear(); baseline.update(updated)
        candidate = next(f for f in self.store["facts"] if f.get("kind") == "candidate")
        candidate["baseline_bindings"][0].update({"baseline_revision": 99, "baseline_hash": baseline["content_hash"]})
        updated = with_content_hash(candidate)
        candidate.clear(); candidate.update(updated)
        newer_committed = dict(baseline)
        newer_committed["id"] = "fixture-baseline-new-source"
        newer_committed["revision"] = 1
        newer_committed = with_content_hash(newer_committed)
        self.store["facts"].append(newer_committed)
        with self.assertRaisesRegex(DesignExportError, "bound baseline is stale"):
            self.export()

    def test_missing_hash_mismatch_scope_and_binding_mismatch_are_explicit(self) -> None:
        cases = []
        cases.append((lambda: (self.material_dir / "candidate.txt").unlink(), "missing"))
        cases.append((lambda: (self.material_dir / "candidate.txt").write_bytes(b"changed"), "hash mismatch"))
        artifact = next(f for f in self.store["facts"] if f.get("id") == "candidate-material")
        cases.append((lambda: artifact.__setitem__("scope", {"family_id": None, "members": [{"project_id": "other", "pages": ["review"]}]}), "scope"))
        for mutate, message in cases:
            with self.subTest(message=message):
                self.tearDown(); self.setUp()
                artifact = next(f for f in self.store["facts"] if f.get("id") == "candidate-material")
                if message == "scope":
                    artifact["scope"] = {"family_id": None, "members": [{"project_id": "other", "pages": ["review"]}]}
                elif message == "missing":
                    (self.material_dir / "candidate.txt").unlink()
                else:
                    (self.material_dir / "candidate.txt").write_bytes(b"changed")
                with self.assertRaisesRegex(DesignExportError, message):
                    self.export()

    def test_path_traversal_and_output_classification_are_rejected(self) -> None:
        artifact = next(f for f in self.store["facts"] if f.get("id") == "candidate-material")
        artifact["location"]["value"] = "data/design_governance/../secret.txt"
        with self.assertRaisesRegex(DesignExportError, "traversal"):
            self.export()
        with self.assertRaisesRegex(DesignExportError, "classification"):
            self.export(output_path=self.root / "docs/reports/ui_design_governance/unit-02/exports/real/wrong.zip")

    def test_symlink_artifact_and_allowed_directory_escape_are_rejected(self) -> None:
        outside = self.root / "outside.txt"
        outside.write_bytes(self.candidate_bytes)
        source = self.material_dir / "candidate.txt"
        source.unlink()
        source.symlink_to(outside)
        with self.assertRaisesRegex(DesignExportError, "symlink"):
            self.export()
        self.tearDown(); self.setUp()
        exports = self.output.parent
        exports.parent.mkdir(parents=True, exist_ok=True)
        outside_dir = self.root / "outside-dir"
        outside_dir.mkdir()
        exports.symlink_to(outside_dir, target_is_directory=True)
        with self.assertRaisesRegex(DesignExportError, "symlink"):
            self.export()

    def test_existing_destination_is_never_overwritten(self) -> None:
        self.output.parent.mkdir(parents=True)
        self.output.write_bytes(b"prior")
        with self.assertRaisesRegex(DesignExportError, "already exists"):
            self.export()
        self.assertEqual(self.output.read_bytes(), b"prior")

    def test_injected_failure_publishes_nothing_and_cleans_temp(self) -> None:
        with mock.patch.dict(os.environ, {"HUB_DESIGN_EXPORT_FAIL_BEFORE_PUBLISH": "1"}):
            with self.assertRaisesRegex(DesignExportError, "injected failure"):
                self.export()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.output.parent.glob(".candidate.zip.*.tmp")), [])

    def test_two_project_family_exports_through_validated_store_after_restart(self) -> None:
        _store, path = self.make_family_store()
        restarted = DesignStore(
            self.root,
            path,
            fixture=True,
            fixture_project_ids={"fixture-project", "fixture-project-b"},
        )
        output = restarted.hub_root / "docs/reports/ui_design_governance/unit-02/exports/synthetic_fixture/family.zip"
        result = restarted.export("fixture-family-candidate", 1, output)
        self.assertEqual(result["outcome"], "COMMITTED")
        self.assertEqual(result["manifest"]["bound_family"]["id"], "fixture-family")
        self.assertEqual(
            {item["project_id"] for item in result["manifest"]["bound_baselines"]},
            {"fixture-project", "fixture-project-b"},
        )
        self.assertEqual(
            {item["record"]["id"] for item in result["manifest"]["artifact_files"]},
            {"family-source", "baseline-a", "baseline-b", "family-candidate"},
        )
        figma_ids = {
            item["value"].get("id")
            for item in result["manifest"]["figma_references"]
            if item["kind"] == "artifact_ref"
        }
        self.assertEqual(figma_ids, {"bound-figma"})
        self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), result["sha256"])

    def test_directory_fsync_failure_returns_verified_committed_outcome(self) -> None:
        original_fsync = os.fsync

        def fail_directory_fsync(descriptor: int) -> None:
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError(errno.EIO, "synthetic directory fsync failure")
            original_fsync(descriptor)

        with mock.patch("hub.design_export.os.fsync", side_effect=fail_directory_fsync):
            result = self.export()
        self.assertEqual(result["outcome"], "COMMITTED_DURABILITY_UNCONFIRMED")
        self.assertEqual(result["store_revision"], self.store["revision"])
        self.assertEqual(hashlib.sha256(self.output.read_bytes()).hexdigest(), result["sha256"])

    def test_unbound_same_scope_figma_pointer_is_excluded(self) -> None:
        unbound = dict(next(f for f in self.store["facts"] if f.get("id") == "figma-pointer"))
        unbound["id"] = "unbound-figma"
        self.store["facts"].append(unbound)
        manifest = self.export()["manifest"]
        exported = [item["value"].get("id") for item in manifest["figma_references"] if item["kind"] == "artifact_ref"]
        self.assertEqual(exported, ["figma-pointer"])

    def test_mixed_connected_classification_is_rejected(self) -> None:
        self.store, self.projection = self.make_snapshot(selected=False)
        baseline = next(f for f in self.store["facts"] if f.get("kind") == "baseline")
        baseline["classification"] = "dry-run"
        updated = with_content_hash(baseline)
        baseline.clear(); baseline.update(updated)
        candidate = next(f for f in self.store["facts"] if f.get("kind") == "candidate")
        candidate["baseline_bindings"][0]["baseline_hash"] = baseline["content_hash"]
        updated = with_content_hash(candidate)
        candidate.clear(); candidate.update(updated)
        with self.assertRaisesRegex(DesignExportError, "classification.*candidate graph"):
            self.export()

    def test_fifo_material_is_rejected_without_blocking(self) -> None:
        candidate_path = self.material_dir / "candidate.txt"
        candidate_path.unlink()
        os.mkfifo(candidate_path)
        with self.assertRaisesRegex(DesignExportError, "not a regular file"):
            self.export()

    def test_postpublication_verification_failure_has_structured_committed_identity(self) -> None:
        from hub.design_export import _read_material
        def fail_published_read(path, *, artifact_id, **kwargs):
            if artifact_id == "published-export-bundle":
                raise DesignExportError("synthetic published verification denial")
            return _read_material(path, artifact_id=artifact_id, **kwargs)
        with mock.patch("hub.design_export._read_material", side_effect=fail_published_read):
            result = self.export()
        self.assertEqual("COMMITTED_VERIFICATION_FAILED", result["outcome"])
        self.assertTrue(self.output.exists())
        self.assertEqual(self.digest(self.output.read_bytes()), result["expected_sha256"])
        self.assertIsNone(result["sha256"])

    def test_postpublication_cleanup_failure_does_not_hide_committed_export(self) -> None:
        with mock.patch("hub.design_export.os.unlink", side_effect=OSError(errno.EACCES, "synthetic cleanup denial")):
            result = self.export()
        self.assertEqual("COMMITTED", result["outcome"])
        self.assertEqual(self.digest(self.output.read_bytes()), result["sha256"])
        self.assertTrue(any("cleanup failed" in value for value in result["warnings"]))
        self.assertEqual(1, len(list(self.output.parent.glob(".candidate.zip.*.tmp"))))


if __name__ == "__main__":
    unittest.main()
