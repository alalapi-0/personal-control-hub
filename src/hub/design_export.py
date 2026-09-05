"""Verified, self-contained exports of one selected design candidate."""
from __future__ import annotations

import copy
import errno
import hashlib
import json
import os
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .design_records import (
    SCHEMA_VERSION,
    DesignRecordError,
    canonical_bytes,
    content_hash,
    validate_decision_event,
    validate_fact,
)

MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_BUNDLE_MATERIAL_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_BUNDLE_BYTES = MAX_BUNDLE_MATERIAL_BYTES + MAX_MANIFEST_BYTES + 1024 * 1024


class DesignExportError(RuntimeError):
    """The requested export cannot be produced without weakening its bindings."""


ExportError = DesignExportError


def _validate_fact_for_export(record: dict[str, Any], label: str) -> dict[str, Any]:
    try:
        return validate_fact(copy.deepcopy(record))
    except DesignRecordError as exc:
        raise DesignExportError(f"invalid {label}: {exc}") from exc


def _validate_event_for_export(record: dict[str, Any], fixture: bool) -> dict[str, Any]:
    try:
        return validate_decision_event(copy.deepcopy(record), fixture_store=fixture)
    except DesignRecordError as exc:
        raise DesignExportError(f"invalid decision history: {exc}") from exc


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _relative_path(value: str | Path, label: str) -> Path:
    path = Path(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise DesignExportError(f"{label} must be a normalized Hub-relative path without traversal")
    return path


def _reject_symlinks(root: Path, target: Path, *, include_target: bool) -> None:
    """Reject every existing symlink from root through target."""
    if not _inside(target, root):
        raise DesignExportError(f"path escapes Hub root: {target}")
    current = root
    parts = target.relative_to(root).parts
    limit = len(parts) if include_target else max(0, len(parts) - 1)
    for part in parts[:limit]:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise DesignExportError(f"symlink is forbidden in export path: {current}")


def _mkdirs_without_symlinks(root: Path, directory: Path) -> None:
    if not _inside(directory, root):
        raise DesignExportError(f"directory escapes Hub root: {directory}")
    current = root
    for part in directory.relative_to(root).parts:
        current = current / part
        try:
            current.mkdir()
        except FileExistsError:
            pass
        mode = current.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise DesignExportError(f"export parent is not a real directory: {current}")


def _resolve_output(hub_root: Path, output_path: str | Path, fixture: bool) -> Path:
    raw = Path(output_path)
    if any(part in {".", ".."} for part in raw.parts):
        raise DesignExportError("output path must not contain traversal")
    output = raw if raw.is_absolute() else hub_root / _relative_path(raw, "output path")
    output = output.absolute()
    reports = hub_root / "docs" / "reports" / "ui_design_governance"
    if not _inside(output, reports):
        raise DesignExportError("output must be in the Hub UI-design-governance reports tree")
    relative = output.relative_to(reports)
    if output.suffix.lower() != ".zip":
        raise DesignExportError("design exports must use a .zip filename")
    expected_class = "synthetic_fixture" if fixture else "real"
    parts = relative.parts
    try:
        marker = parts.index("exports")
    except ValueError as exc:
        raise DesignExportError("output must be in a task exports subtree") from exc
    if marker < 1 or marker + 1 >= len(parts) or parts[marker + 1] != expected_class:
        raise DesignExportError(f"output classification directory must be exports/{expected_class}")
    if marker + 2 != len(parts) - 1:
        raise DesignExportError("output must be a direct file in its classified exports directory")
    _reject_symlinks(hub_root, output, include_target=True)
    return output


def _resolve_store_path(hub_root: Path, store_path: str | Path) -> Path:
    raw = Path(store_path)
    if any(part in {".", ".."} for part in raw.parts):
        raise DesignExportError("store path must not contain traversal")
    path = raw if raw.is_absolute() else hub_root / _relative_path(raw, "store path")
    path = path.absolute()
    if not _inside(path, hub_root):
        raise DesignExportError("store path escapes Hub root")
    _reject_symlinks(hub_root, path, include_target=True)
    return path


def _read_material(path: Path, *, artifact_id: str, limit: int = MAX_ARTIFACT_BYTES) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if exc.errno in {errno.ENOENT, errno.ELOOP}:
            raise DesignExportError(f"artifact missing or symlinked: {artifact_id}") from exc
        raise DesignExportError(f"cannot open artifact {artifact_id}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise DesignExportError(f"artifact is not a regular file: {artifact_id}")
        if before.st_size > limit:
            raise DesignExportError(f"artifact exceeds per-file export budget: {artifact_id}")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > limit:
            raise DesignExportError(f"artifact exceeds per-file export budget: {artifact_id}")
        after = os.fstat(descriptor)
        signature = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if signature(before) != signature(after) or len(data) != after.st_size:
            raise DesignExportError(f"artifact changed while being exported: {artifact_id}")
        return data, after
    finally:
        os.close(descriptor)


def _classification_allowed(classification: str, fixture: bool) -> bool:
    return classification in ({"mock", "dry-run"} if fixture else {"real", "imported"})


def _artifact_scope_matches_baseline(artifact: dict[str, Any], binding: dict[str, Any]) -> bool:
    scope = artifact["scope"]
    if scope["family_id"] is not None or len(scope["members"]) != 1:
        return False
    member = scope["members"][0]
    return member["project_id"] == binding["project_id"] and set(member["pages"]).issubset(binding["pages"])


def _members_covered(child: dict[str, Any], parent: dict[str, Any]) -> bool:
    parent_members = {member["project_id"]: set(member["pages"]) for member in parent["members"]}
    return all(
        member["project_id"] in parent_members
        and set(member["pages"]).issubset(parent_members[member["project_id"]])
        for member in child["members"]
    )


def export_bundle(
    *,
    hub_root: str | Path,
    store_path: str | Path,
    fixture: bool,
    store: dict[str, Any],
    projection: dict[str, Any],
    candidate_id: str,
    candidate_revision: int,
    output_path: str | Path,
) -> dict[str, Any]:
    """Write one create-only material ZIP from the supplied validated snapshot."""
    lexical_root = Path(hub_root).absolute()
    root = lexical_root.resolve(strict=True)
    if not root.is_dir():
        raise DesignExportError("Hub root is not a directory")
    raw_output = Path(output_path)
    if raw_output.is_absolute() and _inside(raw_output, lexical_root):
        raw_output = root / raw_output.relative_to(lexical_root)
    raw_store = Path(store_path)
    if raw_store.is_absolute() and _inside(raw_store, lexical_root):
        raw_store = root / raw_store.relative_to(lexical_root)
    output = _resolve_output(root, raw_output, fixture)
    source_store = _resolve_store_path(root, raw_store)
    lock_path = source_store.with_name(source_store.name + ".lock")
    expected_store_class = "synthetic_fixture" if fixture else "real"
    if store.get("store_classification") != expected_store_class:
        raise DesignExportError("store classification does not match export classification")
    if projection.get("store_revision") != store.get("revision"):
        raise DesignExportError("projection revision does not match the supplied store snapshot")
    projection_class = projection.get("store_classification")
    if projection_class is not None and projection_class != expected_store_class:
        raise DesignExportError("projection classification does not match export classification")

    facts = store.get("facts")
    events = store.get("events")
    if not isinstance(facts, list) or not isinstance(events, list):
        raise DesignExportError("store snapshot collections are invalid")
    candidates = [f for f in facts if isinstance(f, dict) and f.get("kind") == "candidate" and f.get("id") == candidate_id and f.get("revision") == candidate_revision]
    if len(candidates) != 1:
        raise DesignExportError("candidate does not exist exactly once in the supplied snapshot")
    candidate = _validate_fact_for_export(candidates[0], "candidate")
    if not _classification_allowed(candidate["classification"], fixture):
        raise DesignExportError("candidate classification does not match bundle classification")
    graph_classification = candidate["classification"]

    candidate_ref = {"id": candidate["id"], "revision": candidate["revision"], "content_hash": candidate["content_hash"]}
    effective = projection.get("effective")
    history = projection.get("history")
    if not isinstance(effective, dict) or not isinstance(history, list):
        raise DesignExportError("projection is missing effective decisions or history")
    selected = []
    for item in effective.values():
        if not isinstance(item, dict) or not isinstance(item.get("event"), dict):
            raise DesignExportError("projection contains an invalid effective item")
        event = item["event"]
        if event.get("candidate") == candidate_ref and event.get("action") == "select":
            if item.get("stale") or item.get("superseded"):
                continue
            if event.get("scope") != candidate["scope"]:
                raise DesignExportError("selection scope does not match candidate scope")
            selected.append(copy.deepcopy(item))
    if len(selected) > 1:
        raise DesignExportError("candidate has contradictory effective selections")
    selection = selected[0] if selected else None
    if selection is not None:
        if selection["event"].get("source", {}).get("fixture") is not fixture:
            raise DesignExportError("selection provenance does not match bundle classification")
        if fixture and selection["event"].get("source", {}).get("trusted_owner"):
            raise DesignExportError("synthetic fixture selection cannot carry owner authority")

    same_id_candidates = [f for f in facts if isinstance(f, dict) and f.get("kind") == "candidate" and f.get("id") == candidate_id]
    latest_candidate = max(same_id_candidates, key=lambda item: item.get("revision", -1))
    if (
        latest_candidate.get("revision"), latest_candidate.get("content_hash")
    ) != (candidate_revision, candidate["content_hash"]):
        raise DesignExportError("candidate revision is stale")

    family = None
    family_binding = candidate.get("family_binding")
    family_id = candidate["scope"]["family_id"]
    if family_id is not None:
        if not isinstance(family_binding, dict):
            raise DesignExportError("family candidate is missing its exact family binding")
        family_matches = [
            f for f in facts
            if isinstance(f, dict)
            and f.get("kind") == "design_family"
            and f.get("id") == family_binding.get("id")
            and f.get("revision") == family_binding.get("revision")
        ]
        if len(family_matches) != 1:
            raise DesignExportError("bound design family does not exist exactly once")
        family = _validate_fact_for_export(family_matches[0], "bound design family")
        family_ref = {"id": family["id"], "revision": family["revision"], "content_hash": family["content_hash"]}
        if (
            family_binding != family_ref
            or family_id != family["id"]
            or not _members_covered(candidate["scope"], family["scope"])
        ):
            raise DesignExportError("bound design family identity, hash, or scope mismatch")
        if family["classification"] != graph_classification:
            raise DesignExportError("design family classification does not match candidate graph")
        same_id_families = [
            f for f in facts
            if isinstance(f, dict) and f.get("kind") == "design_family" and f.get("id") == family_id
        ]
        latest_family = max(same_id_families, key=lambda item: item.get("revision", -1), default=None)
        if latest_family is None or (
            latest_family.get("revision"), latest_family.get("content_hash")
        ) != (family["revision"], family["content_hash"]):
            raise DesignExportError("bound design family is stale")
    elif family_binding is not None:
        raise DesignExportError("independent candidate cannot carry a family binding")

    baseline_index = {(f.get("id"), f.get("revision")): f for f in facts if isinstance(f, dict) and f.get("kind") == "baseline"}
    baselines = []
    baseline_binding_by_artifact: dict[str, dict[str, Any]] = {}
    latest_baseline_by_page: dict[tuple[str, str], dict[str, Any]] = {}
    for fact in facts:
        if not isinstance(fact, dict) or fact.get("kind") != "baseline":
            continue
        for page in fact.get("scope", {}).get("pages", []):
            latest_baseline_by_page[(fact.get("project_id"), page)] = fact
    for binding in candidate["baseline_bindings"]:
        baseline = baseline_index.get((binding["baseline_id"], binding["baseline_revision"]))
        if baseline is None:
            raise DesignExportError(f"bound baseline is missing: {binding['baseline_id']}")
        baseline = _validate_fact_for_export(baseline, "bound baseline")
        if baseline["project_id"] != binding["project_id"] or baseline["content_hash"] != binding["baseline_hash"]:
            raise DesignExportError(f"bound baseline identity/hash mismatch: {binding['baseline_id']}")
        if not set(binding["pages"]).issubset(set(baseline["scope"]["pages"])):
            raise DesignExportError(f"bound baseline page scope mismatch: {binding['baseline_id']}")
        if baseline["classification"] != graph_classification:
            raise DesignExportError("baseline classification does not match candidate graph")
        for page in binding["pages"]:
            latest = latest_baseline_by_page.get((binding["project_id"], page))
            if latest is None or (latest.get("id"), latest.get("revision"), latest.get("content_hash")) != (baseline["id"], baseline["revision"], baseline["content_hash"]):
                raise DesignExportError(f"bound baseline is stale for {binding['project_id']}:{page}")
        for artifact_binding in baseline["artifact_bindings"]:
            previous = baseline_binding_by_artifact.get(artifact_binding["artifact_id"])
            if previous is not None and previous != binding:
                raise DesignExportError(f"baseline artifact is ambiguously bound: {artifact_binding['artifact_id']}")
            baseline_binding_by_artifact[artifact_binding["artifact_id"]] = binding
        baselines.append(baseline)

    reviews = []
    for fact in facts:
        if isinstance(fact, dict) and fact.get("kind") == "review" and fact.get("candidate") == candidate_ref:
            reviews.append(_validate_fact_for_export(fact, "candidate review"))

    related_history = []
    event_by_id = {event.get("id"): event for event in events if isinstance(event, dict)}
    for item in history:
        if not isinstance(item, dict) or not isinstance(item.get("event"), dict):
            raise DesignExportError("projection history contains an invalid item")
        event = item["event"]
        if event.get("candidate") == candidate_ref and event.get("scope") == candidate["scope"]:
            validated = _validate_event_for_export(event, fixture)
            if event_by_id.get(validated["id"]) != event:
                raise DesignExportError("projection history is not bound to the supplied event snapshot")
            related_history.append(copy.deepcopy(item))
    if selection is not None and not any(item["event"]["id"] == selection["event"]["id"] for item in related_history):
        raise DesignExportError("selection is missing from related decision history")
    artifact_index = {f.get("id"): f for f in facts if isinstance(f, dict) and f.get("kind") == "artifact_ref"}
    required: dict[str, dict[str, Any]] = {}

    def require_artifact(artifact_id: str, *, digest: str | None, role: str, scope_mode: str) -> None:
        entry = required.setdefault(artifact_id, {"digest": digest, "roles": [], "scope_modes": set()})
        if digest is not None and entry["digest"] is not None and entry["digest"] != digest:
            raise DesignExportError(f"conflicting artifact digest: {artifact_id}")
        if digest is not None:
            entry["digest"] = digest
        entry["roles"].append(role)
        entry["scope_modes"].add(scope_mode)

    for baseline in baselines:
        for binding in baseline["artifact_bindings"]:
            artifact_id = binding["artifact_id"]
            artifact = artifact_index.get(artifact_id)
            if artifact is None or artifact.get("sha256") != binding["sha256"]:
                raise DesignExportError(f"baseline artifact digest mismatch: {artifact_id}")
            require_artifact(artifact_id, digest=binding["sha256"], role=f"baseline:{baseline['id']}", scope_mode="baseline")
    for binding in candidate["artifact_bindings"]:
        require_artifact(binding["artifact_id"], digest=binding["sha256"], role="candidate_material", scope_mode="candidate_material")
    for artifact_id in candidate["evidence_refs"]:
        require_artifact(artifact_id, digest=None, role="candidate_evidence", scope_mode="candidate_evidence")
    for review in reviews:
        for artifact_id in review["evidence_refs"]:
            require_artifact(artifact_id, digest=None, role=f"review:{review['id']}", scope_mode="candidate_evidence")
        for lane in review["lanes"]:
            for artifact_id in lane["evidence_refs"]:
                require_artifact(artifact_id, digest=None, role=f"review:{review['id']}:{lane['name']}", scope_mode="candidate_evidence")
    if family is not None:
        for artifact_id in family["source"]["evidence_refs"]:
            require_artifact(artifact_id, digest=None, role=f"family:{family['id']}", scope_mode="family_evidence")

    artifact_records = []
    material: list[tuple[str, bytes, str]] = []
    total_size = 0
    seen_files: set[tuple[int, int]] = set()
    protected_files: set[tuple[int, int]] = set()
    for protected in (source_store, lock_path):
        try:
            protected_stat = protected.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(protected_stat.st_mode):
            raise DesignExportError(f"protected store path is symlinked: {protected}")
        if stat.S_ISREG(protected_stat.st_mode):
            protected_files.add((protected_stat.st_dev, protected_stat.st_ino))
    material_roots = (root / "data" / "design_governance", root / "docs" / "reports" / "ui_design_governance")
    figma_refs = []
    if any(candidate["figma_ref"].get(key) is not None for key in ("file_key", "node_id", "version")):
        figma_refs.append({"kind": "candidate_figma_ref", "offline_only": True, "value": copy.deepcopy(candidate["figma_ref"])})
    for artifact_id, requirement in sorted(required.items()):
        raw_artifact = artifact_index.get(artifact_id)
        if raw_artifact is None:
            raise DesignExportError(f"artifact record is missing: {artifact_id}")
        artifact = _validate_fact_for_export(raw_artifact, f"artifact {artifact_id}")
        if artifact["classification"] != graph_classification:
            raise DesignExportError(f"artifact classification does not match candidate graph: {artifact_id}")
        scope_modes = requirement["scope_modes"]
        if "baseline" in scope_modes and len(scope_modes) != 1:
            raise DesignExportError(f"artifact mixes baseline and candidate/family scope roles: {artifact_id}")
        if "baseline" in scope_modes:
            binding = baseline_binding_by_artifact[artifact_id]
            if not _artifact_scope_matches_baseline(artifact, binding):
                raise DesignExportError(f"baseline artifact scope does not match bound project/pages: {artifact_id}")
        else:
            if "candidate_material" in scope_modes and artifact["scope"] != candidate["scope"]:
                raise DesignExportError(f"artifact scope does not match full candidate scope: {artifact_id}")
            if "candidate_evidence" in scope_modes and (
                artifact["scope"]["family_id"] != candidate["scope"]["family_id"]
                or not _members_covered(artifact["scope"], candidate["scope"])
            ):
                raise DesignExportError(f"candidate/review evidence scope is not covered by candidate: {artifact_id}")
            if "family_evidence" in scope_modes and not _members_covered(artifact["scope"], family["scope"]):
                raise DesignExportError(f"family evidence scope is not covered by family: {artifact_id}")
            if artifact["scope"]["family_id"] is not None and family is not None and artifact.get("family_binding") != family_binding:
                raise DesignExportError(f"artifact family binding does not match candidate: {artifact_id}")
        location = artifact["location"]
        if location["kind"] == "figma":
            if requirement["digest"] is not None:
                raise DesignExportError(f"material binding cannot resolve to a Figma pointer: {artifact_id}")
            figma_refs.append({"kind": "artifact_ref", "offline_only": True, "roles": requirement["roles"], "value": artifact})
            continue
        bound_digest = requirement["digest"] if requirement["digest"] is not None else artifact["sha256"]
        if artifact["sha256"] != bound_digest:
            raise DesignExportError(f"referenced artifact digest mismatch: {artifact_id}")
        relative = _relative_path(location["value"], f"artifact {artifact_id} location")
        source = (root / relative).absolute()
        if not any(_inside(source, allowed) for allowed in material_roots):
            raise DesignExportError(f"artifact is outside Hub task material roots: {artifact_id}")
        _reject_symlinks(root, source, include_target=True)
        if source in {output, source_store, lock_path}:
            raise DesignExportError(f"artifact collides with protected export/store path: {artifact_id}")
        data, source_stat = _read_material(source, artifact_id=artifact_id)
        identity = (source_stat.st_dev, source_stat.st_ino)
        if identity in protected_files:
            raise DesignExportError(f"artifact collides with store or lock material: {artifact_id}")
        if identity in seen_files:
            raise DesignExportError(f"multiple artifact records resolve to the same source file: {artifact_id}")
        seen_files.add(identity)
        digest = hashlib.sha256(data).hexdigest()
        if digest != bound_digest:
            raise DesignExportError(f"artifact hash mismatch: {artifact_id}")
        total_size += len(data)
        if total_size > MAX_BUNDLE_MATERIAL_BYTES:
            raise DesignExportError("bundle exceeds material export budget")
        archive_path = f"artifacts/{artifact_id}/{source.name}"
        material.append((archive_path, data, digest))
        artifact_records.append({"record": artifact, "roles": requirement["roles"], "archive_path": archive_path, "size": len(data), "sha256": digest})

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": "design_export_bundle",
        "bundle_classification": expected_store_class,
        "authority": {"real_selection": selection is not None and not fixture, "implementation_authority": False, "fixture": fixture},
        "store": {"path": str(source_store.relative_to(root)), "revision": store["revision"], "classification": expected_store_class},
        "candidate_identity": {**candidate_ref, "scope": copy.deepcopy(candidate["scope"])},
        "candidate": copy.deepcopy(candidate),
        "bound_family": copy.deepcopy(family),
        "bound_baselines": sorted(baselines, key=lambda item: (item["project_id"], item["id"], item["revision"])),
        "reviews": sorted(reviews, key=lambda item: (item["created_at"], item["id"])),
        "selection_state": "selected" if selection is not None else "unselected",
        "selection": selection,
        "decision_history": related_history,
        "history_hash": content_hash(related_history),
        "artifact_files": artifact_records,
        "figma_references": figma_refs,
        "provenance": {"snapshot_only": True, "offline": True, "external_business_data": False},
    }
    manifest_bytes = canonical_bytes(manifest) + b"\n"
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise DesignExportError("manifest exceeds export budget")

    _mkdirs_without_symlinks(root, output.parent)
    _reject_symlinks(root, output, include_target=True)
    if output.exists() or output.is_symlink():
        raise DesignExportError("export destination already exists")
    temp_name: str | None = None
    published = False
    durability_error: OSError | None = None
    cleanup_error: OSError | None = None
    bundle_digest: str | None = None
    bundle_size: int | None = None
    try:
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
        os.close(descriptor)
        with zipfile.ZipFile(temp_name, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            bundle.writestr("manifest.json", manifest_bytes)
            for archive_path, data, _digest in material:
                bundle.writestr(archive_path, data)
        with open(temp_name, "rb") as handle:
            os.fsync(handle.fileno())
        with zipfile.ZipFile(temp_name, "r") as bundle:
            if bundle.testzip() is not None:
                raise DesignExportError("generated export bundle failed ZIP integrity verification")
        staged_bytes = _read_material(Path(temp_name), artifact_id="staged-export-bundle", limit=MAX_BUNDLE_BYTES)[0]
        bundle_digest = hashlib.sha256(staged_bytes).hexdigest()
        bundle_size = len(staged_bytes)
        if os.environ.get("HUB_DESIGN_EXPORT_FAIL_BEFORE_PUBLISH") == "1":
            raise DesignExportError("injected failure before export publication")
        try:
            os.link(temp_name, output, follow_symlinks=False)
            published = True
        except FileExistsError as exc:
            raise DesignExportError("export destination already exists") from exc
        try:
            directory_fd = os.open(output.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        except OSError as exc:
            durability_error = exc
        else:
            try:
                try:
                    os.fsync(directory_fd)
                except OSError as exc:
                    durability_error = exc
            finally:
                try:
                    os.close(directory_fd)
                except OSError as exc:
                    durability_error = durability_error or exc
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            except OSError as exc:
                cleanup_error = exc
    if not published or bundle_digest is None or bundle_size is None:
        raise DesignExportError("export was not published")
    try:
        published_bytes = _read_material(output, artifact_id="published-export-bundle", limit=MAX_BUNDLE_BYTES)[0]
    except DesignExportError as exc:
        return {
            "outcome": "COMMITTED_VERIFICATION_FAILED",
            "path": str(output),
            "expected_sha256": bundle_digest,
            "sha256": None,
            "store_revision": store["revision"],
            "warnings": [str(exc)] + ([f"temporary-file cleanup failed: {cleanup_error}"] if cleanup_error else []),
            "manifest": manifest,
        }
    published_digest = hashlib.sha256(published_bytes).hexdigest()
    if len(published_bytes) != bundle_size or published_digest != bundle_digest:
        return {
            "outcome": "COMMITTED_VERIFICATION_FAILED",
            "path": str(output),
            "expected_sha256": bundle_digest,
            "sha256": published_digest,
            "store_revision": store["revision"],
            "warnings": ["published export bytes do not match the verified staged bundle"] + ([f"temporary-file cleanup failed: {cleanup_error}"] if cleanup_error else []),
            "manifest": manifest,
        }
    outcome = "COMMITTED_DURABILITY_UNCONFIRMED" if durability_error is not None else "COMMITTED"
    warnings = []
    if durability_error is not None:
        warnings.append(f"directory durability could not be confirmed: {durability_error}")
    if cleanup_error is not None:
        warnings.append(f"temporary-file cleanup failed: {cleanup_error}")
    return {
        "outcome": outcome,
        "path": str(output),
        "sha256": published_digest,
        "store_revision": store["revision"],
        "warnings": warnings,
        "manifest": manifest,
    }
