import {el, button, api, notify, navigate, validRefreshCommand, connectionState, ownerLogin, ownerLogout, parseHubRoute, errorRecovery} from './common.js';
import {renderDesigns} from './designs.js';
import {renderBusinessPlan} from './business_tasks.mjs';
import {renderManagement} from './management.mjs';
import {renderTimeline} from './timeline.mjs';
import {projectDirectory, renderDirectoryTree} from './directory.mjs';
import {renderPageInventory} from './page_inventory.mjs';
import {renderCaptures} from './captures.mjs';
import {attention, freshness, refreshable, currentSourceAvailable, projectDefinition, projectFacts, projectTasks, projectSummary, loadProjectDirectory, loadSyncStatus, coreGapSummary, scheduleSummary, requestSummary} from './connection_view.mjs';

const main = document.querySelector('#main');
const connection = document.querySelector('#connection');
let wasOwner = false, connectionGeneration = 0;

function loginDialog() {
  const proof = el('input', {type:'password', id:'owner-proof', minLength:32, maxLength:512,
    required:true, autocomplete:'off', spellcheck:false});
  const message = el('p', {role:'status','aria-live':'polite'});
  const dialog = el('dialog', {'aria-labelledby':'owner-title', className:'owner-dialog stack'});
  const submit = button('认证', async () => {
    if (!proof.reportValidity()) return;
    const transientProof = proof.value; proof.value = ''; submit.disabled = true;
    message.textContent = '正在认证…';
    try { await ownerLogin(transientProof); dialog.close(); await updateConnection();
      if(routeAuthRequired)await route(false); }
    catch(error) { message.textContent = ({OWNER_AUTH_FAILED:'身份未通过，请重新输入。',
      OWNER_AUTH_UNAVAILABLE:'所有者身份尚未配置，请在受保护的后台入口完成配置。',
      OWNER_AUTH_RATE_LIMITED:'认证尝试过多，请稍后再试。',SESSION_REQUIRED:'本次认证会话已失效，请重新打开认证。'})[error.code] || '后端暂时无法连接，请稍后重试。';
      if(error.code==='NETWORK_UNAVAILABLE') await updateConnection(); }
    finally { submit.disabled = false; if(dialog.open) proof.focus(); }
  }, {className:'primary'});
  proof.addEventListener('keydown',e=>{if(e.key==='Enter'&&!submit.disabled){e.preventDefault();submit.click();}});
  dialog.append(el('h2', {id:'owner-title'}, '连接所有者'),
    el('p', {className:'caption'}, '输入后台已配置的所有者凭据。此页面不保存凭据；项目执行还需要独立授权。'),
    el('label', {className:'field',htmlFor:'owner-proof'}, '所有者凭据', proof), message,
    el('div', {className:'row'}, submit, button('取消',()=>dialog.close())));
  dialog.addEventListener('close',()=>{proof.value='';dialog.remove();connection.querySelector('button:not(:disabled)')?.focus();});
  document.body.append(dialog); dialog.showModal(); proof.focus();
}

