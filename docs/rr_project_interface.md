# PCH RR adapter v2 — real review and native candidate

Reader: RR maintainer and PCH Agent; update when routes, fields, identity or lifecycle change.
The candidate remains disabled. Preparation and fixture checks do not install, enable,
restart, migrate the real database or claim real mTLS / feedback acceptance.

## Real review surface

`GET /__rr/review/` and registered `GET /__rr/review/projects/{project_id}` serve
one SPA. It reuses PCH hub.css, the extracted shared dom.mjs component used by
common.js, and the existing ProjectService and TaskStore read methods. It shows
actual stored project definitions, work/last-success snapshots, bounded progress
and task state. It has no Owner login, source refresh, task execution/cancel,
management edits, raw task/result text or original-file access. New work does
not silently promote historical or unavailable facts to current.

Same-origin assets are individually fixed, never directory mounts:

| opaque ID | source under `src/hub/web/` | MIME |
| --- | --- | --- |
| review-page | rr.html | text/html; charset=utf-8 |
| review-script | rr.js | text/javascript; charset=utf-8 |
| pch-dom | dom.mjs | text/javascript; charset=utf-8 |
| pch-style | hub.css | text/css; charset=utf-8 |
| pch-icon | icon.svg | image/svg+xml |

All asset URLs are `/__rr/v1/resources/{id}`. The page has no inline script,
inline style or external dependency. It supports the B policy
`default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`.
PCH HTML additionally restricts script/style/connect/img to self and form-action
none; feedback uses fetch. No WS/SSE. The frozen manifest explicitly enumerates
page/resource/data/feedback routes, source commit/build_id/dirty and resource
SHA/mode/preview_version. The installation runtime manifest verifies actual code
bytes; installed code does not infer source commit from the parent dirty repo.

## Explicit data whitelist

All routes below require the same gateway authentication, including static pages.

| Method | Route | Contents |
| --- | --- | --- |
| GET | /__rr/v1/manifest | frozen source/resource/route allowlist |
| GET | /__rr/v1/data/projects | scoped project list and verified ledger head |
| GET | /__rr/v1/data/projects/{project_id} | that scoped project detail |
| GET | /__rr/v1/data/projects/{project_id}/tasks | actual TaskStore, newest at most20 |

Each data response has schema1, actual source, observed_at and a SHA-256 content
revision/body_sha256. Project fields are exactly project_id, name, project_type,
status, freshness(state/authority_drift/observed_at), definition
(official_name/aliases/purpose/outputs, each state/value), and work
(state/observed_at/fields/progress/blockers_available/blockers). Work fields are
objective/phase/round/status/next_action/completed/accepted; progress fields are
completed/total/counting_basis. Blockers contain only reason/waiting_for_user/
recovery_condition. Text/list lengths are bounded; local paths, URLs and credential
assignments are removed. Field whitelisting is not a general secret detector:
review_project_ids and the actual native data export still need adoption review.

Tasks contain only id, sequence, project_id, status, enumerated kind, created,
result_available, result_version and human_acceptance. No payload/intent/task_spec,
message/result/event body, thread/turn/grant/process/provider details or private
path inventory is exported. Removed and read-disallowed projects return404.
GETs reuse existing read-only ledger/store operations; they never refresh sibling
sources or initialize a missing database. There is no polling in the SPA.

## Browser feedback and offline Agent

| Method | Route | Semantics |
| --- | --- | --- |
| POST | /__rr/v1/feedback | multipart atomic201 / identical retry200 / conflict409 |
| GET | /__rr/v1/feedback/by-request/{UUID} | same device/project context, receipt and state |
| GET | /__rr/v1/feedback/{feedback_id} | saved document and actual state |
| GET | /__rr/v1/feedback/{feedback_id}/attachments/{attachment_id} | belonging actual bytes |

