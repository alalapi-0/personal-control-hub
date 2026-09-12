# HUB-GOV-2026-09-06-TC10 · version 2 · ACTIVE

Read-only Governor /root/tc4_governor supplied this revision. Root owns activation, evidence and effects. Reader: Root, fresh Judge and Governor. Update only a frozen dimension. No numerical budget; prior work and failures remain cumulative.

## Objective and authority

Implement the owner’s selection: “系统钥匙串保存密钥，Hub 保存统一索引，并适配现有工具取用”. Thread 01a071cc-f2c6-76c2-bc43-f300db610b0b, turn 01a07400-d059-7f90-a685-2e3250945f2d, message 01a07403-e994-7d11-854c-1799cc3d35e8. Inventory exact configured global tool inputs, migrate confirmed plaintext tool credentials into macOS Keychain, adapt consumers and keep one nonsecret Hub index.

Root exclusively owns Keychain, exact global configuration, launchers, Hub index, STATE, evidence and Git. Global scope: ~/.codex/config.toml, ~/.cursor/mcp.json, ~/.claude.json root mcpServers, obsolete ~/.claude/settings.json Figma declaration, directly referenced credential launchers and relevant shell startup exports. No browser sessions, project business env, unrelated authentication or source repositories.

## Revision evidence and preservation

The initial inventory missed five root MCP entries in ~/.claude.json; its original hash remains unchanged. Claude officially ignores settings.json.mcpServers and reads user scope from ~/.claude.json. Cached figma-developer-mcp 0.13.2 needs --stdio (or NODE_ENV=cli) for stdio. Therefore v1’s unchanged membership/args constraint could not deliver working consumer adaptation. v2 fixes only these prerequisites and the two newly identified Claude plaintext entries. Earlier Figma Keychain equality, /me HTTP200, plaintext removal and three existing secure-launcher checks remain valid.

Final inventory: 19 effective consumer declarations (Codex7, Cursor6, Claude6). Existing Claude servers chrome-devtools, context7, github, playwright, stitch retain identity. Preserve all other root fields, eight project mappings, OAuth/application state, models, permissions and all unrelated files. Existing Stitch Keychain and GitHub CLI routes remain distinct and unchanged. No client restart, hot-reload claim, package install/update, provider-data writes or Codex Figma OAuth change.

## Authorized effects

Never put credential values, reversible encodings or credential fingerprints in Hub/Git/docs/evidence/logs/shell history/temporary files/arguments/output/backups. Credentials enter controlled memory and the directly launched consumer environment only. No plaintext fallback or secret-bearing errors. Existing configuration bytes stay in memory until cutover; no secret backup. Compare exact bytes before each write and preserve unrelated JSON bytes or prove exact semantic preservation. Root alone performs effects.

1. Figma: retain service com.openai.codex.mcp.figma-api-key/account alalapi and the Hub wrapper. Add one user-level figma MCP in ~/.claude.json using /usr/bin/python3 and scripts/tool_credentials.py run figma-api -- npx -y figma-developer-mcp --stdio. --stdio is the only new production downstream argument; do not add NODE_ENV or persistent telemetry flags. After successful new-process validation, delete only obsolete settings.json.mcpServers.figma, retaining other settings and an empty parent map if needed. If an unknown figma entry appears concurrently, stop that entry and resolve ownership.
2. Claude GitHub and Stitch: the literal env credentials differ from the existing secure routes, so keep them separate. Migrate each into a distinct owned generic-password Keychain locator and use the same fail-closed Hub wrapper. Preserve exact downstream GitHub npx -y @modelcontextprotocol/server-github and Stitch npx -y @_davideast/stitch-mcp proxy. Remove only the corresponding plaintext env entry after native equality and fresh-process behavior checks pass. Never replace them with another account’s credential or overwrite existing Keychain items.
3. For GitHub, baseline official GET /user is401. Safe migration may preserve this invalid behavior: Keychain write, in-memory equality, fresh-process /user401, then plaintext removal. Index storage, consumer_route and auth_status separately; invalid auth remains invalid. Switching to the valid GitHub CLI route is a later owner identity choice and is not needed for secure storage migration.
4. Stitch validation is limited to official MCP initialize and immediate close, with no tools/list, tools/call or business data. Distinguish successful handshake from proven provider authentication; record limits if initialize also succeeds without credentials. Its safe migration must preserve observed behavior and exact credential value.
5. Figma MCP validation may use temporary FRAMELINK_TELEMETRY=off / supported no-telemetry flag only inside the probe process, not production configuration. Only initialize, tools/list and close are allowed; no tools/call/file data. Existing Figma /me200 evidence is reused unless a changed route requires revalidation.

## Acceptance

1. All19 consumers uniquely covered with actual configuration references, authentication kinds, environment/header names and credential ownership; three Claude plaintext candidates have explicit final classifications.
2. One machine-readable Hub index has only stable nonsecret IDs, locators, consumers, runtime env, migration status and redacted verification. OAuth stays separate.
3. Before each plaintext removal: native Keychain insertion without overwrite, in-memory equality, fresh consumer retrieval and preserved provider behavior. GitHub401 is recorded as auth invalid, never authenticated. Secret values are absent from exact owned configuration, evidence, staged content and task-created temp/backup paths.
4. Missing, locked, denied, empty or invalid Keychain results stop before launch. Tests verify exact argument forwarding, environment preservation and fail-closed behavior without leaking values.
5. Claude structural changes are limited to new figma, credential wrapper/env changes for github/stitch, and removal of obsolete settings Figma declaration. All other configuration fields/objects and protected Hub/global hashes remain unchanged. Original source values are represented only by redaction markers in evidence.
6. New process discovers Claude user-scope figma; actual configured command completes MCP initialize and tool enumeration with telemetry disabled only for the probe. Existing session adoption remains UNVERIFIED. JSON syntax, index, focused tests, repository and state consistency checks pass.
7. Existing secure Stitch/GitHub routes and Codex/Cursor configurations remain unchanged. No package install, provider data write, external project change, browser-auth copying or OAuth connection claim.
8. Register exact v2 candidate with preimages/preservation proofs and reproducible checks. Fresh Judge PASS then Governor APPROVE. Review/decision/delivery and STATE status/next-action/timestamp metadata do not reopen unchanged content; no self-hash.
9. After approval, commit only Hub-owned nonsecret files, normal push current tracking branch and verify exact remote SHA. Never include global files or Keychain in Git. TC10 does not complete Hub UI/Figma choice/overall Goal.

## Recovery and isolated failure

Retain migrated Keychain items and secure wrapper. Never restore plaintext. If a newly added Figma user entry fails verification and still matches the owned postimage, remove only that entry; the obsolete ignored declaration is not a working fallback. A classification or permission problem protects only the exact affected MCP object, not unrelated authorized entries. Concurrent changes prevent blind overwrite or rollback. Preserve a precise resume point and continue independent work.

Sources: [Claude MCP](https://code.claude.com/docs/en/mcp), [Claude config diagnosis](https://code.claude.com/docs/en/debug-your-config), cached package0.13.2 dist/bin.js and telemetry module, [GitHub authenticated user](https://docs.github.com/en/rest/users/users#get-the-authenticated-user), [Stitch setup](https://stitch.withgoogle.com/docs/mcp/setup) and configured package0.9.0 endpoint implementation.