async function updateConnection() {
  const token = ++connectionGeneration; let identity, error;
  try { identity = await api('/api/identity'); } catch(e) { error = e; }
  if (token !== connectionGeneration) return;
  const state = connectionState(identity, error, wasOwner); wasOwner = state.owner;
  document.querySelector('section[aria-label="Hub 管理配置"]')?.setOwner?.(state.owner);
  window.dispatchEvent(new CustomEvent('hub:identity',{detail:identity||{owner_authenticated:false}}));
  const control = state.owner ? button('断开所有者', async()=>{try{await ownerLogout();wasOwner=false;await updateConnection();}catch{notify('无法确认断开结果，请重新读取连接状态。','warning');}})
    : button('连接所有者', loginDialog, {disabled: !identity?.owner_configured});
  if (!identity?.owner_configured) control.title = '后台需要先配置受保护的所有者身份。';
  const capabilities = identity?.capabilities || {};
  let taskLabel = '任务能力暂未核实';
  if (identity) {
    if (!identity.owner_configured) taskLabel = '只读 · 所有者身份尚未配置';
    else if (!state.owner) taskLabel = '只读 · 连接所有者后核对任务权限';
    else if (capabilities.ordinary_execution?.available) taskLabel = '任务通道可用 · 发送时核对授权范围';
    else if (capabilities.annotation_execution?.available) taskLabel = '图片任务通道可用 · 普通文本任务尚未开放';
    else if (!capabilities.worker_runtime?.available) taskLabel = '任务执行器尚未就绪';
    else if (!capabilities.provider?.available) taskLabel = '任务通道尚未就绪';
    else taskLabel = '已连接所有者 · 执行权限未启用';
  }
  connection.replaceChildren(...[el('span', {className: state.kind==='offline'||state.kind==='expired'?'warning':'caption'}, state.label),
    identity?.data_classification==='synthetic_fixture'?el('span',{className:'warning'},'隔离测试数据'):null,
    el('span', {className:'caption'}, taskLabel), control,
    error ? button('重新核对',updateConnection) : el('details', {className:'connection-details'},
      el('summary', {}, '连接信息'), el('p', {}, '后台实例：'+identity.backend_instance),
      el('p', {}, '来源账本：'+(identity.ledger_head?.sequence ?? '暂不可用')),
      el('p', {}, '设计记录版本：'+(identity.design_revision ?? '暂不可用')))].filter(Boolean));
  return {identity,error};
}
const statuses = {active:'进行中',paused:'已暂停',blocked:'受阻',complete:'已完成',unknown:'来源未提供状态'};
const types = {internal_control_plane:'管理',governance_program:'治理',code:'开发',creative:'创作',document:'文档'};
let generation=0, lastProjects=[], pendingRefresh=null, projectDirectoryLoaded=false, syncStatus=null, syncStatusError=null, routeAuthRequired=false;
const projectNames=new Map();
let lastDirectoryHash='#projects', stopDirectoryObservation=()=>{};
try {pendingRefresh=validRefreshCommand(JSON.parse(localStorage.getItem('hub:refresh-pending')||'null'));} catch {}
function time(value){if(!value)return '尚未读取';const d=new Date(value);return Number.isNaN(d.valueOf())?'时间未提供':d.toLocaleString('zh-CN',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});}
function human(value){if(value===null||value===undefined)return '来源未提供';if(typeof value==='string')return value;return JSON.stringify(value,null,2);}
function sourceLine(p){return `${types[p.declared.project_type]||'项目'} · ${freshness(p)}`;}
function dataTime(value){return value ? time(value) : '未知 · 来源未提供';}
function diagnostic(value,label='查看诊断信息'){return el('details',{className:'diagnostics'},el('summary',{},label),el('pre',{},JSON.stringify(value,null,2)));}
const relationKinds={pipeline:'流程衔接',shared_review_pattern:'相似审核方式',shared_visual_language:'相似视觉语言'};
function relationPoints(title,items){return el('div',{},el('h3',{},title),el('ul',{className:'source-list'},...(items||[]).map(item=>el('li',{},item))));}
function relationCard(relation,currentProject,names){
  const others=(relation.project_ids||[]).filter(id=>id!==currentProject);
  return el('article',{className:'stack'},
    el('div',{className:'row'},el('h3',{},relationKinds[relation.kind]||'关系类型待识别'),el('span',{className:'warning'},relation.status==='proposed'?'待确认提议':'状态未确认')),
    el('p',{className:'caption'},'这是一项有材料依据但尚未确认的关系提议，不代表共用实现、正式视觉家族或执行授权。'),
    relationPoints('可能衔接的工作',relation.shared_tasks),
    relationPoints('已知差异',relation.differences),
    relationPoints('明确不共享的边界',relation.not_shared),
    el('div',{className:'stack'},el('h3',{},'其他相关项目'),others.length
      ? el('div',{className:'row'},...others.map(id=>el('a',{className:'button',href:`#projects/${encodeURIComponent(id)}`},names.get(id)||id)))
      : el('p',{className:'muted'},'没有其他已登记项目。')),
  );
}
function operationalSourceNotice(project){
  const facts=project.operational?.facts||[],latest=project.operational?.latest_attempt;
  if(project.freshness.state!=='fresh'||latest?.success!==true||!facts.length||project.source.availability!=='unknown'||project.business.normalized_status!=='unknown'||project.business.next_action!==null)return null;
  const diagnostics=project.errors||[];
  return el('div',{className:'stack'},
    el('p',{className:'success'},`已成功读取 ${facts.length} 项运行资料，业务状态仍未知。`),
    el('p',{className:'caption'},'运行资料只说明系统运行情况，不提供项目业务进度或下一步。'),
    diagnostics.length?el('p',{className:'warning'},`另有 ${diagnostics.length} 条来源诊断；完整记录见页面底部诊断信息。`):null,
  );
}
function failure(error){const code=error.code||error.message;const map={SYNC_SCOPE_UNAVAILABLE:'固定刷新范围尚未配置或不可读取，请核对接入范围后再试。',SYNC_AUTHORITY_CHANGED:'来源规则在读取期间发生变化，请重新核对目录。',SYNC_HEAD_CHANGED:'其他刷新刚刚完成，请重新核对目录。',SYNC_STATUS_INCONSISTENT:'刷新范围或统计版本不一致，请重新读取完整目录后再刷新。',PROJECT_DIRECTORY_INCONSISTENT:'项目目录在读取期间发生变化或分页不完整。请重新读取完整目录。',REFRESH_AUTHORITY_UNAVAILABLE:'项目来源规则已变化，请先核对接入记录。原快照已保留。',REFRESH_HEAD_CONFLICT:'刷新期间其他更新已完成。请重新读取后再刷新。',SESSION_REQUIRED:'本地会话已失效，请重试。',REFRESH_REQUEST_CONFLICT:'该刷新请求与已保存的记录冲突，请核对原请求。'};return map[code]||errorRecovery(error).message;}
function savePending(value){pendingRefresh=value;try{if(value)localStorage.setItem('hub:refresh-pending',JSON.stringify(value));else localStorage.removeItem('hub:refresh-pending');}catch{notify('浏览器无法保存恢复记录，请保持当前页面打开。','warning');}}
async function refreshProjects(ids,control){
  if(!ids.length&&!pendingRefresh){notify('当前没有可读取的项目。');return;}
  if(!syncStatus||!(pendingRefresh?.project_ids||ids).every(id=>syncStatus.eligible_project_ids.includes(id))){notify('刷新范围暂不可用，请重新读取目录后再试。','warning');return;}
  const command=pendingRefresh||{request_id:crypto.randomUUID(),project_ids:ids,expected_head:syncStatus.head||{sequence:0,hash:'0'.repeat(64)}};
  if(!command.expected_head){notify('尚未取得来源版本，请重新读取列表。','warning');return;}
  if(!pendingRefresh)savePending(command);control.disabled=true;const label=control.textContent;control.textContent='正在刷新…';
  notify(`正在读取 ${command.project_ids.length} 个项目的允许来源…`);
  try{
    const result=await api('/api/refresh',command);
    const rows=Object.values(result.projection?.projects||{}).filter(p=>p.latest_attempt?.request_id===command.request_id);
    const failed=rows.filter(p=>p.latest_attempt?.success===false).length;
    const complete=rows.length===command.project_ids.length&&result.request?.status==='FINISHED';
    if(complete)savePending(null);
    notify(!complete?'刷新尚未覆盖原请求全部项目，原请求已保留，可继续重试。':failed?`刷新结束，${failed} 项读取失败；缺失值保留未知，已有成功记录保留为历史。`:`已完成 ${command.project_ids.length} 项刷新。`,!complete||failed?'warning':'success');
    await route(false);
  }catch(error){
    // Keep uncertain commands byte-for-byte. A known noncommit conflict is safe
    // to clear only after explicitly surfacing that the view must be reread.
    if(error.outcome==='NOT_COMMITTED'&&(!error.retryable||error.code==='REFRESH_HEAD_CONFLICT'))savePending(null);
    notify(failure(error)+(pendingRefresh?' 可点击重试原刷新。':''),'error');
    control.after(diagnostic({code:error.code,outcome:error.outcome,details:error.details},'查看刷新问题'));
    if(error.code==='REFRESH_HEAD_CONFLICT'&&error.outcome==='NOT_COMMITTED')await route(false);
  }finally{if(control.isConnected){control.disabled=false;control.textContent=label;}}
}
async function syncFeedback(directory,token){
  let status=null,error=null;
  try{status=await loadSyncStatus(api,directory);}catch(problem){error=problem;}
  if(token===generation){syncStatus=status;syncStatusError=error;}
}
function refreshButton(projects,full=false){
  const ids=full?(syncStatus?.eligible_project_ids||[]):projects.filter(p=>refreshable(p)&&syncStatus?.eligible_project_ids.includes(p.project_id)).map(p=>p.project_id);
  const label=pendingRefresh?'重试原刷新':full?`刷新全部符合资格项目（${syncStatus?.counts.eligible??'未知'} 项）`:'刷新此项目';
  const b=button(label,()=>refreshProjects(ids,b));
  b.disabled=!syncStatus||(!full&&ids.length!==1)||(pendingRefresh?!pendingRefresh.project_ids.every(id=>syncStatus.eligible_project_ids.includes(id)):ids.length===0);
  return b;
}
function syncOverview(){
  if(!syncStatus)return el('section',{className:'panel stack','aria-label':'刷新范围'},
    el('p',{role:'status',className:'warning'},'刷新范围与统计暂不可用。新的全量或单项目刷新已停用；项目来源仍可查看。'),
    el('p',{className:'caption'},syncStatusError?failure(syncStatusError):'等待核对完整目录。'),button('重新核对范围',()=>route(false)));
  const c=syncStatus.counts,last=syncStatus.last_request;
  return el('section',{className:'panel stack','aria-label':'刷新范围'},el('h2',{},'项目来源同步'),
    el('p',{},`固定范围 ${syncStatus.scope_total} 项 · 已登记 ${c.registered} 项 · 符合刷新资格 ${c.eligible} 项 · 排除条目 ${c.excluded} 项 · 固定范围内无资格 ${c.ineligible} 项`),
    el('p',{},`完整 ${c.complete} · 不完整 ${c.partial} · 读取失败 ${c.failed} · 过期 ${c.stale} · 尚未读取 ${c.unread}`),
    el('p',{className:'caption'},'统计覆盖固定范围全部项目，不随搜索或筛选变化。刷新只读取符合资格项目的允许来源，不启动 Agent、测试或 GPU 工作。未登记项目数量未知；此处只核对已登记范围。'),
    last?el('p',{className:last.failed?'warning':'caption'},`最近批次记录：已处理 ${last.completed} · 剩余 ${last.remaining} · 失败 ${last.failed}。${last.authority_drift?'来源规则已变化；':''}历史批次不代表当前来源已更新。`):el('p',{className:'caption'},'尚无刷新批次记录。'),
    pendingRefresh?el('p',{className:'warning'},pendingRefresh.project_ids.every(id=>syncStatus.eligible_project_ids.includes(id))?'已保存原刷新请求，可按原请求重试；不会扩大范围。':'已保存的原刷新请求包含当前无资格项目，重试已停用。原请求完整保留，请先核对资格。'):null,
    Object.keys(syncStatus.core_field_gaps).length?el('p',{className:'caption'},'当前字段缺口：'+coreGapSummary(syncStatus)):null,
    Object.keys(syncStatus.failure_codes).length?diagnostic(syncStatus.failure_codes,'查看批次失败原因'):null,
    c.excluded?el('details',{},el('summary',{},'查看排除项及原因'),el('ul',{},...syncStatus.excluded.map(row=>el('li',{},`${projectNames.get(row.project_id)||row.project_id}：${({missing_registration:'缺少当前登记',removed_local:'已从本地移除',removed_local_no_probe:'已从本地移除，禁止探测',disabled:'接入已停用',summary_disabled:'汇总已停用',connection_read_disabled:'当前来源读取未获允许',legacy_noncurrent_root:'历史入口，不属于当前范围'})[row.reason]||row.reason}`)))):null,
    el('details',{},el('summary',{},'自动刷新与请求恢复'),
      el('p',{className:'caption'},scheduleSummary(syncStatus.schedule)),
      el('p',{},'最近成功读取（不代表业务验收）：'+requestSummary(syncStatus.recovery?.last_success)),
      el('p',{},'最近失败读取：'+requestSummary(syncStatus.recovery?.last_failure)),
      ...(syncStatus.recovery?.open_requests||[]).map(r=>el('p',{className:'warning'},'未结束请求：'+requestSummary(r)+(r.can_resume?'可用原请求 ID 续接。':'请先核对权限和来源版本。')))),
    syncStatus.projects?el('details',{},el('summary',{},`查看固定范围逐项质量（${syncStatus.scope_total} 项）`),
      el('p',{className:'caption'},`范围版本 ${syncStatus.reconciliation.scope_sha256}；登记版本 ${syncStatus.reconciliation.registry_sha256}。登记缺失 ${syncStatus.reconciliation.missing_registration_ids.length} 项；范围外 ${syncStatus.reconciliation.outside_baseline_ids.length} 项。`),
      el('ul',{className:'source-list'},...syncStatus.projects.map(row=>el('li',{},
        el('a',{href:'#projects/'+encodeURIComponent(row.project_id)},row.name),
        el('p',{},`${({complete:'核心完整',partial:'核心不完整',failed:'读取失败',stale:'已过期',unread:'尚未读取'})[row.category]}；读取观察 ${dataTime(row.observed_at)}；最后成功 ${dataTime(row.last_success_at)}`),
        row.core_missing.length?el('p',{className:'caption'},'核心缺口：'+coreGapSummary({core_field_gaps:Object.fromEntries(row.core_missing.map(k=>[k,1]))})):null,
        el('p',{className:'caption'},'本次来源版本：'+(row.sources.map(s=>s.id+' '+s.sha256).join('；')||'无成功来源证据')))))):null,
    el('details',{},el('summary',{},'新增项目或迁移路径'),
      el('p',{},'先确认稳定项目身份、唯一状态路径和明确读取权限。非 Git 项目也可以登记；同一项目迁移路径沿用原 ID，旧账本历史保留。'),
      el('p',{className:'caption'},'在本机按接入文档准备单条登记候选，运行 python3 scripts/hub_connections.py register --help。默认只检查；确认登记版本后才显式应用。登记不会创建业务声明、自动运行代码或扩大当前固定范围；缺少声明会如实显示。')));
}
function summaryView(project,compact=false){
  const summary=projectSummary(project);
  const shorten=text=>compact && text.length>140 ? text.slice(0,140)+'…（详情中查看完整内容）' : text;
  return el('div',{className:'project-summary'},
    el('p',{className:attention(project)?'warning':'caption'},'来源与内容：'+summary.quality),
    el('dl',{className:'summary-grid'},...summary.fields.map(([label,text])=>el('div',{},
      el('dt',{},label),el('dd',{className:text==='未知'?'muted':''},shorten(text))))),
    el('p',{className:'caption'},'读取观察时间：'+dataTime(summary.observedAt)),
    el('p',{className:'caption'},'来源事实更新时间：'+dataTime(summary.factAt)));
}
function projectRow(p,reasons=[]){
  const summary=projectSummary(p),fields=Object.fromEntries(summary.fields),purpose=projectDefinition(p).find(f=>f.key==='purpose');
  return el('a',{href:`#projects/${encodeURIComponent(p.project_id)}`,className:'project-row','data-project-id':p.project_id},
    el('h2',{},p.name),el('p',{className:`caption ${attention(p)?'warning':''}`},sourceLine(p)),
    el('p',{},purpose.known?purpose.text:'用途尚未声明'),
    el('p',{className:'caption'},`当前：${fields['工作状态']} · 业务进度 ${fields['业务进度 V/N']}`),
    reasons.length?el('p',{className:'caption'},'匹配：'+reasons.map(r=>r.label+'「'+r.value+'」').join('、')):null);
}
function definitionView(project){
  return el('section',{className:'panel stack','aria-label':'项目定义'},el('h2',{},'这是什么项目'),
    el('dl',{className:'summary-grid'},...projectDefinition(project).map(field=>el('div',{},
      el('dt',{},field.label),el('dd',{className:field.known?'':'muted'},field.text),
      field.reason?el('p',{className:'caption'},field.reason):null,
      field.source?el('details',{},el('summary',{},field.label+'的依据'),
        el('p',{className:'caption'},field.source.kind==='registry'?'项目登记明确声明':'项目来源明确声明'),
        el('p',{className:'caption'},'观察时间：'+dataTime(field.source.observed_at))):null))));
}
function factsView(project,history=false){
  return el('div',{className:'stack'},...projectFacts(project,{history}).map((group,index)=>el(index<2?'section':'details',{className:'stack'},
    el(index<2?'h3':'summary',{},group.title),el('dl',{},...group.fields.map(f=>el('div',{},el('dt',{},f.label),
      el('dd',{className:f.known?'':'muted'},f.text),
      f.provenance?el('details',{},el('summary',{},f.label+'的来源'),
        el('p',{},f.provenance.path),el('p',{className:'caption'},'来源选择器：'+JSON.stringify(f.provenance.selector)),
        el('p',{className:'caption'},'观察时间：'+time(f.provenance.observed_at)),
        el('p',{className:'caption'},'来源指纹：'+f.provenance.sha256)):null))),
    group.fields.some(f=>f.reason)?el('details',{},el('summary',{},group.title+'的缺失原因'),
      el('dl',{},...group.fields.filter(f=>f.reason).map(f=>el('div',{},el('dt',{},f.label),el('dd',{},f.reason))))):null)));
}
async function projectTaskPanel(project,token){
  const panel=el('section',{className:'panel stack'},el('h2',{},'Hub 执行任务'),
    el('p',{className:'caption'},'任务执行和检查结果独立记录；不会自动更新项目完成、用户接受或交付。'));
  const reload=button('核对执行任务',async()=>{reload.disabled=true;try{const next=await projectTaskPanel(project,token);if(token===generation&&panel.isConnected){panel.replaceWith(next);next.querySelector('button')?.focus();}}catch{reload.disabled=false;panel.append(el('p',{role:'status'},'暂时无法核对，保留当前任务记录。'));}});
  panel.append(reload);
  if(project.declared.connection_read_allowed!==true || project.freshness.local_presence==='removed_local'){
    reload.disabled=true;panel.append(el('p',{className:'muted'},'此项目当前未开放读取。'));return panel;
  }
  try{
    const identity=await api('/api/identity');
    if(!identity.owner_authenticated){panel.append(el('p',{className:'muted'},'连接所有者后可核对该项目的执行任务。'));return panel;}
    const data=await api('/api/tasks');const tasks=projectTasks(data.tasks,project.project_id);
    panel.append(el('p',{className:'muted'},data.test_gate?'隔离测试记录；真实项目执行仍需独立授权。':'执行入口尚未开放。'));
    if(!tasks.length)panel.append(el('p',{className:'muted'},'尚无这个项目的已接收任务。'));
    else{
      const {taskLabels}=await import('./tasks.js');
      for(const task of tasks)panel.append(el('a',{className:'project-row',href:'#tasks/'+encodeURIComponent(task.id)},
        el('h3',{},taskLabels[task.status]||'任务状态未知'),el('p',{className:'caption'},task.id),
        el('p',{},'用户验收：待决定')));
    }
  }catch(error){panel.append(el('p',{role:'status',className:'warning'},error.code==='TASK_STORE_MISSING'?'尚无任务记录。':'执行任务暂不可读取；项目来源信息仍可查看。'));}
  return panel;
}
async function projectsPage(token){
  const data=await loadProjectDirectory(api);if(token!==generation)return;
  await syncFeedback(data,token);if(token!==generation)return;lastProjects=data.projects;
  projectNames.clear();
  for(const project of lastProjects)projectNames.set(project.project_id,project.name);
  projectDirectoryLoaded=true;
  const directoryProjects=data.projects;
  const content=el('div',{className:'stack','aria-label':'项目目录视图'});
  const count=el('p',{className:'muted',role:'status','aria-live':'polite'}), search=el('input',{type:'search',placeholder:'搜索项目…',maxLength:240,id:'search-projects'}), status=el('select',{id:'status-filter','aria-label':'项目状态'},el('option',{value:''},'全部状态'),...Object.entries(statuses).map(([value,label])=>el('option',{value},label)));
  const fresh=el('select',{id:'fresh-filter','aria-label':'来源筛选'},el('option',{value:''},'全部来源'),el('option',{value:'attention'},'需要关注'),el('option',{value:'fresh'},'已更新'));
  const group=el('select',{id:'group-projects'},...Object.entries({none:'不分组',status:'按项目状态',freshness:'按来源状态',type:'按项目类型'}).map(([value,label])=>el('option',{value},label)));
  const view=el('select',{id:'view-projects'},...Object.entries({list:'项目列表',tree:'业务任务树',timeline:'甘特与未排期',all:'列表与甘特'}).map(([value,label])=>el('option',{value},label)));
  const query=parseHubRoute(location.hash).query, saved=history.state?.directory;
  for(const [key,control] of Object.entries({q:search,status,freshness:fresh,group,view})){
    const value=query.get(key);if(value!==null&&(control===search||[...control.options].some(o=>o.value===value)))control.value=value;
  }
  let rememberTimer;
  function remember(){
    clearTimeout(rememberTimer);
    if(parseHubRoute(location.hash).kind!=='projects'||parseHubRoute(location.hash).parts[1])return;
    const active=document.activeElement;
    const focus=active?.id&&active.id!=='main'?{id:active.id}:active?.closest?.('[data-project-id]')?{project:active.closest('[data-project-id]').dataset.projectId}:history.state?.directory?.focus;
    const directory={key:lastDirectoryHash,x:scrollX,y:scrollY,focus,
      open:[...content.querySelectorAll('details[data-project-id][open]')].map(n=>n.dataset.projectId)};
    if(location.hash===lastDirectoryHash&&JSON.stringify(history.state?.directory)===JSON.stringify(directory))return;
    history.replaceState({...history.state,directory},'',lastDirectoryHash);
  }
  const scheduleRemember=()=>{clearTimeout(rememberTimer);rememberTimer=setTimeout(remember,150);};
  function clear(){search.value='';status.value='';fresh.value='';filter();search.focus();}
  function filter(persist=true){
    if(persist){
      const params=new URLSearchParams();
      for(const [key,value] of Object.entries({q:search.value,status:status.value,freshness:fresh.value,group:group.value,view:view.value}))
        if(value&&!(key==='group'&&value==='none')&&!(key==='view'&&value==='list'))params.set(key,value);
      lastDirectoryHash='#projects'+(params.size?'?'+params:'');
      scheduleRemember();
    }else lastDirectoryHash=location.hash||'#projects';
    const projection=projectDirectory(directoryProjects,{query:search.value,status:status.value,freshness:fresh.value,groupBy:group.value});
    count.textContent=`${projection.matched} / ${projection.total} 个项目 · ${projection.rows.filter(attention).length} 项来源需关注`;
    if(!projection.total){content.replaceChildren(el('section',{className:'empty'},el('h2',{},'尚未登记项目'),el('p',{className:'muted'},'当前目录为空。可重新读取；登记入口和读取资格仍需按项目接入规则核对。'),button('重新读取目录',()=>route(false))));return;}
    if(!projection.matched){content.replaceChildren(el('section',{className:'empty'},el('h2',{},'没有匹配的项目'),el('p',{className:'muted'},'试试其他名称或清除筛选。'),button('清除筛选',clear)));return;}
    content.replaceChildren(...projection.groups.map(g=>{
      const section=el('section',{className:'stack directory-group','data-group':g.key});
      if(group.value!=='none')section.append(el('h2',{},`${g.label} · ${g.rows.length} 个项目`));
      if(view.value==='all'||view.value==='timeline')section.append(el('div',{className:'project-timeline'},renderTimeline(g.rows,{total:projection.total})));
      if(view.value==='all'||view.value==='list')section.append(el('div',{className:'project-list'},...g.rows.map(p=>projectRow(p,projection.matches.get(p.project_id)||[]))));
      if(view.value==='tree')section.append(renderDirectoryTree(g.rows));
      return section;
    }));
    const open=history.state?.directory?.open||[];
    for(const details of content.querySelectorAll('details[data-project-id]'))details.open=open.includes(details.dataset.projectId);
  }
  search.addEventListener('input',filter);
  for(const control of [status,fresh,group,view])control.addEventListener('change',filter);
  const field=(label,control)=>el('label',{className:'field',htmlFor:control.id},el('span',{className:'caption'},label),control);
  main.replaceChildren(el('section',{className:'stack'},el('h1',{},'项目'),count,el('div',{className:'filters'},el('label',{className:'field search',htmlFor:'search-projects'},el('span',{className:'caption'},'搜索'),search),field('项目状态',status),refreshButton(lastProjects,true)),el('div',{className:'directory-controls'},field('来源筛选',fresh),field('分组方式',group),field('显示方式',view)),el('details',{},el('summary',{},`来源同步与读取资格 · ${syncStatus?.counts.eligible??'未知'} 项可刷新`),syncOverview()),content));filter(false);
  // Persist the latest interaction once, and flush before an actual link leaves
  // the entry. Per-keystroke/focus/scroll writes can exhaust browser navigation
  // limits and silently disable otherwise valid links.
  const save=scheduleRemember, beforeClick=event=>{if(event.target.closest('a'))remember();};
  window.addEventListener('scroll',save,{passive:true});main.addEventListener('focusin',save);main.addEventListener('click',beforeClick,true);content.addEventListener('toggle',save,true);
  stopDirectoryObservation=()=>{clearTimeout(rememberTimer);window.removeEventListener('scroll',save);main.removeEventListener('focusin',save);main.removeEventListener('click',beforeClick,true);content.removeEventListener('toggle',save,true);};
  if(saved?.key===(location.hash||'#projects'))requestAnimationFrame(()=>{
    if(token!==generation)return;
    const focus=saved.focus?.id?document.getElementById(saved.focus.id):[...content.querySelectorAll('[data-project-id]')].find(n=>n.dataset.projectId===saved.focus?.project);
    focus?.focus({preventScroll:true});window.scrollTo(saved.x||0,saved.y||0);
  });
}

