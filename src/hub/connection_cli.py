"""Reproducible connection commands. All persistence is explicitly inside Hub."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter
from pathlib import Path

from hub.connection_records import RecordError, record_schema, require, validate_collection
from hub.connections import Connections, connection_evidence, freeze_manifest, load_registry_at
from hub.paths import PROJECT_ROOT


def write_json(root: Path, relative: str, value: object, *, create_only: bool = False) -> None:
    """Atomic task-owned output; never accepts paths outside designated Hub data/evidence."""
    path = (root / relative).resolve()
    allowed = [root / "data/design_governance", root / "docs/reports/ui_design_governance"]
    require(path.is_relative_to(root.resolve()) and
            all(p.resolve().is_relative_to(root.resolve()) for p in allowed), "output symlink escapes Hub root")
    require(any(path.is_relative_to(p.resolve()) for p in allowed), "output outside Hub task scope")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".hub-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if create_only:
            os.link(temporary, path)  # Never overwrite an earlier manifest revision.
        else:
            os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("schema", help="Print the versioned schema used by the validator")
    freeze = sub.add_parser("freeze", help="Freeze all registry identities; never probes external projects")
    freeze.add_argument("--revision", type=int, required=True)
    freeze.add_argument("--output", required=True)
    refresh = sub.add_parser("refresh", help="Read named sources; stdout by default")
    refresh.add_argument("--manifest", required=True)
    refresh.add_argument("--adapters", default="data/design_governance/connection_adapters.json")
    refresh.add_argument("--project")
    refresh.add_argument("--output", help="Optional explicit Hub-owned projection/evidence output")
    validate = sub.add_parser("validate", help="Validate record versions, references and frozen coverage")
    validate.add_argument("--adapters", default="data/design_governance/connection_adapters.json")
    validate.add_argument("paths", nargs="+")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.action == "schema":
            print(json.dumps(record_schema(), ensure_ascii=False, indent=2))
            return 0
        if args.action == "freeze":
            record = freeze_manifest(root, args.revision)
            write_json(root, args.output, record, create_only=True)
            print(json.dumps({"valid": True, "manifest_id": record["id"], "count": len(record["entries"]),
                              "content_hash": record["content_hash"]}))
            return 0
        registry, digest = load_registry_at(root)
        ids = {p["id"] for p in registry["projects"]}
        if args.action == "validate":
            all_records = []
            for path in args.paths:
                value = json.loads((root / path).read_text())
                if isinstance(value, dict) and "records" in value:
                    require(value.get("schema_version") == "1.0" and
                            value.get("kind") == "rebuildable_connection_projection", "unsupported projection version/kind")
                    records = value["records"]
                else:
                    records = [value]
                require(isinstance(records, list) and bool(records), "records must be nonempty")
                for record in records:
                    if record.get("record_type") == "connection_manifest":
                        require(record["registry_ref"]["sha256"] == digest, "registry drift")
                    all_records.append(record)
            adapters = json.loads((root / args.adapters).read_text())
            print(json.dumps(validate_collection(all_records, registry, digest, adapters)))
            return 0
        manifest = json.loads((root / args.manifest).read_text())
        adapters = json.loads((root / args.adapters).read_text())
        connection = Connections(root, manifest, adapters)
        snapshots = [connection.refresh(args.project)] if args.project else connection.refresh_all()
        command = "python3 scripts/hub_connections.py refresh --manifest " + args.manifest
        if args.project:
            command += " --project " + args.project
        evidence = [connection_evidence(s, command, f"#snapshot-{s['project_id']}") for s in snapshots]
        validate_collection([manifest, *snapshots, *evidence], registry, digest, adapters)
        result = {"schema_version": "1.0", "kind": "rebuildable_connection_projection",
                  "authority": "source_projects_only", "records": [*snapshots, *evidence],
                  "summary": {"count": len(snapshots), "availability": dict(Counter(s["availability"] for s in snapshots)),
                              "final_acceptance": "UNVERIFIED"}}
        if args.output:
            write_json(root, args.output, result)
            print(json.dumps(result["summary"], ensure_ascii=False))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if all(s["availability"] == "fresh" for s in snapshots) else 2
    except (RecordError, OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
