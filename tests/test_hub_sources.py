from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from hub.connection_records import RecordError, content_hash
from hub.connection_sources import (ACCEPTED_INVENTORY_SHA256, SourceResolver,
                                    extract_static_path_assignments, freeze_source_plan,
                                    validate_source_plan)
from hub.connections import freeze_manifest


class SourceResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo_root = Path(__file__).resolve().parents[1]
        inventory = (self.repo_root / "docs/reports/ui_design_governance/unit-02/ui-source-inventory.json").read_bytes()
        self.assertEqual(ACCEPTED_INVENTORY_SHA256, hashlib.sha256(inventory).hexdigest())
        inventory_path = self.root / "docs/reports/ui_design_governance/unit-02/ui-source-inventory.json"
        inventory_path.parent.mkdir(parents=True)
        inventory_path.write_bytes(inventory)
        self.registry, self.adapters, self.discovery = self._fixture()
        registry_path = self.root / "data/registry/external_projects.yaml"
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text(yaml.safe_dump(self.registry, sort_keys=False), encoding="utf-8")
        adapter_path = self.root / "data/design_governance/connection_adapters.json"
        adapter_path.parent.mkdir(parents=True)
        adapter_path.write_text(json.dumps(self.adapters, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        discovery_path = self.root / "docs/reports/ui_design_governance/unit-03/source-discovery.json"
        discovery_path.parent.mkdir(parents=True)
        discovery_raw = json.dumps(self.discovery, ensure_ascii=False, sort_keys=True).encode()
        discovery_path.write_bytes(discovery_raw)
        self.discovery_hash = hashlib.sha256(discovery_raw).hexdigest()
        self.manifest = freeze_manifest(self.root, revision=2)
        registry_hash = self.manifest["registry_ref"]["sha256"]
        self.plan = freeze_source_plan(self.manifest, self.registry, registry_hash, self.adapters,
                                       self.discovery, self.discovery_hash,
                                       created_at="2026-09-05T05:00:00+00:00")
        self.resolver = SourceResolver(self.root, self.manifest, self.adapters, self.plan)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _write(self, project_root: Path, relative: str, data: bytes) -> dict:
        path = project_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"path": relative, "sha256": self._digest(data), "bytes": len(data)}

    def _fixture(self) -> tuple[dict, dict, dict]:
        special = ["manga-localizer", "light-novel", "desktop-magnet", "pycharm-misc-project",
                   "desktop-downloads-scripts"]
        ids = special + [f"declared-{index:02d}" for index in range(19)]
        projects, adapter_projects = [], {}
        absence_results = []
        for project_id in ids:
            project_root = self.root / "projects" / project_id
            project_root.mkdir(parents=True)
            project = {"id": project_id, "name": project_id, "root_path": str(project_root),
                       "enabled": True, "scan_enabled": True, "profile_enabled": True,
                       "summary_enabled": True, "project_type": "fixture", "priority_source": "user",
                       "watch_paths": ["STATE.yaml"], "rules_paths": [], "current_state_paths": [],
                       "current_state_status": "fixture", "supporting_authority_paths": [],
                       "access_profile": "registered_project_read", "external_write_allowed": False}
            adapter = {"role": "canonical_current_state", "format": "yaml",
                       "status": {"jsonpath": "status"}, "next": {"jsonpath": "next_action"},
                       "next_kind": "explicit", "unknown": "field is absent"}
            if project_id == "manga-localizer":
                project.update(enabled=False, scan_enabled=False, profile_enabled=False,
                               access_profile="no_current_goal_access", watch_paths=[])
            elif project_id == "light-novel":
                legacy = self._write(project_root, "governance/round_state.yaml", b"status: [broken\n")
                project["current_state_paths"] = [str(project_root / legacy["path"])]
                project["watch_paths"] = ["scripts/local_scheduler_status.py"]
                project["supporting_authority_paths"] = [str(project_root / f"unused-{i}") for i in range(5)]
                project["supporting_authority_paths"].append(str(project_root / "scripts/local_scheduler_status.py"))
                anchor = self._write(project_root, "scripts/local_scheduler_status.py",
                                     b"from scheduler.status import collect_status\n")
                status = self._write(project_root, "src/scheduler/status.py",
                                     b"from scheduler.control import is_paused, lock_status\nTICK_STATE_REL = 'workspace/control/scheduler_tick_state.json'\n")
                control = self._write(project_root, "src/scheduler/control.py",
                                      b"PAUSE_REL = 'workspace/control/scheduler_paused.json'\n")
                self._write(project_root, "workspace/control/scheduler_tick_state.json",
                            json.dumps({"last_blocked_reason": None, "last_successful_tick": "2026-09-05T01:00:00Z",
                                        "last_tick_id": "tick-1", "last_tick_status": "success",
                                        "updated_at": "2026-09-05T01:00:00Z"}).encode())
                self._write(project_root, "workspace/control/scheduler_paused.json",
                            json.dumps({"paused": True, "reason": "user_requested_pause",
                                        "requested_at": "2026-06-12T13:13:03.153396+00:00",
                                        "requested_by": "must-not-leak"}).encode())
                self.light_static = [anchor, status, control]
            elif project_id == "desktop-magnet":
                row = self._write(project_root, "README.md", b"# Magnet Auto Fetcher\n- each item status is output only\n")
                project["watch_paths"] = ["README.md"]
                absence_results.append({"project_id": project_id, "read_only": True,
                                        "declared_state_paths": [], "files": [row]})
            elif project_id == "pycharm-misc-project":
                files = [self._write(project_root, name, b"from pathlib import Path\ndef main():\n    return None\nif __name__ == '__main__':\n    main()\n")
                         for name in ("fix_mojibake_names.py", "collect_by_ext.py", "transcribe_ja.py",
                                      "pack_nwjs_game.py")]
                project["watch_paths"] = ["."]
                absence_results.append({"project_id": project_id, "read_only": True,
                                        "declared_state_paths": [], "files": files})
            elif project_id == "desktop-downloads-scripts":
                row = self._write(project_root, "main.py", b"def print_hi(name):\n    print(name)\n")
                project["watch_paths"] = ["."]
                absence_results.append({"project_id": project_id, "read_only": True,
                                        "declared_state_paths": [], "files": [row]})
            else:
                source = self._write(project_root, "STATE.yaml",
                                     b"status: active\nnext_action: continue fixture\n")
                project["current_state_paths"] = [str(project_root / source["path"])]
            projects.append(project)
            adapter_projects[project_id] = adapter
        registry = {"schema_version": "1.0", "policy": {"read_only": True,
                    "write_external_forbidden": True, "forbidden_scan_dirs": ["private"]},
                    "projects": projects}
        discovery = {"schema_version": "1.0", "kind": "static_source_discovery",
                     "authority": "read_only_observation_not_business_state",
                     "source_inventory": {"path": "docs/reports/ui_design_governance/unit-02/ui-source-inventory.json",
                                          "sha256": ACCEPTED_INVENTORY_SHA256,
                                          "accepted_candidate": "f0d2f820f5b3bed541cee64b617f4e23fe6b0342d1b2db1541368df31c573512"},
                     "light_novel": {"project_id": "light-novel", "inspection": "static only",
                                     "sources": self.light_static},
                     "undeclared_sources": {"inspection": "exact named files", "results": absence_results},
                     "light_novel_findings": {"malformed_governance_yaml": "diagnostic",
                                              "known_static_control_paths": [
                                                  "workspace/control/scheduler_tick_state.json",
                                                  "workspace/control/scheduler_paused.json"],
                                              "runtime_control_files_read": False,
                                              "external_probes_executed": False,
                                              "pending": "bounded parser"},
                     "external_writes": 0, "manga_probes": 0}
        return registry, {"schema_version": "1.0", "adapter_version": "1.0",
                          "projects": adapter_projects}, discovery

    @staticmethod
    def _rehash(plan: dict) -> dict:
        plan["content_hash"] = content_hash({key: value for key, value in plan.items()
                                             if key != "content_hash"})
        return plan

    def _write_registry(self, registry: dict) -> None:
        (self.root / "data/registry/external_projects.yaml").write_text(
            yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")

    def _external_safe_read_wrapper(self, on_read=None):
        from hub import connection_sources

        original = connection_sources._safe_relative_read
        calls = []

        def wrapped(root, relative, **kwargs):
            if Path(root).resolve().is_relative_to((self.root / "projects").resolve()):
                calls.append((Path(root), relative))
                if on_read is not None:
                    on_read(len(calls), Path(root), relative)
            return original(root, relative, **kwargs)

        return calls, wrapped

    def test_plan_covers_all_24_and_contains_no_roots(self) -> None:
        self.assertEqual(24, len(self.plan["entries"]))
        self.assertEqual({p["id"] for p in self.registry["projects"]},
                         {e["project_id"] for e in self.plan["entries"]})
        self.assertNotIn(str(self.root / "projects"), json.dumps(self.plan))
        self.assertEqual(content_hash(self.adapters), self.resolver.authority["adapter_hash"])

    def test_declared_legacy_result_is_verified_v1_snapshot(self) -> None:
        result = self.resolver.refresh("declared-00")
        self.assertEqual("SOURCE_RESOLVED", result["disposition"])
        self.assertTrue(result["success"])
        self.assertEqual("v1_snapshot", result["business_snapshot"]["state"])
        self.assertEqual("fresh", result["business_snapshot"]["snapshot"]["availability"])
        self.assertEqual("UNVERIFIED", result["ui_verification"])

    def test_no_current_source_requires_exact_content_proof(self) -> None:
        result = self.resolver.refresh("pycharm-misc-project")
        self.assertEqual("EXPLICIT_NO_CURRENT_SOURCE_VERIFIED", result["disposition"])
        self.assertEqual(4, len(result["evidence"]))
        self.assertEqual("unknown", result["business_snapshot"]["state"])
        path = self.root / "projects/pycharm-misc-project/fix_mojibake_names.py"
        path.write_text("STATE = 'STATE.yaml'\n", encoding="utf-8")
        failed = self.resolver.refresh("pycharm-misc-project")
        self.assertEqual("VALIDATION_FAILED", failed["disposition"])

    def test_light_novel_operations_are_separate_and_business_unknown(self) -> None:
        result = self.resolver.refresh("light-novel")
        self.assertEqual("SOURCE_RESOLVED", result["disposition"])
        self.assertEqual("unknown", result["business_snapshot"]["state"])
        facts = {fact["kind"]: fact["value"] for fact in result["operational_facts"]}
        self.assertEqual({"scheduler_tick", "scheduler_pause"}, set(facts))
        self.assertTrue(facts["scheduler_pause"]["paused"])
        self.assertNotIn("requested_by", facts["scheduler_pause"])
        self.assertTrue(any(source["role"] == "diagnostic" for source in result["sources"]))
        self.assertTrue(result["errors"])

    def test_no_access_rejects_before_any_project_path_operation(self) -> None:
        with mock.patch.object(Path, "expanduser", side_effect=AssertionError("project probe")):
            result = self.resolver.refresh("manga-localizer")
        self.assertEqual("BLOCKED_BY_AUTHORITY", result["disposition"])
        self.assertFalse(result["success"])
        self.assertFalse(result["sources"] or result["evidence"] or result["errors"])

    def test_plan_rejects_traversal_and_unknown_schema(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["entries"][2]["absence_files"][0]["path"] = "../README.md"
        self._rehash(plan)
        with self.assertRaisesRegex(RecordError, "traversal"):
            validate_source_plan(plan)
        plan = copy.deepcopy(self.plan)
        plan["surprise"] = True
        with self.assertRaisesRegex(RecordError, "exact fields"):
            validate_source_plan(plan)

    def test_source_symlink_fifo_and_budget_fail_closed(self) -> None:
        project_root = self.root / "projects/desktop-downloads-scripts"
        source = project_root / "main.py"
        outside = self.root / "outside.py"
        outside.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(outside)
        self.assertEqual("SOURCE_UNAVAILABLE", self.resolver.refresh("desktop-downloads-scripts")["disposition"])
        source.unlink()
        os.mkfifo(source)
        self.assertEqual("VALIDATION_FAILED", self.resolver.refresh("desktop-downloads-scripts")["disposition"])
        source.unlink()
        source.write_bytes(b"x" * (1024 * 1024 + 1))
        self.assertEqual("VALIDATION_FAILED", self.resolver.refresh("desktop-downloads-scripts")["disposition"])

    def test_sensitive_path_and_forbidden_access_profile_rejected(self) -> None:
        plan = copy.deepcopy(self.plan)
        row = next(e for e in plan["entries"] if e["project_id"] == "desktop-downloads-scripts")
        row["absence_files"][0]["path"] = ".env.py"
        self._rehash(plan)
        with self.assertRaisesRegex(RecordError, "sensitive"):
            validate_source_plan(plan)
        plan = copy.deepcopy(self.plan)
        row = next(e for e in plan["entries"] if e["project_id"] == "declared-00")
        row["access_profile"] = "no_current_goal_access"
        self._rehash(plan)
        with self.assertRaisesRegex(RecordError, "access profile drift"):
            SourceResolver(self.root, self.manifest, self.adapters, plan)

    def test_inventory_discovery_and_external_evidence_drift(self) -> None:
        discovery_path = self.root / "docs/reports/ui_design_governance/unit-03/source-discovery.json"
        discovery_path.write_bytes(discovery_path.read_bytes() + b" ")
        with self.assertRaisesRegex(RecordError, "discovery drift"):
            SourceResolver(self.root, self.manifest, self.adapters, self.plan)
        discovery_path.write_bytes(json.dumps(self.discovery, ensure_ascii=False, sort_keys=True).encode())
        evidence = self.root / "projects/desktop-magnet/README.md"
        evidence.write_text("changed", encoding="utf-8")
        result = self.resolver.refresh("desktop-magnet")
        self.assertEqual("VALIDATION_FAILED", result["disposition"])

    def test_dynamic_ast_does_not_execute_or_authorize_path(self) -> None:
        sentinel = self.root / "sentinel"
        source = f"import os\nTICK_STATE_REL = os.system('touch {sentinel}') or 'workspace/control/x.json'\n"
        self.assertEqual({}, extract_static_path_assignments(source))
        self.assertFalse(sentinel.exists())

    def test_static_proof_must_precede_derived_target_and_match_named_assignment(self) -> None:
        status = self.root / "projects/light-novel/src/scheduler/status.py"
        text = status.read_text(encoding="utf-8").replace("TICK_STATE_REL", "OTHER_NAME")
        status.write_text(text, encoding="utf-8")
        target = self.root / "projects/light-novel/workspace/control/scheduler_tick_state.json"
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded target read")):
            result = self.resolver.refresh("light-novel")
        self.assertEqual("VALIDATION_FAILED", result["disposition"])
        self.assertTrue(target.exists())

    def test_result_forgery_and_unknown_result_fields_fail_closed_offline(self) -> None:
        result = self.resolver.refresh("declared-00")
        forged = copy.deepcopy(result)
        forged["success"] = False
        forged["result_hash"] = content_hash({k: v for k, v in forged.items() if k != "result_hash"})
        with self.assertRaisesRegex(RecordError, "success/disposition"):
            self.resolver.validate_result(forged)
        forged = copy.deepcopy(result)
        forged["extra"] = 1
        with self.assertRaisesRegex(RecordError, "exact fields"):
            self.resolver.validate_result(forged)
        with mock.patch("hub.connection_sources._safe_relative_read",
                        side_effect=AssertionError("validator performed I/O")):
            self.assertIs(result, self.resolver.validate_result(result))
            frozen = SourceResolver.from_frozen(self.manifest, self.adapters, self.plan)
            self.assertIs(result, frozen.validate_result(result))

    def test_mode_specific_fingerprints_and_fact_schemas_cannot_be_forged(self) -> None:
        declared = self.resolver.refresh("declared-00")
        forged = copy.deepcopy(declared)
        forged["sources"][0]["sha256"] = "a" * 64
        forged["result_hash"] = content_hash({k: v for k, v in forged.items() if k != "result_hash"})
        with self.assertRaisesRegex(RecordError, "v1 snapshot"):
            self.resolver.validate_result(forged)
        forged = copy.deepcopy(declared)
        forged["business_snapshot"]["snapshot"]["sources"][0]["ref"] = "current_state_paths[99]"
        forged["sources"][0]["ref"] = "current_state_paths[99]"
        forged["result_hash"] = content_hash({k: v for k, v in forged.items() if k != "result_hash"})
        with self.assertRaisesRegex(RecordError, "source refs differ"):
            self.resolver.validate_result(forged)

        absence = self.resolver.refresh("desktop-downloads-scripts")
        forged = copy.deepcopy(absence)
        forged["evidence"][0]["sha256"] = "b" * 64
        forged["result_hash"] = content_hash({k: v for k, v in forged.items() if k != "result_hash"})
        with self.assertRaisesRegex(RecordError, "exact evidence"):
            self.resolver.validate_result(forged)

        operational = self.resolver.refresh("light-novel")
        forged = copy.deepcopy(operational)
        forged["operational_facts"][1]["value"]["requested_by"] = "forged"
        forged["result_hash"] = content_hash({k: v for k, v in forged.items() if k != "result_hash"})
        with self.assertRaisesRegex(RecordError, "pause fact schema"):
            self.resolver.validate_result(forged)

        blocked = self.resolver.refresh("manga-localizer")
        forged = copy.deepcopy(blocked)
        forged["disposition"] = "SOURCE_RESOLVED"
        forged["success"] = True
        forged["result_hash"] = content_hash({k: v for k, v in forged.items() if k != "result_hash"})
        with self.assertRaisesRegex(RecordError, "contradicts plan mode"):
            self.resolver.validate_result(forged)

    def test_adapter_registry_and_manifest_hash_bindings_fail_closed(self) -> None:
        adapters = copy.deepcopy(self.adapters)
        adapters["projects"]["declared-00"]["unknown"] = "drift"
        with self.assertRaisesRegex(RecordError, "adapter drift"):
            SourceResolver(self.root, self.manifest, adapters, self.plan)
        plan = copy.deepcopy(self.plan)
        plan["authority"]["registry_hash"] = "1" * 64
        self._rehash(plan)
        with self.assertRaisesRegex(RecordError, "registry drift"):
            SourceResolver(self.root, self.manifest, self.adapters, plan)

    def test_rehashed_plan_cannot_expand_static_discovery_or_rewrite_frozen_permission(self) -> None:
        plan = copy.deepcopy(self.plan)
        light = next(entry for entry in plan["entries"] if entry["project_id"] == "light-novel")
        light["static_evidence"][0]["path"] = "arbitrary.py"
        self._rehash(plan)
        with self.assertRaisesRegex(RecordError, "differs from registry"):
            SourceResolver(self.root, self.manifest, self.adapters, plan)

        plan = copy.deepcopy(self.plan)
        declared = next(entry for entry in plan["entries"] if entry["project_id"] == "declared-00")
        declared.update(mode="blocked_by_authority", access_profile="no_current_goal_access",
                        declared_sources=[], static_evidence=[], derived_sources=[], absence_files=[])
        self._rehash(plan)
        with self.assertRaisesRegex(RecordError, "frozen source-plan permission mismatch"):
            SourceResolver.from_frozen(self.manifest, self.adapters, plan)

    def test_live_registry_revocation_after_constructor_prevents_external_read(self) -> None:
        registry = copy.deepcopy(self.registry)
        project = next(row for row in registry["projects"] if row["id"] == "desktop-magnet")
        project.update(enabled=False, access_profile="no_current_goal_access")
        self._write_registry(registry)
        calls, wrapped = self._external_safe_read_wrapper()
        with mock.patch("hub.connection_sources._safe_relative_read", side_effect=wrapped):
            result = self.resolver.refresh("desktop-magnet")
        self.assertEqual("VALIDATION_FAILED", result["disposition"])
        self.assertFalse(result["success"] or result["sources"] or result["evidence"])
        self.assertEqual([], calls)

    def test_live_adapter_drift_or_missing_malformed_authority_prevents_declared_read(self) -> None:
        adapter_path = self.root / "data/design_governance/connection_adapters.json"
        for raw in (b'{"schema_version":', None):
            with self.subTest(raw=raw):
                if adapter_path.exists():
                    adapter_path.unlink()
                if raw is not None:
                    adapter_path.write_bytes(raw)
                with mock.patch("hub.connections.bounded_read",
                                side_effect=AssertionError("declared external read")):
                    result = self.resolver.refresh("declared-00")
                self.assertIn(result["disposition"], {"VALIDATION_FAILED", "SOURCE_UNAVAILABLE"})
                self.assertEqual("unknown", result["business_snapshot"]["state"])
                adapter_path.write_text(json.dumps(self.adapters, ensure_ascii=False, sort_keys=True),
                                        encoding="utf-8")
        drifted = copy.deepcopy(self.adapters)
        drifted["projects"]["declared-00"]["unknown"] = "changed"
        adapter_path.write_text(json.dumps(drifted, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        with mock.patch("hub.connections.bounded_read", side_effect=AssertionError("declared external read")):
            result = self.resolver.refresh("declared-00")
        self.assertEqual("VALIDATION_FAILED", result["disposition"])

    def test_registry_drift_between_absence_files_stops_next_read_and_discards_partial_evidence(self) -> None:
        registry = copy.deepcopy(self.registry)

        def mutate_after_first(count, _root, _relative):
            if count == 1:
                registry["projects"][0]["name"] = "authority-drift"
                self._write_registry(registry)

        calls, wrapped = self._external_safe_read_wrapper(mutate_after_first)
        with mock.patch("hub.connection_sources._safe_relative_read", side_effect=wrapped):
            result = self.resolver.refresh("pycharm-misc-project")
        self.assertEqual(1, len(calls))
        self.assertEqual("VALIDATION_FAILED", result["disposition"])
        self.assertFalse(result["sources"] or result["evidence"] or result["operational_facts"])
        self.assertEqual("unknown", result["business_snapshot"]["state"])

    def test_light_novel_drift_between_root_and_anchor_resolution_stops_all_file_reads(self) -> None:
        registry = copy.deepcopy(self.registry)
        original_resolve = Path.resolve
        mutated = False

        def resolving(path, *args, **kwargs):
            nonlocal mutated
            resolved = original_resolve(path, *args, **kwargs)
            if path == self.root / "projects/light-novel" and not mutated:
                mutated = True
                registry["projects"][0]["name"] = "authority-drift"
                self._write_registry(registry)
            return resolved

        calls, wrapped = self._external_safe_read_wrapper()
        with mock.patch.object(Path, "resolve", autospec=True, side_effect=resolving), \
                mock.patch("hub.connection_sources._safe_relative_read", side_effect=wrapped), \
                mock.patch("hub.connections.bounded_read", side_effect=AssertionError("legacy source read")):
            result = self.resolver.refresh("light-novel")
        self.assertTrue(mutated)
        self.assertEqual([], calls)
        self.assertEqual("VALIDATION_FAILED", result["disposition"])
        self.assertFalse(result["sources"] or result["evidence"] or result["operational_facts"])

    def test_light_novel_drift_between_legacy_and_static_reads_stops_static_sources(self) -> None:
        registry = copy.deepcopy(self.registry)
        from hub import connection_sources
        original = connection_sources._safe_relative_read
        calls = []

        def legacy_then_revoke(root, relative, **kwargs):
            value = original(root, relative, **kwargs)
            if Path(root).resolve().is_relative_to((self.root / "projects").resolve()):
                calls.append((Path(root), relative))
                if relative == "governance/round_state.yaml":
                    project = next(row for row in registry["projects"] if row["id"] == "light-novel")
                    project.update(enabled=False, access_profile="no_current_goal_access")
                    self._write_registry(registry)
            return value

        with mock.patch("hub.connection_sources._safe_relative_read", side_effect=legacy_then_revoke):
            result = self.resolver.refresh("light-novel")
        self.assertEqual(["governance/round_state.yaml"], [relative for _, relative in calls])
        self.assertEqual("VALIDATION_FAILED", result["disposition"])
        self.assertEqual("AUTHORITY_DRIFT", result["errors"][0]["code"])
        self.assertEqual("unknown", result["business_snapshot"]["state"])
        self.assertFalse(result["sources"] or result["evidence"] or result["operational_facts"])


if __name__ == "__main__":
    unittest.main()
