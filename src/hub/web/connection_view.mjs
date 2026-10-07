function removed(project) {
  return project?.freshness?.local_presence === 'removed_local'
    || project?.freshness?.state === 'removed_local';
}

function latestFailure(project) {
  const latest = project?.operational?.latest_attempt;
  return (latest !== null && latest !== undefined && latest.success === false)
    || (Array.isArray(project?.errors) && project.errors.length > 0);
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
  const current=!history && project?.business?.state==='current'
    && project?.freshness?.state==='fresh' && project?.operational?.latest_attempt?.success!==false;
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
