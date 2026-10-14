"""Synthetic literal task plans traverse the real read, ledger and DTO pipeline."""
import copy
import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import yaml

from connection_fixtures import Fixture
from test_hub_business_tasks import plan
from hub.connection_extensions import validate_extensions
from hub.connection_records import RecordError, empty_business, update_key, validate_result
from hub.connection_refresh import RefreshLedger, refresh
from hub.connection_sources import SourceResolver
from hub.project_service import ProjectService


class TaskPlanPipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.root, self.declaration = self.fixture.add("business")
        self.other, _ = self.fixture.add("other")
        self.now = datetime.now(timezone.utc).isoformat()
        self.declaration["extensions"] = {
            "schema_version": "1.1",
            "fact_mapping": {"task_plan": {"source_ref": "state", "selector": {"path": ["task_plan"]}}},
            "unknown_fields": {"task_plan": "Source does not publish a task plan"},
        }
        self.save_declaration()
        self.write_plan(plan())
        self.ledger = RefreshLedger(self.fixture.hub, result_validator=validate_result)

    def tearDown(self):
        self.fixture.close()

    def save_declaration(self):
        (self.root / "hub.connection.yaml").write_text(yaml.safe_dump(self.declaration, sort_keys=False))

    def write_plan(self, value):
        state = copy.deepcopy(self.fixture.business)
        state["task_plan"] = value
        (self.root / "STATE.yaml").write_text(yaml.safe_dump(state, sort_keys=False))
        return state

    def resolver(self, observed_at=None):
        return SourceResolver(self.fixture.hub, clock=lambda: observed_at or self.now)

    def publish(self, request_id):
        result = refresh(self.ledger, self.resolver(), request_id, ["business", "other"])
        self.assertEqual({}, result["resolver_errors"])
        self.assertEqual("FINISHED", result["request"]["status"])
        return ProjectService(self.fixture.hub).get_project("business")

    def test_literal_read_proof_ledger_and_fresh_dto_preserve_base_contract(self):
        source = self.resolver().refresh("business")
        validate_result(source)
        self.assertTrue(source["success"])
        self.assertEqual("2.0", source["schema_version"])
        self.assertEqual(self.fixture.business, source["business"])
        fact = source["extensions"]["fact"]["task_plan"]
        self.assertEqual(plan(), fact["value"])
        self.assertEqual({"source_ref": "state", "sha256": hashlib.sha256(
            (self.root / "STATE.yaml").read_bytes()).hexdigest(), "observed_at": self.now,
            "selector": {"path": ["task_plan"]}}, fact["proof"])
        dto = self.publish("literal")
        self.assertEqual("fresh", dto["freshness"]["state"])
        view = dto["business"]["task_plan"]
        self.assertTrue(view["available"])
        self.assertEqual("current", view["view_role"])
        self.assertEqual(2, view["known_leaf_count"])
        self.assertEqual(2, view["progress"]["denominator"])
        self.assertEqual(0, view["progress"]["numerator"])
        self.assertEqual(fact["proof"], view["proof"])
        self.assertEqual(source["extensions"], dto["operational"]["latest_attempt"]["extensions"])
        other = ProjectService(self.fixture.hub).get_project("other")
        self.assertTrue(other["operational"]["latest_attempt"]["success"])
        self.assertFalse(other["business"]["task_plan"]["available"])
        self.assertEqual("TASK_PLAN_NOT_DECLARED", other["business"]["task_plan"]["reason"])
        self.assertNotIn("extensions", other["operational"]["latest_attempt"])

    def test_observation_time_does_not_change_update_key(self):
        first = self.resolver().refresh("business")
        later = (datetime.fromisoformat(self.now) + timedelta(seconds=10)).isoformat()
        second = self.resolver(later).refresh("business")
        self.assertNotEqual(first["observed_at"], second["observed_at"])
        self.assertEqual(update_key(first), update_key(second))
        self.assertNotEqual(first["extensions"]["fact"]["task_plan"]["proof"]["observed_at"],
                            second["extensions"]["fact"]["task_plan"]["proof"]["observed_at"])

    def test_unknown_denominator_is_not_inferred_from_known_leaves(self):
        value = plan()
        value["counts"]["total"] = None
        self.write_plan(value)
        view = self.publish("unknown")["business"]["task_plan"]
        self.assertEqual(2, view["known_leaf_count"])
        self.assertIsNone(view["progress"]["denominator"])
        self.assertIsNone(view["progress"]["ratio"])
        self.assertEqual("unknown_count", view["progress"]["unknown_reason"])

    def test_explicit_empty_plan_has_zero_leaf_denominator_and_no_ratio(self):
        value = plan(())
        value.update(nodes=[], labels={}, acceptance=[])
        self.write_plan(value)
        view = self.publish("empty")["business"]["task_plan"]
        self.assertTrue(view["available"])
        self.assertEqual(0, view["known_leaf_count"])
        self.assertEqual(0, view["progress"]["denominator"])
        self.assertIsNone(view["progress"]["ratio"])
        self.assertEqual("not_applicable", view["progress"]["unknown_reason"])

    def test_bad_tree_fails_only_its_project_and_preserves_success_as_history(self):
        previous = self.publish("valid")
        other_before = ProjectService(self.fixture.hub).get_project("other")
        value = plan()
        value["nodes"][-1]["parent"] = "missing"
        self.write_plan(value)
        current = self.publish("invalid")
        failed = current["operational"]["latest_attempt"]
        self.assertFalse(failed["success"])
        self.assertEqual(empty_business(), failed["business"])
        self.assertNotIn("extensions", failed)
        self.assertFalse(current["business"]["task_plan"]["available"])
        self.assertEqual("SOURCE_UNAVAILABLE", current["business"]["task_plan"]["reason"])
        self.assertEqual(previous["operational"]["last_success"], current["operational"]["last_success"])
        other_after = ProjectService(self.fixture.hub).get_project("other")
        self.assertEqual(other_before["operational"]["latest_attempt"]["update_key"],
                         other_after["operational"]["latest_attempt"]["update_key"])
        self.assertTrue(other_after["operational"]["latest_attempt"]["success"])

    def test_offline_history_validation_never_reopens_source_files(self):
        current = self.publish("history")
        record = current["operational"]["last_success"]
        (self.root / "STATE.yaml").unlink()
        with mock.patch("builtins.open", side_effect=AssertionError("offline source read")), \
             mock.patch("pathlib.Path.read_bytes", side_effect=AssertionError("offline source read")):
            self.assertEqual(record, validate_result(record))
        reread = RefreshLedger(self.fixture.hub, result_validator=validate_result, read_only=True)
        self.assertEqual(self.ledger.head, reread.head)

    def test_proof_hash_time_identity_and_selector_shape_tamper_are_rejected(self):
        record = self.resolver().refresh("business")
        mutations = {"sha256": "f" * 64, "observed_at": "2020-01-01T00:00:00+00:00",
                     "source_ref": "other", "selector": {"heading": "Plan", "label": "Tasks"}}
        for field, replacement in mutations.items():
            invalid = copy.deepcopy(record)
            invalid["extensions"]["fact"]["task_plan"]["proof"][field] = replacement
            with self.subTest(field=field), self.assertRaises(RecordError):
                validate_result(invalid)

    def test_selected_value_and_valid_but_wrong_selector_need_actual_read_context(self):
        record = self.resolver().refresh("business")
        context = {"state": {"sha256": record["sources"][0]["sha256"], "observed_at": self.now,
                             "format": "yaml", "data": yaml.safe_load((self.root / "STATE.yaml").read_text())}}
        for mutation in ("value", "selector"):
            invalid = copy.deepcopy(record["extensions"])
            if mutation == "value":
                invalid["fact"]["task_plan"]["value"]["labels"]["a"] = "Forged label"
            else:
                invalid["fact"]["task_plan"]["proof"]["selector"] = {"path": ["current_work"]}
            with self.subTest(mutation=mutation), self.assertRaises(RecordError):
                validate_extensions(invalid, source_context=context)

    def test_old_plan_verified_acceptance_is_rejected_at_actual_source_read(self):
        value = plan()
        value["acceptance"][0].update(implemented=True, verified=True, plan_version="P0",
                                      evidence_version="old", evidence_refs=["synthetic/check"])
        value["counts"].update(implemented=1, verified=1)
        self.write_plan(value)
        result = self.resolver().refresh("business")
        self.assertFalse(result["success"])
        self.assertNotIn("extensions", result)
        validate_result(result)

    def test_synthetic_credential_pattern_is_rejected_without_echoing_it(self):
        value = plan()
        marker = "sk-" + "syntheticfixture" * 3
        value["labels"]["a"] = marker
        self.write_plan(value)
        result = self.resolver().refresh("business")
        self.assertFalse(result["success"])
        self.assertNotIn("extensions", result)
        self.assertNotIn(marker, str(result))
        validate_result(result)

    def test_extension_declaration_cannot_widen_source_or_claim_managed_authority(self):
        self.declaration["extensions"]["fact_mapping"]["task_plan"]["source_ref"] = "other"
        self.save_declaration()
        self.assertFalse(self.resolver().refresh("business")["success"])
        self.declaration["extensions"]["fact_mapping"]["task_plan"]["source_ref"] = "state"
        self.declaration["extensions"]["managed"] = {"watch": True}
        self.save_declaration()
        self.assertFalse(self.resolver().refresh("business")["success"])


if __name__ == "__main__":
    unittest.main()