Retain v1 strict JSON/UTF-8/no duplicate keys or floats, UUID, source/resource,
page-path, size, MIME/content and canonical SHA rules. Body JSON is at most16KiB,
attachments at most2 x1MiB, total2MiB+32KiB. Standard FormData text feedback fields
may omit a part Content-Type; JSON is still strictly parsed. UTF-8 display file
names are accepted; controls, path separators and unknown parameters are refused.
PNG verifies CRC/bounded decompression and excludes Adam7; JPEG/WebP validate
bounded container framing rather than claiming full pixel decoding.

Schema1 keeps its existing fields and permits one exact optional context object:
`{data_revision: SHA256, observed_at: timestamp}`. The SPA records the rendered
view context, freezes submitted_at/UUID/body/attachment digests, and queries the
same UUID after uncertain responses before retry. Content revision remains1;
ack and result are separate. Attachments remain same-transaction BLOBs outside
preview TTL/LRU, within the original16MiB TaskStore ceiling. No second database.

The formal Owner CLI remains `python scripts/pch_rr.py pending [--cursor C]`,
`read ID`, `ack ID --proof PRIVATE_FILE`, and
`result ID --proof PRIVATE_FILE --handling PRIVATE_FILE`.
Read verifies actual body and all attachment bytes and materializes private
opaque files/proof under the one business root's data/rr-agent-reads. Ack checks
exact read/revision/body/all attachment digests; result requires that ack and
separately records summary/references/time without executing feedback text.
Browser off or rollback does not disable the protected offline Agent path.
No Agent HTTP routes are forwarded. The standalone `pch_rr.py serve` is removed.

## Same-process runtime and identity

The current formal code, data and service are distinct:

- Code: `.venv/pch-refresh/versions/3150d69013d1da45e80766a6f06d92a50a56130aca26fb1984fdbd08073d16ae/code`
  below the existing PCH business root.
- Unique DB: business root `data/workbench/tasks.sqlite3`, with its existing
  storage-registration.json; no new register_storage operation during adoption.
- Service: personal-control-hub.service. Its loaded ExecStart is normal hub_server,
  but the currently running invocation is the retired finite read-only wrapper.
  Restoring service configuration cannot resurrect that old PID/window. Never
  replay its grant/arming procedure.

`hub_server.py --rr-binding PRIVATE_BINDING` attaches the Browser Unix handler to
that same PCH process and exact server.projects / server.tasks.store objects.
It starts one owned non-daemon listener thread and closes/shuts down/joins/unlinks
its exact socket inode through server.server_close. Normal8766 management routing
and Owner credential directives are retained; ordinary executor defaults OFF.
No separate Browser business writer process or runtime is started.

Browser endpoint is `/run/pch-rr/browser.sock`. Its parent is Owner-owned,
gateway group,0750 (or2750 to inherit that group without supplementary-group
changes); socket0660. Group membership must include the dedicated gateway and
may include only gateway and Owner. An existing path is never overwritten.

The native packet includes Owner-protected rr-installation.json plus
rr-runtime-manifest.json in the immutable version code root. The former resolves
the exact protected business-root data/rr-adapter.json; CLI arguments cannot
override identity or store. Binding schema2 fields are project_id/program_id/
code_root/data_root/browser_enabled/agent_uid/gateway_uid/gateway_gid/origin/
devices/review_project_ids/ledger_identity/storage_registration_sha256/
key_provider_socket, plus schema. Keys are not present in this JSON.
Browser disabled still permits Owner-only CLI access to the exact registered DB.

Origin is fixed `https://rr-personal-control-hub.invalid:9419`. Each browser
request requires both configured SO_PEERCRED gateway UID and the existing HMAC
assertion. The gateway must run as a dedicated non-root UID different from Owner.
It generates device_sha256 from verified real mTLS certificate identity and maps
it to personal-control-hub; arbitrary forwarded/identity headers confer nothing.
For static navigation and assets as well as fetch, B must set the fixed backend
Origin and freshly signed assertion. PCH trusts B's verified mTLS result, not an
independent remote TLS observation.

