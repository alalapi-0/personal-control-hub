import copy
import json
import unittest
from pathlib import Path

from connection_fixtures import Fixture
from hub.connection_records import RecordError, record_schema, update_key, validate_declaration, validate_result
from hub.connections import parse_source, select_value


class ConnectionContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.root, self.declaration = self.fixture.add()

    def tearDown(self):
        self.fixture.close()

    def test_read_completion_does_not_promote_acceptance_or_delivery(self):
        result = self.fixture.resolver().refresh("a")
        self.assertTrue(result["success"])
        self.assertTrue(result["business"]["current_work"]["completed"])
        self.assertFalse(result["business"]["current_work"]["accepted"])
        self.assertEqual("pending_delivery", result["business"]["delivery"]["status"])
        self.assertEqual(set(self.declaration["mapping"]), set(result["field_provenance"]))

    def test_schema_file_and_template_use_current_executable_contract(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(record_schema(), json.loads((root / "data/connections/schema.json").read_text()))
        validate_declaration(parse_source((root / "data/connections/hub.connection.template.yaml").read_bytes(), "yaml"))

    def test_metric_export_is_a_stable_python_entry_not_a_shell_command(self):
        declaration = copy.deepcopy(self.declaration)
        declaration["metric_export"] = {
            "entry": "scripts/export_hub_metric_snapshot.py",
            "snapshot": ".hub/status.json",
        }
        validate_declaration(declaration, "a")
        for entry in (
            "/tmp/export.py",
            "../export.py",
            "scripts/../export.py",
            ".cursor/export.py",
            "scripts/token/export.py",
            "scripts/export.sh",
            "scripts/export.py --live",
            "scripts/$(id).py",
            "scripts/`id`.py",
            "scripts/export;id.py",
            "scripts/export|id.py",
        ):
            with self.subTest(entry=entry):
                invalid = copy.deepcopy(declaration)
                invalid["metric_export"]["entry"] = entry
                with self.assertRaises(RecordError):
                    validate_declaration(invalid, "a")
        invalid = copy.deepcopy(declaration)
        invalid["metric_export"]["snapshot"] = "status.json"
        with self.assertRaises(RecordError):
            validate_declaration(invalid, "a")

    def test_unknown_fields_and_identity_are_exhaustive(self):
        for mutate in (lambda d: d.update(extra=True), lambda d: d.update(schema_version="1.0"),
                       lambda d: d.update(project_id="../a"), lambda d: d["unknown_fields"].pop("blockers"),
                       lambda d: d["mapping"]["current_work.status"].update(source_ref="other")):
            with self.subTest(mutation=mutate):
                declaration = copy.deepcopy(self.declaration)
                mutate(declaration)
                with self.assertRaises(RecordError):
                    validate_declaration(declaration, "a")

    def test_paths_and_formats_fail_closed(self):
        for path in ("/STATE.yaml", "../STATE.yaml", "sub/../STATE.yaml", ".env.yaml", ".cursor/STATE.yaml",
                     ".ssh/STATE.yaml", "docs/credentials.yaml", "STATE.py", "./STATE.yaml", "a//STATE.yaml",
                     "auth.json", "tokens.json", "AUTH.yaml", "token-store.json"):
            with self.subTest(path=path):
                declaration = copy.deepcopy(self.declaration)
                declaration["source_refs"][0]["path"] = path
                with self.assertRaises(RecordError):
                    validate_declaration(declaration)

    def test_ambiguous_structures_and_markdown_do_not_pick_first_value(self):
        for data, format_name in ((b'{"status":"active","status":"complete"}', "json"),
                                  (b"status: active\nstatus: complete\n", "yaml"),
                                  (b"a: &a [1]\nb: *a", "yaml")):
            with self.subTest(format_name=format_name):
                with self.assertRaises(RecordError):
                    parse_source(data, format_name)
        selector = {"heading": "Current", "label": "Status"}
        self.assertEqual("active", select_value("# Current\n- Status: active\n# History\nStatus: complete", selector))
        for source in ("# Current\nStatus: active\nStatus: complete", "# Current\nStatus: active\n# Current\nStatus: complete"):
            with self.assertRaises(RecordError):
                select_value(source, selector)

    def test_offline_record_validator_rejects_impossible_or_forged_field_evidence(self):
        result = self.fixture.resolver().refresh("a")
        for mutate in (lambda r: r["sources"][0].update(format="markdown"),
                       lambda r: r["business"]["current_work"].update(accepted=True),
                       lambda r: r["field_provenance"]["current_work.status"].update(sha256="b" * 64),
                       lambda r: r["business"]["progress"].update(completed=True),
                       lambda r: r["freshness"].update(root_binding="not-a-hash")):
            with self.subTest(mutation=mutate):
                bad = copy.deepcopy(result)
                mutate(bad)
                bad["update_key"] = update_key(bad)
                with self.assertRaises(RecordError):
                    validate_result(bad)

    def test_unused_value_map_targets_are_also_strictly_typed(self):
        result = self.fixture.resolver().refresh("a")
        for unused in ({"unexpected": "object"}, False, "invented_state", None):
            with self.subTest(unused=unused):
                bad = copy.deepcopy(result)
                bad["field_provenance"]["current_work.status"]["value_map"] = {"complete": "complete", "unused": unused}
                bad["update_key"] = update_key(bad)
                with self.assertRaises(RecordError):
                    validate_result(bad)

    def test_nested_provenance_cannot_substitute_integers_for_booleans(self):
        result = self.fixture.resolver().refresh("a")
        for field, key, replacement in (("progress.milestones", "completed", 1), ("blockers", "waiting_for_user", 0)):
            bad = copy.deepcopy(result)
            # Break the producer's shared in-memory reference; model independent JSON inputs.
            bad["field_provenance"][field]["raw_value"] = copy.deepcopy(bad["field_provenance"][field]["raw_value"])
            bad["field_provenance"][field]["raw_value"][0][key] = replacement
            bad["update_key"] = update_key(bad)
            with self.assertRaises(RecordError):
                validate_result(bad)
