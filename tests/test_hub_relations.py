from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hub.connection_records import RecordError
from hub.connection_relations import project_relations, validate_relations, relation_hash as content_hash


class RelationTests(unittest.TestCase):
    def setUp(self):
        self.ids = {"alpha", "beta", "gamma"}
        self.ref = {"path": "docs/reports/ui_design_governance/test/inventory.json", "sha256": "1" * 64, "accepted_candidate_hash": "2" * 64}
        self.inventory = {"relations": [{"project_ids": ["alpha", "beta"], "kind": "pipeline", "source_refs": ["alpha:README.md:7"]}]}
        self.value = {"schema_version": "1.0", "kind": "connection_relation_proposals", "id": "fixture-relations", "revision": 1,
                      "created_at": "2026-09-05T00:00:00+00:00", "registry_ref": {"path": "data/registry/external_projects.yaml", "sha256": "3" * 64},
                      "inventory_ref": self.ref, "relations": [{"id": "alpha-beta", "project_ids": ["alpha", "beta"], "kind": "pipeline",
                        "status": "proposed", "shared_tasks": ["Fixture handoff"], "differences": ["Separate workflows"],
                        "not_shared": ["No visual approval"], "inventory_index": 0, "evidence_refs": ["alpha:README.md:7"]}]}
        self.value["content_hash"] = content_hash(self.value)

    def validate(self, value):
        return validate_relations(value, project_ids=self.ids, registry_hash="3" * 64, inventory=self.inventory, inventory_ref=self.ref)

    def test_proposal_projection_keeps_unknown_and_denies_authority(self):
        rows = project_relations(self.validate(self.value), self.ids)
        self.assertEqual(["proposed", "proposed", "unknown"], [row["status"] for row in rows])
        self.assertTrue(all(not row["program_link_authority"] and not row["design_selection_authority"] for row in rows))

    def test_confirmation_different_scope_kind_or_evidence_cannot_be_forged(self):
        mutations = [("status", "confirmed"), ("project_ids", ["alpha", "gamma"]), ("kind", "shared_visual_language"),
                     ("inventory_index", True), ("evidence_refs", ["unseen:README.md:1"]), ("project_ids", ["beta", "alpha"])]
        for field, replacement in mutations:
            with self.subTest(field=field):
                value = copy.deepcopy(self.value); value["relations"][0][field] = replacement; value["content_hash"] = content_hash(value)
                with self.assertRaises(RecordError): self.validate(value)

    def test_unknown_schema_fields_and_authority_drift_reject(self):
        for kind in ("version", "extra", "inventory", "registry"):
            value = copy.deepcopy(self.value)
            if kind == "version": value["schema_version"] = "2.0"
            elif kind == "extra": value["relations"][0]["approved"] = True
            elif kind == "inventory": value["inventory_ref"]["sha256"] = "4" * 64
            else: value["registry_ref"]["sha256"] = "4" * 64
            value["content_hash"] = content_hash(value)
            with self.subTest(kind=kind), self.assertRaises(RecordError): self.validate(value)

    def test_tampered_content_hash_rejects(self):
        value = copy.deepcopy(self.value); value["relations"][0]["differences"] = ["Changed content"]
        with self.assertRaisesRegex(RecordError, "hash mismatch"): self.validate(value)


if __name__ == "__main__": unittest.main()
