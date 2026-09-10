"""Hub metric import boundary: consume one standard snapshot, never project sources."""
from __future__ import annotations

import json
from pathlib import Path

from hub.connection_records import RecordError, content_hash, identifier, require
from hub.metric_snapshot import (
    MAX_SNAPSHOT_BYTES,
    SNAPSHOT_RELATIVE_PATH,
    validate_metric_snapshot,
)


def read_metric_snapshot(project_root):
    """Read only the canonical project snapshot path."""
    root = Path(project_root).absolute()
    require(
        root.exists() and not root.is_symlink() and root.is_dir(),
        "snapshot project root must be an existing ordinary directory",
    )
    directory = root / ".hub"
    target = root / SNAPSHOT_RELATIVE_PATH
    require(
        directory.is_dir()
        and not directory.is_symlink()
        and target.is_file()
        and not target.is_symlink()
        and target.stat().st_nlink == 1,
        "snapshot must be an ordinary canonical project file",
    )
    require(target.stat().st_size <= MAX_SNAPSHOT_BYTES, "metric snapshot exceeds byte limit")
    try:
        with target.open("rb") as handle:
            payload = handle.read(MAX_SNAPSHOT_BYTES + 1)
        require(len(payload) <= MAX_SNAPSHOT_BYTES, "metric snapshot exceeds byte limit")
        value = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecordError("metric snapshot cannot be read as JSON") from exc
    return validate_metric_snapshot(value)


def import_metric_snapshot_file(
    store,
    request_id,
    project_root,
    *,
    expected_project,
):
    snapshot = read_metric_snapshot(project_root)
    return import_metric_snapshot(
        store,
        request_id,
        snapshot,
        expected_project=expected_project,
    )


def import_metric_snapshot(store, request_id, snapshot, *, expected_project):
    """Validate and persist snapshot facts without resolving any project source."""
    validate_metric_snapshot(snapshot)
    require(type(expected_project) is dict, "expected project binding must be an object")
    project_id = expected_project.get("id")
    identifier(project_id, "expected snapshot project")
    require(project_id == snapshot["project_id"], "snapshot project binding mismatch")
    request_identity = content_hash(
        [
            "project_metric_snapshot",
            snapshot["schema_version"],
            snapshot["snapshot_id"],
            snapshot["exporter"],
        ]
    )
    store.begin(request_id, [project_id], request_identity)
    result = {
        "project_id": project_id,
        "observed_at": snapshot["observed_at"],
        "disposition": snapshot["disposition"],
        "metrics": snapshot["metrics"],
        "metric_definitions": snapshot["metric_definitions"],
        "issues": snapshot["issues"],
        "source_versions": snapshot["source_versions"],
        "registry_binding": content_hash(expected_project),
        "collector_identity": content_hash(snapshot["exporter"]),
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_schema_version": snapshot["schema_version"],
    }
    return store.save(request_id, result)
