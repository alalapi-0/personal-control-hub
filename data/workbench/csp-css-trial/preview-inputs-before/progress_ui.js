// progress_ui.js — Web 写操作、阅读器、任务反馈与动作记录

let feedbackMap = {};
let eventMap = {};
let apiReady = false;
const learningResourceStates = {
  progress: { phase: "loading", source: "none", stale: false, error: "", loadedAt: "", requestId: 0 },
  events: { phase: "loading", source: "none", stale: false, error: "", loadedAt: "", requestId: 0 },
  feedback: { phase: "loading", source: "none", stale: false, error: "", loadedAt: "", requestId: 0 },
};
const RESOURCE_FETCH_TIMEOUT_MS = 10000;
const TASK_ACTION_TIMEOUT_MS = 10000;
const TASK_RUN_TIMEOUT_MS = 25000;
const TASK_ACTION_TEXT_CODEPOINT_LIMIT = 500;
const TASK_ACTION_RESULTS = new Set(["ok", "noop_already_done", "noop_already_undone", "replayed_operation"]);
const TASK_DONE_RESULTS = new Set(["ok", "noop_already_done", "replayed_operation"]);
const TASK_UNDO_RESULTS = new Set(["ok", "noop_already_undone", "replayed_operation"]);
const TASK_FEEDBACK_TYPES = new Set(["not_started", "in_progress", "completed"]);
const TASK_RUN_RESULTS = new Set(["ok", "failed", "timeout"]);
const pendingTaskActions = new Map();
const pendingTaskRuns = new Map();
const retryableTaskOperationIds = new Map();
const retryableTaskRunOperationIds = new Map();
let progressRevision = Number.isSafeInteger(window.PROGRESS_DATA?.revision)
  && window.PROGRESS_DATA.revision >= 0
  ? window.PROGRESS_DATA.revision
  : 0;
let terminalHistory = [];
let terminalCwd = "";
let terminalCwdDisplay = "~";
let terminalStateInfo = null;
let activeTerminalTaskId = "";
let terminalContextRequestId = 0;
let terminalRunRequestId = 0;
let terminalRunPending = false;
let workspaceTaskId = "";
let inlineReaderTaskId = "";
let inlineReaderFile = "";
let routeFocusedRound = false;
let autoBindingTerminalTaskId = "";
let activeView = "home";
let currentKnowledgeId = "round-00";
let currentKnowledgeFile = "rounds/round_00/final/command_cheatsheet.md";
let currentKnowledgeTitle = "Terminal 命令小抄";
let currentKnowledgeSection = "";
let knowledgeSearchQuery = "";
let lastModalFocus = null;
let lastModalFocusTaskId = "";
let inlineReaderRequestId = 0;
let modalReaderRequestId = 0;
let knowledgeRequestId = 0;
let knowledgeCommittedId = "";
let knowledgeResourcePhase = "idle";
let knowledgeModalHistoryEntry = false;
let initialLegacyAnchorId = "";
let forceTerminalVisible = false;
let activeWorkspaceTab = "task";
let completionReceipt = null;
let activeCheckpointId = "";
const inlineReaderScrollPositions = {};
const knowledgeReaderScrollPositions = {};
const knowledgeDocumentCache = new Map();
const knowledgeDocumentErrors = new Map();
const knowledgeDocumentLoads = new Map();
const reducedMotionMedia = window.matchMedia?.("(prefers-reduced-motion: reduce)") || null;
let activeAppViewTransition = null;
let appNavigationEpoch = 0;
let pendingHistoryNavigationTimer = 0;
let pendingHistoryNavigationState = null;
let lastHistoryNavigationSignature = "";

function prefersReducedMotion() {
  return !!reducedMotionMedia?.matches;
}

function clearHomeTaskTransitionMarker() {
  document.querySelector('.app-view.is-entering:not([hidden])')?.classList.remove('is-entering');
  document.documentElement.classList.remove("home-task-transition");
  delete document.documentElement.dataset.taskTransition;
}

function cancelActiveAppViewTransition() {
  const record = activeAppViewTransition;
  activeAppViewTransition = null;
  if (record?.transition?.skipTransition) {
    try {
      record.transition.skipTransition();
    } catch (_) {}
  }
  clearHomeTaskTransitionMarker();
}

function beginAppNavigation() {
  appNavigationEpoch += 1;
  cancelActiveAppViewTransition();
  return appNavigationEpoch;
}

function canRunHomeTaskTransition(taskId, sourceElement) {
  const sourceIsHomeEntry = sourceElement instanceof Element
    && !!sourceElement.closest("#homeTaskCard, .home-primary-action");
  const homeTaskId = registeredLearningTaskId(document.getElementById("homeTaskCard")?.dataset.learningTaskId);
  return activeView === "home"
    && sourceIsHomeEntry
    && homeTaskId === taskId
    && !prefersReducedMotion()
    && typeof document.startViewTransition === "function";
}

function runHomeTaskNavigation(taskId, sourceElement, commit) {
  if (activeAppViewTransition?.taskId === taskId) return;
  const navigationEpoch = beginAppNavigation();
  const commitIfCurrent = () => {
    if (navigationEpoch !== appNavigationEpoch) return false;
    commit();
    return true;
  };
  if (!canRunHomeTaskTransition(taskId, sourceElement)) {
    commitIfCurrent();
    return;
  }

  document.documentElement.classList.add("home-task-transition");
  document.documentElement.dataset.taskTransition = taskId;
  let transition;
  try {
    transition = document.startViewTransition(() => commitIfCurrent());
  } catch (_) {
    clearHomeTaskTransitionMarker();
    commitIfCurrent();
    return;
  }

  const record = { transition, navigationEpoch, taskId };
  activeAppViewTransition = record;
  Promise.resolve(transition.ready).catch(() => {});
  Promise.resolve(transition.updateCallbackDone).catch(() => {});
  Promise.resolve(transition.finished).catch(() => {}).then(() => {
    if (activeAppViewTransition !== record) return;
    activeAppViewTransition = null;
    clearHomeTaskTransitionMarker();
  });
}

const VIEW_META = {
  home: { title: "今日学习", eyebrow: "LINUX FOUNDATIONS" },
  learn: { title: "学习工作区", eyebrow: "FOCUS WORKSPACE" },
  route: { title: "学习路线", eyebrow: "COURSE ROUTE" },
  review: { title: "复习与反馈", eyebrow: "REVIEW SIGNALS" },
  knowledge: { title: "知识手册", eyebrow: "FIELD MANUAL" },
  growth: { title: "成长记录", eyebrow: "EVIDENCE OF GROWTH" },
  completion: { title: "模块检查点", eyebrow: "MODULE CHECKPOINT" },
};

function learningResource(name) {
  return learningResourceStates[name] || learningResourceStates.progress;
}

function beginLearningResourceLoad(name) {
  const state = learningResource(name);
  state.requestId += 1;
  state.phase = "loading";
  state.error = "";
  state.stale = !!state.loadedAt;
  return state.requestId;
}

function learningResourceRequestIsCurrent(name, requestId) {
  return learningResource(name).requestId === requestId;
}

function finishLearningResourceLoad(name, requestId, options = {}) {
  if (!learningResourceRequestIsCurrent(name, requestId)) return false;
  const state = learningResource(name);
  state.phase = options.empty ? "ready-empty" : "ready";
  state.source = options.source || "live";
  state.stale = !!options.stale;
  state.error = options.error || "";
  state.loadedAt = new Date().toISOString();
  return true;
}

function failLearningResourceLoad(name, requestId, error, options = {}) {
  if (!learningResourceRequestIsCurrent(name, requestId)) return false;
  const state = learningResource(name);
  state.phase = options.unavailable ? "unavailable" : "error";
  state.stale = !!state.loadedAt;
  state.error = String(error?.message || error || "读取失败");
  if (options.source) state.source = options.source;
  return true;
}

function learningResourceIsVerified(name) {
  const state = learningResource(name);
  return (state.phase === "ready" || state.phase === "ready-empty")
    && state.source === "live"
    && !state.stale;
}

function learningResourceHasStaleData(name) {
  const state = learningResource(name);
  return state.stale && !!state.loadedAt;
}

function learningResourceCanDisplay(name) {
  const state = learningResource(name);
  return state.phase === "ready"
    || state.phase === "ready-empty"
    || learningResourceHasStaleData(name);
}

function learningResourceStateLabel(name) {
  const state = learningResource(name);
  if (state.phase === "loading") return "正在读取";
  if (state.phase === "unavailable") return "当前模式不可读取";
  if (state.phase === "error" && state.stale) return "同步失败，显示上次读取值";
  if (state.phase === "error") return "读取失败";
  if (state.source === "mirror" && state.stale) return "实时读取失败，显示本地镜像";
  if (state.source === "mirror") return "来自本地进度镜像";
  return "已从真实记录读取";
}

async function fetchWithTimeout(resource, options = {}, timeoutMs = RESOURCE_FETCH_TIMEOUT_MS) {
  const controller = new AbortController();
  const externalSignal = options.signal;
  const forwardAbort = () => controller.abort();
  if (externalSignal?.aborted) controller.abort();
  else externalSignal?.addEventListener?.("abort", forwardAbort, { once: true });
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(resource, { ...options, signal: controller.signal });
  } catch (error) {
    if (error?.name === "AbortError") throw new Error("读取超时，请重试");
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
    externalSignal?.removeEventListener?.("abort", forwardAbort);
  }
}

async function fetchTaskAction(
  resource,
  options = {},
  timeoutMs = TASK_ACTION_TIMEOUT_MS,
  timeoutMessage = "保存超时；结果可能已经到达本地服务，请先同步进度再决定是否重试。",
) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(resource, { ...options, signal: controller.signal });
  } catch (error) {
    if (error?.name === "AbortError") {
      const timeoutError = new Error(timeoutMessage);
      timeoutError.code = "task_action_timeout";
      throw timeoutError;
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

function assertValidFeedbackMap(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("反馈响应结构无效");
  }
  const expectedIds = registeredCourseTaskIds();
  const expected = new Set(expectedIds);
  const actualIds = Object.keys(value);
  const missing = expectedIds.filter((taskId) => !Object.hasOwn(value, taskId));
  const extra = actualIds.filter((taskId) => !expected.has(taskId));
  if (missing.length || extra.length || actualIds.length !== expectedIds.length) {
    throw new Error("反馈任务范围无效");
  }
  for (const taskId of expectedIds) {
    const entry = value[taskId];
    if (!entry || typeof entry !== "object" || Array.isArray(entry)
      || entry.task_id !== taskId
      || entry.lane !== "linux-foundations"
      || typeof entry.done !== "boolean"
      || !Number.isSafeInteger(entry.action_count) || entry.action_count < 0
      || (entry.last_action_type !== null && typeof entry.last_action_type !== "string")
      || (entry.last_action_at !== null && typeof entry.last_action_at !== "string")
      || !TASK_FEEDBACK_TYPES.has(entry.feedback_type)
      || typeof entry.message !== "string"
      || typeof entry.next_suggestion !== "string") {
      throw new Error(`反馈条目结构无效：${taskId}`);
    }
  }
  return value;
}

function assertValidLaneMap(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("课程响应结构无效");
  }
  const keys = Object.keys(value);
  const lane = value["linux-foundations"];
  if (keys.length !== 1 || !lane || typeof lane !== "object" || Array.isArray(lane)
    || lane.course_id !== "linux-foundations"
    || typeof lane.title !== "string" || typeof lane.description !== "string") {
    throw new Error("课程响应范围无效");
  }
  return value;
}

function assertValidSnapshotRevision(value) {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error("进度响应版本无效");
  }
  return value;
}

function validateLearningSnapshot(data, options = {}) {
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new Error("进度响应结构无效");
  }
  const revision = assertValidSnapshotRevision(data.revision);
  assertValidProgressTaskScope(data.tasks);
  assertValidLaneMap(data.lanes);
  const feedback = options.requireFeedback ? assertValidFeedbackMap(data.feedback) : null;
  if (feedback) {
    for (const taskId of registeredCourseTaskIds()) {
      if (feedback[taskId].done !== data.tasks[taskId].done) {
        throw new Error(`反馈与任务状态不一致：${taskId}`);
      }
    }
  }
  return { revision, tasks: data.tasks, lanes: data.lanes, feedback };
}

function commitLearningSnapshot(snapshot, options = {}) {
  progressRevision = snapshot.revision;
  progressData = snapshot.tasks;
  lanesData = snapshot.lanes;
  if (options.includeFeedback) feedbackMap = snapshot.feedback;
}

function learningSnapshotsMatch(left, right) {
  return left.revision === right.revision
    && JSON.stringify(left.tasks) === JSON.stringify(right.tasks)
    && JSON.stringify(left.lanes) === JSON.stringify(right.lanes)
    && JSON.stringify(left.feedback) === JSON.stringify(right.feedback);
}

function assertValidEventMap(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("动作日志响应结构无效");
  }
  if (Object.values(value).some((events) => !Array.isArray(events) || events.some((event) => !event || typeof event !== "object" || Array.isArray(event)))) {
    throw new Error("动作日志条目结构无效");
  }
  return value;
}

function setElementText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
}

function setProgressElement(id, pct, label = "", canDisplay = true) {
  const element = document.getElementById(id);
  if (!element) return;
  const value = Math.max(0, Math.min(100, Number(pct) || 0));
  element.style.width = canDisplay ? `${value}%` : "";
  element.style.visibility = canDisplay ? "" : "hidden";
  const container = element.parentElement;
  if (container) {
    if (canDisplay) {
      container.setAttribute("role", "progressbar");
      container.setAttribute("aria-valuemin", "0");
      container.setAttribute("aria-valuemax", "100");
      container.setAttribute("aria-valuenow", String(value));
      if (label) container.setAttribute("aria-label", label);
      container.removeAttribute("aria-hidden");
    } else {
      container.removeAttribute("role");
      container.removeAttribute("aria-valuemin");
      container.removeAttribute("aria-valuemax");
      container.removeAttribute("aria-valuenow");
      container.removeAttribute("aria-label");
      container.setAttribute("aria-hidden", "true");
    }
  }
}

function workspaceIsMobile() {
  return !!window.matchMedia?.("(max-width: 920px)")?.matches;
}

function workspaceTabForPanel(panelId) {
  return {
    continueCard: "task",
    inlineReaderPanel: "guide",
    terminal: "practice",
  }[panelId] || "task";
}

function workspacePracticeAvailable(meta = null) {
  const current = meta || currentWorkspaceTask();
  return !!(current?.task && taskUsesTerminal(current.task));
}

function syncWorkspaceTabs(meta = null) {
  const workspace = document.getElementById("learnWorkspace");
  if (!workspace) return;
  const practiceAvailable = workspacePracticeAvailable(meta);
  const practiceTab = document.getElementById("workspaceTabPractice");
  if (practiceTab) practiceTab.hidden = !practiceAvailable;
  if (!practiceAvailable && activeWorkspaceTab === "practice") {
    activeWorkspaceTab = "guide";
  }
  workspace.dataset.workspaceActive = activeWorkspaceTab;
  const mobile = workspaceIsMobile();
  workspace.querySelectorAll("[data-workspace-tab]").forEach((tab) => {
    const name = tab.getAttribute("data-workspace-tab") || "task";
    const selected = name === activeWorkspaceTab;
    if (tab.getAttribute("role") === "tab") {
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
    }
  });
  workspace.querySelectorAll("[data-workspace-panel]").forEach((panel) => {
    const name = panel.getAttribute("data-workspace-panel") || "task";
    const unavailable = name === "practice" && !practiceAvailable;
    const inactive = unavailable || (mobile && name !== activeWorkspaceTab);
    panel.classList.toggle("is-active", !inactive);
    panel.setAttribute("aria-hidden", String(inactive));
    panel.toggleAttribute("inert", inactive);
  });
}

function setWorkspaceTab(name, options = {}) {
  let requested = ["task", "guide", "practice"].includes(name) ? name : "task";
  if (requested === "practice" && !workspacePracticeAvailable()) requested = "guide";
  activeWorkspaceTab = requested;
  syncWorkspaceTabs();
  if (options.focus) {
    document.querySelector(`[role="tab"][data-workspace-tab="${requested}"]`)?.focus({ preventScroll: true });
  }
}

function setupWorkspaceTabs() {
  const workspace = document.getElementById("learnWorkspace");
  if (!workspace) return;
  workspace.querySelectorAll("[data-workspace-tab]").forEach((tab) => {
    tab.addEventListener("click", () => {
      const name = tab.getAttribute("data-workspace-tab") || "task";
      setWorkspaceTab(name, { focus: tab.getAttribute("role") === "tab" });
    });
  });
  workspace.querySelector('[role="tablist"]')?.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = [...workspace.querySelectorAll('[role="tab"]')].filter((tab) => !tab.hidden);
    const index = tabs.indexOf(document.activeElement);
    if (index < 0) return;
    event.preventDefault();
    const nextIndex = event.key === "Home"
      ? 0
      : event.key === "End"
        ? tabs.length - 1
        : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    const next = tabs[nextIndex];
    setWorkspaceTab(next.getAttribute("data-workspace-tab"), { focus: true });
  });
  window.matchMedia?.("(max-width: 920px)")?.addEventListener?.("change", () => syncWorkspaceTabs());
  syncWorkspaceTabs();
}

function renderLearnHeader(meta) {
  const tasks = meta?.round?.weeks?.flatMap((week) => week.tasks || []) || [];
  const index = meta?.task ? tasks.findIndex((task) => task.id === meta.task.id) : -1;
  const roundCode = String(meta?.round?.id || "").match(/round_(\d{2})/)?.[1];
  const allTasks = typeof allCourseTasks === "function" ? allCourseTasks() : [];
  const doneCount = allTasks.filter((entry) => isTaskDone(entry.task.id)).length;
  const taskTitle = meta?.task?.title || "当前没有待完成任务";
  setElementText("learnTaskPosition", meta
    ? `${roundCode ? `ROUND ${roundCode}` : "COURSE"}  ·  TASK ${String(Math.max(1, index + 1)).padStart(2, "0")} / ${String(tasks.length).padStart(2, "0")}`
    : "LINUX FOUNDATIONS  ·  CHECKPOINT");
  setElementText("learnHeaderProgress", `${doneCount} / ${allTasks.length || Object.keys(progressData).length} 个任务`);
  setElementText("learnRoundMeta", meta
    ? `${meta.round.title} / ${meta.week.title}`
    : "LEARNING PATH / CURRENT STATE");
  setElementText("learnHeading", taskTitle);
  setElementText("learnContextSummary", meta
    ? (taskUsesTerminal(meta.task)
      ? "先理解任务，再在本地受限终端完成真实操作；执行结果与完成记录分开呈现。"
      : "阅读课程真源，整理一个最小结论，再用记录保存本次学习证据。")
    : "所有已注册任务均已完成；可进入复习或成长页核对已有记录。");
  syncWorkspaceTabs(meta);
}

