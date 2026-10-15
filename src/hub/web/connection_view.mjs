function removed(project) {
  return project?.freshness?.local_presence === 'removed_local'
    || project?.freshness?.state === 'removed_local';
}

function latestFailure(project) {
  const latest = project?.operational?.latest_attempt;
  return (latest !== null && latest !== undefined && latest.success === false)
    || (Array.isArray(project?.errors) && project.errors.length > 0);
}

// One current-authority gate shared by detail facts, summaries and history toggles.
export function currentSourceAvailable(project) {
  const record=project?.business?.source_record;
  return !removed(project) && project?.declared?.connection_read_allowed===true
    && project?.business?.state==='current' && project?.freshness?.state==='fresh'
    && project?.freshness?.authority_drift!==true && !latestFailure(project)
    && project?.operational?.latest_attempt?.success===true
    && record?.schema_version==='2.0' && record.success===true;
}

export function attention(project) {
  if (removed(project)) return false;
  if (latestFailure(project)) return true;
  return project?.freshness?.state !== 'fresh' && !project?.declared?.hub_connection_exception;
}

export function freshness(project) {
  if (removed(project)) return '已从本地移除';
  if (latestFailure(project)) return '最新读取失败';
  if (project?.freshness?.authority_drift) return '来源规则变化 · 需重新核对';
  if (project?.freshness?.state === 'stale') return '上次快照 · 尚未更新';
  if (project?.freshness?.state === 'fresh') return '已更新';
  if (project?.declared?.hub_connection_exception) return '已登记例外';
  return '尚无成功快照';
}

export function refreshable(project) {
  return !removed(project)
    && project?.declared?.connection_read_allowed === true
    && project?.declared?.summary_enabled === true;
}

const factGroups = [
  ['当前工作', ['current_work.objective','工作目标'], ['current_work.phase','项目阶段'],
    ['current_work.round','当前轮次'], ['current_work.status','工作状态'],
    ['current_work.next_action','下一步'], ['current_work.owner_role','负责角色']],
  ['完成与用户决定', ['current_work.completed','工作完成'], ['current_work.accepted','用户接受'],
    ['blockers','阻塞与待决定']],
  ['项目进度', ['progress.completed','已完成数量'], ['progress.total','计数总量'],
    ['progress.counting_basis','计数依据'], ['progress.milestones','里程碑']],
  ['检查与验收依据', ['verification.status','检查状态'],
    ['verification.evidence_refs','检查证据'], ['verification.accepted_basis','验收依据']],
  ['交付', ['delivery.status','交付状态'], ['delivery.branch','分支'],
    ['delivery.commit','提交'], ['delivery.remote','目标位置'], ['delivery.receipt_ref','交付记录']],
];
const fieldValue = (business, path) => path.split('.').reduce((v, key) => v?.[key], business);

function factText(key, value) {
  if(key==='current_work.status')return ({active:'进行中',paused:'已暂停',blocked:'受阻',complete:'已完成',unknown:'来源标记未知'})[value] || String(value);
  if(key==='current_work.completed')return value ? '已完成' : '尚未完成';
  if(key==='current_work.accepted')return value ? '已接受' : '尚未接受';
  if(key==='delivery.status')return ({local:'仅在本地',pending_delivery:'待交付',delivered:'已交付',unknown:'来源标记未知'})[value] || String(value);
  if(key==='blockers')return value.length ? value.map(b=>`${b.waiting_for_user?'待用户决定：':''}${b.reason}\n恢复条件：${b.recovery_condition}`).join('\n\n') : '来源未列出阻塞';
  if(key==='progress.milestones')return value.length ? value.map(m=>`${m.label} · ${m.completed?'已完成':'尚未完成'} · ${m.accepted?'已接受':'尚未接受'}`).join('\n') : '来源未列出里程碑';
  return Array.isArray(value) ? (value.join('\n') || '来源未列出证据') : String(value);
}

// These are projections of validated authority records, never process/task/commit
// heuristics. A stale last-success is available only as explicitly dated history.
export function projectFacts(project, {history=false}={}) {
  const hidden=removed(project) || project?.declared?.connection_read_allowed!==true;
  const record=hidden ? null : history ? project?.operational?.last_success : project?.business?.source_record;
  const current=!history && currentSourceAvailable(project);
  const usable=!hidden && record?.schema_version==='2.0' && record.success===true && (history || current);
  return factGroups.map(([title,...specs])=>({title,fields:specs.map(([key,label])=>{
    const value=usable ? fieldValue(record.business,key) : null;
    const proof=usable ? record.field_provenance?.[key] : null;
    const source=record?.sources?.find(s=>s.id===proof?.source_ref && s.sha256===proof?.sha256);
    const known=value!==null && value!==undefined && !!proof && !!source;
    return {key,label,known,text:known?factText(key,value):'未知 · 来源未提供当前值',
      reason:known?null:(history ? record?.unknown_fields?.[key] : project?.business?.unknown_fields?.[key]) || record?.unknown_fields?.[key]
        || project?.freshness?.stale_reason || '当前来源或字段证据不可用。',
      provenance:known?{path:source.path,selector:proof.selector,sha256:proof.sha256,
        observed_at:record.observed_at}:null};
  })}));
}

