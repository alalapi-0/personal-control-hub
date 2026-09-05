"""Transport-independent project query and bounded refresh facade."""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Mapping

from .connection_manager_cli import (
    DEFAULT_BUNDLE, DEFAULT_RELATIONS, ADAPTER_PATH, current_authority_status,
    load_bundles, load_json, load_relations, result_validator,
)
from .connection_records import RecordError
from .connection_refresh import (
    HeadConflictError, LedgerBusyError, LedgerCorruptionError, LedgerPathError,
    LedgerSchemaError, PrecommitFaultError, RefreshLedger, RefreshLedgerError,
    RequestConflictError, ResultValidationError, refresh as refresh_projects,
)
from .connection_sources import SourceResolver
from .connections import load_registry_at
from .service_contract import ServiceError


QUERY_FIELDS = {"q", "status", "freshness", "order", "offset", "limit"}
ORDER_FIELDS = {"project_id", "name", "status", "freshness"}
REFRESH_FIELDS = {"request_id", "project_ids", "expected_head"}
HEAD_FIELDS = {"sequence", "hash"}
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_QUERY = 240
MAX_PAGE = 100


def _error(code: str, *, status: int = 400, retryable: bool = False,
           outcome: str = "NOT_COMMITTED",
           details: dict[str, Any] | None = None) -> ServiceError:
    return ServiceError(code, status=status, retryable=retryable, outcome=outcome,
                        details=details)