function normalizedViewName(value) {
  const key = String(value || "").replace(/^#/, "");
  const legacy = {
    today: "home",
    learnWorkspace: "learn",
    continueCard: "learn",
    inlineReaderPanel: "learn",
    terminal: "learn",
    rounds: "route",
    progress: "route",
    lanes: "route",
    secondaryTools: "growth",
    stages: "growth",
    config: "growth",
    saves: "growth",
  };
  const normalized = legacy[key] || key;
  return VIEW_META[normalized] ? normalized : "";
}

function registeredLearningTaskId(value) {
  const taskId = String(value || "");
  const meta = taskId ? taskMeta(taskId) : null;
  return meta?.task?.id === taskId ? taskId : "";
}

function learningTaskSelectionFromLocation() {
  const url = new URL(window.location.href);
  const values = url.searchParams.getAll("task");
  const rawTaskId = values.length === 1 ? values[0] : "";
  const taskId = registeredLearningTaskId(rawTaskId);
  return {
    taskId,
    hasParameter: values.length > 0,
    canonical: values.length === 1 && !!taskId && rawTaskId === taskId,
  };
}

function clearLearningSourceParams(url) {
  [...url.searchParams.keys()].forEach((key) => {
    if (["round", "activeRound", "lane", "manual", "manualSection"].includes(key)
      || /^round_?\d{1,2}$/i.test(key)) {
      url.searchParams.delete(key);
    }
  });
}

function historyStateForView(viewName, taskId = "") {
  const state = { ...(history.state || {}), view: viewName };
  delete state.readerModal;
  delete state.readerContext;
  delete state.taskId;
  if (viewName === "learn" && taskId) state.taskId = taskId;
  if (viewName !== "route") {
    delete state.routeScreen;
    delete state.routeRoundId;
  }
  if (viewName !== "knowledge") {
    delete state.manualId;
    delete state.manualSection;
  }
  if (viewName !== "completion") delete state.checkpointId;
  if (viewName === "completion" && activeCheckpointId) state.checkpointId = activeCheckpointId;
  return state;
}

function appViewLocation(viewName, taskId = "") {
  const url = new URL(window.location.href);
  if (viewName === "learn") {
    clearLearningSourceParams(url);
    const registeredTaskId = registeredLearningTaskId(taskId);
    if (registeredTaskId) url.searchParams.set("task", registeredTaskId);
    else url.searchParams.delete("task");
  } else {
    url.searchParams.delete("task");
  }
  url.hash = viewName;
  return `${url.pathname}${url.search}${url.hash}`;
}

function writeAppViewLocation(viewName, taskId = "", mode = "push") {
  const registeredTaskId = viewName === "learn" ? registeredLearningTaskId(taskId) : "";
  const nextUrl = appViewLocation(viewName, registeredTaskId);
  const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  const state = historyStateForView(viewName, registeredTaskId);
  const method = mode === "replace" || nextUrl === currentUrl ? "replaceState" : "pushState";
  history[method](state, "", nextUrl);
  // pushState/replaceState do not emit navigation events. Clear the short-lived
  // popstate/hashchange dedupe key so a rapid Back to an earlier URL is never
  // mistaken for the second event from the previous traversal.
  lastHistoryNavigationSignature = "";
  return method === "pushState";
}

function syncTerminalRunControls() {
  const terminalInput = document.getElementById("terminalInput");
  const terminalRun = document.getElementById("terminalRun");
  const terminalInputRow = terminalInput?.closest(".terminal-input-row");
  if (terminalInput) terminalInput.disabled = terminalRunPending || !apiReady;
  if (terminalRun) {
    terminalRun.disabled = terminalRunPending || !apiReady;
    terminalRun.textContent = terminalRunPending ? "执行中…" : "执行";
    if (terminalRunPending) terminalRun.setAttribute("aria-busy", "true");
    else terminalRun.removeAttribute("aria-busy");
  }
  if (terminalRunPending) terminalInputRow?.setAttribute("aria-busy", "true");
  else terminalInputRow?.removeAttribute("aria-busy");
}

function setTerminalRunPending(pending) {
  terminalRunPending = !!pending;
  syncTerminalRunControls();
}

function invalidateTerminalRun(options = {}) {
  terminalRunRequestId += 1;
  setTerminalRunPending(false);
  const terminalInput = document.getElementById("terminalInput");
  if (!terminalInput) return;
  if (options.clearInput) terminalInput.value = "";
}

function invalidateLearningSurfaceRequests(options = {}) {
  inlineReaderRequestId += 1;
  terminalContextRequestId += 1;
  invalidateTerminalRun(options);
}

function resetTaskBoundSurfaces(nextTaskId) {
  const previousTaskId = workspaceTaskId;
  if (previousTaskId !== nextTaskId) {
    invalidateLearningSurfaceRequests();
    autoBindingTerminalTaskId = "";
    terminalHistory = [];
    terminalCwd = "";
    terminalCwdDisplay = "~";
    terminalStateInfo = null;
  }
  if (inlineReaderTaskId && inlineReaderTaskId !== nextTaskId) {
    inlineReaderTaskId = "";
    inlineReaderFile = "";
  }
  if (activeTerminalTaskId && activeTerminalTaskId !== nextTaskId) activeTerminalTaskId = "";
  if (completionReceipt?.taskId && completionReceipt.taskId !== nextTaskId) completionReceipt = null;
}

function applyLearningTaskContext(taskId) {
  const registeredTaskId = registeredLearningTaskId(taskId);
  resetTaskBoundSurfaces(registeredTaskId);
  workspaceTaskId = registeredTaskId;
  routeFocusedRound = false;
  if (!registeredTaskId) return "";
  const meta = taskMeta(registeredTaskId);
  activeLane = meta.round.lane || activeLane;
  activeRound = meta.round.id || activeRound;
  return registeredTaskId;
}

function syncLearningTaskFromLocation(viewName, options = {}) {
  const selection = learningTaskSelectionFromLocation();
  const taskId = viewName === "learn" ? selection.taskId : "";
  applyLearningTaskContext(taskId);
  const invalidForView = selection.hasParameter
    && (viewName !== "learn" || !selection.canonical);
  const stateTaskId = registeredLearningTaskId(history.state?.taskId);
  const stateMismatch = stateTaskId !== taskId || (!!history.state?.taskId && !stateTaskId);
  if (options.canonicalize && (invalidForView || stateMismatch)) {
    writeAppViewLocation(viewName, taskId, "replace");
  }
  return taskId;
}

function applyLegacyWorkspaceAnchor(rawHash) {
  forceTerminalVisible = rawHash === "terminal";
  if (rawHash === "terminal") activeWorkspaceTab = "practice";
  else if (rawHash === "inlineReaderPanel") activeWorkspaceTab = "guide";
  else if (["learn", "learnWorkspace", "continueCard"].includes(rawHash)) activeWorkspaceTab = "task";
}

function preferredScrollBehavior(behavior = "auto") {
  return prefersReducedMotion() ? "auto" : behavior;
}

function focusLegacyAnchor(anchorId, behavior = "auto") {
  const anchor = document.getElementById(anchorId);
  const disclosure = anchor?.closest("details");
  if (disclosure) disclosure.open = true;
  anchor?.scrollIntoView({ behavior: preferredScrollBehavior(behavior), block: "start" });
  if (anchor) {
    anchor.setAttribute("tabindex", "-1");
    anchor.focus({ preventScroll: true });
  }
}

function focusInitialLegacyAnchor() {
  if (!initialLegacyAnchorId) return;
  const anchorId = initialLegacyAnchorId;
  initialLegacyAnchorId = "";
  focusLegacyAnchor(anchorId);
}

function updateViewChrome(viewName) {
  const meta = VIEW_META[viewName] || VIEW_META.home;
  setElementText("pageTitle", meta.title);
  setElementText("pageEyebrow", meta.eyebrow);
  document.title = `${meta.title} · Linux 基础与工程实践`;
  document.querySelectorAll("[data-view-target]").forEach((item) => {
    const selected = item.dataset.viewTarget === viewName;
    item.classList.toggle("active", selected);
    if (item.matches("a[data-view-target]")) {
      if (selected) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    } else {
      item.removeAttribute("aria-current");
    }
  });
}

function showView(requestedView, options = {}) {
  const viewName = normalizedViewName(requestedView) || "home";
  const target = document.querySelector(`[data-view="${viewName}"]`);
  if (!target) return;
  if (!options.navigationTransaction) beginAppNavigation();
  // A taskless legacy Learn URL can still start reader or terminal work. Any
  // transition out of Learn must therefore invalidate pending surface work,
  // even when the selected task ID remains the empty string.
  if (viewName !== "learn") invalidateLearningSurfaceRequests();
  const commit = () => {
    document.querySelectorAll(".app-view[data-view]").forEach((view) => {
      const selected = view === target;
      view.hidden = !selected;
      view.setAttribute("aria-hidden", selected ? "false" : "true");
      view.classList.toggle("is-entering", selected);
    });
    activeView = viewName;
    document.body.dataset.activeView = viewName;
    updateViewChrome(viewName);
    if (options.scroll !== false) window.scrollTo({ top: 0, behavior: "auto" });
    if (options.focus) {
      const requestedFocus = typeof options.focusTarget === "string"
        ? target.querySelector(options.focusTarget) || document.querySelector(options.focusTarget)
        : options.focusTarget;
      const focusTarget = requestedFocus instanceof HTMLElement ? requestedFocus : target;
      if (!focusTarget.hasAttribute("tabindex")) focusTarget.setAttribute("tabindex", "-1");
      focusTarget.focus({ preventScroll: true });
    }
  };
  // Commit visibility, inertness and focus atomically. Only the entering view
  // animates; the previous view never remains actionable during a decorative
  // exit delay.
  commit();
  if (options.updateHash !== false) {
    const taskId = viewName === "learn"
      ? registeredLearningTaskId(options.taskId || workspaceTaskId)
      : "";
    writeAppViewLocation(viewName, taskId, options.historyMode || "push");
    if (viewName !== "learn") applyLearningTaskContext("");
  }
  if (viewName === "knowledge") {
    void restoreKnowledgeFromLocation({ historyMode: "replace", focusSection: false });
  }
}

function setupViewNavigation() {
  document.querySelectorAll("[data-view-target]").forEach((item) => {
    item.addEventListener("click", (event) => {
      event.preventDefault();
      if (item.matches(":disabled") || item.getAttribute("aria-disabled") === "true") return;
      initialLegacyAnchorId = "";
      forceTerminalVisible = false;
      const targetView = item.dataset.viewTarget;
      if (targetView === "learn") {
        const requestedTaskId = registeredLearningTaskId(item.dataset.learningTaskId)
          || (learningResourceCanDisplay("progress") ? findGlobalNextTask()?.task?.id || "" : "");
        if (requestedTaskId) {
          continueToLearningTask(requestedTaskId, { sourceElement: item });
          return;
        }
        showToast(learningResource("progress").phase === "loading"
          ? "正在核对课程任务，请稍后再进入"
          : "当前没有可进入的已注册任务", "warn");
        return;
      }
      showView(targetView, { updateHash: true, focus: true });
      if (targetView === "route" && typeof prepareRouteMapNavigation === "function") {
        prepareRouteMapNavigation();
      }
    });
  });
  const handleHistory = (event) => {
    let closedKnowledgeModal = false;
    if (knowledgeModalHistoryEntry && event?.state?.readerModal !== "knowledge") {
      closeMarkdownViewer({ fromHistory: true });
      closedKnowledgeModal = true;
    } else if (!knowledgeModalHistoryEntry && document.getElementById("readerModal")?.classList.contains("open")) {
      closeMarkdownViewer({ fromHistory: true, restoreFocus: false });
    }
    initialLegacyAnchorId = "";
    const rawHash = String(window.location.hash || "").replace(/^#/, "");
    const locationTask = learningTaskSelectionFromLocation();
    const fromHash = normalizedViewName(window.location.hash)
      || (locationTask.taskId ? "learn" : (!rawHash ? "home" : ""));
    if (!fromHash) return;
    syncLearningTaskFromLocation(fromHash, { canonicalize: true });
    applyLegacyWorkspaceAnchor(rawHash);
    render();
    if (closedKnowledgeModal && fromHash === activeView) return;
    if (!knowledgeModalHistoryEntry && event?.state?.readerModal === "knowledge" && fromHash === "knowledge") {
      const documentMeta = knowledgeDocumentById(event.state.readerContext) || knowledgeSelectionFromLocation().documentMeta;
      showView("knowledge", { updateHash: false, scroll: false, animate: false, focus: false });
      if (documentMeta) {
        knowledgeModalHistoryEntry = true;
        void openMarkdownViewer(documentMeta.file, documentMeta.title, { context: "knowledge", history: false, documentId: documentMeta.id });
      }
      return;
    }
    if (fromHash === "route" && typeof syncRouteStateFromLocation === "function") {
      syncRouteStateFromLocation(event);
    }
    if (fromHash === "completion") {
      const checkpointId = event?.state?.checkpointId || history.state?.checkpointId || "";
      if (learningCheckpointById(checkpointId)) activeCheckpointId = checkpointId;
      renderCompletion();
    }
    const isViewHash = !rawHash || !!VIEW_META[rawHash] || ["today", "learnWorkspace", "secondaryTools"].includes(rawHash);
    showView(fromHash, { updateHash: false, scroll: isViewHash, focus: isViewHash });
    if (rawHash === "terminal") renderTerminal();
    if (!isViewHash) {
      window.setTimeout(() => focusLegacyAnchor(rawHash, "smooth"), 170);
    }
  };
  const historyNavigationSignature = () => {
    const state = history.state || {};
    return JSON.stringify([
      window.location.pathname,
      window.location.search,
      window.location.hash,
      state.view || "",
      state.taskId || "",
      state.readerModal || "",
      state.readerContext || "",
      state.routeScreen || "",
      state.routeRoundId || "",
      state.manualId || "",
      state.manualSection || "",
      state.checkpointId || "",
    ]);
  };
  const scheduleHistoryNavigation = (event) => {
    pendingHistoryNavigationState = event?.state || history.state;
    if (pendingHistoryNavigationTimer) return;
    pendingHistoryNavigationTimer = window.setTimeout(() => {
      pendingHistoryNavigationTimer = 0;
      const signature = historyNavigationSignature();
      if (signature === lastHistoryNavigationSignature) return;
      lastHistoryNavigationSignature = signature;
      const state = pendingHistoryNavigationState || history.state;
      pendingHistoryNavigationState = null;
      handleHistory({ state });
      window.setTimeout(() => {
        if (lastHistoryNavigationSignature === signature) lastHistoryNavigationSignature = "";
      }, 50);
    }, 0);
  };
  window.addEventListener("popstate", scheduleHistoryNavigation);
  window.addEventListener("hashchange", scheduleHistoryNavigation);
  const query = new URLSearchParams(window.location.search || "");
  const requestedInitialHash = String(window.location.hash || "").replace(/^#/, "");
  const deepLinkedRound = query.has("round") || query.has("activeRound") || query.has("lane") || [...query.keys()].some((key) => /^round_?\d{1,2}$/i.test(key));
  const initialTask = learningTaskSelectionFromLocation();
  const initialView = normalizedViewName(window.location.hash) || (initialTask.taskId ? "learn" : (deepLinkedRound ? "route" : "home"));
  syncLearningTaskFromLocation(initialView, { canonicalize: true });
  if (!window.location.hash && initialView === "learn" && initialTask.taskId) {
    writeAppViewLocation("learn", initialTask.taskId, "replace");
  }
  if (initialView === "route" && typeof syncRouteStateFromLocation === "function") {
    syncRouteStateFromLocation({ state: history.state });
  }
  if (initialView === "completion" && learningCheckpointById(history.state?.checkpointId)) {
    activeCheckpointId = history.state.checkpointId;
  }
  applyLegacyWorkspaceAnchor(requestedInitialHash);
  showView(initialView, { updateHash: false, scroll: false, animate: false });
  const initialHash = requestedInitialHash;
  if (initialHash && !VIEW_META[initialHash] && !["today", "learnWorkspace", "secondaryTools"].includes(initialHash)) {
    initialLegacyAnchorId = initialHash;
  }
}

async function detectApi() {
  if (window.location.protocol === "file:") {
    apiReady = false;
  } else {
    try {
      const res = await fetchWithTimeout("/api/health?_=" + Date.now());
      apiReady = res.ok;
    } catch (_) {
      apiReady = false;
    }
  }
  document.getElementById("apiStatusDot")?.classList.toggle("online", apiReady);
  document.querySelector(".app-shell")?.setAttribute("data-app-ready", "true");
  syncTerminalRunControls();
  const banner = document.getElementById("apiBanner");
  if (!banner) return;
  if (apiReady) {
    banner.className = "banner ok";
    banner.innerHTML = "";
    banner.style.display = "none";
  } else if (window.location.protocol !== "file:") {
    banner.className = "banner warn";
    banner.innerHTML =
      '<strong>只读模式</strong> — 请用 <code>python3 scripts/progress_server.py</code> 启动以启用网页记录。';
    banner.style.display = "block";
  } else {
    banner.className = "banner warn";
    banner.innerHTML =
      '<strong>本地文件模式</strong> — 建议运行 <code>python3 scripts/progress_server.py</code> 后访问 Web UI 学习工作区。';
    banner.style.display = "block";
  }
}

async function loadFeedbackData() {
  const requestId = beginLearningResourceLoad("feedback");
  if (window.location.protocol === "file:") {
    feedbackMap = {};
    failLearningResourceLoad("feedback", requestId, "本地文件模式不提供反馈记录", { unavailable: true, source: "local" });
    return false;
  }
  try {
    const res = await fetchWithTimeout("/api/feedback?_=" + Date.now(), { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    assertValidFeedbackMap(data?.feedback);
    if (!learningResourceRequestIsCurrent("feedback", requestId)) return false;
    feedbackMap = data.feedback || {};
    finishLearningResourceLoad("feedback", requestId, {
      source: "live",
      empty: Object.keys(feedbackMap).length === 0,
    });
    return true;
  } catch (error) {
    failLearningResourceLoad("feedback", requestId, error);
    return false;
  }
}

async function loadEventData() {
  const requestId = beginLearningResourceLoad("events");
  if (window.location.protocol === "file:") {
    eventMap = {};
    failLearningResourceLoad("events", requestId, "本地文件模式不提供动作日志", { unavailable: true, source: "local" });
    return false;
  }
  try {
    const res = await fetchWithTimeout("/api/events?_=" + Date.now(), { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    assertValidEventMap(data?.by_task);
    if (!learningResourceRequestIsCurrent("events", requestId)) return false;
    eventMap = data.by_task || {};
    const eventCount = Object.values(eventMap).reduce((sum, events) => sum + (Array.isArray(events) ? events.length : 0), 0);
    finishLearningResourceLoad("events", requestId, { source: "live", empty: eventCount === 0 });
    return true;
  } catch (error) {
    failLearningResourceLoad("events", requestId, error);
    return false;
  }
}

function createTaskOperationId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `task-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

function taskActionPending(taskId) {
  return pendingTaskActions.get(taskId) || null;
}

function taskRunPending(taskId) {
  return pendingTaskRuns.get(taskId) || null;
}

function taskMutationPending(taskId) {
  return taskActionPending(taskId) || taskRunPending(taskId) || null;
}

function taskActionPayloadFingerprint(taskId, undo, payload = {}) {
  return JSON.stringify({
    taskId,
    action: undo ? "undo" : "done",
    note: String(payload.note || ""),
    evidencePath: String(payload.evidence_path || ""),
  });
}

function updateTaskActionControls(taskId) {
  const actionPending = taskActionPending(taskId);
  const runPending = taskRunPending(taskId);
  const pending = actionPending || runPending;
  document.querySelectorAll(`[data-task="${CSS.escape(taskId)}"], .record-panel[data-task="${CSS.escape(taskId)}"] #recordSaveDone, .record-panel[data-task="${CSS.escape(taskId)}"] #recordUndoDone`).forEach((element) => {
    if (!(element instanceof HTMLButtonElement)) return;
    const writesTask = element.matches(".task-complete-open, .task-btn[data-action], #recordSaveDone, #recordUndoDone, .task-run");
    if (!writesTask) return;
    if (!element.dataset.idleLabel) element.dataset.idleLabel = element.textContent || "";
    element.disabled = !!pending;
    element.setAttribute("aria-busy", pending ? "true" : "false");
    if (pending) {
      element.classList.add("is-task-action-pending");
      element.textContent = runPending
        ? (element.matches(".task-run") ? "正在运行…" : "脚本运行中…")
        : actionPending.undo ? "正在撤销…" : "正在保存…";
    } else {
      element.classList.remove("is-task-action-pending");
      element.textContent = element.dataset.idleLabel || element.textContent || "";
    }
  });
  const recordPanel = document.querySelector(`.record-panel[data-task="${CSS.escape(taskId)}"]`);
  if (recordPanel) {
    recordPanel.setAttribute("aria-busy", pending ? "true" : "false");
    recordPanel.closest("#readerBody")?.setAttribute("aria-busy", pending ? "true" : "false");
    const modalPanel = recordPanel.closest(".modal-panel");
    if (pending) setReaderModalVariant();
    modalPanel?.classList.toggle("task-action-pending", !!pending);
    recordPanel.querySelectorAll("input, textarea").forEach((element) => { element.disabled = !!pending; });
    const status = recordPanel.querySelector(".record-transaction-status");
    if (status && pending) {
      status.className = "record-transaction-status is-pending";
      status.textContent = runPending
        ? "正在运行本任务的练习脚本；结束后会重新核对完成状态与动作记录。"
        : `${actionPending.undo ? "正在撤销完成状态" : "正在保存本次记录"}；可以关闭窗口，完成后会在页面提示。`;
    } else if (status?.classList.contains("is-pending")) {
      status.className = "record-transaction-status";
      status.textContent = "";
    }
  }
}

function showRecordTransactionError(taskId, message) {
  const panel = document.querySelector(`.record-panel[data-task="${CSS.escape(taskId)}"]`);
  if (!panel) return;
  const status = panel.querySelector(".record-transaction-status");
  if (!status) return;
  setReaderModalVariant("feedback-error");
  status.className = "record-transaction-status is-error";
  status.textContent = message;
  status.setAttribute("tabindex", "-1");
  status.focus({ preventScroll: false });
}

function captureTaskFocusSnapshot() {
  const active = document.activeElement;
  if (!(active instanceof HTMLElement)) return null;
  const taskId = active.dataset.task || "";
  if (!taskId || active.closest("#readerModal")) return null;
  const kinds = ["task-record-open", "task-complete-open", "task-receipt-open", "task-run", "task-terminal"];
  return { taskId, kind: kinds.find((item) => active.classList.contains(item)) || "" };
}

function restoreTaskFocusSnapshot(snapshot) {
  if (!snapshot?.taskId) return;
  const escaped = CSS.escape(snapshot.taskId);
  const preferred = snapshot.kind ? `.${snapshot.kind}[data-task="${escaped}"]` : "";
  const selectors = [preferred, `.task-record-open[data-task="${escaped}"]`, `.task-complete-open[data-task="${escaped}"]`, `[data-task="${escaped}"]`].filter(Boolean);
  const target = selectors.flatMap((selector) => [...document.querySelectorAll(selector)])
    .find((element) => element instanceof HTMLElement && element.offsetParent !== null && !element.closest("[hidden]"));
  (target || document.getElementById("learnHeading"))?.focus({ preventScroll: true });
}

function reconcileOpenRecordPanel(taskId, message) {
  const panel = document.querySelector(`#readerModal.open .record-panel[data-task="${CSS.escape(taskId)}"]`);
  if (!panel) return false;
  const note = panel.querySelector("#recordNote")?.value || "";
  const evidencePath = panel.querySelector("#recordEvidence")?.value || "";
  const focusedId = panel.contains(document.activeElement) ? document.activeElement?.id || "" : "";
  openRecordViewer(taskId, { focus: false });
  const refreshed = document.querySelector(`#readerModal.open .record-panel[data-task="${CSS.escape(taskId)}"]`);
  const noteInput = refreshed?.querySelector("#recordNote");
  const evidenceInput = refreshed?.querySelector("#recordEvidence");
  if (noteInput) noteInput.value = note;
  if (evidenceInput) evidenceInput.value = evidencePath;
  const status = refreshed?.querySelector(".record-transaction-status");
  if (status) {
    status.className = "record-transaction-status is-synced";
    status.textContent = message;
  }
  if (focusedId) refreshed?.querySelector(`#${CSS.escape(focusedId)}`)?.focus({ preventScroll: true });
  return true;
}

function expectedActionEventRoundId(taskId) {
  if (/^(w\d+-|fin-)/.test(taskId)) return "round_00";
  const roundMatch = taskId.match(/^r(\d{2})-/);
  if (roundMatch) return `round_${roundMatch[1]}`;
  if (/^linux[-_]/.test(taskId)) return "linux";
  if (taskId.startsWith("vps-")) return "vps";
  return "unknown";
}

function assertExactObjectKeys(value, requiredKeys, errorMessage) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(errorMessage);
  const actual = Object.keys(value).sort();
  const required = [...requiredKeys].sort();
  if (actual.length !== required.length || actual.some((key, index) => key !== required[index])) {
    throw new Error(errorMessage);
  }
  return value;
}

function isCanonicalActionId(value) {
  return typeof value === "string"
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
}

function validateActionEvent(event, taskId, undo, operationId, payload, responseResult = "") {
  const message = "保存响应的动作记录无效，请同步后重试";
  assertExactObjectKeys(event, [
    "action_id", "task_id", "round_id", "lane", "action_type", "timestamp",
    "result", "note", "evidence_path", "operation_id",
  ], message);
  const allowedEventResults = undo
    ? new Set(["ok", "noop_already_undone"])
    : new Set(["ok", "noop_already_done"]);
  if (!isCanonicalActionId(event.action_id)
    || event.task_id !== taskId
    || event.round_id !== expectedActionEventRoundId(taskId)
    || event.lane !== "linux-foundations"
    || event.action_type !== (undo ? "undo_done" : "mark_done")
    || typeof event.timestamp !== "string" || !event.timestamp || !Number.isFinite(Date.parse(event.timestamp))
    || !allowedEventResults.has(event.result)
    || (responseResult !== "replayed_operation" && event.result !== responseResult)
    || event.note !== String(payload?.note || "")
    || event.evidence_path !== String(payload?.evidence_path || "")
    || event.operation_id !== operationId) {
    throw new Error(message);
  }
  return event;
}

function validateRunEvent(event, taskId, execution, operationId) {
  const message = "脚本响应的动作记录无效，请同步后重试";
  assertExactObjectKeys(event, [
    "action_id", "task_id", "round_id", "lane", "action_type", "timestamp",
    "result", "note", "evidence_path", "details", "operation_id",
  ], message);
  assertExactObjectKeys(event.details, [
    "script_path", "sandbox_path", "returncode", "timed_out", "duration_ms",
    "stdout_excerpt", "stderr_excerpt", "verification_reached", "completion_applied",
  ], message);
  if (!isCanonicalActionId(event.action_id)
    || event.action_id !== execution.action_id
    || event.task_id !== taskId
    || event.round_id !== expectedActionEventRoundId(taskId)
    || event.lane !== "linux-foundations"
    || event.action_type !== "run_exercise"
    || typeof event.timestamp !== "string" || !event.timestamp || !Number.isFinite(Date.parse(event.timestamp))
    || event.result !== execution.result
    || event.note !== `Web UI 执行练习脚本：${execution.script_path}；结果：${execution.result}`
    || event.evidence_path !== execution.sandbox_path
    || event.details.script_path !== execution.script_path
    || event.details.sandbox_path !== execution.sandbox_path
    || event.details.returncode !== execution.returncode
    || event.details.timed_out !== execution.timed_out
    || event.details.duration_ms !== execution.duration_ms
    || event.details.stdout_excerpt !== execution.stdout
    || event.details.stderr_excerpt !== execution.stderr
    || event.details.verification_reached !== execution.verification_reached
    || event.details.completion_applied !== execution.completion_applied
    || event.operation_id !== operationId) {
    throw new Error(message);
  }
  return event;
}

function eventMapWithValidatedEvent(event) {
  const next = Object.fromEntries(Object.entries(eventMap || {}).map(([taskId, rows]) => [
    taskId,
    Array.isArray(rows) ? rows.map((row) => ({ ...row })) : [],
  ]));
  const rows = next[event.task_id] || [];
  const existingIndex = rows.findIndex((row) => row.action_id === event.action_id);
  if (existingIndex >= 0) {
    if (JSON.stringify(rows[existingIndex]) !== JSON.stringify(event)) {
      throw new Error("动作记录与已读取历史不一致，请同步后重试");
    }
  } else {
    rows.push(event.details ? { ...event, details: { ...event.details } } : { ...event });
  }
  next[event.task_id] = rows;
  return next;
}

function commitLearningTransaction(snapshot, event) {
  const nextEventMap = eventMapWithValidatedEvent(event);
  commitLearningSnapshot(snapshot, { includeFeedback: true });
  eventMap = nextEventMap;
  const progressRequestId = beginLearningResourceLoad("progress");
  finishLearningResourceLoad("progress", progressRequestId, { source: "live", empty: false });
  const feedbackRequestId = beginLearningResourceLoad("feedback");
  finishLearningResourceLoad("feedback", feedbackRequestId, {
    source: "live",
    empty: Object.keys(feedbackMap).length === 0,
  });
  const eventsRequestId = beginLearningResourceLoad("events");
  finishLearningResourceLoad("events", eventsRequestId, { source: "live", empty: false });
}

function validateTaskActionResponse(data, taskId, undo, operationId, payload) {
  if (!data || data.ok !== true || data.task_id !== taskId) throw new Error("保存响应与当前任务不匹配，请同步后重试");
  if (typeof data.operation_id !== "string" || data.operation_id !== operationId) throw new Error("保存响应与当前操作不匹配，请同步后重试");
  if (!TASK_ACTION_RESULTS.has(data.result)) throw new Error("保存响应结果无效，请同步后重试");
  const allowedResults = undo ? TASK_UNDO_RESULTS : TASK_DONE_RESULTS;
  if (!allowedResults.has(data.result)) throw new Error("保存响应结果与当前操作不一致，请同步后重试");
  if (typeof data.action_id !== "string" || !data.action_id) throw new Error("保存响应缺少动作记录，请同步后重试");
  if (typeof data.done !== "boolean") throw new Error("保存响应的完成状态无效，请同步后重试");
  if (data.result !== "replayed_operation" && data.done !== !undo) {
    throw new Error("保存响应状态不一致，请同步后重试");
  }
  const snapshot = validateLearningSnapshot(data, { requireFeedback: true });
  if (snapshot.revision < 1) {
    throw new Error("保存响应缺少状态版本，请同步后重试");
  }
  if (snapshot.tasks[taskId]?.done !== data.done) {
    throw new Error("保存响应的任务状态不一致，请同步后重试");
  }
  const taskState = snapshot.tasks[taskId];
  if (data.done_at !== taskState.done_at
    || (data.done && (typeof data.done_at !== "string" || !data.done_at))
    || (!data.done && data.done_at !== null)) {
    throw new Error("保存响应的完成时间不一致，请同步后重试");
  }
  if (data.lane !== taskState.lane || typeof data.message !== "string") {
    throw new Error("保存响应的任务事实不一致，请同步后重试");
  }
  const expectedMessage = data.result === "replayed_operation"
    ? `replayed:${taskId}`
    : data.result === "noop_already_done"
      ? `already_done:${taskId}`
      : data.result === "noop_already_undone"
        ? `already_undone:${taskId}`
        : undo ? `undone:${taskId}` : `done:${taskId}:${data.done_at}`;
  if (data.message !== expectedMessage) {
    throw new Error("保存响应消息与当前操作不一致，请同步后重试");
  }
  const event = validateActionEvent(data.event, taskId, undo, operationId, payload, data.result);
  if (event.action_id !== data.action_id) {
    throw new Error("保存响应与动作记录不匹配，请同步后重试");
  }
  const registeredTotal = registeredCourseTaskIds().length;
  const computedDone = registeredCourseTaskIds().filter((id) => !!snapshot.tasks[id]?.done).length;
  if (!Number.isSafeInteger(data.total) || !Number.isSafeInteger(data.done_count)
    || data.total !== registeredTotal || data.done_count !== computedDone) {
    throw new Error("保存响应的课程计数不一致，请同步后重试");
  }
  return { data, event, snapshot };
}

function validateTaskRunResponse(data, taskId, operationId) {
  if (!data || data.ok !== true || !data.execution || typeof data.execution !== "object" || Array.isArray(data.execution)) {
    throw new Error("脚本响应结构无效，请同步后重试");
  }
  const snapshot = validateLearningSnapshot(data, { requireFeedback: true });
  const execution = data.execution;
  assertExactObjectKeys(execution, [
    "task_id", "action_id", "event", "result", "execution_ok", "timed_out",
    "duration_ms", "returncode", "script_path", "sandbox_path", "stdout", "stderr",
    "verification_reached", "completion_applied", "operation_id", "replayed_operation",
    "revision", "total", "done_count",
  ], "脚本响应事实无效，请同步后重试");
  const resultFactsAreConsistent = (
    execution.result === "ok"
      ? execution.returncode === 0 && execution.timed_out === false
      : execution.result === "failed"
        ? Number.isSafeInteger(execution.returncode) && execution.returncode !== 0 && execution.timed_out === false
        : execution.result === "timeout" && execution.returncode === null && execution.timed_out === true
  );
  if (execution.task_id !== taskId
    || !isCanonicalActionId(execution.action_id)
    || !TASK_RUN_RESULTS.has(execution.result)
    || typeof execution.execution_ok !== "boolean"
    || execution.execution_ok !== (execution.result === "ok")
    || typeof execution.timed_out !== "boolean"
    || !Number.isSafeInteger(execution.duration_ms) || execution.duration_ms < 0
    || (execution.returncode !== null && !Number.isSafeInteger(execution.returncode))
    || typeof execution.script_path !== "string"
    || typeof execution.sandbox_path !== "string"
    || typeof execution.stdout !== "string"
    || typeof execution.stderr !== "string"
    || typeof execution.verification_reached !== "boolean"
    || typeof execution.completion_applied !== "boolean"
    || (execution.completion_applied && (execution.result !== "ok" || !execution.verification_reached))
    || execution.operation_id !== operationId
    || typeof execution.replayed_operation !== "boolean"
    || execution.revision !== snapshot.revision
    || !resultFactsAreConsistent) {
    throw new Error("脚本响应事实无效，请同步后重试");
  }
  const registeredTotal = registeredCourseTaskIds().length;
  const computedDone = registeredCourseTaskIds().filter((id) => !!snapshot.tasks[id]?.done).length;
  if (!Number.isSafeInteger(execution.total) || !Number.isSafeInteger(execution.done_count)
    || execution.total !== registeredTotal || execution.done_count !== computedDone) {
    throw new Error("脚本响应的课程计数不一致，请同步后重试");
  }
  if (snapshot.revision < 1
    || (!execution.replayed_operation && execution.completion_applied && !snapshot.tasks[taskId]?.done)) {
    throw new Error("脚本响应的完成事实无效，请同步后重试");
  }
  const event = validateRunEvent(execution.event, taskId, execution, operationId);
  return { data, event, execution, snapshot };
}

async function parseTaskActionResponse(res, operationLabel = "保存") {
  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    const error = new Error(`本地服务返回了无法识别的${operationLabel}响应（HTTP ${res.status}）`);
    error.httpStatus = res.status;
    error.retryOperation = res.ok || res.status >= 500;
    throw error;
  }
  let data;
  try {
    data = await res.json();
  } catch (_parseError) {
    const error = new Error(`本地服务返回了无效的${operationLabel}响应（HTTP ${res.status}）`);
    error.httpStatus = res.status;
    error.retryOperation = res.ok || res.status >= 500;
    throw error;
  }
  if (!res.ok) {
    const serverError = data && typeof data === "object" && !Array.isArray(data)
      && typeof data.error === "string" ? data.error : "";
    const error = new Error(serverError || `操作失败（HTTP ${res.status}）`);
    error.code = serverError || "task_action_http_error";
    error.httpStatus = res.status;
    error.retryOperation = res.status >= 500 || serverError === "run_in_progress";
    throw error;
  }
  return data;
}

async function postTaskAction(taskId, undo, payload) {
  if (!apiReady) {
    showToast("请先运行 python3 scripts/progress_server.py 启动服务", "warn");
    return null;
  }
  if (taskActionPending(taskId)) return taskActionPending(taskId).promise;
  if (taskRunPending(taskId)) {
    showToast("本任务的练习脚本仍在运行，请等待状态核对完成", "warn");
    return null;
  }
  const action = undo ? "undo" : "done";
  const operationKey = `${taskId}:${action}`;
  const metaBeforeAction = taskMeta(taskId);
  const suppliedOperationId = typeof payload?.operation_id === "string" ? payload.operation_id : "";
  const payloadFingerprint = taskActionPayloadFingerprint(taskId, undo, payload);
  const retryable = retryableTaskOperationIds.get(operationKey);
  const operationId = suppliedOperationId
    || (retryable?.fingerprint === payloadFingerprint ? retryable.operationId : "")
    || createTaskOperationId();
  const recordModalSession = modalReaderRequestId;
  const recordModalWasOpen = document.getElementById("readerModal")?.classList.contains("open") || false;
  const pending = { taskId, undo, operationId, payloadFingerprint, recordModalSession, recordModalWasOpen, promise: null };
  const requestPayload = { ...(payload || {}), operation_id: operationId };
  const request = (async () => {
  try {
    const res = await fetchTaskAction(`/api/tasks/${encodeURIComponent(taskId)}/${action}`, {
      method: "POST",
      headers: {
        "Accept": "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(requestPayload),
    });
    const validated = validateTaskActionResponse(
      await parseTaskActionResponse(res),
      taskId,
      undo,
      operationId,
      requestPayload,
    );
    const { data, event, snapshot } = validated;
    retryableTaskOperationIds.delete(operationKey);
    const responseRevision = snapshot.revision;
    if (responseRevision < progressRevision) {
      await Promise.all([loadProgress(), loadFeedbackData(), loadEventData()]);
      const focusSnapshot = captureTaskFocusSnapshot() || { taskId, kind: "" };
      render();
      restoreTaskFocusSnapshot(focusSnapshot);
      reconcileOpenRecordPanel(taskId, "后台操作已处理，但已有更新版本；当前状态已重新读取，输入尚未再次提交。");
      showToast("已忽略旧响应并同步较新的任务状态", "warn");
      return { data, applied: false, expectedState: false, stale: true };
    }
    commitLearningTransaction(snapshot, event);
    const modalPresentationIsCurrent = recordModalWasOpen
      && recordModalSession === modalReaderRequestId
      && document.getElementById("readerModal")?.classList.contains("open")
      && lastModalFocusTaskId === taskId;
    const expectedState = data.done === !undo;
    if (!undo && expectedState) {
      const savedEvent = event;
      const receiptCheckpoint = learningCheckpointForRound(metaBeforeAction?.round?.id);
      if (receiptCheckpoint) activeCheckpointId = receiptCheckpoint.id;
      completionReceipt = {
        taskId,
        checkpointId: receiptCheckpoint?.id || "",
        title: metaBeforeAction?.task?.title || taskId,
        note: String(savedEvent?.note || ""),
        evidencePath: String(savedEvent?.evidence_path || ""),
        actionAt: String(savedEvent?.timestamp || ""),
        completedAt: data.done_at || progressData[taskId]?.done_at || "",
        doneCount: Number(data.done_count) || 0,
        total: registeredCourseTaskIds().length,
        feedback: data.feedback?.[taskId] || feedbackMap[taskId] || null,
        savedExistingCompletion: data.result === "noop_already_done" || data.result === "replayed_operation",
        eventLoaded: true,
      };
      if (!modalPresentationIsCurrent) completionReceipt = null;
    } else if (completionReceipt?.taskId === taskId) {
      completionReceipt = null;
    }
    const resultLabel = data.result === "replayed_operation"
      ? "该操作此前已处理；已同步当前任务状态"
      : data.result === "noop_already_done"
      ? "任务此前已完成；本次记录已追加"
      : data.result === "noop_already_undone"
        ? "任务已经是未完成状态"
        : undo ? "已撤销完成" : "已保存记录并完成";
    showToast(resultLabel, expectedState ? "ok" : "warn");
    const focusSnapshot = captureTaskFocusSnapshot() || { taskId, kind: "" };
    render();
    restoreTaskFocusSnapshot(focusSnapshot);
    if (!modalPresentationIsCurrent || !expectedState) {
      reconcileOpenRecordPanel(taskId, "后台操作已完成；当前状态与记录已重新核对，当前输入尚未再次提交。");
    }
    return { data, applied: true, expectedState, stale: false };
  } catch (error) {
    if (error.retryOperation === false) {
      retryableTaskOperationIds.delete(operationKey);
    } else {
      retryableTaskOperationIds.set(operationKey, { operationId, fingerprint: payloadFingerprint });
    }
    const message = error.message || "网络连接中断，请重试";
    showToast(message, error.code === "task_action_timeout" ? "warn" : "error");
    if (recordModalWasOpen && recordModalSession === modalReaderRequestId) {
      showRecordTransactionError(taskId, message);
    }
    return null;
  } finally {
    if (pendingTaskActions.get(taskId) === pending) {
      pendingTaskActions.delete(taskId);
      updateTaskActionControls(taskId);
    }
  }
  })();
  pending.promise = request;
  pendingTaskActions.set(taskId, pending);
  updateTaskActionControls(taskId);
  return request;
}

function showToast(msg, kind) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    document.body.appendChild(el);
  }
  el.className = `toast ${kind || ""} is-visible`;
  el.textContent = msg;
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.classList.remove("is-visible"); }, 2600);
}

