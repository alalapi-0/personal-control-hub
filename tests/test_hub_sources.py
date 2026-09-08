import copy
import os
import unittest
from pathlib import Path
from unittest import mock

import yaml

from connection_fixtures import Fixture
from hub.connection_records import RecordError
from hub.connection_sources import SourceResolver, _safe_relative_read


class SourceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.root, self.declaration = self.fixture.add()

    def tearDown(self):
        self.fixture.close()

    def test_removed_ignores_activation_flags_without_any_root_access(self):
        self.fixture.projects[0].update(local_presence={"status": "removed_local"}, enabled="false",
                                        connection_read_allowed=True, current_state_status="active")
        self.fixture.save_registry()
        resolver = self.fixture.resolver()
        with mock.patch.object(Path, "expanduser", side_effect=AssertionError("root expanded")), \
             mock.patch.object(Path, "resolve", side_effect=AssertionError("root resolved")), \
             mock.patch.object(Path, "stat", side_effect=AssertionError("root inspected")), \
             mock.patch("os.open", side_effect=AssertionError("file opened")):
            result = resolver.refresh("a")
        self.assertEqual("removed_local", result["disposition"])
        self.assertIsNone(result["business"]["current_work"]["status"])

    def test_string_permission_does_not_enable_root(self):
        for enabled, extra in (("false", False), (False, "false")):
            self.fixture.projects[0].update(enabled=enabled, connection_read_allowed=extra)
            self.fixture.save_registry()
            with self.assertRaises(RecordError):
                self.fixture.resolver()

    def test_explicit_route_denial_overrides_enabled_before_any_project_access(self):
        self.fixture.projects[0].update(enabled=True, connection_read_allowed=False)
        self.fixture.save_registry()
        resolver = self.fixture.resolver()
        with mock.patch.object(Path, "expanduser", side_effect=AssertionError("project root expanded")), \
             mock.patch.object(Path, "resolve", side_effect=AssertionError("project root resolved")), \
             mock.patch.object(Path, "stat", side_effect=AssertionError("project root inspected")), \
             mock.patch("os.open", side_effect=AssertionError("project source opened")):
            result = resolver.refresh("a")
        self.assertEqual("disabled", result["disposition"])
        self.assertEqual([], result["sources"])

    def test_codex_root_and_alias_are_denied_before_declaration_access(self):
        protected = self.fixture.base / ".codex/a"
        protected.mkdir(parents=True)
        alias = self.fixture.base / "root-alias"
        alias.symlink_to(protected, target_is_directory=True)
        for candidate in (protected, alias):
            self.fixture.projects[0]["root_path"] = str(candidate)
            self.fixture.save_registry()
            resolver = self.fixture.resolver()
            with mock.patch("hub.connection_sources._safe_relative_read", side_effect=AssertionError("project content read")), \
                 mock.patch("os.open", side_effect=AssertionError("project content opened")):
                result = resolver.refresh("a")
            self.assertEqual("unsafe_path", result["disposition"])
            self.assertEqual([], result["sources"])
        self.fixture.projects[0]["root_path"] = str(protected)
        self.fixture.save_registry()
        resolver = self.fixture.resolver()
        with mock.patch.object(Path, "resolve", side_effect=AssertionError("direct protected root resolved")):
            self.assertEqual("unsafe_path", resolver.refresh("a")["disposition"])

    def test_credential_filenames_are_rejected_without_opening_the_source(self):
        for filename in ("auth.json", "tokens.json", "AUTH.yaml", "token-store.json", "service_account.json",
                         "SERVICE_ACCOUNT.json", "service-account.json", "service.account.json", "serviceaccount.json",
                         "gcp-service_account-key.json", "private-key.json"):
            declaration = copy.deepcopy(self.declaration)
            declaration["source_refs"][0].update(path=filename, format="json" if filename.endswith(".json") else "yaml")
            (self.root / "hub.connection.yaml").write_text(yaml.safe_dump(declaration))
            self.fixture.projects[0]["current_state_paths"] = [filename]
            self.fixture.save_registry()
            calls = []
            def read(root, relative, **kwargs):
                calls.append(relative)
                self.assertEqual("hub.connection.yaml", relative, "credential source opened")
                return _safe_relative_read(root, relative, **kwargs)
            with mock.patch("hub.connection_sources._safe_relative_read", read):
                result = self.fixture.resolver().refresh("a")
            self.assertEqual("invalid", result["disposition"])
            self.assertEqual(["hub.connection.yaml"], calls)
            self.assertEqual([], result["sources"])

    def test_registry_parses_the_same_frozen_bytes_as_its_hash(self):
        original = Path.read_bytes
        registry_path = self.fixture.hub / "data/registry/external_projects.yaml"
        frozen = registry_path.read_bytes()
        alternate = frozen.replace(str(self.root).encode(), b"/unowned/project")
        reads = iter([frozen, alternate, frozen])
        def read(path):
            return next(reads) if path == registry_path else original(path)
        with mock.patch.object(Path, "read_bytes", read):
            with self.assertRaises(RecordError):
                self.fixture.resolver()

    def test_each_failure_has_an_explicit_disposition(self):
        resolver = self.fixture.resolver()
        (self.root / "hub.connection.yaml").unlink()
        self.assertEqual("missing_declaration", resolver.refresh("a")["disposition"])
        (self.root / "hub.connection.yaml").write_text(yaml.safe_dump(self.declaration))
        (self.root / "STATE.yaml").unlink()
        self.assertEqual("missing_source", resolver.refresh("a")["disposition"])
        (self.root / "STATE.yaml").write_text("status: [invalid")
        self.assertEqual("invalid", resolver.refresh("a")["disposition"])
        with mock.patch("hub.connection_sources._safe_relative_read", side_effect=PermissionError):
            self.assertEqual("permission_denied", resolver.refresh("a")["disposition"])
        self.root.rename(self.root.with_name("offline"))
        self.assertEqual("offline", resolver.refresh("a")["disposition"])

    def test_symlink_source_and_protected_roots_are_never_opened(self):
        outside = self.fixture.base / "outside.yaml"
        outside.write_text("private: original")
        (self.root / "STATE.yaml").unlink()
        (self.root / "STATE.yaml").symlink_to(outside)
        self.assertEqual("unsafe_path", self.fixture.resolver().refresh("a")["disposition"])
        self.fixture.projects[0]["root_path"] = str(self.fixture.base / ".cursor/project")
        self.fixture.save_registry()
        resolver = self.fixture.resolver()
        with mock.patch.object(Path, "resolve", side_effect=AssertionError("protected root resolved")):
            self.assertEqual("unsafe_path", resolver.refresh("a")["disposition"])

    def test_authorized_root_alias_works_but_source_alias_drift_is_not_fresh(self):
        alias = self.fixture.base / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.fixture.projects[0]["root_path"] = str(alias)
        self.fixture.projects[0]["current_state_paths"] = [str(alias / "STATE.yaml")]
        self.fixture.save_registry()
        self.assertTrue(self.fixture.resolver().refresh("a")["success"])
        # Keep actual root stable, change only the independently registered source alias.
        self.fixture.projects[0]["root_path"] = str(self.root)
        self.fixture.save_registry()
        second = self.fixture.base / "other"
        second.mkdir()
        def read(root, relative, **kwargs):
            if relative == "STATE.yaml":
                alias.unlink()
                alias.symlink_to(second, target_is_directory=True)
            return _safe_relative_read(root, relative, **kwargs)
        with mock.patch("hub.connection_sources._safe_relative_read", read):
            self.assertEqual("authority_drift", self.fixture.resolver().refresh("a")["disposition"])

    def test_registry_change_and_unregistered_route_fail_closed(self):
        resolver = self.fixture.resolver()
        self.fixture.projects[0]["name"] = "new identity label"
        self.fixture.save_registry()
        self.assertEqual("authority_drift", resolver.refresh("a")["disposition"])
        self.fixture.projects[0]["current_state_paths"] = ["OTHER.yaml"]
        self.fixture.save_registry()
        self.assertEqual("invalid", self.fixture.resolver().refresh("a")["disposition"])

    def test_bad_mapping_never_leaks_partial_business_or_secret_values(self):
        state = copy.deepcopy(self.fixture.business)
        state["current_work"]["next_action"] = "api_key=" + "synthetic-value-for-rejection"
        (self.root / "STATE.yaml").write_text(yaml.safe_dump(state))
        result = self.fixture.resolver().refresh("a")
        self.assertFalse(result["success"])
        self.assertEqual({}, result["field_provenance"])
        self.assertIsNone(result["business"]["current_work"]["completed"])
        self.assertNotIn("synthetic-value-for-rejection", str(result))

    def test_missing_fact_remains_unknown_without_fabricating_zero_or_false(self):
        state = copy.deepcopy(self.fixture.business)
        state["current_work"]["accepted"] = None
        state["progress"].update(completed=None, total=None, counting_basis=None)
        (self.root / "STATE.yaml").write_text(yaml.safe_dump(state))
        result = self.fixture.resolver().refresh("a")
        self.assertTrue(result["success"])
        self.assertIsNone(result["business"]["current_work"]["accepted"])
        self.assertIn("current_work.accepted", result["unknown_fields"])
        self.assertIsNone(result["business"]["progress"]["total"])
