import { api, button, el, navigate, notify } from "./common.js";

const DRAFT_PREFIX = "hub-design-feedback:";
const PENDING_KEY = "hub-design-pending-command";
const EXPORT_KEY = "hub-design-last-export";
const CONFLICTS = new Set([
  "REVISION_CONFLICT", "CONCURRENCY_CONFLICT", "REQUEST_CONFLICT",
  "CANDIDATE_STALE", "BASELINE_STALE", "FAMILY_STALE",
]);
const ACTION_LABELS = {
  select: "选择此设计", request_changes: "请求修改", defer: "稍后决定", withdraw: "撤回决定",
};
const STATUS_LABELS = {
  select: "已选择", request_changes: "已请求修改", defer: "已暂缓", withdraw: "已撤回",
};

function storageGet(key) {
  try { return localStorage.getItem(key); } catch (_) { return null; }
}
function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch (_) { /* storage may be unavailable */ }
}
function storageRemove(key) {
  try { localStorage.removeItem(key); } catch (_) { /* storage may be unavailable */ }
}
function parseStored(key) {
  try { return JSON.parse(storageGet(key)); } catch (_) { return null; }
}
function sameCandidate(left, right) {
  return Boolean(left && right && left.id === right.id && left.revision === right.revision && left.content_hash === right.content_hash);
}
function validPending(value, candidate = null) {
  if (!value || !value.command || !value.candidate) return null;
  if (!["/api/designs/decisions", "/api/designs/exports"].includes(value.path)) return null;
  if ((value.kind === "decision") !== (value.path === "/api/designs/decisions") || !["decision", "export"].includes(value.kind)) return null;
  const reference = value.command.candidate;
  if (!sameCandidate(reference, value.candidate) || (candidate && !sameCandidate(reference, candidate))) return null;
  if (!Number.isInteger(value.command.expected_revision) || value.command.expected_revision < 0 ||
      typeof value.command.request_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(value.command.request_id)) return null;
  return value;
}
function safeStoredExport(value) {
  if (!value || typeof value.href !== "string" || typeof value.request_id !== "string") return null;
  try {
    const url = new URL(value.href, location.origin);
    if (url.origin !== location.origin || !url.pathname.startsWith("/api/exports/")) return null;
    return { ...value, href: `${url.pathname}${url.search}` };
  } catch (_) { return null; }
}
function identity(candidate) {
  return { id: candidate.id, revision: candidate.revision, content_hash: candidate.content_hash };
}
function scopeKey(scope) {
  const members = [...(scope?.members || [])].map((member) => ({
    project_id: member.project_id, pages: [...member.pages].sort(),
  })).sort((a, b) => a.project_id.localeCompare(b.project_id));
  return JSON.stringify({ family_id: scope?.family_id ?? null, members });
}
function effectiveFor(snapshot, candidate) {
  return Object.values(snapshot.effective || {}).find((item) =>
    !item.superseded && scopeKey(item.event?.scope) === scopeKey(candidate.scope),
  ) || null;
}
function exactEffectiveFor(snapshot, candidate) {
  const current = effectiveFor(snapshot, candidate);
  return current && sameCandidate(current.event?.candidate, candidate) ? current : null;
}
function latestCandidates(candidates) {
  const latest = new Map();
  for (const candidate of candidates) {
    const current = latest.get(candidate.id);
    if (!current || candidate.revision > current.revision) latest.set(candidate.id, candidate);
  }
  return [...latest.values()];
}
function focusRendered(container, selector) {
  if (!selector) return;
  queueMicrotask(() => {
    if (container.isConnected) (container.querySelector(selector) || container.querySelector('#design-feedback'))?.focus();
  });
}
function staleReasons(facts, candidate) {
  const reasons = [];
  const candidateRevisions = facts.filter((fact) => fact.kind === "candidate" && fact.id === candidate.id);
  const latestCandidate = candidateRevisions.sort((a, b) => b.revision - a.revision)[0];
  if (!latestCandidate || latestCandidate.revision !== candidate.revision || latestCandidate.content_hash !== candidate.content_hash) reasons.push("候选已有新修订");
  for (const binding of candidate.baseline_bindings || []) {
    for (const page of binding.pages || []) {
      const latest = [...facts].reverse().find((fact) => fact.kind === "baseline" && fact.project_id === binding.project_id && fact.scope?.pages?.includes(page));
      if (!latest || latest.id !== binding.baseline_id || latest.revision !== binding.baseline_revision || latest.content_hash !== binding.baseline_hash) {
        reasons.push(`页面「${page}」的原始版本已变化`);
      }
    }
  }
  if (candidate.family_binding) {
    const latest = facts.filter((fact) => fact.kind === "design_family" && fact.id === candidate.family_binding.id).sort((a, b) => b.revision - a.revision)[0];
    if (!latest || latest.revision !== candidate.family_binding.revision || latest.content_hash !== candidate.family_binding.content_hash) reasons.push("共享设计规范已变化");
  }
  return [...new Set(reasons)];
}
function candidateTitle(candidate) {
  return candidate.visual?.differences?.[0] || `未命名候选 · 修订 ${candidate.revision}`;
}
function projectLabel(id, names) { return names[id] || id; }
function formatTime(value) {
  if (!value) return "时间未知";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", { hour12: false });
}
function idPart() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
function draftKey(candidate) { return `${DRAFT_PREFIX}${candidate.id}:${candidate.revision}`; }
function errorText(error) {
  const labels = {
    DESIGN_STORE_UNAVAILABLE: "设计资料暂时不可用，请稍后重试。",
    DESIGN_STORE_CORRUPT: "设计资料未通过完整性检查。",
    REVISION_CONFLICT: "资料已更新。已保留反馈，请重读最新内容后再次确认。",
    CONCURRENCY_CONFLICT: "另一项写入正在进行。已保留反馈，请重读后再次确认。",
    REQUEST_CONFLICT: "这个请求标识对应的内容不一致。请重读后发起新请求。",
    CANDIDATE_STALE: "此候选已有更新。已保留反馈，请重读最新版本。",
    BASELINE_STALE: "原始版本已有更新。已保留反馈，请重读后再次确认。",
    FAMILY_STALE: "共享设计规范已有更新。已保留反馈，请重读后再次确认。",
    EXPORT_REJECTED: "导出未通过绑定检查。",
  };
  return labels[error?.code] || `操作未完成（${error?.code || "网络错误"}）。`;
}
function importedLabel(classification) {
  const labels = { imported: "导入的设计稿 · 示例内容", mock: "示例演练", "dry-run": "试运行预览", real: "真实资料" };
  return labels[classification] || "资料性质未知";
}
function artifactUrl(artifact, candidate) {
  if (artifact?.delivery?.kind !== "registered_artifact") return null;
  return `/api/artifacts/${encodeURIComponent(artifact.id)}?candidate_id=${encodeURIComponent(candidate.id)}&candidate_revision=${candidate.revision}`;
}
function trustedFigmaUrl(artifact) {
  const raw = artifact?.delivery?.kind === "figma_link" ? artifact.delivery.value : null;
  if (!raw) return null;
  try {
    const url = new URL(raw);
    return url.protocol === "https:" && (url.hostname === "figma.com" || url.hostname === "www.figma.com") ? url.href : null;
  } catch (_) { return null; }
}
function figmaUrl(candidate) {
  const ref = candidate.figma_ref || {};
  if (!ref.file_key) return null;
  const url = new URL(`https://www.figma.com/design/${encodeURIComponent(ref.file_key)}`);
  if (ref.node_id) url.searchParams.set("node-id", ref.node_id);
  return url.href;
}

