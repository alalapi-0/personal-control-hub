# Hub design records unit

Reader: Root, Repair, Judge, Governor and owner. Purpose: index TC2 implementation evidence. Update on a material candidate change; current execution lives only in `STATE.yaml`.

- Contract: `contract.md`, HUB-GOV-2026-09-05-TC2 version 1.
- Preimage: `baseline.json`; TC1 candidate v3 accepted under `../unit-01/governor-v3.json`.
- Discovery: `ui-source-inventory.json` preserves all 24 projects, four source-observed Figma references and two carefully scoped relation observations. No external app was launched; no runtime UI or owner visual choice is claimed.
- Implementation: design schemas, fact/event persistence, deterministic projections and local material export. Synthetic-only proof; no real decision or implementation authority is created.
- Ownership: both bounded Repair writers returned PATCH_READY and were collected. Root then completed baseline material identity and path/type guards and owns the frozen candidate. External projects and registry remain read-only.

Root reproduced 66 tests: the accepted 38 connection tests plus 28 design/store/export tests. Actual CLI demo completed with an 11-revision fixture store and four events. Reopening through CLI succeeded; committed receipt prefixes reconstruct a selected revision 7 and stale revision 8. The exported ZIP contains separate baseline and candidate bytes, both verified independently against their SHA-256 digests, plus exact baseline/candidate identities and two then-current feedback/selection events. The final fixture retains reselect and withdrawal history with zero real selections.

`validation-v1.json` registers commands, outputs, preservation and material checks. Its initial recorder used the wrong export-manifest key after a successful demo; the existing fixture was preserved and reopened, and the evidence recorder was corrected. This was an evidence-capture error, not a failed product demo. `state-task.diff` is relative to the protected dirty TC2 preimage. All 53 other protected files remain byte-identical, STATE changes only current_work, and bootstrap is 8001 bytes. Repository/state/diff checks pass; the two unchanged MPV registry errors remain baseline failures.

Candidate v1 is preserved in `candidate-v1.json`. Fresh Judge rejected it in `judge-v1.json`; Governor requested repair under the unchanged contract in `governor-v1.json`. The repair addresses post-publication outcomes, per-project baseline material export and unrelated Figma references, and completes family identity, dry-run isolation and stable behavior-ID validation.

Root reproduced 83 tests for candidate v2, including actual-store two-project family export, immutable receipt order, committed outcome/CLI propagation, and synthetic failure branches. `validation-v2.json` preserves all commands and readbacks. Both original and v2 CLI fixtures reopen at revision 11 with four events and zero real selections; their receipt prefixes reconstruct the expected selected/stale states. All ZIP materials verify by digest. The v1 file catalog omitted the existing candidate preview text; v2 includes both complete output trees. All 53 protected files are unchanged, other STATE fields are preserved, and bootstrap is 7992 bytes. A misspelled consistency-command invocation is recorded alongside the corrected passing command; the two unchanged registry failures remain baseline failures.

Fresh Judge reproduced one further v2 defect: reordered members/pages could represent the same scope with different hashes and bypass supersession. `judge-v2.json` and `governor-v2.json` preserve that decision. Root's v3 repair requires canonical member/page order before persistence, with negative cases for artifact/candidate/family/event scope and a cross-candidate supersession roundtrip. The Judge's four accidental ignored bytecode files were removed by exact path; frozen source and evidence were unchanged.

The repaired exact candidate is `candidate-v3.json`; `validation-v3.json` registers the current checks and canonical fixture readback. Independent acceptance remains pending. No UI, Figma, real choice, all-connections or Goal acceptance is claimed. This index is not the final Hub handoff.