function terminalPrompt(cwdDisplay) {
  return `${cwdDisplay || "~"} $`;
}

function terminalTaskContext() {
  if (!activeTerminalTaskId) return null;
  return taskMeta(activeTerminalTaskId);
}

function currentWorkspaceTask() {
  if (completionReceipt?.taskId) {
    const receiptTask = taskMeta(completionReceipt.taskId);
    if (receiptTask) return receiptTask;
  }
  return typeof findNextTask === "function" ? findNextTask() : null;
}

function leaveCompletionReceipt(nextTaskId = "") {
  if (!completionReceipt) return;
  if (!nextTaskId || completionReceipt.taskId !== nextTaskId) completionReceipt = null;
}

function terminalCwdForRound(round) {
  const match = String(round?.id || "").match(/round_(\d{2})/);
  if (!match) return "~";
  return `~/cli-lab/round${Number(match[1])}`;
}

function terminalTaskTarget(taskId) {
  const meta = taskMeta(taskId);
  if (!meta) return "~";
  return terminalCwdForRound(meta.round);
}

function taskUsesTerminal(task) {
  if (!task) return false;
  const meta = taskMeta(task.id);
  if (meta?.round?.lane !== "linux-foundations") return false;
  if (task.type === "exercise") return true;
  if (["test", "output"].includes(task.type)) {
    return /round_(?:00|01|02|06)/.test(meta?.round?.id || "");
  }
  return /\.(?:sh|py)$/i.test(task.file || "");
}

function terminalQuickCommands() {
  const meta = terminalTaskContext();
  const commands = ["pwd", "ls", "ls -la"];
  const weekMatch = String(meta?.week?.id || "").match(/week(\d+)/);
  if (weekMatch) {
    const weekDir = `week${Number(weekMatch[1])}`;
    commands.push(`mkdir -p ${weekDir}/self_check`, `cd ${weekDir}/self_check`);
  }
  if (meta?.round?.id === "round_00" && meta?.week?.id?.includes("week1")) {
    commands.push("cd notes", "pwd", "cd ..");
  }
  commands.push("find . -maxdepth 2 -type f");
  return [...new Set(commands)];
}

function scrollWorkspacePanel(panelId) {
  const panel = document.getElementById(panelId);
  const workspace = document.getElementById("learnWorkspace");
  const mobile = workspaceIsMobile();
  if (mobile) setWorkspaceTab(workspaceTabForPanel(panelId));
  const target = mobile ? document.querySelector(".learn-shell") : workspace;
  // Contextual task jumps need the workspace to exist before we calculate the
  // scroll position, so skip the decorative page-exit delay here.
  showView("learn", { updateHash: true, scroll: false, animate: false });
  requestAnimationFrame(() => target?.scrollIntoView({ behavior: preferredScrollBehavior("smooth"), block: "start" }));
}

async function autoBindTerminalForTask(taskId) {
  if (!apiReady || !taskId || activeTerminalTaskId || autoBindingTerminalTaskId === taskId) return;
  const meta = taskMeta(taskId);
  if (!meta || !taskUsesTerminal(meta.task)) return;
  autoBindingTerminalTaskId = taskId;
  activeTerminalTaskId = taskId;
  try {
    const target = terminalTaskTarget(taskId);
    const state = await setTerminalCwd(target, taskId);
    if (!state || workspaceTaskId !== taskId) return;
    if (!terminalHistory.length) {
      terminalHistory.push({
        kind: "system",
        message: `已自动绑定当前任务：${meta.task.title}；工作目录 ${state?.cwd_display || target}`,
        cwd_display: state?.cwd_display || "~",
      });
    }
    renderTerminal();
  } catch (err) {
    if (workspaceTaskId === taskId) {
      activeTerminalTaskId = "";
      showToast(err.message || "终端自动绑定失败", "warn");
    }
  } finally {
    if (autoBindingTerminalTaskId === taskId) autoBindingTerminalTaskId = "";
  }
}

function renderTerminalContext() {
  const contextEl = document.getElementById("terminalContext");
  const quickEl = document.getElementById("terminalQuickCommands");
  const cwdEl = document.getElementById("terminalCwd");
  const allowedEl = document.getElementById("terminalAllowed");
  if (cwdEl) cwdEl.textContent = terminalCwdDisplay || "~";
  if (allowedEl) {
    const allowed = terminalStateInfo?.allowed || [];
    allowedEl.textContent = allowed.length
      ? allowed.slice(0, 18).join(" ")
      : "等待连接";
  }
  if (quickEl) {
    quickEl.innerHTML = terminalQuickCommands().map((cmd) => (
      `<button type="button" class="terminal-chip" data-command="${escapeHtml(cmd)}">${escapeHtml(cmd)}</button>`
    )).join("");
    quickEl.querySelectorAll(".terminal-chip").forEach((btn) => {
      btn.addEventListener("click", () => runTerminalCommand(btn.getAttribute("data-command") || ""));
    });
  }
  if (!contextEl) return;
  const meta = terminalTaskContext();
  if (!meta) {
    const current = currentWorkspaceTask();
    if (current && !taskUsesTerminal(current.task)) {
      contextEl.innerHTML = `
      <div class="terminal-context-title">当前任务不需要终端</div>
      <div class="terminal-context-meta">${escapeHtml(current.task.title)} 属于 ${escapeHtml(current.round.lane)}，先读资料并写记录即可。</div>
    `;
      return;
    }
    if (!current && allCourseTasks().length && allCourseTasks().every((entry) => isTaskDone(entry.task.id))) {
      contextEl.innerHTML = `
      <div class="terminal-context-title">当前没有待完成任务</div>
      <div class="terminal-context-meta">课程任务已全部完成。终端历史仍可在本地记录中核对。</div>
    `;
      return;
    }
    contextEl.innerHTML = `
      <div class="terminal-context-title">未绑定任务</div>
      <div class="terminal-context-meta">打开工程任务时会自动绑定；任务行的“终端练习”用于切换或重新聚焦。</div>
    `;
    return;
  }
  const done = isTaskDone(meta.task.id);
  const canRead = canOpenFile(meta.task.file);
  contextEl.innerHTML = `
    <div class="terminal-context-title">${escapeHtml(meta.task.title)}</div>
    <div class="terminal-context-meta">${escapeHtml(meta.round.title)} / ${escapeHtml(meta.week.title)}</div>
    <div class="terminal-context-path">${escapeHtml(meta.task.file || "手动练习")}</div>
    <div class="terminal-context-actions">
      ${canRead ? `<button type="button" class="task-btn read task-open" data-task="${escapeHtml(meta.task.id)}" data-file="${escapeHtml(meta.task.file)}" data-title="${escapeHtml(meta.task.title)}">${fileActionLabel(meta.task.file, meta.task.type)}</button>` : ""}
      ${taskRecordButton(meta.task.id)}
      ${taskActionButtons(meta.task.id, done)}
    </div>
  `;
  bindTaskActions(contextEl);
}

function renderTerminal() {
  const output = document.getElementById("terminalOutput");
  const prompt = document.getElementById("terminalPrompt");
  if (!output || !prompt) return;
  const current = currentWorkspaceTask();
  const idleForCurrentTask = !forceTerminalVisible && !activeTerminalTaskId && current && !taskUsesTerminal(current.task);
  document.getElementById("terminal")?.classList.toggle("terminal-idle", !!idleForCurrentTask);
  document.getElementById("learnWorkspace")?.classList.toggle("no-terminal-task", !!idleForCurrentTask);
  syncWorkspaceTabs(current);
  prompt.textContent = terminalPrompt(terminalCwdDisplay || "~");
  renderTerminalContext();
  if (!terminalHistory.length) {
    if (idleForCurrentTask) {
      output.innerHTML = `<div class="terminal-line muted">当前任务以阅读和记录为主，不需要终端。切到工程实操任务后，终端会自动绑定沙盒目录。</div>`;
      return;
    }
    if (!current && allCourseTasks().length && allCourseTasks().every((entry) => isTaskDone(entry.task.id))) {
      output.innerHTML = `<div class="terminal-line muted">当前没有待完成任务。这里不会伪造未绑定的练习上下文。</div>`;
      return;
    }
    output.innerHTML = `<div class="terminal-line muted">终端已映射到 <code>~/cli-lab</code> 沙盒。工程任务会自动绑定到对应 Round 目录。</div>`;
    return;
  }
  output.innerHTML = terminalHistory.map((entry) => {
    if (entry.kind === "system") {
      return `<div class="terminal-entry system"><div class="terminal-command">${escapeHtml(entry.message)}</div></div>`;
    }
    if (entry.kind === "error") {
      return `<div class="terminal-entry"><div class="terminal-command">${escapeHtml(entry.prompt)} ${escapeHtml(entry.command)}</div><pre class="terminal-stderr">${escapeHtml(entry.error)}</pre></div>`;
    }
    const stdout = entry.stdout ? `<pre>${escapeHtml(entry.stdout)}</pre>` : "";
    const stderr = entry.stderr ? `<pre class="terminal-stderr">${escapeHtml(entry.stderr)}</pre>` : "";
    const status = entry.result && entry.result !== "ok" ? `<span class="terminal-result ${escapeHtml(entry.result)}">${escapeHtml(entry.result)}</span>` : "";
    return `<div class="terminal-entry"><div class="terminal-command">${escapeHtml(entry.prompt)} ${escapeHtml(entry.command)} ${status}</div>${stdout}${stderr}</div>`;
  }).join("");
  output.scrollTop = output.scrollHeight;
}