class ProjectService:
    """Joins accepted Hub-local facts without implicitly refreshing sources."""

    def __init__(self, root: Path | str, *, bundle_paths: list[str] | None = None,
                 ledger_path: Path | str | None = None,
                 relations_path: str | None = None) -> None:
        self.root = Path(root).absolute()
        self.bundle_paths = list(bundle_paths) if bundle_paths is not None else [DEFAULT_BUNDLE]
        self.ledger_path = ledger_path
        self.relations_path = relations_path or DEFAULT_RELATIONS

    def _authorities(self) -> tuple[list[dict], dict[str, SourceResolver]]:
        try:
            return load_bundles(self.root, self.bundle_paths)
        except (RecordError, OSError, ValueError) as exc:
            raise _error("SOURCE_AUTHORITY_CORRUPT", status=503) from exc

    def _registry(self) -> tuple[list[dict], str]:
        try:
            registry, digest = load_registry_at(self.root)
            return registry["projects"], digest
        except (RecordError, OSError, ValueError) as exc:
            raise _error("PROJECT_REGISTRY_UNAVAILABLE", status=503) from exc

    def _relations(self, project_ids: set[str], *, allow_unavailable: bool) -> dict[str, dict]:
        try:
            loaded = load_relations(self.root, self.relations_path)
            return {row["project_id"]: row for row in loaded["projects"]}
        except (RecordError, OSError, ValueError) as exc:
            if allow_unavailable:
                return {project_id: {
                    "project_id": project_id, "status": "unknown",
                    "reason": "RELATION_STORE_UNAVAILABLE", "relations": [],
                    "program_link_authority": False, "design_selection_authority": False,
                } for project_id in project_ids}
            raise _error("RELATION_STORE_CORRUPT", status=503) from exc

    def _ledger_snapshot(self, validators: dict[str, SourceResolver],
                         current_authority: Mapping[str, Any],
                         authority_state: str) -> tuple[dict, dict]:
        validate = result_validator(validators)
        try:
            ledger = RefreshLedger(self.root, self.ledger_path, result_validator=validate,
                                   read_only=True)
        except LedgerPathError as exc:
            # An absent ledger is a valid initial state; unsafe existing paths are not.
            candidate = (self.root / "data/design_governance/connection_refresh.sqlite3"
                         if self.ledger_path is None else Path(self.ledger_path))
            if not candidate.is_absolute():
                candidate = self.root / candidate
            if candidate.absolute().exists() or candidate.absolute().is_symlink():
                raise _error("LEDGER_UNAVAILABLE", status=503) from exc
            return ({"schema_version": "1.0", "head": None, "requests": [],
                     "events": [], "results": []},
                    {"schema_version": "1.0", "head": None,
                     "authority_drift": False, "projects": {}})
        except (LedgerSchemaError, LedgerCorruptionError) as exc:
            raise _error("LEDGER_CORRUPT", status=503) from exc
        except RefreshLedgerError as exc:
            raise _error("LEDGER_UNAVAILABLE", status=503, retryable=True) from exc
        for _ in range(3):
            try:
                history = ledger.history(current_authority=current_authority)
                projection = ledger.rebuild(current_authority=current_authority)
            except (LedgerSchemaError, LedgerCorruptionError, ResultValidationError) as exc:
                raise _error("LEDGER_CORRUPT", status=503) from exc
            except LedgerBusyError as exc:
                raise _error("LEDGER_BUSY", status=503, retryable=True) from exc
            if history["head"] == projection["head"]:
                if authority_state != "matched":
                    for project in projection["projects"].values():
                        project["authority_drift"] = True
                        if project["freshness"] == "fresh":
                            project["freshness"] = "stale"
                            project["stale_reason"] = "Current Hub source authority is unavailable or drifted."
                return history, projection
        raise _error("LEDGER_SNAPSHOT_CONFLICT", status=409, retryable=True)

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
        if not isinstance(snapshot, dict) or set(snapshot) != {
                "available", "store_revision", "store_classification", "facts", "history",
                "effective", "queues", "reason"} or type(snapshot["available"]) is not bool:
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
                members = scope.get("members", [])
                applies = applies or any(isinstance(member, dict) and
                                         member.get("project_id") == project_id
                                         for member in members)
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
    def _result_index(history: dict) -> dict[tuple[str, str], dict]:
        return {(row["request_id"], row["project_id"]): row["result"]
                for row in history["results"]}

    def _dto(self, declared: dict, registry_hash: str, relation: dict,
             history: dict, projection: dict, design_snapshot: dict | None) -> dict:
        project_id = declared["id"]
        operational = projection["projects"].get(project_id, {
            "latest_attempt": None, "last_success": None, "freshness": "unknown",
            "stale_reason": "No refresh result has been recorded.", "authority_drift": False,
        })
        indexed = self._result_index(history)
        latest_ref, success_ref = operational["latest_attempt"], operational["last_success"]
        latest = indexed.get((latest_ref["request_id"], project_id)) if latest_ref else None
        successful = indexed.get((success_ref["request_id"], project_id)) if success_ref else None
        business_result = next((row["result"] for row in reversed(history["results"])
                                if row["project_id"] == project_id
                                and isinstance(row["result"].get("business_snapshot"), dict)
                                and isinstance(row["result"]["business_snapshot"].get("snapshot"), dict)),
                               None)
        business = (business_result or successful or latest or {}).get("business_snapshot", {
            "state": "unknown", "snapshot": None,
            "reason": "No project source result has been recorded.",
        })
        snapshot = business.get("snapshot") if isinstance(business, dict) else None
        if not isinstance(snapshot, dict):
            snapshot = {}
        latest_facts = copy.deepcopy(latest.get("operational_facts", [])) if latest else []
        latest_errors = copy.deepcopy(latest.get("errors", [])) if latest else []
        source_result = business_result or successful
        source_snapshot = ((source_result or {}).get("business_snapshot") or {}).get("snapshot")
        if not isinstance(source_snapshot, dict):
            source_snapshot = {}
        source = {
            "availability": source_snapshot.get("availability", "unknown"),
            "role": source_snapshot.get("source_role"),
            "observed_at": (source_result or {}).get("observed_at"),
            "last_success_at": source_snapshot.get("last_success_at"),
            "error": source_snapshot.get("refresh_error"),
            "sources": copy.deepcopy((source_result or {}).get("sources", [])),
            "latest_observation": ({"observed_at": latest.get("observed_at"),
                                    "disposition": latest.get("disposition"),
                                    "success": latest.get("success"),
                                    "sources": copy.deepcopy(latest.get("sources", [])),
                                    "errors": copy.deepcopy(latest.get("errors", []))}
                                   if latest else None),
        }
        business_view = {
            "state": business.get("state", "unknown") if isinstance(business, dict) else "unknown",
            "raw_status": snapshot.get("raw_status"),
            "normalized_status": snapshot.get("normalized_status", "unknown"),
            "next_action": snapshot.get("next_action"),
            "next_action_kind": snapshot.get("next_action_kind", "unknown"),
            "blockers": copy.deepcopy(snapshot.get("blockers")),
            "unknown_fields": copy.deepcopy(snapshot.get("unknown_fields", {})),
            "reason": business.get("reason") if isinstance(business, dict) else None,
        }
        return {
            "schema_version": "1.0", "project_id": project_id, "name": declared["name"],
            "declared": {key: copy.deepcopy(declared.get(key)) for key in (
                "enabled", "summary_enabled", "project_type", "priority_source",
                "current_state_status", "access_profile")},
            "business": business_view,
            "operational": {"facts": latest_facts, "latest_attempt": copy.deepcopy(latest_ref),
                            "last_success": copy.deepcopy(success_ref)},
            "freshness": {"state": operational["freshness"],
                          "stale_reason": operational["stale_reason"],
                          "authority_drift": operational["authority_drift"]},
            "source": source, "errors": latest_errors,
            "relations": copy.deepcopy(relation),
            "design": self._design_for(project_id, design_snapshot),
            "provenance": {"registry_sha256": registry_hash,
                           "ledger_head": copy.deepcopy(history["head"]),
                           "latest_result_hash": latest_ref["result_hash"] if latest_ref else None,
                           "last_success_result_hash": success_ref["result_hash"] if success_ref else None,
                           "business_result_hash": business_result.get("result_hash") if business_result else None,
                           "latest_authority": copy.deepcopy((latest or {}).get("authority")),
                           "business_authority": copy.deepcopy((source_result or {}).get("authority"))},
        }

    def _refresh_receipt(self, request_id: str,
                         validators: dict[str, SourceResolver]) -> tuple[str, dict[str, Any]]:
        try:
            ledger = RefreshLedger(self.root, self.ledger_path,
                                   result_validator=result_validator(validators), read_only=True)
            snapshot = ledger.history(request_id)
            request = snapshot["requests"][0]
            return "PARTIALLY_COMMITTED", {
                "request_id": request_id, "head": snapshot["head"],
                "completed_project_ids": request["completed_project_ids"],
                "remaining_project_ids": request["remaining_project_ids"],
            }
        except Exception:
            return "UNKNOWN", {"request_id": request_id}

    @staticmethod
    def _query(query: dict[str, str] | None) -> dict[str, Any]:
        query = {} if query is None else query
        if not isinstance(query, dict) or set(query) - QUERY_FIELDS or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in query.items()):
            raise _error("QUERY_INVALID")
        q = query.get("q", "").strip().casefold()
        if len(q) > MAX_QUERY:
            raise _error("QUERY_INVALID")
        status, freshness = query.get("status"), query.get("freshness")
        if status is not None and status not in {"unknown", "active", "paused", "blocked", "complete"}:
            raise _error("QUERY_INVALID")
        if freshness is not None and freshness not in {"unknown", "fresh", "stale"}:
            raise _error("QUERY_INVALID")
        raw_order = query.get("order", "project_id")
        descending = raw_order.startswith("-")
        order = raw_order[1:] if descending else raw_order
        if order not in ORDER_FIELDS:
            raise _error("QUERY_INVALID")
        try:
            offset, limit = int(query.get("offset", "0")), int(query.get("limit", str(MAX_PAGE)))
        except ValueError as exc:
            raise _error("QUERY_INVALID") from exc
        if offset < 0 or not 1 <= limit <= MAX_PAGE or str(offset) != query.get("offset", str(offset)) or str(limit) != query.get("limit", str(limit)):
            raise _error("QUERY_INVALID")
        return {"q": q, "status": status, "freshness": freshness, "order": order,
                "descending": descending, "offset": offset, "limit": limit}

    def _all(self, design_snapshot: dict | None) -> list[dict]:
        projects, registry_hash = self._registry()
        bundles, validators = self._authorities()
        active = bundles[-1]
        authority = validators[active["source_plan"]["content_hash"]].authority
        authority_state = current_authority_status(self.root, active)["state"]
        history, projection = self._ledger_snapshot(validators, authority, authority_state)
        project_ids = {project["id"] for project in projects}
        relations = self._relations(project_ids, allow_unavailable=authority_state != "matched")
        return [self._dto(project, registry_hash, relations[project["id"]], history,
                          projection, design_snapshot) for project in projects]

    def list_projects(self, query: dict[str, str] | None = None,
                      design_snapshot: dict | None = None) -> dict:
        parsed = self._query(query)
        rows = self._all(design_snapshot)
        response_head = rows[0]["provenance"]["ledger_head"] if rows else None
        if parsed["q"]:
            rows = [row for row in rows if parsed["q"] in row["project_id"].casefold()
                    or parsed["q"] in row["name"].casefold()]
        if parsed["status"]:
            rows = [row for row in rows if row["business"]["normalized_status"] == parsed["status"]]
        if parsed["freshness"]:
            rows = [row for row in rows if row["freshness"]["state"] == parsed["freshness"]]
        field = parsed["order"]
        def key(row: dict) -> tuple[str, str]:
            value = (row["project_id"] if field == "project_id" else row["name"] if field == "name"
                     else row["business"]["normalized_status"] if field == "status"
                     else row["freshness"]["state"])
            return str(value).casefold(), row["project_id"]
        rows.sort(key=key, reverse=parsed["descending"])
        total = len(rows)
        rows = rows[parsed["offset"]:parsed["offset"] + parsed["limit"]]
        return {"schema_version": "1.0", "head": response_head, "total": total,
                "offset": parsed["offset"], "limit": parsed["limit"], "projects": rows}

    def get_project(self, project_id: str, design_snapshot: dict | None = None) -> dict:
        if not isinstance(project_id, str) or not ID.fullmatch(project_id):
            raise _error("PROJECT_ID_INVALID")
        for row in self._all(design_snapshot):
            if row["project_id"] == project_id:
                return row
        raise _error("PROJECT_NOT_FOUND", status=404, details={"project_id": project_id})

    def refresh(self, command: dict[str, Any]) -> dict:
        if not isinstance(command, dict) or set(command) != REFRESH_FIELDS:
            raise _error("REFRESH_COMMAND_INVALID")
        request_id, project_ids, expected = (command["request_id"], command["project_ids"],
                                             command["expected_head"])
        if (not isinstance(request_id, str) or not ID.fullmatch(request_id)
                or not isinstance(project_ids, list) or not project_ids
                or any(not isinstance(pid, str) or not ID.fullmatch(pid) for pid in project_ids)
                or len(project_ids) != len(set(project_ids))
                or not isinstance(expected, dict) or set(expected) != HEAD_FIELDS
                or type(expected["sequence"]) is not int or expected["sequence"] < 0
                or not isinstance(expected["hash"], str) or not SHA256.fullmatch(expected["hash"])):
            raise _error("REFRESH_COMMAND_INVALID")
        projects, _ = self._registry()
        known = {project["id"] for project in projects}
        unknown = sorted(set(project_ids) - known)
        if unknown:
            raise _error("PROJECT_NOT_FOUND", status=404, details={"project_ids": unknown})
        bundles, validators = self._authorities()
        active = bundles[-1]
        if current_authority_status(self.root, active)["state"] != "matched":
            raise _error("REFRESH_AUTHORITY_UNAVAILABLE", status=409)
        coordinator_started = False
        try:
            adapters, _ = load_json(self.root, ADAPTER_PATH)
            resolver = SourceResolver(self.root, active["manifest"], adapters, active["source_plan"])
            ledger = RefreshLedger(self.root, self.ledger_path,
                                   result_validator=result_validator(validators))
            coordinator_started = True
            outcome = refresh_projects(ledger, resolver, request_id, project_ids,
                                       expected_head=expected)
            authority_status = current_authority_status(self.root, active)
            outcome["current_authority"] = authority_status
            if authority_status["state"] != "matched":
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
            outcome, details = self._refresh_receipt(request_id, validators)
            raise _error("REFRESH_PRECOMMIT_FAILED", status=503, retryable=True,
                         outcome=outcome, details=details) from exc
        except (LedgerSchemaError, LedgerCorruptionError, ResultValidationError) as exc:
            commit, details = (self._refresh_receipt(request_id, validators)
                               if coordinator_started else ("NOT_COMMITTED", None))
            raise _error("REFRESH_STORAGE_CORRUPT", status=503,
                         outcome=commit, details=details) from exc
        except LedgerBusyError as exc:
            commit, details = (self._refresh_receipt(request_id, validators)
                               if coordinator_started else ("NOT_COMMITTED", None))
            raise _error("REFRESH_CONCURRENCY_BUSY", status=503, retryable=True,
                         outcome=commit, details=details) from exc
        except (LedgerPathError, RecordError, OSError, ValueError) as exc:
            commit, details = (self._refresh_receipt(request_id, validators)
                               if coordinator_started else ("NOT_COMMITTED", None))
            raise _error("REFRESH_VALIDATION_FAILED", outcome=commit, details=details) from exc
        except RefreshLedgerError as exc:
            commit, details = (self._refresh_receipt(request_id, validators)
                               if coordinator_started else ("NOT_COMMITTED", None))
            raise _error("REFRESH_FAILED", status=503, retryable=True,
                         outcome=commit, details=details) from exc