function previewDialog(image, title) {
  const restore = document.activeElement;
  const dialog = el("dialog", { className: "dialog", "aria-labelledby": "preview-dialog-title" });
  const close = button("关闭大图", () => dialog.close(), { className: "subtle" });
  dialog.append(
    el("div", { className: "row" }, el("h2", { id: "preview-dialog-title" }, title), close),
    el("img", { src: image.src, alt: image.alt }),
  );
  dialog.addEventListener("close", () => { dialog.remove(); restore?.focus?.(); });
  document.body.append(dialog);
  dialog.showModal();
  close.focus();
}

function previewMedia(artifact, candidate, title) {
  if (!artifact) return el("div", { className: "empty" }, "没有绑定预览图；文字资料仍可在导出包中核对。");
  const src = artifactUrl(artifact, candidate);
  if (src) {
    const media = el("div", { className: "stack" });
    let image = null;
    const zoom = button("放大预览", () => image && previewDialog(image, title), { className: "subtle", disabled: true });
    const retry = button("重新载入预览", async () => {
      retry.disabled = true;
      try { await api("/api/designs"); load(); }
      catch (_) { media.replaceChildren(el("div", { className: "empty warning" }, "会话或预览仍不可用，请稍后重试。"), retry, zoom); }
      finally { retry.disabled = false; }
    }, { className: "subtle" });
    const load = () => {
      zoom.disabled = true;
      image = el("img", { className: "preview", src, alt: `${title}预览` });
      image.addEventListener("load", () => { zoom.disabled = false; });
      image.addEventListener("error", () => {
        image = null;
        zoom.disabled = true;
        media.replaceChildren(el("div", { className: "empty warning" }, "预览未能读取，资料记录仍保留。"), retry, zoom);
      }, { once: true });
      image.addEventListener("click", () => image && previewDialog(image, title));
      media.replaceChildren(image, zoom);
    };
    load();
    return media;
  }
  const trusted = trustedFigmaUrl(artifact);
  return trusted
    ? el("a", { href: trusted, target: "_blank", rel: "noopener noreferrer" }, "在 Figma 中查看")
    : el("p", { className: "muted" }, "外部预览地址未通过 HTTPS Figma 域名检查。");
}