async function loadTerminalState() {
  const requestId = ++terminalContextRequestId;
  const requestedTaskId = workspaceTaskId;
  if (!apiReady) {
    terminalHistory = [{ kind: "error", prompt: "~ $", command: "", error: "请用 python3 scripts/progress_server.py 启动后使用终端练习。", cwd_display: "~" }];
    renderTerminal();
    syncTerminalRunControls();
    return;
  }
  syncTerminalRunControls();
  try {
    const requestedCwd = terminalCwd || (activeTerminalTaskId ? terminalTaskTarget(activeTerminalTaskId) : "");
    const res = await fetch(`/api/terminal?cwd=${encodeURIComponent(requestedCwd)}&_=${Date.now()}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "terminal_state_failed");
    if (requestId !== terminalContextRequestId || workspaceTaskId !== requestedTaskId) return;
    terminalStateInfo = data.terminal || null;
    terminalCwd = data.terminal.cwd || "";
    terminalCwdDisplay = data.terminal.cwd_display || "~";
    renderTerminal();
  } catch (err) {
    if (requestId !== terminalContextRequestId || workspaceTaskId !== requestedTaskId) return;
    terminalHistory.push({ kind: "error", prompt: "~ $", command: "", error: err.message, cwd_display: "~" });
    renderTerminal();
  }
}

async function setTerminalCwd(cwd, expectedTaskId = "") {
  if (!apiReady) {
    showToast("请先运行 python3 scripts/progress_server.py 启动服务", "warn");
    return null;
  }
  const requestId = ++terminalContextRequestId;
  const requestedWorkspaceTaskId = workspaceTaskId;
  const requestIsCurrent = () => requestId === terminalContextRequestId
    && workspaceTaskId === requestedWorkspaceTaskId
    && (!expectedTaskId || workspaceTaskId === expectedTaskId);
  try {
    const res = await fetch(`/api/terminal?cwd=${encodeURIComponent(cwd || "")}&_=${Date.now()}`);
    const data = await res.json();
    if (!requestIsCurrent()) return null;
    if (!res.ok) throw new Error(data.error || "terminal_state_failed");
    terminalStateInfo = data.terminal || null;
    terminalCwd = data.terminal.cwd || "";
    terminalCwdDisplay = data.terminal.cwd_display || "~";
    renderTerminal();
    return data.terminal;
  } catch (err) {
    if (!requestIsCurrent()) return null;
    throw err;
  }
}

async function openTaskTerminal(taskId) {
  if (!apiReady) {
    showToast("请先运行 python3 scripts/progress_server.py 启动服务", "warn");
    return;
  }
  const meta = taskMeta(taskId);
  if (!meta) return;
  invalidateTerminalRun();
  leaveCompletionReceipt(taskId);
  forceTerminalVisible = false;
  const registeredTaskId = applyLearningTaskContext(taskId);
  if (!registeredTaskId) return;
  writeAppViewLocation("learn", registeredTaskId, "push");
  activeTerminalTaskId = taskId;
  setWorkspaceTab("practice");
  try {
    const target = terminalTaskTarget(taskId);
    const state = await setTerminalCwd(target, taskId);
    if (!state || workspaceTaskId !== taskId) return;
    terminalHistory.push({
      kind: "system",
      message: `已绑定任务：${meta.task.title}；工作目录 ${state?.cwd_display || target}`,
      cwd_display: state?.cwd_display || "~",
    });
    renderTerminal();
    if (canOpenFile(meta.task.file)) {
      await openInlineReader(meta.task.file, meta.task.title, taskId, { silent: true });
    }
    if (workspaceTaskId !== taskId || activeTerminalTaskId !== taskId) return;
    renderContinue();
    scrollWorkspacePanel("terminal");
    setTimeout(() => {
      if (workspaceTaskId === taskId && activeTerminalTaskId === taskId && activeView === "learn") {
        document.getElementById("terminalInput")?.focus({ preventScroll: true });
      }
    }, 220);
  } catch (err) {
    if (workspaceTaskId === taskId && activeTerminalTaskId === taskId) {
      showToast(err.message || "终端切换失败", "error");
    }
  }
}

async function runTerminalCommand(command) {
  if (!apiReady) {
    showToast("请先运行 python3 scripts/progress_server.py 启动服务", "warn");
    return;
  }
  const value = String(command || "").trim();
  if (!value || terminalRunPending) return;
  const requestId = ++terminalRunRequestId;
  const requestedWorkspaceTaskId = workspaceTaskId;
  const requestedTerminalTaskId = activeTerminalTaskId;
  const requestIsCurrent = () => requestId === terminalRunRequestId
    && workspaceTaskId === requestedWorkspaceTaskId
    && activeTerminalTaskId === requestedTerminalTaskId;
  const input = document.getElementById("terminalInput");
  setTerminalRunPending(true);
  const currentPrompt = document.getElementById("terminalPrompt")?.textContent || "~ $";
  try {
    const res = await fetch("/api/terminal/run", {
      method: "POST",
      headers: {
        "Accept": "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ command: value, cwd: terminalCwd, task_id: activeTerminalTaskId }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "terminal_run_failed");
    if (!requestIsCurrent()) return;
    const term = data.terminal || {};
    terminalCwd = term.cwd || terminalCwd;
    terminalCwdDisplay = term.cwd_display || terminalCwdDisplay;
    if (term.clear) {
      terminalHistory = [];
    } else {
      terminalHistory.push({
        command: value,
        prompt: currentPrompt,
        result: term.result || "ok",
        returncode: term.returncode,
        stdout: term.stdout || "",
        stderr: term.stderr || "",
        cwd_display: term.cwd_display || "~",
      });
    }
    renderTerminal();
  } catch (err) {
    if (!requestIsCurrent()) return;
    terminalHistory.push({
      kind: "error",
      command: value,
      prompt: currentPrompt,
      error: err.message,
      cwd_display: terminalCwdDisplay || "~",
    });
    renderTerminal();
    showToast("命令被拦截或执行失败", "warn");
  } finally {
    if (requestIsCurrent()) {
      setTerminalRunPending(false);
      if (input) {
        input.value = "";
        input.focus();
      }
    }
  }
}

async function resetTerminal() {
  invalidateTerminalRun({ clearInput: true });
  activeTerminalTaskId = "";
  terminalCwd = "";
  try {
    const state = await setTerminalCwd("~/cli-lab");
    if (!state) return;
    terminalHistory.push({
      kind: "system",
      message: `已回到 ${state?.cwd_display || "~/cli-lab"} 根目录`,
      cwd_display: state?.cwd_display || "~/cli-lab",
    });
    renderTerminal();
    document.getElementById("terminalInput")?.focus({ preventScroll: true });
  } catch (err) {
    showToast(err.message || "终端重置失败", "warn");
  }
}

function feedbackFor(taskId) {
  return feedbackMap[taskId] || null;
}

function eventsFor(taskId) {
  return eventMap[taskId] || [];
}

function allCourseTasks() {
  const rows = [];
  for (const round of ROUNDS || []) {
    for (const week of round.weeks || []) {
      for (const task of week.tasks || []) rows.push({ round, week, task });
    }
  }
  return rows;
}

function registeredCourseTaskIds() {
  return allCourseTasks().map((entry) => entry.task.id);
}

function validateProgressTaskScope(tasks) {
  if (!tasks || typeof tasks !== "object" || Array.isArray(tasks)) {
    return { ok: false, error: "进度任务结构无效" };
  }
  const expectedIds = registeredCourseTaskIds();
  const expected = new Set(expectedIds);
  const actualIds = Object.keys(tasks);
  const actual = new Set(actualIds);
  const duplicateCount = expectedIds.length - expected.size;
  const missing = [...expected].filter((taskId) => !actual.has(taskId));
  const extra = actualIds.filter((taskId) => !expected.has(taskId));
  const invalid = actualIds.filter((taskId) => {
    const entry = tasks[taskId];
    return !entry || typeof entry !== "object" || Array.isArray(entry)
      || typeof entry.done !== "boolean"
      || entry.lane !== "linux-foundations"
      || (entry.done
        ? (typeof entry.done_at !== "string" || !entry.done_at)
        : entry.done_at !== null);
  });
  if (!expectedIds.length || duplicateCount || missing.length || extra.length || invalid.length) {
    const details = [];
    if (!expectedIds.length) details.push("课程未注册任务");
    if (duplicateCount) details.push(`课程定义含 ${duplicateCount} 个重复任务 ID`);
    if (missing.length) details.push(`缺少 ${missing.length} 项（${missing.slice(0, 3).join("、")}）`);
    if (extra.length) details.push(`多出 ${extra.length} 项（${extra.slice(0, 3).join("、")}）`);
    if (invalid.length) details.push(`结构无效 ${invalid.length} 项（${invalid.slice(0, 3).join("、")}）`);
    return { ok: false, error: `进度任务范围不一致：${details.join("；")}` };
  }
  return { ok: true, expectedIds, actualIds };
}

function assertValidProgressTaskScope(tasks) {
  const result = validateProgressTaskScope(tasks);
  if (!result.ok) throw new Error(result.error);
  return result;
}

function taskVerb(task) {
  return {
    reading: "阅读",
    exercise: "练习",
    test: "验收",
    output: "产出",
  }[task?.type] || "学习";
}

function reviewEventTimestamp(event) {
  const parsed = Date.parse(String(event?.timestamp || ""));
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

function compareReviewEvents(a, b) {
  const aTime = reviewEventTimestamp(a);
  const bTime = reviewEventTimestamp(b);
  if (aTime !== bTime) return bTime > aTime ? 1 : -1;
  const timestampDelta = String(b?.timestamp || "").localeCompare(String(a?.timestamp || ""));
  if (timestampDelta) return timestampDelta;
  const actionDelta = String(b?.action_id || "").localeCompare(String(a?.action_id || ""));
  if (actionDelta) return actionDelta;
  return String(b?.taskId || b?.task_id || "").localeCompare(String(a?.taskId || a?.task_id || ""));
}

function registeredEventsForTask(taskId) {
  return eventsFor(taskId)
    .filter((event) => !event.task_id || event.task_id === taskId)
    .map((event) => ({ ...event, taskId }))
    .sort(compareReviewEvents);
}

function rawEventCount() {
  return Object.values(eventMap || {}).reduce((sum, events) => (
    sum + (Array.isArray(events) ? events.length : 0)
  ), 0);
}

function reviewCandidates() {
  return allCourseTasks().map((entry) => {
    const events = registeredEventsForTask(entry.task.id);
    const feedback = feedbackFor(entry.task.id);
    const lastEvent = events[0] || null;
    return { ...entry, events, feedback, lastEvent };
  }).filter((entry) => entry.events.length > 0).sort((a, b) => compareReviewEvents(a.lastEvent, b.lastEvent));
}

function renderHome() {
  if (typeof findGlobalNextTask !== "function") return;
  const progressCanDisplay = learningResourceCanDisplay("progress");
  const allIds = registeredCourseTaskIds();
  const doneCount = progressCanDisplay ? allIds.filter((id) => isTaskDone(id)).length : 0;
  const routePct = progressCanDisplay && allIds.length ? Math.round(doneCount / allIds.length * 100) : 0;
  const next = progressCanDisplay ? findGlobalNextTask() : null;
  const reviews = reviewCandidates();

  setElementText("railProgressPct", progressCanDisplay ? `${routePct}%` : "—");
  setElementText("railProgressMeta", progressCanDisplay ? `${doneCount} / ${allIds.length} 个任务` : `— / ${allIds.length || "—"} 个任务`);
  setProgressElement("railProgressBar", routePct, "课程总进度", progressCanDisplay);
  setElementText("homeRoutePct", progressCanDisplay ? `${routePct}%` : "—");
  setProgressElement("homeRouteBar", routePct, "课程路线进度", progressCanDisplay);
  setElementText("homeCardRoutePct", progressCanDisplay ? `${routePct}%` : "—");
  setProgressElement("homeCardRouteBar", routePct, "当前任务卡课程路线进度", progressCanDisplay);
  setElementText("homeHeaderProgress", progressCanDisplay ? `${doneCount} / ${allIds.length} · ROUTE ${routePct}%` : `— / ${allIds.length || "—"} · ROUTE —`);
  const homeAction = document.querySelector(".home-primary-action");
  const homeCard = document.getElementById("homeTaskCard");
  const homeActionLabel = homeAction?.querySelector("span");
  const setHomeEntryEnabled = (enabled) => {
    if (homeAction) {
      homeAction.disabled = !enabled;
      if (enabled) homeAction.removeAttribute("aria-disabled");
      else homeAction.setAttribute("aria-disabled", "true");
    }
    if (homeCard) {
      if (enabled) {
        homeCard.removeAttribute("aria-disabled");
        homeCard.removeAttribute("tabindex");
      } else {
        homeCard.setAttribute("aria-disabled", "true");
        homeCard.setAttribute("tabindex", "-1");
      }
    }
  };

  if (!next) {
    const courseComplete = progressCanDisplay && allIds.length > 0 && doneCount === allIds.length;
    const progressLoading = !progressCanDisplay && learningResource("progress").phase === "loading";
    setElementText("homeTaskKicker", courseComplete ? "COURSE / COMPLETE" : progressLoading ? "COURSE / VERIFYING" : "COURSE / UNAVAILABLE");
    setElementText("homeTaskVerb", courseComplete ? "完成" : progressLoading ? "核对" : "等待");
    setElementText("homeTaskTitle", courseComplete ? "所有已注册任务均已完成" : progressLoading ? "正在核对课程任务" : "课程任务尚未载入");
    setElementText("homeTaskMeta", courseComplete ? "进入成长页查看模块检查点" : progressLoading ? "任务确认后才会开放学习入口" : "请先同步课程数据");
    setElementText("homeRoundMeta", courseComplete ? "COURSE COMPLETE" : progressLoading ? "COURSE VERIFYING" : "COURSE STATUS");
    setElementText("homeModuleValue", courseComplete ? "完成" : "—");
    setProgressElement("homeModuleBar", courseComplete ? 100 : 0, "当前模块进度", progressCanDisplay);
    const destination = courseComplete ? "growth" : "route";
    if (homeAction && !progressLoading) {
      homeAction.dataset.viewTarget = destination;
      delete homeAction.dataset.learningTaskId;
    }
    if (homeCard && !progressLoading) {
      homeCard.dataset.viewTarget = destination;
      delete homeCard.dataset.learningTaskId;
      homeCard.setAttribute("href", `#${destination}`);
    }
    if (progressLoading && homeCard) homeCard.setAttribute("href", "#home");
    setHomeEntryEnabled(!progressLoading);
    if (homeActionLabel) homeActionLabel.textContent = courseComplete ? "查看成长与检查点" : progressLoading ? "正在核对课程任务" : "查看课程状态";
  } else {
    const roundTasks = (next.round.weeks || []).flatMap((week) => week.tasks || []);
    const roundDone = roundTasks.filter((task) => isTaskDone(task.id)).length;
    const roundPct = roundTasks.length ? Math.round(roundDone / roundTasks.length * 100) : 0;
    const taskIndex = Math.max(0, roundTasks.findIndex((task) => task.id === next.task.id));
    const moduleCode = moduleDisplayCode(next.round.id);
    setElementText("homeTaskKicker", `CURRENT / ${String(taskIndex + 1).padStart(2, "0")}`);
    setElementText("homeRoundMeta", `ROUND ${moduleCode} • TASK ${String(taskIndex + 1).padStart(2, "0")} / ${String(roundTasks.length).padStart(2, "0")}`);
    setElementText("homeTaskVerb", taskVerb(next.task));
    setElementText("homeTaskTitle", next.task.title);
    setElementText("homeTaskMeta", `${next.round.title} · ${next.week.title}`);
    setElementText("homeModuleValue", `${roundDone} / ${roundTasks.length}`);
    setProgressElement("homeModuleBar", roundPct, "当前模块进度");
    if (homeAction) {
      homeAction.dataset.viewTarget = "learn";
      homeAction.dataset.learningTaskId = next.task.id;
    }
    if (homeCard) {
      homeCard.dataset.viewTarget = "learn";
      homeCard.dataset.learningTaskId = next.task.id;
      homeCard.setAttribute("href", appViewLocation("learn", next.task.id));
    }
    setHomeEntryEnabled(true);
    if (homeActionLabel) homeActionLabel.textContent = "进入当前任务";
  }

  if (!learningResourceCanDisplay("events")) {
    setElementText("homeReviewValue", learningResource("events").phase === "loading" ? "读取中" : "不可核对");
    setElementText("homeReviewMeta", `${learningResourceStateLabel("events")}；不会显示成暂无记录`);
  } else if (reviews.length) {
    setElementText("homeReviewValue", reviews[0].task.title);
    setElementText("homeReviewMeta", `${reviews[0].events.length} 条动作记录 · 可复盘${learningResourceHasStaleData("events") ? " · 上次读取值" : ""}`);
  } else {
    setElementText("homeReviewValue", "暂无");
    setElementText("homeReviewMeta", "运行练习或保存记录后可回看");
  }
}

function formatReviewEventTime(timestamp) {
  const date = new Date(String(timestamp || ""));
  if (Number.isNaN(date.getTime())) return timestamp || "时间待同步";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date).replaceAll("/", ".");
}

function eventResultLabel(result) {
  return {
    ok: "已记录",
    noop_already_done: "状态未变",
    failed: "运行失败",
    timeout: "运行超时",
  }[result] || (result ? String(result) : "结果待记录");
}

function eventResultClass(result) {
  return {
    ok: "is-ok",
    noop_already_done: "is-noop",
    failed: "is-failed",
    timeout: "is-timeout",
  }[result] || "is-unknown";
}

function eventResultBadge(event) {
  return `<span class="event-result ${eventResultClass(event?.result)}">${escapeHtml(eventResultLabel(event?.result))}</span>`;
}

function feedbackTypeLabel(type) {
  return {
    completed: "任务已完成",
    in_progress: "任务进行中",
    not_started: "任务当前未完成",
  }[type] || "任务建议";
}

function reviewNextActionMarkup(progressCanDisplay) {
  if (!progressCanDisplay) {
    const progressState = learningResource("progress");
    return `<div class="review-next-state resource-state-panel ${progressState.phase === "loading" ? "" : "is-error"}">
      <strong>下一任务${escapeHtml(learningResourceStateLabel("progress"))}</strong>
      <small>动作记录仍可查看；进度恢复前不会猜测当前任务。</small>
    </div>`;
  }
  const next = typeof findGlobalNextTask === "function" ? findGlobalNextTask() : null;
  if (!next) {
    return `<div class="review-next-task is-complete">
      <span class="review-next-label">COURSE STATUS</span>
      <strong>所有已注册任务均已标记完成</strong>
      <p>前往成长页核对课程任务证据与模块检查点。</p>
      <button class="physical-button review-primary-action" type="button" data-review-view="growth"><span>查看成长记录</span><span aria-hidden="true">→</span></button>
    </div>`;
  }
  const verb = taskVerb(next.task);
  const title = String(next.task.title || "");
  const actionTitle = title.startsWith(verb) ? title : `${verb} · ${title}`;
  return `<div class="review-next-task">
    <span class="review-next-label">NEXT REAL TASK</span>
    <strong>${escapeHtml(actionTitle)}</strong>
    <p>${escapeHtml(next.round.title)} · ${escapeHtml(next.week.title)}</p>
    <button class="physical-button review-primary-action" type="button" data-review-task="${escapeHtml(next.task.id)}"><span>进入当前任务</span><span aria-hidden="true">→</span></button>
  </div>`;
}

function bindReviewActions(root) {
  if (!root) return;
  const bindKeyboardActivation = (button, action) => {
    button.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      action();
    });
  };
  root.querySelectorAll("[data-review-task]").forEach((button) => {
    const action = () => continueToLearningTask(button.dataset.reviewTask || "");
    button.addEventListener("click", action);
    bindKeyboardActivation(button, action);
  });
  root.querySelectorAll("[data-review-view]").forEach((button) => {
    const action = () => showView(button.dataset.reviewView || "growth", { updateHash: true, focus: true });
    button.addEventListener("click", action);
    bindKeyboardActivation(button, action);
  });
  bindTaskActions(root);
  root.querySelectorAll(".task-record-open").forEach((button) => {
    bindKeyboardActivation(button, () => button.click());
  });
}

function renderReview() {
  const scene = document.getElementById("reviewScene");
  const current = document.getElementById("reviewCurrent");
  const queue = document.getElementById("reviewQueue");
  const feedbackEl = document.getElementById("feedbackOverview");
  const timeline = document.getElementById("actionTimeline");
  const queueCard = document.getElementById("reviewQueueCard");
  const feedbackCard = document.getElementById("reviewFeedbackCard");
  const timelineCard = document.getElementById("reviewTimelineCard");
  if (!scene || !current || !queue || !feedbackEl || !timeline || !queueCard || !feedbackCard || !timelineCard) return;

  const eventsCanDisplay = learningResourceCanDisplay("events");
  const feedbackCanDisplay = learningResourceCanDisplay("feedback");
  const progressCanDisplay = learningResourceCanDisplay("progress");
  const candidates = eventsCanDisplay ? reviewCandidates() : [];
  const timelineEvents = eventsCanDisplay ? allEventRows().sort(compareReviewEvents) : [];
  const ignoredEvents = eventsCanDisplay ? Math.max(0, rawEventCount() - timelineEvents.length) : 0;
  const hasRecords = timelineEvents.length > 0;
  const eventsStale = learningResourceHasStaleData("events");
  const doneCount = progressCanDisplay
    ? registeredCourseTaskIds().filter((id) => isTaskDone(id)).length
    : 0;

  scene.dataset.state = eventsCanDisplay ? (hasRecords ? "records" : (eventsStale ? "stale-empty" : "empty")) : learningResource("events").phase;
  [queueCard, feedbackCard, timelineCard].forEach((card) => { card.hidden = !hasRecords; });
  setElementText("reviewDueCount", eventsCanDisplay ? String(candidates.length) : "—");
  setElementText("reviewEventCount", eventsCanDisplay ? String(timelineEvents.length) : "—");
  setElementText("reviewDoneCount", progressCanDisplay ? String(doneCount) : "—");
  setElementText("reviewDueMeta", eventsCanDisplay ? (eventsStale ? "来自上次成功读取" : "当前课程真实记录") : learningResourceStateLabel("events"));
  setElementText("reviewEventMeta", eventsCanDisplay
    ? `${learningResourceStateLabel("events")}${ignoredEvents ? `；已忽略 ${ignoredEvents} 条非当前课程记录` : ""}`
    : learningResourceStateLabel("events"));
  setElementText("reviewDoneMeta", learningResourceStateLabel("progress"));

  if (!eventsCanDisplay) {
    const state = learningResource("events");
    setElementText("reviewCurrentKicker", state.phase === "loading" ? "READING ACTION LOG" : "ACTION LOG UNAVAILABLE");
    setElementText("reviewCurrentHeading", state.phase === "loading" ? "正在读取学习动作" : "动作记录暂时无法核对");
    setElementText("reviewDataStatus", `动作日志${learningResourceStateLabel("events")}；当前不会显示 0、空队列或复习结论。`);
    current.innerHTML = `<div class="resource-state-panel ${state.phase === "loading" ? "" : "is-error"}">
      <strong>${escapeHtml(learningResourceStateLabel("events"))}</strong>
      <small>${escapeHtml(state.error || "动作日志未就绪；真实记录恢复后会在这里出现。")}</small>
    </div>${reviewNextActionMarkup(progressCanDisplay)}`;
    bindReviewActions(current);
    return;
  }

  if (!hasRecords) {
    const emptyTitle = eventsStale ? "上次读取时没有学习动作" : "还没有学习动作记录";
    setElementText("reviewCurrentKicker", eventsStale ? "LAST VERIFIED STATE" : "FIRST REAL ACTION");
    setElementText("reviewCurrentHeading", emptyTitle);
    setElementText("reviewDataStatus", eventsStale
      ? `动作日志同步失败；当前显示上次成功读取的空状态${ignoredEvents ? `，并忽略 ${ignoredEvents} 条非当前课程记录` : ""}。`
      : `当前课程尚无真实动作记录${ignoredEvents ? `；${ignoredEvents} 条非当前课程记录未计入` : ""}。运行练习或保存记录后，这里才会形成历史。`);
    current.innerHTML = `<div class="review-empty-state">
      <p>这里不会根据静态任务、未开始反馈或设计示例生成“到期复习”。完成一次真实运行，或在任务中保存一条学习记录后，才会出现动作、结果与任务建议。</p>
      ${reviewNextActionMarkup(progressCanDisplay)}
    </div>`;
    bindReviewActions(current);
    return;
  }

  const latestEvent = timelineEvents[0];
  const latestMeta = taskMeta(latestEvent.taskId);
  const latestTitle = latestMeta?.task?.title || latestEvent.taskId;
  const latestDone = progressCanDisplay ? isTaskDone(latestEvent.taskId) : null;
  setElementText("reviewCurrentKicker", eventsStale ? "LATEST ACTION · LAST READ" : "LATEST REAL ACTION");
  setElementText("reviewCurrentHeading", latestTitle);
  setElementText("reviewDataStatus", `${candidates.length} 个当前课程任务已有真实动作，合计 ${timelineEvents.length} 条${eventsStale ? "；动作日志同步失败，当前为上次读取值" : ""}${ignoredEvents ? `；另有 ${ignoredEvents} 条非当前课程记录已忽略` : ""}。`);
  current.innerHTML = `
    <div class="review-current-meta">
      ${eventResultBadge(latestEvent)}
      <time datetime="${escapeHtml(latestEvent.timestamp || "")}" title="${escapeHtml(latestEvent.timestamp || "")}">${escapeHtml(formatReviewEventTime(latestEvent.timestamp))}</time>
    </div>
    <strong class="review-current-action">${escapeHtml(actionLabel(latestEvent.action_type))}</strong>
    <p class="review-current-context">${escapeHtml(latestMeta ? `${latestMeta.round.title} · ${latestMeta.week.title}` : "当前课程任务")}${latestDone === null ? "" : ` · ${latestDone ? "当前已完成" : "当前未完成"}`}</p>
    <div class="review-action-evidence">
      <span>本次动作证据</span>
      ${latestEvent.note ? `<p>${escapeHtml(latestEvent.note)}</p>` : "<p>本次动作未附学习备注。</p>"}
      ${latestEvent.evidence_path ? `<code>${escapeHtml(latestEvent.evidence_path)}</code>` : ""}
    </div>
    <div class="review-current-actions">
      <button class="task-btn record task-record-open" type="button" data-task="${escapeHtml(latestEvent.taskId)}">查看记录</button>
      <button class="task-btn read" type="button" data-review-task="${escapeHtml(latestEvent.taskId)}">打开该任务</button>
    </div>`;

  queue.innerHTML = `${eventsStale ? '<p class="resource-inline-note">动作日志同步失败；以下为上次成功读取值。</p>' : ""}${candidates.slice(0, 6).map((entry, index) => `
    <div class="review-item">
      <span class="review-item-index">${String(index + 1).padStart(2, "0")}</span>
      <div><strong>${escapeHtml(entry.task.title)}</strong><p>${escapeHtml(entry.round.title)} · ${entry.events.length} 条动作</p></div>
      ${eventResultBadge(entry.lastEvent)}
      <button class="task-btn record task-record-open" type="button" data-task="${escapeHtml(entry.task.id)}">查看</button>
    </div>
  `).join("")}`;

  const feedbackRows = candidates.filter((entry) => (
    entry.feedback && Number(entry.feedback.action_count) > 0
  )).slice(0, 5);
  if (!feedbackCanDisplay) {
    const state = learningResource("feedback");
    feedbackEl.innerHTML = `<div class="resource-state-panel ${state.phase === "loading" ? "" : "is-error"}"><strong>${escapeHtml(learningResourceStateLabel("feedback"))}</strong><small>动作历史仍可查看；任务建议未就绪，不能显示成“暂无建议”。</small></div>`;
  } else {
    feedbackEl.innerHTML = `${learningResourceHasStaleData("feedback") ? '<p class="resource-inline-note">任务建议同步失败；以下为上次成功读取值。</p>' : ""}${feedbackRows.length ? feedbackRows.map((entry) => `
      <div class="feedback-item">
        <span class="feedback-kind">${escapeHtml(feedbackTypeLabel(entry.feedback.feedback_type))}</span>
        <strong>${escapeHtml(entry.task.title)}</strong>
        <p>${escapeHtml(entry.feedback.next_suggestion || entry.feedback.message || "当前聚合反馈没有下一步建议。")}</p>
      </div>
    `).join("") : '<div class="empty-state">任务已有动作，但当前反馈聚合尚未形成可显示的建议。</div>'}`;
  }

  timeline.innerHTML = timelineEvents.slice(0, 12).map((event) => {
    const meta = taskMeta(event.taskId);
    return `
      <div class="timeline-item">
        <div class="timeline-item-head">
          <time datetime="${escapeHtml(event.timestamp || "")}" title="${escapeHtml(event.timestamp || "")}">${escapeHtml(formatReviewEventTime(event.timestamp))}</time>
          ${eventResultBadge(event)}
        </div>
        <strong>${escapeHtml(meta?.task?.title || event.taskId)}</strong>
        <p>${escapeHtml(actionLabel(event.action_type))}</p>
        ${event.note ? `<blockquote>${escapeHtml(event.note)}</blockquote>` : ""}
        ${event.evidence_path ? `<code>${escapeHtml(event.evidence_path)}</code>` : ""}
      </div>`;
  }).join("");

  bindReviewActions(current);
  bindReviewActions(queue);
}

