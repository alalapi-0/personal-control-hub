"""Registration changes only synthetic Hub metadata, never project contents."""
import copy
import contextlib
import hashlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

import yaml

from connection_fixtures import Fixture
from hub.connection_records import RecordError, validate_result
from hub.connection_refresh import RefreshLedger, refresh
from hub.connection_cli import main
from hub.project_service import ProjectService
from hub.registration import RegistrationError, register_project
from hub import registration


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.old_root, _ = self.fixture.add("existing")
        self.fixture.projects[0].update(connection_read_allowed=True, scan_enabled=False,
                                        external_write_allowed=False)
        self.fixture.save_registry()
        self.path = self.fixture.hub / "data/registry/external_projects.yaml"
        self.new_root = self.fixture.base / "ordinary-folder"
        self.new_root.mkdir()
        self.entry = {"id": "new", "name": "New ordinary folder", "root_path": str(self.new_root),
                      "enabled": True, "connection_read_allowed": True, "scan_enabled": False,
                      "external_write_allowed": False, "current_state_paths": ["STATE.yaml"]}
        self.addCleanup(self.fixture.close)

    def digest(self):
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def register(self, entry=None, *, apply=False, expected=None):
        return register_project(self.fixture.hub, self.entry if entry is None else entry,
                                self.digest() if expected is None else expected, apply=apply)

    def assert_error(self, code, entry=None, **kwargs):
        before = self.path.read_bytes()
        with self.assertRaises(RegistrationError) as failure:
            self.register(entry, **kwargs)
        self.assertEqual(code, failure.exception.code)
        self.assertEqual(before, self.path.read_bytes())

    def test_new_non_git_check_is_effect_free_then_apply_only_registers_metadata(self):
        original = self.path.read_bytes()
        registry = yaml.safe_load(original)
        registry.update(policy={"read_only": True, "custom": {"keep": 7}}, extra=["untouched"])
        self.path.write_text(yaml.safe_dump(registry, sort_keys=False))
        original = self.path.read_bytes()
        checked = self.register()
        self.assertFalse(checked["applied"])
        self.assertEqual(original, self.path.read_bytes())
        self.assertEqual(original.decode(), checked["registry_preimage"])
        self.assertEqual({"old_id": None, "new_id": "new"}, checked["id_mapping"])
        self.assertEqual("added", checked["change_kind"])
        self.assertEqual([], list(self.new_root.iterdir()))
        applied = self.register(apply=True)
        self.assertTrue(applied["applied"])
        self.assertEqual(checked["new_registry_hash"], self.digest())
        after = yaml.safe_load(self.path.read_bytes())
        self.assertEqual(registry["policy"], after["policy"])
        self.assertEqual(registry["extra"], after["extra"])
        self.assertEqual(registry["projects"][0], after["projects"][0])
        self.assertEqual(self.entry, after["projects"][1])
        self.assertEqual("missing_declaration", self.fixture.resolver().refresh("new")["disposition"])
        self.assertEqual([], list(self.new_root.iterdir()))
        self.assertFalse((self.fixture.hub / "data/connections").exists())
        self.assertEqual([], list(self.path.parent.glob(".registration-*.tmp")))

    def test_same_id_migration_retains_ledger_identity_and_marks_old_projection_stale(self):
        resolver = self.fixture.resolver()
        ledger = RefreshLedger(self.fixture.hub, result_validator=validate_result)
        refresh(ledger, resolver, "before-move", ["existing"])
        before_head = ledger.head
        history = ledger.history()
        moved = {**self.fixture.projects[0], "root_path": str(self.new_root)}
        result = self.register(moved, apply=True)
        self.assertEqual("migrated", result["change_kind"])
        self.assertEqual({"old_id": "existing", "new_id": "existing"}, result["id_mapping"])
        self.assertEqual(before_head, ledger.head)
        self.assertEqual(history, ledger.history())
        current = self.fixture.resolver()
        rebuilt = ledger.rebuild(current_authority=current.authority)
        self.assertTrue(rebuilt["projects"]["existing"]["authority_drift"])
        self.assertEqual("stale", rebuilt["projects"]["existing"]["freshness"])
        self.assertEqual("missing_declaration", current.refresh("existing")["disposition"])
        self.assertTrue((self.old_root / "hub.connection.yaml").exists())

    def test_duplicate_normalized_root_and_physical_alias_rejected(self):
        self.assert_error("ROOT_ALREADY_REGISTERED", {**self.entry, "root_path": str(self.old_root) + "/."})
        self.assert_error("ROOT_ALREADY_REGISTERED", {**self.entry, "root_path": "/" + str(self.old_root)})
        alias = self.fixture.base / "alias"
        alias.symlink_to(self.old_root, target_is_directory=True)
        self.assert_error("ROOT_IDENTITY_ALREADY_REGISTERED", {**self.entry, "root_path": str(alias)})

    def test_protected_rows_cannot_change_and_never_probe_roots(self):
        cases = ({"local_presence": {"status": "removed_local"}}, {"current_state_status": "removed_local"},
                 {"legacy": True}, {"access_profile": "legacy_read_only"}, {"enabled": False},
                 {"summary_enabled": False}, {"connection_read_allowed": False})
        for changes in cases:
            with self.subTest(changes=changes):
                old = {**self.fixture.projects[0], **changes}
                self.path.write_text(yaml.safe_dump({"projects": [old]}))
                with mock.patch.object(Path, "resolve", side_effect=AssertionError("protected root resolve")), \
                     mock.patch.object(Path, "stat", side_effect=AssertionError("protected root stat")):
                    self.assert_error("PROTECTED_REGISTRATION", {**old, "name": "changed"}, apply=True)
                    noop = self.register(old, apply=True)
                self.assertFalse(noop["applied"])
                self.assertEqual("unchanged", noop["change_kind"])
                self.assertIn({"code": "physical_identity_unverified", "project_id": "existing"}, noop["warnings"])

    def test_other_protected_roots_stay_unprobed_and_exclusion_flags_unchanged(self):
        protected = {**self.fixture.projects[0], "enabled": False, "connection_read_allowed": False,
                     "local_presence": {"status": "removed_local"}, "root_path": "/unavailable/removed"}
        self.path.write_text(yaml.safe_dump({"projects": [protected]}))
        original_resolve, original_stat = Path.resolve, Path.stat
        def resolve(path, *args, **kwargs):
            self.assertNotEqual(str(path), protected["root_path"], "protected resolve")
            return original_resolve(path, *args, **kwargs)
        def metadata(path, *args, **kwargs):
            self.assertNotEqual(str(path), protected["root_path"], "protected stat")
            return original_stat(path, *args, **kwargs)
        with mock.patch.object(Path, "resolve", resolve), mock.patch.object(Path, "stat", metadata):
            result = self.register(apply=True)
        self.assertEqual(protected, yaml.safe_load(self.path.read_bytes())["projects"][0])
        self.assertIn({"code": "physical_identity_unverified", "project_id": "existing"}, result["warnings"])

    def test_nested_root_needs_review_and_apply_does_not_write(self):
        nested = self.old_root / "subproject"
        nested.mkdir()
        candidate = {**self.entry, "root_path": str(nested)}
        checked = self.register(candidate)
        self.assertIn({"code": "nested_root_review", "project_id": "existing"}, checked["warnings"])
        self.assert_error("NESTED_ROOT_REVIEW_REQUIRED", candidate, apply=True)

    def test_explicit_read_permission_and_no_new_scan_or_external_write_grants(self):
        missing = copy.deepcopy(self.entry)
        del missing["connection_read_allowed"]
        with self.assertRaises(RecordError):
            self.register(missing)
        for field in ("scan_enabled", "external_write_allowed"):
            self.assert_error("PERMISSION_EXPANSION_FORBIDDEN", {**self.entry, field: True}, apply=True)
        candidate = {**self.entry, "connection_read_allowed": False}
        with mock.patch.object(Path, "resolve", side_effect=AssertionError("root resolve")), \
             mock.patch.object(Path, "stat", side_effect=AssertionError("root stat")):
            # Make the other registry row protected too, so no root is qualified.
            self.path.write_text(yaml.safe_dump({"projects": [{**self.fixture.projects[0], "enabled": False}]}))
            result = self.register(candidate, apply=True)
        self.assertIn({"code": "physical_identity_unverified", "project_id": "new"}, result["warnings"])
        self.assertEqual("disabled", self.fixture.resolver().refresh("new")["disposition"])

    def test_paths_are_single_line_exact_routes_inside_absolute_root(self):
        for change in ({"root_path": "relative"}, {"root_path": str(self.new_root) + "\nwrong"},
                       {"current_state_paths": ["../outside.yaml"]},
                       {"current_state_paths": [str(self.old_root / "STATE.yaml")]},
                       {"rules_paths": ["rules\r.md"]}, {"watch_paths": [".ssh/config"]}):
            with self.subTest(change=change), self.assertRaises(RecordError):
                self.register({**self.entry, **change})
        result = self.register({**self.entry, "current_state_paths": [str(self.new_root / "STATE.yaml")]})
        self.assertFalse(result["applied"])
        result = self.register({**self.entry, "watch_paths": [".", "state"]})
        self.assertFalse(result["applied"])

    def test_expected_hash_conflict_and_registry_symlink_or_hardlink_are_rejected(self):
        self.assert_error("REGISTRY_CONFLICT", expected="0" * 64, apply=True)
        original = self.path.read_bytes()
        target = self.path.parent / "original.yaml"
        self.path.rename(target)
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            self.register(apply=True, expected=hashlib.sha256(original).hexdigest())
        self.path.unlink()
        os.link(target, self.path)
        with self.assertRaises(RecordError):
            self.register(apply=True, expected=hashlib.sha256(original).hexdigest())
        self.assertEqual(original, target.read_bytes())

    def test_second_cas_detects_concurrent_change_and_cleans_only_owned_temp(self):
        expected = self.digest()
        concurrent = self.path.read_bytes() + b"\n# another writer\n"
        original_read = registration._read_registry
        calls = 0
        def read(directory):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.path.write_bytes(concurrent)
            return original_read(directory)
        with mock.patch.object(registration, "_read_registry", read):
            with self.assertRaises(RegistrationError) as failure:
                self.register(apply=True, expected=expected)
        self.assertEqual("REGISTRY_CONFLICT", failure.exception.code)
        self.assertEqual(concurrent, self.path.read_bytes())
        self.assertEqual([], list(self.path.parent.glob(".registration-*.tmp")))

    def test_cli_check_apply_is_visible_and_never_prints_private_registry_body(self):
        entry = self.path.parent / 'candidate.yaml'
        entry.write_text(yaml.safe_dump(self.entry))
        digest = self.digest()
        for apply in (False, True):
            output = io.StringIO()
            command = ['--root', str(self.fixture.hub), 'register', '--entry',
                       'data/registry/candidate.yaml', '--expected-registry-sha256', digest]
            with contextlib.redirect_stdout(output):
                code = main(command + (['--apply'] if apply else []))
            self.assertEqual(0, code)
            result = json.loads(output.getvalue())
            self.assertEqual(apply, result['applied'])
            self.assertNotIn('registry_preimage', result)
            self.assertNotIn(str(self.new_root), output.getvalue())
            self.assertNotIn('root_path', output.getvalue())
        service = ProjectService(self.fixture.hub)
        self.assertEqual('new', service.get_project('new')['project_id'])
        self.assertEqual('unknown', service.get_project('new')['source']['availability'])
        self.assertFalse((self.fixture.hub / 'data/connections/connection_refresh.sqlite3').exists())
        self.assertEqual([], list(self.new_root.iterdir()))

    def test_full_registry_metadata_contract_is_preserved_before_apply(self):
        registry = yaml.safe_load(self.path.read_bytes())
        registry['required_project_fields'] = ['id']
        self.path.write_text(yaml.safe_dump(registry))
        before = self.path.read_bytes()
        with mock.patch('hub.services.project_registry_service.validate_registry',
                        return_value={'valid': False}) as check:
            with self.assertRaises(RecordError):
                self.register(apply=True)
        self.assertFalse(check.call_args.kwargs['check_paths'])
        self.assertEqual(before, self.path.read_bytes())

    def test_malformed_presence_is_a_validation_error_before_any_effect(self):
        before = self.path.read_bytes()
        for presence in (None, 'not-object', []):
            with self.subTest(presence=presence), self.assertRaises(RecordError):
                self.register({**self.entry, 'local_presence': presence}, apply=True)
        self.assertEqual(before, self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
