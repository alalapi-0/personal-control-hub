from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hub.design_cli import _artifact, _baseline, _candidate
from hub.design_records import with_content_hash
from hub.design_service import DesignService
from hub.design_store import DesignStore, content_hash_bytes
from hub.service_contract import ArtifactResponse, OwnerAction, ServiceError


NOW = "2026-09-05T12:00:00+08:00"


class DesignServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "hub"
        self.work = self.root / "docs/reports/ui_design_governance/unit-04"
        self.material = self.work / "material"
        self.material.mkdir(parents=True)
        self.store = DesignStore(
            self.root,
            self.work / "fixture-store.json",
            fixture=True,
            fixture_project_ids={"fixture-project"},
        )
        self.revision = self.store.initialize()["revision"]
        self.baseline_data = b"synthetic baseline\n"
        self.png_data = b"\x89PNG\r\n\x1a\nsynthetic-pixels"
        self.html_data = b"<script>must remain inert</script>\n"
        self.baseline_path = self.material / "baseline.txt"
        self.png_path = self.material / "candidate image.png"
        self.html_path = self.material / "candidate.html"
        self.baseline_path.write_bytes(self.baseline_data)
        self.png_path.write_bytes(self.png_data)
        self.html_path.write_bytes(self.html_data)
        self.baseline_artifact = self.artifact("fixture-baseline-artifact", self.baseline_path)
        self.png_artifact = self.artifact("fixture-candidate-raster", self.png_path)
        self.html_artifact = self.artifact("fixture-candidate-html", self.html_path)
        self.figma_artifact = self.artifact("fixture-candidate-figma", None)
        self.baseline = _baseline(1, self.baseline_artifact)
        self.candidate = _candidate(1, self.baseline, self.png_artifact)
        self.candidate["evidence_refs"] = [self.html_artifact["id"], self.figma_artifact["id"]]
        self.candidate = with_content_hash(self.candidate)
        for index, fact in enumerate((
            self.baseline_artifact,
            self.png_artifact,
            self.html_artifact,
            self.figma_artifact,
            self.baseline,
            self.candidate,
        )):
            self.append(fact, f"fixture-fact-{index}")
        self.service = DesignService(self.store)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def artifact(self, artifact_id: str, path: Path | None) -> dict:
        if path is None:
            record = _artifact(artifact_id, "placeholder.txt", "0" * 64)
            record["location"] = {"kind": "figma", "value": "figma://offline-file/node-1?v=1"}
            record["sha256"] = None
            return record
        return _artifact(
            artifact_id,
            str(path.relative_to(self.root)),
            content_hash_bytes(path.read_bytes()),
        )

    def append(self, fact: dict, request_id: str) -> None:
        state, _receipt = self.store.append_fact(
            fact, expected_revision=self.revision, request_id=request_id
        )
        self.revision = state["revision"]

    def decision(self, **updates) -> dict:
        command = {
            "request_id": "fixture-service-decision",
            "event_id": "fixture-service-event",
            "created_at": NOW,
            "expected_revision": self.revision,
            "action": "select",
            "candidate": {
                "id": self.candidate["id"],
                "revision": self.candidate["revision"],
                "content_hash": self.candidate["content_hash"],
            },
            "scope": copy.deepcopy(self.candidate["scope"]),
            "feedback": None,
            "supersedes": None,
        }
        command.update(updates)
        return command

    def export_command(self, **updates) -> dict:
        command = {
            "request_id": "fixture-export-request",
            "expected_revision": self.revision,
            "candidate": {
                "id": self.candidate["id"],
                "revision": self.candidate["revision"],
                "content_hash": self.candidate["content_hash"],
            },
        }
        command.update(updates)
        return command

    def test_absent_snapshot_is_unavailable_and_does_not_initialize(self) -> None:
        absent = DesignStore(
            self.root,
            self.work / "absent.json",
            fixture=True,
            fixture_project_ids={"fixture-project"},
        )
        result = DesignService(absent).snapshot()
        self.assertFalse(result["available"])
        self.assertEqual("DESIGN_STORE_UNAVAILABLE", result["reason"])
        self.assertFalse(absent.path.exists())

    def test_snapshot_reads_once_and_never_exposes_hub_relative_paths(self) -> None:
        with mock.patch.object(self.store, "read", wraps=self.store.read) as read:
            result = self.service.snapshot()
        self.assertEqual(1, read.call_count)
        self.assertEqual(self.revision, result["store_revision"])
        encoded = repr(result)
        self.assertNotIn(str(self.root), encoded)
        self.assertNotIn(str(self.png_path.relative_to(self.root)), encoded)
        raster = next(item for item in result["facts"] if item["id"] == self.png_artifact["id"])
        self.assertEqual("registered_artifact", raster["delivery"]["kind"])

    def test_corrupt_snapshot_uses_fixed_sanitized_error(self) -> None:
        self.store.path.write_text("not-json", encoding="utf-8")
        with self.assertRaises(ServiceError) as raised:
            self.service.snapshot()
        self.assertEqual("DESIGN_STORE_CORRUPT", raised.exception.code)
        self.assertNotIn(str(self.store.path), repr(raised.exception.as_dict()))

    def test_decision_derives_fixture_source_and_retry_is_idempotent(self) -> None:
        command = self.decision()
        first = self.service.decide(command, owner_action=OwnerAction(fixture=True))
        retry = self.service.decide(command, owner_action=OwnerAction(fixture=True))
        self.assertEqual(first, retry)
        event = self.store.read()["events"][0]
        self.assertEqual("synthetic_fixture", event["source"]["type"])
        self.assertEqual("fixture://local-http/" + command["request_id"], event["source"]["reference"])

    def test_decision_rejects_body_authority_wrong_capability_and_changed_retry(self) -> None:
        extra = self.decision()
        extra["trusted_owner"] = True
        with self.assertRaisesRegex(ServiceError, "INVALID_COMMAND"):
            self.service.decide(extra, owner_action=OwnerAction(fixture=True))
        with self.assertRaisesRegex(ServiceError, "OWNER_ACTION_CLASSIFICATION_MISMATCH"):
            self.service.decide(self.decision(), owner_action=OwnerAction(fixture=False))
        command = self.decision()
        self.service.decide(command, owner_action=OwnerAction(fixture=True))
        changed = copy.deepcopy(command)
        changed["feedback"] = "changed retry"
        with self.assertRaisesRegex(ServiceError, "REQUEST_CONFLICT"):
            self.service.decide(changed, owner_action=OwnerAction(fixture=True))

    def test_new_decision_requires_exact_candidate_scope_head_and_revision(self) -> None:
        cases = []
        wrong_hash = self.decision()
        wrong_hash["candidate"]["content_hash"] = "0" * 64
        cases.append(wrong_hash)
        wrong_scope = self.decision()
        wrong_scope["scope"]["members"][0]["pages"] = ["other"]
        cases.append(wrong_scope)
        wrong_revision = self.decision(expected_revision=0)
        cases.append(wrong_revision)
        for command in cases:
            with self.subTest(command=command):
                with self.assertRaises(ServiceError):
                    self.service.decide(command, owner_action=OwnerAction(fixture=True))

    def test_postcommit_uncertainty_keeps_receipt_and_retry_succeeds(self) -> None:
        command = self.decision()
        with mock.patch.dict(os.environ, {"HUB_DESIGN_FAIL_DIRECTORY_FSYNC": "1"}):
            with self.assertRaises(ServiceError) as raised:
                self.service.decide(command, owner_action=OwnerAction(fixture=True))
        error = raised.exception
        self.assertEqual("COMMITTED_DURABILITY_UNCONFIRMED", error.outcome)
        self.assertEqual(command["request_id"], error.details["receipt"]["request_id"])
        retry = self.service.decide(command, owner_action=OwnerAction(fixture=True))
        self.assertEqual("COMMITTED", retry["outcome"])

    def test_all_decision_actions_require_explicit_supersession(self) -> None:
        prior = None
        for index, action in enumerate(("request_changes", "select", "defer", "withdraw")):
            command = self.decision(
                request_id=f"fixture-action-request-{index}",
                event_id=f"fixture-action-event-{index}",
                expected_revision=self.store.read()["revision"],
                action=action,
                feedback="explicit fixture feedback" if action == "request_changes" else None,
                supersedes=prior,
            )
            result = self.service.decide(command, owner_action=OwnerAction(fixture=True))
            self.assertEqual("COMMITTED", result["outcome"])
            prior = command["event_id"]
        projection = self.store.projection()
        self.assertEqual(1, len(projection["queues"]["withdrawn"]))

    def test_new_decision_rejects_stale_bound_baseline(self) -> None:
        replacement = _baseline(2, self.baseline_artifact)
        self.append(replacement, "fixture-new-baseline-for-decision")
        command = self.decision(expected_revision=self.revision)
        with self.assertRaisesRegex(ServiceError, "BASELINE_STALE"):
            self.service.decide(command, owner_action=OwnerAction(fixture=True))

    def test_export_is_create_only_idempotent_and_survives_later_store_revision(self) -> None:
        command = self.export_command()
        first = self.service.export(command)
        path = self.service._export_path(command["request_id"])
        original = path.read_bytes()
        later_path = self.material / "later.txt"
        later_path.write_bytes(b"later unrelated fixture\n")
        self.append(self.artifact("fixture-later-artifact", later_path), "fixture-later-request")
        retry = self.service.export(command)
        self.assertEqual(first["sha256"], retry["sha256"])
        self.assertEqual(original, path.read_bytes())

    def test_export_changed_request_conflicts_and_tampered_zip_is_committed_corruption(self) -> None:
        command = self.export_command()
        self.service.export(command)
        changed = copy.deepcopy(command)
        changed["candidate"]["content_hash"] = "0" * 64
        with self.assertRaisesRegex(ServiceError, "EXPORT_REQUEST_CONFLICT"):
            self.service.export(changed)
        path = self.service._export_path(command["request_id"])
        with zipfile.ZipFile(path, "a") as bundle:
            bundle.writestr("unexpected.txt", b"tamper")
        with self.assertRaises(ServiceError) as raised:
            self.service.export(command)
        self.assertEqual("COMMITTED_UNCONFIRMED", raised.exception.outcome)

    def rewrite_export_manifest(self, command: dict, mutate) -> Path:
        path = self.service._export_path(command["request_id"])
        with zipfile.ZipFile(path, "r") as source:
            members = {name: source.read(name) for name in source.namelist()}
        manifest = json.loads(members["manifest.json"])
        replacement_manifest = mutate(manifest)
        if replacement_manifest is not None:
            manifest = replacement_manifest
        members["manifest.json"] = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        replacement = path.with_suffix(".replacement")
        with zipfile.ZipFile(replacement, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for name, payload in members.items():
                target.writestr(name, payload)
        os.replace(replacement, path)
        return path

    def test_export_retry_rejects_authority_figma_archive_and_role_tampering(self) -> None:
        mutations = (
            ("authority", lambda value: value["authority"].__setitem__("implementation_authority", True)),
            ("provenance", lambda value: value["provenance"].__setitem__("offline", False)),
            ("figma", lambda value: value["figma_references"][0]["value"].__setitem__("version", "forged")),
            ("archive", lambda value: value["artifact_files"][0].__setitem__("archive_path", "artifacts/../escape")),
            ("roles", lambda value: value["artifact_files"][0]["roles"].append("forged_role")),
            ("manifest_list", lambda _value: []),
            ("candidate_identity_list", lambda value: value.__setitem__("candidate_identity", [])),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                self.tearDown()
                self.setUp()
                command = self.export_command()
                self.service.export(command)
                self.rewrite_export_manifest(command, mutate)
                with self.assertRaises(ServiceError) as raised:
                    self.service.export(command)
                self.assertEqual("COMMITTED_UNCONFIRMED", raised.exception.outcome)

    def test_export_retry_rejects_duplicate_json_keys_and_declared_size_budget(self) -> None:
        command = self.export_command()
        self.service.export(command)
        path = self.service._export_path(command["request_id"])
        with zipfile.ZipFile(path, "r") as source:
            members = {name: source.read(name) for name in source.namelist()}
        members["manifest.json"] = b'{"kind":"design_export_bundle","kind":"forged"}'
        replacement = path.with_suffix(".replacement")
        with zipfile.ZipFile(replacement, "w") as target:
            for name, payload in members.items():
                target.writestr(name, payload)
        os.replace(replacement, path)
        with self.assertRaisesRegex(ServiceError, "EXPORT_CORRUPT"):
            self.service.export(command)
        self.tearDown()
        self.setUp()
        command = self.export_command()
        self.service.export(command)
        path = self.service._export_path(command["request_id"])
        with zipfile.ZipFile(path, "r") as source:
            members = {name: source.read(name) for name in source.namelist()}
        replacement = path.with_suffix(".replacement")
        with zipfile.ZipFile(replacement, "w", compression=zipfile.ZIP_BZIP2) as target:
            for name, payload in members.items():
                target.writestr(name, payload)
        os.replace(replacement, path)
        with self.assertRaisesRegex(ServiceError, "EXPORT_CORRUPT"):
            self.service.export(command)
        self.tearDown()
        self.setUp()
        command = self.export_command()
        self.service.export(command)
        path = self.service._export_path(command["request_id"])
        with zipfile.ZipFile(path, "a", compression=zipfile.ZIP_DEFLATED) as target:
            target.writestr("oversized.bin", b"x" * (8 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(ServiceError, "EXPORT_CORRUPT"):
            self.service.export(command)

    def test_export_download_is_read_only_and_returns_verified_zip(self) -> None:
        command = self.export_command()
        path = self.service._export_path(command["request_id"])
        with self.assertRaisesRegex(ServiceError, "EXPORT_NOT_FOUND"):
            self.service.export_download(command)
        self.assertFalse(path.exists())
        self.service.export(command)
        response = self.service.export_download(command)
        self.assertIsInstance(response, ArtifactResponse)
        self.assertEqual("application/zip", response.content_type)
        self.assertEqual(command["request_id"] + ".zip", response.filename)
        self.assertEqual(hashlib.sha256(response.data).hexdigest(), response.sha256)

    def test_candidate_baseline_and_figma_artifacts_use_safe_delivery(self) -> None:
        raster = self.service.artifact(
            self.png_artifact["id"], candidate_id=self.candidate["id"], candidate_revision=1
        )
        self.assertEqual("image/png", raster.content_type)
        self.assertEqual("inline", raster.disposition)
        self.assertEqual(self.png_artifact["id"] + ".png", raster.filename)
        baseline = self.service.artifact(
            self.baseline_artifact["id"], candidate_id=self.candidate["id"], candidate_revision=1
        )
        self.assertEqual("application/octet-stream", baseline.content_type)
        figma = self.service.artifact(
            self.figma_artifact["id"], candidate_id=self.candidate["id"], candidate_revision=1
        )
        self.assertEqual("text/plain; charset=utf-8", figma.content_type)
        self.assertTrue(figma.data.startswith(b"figma://"))

    def test_active_content_is_inert_and_unbound_ids_are_not_discoverable(self) -> None:
        response = self.service.artifact(
            self.html_artifact["id"], candidate_id=self.candidate["id"], candidate_revision=1
        )
        self.assertEqual("application/octet-stream", response.content_type)
        self.assertEqual("attachment", response.disposition)
        with self.assertRaisesRegex(ServiceError, "ARTIFACT_NOT_FOUND"):
            self.service.artifact("fixture-unknown", candidate_id=self.candidate["id"], candidate_revision=1)

    def test_historical_candidate_artifact_remains_readable_after_drift(self) -> None:
        replacement = _baseline(2, self.baseline_artifact)
        self.append(replacement, "fixture-new-baseline")
        response = self.service.artifact(
            self.png_artifact["id"], candidate_id=self.candidate["id"], candidate_revision=1
        )
        self.assertEqual(self.png_data, response.data)

    def test_symlink_fifo_hardlink_hash_and_traversal_artifacts_fail_closed(self) -> None:
        operations = ("symlink", "fifo", "hardlink", "hash", "traversal")
        for operation in operations:
            with self.subTest(operation=operation):
                self.tearDown()
                self.setUp()
                if operation == "symlink":
                    outside = self.root / "outside.png"
                    outside.write_bytes(self.png_data)
                    self.png_path.unlink()
                    self.png_path.symlink_to(outside)
                elif operation == "fifo":
                    self.png_path.unlink()
                    os.mkfifo(self.png_path)
                elif operation == "hardlink":
                    os.link(self.png_path, self.material / "second-link.png")
                elif operation == "hash":
                    self.png_path.write_bytes(b"changed")
                else:
                    raw = self.store.read()
                    target = next(item for item in raw["facts"] if item.get("id") == self.png_artifact["id"])
                    target["location"]["value"] = "docs/reports/ui_design_governance/../escape.png"
                    self.store.path.write_text(__import__("json").dumps(raw), encoding="utf-8")
                with self.assertRaises(ServiceError):
                    self.service.artifact(
                        self.png_artifact["id"], candidate_id=self.candidate["id"], candidate_revision=1
                    )


if __name__ == "__main__":
    unittest.main()