function artifactRole(id) {
  const lower = String(id).toLowerCase();
  const surface = lower.includes("overview") ? "overview" : lower.includes("compare") ? "compare" : lower.includes("state") ? "states" : "preview";
  const viewport = lower.includes("mobile") ? "mobile" : lower.includes("desktop") ? "desktop" : "default";
  if (surface !== "preview" || viewport !== "default") return `${surface}-${viewport}`;
  return "default";
}
function chooseArtifact(artifacts, role) {
  return artifacts.find((item) => artifactRole(item.id) === role) || artifacts[0] || null;
}

function renderList(container, snapshot, candidates, names) {
  const byProject = new Map();
  for (const candidate of latestCandidates(candidates)) {
    for (const member of candidate.scope?.members || []) {
      if (!byProject.has(member.project_id)) byProject.set(member.project_id, []);
      byProject.get(member.project_id).push(candidate);
    }
  }
  container.replaceChildren(
    el("section", { className: "stack", "aria-labelledby": "design-list-title" },
      el("div", { className: "stack" },
        el("p", { className: "caption" }, "DESIGN REVIEW"),
        el("h1", { id: "design-list-title" }, "设计选择"),
        el("p", { className: "muted" }, "在原始版本与候选设计之间逐项核对。选择设计不会授予代码实现权限。"),
      ),
      byProject.size
        ? el("div", { className: "project-list" }, [...byProject.entries()].map(([id, items]) => {
            const latest = [...items].sort((a, b) => b.revision - a.revision)[0];
            const decided = items.filter((candidate) => exactEffectiveFor(snapshot, candidate)).length;
            return el("a", { className: "project-row", href: `#designs/${encodeURIComponent(id)}` },
              el("h2", {}, projectLabel(id, names)),
              el("span", {}, `${items.length} 个候选 · 最新：${candidateTitle(latest)}`),
              el("span", { className: "caption" }, decided ? `${decided} 个候选有当前决定` : "尚未决定"),
            );
          }))
        : el("div", { className: "empty" }, el("h2", {}, "暂无设计候选"), el("p", { className: "muted" }, "设计资料中还没有可比较的候选。")),
    ),
  );
}

