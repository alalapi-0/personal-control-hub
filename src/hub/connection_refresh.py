"""Durable, Hub-local connection refresh history and current projections.

The ledger stores frozen authority capsules and opaque, resolver-validated
results.  Rebuild and history only read this Hub database; they never resolve
registry roots or source paths.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence
from urllib.parse import quote


SCHEMA_VERSION = "1.0"
GENESIS_HASH = "0" * 64
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
AUTHORITY_FIELDS = {
    "source_plan_id", "source_plan_hash", "manifest_id", "manifest_hash",
    "registry_hash", "adapter_version", "adapter_hash",
    "accepted_inventory_hash", "accepted_candidate",
}


class RefreshLedgerError(RuntimeError):
    """Base class with a stable machine-facing diagnostic code."""

    code = "LEDGER_ERROR"


class LedgerPathError(RefreshLedgerError):
    code = "UNSAFE_LEDGER_PATH"


class LedgerSchemaError(RefreshLedgerError):
    code = "UNSUPPORTED_LEDGER_SCHEMA"


class LedgerCorruptionError(RefreshLedgerError):
    code = "LEDGER_CORRUPT"


class LedgerBusyError(RefreshLedgerError):
    code = "LEDGER_LOCKED"


class HeadConflictError(RefreshLedgerError):
    code = "EXPECTED_HEAD_CONFLICT"


class RequestConflictError(RefreshLedgerError):
    code = "REQUEST_IDENTITY_CONFLICT"


class ResultConflictError(RefreshLedgerError):
    code = "PROJECT_RESULT_CONFLICT"


class IncompleteRequestError(RefreshLedgerError):
    code = "REQUEST_INCOMPLETE"


class ResultValidationError(RefreshLedgerError):
    code = "RESULT_VALIDATION_FAILED"


class PrecommitFaultError(RefreshLedgerError):
    code = "PRECOMMIT_FAULT"


class ReadOnlyLedgerError(RefreshLedgerError):
    code = "LEDGER_READ_ONLY"


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ResultValidationError("value is not canonical JSON") from exc


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ResultValidationError(f"{label}: timezone timestamp required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResultValidationError(f"{label}: invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ResultValidationError(f"{label}: timezone required")
    return value


def _validate_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise RefreshLedgerError(f"{label}: invalid identifier")
    return value


def _validate_authority(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != AUTHORITY_FIELDS:
        raise RequestConflictError("authority capsule has missing or unknown fields")
    capsule = dict(value)
    for field in AUTHORITY_FIELDS:
        if not isinstance(capsule[field], str) or not capsule[field]:
            raise RequestConflictError(f"authority.{field}: nonempty string required")
    for field in ("source_plan_hash", "manifest_hash", "registry_hash",
                  "adapter_hash", "accepted_inventory_hash", "accepted_candidate"):
        if not SHA256.fullmatch(capsule[field]):
            raise RequestConflictError(f"authority.{field}: invalid sha256")
    _canonical(capsule)
    return capsule


def _safe_database_path(root: Path, path: Path | str | None, *, create: bool) -> Path:
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise LedgerPathError("Hub root must be an existing ordinary directory")
    allowed = (root / "data" / "design_governance",
               root / "docs" / "reports" / "ui_design_governance")
    candidate = (root / "data" / "design_governance" / "connection_refresh.sqlite3"
                 if path is None else Path(path))
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.absolute()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise LedgerPathError("ledger path escapes Hub root") from exc
    if ".." in relative.parts or candidate.name in {"", ".", ".."}:
        raise LedgerPathError("ledger path is not a concrete Hub file")
    if not any(candidate == base or base in candidate.parents for base in allowed):
        raise LedgerPathError("ledger path must be under an approved Hub data/report directory")
    if candidate in allowed:
        raise LedgerPathError("ledger path cannot replace an approved directory")

    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.exists() or current.is_symlink():
            if current.is_symlink() or not current.is_dir():
                raise LedgerPathError("ledger parent contains a symlink or non-directory")
        elif not create:
            raise LedgerPathError("read-only ledger parent does not exist")
        else:
            current.mkdir()
    if candidate.exists() or candidate.is_symlink():
        if candidate.is_symlink() or not candidate.is_file():
            raise LedgerPathError("ledger target must be an ordinary non-symlink file")
        if candidate.stat().st_nlink != 1:
            raise LedgerPathError("ledger target must not be a multiply-linked file")
        with candidate.open("rb") as handle:
            header = handle.read(16)
        if header != b"SQLite format 3\x00":
            raise LedgerPathError("refusing to overwrite an unknown existing file")
    elif not create:
        raise LedgerPathError("read-only ledger does not exist")
    return candidate


class RefreshLedger:
    """SQLite-backed immutable refresh ledger with global CAS and hash links."""

    def __init__(self, root: Path | str, path: Path | str | None = None, *,
                 timeout: float = 5.0,
                 result_validator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
                 clock: Callable[[], str] = _now,
                 precommit_fault: Callable[[str, Mapping[str, Any]], None] | None = None,
                 read_only: bool = False) -> None:
        self.root = Path(root).absolute()
        self.read_only = read_only
        self.path = _safe_database_path(self.root, path, create=not read_only)
        self.timeout = timeout
        self.result_validator = result_validator
        self.clock = clock
        self.precommit_fault = precommit_fault
        if read_only:
            connection = self._connect()
            try:
                self._check_schema(connection)
            finally:
                connection.close()
        else:
            self._initialize()

    def _connect(self) -> sqlite3.Connection:
        try:
            target: str | Path = self.path
            kwargs: dict[str, Any] = {}
            if self.read_only:
                target = f"file:{quote(str(self.path), safe='/')}?mode=ro"
                kwargs["uri"] = True
            connection = sqlite3.connect(target, timeout=self.timeout, **kwargs)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {max(0, int(self.timeout * 1000))}")
            return connection
        except sqlite3.Error as exc:
            self._translate_sqlite(exc)
            raise AssertionError("unreachable")

    @staticmethod
    def _translate_sqlite(exc: sqlite3.Error) -> None:
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            raise LedgerBusyError("refresh ledger is locked by another writer") from exc
        raise LedgerCorruptionError("refresh ledger cannot be read safely") from exc

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            try:
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                owned = {"ledger_meta", "refresh_requests", "refresh_events", "refresh_results"}
                if tables == owned:
                    self._check_schema(connection)
                    return
                if tables:
                    raise LedgerSchemaError("existing SQLite file is not a refresh ledger")
                connection.execute("BEGIN IMMEDIATE")
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS ledger_meta (
                        singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                        schema_version TEXT NOT NULL,
                        head_sequence INTEGER NOT NULL,
                        head_hash TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS refresh_requests (
                        request_id TEXT PRIMARY KEY,
                        project_ids_json TEXT NOT NULL,
                        authority_json TEXT NOT NULL,
                        authority_hash TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('OPEN','FINISHED')),
                        started_at TEXT NOT NULL,
                        finished_at TEXT,
                        begin_sequence INTEGER NOT NULL UNIQUE,
                        finish_sequence INTEGER UNIQUE
                    );
                    CREATE TABLE IF NOT EXISTS refresh_events (
                        sequence INTEGER PRIMARY KEY,
                        event_id TEXT NOT NULL UNIQUE,
                        event_type TEXT NOT NULL CHECK(event_type IN ('REQUEST_BEGUN','PROJECT_RESULT','REQUEST_FINISHED')),
                        request_id TEXT NOT NULL REFERENCES refresh_requests(request_id),
                        project_id TEXT,
                        created_at TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        payload_hash TEXT NOT NULL,
                        previous_hash TEXT NOT NULL,
                        event_hash TEXT NOT NULL UNIQUE
                    );
                    CREATE TABLE IF NOT EXISTS refresh_results (
                        request_id TEXT NOT NULL REFERENCES refresh_requests(request_id),
                        project_id TEXT NOT NULL,
                        result_ref TEXT NOT NULL UNIQUE,
                        result_json TEXT NOT NULL,
                        result_hash TEXT NOT NULL,
                        success INTEGER NOT NULL CHECK(success IN (0,1)),
                        event_sequence INTEGER NOT NULL UNIQUE REFERENCES refresh_events(sequence),
                        PRIMARY KEY(request_id, project_id)
                    );
                """)
                row = connection.execute("SELECT * FROM ledger_meta WHERE singleton=1").fetchone()
                if row is None:
                    connection.execute("INSERT INTO ledger_meta VALUES (1,?,?,?)",
                                       (SCHEMA_VERSION, 0, GENESIS_HASH))
                elif row["schema_version"] != SCHEMA_VERSION:
                    raise LedgerSchemaError("unsupported refresh ledger schema")
                connection.commit()
            except (LedgerSchemaError, sqlite3.Error):
                connection.rollback()
                raise
        except sqlite3.Error as exc:
            self._translate_sqlite(exc)
        finally:
            connection.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            raise ReadOnlyLedgerError("read-only ledger cannot begin a write transaction")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._check_schema(connection)
            yield connection
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            self._translate_sqlite(exc)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _check_schema(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT schema_version FROM ledger_meta WHERE singleton=1").fetchone()
        if row is None or row[0] != SCHEMA_VERSION:
            raise LedgerSchemaError("unsupported or missing refresh ledger schema")

    @staticmethod
    def _head_in(connection: sqlite3.Connection) -> dict[str, Any]:
        row = connection.execute(
            "SELECT head_sequence, head_hash FROM ledger_meta WHERE singleton=1").fetchone()
        if row is None:
            raise LedgerCorruptionError("ledger head is missing")
        return {"sequence": row[0], "hash": row[1]}

    @property
    def head(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            self._verified_rows(connection)
            return self._head_in(connection)
        except sqlite3.Error as exc:
            self._translate_sqlite(exc)
            raise AssertionError("unreachable")
        finally:
            connection.close()

    @staticmethod
    def _assert_expected(actual: Mapping[str, Any], expected: Mapping[str, Any] | None) -> None:
        if expected is None:
            return
        if (not isinstance(expected, Mapping) or set(expected) != {"sequence", "hash"}
                or type(expected["sequence"]) is not int
                or not isinstance(expected["hash"], str)):
            raise HeadConflictError("expected_head must contain exact sequence and hash")
        if dict(expected) != dict(actual):
            raise HeadConflictError(
                f"expected head {expected['sequence']} does not match current head {actual['sequence']}")

    def _append_event(self, connection: sqlite3.Connection, event_type: str,
                      request_id: str, project_id: str | None, created_at: str,
                      payload: Mapping[str, Any]) -> tuple[int, str]:
        head = self._head_in(connection)
        sequence = head["sequence"] + 1
        payload_json = _canonical(payload)
        payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
        event_id = f"refresh-event-{sequence}-{payload_hash[:16]}"
        event_core = {"schema_version": SCHEMA_VERSION, "sequence": sequence,
                      "event_id": event_id, "event_type": event_type,
                      "request_id": request_id, "project_id": project_id,
                      "created_at": created_at, "payload_hash": payload_hash,
                      "previous_hash": head["hash"]}
        event_hash = _hash(event_core)
        connection.execute(
            "INSERT INTO refresh_events VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sequence, event_id, event_type, request_id, project_id, created_at,
             payload_json, payload_hash, head["hash"], event_hash))
        connection.execute(
            "UPDATE ledger_meta SET head_sequence=?, head_hash=? WHERE singleton=1",
            (sequence, event_hash))
        return sequence, event_hash

    def _fault(self, operation: str, context: Mapping[str, Any]) -> None:
        if self.precommit_fault is None:
            return
        try:
            self.precommit_fault(operation, context)
        except Exception as exc:
            raise PrecommitFaultError(f"{operation} aborted before commit") from exc

    def begin_request(self, request_id: str, project_ids: Sequence[str],
                      authority: Mapping[str, Any], *,
                      expected_head: Mapping[str, Any] | None = None) -> dict[str, Any]:
        request_id = _validate_id(request_id, "request_id")
        if (not isinstance(project_ids, Sequence) or isinstance(project_ids, (str, bytes))
                or not project_ids):
            raise RequestConflictError("project_ids must be a nonempty sequence")
        projects = sorted(_validate_id(value, "project_id") for value in project_ids)
        if len(projects) != len(set(projects)):
            raise RequestConflictError("project_ids contain duplicates")
        capsule = _validate_authority(authority)
        projects_json, authority_json = _canonical(projects), _canonical(capsule)
        authority_hash = hashlib.sha256(authority_json.encode()).hexdigest()
        with self._write() as connection:
            self._verified_rows(connection)
            existing = connection.execute(
                "SELECT * FROM refresh_requests WHERE request_id=?", (request_id,)).fetchone()
            if existing is not None:
                if (existing["project_ids_json"] != projects_json
                        or existing["authority_json"] != authority_json):
                    raise RequestConflictError("request ID is already bound to different authority or projects")
                return self._request_view(connection, existing)
            self._assert_expected(self._head_in(connection), expected_head)
            started_at = _validate_timestamp(self.clock(), "started_at")
            next_sequence = self._head_in(connection)["sequence"] + 1
            connection.execute(
                "INSERT INTO refresh_requests VALUES (?,?,?,?,?,?,?,?,?)",
                (request_id, projects_json, authority_json, authority_hash, "OPEN",
                 started_at, None, next_sequence, None))
            payload = {"schema_version": SCHEMA_VERSION, "request_id": request_id,
                       "project_ids": projects, "authority": capsule,
                       "authority_hash": authority_hash, "started_at": started_at}
            sequence, _ = self._append_event(connection, "REQUEST_BEGUN", request_id,
                                             None, started_at, payload)
            if sequence != next_sequence:
                raise LedgerCorruptionError("begin event sequence diverged")
            self._fault("begin_request", payload)
            row = connection.execute(
                "SELECT * FROM refresh_requests WHERE request_id=?", (request_id,)).fetchone()
            return self._request_view(connection, row)

    def _validate_result(self, result: Any,
                         validator: Callable[[dict[str, Any]], dict[str, Any]] | None) -> dict[str, Any]:
        callback = validator or self.result_validator
        if callback is None:
            raise ResultValidationError("a SourceResolver result validator is required")
        if not isinstance(result, dict):
            raise ResultValidationError("resolver result must be an object")
        try:
            validated = callback(result)
        except Exception as exc:
            raise ResultValidationError("SourceResolver rejected the result") from exc
        if not isinstance(validated, dict) or _canonical(validated) != _canonical(result):
            raise ResultValidationError("validator must return the unchanged canonical result")
        if (validated.get("schema_version") != "1.0"
                or validated.get("kind") != "source_resolution"
                or type(validated.get("success")) is not bool):
            raise ResultValidationError("result has unsupported identity or success semantics")
        _validate_timestamp(validated.get("observed_at"), "result.observed_at")
        return validated

    def append_project_result(self, request_id: str, project_id: str,
                              result: dict[str, Any], *,
                              validator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
                              expected_head: Mapping[str, Any] | None = None) -> dict[str, Any]:
        request_id = _validate_id(request_id, "request_id")
        project_id = _validate_id(project_id, "project_id")
        validated = self._validate_result(result, validator)
        result_json = _canonical(validated)
        result_hash = hashlib.sha256(result_json.encode()).hexdigest()
        with self._write() as connection:
            self._verified_rows(connection)
            request = connection.execute(
                "SELECT * FROM refresh_requests WHERE request_id=?", (request_id,)).fetchone()
            if request is None:
                raise RequestConflictError("unknown request ID")
            projects = json.loads(request["project_ids_json"])
            authority = json.loads(request["authority_json"])
            if project_id not in projects:
                raise RequestConflictError("project is outside the frozen request")
            if validated.get("project_id") != project_id:
                raise ResultValidationError("result project does not match append target")
            if validated.get("authority") != authority:
                raise RequestConflictError("result authority differs from request authority")
            existing = connection.execute(
                "SELECT * FROM refresh_results WHERE request_id=? AND project_id=?",
                (request_id, project_id)).fetchone()
            if existing is not None:
                if existing["result_hash"] != result_hash or existing["result_json"] != result_json:
                    raise ResultConflictError("project already has a different immutable result")
                return self._result_view(existing)
            if request["status"] != "OPEN":
                raise RequestConflictError("finished request cannot accept another result")
            self._assert_expected(self._head_in(connection), expected_head)
            created_at = _validate_timestamp(self.clock(), "event.created_at")
            result_ref = f"refresh-result:{request_id}:{project_id}:{result_hash}"
            payload = {"schema_version": SCHEMA_VERSION, "request_id": request_id,
                       "project_id": project_id, "result_ref": result_ref,
                       "result_hash": result_hash, "success": validated["success"],
                       "observed_at": validated["observed_at"]}
            sequence, _ = self._append_event(connection, "PROJECT_RESULT", request_id,
                                             project_id, created_at, payload)
            connection.execute(
                "INSERT INTO refresh_results VALUES (?,?,?,?,?,?,?)",
                (request_id, project_id, result_ref, result_json, result_hash,
                 int(validated["success"]), sequence))
            self._fault("append_project_result", payload)
            row = connection.execute(
                "SELECT * FROM refresh_results WHERE request_id=? AND project_id=?",
                (request_id, project_id)).fetchone()
            return self._result_view(row)

    def finish_request(self, request_id: str, *,
                       expected_head: Mapping[str, Any] | None = None) -> dict[str, Any]:
        request_id = _validate_id(request_id, "request_id")
        with self._write() as connection:
            self._verified_rows(connection)
            request = connection.execute(
                "SELECT * FROM refresh_requests WHERE request_id=?", (request_id,)).fetchone()
            if request is None:
                raise RequestConflictError("unknown request ID")
            if request["status"] == "FINISHED":
                return self._request_view(connection, request)
            projects = json.loads(request["project_ids_json"])
            completed = {row[0] for row in connection.execute(
                "SELECT project_id FROM refresh_results WHERE request_id=?", (request_id,))}
            missing = sorted(set(projects) - completed)
            if missing:
                raise IncompleteRequestError(f"request is missing project results: {missing}")
            self._assert_expected(self._head_in(connection), expected_head)
            finished_at = _validate_timestamp(self.clock(), "finished_at")
            payload = {"schema_version": SCHEMA_VERSION, "request_id": request_id,
                       "project_ids": projects, "result_count": len(completed),
                       "finished_at": finished_at}
            sequence, _ = self._append_event(connection, "REQUEST_FINISHED", request_id,
                                             None, finished_at, payload)
            connection.execute(
                "UPDATE refresh_requests SET status='FINISHED', finished_at=?, finish_sequence=? "
                "WHERE request_id=?", (finished_at, sequence, request_id))
            self._fault("finish_request", payload)
            row = connection.execute(
                "SELECT * FROM refresh_requests WHERE request_id=?", (request_id,)).fetchone()
            return self._request_view(connection, row)

    @staticmethod
    def _result_view(row: sqlite3.Row) -> dict[str, Any]:
        return {"request_id": row["request_id"], "project_id": row["project_id"],
                "result_ref": row["result_ref"], "result_hash": row["result_hash"],
                "success": bool(row["success"]), "event_sequence": row["event_sequence"],
                "result": json.loads(row["result_json"])}

    def _request_view(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        completed = [item[0] for item in connection.execute(
            "SELECT project_id FROM refresh_results WHERE request_id=? ORDER BY project_id",
            (row["request_id"],))]
        return {"request_id": row["request_id"],
                "project_ids": json.loads(row["project_ids_json"]),
                "authority": json.loads(row["authority_json"]),
                "authority_hash": row["authority_hash"], "status": row["status"],
                "started_at": row["started_at"], "finished_at": row["finished_at"],
                "begin_sequence": row["begin_sequence"],
                "finish_sequence": row["finish_sequence"],
                "completed_project_ids": completed,
                "remaining_project_ids": sorted(set(json.loads(row["project_ids_json"])) - set(completed))}

    def _verified_rows(self, connection: sqlite3.Connection) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
        self._check_schema(connection)
        events = connection.execute("SELECT * FROM refresh_events ORDER BY sequence").fetchall()
        requests = {row["request_id"]: row for row in connection.execute(
            "SELECT * FROM refresh_requests")}
        results = connection.execute("SELECT * FROM refresh_results ORDER BY event_sequence").fetchall()
        result_by_sequence = {row["event_sequence"]: row for row in results}
        for request in requests.values():
            try:
                project_ids = json.loads(request["project_ids_json"])
                authority = json.loads(request["authority_json"])
            except (json.JSONDecodeError, TypeError) as exc:
                raise LedgerCorruptionError("request identity is not JSON") from exc
            if (_canonical(project_ids) != request["project_ids_json"]
                    or not isinstance(project_ids, list) or project_ids != sorted(set(project_ids))
                    or _canonical(authority) != request["authority_json"]
                    or _hash(authority) != request["authority_hash"]
                    or request["status"] not in {"OPEN", "FINISHED"}
                    or (request["status"] == "OPEN" and
                        (request["finished_at"] is not None or request["finish_sequence"] is not None))):
                raise LedgerCorruptionError("request identity or lifecycle is inconsistent")
        previous, expected_sequence = GENESIS_HASH, 1
        seen_results: set[tuple[str, str]] = set()
        for event in events:
            if event["sequence"] != expected_sequence or event["previous_hash"] != previous:
                raise LedgerCorruptionError("event sequence or hash link is broken")
            try:
                payload = json.loads(event["payload_json"])
            except (json.JSONDecodeError, TypeError) as exc:
                raise LedgerCorruptionError("event payload is not canonical JSON") from exc
            if _canonical(payload) != event["payload_json"] or _hash(payload) != event["payload_hash"]:
                raise LedgerCorruptionError("event payload hash mismatch")
            core = {"schema_version": SCHEMA_VERSION, "sequence": event["sequence"],
                    "event_id": event["event_id"], "event_type": event["event_type"],
                    "request_id": event["request_id"], "project_id": event["project_id"],
                    "created_at": event["created_at"], "payload_hash": event["payload_hash"],
                    "previous_hash": event["previous_hash"]}
            if _hash(core) != event["event_hash"]:
                raise LedgerCorruptionError("event identity hash mismatch")
            request = requests.get(event["request_id"])
            if request is None:
                raise LedgerCorruptionError("event references a missing request")
            if event["event_type"] == "REQUEST_BEGUN":
                if event["project_id"] is not None or request["begin_sequence"] != event["sequence"]:
                    raise LedgerCorruptionError("begin event reference mismatch")
                if (set(payload) != {"schema_version", "request_id", "project_ids", "authority",
                                     "authority_hash", "started_at"}
                        or payload.get("schema_version") != SCHEMA_VERSION
                        or payload.get("request_id") != request["request_id"]
                        or payload.get("started_at") != request["started_at"]
                        or event["created_at"] != request["started_at"]
                        or payload.get("authority_hash") != request["authority_hash"]
                        or _canonical(payload.get("authority")) != request["authority_json"]
                        or _canonical(payload.get("project_ids")) != request["project_ids_json"]):
                    raise LedgerCorruptionError("begin event authority mismatch")
            elif event["event_type"] == "PROJECT_RESULT":
                row = result_by_sequence.get(event["sequence"])
                if row is None or row["request_id"] != event["request_id"] or row["project_id"] != event["project_id"]:
                    raise LedgerCorruptionError("project event has a broken result reference")
                try:
                    result = json.loads(row["result_json"])
                except json.JSONDecodeError as exc:
                    raise LedgerCorruptionError("stored result is not JSON") from exc
                if (set(payload) != {"schema_version", "request_id", "project_id", "result_ref",
                                     "result_hash", "success", "observed_at"}
                        or payload.get("schema_version") != SCHEMA_VERSION
                        or payload.get("request_id") != event["request_id"]
                        or payload.get("project_id") != event["project_id"]
                        or row["project_id"] not in json.loads(request["project_ids_json"])
                        or _canonical(result) != row["result_json"]
                        or hashlib.sha256(row["result_json"].encode()).hexdigest() != row["result_hash"]
                        or payload.get("result_ref") != row["result_ref"]
                        or payload.get("result_hash") != row["result_hash"]
                        or type(payload.get("success")) is not bool
                        or payload.get("success") != bool(row["success"])
                        or payload.get("observed_at") != result.get("observed_at")):
                    raise LedgerCorruptionError("stored result identity mismatch")
                if result.get("authority") != json.loads(request["authority_json"]):
                    raise LedgerCorruptionError("stored result authority mismatch")
                seen_results.add((row["request_id"], row["project_id"]))
            elif event["event_type"] == "REQUEST_FINISHED":
                if request["finish_sequence"] != event["sequence"] or request["status"] != "FINISHED":
                    raise LedgerCorruptionError("finish event reference mismatch")
                project_ids = set(json.loads(request["project_ids_json"]))
                if (set(payload) != {"schema_version", "request_id", "project_ids",
                                     "result_count", "finished_at"}
                        or payload.get("schema_version") != SCHEMA_VERSION
                        or payload.get("request_id") != request["request_id"]
                        or payload.get("project_ids") != json.loads(request["project_ids_json"])
                        or payload.get("result_count") != len(project_ids)
                        or payload.get("finished_at") != request["finished_at"]
                        or event["created_at"] != request["finished_at"]
                        or {pid for rid, pid in seen_results if rid == request["request_id"]} != project_ids):
                    raise LedgerCorruptionError("finished request lacks complete prior results")
            else:
                raise LedgerSchemaError("unknown refresh event schema")
            previous, expected_sequence = event["event_hash"], expected_sequence + 1
        head = self._head_in(connection)
        if head != {"sequence": len(events), "hash": previous}:
            raise LedgerCorruptionError("ledger head does not match immutable events")
        if len(results) != len(seen_results):
            raise LedgerCorruptionError("orphan or duplicate result rows")
        for request in requests.values():
            if request["status"] == "FINISHED" and request["finish_sequence"] is None:
                raise LedgerCorruptionError("finished request has no finish event")
        return events, results

    def history(self, request_id: str | None = None, *,
                current_authority: Mapping[str, Any] | None = None) -> dict[str, Any]:
        authority = _validate_authority(current_authority) if current_authority is not None else None
        connection = self._connect()
        try:
            # Pin schema, immutable rows, request projections, and the final head
            # to one snapshot. In WAL mode a writer may commit while this reader
            # runs; every SELECT below must still describe the same ledger head.
            connection.execute("BEGIN")
            events, results = self._verified_rows(connection)
            request_rows = connection.execute(
                "SELECT * FROM refresh_requests ORDER BY begin_sequence").fetchall()
            if request_id is not None:
                request_id = _validate_id(request_id, "request_id")
                request_rows = [row for row in request_rows if row["request_id"] == request_id]
                if not request_rows:
                    raise RequestConflictError("unknown request ID")
                allowed = {request_id}
                events = [row for row in events if row["request_id"] in allowed]
                results = [row for row in results if row["request_id"] in allowed]
            requests = []
            for row in request_rows:
                view = self._request_view(connection, row)
                view["authority_drift"] = authority is not None and view["authority"] != authority
                requests.append(view)
            return {"schema_version": SCHEMA_VERSION, "head": self._head_in(connection),
                    "requests": requests,
                    "events": [{"sequence": row["sequence"], "event_id": row["event_id"],
                                "event_type": row["event_type"], "request_id": row["request_id"],
                                "project_id": row["project_id"], "created_at": row["created_at"],
                                "payload": json.loads(row["payload_json"]),
                                "previous_hash": row["previous_hash"], "event_hash": row["event_hash"]}
                               for row in events],
                    "results": [self._result_view(row) for row in results]}
        except sqlite3.Error as exc:
            self._translate_sqlite(exc)
            raise AssertionError("unreachable")
        finally:
            connection.close()

    def rebuild(self, *, current_authority: Mapping[str, Any] | None = None,
                validator: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> dict[str, Any]:
        """Verify immutable storage and derive latest-attempt/last-success views."""
        history = self.history(current_authority=current_authority)
        callback = validator or self.result_validator
        if callback is None:
            raise ResultValidationError("offline rebuild requires the bound result authority validator")
        projects: dict[str, dict[str, Any]] = {}
        for stored in history["results"]:
            validated = self._validate_result(stored["result"], callback)
            if _hash(validated) != stored["result_hash"]:
                raise LedgerCorruptionError("offline validator changed stored result identity")
            project = projects.setdefault(stored["project_id"], {
                "latest_attempt": None, "last_success": None,
                "freshness": "unknown", "stale_reason": "no successful source result has been recorded",
                "authority_drift": False,
            })
            reference = {"request_id": stored["request_id"],
                         "event_sequence": stored["event_sequence"],
                         "result_ref": stored["result_ref"],
                         "result_hash": stored["result_hash"],
                         "observed_at": validated["observed_at"],
                         "disposition": validated.get("disposition"),
                         "success": stored["success"]}
            project["latest_attempt"] = reference
            project["authority_drift"] = (current_authority is not None and
                                           validated["authority"] != current_authority)
            if stored["success"]:
                project["last_success"] = reference
                project["freshness"] = "fresh"
                project["stale_reason"] = None
            elif project["last_success"] is not None:
                project["freshness"] = "stale"
                errors = validated.get("errors")
                project["stale_reason"] = (_canonical(errors) if errors
                                            else "latest source attempt was not successful")
            else:
                project["freshness"] = "unknown"
                errors = validated.get("errors")
                project["stale_reason"] = (_canonical(errors) if errors
                                            else "no successful source result has been recorded")
            if project["authority_drift"] and project["freshness"] == "fresh":
                project["freshness"] = "stale"
                project["stale_reason"] = "recorded source authority differs from current authority"
        return {"schema_version": SCHEMA_VERSION, "head": history["head"],
                "authority_drift": any(row["authority_drift"] for row in history["requests"]),
                "projects": projects}


def _resolver_authority(resolver: Any) -> Mapping[str, Any]:
    authority = getattr(resolver, "authority", None)
    if callable(authority):
        authority = authority()
    return _validate_authority(authority)


def refresh(ledger: RefreshLedger, resolver: Any, request_id: str,
            project_ids: Sequence[str], *,
            expected_head: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Resume or execute one bounded request, committing each project immediately."""
    validator = getattr(resolver, "validate_result", None)
    if not callable(validator) or not callable(getattr(resolver, "refresh", None)):
        raise ResultValidationError("resolver must provide refresh and validate_result")
    authority = _resolver_authority(resolver)
    request = ledger.begin_request(request_id, project_ids, authority,
                                   expected_head=expected_head)
    completed = set(request["completed_project_ids"])
    failures: dict[str, str] = {}
    appended: list[str] = []
    for project_id in sorted(request["project_ids"]):
        if project_id in completed:
            continue
        try:
            result = resolver.refresh(project_id)
            ledger.append_project_result(request_id, project_id, result,
                                         validator=validator)
            appended.append(project_id)
        except ResultValidationError as exc:
            failures[project_id] = exc.code
        except RefreshLedgerError:
            raise
        except Exception as exc:
            # Resolver exceptions are not fabricated into source facts. A later
            # call resumes this exact missing project without re-reading commits.
            failures[project_id] = type(exc).__name__
    request = ledger.history(request_id)["requests"][0]
    if not request["remaining_project_ids"]:
        request = ledger.finish_request(request_id)
    # A ledger-level dispatcher can validate immutable results from older source
    # plan revisions.  The active resolver validator only covers this request.
    projection_validator = ledger.result_validator or validator
    return {"request": request, "appended_project_ids": appended,
            "resolver_errors": failures,
            "projection": ledger.rebuild(validator=projection_validator)}
