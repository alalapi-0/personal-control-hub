# PCH RR adapter v1

Reader: the RR maintainer and PCH Agent. Update when routes, identity, schema or
deployment prerequisites change. This is a disabled local candidate, not a
deployment or a claim that a real feedback loop has passed.

The adapter uses PCH's existing registered TaskStore, with optional `rr_*`
tables created inside the first authenticated submission transaction. It does
not enqueue tasks, use Owner Cookies, expose the 8766 management handler, create
a second database, or execute feedback instructions. Existing tasks and drafts
retain their semantics. The existing 16 MiB database ceiling still applies;
capacity or unready storage produces 503 and never partial feedback.

## Explicit browser allowlist

| Method | Route | Result |
| --- | --- | --- |
| GET | `/__rr/v1/manifest` | Frozen public metadata, actual commit/build_id/dirty, opaque resource map and registered page paths |
| GET | `/__rr/v1/resources/{resource_id}` | The exact frozen public icon bytes |
| POST | `/__rr/v1/feedback` | Atomic new 201, identical retry 200, conflicting payload 409 |
| GET | `/__rr/v1/feedback/by-request/{UUID}` | Same device/project request recovery; query before retrying an uncertain submission |
| GET | `/__rr/v1/feedback/{feedback_id}` | Saved document, receipt and actual pending/agent_read/processed state |
| GET | `/__rr/v1/feedback/{feedback_id}/attachments/{attachment_id}` | Actual belonging attachment bytes and validated Content-Type |

No WS/SSE, original `/api`, Agent, ack or result routes are forwarded. Unknown or
unauthorized objects return 404. The only public resource is the fixed
`src/hub/web/icon.svg`, mapped as `pch-icon`; no project directory, screenshot
inventory, task, session or private path listing is exported. Resource bytes and
manifest are frozen at adapter startup. Feedback must match the frozen source,
resource and registered page path. Changed versions require a fresh manifest;
source commit, content build hash and dirty state are distinct facts.

## Authentication and local wiring

Every browser request requires both the configured Unix `SO_PEERCRED` UID and an
authenticated gateway assertion. The RR gateway must run as a dedicated UID
different from the PCH Owner/Agent. Its group may contain only that UID and the
Owner; it has access to the Browser socket and its dedicated key, without access
to the Owner installation, database, CLI, output or credential store. Root is
trusted. This protocol does not claim to resist root.

The gateway must generate identity from its verified real mTLS peer certificate
SHA-256 and fixed project binding. Client identity headers, forwarded headers,
loopback and payload project names confer no authority. A malicious gateway that
holds its signing key is inside the identity trust boundary: PCH cannot itself
observe its remote TLS connection. The gateway's real mTLS verification remains
a separate live acceptance requirement.

`GET /__rr-local/v1/challenge` is a local peer-UID-protected handshake on the
Browser socket. It **must not** be in the RR remote forwarding allowlist. Each
PCH adapter start creates a fresh challenge. For subsequent requests the gateway
sets `Origin` to the fixed origin and `X-PCH-RR-Auth` to unpadded base64url of
canonical UTF-8 JSON containing exactly:

`v`, `challenge`, `device_sha256`, `project_id`, `origin`, `method`, `path`,
`body_sha256`, `nonce`, `issued_at`, `expires`, `mac`.

`mac` is lowercase HMAC-SHA256 over canonical JSON of the other fields. Object
keys sort, separators have no whitespace and non-ASCII is not escaped. Protocol
version is integer 1. The signed method/path/body SHA and origin must match
exactly. UUID nonces are single-use within this instance, with a bounded cache.
Issued time must be within five seconds; expiry is at most sixty seconds after
issue and must not be past. Old-instance assertions fail the challenge check.
Keys never enter command arguments, source, logs or evidence.

The formal local entry is `python scripts/pch_rr.py`. It has no identity,
database-root or fixture override flags. It derives the installation root and
`project.yaml` identity and verifies the existing TaskStore registration. All
commands refuse absent or disabled provisioning. It requires an Owner-only
installation root (0700), private data directories and 0600 ledger/registration.
Symlinks, shared writable ancestors or code and unsafe ownership are rejected;
an owner-exclusive primary group may have writable ancestors.

Future provisioning, which this candidate does not perform, must supply:

