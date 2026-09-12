"""Transport-independent, authority-preserving access to accepted design facts."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import stat
import zipfile
import zlib
from pathlib import Path
from typing import Any

from .design_export import (
    MAX_ARTIFACT_BYTES as MAX_EXPORT_ARTIFACT_BYTES,
    MAX_BUNDLE_BYTES,
    MAX_BUNDLE_MATERIAL_BYTES,
    MAX_MANIFEST_BYTES,
    DesignExportError,
    export_bundle,
)
from .design_records import (
    HASH_RE,
    ID_RE,
    DesignRecordError,
    content_hash,
    validate_candidate_ref,
    validate_decision_event,
    validate_fact,
    validate_scope,
)
from .design_store import (
    CommittedDurabilityUnconfirmed,
    CommittedVerificationFailed,
    ConflictError,
    DesignStore,
    LockConflict,
    StoreError,
)
from .service_contract import ArtifactResponse, OwnerAction, ServiceError


DECISION_FIELDS = {
    "request_id", "event_id", "created_at", "expected_revision", "action",
    "candidate", "scope", "feedback", "supersedes",
}
EXPORT_FIELDS = {"request_id", "expected_revision", "candidate"}
CANDIDATE_REF_FIELDS = {"id", "revision", "content_hash"}
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ServiceError("INVALID_COMMAND", details={"field": label})
    return value


def _candidate_ref(value: Any) -> dict[str, Any]:
    _exact(value, CANDIDATE_REF_FIELDS, "candidate")
    try:
        return copy.deepcopy(validate_candidate_ref(value, "candidate"))
    except DesignRecordError as exc:
        raise ServiceError("INVALID_COMMAND", details={"field": "candidate"}) from exc


def _request_id(value: Any) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ServiceError("INVALID_COMMAND", details={"field": "request_id"})
    return value


def _receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "request_id", "payload_hash", "operation", "result_revision",
        "result_id", "result_kind", "result_hash",
    }
    return {key: copy.deepcopy(receipt.get(key)) for key in fields}


def _committed_error(exc: CommittedDurabilityUnconfirmed | CommittedVerificationFailed) -> ServiceError:
    details: dict[str, Any] = {
        "revision": exc.revision,
        "receipt": _receipt(exc.receipt),
    }
    if isinstance(exc, CommittedDurabilityUnconfirmed):
        details["store_sha256"] = exc.store_sha256
        code = "COMMITTED_DURABILITY_UNCONFIRMED"
    else:
        details["expected_store_sha256"] = exc.expected_store_sha256
        details["observed_store_sha256"] = exc.observed_store_sha256
        code = "COMMITTED_VERIFICATION_FAILED"
    return ServiceError(code, status=503, retryable=True, outcome=code, details=details)


class DesignService:
    """Application service over one already-configured ``DesignStore``."""

    def __init__(self, store: DesignStore):
        if not isinstance(store, DesignStore):
            raise TypeError("store must be a DesignStore")
        self.store = store

    def _read(self) -> dict[str, Any]:
        if not self.store.path.exists():
            raise ServiceError("DESIGN_STORE_UNAVAILABLE", status=503, retryable=True)
        try:
            return self.store.read()
        except (DesignRecordError, StoreError) as exc:
            raise ServiceError("DESIGN_STORE_CORRUPT", status=500) from exc
        except OSError as exc:
            raise ServiceError("DESIGN_STORE_UNAVAILABLE", status=503, retryable=True) from exc

    def snapshot(self) -> dict[str, Any]:
        """Read and validate once; never initialize or refresh an absent store."""
        if not self.store.path.exists():
            return {
                "available": False,
                "store_revision": None,
                "store_classification": None,
                "facts": [],
                "history": [],
                "effective": {},
                "queues": {},
                "reason": "DESIGN_STORE_UNAVAILABLE",
            }
        store = self._read()
        try:
            projection = self.store.projection(store)
        except DesignRecordError as exc:
            raise ServiceError("DESIGN_STORE_CORRUPT", status=500) from exc
        facts = []
        for fact in store["facts"]:
            item = copy.deepcopy(fact)
            if item["kind"] == "artifact_ref":
                location = item.pop("location")
                item["delivery"] = (
                    {"kind": "figma_link", "value": location["value"]}
                    if location["kind"] == "figma"
                    else {"kind": "registered_artifact", "artifact_id": item["id"]}
                )
            facts.append(item)
        return {
            "available": True,
            "store_revision": store["revision"],
            "store_classification": store["store_classification"],
            "facts": facts,
            "history": projection["history"],
            "effective": projection["effective"],
            "queues": projection["queues"],
            "reason": None,
        }

    @staticmethod
    def _candidate(store: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
        matches = [
            fact for fact in store["facts"]
            if fact.get("kind") == "candidate"
            and fact.get("id") == reference["id"]
            and fact.get("revision") == reference["revision"]
        ]
        if len(matches) != 1 or matches[0].get("content_hash") != reference["content_hash"]:
            raise ServiceError("CANDIDATE_NOT_FOUND", status=404)
        return matches[0]

    @staticmethod
    def _assert_current_candidate(store: dict[str, Any], candidate: dict[str, Any]) -> None:
        same_id = [f for f in store["facts"] if f.get("kind") == "candidate" and f.get("id") == candidate["id"]]
        latest = max(same_id, key=lambda item: item["revision"])
        if (latest["revision"], latest["content_hash"]) != (candidate["revision"], candidate["content_hash"]):
            raise ServiceError("CANDIDATE_STALE", status=409)
        latest_baseline: dict[tuple[str, str], dict[str, Any]] = {}
        for fact in store["facts"]:
            if fact.get("kind") == "baseline":
                for page in fact["scope"]["pages"]:
                    latest_baseline[(fact["project_id"], page)] = fact
        for binding in candidate["baseline_bindings"]:
            for page in binding["pages"]:
                current = latest_baseline.get((binding["project_id"], page))
                if current is None or (
                    current["id"], current["revision"], current["content_hash"]
                ) != (binding["baseline_id"], binding["baseline_revision"], binding["baseline_hash"]):
                    raise ServiceError("BASELINE_STALE", status=409)
        family_binding = candidate.get("family_binding")
        if family_binding is not None:
            families = [f for f in store["facts"] if f.get("kind") == "design_family" and f.get("id") == family_binding["id"]]
            latest_family = max(families, key=lambda item: item["revision"], default=None)
            if latest_family is None or (
                latest_family["revision"], latest_family["content_hash"]
            ) != (family_binding["revision"], family_binding["content_hash"]):
                raise ServiceError("FAMILY_STALE", status=409)

    def decide(self, command: dict[str, Any], *, owner_action: OwnerAction) -> dict[str, Any]:
        command = _exact(command, DECISION_FIELDS, "command")
        if not isinstance(owner_action, OwnerAction):
            raise ServiceError("OWNER_ACTION_REQUIRED", status=403)
        request_id = _request_id(command["request_id"])
        reference = _candidate_ref(command["candidate"])
        if type(command["expected_revision"]) is not int or command["expected_revision"] < 0:
            raise ServiceError("INVALID_COMMAND", details={"field": "expected_revision"})
        try:
            scope = copy.deepcopy(validate_scope(command["scope"], "scope"))
        except DesignRecordError as exc:
            raise ServiceError("INVALID_COMMAND", details={"field": "scope"}) from exc
        if owner_action.fixture != self.store.fixture:
            raise ServiceError("OWNER_ACTION_CLASSIFICATION_MISMATCH", status=403)
        event = {
            "schema_version": "1.0",
            "kind": "decision_event",
            "id": command["event_id"],
            "request_id": request_id,
            "created_at": command["created_at"],
            "source": owner_action.source(request_id),
            "action": command["action"],
            "candidate": reference,
            "scope": scope,
            "feedback": command["feedback"],
            "supersedes": command["supersedes"],
        }
        try:
            validate_decision_event(event, fixture_store=self.store.fixture)
        except DesignRecordError as exc:
            raise ServiceError("INVALID_COMMAND") from exc
        store = self._read()
        prior = next((item for item in store["requests"] if item["request_id"] == request_id), None)
        if prior is not None:
            committed = next((item for item in store["events"] if item["request_id"] == request_id), None)
            if committed != event or prior["result_hash"] != content_hash(event):
                raise ServiceError("REQUEST_CONFLICT", status=409)
            return self._decision_result(prior)
        candidate = self._candidate(store, reference)
        if candidate["scope"] != scope:
            raise ServiceError("CANDIDATE_SCOPE_MISMATCH", status=409)
        self._assert_current_candidate(store, candidate)
        try:
            _state, receipt = self.store.append_decision(
                event,
                expected_revision=command["expected_revision"],
                trusted_owner_reference=None if self.store.fixture else event["source"]["reference"],
            )
        except LockConflict as exc:
            raise ServiceError("CONCURRENCY_CONFLICT", status=409, retryable=True) from exc
        except ConflictError as exc:
            raise ServiceError("REVISION_CONFLICT", status=409) from exc
        except (CommittedDurabilityUnconfirmed, CommittedVerificationFailed) as exc:
            raise _committed_error(exc) from exc
        except DesignRecordError as exc:
            raise ServiceError("INVALID_COMMAND") from exc
        except (StoreError, OSError) as exc:
            raise ServiceError("DESIGN_STORE_UNAVAILABLE", status=503, retryable=True) from exc
        return self._decision_result(receipt)

    @staticmethod
    def _decision_result(receipt: dict[str, Any]) -> dict[str, Any]:
        return {
            "outcome": "COMMITTED",
            "request_id": receipt["request_id"],
            "event_id": receipt["result_id"],
            "event_hash": receipt["result_hash"],
            "store_revision": receipt["result_revision"],
            "receipt": _receipt(receipt),
        }

    def _export_path(self, request_id: str) -> Path:
        if self.store.fixture:
            relative = f"docs/reports/ui_design_governance/unit-04/exports/synthetic_fixture/{request_id}.zip"
        else:
            relative = f"docs/reports/ui_design_governance/service/exports/real/{request_id}.zip"
        return self.store.hub_root / relative

    def export(self, command: dict[str, Any]) -> dict[str, Any]:
        command = _exact(command, EXPORT_FIELDS, "command")
        request_id = _request_id(command["request_id"])
        reference = _candidate_ref(command["candidate"])
        if type(command["expected_revision"]) is not int or command["expected_revision"] < 0:
            raise ServiceError("INVALID_COMMAND", details={"field": "expected_revision"})
        output = self._export_path(request_id)
        relative_output = output.relative_to(self.store.hub_root)
        if output.exists() or output.is_symlink():
            return self._existing_export(relative_output, request_id, command["expected_revision"], reference)
        store = self._read()
        if store["revision"] != command["expected_revision"]:
            raise ServiceError("REVISION_CONFLICT", status=409)
        candidate = self._candidate(store, reference)
        self._assert_current_candidate(store, candidate)
        try:
            projection = self.store.projection(store)
            result = export_bundle(
                hub_root=self.store.hub_root,
                store_path=self.store.path,
                fixture=self.store.fixture,
                store=store,
                projection=projection,
                candidate_id=reference["id"],
                candidate_revision=reference["revision"],
                output_path=output,
            )
        except (DesignRecordError, DesignExportError, StoreError, OSError) as exc:
            if output.exists() and not output.is_symlink():
                try:
                    self._verify_export(relative_output, command["expected_revision"], reference)
                except ServiceError as verification:
                    raise verification from exc
                raise ServiceError(
                    "COMMITTED_DURABILITY_UNCONFIRMED", status=503, retryable=True,
                    outcome="COMMITTED_DURABILITY_UNCONFIRMED",
                    details={"request_id": request_id, "store_revision": command["expected_revision"]},
                ) from exc
            if isinstance(exc, (DesignRecordError, DesignExportError)):
                raise ServiceError("EXPORT_REJECTED") from exc
            raise ServiceError("DESIGN_STORE_UNAVAILABLE", status=503, retryable=True) from exc
        return {
            "outcome": result["outcome"],
            "request_id": request_id,
            "candidate": reference,
            "store_revision": result["store_revision"],
            "sha256": result.get("sha256"),
            "expected_sha256": result.get("expected_sha256", result.get("sha256")),
        }

    def _existing_export(
        self, relative_path: Path, request_id: str, expected_revision: int, reference: dict[str, Any]
    ) -> dict[str, Any]:
        _data, digest, _manifest = self._verify_export(relative_path, expected_revision, reference)
        return {
            "outcome": "COMMITTED",
            "request_id": request_id,
            "candidate": copy.deepcopy(reference),
            "store_revision": expected_revision,
            "sha256": digest,
            "expected_sha256": digest,
        }

    def export_download(self, command: dict[str, Any]) -> ArtifactResponse:
        """Read an already-published registered export; this method never creates one."""
        command = _exact(command, EXPORT_FIELDS, "command")
        request_id = _request_id(command["request_id"])
        reference = _candidate_ref(command["candidate"])
        revision = command["expected_revision"]
        if type(revision) is not int or revision < 0:
            raise ServiceError("INVALID_COMMAND", details={"field": "expected_revision"})
        output = self._export_path(request_id)
        if not output.exists() or output.is_symlink():
            raise ServiceError("EXPORT_NOT_FOUND", status=404)
        data, digest, _manifest = self._verify_export(
            output.relative_to(self.store.hub_root), revision, reference,
        )
        return ArtifactResponse(
            data=data,
            content_type="application/zip",
            filename=f"{request_id}.zip",
            disposition="attachment",
            sha256=digest,
        )

    def _store_prefix(self, store: dict[str, Any], revision: int) -> dict[str, Any]:
        if type(revision) is not int or revision < 1 or revision > store["revision"]:
            raise ServiceError("EXPORT_REQUEST_CONFLICT", status=409)
        receipts = copy.deepcopy(store["requests"][:revision])
        fact_count = sum(item["operation"] == "append_fact" for item in receipts)
        event_count = sum(item["operation"] == "append_decision" for item in receipts)
        prefix = {
            "schema_version": store["schema_version"],
            "kind": store["kind"],
            "store_classification": store["store_classification"],
            "revision": revision,
            "facts": copy.deepcopy(store["facts"][:fact_count]),
            "events": copy.deepcopy(store["events"][:event_count]),
            "requests": receipts,
        }
        try:
            self.store.validate(prefix)
        except DesignRecordError as exc:
            raise ServiceError("EXPORT_CORRUPT", status=500, outcome="COMMITTED_UNCONFIRMED") from exc
        return prefix

    def _verify_export(
        self, relative_path: Path, expected_revision: int, reference: dict[str, Any]
    ) -> tuple[bytes, str, dict[str, Any]]:
        try:
            data, _info = self._read_path(relative_path, limit=MAX_BUNDLE_BYTES)
        except ServiceError as exc:
            raise ServiceError(
                "EXPORT_CORRUPT", status=500, outcome="COMMITTED_UNCONFIRMED"
            ) from exc
        digest = hashlib.sha256(data).hexdigest()
        try:
            with zipfile.ZipFile(io.BytesIO(data), "r") as bundle:
                infos = bundle.infolist()
                names = [item.filename for item in infos]
                if not infos or len(infos) > 1024 or len(names) != len(set(names)):
                    raise ValueError("invalid zip member")
                manifest_info = next(item for item in infos if item.filename == "manifest.json")
                material_infos = [item for item in infos if item.filename != "manifest.json"]
                if (
                    manifest_info.file_size > MAX_MANIFEST_BYTES
                    or any(item.compress_type != zipfile.ZIP_DEFLATED for item in infos)
                    or any(item.is_dir() or item.flag_bits & 1 or item.file_size > MAX_EXPORT_ARTIFACT_BYTES for item in infos)
                    or sum(item.file_size for item in material_infos) > MAX_BUNDLE_MATERIAL_BYTES
                ):
                    raise ValueError("zip budget exceeded")
                if bundle.testzip() is not None:
                    raise ValueError("invalid zip member")
                def object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                    result: dict[str, Any] = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError("duplicate JSON key")
                        result[key] = value
                    return result
                manifest = json.loads(bundle.read(manifest_info), object_pairs_hook=object_without_duplicates)
                if not isinstance(manifest, dict):
                    raise ValueError("manifest must be an object")
                identity = manifest.get("candidate_identity")
                store_identity = manifest.get("store")
                if not isinstance(identity, dict) or not isinstance(store_identity, dict):
                    raise ValueError("manifest identity must be an object")
                declared = manifest.get("artifact_files")
                if not isinstance(declared, list):
                    raise ValueError("artifact declarations missing")
                archive_names = []
                for item in declared:
                    if not isinstance(item, dict) or set(item) != {"record", "roles", "archive_path", "size", "sha256"}:
                        raise ValueError("invalid artifact declaration")
                    archive_path = item["archive_path"]
                    if not isinstance(archive_path, str) or not archive_path.startswith("artifacts/"):
                        raise ValueError("invalid archive path")
                    member = bundle.getinfo(archive_path)
                    payload = bundle.read(member)
                    if member.file_size != item["size"] or hashlib.sha256(payload).hexdigest() != item["sha256"]:
                        raise ValueError("artifact member mismatch")
                    archive_names.append(archive_path)
                if set(names) != {"manifest.json", *archive_names} or len(archive_names) != len(set(archive_names)):
                    raise ValueError("unexpected zip members")
        except (
            KeyError, StopIteration, TypeError, ValueError, RecursionError, NotImplementedError,
            zipfile.BadZipFile, UnicodeError, json.JSONDecodeError, zlib.error,
        ) as exc:
            raise ServiceError("EXPORT_CORRUPT", status=500, outcome="COMMITTED_UNCONFIRMED") from exc
        try:
            store = self._read()
        except ServiceError as exc:
            raise ServiceError(
                "EXPORT_AUTHORITY_UNAVAILABLE", status=503, retryable=True,
                outcome="COMMITTED_UNCONFIRMED",
                details={"store_revision": expected_revision},
            ) from exc
        prefix = self._store_prefix(store, expected_revision)
        projection = self.store.projection(prefix)
        candidate = {key: identity.get(key) for key in CANDIDATE_REF_FIELDS}
        store_revision = store_identity.get("revision")
        if candidate != reference or store_revision != expected_revision:
            raise ServiceError("EXPORT_REQUEST_CONFLICT", status=409)
        expected_candidate = self._candidate(prefix, reference)
        self._assert_current_candidate(prefix, expected_candidate)
        expected_store = {
            "path": str(self.store.path.relative_to(self.store.hub_root)),
            "revision": expected_revision,
            "classification": prefix["store_classification"],
        }
        expected_baselines = []
        for binding in expected_candidate["baseline_bindings"]:
            matches = [
                item for item in prefix["facts"]
                if item.get("kind") == "baseline" and item.get("id") == binding["baseline_id"]
                and item.get("revision") == binding["baseline_revision"]
                and item.get("content_hash") == binding["baseline_hash"]
            ]
            if len(matches) != 1:
                raise ServiceError("EXPORT_CORRUPT", status=500, outcome="COMMITTED_UNCONFIRMED")
            expected_baselines.append(matches[0])
        family = None
        if expected_candidate.get("family_binding") is not None:
            binding = expected_candidate["family_binding"]
            family = next((
                item for item in prefix["facts"] if item.get("kind") == "design_family"
                and (item["id"], item["revision"], item["content_hash"])
                == (binding["id"], binding["revision"], binding["content_hash"])
            ), None)
        candidate_history = [
            item for item in projection["history"]
            if item["event"]["candidate"] == reference and item["event"]["scope"] == expected_candidate["scope"]
        ]
        selected = [
            item for item in projection["effective"].values()
            if item["event"]["candidate"] == reference and item["event"]["action"] == "select"
            and not item["stale"] and not item["superseded"]
        ]
        expected_selection = selected[0] if len(selected) == 1 else None
        expected_reviews = sorted(
            [item for item in prefix["facts"] if item.get("kind") == "review" and item.get("candidate") == reference],
            key=lambda item: (item["created_at"], item["id"]),
        )
        manifest_fields = {
            "schema_version", "kind", "bundle_classification", "authority", "store",
            "candidate_identity", "candidate", "bound_family", "bound_baselines", "reviews",
            "selection_state", "selection", "decision_history", "history_hash", "artifact_files",
            "figma_references", "provenance",
        }
        expected_classification = "synthetic_fixture" if self.store.fixture else "real"
        graph_checks = (
            isinstance(manifest, dict) and set(manifest) == manifest_fields,
            manifest.get("schema_version") == "1.0",
            manifest.get("kind") == "design_export_bundle",
            manifest.get("bundle_classification") == expected_classification,
            manifest.get("authority") == {
                "real_selection": expected_selection is not None and not self.store.fixture,
                "implementation_authority": False,
                "fixture": self.store.fixture,
            },
            manifest.get("provenance") == {
                "snapshot_only": True, "offline": True, "external_business_data": False,
            },
            manifest.get("store") == expected_store,
            manifest.get("candidate") == expected_candidate,
            identity == {**reference, "scope": expected_candidate["scope"]},
            manifest.get("bound_family") == family,
            manifest.get("bound_baselines") == sorted(expected_baselines, key=lambda item: (item["project_id"], item["id"], item["revision"])),
            manifest.get("reviews") == expected_reviews,
            manifest.get("decision_history") == candidate_history,
            manifest.get("history_hash") == content_hash(candidate_history),
            manifest.get("selection") == expected_selection,
            manifest.get("selection_state") == ("selected" if expected_selection is not None else "unselected"),
        )
        if not all(graph_checks):
            raise ServiceError("EXPORT_CORRUPT", status=500, outcome="COMMITTED_UNCONFIRMED")
        artifacts = {item["id"]: item for item in prefix["facts"] if item.get("kind") == "artifact_ref"}
        requirements: dict[str, dict[str, Any]] = {}
        def require_artifact(artifact_id: str, role: str) -> None:
            requirements.setdefault(artifact_id, {"roles": []})["roles"].append(role)
        for baseline in expected_baselines:
            for item in baseline["artifact_bindings"]:
                require_artifact(item["artifact_id"], f"baseline:{baseline['id']}")
        for item in expected_candidate["artifact_bindings"]:
            require_artifact(item["artifact_id"], "candidate_material")
        for artifact_id in expected_candidate["evidence_refs"]:
            require_artifact(artifact_id, "candidate_evidence")
        for review in expected_reviews:
            for artifact_id in review["evidence_refs"]:
                require_artifact(artifact_id, f"review:{review['id']}")
            for lane in review["lanes"]:
                for artifact_id in lane["evidence_refs"]:
                    require_artifact(artifact_id, f"review:{review['id']}:{lane['name']}")
        if family is not None:
            for artifact_id in family["source"]["evidence_refs"]:
                require_artifact(artifact_id, f"family:{family['id']}")
        expected_figma = []
        if any(expected_candidate["figma_ref"].get(key) is not None for key in ("file_key", "node_id", "version")):
            expected_figma.append({
                "kind": "candidate_figma_ref", "offline_only": True,
                "value": expected_candidate["figma_ref"],
            })
        expected_files: dict[str, dict[str, Any]] = {}
        for artifact_id in sorted(requirements):
            artifact = artifacts.get(artifact_id)
            if artifact is None:
                raise ServiceError("EXPORT_CORRUPT", status=500, outcome="COMMITTED_UNCONFIRMED")
            if artifact["location"]["kind"] == "figma":
                expected_figma.append({
                    "kind": "artifact_ref", "offline_only": True,
                    "roles": requirements[artifact_id]["roles"], "value": artifact,
                })
                continue
            archive_path = f"artifacts/{artifact_id}/{Path(artifact['location']['value']).name}"
            expected_files[artifact_id] = {
                "record": artifact,
                "roles": requirements[artifact_id]["roles"],
                "archive_path": archive_path,
                "sha256": artifact["sha256"],
            }
        declared_ids = set()
        for item in manifest["artifact_files"]:
            record = item["record"]
            expected = expected_files.get(record.get("id") if isinstance(record, dict) else None)
            if (
                expected is None or set(item) != {"record", "roles", "archive_path", "size", "sha256"}
                or any(item[key] != expected[key] for key in ("record", "roles", "archive_path", "sha256"))
            ):
                raise ServiceError("EXPORT_CORRUPT", status=500, outcome="COMMITTED_UNCONFIRMED")
            declared_ids.add(record["id"])
        if declared_ids != set(expected_files) or manifest.get("figma_references") != expected_figma:
            raise ServiceError("EXPORT_CORRUPT", status=500, outcome="COMMITTED_UNCONFIRMED")
        return data, digest, manifest

    def artifact(
        self, artifact_id: str, *, candidate_id: str, candidate_revision: int
    ) -> ArtifactResponse:
        if not isinstance(artifact_id, str) or not ID_RE.fullmatch(artifact_id):
            raise ServiceError("INVALID_ARTIFACT_ID")
        if not isinstance(candidate_id, str) or not ID_RE.fullmatch(candidate_id):
            raise ServiceError("INVALID_CANDIDATE_ID")
        if type(candidate_revision) is not int or candidate_revision <= 0:
            raise ServiceError("INVALID_CANDIDATE_REVISION")
        store = self._read()
        candidates = [
            item for item in store["facts"]
            if item.get("kind") == "candidate" and item.get("id") == candidate_id
            and item.get("revision") == candidate_revision
        ]
        if len(candidates) != 1:
            raise ServiceError("CANDIDATE_NOT_FOUND", status=404)
        candidate = candidates[0]
        artifacts = [item for item in store["facts"] if item.get("kind") == "artifact_ref" and item.get("id") == artifact_id]
        if len(artifacts) != 1:
            raise ServiceError("ARTIFACT_NOT_FOUND", status=404)
        artifact = artifacts[0]
        try:
            validate_fact(copy.deepcopy(artifact))
        except DesignRecordError as exc:
            raise ServiceError("DESIGN_STORE_CORRUPT", status=500) from exc
        bindings = {item["artifact_id"]: item["sha256"] for item in candidate["artifact_bindings"]}
        evidence = set(candidate["evidence_refs"])
        baseline_bindings: dict[str, tuple[str, dict[str, Any]]] = {}
        for candidate_binding in candidate["baseline_bindings"]:
            baseline = next((
                item for item in store["facts"] if item.get("kind") == "baseline"
                and item.get("id") == candidate_binding["baseline_id"]
                and item.get("revision") == candidate_binding["baseline_revision"]
                and item.get("content_hash") == candidate_binding["baseline_hash"]
            ), None)
            if baseline is None:
                raise ServiceError("DESIGN_STORE_CORRUPT", status=500)
            for item in baseline["artifact_bindings"]:
                baseline_bindings[item["artifact_id"]] = (item["sha256"], candidate_binding)
        if artifact_id not in bindings and artifact_id not in evidence and artifact_id not in baseline_bindings:
            raise ServiceError("ARTIFACT_NOT_BOUND", status=404)
        if artifact["classification"] != candidate["classification"]:
            raise ServiceError("ARTIFACT_BINDING_MISMATCH", status=409)
        if artifact_id in baseline_bindings:
            expected_digest, candidate_binding = baseline_bindings[artifact_id]
            members = artifact["scope"]["members"]
            scope_ok = (
                artifact["scope"]["family_id"] is None and len(members) == 1
                and members[0]["project_id"] == candidate_binding["project_id"]
                and set(members[0]["pages"]).issubset(candidate_binding["pages"])
            )
            if not scope_ok or artifact["sha256"] != expected_digest:
                raise ServiceError("ARTIFACT_BINDING_MISMATCH", status=409)
        elif artifact["scope"] != candidate["scope"]:
            raise ServiceError("ARTIFACT_BINDING_MISMATCH", status=409)
        elif artifact_id in bindings and artifact["sha256"] != bindings[artifact_id]:
            raise ServiceError("ARTIFACT_BINDING_MISMATCH", status=409)
        location = artifact["location"]
        if location["kind"] == "figma":
            data = (location["value"] + "\n").encode("utf-8")
            return ArtifactResponse(data, "text/plain; charset=utf-8", f"{artifact_id}.url", "inline", None)
        path = Path(location["value"])
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            raise ServiceError("ARTIFACT_UNAVAILABLE", status=404)
        data, _info = self._read_path(path, limit=MAX_ARTIFACT_BYTES)
        digest = hashlib.sha256(data).hexdigest()
        if not isinstance(artifact["sha256"], str) or not HASH_RE.fullmatch(artifact["sha256"]) or digest != artifact["sha256"]:
            raise ServiceError("ARTIFACT_HASH_MISMATCH", status=409)
        suffix = path.suffix.lower()
        if not suffix[1:].isalnum() or len(suffix) > 9:
            suffix = ".bin"
        filename = artifact_id + suffix
        media_type = self._raster_type(data, path.suffix.lower())
        if media_type is not None:
            return ArtifactResponse(data, media_type, filename, "inline", digest)
        return ArtifactResponse(data, "application/octet-stream", filename, "attachment", digest)

    @staticmethod
    def _raster_type(data: bytes, suffix: str) -> str | None:
        if suffix == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if suffix in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if suffix == ".gif" and data[:6] in {b"GIF87a", b"GIF89a"}:
            return "image/gif"
        if suffix == ".webp" and len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return "image/webp"
        return None

    def _read_path(self, relative: Path, *, limit: int) -> tuple[bytes, os.stat_result]:
        allowed = (
            Path("data/design_governance"),
            Path("docs/reports/ui_design_governance"),
        )
        if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise ServiceError("ARTIFACT_UNAVAILABLE", status=404)
        if not any(relative == root or relative.is_relative_to(root) for root in allowed):
            raise ServiceError("ARTIFACT_UNAVAILABLE", status=404)
        target = self.store.hub_root / relative
        if target in {self.store.path, self.store.lock_path}:
            raise ServiceError("ARTIFACT_UNAVAILABLE", status=404)
        directory = os.open(self.store.hub_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        descriptor: int | None = None
        try:
            for part in relative.parts[:-1]:
                child = os.open(
                    part,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory,
                )
                os.close(directory)
                directory = child
            descriptor = os.open(relative.name, READ_FLAGS, dir_fd=directory)
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            raise ServiceError("ARTIFACT_UNAVAILABLE", status=404) from exc
        finally:
            os.close(directory)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                raise ServiceError("ARTIFACT_UNAVAILABLE", status=404)
            data = b""
            while len(data) <= limit:
                chunk = os.read(descriptor, min(1024 * 1024, limit + 1 - len(data)))
                if not chunk:
                    break
                data += chunk
            after = os.fstat(descriptor)
            signature = lambda item: (
                item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns, item.st_nlink
            )
            if len(data) > limit or len(data) != after.st_size or signature(before) != signature(after):
                raise ServiceError("ARTIFACT_UNAVAILABLE", status=404)
            return data, after
        finally:
            os.close(descriptor)
