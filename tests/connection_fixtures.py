"""Synthetic source authorities; never use real project data."""
import json
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hub.connection_records import FIELDS, empty_business, put_field
from hub.connection_sources import SourceResolver

TIME = "2026-09-08T10:00:00+00:00"


class Fixture:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.hub = self.base / "hub"
        (self.hub / "data/registry").mkdir(parents=True)
        self.projects = []
        self.business = empty_business()
        values = {"current_work.objective": "Repair current candidate", "current_work.phase": "verification",
                  "current_work.round": "R2", "current_work.status": "complete", "current_work.completed": True,
                  "current_work.accepted": False, "current_work.next_action": "Review candidate B", "current_work.owner_role": "Root",
                  "progress.completed": 2, "progress.total": 3, "progress.counting_basis": "Three registered regression cases",
                  "progress.milestones": [{"id": "repair", "label": "Repair", "completed": True, "accepted": False}],
                  "blockers": [{"code": "review_pending", "reason": "Candidate has changed", "affected_items": ["B"],
                                "recovery_condition": "Review B", "retry_entry": "review B", "waiting_for_user": False}],
                  "verification.status": "checks_passed", "verification.evidence_refs": ["tests/result.json"],
                  "verification.accepted_basis": "Independent acceptance pending", "delivery.status": "pending_delivery",
                  "delivery.remote": "https://github.com/example/example", "delivery.branch": "main",
                  "delivery.commit": "a" * 40, "delivery.receipt_ref": "docs/delivery.json"}
        for key, value in values.items():
            put_field(self.business, key, value)

    def add(self, project_id="a"):
        root = self.base / project_id
        root.mkdir()
        declaration = {"schema_version": "2.0", "project_id": project_id,
                       "source_refs": [{"id": "state", "path": "STATE.yaml", "format": "yaml", "role": "current_state"}],
                       "mapping": {field: {"source_ref": "state", "selector": {"path": field.split(".")}, "value_map": None} for field in FIELDS},
                       "unknown_fields": {field: "Source does not publish this fact." for field in FIELDS},
                       "validation_entry": ["python3 scripts/check_state.py"], "max_read_age_seconds": 3600}
        (root / "hub.connection.yaml").write_text(yaml.safe_dump(declaration, sort_keys=False))
        (root / "STATE.yaml").write_text(yaml.safe_dump(self.business, sort_keys=False))
        self.projects.append({"id": project_id, "name": project_id, "root_path": str(root), "enabled": True,
                              "current_state_paths": ["STATE.yaml"]})
        self.save_registry()
        return root, declaration

    def save_registry(self):
        (self.hub / "data/registry/external_projects.yaml").write_text(yaml.safe_dump({"projects": self.projects}))

    def resolver(self):
        return SourceResolver(self.hub, clock=lambda: TIME)

    def close(self):
        self.temporary.cleanup()
