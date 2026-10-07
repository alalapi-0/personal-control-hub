import {el, button, api, notify, navigate, validRefreshCommand, connectionState, ownerLogin, ownerLogout} from './common.js';
import {renderDesigns} from './designs.js';
import {attention, freshness, refreshable, projectFacts, projectTasks} from './connection_view.mjs';

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
    try { await ownerLogin(transientProof); dialog.close(); await updateConnection(); }
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
  window.dispatchEvent(new CustomEvent('hub:identity',{detail:identity||{owner_authenticated:false}}));
  const control = state.owner ? button('断开所有者', async()=>{try{await ownerLogout();wasOwner=false;await updateConnection();}catch{notify('无法确认断开结果，请重新读取连接状态。','warning');}})
    : button('连接所有者', loginDialog, {disabled: !identity?.owner_configured});
  if (!identity?.owner_configured) control.title = '后台需要先配置受保护的所有者身份。';
  connection.replaceChildren(...[el('span', {className: state.kind==='offline'||state.kind==='expired'?'warning':'caption'}, state.label),
    identity?.data_classification==='synthetic_fixture'?el('span',{className:'warning'},'隔离测试数据'):null,
    el('span', {className:'caption'}, '执行入口尚未开放'), control,
    error ? button('重新核对',updateConnection) : el('details', {className:'connection-details'},
      el('summary', {}, '连接信息'), el('p', {}, '后台实例：'+identity.backend_instance),
      el('p', {}, '来源账本：'+(identity.ledger_head?.sequence ?? '暂不可用')),
      el('p', {}, '设计记录版本：'+(identity.design_revision ?? '暂不可用')))].filter(Boolean));
}
const statuses = {active:'进行中',paused:'已暂停',blocked:'受阻',complete:'已完成',unknown:'来源未提供状态'};
const types = {internal_control_plane:'管理',governance_program:'治理',code:'开发',creative:'创作',document:'文档'};
let generation=0, lastProjects=[], pendingRefresh=null, projectDirectoryLoaded=false;
const projectNames=new Map();
try {pendingRefresh=validRefreshCommand(JSON.parse(localStorage.getItem('hub:refresh-pending')||'null'));} catch {}
function time(value){if(!value)return '尚未读取';const d=new Date(value);return Number.isNaN(d.valueOf())?'时间未提供':d.toLocaleString('zh-CN',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});}
function human(value){if(value===null||value===undefined)return '来源未提供';if(typeof value==='string')return value;return JSON.stringify(value,null,2);}
function sourceLine(p){return `${types[p.declared.project_type]||'项目'} · ${time(p.source.observed_at)} · ${freshness(p)}`;}
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
function failure(error){const code=error.code||error.message;const map={REFRESH_AUTHORITY_UNAVAILABLE:'项目来源规则已变化，请先核对接入记录。原快照已保留。',REFRESH_HEAD_CONFLICT:'刷新期间其他更新已完成。请重新读取后再刷新。',SESSION_REQUIRED:'本地会话已失效，请重试。',REFRESH_REQUEST_CONFLICT:'该刷新请求与已保存的记录冲突，请核对原请求。'};return map[code]||'暂时无法读取或保存。已保留原有数据，请重试。';}
function savePending(value){pendingRefresh=value;try{if(value)localStorage.setItem('hub:refresh-pending',JSON.stringify(value));else localStorage.removeItem('hub:refresh-pending');}catch{notify('浏览器无法保存恢复记录，请保持当前页面打开。','warning');}}
async function refreshProjects(ids,control){
  if(!ids.length&&!pendingRefresh){notify('当前没有可读取的项目。');return;}
  const command=pendingRefresh||{request_id:crypto.randomUUID(),project_ids:ids,expected_head:lastProjects[0]?.provenance.ledger_head||{sequence:0,hash:'0'.repeat(64)}};
  if(!command.expected_head){notify('尚未取得来源版本，请重新读取列表。','warning');return;}
  savePending(command);control.disabled=true;const label=control.textContent;control.textContent='正在刷新…';
  notify(`正在读取 ${command.project_ids.length} 个项目的允许来源…`);
  try{
    const result=await api('/api/refresh',command);
    savePending(null);
    const rows=Object.values(result.projection?.projects||{}).filter(p=>p.latest_attempt?.request_id===command.request_id);
    const failed=rows.filter(p=>p.latest_attempt?.success===false).length;
    notify(failed?`刷新完成，${failed} 项未能更新，保留上次成功快照。`:`已完成 ${command.project_ids.length} 项刷新。`,failed?'warning':'success');
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
function refreshButton(projects){let b=button(pendingRefresh?'重试原刷新':projects.length===1?'刷新此项目':'刷新全部',()=>refreshProjects(projects.filter(refreshable).map(p=>p.project_id),b));b.disabled=!pendingRefresh&&!projects.some(refreshable);return b;}
function projectRow(p){const full=p.business.next_action||'下一步：来源未提供';const summary=full.length>140?full.slice(0,140)+'…（详情中查看完整下一步）':full;return el('a',{href:`#projects/${encodeURIComponent(p.project_id)}`,className:'project-row'},el('h2',{},p.name),el('p',{},statuses[p.business.normalized_status]||'状态未知'),el('p',{className:'next'},summary),el('p',{className:`caption ${attention(p)?'warning':''}`},sourceLine(p)));}
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
  const data=await api('/api/projects');if(token!==generation)return;lastProjects=data.projects;
  for(const project of lastProjects)projectNames.set(project.project_id,project.name);
  projectDirectoryLoaded=true;
  const list=el('div',{className:'project-list'}), count=el('p',{className:'muted'}), search=el('input',{type:'search',placeholder:'搜索项目…',maxLength:240,id:'search-projects'}), status=el('select',{id:'status-filter','aria-label':'项目状态'},el('option',{value:''},'全部状态'),...Object.entries(statuses).map(([value,label])=>el('option',{value},label)));
  const fresh=el('select',{id:'fresh-filter','aria-label':'来源筛选'},el('option',{value:''},'全部来源'),el('option',{value:'attention'},'需要关注'),el('option',{value:'fresh'},'已更新'));
  function filter(){const q=search.value.trim().toLocaleLowerCase();const rows=lastProjects.filter(p=>(!q||`${p.name} ${p.project_id}`.toLocaleLowerCase().includes(q))&&(!status.value||p.business.normalized_status===status.value)&&(!fresh.value||(fresh.value==='attention'?attention(p):p.freshness.state==='fresh')));count.textContent=`${rows.length} 个项目 · ${rows.filter(attention).length} 项来源需关注`;list.replaceChildren(...(rows.length?rows.map(projectRow):[el('div',{className:'empty'},el('h2',{},'没有匹配的项目'),el('p',{className:'muted'},'试试其他名称或清除筛选。'),button('清除筛选',()=>{search.value='';status.value='';fresh.value='';filter();}))]));}
  search.addEventListener('input',filter);status.addEventListener('change',filter);fresh.addEventListener('change',filter);
  main.replaceChildren(el('section',{className:'stack'},el('h1',{},'项目'),count,el('div',{className:'filters'},el('label',{className:'field search',htmlFor:'search-projects'},el('span',{className:'caption'},'搜索'),search),el('label',{className:'field',htmlFor:'status-filter'},el('span',{className:'caption'},'项目状态'),status),refreshButton(lastProjects)),el('div',{className:'row'},el('label',{className:'field',htmlFor:'fresh-filter'},el('span',{className:'caption'},'来源筛选'),fresh)),list));filter();
}
async function projectDetail(id,token){
  const p=await api(`/api/projects/${encodeURIComponent(id)}`);if(token!==generation)return;
  projectNames.set(p.project_id,p.name);
  const relatedIds=p.relations.relations.flatMap(relation=>relation.project_ids||[]);
  if(relatedIds.some(projectId=>!projectNames.has(projectId))){
    const directory=await api('/api/projects');if(token!==generation)return;
    for(const project of directory.projects)projectNames.set(project.project_id,project.name);
    projectDirectoryLoaded=true;
  }
  // Single detail supplies the exact ledger head used by its refresh command.
  lastProjects=[p];
  const fields=factsView(p),taskPanel=await projectTaskPanel(p,token);if(token!==generation)return;
  const sources=el('section',{className:'panel stack'},el('h2',{},'状态来源'),el('p',{className:'caption'},`最近观察：${time(p.source.observed_at)}`),el('p',{className:attention(p)?'warning':'success'},freshness(p)));
  const operationalNotice=operationalSourceNotice(p);if(operationalNotice)sources.append(operationalNotice);
  if(p.operational?.last_success && p.freshness.state!=='fresh' && p.declared.connection_read_allowed===true && p.freshness.local_presence!=='removed_local')sources.append(el('details',{},el('summary',{},'历史成功快照 · '+time(p.operational.last_success.observed_at)),el('p',{className:'warning'},'这是过去的项目记录，不能替代当前失败或未知。'),factsView(p,true)));
  for(const path of p.source.entrypoints||[]){const copy=button('复制来源位置',async()=>{try{await navigator.clipboard.writeText(path);notify('来源位置已复制。','success');}catch{notify('无法访问剪贴板，可选中来源位置复制。','warning');}});sources.append(el('p',{},path),copy);}
  if(!p.source.entrypoints?.length)sources.append(el('p',{className:'muted'},p.declared.hub_connection_exception?'此项目已登记管理读取例外。':'来源未提供可打开的位置。'));
  const relations=el('section',{className:'panel stack'},el('h2',{},'项目关联'));
  if(!p.relations.relations.length)relations.append(el('p',{className:'muted'},p.relations.status==='unknown'?'尚未确认关联。':'当前没有已登记的关联。'));
  for(const relation of p.relations.relations)relations.append(relationCard(relation,p.project_id,projectNames));
  const designCount=p.design.references.filter(x=>x.kind==='candidate').length;
  const design=el('section',{className:'panel stack'},el('h2',{},'界面设计'),el('p',{className:'muted'},designCount?`${designCount} 个设计版本可查看`:'尚未登记设计候选'),el('a',{className:'button',href:`#designs/${encodeURIComponent(id)}`},designCount?'进入设计审核':'查看设计入口'));
  const exception=p.declared.hub_connection_exception;
  main.replaceChildren(el('section',{className:'stack'},el('a',{href:'#projects'},'← 所有项目'),el('h1',{},p.name),el('p',{className:'muted'},sourceLine(p)),el('div',{className:'row'},refreshButton([p]),!refreshable(p)?el('p',{className:'caption'},'按当前读取范围停用刷新。'):null),exception?el('div',{className:'panel'},el('h2',{},'已授权的管理例外'),el('p',{},exception.reason||'已登记管理读取例外。')):null,el('section',{className:'panel stack'},el('h2',{},'项目权威状态'),el('p',{className:'caption'},'仅显示项目登记来源明确提供的事实。缺值和过期记录保留为未知。'),fields),taskPanel,el('a',{className:'button',href:'#host'},'查看 Linux 主机观测'),el('div',{className:'details-grid'},el('div',{className:'stack'},sources,relations),design),diagnostic({source:p.source,errors:p.errors,relations:p.relations,provenance:p.provenance,exception:p.declared.hub_connection_exception,unknown_fields:p.business.unknown_fields})));
}
async function route(focus=true){
  const token=++generation;const parts=location.hash.slice(1).split('/').map(p=>{try{return decodeURIComponent(p);}catch{return '';}});const kind=parts[0]||'projects';
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
        const directory=await api('/api/projects');if(token!==generation)return;
        for(const project of directory.projects)projectNames.set(project.project_id,project.name);
        projectDirectoryLoaded=true;
      }
      const names=Object.fromEntries(projectNames);
      if(parts[1]&&!names[parts[1]])names[parts[1]]='项目名称暂不可用';
      const view=el('div');main.replaceChildren(view);
      await renderDesigns(view,{projectId:parts[1]||null,candidateId:parts[2]||null,projectNames:names});
    }else if(kind==='projects'&&parts[1])await projectDetail(parts[1],token);
    else await projectsPage(token);
    if(focus&&token===generation)main.focus();
  }
  catch(error){if(token!==generation)return;main.replaceChildren(el('section',{className:'empty'},el('h1',{},'暂时无法读取'),el('p',{},failure(error)),button('重新读取',()=>route(false)),diagnostic({code:error.code||'NETWORK_UNAVAILABLE',outcome:error.outcome})));}
  finally{if(token===generation){main.removeAttribute('aria-busy');updateConnection();}}
}
window.addEventListener('hashchange',()=>route());
window.addEventListener('online',updateConnection);
window.addEventListener('offline',()=>{connectionGeneration++;connection.replaceChildren(el('span',{className:'warning'},'浏览器离线 · 已接收的后台任务需要重连后对账'),button('重新核对',updateConnection));});
document.querySelector('.skip').addEventListener('click',e=>{e.preventDefault();main.focus();});
route(false);