- Owner-only `data/rr-adapter.json` (0600), with exactly `schema:1`,
  `enabled:true`, `project_id:"personal-control-hub"`, numeric `agent_uid`,
  distinct non-root `gateway_uid`, dedicated `gateway_gid`, fixed HTTPS `origin`
  and `devices` mapping verified certificate SHA-256 to this project ID.
- An already provisioned `/run/pch-rr` directory, Owner-owned, gateway group,
  mode0750. The Browser socket is `/run/pch-rr/browser.sock`, mode0660. Existing
  paths are never overwritten. Closing removes only the socket's owned inode.
- A dedicated 32–64-byte key through the OS provider's `CREDENTIALS_DIRECTORY`,
  in protected `pch-rr-gateway-key`; the gateway receives the same dedicated key
  through its own protected provider. This does not use the Owner login proof.
- Explicit service management, actual mTLS device registration and a reviewed
  forwarding allowlist. No production fixture mode is accepted by the CLI.

## Submission and receipts

Multipart fields: `feedback` (JSON, at most16 KiB), optional `attachment.0` and
`attachment.1` (at most1 MiB each), with a total ceiling2 MiB+32 KiB. JSON has
exact fields `schema`, `client_request_id`, `project_id`, `page_path`, `source`,
`resource`, `description`, `expected`, `actual`, `steps`, `submitted_at`,
`attachments`. Schema is integer1; request ID is a canonical UUID; page paths
contain no query, fragment, traversal or token. Source fields are
`commit/build_id/dirty`; resource fields are `id/sha256/mode/preview_version`.
Attachment declarations contain `attachment_id/size/sha256`. Preserve submitted
time, document and bytes when retrying. Duplicate/unknown JSON keys, floats,
invalid UTF-8 or surrogates are rejected.

Allowed attachments: UTF-8 plain text, PNG, JPEG and WebP. MIME must match actual
bounded container structure, not a filename extension. PNG also checks CRC,
bounded decompression and scanline filter framing; Adam7 is unsupported. Images
are limited to4096×4096 and PNG decompression to16 MiB. JPEG/WebP checks cover
container/frame/scan/header structure, not complete pixel decoding. Filenames
are display metadata only. Attachments are BLOBs in the same transaction and
never enter preview TTL/LRU.

The receipt includes schema/project/request/feedback IDs, immutable content
revision1, accepted time, canonical body/payload SHA-256 and actual attachment
size/SHA/type/name. The canonical document includes attachment length/digest
declarations; thus `payload_sha256` and `body_sha256` match. Original multipart
boundary bytes and filename changes are not part of canonical payload identity.
409 preserves the original record; 400 means format, 413 size, 415 MIME/content,
503 unavailable/unconfirmed persistence. After an uncertain response, use the
same UUID query, then the original payload. Errors and request logs omit content.

## Protected local Agent operations

These commands execute only as the PCH Owner UID, never through the RR gateway:

```text
python scripts/pch_rr.py pending [--cursor CURSOR]
python scripts/pch_rr.py read FEEDBACK_ID
python scripts/pch_rr.py ack FEEDBACK_ID --proof PRIVATE_PROOF_FILE
python scripts/pch_rr.py result FEEDBACK_ID --proof PRIVATE_PROOF_FILE --handling PRIVATE_JSON_FILE
```

Pending pages contain at most20 records. Read verifies current canonical body and
all actual attachment bytes, writes private opaque files and proof beneath
`data/rr-agent-reads`, and returns context plus their local locations. The Agent
must inspect the actual content before ack. The proof includes read ID,
revision, body SHA and **all** attachment IDs/lengths/SHAs; same UID/project/read
binding is required. Read alone does not change browser state to `agent_read`;
only a matching ack does. Result requires that ack, independently records
`summary/references/processed_at`, and does not execute a task. Handling JSON has
exactly `summary` and `references`. Changed proof or handling conflicts with409.

## Verification scope and remaining inputs

Targeted tests exercise isolated TaskStore fixtures, transport, authenticated
gateway-to-store submission/recovery/attachment read and the local Agent
read→ack→result path. Offline restoration validates complete optional RR schema,
body, actual BLOB digests and receipt relationships; legacy and ordinary drafts
remain supported. Tests do not inspect or migrate real business data.

No real gateway UID/group, mTLS device/project registration, key provisioning,
origin or deployment has been supplied or activated. No VPS/cross-network
business test, real feedback ID or real Agent handling acceptance is claimed.
The existing 8766 service, Owner session, blocked PCH Goal, original98/G10 and
final Owner acceptance remain separate and unchanged.
