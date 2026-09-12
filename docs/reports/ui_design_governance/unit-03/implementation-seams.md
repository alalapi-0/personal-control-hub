# TC3 implementation seams

Reader: Root and bounded implementation workers. Purpose: preserve the read-only code exploration that selects integration boundaries. Update when implemented interfaces change. Current execution and next action remain in STATE.

Explorer `/root/connection_evolution` inspected Hub code only, made no writes and ran no external probes. Its bounded findings:

- Preserve v1 connection truth/authority semantics and frozen schema, adapter and manifest files. `connection_records.py` intentionally forbids business claims and last-success fields in nonfresh snapshots. A current-view wrapper must retain separate latest-attempt and last-success identities instead of modifying old snapshots.
- Add an independently versioned source-plan/resolution module. Each derived source binds registry ID, permitted static evidence entry/hash, exact literal/relative target, format, field meaning and authority. Never pretend a derived source is a registry current_state_paths entry. Validate evidence before target access; no-access projects reject before path resolution.
- Use the accepted inventory and exact named script content only for no-current-source disposition. Light Novel scheduler operational facts stay separate from unknown business status. Existing per-project snapshots remain valid historical observations.
- Add a durable refresh coordinator/ledger with request creation, per-project append, finish, history and rebuild. Bind manifest/adapter/source-plan identities, source fingerprints, times and content hashes. Current projection references latest attempt and last success separately and marks stale explicitly.
- Existing `Connections.refresh_all` eagerly accumulates all rows, and the CLI writes only after all reads. The new coordinator must commit each project as it completes, preserving progress across another project's failure or process interruption.
- Existing snapshot IDs repeat across refreshes; history requires unique event identities and hashes. Rebuild must work without opening external project paths and must separately flag current-authority drift.
- Existing v1 relation arrays remain empty. Evidence-backed proposed relation facts belong in a separate validated collection until integration supports their references.

Likely modules are `connection_sources.py` and `connection_refresh.py`, with focused tests and CLI integration. Root owns versioned source-plan/manifest metadata and protocols; workers must receive disjoint implementation scopes. Persistence may use the standard-library SQLite transaction/uniqueness facilities to provide per-project commits and CAS without changing accepted design-store code; implementation remains adaptive and must prove failure/restart semantics.

Tests should reuse existing isolated 24-project fixtures and no-probe/path/schema-forgery cases. Add exact static-evidence membership and drift, source-layer disposition, interrupted/partial refresh, idempotent conflict, cross-process contention, restart/rebuild equivalence, last-success retention, corrupt-event handling and relation-evidence validation.
