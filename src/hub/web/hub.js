import {el, button, api, notify, navigate, validRefreshCommand} from './common.js';
import {renderDesigns} from './designs.js';

const main = document.querySelector('#main');
const statuses = {active:'进行中',paused:'已暂停',blocked:'受阻',complete:'已完成',unknown:'来源未提供状态'};
const types = {internal_control_plane:'管理',governance_program:'治理',code:'开发',creative:'创作',document:'文档'};
let generation=0, lastProjects=[], pendingRefresh=null;
const projectNames=new Map();
try {pendingRefresh=validRefreshCommand(JSON.parse(localStorage.getItem('hub:refresh-pending')||'null'));} catch {}
function time(value){if(!value)return '尚未读取';const d=new Date(value);return Number.isNaN(d.valueOf())?'时间未提供':d.toLocaleString('zh-CN',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});}
function human(value){if(value===null||value===undefined)return '来源未提供';if(typeof value==='string')return value;return JSON.stringify(value,null,2);}
function attention(p){return !p.declared.hub_connection_exception&&(p.freshness.state!=='fresh'||p.errors.length>0);}
function freshness(p){if(p.declared.hub_connection_exception)return '已登记例外';return p.freshness.authority_drift?'来源规则变化 · 需重新核对':p.freshness.state==='stale'?'上次快照 · 尚未更新':p.freshness.state==='fresh'?'已更新':'尚无成功快照';}
function refreshable(p){return p.declared.enabled&&p.declared.summary_enabled&&!p.declared.hub_connection_exception;}
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
async function projectsPage(token){
  const data=await api('/api/projects');if(token!==generation)return;lastProjects=data.projects;
  for(const project of lastProjects)projectNames.set(project.project_id,project.name);
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
  }
  // Single detail supplies the exact ledger head used by its refresh command.
  lastProjects=[p];
  const fields=el('dl',{},...['raw_status','normalized_status','next_action','blockers'].map(key=>el('div',{},el('dt',{},({raw_status:'来源原始状态',normalized_status:'统一状态',next_action:'完整下一步',blockers:'阻塞'}[key])),el('dd',{},key==='normalized_status'?statuses[p.business[key]]:human(p.business[key])))));
  const sources=el('section',{className:'panel stack'},el('h2',{},'状态来源'),el('p',{className:'caption'},`最近观察：${time(p.source.observed_at)}`),el('p',{className:attention(p)?'warning':'success'},freshness(p)));
  const operationalNotice=operationalSourceNotice(p);if(operationalNotice)sources.append(operationalNotice);
  for(const path of p.source.entrypoints||[]){const copy=button('复制来源位置',async()=>{try{await navigator.clipboard.writeText(path);notify('来源位置已复制。','success');}catch{notify('无法访问剪贴板，可选中来源位置复制。','warning');}});sources.append(el('p',{},path),copy);}
  if(!p.source.entrypoints?.length)sources.append(el('p',{className:'muted'},p.declared.hub_connection_exception?'此项目已登记管理读取例外。':'来源未提供可打开的位置。'));
  const relations=el('section',{className:'panel stack'},el('h2',{},'项目关联'));
  if(!p.relations.relations.length)relations.append(el('p',{className:'muted'},p.relations.status==='unknown'?'尚未确认关联。':'当前没有已登记的关联。'));
  for(const relation of p.relations.relations)relations.append(relationCard(relation,p.project_id,projectNames));
  const designCount=p.design.references.filter(x=>x.kind==='candidate').length;
  const design=el('section',{className:'panel stack'},el('h2',{},'界面设计'),el('p',{className:'muted'},designCount?`${designCount} 个设计版本可查看`:'尚未登记设计候选'),el('a',{className:'button',href:`#designs/${encodeURIComponent(id)}`},designCount?'进入设计审核':'查看设计入口'));
  const exception=p.declared.hub_connection_exception;
  main.replaceChildren(el('section',{className:'stack'},el('a',{href:'#projects'},'← 所有项目'),el('h1',{},p.name),el('p',{className:'muted'},sourceLine(p)),el('div',{className:'row'},refreshButton([p]),!refreshable(p)?el('p',{className:'caption'},'按当前读取范围停用刷新。'):null),exception?el('div',{className:'panel'},el('h2',{},'已授权的管理例外'),el('p',{},exception.reason||'已登记管理读取例外。')):null,el('section',{className:'panel stack'},el('h2',{},'当前工作'),fields),el('div',{className:'details-grid'},el('div',{className:'stack'},sources,relations),design),diagnostic({source:p.source,errors:p.errors,relations:p.relations,provenance:p.provenance,exception:p.declared.hub_connection_exception,unknown_fields:p.business.unknown_fields})));
}
async function route(focus=true){
  const token=++generation;const parts=location.hash.slice(1).split('/').map(p=>{try{return decodeURIComponent(p);}catch{return '';}});const kind=parts[0]||'projects';
  for(const name of ['projects','designs']){const nav=document.querySelector(`#nav-${name}`);if(name===kind)nav.setAttribute('aria-current','page');else nav.removeAttribute('aria-current');}
  main.setAttribute('aria-busy','true');main.replaceChildren(el('p',{role:'status'},'正在读取…'));
  try{if(kind==='designs'){const names=Object.fromEntries(lastProjects.map(p=>[p.project_id,p.name]));const view=el('div');main.replaceChildren(view);await renderDesigns(view,{projectId:parts[1]||null,candidateId:parts[2]||null,projectNames:names});}else if(kind==='projects'&&parts[1])await projectDetail(parts[1],token);else await projectsPage(token);if(focus&&token===generation)main.focus();}
  catch(error){if(token!==generation)return;main.replaceChildren(el('section',{className:'empty'},el('h1',{},'暂时无法读取'),el('p',{},failure(error)),button('重新读取',()=>route(false)),diagnostic({code:error.code||'NETWORK_UNAVAILABLE',outcome:error.outcome})));}
  finally{if(token===generation)main.removeAttribute('aria-busy');}
}
window.addEventListener('hashchange',()=>route());
document.querySelector('.skip').addEventListener('click',e=>{e.preventDefault();main.focus();});
route(false);