Local-only `GET /__rr-local/v1/challenge` is peer-UID protected and must never be
forwarded remotely. Assertion fields remain exactly v/challenge/device_sha256/
project_id/origin/method/path/body_sha256/nonce/issued_at/expires/mac. Use canonical
UTF-8 JSON (sorted keys, compact separators, unescaped non-ASCII), HMAC-SHA256
without mac, then unpadded base64url for X-PCH-RR-Auth. Signed body SHA covers raw
HTTP body, including multipart boundaries; receipt SHA covers canonical JSON.
Issued time is integer seconds within5s, expiry at most60s after issue, UUID nonce
single-use with bounded cache, challenge fresh per PCH instance.

## Anonymous FD key supply

No key value is accepted from argv, dotenv, environment values or ordinary files.
The old credential-file reader is removed. Native OS provider endpoint is fixed
`/run/rr-pch-key/pch.key.sock`, AF_UNIX SOCK_SEQPACKET. Parent0750 and socket0660
belong to gateway_uid/gateway_gid, with explicit Owner access (group active in
the service invocation, or a native Owner ACL). B provider must enforce actual
SO_PEERCRED Owner UID, not trust the request recipient_uid field.

PCH checks provider filesystem metadata and peer UID, and sends one packet:
canonical JSON `{schema:1,project_id:"personal-control-hub",recipient_uid:OWNER_UID}`
plus newline. Provider replies in one packet with canonical
`{schema:1,project_id:"personal-control-hub",key_fd:true}` plus newline and exactly
one SCM_RIGHTS descriptor. It must be an anonymous memfd holding32–64 key bytes,
sealed WRITE/GROW/SHRINK/SEAL. No key bytes occur in the packet. PCH bounds receive,
rejects malformed/excess/truncated FDs, consumes the sealed descriptor and closes
it on success or error. Three-second provider timeout fails closed.

Trusted local API also accepts an inherited descriptor:
`attach_rr_gateway(server, code_root=..., binding_path=..., key_fd=FD_NUMBER)`;
normal hub_server supports `--rr-key-fd N` only with explicit binding. This is
same-invocation FD inheritance, not a claim that systemctl can pass caller FDs.

## Native preparation, effects and rollback

`python scripts/pch_rr_install.py prepare --data-root EXISTING_ROOT --output NEW_PRIVATE_DIR --project-id ID [repeat]`
optionally takes non-secret gateway UID/GID and device SHA registrations. It
prepares a verified code packet, disabled binding, program identity and a service
drop-in candidate only. Missing actual IDs/devices are explicit, not guessed.
`check --candidate DIR` verifies pinned source/store/metadata. Neither command
installs code or changes the service. The target follows the existing
`.venv/pch-refresh/versions/{program_id}/code` mechanism and reuses Python/env.

Future explicit effects are `install --candidate DIR` (new immutable code only),
`enable --candidate DIR` (exact protected binding/drop-in and service restart),
and `rollback --candidate DIR`. Enable checks the prior process identity and
config preimage and refuses unrelated binding or concurrent drift. The new
70-pch-rr.conf preserves existing Owner provider directives and resets only
ExecStart to the reviewed normal Web+RR startup. It does not reactivate ordinary
native execution. Failure/uncertain startup requires exact reconciliation before
retry; do not treat a systemctl result as adoption evidence.

Rollback disables Browser, restores the affected service drop-in, and leaves the
new protected Agent binding/code and all persisted feedback/attachments intact.
It never deletes RR tables, rolls back business data or replays the old finite
window. It restores configuration, not the old process or final Owner acceptance.

Still required for real adoption: actual dedicated gateway UID/GID and effective
socket access, mTLS devices mapping, OS-protected sealed-FD provider, exact
installation/preimage review, and the necessary Owner native authentication.
No current service, Owner session, blocked Goal, original98/G10/final acceptance,
real DB or cross-network payload is changed merely by this candidate.
