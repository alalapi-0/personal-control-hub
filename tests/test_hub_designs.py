from __future__ import annotations

import copy
import contextlib
import io
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hub.design_cli import _artifact, _baseline, _candidate, _event
from hub.design_records import DesignRecordError, content_hash, validate_fact, with_content_hash
from hub.design_store import CommittedDurabilityUnconfirmed, CommittedVerificationFailed, ConflictError, DesignStore, LockConflict, StoreError, content_hash_bytes


class DesignStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "hub"
        self.work = self.root / "docs/reports/ui_design_governance/fixtures/test"
        self.work.mkdir(parents=True)
        self.store = DesignStore(self.root, self.work / "store.json", fixture=True, lock_timeout=0.1)
        self.revision = self.store.initialize()["revision"]
        self.preview = self.work / "preview.txt"; self.preview.write_bytes(b"mock preview\n")
        self.artifact = _artifact("fixture-preview", str(self.preview.relative_to(self.root)), content_hash_bytes(self.preview.read_bytes()))
        self.baseline1 = _baseline(1)
        self.candidate1 = _candidate(1, self.baseline1, self.artifact)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add(self, fact: dict, request: str) -> None:
        state, _ = self.store.append_fact(fact, expected_revision=self.revision, request_id=request)
        self.revision = state["revision"]

    def facts(self) -> None:
        self.add(self.artifact, "fixture-add-artifact")
        self.add(self.baseline1, "fixture-add-baseline-1")
        self.add(self.candidate1, "fixture-add-candidate-1")

    def event(self, event: dict) -> dict:
        state, receipt = self.store.append_decision(event, expected_revision=self.revision)
        self.revision = state["revision"]
        return receipt

    def test_closed_loop_restart_stale_reselect_withdraw_preserves_history(self) -> None:
        self.facts()
        feedback = _event("fixture-feedback", "fixture-request-feedback", "request_changes", self.candidate1, feedback="Mock feedback")
        self.event(feedback)
        select1 = _event("fixture-select-1", "fixture-request-select-1", "select", self.candidate1, feedback=None, supersedes=feedback["id"])
        self.event(select1)
        restarted = DesignStore(self.root, self.store.path, fixture=True)
        before = restarted.projection()
        self.assertEqual("synthetic_fixture", before["store_classification"])
        self.assertEqual(0, before["real_selection_count"])
        self.assertEqual(1, len(before["queues"]["selected"]))
        baseline2 = _baseline(2); self.add(baseline2, "fixture-add-baseline-2")
        after = restarted.projection()
        self.assertEqual(1, len(after["queues"]["stale"]))
        self.assertIn("baseline_drift:fixture-project:review", after["queues"]["stale"][0]["stale_reasons"])
        candidate2 = _candidate(2, baseline2, self.artifact); self.add(candidate2, "fixture-add-candidate-2")
        select2 = _event("fixture-select-2", "fixture-request-select-2", "select", candidate2, feedback="Mock reselect", supersedes=select1["id"])
        self.event(select2)
        withdraw = _event("fixture-withdraw", "fixture-request-withdraw", "withdraw", candidate2, feedback="Mock withdraw", supersedes=select2["id"])
        self.event(withdraw)
        final = restarted.projection()
        self.assertEqual(["fixture-feedback", "fixture-select-1", "fixture-select-2", "fixture-withdraw"], [x["event"]["id"] for x in final["history"]])
        self.assertEqual(1, len(final["queues"]["withdrawn"]))

    def test_unknown_version_and_extra_nested_field_are_rejected(self) -> None:
        bad = copy.deepcopy(self.baseline1); bad["schema_version"] = "9.0"
        with self.assertRaisesRegex(DesignRecordError, "unsupported schema_version"): validate_fact(bad)
        bad = copy.deepcopy(self.candidate1); bad["visual"]["unexpected"] = []
        bad = with_content_hash(bad)
        with self.assertRaisesRegex(DesignRecordError, "fields mismatch"): validate_fact(bad)

    def test_corrupt_store_bad_reference_duplicate_identity_and_hash_change_reject(self) -> None:
        self.facts(); raw = self.store.read()
        corrupt = copy.deepcopy(raw); corrupt["facts"][-1]["baseline_bindings"][0]["baseline_id"] = "missing"
        corrupt["facts"][-1] = with_content_hash(corrupt["facts"][-1])
        with self.assertRaisesRegex(DesignRecordError, "invalid baseline binding"): self.store.validate(corrupt)
        duplicate = copy.deepcopy(raw); duplicate["facts"].append(copy.deepcopy(self.baseline1))
        with self.assertRaisesRegex(DesignRecordError, "duplicate fact identity"): self.store.validate(duplicate)
        changed = copy.deepcopy(self.candidate1); changed["visual"]["tokens"] = ["changed"]
        with self.assertRaisesRegex(DesignRecordError, "content hash mismatch"): validate_fact(changed)

    def test_candidate_binds_artifact_digest_scope_and_baseline_pages(self) -> None:
        self.add(self.artifact, "fixture-artifact"); self.add(self.baseline1, "fixture-baseline")
        bad = copy.deepcopy(self.candidate1); bad["artifact_bindings"][0]["sha256"] = "1" * 64; bad = with_content_hash(bad)
        with self.assertRaisesRegex(DesignRecordError, "artifact digest"): self.store.append_fact(bad, expected_revision=self.revision, request_id="fixture-bad-artifact")
        narrow = copy.deepcopy(self.baseline1); narrow["id"] = "fixture-baseline-narrow"; narrow["scope"]["pages"] = ["other"]; narrow = with_content_hash(narrow); self.add(narrow, "fixture-narrow-baseline")
        bad = copy.deepcopy(self.candidate1); bad["baseline_bindings"][0].update({"baseline_id": narrow["id"], "baseline_hash": narrow["content_hash"]}); bad = with_content_hash(bad)
        with self.assertRaisesRegex(DesignRecordError, "exceed baseline pages"): self.store.append_fact(bad, expected_revision=self.revision, request_id="fixture-bad-pages")

    def test_idempotent_retry_precedes_stale_revision_and_payload_conflict(self) -> None:
        state, first = self.store.append_fact(self.artifact, expected_revision=self.revision, request_id="fixture-retry")
        new_revision = state["revision"]
        state, retry = self.store.append_fact(self.artifact, expected_revision=0, request_id="fixture-retry")
        self.assertEqual(first, retry); self.assertEqual(new_revision, state["revision"])
        changed = copy.deepcopy(self.artifact); changed["provenance"]["method"] = "different"
        with self.assertRaisesRegex(ConflictError, "different payload"):
            self.store.append_fact(changed, expected_revision=new_revision, request_id="fixture-retry")

    def test_expected_revision_failure_and_same_identity_different_request(self) -> None:
        with self.assertRaisesRegex(ConflictError, "expected revision"):
            self.store.append_fact(self.artifact, expected_revision=99, request_id="fixture-stale")
        self.add(self.artifact, "fixture-first")
        changed = copy.deepcopy(self.artifact); changed["provenance"]["method"] = "changed"
        with self.assertRaisesRegex(ConflictError, "different payload"):
            self.store.append_fact(changed, expected_revision=self.revision, request_id="fixture-second")

    def test_failure_before_replace_preserves_old_store_and_cleans_temp(self) -> None:
        old = self.store.path.read_bytes()
        with mock.patch.dict(os.environ, {"HUB_DESIGN_FAIL_BEFORE_REPLACE": "1"}):
            with self.assertRaisesRegex(StoreError, "injected failure"):
                self.store.append_fact(self.artifact, expected_revision=self.revision, request_id="fixture-interrupted")
        self.assertEqual(old, self.store.path.read_bytes())
        self.assertEqual([], list(self.store.path.parent.glob(".store.json.*.tmp")))

    def test_lock_conflict_uses_persistent_nofollow_lock(self) -> None:
        lock = self.store.lock_path.open("r+b"); fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            with self.assertRaises(LockConflict): self.store.append_fact(self.artifact, expected_revision=self.revision, request_id="fixture-lock")
        finally: lock.close()
        self.assertTrue(self.store.lock_path.exists())
        self.store.lock_path.unlink(); self.store.lock_path.symlink_to(self.preview)
        with self.assertRaises(OSError): self.store.append_fact(self.artifact, expected_revision=self.revision, request_id="fixture-lock-symlink")

    def test_real_subprocess_concurrent_writers_have_one_explicit_conflict(self) -> None:
        first = self.work / "one.json"; second = self.work / "two.json"
        first.write_text(json.dumps(self.artifact), encoding="utf-8")
        other = copy.deepcopy(self.artifact); other["id"] = "fixture-preview-two"; first_digest = other["sha256"]
        second.write_text(json.dumps(other), encoding="utf-8")
        code = """import json,sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nfrom hub.design_store import DesignStore\ns=DesignStore(Path(sys.argv[2]),Path(sys.argv[3]),fixture=True)\ntry:\n s.append_fact(json.loads(Path(sys.argv[4]).read_text()),expected_revision=int(sys.argv[5]),request_id=sys.argv[6]); print('ok')\nexcept Exception as e: print(type(e).__name__)\n"""
        args = [sys.executable, "-c", code, str(Path(__file__).resolve().parents[1] / "src"), str(self.root), str(self.store.path)]
        processes = [subprocess.Popen(args + [str(first), str(self.revision), "fixture-process-one"], stdout=subprocess.PIPE, text=True), subprocess.Popen(args + [str(second), str(self.revision), "fixture-process-two"], stdout=subprocess.PIPE, text=True)]
        results = sorted(p.communicate(timeout=10)[0].strip() for p in processes)
        self.assertEqual(["ConflictError", "ok"], results)
        self.store.validate(self.store.read())

    def test_store_and_allowed_root_symlink_escapes_reject(self) -> None:
        outside = Path(self.temp.name) / "outside"; outside.mkdir()
        escaped = self.root / "docs/reports/ui_design_governance/escaped"
        escaped.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(StoreError, "escapes|symlinks"): DesignStore(self.root, escaped / "store.json", fixture=True)
        reports = self.root / "docs/reports/ui_design_governance"; moved = self.root / "reports-real"; reports.rename(moved); reports.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(StoreError, "root escapes|symlinks"): DesignStore(self.root, reports / "store.json", fixture=True)

    def test_real_decision_requires_separate_trusted_reference_and_fixture_never_counts_real(self) -> None:
        self.facts(); event = _event("fixture-select", "fixture-select-request", "select", self.candidate1, feedback=None)
        self.event(event); self.assertEqual(0, self.store.projection()["real_selection_count"])
        real_root = Path(self.temp.name) / "realhub"; (real_root / "data/design_governance").mkdir(parents=True); (real_root / "data/registry").mkdir(parents=True)
        (real_root / "data/registry/external_projects.yaml").write_text("projects:\n  - id: real-project\n", encoding="utf-8")
        real = DesignStore(real_root, real_root / "data/design_governance/store.json"); real_revision = real.initialize()["revision"]
        # Structural source flags cannot supply the separate trusted context required by append_decision.
        real_event = copy.deepcopy(event); real_event["source"] = {"type": "trusted_owner_reference", "reference": "owner://explicit/1", "trusted_owner": True, "fixture": False}
        with self.assertRaisesRegex(StoreError, "separately supplied"): real.append_decision(real_event, expected_revision=real_revision)

    def test_later_same_scope_event_must_supersede_current(self) -> None:
        self.facts(); first = _event("fixture-defer", "fixture-request-defer", "defer", self.candidate1, feedback=None); self.event(first)
        second = _event("fixture-select", "fixture-request-select", "select", self.candidate1, feedback=None)
        with self.assertRaisesRegex(DesignRecordError, "must supersede"):
            self.store.append_decision(second, expected_revision=self.revision)

    def test_receipts_are_bijective_and_initialize_receipt_is_canonical(self) -> None:
        self.add(self.artifact, "fixture-receipt-fact"); raw = self.store.read()
        forged = copy.deepcopy(raw); forged["requests"][0]["operation"] = "forged"
        with self.assertRaisesRegex(DesignRecordError, "unknown operation"): self.store.validate(forged)
        forged = copy.deepcopy(raw); forged["requests"][0]["payload_hash"] = "0" * 64
        with self.assertRaisesRegex(DesignRecordError, "non-canonical"): self.store.validate(forged)
        extra = copy.deepcopy(self.artifact); extra["id"] = "fixture-unreceipted"; forged = copy.deepcopy(raw); forged["facts"].append(extra)
        with self.assertRaisesRegex(DesignRecordError, "bijective"): self.store.validate(forged)
        forged = copy.deepcopy(raw); forged["requests"][1]["result_revision"] = True
        with self.assertRaisesRegex(DesignRecordError, "invalid result revision"): self.store.validate(forged)

    def test_artifact_family_scope_classification_boundaries(self) -> None:
        family = copy.deepcopy(self.artifact); family["scope"]["family_id"] = "fixture-family"
        with self.assertRaisesRegex(DesignRecordError, "family_binding"):
            self.store.append_fact(family, expected_revision=self.revision, request_id="fixture-family-artifact")
        non_mock = copy.deepcopy(self.artifact); non_mock["classification"] = "dry-run"
        self.add(non_mock, "fixture-dry-artifact")
        wrong_scope = copy.deepcopy(self.artifact); wrong_scope["id"] = "fixture-wrong-scope"; wrong_scope["scope"]["members"][0]["pages"] = ["other"]
        self.add(wrong_scope, "fixture-wrong-scope-artifact")
        baseline = copy.deepcopy(self.baseline1); baseline["artifact_bindings"] = [{"artifact_id": wrong_scope["id"], "sha256": wrong_scope["sha256"]}]; baseline = with_content_hash(baseline)
        with self.assertRaisesRegex(DesignRecordError, "artifact scope"):
            self.store.append_fact(baseline, expected_revision=self.revision, request_id="fixture-bad-baseline-artifact")

    def test_post_replace_fsync_failure_is_explicit_committed_and_retry_is_idempotent(self) -> None:
        old_revision = self.revision
        with mock.patch.dict(os.environ, {"HUB_DESIGN_FAIL_DIRECTORY_FSYNC": "1"}):
            with self.assertRaises(CommittedDurabilityUnconfirmed) as raised:
                self.store.append_fact(self.artifact, expected_revision=old_revision, request_id="fixture-durability")
        error = raised.exception
        self.assertEqual("COMMITTED_DURABILITY_UNCONFIRMED", error.status)
        self.assertEqual(old_revision + 1, error.revision)
        self.assertEqual("fixture-durability", error.receipt["request_id"])
        published = self.store.read(); self.assertEqual(error.revision, published["revision"])
        retry_store, retry_receipt = self.store.append_fact(self.artifact, expected_revision=old_revision, request_id="fixture-durability")
        self.assertEqual(published, retry_store); self.assertEqual(error.receipt, retry_receipt)
        self.assertEqual(error.store_sha256, content_hash_bytes(self.store.path.read_bytes()))

    def test_initialize_directory_fsync_failure_publishes_one_canonical_initialize(self) -> None:
        other = DesignStore(self.root, self.work / "initialize-fsync.json", fixture=True)
        with mock.patch.dict(os.environ, {"HUB_DESIGN_FAIL_DIRECTORY_FSYNC": "1"}):
            with self.assertRaises(CommittedDurabilityUnconfirmed) as raised:
                other.initialize()
        self.assertEqual(1, raised.exception.revision)
        first = other.read(); self.assertEqual(1, first["revision"])
        self.assertEqual(first, other.initialize())
        self.assertEqual(1, len(first["requests"]))

    def test_post_replace_verification_failure_reports_committed_identity(self) -> None:
        with mock.patch.dict(os.environ, {"HUB_DESIGN_FAIL_DIRECTORY_FSYNC": "1", "HUB_DESIGN_FAIL_PUBLISHED_VERIFICATION": "1"}):
            with self.assertRaises(CommittedVerificationFailed) as raised:
                self.store.append_fact(self.artifact, expected_revision=self.revision, request_id="fixture-verification-failed")
        error = raised.exception
        self.assertEqual("COMMITTED_VERIFICATION_FAILED", error.status)
        self.assertEqual(self.revision + 1, error.revision)
        self.assertEqual("fixture-verification-failed", error.receipt["request_id"])
        self.assertIsNone(error.observed_store_sha256)
        published = self.store.read(); self.assertEqual(error.revision, published["revision"])
        retry, receipt = self.store.append_fact(self.artifact, expected_revision=self.revision, request_id="fixture-verification-failed")
        self.assertEqual(published, retry); self.assertEqual(error.receipt, receipt)

    def test_duplicate_behavior_action_and_review_lane_ids_reject(self) -> None:
        behavior = {"action_id": "fixture-action", "entry": "entry", "preconditions": [], "input": "input", "output": "output", "transition": "transition", "storage_effects": "none", "external_effects": "none", "recovery": "retry", "test_refs": []}
        baseline = copy.deepcopy(self.baseline1); baseline["behaviors"] = [behavior, copy.deepcopy(behavior)]; baseline = with_content_hash(baseline)
        with self.assertRaisesRegex(DesignRecordError, "duplicate action_id"): validate_fact(baseline)
        candidate_ref = {"id": self.candidate1["id"], "revision": self.candidate1["revision"], "content_hash": self.candidate1["content_hash"]}
        lane = {"name": "visual", "result": "PASS", "reason": "fixture", "evidence_refs": []}
        review = {"schema_version": "1.0", "kind": "review", "id": "fixture-review", "created_at": "2026-09-05T12:00:00+08:00", "candidate": candidate_ref, "functional_invariants": [], "lanes": [lane, copy.deepcopy(lane)], "tool_limits": [], "reviewer": {"type": "agent", "reference": "fixture://reviewer"}, "evidence_refs": []}
        with self.assertRaisesRegex(DesignRecordError, "duplicate lane name"): validate_fact(review)

    def test_dry_run_fixture_graph_is_isolated_and_mixed_graph_rejects(self) -> None:
        dry_store = DesignStore(self.root, self.work / "dry-run.json", fixture=True)
        revision = dry_store.initialize()["revision"]
        artifact = copy.deepcopy(self.artifact); artifact["id"] = "fixture-dry-preview"; artifact["classification"] = "dry-run"
        baseline = copy.deepcopy(self.baseline1); baseline["id"] = "fixture-dry-baseline"; baseline["classification"] = "dry-run"; baseline = with_content_hash(baseline)
        candidate = _candidate(1, baseline, artifact); candidate["id"] = "fixture-dry-candidate"; candidate["classification"] = "dry-run"; candidate = with_content_hash(candidate)
        for fact, request in ((artifact, "fixture-dry-artifact"), (baseline, "fixture-dry-baseline-request"), (candidate, "fixture-dry-candidate-request")):
            state, _ = dry_store.append_fact(fact, expected_revision=revision, request_id=request); revision = state["revision"]
        event = _event("fixture-dry-select", "fixture-dry-select-request", "select", candidate, feedback=None)
        state, _ = dry_store.append_decision(event, expected_revision=revision)
        self.assertEqual(0, dry_store.projection(state)["real_selection_count"])
        mixed = copy.deepcopy(candidate); mixed["id"] = "fixture-mixed-candidate"; mixed["classification"] = "mock"; mixed["artifact_bindings"] = []; mixed = with_content_hash(mixed)
        with self.assertRaisesRegex(DesignRecordError, "baseline classification mismatch"):
            dry_store.append_fact(mixed, expected_revision=state["revision"], request_id="fixture-mixed-request")

    def test_committed_fact_order_not_revision_number_defines_latest_baseline(self) -> None:
        self.add(self.artifact, "fixture-order-artifact")
        old = copy.deepcopy(self.baseline1); old["id"] = "fixture-old-baseline"; old["revision"] = 99; old = with_content_hash(old)
        self.add(old, "fixture-old-baseline-request")
        candidate = _candidate(1, old, self.artifact); candidate["id"] = "fixture-order-candidate"; candidate = with_content_hash(candidate)
        self.add(candidate, "fixture-order-candidate-request")
        selected = _event("fixture-order-select", "fixture-order-select-request", "select", candidate, feedback=None)
        self.event(selected)
        replacement = copy.deepcopy(self.baseline1); replacement["id"] = "fixture-new-baseline"; replacement["revision"] = 1; replacement = with_content_hash(replacement)
        self.add(replacement, "fixture-new-baseline-request")
        stale = self.store.projection()["queues"]["stale"]
        self.assertEqual(1, len(stale)); self.assertIn("baseline_drift:fixture-project:review", stale[0]["stale_reasons"])

    def test_two_project_family_graph_rebuilds_and_family_revision_drifts(self) -> None:
        family_store = DesignStore(self.root, self.work / "family.json", fixture=True, fixture_project_ids={"fixture-project", "fixture-project-b"})
        revision = family_store.initialize()["revision"]
        family_scope = {"family_id": "fixture-family", "members": [{"project_id": "fixture-project", "pages": ["review"]}, {"project_id": "fixture-project-b", "pages": ["review"]}]}
        evidence = copy.deepcopy(self.artifact); evidence["id"] = "fixture-family-evidence"
        family = with_content_hash({"schema_version": "1.0", "kind": "design_family", "id": "fixture-family", "created_at": "2026-09-05T12:00:00+08:00", "classification": "mock", "revision": 1, "content_hash": "", "scope": family_scope, "source": {"reference": "fixture://family-source", "evidence_refs": [evidence["id"]]}, "shared_visual_semantics": ["fixture spacing"], "component_mappings": [{"component_id": "review-canvas", "members": ["fixture-project", "fixture-project-b"]}], "member_exceptions": [{"project_id": "fixture-project-b", "reason": "fixture-only mobile exception"}]})
        family_ref = {"id": family["id"], "revision": family["revision"], "content_hash": family["content_hash"]}
        for permutation in ("members", "pages"):
            bad_family = copy.deepcopy(family)
            if permutation == "members": bad_family["scope"]["members"].reverse()
            else: bad_family["scope"]["members"][0]["pages"] = ["z", "a"]
            with self.assertRaisesRegex(DesignRecordError, "canonical"):
                validate_fact(with_content_hash(bad_family))
        family_artifact = copy.deepcopy(self.artifact); family_artifact["id"] = "fixture-family-preview"; family_artifact["scope"] = copy.deepcopy(family_scope); family_artifact["family_binding"] = family_ref
        baseline_a = copy.deepcopy(self.baseline1); baseline_a["id"] = "fixture-baseline-a"; baseline_a = with_content_hash(baseline_a)
        baseline_b = copy.deepcopy(self.baseline1); baseline_b["id"] = "fixture-baseline-b"; baseline_b["project_id"] = "fixture-project-b"; baseline_b = with_content_hash(baseline_b)
        candidate = _candidate(1, baseline_a, family_artifact); candidate["id"] = "fixture-family-candidate"; candidate["scope"] = copy.deepcopy(family_scope); candidate["family_binding"] = family_ref; candidate["baseline_bindings"] = [{"project_id": "fixture-project", "baseline_id": baseline_a["id"], "baseline_revision": 1, "baseline_hash": baseline_a["content_hash"], "pages": ["review"]}, {"project_id": "fixture-project-b", "baseline_id": baseline_b["id"], "baseline_revision": 1, "baseline_hash": baseline_b["content_hash"], "pages": ["review"]}]; candidate = with_content_hash(candidate)
        facts = [(evidence, "fixture-family-evidence-request"), (family, "fixture-family-request"), (family_artifact, "fixture-family-artifact-request"), (baseline_a, "fixture-baseline-a-request"), (baseline_b, "fixture-baseline-b-request"), (candidate, "fixture-family-candidate-request")]
        for fact, request in facts:
            state, _ = family_store.append_fact(fact, expected_revision=revision, request_id=request); revision = state["revision"]
        event = _event("fixture-family-select", "fixture-family-select-request", "select", candidate, feedback=None); event["scope"] = copy.deepcopy(family_scope)
        state, _ = family_store.append_decision(event, expected_revision=revision)
        restarted = DesignStore(self.root, family_store.path, fixture=True, fixture_project_ids={"fixture-project", "fixture-project-b"})
        self.assertEqual(1, len(restarted.projection()["queues"]["selected"]))
        family2 = copy.deepcopy(family); family2["revision"] = 2; family2["shared_visual_semantics"] = ["fixture revised spacing"]; family2 = with_content_hash(family2)
        restarted.append_fact(family2, expected_revision=state["revision"], request_id="fixture-family-2-request")
        stale = restarted.projection()["queues"]["stale"]
        self.assertEqual(1, len(stale)); self.assertIn("family_drift:fixture-family", stale[0]["stale_reasons"])

    def test_baseline_material_digest_is_bound_into_candidate_identity(self) -> None:
        original = _baseline(1, self.artifact)
        changed_artifact = copy.deepcopy(self.artifact)
        changed_artifact["sha256"] = "1" * 64
        changed = _baseline(1, changed_artifact)
        self.assertNotEqual(original["content_hash"], changed["content_hash"])
        self.assertNotEqual(_candidate(1, original, self.artifact)["content_hash"],
                            _candidate(1, changed, self.artifact)["content_hash"])
        self.add(changed_artifact, "fixture-new-artifact-digest")
        with self.assertRaisesRegex(DesignRecordError, "artifact digest mismatch"):
            self.store.append_fact(original, expected_revision=self.revision, request_id="fixture-old-baseline")

    def test_traversal_and_demo_escape_create_no_external_directory(self) -> None:
        bad_path = self.work / "missing" / ".." / ".." / ".." / ".." / ".." / ".." / "escaped.json"
        with self.assertRaises(StoreError):
            DesignStore(self.root, bad_path, fixture=True)
        outside = Path(self.temp.name) / "outside-demo"
        script = Path(__file__).resolve().parents[1] / "scripts/hub_designs.py"
        result = subprocess.run([sys.executable, str(script), "--root", str(self.root), "demo",
                                 "--output-dir", str(outside)], text=True, capture_output=True)
        self.assertEqual(2, result.returncode)
        self.assertFalse(outside.exists())

    def test_nonregular_store_and_lock_fail_without_blocking(self) -> None:
        pipe = self.work / "pipe.json"
        os.mkfifo(pipe)
        with self.assertRaisesRegex(StoreError, "regular file"):
            DesignStore(self.root, pipe, fixture=True).read()
        self.store.lock_path.unlink()
        os.mkfifo(self.store.lock_path)
        with self.assertRaisesRegex(StoreError, "regular file"):
            self.store.append_fact(self.artifact, expected_revision=self.revision, request_id="fixture-pipe-lock")

    def test_expected_revision_requires_integer_without_mutation(self) -> None:
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(ConflictError, "nonnegative integer"):
            self.store.append_fact(self.artifact, expected_revision=True, request_id="fixture-bool-revision")
        self.assertEqual(before, self.store.path.read_bytes())

    def test_cli_errors_are_json_nonzero_and_demo_rejects_existing_directory(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts/hub_designs.py"
        missing = subprocess.run([sys.executable, str(script), "--root", str(self.root), "read", "--store", str(self.work / "missing.json"), "--fixture"], text=True, capture_output=True)
        self.assertEqual(2, missing.returncode); self.assertFalse(json.loads(missing.stderr)["ok"])
        corrupt = self.work / "corrupt.json"; corrupt.write_text('{"schema_version":"9.0"}', encoding="utf-8")
        invalid = subprocess.run([sys.executable, str(script), "--root", str(self.root), "validate", "--store", str(corrupt), "--fixture"], text=True, capture_output=True)
        self.assertEqual(2, invalid.returncode); self.assertEqual("DesignRecordError", json.loads(invalid.stderr)["error"])
        existing = self.work / "existing-demo"; existing.mkdir()
        demo = subprocess.run([sys.executable, str(script), "--root", str(self.root), "demo", "--output-dir", str(existing)], text=True, capture_output=True)
        self.assertEqual(2, demo.returncode); self.assertEqual("FileExistsError", json.loads(demo.stderr)["error"])
        uncertain_dir = self.work / "uncertain-demo"
        environment = {**os.environ, "HUB_DESIGN_FAIL_DIRECTORY_FSYNC": "1"}
        uncertain = subprocess.run([sys.executable, str(script), "--root", str(self.root), "demo", "--output-dir", str(uncertain_dir)], text=True, capture_output=True, env=environment)
        outcome = json.loads(uncertain.stderr)
        self.assertEqual(3, uncertain.returncode); self.assertEqual("COMMITTED_DURABILITY_UNCONFIRMED", outcome["status"])
        self.assertEqual("initialize", outcome["receipt"]["request_id"]); self.assertEqual(1, outcome["revision"])
        reopened = DesignStore(self.root, uncertain_dir / "fixture-store.json", fixture=True)
        self.assertEqual(1, reopened.read()["revision"])


    def test_reordering_facts_cannot_change_committed_baseline_order(self) -> None:
        self.facts()
        self.add(_baseline(2), "fixture-next-baseline")
        raw = self.store.read()
        raw["facts"][-1], raw["facts"][1] = raw["facts"][1], raw["facts"][-1]
        with self.assertRaisesRegex(DesignRecordError, "committed receipt order"):
            self.store.validate(raw)

    def test_cli_and_demo_preserve_uncertain_export_outcome(self) -> None:
        from hub.design_cli import main
        result = {"outcome": "COMMITTED_DURABILITY_UNCONFIRMED", "path": str(self.work / "result.zip"), "sha256": "1" * 64, "store_revision": 7}
        for command in (
            ["export", "--fixture", "--store", str(self.store.path), "--candidate", "fixture-candidate", "--revision", "1", "--output", str(self.work / "result.zip")],
            ["demo", "--output-dir", str(self.work / "uncertain-export-demo")],
        ):
            with self.subTest(command=command[0]), mock.patch.object(DesignStore, "export", return_value=result):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = main(["--root", str(self.root), *command])
                self.assertEqual(3, code)
                self.assertEqual(result["outcome"], json.loads(output.getvalue())["outcome"])

    def test_scope_permutations_are_rejected_before_persistence(self) -> None:
        from hub.design_records import validate_scope, validate_decision_event
        scopes = [
            {"family_id": None, "members": [{"project_id": "fixture-project", "pages": ["b", "a"]}]},
            {"family_id": None, "members": [{"project_id": "fixture-project-b", "pages": ["review"]}, {"project_id": "fixture-project", "pages": ["review"]}]},
        ]
        before = self.store.path.read_bytes()
        for scope in scopes:
            with self.subTest(scope=scope):
                with self.assertRaisesRegex(DesignRecordError, "canonical"):
                    validate_scope(scope)
                artifact = copy.deepcopy(self.artifact); artifact["scope"] = scope
                with self.assertRaisesRegex(DesignRecordError, "canonical"):
                    self.store.append_fact(artifact, expected_revision=self.revision, request_id="fixture-unsorted-artifact")
                candidate = copy.deepcopy(self.candidate1); candidate["scope"] = scope
                with self.assertRaisesRegex(DesignRecordError, "canonical"):
                    validate_fact(with_content_hash(candidate))
                event = _event("fixture-unsorted-event", "fixture-unsorted-request", "select", self.candidate1, feedback=None); event["scope"] = scope
                with self.assertRaisesRegex(DesignRecordError, "canonical"):
                    validate_decision_event(event, fixture_store=True)
        self.assertEqual(before, self.store.path.read_bytes())

    def test_same_canonical_scope_different_candidates_requires_supersedes(self) -> None:
        baseline = copy.deepcopy(self.baseline1); baseline["scope"]["pages"] = ["a", "b"]; baseline = with_content_hash(baseline)
        first = copy.deepcopy(self.candidate1); first["scope"]["members"][0]["pages"] = ["a", "b"]
        first["baseline_bindings"][0].update({"baseline_hash": baseline["content_hash"], "pages": ["a", "b"]})
        first["artifact_bindings"] = []; first = with_content_hash(first)
        second = copy.deepcopy(first); second["id"] = "fixture-other-candidate"; second = with_content_hash(second)
        for i, fact in enumerate([baseline, first, second]): self.add(fact, f"fixture-canonical-fact-{i}")
        old = _event("fixture-canonical-first", "fixture-canonical-request-first", "select", first, feedback=None); old["scope"] = first["scope"]; self.event(old)
        new = _event("fixture-canonical-next", "fixture-canonical-request-next", "select", second, feedback=None); new["scope"] = second["scope"]
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(DesignRecordError, "must supersede"):
            self.event(new)
        self.assertEqual(before, self.store.path.read_bytes())
        new["supersedes"] = old["id"]; self.event(new)
        projection = DesignStore(self.root, self.store.path, fixture=True).projection()
        self.assertEqual(2, len(projection["history"]))
        self.assertEqual(1, len(projection["effective"]))
        self.assertEqual([new["id"]], [item["event"]["id"] for item in projection["queues"]["selected"]])


if __name__ == "__main__": unittest.main()