function renderProject(container, snapshot, candidates, projectId, names) {
  const projectCandidates = latestCandidates(candidates.filter((candidate) =>
    candidate.scope?.members?.some((member) => member.project_id === projectId),
  )).sort((a, b) => b.revision - a.revision);
  container.replaceChildren(
    el("section", { className: "stack", "aria-labelledby": "project-design-title" },
      el("div", { className: "stack" },
        el("a", { href: "#designs" }, "← 全部设计"),
        el("h1", { id: "project-design-title" }, projectLabel(projectId, names)),
        el("p", { className: "muted" }, "请选择一个候选，查看原始版本、预览和完整决策记录。"),
      ),
      projectCandidates.length
        ? el("div", { className: "project-list" }, projectCandidates.map((candidate) => {
            const current = exactEffectiveFor(snapshot, candidate);
            return el("a", { className: "project-row", href: `#designs/${encodeURIComponent(projectId)}/${encodeURIComponent(candidate.id)}` },
              el("h2", {}, candidateTitle(candidate)),
              el("span", {}, `修订 ${candidate.revision} · ${importedLabel(candidate.classification)}`),
              el("span", { className: current?.stale ? "warning caption" : "caption" },
                current ? `${STATUS_LABELS[current.event.action]}${current.stale ? " · 已过期" : ""}` : "尚未决定"),
            );
          }))
        : el("div", { className: "empty" }, el("h2", {}, "这个项目暂无设计候选"), button("返回设计列表", () => navigate("#designs"))),
    ),
  );
}

function currentCandidate(candidates, projectId, candidateId) {
  return candidates.filter((candidate) => candidate.id === candidateId &&
    candidate.scope?.members?.some((member) => member.project_id === projectId))
    .sort((a, b) => b.revision - a.revision)[0] || null;
}

function commandFor(action, candidate, revision, feedback, supersedes) {
  const suffix = idPart();
  return {
    request_id: `ui-${action}-${suffix}`,
    event_id: `ui-event-${suffix}`,
    created_at: new Date().toISOString(),
    expected_revision: revision,
    action,
    candidate: identity(candidate),
    scope: candidate.scope,
    feedback: feedback || null,
    supersedes: supersedes || null,
  };
}

async function submitPending(pending, onSuccess, onConflict) {
  storageSet(PENDING_KEY, JSON.stringify(pending));
  notify(pending.kind === "export" ? "正在准备导出…" : "正在保存决定…");
  try {
    const result = await api(pending.path, pending.command);
    storageRemove(PENDING_KEY);
    notify(pending.kind === "export" ? "导出已准备好。" : "决定已保存。", "success");
    await onSuccess(result);
  } catch (error) {
    if (CONFLICTS.has(error.code) && error.outcome === "NOT_COMMITTED") {
      storageRemove(PENDING_KEY);
      notify(errorText(error), "error");
      await onConflict(error);
      return;
    }
    notify(`${errorText(error)} 请求已保留，可按原内容重试；不会自动提交。`, "error");
    throw error;
  }
}

function recoveryPanel(candidate, rerender, pending) {
  if (!pending) return null;
  const belongsHere = sameCandidate(pending.candidate, candidate);
  const recoveryResult = el("div", { id: "pending-recovery-result", tabIndex: -1, className: "stack", role: "status", "aria-live": "polite" });
  return el("section", { id: "pending-recovery", tabIndex: -1, className: "panel stack warning", "aria-labelledby": "pending-title" },
    el("h2", { id: "pending-title" }, "有一项结果不确定的操作"),
    el("p", {}, belongsHere
      ? "这项操作属于当前候选。你可以按完全相同的内容重试，或保留现有结果并清除恢复提示。"
      : "这项操作属于另一候选或旧修订。为避免覆盖恢复记录，当前候选的决定与导出已停用。"),
    el("div", { className: "toolbar" },
      button("按原请求重试", async (event) => {
        event.currentTarget.disabled = true;
        try {
          await submitPending(pending, async (result) => {
            if (pending.kind === "export") {
              exportDownload(result, pending.command, recoveryResult);
              recoveryResult.prepend(el("p", {}, belongsHere ? "当前候选的旧导出请求已恢复。" : "另一候选或旧修订的导出请求已恢复。"));
              recoveryResult.append(button("完成恢复并返回当前候选", () => rerender("#design-feedback"), { className: "subtle" }));
            } else {
              await rerender("#decision-status");
            }
          }, (error) => rerender("#design-conflict", error));
        } catch (_) { event.currentTarget.disabled = false; }
      }, { className: "primary" }),
      button("清除本地恢复提示", () => { storageRemove(PENDING_KEY); rerender("#design-feedback"); }, { className: "subtle" }),
    ),
    recoveryResult,
  );
}

