from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hub import connection_records
from hub.connection_cli import write_json
from hub.connection_records import RecordError, adapter_hash, content_hash, validate_record
from hub.connections import Connections, bounded_read, connection_evidence, freeze_manifest, load_registry_at


class HubConnectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "hub"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def fixture(self, count: int = 24) -> tuple[dict, dict, set[str]]:
        projects = []
        adapters = {}
        for index in range(count):
            project_id = f"project-{index:02d}"
            project_root = self.root / "external" / project_id
            project_root.mkdir(parents=True)
            source = project_root / "STATE.yaml"
            source.write_text("status: active\nnext_action: Continue safely.\n", encoding="utf-8")
            projects.append({
                "id": project_id,
                "enabled": True,
                "summary_enabled": True,
                "root_path": str(project_root),
                "current_state_paths": [str(source)],
                "external_write_allowed": False,
                "access_profile": "registered_project_read",
            })
            adapters[project_id] = {
                "role": "canonical_current_state",
                "format": "yaml",
                "status": {"jsonpath": "status"},
                "next": {"jsonpath": "next_action"},
                "next_kind": "explicit",
                "unknown": "Source does not declare this field.",
            }
        registry = {
            "schema_version": "1.0",
            "policy": {
                "read_only": True,
                "write_external_forbidden": True,
                "forbidden_scan_dirs": ["private"],
            },
            "projects": projects,
        }
        registry_path = self.root / "data/registry/external_projects.yaml"
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
        adapter_record = {
            "schema_version": "1.0",
            "adapter_version": "1.0",
            "projects": adapters,
        }
        return registry, adapter_record, {p["id"] for p in projects}

    @staticmethod
    def rehash(manifest: dict) -> dict:
        manifest["content_hash"] = content_hash(
            {key: value for key, value in manifest.items() if key != "content_hash"}
        )
        return manifest

    def connection(self, count: int = 24) -> tuple[Connections, dict, dict, set[str]]:
        _registry, adapters, project_ids = self.fixture(count)
        manifest = freeze_manifest(self.root)
        return Connections(self.root, manifest, adapters), manifest, adapters, project_ids

    def test_manifest_freezes_all_24_registry_ids_without_roots(self) -> None:
        _registry, _adapters, project_ids = self.fixture()
        manifest = freeze_manifest(self.root)
        self.assertEqual(project_ids, {entry["project_id"] for entry in manifest["entries"]})
        self.assertEqual(24, len(manifest["entries"]))
        self.assertTrue(all("root_path" not in entry for entry in manifest["entries"]))

    def test_manifest_rejects_duplicate_id_even_when_rehashed(self) -> None:
        _registry, _adapters, project_ids = self.fixture()
        manifest = freeze_manifest(self.root)
        manifest["entries"].append(copy.deepcopy(manifest["entries"][0]))
        self.rehash(manifest)
        with self.assertRaisesRegex(RecordError, "duplicate manifest project ID"):
            validate_record(manifest, project_ids)

    def test_manifest_rejects_omitted_registry_id_even_when_rehashed(self) -> None:
        _registry, _adapters, project_ids = self.fixture()
        manifest = freeze_manifest(self.root)
        manifest["entries"].pop()
        self.rehash(manifest)
        with self.assertRaisesRegex(RecordError, "manifest coverage mismatch"):
            validate_record(manifest, project_ids)

    def test_manifest_rejects_tampering_without_rehash(self) -> None:
        self.fixture()
        manifest = freeze_manifest(self.root)
        manifest["entries"][0]["scope"]["reason"] = "tampered"
        with self.assertRaisesRegex(RecordError, "manifest hash mismatch"):
            validate_record(manifest)

    def test_schema_rejects_missing_field_invalid_enum_and_naive_timestamp(self) -> None:
        self.fixture(1)
        manifest = freeze_manifest(self.root)
        missing = copy.deepcopy(manifest)
        del missing["entries"][0]["scope"]["reason"]
        self.rehash(missing)
        with self.assertRaisesRegex(RecordError, "missing fields"):
            validate_record(missing, {"project-00"})
        invalid = copy.deepcopy(manifest["entries"][0]["scope"])
        invalid["disposition"] = "maybe"
        with self.assertRaisesRegex(RecordError, "invalid UI disposition"):
            validate_record(invalid)
        invalid["disposition"] = "unresolved"
        invalid["created_at"] = "2026-09-05T12:00:00"
        with self.assertRaisesRegex(RecordError, "invalid timestamp"):
            validate_record(invalid)

    def test_blocked_record_never_expands_or_resolves_its_root(self) -> None:
        project = {
            "id": "blocked",
            "enabled": True,
            "summary_enabled": True,
            "access_profile": "no_current_goal_access",
            "root_path": "/must-not-be-probed",
            "current_state_paths": ["/must-not-be-probed/STATE.yaml"],
            "external_write_allowed": False,
        }
        with mock.patch.object(Path, "expanduser", side_effect=AssertionError("filesystem probe")):
            with self.assertRaisesRegex(RecordError, "source access denied"):
                bounded_read(project, 0, set())

    def test_missing_named_source_is_explicit_unknown(self) -> None:
        connection, _manifest, _adapters, _ids = self.connection(1)
        Path(connection.projects["project-00"]["current_state_paths"][0]).unlink()
        snapshot = connection.refresh("project-00")
        self.assertEqual("missing", snapshot["availability"])
        self.assertIsNone(snapshot["last_success_at"])
        self.assertEqual("unknown", snapshot["normalized_status"])
        for field in ("raw_status", "next_action", "blockers", "normalized_status"):
            self.assertIn(field, snapshot["unknown_fields"])
            self.assertTrue(snapshot["unknown_fields"][field].strip())

    def test_unavailable_project_root_is_distinct_from_missing_source(self) -> None:
        registry, adapters, _ids = self.fixture(1)
        manifest = freeze_manifest(self.root)
        project_root = Path(registry["projects"][0]["root_path"])
        source = project_root / "STATE.yaml"
        source.unlink()
        project_root.rmdir()
        snapshot = Connections(self.root, manifest, adapters).refresh("project-00")
        self.assertEqual("unavailable", snapshot["availability"])
        self.assertIn("根目录不可用", snapshot["refresh_error"])

    def test_corrupt_yaml_is_invalid_and_keeps_source_fingerprint(self) -> None:
        connection, _manifest, _adapters, _ids = self.connection(1)
        source = Path(connection.projects["project-00"]["current_state_paths"][0])
        source.write_text("status: [unterminated\n", encoding="utf-8")
        snapshot = connection.refresh("project-00")
        self.assertEqual("invalid", snapshot["availability"])
        self.assertEqual(1, len(snapshot["sources"]))
        self.assertNotIn("unterminated", snapshot["refresh_error"])

    def test_safe_yaml_rejects_python_object_tag_without_execution(self) -> None:
        connection, _manifest, _adapters, _ids = self.connection(1)
        source = Path(connection.projects["project-00"]["current_state_paths"][0])
        source.write_text("status: !!python/object/apply:os.system ['touch sentinel']\n", encoding="utf-8")
        snapshot = connection.refresh("project-00")
        self.assertEqual("invalid", snapshot["availability"])
        self.assertFalse((source.parent / "sentinel").exists())

    def test_source_symlink_cannot_escape_authorized_root(self) -> None:
        registry, adapters, _ids = self.fixture(1)
        project = registry["projects"][0]
        source = Path(project["current_state_paths"][0])
        outside = self.root / "outside.yaml"
        outside.write_text("status: complete\n", encoding="utf-8")
        source.unlink()
        source.symlink_to(outside)
        manifest = freeze_manifest(self.root)
        snapshot = Connections(self.root, manifest, adapters).refresh("project-00")
        self.assertEqual("invalid", snapshot["availability"])
        self.assertIn("escapes authorized root", snapshot["refresh_error"])

    def test_file_cannot_serve_as_authorized_project_root(self) -> None:
        registry, _adapters, _ids = self.fixture(1)
        project = registry["projects"][0]
        file_root = self.root / "root-is-file"
        file_root.write_text("not a directory", encoding="utf-8")
        project["root_path"] = str(file_root)
        project["current_state_paths"] = [str(file_root / "STATE.yaml")]
        with self.assertRaisesRegex(ConnectionError, "root unavailable"):
            bounded_read(project, 0, set())

    def test_env_prefixed_source_is_forbidden(self) -> None:
        registry, _adapters, _ids = self.fixture(1)
        project = registry["projects"][0]
        source = Path(project["root_path"]) / ".env.yaml"
        source.write_text("TOKEN: secret\n", encoding="utf-8")
        project["current_state_paths"] = [str(source)]
        with self.assertRaisesRegex(RecordError, "secret-bearing source forbidden"):
            bounded_read(project, 0, set())

    def test_fresh_snapshot_has_fingerprint_and_matching_success_time(self) -> None:
        connection, _manifest, _adapters, _ids = self.connection(1)
        snapshot = connection.refresh("project-00")
        self.assertEqual("fresh", snapshot["availability"])
        self.assertEqual(snapshot["observed_at"], snapshot["last_success_at"])
        self.assertEqual(64, len(snapshot["sources"][0]["sha256"]))
        self.assertEqual("active", snapshot["normalized_status"])

    def test_unrecognized_status_is_not_falsely_normalized(self) -> None:
        connection, _manifest, _adapters, _ids = self.connection(1)
        source = Path(connection.projects["project-00"]["current_state_paths"][0])
        source.write_text("status: Storage governance completed\n", encoding="utf-8")
        snapshot = connection.refresh("project-00")
        self.assertEqual("Storage governance completed", snapshot["raw_status"])
        self.assertEqual("unknown", snapshot["normalized_status"])
        self.assertIsNone(snapshot["next_action"])
        self.assertIn("next_action", snapshot["unknown_fields"])

    def test_unknown_adapter_version_fails_closed(self) -> None:
        _registry, adapters, _ids = self.fixture(1)
        manifest = freeze_manifest(self.root)
        adapters["adapter_version"] = "2.0"
        with self.assertRaisesRegex(RecordError, "unsupported adapter version"):
            Connections(self.root, manifest, adapters)

    def test_registry_drift_invalidates_frozen_manifest(self) -> None:
        registry, adapters, _ids = self.fixture(1)
        manifest = freeze_manifest(self.root)
        registry["projects"][0]["summary_enabled"] = False
        path = self.root / "data/registry/external_projects.yaml"
        path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
        with self.assertRaisesRegex(RecordError, "registry changed"):
            Connections(self.root, manifest, adapters)

    def collection_records(self) -> tuple[list[dict], dict, str, dict]:
        connection, manifest, adapters, _project_ids = self.connection()
        snapshots = connection.refresh_all()
        evidence = [
            connection_evidence(snapshot, "bounded fixture read", f"#snapshot-{snapshot['project_id']}")
            for snapshot in snapshots
        ]
        registry, registry_digest = load_registry_at(self.root)
        return [manifest, *snapshots, *evidence], registry, registry_digest, adapters

    @staticmethod
    def forged_fresh_snapshot(manifest: dict, project_id: str, path: str, adapter_digest: str) -> dict:
        observed = manifest["created_at"]
        return {
            "schema_version": "1.0",
            "record_type": "project_snapshot",
            "id": f"snapshot-{project_id}",
            "created_at": observed,
            "project_id": project_id,
            "manifest_id": manifest["id"],
            "manifest_hash": manifest["content_hash"],
            "adapter_version": "1.0",
            "adapter_hash": adapter_digest,
            "raw_status": "active",
            "normalized_status": "active",
            "next_action": "Continue safely.",
            "next_action_kind": "explicit",
            "unknown_fields": {"blockers": "Fixture adapter does not extract blockers."},
            "blockers": None,
            "availability": "fresh",
            "sources": [{
                "ref": "current_state_paths[0]",
                "path": path,
                "sha256": "0" * 64,
                "bytes": 1,
            }],
            "observed_at": observed,
            "last_success_at": observed,
            "refresh_error": None,
            "relations": [],
            "designs": [],
            "source_role": "canonical_current_state",
        }

    def test_collection_accepts_complete_manifest_snapshot_evidence_graph(self) -> None:
        records, registry, registry_digest, adapters = self.collection_records()
        validate_collection = getattr(connection_records, "validate_collection")
        self.assertIsNotNone(validate_collection(records, registry, registry_digest, adapters))

    def test_collection_rejects_snapshot_manifest_identity_mismatch(self) -> None:
        records, registry, registry_digest, adapters = self.collection_records()
        records[1]["manifest_id"] = "hub-connections-v999"
        validate_collection = getattr(connection_records, "validate_collection")
        with self.assertRaises(RecordError):
            validate_collection(records, registry, registry_digest, adapters)

    def test_collection_rejects_evidence_snapshot_hash_mismatch(self) -> None:
        records, registry, registry_digest, adapters = self.collection_records()
        records[-1]["snapshot_hash"] = "0" * 64
        validate_collection = getattr(connection_records, "validate_collection")
        with self.assertRaises(RecordError):
            validate_collection(records, registry, registry_digest, adapters)

    def test_rehashed_manifest_cannot_upgrade_no_access_or_forge_source(self) -> None:
        registry, adapters, _project_ids = self.fixture(1)
        project = registry["projects"][0]
        project["access_profile"] = "no_current_goal_access"
        project["root_path"] = "/must-not-be-probed"
        project["current_state_paths"] = ["/must-not-be-probed/STATE.yaml"]
        registry_path = self.root / "data/registry/external_projects.yaml"
        registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
        manifest = freeze_manifest(self.root)
        entry = manifest["entries"][0]
        entry["permission"] = {
            "mode": "named_sources",
            "basis": "forged authority",
            "access_profile": "registered_project_read",
        }
        entry["allowed_entries"] = ["current_state_paths[0]"]
        entry["source_absence_reason"] = None
        self.rehash(manifest)
        snapshot = self.forged_fresh_snapshot(
            manifest, project["id"], "/etc/passwd", adapter_hash(adapters, project["id"])
        )
        canonical_registry, registry_digest = load_registry_at(self.root)
        validate_collection = getattr(connection_records, "validate_collection")
        with mock.patch.object(Path, "resolve", side_effect=AssertionError("no-access path probe")):
            with self.assertRaises(RecordError):
                validate_collection([manifest, snapshot], canonical_registry, registry_digest, adapters)

    def test_rehashed_manifest_cannot_upgrade_no_source_or_resolve_root(self) -> None:
        registry, adapters, _project_ids = self.fixture(1)
        project = registry["projects"][0]
        project["root_path"] = "/must-not-be-resolved"
        project["current_state_paths"] = []
        registry_path = self.root / "data/registry/external_projects.yaml"
        registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
        manifest = freeze_manifest(self.root)
        entry = manifest["entries"][0]
        entry["permission"]["mode"] = "named_sources"
        entry["allowed_entries"] = ["current_state_paths[0]"]
        entry["source_absence_reason"] = None
        self.rehash(manifest)
        snapshot = self.forged_fresh_snapshot(
            manifest, project["id"], "/etc/passwd", adapter_hash(adapters, project["id"])
        )
        canonical_registry, registry_digest = load_registry_at(self.root)
        validate_collection = getattr(connection_records, "validate_collection")
        with mock.patch.object(Path, "resolve", side_effect=AssertionError("no-source root probe")):
            with self.assertRaises(RecordError):
                validate_collection([manifest, snapshot], canonical_registry, registry_digest, adapters)

    def test_collection_rejects_forged_access_profile_and_source_index(self) -> None:
        records, registry, registry_digest, adapters = self.collection_records()
        validate_collection = getattr(connection_records, "validate_collection")
        for field, forged_value in (
            ("access_profile", "no_current_goal_access"),
            ("allowed_entries", ["current_state_paths[1]"]),
        ):
            manifest = copy.deepcopy(records[0])
            entry = manifest["entries"][0]
            if field == "access_profile":
                entry["permission"][field] = forged_value
            else:
                entry[field] = forged_value
            self.rehash(manifest)
            with self.subTest(field=field):
                with self.assertRaises(RecordError):
                    validate_collection([manifest], registry, registry_digest, adapters)

    def test_named_source_reference_cannot_point_to_etc_passwd(self) -> None:
        records, registry, registry_digest, adapters = self.collection_records()
        snapshot = records[1]
        snapshot["sources"][0]["path"] = "/etc/passwd"
        validate_collection = getattr(connection_records, "validate_collection")
        with self.assertRaises(RecordError):
            validate_collection([records[0], snapshot], registry, registry_digest, adapters)

    def test_snapshot_and_evidence_reject_unknown_adapter_version(self) -> None:
        records, _registry, _registry_digest, _adapters = self.collection_records()
        snapshot = copy.deepcopy(records[1])
        evidence = copy.deepcopy(records[25])
        for record in (snapshot, evidence):
            with self.subTest(record_type=record["record_type"]):
                record["adapter_version"] = "999"
                with self.assertRaisesRegex(RecordError, r"adapter[_ ]version"):
                    validate_record(record)

    def test_collection_rejects_evidence_snapshot_adapter_version_mismatch(self) -> None:
        records, registry, registry_digest, adapters = self.collection_records()
        evidence = records[25]
        evidence["adapter_version"] = "999"
        validate_collection = getattr(connection_records, "validate_collection")
        with self.assertRaises(RecordError):
            validate_collection(records, registry, registry_digest, adapters)

    def test_collection_rejects_forged_source_role_and_adapter_hash(self) -> None:
        records, registry, registry_digest, adapters = self.collection_records()
        validate_collection = getattr(connection_records, "validate_collection")
        for field, value in (("source_role", "forged_role"), ("adapter_hash", "0" * 64)):
            snapshot = copy.deepcopy(records[1])
            snapshot[field] = value
            with self.subTest(field=field):
                with self.assertRaises(RecordError):
                    validate_collection([records[0], snapshot], registry, registry_digest, adapters)

    def test_fresh_snapshot_rejects_blocked_status_and_failure_exit(self) -> None:
        records, registry, registry_digest, adapters = self.collection_records()
        validate_collection = getattr(connection_records, "validate_collection")
        manifest, snapshot, evidence = records[0], records[1], records[25]
        for status, exit_code in (("BLOCKED_BY_AUTHORITY", 0), ("PENDING", 2)):
            forged = copy.deepcopy(evidence)
            forged["status"] = status
            forged["exit_code"] = exit_code
            with self.subTest(status=status, exit_code=exit_code):
                with self.assertRaises(RecordError):
                    validate_collection([manifest, snapshot, forged], registry, registry_digest, adapters)

    def test_invalid_snapshot_cannot_claim_connected_and_verified(self) -> None:
        records, registry, registry_digest, adapters = self.collection_records()
        manifest = records[0]
        snapshot = copy.deepcopy(records[1])
        snapshot["availability"] = "invalid"
        snapshot["last_success_at"] = None
        snapshot["refresh_error"] = "Source validation failed."
        evidence = copy.deepcopy(records[25])
        evidence["snapshot_hash"] = content_hash(snapshot)
        evidence["status"] = "CONNECTED_AND_VERIFIED"
        evidence["exit_code"] = 0
        evidence["ui_verification"] = "PASS"
        validate_collection = getattr(connection_records, "validate_collection")
        with self.assertRaises(RecordError):
            validate_collection([manifest, snapshot, evidence], registry, registry_digest, adapters)

    @staticmethod
    def nonfresh_snapshot(snapshot: dict, availability: str) -> dict:
        value = copy.deepcopy(snapshot)
        value.update(
            availability=availability,
            raw_status=None,
            normalized_status="unknown",
            next_action=None,
            next_action_kind="unknown",
            blockers=None,
            sources=[],
            last_success_at=None,
            refresh_error="Fixture source was not fresh.",
            unknown_fields={
                "raw_status": "Source unavailable.",
                "normalized_status": "No raw status to normalize.",
                "next_action": "Source unavailable.",
                "blockers": "Source unavailable.",
            },
        )
        return value

    def test_nonfresh_snapshots_cannot_claim_business_state(self) -> None:
        records, _registry, _registry_digest, _adapters = self.collection_records()
        fresh = records[1]
        for availability in (
            "source_not_declared",
            "invalid",
            "missing",
            "unavailable",
            "unreadable",
            "blocked_by_authority",
        ):
            baseline = self.nonfresh_snapshot(fresh, availability)
            validate_record(baseline)
            mutations = (
                {"raw_status": "complete", "normalized_status": "complete",
                 "unknown_fields": {k: v for k, v in baseline["unknown_fields"].items()
                                    if k not in {"raw_status", "normalized_status"}}},
                {"next_action": "Publish now", "next_action_kind": "explicit",
                 "unknown_fields": {k: v for k, v in baseline["unknown_fields"].items()
                                    if k != "next_action"}},
                {"normalized_status": "complete",
                 "unknown_fields": {k: v for k, v in baseline["unknown_fields"].items()
                                    if k != "normalized_status"}},
            )
            for mutation in mutations:
                forged = copy.deepcopy(baseline)
                forged.update(mutation)
                with self.subTest(availability=availability, mutation=sorted(mutation)):
                    with self.assertRaises(RecordError):
                        validate_record(forged)

    def test_fresh_snapshot_requires_matching_success_time_and_no_error(self) -> None:
        records, _registry, _registry_digest, _adapters = self.collection_records()
        fresh = records[1]
        for field, value in (
            ("last_success_at", "2026-09-05T00:00:00+00:00"),
            ("refresh_error", "forged warning"),
        ):
            forged = copy.deepcopy(fresh)
            forged[field] = value
            with self.subTest(field=field):
                with self.assertRaises(RecordError):
                    validate_record(forged)

    def test_concrete_next_action_cannot_use_unknown_kind(self) -> None:
        records, _registry, _registry_digest, _adapters = self.collection_records()
        snapshot = copy.deepcopy(records[1])
        self.assertIsNotNone(snapshot["next_action"])
        snapshot["next_action_kind"] = "unknown"
        with self.assertRaisesRegex(RecordError, "next_action"):
            validate_record(snapshot)

    def test_nested_records_reject_unknown_fields(self) -> None:
        records, _registry, _registry_digest, _adapters = self.collection_records()
        manifest, snapshot = records[0], records[1]
        cases = []
        entry_alias = copy.deepcopy(manifest)
        entry_alias["entries"][0]["root_path"] = "/forged/root"
        cases.append(("entry_root_alias", self.rehash(entry_alias)))
        permission_alias = copy.deepcopy(manifest)
        permission_alias["entries"][0]["permission"]["allow_arbitrary_paths"] = True
        cases.append(("permission_alias", self.rehash(permission_alias)))
        registry_alias = copy.deepcopy(manifest)
        registry_alias["registry_ref"]["description"] = "forged"
        cases.append(("registry_ref_extra", self.rehash(registry_alias)))
        source_alias = copy.deepcopy(snapshot)
        source_alias["sources"][0]["trusted"] = True
        cases.append(("source_extra", source_alias))
        for label, record in cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(RecordError, "exact fields"):
                    validate_record(record)

    def test_scope_and_entry_evidence_must_target_same_registry_project(self) -> None:
        records, _registry, _registry_digest, _adapters = self.collection_records()
        manifest = records[0]
        for location in ("scope", "entry"):
            for forged_ref in ("report:unrelated", "registry:projects/project-99"):
                forged = copy.deepcopy(manifest)
                entry = forged["entries"][0]
                target = entry["scope"] if location == "scope" else entry
                target["evidence_refs"] = [forged_ref]
                self.rehash(forged)
                with self.subTest(location=location, forged_ref=forged_ref):
                    with self.assertRaisesRegex(RecordError, "evidence reference"):
                        validate_record(forged)

    def test_schema_v1_rejects_unintegrated_relation_and_design_refs(self) -> None:
        records, _registry, _registry_digest, _adapters = self.collection_records()
        for field in ("relations", "designs"):
            snapshot = copy.deepcopy(records[1])
            snapshot[field] = ["unvalidated-ref"]
            with self.subTest(field=field):
                with self.assertRaisesRegex(RecordError, "schema 1.0"):
                    validate_record(snapshot)

    def test_output_outside_task_scope_is_denied(self) -> None:
        with self.assertRaisesRegex(RecordError, "output outside Hub task scope"):
            write_json(self.root, "unrelated/output.json", {"value": 1})

    def test_output_through_symlinked_allowed_directory_is_denied(self) -> None:
        outside = Path(self.temporary.name) / "outside-output"
        outside.mkdir()
        data = self.root / "data"
        data.mkdir()
        (data / "design_governance").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RecordError, "output (outside Hub task scope|symlink escapes Hub root)"):
            write_json(self.root, "data/design_governance/escape.json", {"value": 1})
        self.assertFalse((outside / "escape.json").exists())

    def test_create_only_manifest_preserves_existing_file(self) -> None:
        output = self.root / "data/design_governance/manifest.json"
        output.parent.mkdir(parents=True)
        output.write_text("original\n", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            write_json(self.root, "data/design_governance/manifest.json", {"replacement": True}, create_only=True)
        self.assertEqual("original\n", output.read_text(encoding="utf-8"))
        self.assertEqual([], list(output.parent.glob(".hub-*")))


if __name__ == "__main__":
    unittest.main()
