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
