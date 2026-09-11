"""Publish the storage-governance program's bounded standard Hub snapshot."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ID = "storage_governance"
EVIDENCE_ROOT = "/Volumes/AI_WORK_SSD/_governance/evidence/dev_projects_2026_09_04_r1"
EVIDENCE_PATH = "POST_MIGRATION_VERIFICATION.yaml"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args(argv)

    hub_root = args.hub_root.resolve(strict=True)
    project_root = args.project_root.absolute()
    if project_root.is_symlink() or not project_root.is_dir():
        return 2
    project_root = project_root.resolve(strict=True)
    sys.path.insert(0, str(hub_root / "src"))

    from hub.connection_sources import SourceResolver
    from hub.metric_export import export_metric_snapshot
    from hub.metric_storage import collect_storage
    from hub.metrics import utcnow

    observed_at = utcnow()
    resolver = SourceResolver(hub_root, clock=lambda: observed_at)
    registered = Path(resolver.projects[PROJECT_ID]["root_path"]).expanduser()
    if registered.resolve(strict=True) != project_root:
        return 2
    management = resolver.refresh(PROJECT_ID)
    if not management["success"]:
        return 2

    def storage(project, project_id, observed):
        return collect_storage(
            project,
            project_id,
            observed,
            {"evidence_root": EVIDENCE_ROOT, "evidence_path": EVIDENCE_PATH},
        )

    export_metric_snapshot(
        project_root,
        PROJECT_ID,
        {"storage": storage},
        exporter_id="storage-governance-export",
        exporter_version="1.0",
        management=management,
        clock=lambda: observed_at,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
