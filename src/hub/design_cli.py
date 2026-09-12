"""Command line access to the local design-governance fact store."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .design_records import SCHEMA_VERSION, DesignRecordError, with_content_hash
from .design_store import CommittedDurabilityUnconfirmed, CommittedVerificationFailed, DesignStore, StoreError, content_hash_bytes
from .design_export import DesignExportError

FIXTURE_TIME = "2026-09-05T12:00:00+08:00"


def _write(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _scope() -> dict[str, Any]:
    return {"family_id": None, "members": [{"project_id": "fixture-project", "pages": ["review"]}]}


def _artifact(artifact_id: str, relative: str, digest: str) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "kind": "artifact_ref", "id": artifact_id, "created_at": FIXTURE_TIME, "classification": "mock", "location": {"kind": "hub_relative", "value": relative}, "sha256": digest, "provenance": {"method": "deterministic synthetic fixture", "source_refs": ["fixture://tc2-demo"]}, "scope": _scope()}


def _baseline(revision: int, artifact: dict[str, Any] | None = None) -> dict[str, Any]:
    record = {"schema_version": SCHEMA_VERSION, "kind": "baseline", "id": "fixture-baseline", "created_at": FIXTURE_TIME, "classification": "mock", "project_id": "fixture-project", "revision": revision, "content_hash": "", "source": {"kind": "new_surface_spec", "reference": f"fixture://baseline/{revision}", "commit": None, "dirty_fingerprint": None, "observed_at": FIXTURE_TIME}, "scope": {"pages": ["review"], "flows": ["compare-and-choose"], "viewport": {"width": 390, "height": 844, "platform": "mobile"}}, "behaviors": [], "data_contract_refs": ["fixture://contract"], "unverified": ["synthetic fixture has no live UI"], "artifact_bindings": [{"artifact_id": artifact["id"], "sha256": artifact["sha256"]}] if artifact else []}
    return with_content_hash(record)


def _candidate(revision: int, baseline: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    record = {"schema_version": SCHEMA_VERSION, "kind": "candidate", "id": "fixture-candidate", "created_at": FIXTURE_TIME, "classification": "mock", "revision": revision, "content_hash": "", "scope": _scope(), "baseline_bindings": [{"project_id": "fixture-project", "baseline_id": baseline["id"], "baseline_revision": baseline["revision"], "baseline_hash": baseline["content_hash"], "pages": ["review"]}], "visual": {"tokens": [f"fixture-revision-{revision}"], "components": ["comparison-canvas"], "structure": ["single-canvas-mobile"], "differences": [f"synthetic revision {revision}"]}, "figma_ref": {"file_key": "offline-fixture", "node_id": f"fixture-node-{revision}", "version": str(revision), "offline": True}, "artifact_bindings": [{"artifact_id": artifact["id"], "sha256": artifact["sha256"]}], "evidence_refs": []}
    return with_content_hash(record)


def _event(event_id: str, request_id: str, action: str, candidate: dict[str, Any], *, feedback: str | None, supersedes: str | None = None) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "kind": "decision_event", "id": event_id, "request_id": request_id, "created_at": FIXTURE_TIME, "source": {"type": "synthetic_fixture", "reference": "fixture://tc2-demo", "trusted_owner": False, "fixture": True}, "action": action, "candidate": {"id": candidate["id"], "revision": candidate["revision"], "content_hash": candidate["content_hash"]}, "scope": _scope(), "feedback": feedback, "supersedes": supersedes}


def run_demo(root: Path, output_dir: Path) -> dict[str, Any]:
    root = root.resolve(); output_dir = output_dir if output_dir.is_absolute() else root / output_dir
    if output_dir.exists(): raise FileExistsError(f"demo output directory must not already exist: {output_dir}")
    store = DesignStore(root, output_dir / "fixture-store.json", fixture=True)
    output_dir.mkdir(parents=True)
    state = store.initialize(); revision = state["revision"]
    baseline_path = output_dir / "baseline-preview.txt"; candidate_path = output_dir / "candidate-preview.txt"
    baseline_path.write_bytes(b"TC2 deterministic synthetic baseline preview\n")
    candidate_path.write_bytes(b"TC2 deterministic synthetic candidate preview\n")
    records = []
    baseline_artifact = _artifact("fixture-baseline-preview", str(baseline_path.resolve().relative_to(root)), content_hash_bytes(baseline_path.read_bytes())); records.append((baseline_artifact, "fixture-request-baseline-artifact"))
    candidate_artifact = _artifact("fixture-candidate-preview", str(candidate_path.resolve().relative_to(root)), content_hash_bytes(candidate_path.read_bytes())); records.append((candidate_artifact, "fixture-request-candidate-artifact"))
    baseline1 = _baseline(1, baseline_artifact); records.append((baseline1, "fixture-request-baseline-1"))
    candidate1 = _candidate(1, baseline1, candidate_artifact); records.append((candidate1, "fixture-request-candidate-1"))
    for record, request_id in records:
        state, _ = store.append_fact(record, expected_revision=revision, request_id=request_id); revision = state["revision"]
    feedback = _event("fixture-feedback-1", "fixture-request-feedback-1", "request_changes", candidate1, feedback="Synthetic spacing feedback")
    state, _ = store.append_decision(feedback, expected_revision=revision); revision = state["revision"]
    select1 = _event("fixture-select-1", "fixture-request-select-1", "select", candidate1, feedback="Synthetic selection", supersedes=feedback["id"])
    state, _ = store.append_decision(select1, expected_revision=revision); revision = state["revision"]
    restarted = DesignStore(root, store.path, fixture=True)
    before_drift = restarted.projection()
    exported = restarted.export(candidate1["id"], candidate1["revision"], output_dir / "exports/synthetic_fixture/selected-candidate.zip")
    if exported["outcome"] != "COMMITTED":
        return {"classification": "synthetic_fixture", "outcome": exported["outcome"], "stage": "export", "store": str(store.path), "store_revision": revision, "export": exported}
    baseline2 = _baseline(2, baseline_artifact)
    state, _ = restarted.append_fact(baseline2, expected_revision=revision, request_id="fixture-request-baseline-2"); revision = state["revision"]
    after_drift = restarted.projection()
    candidate2 = _candidate(2, baseline2, candidate_artifact)
    state, _ = restarted.append_fact(candidate2, expected_revision=revision, request_id="fixture-request-candidate-2"); revision = state["revision"]
    select2 = _event("fixture-select-2", "fixture-request-select-2", "select", candidate2, feedback="Synthetic reselect", supersedes=select1["id"])
    state, _ = restarted.append_decision(select2, expected_revision=revision); revision = state["revision"]
    withdraw = _event("fixture-withdraw-2", "fixture-request-withdraw-2", "withdraw", candidate2, feedback="Synthetic withdrawal", supersedes=select2["id"])
    state, _ = restarted.append_decision(withdraw, expected_revision=revision)
    final = restarted.projection()
    return {"classification": "synthetic_fixture", "store": str(store.path), "store_revision": state["revision"], "export": {"path": exported["path"], "sha256": exported["sha256"]}, "before_drift_selected": len(before_drift["queues"]["selected"]), "after_drift_stale": len(after_drift["queues"]["stale"]), "final_queue_counts": {key: len(value) for key, value in final["queues"].items()}, "history_event_ids": [item["event"]["id"] for item in final["history"]]}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("--root", type=Path, default=Path.cwd())
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("validate", "read", "history"):
        command = sub.add_parser(name); command.add_argument("--store", type=Path, required=True); command.add_argument("--fixture", action="store_true")
    export = sub.add_parser("export"); export.add_argument("--store", type=Path, required=True); export.add_argument("--fixture", action="store_true"); export.add_argument("--candidate", required=True); export.add_argument("--revision", type=int, required=True); export.add_argument("--output", type=Path, required=True)
    demo = sub.add_parser("demo"); demo.add_argument("--output-dir", type=Path, required=True)
    return p


def _main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "demo":
        result = run_demo(args.root, args.output_dir)
        _write(result)
        return 0 if result.get("outcome", "COMMITTED") == "COMMITTED" else 3
    store = DesignStore(args.root, args.store, fixture=args.fixture)
    if args.command == "validate": data = store.read(); _write({"valid": True, "revision": data["revision"], "classification": data["store_classification"]})
    elif args.command == "read": _write(store.read())
    elif args.command == "history": _write(store.projection())
    elif args.command == "export":
        result = store.export(args.candidate, args.revision, args.output)
        _write(result)
        return 0 if result["outcome"] == "COMMITTED" else 3
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except (CommittedDurabilityUnconfirmed, CommittedVerificationFailed) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc), **exc.as_dict()}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 3
    except (DesignRecordError, DesignExportError, StoreError, OSError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__": raise SystemExit(main())
