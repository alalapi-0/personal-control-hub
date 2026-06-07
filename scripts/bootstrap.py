#!/usr/bin/env python3
"""Create missing lightweight skeleton paths without overwriting files."""

from __future__ import annotations

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

DIRECTORIES = [
    "docs/archive",
    "docs/reports",
    "data/registry",
    "data/mcp",
    "data/programs",
    "data/project_profiles",
    "data/project_snapshots",
    "data/project_scans",
    "data/tasks",
    "data/scheduler",
    "data/integrations",
    "data/state",
    "data/logs",
    "prompts/templates",
    "src/hub/services",
    "tests",
    ".cursor",
]

PLACEHOLDER_FILES = {
    "data/project_profiles/.gitkeep": "",
    "data/project_snapshots/.gitkeep": "",
    "data/project_scans/.gitkeep": "",
    "prompts/templates/.gitkeep": "",
    "src/hub/__init__.py": "",
    "src/hub/services/__init__.py": "",
    "tests/.gitkeep": "",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap personal-control-hub skeleton.")
    parser.add_argument("--dry-run", action="store_true", help="Show planned changes without writing.")
    args = parser.parse_args()

    print("personal-control-hub bootstrap")
    print(f"root: {ROOT}")
    print(f"dry_run: {args.dry_run}")

    planned_dirs = [path for path in DIRECTORIES if not (ROOT / path).exists()]
    planned_files = [path for path in PLACEHOLDER_FILES if not (ROOT / path).exists()]

    print("\nDirectories to create:")
    if planned_dirs:
        for path in planned_dirs:
            print(f"  - {path}")
    else:
        print("  - none")

    print("\nPlaceholder files to create:")
    if planned_files:
        for path in planned_files:
            print(f"  - {path}")
    else:
        print("  - none")

    if args.dry_run:
        print("\nNo changes written.")
        return 0

    for relative in planned_dirs:
        (ROOT / relative).mkdir(parents=True, exist_ok=True)

    for relative in planned_files:
        target = ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(PLACEHOLDER_FILES[relative], encoding="utf-8")

    print("\nBootstrap completed without overwriting existing files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