export function projectTasks(tasks, projectId) {
  return Array.isArray(tasks) ? tasks.filter(task=>task.project===projectId) : [];
}

// Keep the daily summary on the same field-proof gate as the full detail.
export function projectSummary(project) {
  const facts=Object.fromEntries(projectFacts(project).flatMap(group=>group.fields.map(f=>[f.key,f])));
  const value=key=>facts[key].known ? facts[key].text : '未知';
  const current=currentSourceAvailable(project);
  const quality=latestFailure(project) ? 'error' : current ? project?.business?.quality?.integration_quality : 'partial';
  return {
    fields:[
      ['项目阶段',value('current_work.phase')], ['当前轮次',value('current_work.round')],
      ['当前目标',value('current_work.objective')], ['工作状态',value('current_work.status')],
      ['业务进度 V/N',`${value('progress.completed')} / ${value('progress.total')}`],
      ['计数依据',value('progress.counting_basis')],
      ['阻塞与待决定',value('blockers')], ['下一步',value('current_work.next_action')],
    ],
    quality:({complete:'日常查看信息完整',partial:'日常查看信息不完整',error:'当前来源读取失败'})[quality] || '可用程度未知',
    observedAt:project?.source?.observed_at ?? null,
    factAt:current && project?.freshness?.fact_time_state==='known' ? project.freshness.fact_updated_at : null,
  };
}

// Never publish a partial or mixed-version directory to the caller.
export async function loadProjectDirectory(request) {
  const projects=[],ids=new Set();let head=null,total=null,initialized=false,management,managementInitialized=false;
  const invalid=()=>{const error=new Error('PROJECT_DIRECTORY_INCONSISTENT');error.code=error.message;throw error;};
  const validHead=h=>h===null || (Number.isSafeInteger(h?.sequence) && h.sequence>=0 && /^[a-f0-9]{64}$/.test(h?.hash));
  const sameHead=(a,b)=>validHead(a) && validHead(b)
    && (a===null || b===null ? a===b : a.sequence===b.sequence && a.hash===b.hash);
  for(let page=0;page<1000;page++){
    const offset=projects.length;
    const data=await request(`/api/projects?offset=${offset}&limit=100`);
    if(!validHead(data?.head) || !Number.isSafeInteger(data.total) || data.total<0
      || data.total>100000 || data.offset!==offset || data.limit!==100 || !Array.isArray(data.projects))invalid();
    if(!initialized){head=data.head;total=data.total;initialized=true;}
    if(!sameHead(head,data.head) || total!==data.total || data.projects.length!==Math.min(100,total-offset))invalid();
    for(const project of data.projects){
      if(typeof project?.project_id!=='string' || !project.project_id || ids.has(project.project_id)
        || !sameHead(head,project?.provenance?.ledger_head))invalid();
      const value=project.management;
      let snapshot;
      if(value===undefined)snapshot=null; // Consistently absent legacy fixtures only.
      else {
        const hash=v=>typeof v==='string' && /^[a-f0-9]{64}$/.test(v);
        if(!value || typeof value!=='object' || Array.isArray(value) || typeof value.available!=='boolean'
          || (value.available ? !Number.isSafeInteger(value.revision) || value.revision<0
              || !hash(value.config_hash) || !hash(value.source_sha256)
            : value.revision!==null || value.config_hash!==null || value.source_sha256!==null))invalid();
        snapshot=[value.available,value.revision,value.config_hash,value.source_sha256];
      }
      if(!managementInitialized){management=snapshot;managementInitialized=true;}
      if(JSON.stringify(management)!==JSON.stringify(snapshot))invalid();
      ids.add(project.project_id);projects.push(project);
    }
    if(projects.length===total)return {projects,total,head};
  }
  invalid();
}