function moduleDisplayCode(roundId) {
  return {
    round_00: "00",
    round_01: "01",
    round_02: "02",
    round_06: "03",
    plan_vps: "04",
    plan_linux: "导览",
  }[roundId] || "—";
}

function learningCheckpoints() {
  return typeof LEARNING_CHECKPOINTS === "undefined" ? [] : LEARNING_CHECKPOINTS;
}

function learningCheckpointById(checkpointId) {
  return learningCheckpoints().find((checkpoint) => checkpoint.id === checkpointId) || null;
}

function learningCheckpointForRound(roundId) {
  return learningCheckpoints().find((checkpoint) => checkpoint.round_id === roundId) || null;
}

function learningCheckpointRound(checkpoint) {
  return (ROUNDS || []).find((round) => round.id === checkpoint?.round_id) || null;
}

function learningCheckpointEntries(checkpoint) {
  const round = learningCheckpointRound(checkpoint);
  if (!round) return [];
  return (round.weeks || []).flatMap((week) => (week.tasks || []).map((task) => ({ round, week, task })));
}

function learningCheckpointStats(checkpoint) {
  const entries = learningCheckpointEntries(checkpoint);
  const done = entries.filter((entry) => isTaskDone(entry.task.id)).length;
  const total = entries.length;
  return {
    entries,
    done,
    total,
    pct: total ? Math.round(done / total * 100) : 0,
    complete: total > 0 && done === total,
  };
}

function firstOpenTaskForCheckpoint(checkpoint) {
  return learningCheckpointEntries(checkpoint).find((entry) => !isTaskDone(entry.task.id)) || null;
}

function selectedLearningCheckpoint() {
  const checkpoints = learningCheckpoints();
  const selected = learningCheckpointById(activeCheckpointId);
  if (selected) return selected;
  const receiptCheckpoint = learningCheckpointById(completionReceipt?.checkpointId)
    || learningCheckpointForRound(taskMeta(completionReceipt?.taskId)?.round?.id);
  const next = typeof findGlobalNextTask === "function" ? findGlobalNextTask() : null;
  const inferred = receiptCheckpoint
    || learningCheckpointForRound(next?.round?.id)
    || (routeFocusedRound ? learningCheckpointForRound(activeRound) : null)
    || [...checkpoints].reverse().find((checkpoint) => learningCheckpointEntries(checkpoint).length)
    || checkpoints[0]
    || null;
  activeCheckpointId = inferred?.id || "";
  return inferred;
}

function nextOpenTaskAfterCheckpoint(checkpointId) {
  const checkpoints = learningCheckpoints();
  const index = checkpoints.findIndex((checkpoint) => checkpoint.id === checkpointId);
  for (const checkpoint of checkpoints.slice(Math.max(0, index + 1))) {
    const entry = firstOpenTaskForCheckpoint(checkpoint);
    if (entry) return { ...entry, checkpoint, relation: "next" };
  }
  const globalNext = typeof findGlobalNextTask === "function" ? findGlobalNextTask() : null;
  const globalCheckpoint = learningCheckpointForRound(globalNext?.round?.id);
  if (globalNext && globalCheckpoint && globalCheckpoint.id !== checkpointId) {
    return { ...globalNext, checkpoint: globalCheckpoint, relation: "remaining" };
  }
  return null;
}

function eventCountForEntries(entries) {
  return (entries || []).reduce((sum, entry) => sum + eventsFor(entry.task.id).length, 0);
}

function allEventRows() {
  return registeredCourseTaskIds().flatMap((taskId) => registeredEventsForTask(taskId));
}

function openLearningCheckpoint(checkpointId) {
  const checkpoint = learningCheckpointById(checkpointId);
  if (!checkpoint) return;
  activeCheckpointId = checkpoint.id;
  renderCompletion();
  showView("completion", { updateHash: true, focus: true });
}

function continueToLearningTask(taskId, options = {}) {
  const registeredTaskId = registeredLearningTaskId(taskId);
  if (!registeredTaskId) return;
  const commit = () => {
    completionReceipt = null;
    applyLearningTaskContext(registeredTaskId);
    activeWorkspaceTab = "task";
    writeAppViewLocation("learn", registeredTaskId, "push");
    render();
    showView("learn", {
      updateHash: false,
      focus: true,
      focusTarget: "#learnHeading",
      navigationTransaction: true,
    });
  };
  runHomeTaskNavigation(registeredTaskId, options.sourceElement, commit);
}

function renderGrowthActivity() {
  const container = document.getElementById("growthActivity");
  if (!container) return;
  const state = learningResource("events");
  if (!learningResourceCanDisplay("events")) {
    const copy = state.phase === "loading"
      ? "正在读取动作记录…"
      : state.phase === "unavailable"
        ? "当前模式无法读取动作日志；这里不会把不可用显示成 0。"
        : "动作日志读取失败；恢复服务后可重新同步。";
    container.innerHTML = `<div class="resource-state-panel ${state.phase === "loading" ? "" : "is-error"}"><strong>${escapeHtml(copy)}</strong>${state.error ? `<small>${escapeHtml(state.error)}</small>` : ""}</div>`;
    setElementText("growthActivityTotal", "—");
    return;
  }

  const rows = allEventRows();
  const today = new Date();
  today.setHours(12, 0, 0, 0);
  const days = Array.from({ length: 7 }, (_, index) => {
    const date = new Date(today);
    date.setDate(today.getDate() - (6 - index));
    const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
    const count = rows.filter((event) => {
      const parsed = new Date(event.timestamp || "");
      if (Number.isNaN(parsed.getTime())) return false;
      const eventKey = `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, "0")}-${String(parsed.getDate()).padStart(2, "0")}`;
      return eventKey === key;
    }).length;
    return {
      key,
      count,
      label: new Intl.DateTimeFormat("zh-CN", { weekday: "narrow" }).format(date),
      dateLabel: `${date.getMonth() + 1}月${date.getDate()}日`,
    };
  });
  const max = Math.max(1, ...days.map((day) => day.count));
  const recentTotal = days.reduce((sum, day) => sum + day.count, 0);
  const staleNote = learningResourceHasStaleData("events")
    ? '<p class="resource-inline-note">动作日志同步失败；以下为上次成功读取值。</p>'
    : "";
  container.innerHTML = `
    ${staleNote}
    <div class="activity-bars" role="list" aria-label="最近七天动作记录">
      ${days.map((day) => `
        <div class="activity-day" role="listitem" aria-label="${escapeHtml(day.dateLabel)}：${day.count} 条动作记录">
          <span class="activity-value">${day.count || ""}</span>
          <span class="activity-track" aria-hidden="true"><span style="--activity-height:${Math.round(day.count / max * 100)}%"></span></span>
          <small>${escapeHtml(day.label)}</small>
        </div>
      `).join("")}
    </div>
    ${recentTotal === 0 && learningResourceIsVerified("events")
      ? '<div class="activity-empty"><p>最近 7 天还没有动作记录。</p><button class="text-action" type="button" id="growthActivityStart">进入当前任务 →</button></div>'
      : ""}
  `;
  setElementText("growthActivityTotal", `${recentTotal} 条记录`);
  container.querySelector("#growthActivityStart")?.addEventListener("click", () => {
    const next = typeof findGlobalNextTask === "function" ? findGlobalNextTask() : null;
    if (next) continueToLearningTask(next.task.id);
  });
}

function renderLearningCheckpoints() {
  const container = document.getElementById("checkpointList");
  if (!container) return;
  const checkpoints = learningCheckpoints();
  const progressState = learningResource("progress");
  const canDisplay = learningResourceCanDisplay("progress");
  const verified = learningResourceIsVerified("progress");
  if (!canDisplay) {
    const copy = progressState.phase === "loading"
      ? "正在读取模块任务状态…"
      : "进度数据不可用，暂时不能判断任何模块是否完成。";
    container.innerHTML = `<div class="resource-state-panel ${progressState.phase === "loading" ? "" : "is-error"}"><strong>${escapeHtml(copy)}</strong>${progressState.error ? `<small>${escapeHtml(progressState.error)}</small>` : ""}</div>`;
    return;
  }
  const globalNext = typeof findGlobalNextTask === "function" ? findGlobalNextTask() : null;
  const currentCheckpoint = learningCheckpointForRound(globalNext?.round?.id);
  container.innerHTML = checkpoints.map((checkpoint) => {
    const stats = learningCheckpointStats(checkpoint);
    const current = currentCheckpoint?.id === checkpoint.id;
    const kindLabel = checkpoint.kind === "guide" ? "资料" : `MODULE ${checkpoint.code}`;
    const stateLabel = stats.complete ? "任务均已标记完成" : `${stats.total - stats.done} 项待完成`;
    return `
      <button class="checkpoint-item${current ? " is-current" : ""}${stats.complete ? " is-complete" : ""}" type="button" data-checkpoint-open="${escapeHtml(checkpoint.id)}" ${verified ? "" : "disabled"} aria-label="查看${escapeHtml(checkpoint.name)}检查点，${stats.done}/${stats.total} 个任务已完成">
        <span class="checkpoint-code">${escapeHtml(kindLabel)}</span>
        <strong>${escapeHtml(checkpoint.name)}</strong>
        <small>${escapeHtml(checkpoint.note)}</small>
        <span class="checkpoint-track" aria-hidden="true"><span style="width:${stats.pct}%"></span></span>
        <span class="checkpoint-meta"><b>${stats.done} / ${stats.total}</b><em>${escapeHtml(stateLabel)}</em></span>
      </button>
    `;
  }).join("");
  if (!verified) {
    container.insertAdjacentHTML("afterbegin", `<p class="resource-inline-note">${escapeHtml(learningResourceStateLabel("progress"))}；恢复实时进度后才能打开检查点。</p>`);
  }
  container.querySelectorAll("[data-checkpoint-open]").forEach((button) => {
    button.addEventListener("click", () => openLearningCheckpoint(button.dataset.checkpointOpen));
  });
}

function renderGrowth() {
  const taskIds = registeredCourseTaskIds();
  const progressCanDisplay = learningResourceCanDisplay("progress");
  const progressVerified = learningResourceIsVerified("progress");
  const doneCount = progressCanDisplay ? taskIds.filter((id) => isTaskDone(id)).length : 0;
  const pct = progressCanDisplay && taskIds.length ? Math.round(doneCount / taskIds.length * 100) : 0;
  const ring = document.getElementById("growthProgressRing");
  if (ring) {
    ring.style.setProperty("--growth-progress", `${pct * 3.6}deg`);
    if (progressCanDisplay) {
      ring.setAttribute("aria-valuenow", String(pct));
      ring.removeAttribute("aria-valuetext");
    } else {
      ring.removeAttribute("aria-valuenow");
      ring.setAttribute("aria-valuetext", learningResourceStateLabel("progress"));
    }
  }
  setElementText("growthDoneValue", progressCanDisplay ? String(doneCount) : "—");
  setElementText("growthDoneMeta", progressCanDisplay ? `/ ${taskIds.length} 项任务` : "/ — 项任务");
  setElementText("growthProgressSource", learningResourceStateLabel("progress"));

  const next = progressCanDisplay && typeof findGlobalNextTask === "function" ? findGlobalNextTask() : null;
  const currentCheckpoint = learningCheckpointForRound(next?.round?.id)
    || (progressCanDisplay ? [...learningCheckpoints()].reverse().find((checkpoint) => learningCheckpointStats(checkpoint).complete) : null);
  setElementText("growthModuleValue", next ? moduleDisplayCode(next.round.id) : (taskIds.length && doneCount === taskIds.length ? "完成" : "—"));
  setElementText("growthModuleMeta", next?.round?.title || (taskIds.length && doneCount === taskIds.length ? "全部课程任务均已标记完成" : "课程任务状态不可用"));
  const currentButton = document.getElementById("growthCurrentCheckpoint");
  if (currentButton) {
    currentButton.disabled = !progressVerified || !currentCheckpoint;
    currentButton.querySelector("span")?.replaceChildren(document.createTextNode(currentCheckpoint ? `查看 ${currentCheckpoint.code} 检查点` : "查看当前检查点"));
    currentButton.onclick = () => currentCheckpoint && openLearningCheckpoint(currentCheckpoint.id);
  }

  const evidenceCanDisplay = learningResourceCanDisplay("events");
  const evidenceCount = evidenceCanDisplay ? allEventRows().length : 0;
  setElementText("growthEvidenceValue", evidenceCanDisplay ? String(evidenceCount) : "—");
  setElementText("growthEvidenceMeta", learningResourceStateLabel("events"));
  renderGrowthActivity();
}

function renderCompletion() {
  const shell = document.getElementById("completionState");
  if (!shell) return;
  const progressState = learningResource("progress");
  if (!learningResourceIsVerified("progress")) {
    shell.className = "completion-shell is-unavailable";
    const waiting = progressState.phase === "loading";
    shell.innerHTML = `
      <div class="resource-state-panel ${waiting ? "" : "is-error"}">
        <span class="section-kicker">MODULE CHECKPOINT</span>
        <h2 id="completionHeading">${waiting ? "正在核对任务状态" : "暂时无法核对检查点"}</h2>
        <p>${escapeHtml(waiting ? "完成与未完成不会在数据就绪前被推断。" : learningResourceStateLabel("progress"))}</p>
        ${progressState.error ? `<small>${escapeHtml(progressState.error)}</small>` : ""}
        ${waiting ? "" : '<button class="btn" type="button" id="completionRetry">重新同步</button>'}
      </div>
    `;
    shell.querySelector("#completionRetry")?.addEventListener("click", () => { void syncAppData(); });
    return;
  }

  const checkpoint = selectedLearningCheckpoint();
  if (!checkpoint) {
    shell.className = "completion-shell is-unavailable";
    shell.innerHTML = '<div class="resource-state-panel is-error"><span class="section-kicker">MODULE CHECKPOINT</span><h2 id="completionHeading">暂无检查点</h2><p>课程任务组尚未注册。</p></div>';
    return;
  }
  const stats = learningCheckpointStats(checkpoint);
  const openTask = firstOpenTaskForCheckpoint(checkpoint);
  const nextTask = stats.complete ? nextOpenTaskAfterCheckpoint(checkpoint.id) : (openTask ? { ...openTask, checkpoint, relation: "current" } : null);
  const courseTaskIds = registeredCourseTaskIds();
  const courseDoneCount = courseTaskIds.filter((taskId) => isTaskDone(taskId)).length;
  const courseComplete = courseTaskIds.length > 0 && courseDoneCount === courseTaskIds.length;
  const eventCanDisplay = learningResourceCanDisplay("events");
  const eventVerified = learningResourceIsVerified("events");
  const checkpointEvents = eventCanDisplay ? eventCountForEntries(stats.entries) : 0;
  const kindLabel = checkpoint.kind === "guide" ? "资料检查点" : `MODULE ${checkpoint.code}`;
  const headline = stats.complete
    ? (courseComplete ? "课程任务已全部标记完成" : "本组任务已全部标记完成")
    : "继续这个检查点";
  const remaining = Math.max(0, stats.total - stats.done);
  const actionLabel = nextTask
    ? (stats.complete
      ? (nextTask.relation === "next"
        ? `进入${nextTask.checkpoint.kind === "guide" ? "课程导览" : "下一模块"}`
        : "前往未完成任务")
      : "继续下一未完成任务")
    : "返回成长记录";
  const evidenceText = eventCanDisplay ? String(checkpointEvents) : "—";
  const evidenceMeta = eventVerified
    ? "来自 action log"
    : learningResourceStateLabel("events");
  shell.className = `completion-shell${stats.complete ? " is-complete" : ""}`;
  shell.innerHTML = `
    <section class="completion-intro">
      <span class="section-kicker">${escapeHtml(kindLabel)} / CHECKPOINT</span>
      <p class="completion-code">${escapeHtml(checkpoint.code)}</p>
      <h2 id="completionHeading">${escapeHtml(headline)}</h2>
      <p>${escapeHtml(checkpoint.name)} · ${escapeHtml(checkpoint.note)}</p>
      <span class="completion-state-label">${stats.complete ? `${stats.done}/${stats.total} 个任务均有完成标记` : `还有 ${remaining} 个任务未标记完成`}</span>
    </section>
    <section class="completion-portal" aria-label="${escapeHtml(checkpoint.name)}任务完成进度">
      <span class="completion-halo" aria-hidden="true"></span>
      <div class="completion-portal-core">
        <span>${stats.complete ? "TASKS MARKED" : "TASK PROGRESS"}</span>
        <strong>${stats.done}</strong>
        <small>/ ${stats.total} 项任务</small>
        <div class="route-track" role="progressbar" aria-label="${escapeHtml(checkpoint.name)}任务进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${stats.pct}"><span style="width:${stats.pct}%"></span></div>
        <b>${stats.pct}%</b>
      </div>
    </section>
    <section class="completion-summary" aria-label="检查点证据摘要">
      <div><span class="section-kicker cool">RECORDED EVIDENCE</span><h3>这次能确认什么</h3></div>
      <dl class="completion-facts">
        <div><dt>完成标记</dt><dd>${stats.done} / ${stats.total}</dd></div>
        <div><dt>动作记录</dt><dd>${escapeHtml(evidenceText)}<small>${escapeHtml(evidenceMeta)}</small></dd></div>
      </dl>
      <p class="completion-honesty">完成标记来自实时进度记录；动作数来自 action log。这些记录不等同自动验收或能力判断。</p>
      <div class="completion-next">
        <span>${nextTask ? "下一步" : "课程状态"}</span>
        <strong>${escapeHtml(nextTask?.task?.title || "所有已注册任务均已标记完成")}</strong>
        <small>${escapeHtml(nextTask ? `${nextTask.checkpoint.name} · ${nextTask.week.title}` : "可返回成长记录核对各检查点")}</small>
      </div>
      <button class="physical-button" type="button" id="completionPrimary"><span>${escapeHtml(actionLabel)}</span><span aria-hidden="true">→</span></button>
    </section>
  `;
  shell.querySelector("#completionPrimary")?.addEventListener("click", () => {
    if (nextTask) continueToLearningTask(nextTask.task.id);
    else showView("growth", { updateHash: true, focus: true });
  });
}

function knowledgeDocuments() {
  return [...document.querySelectorAll(".knowledge-item[data-knowledge-id]")].map((item) => ({
    id: item.dataset.knowledgeId || "",
    file: item.dataset.knowledgeFile || "",
    title: item.dataset.knowledgeTitle || item.querySelector("strong")?.textContent?.trim() || "",
    subtitle: item.querySelector("small")?.textContent?.trim() || "",
    keywords: item.dataset.knowledgeKeywords || "",
    code: item.querySelector(":scope > span")?.textContent?.trim() || "",
    item,
  })).filter((documentMeta) => documentMeta.id && documentMeta.file);
}

function knowledgeDocumentById(documentId) {
  return knowledgeDocuments().find((documentMeta) => documentMeta.id === documentId) || null;
}

