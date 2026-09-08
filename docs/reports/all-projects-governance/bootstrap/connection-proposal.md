# Minimal connection contract proposal

Reader: the next Hub integration unit and per-project implementers. Update only when the shared connection contract is frozen or amended. This is a proposal, not a second state authority and not proof of connection rollout.

Current working checkout already implements `src/hub/connection_sources.py`, `connection_records.py`, `connection_refresh.py`, `project_service.py` and `data/design_governance/connection_adapters.json`. These are absent from this old remote-main base. Transplant only verified dependencies in the subsequent Hub unit; do not merge the UI/Figma/credential branch. Current source-plan schema and active authority bundle must be discovered from the actual loader then, not assumed permanent v4.

| Required semantics | Existing owner / planned compatible extension |
|---|---|
| schema_version, project_id, name, relative source/format/mapping | Registry owns machine absolute real root; adapter owns stable declaration. Per-project lightweight declaration uses existing source_refs/source_role mapping vocabulary. Source paths must remain bounded under resolved registry roots. |
| objective, phase/round, status, completed, accepted, next action/role | Extend the existing project_snapshot/business view, deriving each field from the sole project source. Keep unknown_fields with reasons; never promote storage completion to business completion. |
| progress denominator or milestones | Add source-bound counts and counting_basis to snapshot; absent denominator means unknown, never a fabricated percentage. |
| blockers, affected IDs, recovery condition, retry entry, waiting on owner | Extend existing blockers/next_action_kind with source references. Differentiate review-only and redo; existing immutable candidate identities remain. |
| validation evidence, accepted basis, main target and delivery receipt | Source snapshot carries validation/acceptance refs; operational delivery record separately proves observed remote ancestry. Completed/accepted/delivered never collapse. |
| source mtime/version/hash, observed_at, read_status, stale/error | Reuse source resolver fingerprints, result validation and refresh ledger. Fail closed on parse/permission/root escape, expose latest error even when retaining a last success. |
| one output for every registered/discovered project | Extend project_service projection; disabled/offline/no source/errors remain rows. Frozen coverage becomes dynamic registry coverage, removing hardcoded 24 only with validator tests. |

Acceptance for the later unit: modify one sole source, normal refresh yields only that row's new source-bound fields; repeated refresh creates no duplicate business item; restart resumes; fingerprint drift invalidates/rebinds; corrupt/offline/permission failures stay visible. A fresh reader must use only Hub entry/output to understand every project. Feishu remains disabled; local JSON/Markdown/card mapping uses stable project_id and source fingerprint for updates.

No project declaration or rollout is accepted by this proposal. The 26 registry records comprise 24 existing local identities and 2 owner-confirmed removed_local entries. All 35 native discoveries remain reconciled in coverage.json; the owner excluded the 17 cloud projects. Seven projects lack Git; no remote is created without a target from the owner.
