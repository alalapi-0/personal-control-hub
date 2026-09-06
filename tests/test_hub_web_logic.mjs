import assert from "node:assert/strict";
import test from "node:test";

const { __test } = await import("../src/hub/web/designs.js");
const { api, validRefreshCommand } = await import("../src/hub/web/common.js");

test("refresh recovery preserves the exact valid command and ignores corrupt browser data", () => {
  const command = { request_id: "refresh-1", project_ids: ["project-1"], expected_head: { sequence: 0, hash: "0".repeat(64) } };
  assert.equal(validRefreshCommand(command), command);
  for (const invalid of [null, {}, { ...command, project_ids: [] }, { ...command, project_ids: ["project-1", "project-1"] },
    { ...command, expected_head: { sequence: -1, hash: "0".repeat(64) } }, { ...command, expected_head: { sequence: 0 } }]) {
    assert.equal(validRefreshCommand(invalid), null);
  }
});

test("scope identity ignores member and page presentation order", () => {
  const first = { family_id: null, members: [
    { project_id: "b", pages: ["detail", "home"] },
    { project_id: "a", pages: ["review"] },
  ] };
  const second = { family_id: null, members: [
    { project_id: "a", pages: ["review"] },
    { project_id: "b", pages: ["home", "detail"] },
  ] };
  assert.equal(__test.scopeKey(first), __test.scopeKey(second));
});

test("effective decision is selected by exact scope", () => {
  const candidate = { id: "candidate-b", revision: 1, content_hash: "b".repeat(64),
    scope: { family_id: null, members: [{ project_id: "hub", pages: ["designs"] }] } };
  const event = { event: { action: "select", scope: candidate.scope,
    candidate: { id: "candidate-a", revision: 1, content_hash: "a".repeat(64) } }, superseded: false };
  assert.equal(__test.effectiveFor({ effective: { opaque: event } }, candidate), event);
  assert.equal(__test.exactEffectiveFor({ effective: { opaque: event } }, candidate), null);
  const exact = { ...event, event: { ...event.event, candidate: {
    id: candidate.id, revision: candidate.revision, content_hash: candidate.content_hash,
  } } };
  assert.equal(__test.exactEffectiveFor({ effective: { opaque: exact } }, candidate), exact);
  assert.equal(__test.effectiveFor({ effective: {} }, candidate), null);
});

test("candidate lists keep only the newest revision for each identity", () => {
  const values = [
    { id: "a", revision: 1 }, { id: "b", revision: 2 },
    { id: "a", revision: 3 }, { id: "b", revision: 1 },
  ];
  assert.deepEqual(__test.latestCandidates(values), [values[2], values[1]]);
});

test("preview roles come from imported artifact identities", () => {
  assert.equal(__test.artifactRole("hub-p5-overview-mobile"), "overview-mobile");
  assert.equal(__test.artifactRole("hub-p5-states-mobile"), "states-mobile");
  assert.equal(__test.artifactRole("hub-p5-compare-desktop"), "compare-desktop");
  assert.equal(__test.artifactRole("unclassified"), "default");
});

test("stale candidate and baseline bindings are explicit", () => {
  const hash = "a".repeat(64);
  const candidate = { kind: "candidate", id: "choice", revision: 1, content_hash: hash,
    baseline_bindings: [{ project_id: "hub", baseline_id: "before", baseline_revision: 1,
      baseline_hash: hash, pages: ["designs"] }] };
  const baseline = { kind: "baseline", id: "before", revision: 1, content_hash: hash,
    project_id: "hub", scope: { pages: ["designs"] } };
  assert.deepEqual(__test.staleReasons([baseline, candidate], candidate), []);
  const replacement = { ...baseline, revision: 2, content_hash: "b".repeat(64) };
  assert.deepEqual(__test.staleReasons([baseline, candidate, replacement], candidate),
    ["页面「designs」的原始版本已变化"]);
});

test("external artifact links allow only HTTPS Figma", () => {
  const artifact = (value) => ({ delivery: { kind: "figma_link", value } });
  assert.equal(__test.trustedFigmaUrl(artifact("https://www.figma.com/design/abc")), "https://www.figma.com/design/abc");
  assert.equal(__test.trustedFigmaUrl(artifact("http://www.figma.com/design/abc")), null);
  assert.equal(__test.trustedFigmaUrl(artifact("https://figma.example/design/abc")), null);
  assert.equal(__test.trustedFigmaUrl(artifact("javascript:alert(1)")), null);
});

test("stored retries remain exactly bound to candidate and API route", () => {
  const candidate = { id: "hub-p5", revision: 2, content_hash: "a".repeat(64) };
  const pending = {
    kind: "decision", path: "/api/designs/decisions", candidate: { ...candidate },
    command: { request_id: "ui-request-1", expected_revision: 7, candidate: { ...candidate } },
  };
  assert.equal(__test.validPending(pending, candidate), pending);
  assert.equal(__test.validPending(pending), pending);
  assert.equal(__test.validPending({ ...pending, path: "https://evil.invalid" }, candidate), null);
  assert.equal(__test.validPending({ ...pending, kind: "export" }, candidate), null);
  assert.equal(__test.validPending({ ...pending, command: { ...pending.command, expected_revision: 8 } },
    { ...candidate, revision: 3 }), null);
  assert.equal(__test.validPending(pending, { ...candidate, revision: 3 }), null);
});

test("mutation network failure is normalized as an unknown outcome", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("offline"); };
  try {
    await assert.rejects(api("/api/designs/decisions", {}),
      (error) => error.code === "NETWORK_UNAVAILABLE" && error.outcome === "UNKNOWN");
  } finally { globalThis.fetch = originalFetch; }
});

test("unparseable mutation response remains an unknown outcome", async () => {
  const originalFetch = globalThis.fetch;
  const responses = [
    { ok: true, status: 200, json: async () => ({ ok: true, data: { csrf_token: "runtime-only" } }) },
    { ok: true, status: 200, json: async () => null },
  ];
  globalThis.fetch = async () => responses.shift();
  try {
    await assert.rejects(api("/api/designs/decisions", {}),
      (error) => error.code === "INVALID_RESPONSE" && error.outcome === "UNKNOWN");
  } finally { globalThis.fetch = originalFetch; }
});