function exportDownload(result, command, target = null) {
  const query = new URLSearchParams({
    candidate_id: command.candidate.id,
    candidate_revision: String(command.candidate.revision),
    candidate_hash: command.candidate.content_hash,
    store_revision: String(command.expected_revision),
  });
  const href = `/api/exports/${encodeURIComponent(command.request_id)}?${query}`;
  storageSet(EXPORT_KEY, JSON.stringify({ href, request_id: command.request_id, sha256: result.sha256 }));
  const link = el("a", { href, download: `${command.request_id}.zip`, className: "button primary" }, "下载校验后的 ZIP");
  if (!target?.isConnected) return;
  target.replaceChildren(
    link,
    el("details", { className: "diagnostics" }, el("summary", {}, "查看导出校验信息"),
      el("p", { className: "caption" }, `SHA-256 ${result.sha256 || result.expected_sha256 || "服务未返回"}`)),
  );
  target.focus();
}

function renderCompare(container, snapshot, candidate, projectId, names, conflict = null, focusTarget = null) {
  const facts = snapshot.facts || [];
  const artifacts = new Map(facts.filter((fact) => fact.kind === "artifact_ref").map((fact) => [fact.id, fact]));
  const candidateArtifacts = (candidate.artifact_bindings || []).map((binding) => artifacts.get(binding.artifact_id)).filter(Boolean);
  const baselineRecords = (candidate.baseline_bindings || []).map((binding) => facts.find((fact) =>
    fact.kind === "baseline" && fact.id === binding.baseline_id && fact.revision === binding.baseline_revision && fact.content_hash === binding.baseline_hash,
  )).filter(Boolean);
  const baselineArtifacts = baselineRecords.flatMap((baseline) => (baseline.artifact_bindings || []).map((binding) => artifacts.get(binding.artifact_id)).filter(Boolean));
  const viewRoles = [...new Set([...candidateArtifacts, ...baselineArtifacts].map((item) => artifactRole(item.id)))];
  const targetView = matchMedia("(max-width: 700px)").matches ? "overview-mobile" : "overview-desktop";
  const preferred = viewRoles.includes(targetView) ? targetView : (viewRoles[0] || "default");
  const scopeCurrent = effectiveFor(snapshot, candidate);
  const current = exactEffectiveFor(snapshot, candidate);
  const history = (snapshot.history || []).filter((item) => item.event?.candidate?.id === candidate.id);
  const stale = staleReasons(facts, candidate);
  const storedPending = validPending(parseStored(PENDING_KEY));
  const actionBlocked = Boolean(storedPending) || stale.length > 0;
  let saving = false;
  const setSaving = (value) => {
    saving = value;
    container.querySelectorAll("button").forEach((control) => {
      if (value) {
        control.dataset.wasDisabled = control.disabled ? "true" : "false";
        control.disabled = true;
      } else {
        control.disabled = control.dataset.wasDisabled === "true";
        delete control.dataset.wasDisabled;
      }
    });
  };
  const feedback = el("textarea", { id: "design-feedback", rows: 4, placeholder: "写下需要保留、修改或选择的理由。" });
  feedback.value = storageGet(draftKey(candidate)) || "";
  feedback.addEventListener("input", () => storageSet(draftKey(candidate), feedback.value));
  const originalCanvas = el("article", { className: "canvas", "aria-labelledby": "original-title" });
  const candidateCanvas = el("article", { className: "canvas", "aria-labelledby": "candidate-title" });
  originalCanvas.id = "original-canvas";
  candidateCanvas.id = "candidate-canvas";
  const renderCanvases = (role) => {
    const platform = role.endsWith("-mobile") ? "mobile" : role.endsWith("-desktop") ? "desktop" : null;
    const baseline = baselineRecords.find((item) => item.scope?.viewport?.platform === platform) || baselineRecords[0];
    const newSurface = baseline?.source?.kind === "new_surface_spec";
    originalCanvas.replaceChildren(
      el("h2", { id: "original-title" }, "原始版本"),
      el("p", { className: "caption" }, baseline ? `创建时基线 · 修订 ${baseline.revision}` : "未找到精确绑定的基线"),
      newSurface
        ? el("div", { className: "empty" }, el("strong", {}, "创建前无图形界面"), el("p", { className: "muted" }, "这是新界面规范，因此没有可截图的旧页面。"))
        : previewMedia(chooseArtifact(baselineArtifacts, role), candidate, "原始版本"),
    );
    candidateCanvas.replaceChildren(
      el("h2", { id: "candidate-title" }, "候选设计"),
      el("p", { className: "caption" }, importedLabel(candidate.classification)),
      previewMedia(chooseArtifact(candidateArtifacts, role), candidate, candidateTitle(candidate)),
    );
  };
  renderCanvases(preferred);

  const mobileTabs = el("div", { className: "toolbar mobile-only", "aria-label": "比较页面" });
  let selectedMobileTab = "candidate";
  const setMobileTab = (target) => {
    selectedMobileTab = target;
    const mobile = matchMedia("(max-width: 700px)").matches;
    originalCanvas.hidden = mobile && target !== "original";
    candidateCanvas.hidden = mobile && target !== "candidate";
    [...mobileTabs.children].forEach((tab) => tab.setAttribute("aria-pressed", String(tab.dataset.target === target)));
  };
  mobileTabs.append(
    button("原始版本", () => setMobileTab("original"), { "data-target": "original", "aria-controls": "original-canvas", "aria-pressed": "true" }),
    button("候选设计", () => setMobileTab("candidate"), { "data-target": "candidate", "aria-controls": "candidate-canvas", "aria-pressed": "false" }),
  );
  const viewport = matchMedia("(max-width: 700px)");
  viewport.onchange = () => setMobileTab(selectedMobileTab);

  const rerender = async (nextFocus = null, nextConflict = null) => {
    const fresh = await api("/api/designs");
    const freshCandidates = fresh.facts.filter((fact) => fact.kind === "candidate");
    const next = currentCandidate(freshCandidates, projectId, candidate.id);
    if (!next) return renderCompare(container, fresh, candidate, projectId, names, nextConflict || { code: "CANDIDATE_STALE" }, nextFocus);
    renderCompare(container, fresh, next, projectId, names, nextConflict, nextFocus);
  };
  const act = async (action) => {
    if (saving || storedPending) return;
    if (action === "request_changes" && !feedback.value.trim()) {
      feedback.focus(); notify("请求修改前，请写下具体反馈。", "error"); return;
    }
    setSaving(true);
    const command = commandFor(action, candidate, snapshot.store_revision, feedback.value.trim(), scopeCurrent?.event?.id);
    const pending = { kind: "decision", path: "/api/designs/decisions", command, candidate: identity(candidate) };
    try {
      await submitPending(pending, async () => {
        storageRemove(draftKey(candidate));
        await rerender("#decision-status");
      }, async (error) => {
        const fresh = await api("/api/designs");
        const next = currentCandidate(fresh.facts.filter((fact) => fact.kind === "candidate"), projectId, candidate.id) || candidate;
        storageSet(draftKey(next), feedback.value);
        renderCompare(container, fresh, next, projectId, names, error, "#design-conflict");
      });
    } catch (_) { renderCompare(container, snapshot, candidate, projectId, names, null, "#pending-recovery"); }
  };
  const exportCandidate = async () => {
    if (saving || storedPending) return;
    setSaving(true);
    const command = { request_id: `ui-export-${idPart()}`, expected_revision: snapshot.store_revision, candidate: identity(candidate) };
    const pending = { kind: "export", path: "/api/designs/exports", command, candidate: identity(candidate) };
    try { await submitPending(pending, (result) => { setSaving(false); exportDownload(result, command, exportResult); }, (error) => rerender("#design-conflict", error)); }
    catch (_) { renderCompare(container, snapshot, candidate, projectId, names, null, "#pending-recovery"); }
  };
  const figureLink = figmaUrl(candidate);
  const storedExport = safeStoredExport(parseStored(EXPORT_KEY));
  const lastExport = storedExport && new URL(storedExport.href, location.origin).searchParams.get("candidate_hash") === candidate.content_hash ? storedExport : null;
  const viewSelect = el("select", { id: "preview-view", onChange: (event) => renderCanvases(event.target.value) },
    viewRoles.map((role) => el("option", { value: role, selected: role === preferred }, ({
      "overview-desktop": "桌面总览", "overview-mobile": "手机总览", "compare-desktop": "桌面对照", "states-mobile": "手机状态",
    })[role] || "其他预览")),
  );
  const exportResult = el("div", { id: "export-result", tabIndex: -1, className: "stack", role: "status", "aria-live": "polite" });
  const recovery = recoveryPanel(candidate, rerender, storedPending);
  container.replaceChildren(
    el("section", { className: "stack design-compare", "aria-labelledby": "compare-title" },
      el("div", { className: "stack" },
        el("a", { href: `#designs/${encodeURIComponent(projectId)}` }, `← ${projectLabel(projectId, names)}的候选`),
        el("p", { className: "caption" }, "ORIGINAL / CANDIDATE"),
        el("h1", { id: "compare-title" }, candidateTitle(candidate)),
        el("p", { className: "muted" }, `${importedLabel(candidate.classification)} · 修订 ${candidate.revision}`),
        el("p", { className: "caption" }, "设计选择与实施授权分别记录。"),
      ),
      stale.length > 0 && el("section", { className: "panel warning", role: "alert" },
        el("h2", {}, "此候选的绑定已过期"), el("p", {}, stale.join("；")), el("p", { className: "caption" }, "请返回候选列表读取新版本；当前页面不会提交决定或导出。")),
      conflict && el("section", { id: "design-conflict", tabIndex: -1, className: "panel warning", role: "alert" },
        el("h2", {}, "资料已变化"), el("p", {}, errorText(conflict)), el("p", { className: "caption" }, "页面已载入最新资料。请重新核对两侧内容，再点击一次操作按钮确认。")),
      recovery,
      el("section", { className: "panel stack", "aria-label": "比较工具" },
        el("div", { className: "row" },
          el("label", { className: "field" }, el("span", {}, "预览视图"), viewSelect),
          figureLink && el("a", { href: figureLink, target: "_blank", rel: "noopener noreferrer", className: "button" }, "查看 Figma 来源"),
        ),
        mobileTabs,
        el("div", { className: "compare-grid" }, originalCanvas, candidateCanvas),
      ),
      el("section", { className: "panel stack", "aria-labelledby": "decision-title" },
        el("h2", { id: "decision-title" }, "记录设计决定"),
        current && el("p", { id: "decision-status", tabIndex: -1, className: current.stale ? "warning" : "success" },
          `当前：${STATUS_LABELS[current.event.action]}${current.stale ? "（绑定已过期）" : ""} · ${formatTime(current.event.created_at)}`),
        scopeCurrent && !current && el("p", { className: "warning" }, "当前范围已有另一候选的决定。选择或暂缓此候选会明确取代该范围的现有决定。"),
        el("label", { className: "field", htmlFor: "design-feedback" }, el("span", {}, "反馈与理由"), feedback,
          el("span", { className: "caption" }, "草稿仅保存在此浏览器，用于恢复输入；它不是设计决定。")),
        el("div", { className: "toolbar" },
          button(ACTION_LABELS.select, () => act("select"), { className: "primary", disabled: actionBlocked }),
          button(ACTION_LABELS.request_changes, () => act("request_changes"), { disabled: actionBlocked }),
          button(ACTION_LABELS.defer, () => act("defer"), { disabled: actionBlocked }),
          button(ACTION_LABELS.withdraw, () => act("withdraw"), { disabled: !current || actionBlocked }),
        ),
      ),
      el("section", { className: "panel stack", "aria-labelledby": "export-title" },
        el("h2", { id: "export-title" }, "导出材料"),
        el("p", { className: "muted" }, "服务会校验候选、基线、历史与材料哈希，再生成绑定到当前资料修订的 ZIP。"),
        el("div", { className: "toolbar" }, button("生成校验导出", exportCandidate, { disabled: actionBlocked }),
          lastExport?.href && el("a", { href: lastExport.href, download: `${lastExport.request_id}.zip`, className: "button" }, "再次下载上次导出")),
        exportResult,
      ),
      el("details", { className: "panel stack" },
        el("summary", { id: "history-title" }, `决定历史（${history.length}）`),
        history.length ? el("ol", { className: "history" }, [...history].reverse().map((item) =>
          el("li", {},
            el("strong", {}, STATUS_LABELS[item.event.action] || item.event.action),
            el("p", {}, item.event.feedback || "未附反馈"),
            el("p", { className: "caption" }, `${formatTime(item.event.created_at)} · 候选修订 ${item.event.candidate.revision}`),
            item.stale && el("p", { className: "warning" }, `已过期：${(item.stale_reasons || []).join("、")}`),
            item.superseded && el("p", { className: "muted caption" }, "已被同范围的后续决定取代"),
          ))) : el("div", { className: "empty" }, "还没有决定记录。"),
      ),
      el("details", { className: "diagnostics" },
        el("summary", {}, "资料绑定与来源"),
        el("dl", {},
          el("div", {}, el("dt", {}, "设计存储修订"), el("dd", {}, String(snapshot.store_revision))),
          el("div", {}, el("dt", {}, "候选完整标识"), el("dd", {}, `${candidate.id} / ${candidate.revision} / ${candidate.content_hash}`)),
          el("div", {}, el("dt", {}, "基线绑定"), el("dd", {}, baselineRecords.map((item) => `${item.id} / ${item.revision} / ${item.source?.kind}`).join("\n") || "未知")),
          el("div", {}, el("dt", {}, "Figma 引用"), el("dd", {}, candidate.figma_ref?.file_key ? `${candidate.figma_ref.file_key} · ${candidate.figma_ref.node_id || "无节点"} · ${candidate.figma_ref.offline ? "离线引用" : "在线引用"}` : "无")),
          el("div", {}, el("dt", {}, "导入材料来源"), el("dd", {}, candidateArtifacts.map((item) => `${item.id}：${item.provenance?.method || "来源方法未知"}`).join("\n") || "无")),
          el("div", {}, el("dt", {}, "当前决定来源"), el("dd", {}, current?.event?.source?.reference || "尚无当前决定")),
        ),
      ),
    ),
  );
  setMobileTab("candidate");
  focusRendered(container, focusTarget);
}

