"""Crash-safe append-only storage and projections for design-governance facts."""
from __future__ import annotations

import copy
import errno
import fcntl
import hashlib
import json
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import yaml

from .design_records import (
    HASH_RE,
    ID_RE,
    SCHEMA_VERSION,
    DesignRecordError,
    canonical_bytes,
    content_hash,
    validate_decision_event,
    validate_fact,
)

STORE_VERSION = "1.0"
MAX_STORE_BYTES = 16 * 1024 * 1024


class StoreError(RuntimeError): pass
class ConflictError(StoreError): pass
class LockConflict(StoreError): pass


class CommittedDurabilityUnconfirmed(StoreError):
    status = "COMMITTED_DURABILITY_UNCONFIRMED"

    def __init__(self, *, revision: int, receipt: dict[str, Any], path: Path, store_sha256: str, cause: OSError):
        super().__init__(f"{self.status}: revision {revision} is published and verified, but directory fsync failed: {cause}")
        self.revision = revision
        self.receipt = copy.deepcopy(receipt)
        self.path = str(path)
        self.store_sha256 = store_sha256

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "revision": self.revision, "receipt": copy.deepcopy(self.receipt), "path": self.path, "store_sha256": self.store_sha256}


class CommittedVerificationFailed(StoreError):
    status = "COMMITTED_VERIFICATION_FAILED"

    def __init__(self, *, revision: int, receipt: dict[str, Any], path: Path, expected_store_sha256: str, observed_store_sha256: str | None, verification_error: str):
        super().__init__(f"{self.status}: revision {revision} was atomically published, but post-replace verification failed: {verification_error}")
        self.revision = revision
        self.receipt = copy.deepcopy(receipt)
        self.path = str(path)
        self.expected_store_sha256 = expected_store_sha256
        self.observed_store_sha256 = observed_store_sha256
        self.verification_error = verification_error

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "revision": self.revision, "receipt": copy.deepcopy(self.receipt), "path": self.path, "expected_store_sha256": self.expected_store_sha256, "observed_store_sha256": self.observed_store_sha256, "verification_error": self.verification_error}


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class DesignStore:
    """One JSON authority containing immutable facts, events, and request receipts."""

    def __init__(self, hub_root: str | Path, path: str | Path, *, fixture: bool = False, lock_timeout: float = 2.0, fixture_project_ids: set[str] | None = None):
        self.hub_root = Path(hub_root).resolve()
        raw = Path(path)
        if not raw.is_absolute(): raw = self.hub_root / raw
        self.path = raw.absolute()
        if ".." in self.path.parts:
            raise StoreError("path traversal is not allowed")
        # Canonicalize aliases above the selected root (macOS /var → /private/var),
        # while retaining every component inside it for the no-symlink check.
        for ancestor in reversed(self.path.parents):
            if ancestor.resolve(strict=False) == self.hub_root:
                self.path = self.hub_root / self.path.relative_to(ancestor)
                break
        self.fixture = fixture
        if fixture_project_ids is not None and (not fixture or not fixture_project_ids or any(not isinstance(value, str) or not ID_RE.fullmatch(value) or not value.startswith("fixture-") for value in fixture_project_ids)):
            raise StoreError("fixture_project_ids require a non-empty fixture-only stable-ID set")
        self.fixture_project_ids = frozenset(fixture_project_ids or {"fixture-project"})
        self.lock_timeout = lock_timeout
        self._assert_writable_path(self.path, fixture=fixture)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def _roots(self, fixture: bool | None = None) -> tuple[Path, ...]:
        fixture = self.fixture if fixture is None else fixture
        data_raw = self.hub_root / "data/design_governance"
        reports_raw = self.hub_root / "docs/reports/ui_design_governance"
        data, reports = data_raw.resolve(), reports_raw.resolve()
        if not _inside(data, self.hub_root) or not _inside(reports, self.hub_root):
            raise StoreError("Hub design-governance root escapes through symlink")
        return (reports,) if fixture else (data, reports)

    def _assert_writable_path(self, path: Path, *, fixture: bool | None = None) -> Path:
        if ".." in path.parts:
            raise StoreError("path traversal is not allowed")
        try:
            parts = path.relative_to(self.hub_root).parts
        except ValueError as exc:
            raise StoreError("path escapes Hub root") from exc
        current = self.hub_root
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise StoreError("design store paths must not contain symlinks")
        resolved = path.resolve(strict=False)
        if not any(_inside(resolved, root) for root in self._roots(fixture)):
            raise StoreError(f"path escapes Hub design-governance roots: {path}")
        return resolved

    def empty(self) -> dict[str, Any]:
        return {"schema_version": STORE_VERSION, "kind": "design_store", "store_classification": "synthetic_fixture" if self.fixture else "real", "revision": 0, "facts": [], "events": [], "requests": []}

    def initialize(self) -> dict[str, Any]:
        if self.path.exists(): return self.read()
        return self._mutate(0, "initialize", {"operation": "initialize", "store": self.empty()}, lambda store: None, allow_initialize=True)[0]

    def read(self) -> dict[str, Any]:
        if not self.path.exists(): raise StoreError(f"store does not exist: {self.path}")
        self._assert_writable_path(self.path)
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STORE_BYTES:
                    raise StoreError("store must be a bounded regular file")
                data = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc: raise StoreError(f"cannot read valid store: {exc}") from exc
        self.validate(data)
        return data

    def validate(self, store: Any) -> dict[str, Any]:
        if not isinstance(store, dict) or set(store) != {"schema_version", "kind", "store_classification", "revision", "facts", "events", "requests"}:
            raise DesignRecordError("design_store: fields mismatch")
        if store["schema_version"] != STORE_VERSION or store["kind"] != "design_store": raise DesignRecordError("design_store: unsupported schema or kind")
        expected_class = "synthetic_fixture" if self.fixture else "real"
        if store["store_classification"] != expected_class: raise DesignRecordError("design_store: classification does not match opened store")
        if not isinstance(store["revision"], int) or isinstance(store["revision"], bool) or store["revision"] < 0: raise DesignRecordError("design_store.revision: invalid")
        if not all(isinstance(store[k], list) for k in ("facts", "events", "requests")): raise DesignRecordError("design_store: collections must be lists")
        allowed_projects = self._project_ids()
        artifact_ids: set[str] = set(); artifacts: dict[str, dict[str, Any]] = {}; baselines: dict[tuple[str, int], dict[str, Any]] = {}; candidates: dict[tuple[str, int], dict[str, Any]] = {}; families: dict[tuple[str, int], dict[str, Any]] = {}; fact_ids: set[tuple[str, str, int | None]] = set()
        for fact in store["facts"]:
            validate_fact(fact)
            if fact["kind"] in {"artifact_ref", "baseline", "candidate", "design_family"}:
                classification = fact["classification"]
                if self.fixture and classification not in {"mock", "dry-run"}: raise DesignRecordError("fixture store facts must be mock or dry-run")
                if not self.fixture and classification in {"mock", "dry-run"}: raise DesignRecordError("real store cannot contain mock or dry-run design facts")
            scopes = [fact["scope"]] if fact["kind"] in {"artifact_ref", "candidate", "design_family"} else []
            if fact["kind"] == "baseline" and fact["project_id"] not in allowed_projects: raise DesignRecordError("baseline: project ID is not in canonical registry")
            for scope in scopes:
                unknown = {m["project_id"] for m in scope["members"]} - allowed_projects
                if unknown: raise DesignRecordError(f"scope: project IDs are not in canonical registry: {sorted(unknown)}")
            identity = (fact["kind"], fact["id"], fact.get("revision"))
            if identity in fact_ids: raise DesignRecordError(f"design_store: duplicate fact identity {identity}")
            fact_ids.add(identity)
            if fact["kind"] == "artifact_ref":
                artifact_ids.add(fact["id"]); artifacts[fact["id"]] = fact
            elif fact["kind"] == "baseline": baselines[(fact["id"], fact["revision"])] = fact
            elif fact["kind"] == "candidate": candidates[(fact["id"], fact["revision"])] = fact
            elif fact["kind"] == "design_family": families[(fact["id"], fact["revision"])] = fact
        for fact in store["facts"]:
            if fact["kind"] == "baseline":
                self._require_refs([b["artifact_id"] for b in fact["artifact_bindings"]], artifact_ids, "baseline artifact")
                for binding in fact["artifact_bindings"]:
                    artifact_id = binding["artifact_id"]
                    artifact = artifacts[artifact_id]
                    if artifact["sha256"] != binding["sha256"]:
                        raise DesignRecordError("baseline: artifact digest mismatch")
                    members = artifact["scope"]["members"]
                    if artifact["classification"] != fact["classification"] or artifact["scope"]["family_id"] is not None or len(members) != 1 or members[0]["project_id"] != fact["project_id"] or not set(members[0]["pages"]).issubset(set(fact["scope"]["pages"])):
                        raise DesignRecordError("baseline: artifact scope or classification mismatch")
            elif fact["kind"] == "candidate":
                self._require_refs([x["artifact_id"] for x in fact["artifact_bindings"]], artifact_ids, "candidate artifact")
                self._require_refs(fact["evidence_refs"], artifact_ids, "candidate evidence")
                for evidence_id in fact["evidence_refs"]:
                    evidence = artifacts[evidence_id]
                    if evidence["classification"] != fact["classification"] or not self._scope_covered(evidence["scope"], fact["scope"]):
                        raise DesignRecordError("candidate: evidence classification or scope mismatch")
                for artifact_binding in fact["artifact_bindings"]:
                    artifact = artifacts[artifact_binding["artifact_id"]]
                    if artifact["sha256"] != artifact_binding["sha256"] or artifact["scope"] != fact["scope"] or artifact["classification"] != fact["classification"]:
                        raise DesignRecordError("candidate: artifact digest, scope, or classification mismatch")
                for binding in fact["baseline_bindings"]:
                    baseline = baselines.get((binding["baseline_id"], binding["baseline_revision"]))
                    if not baseline or baseline["project_id"] != binding["project_id"] or baseline["content_hash"] != binding["baseline_hash"]:
                        raise DesignRecordError("candidate: invalid baseline binding")
                    if baseline["classification"] != fact["classification"]:
                        raise DesignRecordError("candidate: baseline classification mismatch")
                    if not set(binding["pages"]).issubset(set(baseline["scope"]["pages"])): raise DesignRecordError("candidate: binding pages exceed baseline pages")
                if fact["scope"]["family_id"] is not None:
                    family_ref = fact["family_binding"]
                    family = families.get((family_ref["id"], family_ref["revision"]))
                    if not family or family["content_hash"] != family_ref["content_hash"]:
                        raise DesignRecordError("candidate: invalid family binding")
                    if family["classification"] != fact["classification"] or not self._scope_covered(fact["scope"], family["scope"]):
                        raise DesignRecordError("candidate: family classification or scope mismatch")
            elif fact["kind"] == "design_family":
                self._require_refs(fact["source"]["evidence_refs"], artifact_ids, "design family evidence")
                for artifact_id in fact["source"]["evidence_refs"]:
                    artifact = artifacts[artifact_id]
                    if artifact["classification"] != fact["classification"] or not self._members_covered(artifact["scope"], fact["scope"]):
                        raise DesignRecordError("design family: evidence classification or scope mismatch")
            if fact["kind"] == "artifact_ref" and fact["scope"]["family_id"] is not None:
                family_ref = fact["family_binding"]
                family = families.get((family_ref["id"], family_ref["revision"]))
                if not family or family["content_hash"] != family_ref["content_hash"]:
                    raise DesignRecordError("artifact_ref: invalid family binding")
                if fact["classification"] != family["classification"] or not self._scope_covered(fact["scope"], family["scope"]):
                    raise DesignRecordError("artifact_ref: family classification or scope mismatch")
            elif fact["kind"] == "review":
                review_candidate = self._candidate_for_ref(fact["candidate"], candidates)
                self._require_refs(fact["evidence_refs"], artifact_ids, "review evidence")
                for lane in fact["lanes"]: self._require_refs(lane["evidence_refs"], artifact_ids, "review lane evidence")
                for evidence_id in fact["evidence_refs"] + [value for lane in fact["lanes"] for value in lane["evidence_refs"]]:
                    evidence = artifacts[evidence_id]
                    if evidence["classification"] != review_candidate["classification"] or not self._scope_covered(evidence["scope"], review_candidate["scope"]):
                        raise DesignRecordError("review: evidence classification or scope mismatch")
        events: dict[str, dict[str, Any]] = {}
        requests: dict[str, dict[str, Any]] = {}
        effective_scope: dict[str, str] = {}
        for index, event in enumerate(store["events"]):
            validate_decision_event(event, fixture_store=self.fixture)
            if event["id"] in events: raise DesignRecordError("design_store: duplicate event ID")
            self._candidate_for_ref(event["candidate"], candidates)
            candidate = candidates[(event["candidate"]["id"], event["candidate"]["revision"])]
            if event["scope"] != candidate["scope"]: raise DesignRecordError("decision_event: scope must exactly match candidate scope")
            scope_key = content_hash(event["scope"])
            current_id = effective_scope.get(scope_key)
            if event["supersedes"] is not None:
                prior = events.get(event["supersedes"])
                if prior is None: raise DesignRecordError("decision_event: supersedes must reference an earlier event")
                if prior["scope"] != event["scope"]: raise DesignRecordError("decision_event: supersedes scope mismatch")
                if current_id != event["supersedes"]: raise DesignRecordError("decision_event: supersedes must name current same-scope event")
            elif current_id is not None:
                raise DesignRecordError("decision_event: later same-scope event must supersede current event")
            if event["action"] == "withdraw" and event["supersedes"] is None: raise DesignRecordError("decision_event: withdraw requires supersedes")
            events[event["id"]] = event; effective_scope[scope_key] = event["id"]
        for receipt_index, receipt in enumerate(store["requests"], start=1):
            if not isinstance(receipt, dict) or set(receipt) != {"request_id", "payload_hash", "operation", "result_revision", "result_id", "result_kind", "result_hash"}: raise DesignRecordError("request receipt: fields mismatch")
            if not isinstance(receipt["request_id"], str) or not ID_RE.fullmatch(receipt["request_id"]): raise DesignRecordError("request receipt: invalid request ID")
            if receipt["request_id"] in requests: raise DesignRecordError("request receipt: duplicate request ID")
            if receipt["operation"] not in {"initialize", "append_fact", "append_decision"}: raise DesignRecordError("request receipt: unknown operation")
            if not isinstance(receipt["result_revision"], int) or isinstance(receipt["result_revision"], bool) or receipt["result_revision"] < 1: raise DesignRecordError("request receipt: invalid result revision")
            if receipt["result_revision"] != receipt_index: raise DesignRecordError("request receipt: revisions must be contiguous")
            if not isinstance(receipt["payload_hash"], str) or not HASH_RE.fullmatch(receipt["payload_hash"]): raise DesignRecordError("request receipt: invalid payload hash")
            if receipt["result_hash"] is not None and (not isinstance(receipt["result_hash"], str) or not HASH_RE.fullmatch(receipt["result_hash"])): raise DesignRecordError("request receipt: invalid result hash")
            requests[receipt["request_id"]] = receipt
        event_requests = {e["request_id"] for e in store["events"]}
        if not event_requests.issubset(requests): raise DesignRecordError("decision_event: missing request receipt")
        for event in store["events"]:
            receipt = requests[event["request_id"]]
            expected_payload = {"operation": "append_decision", "event": event}
            if receipt["operation"] != "append_decision" or receipt["payload_hash"] != content_hash(expected_payload) or receipt["result_id"] != event["id"] or receipt["result_kind"] != "decision_event" or receipt["result_hash"] != content_hash(event):
                raise DesignRecordError("decision_event: request receipt linkage mismatch")
        fact_receipts = [r for r in store["requests"] if r["operation"] == "append_fact"]
        for receipt in fact_receipts:
            matches = [f for f in store["facts"] if f["kind"] == receipt["result_kind"] and f["id"] == receipt["result_id"] and content_hash(f) == receipt["result_hash"]]
            if len(matches) != 1 or receipt["payload_hash"] != content_hash({"operation": "append_fact", "fact": matches[0]}):
                raise DesignRecordError("fact: request receipt linkage mismatch")
        if len(fact_receipts) != len(store["facts"]): raise DesignRecordError("facts and append-fact receipts must be bijective")
        if [r["result_hash"] for r in fact_receipts] != [content_hash(f) for f in store["facts"]]:
            raise DesignRecordError("facts must retain committed receipt order")
        decision_receipts = [r for r in store["requests"] if r["operation"] == "append_decision"]
        if len(decision_receipts) != len(store["events"]): raise DesignRecordError("events and append-decision receipts must be bijective")
        if [r["result_hash"] for r in decision_receipts] != [content_hash(e) for e in store["events"]]:
            raise DesignRecordError("events must retain committed receipt order")
        initialize_receipts = [r for r in store["requests"] if r["operation"] == "initialize"]
        expected_init_hash = content_hash({"operation": "initialize", "store": self.empty()})
        if len(initialize_receipts) != 1 or initialize_receipts[0] != {"request_id": "initialize", "payload_hash": expected_init_hash, "operation": "initialize", "result_revision": 1, "result_id": None, "result_kind": None, "result_hash": None}:
            raise DesignRecordError("initialize receipt: non-canonical")
        if store["revision"] != len(store["requests"]): raise DesignRecordError("design_store: revision must equal committed request count")
        return store

    def _project_ids(self) -> set[str]:
        if self.fixture: return set(self.fixture_project_ids)
        registry = self.hub_root / "data/registry/external_projects.yaml"
        try:
            fd = os.open(registry, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "r", encoding="utf-8") as handle: data = yaml.safe_load(handle)
        except (OSError, yaml.YAMLError) as exc: raise DesignRecordError(f"canonical registry unreadable: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("projects"), list): raise DesignRecordError("canonical registry: invalid projects collection")
        ids = {item.get("id") for item in data["projects"] if isinstance(item, dict) and isinstance(item.get("id"), str)}
        if len(ids) != len(data["projects"]): raise DesignRecordError("canonical registry: missing or duplicate project IDs")
        return ids

    @staticmethod
    def _scope_covered(child: dict[str, Any], parent: dict[str, Any]) -> bool:
        if child["family_id"] != parent["family_id"]:
            return False
        return DesignStore._members_covered(child, parent)

    @staticmethod
    def _members_covered(child: dict[str, Any], parent: dict[str, Any]) -> bool:
        parent_members = {member["project_id"]: set(member["pages"]) for member in parent["members"]}
        return all(member["project_id"] in parent_members and set(member["pages"]).issubset(parent_members[member["project_id"]]) for member in child["members"])

    @staticmethod
    def _require_refs(refs: list[str], known: set[str], label: str) -> None:
        missing = sorted(set(refs) - known)
        if missing: raise DesignRecordError(f"{label}: unknown refs {missing}")

    @staticmethod
    def _candidate_for_ref(ref: dict[str, Any], candidates: dict[tuple[str, int], dict[str, Any]]) -> dict[str, Any]:
        candidate = candidates.get((ref["id"], ref["revision"]))
        if not candidate or candidate["content_hash"] != ref["content_hash"]: raise DesignRecordError("candidate reference: unknown or hash mismatch")
        return candidate

    def append_fact(self, fact: dict[str, Any], *, expected_revision: int, request_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        validate_fact(fact)
        payload = {"operation": "append_fact", "fact": fact}
        def apply(store: dict[str, Any]) -> None:
            for old in store["facts"]:
                same = old["kind"] == fact["kind"] and old["id"] == fact["id"] and old.get("revision") == fact.get("revision")
                if same:
                    if old != fact: raise ConflictError("fact identity already exists with different payload")
                    raise ConflictError("fact already exists under a different request")
            store["facts"].append(copy.deepcopy(fact))
        return self._mutate(expected_revision, request_id, payload, apply, result_id=fact["id"], result_kind=fact["kind"], result_hash=content_hash(fact))

    def append_decision(self, event: dict[str, Any], *, expected_revision: int, trusted_owner_reference: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        validate_decision_event(event, fixture_store=self.fixture)
        if not self.fixture and (not trusted_owner_reference or trusted_owner_reference != event["source"]["reference"]):
            raise StoreError("real decision requires a separately supplied matching trusted owner reference")
        payload = {"operation": "append_decision", "event": event}
        def apply(store: dict[str, Any]) -> None:
            store["events"].append(copy.deepcopy(event))
        return self._mutate(expected_revision, event["request_id"], payload, apply, result_id=event["id"], result_kind="decision_event", result_hash=content_hash(event))

    def _mutate(self, expected_revision: int, request_id: str, payload: dict[str, Any], apply: Callable[[dict[str, Any]], None], *, result_id: str | None = None, result_kind: str | None = None, result_hash: str | None = None, allow_initialize: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ConflictError("expected_revision must be a nonnegative integer")
        if not isinstance(request_id, str) or not ID_RE.fullmatch(request_id):
            raise DesignRecordError("request_id: invalid ID")
        self._assert_writable_path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._assert_writable_path(self.path)
        lock_fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0), 0o600)
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            os.close(lock_fd)
            raise StoreError("lock must be a regular file")
        with os.fdopen(lock_fd, "r+b") as lock_file:
            deadline = time.monotonic() + self.lock_timeout
            while True:
                try: fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB); break
                except BlockingIOError:
                    if time.monotonic() >= deadline: raise LockConflict(f"lock timeout: {self.lock_path}")
                    time.sleep(0.01)
            if self.path.exists(): store = self.read()
            elif allow_initialize: store = self.empty()
            else: raise StoreError("store is not initialized")
            payload_digest = content_hash(payload)
            prior = next((r for r in store["requests"] if r["request_id"] == request_id), None)
            if prior:
                if prior["payload_hash"] != payload_digest: raise ConflictError("request ID reused with different payload")
                return store, copy.deepcopy(prior)
            if store["revision"] != expected_revision: raise ConflictError(f"expected revision {expected_revision}, found {store['revision']}")
            apply(store)
            new_revision = store["revision"] + 1
            receipt = {"request_id": request_id, "payload_hash": payload_digest, "operation": payload["operation"], "result_revision": new_revision, "result_id": result_id, "result_kind": result_kind, "result_hash": result_hash}
            store["requests"].append(receipt); store["revision"] = new_revision
            self.validate(store); self._atomic_write(store, receipt)
            return store, copy.deepcopy(receipt)

    def _atomic_write(self, store: dict[str, Any], receipt: dict[str, Any]) -> None:
        payload = canonical_bytes(store) + b"\n"
        if len(payload) > MAX_STORE_BYTES:
            raise StoreError("store exceeds 16 MiB limit")
        temp_name: str | None = None
        try:
            fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload); handle.flush(); os.fsync(handle.fileno())
            if os.environ.get("HUB_DESIGN_FAIL_BEFORE_REPLACE") == "1": raise StoreError("injected failure before replace")
            os.replace(temp_name, self.path); temp_name = None
            try:
                directory_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    if os.environ.get("HUB_DESIGN_FAIL_DIRECTORY_FSYNC") == "1":
                        raise OSError(errno.EIO, "injected directory fsync failure")
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError as exc:
                expected_digest = hashlib.sha256(payload).hexdigest()
                observed_digest: str | None = None
                try:
                    if os.environ.get("HUB_DESIGN_FAIL_PUBLISHED_VERIFICATION") == "1":
                        raise OSError(errno.EIO, "injected published-content verification failure")
                    published_fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
                    with os.fdopen(published_fd, "rb") as published:
                        published_info = os.fstat(published.fileno())
                        if not stat.S_ISREG(published_info.st_mode):
                            raise OSError(errno.EINVAL, "published store is not a regular file")
                        actual = published.read(MAX_STORE_BYTES + 1)
                except OSError as verification_error:
                    raise CommittedVerificationFailed(revision=store["revision"], receipt=receipt, path=self.path, expected_store_sha256=expected_digest, observed_store_sha256=None, verification_error=str(verification_error)) from exc
                observed_digest = hashlib.sha256(actual).hexdigest()
                if actual != payload:
                    raise CommittedVerificationFailed(revision=store["revision"], receipt=receipt, path=self.path, expected_store_sha256=expected_digest, observed_store_sha256=observed_digest, verification_error="published bytes differ from expected committed payload") from exc
                raise CommittedDurabilityUnconfirmed(revision=store["revision"], receipt=receipt, path=self.path, store_sha256=observed_digest, cause=exc) from exc
        finally:
            if temp_name:
                try: os.unlink(temp_name)
                except FileNotFoundError: pass

    def projection(self, store: dict[str, Any] | None = None) -> dict[str, Any]:
        store = self.read() if store is None else copy.deepcopy(store)
        self.validate(store)
        baselines = [f for f in store["facts"] if f["kind"] == "baseline"]
        candidates = {(f["id"], f["revision"]): f for f in store["facts"] if f["kind"] == "candidate"}
        latest_baseline: dict[tuple[str, str], dict[str, Any]] = {}
        for baseline in baselines:
            for page in baseline["scope"]["pages"]:
                latest_baseline[(baseline["project_id"], page)] = baseline
        latest_candidate: dict[str, dict[str, Any]] = {}
        for candidate in candidates.values():
            current = latest_candidate.get(candidate["id"])
            if current is None or candidate["revision"] > current["revision"]:
                latest_candidate[candidate["id"]] = candidate
        latest_family: dict[str, dict[str, Any]] = {}
        for family in (fact for fact in store["facts"] if fact["kind"] == "design_family"):
            current = latest_family.get(family["id"])
            if current is None or family["revision"] > current["revision"]:
                latest_family[family["id"]] = family
        superseded = {e["supersedes"] for e in store["events"] if e["supersedes"]}
        history = []
        effective: dict[str, dict[str, Any]] = {}
        for index, event in enumerate(store["events"]):
            candidate = candidates[(event["candidate"]["id"], event["candidate"]["revision"])]
            reasons = []
            if latest_candidate[candidate["id"]]["revision"] != candidate["revision"]: reasons.append("candidate_revision_drift")
            if latest_candidate[candidate["id"]]["scope"] != candidate["scope"]: reasons.append("scope_drift")
            if candidate["scope"]["family_id"] is not None:
                binding = candidate["family_binding"]
                current_family = latest_family.get(binding["id"])
                if not current_family or current_family["revision"] != binding["revision"] or current_family["content_hash"] != binding["content_hash"]:
                    reasons.append(f"family_drift:{binding['id']}")
            for binding in candidate["baseline_bindings"]:
                for page in binding["pages"]:
                    latest = latest_baseline.get((binding["project_id"], page))
                    if not latest or latest["id"] != binding["baseline_id"] or latest["revision"] != binding["baseline_revision"] or latest["content_hash"] != binding["baseline_hash"]: reasons.append(f"baseline_drift:{binding['project_id']}:{page}")
            item = {"sequence": index + 1, "event": copy.deepcopy(event), "stale": bool(reasons), "stale_reasons": sorted(set(reasons)), "superseded": event["id"] in superseded}
            history.append(item)
            key = content_hash(event["scope"])
            if not item["superseded"]: effective[key] = item
        queues = {"selected": [], "changes_requested": [], "deferred": [], "withdrawn": [], "stale": []}
        for key, item in sorted(effective.items()):
            if item["stale"]: queues["stale"].append(item); continue
            action = item["event"]["action"]
            queues[{"select": "selected", "request_changes": "changes_requested", "defer": "deferred", "withdraw": "withdrawn"}[action]].append(item)
        return {"store_revision": store["revision"], "store_classification": store["store_classification"], "real_selection_count": 0 if self.fixture else len(queues["selected"]), "history": history, "effective": effective, "queues": queues}

    def export(self, candidate_id: str, candidate_revision: int, output_path: str | Path) -> dict[str, Any]:
        from .design_export import export_bundle
        store = self.read()
        return export_bundle(hub_root=self.hub_root, store_path=self.path, fixture=self.fixture, store=store, projection=self.projection(store), candidate_id=candidate_id, candidate_revision=candidate_revision, output_path=Path(output_path))


def content_hash_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()
