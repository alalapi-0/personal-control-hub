"""Registry-complete projection, reusing latest-attempt/last-success separation.

Reads only the Hub ledger, never external roots. A previous successful read is
explicitly historical whenever a newer attempt fails or its authority expires.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hub.connection_records import (FIELDS, FORBIDDEN_PARTS, VERSION, RecordError, content_hash,
                                    empty_business, exact, fingerprint, identifier, require, string,
                                    timestamp, typed, validate_authority, validate_result)
from hub.connection_refresh import (GENESIS_HASH, HeadConflictError, LedgerBusyError,
                                    LedgerCorruptionError, LedgerPathError, LedgerSchemaError,
                                    PrecommitFaultError, RefreshLedger, RefreshLedgerError,
                                    RequestConflictError, ResultValidationError,
                                    refresh as refresh_projects)
from hub.connection_sources import SourceResolver
from hub.service_contract import ServiceError


QUERY_FIELDS = {"q", "status", "freshness", "order", "offset", "limit"}
ORDER_FIELDS = {"project_id", "name", "status", "freshness"}
REFRESH_FIELDS = {"request_id", "project_ids", "expected_head"}
HEAD_FIELDS = {"sequence", "hash"}
SERVICE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_QUERY = 240
MAX_PAGE = 100
MAX_RELATIONS_BYTES = 1024 * 1024


def _error(code: str, *, status: int = 400, retryable: bool = False,
           outcome: str = "NOT_COMMITTED",
           details: dict[str, Any] | None = None) -> ServiceError:
    return ServiceError(code, status=status, retryable=retryable,
                        outcome=outcome, details=details)


def _age(observed: str, now: datetime) -> float:
    return (now - datetime.fromisoformat(observed.replace("Z", "+00:00"))).total_seconds()


def row_update_key(row: dict) -> str:
    return content_hash({"project_id": row["project_id"], "name": row["name"],
                         "latest": row["latest_attempt"]["update_key"] if row["latest_attempt"] else None,
                         "last_success": row["last_success"]["update_key"] if row["last_success"] else None,
                         "freshness": row["freshness"]["state"], "authority_drift": row["freshness"]["authority_drift"]})


def project_snapshot(registry: dict, authority: dict, ledger: RefreshLedger | None, *,
                     observed_at: str | None = None) -> dict:
    observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    current = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    require(current.tzinfo is not None, "projection timestamp needs timezone")
    # One verified SQLite snapshot prevents mixing refs across concurrent commits.
    history = ledger.history(current_authority=authority) if ledger else {"head": None, "results": []}
    attempts, successes = {}, {}
    for item in history["results"]:
        record = validate_result(item["result"])
        attempts[record["project_id"]] = record
        if record["success"]:
            successes[record["project_id"]] = record
    projects = []
    for project in sorted(registry["projects"], key=lambda row: row["id"]):
        project_id = project["id"]
        removed = (project.get("local_presence", {}).get("status") == "removed_local"
                   or project.get("current_state_status") == "removed_local")
        latest, previous = attempts.get(project_id), successes.get(project_id)
        if removed:
            previous = None
            if latest and latest["disposition"] != "removed_local":
                latest = None
        drift = bool(latest and latest["authority"] != authority)
        age = _age(latest["observed_at"], current) if latest else None
        source_age = (_age(latest["sources"][0]["modified_at"], current)
                      if latest and latest["sources"] else None)
        if removed:
            state, reason = "removed_local", "已从本地移除；不扫描、不写入、不重试。"
        elif not latest:
            state, reason = "unknown", "Not refreshed under this Hub ledger."
        elif drift:
            state, reason = "stale", "Recorded registry/schema authority differs from current Hub authority."
        elif not latest["success"]:
            state, reason = ("stale" if previous else "unknown"), latest["errors"][0]["message"]
        elif age < 0 or age > latest["freshness"]["max_read_age_seconds"]:
            state, reason = "stale", "Read evidence expired or clock moved backwards; refresh required."
        else:
            state, reason = "fresh", None
        row = {"project_id": project_id, "name": project["name"],
               "local_presence": "removed_local" if removed else "registered_local",
               "latest_attempt": latest, "last_success": previous,
               "freshness": {"state": state, "reason": reason, "authority_drift": drift,
                             "read_age_seconds": age, "source_age_seconds": source_age}}
        row["update_key"] = row_update_key(row)
        projects.append(row)
    snapshot = {"schema_version": VERSION, "kind": "project_connections", "observed_at": observed_at,
                "authority": authority, "ledger_head": history["head"],
                "coverage": {"registry_count": len(projects),
                             "local_count": sum(r["local_presence"] != "removed_local" for r in projects),
                             "removed_local_count": sum(r["local_presence"] == "removed_local" for r in projects),
                             "resolved_count": sum(bool(r["latest_attempt"] and r["latest_attempt"]["success"]
                                                        and r["freshness"]["state"] == "fresh") for r in projects)},
                "projects": projects}
    return validate_snapshot(snapshot)


def validate_snapshot(snapshot: dict) -> dict:
    exact(snapshot, {"schema_version", "kind", "observed_at", "authority", "ledger_head", "coverage", "projects"}, "snapshot")
    require(type(snapshot) is dict and snapshot.get("schema_version") == VERSION
            and snapshot.get("kind") == "project_connections", "invalid snapshot schema")
    require(type(snapshot.get("projects")) is list, "snapshot project list required")
    timestamp(snapshot["observed_at"], "snapshot time")
    validate_authority(snapshot["authority"])
    if snapshot["ledger_head"] is not None:
        exact(snapshot["ledger_head"], {"sequence", "hash"}, "ledger head")
        typed(snapshot["ledger_head"]["sequence"], "integer")
        fingerprint(snapshot["ledger_head"]["hash"], "ledger head hash")
        require((snapshot["ledger_head"]["sequence"] == 0) == (snapshot["ledger_head"]["hash"] == GENESIS_HASH),
                "ledger genesis identity mismatch")
    current = datetime.fromisoformat(snapshot["observed_at"].replace("Z", "+00:00"))
    exact(snapshot["coverage"], {"registry_count", "local_count", "removed_local_count", "resolved_count"}, "coverage")
    for count in snapshot["coverage"].values():
        typed(count, "integer")
    ids = []
    visible_results = set()
    for row in snapshot["projects"]:
        exact(row, {"project_id", "name", "local_presence", "latest_attempt", "last_success", "freshness", "update_key"}, "project view")
        exact(row["freshness"], {"state", "reason", "authority_drift", "read_age_seconds", "source_age_seconds"}, "project freshness")
        identifier(row["project_id"], "projection project")
        string(row["name"], "projection project name")
        require(type(row["freshness"]["state"]) is str and row["freshness"]["state"] in {"fresh", "stale", "unknown", "removed_local"},
                "invalid project freshness state")
        require(type(row["freshness"]["authority_drift"]) is bool, "authority drift must be boolean")
        if row["freshness"]["state"] == "fresh":
            require(row["freshness"]["reason"] is None, "fresh projection cannot carry a failure reason")
        else:
            string(row["freshness"]["reason"], "projection freshness reason")
        for key in ("read_age_seconds", "source_age_seconds"):
            age_value = row["freshness"][key]
            require(age_value is None or (type(age_value) in {int, float} and math.isfinite(age_value)), "invalid freshness age")
        ids.append(row["project_id"])
        for key in ("latest_attempt", "last_success"):
            if row[key] is not None:
                validate_result(row[key])
                visible_results.add(content_hash(row[key]))
                require(row[key]["project_id"] == row["project_id"], "project/result identity mismatch")
                require(snapshot["ledger_head"] is not None and snapshot["ledger_head"]["sequence"] >= 2,
                        "stored result requires a populated ledger head")
        require(row["last_success"] is None or row["last_success"]["success"], "last success must be successful")
        require(row["local_presence"] != "removed_local" or row["last_success"] is None, "removed record exposes business history")
        require(type(row["local_presence"]) is str and row["local_presence"] in {"removed_local", "registered_local"}, "invalid local presence")
        latest = row["latest_attempt"]
        if latest is None:
            require(row["last_success"] is None, "last success requires a latest attempt")
        elif latest["success"]:
            require(row["last_success"] == latest, "successful latest attempt must be the last success")
        age = _age(latest["observed_at"], current) if latest else None
        source_age = _age(latest["sources"][0]["modified_at"], current) if latest and latest["sources"] else None
        drift = bool(latest and latest["authority"] != snapshot["authority"])
        require(row["freshness"]["read_age_seconds"] == age and row["freshness"]["source_age_seconds"] == source_age
                and row["freshness"]["authority_drift"] is drift, "freshness evidence mismatch")
        if row["local_presence"] == "removed_local":
            expected = "removed_local"
            require(latest is None or latest["disposition"] == "removed_local", "removed view contains live facts")
        elif not latest:
            expected = "unknown"
        elif drift or (latest["success"] and (age < 0 or age > latest["freshness"]["max_read_age_seconds"])):
            expected = "stale"
        elif latest["success"]:
            expected = "fresh"
        else:
            expected = "stale" if row["last_success"] else "unknown"
        require(row["freshness"]["state"] == expected, "snapshot must not promote failed or expired evidence")
        require(row["update_key"] == row_update_key(row), "projection update identity mismatch")
    require(len(ids) == len(set(ids)) == snapshot["coverage"]["registry_count"], "snapshot coverage mismatch")
    if visible_results:
        require(snapshot["ledger_head"]["sequence"] >= 1 + len(visible_results),
                "ledger head cannot precede its visible result events and request begin")
    require(snapshot["coverage"]["local_count"] == sum(r["local_presence"] != "removed_local" for r in snapshot["projects"])
            and snapshot["coverage"]["removed_local_count"] == sum(r["local_presence"] == "removed_local" for r in snapshot["projects"]),
            "presence coverage mismatch")
    require(snapshot["coverage"]["resolved_count"] == sum(r["freshness"]["state"] == "fresh" for r in snapshot["projects"]),
            "resolved coverage mismatch")
    return snapshot


class ProjectService:
    """Compatibility facade over the Hub 2 registry, resolver and ledger."""

    def __init__(self, root: Path | str, *, ledger_path: Path | str | None = None,
                 relations_path: Path | str | None = None) -> None:
        self.root = Path(root).absolute()
        self.ledger_path = ledger_path
        self.relations_path = relations_path

    def _resolver(self) -> SourceResolver:
        try:
            return SourceResolver(self.root)
        except (RecordError, OSError, TypeError, ValueError) as exc:
            raise _error("PROJECT_REGISTRY_UNAVAILABLE", status=503) from exc

    def _ledger_candidate(self) -> Path:
        raw = (Path("data/connections/connection_refresh.sqlite3")
               if self.ledger_path is None else Path(self.ledger_path))
        if not raw.parts or ".." in raw.parts:
            raise _error("LEDGER_UNAVAILABLE", status=503)
        candidate = raw if raw.is_absolute() else self.root / raw
        try:
            relative = candidate.relative_to(self.root)
        except ValueError as exc:
            raise _error("LEDGER_UNAVAILABLE", status=503) from exc
        allowed = (("data", "connections"),
                   ("docs", "reports", "all-projects-governance"))
        if (not relative.parts or not any(relative.parts[:len(prefix)] == prefix
                                          and len(relative.parts) > len(prefix)
                                          for prefix in allowed)):
            raise _error("LEDGER_UNAVAILABLE", status=503)
        return candidate

    def _missing_ledger_is_safe(self, candidate: Path) -> bool:
        current = self.root
        if current.is_symlink() or not current.is_dir():
            return False
        for part in candidate.relative_to(self.root).parts[:-1]:
            current = current / part
            if current.is_symlink():
                return False
            if current.exists() and not current.is_dir():
                return False
            if not current.exists():
                break
        return not candidate.exists() and not candidate.is_symlink()

    def _read_ledger(self) -> RefreshLedger | None:
        candidate = self._ledger_candidate()
        try:
            return RefreshLedger(self.root, self.ledger_path,
                                 result_validator=validate_result, read_only=True)
        except LedgerPathError as exc:
            if self._missing_ledger_is_safe(candidate):
                return None
            raise _error("LEDGER_UNAVAILABLE", status=503) from exc
        except (LedgerSchemaError, LedgerCorruptionError, ResultValidationError) as exc:
            raise _error("LEDGER_CORRUPT", status=503) from exc
        except LedgerBusyError as exc:
            raise _error("LEDGER_BUSY", status=503, retryable=True) from exc
        except (RefreshLedgerError, OSError) as exc:
            raise _error("LEDGER_UNAVAILABLE", status=503, retryable=True) from exc

    @staticmethod
    def _unknown_relation(project_id: str, reason: str = "RELATION_STORE_UNAVAILABLE") -> dict:
        return {"project_id": project_id, "status": "unknown", "reason": reason,
                "relations": [], "program_link_authority": False,
                "design_selection_authority": False}

    def _read_local_json(self, path: Path) -> Any:
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("relation store escapes Hub root") from exc
        def protected(part: str) -> bool:
            lowered = part.casefold()
            tokens = set(re.split(r"[._-]+", lowered))
            canonical = re.sub(r"[._-]+", "", lowered)
            return (lowered in FORBIDDEN_PARTS or lowered.startswith(".env")
                    or any(word in lowered
                           for word in ("secret", "credential", "cookie", "private_key"))
                    or bool(tokens & {"auth", "token", "tokens", "password", "passwords",
                                      "apikey"})
                    or any(word in canonical
                           for word in ("serviceaccount", "privatekey", "clientsecret",
                                        "clientcredential")))
        if (not relative.parts or ".." in relative.parts or path.suffix.casefold() != ".json"
                or any(protected(part) for part in relative.parts)):
            raise ValueError("unsafe relation store")
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError("unsafe Hub root")
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened = [descriptor]
        file_descriptor = None
        try:
            for part in relative.parts[:-1]:
                descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                     dir_fd=descriptor)
                opened.append(descriptor)
            file_descriptor = os.open(relative.parts[-1],
                                      os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                      dir_fd=descriptor)
            before = os.fstat(file_descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_size > MAX_RELATIONS_BYTES):
                raise ValueError("unsafe relation store")
            with os.fdopen(file_descriptor, "rb", closefd=True) as stream:
                file_descriptor = None
                raw = stream.read(MAX_RELATIONS_BYTES + 1)
                after = os.fstat(stream.fileno())
            if (len(raw) > MAX_RELATIONS_BYTES
                    or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
                raise ValueError("relation store changed during read")
            return json.loads(raw)
        finally:
            if file_descriptor is not None:
                os.close(file_descriptor)
            for item in reversed(opened):
                os.close(item)

    def _relations(self, project_ids: set[str]) -> dict[str, dict]:
        if self.relations_path is None:
            return {pid: self._unknown_relation(pid) for pid in project_ids}
        try:
            candidate = Path(self.relations_path)
            if not candidate.parts or ".." in candidate.parts:
                raise ValueError("unsafe relation store")
            candidate = candidate if candidate.is_absolute() else self.root / candidate
            loaded = self._read_local_json(candidate)
            if type(loaded) is not dict:
                raise ValueError("invalid relation store")
            rows = loaded.get("projects", loaded.get("relations"))
            if type(rows) is not list:
                raise ValueError("invalid relation store")
            indexed: dict[str, dict] = {}
            for row in rows:
                if type(row) is not dict or type(row.get("project_id")) is not str:
                    raise ValueError("invalid relation row")
                pid = row["project_id"]
                if pid in indexed:
                    raise ValueError("duplicate relation row")
                indexed[pid] = copy.deepcopy(row)
            return {pid: indexed.get(pid, self._unknown_relation(pid, "RELATION_NOT_RECORDED"))
                    for pid in project_ids}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {pid: self._unknown_relation(pid) for pid in project_ids}

    @staticmethod
    def _scoped_to_project(value: Any, project_id: str) -> bool:
        record = value.get("event", value) if isinstance(value, dict) else {}
        if record.get("project_id") == project_id:
            return True
        scope = record.get("scope")
        return isinstance(scope, dict) and any(
            isinstance(member, dict) and member.get("project_id") == project_id
            for member in scope.get("members", []))

    @staticmethod
    def _design_for(project_id: str, snapshot: dict | None) -> dict:
        if snapshot is None:
            return {"available": False, "store_revision": None,
                    "store_classification": None, "references": [], "history": [],
                    "effective": {}, "queues": {}, "reason": "DESIGN_STORE_UNAVAILABLE"}
        expected = {"available", "store_revision", "store_classification", "facts", "history",
                    "effective", "queues", "reason"}
        if (not isinstance(snapshot, dict) or set(snapshot) != expected
                or type(snapshot["available"]) is not bool):
            raise _error("DESIGN_SNAPSHOT_INVALID", status=503)
        if not snapshot["available"]:
            return {"available": False, "store_revision": snapshot["store_revision"],
                    "store_classification": snapshot["store_classification"],
                    "references": [], "history": [], "effective": {}, "queues": {},
                    "reason": snapshot["reason"] or "DESIGN_STORE_UNAVAILABLE"}
        if (not isinstance(snapshot["facts"], list)
                or not isinstance(snapshot["history"], list)
                or not isinstance(snapshot["effective"], dict)
                or not isinstance(snapshot["queues"], dict)
                or any(not isinstance(values, list) for values in snapshot["queues"].values())):
            raise _error("DESIGN_SNAPSHOT_INVALID", status=503)
        references = []
        for fact in snapshot["facts"]:
            if not isinstance(fact, dict):
                raise _error("DESIGN_SNAPSHOT_INVALID", status=503)
            applies = fact.get("project_id") == project_id
            scope = fact.get("scope")
            if isinstance(scope, dict):
                applies = applies or any(isinstance(member, dict)
                                         and member.get("project_id") == project_id
                                         for member in scope.get("members", []))
            if applies:
                references.append(copy.deepcopy(fact))
        references.sort(key=lambda row: (str(row.get("kind", "")), str(row.get("id", "")),
                                         int(row.get("revision", 0) or 0)))
        history = [copy.deepcopy(item) for item in snapshot["history"]
                   if ProjectService._scoped_to_project(item, project_id)]
        effective = {key: copy.deepcopy(item) for key, item in snapshot["effective"].items()
                     if ProjectService._scoped_to_project(item, project_id)}
        queues = {key: [copy.deepcopy(item) for item in values
                        if ProjectService._scoped_to_project(item, project_id)]
                  for key, values in snapshot["queues"].items()}
        return {"available": True, "store_revision": snapshot["store_revision"],
                "store_classification": snapshot["store_classification"],
                "references": references, "history": history, "effective": effective,
                "queues": queues, "reason": None}

    @staticmethod
    def _legacy_business(source_record: dict | None, freshness: dict) -> dict:
        current = (source_record if source_record and source_record["success"]
                   and freshness["state"] == "fresh" else None)
        fields = copy.deepcopy(current["business"] if current else empty_business())
        work = fields["current_work"]
        if current:
            unknown_fields = copy.deepcopy(current["unknown_fields"])
        else:
            recorded_unknowns = source_record["unknown_fields"] if source_record else {}
            fallback = freshness["reason"] or "Current project business fact is unavailable."
            unknown_fields = {field: recorded_unknowns.get(field, fallback) for field in FIELDS}
        return {"state": "current" if current else "unknown",
                "raw_status": work["status"],
                "normalized_status": work["status"] or "unknown",
                "next_action": work["next_action"], "next_action_kind": "unknown",
                "blockers": copy.deepcopy(fields["blockers"]),
                "unknown_fields": unknown_fields,
                "reason": freshness["reason"], "fields": fields,
                "source_record": copy.deepcopy(source_record)}

    def _dto(self, declared: dict, snapshot: dict, row: dict, relation: dict,
             design_snapshot: dict | None) -> dict:
        latest, previous = row["latest_attempt"], row["last_success"]
        removed = row["local_presence"] == "removed_local"
        connection_read_allowed = (
            not removed
            and declared.get("connection_read_allowed", declared.get("enabled")) is True
            and declared.get("access_profile") != "no_current_goal_access"
        )
        declared_view = {key: copy.deepcopy(declared.get(key)) for key in (
            "enabled", "summary_enabled", "project_type", "priority_source",
            "current_state_status", "access_profile")}
        declared_view["connection_read_allowed"] = connection_read_allowed
        declared_view["hub_connection_exception"] = copy.deepcopy(
            declared.get("hub_connection_exception"))
        declared_view["legacy_hub_connection_exception"] = copy.deepcopy(
            declared.get("legacy_hub_connection_exception"))
        source_record = copy.deepcopy(latest)
        source = {
            "entrypoints": (copy.deepcopy(declared.get("current_state_paths", []))
                            if declared.get("summary_enabled") and
                            row["local_presence"] != "removed_local" else []),
            "availability": latest["disposition"] if latest else row["freshness"]["state"],
            "role": (latest["sources"][0]["role"] if latest and latest["sources"] else None),
            "observed_at": latest["observed_at"] if latest else None,
            "last_success_at": previous["observed_at"] if previous else None,
            "error": latest["errors"][0]["message"] if latest and latest["errors"] else None,
            "sources": copy.deepcopy(latest["sources"] if latest else []),
            "latest_observation": ({"observed_at": latest["observed_at"],
                                    "disposition": latest["disposition"],
                                    "success": latest["success"],
                                    "sources": copy.deepcopy(latest["sources"]),
                                    "errors": copy.deepcopy(latest["errors"])}
                                   if latest else None),
            "source_record": source_record,
        }
        return {"schema_version": "1.0", "project_id": row["project_id"],
                "name": row["name"], "declared": declared_view,
                "business": self._legacy_business(latest, row["freshness"]),
                "operational": {"facts": [], "latest_attempt": source_record,
                                "last_success": copy.deepcopy(previous)},
                "freshness": {"state": row["freshness"]["state"],
                              "stale_reason": row["freshness"]["reason"],
                              "authority_drift": row["freshness"]["authority_drift"],
                              "local_presence": row["local_presence"]},
                "source": source, "errors": copy.deepcopy(latest["errors"] if latest else []),
                "relations": copy.deepcopy(relation),
                "design": self._design_for(row["project_id"], design_snapshot),
                "provenance": {"registry_sha256": snapshot["authority"]["registry_hash"],
                               "schema_sha256": snapshot["authority"]["schema_hash"],
                               "adapter_version": snapshot["authority"]["adapter_version"],
                               "ledger_head": copy.deepcopy(snapshot["ledger_head"]),
                               "latest_result_hash": content_hash(latest) if latest else None,
                               "last_success_result_hash": content_hash(previous) if previous else None,
                               "latest_authority": copy.deepcopy(latest["authority"] if latest else None),
                               "business_authority": copy.deepcopy(latest["authority"]
                                                                  if latest and latest["success"] else None)}}

    @staticmethod
    def _query(query: dict[str, str] | None) -> dict[str, Any]:
        query = {} if query is None else query
        if (not isinstance(query, dict) or set(query) - QUERY_FIELDS
                or any(not isinstance(key, str) or not isinstance(value, str)
                       for key, value in query.items())):
            raise _error("QUERY_INVALID")
        q = query.get("q", "").strip().casefold()
        if len(q) > MAX_QUERY:
            raise _error("QUERY_INVALID")
        status, freshness = query.get("status"), query.get("freshness")
        if status is not None and status not in {"unknown", "active", "paused", "blocked", "complete"}:
            raise _error("QUERY_INVALID")
        if freshness is not None and freshness not in {"unknown", "fresh", "stale", "removed_local"}:
            raise _error("QUERY_INVALID")
        raw_order = query.get("order", "project_id")
        descending = raw_order.startswith("-")
        order = raw_order[1:] if descending else raw_order
        if order not in ORDER_FIELDS:
            raise _error("QUERY_INVALID")
        try:
            offset = int(query.get("offset", "0"))
            limit = int(query.get("limit", str(MAX_PAGE)))
        except ValueError as exc:
            raise _error("QUERY_INVALID") from exc
        if (offset < 0 or not 1 <= limit <= MAX_PAGE
                or str(offset) != query.get("offset", str(offset))
                or str(limit) != query.get("limit", str(limit))):
            raise _error("QUERY_INVALID")
        return {"q": q, "status": status, "freshness": freshness, "order": order,
                "descending": descending, "offset": offset, "limit": limit}

    def _all(self, design_snapshot: dict | None) -> tuple[list[dict], dict]:
        resolver = self._resolver()
        ledger = self._read_ledger()
        try:
            snapshot = project_snapshot(resolver.registry, resolver.authority, ledger)
        except (RecordError, LedgerSchemaError, LedgerCorruptionError,
                ResultValidationError) as exc:
            raise _error("LEDGER_CORRUPT", status=503) from exc
        except LedgerBusyError as exc:
            raise _error("LEDGER_BUSY", status=503, retryable=True) from exc
        project_ids = {row["id"] for row in resolver.registry["projects"]}
        relations = self._relations(project_ids)
        declared = {row["id"]: row for row in resolver.registry["projects"]}
        rows = [self._dto(declared[row["project_id"]], snapshot, row,
                          relations[row["project_id"]], design_snapshot)
                for row in snapshot["projects"]]
        return rows, snapshot

    def list_projects(self, query: dict[str, str] | None = None,
                      design_snapshot: dict | None = None) -> dict:
        parsed = self._query(query)
        rows, snapshot = self._all(design_snapshot)
        if parsed["q"]:
            rows = [row for row in rows if parsed["q"] in row["project_id"].casefold()
                    or parsed["q"] in row["name"].casefold()]
        if parsed["status"]:
            rows = [row for row in rows
                    if row["business"]["normalized_status"] == parsed["status"]]
        if parsed["freshness"]:
            rows = [row for row in rows if row["freshness"]["state"] == parsed["freshness"]]
        field = parsed["order"]

        def key(row: dict) -> tuple[str, str]:
            value = (row["project_id"] if field == "project_id" else row["name"]
                     if field == "name" else row["business"]["normalized_status"]
                     if field == "status" else row["freshness"]["state"])
            return str(value).casefold(), row["project_id"]

        rows.sort(key=key, reverse=parsed["descending"])
        total = len(rows)
        rows = rows[parsed["offset"]:parsed["offset"] + parsed["limit"]]
        return {"schema_version": "1.0", "head": copy.deepcopy(snapshot["ledger_head"]),
                "total": total, "offset": parsed["offset"], "limit": parsed["limit"],
                "projects": rows}

    def get_project(self, project_id: str, design_snapshot: dict | None = None) -> dict:
        if not isinstance(project_id, str) or not SERVICE_ID.fullmatch(project_id):
            raise _error("PROJECT_ID_INVALID")
        rows, _ = self._all(design_snapshot)
        for row in rows:
            if row["project_id"] == project_id:
                return row
        raise _error("PROJECT_NOT_FOUND", status=404, details={"project_id": project_id})

    def _refresh_receipt(self, request_id: str) -> tuple[str, dict[str, Any]]:
        try:
            ledger = RefreshLedger(self.root, self.ledger_path,
                                   result_validator=validate_result, read_only=True)
            history = ledger.history(request_id)
            request = history["requests"][0]
            return "PARTIALLY_COMMITTED", {
                "request_id": request_id, "head": history["head"],
                "completed_project_ids": request["completed_project_ids"],
                "remaining_project_ids": request["remaining_project_ids"]}
        except Exception:
            return "UNKNOWN", {"request_id": request_id}

    def refresh(self, command: dict[str, Any]) -> dict:
        if not isinstance(command, dict) or set(command) != REFRESH_FIELDS:
            raise _error("REFRESH_COMMAND_INVALID")
        request_id = command["request_id"]
        project_ids = command["project_ids"]
        expected = command["expected_head"]
        if (not isinstance(request_id, str) or not SERVICE_ID.fullmatch(request_id)
                or not isinstance(project_ids, list) or not project_ids
                or any(not isinstance(pid, str) or not SERVICE_ID.fullmatch(pid)
                       for pid in project_ids)
                or len(project_ids) != len(set(project_ids))
                or not isinstance(expected, dict) or set(expected) != HEAD_FIELDS
                or type(expected["sequence"]) is not int or expected["sequence"] < 0
                or not isinstance(expected["hash"], str)
                or not SHA256.fullmatch(expected["hash"])):
            raise _error("REFRESH_COMMAND_INVALID")
        resolver = self._resolver()
        unknown = sorted(set(project_ids) - set(resolver.projects))
        if unknown:
            raise _error("PROJECT_NOT_FOUND", status=404, details={"project_ids": unknown})
        coordinator_started = False
        try:
            ledger = RefreshLedger(self.root, self.ledger_path,
                                   result_validator=validate_result)
            coordinator_started = True
            outcome = refresh_projects(ledger, resolver, request_id, project_ids,
                                       expected_head=expected)
            try:
                current = SourceResolver(self.root)
                state = "matched" if current.authority == resolver.authority else "drifted"
            except (RecordError, OSError, TypeError, ValueError):
                current, state = None, "unavailable"
            outcome["current_authority"] = {"state": state,
                                            "authority": copy.deepcopy(
                                                current.authority if current else None)}
            if state != "matched":
                outcome["projection"]["authority_drift"] = True
                for project in outcome["projection"]["projects"].values():
                    project["authority_drift"] = True
                    if project["freshness"] == "fresh":
                        project["freshness"] = "stale"
                        project["stale_reason"] = "Current Hub source authority is unavailable or drifted."
            return outcome
        except HeadConflictError as exc:
            raise _error("REFRESH_HEAD_CONFLICT", status=409, retryable=True) from exc
        except RequestConflictError as exc:
            raise _error("REFRESH_REQUEST_CONFLICT", status=409) from exc
        except PrecommitFaultError as exc:
            outcome, details = self._refresh_receipt(request_id)
            raise _error("REFRESH_PRECOMMIT_FAILED", status=503, retryable=True,
                         outcome=outcome, details=details) from exc
        except (LedgerSchemaError, LedgerCorruptionError, ResultValidationError) as exc:
            commit, details = (self._refresh_receipt(request_id)
                               if coordinator_started else ("NOT_COMMITTED", None))
            raise _error("REFRESH_STORAGE_CORRUPT", status=503,
                         outcome=commit, details=details) from exc
        except LedgerBusyError as exc:
            commit, details = (self._refresh_receipt(request_id)
                               if coordinator_started else ("NOT_COMMITTED", None))
            raise _error("REFRESH_CONCURRENCY_BUSY", status=503, retryable=True,
                         outcome=commit, details=details) from exc
        except (LedgerPathError, RecordError, OSError, TypeError, ValueError) as exc:
            commit, details = (self._refresh_receipt(request_id)
                               if coordinator_started else ("NOT_COMMITTED", None))
            raise _error("REFRESH_VALIDATION_FAILED", outcome=commit, details=details) from exc
        except RefreshLedgerError as exc:
            commit, details = (self._refresh_receipt(request_id)
                               if coordinator_started else ("NOT_COMMITTED", None))
            raise _error("REFRESH_FAILED", status=503, retryable=True,
                         outcome=commit, details=details) from exc
