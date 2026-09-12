let csrfToken = null;

export function validRefreshCommand(value) {
  if (!value || typeof value.request_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(value.request_id)) return null;
  if (!Array.isArray(value.project_ids) || !value.project_ids.length ||
      value.project_ids.some((id) => typeof id !== "string" || !id) ||
      new Set(value.project_ids).size !== value.project_ids.length) return null;
  if (!Number.isInteger(value.expected_head?.sequence) || value.expected_head.sequence < 0 ||
      !/^[a-f0-9]{64}$/.test(value.expected_head.hash)) return null;
  return value;
}

function append(parent, child) {
  if (child === null || child === undefined || child === false) return;
  if (Array.isArray(child)) {
    child.forEach((item) => append(parent, item));
    return;
  }
  parent.appendChild(
    typeof child === "string" || typeof child === "number"
      ? document.createTextNode(String(child))
      : child,
  );
}

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (/^on[A-Z]/.test(name) && typeof value === "function") {
      node.addEventListener(name.slice(2).toLowerCase(), value);
    } else if (name.startsWith("aria-") || name.startsWith("data-") || name === "role") {
      node.setAttribute(name, String(value));
    } else if (name in node) {
      node[name] = value;
    } else {
      node.setAttribute(name, String(value));
    }
  }
  children.forEach((child) => append(node, child));
  return node;
}

export function button(label, fn, attrs = {}) {
  return el("button", { type: "button", ...attrs, onClick: fn }, label);
}

async function bootstrap() {
  const response = await fetch("/api/session", {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.ok || typeof payload.data?.csrf_token !== "string") {
    throw apiError(response.status, payload, false);
  }
  csrfToken = payload.data.csrf_token;
}

function apiError(status, payload, mutation = false) {
  const details = payload?.error || {};
  const error = new Error(details.code || `HTTP_${status}`);
  error.status = status;
  error.code = details.code || "INVALID_RESPONSE";
  error.retryable = Boolean(details.retryable);
  error.outcome = details.outcome || (mutation ? "UNKNOWN" : "NOT_COMMITTED");
  error.details = details.details || {};
  return error;
}

export async function api(path, body) {
  if (!csrfToken) {
    try { await bootstrap(); }
    catch (cause) {
      if (cause?.code) throw cause;
      const error = new Error("NETWORK_UNAVAILABLE", { cause });
      error.status = 0;
      error.code = "NETWORK_UNAVAILABLE";
      error.retryable = true;
      error.outcome = body === undefined ? "NOT_COMMITTED" : "UNKNOWN";
      error.details = {};
      throw error;
    }
  }
  const send = async () => {
    const mutation = body !== undefined;
    let response;
    try {
      response = await fetch(path, {
        method: mutation ? "POST" : "GET",
        credentials: "same-origin",
        headers: mutation
          ? { Accept: "application/json", "Content-Type": "application/json", "X-Hub-CSRF": csrfToken }
          : { Accept: "application/json" },
        body: mutation ? JSON.stringify(body) : undefined,
      });
    } catch (cause) {
      const error = new Error("NETWORK_UNAVAILABLE", { cause });
      error.status = 0;
      error.code = "NETWORK_UNAVAILABLE";
      error.retryable = true;
      error.outcome = mutation ? "UNKNOWN" : "NOT_COMMITTED";
      error.details = {};
      throw error;
    }
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload?.ok) throw apiError(response.status, payload, mutation);
    return payload.data;
  };
  try {
    return await send();
  } catch (error) {
    if (error.code !== "SESSION_REQUIRED" && error.code !== "CSRF_REJECTED") throw error;
    csrfToken = null;
    await bootstrap();
    return send();
  }
}

export function notify(text, kind = "info") {
  const notice = document.querySelector("#notice");
  if (!notice) return;
  notice.textContent = String(text || "");
  notice.className = kind === "error" || kind === "warning" ? "warning" : kind === "success" ? "success" : "";
  notice.setAttribute("role", kind === "error" ? "alert" : "status");
  notice.setAttribute("aria-live", kind === "error" ? "assertive" : "polite");
}

export function navigate(hash) {
  location.hash = String(hash).startsWith("#") ? String(hash) : `#${hash}`;
}