function knowledgeDocumentByFile(filePath) {
  const normalized = String(filePath || "").replace(/^\//, "");
  return knowledgeDocuments().find((documentMeta) => documentMeta.file === normalized) || null;
}

function defaultKnowledgeDocument() {
  return knowledgeDocumentById("round-00") || knowledgeDocuments()[0] || null;
}

function knowledgeSelectionFromLocation() {
  const query = new URLSearchParams(window.location.search || "");
  const requestedId = query.get("manual") || "";
  const documentMeta = requestedId
    ? (knowledgeDocumentById(requestedId) || defaultKnowledgeDocument())
    : (knowledgeDocumentById(currentKnowledgeId) || defaultKnowledgeDocument());
  const rawSection = query.get("manualSection") || "";
  return {
    documentMeta,
    sectionKey: /^s\d+$/.test(rawSection) ? rawSection : "",
    canonical: !!documentMeta && requestedId === documentMeta.id && (!rawSection || /^s\d+$/.test(rawSection)),
  };
}

function updateKnowledgeLocation(documentId, sectionKey = "", mode = "push") {
  if (!documentId || mode === "none") return;
  const url = new URL(window.location.href);
  url.searchParams.set("manual", documentId);
  if (sectionKey) url.searchParams.set("manualSection", sectionKey);
  else url.searchParams.delete("manualSection");
  url.hash = "knowledge";
  const nextState = {
    ...(history.state || {}),
    view: "knowledge",
    manualId: documentId,
    manualSection: sectionKey,
  };
  delete nextState.readerModal;
  delete nextState.readerContext;
  const method = mode === "replace" ? "replaceState" : "pushState";
  const nextUrl = `${url.pathname}${url.search}${url.hash}`;
  const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (method === "pushState" && nextUrl === currentUrl) return;
  history[method](nextState, "", nextUrl);
}

function stripKnowledgeMarkdown(value) {
  return String(value || "")
    .replace(/```[\s\S]*?```/g, (block) => block.replace(/```\w*\n?|```/g, " "))
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[`>*_#|]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function knowledgeSections(text, documentMeta) {
  if (!String(text || "").trim()) return [];
  const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
  const headings = [];
  let inCodeFence = false;
  lines.forEach((line, lineIndex) => {
    if (/^```/.test(line.trim())) {
      inCodeFence = !inCodeFence;
      return;
    }
    if (inCodeFence) return;
    const match = line.match(/^(#{1,3})\s+(.+)$/);
    if (!match) return;
    headings.push({
      key: `s${headings.length}`,
      level: match[1].length,
      title: stripKnowledgeMarkdown(match[2]),
      lineIndex,
    });
  });
  if (!headings.length) {
    return [{
      key: "s0",
      level: 1,
      title: documentMeta?.title || "文档正文",
      text: stripKnowledgeMarkdown(text),
    }];
  }
  return headings.map((heading, index) => ({
    ...heading,
    text: stripKnowledgeMarkdown(lines.slice(heading.lineIndex, headings[index + 1]?.lineIndex ?? lines.length).join("\n")),
  }));
}

function knowledgeExcerpt(text, query) {
  const clean = stripKnowledgeMarkdown(text);
  const normalized = clean.toLocaleLowerCase();
  const needle = String(query || "").toLocaleLowerCase();
  const matchIndex = normalized.indexOf(needle);
  if (matchIndex < 0) return clean.slice(0, 88);
  const start = Math.max(0, matchIndex - 34);
  const end = Math.min(clean.length, matchIndex + needle.length + 54);
  return `${start ? "…" : ""}${clean.slice(start, end)}${end < clean.length ? "…" : ""}`;
}

function highlightedKnowledgeText(text, query) {
  const source = String(text || "");
  const normalized = source.toLocaleLowerCase();
  const needle = String(query || "").toLocaleLowerCase();
  const index = normalized.indexOf(needle);
  if (!needle || index < 0) return escapeHtml(source);
  return `${escapeHtml(source.slice(0, index))}<mark>${escapeHtml(source.slice(index, index + needle.length))}</mark>${escapeHtml(source.slice(index + needle.length))}`;
}

function knowledgeTextMatches(value, query) {
  const source = String(value || "");
  const needle = String(query || "").trim();
  if (!needle) return false;
  if (/^[a-z0-9]{1,2}$/i.test(needle)) {
    const escaped = needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(`(^|[^a-z0-9])${escaped}([^a-z0-9]|$)`, "i").test(source);
  }
  return source.toLocaleLowerCase().includes(needle.toLocaleLowerCase());
}

async function loadKnowledgeDocument(documentMeta, options = {}) {
  if (!documentMeta) throw new Error("手册条目未登记");
  if (window.location.protocol === "file:") {
    const unavailable = new Error("当前文件模式不能读取课程资料，请启动本地服务后重试");
    unavailable.unavailable = true;
    throw unavailable;
  }
  if (!options.force && knowledgeDocumentCache.has(documentMeta.id)) {
    return knowledgeDocumentCache.get(documentMeta.id);
  }
  if (!options.force && knowledgeDocumentLoads.has(documentMeta.id)) {
    return knowledgeDocumentLoads.get(documentMeta.id);
  }
  const resourcePath = "/" + documentMeta.file.replace(/^\//, "");
  const loadPromise = (async () => {
    const res = await fetchWithTimeout(`${resourcePath}?_=${Date.now()}`, { cache: "no-store", signal: options.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const text = await res.text();
    knowledgeDocumentCache.set(documentMeta.id, text);
    knowledgeDocumentErrors.delete(documentMeta.id);
    return text;
  })();
  knowledgeDocumentLoads.set(documentMeta.id, loadPromise);
  try {
    return await loadPromise;
  } catch (error) {
    knowledgeDocumentErrors.set(documentMeta.id, String(error?.message || error || "读取失败"));
    throw error;
  } finally {
    if (knowledgeDocumentLoads.get(documentMeta.id) === loadPromise) {
      knowledgeDocumentLoads.delete(documentMeta.id);
    }
  }
}

function updateKnowledgeSourceSelection(documentMeta) {
  const panel = document.getElementById("knowledgeReaderPanel");
  const sourceList = document.querySelector(".knowledge-source-list");
  document.querySelectorAll(".knowledge-item[data-knowledge-id]").forEach((item) => {
    const selected = item.dataset.knowledgeId === documentMeta?.id;
    item.classList.toggle("active", selected);
    item.setAttribute("aria-selected", selected ? "true" : "false");
    item.tabIndex = selected ? 0 : -1;
    if (selected && panel) panel.setAttribute("aria-labelledby", item.id);
    if (selected && sourceList && window.matchMedia?.("(max-width: 720px)")?.matches) {
      requestAnimationFrame(() => {
        const targetLeft = item.offsetLeft - sourceList.offsetLeft - (sourceList.clientWidth - item.offsetWidth) / 2;
        sourceList.scrollTo({ left: Math.max(0, targetLeft), behavior: "auto" });
      });
    }
  });
}

function setKnowledgeResourceState(phase, documentMeta, error = "") {
  knowledgeResourcePhase = phase;
  const workbench = document.getElementById("knowledgeWorkbench");
  const status = document.getElementById("knowledgeResourceStatus");
  const popout = document.getElementById("knowledgeReaderPopout");
  if (workbench) workbench.dataset.state = phase;
  if (popout) popout.disabled = !(phase === "ready" || phase === "ready-empty");
  if (!status) return;
  const messages = {
    loading: `正在读取 ${documentMeta?.title || "课程真源"}…`,
    ready: "已读取原始 Markdown · 只读",
    "ready-empty": "文档返回为空；未推断任何内容",
    error: `读取失败 · ${error || "请重试"}`,
    unavailable: error || "当前模式不可读取课程资料",
  };
  status.textContent = messages[phase] || "等待读取课程真源";
}

function knowledgeSearchMatches(query) {
  const needle = String(query || "").trim().toLocaleLowerCase();
  if (!needle) return [];
  const matches = [];
  knowledgeDocuments().forEach((documentMeta) => {
    const text = knowledgeDocumentCache.get(documentMeta.id);
    if (typeof text !== "string") return;
    const sections = knowledgeSections(text, documentMeta);
    const metaMatches = knowledgeTextMatches(`${documentMeta.title} ${documentMeta.subtitle} ${documentMeta.keywords}`, needle);
    let documentMatchCount = 0;
    sections.forEach((section) => {
      const sectionMatches = knowledgeTextMatches(`${section.title} ${section.text}`, needle);
      if (!sectionMatches && !(metaMatches && section.key === "s0")) return;
      matches.push({ documentMeta, section, excerpt: knowledgeExcerpt(section.text, query) });
      documentMatchCount += 1;
    });
    if (!documentMatchCount && metaMatches && sections[0]) {
      matches.push({ documentMeta, section: sections[0], excerpt: knowledgeExcerpt(sections[0].text, query) });
    }
  });
  return matches.slice(0, 30);
}

function updateKnowledgeSearchStatus(matchCount = null) {
  const status = document.getElementById("knowledgeSearchStatus");
  if (!status) return;
  const total = knowledgeDocuments().length;
  const ready = knowledgeDocumentCache.size;
  const failed = knowledgeDocumentErrors.size;
  if (!knowledgeSearchQuery.trim()) {
    status.textContent = ready === total
      ? `${total} 份课程真源已可搜索 · 只读`
      : `正在建立索引 · ${ready} / ${total} 份已读取${failed ? ` · ${failed} 份暂时失败` : ""}`;
    return;
  }
  const resultText = matchCount === null ? "正在检索" : `${matchCount} 个文本匹配`;
  status.textContent = `${resultText} · ${ready} / ${total} 份可搜索${failed ? ` · ${failed} 份读取失败` : ""}`;
}

function bindKnowledgeIndexActions() {
  const list = document.getElementById("knowledgeIndexList");
  if (!list) return;
  const buttons = [...list.querySelectorAll("[data-knowledge-open]")];
  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      const documentId = button.dataset.knowledgeOpen || "";
      const sectionKey = button.dataset.knowledgeSection || "";
      if (documentId === currentKnowledgeId && knowledgeCommittedId === documentId) {
        activateKnowledgeSection(sectionKey, { historyMode: "push", focus: true });
      } else {
        void openKnowledgeDocumentById(documentId, { historyMode: "push", sectionKey, focusSection: true, restoreScroll: false });
      }
    });
    button.addEventListener("keydown", (event) => {
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const index = buttons.indexOf(button);
      const nextIndex = event.key === "Home"
        ? 0
        : event.key === "End"
          ? buttons.length - 1
          : (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
      buttons[nextIndex]?.focus();
    });
  });
}

function renderKnowledgeIndex() {
  const list = document.getElementById("knowledgeIndexList");
  const heading = document.getElementById("knowledgeIndexHeading");
  const kicker = document.getElementById("knowledgeIndexKicker");
  const count = document.getElementById("knowledgeIndexCount");
  if (!list || !heading || !kicker || !count) return;
  const query = knowledgeSearchQuery.trim();
  const documentMeta = knowledgeDocumentById(currentKnowledgeId) || defaultKnowledgeDocument();
  if (query) {
    const matches = knowledgeSearchMatches(query);
    kicker.textContent = "SEARCH RESULTS";
    heading.textContent = "文本匹配";
    count.textContent = String(matches.length);
    list.setAttribute("aria-label", "手册搜索结果");
    if (matches.length) {
      list.innerHTML = matches.map(({ documentMeta: matchDocument, section, excerpt }) => `
        <button class="knowledge-index-item knowledge-search-result${matchDocument.id === currentKnowledgeId && section.key === currentKnowledgeSection ? " is-current" : ""}" type="button" data-knowledge-open="${escapeHtml(matchDocument.id)}" data-knowledge-section="${escapeHtml(section.key)}"${matchDocument.id === currentKnowledgeId && section.key === currentKnowledgeSection ? ' aria-current="location"' : ""}>
          <span>${escapeHtml(matchDocument.code)} · ${escapeHtml(matchDocument.title)}</span>
          <strong>${highlightedKnowledgeText(section.title, query)}</strong>
          <small>${highlightedKnowledgeText(excerpt, query)}</small>
        </button>
      `).join("");
    } else {
      const stillLoading = knowledgeDocumentCache.size + knowledgeDocumentErrors.size < knowledgeDocuments().length;
      list.innerHTML = `<div class="knowledge-index-state${stillLoading ? "" : " is-empty"}"><strong>${stillLoading ? "仍在核对其他课程真源" : "没有文本匹配"}</strong><p>${stillLoading ? "索引会随着课程真源读取完成自动更新。" : `五份已登记资料中没有找到“${escapeHtml(query)}”。`}</p></div>`;
    }
    updateKnowledgeSearchStatus(matches.length);
    bindKnowledgeIndexActions();
    return;
  }

  kicker.textContent = "DOCUMENT INDEX";
  heading.textContent = "本页目录";
  list.setAttribute("aria-label", "当前文档章节");
  const text = documentMeta ? knowledgeDocumentCache.get(documentMeta.id) : "";
  if (typeof text !== "string") {
    count.textContent = "—";
    const error = documentMeta ? knowledgeDocumentErrors.get(documentMeta.id) : "";
    list.innerHTML = `<div class="knowledge-index-state${error ? " is-error" : ""}"><strong>${error ? "目录暂不可用" : "正在读取目录"}</strong><p>${escapeHtml(error || "章节会在原文读取完成后出现。")}</p></div>`;
    updateKnowledgeSearchStatus();
    return;
  }
  const sections = knowledgeSections(text, documentMeta);
  count.textContent = String(sections.length);
  if (!sections.length) {
    list.innerHTML = '<div class="knowledge-index-state is-empty"><strong>这份文档没有章节</strong><p>空响应不会生成推测目录。</p></div>';
    updateKnowledgeSearchStatus();
    return;
  }
  list.innerHTML = sections.map((section, index) => `
    <button class="knowledge-index-item${section.key === currentKnowledgeSection ? " is-current" : ""}" type="button" data-knowledge-open="${escapeHtml(documentMeta.id)}" data-knowledge-section="${escapeHtml(section.key)}"${section.key === currentKnowledgeSection ? ' aria-current="location"' : ""}>
      <span>${String(index + 1).padStart(2, "0")} · H${section.level}</span>
      <strong>${escapeHtml(section.title)}</strong>
      <small>${escapeHtml(knowledgeExcerpt(section.text, ""))}</small>
    </button>
  `).join("");
  updateKnowledgeSearchStatus();
  bindKnowledgeIndexActions();
}

function bindKnowledgeDocumentLinks(container) {
  if (!container) return;
  container.querySelectorAll(".inline-doc-link").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      const documentMeta = knowledgeDocumentByFile(link.dataset.file || "");
      if (!documentMeta) {
        showToast("该仓库链接不在当前手册的五份登记资料中", "warn");
        return;
      }
      knowledgeSearchQuery = "";
      const input = document.getElementById("knowledgeSearch");
      if (input) input.value = "";
      void openKnowledgeDocumentById(documentMeta.id, { historyMode: "push", restoreScroll: false, focusReader: true });
    });
  });
}

function decorateKnowledgeDocument(body, documentMeta, text) {
  const sections = knowledgeSections(text, documentMeta);
  [...body.querySelectorAll("h1, h2, h3")].forEach((heading, index) => {
    const section = sections[index];
    if (!section) return;
    heading.id = `knowledge-${documentMeta.id}-${section.key}`;
    heading.dataset.knowledgeSection = section.key;
    heading.tabIndex = -1;
  });
  bindKnowledgeDocumentLinks(body);
}

function activateKnowledgeSection(sectionKey, options = {}) {
  const documentMeta = knowledgeDocumentById(currentKnowledgeId);
  const text = documentMeta ? knowledgeDocumentCache.get(documentMeta.id) : "";
  const sections = typeof text === "string" ? knowledgeSections(text, documentMeta) : [];
  const selected = sections.find((section) => section.key === sectionKey) || null;
  const body = document.getElementById("knowledgeReaderBody");
  if (!currentKnowledgeSection && selected && body?.dataset.loadedFor === currentKnowledgeId) {
    knowledgeReaderScrollPositions[currentKnowledgeId] = body.scrollTop;
  }
  currentKnowledgeSection = selected?.key || "";
  if (options.historyMode && options.historyMode !== "none") {
    updateKnowledgeLocation(currentKnowledgeId, currentKnowledgeSection, options.historyMode);
  }
  document.querySelectorAll(".knowledge-index-item").forEach((item) => {
    const active = item.dataset.knowledgeOpen === currentKnowledgeId
      && item.dataset.knowledgeSection === currentKnowledgeSection;
    item.classList.toggle("is-current", active);
    if (active) item.setAttribute("aria-current", "location");
    else item.removeAttribute("aria-current");
  });
  if (!selected) return false;
  const target = document.getElementById(`knowledge-${currentKnowledgeId}-${selected.key}`);
  if (!target) return false;
  target.scrollIntoView({ behavior: preferredScrollBehavior(options.behavior || "smooth"), block: "start" });
  if (options.focus) target.focus({ preventScroll: true });
  return true;
}

async function openKnowledgeDocumentById(documentId, options = {}) {
  const documentMeta = knowledgeDocumentById(documentId) || defaultKnowledgeDocument();
  const body = document.getElementById("knowledgeReaderBody");
  const heading = document.getElementById("knowledgeReaderTitle");
  const path = document.getElementById("knowledgeReaderPath");
  if (!documentMeta || !body || !heading) return;

  const previousSection = currentKnowledgeSection;
  const switchingDocument = knowledgeCommittedId && documentMeta.id !== knowledgeCommittedId;
  const enteringFirstSection = knowledgeCommittedId === documentMeta.id && !previousSection && !!options.sectionKey;
  if (body.dataset.loadedFor === knowledgeCommittedId && (switchingDocument || enteringFirstSection)) {
    knowledgeReaderScrollPositions[knowledgeCommittedId] = body.scrollTop;
  }
  currentKnowledgeId = documentMeta.id;
  currentKnowledgeFile = documentMeta.file;
  currentKnowledgeTitle = documentMeta.title;
  currentKnowledgeSection = options.sectionKey || "";
  updateKnowledgeSourceSelection(documentMeta);
  heading.textContent = documentMeta.title;
  if (path) path.textContent = documentMeta.file;
  if (options.historyMode && options.historyMode !== "none") {
    updateKnowledgeLocation(documentMeta.id, currentKnowledgeSection, options.historyMode);
  }

  const alreadyRendered = knowledgeCommittedId === documentMeta.id
    && body.dataset.loadedFor === documentMeta.id
    && (knowledgeResourcePhase === "ready" || knowledgeResourcePhase === "ready-empty")
    && !options.force;
  if (alreadyRendered) {
    renderKnowledgeIndex();
    if (currentKnowledgeSection && !activateKnowledgeSection(currentKnowledgeSection, { focus: !!options.focusSection, behavior: "auto" })) {
      currentKnowledgeSection = "";
      updateKnowledgeLocation(documentMeta.id, "", "replace");
      renderKnowledgeIndex();
    } else if (!currentKnowledgeSection) {
      body.scrollTop = options.restoreScroll ? (knowledgeReaderScrollPositions[documentMeta.id] || 0) : 0;
    }
    return;
  }

  const requestId = ++knowledgeRequestId;
  setKnowledgeResourceState("loading", documentMeta);
  body.setAttribute("aria-busy", "true");
  delete body.dataset.loadedFor;
  body.innerHTML = `<div class="reader-loading"><strong>正在读取课程真源</strong><p>${escapeHtml(documentMeta.file)}</p></div>`;
  renderKnowledgeIndex();
  try {
    const text = await loadKnowledgeDocument(documentMeta, { force: !!options.force });
    if (requestId !== knowledgeRequestId) return;
    knowledgeCommittedId = documentMeta.id;
    body.innerHTML = text.trim()
      ? renderMarkdown(text, documentMeta.file)
      : '<div class="knowledge-empty-source"><strong>这份文档目前为空</strong><p>没有根据空响应推断命令、章节或学习状态。</p></div>';
    body.dataset.loadedFor = documentMeta.id;
    body.setAttribute("aria-busy", "false");
    decorateKnowledgeDocument(body, documentMeta, text);
    setKnowledgeResourceState(text.trim() ? "ready" : "ready-empty", documentMeta);
    renderKnowledgeIndex();
    requestAnimationFrame(() => {
      if (currentKnowledgeSection) {
        if (!activateKnowledgeSection(currentKnowledgeSection, { focus: !!options.focusSection, behavior: "auto" })) {
          currentKnowledgeSection = "";
          updateKnowledgeLocation(documentMeta.id, "", "replace");
          renderKnowledgeIndex();
        }
      } else {
        body.scrollTop = options.restoreScroll ? (knowledgeReaderScrollPositions[documentMeta.id] || 0) : 0;
        if (options.focusReader) heading.focus({ preventScroll: true });
      }
    });
  } catch (error) {
    if (requestId !== knowledgeRequestId) return;
    const phase = error?.unavailable ? "unavailable" : "error";
    setKnowledgeResourceState(phase, documentMeta, String(error?.message || error || "读取失败"));
    body.setAttribute("aria-busy", "false");
    delete body.dataset.loadedFor;
    body.innerHTML = `
      <div class="reader-error knowledge-reader-error" tabindex="-1">
        <strong>${phase === "unavailable" ? "当前模式无法读取课程资料" : "课程真源暂时无法读取"}</strong>
        <p>${escapeHtml(documentMeta.file)} · ${escapeHtml(error?.message || "读取失败")}</p>
        <button class="task-btn read knowledge-retry" type="button">重新读取</button>
      </div>`;
    body.querySelector(".knowledge-retry")?.addEventListener("click", () => {
      void openKnowledgeDocumentById(documentMeta.id, { historyMode: "none", sectionKey: currentKnowledgeSection, force: true, focusReader: true });
    });
    renderKnowledgeIndex();
    if (options.focusReader) body.querySelector(".knowledge-reader-error")?.focus({ preventScroll: true });
  }
}

function restoreKnowledgeFromLocation(options = {}) {
  const selection = knowledgeSelectionFromLocation();
  if (!selection.documentMeta) return Promise.resolve();
  const historyMode = selection.canonical ? "none" : (options.historyMode || "replace");
  return openKnowledgeDocumentById(selection.documentMeta.id, {
    historyMode,
    sectionKey: selection.sectionKey,
    restoreScroll: true,
    focusSection: !!options.focusSection,
  });
}

function preloadKnowledgeIndex() {
  knowledgeDocuments().forEach((documentMeta) => {
    void loadKnowledgeDocument(documentMeta).then(() => {
      renderKnowledgeIndex();
    }).catch(() => {
      renderKnowledgeIndex();
    });
  });
}

function setupKnowledge() {
  const search = document.getElementById("knowledgeSearch");
  const clear = document.getElementById("knowledgeSearchClear");
  const sourceList = document.querySelector(".knowledge-source-list");
  const mobileQuery = window.matchMedia?.("(max-width: 720px)");
  const syncSourceOrientation = () => sourceList?.setAttribute("aria-orientation", mobileQuery?.matches ? "horizontal" : "vertical");
  syncSourceOrientation();
  mobileQuery?.addEventListener?.("change", syncSourceOrientation);

  const clearSearch = (options = {}) => {
    knowledgeSearchQuery = "";
    if (search) search.value = "";
    if (clear) clear.hidden = true;
    renderKnowledgeIndex();
    if (options.focus) search?.focus();
  };

  document.querySelectorAll(".knowledge-item[data-knowledge-id]").forEach((item) => {
    item.addEventListener("click", () => {
      clearSearch();
      void openKnowledgeDocumentById(item.dataset.knowledgeId || "", { historyMode: "push", restoreScroll: false });
      if (mobileQuery?.matches) {
        window.setTimeout(() => document.getElementById("knowledgeIndexPanel")?.scrollIntoView({ behavior: preferredScrollBehavior("smooth"), block: "start" }), 0);
      }
    });
    item.addEventListener("keydown", (event) => {
      const directionalKeys = mobileQuery?.matches ? ["ArrowRight", "ArrowLeft"] : ["ArrowDown", "ArrowUp"];
      if (![...directionalKeys, "Home", "End"].includes(event.key)) return;
      const items = [...document.querySelectorAll(".knowledge-item[data-knowledge-id]")];
      const index = items.indexOf(item);
      const forward = event.key === directionalKeys[0];
      const nextIndex = event.key === "Home"
        ? 0
        : event.key === "End"
          ? items.length - 1
          : (index + (forward ? 1 : -1) + items.length) % items.length;
      event.preventDefault();
      const next = items[nextIndex];
      next?.focus();
      next?.click();
    });
  });

  search?.addEventListener("input", () => {
    knowledgeSearchQuery = search.value;
    if (clear) clear.hidden = !knowledgeSearchQuery;
    renderKnowledgeIndex();
  });
  search?.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !search.value) return;
    event.preventDefault();
    clearSearch({ focus: true });
  });
  clear?.addEventListener("click", () => clearSearch({ focus: true }));
  document.addEventListener("keydown", (event) => {
    if (activeView !== "knowledge" || document.getElementById("readerModal")?.classList.contains("open")) return;
    const target = event.target;
    const isEditing = target instanceof HTMLElement && target.matches("input, textarea, select, [contenteditable='true']");
    const shortcut = (event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === "k";
    const slash = event.key === "/" && !isEditing;
    if (!shortcut && !slash) return;
    event.preventDefault();
    search?.focus();
    search?.select();
  });
  document.getElementById("knowledgeReaderPopout")?.addEventListener("click", () => {
    if (knowledgeCommittedId !== currentKnowledgeId || !["ready", "ready-empty"].includes(knowledgeResourcePhase)) return;
    void openMarkdownViewer(currentKnowledgeFile, currentKnowledgeTitle, { context: "knowledge", history: true, documentId: currentKnowledgeId });
  });
  preloadKnowledgeIndex();
  renderKnowledgeIndex();
}

function setupSpatialInteraction() {
  const stage = document.getElementById("spatialStage");
  if (!stage) return;
  const blocks = [...stage.querySelectorAll(".spatial-block")];
  const depths = [-18, -12, -5, 6, 12, 18];
  const rotationDepths = [-7, -4.5, -2, 2, 4.5, 7];
  const suspendedParallaxBlocks = new Set();
  const resetBlockInteractions = [];
  const resetBlockParallax = (block) => {
    block.style.setProperty("--parallax-x", "0px");
    block.style.setProperty("--parallax-y", "0px");
    block.style.setProperty("--parallax-rotation", "0deg");
  };
  const resetParallax = () => {
    stage.style.setProperty("--warm-x", "0px");
    stage.style.setProperty("--warm-y", "0px");
    stage.style.setProperty("--cool-x", "0px");
    stage.style.setProperty("--cool-y", "0px");
    stage.style.setProperty("--card-x", "0px");
    stage.style.setProperty("--card-y", "0px");
    stage.style.setProperty("--card-tilt-x", "0deg");
    stage.style.setProperty("--card-tilt-y", "0deg");
    blocks.forEach(resetBlockParallax);
  };
  const setParallax = (x, y) => {
    if (prefersReducedMotion()) {
      resetParallax();
      return;
    }
    stage.style.setProperty("--warm-x", `${x * -12}px`);
    stage.style.setProperty("--warm-y", `${y * -8}px`);
    stage.style.setProperty("--cool-x", `${x * 12}px`);
    stage.style.setProperty("--cool-y", `${y * 8}px`);
    stage.style.setProperty("--card-x", `${x * 6}px`);
    stage.style.setProperty("--card-y", `${y * 4}px`);
    stage.style.setProperty("--card-tilt-x", `${y * -4}deg`);
    stage.style.setProperty("--card-tilt-y", `${x * 4}deg`);
    blocks.forEach((block, index) => {
      if (suspendedParallaxBlocks.has(block)) {
        resetBlockParallax(block);
        return;
      }
      block.style.setProperty("--parallax-x", `${x * depths[index]}px`);
      block.style.setProperty("--parallax-y", `${y * depths[index] * 0.52}px`);
      block.style.setProperty("--parallax-rotation", `${x * rotationDepths[index]}deg`);
    });
  };
  stage.addEventListener("pointermove", (event) => {
    if (event.pointerType === "touch") return;
    const rect = stage.getBoundingClientRect();
    const x = Math.max(-1, Math.min(1, ((event.clientX - rect.left) / rect.width - 0.5) * 2));
    const y = Math.max(-1, Math.min(1, ((event.clientY - rect.top) / rect.height - 0.5) * 2));
    setParallax(x, y);
  });
  stage.addEventListener("pointerleave", () => setParallax(0, 0));

  blocks.forEach((block) => {
    let gesture = null;
    let returnTimer = 0;
    const clearLongPress = () => {
      if (!gesture?.longPressTimer) return;
      window.clearTimeout(gesture.longPressTimer);
      gesture.longPressTimer = 0;
    };
    const resetInteraction = () => {
      clearLongPress();
      window.clearTimeout(returnTimer);
      returnTimer = 0;
      const pointerId = gesture?.pointerId;
      gesture = null;
      if (pointerId !== undefined && block.hasPointerCapture?.(pointerId)) {
        block.releasePointerCapture?.(pointerId);
      }
      suspendedParallaxBlocks.delete(block);
      block.classList.remove("is-dragging", "is-returning");
      block.dataset.dragState = "idle";
      block.style.setProperty("--drag-x", "0px");
      block.style.setProperty("--drag-y", "0px");
      resetBlockParallax(block);
    };
    resetBlockInteractions.push(resetInteraction);
    const beginDragging = () => {
      if (!gesture || gesture.dragging) return;
      if (prefersReducedMotion()) {
        resetInteraction();
        return;
      }
      gesture.dragging = true;
      suspendedParallaxBlocks.add(block);
      resetBlockParallax(block);
      block.classList.remove("is-returning");
      block.classList.add("is-dragging");
      block.dataset.dragState = "dragging";
      block.setPointerCapture?.(gesture.pointerId);
    };
    block.addEventListener("pointerdown", (event) => {
      if (event.button !== undefined && event.button !== 0) return;
      if (prefersReducedMotion()) {
        resetInteraction();
        return;
      }
      window.clearTimeout(returnTimer);
      gesture = {
        pointerId: event.pointerId,
        pointerType: event.pointerType || "mouse",
        x: event.clientX,
        y: event.clientY,
        dragging: false,
        longPressTimer: 0,
      };
      block.dataset.dragState = "pressed";
      if (gesture.pointerType === "touch") {
        gesture.longPressTimer = window.setTimeout(beginDragging, 220);
      } else {
        block.setPointerCapture?.(event.pointerId);
      }
    });
    block.addEventListener("pointermove", (event) => {
      if (!gesture || gesture.pointerId !== event.pointerId) return;
      const rawX = event.clientX - gesture.x;
      const rawY = event.clientY - gesture.y;
      const moved = Math.hypot(rawX, rawY);
      if (!gesture.dragging) {
        if (gesture.pointerType === "touch") {
          if (moved > 6) {
            clearLongPress();
            block.dataset.dragState = "idle";
            gesture = null;
          }
          return;
        }
        if (moved < 6) return;
        beginDragging();
      }
      event.preventDefault();
      const limit = gesture.pointerType === "touch" ? 18 : 28;
      const dx = Math.max(-limit, Math.min(limit, rawX));
      const dy = Math.max(-limit, Math.min(limit, rawY));
      block.style.setProperty("--drag-x", `${dx}px`);
      block.style.setProperty("--drag-y", `${dy}px`);
    });
    block.addEventListener("touchmove", (event) => {
      if (gesture?.dragging) event.preventDefault();
    }, { passive: false });
    const release = (event) => {
      if (!gesture || (event?.pointerId !== undefined && gesture.pointerId !== event.pointerId)) return;
      clearLongPress();
      const wasDragging = gesture.dragging;
      const shouldReturn = wasDragging && !prefersReducedMotion();
      gesture = null;
      block.classList.remove("is-dragging");
      block.classList.toggle("is-returning", shouldReturn);
      block.dataset.dragState = shouldReturn ? "returning" : "idle";
      block.style.setProperty("--drag-x", "0px");
      block.style.setProperty("--drag-y", "0px");
      resetBlockParallax(block);
      if (shouldReturn) {
        suspendedParallaxBlocks.add(block);
        returnTimer = window.setTimeout(() => {
          block.classList.remove("is-returning");
          block.dataset.dragState = "idle";
          suspendedParallaxBlocks.delete(block);
        }, 340);
      } else {
        suspendedParallaxBlocks.delete(block);
      }
    };
    block.addEventListener("pointerup", release);
    block.addEventListener("pointercancel", release);
    block.addEventListener("lostpointercapture", release);
  });
  const syncMotionPreference = () => {
    const reduced = prefersReducedMotion();
    stage.dataset.motionPolicy = reduced ? "reduced" : "full";
    if (!reduced) return;
    resetParallax();
    resetBlockInteractions.forEach((resetInteraction) => resetInteraction());
  };
  if (reducedMotionMedia?.addEventListener) reducedMotionMedia.addEventListener("change", syncMotionPreference);
  else reducedMotionMedia?.addListener?.(syncMotionPreference);
  syncMotionPreference();
}