export async function renderDesigns(container, { projectId = null, candidateId = null, projectNames = {} } = {}) {
  container.replaceChildren(el("div", { className: "empty", role: "status" }, "正在读取设计资料…"));
  try {
    const snapshot = await api("/api/designs");
    if (!snapshot.available) {
      container.replaceChildren(el("section", { className: "empty", role: "alert" },
        el("h1", {}, "设计资料不可用"), el("p", { className: "muted" }, errorText({ code: snapshot.reason })),
        button("重试", () => renderDesigns(container, { projectId, candidateId, projectNames })),
      ));
      return;
    }
    const candidates = (snapshot.facts || []).filter((fact) => fact.kind === "candidate");
    if (!projectId) return renderList(container, snapshot, candidates, projectNames);
    if (!candidateId) return renderProject(container, snapshot, candidates, projectId, projectNames);
    const candidate = currentCandidate(candidates, projectId, candidateId);
    if (!candidate) {
      container.replaceChildren(el("section", { className: "empty" }, el("h1", {}, "未找到这个候选"),
        el("p", { className: "muted" }, "它可能已被移除或不属于这个项目。"),
        button("返回项目候选", () => navigate(`#designs/${projectId}`))));
      return;
    }
    renderCompare(container, snapshot, candidate, projectId, projectNames);
  } catch (error) {
    notify(errorText(error), "error");
    container.replaceChildren(el("section", { className: "empty", role: "alert" },
      el("h1", {}, "无法载入设计资料"), el("p", { className: "muted" }, errorText(error)),
      button("重试", () => renderDesigns(container, { projectId, candidateId, projectNames })),
    ));
  }
}

export const __test = {
  sameCandidate, scopeKey, effectiveFor, exactEffectiveFor, latestCandidates, staleReasons,
  artifactRole, trustedFigmaUrl, errorText, validPending,
};
