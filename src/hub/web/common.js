import {el,button} from './dom.mjs';
export {el,button};
let csrfToken = null;

export function parseHubRoute(hash) {
  const [path, query=''] = String(hash || '#projects').replace(/^#/, '').split('?');
  const parts=path.split('/').map(value=>{try{return decodeURIComponent(value);}catch{return '';}});
  return {kind:parts[0]||'projects',parts,query:new URLSearchParams(query)};
}

export function errorRecovery(error={}) {
  const code=error.code||'', status=error.status;
  if(['SESSION_REQUIRED','OWNER_AUTH_REQUIRED','CSRF_REJECTED'].includes(code)||status===401)
    return {title:'需要重新核对身份',message:'本次会话已失效或尚未认证。请连接所有者，再读取原对象。'};
  if(code==='OWNER_AUTH_UNAVAILABLE')return {title:'所有者身份尚未配置',message:'后台尚未配置受保护的所有者身份。当前来源仍可只读查看。'};
  if(status===403)return {title:'当前权限不足',message:'这项操作不在当前身份的授权范围内。请返回项目，核对允许的操作。'};
  if(status===404||['NOT_FOUND','PROJECT_SECTION_NOT_FOUND'].includes(code))return {title:'对象不存在或已移除',message:'这个项目或页面目前不可用。可返回项目列表重新查找。'};
  if(status===409)return {title:'版本发生冲突',message:'其他更新已先保存。当前输入保留，请重新读取后显式决定如何应用。'};
  if(code==='NETWORK_TIMEOUT')return {title:'等待后台超时',message:'读取可重试；提交结果需要按原请求核对，避免重复执行。'};
  if(status===0||code==='NETWORK_UNAVAILABLE')return {title:'后台连接中断',message:'请核对连接后重新读取。已保存草稿保留；提交结果需要按原请求核对。'};
  if(status===503)return {title:'这项服务暂不可用',message:'所需后台服务尚未配置或暂不可用。已保存内容保留，请核对连接信息。'};
  return {title:'后台读取失败',message:'后台未能完成这次读取。已保存内容保留，可重新读取并查看问题代码。'};
}

async function exchange(path, options) {
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),15000);
  try {
    const response=await fetch(path,{...options,signal:controller.signal});
    const payload=await response.json().catch(()=>null);
    if(controller.signal.aborted){const error=new Error('Request timeout');error.name='TimeoutError';throw error;}
    return {response,payload};
  } catch(cause) {
    if(controller.signal.aborted){const error=new Error('Request timeout',{cause});error.name='TimeoutError';throw error;}
    throw cause;
  } finally {clearTimeout(timer);}
}

export function connectionState(identity, error, wasOwner = false) {
  if (error) return {kind: error.code === 'NOT_FOUND' ? 'unavailable' : 'offline',
    label: error.code === 'NOT_FOUND' ? '连接信息待更新' : '后端暂时失联', owner: false};
  if (identity?.owner_authenticated) return {kind: 'owner', label: '所有者已连接', owner: true};
  if (wasOwner) return {kind: 'expired', label: '所有者会话已失效', owner: false};
  return {kind: 'guest', label: identity?.owner_configured ? '已连接 · 未认证' : '已连接 · 所有者身份未配置', owner: false};
}

export async function ownerLogin(proof) {
  const result = await api('/api/owner/login', {token: proof});
  if (typeof result.csrf_token !== 'string') throw new Error('INVALID_RESPONSE');
  csrfToken = result.csrf_token;
}

export async function ownerLogout() {
  await api('/api/owner/logout', {});
  csrfToken = null;
}

export function validRefreshCommand(value) {
  if (!value || typeof value.request_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(value.request_id)) return null;
  if (!Array.isArray(value.project_ids) || !value.project_ids.length ||
      value.project_ids.some((id) => typeof id !== "string" || !id) ||
      new Set(value.project_ids).size !== value.project_ids.length) return null;
  if (!Number.isInteger(value.expected_head?.sequence) || value.expected_head.sequence < 0 ||
      !/^[a-f0-9]{64}$/.test(value.expected_head.hash)) return null;
  return value;
}

async function bootstrap() {
  const {response,payload} = await exchange("/api/guest", {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
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
      error.code = cause?.name === 'TimeoutError' ? 'NETWORK_TIMEOUT' : 'NETWORK_UNAVAILABLE';
      error.retryable = true;
      error.outcome = body === undefined ? "NOT_COMMITTED" : "UNKNOWN";
      error.details = {};
      throw error;
    }
  }
  const send = async () => {
    const mutation = body !== undefined;
    let response, payload;
    try {
      ({response,payload} = await exchange(path, {
        method: mutation ? "POST" : "GET",
        credentials: "same-origin",
        headers: mutation
          ? { Accept: "application/json", "Content-Type": "application/json", "X-Hub-CSRF": csrfToken }
          : { Accept: "application/json" },
        body: mutation ? JSON.stringify(body) : undefined,
      }));
    } catch (cause) {
      const error = new Error("NETWORK_UNAVAILABLE", { cause });
      error.status = 0;
      error.code = cause?.name === 'TimeoutError' ? 'NETWORK_TIMEOUT' : 'NETWORK_UNAVAILABLE';
      error.retryable = true;
      error.outcome = mutation ? "UNKNOWN" : "NOT_COMMITTED";
      error.details = {};
      throw error;
    }
    if (!response.ok || !payload?.ok) throw apiError(response.status, payload, mutation);
    return payload.data;
  };
  try {
    return await send();
  } catch (error) {
    if (error.code !== "SESSION_REQUIRED" && error.code !== "CSRF_REJECTED") throw error;
    if (body !== undefined && error.outcome !== 'NOT_COMMITTED') throw error;
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