function setupPhysicalButtons() {
  const pointerOwners = new Map();
  const keyboardButtons = new Set();
  const releaseTimers = new WeakMap();
  const beginPress = (button) => {
    const timer = releaseTimers.get(button);
    if (timer) window.clearTimeout(timer);
    button.classList.remove("is-releasing");
    button.classList.add("is-pressed");
  };
  const releasePress = (button) => {
    if (!button?.classList.contains("is-pressed")) return;
    button.classList.remove("is-pressed");
    button.classList.add("is-releasing");
    const timer = window.setTimeout(() => {
      button.classList.remove("is-releasing");
      releaseTimers.delete(button);
    }, 320);
    releaseTimers.set(button, timer);
  };
  document.addEventListener("pointerdown", (event) => {
    const button = event.target.closest?.(".physical-button");
    if (!button || button.disabled || (event.button !== undefined && event.button !== 0)) return;
    pointerOwners.set(event.pointerId, button);
    beginPress(button);
  });
  const releasePointer = (event) => {
    const button = pointerOwners.get(event.pointerId);
    if (!button) return;
    pointerOwners.delete(event.pointerId);
    releasePress(button);
  };
  document.addEventListener("pointerup", releasePointer);
  document.addEventListener("pointercancel", releasePointer);
  document.addEventListener("keydown", (event) => {
    if (event.repeat || (event.key !== " " && event.key !== "Enter")) return;
    const button = event.target.closest?.(".physical-button");
    if (!button || button.disabled) return;
    keyboardButtons.add(button);
    beginPress(button);
  });
  document.addEventListener("keyup", (event) => {
    if (event.key !== " " && event.key !== "Enter") return;
    keyboardButtons.forEach(releasePress);
    keyboardButtons.clear();
  });
  window.addEventListener("blur", () => {
    pointerOwners.forEach(releasePress);
    pointerOwners.clear();
    keyboardButtons.forEach(releasePress);
    keyboardButtons.clear();
  });
}

function renderFeedbackHint(taskId) {
  const fb = feedbackFor(taskId);
  if (!fb || fb.feedback_type === "completed") return "";
  const text = fb.next_suggestion || fb.message || "";
  if (!text) return "";
  return `<div class="task-feedback">${escapeHtml(text)}</div>`;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function taskActionButtons(taskId, done) {
  if (!apiReady) return "";
  const pending = taskMutationPending(taskId);
  const pendingLabel = taskRunPending(taskId) ? "脚本运行中…" : (pending?.undo ? "正在撤销…" : "正在保存…");
  if (done) {
    return `<button type="button" class="task-btn undo${pending ? " is-task-action-pending" : ""}" data-task="${taskId}" data-action="undo" data-idle-label="撤销"${pending ? ' disabled aria-busy="true"' : ' aria-busy="false"'}>${pending ? pendingLabel : "撤销"}</button>`;
  }
  return `<button type="button" class="task-btn done task-complete-open${pending ? " is-task-action-pending" : ""}" data-task="${taskId}" data-idle-label="记录并完成"${pending ? ' disabled aria-busy="true"' : ' aria-busy="false"'}>${pending ? pendingLabel : "记录并完成"}</button>`;
}

function fileActionLabel(filePath, taskType) {
  const file = String(filePath || "");
  if (/\.(sh|py)$/i.test(file)) return "查看脚本";
  if (taskType === "reading" || /\.md$/i.test(file)) return "读教程";
  return "打开资料";
}

function taskRecordButton(taskId) {
  if (!apiReady) return "";
  if (!isTaskDone(taskId)) return "";
  const count = eventsFor(taskId).length;
  const label = count ? `记录 ${count}` : "记录";
  return `<button type="button" class="task-btn record task-record-open" data-task="${taskId}">${label}</button>`;
}

function isRunnableTask(task) {
  return !!(task && /\.(sh|py)$/i.test(task.file || "") && task.type === "exercise");
}

function taskRunButton(task) {
  if (!apiReady || !isRunnableTask(task)) return "";
  const pending = taskMutationPending(task.id);
  const running = taskRunPending(task.id);
  return `<button type="button" class="task-btn run task-run${pending ? " is-task-action-pending" : ""}" data-task="${task.id}" data-idle-label="运行脚本"${pending ? ' disabled aria-busy="true"' : ' aria-busy="false"'}>${running ? "正在运行…" : pending ? "状态更新中…" : "运行脚本"}</button>`;
}

function taskTerminalButton(task) {
  if (!apiReady || !taskUsesTerminal(task)) return "";
  return `<button type="button" class="task-btn terminal task-terminal" data-task="${task.id}">终端练习</button>`;
}

async function postTaskRun(taskId) {
  if (!apiReady) {
    showToast("请先运行 python3 scripts/progress_server.py 启动服务", "warn");
    return null;
  }
  if (taskRunPending(taskId)) return taskRunPending(taskId).promise;
  if (taskActionPending(taskId)) {
    showToast("本任务已有状态更新正在进行，请稍后再运行脚本", "warn");
    return null;
  }
  const meta = taskMeta(taskId);
  const file = meta?.task?.file || "";
  const title = meta?.task?.title || taskId;
  const roundId = meta?.round?.id || "";
  const runCheckpoint = learningCheckpointForRound(roundId);
  const checkpointWasComplete = runCheckpoint ? learningCheckpointStats(runCheckpoint).complete : false;
  const roundMatch = roundId.match(/round_(\d{2})/);
  const sandbox = roundMatch
    ? `~/cli-lab/round${Number(roundMatch[1])}`
    : "~/cli-lab";
  const ok = window.confirm(
    `将在本地沙盒执行白名单练习脚本：\n${file}\n\n工作目录：${sandbox}\n脚本可写入沙盒；只有本次 exercise 的登记验证点与整体成功同时成立时，才会自动完成该任务。继续？`
  );
  if (!ok) return null;
  const operationKey = taskId;
  const payloadFingerprint = JSON.stringify({ taskId, scriptPath: file });
  const retryable = retryableTaskRunOperationIds.get(operationKey);
  const operationId = retryable?.fingerprint === payloadFingerprint
    ? retryable.operationId
    : createTaskOperationId();
  const presentationContext = {
    view: activeView,
    workspaceTaskId,
    modalRequestId: modalReaderRequestId,
  };
  const presentationIsCurrent = () => activeView === presentationContext.view
    && (activeView !== "learn" || workspaceTaskId === presentationContext.workspaceTaskId)
    && modalReaderRequestId === presentationContext.modalRequestId;
  const pending = { taskId, operationId, payloadFingerprint, presentationContext, promise: null };
  const request = (async () => {
  try {
    const res = await fetchTaskAction(`/api/tasks/${encodeURIComponent(taskId)}/run`, {
      method: "POST",
      headers: {
        "Accept": "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ operation_id: operationId }),
    }, TASK_RUN_TIMEOUT_MS, "脚本运行等待超时；本地服务可能仍在执行，请稍后用同一操作重试核对结果。");
    const responseData = await parseTaskActionResponse(res, "脚本运行");
    const { data, event, execution, snapshot } = validateTaskRunResponse(responseData, taskId, operationId);
    retryableTaskRunOperationIds.delete(operationKey);
    const responseRevision = snapshot.revision;
    let stateCurrent = true;
    if (responseRevision < progressRevision) {
      await Promise.all([loadProgress(), loadFeedbackData(), loadEventData()]);
      stateCurrent = false;
    } else {
      commitLearningTransaction(snapshot, event);
    }
    const completedCheckpoint = stateCurrent
      && !execution.replayed_operation
      && execution.completion_applied
      && runCheckpoint
      && !checkpointWasComplete
      && learningCheckpointStats(runCheckpoint).complete
      ? runCheckpoint
      : null;
    const showResultHere = presentationIsCurrent();
    if (completedCheckpoint && showResultHere) activeCheckpointId = completedCheckpoint.id;
    const focusSnapshot = captureTaskFocusSnapshot();
    render();
    restoreTaskFocusSnapshot(focusSnapshot);
    if (showResultHere) {
      openExecutionResult(title, execution, taskId, {
        completedCheckpointId: completedCheckpoint?.id || "",
        stateCurrent,
      });
    }
    const result = execution.result;
    const resultLabel = !stateCurrent
      ? "运行回执已返回；当前学习状态已重新读取"
      : execution.replayed_operation
      ? "此前的脚本结果已核对并同步"
      : execution.completion_applied
        ? "脚本验证点已保存，任务已完成"
        : result === "ok"
          ? "脚本正常结束；未伪造额外完成状态"
          : "脚本已结束，请查看真实输出";
    showToast(
      showResultHere ? resultLabel : `${title}：${resultLabel}；已保留当前页面`,
      stateCurrent && result === "ok" ? "ok" : "warn",
    );
    return data;
  } catch (error) {
    if (error.retryOperation === false) {
      retryableTaskRunOperationIds.delete(operationKey);
    } else {
      retryableTaskRunOperationIds.set(operationKey, { operationId, fingerprint: payloadFingerprint });
    }
    const message = error.code === "run_in_progress"
      ? "同一练习沙盒仍有脚本在运行；稍后重试会沿用本次操作核对结果。"
      : error.code === "run_execution_state_uncertain"
        ? "上次脚本可能已产生沙盒改动，但结果未能持久化；请先检查产物，再决定是否以新操作重跑。"
        : error.message || "练习脚本请求失败";
    const showResultHere = presentationIsCurrent();
    if (showResultHere) {
      openExecutionResult(title, {
        result: "request_error",
        script_path: file,
        sandbox_path: sandbox,
        stderr: message,
      }, taskId);
    }
    showToast(
      showResultHere ? message : `${title}：${message}；已保留当前页面`,
      error.code === "run_in_progress" ? "warn" : "error",
    );
    return null;
  } finally {
    if (pendingTaskRuns.get(taskId) === pending) {
      pendingTaskRuns.delete(taskId);
      updateTaskActionControls(taskId);
    }
  }
  })();
  pending.promise = request;
  pendingTaskRuns.set(taskId, pending);
  updateTaskActionControls(taskId);
  leaveCompletionReceipt(taskId);
  return request;
}

function bindTaskActions(container) {
  container.querySelectorAll(".task-btn[data-action]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const id = btn.getAttribute("data-task");
      const undo = btn.getAttribute("data-action") === "undo";
      await postTaskAction(id, undo);
    });
  });
  container.querySelectorAll(".task-record-open").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      lastModalFocus = btn;
      lastModalFocusTaskId = btn.getAttribute("data-task") || "";
      openRecordViewer(btn.getAttribute("data-task"));
    });
  });
  container.querySelectorAll(".task-complete-open").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      lastModalFocus = btn;
      lastModalFocusTaskId = btn.getAttribute("data-task") || "";
      openRecordViewer(btn.getAttribute("data-task"), { requireNote: true });
    });
  });
  container.querySelectorAll(".task-run").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const id = btn.getAttribute("data-task");
      lastModalFocus = btn;
      lastModalFocusTaskId = id || "";
      await postTaskRun(id);
    });
  });
  container.querySelectorAll(".task-terminal").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      btn.disabled = true;
      try { await openTaskTerminal(btn.getAttribute("data-task")); } finally { btn.disabled = false; }
    });
  });
  container.querySelectorAll(".task-open").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      openInlineReader(
        btn.getAttribute("data-file"),
        btn.getAttribute("data-title") || "阅读",
        btn.getAttribute("data-task") || "",
      );
    });
  });
}

