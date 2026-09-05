# TC4 implementation interfaces

Root owns this implementation note, `service_contract.py`, HTTP transport, launcher,
integration tests, protocol and evidence. These interfaces are adaptive candidate
details under TC4, not additional acceptance or authority.

- Project worker owns only `src/hub/project_service.py` and
  `tests/test_hub_project_service.py`. `ProjectService(root, *, bundle_paths=None,
  ledger_path=None, relations_path=None)`; `list_projects(query=None,
  design_snapshot=None)`, `get_project(project_id, design_snapshot=None)` and
  `refresh(command)`. Query is a dictionary of string values, no implicit paths.
  Both read methods share one deterministic DTO builder. A design snapshot is the
  exact object returned by DesignService.snapshot(); use its store revision.
- Design worker owns only `src/hub/design_service.py` and
  `tests/test_hub_design_service.py`. `DesignService(store)`;
  `snapshot()`, `decide(command, *, owner_action)`, `export(command)`,
  `artifact(artifact_id, *, candidate_id, candidate_revision)` returning
  `ArtifactResponse`. No external UI, real decisions or writes during discovery.
- `DesignService.export_download(command)` is read-only: verify and deliver an
  existing request-bound ZIP only. GET must never create an export implicitly.
- Shared exceptions: `ServiceError(code, status=400, retryable=False,
  outcome='NOT_COMMITTED', details=None)`. Details are allowlisted identifiers,
  revisions and receipts, never raw exceptions or filesystem paths. Distinguish
  concurrency, authority, invalid input, unavailable/corrupt storage and
  postpublication uncertainty. Root owns shared types; workers request changes.
- Decision commands contain exactly `request_id`, `event_id`, `created_at`,
  `expected_revision`, `action`, `candidate`, `scope`, `feedback`, `supersedes`.
  Every field is explicit. OwnerAction is an internal capability constructed after
  HTTP session + same-origin checks, never taken from body fields. Source is
  deterministic per request for durable retries. Fixture actions remain synthetic.
- Export command contains exactly `request_id`, `expected_revision`, `candidate`
  (id, revision, content_hash). Server generates the destination. Reuse of an export
  request is validated from its create-only bundle; uncertainty after publication
  must not be presented as definitely uncommitted. No caller output paths.
- Refresh command contains exactly `request_id`, `project_ids`, `expected_head`
  (sequence and hash). Strict registered-ID set and explicit expected head.
- HTTP is IPv4 127.0.0.1 only. Exact Host, no CORS, request Origin and Fetch Metadata
  checks, bounded JSON, session HttpOnly SameSite=Strict cookie + returned random
  anti-CSRF header token protect commands. Secrets exist only in runtime memory.
  The user's OS account and local native processes are trusted. This is not a
  remote or multi-user authentication service. No HTML app is implemented here.

Read responses never initialize stores or refresh sources. An absent design store
is represented as unavailable, not as an initialized revision or owner choice.
Registered artifact delivery verifies candidate scope/binding/hash, bounded file
access and inert content behavior; Figma links stay links.
