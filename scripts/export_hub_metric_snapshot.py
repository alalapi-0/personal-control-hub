"""Publish the Hub's own saved-service activity as one standard snapshot."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ID = "personal-control-hub"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args(argv)

    hub_root = args.hub_root.absolute()
    project_root = args.project_root.absolute()
    if (hub_root.is_symlink() or project_root.is_symlink()
            or not hub_root.is_dir() or not project_root.is_dir()):
        return 2
    hub_root = hub_root.resolve(strict=True)
    project_root = project_root.resolve(strict=True)
    if hub_root != project_root:
        return 2
    sys.path.insert(0, str(hub_root / "src"))

    from hub.connection_sources import SourceResolver
    from hub.metric_export import export_metric_snapshot
    from hub.metric_hub_activity import collect_hub_activity
    from hub.metrics import utcnow

    observed_at = utcnow()
    resolver = SourceResolver(hub_root, clock=lambda: observed_at)
    registered = Path(resolver.projects[PROJECT_ID]["root_path"]).expanduser()
    if registered.resolve(strict=True) != project_root:
        return 2
    management = resolver.refresh(PROJECT_ID)
    if not management["success"]:
        return 2

    export_metric_snapshot(
        project_root,
        PROJECT_ID,
        {"hub-activity": collect_hub_activity},
        exporter_id="personal-control-hub-export",
        exporter_version="1.0",
        management=management,
        clock=lambda: observed_at,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