async function openInlineReader(filePath, title, taskId, options) {
  if (!filePath) return;
  const registeredTaskId = taskId ? registeredLearningTaskId(taskId) : "";
  if (taskId && !registeredTaskId) return;
  if (registeredTaskId && options?.silent && workspaceTaskId && workspaceTaskId !== registeredTaskId) return;
  const body = document.getElementById("inlineReaderBody");
  const heading = document.getElementById("inlineReaderTitle");
  const metaEl = document.getElementById("inlineReaderMeta");
  const popout = document.getElementById("inlineReaderPopout");
  if (!body || !heading) {
    openMarkdownViewer(filePath, title);
    return;
  }
  if (registeredTaskId) {
    leaveCompletionReceipt(registeredTaskId);
    if (!options?.silent) forceTerminalVisible = false;
    applyLearningTaskContext(registeredTaskId);
    if (!options?.silent) writeAppViewLocation("learn", registeredTaskId, "push");
    const meta = taskMeta(registeredTaskId);
    if (meta) {
      activeLane = meta.round.lane || activeLane;
      activeRound = meta.round.id || activeRound;
    }
    if (activeTerminalTaskId && activeTerminalTaskId !== registeredTaskId) activeTerminalTaskId = "";
  }
  const requestId = ++inlineReaderRequestId;
  if (inlineReaderFile && body) {
    inlineReaderScrollPositions[inlineReaderFile] = body.scrollTop;
  }
  inlineReaderTaskId = registeredTaskId || inlineReaderTaskId;
  inlineReaderFile = filePath;
  heading.textContent = title || filePath;
  if (metaEl) metaEl.textContent = filePath;
  if (popout) {
    popout.disabled = false;
    popout.dataset.file = filePath;
    popout.dataset.title = title || filePath;
  }
  body.setAttribute("aria-busy", "true");
  body.innerHTML = "<p class='reader-loading'>正在读取课程真源…</p>";
  if (registeredTaskId) {
    renderContinue();
    renderTerminal();
  }
  if (!options?.silent) {
    setWorkspaceTab("guide");
    scrollWorkspacePanel("inlineReaderPanel");
    heading.focus({ preventScroll: true });
  }
  try {
    const resourcePath = "/" + filePath.replace(/^\//, "");
    const separator = resourcePath.includes("?") ? "&" : "?";
    const res = await fetch(`${resourcePath}${separator}_=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const text = await res.text();
    if (requestId !== inlineReaderRequestId) return;
    body.innerHTML = /\.(sh|py|js|json)$/i.test(filePath)
      ? renderCodeDocument(text, filePath)
      : renderMarkdown(text, filePath);
    body.setAttribute("aria-busy", "false");
    bindReaderDocumentLinks(body);
    requestAnimationFrame(() => {
      body.scrollTop = inlineReaderScrollPositions[filePath] || 0;
    });
  } catch (err) {
    if (requestId !== inlineReaderRequestId) return;
    body.setAttribute("aria-busy", "false");
    body.innerHTML = `<div class='reader-error' tabindex='-1'><strong>课程资料暂时无法读取</strong><p>${escapeHtml(filePath)} · ${escapeHtml(err.message)}</p><button class='task-btn read inline-reader-retry' type='button'>重新读取</button></div>`;
    body.querySelector(".inline-reader-retry")?.addEventListener("click", () => openInlineReader(filePath, title, taskId, { silent: false }));
    if (!options?.silent) body.querySelector(".reader-error")?.focus({ preventScroll: true });
  }
}

async function openMarkdownViewer(filePath, title, options = {}) {
  if (!filePath) return;
  const knowledgeDocument = options.context === "knowledge"
    ? (knowledgeDocumentById(options.documentId) || knowledgeDocumentByFile(filePath))
    : null;
  if (options.context === "knowledge" && !knowledgeDocument) {
    showToast("该资料不在当前手册允许列表中", "warn");
    return;
  }
  if (knowledgeDocument) {
    filePath = knowledgeDocument.file;
    title = knowledgeDocument.title;
  }
  const requestId = ++modalReaderRequestId;
  const modal = document.getElementById("readerModal");
  const body = document.getElementById("readerBody");
  const heading = document.getElementById("readerTitle");
  if (!modal || !body) return;
  setReaderModalVariant();
  heading.textContent = title || filePath;
  body.setAttribute("aria-busy", "true");
  body.innerHTML = `<div class='reader-loading'><strong>正在读取课程真源</strong><p>${escapeHtml(filePath)}</p></div>`;
  revealReaderModal();
  if (options.context === "knowledge" && options.history && !knowledgeModalHistoryEntry) {
    history.pushState({
      ...(history.state || {}),
      view: "knowledge",
      manualId: knowledgeDocument.id,
      manualSection: currentKnowledgeSection,
      readerModal: "knowledge",
      readerContext: knowledgeDocument.id,
    }, "", window.location.href);
    knowledgeModalHistoryEntry = true;
  }
  try {
    let text = knowledgeDocument ? knowledgeDocumentCache.get(knowledgeDocument.id) : "";
    if (typeof text !== "string") {
      if (knowledgeDocument) {
        text = await loadKnowledgeDocument(knowledgeDocument);
      } else {
        const resourcePath = "/" + filePath.replace(/^\//, "");
        const separator = resourcePath.includes("?") ? "&" : "?";
        const res = await fetchWithTimeout(`${resourcePath}${separator}_=${Date.now()}`, { cache: "no-store" });
        if (!res.ok) throw new Error("HTTP " + res.status);
        text = await res.text();
      }
    }
    if (requestId !== modalReaderRequestId) return;
    body.setAttribute("aria-busy", "false");
    if (!text.trim()) {
      body.innerHTML = '<div class="knowledge-empty-source"><strong>这份文档目前为空</strong><p>没有根据空响应推断任何内容。</p></div>';
    } else if (/\.(sh|py|js|json)$/i.test(filePath)) {
      body.innerHTML = renderCodeDocument(text, filePath);
    } else {
      body.innerHTML = renderMarkdown(text, filePath);
    }
    bindReaderDocumentLinks(body, { context: options.context || "" });
  } catch (err) {
    if (requestId !== modalReaderRequestId) return;
    body.setAttribute("aria-busy", "false");
    body.innerHTML = `<div class='reader-error' tabindex='-1'><strong>课程真源暂时无法读取</strong><p>${escapeHtml(filePath)} · ${escapeHtml(err.message)}</p><button class='task-btn read modal-reader-retry' type='button'>重新读取</button></div>`;
    body.querySelector(".modal-reader-retry")?.addEventListener("click", () => {
      void openMarkdownViewer(filePath, title, { ...options, history: false });
    });
  }
}

function taskMeta(taskId) {
  for (const round of ROUNDS || []) {
    for (const week of round.weeks || []) {
      const task = (week.tasks || []).find((item) => item.id === taskId);
      if (task) return { round, week, task };
    }
  }
  return null;
}

function openRecordViewer(taskId, options = {}) {
  modalReaderRequestId += 1;
  const modal = document.getElementById("readerModal");
  const body = document.getElementById("readerBody");
  const heading = document.getElementById("readerTitle");
  if (!modal || !body || !heading) return;
  setReaderModalVariant();

  const meta = taskMeta(taskId);
  const title = meta?.task?.title || taskId;
  const done = isTaskDone(taskId);
  const fb = feedbackFor(taskId);
  const events = eventsFor(taskId).slice().reverse();

  heading.textContent = `学习记录 · ${title}`;
  body.innerHTML = renderRecordBody(taskId, done, fb, events, options, meta);
  revealReaderModal({ initialFocus: "#recordNote", focus: options.focus !== false });
  updateTaskActionControls(taskId);

  const saveBtn = body.querySelector("#recordSaveDone");
  const undoBtn = body.querySelector("#recordUndoDone");
  const action = async (undo) => {
    const noteInput = body.querySelector("#recordNote");
    const evidenceInput = body.querySelector("#recordEvidence");
    const note = noteInput?.value || "";
    const evidencePath = evidenceInput?.value || "";
    if (!undo && (options.requireNote || !done) && !note.trim()) {
      showRecordTransactionError(taskId, "请先写一条本次记录，再保存完成");
      noteInput?.focus();
      return;
    }
    if (Array.from(note).length > TASK_ACTION_TEXT_CODEPOINT_LIMIT) {
      showRecordTransactionError(taskId, `本次记录不能超过 ${TASK_ACTION_TEXT_CODEPOINT_LIMIT} 个字符`);
      return;
    }
    if (Array.from(evidencePath).length > TASK_ACTION_TEXT_CODEPOINT_LIMIT) {
      showRecordTransactionError(taskId, `证据路径不能超过 ${TASK_ACTION_TEXT_CODEPOINT_LIMIT} 个字符`);
      return;
    }
    const modalSession = modalReaderRequestId;
    const result = await postTaskAction(taskId, undo, { note, evidence_path: evidencePath });
    if (result?.applied && result.expectedState && modalSession === modalReaderRequestId && document.getElementById("readerModal")?.classList.contains("open")) {
      if (undo) closeMarkdownViewer();
      else openCompletionReceipt();
    }
  };
  if (saveBtn) saveBtn.addEventListener("click", () => action(false));
  if (undoBtn) undoBtn.addEventListener("click", () => action(true));
}

function recordPlaceholders(meta, taskId) {
  const lane = meta?.round?.lane || taskLane(taskId);
  const file = meta?.task?.file || "";
  if (lane === "linux-foundations") {
    const match = String(meta?.round?.id || "").match(/round_(\d{2})/);
    const roundPath = match ? `~/cli-lab/round${Number(match[1])}` : "~/cli-lab";
    return {
      note: "例如：读完本节 Linux 笔记，并在终端完成 1 个最小验证；下一步继续做本周练习。",
      evidence: `例如：${roundPath}/week1_auto`,
    };
  }
  return {
    note: "例如：读完当前 Linux 资料，整理一个最小结论，并写清下一步。",
    evidence: file ? `例如：${file}` : "例如：records/weekly_reviews/YYYY-WW.md",
  };
}

function exampleText(text) {
  return String(text || "").replace(/^例如：/, "");
}

function renderRecordBody(taskId, done, fb, events, options = {}, meta = null) {
  const placeholders = recordPlaceholders(meta, taskId);
  const eventsCanDisplay = learningResourceCanDisplay("events");
  const feedbackCanDisplay = learningResourceCanDisplay("feedback");
  const suggestion = feedbackCanDisplay ? (fb?.next_suggestion || "") : "";
  const noteRequired = options.requireNote || !done;
  const eventRows = !eventsCanDisplay
    ? `<li class='record-empty resource-error'>动作日志${escapeHtml(learningResourceStateLabel("events"))}，不能判断是否已有记录。</li>`
    : events.length
    ? events.slice(0, 12).map((event) => `
        <li>
          <div class="record-event-head"><strong>${escapeHtml(actionLabel(event.action_type))}</strong>${eventResultBadge(event)}</div>
          <span>${escapeHtml(event.timestamp || "")}</span>
          ${event.note ? `<p>${escapeHtml(event.note)}</p>` : ""}
          ${event.evidence_path ? `<code>${escapeHtml(event.evidence_path)}</code>` : ""}
          ${renderRecordedRunDetails(event)}
        </li>
      `).join("")
    : "<li class='record-empty'>还没有动作记录。</li>";
  const feedbackMessage = feedbackCanDisplay
    ? (fb?.message || "反馈文件已读取，本任务暂无反馈。")
    : `反馈${learningResourceStateLabel("feedback")}；这里不会显示成“暂无反馈”。`;

  return `
    <div class="record-panel" data-task="${escapeHtml(taskId)}" aria-busy="${taskMutationPending(taskId) ? "true" : "false"}">
      <div class="record-status ${done ? "done" : "open"}">${done ? "当前状态：已完成" : "当前状态：未完成"}</div>
      <p>${escapeHtml(feedbackMessage)}</p>
      ${learningResourceHasStaleData("feedback") ? '<p class="resource-inline-note">反馈同步失败；当前内容来自上次成功读取。</p>' : ""}
      ${suggestion ? `<p class="record-suggestion">${escapeHtml(suggestion)}</p>` : ""}
      <div class="record-template">
        <strong>记录参考</strong>
        <p>${escapeHtml(exampleText(placeholders.note))}</p>
        <p>证据示例：<code>${escapeHtml(exampleText(placeholders.evidence))}</code></p>
      </div>
      <label class="record-label" for="recordNote">本次记录${noteRequired ? "（必填）" : "（建议填写）"}</label>
      <textarea id="recordNote" class="record-input"${noteRequired ? ' required aria-required="true"' : ""} placeholder="${escapeHtml(placeholders.note)}"></textarea>
      <label class="record-label" for="recordEvidence">证据路径（可选）</label>
      <input id="recordEvidence" class="record-input" placeholder="${escapeHtml(placeholders.evidence)}" />
      <p class="record-transaction-status" role="status" aria-live="polite"></p>
      <div class="record-actions">
        <button type="button" class="task-btn done" id="recordSaveDone" data-task="${escapeHtml(taskId)}" data-idle-label="${done ? "保存记录并保持完成" : "记录并完成"}" aria-busy="false">${done ? "保存记录并保持完成" : "记录并完成"}</button>
        ${done ? `<button type="button" class="task-btn undo" id="recordUndoDone" data-task="${escapeHtml(taskId)}" data-idle-label="撤销完成" aria-busy="false">撤销完成</button>` : ""}
      </div>
      <h4>最近记录</h4>
      <ul class="record-list">${eventRows}</ul>
    </div>
  `;
}

function renderRecordedRunDetails(event) {
  const details = event?.action_type === "run_exercise" && event.details && typeof event.details === "object"
    ? event.details
    : null;
  if (!details) return "";
  const verification = details.completion_applied
    ? "本次运行当时提交了任务完成事实"
    : details.verification_reached
      ? "本次运行到达验证点，但没有改变任务完成状态"
      : "本次运行没有到达该任务的登记验证点";
  return `
    <details class="record-run-details">
      <summary>查看脚本回执</summary>
      <p>${escapeHtml(verification)}；这是历史动作事实，当前状态以上方任务状态为准。</p>
      <div class="run-meta"><span>返回码：<code>${escapeHtml(details.returncode ?? "—")}</code></span><span>耗时：<code>${escapeHtml(details.duration_ms ?? 0)}ms</code></span></div>
      <pre class="run-output"><code>${escapeHtml(details.stdout_excerpt || "（无标准输出）")}</code></pre>
      ${details.stderr_excerpt ? `<pre class="run-output terminal-stderr"><code>${escapeHtml(details.stderr_excerpt)}</code></pre>` : ""}
    </details>
  `;
}

function actionLabel(actionType) {
  if (actionType === "mark_done") return "记录并完成";
  if (actionType === "undo_done") return "撤销完成";
  if (actionType === "run_exercise") return "运行脚本";
  return actionType || "动作";
}

function setReaderModalVariant(variant = "") {
  const panel = document.querySelector("#readerModal .modal-panel");
  if (!panel) return;
  panel.classList.remove("feedback-modal", "feedback-success", "feedback-error");
  if (variant) panel.classList.add("feedback-modal", variant);
}

function openExecutionResult(title, execution, taskId = "", options = {}) {
  modalReaderRequestId += 1;
  const modal = document.getElementById("readerModal");
  const body = document.getElementById("readerBody");
  const heading = document.getElementById("readerTitle");
  if (!modal || !body || !heading) return;
  const success = execution.result === "ok";
  setReaderModalVariant(success ? "feedback-success" : "feedback-error");
  heading.textContent = success ? `练习执行回执 · ${title}` : `练习需要处理 · ${title}`;
  body.innerHTML = renderExecutionResult(execution, taskId, options);
  revealReaderModal();
  body.querySelector("#executionClose")?.addEventListener("click", () => {
    const completedCheckpoint = learningCheckpointById(options.completedCheckpointId);
    closeMarkdownViewer();
    if (completedCheckpoint) {
      activeCheckpointId = completedCheckpoint.id;
      render();
      showView("completion", { updateHash: true, scroll: true, animate: true, focus: true });
    }
  });
  body.querySelector("#executionRetry")?.addEventListener("click", () => {
    closeMarkdownViewer();
    void postTaskRun(taskId);
  });
  body.querySelector("#executionRecord")?.addEventListener("click", () => openRecordViewer(taskId, { requireNote: true }));
}

function renderExecutionResult(execution, taskId = "", options = {}) {
  const result = execution.result || "unknown";
  const statusText = {
    ok: "脚本正常结束",
    failed: "脚本返回非零状态",
    timeout: "运行超时，已停止",
    request_error: "运行请求未完成",
  }[result] || "运行结果未知";
  const success = result === "ok";
  const stdout = execution.stdout || "";
  const stderr = execution.stderr || "";
  const completedCheckpoint = learningCheckpointById(options.completedCheckpointId);
  const stateCurrent = options.stateCurrent !== false;
  const verificationText = !stateCurrent
    ? "这是本次脚本运行的真实回执；页面已读取到更高版本的学习状态，因此当前完成状态以页面为准。"
    : execution.completion_applied
    ? "白名单脚本到达了本任务的登记验证点并整体成功；完成事实与本次动作已同版保存，但这不等同于 Mastery 或人工验收。"
    : success && execution.verification_reached
      ? "白名单脚本到达了本任务的登记验证点并整体成功；任务此前已经完成，本次只追加真实运行记录。"
      : success
        ? "脚本返回码为 0，但没有收到本任务的登记验证点，因此没有自动完成任务。"
        : execution.verification_reached
          ? "脚本曾到达本任务验证点，但整体未成功结束；本次不会自动完成任务。"
          : "以下是本次真实返回结果；失败或超时不会自动完成任务。";
  return `
    <div class="run-panel feedback-result">
      <div class="feedback-result-head">
        <div><span class="feedback-eyebrow">EXECUTION / ${success ? "FINISHED" : "NEEDS ATTENTION"}</span><h3>${escapeHtml(statusText)}</h3></div>
        <span class="feedback-mark" aria-hidden="true">${success ? "✓" : "!"}</span>
      </div>
      <p class="feedback-lead">${verificationText}</p>
      <div class="run-meta">
        <span>脚本：<code>${escapeHtml(execution.script_path || "")}</code></span>
        <span>沙盒：<code>${escapeHtml(execution.sandbox_path || "")}</code></span>
        <span>返回码：<code>${escapeHtml(execution.returncode ?? "—")}</code></span>
        <span>耗时：<code>${escapeHtml(execution.duration_ms || 0)}ms</code></span>
      </div>
      <h4>标准输出</h4>
      <pre class="run-output"><code>${escapeHtml(stdout || "（无输出）")}</code></pre>
      <h4>错误输出</h4>
      <pre class="run-output"><code>${escapeHtml(stderr || "（无错误输出）")}</code></pre>
      <p class="run-hint">${result === "request_error"
        ? "运行请求未完成，因此不能声称结果已写入动作记录；请恢复服务后重试。"
        : stateCurrent
          ? `运行结果、验证点事实和当前任务状态已按同一 revision 核对。没有自动完成时，可在同一任务旁点击“记录并完成”补充人工证据。${completedCheckpoint ? ` 返回后可核对${completedCheckpoint.name}检查点。` : ""}`
          : "运行结果本身可供核对，但它不覆盖页面当前的更高版本状态；如需确认记录是否仍在当前历史中，请查看复习页。"}</p>
      <div class="feedback-actions">
        <button class="task-btn read" type="button" id="executionClose">${completedCheckpoint ? "查看模块检查点" : "返回任务"}</button>
        ${!success && taskId ? '<button class="task-btn run" type="button" id="executionRetry">重新运行</button>' : ""}
        ${taskId ? `<button class="task-btn done" type="button" id="executionRecord">${isTaskDone(taskId) ? "追加学习记录" : "记录并完成"}</button>` : ""}
      </div>
    </div>
  `;
}

function openCompletionReceipt() {
  const receipt = completionReceipt;
  const body = document.getElementById("readerBody");
  const heading = document.getElementById("readerTitle");
  if (!receipt || !body || !heading) return;
  setReaderModalVariant("feedback-success");
  heading.textContent = receipt.savedExistingCompletion ? "学习记录已保存" : "任务完成回执";
  heading.setAttribute("tabindex", "-1");
  const feedbackText = receipt.feedback?.message || "完成状态与本次动作已经写入本地记录。";
  const receiptTime = receipt.actionAt || receipt.completedAt || "";
  const next = typeof findGlobalNextTask === "function" ? findGlobalNextTask() : null;
  const receiptCheckpoint = learningCheckpointById(receipt.checkpointId)
    || learningCheckpointForRound(taskMeta(receipt.taskId)?.round?.id);
  const checkpointComplete = !receipt.savedExistingCompletion
    && receiptCheckpoint
    && learningCheckpointStats(receiptCheckpoint).complete;
  body.innerHTML = `
    <div class="feedback-result completion-receipt">
      <div class="feedback-result-head">
        <div><span class="feedback-eyebrow">LEARNING RECORD / SAVED</span><h3>${receipt.savedExistingCompletion ? "本次记录已追加" : "本次学习已记录"}</h3></div>
        <span class="feedback-mark" aria-hidden="true">✓</span>
      </div>
      <p class="feedback-lead">${escapeHtml(receipt.title)}</p>
      <div class="receipt-note"><span>本次记录</span><strong>${escapeHtml(receipt.eventLoaded ? (receipt.note || "（本次未填写备注）") : "动作已保存；记录详情暂时无法重新读取")}</strong></div>
      ${receipt.evidencePath ? `<div class="receipt-evidence"><span>证据路径</span><code>${escapeHtml(receipt.evidencePath)}</code></div>` : ""}
      <div class="feedback-facts">
        <div><span>课程任务</span><strong>${receipt.doneCount} / ${receipt.total}</strong></div>
        <div><span>${receipt.actionAt ? "本次动作时间" : "任务完成时间"}</span><strong>${escapeHtml(receiptTime || "刚刚")}</strong></div>
      </div>
      <p class="receipt-feedback">${escapeHtml(feedbackText)}</p>
      <p class="feedback-honesty">这里只展示服务器已保存的完成状态、记录与证据，不推导未经系统验证的学习结论。</p>
      <div class="feedback-actions">
        <button class="task-btn record" type="button" id="receiptRecord">查看学习记录</button>
        <button class="task-btn done" type="button" id="receiptContinue">${checkpointComplete ? "查看模块检查点" : (next ? "继续下一任务" : "前往复习")}</button>
      </div>
    </div>
  `;
  revealReaderModal({ initialFocus: "#readerTitle" });
  body.querySelector("#receiptRecord")?.addEventListener("click", () => openRecordViewer(receipt.taskId));
  body.querySelector("#receiptContinue")?.addEventListener("click", advanceFromCompletionReceipt);
}

function advanceFromCompletionReceipt() {
  const finishedTaskId = completionReceipt?.taskId || "";
  const savedExistingCompletion = !!completionReceipt?.savedExistingCompletion;
  const checkpoint = learningCheckpointById(completionReceipt?.checkpointId)
    || learningCheckpointForRound(taskMeta(finishedTaskId)?.round?.id);
  const checkpointComplete = !savedExistingCompletion && checkpoint && learningCheckpointStats(checkpoint).complete;
  const nextTask = typeof findGlobalNextTask === "function" ? findGlobalNextTask() : null;
  closeMarkdownViewer();
  completionReceipt = null;
  if (workspaceTaskId === finishedTaskId) workspaceTaskId = "";
  if (inlineReaderTaskId === finishedTaskId) {
    inlineReaderTaskId = "";
    inlineReaderFile = "";
  }
  if (activeTerminalTaskId === finishedTaskId) activeTerminalTaskId = "";
  activeWorkspaceTab = "task";
  if (checkpointComplete) activeCheckpointId = checkpoint.id;
  if (!checkpointComplete && nextTask) {
    continueToLearningTask(nextTask.task.id);
    return;
  }
  render();
  showView(checkpointComplete ? "completion" : "review", { updateHash: true, scroll: true, animate: true, focus: true });
}

function revealReaderModal(options = {}) {
  const modal = document.getElementById("readerModal");
  if (!modal) return;
  if (!lastModalFocus?.isConnected || lastModalFocus.closest("#readerModal")) {
    const active = document.activeElement;
    lastModalFocus = active instanceof HTMLElement && !active.closest("#readerModal") ? active : null;
  }
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
  document.querySelector(".app-shell")?.setAttribute("inert", "");
  if (options.focus === false) return;
  const initial = options.initialFocus ? modal.querySelector(options.initialFocus) : null;
  const focusTarget = initial || document.getElementById("readerClose");
  focusTarget?.focus({ preventScroll: true });
  requestAnimationFrame(() => {
    if (!modal.contains(document.activeElement)) focusTarget?.focus({ preventScroll: true });
  });
}

function closeMarkdownViewer(options = {}) {
  const modal = document.getElementById("readerModal");
  if (!modal) return;
  if (!options.fromHistory && knowledgeModalHistoryEntry && history.state?.readerModal === "knowledge") {
    history.back();
    return;
  }
  modalReaderRequestId += 1;
  const visibleReceiptTarget = lastModalFocusTaskId
    ? [...document.querySelectorAll(`.task-receipt-open[data-task="${CSS.escape(lastModalFocusTaskId)}"]`)].find((element) => element instanceof HTMLElement && element.offsetParent !== null)
    : null;
  const visibleTaskTarget = visibleReceiptTarget || (lastModalFocusTaskId
    ? [...document.querySelectorAll(`[data-task="${CSS.escape(lastModalFocusTaskId)}"]`)].find((element) => element instanceof HTMLElement && element.offsetParent !== null)
    : null);
  const lastFocusIsVisible = lastModalFocus?.isConnected
    && lastModalFocus.offsetParent !== null
    && !lastModalFocus.closest("[hidden]")
    && !lastModalFocus.closest("#readerModal");
  const focusTarget = lastFocusIsVisible
    ? lastModalFocus
    : visibleTaskTarget || document.querySelector(".app-view:not([hidden])");
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
  document.querySelector(".app-shell")?.removeAttribute("inert");
  knowledgeModalHistoryEntry = false;
  if (options.restoreFocus !== false && focusTarget) {
    if (!focusTarget.hasAttribute("tabindex") && !focusTarget.matches("button, a, input, textarea, select, summary")) {
      focusTarget.setAttribute("tabindex", "-1");
    }
    focusTarget.focus({ preventScroll: true });
    // Keep keyboard dismissal deterministic after the browser finishes
    // dispatching Escape and applies its own default focus behavior.
    requestAnimationFrame(() => {
      if (focusTarget.isConnected && !modal.classList.contains("open")) {
        focusTarget.focus({ preventScroll: true });
      }
    });
  }
  lastModalFocus = null;
  lastModalFocusTaskId = "";
}

function resolveReaderLink(href, baseFilePath) {
  const raw = String(href || "").trim();
  if (!raw || /^(?:[a-z][a-z0-9+.-]*:|#)/i.test(raw)) return "";
  const clean = raw.split("#")[0].split("?")[0];
  const baseDir = String(baseFilePath || "").split("/").slice(0, -1).join("/");
  const joined = clean.startsWith("/") ? clean.slice(1) : `${baseDir}/${clean}`;
  const parts = [];
  for (const part of joined.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") {
      parts.pop();
      continue;
    }
    parts.push(part);
  }
  const normalized = parts.join("/");
  return READABLE_FILE_RE.test(normalized) ? normalized : "";
}

function inlineMarkdown(text, baseFilePath = "") {
  return escapeHtml(text)
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (match, label, href) => {
      if (/^https?:\/\//i.test(href)) {
        return `<a href="${href}" target="_blank" rel="noreferrer noopener">${label}</a>`;
      }
      const file = resolveReaderLink(href, baseFilePath);
      if (!file) return match;
      return `<a href="${file}" class="inline-doc-link" data-file="${file}" data-title="${label}">${label}</a>`;
    })
    .replace(/&lt;(https?:\/\/[^&]+)&gt;/g, '<a href="$1" target="_blank" rel="noreferrer noopener">$1</a>')
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function renderMarkdown(src, baseFilePath = "") {
  const lines = String(src).replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let paragraph = [];
  let list = [];
  let orderedList = [];
  let table = [];
  let code = [];
  let inCode = false;
  let codeLang = "";

  const flushParagraph = () => {
    if (!paragraph.length) return;
    out.push(`<p>${inlineMarkdown(paragraph.join(" "), baseFilePath)}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (!list.length) return;
    out.push(`<ul>${list.map((item) => `<li>${inlineMarkdown(item, baseFilePath)}</li>`).join("")}</ul>`);
    list = [];
  };
  const flushOrderedList = () => {
    if (!orderedList.length) return;
    out.push(`<ol>${orderedList.map((item) => `<li>${inlineMarkdown(item, baseFilePath)}</li>`).join("")}</ol>`);
    orderedList = [];
  };
  const flushTable = () => {
    if (!table.length) return;
    const rows = table
      .filter((line) => !/^\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(line))
      .map((line) => line.replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim()));
    if (rows.length) {
      const head = rows[0];
      const body = rows.slice(1);
      out.push(`<div class="table-scroll"><table><thead><tr>${head.map((cell) => `<th>${inlineMarkdown(cell, baseFilePath)}</th>`).join("")}</tr></thead><tbody>${body.map((row) => `<tr>${row.map((cell) => `<td>${inlineMarkdown(cell, baseFilePath)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
    }
    table = [];
  };
  const flushBlocks = () => {
    flushParagraph();
    flushList();
    flushOrderedList();
    flushTable();
  };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const fence = line.match(/^```(\w+)?/);
    if (fence) {
      if (inCode) {
        out.push(`<pre><code class="language-${escapeHtml(codeLang)}">${escapeHtml(code.join("\n"))}</code></pre>`);
        code = [];
        codeLang = "";
        inCode = false;
      } else {
        flushBlocks();
        inCode = true;
        codeLang = fence[1] || "";
      }
      continue;
    }
    if (inCode) {
      code.push(rawLine);
      continue;
    }
    if (!line.trim()) {
      flushBlocks();
      continue;
    }
    if (/^\|.+\|$/.test(line)) {
      flushParagraph();
      flushList();
      table.push(line);
      continue;
    }
    flushTable();
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushOrderedList();
      flushList();
      const level = heading[1].length;
      out.push(`<h${level}>${inlineMarkdown(heading[2], baseFilePath)}</h${level}>`);
      continue;
    }
    if (/^---+$/.test(line.trim())) {
      flushBlocks();
      out.push("<hr>");
      continue;
    }
    if (line.startsWith("> ")) {
      flushBlocks();
      out.push(`<blockquote>${inlineMarkdown(line.slice(2), baseFilePath)}</blockquote>`);
      continue;
    }
    const bullet = line.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      flushOrderedList();
      list.push(bullet[1]);
      continue;
    }
    const ordered = line.match(/^\d+\.\s+(.+)$/);
    if (ordered) {
      flushParagraph();
      flushList();
      orderedList.push(ordered[1]);
      continue;
    }
    flushList();
    flushOrderedList();
    paragraph.push(line.trim());
  }
  if (inCode) {
    out.push(`<pre><code class="language-${escapeHtml(codeLang)}">${escapeHtml(code.join("\n"))}</code></pre>`);
  }
  flushBlocks();
  return `<div class="md-body">${out.join("\n")}</div>`;
}

function renderCodeDocument(src, filePath) {
  const ext = filePath.split(".").pop() || "";
  return `<div class="md-body code-doc"><p class="code-doc-note">这是练习脚本内容，可先在这里阅读步骤，再按任务要求练习。</p><pre><code class="language-${escapeHtml(ext)}">${escapeHtml(src)}</code></pre></div>`;
}

function bindReaderDocumentLinks(container, options = {}) {
  if (!container) return;
  container.querySelectorAll(".inline-doc-link").forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      const file = link.getAttribute("data-file") || "";
      if (!file) return;
      if (options.context === "knowledge") {
        const documentMeta = knowledgeDocumentByFile(file);
        if (!documentMeta) {
          showToast("该仓库链接不在当前手册的五份登记资料中", "warn");
          return;
        }
        void openMarkdownViewer(documentMeta.file, documentMeta.title, { context: "knowledge", history: false, documentId: documentMeta.id });
        return;
      }
      openInlineReader(file, link.getAttribute("data-title") || file, "", { silent: true });
      closeMarkdownViewer();
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupViewNavigation();
  setupWorkspaceTabs();
  setupKnowledge();
  setupSpatialInteraction();
  setupPhysicalButtons();
  const syncAppMotionPreference = () => {
    if (prefersReducedMotion()) cancelActiveAppViewTransition();
  };
  if (reducedMotionMedia?.addEventListener) reducedMotionMedia.addEventListener("change", syncAppMotionPreference);
  else reducedMotionMedia?.addListener?.(syncAppMotionPreference);
  window.addEventListener("resize", cancelActiveAppViewTransition, { passive: true });
  window.addEventListener("orientationchange", cancelActiveAppViewTransition, { passive: true });
  const closeBtn = document.getElementById("readerClose");
  const modal = document.getElementById("readerModal");
  const terminalInput = document.getElementById("terminalInput");
  const terminalRun = document.getElementById("terminalRun");
  const terminalClear = document.getElementById("terminalClear");
  const terminalReset = document.getElementById("terminalReset");
  const inlinePopout = document.getElementById("inlineReaderPopout");
  const settingsLink = document.querySelector('a[href="#secondaryTools"]');
  const secondaryTools = document.getElementById("secondaryTools");
  if (closeBtn) closeBtn.addEventListener("click", closeMarkdownViewer);
  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeMarkdownViewer();
    });
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && modal && modal.classList.contains("open")) {
      closeMarkdownViewer();
      return;
    }
    if (e.key === "Tab" && modal?.classList.contains("open")) {
      const focusable = [...modal.querySelectorAll('button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')]
        .filter((element) => element instanceof HTMLElement && element.offsetParent !== null);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  });
  if (terminalInput) {
    terminalInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        runTerminalCommand(terminalInput.value);
      }
    });
  }
  if (terminalRun && terminalInput) terminalRun.addEventListener("click", () => runTerminalCommand(terminalInput.value));
  if (terminalClear) terminalClear.addEventListener("click", () => runTerminalCommand("clear"));
  if (terminalReset) terminalReset.addEventListener("click", resetTerminal);
  if (inlinePopout) {
    inlinePopout.addEventListener("click", () => {
      const file = inlinePopout.dataset.file || inlineReaderFile;
      const title = inlinePopout.dataset.title || document.getElementById("inlineReaderTitle")?.textContent || "阅读";
      if (file) openMarkdownViewer(file, title);
    });
  }
  if (settingsLink && secondaryTools) {
    settingsLink.addEventListener("click", (e) => {
      e.preventDefault();
      secondaryTools.open = true;
      secondaryTools.scrollIntoView({ behavior: preferredScrollBehavior("smooth"), block: "start" });
    });
  }
});
