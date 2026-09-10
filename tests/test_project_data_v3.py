"""Regression checks for the V3 baseline and inert removed-project records."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_project_data_v3",
    ROOT / "scripts/check_project_data_v3.py",
)
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


def load_inputs():
    registry = yaml.safe_load(
        (ROOT / "data/registry/external_projects.yaml").read_text(encoding="utf-8")
    )
    roadmap = yaml.safe_load(
        (ROOT / "data/roadmap/project_data_v3.yaml").read_text(encoding="utf-8")
    )
    sources = yaml.safe_load(
        (ROOT / "data/connections/metric_sources.yaml").read_text(encoding="utf-8")
    )
    coverage = json.loads(
        (
            ROOT
            / "docs/reports/all-projects-governance/bootstrap/coverage.json"
        ).read_text(encoding="utf-8")
    )
    return registry, roadmap, sources, coverage


class ProjectDataV3Tests(unittest.TestCase):
    def test_current_portable_baseline_is_valid(self):
        result = CHECKER.run_check(ROOT)
        self.assertTrue(result["valid"], result["hard_blockers"])
        self.assertEqual(
            result["counts"],
            {
                "registry": 26,
                "roadmap": 26,
                "source_bindings": 23,
                "active_source_gaps": 1,
                "removed_local": 2,
                "no_git": 7,
                "cloud_excluded": 17,
                "stages": 11,
                "rollout_project_units": 22,
            },
        )
        self.assertEqual(
            result["active_source_gaps"],
            ["desktop-downloads-scripts"],
        )
        self.assertEqual(result["storage_entry"]["files"], 6)
        self.assertEqual(
            result["storage_entry"]["terminal_state"],
            "COMPLETE_WITH_OWNER_REMOVAL",
        )
        self.assertEqual(result["storage_entry"]["active_writers"], 0)

    def test_metadata_validation_never_probes_registered_roots(self):
        inputs = load_inputs()
        with patch.object(
            Path,
            "exists",
            side_effect=AssertionError("registered path was probed"),
        ):
            result = CHECKER.validate_documents(*inputs)
        self.assertTrue(result["valid"], result["hard_blockers"])

    def test_candidate_identity_has_reproducible_serialization(self):
        identity = CHECKER.candidate_identity(ROOT)
        manifest = bytearray(f"{identity['format']}\n".encode("utf-8"))
        for row in identity["files"]:
            manifest.extend(row["path"].encode("utf-8"))
            manifest.extend(b"\0")
            manifest.extend(row["sha256"].encode("ascii"))
            manifest.extend(b"\n")
        self.assertEqual(
            identity["sha256"],
            hashlib.sha256(manifest).hexdigest(),
        )
        self.assertEqual(
            [row["path"] for row in identity["files"]],
            sorted(row["path"] for row in identity["files"]),
        )

    def test_pointer_without_expected_hash_is_existence_only(self):
        pointer, error = CHECKER._runtime_pointer(
            str(ROOT / "data/roadmap/project_data_v3.yaml")
        )
        self.assertIsNone(error)
        self.assertEqual(pointer["verification"], "existence_only")
        self.assertIsNone(pointer["hash_match"])

    def test_duplicate_and_unknown_source_fail_closed(self):
        registry, roadmap, sources, coverage = load_inputs()
        broken_map = deepcopy(roadmap)
        broken_map["project_map"].append(deepcopy(broken_map["project_map"][0]))
        duplicate = CHECKER.validate_documents(
            registry,
            broken_map,
            sources,
            coverage,
        )
        self.assertFalse(duplicate["valid"])
        self.assertTrue(
            any("duplicate IDs" in item for item in duplicate["hard_blockers"])
        )

        broken_sources = deepcopy(sources)
        broken_sources["projects"]["unregistered"] = {"adapter": "unknown"}
        unknown = CHECKER.validate_documents(
            registry,
            roadmap,
            broken_sources,
            coverage,
        )
        self.assertFalse(unknown["valid"])
        self.assertTrue(
            any(
                "without registry identity" in item
                for item in unknown["hard_blockers"]
            )
        )

    def test_removed_project_cannot_gain_source_or_execution_unit(self):
        registry, roadmap, sources, coverage = load_inputs()
        broken = deepcopy(roadmap)
        removed = next(
            row
            for row in broken["project_map"]
            if row["project_id"] == "manga-removed-local"
        )
        removed["source_binding_ref"] = (
            "data/connections/metric_sources.yaml#projects.manga-removed-local"
        )
        removed["stage_id"] = "V3-08"
        result = CHECKER.validate_documents(
            registry,
            broken,
            sources,
            coverage,
        )
        self.assertFalse(result["valid"])
        self.assertTrue(
            any(
                "removed_local" in item
                for item in result["hard_blockers"]
            )
        )

    def test_storage_state_must_be_terminal_and_authority_closed(self):
        adapter = yaml.safe_load(
            (ROOT / CHECKER.STORAGE_FILES["adapter"]).read_text(encoding="utf-8")
        )
        parameters = yaml.safe_load(
            (ROOT / CHECKER.STORAGE_FILES["parameters"]).read_text(
                encoding="utf-8"
            )
        )
        state = yaml.safe_load(
            (ROOT / CHECKER.STORAGE_FILES["state"]).read_text(encoding="utf-8")
        )
        broken = deepcopy(state)
        broken["execution_control"]["active_writers"] = 1
        broken["authorities"]["migration"] = "active"
        blockers = CHECKER.validate_storage_documents(
            adapter,
            parameters,
            broken,
        )
        self.assertTrue(any("active_writers" in item for item in blockers))
        self.assertTrue(any("authorities" in item for item in blockers))


if __name__ == "__main__":
    unittest.main()
