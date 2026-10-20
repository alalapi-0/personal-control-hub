"""Registry field selection uses real source facts and existing read boundaries."""
import copy
import hashlib
import json
import unittest
from pathlib import Path
from unittest import mock

import yaml

from connection_fixtures import Fixture
from hub.connection_sources import _safe_relative_read


class ProjectSemanticProjectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture(); self.addCleanup(self.fixture.close)
        self.root, self.declaration = self.fixture.add('a')
        self.fixture.projects[0]['description'] = 'Synthetic control project purpose'
        self.fixture.projects[0]['project_definition'] = {'outputs': {'state': 'known', 'value': ['Synthetic export bundle'], 'reason': None}}
        self.fixture.save_registry()

    def collect(self, state, *, mapping=None):
        from hub.connection_sources import SourceResolver
        from hub.connection_refresh import RefreshLedger, refresh
        from hub.connection_records import validate_result
        from hub.project_service import ProjectService
        if mapping is not None:
            self.declaration['mapping'] = mapping
            (self.root / 'hub.connection.yaml').write_text(yaml.safe_dump(self.declaration))
        (self.root / 'STATE.yaml').write_text(yaml.safe_dump(state))
        resolver = SourceResolver(self.fixture.hub)
        ledger = RefreshLedger(self.fixture.hub, result_validator=validate_result)
        self.assertEqual({}, refresh(ledger, resolver, 'semantic-fixture', ['a'])['resolver_errors'])
        dto = ProjectService(self.fixture.hub).get_project('a')
        return dto

    def test_read_success_with_unknown_business_keeps_five_concrete_unknowns(self):
        self.fixture.projects[0].pop('project_definition')
        self.fixture.save_registry()
        dto = self.collect({}, mapping={})
        semantic = dto['semantics']
        self.assertEqual({'purpose', 'outputs', 'current_work', 'next_action', 'actor'}, set(semantic['explanations']))
        self.assertEqual('success', semantic['collection_health']['state'])
        self.assertEqual('resolved', semantic['availability']['source_disposition'])
        self.assertEqual('unknown', semantic['business_state']['state'])
        self.assertFalse(semantic['completeness']['core_complete'])
        self.assertEqual('unknown', semantic['progress']['scope'])
        self.assertTrue(semantic['progress']['scope_unknown_reason'])
        self.assertEqual('unknown', semantic['fact_time']['fact_time_state'])
        self.assertTrue(semantic['fact_time']['fact_time_unknown_reason'])
        self.assertEqual('Synthetic control project purpose', semantic['explanations']['purpose']['value'])
        self.assertEqual('registry', semantic['explanations']['purpose']['source']['kind'])
        for key in ('outputs', 'current_work', 'next_action', 'actor'):
            self.assertEqual('unknown', semantic['explanations'][key]['state'])
            self.assertIsNone(semantic['explanations'][key]['value'])
            self.assertTrue(semantic['explanations'][key]['unknown_reason'])
        self.assertEqual('unknown', semantic['availability']['outputs']['state'])
        self.assertEqual({'origin': 'hub_authored_interpretation', 'fields': {}}, semantic['interpretations'])

    def test_every_unread_active_identity_has_five_explanations_without_root_probes(self):
        from hub.project_service import ProjectService
        self.fixture.add('b')
        with mock.patch('hub.connection_sources._project_binding', side_effect=AssertionError('GET probed root')):
            projects = ProjectService(self.fixture.hub).list_projects()['projects']
        self.assertEqual({'a', 'b'}, {project['project_id'] for project in projects})
        for project in projects:
            semantic = project['semantics']
            self.assertEqual('unobserved', semantic['collection_health']['state'])
            self.assertEqual({'purpose', 'outputs', 'current_work', 'next_action', 'actor'}, set(semantic['explanations']))
            for item in semantic['explanations'].values():
                self.assertTrue(item['source'] is not None or item['unknown_reason'])
            for key in ('current_work', 'next_action', 'actor'):
                self.assertEqual('unknown', semantic['explanations'][key]['state'])
                self.assertIsNone(semantic['explanations'][key]['value'])

    def test_active_project_keeps_local_blocker_and_exact_field_sources(self):
        state = copy.deepcopy(self.fixture.business)
        state['current_work']['status'] = 'active'
        state['blockers'][0].update(code='local_dependency', reason='Local export blocked', affected_items=['export-check'])
        dto = self.collect(state)
        semantic = dto['semantics']
        self.assertEqual('active', semantic['business_state']['value'])
        self.assertEqual(['Synthetic export bundle'], semantic['explanations']['outputs']['value'])
        self.assertEqual('project_definition.outputs', semantic['explanations']['outputs']['source']['field'])
        self.assertEqual('Local export blocked', semantic['blockers']['value'][0]['reason'])
        self.assertEqual(['export-check'], semantic['blockers']['value'][0]['affected_items'])
        for key, field in (('current_work', 'current_work.objective'), ('next_action', 'current_work.next_action'), ('actor', 'current_work.owner_role')):
            item = semantic['explanations'][key]
            self.assertEqual('known', item['state'])
            self.assertEqual(field, item['source']['field'])
            self.assertEqual('STATE.yaml', item['source']['path'])
            self.assertEqual(dto['business']['source_record']['field_provenance'][field]['sha256'], item['source']['sha256'])
            self.assertEqual(field.split('.'), item['source']['selector']['path'])
        self.assertEqual('active', dto['business']['normalized_status'])

    def test_local_four_of_four_never_completes_project_or_makes_outputs_available(self):
        state = copy.deepcopy(self.fixture.business)
        state['current_work'].update(status='active', completed=False)
        state['progress'].update(completed=4, total=4, counting_basis='Four local export checks; project remains active')
        dto = self.collect(state); semantic = dto['semantics']
        self.assertEqual((4, 4), (semantic['progress']['completed']['value'], semantic['progress']['total']['value']))
        self.assertEqual('source_declared_units', semantic['progress']['scope'])
        self.assertIn('local export checks', semantic['progress']['counting_basis']['value'])
        self.assertFalse(semantic['progress']['project_completion_inferred'])
        self.assertFalse(semantic['progress']['current_work_completed']['value'])
        self.assertEqual('active', semantic['business_state']['value'])
        self.assertEqual('unknown', semantic['availability']['outputs']['state'])


class RegistryFieldMappingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.root, self.declaration = self.fixture.add()
        self.declaration["mapping"] = {}
        self.write_declaration()
        self.mapping = {
            "current_work.objective": self.rule(["task", "objective"]),
            "current_work.status": self.rule(["task", "stage"], {"ready": "active"}),
            "progress.completed": self.rule(["counts", "done"]),
            "progress.counting_basis": self.rule(["counts", "basis"]),
        }
        self.state = {"task": {"objective": "Check source facts", "stage": "ready"},
                      "counts": {"done": 2, "basis": "Two source checklist items"}}
        self.write_state()

    @staticmethod
    def rule(path, value_map=None):
        return {"source_ref": "state", "selector": {"path": path}, "value_map": value_map}

    def tearDown(self):
        self.fixture.close()

    def write_declaration(self):
        (self.root / "hub.connection.yaml").write_text(yaml.safe_dump(self.declaration))

    def write_state(self):
        (self.root / "STATE.yaml").write_text(yaml.safe_dump(self.state))

    def overlay(self, mapping=None):
        self.fixture.projects[0]["hub_field_mapping"] = copy.deepcopy(self.mapping if mapping is None else mapping)
        self.fixture.save_registry()

    def test_empty_declaration_overlay_resolves_with_exact_source_and_authority(self):
        original = (self.root / "hub.connection.yaml").read_bytes()
        self.overlay()
        resolver = self.fixture.resolver()
        result = resolver.refresh("a")
        self.assertTrue(result["success"])
        self.assertEqual("Check source facts", result["business"]["current_work"]["objective"])
        self.assertEqual("active", result["business"]["current_work"]["status"])
        self.assertEqual(2, result["business"]["progress"]["completed"])
        self.assertIsNone(result["business"]["progress"]["total"])
        source_hash = hashlib.sha256((self.root / "STATE.yaml").read_bytes()).hexdigest()
        for field, raw in (("current_work.objective", "Check source facts"),
                           ("current_work.status", "ready"), ("progress.completed", 2),
                           ("progress.counting_basis", "Two source checklist items")):
            rule = self.mapping[field]
            self.assertEqual({"source_ref": "state", "sha256": source_hash,
                              "selector": rule["selector"], "value_map": rule["value_map"],
                              "raw_value": raw}, result["field_provenance"][field])
            self.assertNotIn(field, result["unknown_fields"])
        self.assertEqual(self.declaration["unknown_fields"]["progress.total"],
                         result["unknown_fields"]["progress.total"])
        self.assertEqual(hashlib.sha256(original).hexdigest(), result["declaration"]["sha256"])
        self.assertEqual("hub.connection.yaml", result["declaration"]["path"])
        self.assertEqual(self.declaration["validation_entry"], result["validation_entry"])
        self.assertEqual(self.declaration["max_read_age_seconds"], result["freshness"]["max_read_age_seconds"])
        self.assertEqual(hashlib.sha256(resolver.registry_bytes).hexdigest(), result["authority"]["registry_hash"])
        self.assertEqual(original, (self.root / "hub.connection.yaml").read_bytes())

    def test_absent_overlay_preserves_declaration_mapping(self):
        self.declaration["mapping"] = {"current_work.objective": self.mapping["current_work.objective"]}
        self.write_declaration()
        result = self.fixture.resolver().refresh("a")
        self.assertTrue(result["success"])
        self.assertEqual("Check source facts", result["business"]["current_work"]["objective"])
        self.assertIsNone(result["business"]["current_work"]["status"])

    def test_present_empty_overlay_replaces_instead_of_merging(self):
        self.declaration["mapping"] = self.mapping
        self.write_declaration()
        self.overlay({})
        result = self.fixture.resolver().refresh("a")
        self.assertTrue(result["success"])
        self.assertEqual({}, result["field_provenance"])
        self.assertIsNone(result["business"]["current_work"]["objective"])

    def test_explicit_null_subtree_remains_unknown(self):
        self.state["task"] = None
        self.write_state()
        self.overlay()
        result = self.fixture.resolver().refresh("a")
        self.assertTrue(result["success"])
        self.assertIsNone(result["business"]["current_work"]["status"])
        self.assertIsNone(result["business"]["current_work"]["objective"])
        self.assertIn("current_work.status", result["unknown_fields"])
        self.assertNotIn("current_work.status", result["field_provenance"])
        self.assertEqual(2, result["business"]["progress"]["completed"])

    def test_invalid_overlays_are_project_local_and_do_not_read_state(self):
        self.fixture.add("healthy")
        invalid = [None, [], {"not_a_field": self.rule(["task", "objective"])},
                   {"current_work.objective": {**self.rule(["task", "objective"]), "literal": "invented"}},
                   {"current_work.objective": {**self.rule(["task", "objective"]), "source_ref": "outside"}},
                   {"current_work.objective": self.rule([])},
                   {"current_work.objective": {**self.rule(["task"]), "selector": {"heading": "task", "label": "objective"}}},
                   {"progress.completed": self.rule(["counts", "done"], {"ready": "two"})}]
        for mapping in invalid:
            with self.subTest(mapping=mapping):
                self.fixture.projects[0]["hub_field_mapping"] = mapping
                self.fixture.save_registry()
                resolver = self.fixture.resolver()
                calls = []
                def read(root, relative, **kwargs):
                    calls.append(relative)
                    return _safe_relative_read(root, relative, **kwargs)
                with mock.patch("hub.connection_sources._safe_relative_read", read):
                    result = resolver.refresh("a")
                self.assertEqual("invalid", result["disposition"])
                self.assertEqual(["hub.connection.yaml"], calls)
                self.assertEqual({}, result["field_provenance"])
                self.assertTrue(resolver.refresh("healthy")["success"])

    def test_overlay_cannot_bypass_disabled_removed_or_protected_root(self):
        self.overlay()
        row = self.fixture.projects[0]
        original = copy.deepcopy(row)
        cases = [({"connection_read_allowed": False}, "disabled"),
                 ({"access_profile": "no_current_goal_access"}, "disabled"),
                 ({"local_presence": {"status": "removed_local"}}, "removed_local"),
                 ({"root_path": str(self.fixture.base / ".codex/project")}, "unsafe_path")]
        for change, expected in cases:
            with self.subTest(expected=expected):
                row.clear()
                row.update(original)
                row.update(change)
                self.fixture.save_registry()
                resolver = self.fixture.resolver()
                with mock.patch("hub.connection_sources._safe_relative_read", side_effect=AssertionError("source read")):
                    self.assertEqual(expected, resolver.refresh("a")["disposition"])

    def test_overlay_cannot_widen_source_route_or_role(self):
        self.overlay()
        for source_change in ({"path": "other.yaml"}, {"role": "exporter"}, {"path": "credentials/state.yaml"}):
            with self.subTest(source_change=source_change):
                declaration = copy.deepcopy(self.declaration)
                declaration["source_refs"][0].update(source_change)
                (self.root / "hub.connection.yaml").write_text(yaml.safe_dump(declaration))
                calls = []
                def read(root, relative, **kwargs):
                    calls.append(relative)
                    return _safe_relative_read(root, relative, **kwargs)
                with mock.patch("hub.connection_sources._safe_relative_read", read):
                    self.assertEqual("invalid", self.fixture.resolver().refresh("a")["disposition"])
                self.assertEqual(["hub.connection.yaml"], calls)

    def test_overlay_preserves_no_follow_and_permission_denial(self):
        self.overlay()
        outside = self.fixture.base / "outside.yaml"
        outside.write_text(yaml.safe_dump(self.state))
        (self.root / "STATE.yaml").unlink()
        (self.root / "STATE.yaml").symlink_to(outside)
        self.assertEqual("unsafe_path", self.fixture.resolver().refresh("a")["disposition"])
        with mock.patch("hub.connection_sources._safe_relative_read", side_effect=PermissionError):
            self.assertEqual("permission_denied", self.fixture.resolver().refresh("a")["disposition"])

    def test_overlay_cannot_repair_invalid_exporter_declaration(self):
        self.overlay()
        self.declaration["metric_export"] = {"entry": "../outside.py", "snapshot": ".hub/status.json"}
        self.write_declaration()
        self.assertEqual("invalid", self.fixture.resolver().refresh("a")["disposition"])

    def test_registry_mapping_change_during_read_rejects_and_changes_authority(self):
        self.overlay()
        resolver = self.fixture.resolver()
        original_hash = resolver.authority["registry_hash"]
        def read(root, relative, **kwargs):
            value = _safe_relative_read(root, relative, **kwargs)
            if relative == "STATE.yaml":
                self.overlay({field: self.mapping[field] for field in
                              ("progress.completed", "progress.counting_basis")})
            return value
        with mock.patch("hub.connection_sources._safe_relative_read", read):
            result = resolver.refresh("a")
        self.assertEqual("authority_drift", result["disposition"])
        self.assertEqual({}, result["field_provenance"])
        fresh = self.fixture.resolver().refresh("a")
        self.assertTrue(fresh["success"])
        self.assertNotEqual(original_hash, fresh["authority"]["registry_hash"])
        self.assertIsNone(fresh["business"]["current_work"]["objective"])
        self.assertEqual(2, fresh["business"]["progress"]["completed"])

    def test_original_declaration_change_during_mapping_read_rejects(self):
        self.overlay()
        def read(root, relative, **kwargs):
            value = _safe_relative_read(root, relative, **kwargs)
            if relative == "STATE.yaml":
                self.declaration["max_read_age_seconds"] = 7200
                self.write_declaration()
            return value
        with mock.patch("hub.connection_sources._safe_relative_read", read):
            result = self.fixture.resolver().refresh("a")
        self.assertEqual("invalid", result["disposition"])
        self.assertEqual({}, result["field_provenance"])

    def bind_json_source(self, *, register=True):
        self.alternate = self.root / "docs/current.json"
        self.alternate.parent.mkdir(exist_ok=True)
        self.alternate.write_text(json.dumps(self.state))
        self.fixture.projects[0]["hub_source_ref"] = {
            "id": "state", "path": "docs/current.json", "format": "json", "role": "current_state"}
        if register:
            self.fixture.projects[0]["current_state_paths"].append("docs/current.json")
        self.overlay()

    def test_explicit_registered_json_binding_uses_alternate_bytes_and_original_declaration_hash(self):
        original = (self.root / "hub.connection.yaml").read_bytes()
        self.bind_json_source()
        # Prove the original YAML route supplies no facts for the bound JSON read.
        (self.root / "STATE.yaml").write_text("broken: [yaml")
        result = self.fixture.resolver().refresh("a")
        self.assertTrue(result["success"])
        self.assertEqual("docs/current.json", result["sources"][0]["path"])
        self.assertEqual("json", result["sources"][0]["format"])
        self.assertEqual("state", result["sources"][0]["id"])
        self.assertEqual("current_state", result["sources"][0]["role"])
        self.assertEqual("Check source facts", result["business"]["current_work"]["objective"])
        self.assertEqual(hashlib.sha256(self.alternate.read_bytes()).hexdigest(),
                         result["field_provenance"]["current_work.objective"]["sha256"])
        self.assertEqual(hashlib.sha256(original).hexdigest(), result["declaration"]["sha256"])
        self.assertEqual(original, (self.root / "hub.connection.yaml").read_bytes())
        self.assertEqual(self.declaration["validation_entry"], result["validation_entry"])
        self.assertEqual(self.declaration["max_read_age_seconds"], result["freshness"]["max_read_age_seconds"])

    def test_source_binding_requires_explicit_registered_route_before_read(self):
        self.bind_json_source(register=False)
        calls = []
        def read(root, relative, **kwargs):
            calls.append(relative)
            return _safe_relative_read(root, relative, **kwargs)
        with mock.patch("hub.connection_sources._safe_relative_read", read):
            self.assertEqual("invalid", self.fixture.resolver().refresh("a")["disposition"])
        self.assertEqual(["hub.connection.yaml"], calls)

    def test_source_binding_cannot_change_identity_role_or_add_exporter_capabilities(self):
        self.bind_json_source()
        valid = copy.deepcopy(self.fixture.projects[0]["hub_source_ref"])
        invalid = [None, [], {**valid, "id": "other"}, {**valid, "role": "exporter"},
                   {**valid, "role": "current_goal"}, {**valid, "exporter": "run.py"},
                   {**valid, "path": "../outside.json"}, {**valid, "path": "credentials/current.json"},
                   {**valid, "path": "/outside.json"}, {**valid, "format": "python"}]
        for source in invalid:
            with self.subTest(source=source):
                self.fixture.projects[0]["hub_source_ref"] = source
                self.fixture.save_registry()
                calls = []
                def read(root, relative, **kwargs):
                    calls.append(relative)
                    return _safe_relative_read(root, relative, **kwargs)
                with mock.patch("hub.connection_sources._safe_relative_read", read):
                    self.assertEqual("invalid", self.fixture.resolver().refresh("a")["disposition"])
                self.assertEqual(["hub.connection.yaml"], calls)

    def test_source_binding_disabled_removed_have_zero_root_probes(self):
        self.bind_json_source()
        row = self.fixture.projects[0]
        original = copy.deepcopy(row)
        for change, expected in (({"connection_read_allowed": False}, "disabled"),
                                 ({"local_presence": {"status": "removed_local"}}, "removed_local")):
            row.clear()
            row.update(original)
            row.update(change)
            self.fixture.save_registry()
            resolver = self.fixture.resolver()
            with mock.patch.object(Path, "expanduser", side_effect=AssertionError("root expanded")), \
                 mock.patch.object(Path, "resolve", side_effect=AssertionError("root resolved")), \
                 mock.patch.object(Path, "stat", side_effect=AssertionError("root probed")), \
                 mock.patch("os.open", side_effect=AssertionError("file opened")):
                self.assertEqual(expected, resolver.refresh("a")["disposition"])

    def test_bound_source_keeps_no_follow_and_exporter_validation(self):
        self.bind_json_source()
        outside = self.fixture.base / "outside.json"
        outside.write_text(json.dumps(self.state))
        self.alternate.unlink()
        self.alternate.symlink_to(outside)
        self.assertEqual("unsafe_path", self.fixture.resolver().refresh("a")["disposition"])
        self.declaration["metric_export"] = {"entry": "../outside.py", "snapshot": ".hub/status.json"}
        self.write_declaration()
        self.assertEqual("invalid", self.fixture.resolver().refresh("a")["disposition"])

    def test_source_binding_change_during_alternate_read_rejects_registry_drift(self):
        self.bind_json_source()
        resolver = self.fixture.resolver()
        before = resolver.authority["registry_hash"]
        def read(root, relative, **kwargs):
            value = _safe_relative_read(root, relative, **kwargs)
            if relative == "docs/current.json":
                self.fixture.projects[0]["hub_source_ref"] = copy.deepcopy(self.declaration["source_refs"][0])
                self.fixture.save_registry()
            return value
        with mock.patch("hub.connection_sources._safe_relative_read", read):
            result = resolver.refresh("a")
        self.assertEqual("authority_drift", result["disposition"])
        self.assertEqual({}, result["field_provenance"])
        fresh = self.fixture.resolver().refresh("a")
        self.assertTrue(fresh["success"])
        self.assertEqual("STATE.yaml", fresh["sources"][0]["path"])
        self.assertNotEqual(before, fresh["authority"]["registry_hash"])


if __name__ == "__main__":
    unittest.main()
