# Named source discovery · 2026-09-05

Reader: adapter implementer and reviewer. Purpose: provenance for deterministic selectors. Update: source structure changes require new adapter revision and fresh evidence. This is discovery evidence, not a current project-state authority.

Input: the 24 rows of `data/registry/external_projects.yaml`, as frozen in `manifest-v1.json`. Read-only explorer `/root/hub_code_discovery` read the allowed named state files and project-specific rules; no external scripts/services or writes. Root verified declared source symlinks resolve within current registry roots. Manga Localizer was excluded from all filesystem probes pending the owner decision.

The executable selector table is `data/design_governance/connection_adapters.json`; it contains project IDs and field selectors, never a second root-path registry. Missing selectors return an explicit unknown. All returned values are source claims, not independent runtime acceptance.

- StorageGovernance: only `execution_control.state` and `next_action.description`, no terminal-project/history/effect-authority aggregation.
- Hub: only `current_work.status` and `current_work.next_action`.
- Universal Player: `Status:`; its follow-up is explicitly historical, so no current next action.
- AI Anime: `governed_round.status`; queue is empty and mode is not progress.
- AI Music: current product-priority declaration and original-composition milestone. Milestone is track-scoped, not a global next-action authorization.
- Novel Continuation: `current_round.status` and bounded `next_agent_recommended_actions`; recommendations are not effect authority.
- Story Faceless Utopia: first product paragraph of `Current stage`; exclude storage completion from product status.
- Light Novel: registered YAML fails safe parsing near lines 9–10. Report invalid source; no regex fallback masquerading as valid YAML, no external repair or live-probe execution.
- WeChat: `project.status`; no explicit next action.
- Resilient Personal Network: README repository-stage claim only; does not verify remote service health.
- Pixel World: `round_progression.cycle_status`; the round map contains task names, not per-round states.
- Desktop JAV, Desktop Tool and Audio Clone: operations/storage documents lack a business current-state/next-action declaration.
- Zarathustra: Snapshot/Phase and ordered Next section.
- Computer Study Plan: current-position paragraph and follow-up backlog; task-progress data was not read.
- Cognitive Asset Library: uniquely matching in-progress phase heading; unchecked DoD items are not a unique next action.
- PyCharm Agent Workspace: version-manifest `status`; next-version naming hint is not next action.
- YouTube HQ Downloader: nonunique status-document current-progress claim; future extensions are not prioritized next actions.
- Desktop Magnet, PyCharm Misc and Desktop Downloads: no registered current-state source. Allowed README/top-level-name observations do not establish a replacement authority. Keep missing-source semantics and full manifest membership.
- MPV: `Round Status` and `下一入口`; explicitly reject the misleading `Next Authorized Round` label because adjacent text states the round is not authorized.

UI source discovery by `/root/ui_sources` found independent existing directions for Universal Player (SwiftUI, Figma `lURMGTfFpSzWQhVy0hBS3m`), Computer Study Plan (HTML/CSS/JS, Figma `WtmY7TC867Wdp0qXY2b5uz`) and MPV (QML, Figma `JXHe6V8OMEz9qt5rbRHuuz`). All are dirty. No cross-project shared-token or Code Connect proof was found in the inspected paths; no visual family is confirmed. Runtime UI evidence remains UNVERIFIED. Existing launch/fixture paths may touch media history, real progress or `.local-runtime`, so none was started. Exact functional UI baselines and safe local previews remain later work.

Figma `whoami` returned `UNAUTHORIZED`, `oauth_token_invalid_grant`; owner reconnection was requested. No Figma file or candidate exists for this Goal yet.
