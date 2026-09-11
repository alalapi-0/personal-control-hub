"""Project-boundary metric export with validated atomic snapshot replacement."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from hub.connection_records import (
    RecordError,
    content_hash,
    exact,
    identifier,
    require,
    validate_result,
)
from hub.metric_snapshot import (
    SNAPSHOT_DISPOSITIONS,
    SNAPSHOT_RELATIVE_PATH,
    make_metric_snapshot,
    validate_metric_snapshot,
)
from hub.metrics import issue, utcnow


GROUP_FIELDS = {"metrics", "issues", "disposition", "source_version"}


def export_metric_snapshot(
    project_root,
    project_id,
    collectors,
    *,
    exporter_id,
    exporter_version,
    management,
    clock=utcnow,
):
    """Run explicit project collectors and atomically replace `.hub/status.json`."""
    root = _project_root(project_root)
    snapshot = collect_metric_snapshot(
        root,
        project_id,
        collectors,
        exporter_id=exporter_id,
        exporter_version=exporter_version,
        management=management,
        observed_at=clock(),
    )
    _write_metric_snapshot(root, snapshot)
    return snapshot


def collect_metric_snapshot(
    project_root,
    project_id,
    collectors,
    *,
    exporter_id,
    exporter_version,
    management,
    observed_at,
):
    """Build a standard snapshot; collectors are ordinary project Python callables."""
    root = _project_root(project_root)
    identifier(project_id, "export project")
    require(
        type(collectors) is dict and 0 < len(collectors) <= 31,
        "metric export needs bounded explicit collectors",
    )
    require("management" not in collectors, "management is a reserved export group")
    management = validate_result(management)
    require(
        management["project_id"] == project_id
        and management["observed_at"] == observed_at,
        "metric export management binding mismatch",
    )
    rows, issues = [], []
    versions = {"management": management["update_key"]}
    dispositions = ["resolved" if management["success"] else "partial"]
    for group in sorted(collectors):
        identifier(group, "metric export group")
        collector = collectors[group]
        require(callable(collector), "metric export collector must be callable")
        try:
            result = collector(root, project_id, observed_at)
        except (OSError, RecordError, ValueError, KeyError, TypeError):
            issues.append(
                issue(
                    project_id,
                    "collector_failure",
                    "exporter:" + group,
                    recovery_condition=(
                        "Inspect this project export group; export never runs its producer."
                    ),
                )
            )
            versions[group] = content_hash(
                [exporter_id, exporter_version, group, "collector_failure"]
            )
            dispositions.append("partial")
            continue
        exact(result, GROUP_FIELDS, "metric export group")
        require(
            type(result["metrics"]) is list and type(result["issues"]) is list,
            "metric export group collections must be arrays",
        )
        require(
            type(result["disposition"]) is str
            and result["disposition"] in SNAPSHOT_DISPOSITIONS,
            "invalid metric export group disposition",
        )
        require(
            type(result["source_version"]) is str
            and 0 < len(result["source_version"]) <= 1000,
            "invalid metric export source version",
        )
        rows.extend(result["metrics"])
        issues.extend(result["issues"])
        versions[group] = result["source_version"]
        dispositions.append(result["disposition"])

    disposition = _combined_disposition(dispositions, rows, issues)
    return make_metric_snapshot(
        project_id,
        observed_at,
        exporter_id=exporter_id,
        exporter_version=exporter_version,
        management=management,
        disposition=disposition,
        metrics=rows,
        issues=issues,
        source_versions=versions,
    )


def _combined_disposition(dispositions, rows, issues):
    if (
        issues
        or any(value in {"partial", "unavailable"} for value in dispositions)
        or any(row.get("quality") != "good" for row in rows)
    ):
        return "partial"
    if "resolved" in dispositions:
        return "resolved"
    if "observed" in dispositions:
        return "observed"
    if set(dispositions) == {"disabled"}:
        return "disabled"
    if set(dispositions) == {"no_git"}:
        return "no_git"
    return "partial"


def _project_root(value):
    root = Path(value).absolute()
    require(
        root.exists() and not root.is_symlink() and root.is_dir(),
        "project export root must be an existing ordinary directory",
    )
    return root.resolve(strict=True)


def _write_metric_snapshot(root, snapshot):
    validate_metric_snapshot(snapshot)
    directory = root / ".hub"
    if directory.exists() or directory.is_symlink():
        require(
            directory.is_dir() and not directory.is_symlink(),
            "snapshot directory must be an ordinary directory",
        )
    else:
        directory.mkdir(mode=0o755)
    target = root / SNAPSHOT_RELATIVE_PATH
    if target.exists() or target.is_symlink():
        require(
            target.is_file()
            and not target.is_symlink()
            and target.stat().st_nlink == 1,
            "snapshot target must be an ordinary single-link file",
        )
    payload = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=directory,
            prefix=".status.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
