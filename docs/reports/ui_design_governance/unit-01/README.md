# Hub foundation unit · candidate for independent review

Reader: Root, Judge, Governor and owner. Purpose: index the first implementation unit's frozen evidence. Update: candidate changes require a new fingerprint manifest. Current execution remains in `STATE.yaml`.

Contract: `HUB-GOV-2026-09-05-TC1`, [contract.md](contract.md). Starting HEAD: `99c0dffc7d446ec17751632ea312220a28a7159e`. Existing checkout and all initial changes remain in place. This unit has no Git delivery authority.

## Candidate

- Versioned scope, connection manifest, snapshot and evidence validation, with cross-record identity/hash checks and unknown-version rejection.
- Registry-bound manifest for all 24 projects; no copied root registry and no denominator reduction.
- Named-source reader with explicit permission checks before any external path probes, bounded files, safe structured parsing, symlink boundary enforcement and per-source errors.
- Deterministic per-project selectors, refresh/validate/schema CLI, and atomic explicit Hub-only projection outputs. No UI or user-decision persistence is claimed here.

Run `python3 scripts/hub_connections.py schema` or consult [the implemented protocol](../../../design/hub_connection_protocol.md). CLI defaults to stdout. The repaired candidate file list and hashes are in `candidate-v3.json`; `state-task.diff` is the exact delta against the dirty starting STATE preimage, not against HEAD. Candidates v1/v2 and their Judge FAIL/Governor REQUEST_REPAIR remain as audit history.

## Results and limits

The full real read produced 19 fresh sources, 1 authority-blocked record, 1 invalid source and 3 records with no named current-state source. Every row remains in the manifest. Successful reads remain `PENDING` for final connection acceptance because no product UI exists yet; all UI lanes are `UNVERIFIED`.

- Manga Localizer: no probes; owner scope decision pending.
- Light Novel: source YAML syntax invalid; no external repair or live probe run.
- Desktop Magnet, PyCharm Misc and Desktop Downloads: no declared current-state entry; allowed observations did not justify inventing one.
- Figma: OAuth reauthentication needed; no Hub Figma file/candidate/selection exists yet.
- Other UI observations are source-only, not runtime screenshots. No visual family is confirmed.

Root reproduced all 38 isolated `unittest` cases successfully. Collection validation binds full canonical registry authority, exact named paths and adapter identity, rejects rehashed access grants and forged paths without probing prohibited roots, and enforces source/evidence/UI-state consistency. The second repair replaces partial nested validation with exact structures and an explicit snapshot truth matrix, covering nonfresh business claims, contradictory success/error fields and invalid evidence references. Real single-Hub read and the 49-record manifest/snapshot/evidence graph validate. Repository check, round consistency and diff whitespace check pass. Gate/runner report 10 pre-existing soft warnings and zero hard blockers.

Existing registry validation still fails for MPV's watch-path comparison and unrecognized storage scope. The nine local legacy tests reproduce 7 passes and 2 pre-existing failures (removed SubPlz and old storage authority expectation). The current Python lacks pytest; the two unrelated storage external-contract tests were not run. None of those files was changed by this unit. This is not an all-repository-green or product-acceptance claim.

## Evidence

- `baseline.json`: initial HEAD, branch, 21 protected-file hashes and governed STATE preimage.
- `preservation.json`: 20 other protected files byte-identical; all STATE sections except current_work/last_updated semantically identical; bootstrap size 7997 bytes.
- `validation-v3.json`: repaired-candidate commands, stdout/stderr and exit codes; v1/v2 evidence retained.
- `real-smoke-v3.json`: repaired-reader authorized Hub source read and fingerprint; v1/v2 observations retained.
- `connections-v3.json`: repaired-reader 24-row real-source projection with 24 linked evidence records, explicitly rebuildable and non-authoritative; v1/v2 observations retained.
- `judge-v1.json`, `governor-v1.json`, `judge-v2.json`, `governor-v2.json`: failed-candidate lineage and required repairs, not acceptance claims.
- `source-discovery.md`: selector semantics and narrowly scoped UI discovery provenance.

After independent acceptance, the next delivery unit is design-record persistence, version/decision conflict handling and material export. Figma reconnection and the owner-only scope/visual decisions remain separate dependencies. This document is not the final Hub handoff.
