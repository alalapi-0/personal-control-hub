# HUB-GOV-2026-09-05-TC2 · version 1 · ACTIVE

Reader: Root, Repair, fresh Judge and Governor. Purpose: capability contract for the second Hub unit; no competing current-state authority. Created by read-only Governor `/root/hub_contract`, activated after TC1 candidate v3 approval. Root registered the preimage and sole active Hub task before dispatch.

## Objective

Build local versioned design facts and append-only decision events, with validation, safe persistence, history/current-selection reconstruction, drift detection and verified material export. Prove this unit using isolated synthetic fixtures only. Implementation paths remain adaptive.

## Protected boundaries and effects

- STATE remains the only execution state. Design facts and derived queues do not replace it.
- Preserve registered dirty files and storage facts. Root may update current_work/last_updated only in existing STATE; bootstrap remains at most 8192 bytes.
- External projects, registry, business content/code/data/STATE, other tasks and previously accepted connection infrastructure remain read-only in this unit.
- User choice, implementation authority, review, implementation and publication are distinct facts. Agent, defaults, recommendations, silence and fixture actions never become real user choices.
- Historical events cannot be removed, overwritten or rebound to another candidate.
- No UI implementation, Figma candidate creation, real business materials, Git delivery, login, remote services or other external effects. Figma reconnection and Manga access remain independent pending dependencies.
- Root owns STATE, protocol, evidence and control-plane writes. Bounded Repair may own disjoint non-control source/tests only; no descendants, Git or external effects.

## Acceptance

1. Exact versioned schemas cover baseline, candidate, review, decision_event, artifact_ref and their collection. Enforce stable IDs, timezone timestamps, enums, exact nested fields, reference integrity and unknown-version/field rejection.
2. Bind candidate project/family members and pages to baseline IDs, revision and content hash. Hash covers scope, baseline references, visual/structural configuration and artifact digests. An existing identity cannot acquire different content.
3. Decision events carry stable event/request IDs, source type/checkable reference, select/request_changes/defer/withdraw action, exact candidate, explicit member/page scope, feedback, timestamp and supersedes. Fixtures are unmistakably synthetic and never project as real decisions.
4. Persistence uses expected_revision, short cross-process lock, reread inside lock, compare, append and atomic replace. Same request/payload is idempotent, same request/different payload rejects, and concurrent conflict is explicit. Failed writes preserve prior facts; no permanent backup files.
5. History, effective decisions and queues rebuild deterministically from facts/events across restart or removal of derived output. Supersedes/reselect/withdraw/request_changes/defer retain lineage.
6. Baseline/source/scope/candidate changes derive stale; old decisions stay visible but cannot apply to new revisions or grant implementation. No decision operation changes or rolls back code.
7. Artifacts and exports remain under Hub task artifact directories after path resolution; reject traversal and symlink escapes. Verify hashes, classification (real/mock/dry-run/imported), generation provenance, time, exact version and scope. Missing, mismatched, stale or failed export is explicit; never silently mix versions or include business data.
8. Synthetic fixture proves baseline/candidate → feedback → choice semantics → restart → export/hash verification → new revision makes old choice stale → reselect/withdraw with history. Cover real cross-process contention, idempotent retries, conflicting request/revision, unknown schema, bad references, path/symlink boundaries and interruption/failure injection.
9. Root records true TC2 status, dependencies and exactly one next action. No UI, full-connection or Goal completion claim.

## Evidence and review

Run prewrite runner/gate; focused schema/store/rebuild/export checks plus repository, state consistency and diff checks. Register HEAD, dirty preimages, exact diff, candidate hashes and aggregate algorithm, command results, concurrency/failure evidence, output list, bootstrap bytes and external-effect boundaries. Freeze candidate for fresh Judge, then Governor decision on that exact identity. Changed content requires a new identity. Recovery uses only registered task-owned preimages/inverse patch; no reset, stash or broad overwrite.
