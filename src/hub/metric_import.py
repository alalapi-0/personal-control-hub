"""Hub metric import boundary: consume one standard snapshot, never project sources."""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
from pathlib import Path
import signal
import sqlite3
import threading

from hub.connection_records import RecordError, content_hash, identifier, require
from hub.connection_refresh import RefreshLedgerError
from hub.metric_snapshot import (
    DEFAULT_IMPORT_TIMEOUT_SECONDS,
    MAX_IMPORT_TIMEOUT_SECONDS,
    MAX_SNAPSHOT_BYTES,
    MIN_IMPORT_TIMEOUT_SECONDS,
    SNAPSHOT_RELATIVE_PATH,
    SNAPSHOT_SCHEMA_VERSION,
    validate_metric_snapshot,
)


class SnapshotImportTimeout(RuntimeError):
    """One canonical snapshot read exceeded its bounded deadline."""


class MetricSnapshotPersistenceError(RuntimeError):
    """A validated synchronized snapshot could not commit to the Hub ledger."""


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
    sync_attempt_sequence=None,
    clock=None,
):
    snapshot = read_metric_snapshot(project_root)
    return import_metric_snapshot(
        store,
        request_id,
        snapshot,
        expected_project=expected_project,
        sync_attempt_sequence=sync_attempt_sequence,
        clock=clock,
    )


def import_metric_snapshot(
    store,
    request_id,
    snapshot,
    *,
    expected_project,
    sync_attempt_sequence=None,
    clock=None,
):
    """Validate and persist snapshot facts without resolving any project source."""
    result = _metric_result(snapshot, expected_project)
    request_identity = content_hash(
        [
            "project_metric_snapshot",
            snapshot["schema_version"],
            snapshot["snapshot_id"],
            snapshot["exporter"],
        ]
    )
    sync_attempt = None
    if sync_attempt_sequence is not None:
        require(
            type(sync_attempt_sequence) is int
            and sync_attempt_sequence > 0
            and callable(clock),
            "metric synchronization completion binding invalid",
        )
        sync_attempt = {
            "sequence": sync_attempt_sequence,
            "finished_at": clock(),
        }
    else:
        require(clock is None, "metric synchronization clock is unbound")
        store.begin(request_id, [result["project_id"]], request_identity)
    try:
        return store.save(
            request_id,
            result,
            sync_attempt=sync_attempt,
            request_identity=(
                request_identity
                if sync_attempt_sequence is not None
                else None
            ),
            rebind_uncommitted_request=sync_attempt_sequence is not None,
        )
    except (
        OSError,
        RecordError,
        RefreshLedgerError,
        sqlite3.Error,
        TypeError,
        ValueError,
        KeyError,
    ) as exc:
        if sync_attempt_sequence is None:
            raise
        raise MetricSnapshotPersistenceError(
            "synchronized metric snapshot persistence failed"
        ) from exc


def import_metric_snapshots(
    store,
    request_id,
    project_roots,
    *,
    expected_projects,
    timeout_seconds=DEFAULT_IMPORT_TIMEOUT_SECONDS,
):
    """Import a frozen project set, resuming only units without receipts."""
    require(
        type(project_roots) is dict
        and type(expected_projects) is dict
        and 0 < len(project_roots) <= 64
        and set(project_roots) == set(expected_projects),
        "batch snapshot bindings must be matching bounded objects",
    )
    frozen_roots, frozen_projects = {}, {}
    for project_id in sorted(project_roots):
        identifier(project_id, "batch snapshot project")
        expected = expected_projects[project_id]
        require(
            type(expected) is dict and expected.get("id") == project_id,
            "batch expected project binding mismatch",
        )
        try:
            frozen_roots[project_id] = str(Path(project_roots[project_id]).absolute())
            frozen_projects[project_id] = json.loads(
                json.dumps(
                    expected,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
        except (TypeError, ValueError) as exc:
            raise RecordError(
                "batch snapshot bindings must be canonical JSON and path-like"
            ) from exc
    project_ids = tuple(sorted(frozen_roots))
    _validate_timeout(timeout_seconds)
    request_identity = content_hash(
        [
            "project_metric_snapshot_batch",
            SNAPSHOT_SCHEMA_VERSION,
            frozen_roots,
            {
                project_id: content_hash(frozen_projects[project_id])
                for project_id in sorted(frozen_projects)
            },
        ]
    )
    store.begin(request_id, project_ids, request_identity)

    receipts, errors = {}, {}
    for project_id in project_ids:
        previous = store.receipt(request_id, project_id)
        if previous is not None:
            receipts[project_id] = previous
            continue
        try:
            snapshot = _read_with_timeout(
                frozen_roots[project_id], timeout_seconds
            )
            result = _metric_result(snapshot, frozen_projects[project_id])
            receipts[project_id] = store.save(request_id, result)
        except SnapshotImportTimeout:
            errors[project_id] = "snapshot_timeout"
        except (OSError, RecordError, ValueError, KeyError, TypeError):
            errors[project_id] = "snapshot_invalid_or_unavailable"
    return {
        "request_id": request_id,
        "requested": len(project_ids),
        "completed": len(receipts),
        "receipts": receipts,
        "errors": errors,
        "complete": len(receipts) == len(project_ids),
    }


def _metric_result(snapshot, expected_project):
    validate_metric_snapshot(snapshot)
    require(type(expected_project) is dict, "expected project binding must be an object")
    project_id = expected_project.get("id")
    identifier(project_id, "expected snapshot project")
    require(project_id == snapshot["project_id"], "snapshot project binding mismatch")
    return {
        "project_id": project_id,
        "observed_at": snapshot["observed_at"],
        "disposition": snapshot["disposition"],
        "metrics": snapshot["metrics"],
        "metric_definitions": snapshot["metric_definitions"],
        "management": snapshot["management"],
        "issues": snapshot["issues"],
        "source_versions": snapshot["source_versions"],
        "registry_binding": content_hash(expected_project),
        "collector_identity": content_hash(snapshot["exporter"]),
        "exporter": snapshot["exporter"],
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_kind": snapshot["kind"],
        "snapshot_schema_version": snapshot["schema_version"],
    }


def _validate_timeout(value):
    require(
        type(value) in (int, float)
        and math.isfinite(value)
        and MIN_IMPORT_TIMEOUT_SECONDS <= value <= MAX_IMPORT_TIMEOUT_SECONDS,
        "snapshot import timeout is outside the supported range",
    )


def _read_with_timeout(project_root, timeout_seconds):
    with _snapshot_deadline(timeout_seconds):
        return read_metric_snapshot(project_root)


@contextmanager
def _snapshot_deadline(timeout_seconds):
    """A synchronous POSIX deadline leaves no worker behind after timeout."""
    _validate_timeout(timeout_seconds)
    require(
        threading.current_thread() is threading.main_thread()
        and hasattr(signal, "SIGALRM")
        and hasattr(signal, "setitimer")
        and hasattr(signal, "getitimer")
        and hasattr(signal, "ITIMER_REAL"),
        "bounded snapshot import requires the main thread on this platform",
    )
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    require(
        previous_timer == (0.0, 0.0),
        "snapshot import cannot replace an existing process deadline",
    )
    previous_handler = signal.getsignal(signal.SIGALRM)

    def expired(signum, frame):
        raise SnapshotImportTimeout("snapshot read exceeded its deadline")

    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    finally:
        signal.signal(signal.SIGALRM, previous_handler)
