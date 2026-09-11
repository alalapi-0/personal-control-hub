"""Cache-first coordination for declared project metric snapshot exports.

The Hub process reads its local metric projection before visiting any project
root.  Each eligible project may then run exactly one reviewed Python export
entry in a bounded subprocess; the Hub imports only ``.hub/status.json``.
"""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path, PurePosixPath
import signal
import sqlite3
import stat
import subprocess
import sys
from typing import Callable

from hub.connection_records import (
    RecordError,
    content_hash,
    identifier,
    validate_declaration,
)
from hub.connection_refresh import RefreshLedgerError
from hub.connection_sources import (
    DECLARATION_PATH,
    SourceResolver,
    _root_path_allowed,
    _safe_relative_read,
)
from hub.connections import parse_source
from hub.metric_import import import_metric_snapshot_file
from hub.metric_store import MetricStore


SYNC_SCHEMA_VERSION = "1.0"
DEFAULT_EXPORT_TIMEOUT_SECONDS = 15.0
MIN_EXPORT_TIMEOUT_SECONDS = 0.01
MAX_EXPORT_TIMEOUT_SECONDS = 60.0
MAX_EXPORT_ENTRY_BYTES = 256 * 1024
MAX_SYNC_PROJECTS = 64


class MetricSyncUnitError(RuntimeError):
    """One safe per-project synchronization failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _is_removed(project: dict) -> bool:
    return (
        project.get("local_presence", {}).get("status") == "removed_local"
        or project.get("current_state_status") == "removed_local"
    )


def _is_enabled(project: dict) -> bool:
    return (
        not _is_removed(project)
        and project.get("connection_read_allowed", project.get("enabled")) is True
        and project.get("access_profile") != "no_current_goal_access"
    )


def _read_export_entry(
    root: Path,
    relative: str,
    *,
    authority_check: Callable[[], None],
) -> bytes:
    """Read one already-validated Python entry without following aliases."""
    parts = PurePosixPath(relative).parts
    authority_check()
    descriptor = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
    opened = [descriptor]
    file_descriptor = None
    try:
        for part in (*root.parts[1:], *parts[:-1]):
            authority_check()
            descriptor = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            opened.append(descriptor)
        authority_check()
        file_descriptor = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=descriptor,
        )
        before = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > MAX_EXPORT_ENTRY_BYTES
        ):
            raise MetricSyncUnitError("export_entry_unsafe")
        with os.fdopen(file_descriptor, "rb", closefd=True) as stream:
            file_descriptor = None
            payload = stream.read(MAX_EXPORT_ENTRY_BYTES + 1)
            after = os.fstat(stream.fileno())
        if (
            len(payload) > MAX_EXPORT_ENTRY_BYTES
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise MetricSyncUnitError("export_entry_changed")
        authority_check()
        return payload
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for item in reversed(opened):
            os.close(item)


class MetricSyncCoordinator:
    """Synchronize only explicitly declared snapshots into the Hub metric store."""

    def __init__(
        self,
        root: Path | str,
        *,
        db: Path | str | None = None,
        registry_path: Path | None = None,
        python_executable: Path | str | None = None,
    ) -> None:
        self.root = Path(root).absolute()
        self.db = db
        self.registry_path = registry_path
        self.python_executable = str(
            Path(python_executable or sys.executable).absolute()
        )

    def _resolver(self) -> SourceResolver:
        return SourceResolver(self.root, registry_path=self.registry_path)

    @staticmethod
    def _empty_cache(registry: dict) -> dict:
        projects = registry["projects"]
        removed = sum(_is_removed(project) for project in projects)
        return {
            "coverage": {
                "registered": len(projects),
                "local": len(projects) - removed,
                "removed_local": removed,
                "cloud_excluded": registry.get("cloud_excluded_count", 17),
                "dispositions": {},
                "freshness": {},
                "metrics": 0,
                "numeric": 0,
                "unknown": 0,
                "read_errors": 0,
                "data_gaps": 0,
                "business_blockers": 0,
                "issues": 0,
            },
            "projects": [],
            "projects_total": len(projects),
            "next_cursor": None,
        }

    def read_cache(self, *, after: int = 0, limit: int = 10) -> dict:
        """Read Hub-local projection only; a missing cache is an explicit value."""
        resolver = self._resolver()
        try:
            store = MetricStore(self.root, self.db, read_only=True)
            projection = store.coverage(
                resolver.registry,
                after=after,
                limit=limit,
            )
            available, reason = True, None
        except (OSError, RecordError, RefreshLedgerError, sqlite3.Error):
            projection = self._empty_cache(resolver.registry)
            available, reason = False, "metric_cache_unavailable"
        return {
            "schema_version": SYNC_SCHEMA_VERSION,
            "kind": "metric_sync_cache",
            "available": available,
            "reason": reason,
            **projection,
        }

    @staticmethod
    def _validate_timeout(timeout_seconds: float) -> float:
        if (
            type(timeout_seconds) not in (int, float)
            or not MIN_EXPORT_TIMEOUT_SECONDS
            <= timeout_seconds
            <= MAX_EXPORT_TIMEOUT_SECONDS
        ):
            raise RecordError("metric export timeout is outside the supported range")
        return float(timeout_seconds)

    @staticmethod
    def _unit_request_id(request_id: str, project_id: str) -> str:
        return "metric-sync-" + content_hash(
            ["metric_sync_unit", request_id, project_id]
        )[:24]

    def _binding(self, resolver: SourceResolver, project_id: str) -> dict:
        project = resolver.projects[project_id]
        if _is_removed(project):
            raise MetricSyncUnitError("removed_local")
        if not _is_enabled(project):
            raise MetricSyncUnitError("project_export_disabled")

        try:
            registered = Path(project["root_path"]).expanduser()
            _root_path_allowed(registered)
            root = registered.resolve(strict=True)
            _root_path_allowed(root)
            root_stat = root.stat()
            if not root.is_dir() or root.is_symlink():
                raise MetricSyncUnitError("project_root_unsafe")
            root_identity = (str(root), root_stat.st_dev, root_stat.st_ino)

            def check() -> None:
                resolver._check_registry()
                current = registered.resolve(strict=True)
                current_stat = current.stat()
                if (
                    str(current),
                    current_stat.st_dev,
                    current_stat.st_ino,
                ) != root_identity:
                    raise MetricSyncUnitError("export_binding_changed")

            declaration_bytes, declaration_metadata = _safe_relative_read(
                root,
                DECLARATION_PATH,
                authority_check=check,
            )
            declaration = validate_declaration(
                parse_source(declaration_bytes, "yaml"),
                project_id,
            )
            export = declaration.get("metric_export")
            if export is None:
                raise MetricSyncUnitError("metric_export_not_declared")
            entry_bytes = _read_export_entry(
                root,
                export["entry"],
                authority_check=check,
            )
            repeated, _ = _safe_relative_read(
                root,
                DECLARATION_PATH,
                authority_check=check,
            )
            if hashlib.sha256(repeated).hexdigest() != declaration_metadata["sha256"]:
                raise MetricSyncUnitError("export_binding_changed")
            return {
                "project": copy.deepcopy(project),
                "root": root,
                "declaration_sha256": declaration_metadata["sha256"],
                "entry": export["entry"],
                "entry_sha256": hashlib.sha256(entry_bytes).hexdigest(),
                "entry_bytes": entry_bytes,
                "check": check,
            }
        except MetricSyncUnitError:
            raise
        except FileNotFoundError as exc:
            raise MetricSyncUnitError("project_export_unavailable") from exc
        except PermissionError as exc:
            raise MetricSyncUnitError("project_export_permission_denied") from exc
        except (OSError, RecordError, TypeError, ValueError, KeyError) as exc:
            raise MetricSyncUnitError("project_export_invalid") from exc

    @staticmethod
    def _stop_process_group(process: subprocess.Popen) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except (AttributeError, OSError):
            try:
                process.kill()
            except (AttributeError, OSError):
                pass

    def _run_export(self, binding: dict, timeout_seconds: float) -> None:
        """Run the frozen entry bytes with no shell, inherited secrets, or output."""
        command = [
            self.python_executable,
            "-I",
            "-B",
            "-",
            "--hub-root",
            str(self.root),
            "--project-root",
            str(binding["root"]),
        ]
        environment = {
            "PATH": "",
            "PYTHONNOUSERSITE": "1",
            "PYTHONSAFEPATH": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
        }
        try:
            process = subprocess.Popen(
                command,
                cwd=binding["root"],
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise MetricSyncUnitError("export_process_unavailable") from exc
        try:
            process.communicate(
                input=binding["entry_bytes"],
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            self._stop_process_group(process)
            try:
                process.communicate(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
            raise MetricSyncUnitError("export_timeout") from exc
        finally:
            self._stop_process_group(process)
        if process.returncode != 0:
            raise MetricSyncUnitError("export_failed")

    def _assert_binding_current(
        self,
        binding: dict,
    ) -> None:
        try:
            binding["check"]()
            declaration, metadata = _safe_relative_read(
                binding["root"],
                DECLARATION_PATH,
                authority_check=binding["check"],
            )
            if metadata["sha256"] != binding["declaration_sha256"]:
                raise MetricSyncUnitError("export_binding_changed")
            validate_declaration(
                parse_source(declaration, "yaml"),
                binding["project"]["id"],
            )
            entry = _read_export_entry(
                binding["root"],
                binding["entry"],
                authority_check=binding["check"],
            )
            if hashlib.sha256(entry).hexdigest() != binding["entry_sha256"]:
                raise MetricSyncUnitError("export_binding_changed")
        except MetricSyncUnitError:
            raise
        except (OSError, RecordError, TypeError, ValueError, KeyError) as exc:
            raise MetricSyncUnitError("export_binding_changed") from exc

    def synchronize(
        self,
        request_id: str,
        *,
        project_ids: list[str] | None = None,
        timeout_seconds: float = DEFAULT_EXPORT_TIMEOUT_SECONDS,
    ) -> dict:
        """Export and import a frozen bounded project set, isolating each failure."""
        identifier(request_id, "metric sync request")
        timeout = self._validate_timeout(timeout_seconds)
        resolver = self._resolver()
        if project_ids is None:
            selected = sorted(
                project_id
                for project_id, project in resolver.projects.items()
                if _is_enabled(project)
            )
        else:
            if (
                type(project_ids) is not list
                or not project_ids
                or len(project_ids) > MAX_SYNC_PROJECTS
                or len(project_ids) != len(set(project_ids))
            ):
                raise RecordError("metric sync projects must be a bounded unique list")
            for project_id in project_ids:
                identifier(project_id, "metric sync project")
            if not set(project_ids) <= set(resolver.projects):
                raise RecordError("metric sync contains an unknown project")
            selected = sorted(project_ids)
        if len(selected) > MAX_SYNC_PROJECTS:
            raise RecordError("metric sync project set exceeds the supported bound")

        store = MetricStore(self.root, self.db)
        results: dict[str, dict] = {}
        skipped: dict[str, str] = {}
        errors: dict[str, str] = {}
        for project_id in selected:
            unit_request_id = self._unit_request_id(request_id, project_id)
            previous = store.receipt(unit_request_id, project_id)
            if previous is not None:
                results[project_id] = {"status": "reused", "receipt": previous}
                continue
            try:
                binding = self._binding(resolver, project_id)
                self._run_export(binding, timeout)
                self._assert_binding_current(binding)
                receipt = import_metric_snapshot_file(
                    store,
                    unit_request_id,
                    binding["root"],
                    expected_project=binding["project"],
                )
                results[project_id] = {"status": "imported", "receipt": receipt}
            except MetricSyncUnitError as exc:
                if exc.code in {
                    "removed_local",
                    "project_export_disabled",
                    "metric_export_not_declared",
                }:
                    skipped[project_id] = exc.code
                else:
                    errors[project_id] = exc.code
            except (
                OSError,
                RecordError,
                RefreshLedgerError,
                sqlite3.Error,
                TypeError,
                ValueError,
                KeyError,
            ):
                errors[project_id] = "snapshot_import_failed"
        return {
            "schema_version": SYNC_SCHEMA_VERSION,
            "kind": "metric_sync_result",
            "request_id": request_id,
            "requested": len(selected),
            "completed": len(results),
            "results": results,
            "skipped": skipped,
            "errors": errors,
            "complete": not errors,
        }


def cache_then_sync(
    coordinator: MetricSyncCoordinator,
    request_id: str,
    *,
    mode: str,
    emit_cache: Callable[[dict], None],
    project_ids: list[str] | None = None,
    timeout_seconds: float = DEFAULT_EXPORT_TIMEOUT_SECONDS,
) -> dict:
    """Emit the cache synchronously before any project export is considered."""
    if mode not in {"startup", "manual"}:
        raise RecordError("unsupported metric sync mode")
    cache = coordinator.read_cache()
    emit_cache(
        {
            "schema_version": SYNC_SCHEMA_VERSION,
            "kind": "metric_sync_event",
            "mode": mode,
            "phase": "cache",
            "data": cache,
        }
    )
    return coordinator.synchronize(
        request_id,
        project_ids=project_ids,
        timeout_seconds=timeout_seconds,
    )
