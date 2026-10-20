import {el,button} from '/__rr/v1/resources/pch-dom';

const main=document.querySelector('#main'),notice=document.querySelector('#notice');
const labels={active:'进行中',paused:'已暂停',blocked:'受阻',complete:'已完成',unknown:'状态未提供',
  queued:'排队中',running:'执行中',checks_complete:'检查完成',failed:'失败',cancelled:'已取消',
  pending:'待阅读',agent_read:'Agent 已确认阅读',processed:'已记录处理结果'};
let manifest,context=null,logicalPath='/__rr/review/',generation=0,pending=null;
try{pending=JSON.parse(localStorage.getItem('pch:rr:pending')||'null');}catch{}
const text=v=>v===null||v===undefined?'来源未提供':Array.isArray(v)?v.join('、'):String(v);
const message=(s,error=false)=>{notice.textContent=s;notice.setAttribute('role',error?'alert':'status');};
async function request(path,options={}){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
  try{
    const response=await fetch(path,{...options,credentials:'omit',signal:controller.signal});
    const body=await response.json();if(!response.ok)throw Object.assign(new Error(body.code||'RR_UNAVAILABLE'),{status:response.status});
    return body;
  }finally{clearTimeout(timer);}
}
function showContext(envelope){
  context=envelope;document.querySelector('#rr-context').textContent=JSON.stringify({source:manifest.source,
    page_path:logicalPath,data_revision:envelope?.revision,observed_at:envelope?.observed_at},null,2);
}
const pairs=values=>el('dl',{className:'summary-grid'},...values.map(([name,v])=>el('div',{},el('dt',{},name),el('dd',{},text(v)))));
function card(p){
  return el('a',{href:'#projects/'+encodeURIComponent(p.project_id),className:'project-row','data-project-id':p.project_id},
    el('h2',{},p.name),el('p',{className:'caption'},labels[p.status]||p.status),
    el('p',{},text(p.definition.purpose.value)),
    el('p',{className:p.work.state==='current'?'caption':'warning'},p.work.state==='current'?'当前来源快照':p.work.state==='historical'?'历史快照 · 尚未更新':'尚无可用工作快照'));
}
async function directory(token){
  logicalPath='/__rr/review/';const result=await request('/__rr/v1/data/projects');if(token!==generation)return;showContext(result);
  const rows=result.data.projects, list=el('div',{className:'stack','aria-live':'polite'});
  const search=el('input',{type:'search',placeholder:'搜索项目名称或用途','aria-label':'搜索项目'});
  const status=el('select',{'aria-label':'按状态筛选'},el('option',{value:''},'全部状态'),
    ...['active','paused','blocked','complete','unknown'].map(s=>el('option',{value:s},labels[s])));
  function filter(){const q=search.value.trim().toLocaleLowerCase();const selected=rows.filter(p=>(!status.value||p.status===status.value)
    &&[p.name,p.project_id,text(p.definition.purpose.value)].some(v=>v.toLocaleLowerCase().includes(q)));
    list.replaceChildren(...(selected.length?selected.map(card):[el('p',{className:'empty'},rows.length?'没有匹配的项目。':'当前审核范围没有可展示项目。')]));}
  search.addEventListener('input',filter);status.addEventListener('change',filter);
  main.replaceChildren(el('h1',{},'项目审核'),el('p',{className:'caption'},`${result.data.total} 个项目 · PCH 已保存的实际快照`),
    el('div',{className:'filters'},el('label',{className:'field search'},'项目搜索',search),el('label',{className:'field'},'状态',status),button('重新读取',()=>route())),list,feedbackForm());filter();
}
async function detail(id,token){
  logicalPath='/__rr/review/projects/'+id;const result=await request('/__rr/v1/data/projects/'+encodeURIComponent(id));if(token!==generation)return;showContext(result);
  const p=result.data,work=p.work.fields,progress=p.work.progress;
  const panel=el('section',{className:'panel stack'},el('h2',{},'当前工作'),
    el('p',{className:p.work.state==='current'?'caption':'warning'},p.work.state==='current'?'当前来源快照':p.work.state==='historical'?'以下为历史快照，尚未核实为当前事实。':'来源尚未提供工作快照。'),
    pairs([['目标',work.objective],['阶段',work.phase],['轮次',work.round],['状态',labels[work.status]||work.status],
      ['下一步',work.next_action],['完成',work.completed===null?null:work.completed?'已完成':'尚未完成'],['本人接受',work.accepted===null?null:work.accepted?'已接受':'尚未接受'],
      ['进度',progress.total===null?null:`${progress.completed??'?'} / ${progress.total}`],['计数依据',progress.counting_basis],['快照时间',p.work.observed_at]]),
    el('h3',{},'阻塞与待决定'),p.work.blockers.length?el('ul',{},...p.work.blockers.map(b=>el('li',{},text(b.reason)+'；恢复条件：'+text(b.recovery_condition)))):el('p',{},p.work.blockers_available?'该快照未列出阻塞。':'来源未提供阻塞信息。'));
  const taskPanel=el('section',{className:'panel stack','aria-live':'polite'},el('h2',{},'任务记录'),el('p',{},'正在读取任务…'));
  main.replaceChildren(el('a',{href:'#projects'},'返回项目'),el('h1',{},p.name),
    el('section',{className:'panel stack'},el('h2',{},'这是什么项目'),pairs([['用途',p.definition.purpose.value],['主要产出',p.definition.outputs.value],['已确认别名',p.definition.aliases.value]])),panel,taskPanel,feedbackForm());
  try{const tasks=await request('/__rr/v1/data/projects/'+encodeURIComponent(id)+'/tasks');if(token!==generation)return;
    taskPanel.replaceChildren(el('h2',{},'任务记录'),el('p',{className:'caption'},'实际 TaskStore · 最近至多 20 条；结果正文与管理操作不在此审核页展示。'),
      ...(tasks.data.tasks.length?tasks.data.tasks.map(t=>el('article',{className:'project-row'},el('h3',{},labels[t.status]||t.status),
        pairs([['任务',t.id],['类型',t.kind],['创建时间',t.created],['结果',t.result_available?'已有结果记录':'尚无结果记录'],['本人接受',t.human_acceptance==='pending'?'待本人审核':t.human_acceptance]]),
        el('details',{},el('summary',{},'任务版本'),el('pre',{},t.result_version||'尚无结果版本')))):[el('p',{className:'empty'},'此项目尚无任务记录。')]));
  }catch{if(token!==generation)return;taskPanel.replaceChildren(el('h2',{},'任务记录暂不可用'),button('重新读取',()=>route()));}
}
function savePending(value){pending=value;try{if(value)localStorage.setItem('pch:rr:pending',JSON.stringify(value));else localStorage.removeItem('pch:rr:pending');}catch{message('浏览器未能保存恢复记录，请保持当前页面打开。',true);}}
async function recover(){
  if(!pending){message('暂无待核对请求。');return;}
  const result=await request('/__rr/v1/feedback/by-request/'+pending.client_request_id);
  savePending({...pending,receipt:result.receipt});message(`反馈 ${result.receipt.feedback_id} · ${labels[result.state]||result.state}`);return result;
}
function feedbackForm(){
  const form=el('form',{className:'panel stack','aria-label':'提交审核反馈'});
  const fields={};for(const [key,label]of[['description','问题描述'],['expected','期望结果'],['actual','实际结果'],['steps','复现步骤']]){
    fields[key]=el('textarea',{required:key==='description',maxLength:4000,'aria-label':label,value:pending&&!pending.receipt?pending[key]||'':''});form.append(el('label',{className:'field'},label,fields[key]));}
  const files=el('input',{type:'file',multiple:true,accept:'image/png,image/jpeg,image/webp,text/plain','aria-label':'附件，至多两件'});
  const submit=el('button',{type:'submit',className:'primary'},'保存反馈');form.prepend(el('h2',{},'审核反馈'));
  form.append(el('label',{className:'field'},'附件（至多两件，各 1 MiB）',files),el('p',{className:'caption'},'提交前请检查文字与附件；页面不会自动抓取截图、历史或登录信息。'),submit,button('按原请求核对',()=>recover().catch(()=>message('尚未取得持久回执，请保留原请求和附件。',true))));
  form.addEventListener('submit',async event=>{event.preventDefault();submit.disabled=true;
    try{
      const chosen=[...files.files];if(chosen.length>2||chosen.some(f=>f.size>1048576))throw new Error('附件超出限额');
      if(pending&&!pending.receipt){try{await recover();return;}catch(error){if(error.status!==404)throw error;}}
      const attachments=[];for(let i=0;i<chosen.length;i++){const bytes=await chosen[i].arrayBuffer();const digest=await crypto.subtle.digest('SHA-256',bytes);
        attachments.push({attachment_id:'attachment-'+i,size:bytes.byteLength,sha256:[...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('')});}
      let document={schema:1,client_request_id:pending&&!pending.receipt?pending.client_request_id:crypto.randomUUID(),project_id:manifest.project_id,
        page_path:logicalPath,source:manifest.source,resource:manifest.resources.find(x=>x.id==='review-page'),
        ...Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,v.value])),submitted_at:pending&&!pending.receipt?pending.submitted_at:new Date().toISOString(),attachments,
        context:{data_revision:context.revision,observed_at:context.observed_at}};
      if(pending&&!pending.receipt){if(['description','expected','actual','steps'].some(k=>pending[k]!==document[k])||JSON.stringify(pending.attachments)!==JSON.stringify(attachments))throw new Error('原请求待核对，请恢复原正文并重新选择原附件。');document=pending;}
      const encoded=JSON.stringify(document);if(new TextEncoder().encode(encoded).byteLength>16384)throw new Error('反馈正文合计不能超过 16 KiB，请缩短文字。');
      savePending(document);const body=new FormData();body.append('feedback',encoded);chosen.forEach((f,i)=>body.append('attachment.'+i,f));
      const receipt=await request('/__rr/v1/feedback',{method:'POST',body});savePending({...document,receipt});
      message('反馈已持久保存：'+receipt.feedback_id);form.reset();
    }catch(error){if([400,413,415].includes(error.status))savePending(null);
      const failures={400:'反馈格式未通过，请检查文字和附件后重试。',413:'正文或附件超过限额，请缩短文字或减少附件。',415:'附件类型或内容不符合要求，请更换附件。',409:'原请求与已存载荷冲突，请按原请求核对。',503:'暂未取得持久回执，请保留原请求并核对保存结果。'};
      message(failures[error.status]||(error.name==='AbortError'?'响应超时，请先按原请求核对。':error.message||'反馈尚未取得回执，请先按原请求核对。'),true);}
    finally{submit.disabled=false;}
  });return form;
}
async function route(){
  const token=++generation;main.replaceChildren(el('p',{role:'status'},'正在读取项目…'));
  try{manifest??=await request('/__rr/v1/manifest');if(token!==generation)return;
    let viewPath=location.hash.slice(1);
    if(!viewPath){
      if(!manifest.page_paths.includes(location.pathname))throw new Error('RR_PAGE_NOT_ALLOWED');
      viewPath=location.pathname.slice('/__rr/review/'.length);
    }
    const parts=viewPath.split('/');
    if(parts[0]==='feedback'){logicalPath='/__rr/review/';const envelope=await request('/__rr/v1/data/projects');if(token!==generation)return;showContext(envelope);
      main.replaceChildren(el('h1',{},'反馈记录'),el('p',{},pending?'可按本浏览器保存的原请求查询实际回执。':'本浏览器尚无反馈恢复记录。'),button('核对原请求',()=>recover().catch(()=>message('未取得原请求回执。',true))),feedbackForm());}
    else if(parts[0]==='projects'&&parts[1])await detail(decodeURIComponent(parts[1]),token);else await directory(token);
    if(token===generation){main.focus({preventScroll:true});window.scrollTo(0,0);}
  }catch{if(token===generation){main.replaceChildren(el('h1',{},'项目暂不可用'),el('p',{},'未能读取项目快照。请核对审核连接后重试。'),button('重新读取',()=>route()));main.focus();}}
}
window.addEventListener('hashchange',()=>route());route();