// Scope totals come only from the server's frozen scope, never a filter/page.
export function validateSyncStatus(directory, value) {
  const invalid=()=>{const error=new Error('SYNC_STATUS_INCONSISTENT');error.code=error.message;throw error;};
  const integer=v=>Number.isSafeInteger(v)&&v>=0;
  const head=h=>h===null || (integer(h?.sequence)&&/^[a-f0-9]{64}$/.test(h?.hash));
  const same=(a,b)=>head(a)&&head(b)&&(a===null||b===null?a===b:a.sequence===b.sequence&&a.hash===b.hash);
  if(!Array.isArray(directory?.projects))invalid();
  const ids=new Map(directory.projects.map(p=>[p?.project_id,p]));
  const counts=value?.counts;
  if(value?.schema_version!=='1.0'||typeof value.scope_id!=='string'||!value.scope_id
    ||!integer(value.scope_total)||!same(value.head,directory?.head)
    ||!integer(directory?.total)||directory.total!==ids.size||directory.projects.length!==ids.size
    ||!directory.projects.every(p=>typeof p?.project_id==='string'&&p.project_id.length>0&&same(directory.head,p?.provenance?.ledger_head))
    ||!counts||!['registered','scope','eligible','excluded','ineligible','complete','partial','failed','stale','unread'].every(k=>integer(counts[k]))
    ||counts.registered!==directory.total||counts.scope!==value.scope_total
    ||counts.eligible>counts.scope||counts.ineligible!==counts.scope-counts.eligible
    ||['complete','partial','failed','stale','unread'].reduce((sum,k)=>sum+counts[k],0)!==counts.scope
    ||!Array.isArray(value.eligible_project_ids)||value.eligible_project_ids.length!==counts.eligible
    ||new Set(value.eligible_project_ids).size!==counts.eligible
    ||!value.eligible_project_ids.every(id=>typeof id==='string'&&ids.has(id)&&refreshable(ids.get(id)))
    ||!Array.isArray(value.excluded)||value.excluded.length!==counts.excluded
    ||new Set(value.excluded.map(row=>row?.project_id)).size!==counts.excluded
    ||!value.excluded.every(row=>typeof row?.project_id==='string'&&row.project_id.length>0&&!value.eligible_project_ids.includes(row.project_id)
      &&typeof row.reason==='string'&&row.reason.length>0)
    ||value.discovery?.mode!=='registered_scope_only'||value.discovery.registered_candidates!==value.scope_total
    ||value.discovery.unregistered_count!==null
    ||!value.failure_codes||typeof value.failure_codes!=='object'||Array.isArray(value.failure_codes)
    ||!Object.entries(value.failure_codes).every(([code,count])=>code.length>0&&integer(count))
    ||!value.core_field_gaps||typeof value.core_field_gaps!=='object'||Array.isArray(value.core_field_gaps)
    ||!Object.entries(value.core_field_gaps).every(([field,count])=>field.length>0&&integer(count)))invalid();
  if(value.last_request!==null){
    const request=value.last_request;
    if(typeof request?.request_id!=='string'||!request.request_id||typeof request.status!=='string'||!request.status
      ||!['completed','remaining','failed'].every(k=>integer(request[k]))||request.failed>request.completed||typeof request.authority_drift!=='boolean')invalid();
  }
  if(value.projects!==undefined){
    const rows=value.projects,r=value.reconciliation;
    if(!Array.isArray(rows)||rows.length!==counts.scope||new Set(rows.map(p=>p?.project_id)).size!==counts.scope
      ||!r||!/^[a-f0-9]{64}$/.test(r.scope_sha256)||!/^[a-f0-9]{64}$/.test(r.registry_sha256)
      ||!Array.isArray(r.baseline_project_ids)||r.baseline_project_ids.length!==counts.scope
      ||new Set(r.baseline_project_ids).size!==counts.scope
      ||!rows.every(p=>typeof p.project_id==='string'&&r.baseline_project_ids.includes(p.project_id)
        &&['complete','partial','failed','stale','unread'].includes(p.category)
        &&p.eligible===value.eligible_project_ids.includes(p.project_id)&&Array.isArray(p.core_missing)
        &&p.core_missing.every(x=>typeof x==='string')&&Array.isArray(p.sources)
        &&p.sources.every(s=>typeof s.id==='string'&&/^[a-f0-9]{64}$/.test(s.sha256)))
      ||!['complete','partial','failed','stale','unread'].every(k=>rows.filter(p=>p.category===k).length===counts[k]))invalid();
  }
  return value;
}

export function scheduleSummary(schedule){
  if(!schedule?.available)return '自动刷新状态暂不可用；无法确认计时器、错过触发或补跑情况。';
  const timer=schedule.timer,service=schedule.service;
  const state=({active:'当前运行',paused:'当前暂停',unavailable:'当前状态不可用'})[schedule.status]||'当前状态未知';
  return `自动刷新：${state}；开机登录后启用状态：${timer.UnitFileState}；最近触发记录：${timer.LastTriggerUSec||'未知'}；下次计划：${timer.NextElapseUSecRealtime||'未知'}。最近服务结果：${service.Result}，退出码 ${service.ExecMainStatus}。日历真实触发、退出登录后恢复和错过补跑尚未验收；计划时间不代表已经执行。`;
}

export function requestSummary(request){
  if(!request)return '尚无记录';
  return `${request.request_id}；开始 ${request.started_at}；结束 ${request.finished_at||'尚未结束'}；已记录 ${request.completed}；失败 ${request.failed}；剩余 ${request.remaining_project_ids.length}。${request.authority_drift?'规则版本已变化，原请求不能直接续接。':''}`;
}

export async function loadSyncStatus(request, directory) {
  return validateSyncStatus(directory,await request('/api/sync-status'));
}

export function coreGapSummary(status){
  const labels=new Map(factGroups.flatMap(([, ...fields])=>fields));
  labels.set('current_work.phase|current_work.objective','项目阶段或目标');
  labels.set('current_work.next_action|blockers','下一步或阻塞说明');
  labels.set('source.current_observation','当前来源观察');
  labels.set('declared.connection_read_allowed','来源读取资格');
  return Object.entries(status.core_field_gaps).map(([field,count])=>`${labels.get(field)||'其他核心字段'} ${count} 项`).join(' · ');
}