async function projectDetail(id,token,section='overview'){
  if(!['overview','tasks','schedule','sources'].includes(section)){const error=new Error('PROJECT_SECTION_NOT_FOUND');error.code='PROJECT_SECTION_NOT_FOUND';error.status=404;throw error;}
  const p=await api(`/api/projects/${encodeURIComponent(id)}`);if(token!==generation)return;
  projectNames.set(p.project_id,p.name);
  try{
    const scopeDirectory=await loadProjectDirectory(api);if(token!==generation)return;
    projectNames.clear();
    for(const project of scopeDirectory.projects)projectNames.set(project.project_id,project.name);
    projectDirectoryLoaded=true;
    await syncFeedback(scopeDirectory,token);
  }catch(error){if(token===generation){syncStatus=null;syncStatusError=error;}}
  if(token!==generation)return;
  if(syncStatus&&JSON.stringify(syncStatus.head)!==JSON.stringify(p.provenance.ledger_head)){syncStatus=null;syncStatusError={code:'SYNC_STATUS_INCONSISTENT'};}
  lastProjects=[p];
  const fields=factsView(p),taskPanel=section==='tasks'?await projectTaskPanel(p,token):null;if(token!==generation)return;
  let captures=null;if(section==='sources')try{captures=await api(`/api/projects/${encodeURIComponent(id)}/captures`);}catch{}
  if(token!==generation)return;
  const managed = p.management;
  let identity=null,identityError=null;
  try{identity=await api('/api/identity');}catch(error){identityError=error;}
  if(token!==generation)return;
  const timeView=el('div',{className:'project-timeline'},renderTimeline([p],{total:1,context:'project'}));
  const managementPanel=renderManagement(managed,{projectId:id,owner:identity?.owner_authenticated===true,
    save:command=>api('/api/management/project',command),
    reload:()=>api('/api/management?project_id='+encodeURIComponent(id)),
    onSaved:view=>{p.management=view;timeView.replaceChildren(renderTimeline([p],{total:1,context:'project'}));notify('Hub 管理配置已保存。','success');}});
  let identityNotice=null;
  if(identityError){
    const notice=el('section',{className:'panel stack','aria-label':'身份核对'},el('p',{role:'status',className:'warning'},'身份暂时无法核对；项目来源仍可查看，管理编辑已停用。'));
    const retry=button('重新核对身份',async()=>{retry.disabled=true;const next=await updateConnection();if(token!==generation||!notice.isConnected)return;if(next?.identity&&!next.error){managementPanel.setOwner?.(next.identity.owner_authenticated===true);notice.replaceChildren(el('p',{role:'status'},'身份已重新核对。'));}else retry.disabled=false;});
    notice.append(retry);identityNotice=notice;
  }
  const sources=el('section',{className:'panel stack'},el('h2',{},'状态来源'),el('p',{className:'caption'},`最近观察：${time(p.source.observed_at)}`),el('p',{className:attention(p)?'warning':'success'},freshness(p)));
  const operationalNotice=operationalSourceNotice(p);if(operationalNotice)sources.append(operationalNotice);
  if(p.operational?.last_success && !currentSourceAvailable(p) && p.declared.connection_read_allowed===true && p.freshness.local_presence!=='removed_local')sources.append(el('details',{},el('summary',{},'历史成功快照 · '+time(p.operational.last_success.observed_at)),el('p',{className:'warning'},'这是过去的项目记录，不能替代当前失败或未知。'),factsView(p,true),renderBusinessPlan(p,{history:true})));
  for(const path of p.source.entrypoints||[]){const copy=button('复制来源位置',async()=>{try{await navigator.clipboard.writeText(path);notify('来源位置已复制。','success');}catch{notify('无法访问剪贴板，可选中来源位置复制。','warning');}});sources.append(el('p',{},path),copy);}
  if(!p.source.entrypoints?.length)sources.append(el('p',{className:'muted'},p.declared.hub_connection_exception?'此项目已登记管理读取例外。':'来源未提供可打开的位置。'));
  const relations=el('section',{className:'panel stack'},el('h2',{},'项目关联'));
  if(!p.relations.relations.length)relations.append(el('p',{className:'muted'},p.relations.status==='unknown'?'尚未确认关联。':'当前没有已登记的关联。'));
  for(const relation of p.relations.relations)relations.append(relationCard(relation,p.project_id,projectNames));
  const designCount=p.design.references.filter(x=>x.kind==='candidate').length;
  const design=el('section',{className:'panel stack'},el('h2',{},'界面设计'),el('p',{className:'muted'},designCount?`${designCount} 个设计版本可查看`:'尚未登记设计候选'),el('a',{className:'button',href:`#designs/${encodeURIComponent(id)}`},designCount?'进入设计审核':'查看设计入口'));
  const exception=p.declared.hub_connection_exception;
  const base='#projects/'+encodeURIComponent(id),navigation=el('nav',{className:'row','aria-label':'项目内导航'},
    ...Object.entries({overview:'概览',tasks:'任务',schedule:'排期与关系',sources:'来源与产物'}).map(([key,label])=>
      el('a',{className:'button'+(key===section?' primary':''),href:base+(key==='overview'?'':'/'+key),
        'aria-current':key===section?'page':null},label)));
  let body;
  if(section==='overview')body=[definitionView(p),el('section',{className:'panel stack'},el('h2',{},'当前工作与下一步'),summaryView(p))];
  else if(section==='tasks')body=[taskPanel];
  else if(section==='schedule')body=[timeView,relations,managementPanel,renderBusinessPlan(p)];
  else body=[sources,el('section',{className:'panel stack'},el('h2',{},'全部来源字段'),fields),renderPageInventory(p),renderCaptures(p,captures),design,
    diagnostic({source:p.source,errors:p.errors,relations:p.relations,provenance:p.provenance,exception:p.declared.hub_connection_exception,unknown_fields:p.business.unknown_fields,quality:p.business.quality})];
  main.replaceChildren(el('section',{className:'stack'},el('a',{href:lastDirectoryHash},'← 所有项目'),el('h1',{},p.name),
    el('p',{className:'muted'},sourceLine(p)),el('div',{className:'row'},el('a',{className:'button primary',href:base+'/tasks'},'编写与查看任务'),refreshButton([p])),
    navigation,identityNotice,exception?el('div',{className:'panel'},el('h2',{},'已授权的管理例外'),el('p',{},exception.reason||'已登记管理读取例外。')):null,...body));
}
async function route(focus=true){
  stopDirectoryObservation();stopDirectoryObservation=()=>{};routeAuthRequired=false;
  const token=++generation,{parts,kind}=parseHubRoute(location.hash);
  for(const name of ['projects','designs','workbench','materials','host']){const nav=document.querySelector(`#nav-${name}`);if(name===(['tasks','sessions'].includes(kind)?'workbench':kind))nav.setAttribute('aria-current','page');else nav.removeAttribute('aria-current');}
  main.setAttribute('aria-busy','true');main.replaceChildren(el('p',{role:'status'},'正在读取…'));
  try{
    if(kind==='workbench'){
      const {renderWorkbench}=await import('./workbench.js');
      await renderWorkbench(main,{previewId:parts[1]||null,isCurrent:()=>token===generation});
    }else if(kind==='tasks'){
      const {renderTasks}=await import('./tasks.js');
      await renderTasks(main,{taskId:parts[1]||null,isCurrent:()=>token===generation});
    }else if(kind==='sessions'){
      const {renderSessions}=await import('./sessions.js');
      await renderSessions(main,{isCurrent:()=>token===generation});
    }else if(kind==='materials'){
      const {renderMaterials}=await import('./materials.js');
      await renderMaterials(main,{materialId:parts[1]||null,isCurrent:()=>token===generation});
    }else if(kind==='host'){
      const {renderHost}=await import('./host.js');
      const data=await api('/api/host');if(token!==generation)return;
      renderHost(main,data);
    }else if(kind==='designs'){
      if(!projectDirectoryLoaded){
        const directory=await loadProjectDirectory(api);if(token!==generation)return;
        projectNames.clear();
        for(const project of directory.projects)projectNames.set(project.project_id,project.name);
        projectDirectoryLoaded=true;
      }
      const names=Object.fromEntries(projectNames);
      if(parts[1]&&!names[parts[1]])names[parts[1]]='项目名称暂不可用';
      const view=el('div');main.replaceChildren(view);
      await renderDesigns(view,{projectId:parts[1]||null,candidateId:parts[2]||null,projectNames:names});
    }else if(kind==='projects'&&parts[1])await projectDetail(parts[1],token,parts[2]||'overview');
    else await projectsPage(token);
    if(focus&&token===generation)main.focus();
  }
  catch(error){if(token!==generation)return;routeAuthRequired=['OWNER_AUTH_REQUIRED','SESSION_REQUIRED'].includes(error.code);const recovery=errorRecovery(error);main.replaceChildren(el('section',{className:'empty'},el('h1',{},recovery.title),el('p',{},failure(error)),el('a',{className:'button',href:lastDirectoryHash},'返回项目列表'),button('重新读取',()=>route(false)),diagnostic({code:error.code||'NETWORK_UNAVAILABLE',outcome:error.outcome})));}
  finally{if(token===generation){main.removeAttribute('aria-busy');updateConnection();}}
}
window.addEventListener('hashchange',()=>route());
window.addEventListener('online',updateConnection);
window.addEventListener('offline',()=>{connectionGeneration++;connection.replaceChildren(el('span',{className:'warning'},'浏览器离线 · 已接收的后台任务需要重连后对账'),button('重新核对',updateConnection));});
document.querySelector('.skip').addEventListener('click',e=>{e.preventDefault();main.focus();});
route(false);
