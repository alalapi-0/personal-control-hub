import {el,button,api,navigate} from './common.js';
import {renderBusinessPreview} from './business_preview.mjs';
import {renderOrdinaryTaskResult} from './ordinary_tasks.mjs';

export async function renderBusinessTaskPreview(target,task,{isCurrent=()=>true,expectedResultVersion=null,onVersionStale=()=>{}}={}){
  if(task.kind!=='story_exchange')return;
  const panel=el('section',{className:'panel stack','aria-label':'本次写作任务'},el('h2',{},'本次写作任务'));
  const specification=el('dl',{});
  for(const [key,label]of [['requirement','写作要求'],['scope','本次范围'],['acceptance','验收标准']]){
    const value=task.task_spec?.[key];
    specification.append(el('div',{},el('dt',{},label),el('dd',{},typeof value==='string'&&value.trim()?value:'尚未提供，需核对原任务。')));
  }
  panel.append(specification);target.append(panel);
  const message=el('p',{role:'status','aria-live':'polite'});panel.append(message);
  if(task.classification!=='real'){
    message.textContent='当前任务尚未核实为真实业务，不能展示为真实故事产出。';return;
  }
  if(task.status!=='checks_complete'||task.result?.source_promoted!==true){
    message.textContent=task.status==='cancelled'?'本次写作已取消；已有修改仍需核对，此处没有已核实并接入工作台的故事结果。'
      :['failed','lost','requires_reconcile'].includes(task.status)?'本次写作结果尚未核实；请查看失败或对账记录。'
      :task.status==='checks_complete'?'检查已结束，但故事结果尚未接入真实工作台。':'故事任务尚未完成检查与工作台接入。';
    return;
  }
  message.textContent='正在读取本次完整故事候选…';
  try{
    const preview=await api('/api/tasks/'+encodeURIComponent(task.id)+'/preview');
    if(!isCurrent())return;
    if(expectedResultVersion){
      const source=task.result?.source_binding,actual=preview?.binding;
      const expected=source&&{...source,output_sha256:task.result.output_sha256};
      if(!expected||!resultHash(expected.output_sha256)||!actual
        ||Object.keys(actual).length!==Object.keys(expected).length
        ||Object.keys(expected).some(key=>actual[key]!==expected[key])){
        message.textContent='无法核实预览属于此固定结果；没有展示其他版本的正文。';return false;
      }
      const checked=await api(taskReadPath(task.id,expectedResultVersion));
      if(!isCurrent())return false;
      if(checked.task?.result_version!==expectedResultVersion){onVersionStale();return false;}
    }
    panel.replaceChildren(el('h2',{},'本次写作任务'),specification,renderBusinessPreview(preview));
  }catch(error){
    if(!isCurrent())return;
    if(expectedResultVersion&&error.status===409&&error.code==='RESULT_VERSION_STALE'){onVersionStale();return false;}
    message.textContent='暂时无法核实完整故事候选。保留原任务，请重新对账；不会重新执行写作。';
    if(expectedResultVersion)return false;
  }
}

export const taskLabels={queued:'已接收 · 排队中',running:'Codex 执行中',waiting_input:'等待补充',waiting_approval:'等待审批',validating:'实际检查中',checks_complete:'检查完成 · 待用户验收',failed:'失败',cancelled:'已取消',lost:'执行器失联',requires_reconcile:'结果待对账'};
const lifecycleLabels={queued:'已接收 · 排队中',preparing:'正在核对执行条件',dispatching:'已准备发送 · 接收结果待确认',accepted:'Agent 已接收 · 尚未确认开始',running:'Agent 正在执行',waiting_input:'等待补充',waiting_approval:'等待审批',validating:'实际检查中',await_owner_review:'检查完成 · 等待验收',failed:'失败',cancelled:'已取消',lost:'执行器失联',requires_reconcile:'结果待对账',unknown:'状态待核对'};
export const lifecycleLabel=task=>lifecycleLabels[task.lifecycle?.stage]||taskLabels[task.status]||'状态未知';
const reviewLabels={pending:'待决定',accepted:'已接受此次结果',changes_requested:'需要修改',deferred:'暂缓验收'};
export function controlState(control,task,owner=true,now=Date.now()/1000){
  const labels={pending:control.kind==='input'?'等待补充输入':'等待审批',accepted:'仅本次已允许',answered:'已提交补充输入',declined:'已拒绝',expired:'已过期',cancelled:'已取消',grant_revoked:'授权已撤销'};
  const active=['waiting_input','waiting_approval'].includes(task.status);
  const pending=control.status==='pending'&&control.expires_at>now;
  const current=owner&&active&&pending&&control.version===1&&control.thread_id===task.thread_id&&control.turn_id===task.turn_id;
  return {label:control.status==='pending'&&!pending?'已过期':labels[control.status]||'需要对账',
    canDecline:current&&control.allowed_decisions.includes('decline'),
    canAccept:current&&control.kind==='approval'&&control.allowed_decisions.includes('accept'),
    canAnswer:current&&control.kind==='input'&&control.allowed_decisions.includes('answer')};
}
export function feedbackState(status){
  if(status==='cancelled')return {label:'排队意见已取消',detail:'意见记录仍保存在 Linux，这条反馈不会执行。'};
  if(status==='checks_complete')return {label:'排队意见已执行',detail:'前序任务完成后执行了这条意见；请分别查看实际检查、差异和用户验收。'};
  if(['running','validating','waiting_input','waiting_approval'].includes(status))return {label:'正在处理排队意见',detail:'已在前序任务结束后接收执行；这不代表向原活跃回合追加了意见。'};
  if(status!=='queued')return {label:'排队意见需要核对',detail:'意见记录仍保留；请核对结果、授权和会话，不能自动重放。'};
  return {label:'意见已排队',detail:'意见保存在 Linux，尚未追加到正在运行的回合。仅在前序任务完成、授权和会话重新核对后执行。'};
}
export function recoveryState(task,storage){
  const reasons={LOW_SPACE:'可用空间不足',ROOT_UNAVAILABLE:'存储入口不可用',ROOT_IDENTITY_CHANGED:'存储入口身份变化',
    PARENT_IDENTITY_CHANGED:'存储父路径身份变化',
    MOUNT_UNAVAILABLE:'挂载不可用',MOUNT_IDENTITY_CHANGED:'挂载身份变化',PROFILE_REGISTRATION_REQUIRED:'旧账本需要核对存储登记',
    PROFILE_VERSION_STALE:'存储登记版本变化',PROFILE_REJECTED:'存储登记不可用',PATH_REJECTED:'存储路径校验未通过',
    REGISTRATION_MISSING:'存储登记缺失',REGISTRATION_INCOMPLETE:'存储登记中断，需核对',REGISTRATION_REJECTED:'存储登记校验未通过',
    LEDGER_MISSING:'原任务账本缺失',LEDGER_IDENTITY_CHANGED:'任务账本身份变化',REGISTRATION_ALREADY_EXISTS:'存储已登记，不能重新初始化',
    SPACE_UNAVAILABLE:'空间观测不可用',TASK_STORE_CORRUPT:'账本完整性检查未通过',TASK_SCHEMA_UNSUPPORTED:'账本版本不支持',
    TASK_LOCATION_REJECTED:'账本位置或容量未通过检查'};
  return {blocked:storage?.state==='blocked',reason:reasons[storage?.reason_code]||'存储条件尚未确认',
    recordUnavailable:storage?.state==='blocked'&&!['LOW_SPACE','SPACE_UNAVAILABLE'].includes(storage?.reason_code),
    needsReconcile:['lost','requires_reconcile'].includes(task?.status)||task?.worker_state==='lease_expired',canRetry:false};
}

const resultHash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
export function taskResultQuery(query){
  if([...query.keys()].some(key=>key!=='result_version')||query.getAll('result_version').length>1
      ||query.has('result_version')&&!resultHash(query.get('result_version')))
    throw Object.assign(new Error('INVALID_QUERY'),{code:'INVALID_QUERY',status:400});
  return query.get('result_version');
}
export function taskResultLink(task){
  const base='#tasks/'+encodeURIComponent(task.id);
  return resultHash(task.result_version)?base+'?result_version='+task.result_version:base;
}
export function taskReadPath(taskId,expectedResultVersion=null){
  return '/api/tasks/'+encodeURIComponent(taskId)+(expectedResultVersion?'?expected_result_version='+expectedResultVersion:'');
}

function taskFocusSnapshot(target){
  const node=document.activeElement;
  if(!node||!target.contains?.(node))return null;
  return {node,id:node.id,key:node.getAttribute?.('data-task-focus'),value:node.value,
    start:node.selectionStart,end:node.selectionEnd};
}
function taskFocusIdle(target){
  const active=document.activeElement;
  return !active||active===document.body||active===target||active.isConnected===false;
}
function restoreTaskFocus(target,snapshot){
  if(!snapshot||!taskFocusIdle(target))return;
  let next=snapshot.id?document.getElementById?.(snapshot.id):null;
  if(!next||!target.contains?.(next))next=snapshot.key?target.querySelector('[data-task-focus="'+snapshot.key+'"]'):null;
  if(next&&!next.disabled){
    if(snapshot.value!==undefined&&['INPUT','TEXTAREA','SELECT'].includes(next.tagName))next.value=snapshot.value;
    next.focus();
    if(snapshot.start!==null&&snapshot.start!==undefined)try{next.setSelectionRange(snapshot.start,snapshot.end);}catch{}
  }else target.querySelector('[aria-label="任务执行状态"]')?.focus();
}
function focusTaskStatus(target,isCurrent){
  if(isCurrent()&&taskFocusIdle(target))target.querySelector('[aria-label="任务执行状态"]')?.focus();
}
function taskMessage(node,value){if(node.textContent!==value)node.textContent=value;}

export async function renderTasks(target,{taskId=null,isCurrent=()=>true,expectedResultVersion=null}={}){
  const options={taskId,isCurrent,expectedResultVersion};
  let focusSnapshot=null;
  function stale(){
    if(!isCurrent())return;
    const previous=target.querySelector?.('[data-task-record="'+taskId+'"]');
    const notice=el('section',{className:'panel stack','aria-label':'固定任务结果已变化'},
      el('h2',{},'固定任务结果已变化'),el('p',{role:'status'},previous?.getAttribute('data-result-visible')==='true'
        ?'原结果仍保留在下方；固定版本已无法重新读取，不会自动替换为新版。'
        :'原结果版本已变化，旧正文无法从当前记录恢复；没有展示新版结果。'),
      el('a',{href:'#tasks/'+encodeURIComponent(taskId)},'查看当前任务'),
      button('重新核对固定结果',()=>renderTasks(target,options)));
    if(previous){previous.inert=true;for(const action of previous.querySelectorAll('button'))action.disabled=true;
      const old=target.querySelector('[aria-label="固定任务结果已变化"]');old?.remove();target.prepend(notice);
    }else target.replaceChildren(notice);
  }
  let data;
  try{data=await api(taskId?taskReadPath(taskId,expectedResultVersion):'/api/tasks');}
  catch(error){if(expectedResultVersion&&error.status===409&&error.code==='RESULT_VERSION_STALE'){stale();return;}throw error;}
  if(expectedResultVersion&&data.task?.result_version!==expectedResultVersion){stale();return;}
  if(!isCurrent())return;
  const root=el('section',{className:'stack'},el('h1',{},'任务与结果'),el('a',{href:'#workbench'},'← 可视化工作台'),
    el('p',{className:'muted'},'浏览器关闭后读取同一请求核对结果。执行回复、实际检查与用户验收分别记录。'),
    el('a',{href:'#sessions'},'核对既有 Codex 会话'));
  const refresh=button('重新对账',async()=>{refresh.disabled=true;try{await renderTasks(target,options);focusTaskStatus(target,isCurrent);}catch{root.append(el('p',{role:'status'},'连接暂不可用；保留当前记录，请重连后核对。'));refresh.disabled=false;refresh.focus();}});
  refresh.setAttribute('data-task-focus','refresh');
  focusSnapshot=taskFocusSnapshot(target);
  const previousStatus=target.querySelector?.('[data-task-record]')?.getAttribute('data-task-status');
  root.append(refresh);target.replaceChildren(root);
  const recovery=recoveryState(data.task,data.storage);
  if(recovery.blocked)root.append(el('section',{className:'panel stack'},el('h2',{},'存储写入暂停'),el('p',{},recovery.reason),
    el('p',{className:'muted'},recovery.recordUnavailable?'当前无法确认原账本。需要核对存储和原请求；不会建立空库或自动重发任务。':'原请求仍保留。请核对存储后读取同一记录；不会改写入口或自动重发任务。')));
  if(!taskId){
    root.append(el('p',{className:'warning'},data.test_gate?'隔离测试入口；不代表真实项目执行授权。':'执行入口尚未开放；需要有效的项目授权、隔离与后台配置。'));
    if(data.tasks_available===false)root.append(el('p',{className:'warning'},'目前无法读取任务账本；原记录未被初始化或覆盖。'));
    else if(!data.tasks.length)root.append(el('div',{className:'empty'},el('h2',{},'尚无已接收任务')));
    for(const t of data.tasks)root.append(el('a',{href:taskResultLink(t),className:'panel stack'},el('h2',{},t.project),el('p',{},t.id),el('p',{},lifecycleLabel(t)),
      t.kind==='ordinary_project'?el('p',{},'普通只读任务：'+(t.task_spec?.objective||'原指令待核对')):null));
    return;
  }
  const task=data.task;root.setAttribute('data-task-record',task.id);root.setAttribute('data-task-status',lifecycleLabel(task));
  if(previousStatus&&previousStatus!==lifecycleLabel(task)){const notice=document.getElementById?.('notice');if(notice)taskMessage(notice,'任务状态：'+lifecycleLabel(task));}
  if(task.result_version)root.append(el('a',{href:taskResultLink(task)},'本次结果固定链接'));
  let owner=!recovery.blocked;const actions=[];
  root.append(...[el('h2',{},task.id),el('p',{className:task.status==='checks_complete'?'success':'warning',role:'status','aria-live':'polite','aria-label':'任务执行状态',tabIndex:-1},lifecycleLabel(task)),
    task.worker_state==='lease_expired'?el('p',{className:'warning'},'执行器租约已过期；需要核对精确进程与会话，不能重新发送。'):null,
    el('p',{},'项目：'+task.project),el('p',{className:'caption'},`会话 ${task.thread_id||'尚未绑定'} · 回合 ${task.turn_id||'尚未绑定'} · 原生状态 ${task.provider_status||'尚未取得'}`),
    el('p',{className:'caption'},'模式：'+({new:'新建会话',idle_continue:'继续明确的空闲会话',busy_feedback:'排队反馈'}[task.mode]||'待核对')+(task.parent?' · 前序任务 '+task.parent:'')),
    task.annotation_id?el('p',{className:'caption'},`保存意见 ${task.annotation_id} · revision ${task.annotation_revision}`):null,
    el('p',{className:'caption'},'用户验收：'+(reviewLabels[task.human_acceptance]||'待决定')+'。Git 交付另行核对。')].filter(Boolean));
  if(recovery.needsReconcile){
    const retry=button('重新执行（尚未开放）',()=>{});retry.disabled=true;
    root.append(el('p',{className:'warning'},'任务运行或结果尚未确认。核对原请求、执行器和会话后再决定；此处不会重新执行。'),retry);
  }
  if(task.feedback){
    const state=feedbackState(task.status);
    root.append(el('section',{className:'panel stack'},el('h3',{},state.label),el('p',{},state.detail),
      el('p',{className:'caption'},'顺序 '+task.feedback.sequence+' · 绑定前序回合 '+(task.feedback.parent_binding?.turn_id||'尚未确认')),
      task.feedback.writer_status==='unknown'?el('p',{className:'warning'},'外部写者状态未确认；保留意见，禁止启动竞争任务。'):null));
  }
  const previewAccepted=await renderBusinessTaskPreview(root,task,{isCurrent,expectedResultVersion,onVersionStale:stale});
  if(!isCurrent()||expectedResultVersion&&previewAccepted===false)return;
  if(task.result)root.setAttribute('data-result-visible','true');
  const ordinary=renderOrdinaryTaskResult(task);if(ordinary)root.append(ordinary);
  for(const control of data.controls||[]){
    const state=controlState(control,task,owner),label=el('h3',{},state.label),section=el('section',{className:'panel stack'},label,
      el('p',{},control.kind==='input'?(state.canAnswer?'请补充本次任务的信息。答复只送给这个任务，不会扩大执行权限。':state.canDecline?'本次输入请求不可答复。可拒绝本次请求，原任务指令仍保留。':'这条输入请求已结束或目前不可操作，请核对原任务状态。'):control.allowed_decisions.includes('accept')?'本次命令已与既有授权核对。允许只适用于这一次请求；项目、命令和网络权限不会扩大。':'请求权限尚未确认在已有授权内。本入口只允许拒绝，不会扩大项目、命令或网络权限。'),
      el('p',{className:'caption'},'请求版本 '+control.version+' · '+new Date(control.expires_at*1000).toLocaleTimeString()+' 到期'));
    const controlButtons=[];let responding=false;
    if(control.kind==='input'&&control.status==='pending'&&control.allowed_decisions.includes('answer')){
      const fields=[];
      for(const question of control.questions||[]){
        const options=Array.isArray(question.options)?question.options:[];
        const id='control-'+control.id+'-'+question.id;
        const input=options.length&&!question.isOther?el('select',{id},
          el('option',{value:''},'请选择'),...options.map(option=>el('option',{value:option.label},option.label+' — '+option.description))):el('textarea',{id,rows:3,maxLength:2000});
        input.disabled=!state.canAnswer;input.setAttribute('aria-describedby','task-input-error-'+control.id);input.setAttribute('aria-invalid','false');
        input.addEventListener('input',()=>{input.setAttribute('aria-invalid','false');});
        section.append(el('label',{htmlFor:id},question.header+'：'+question.question),input);
        if(options.length&&question.isOther)section.append(el('p',{className:'caption'},'可填写 '+options.map(option=>option.label).join(' / ')+'，或补充其他回答。'));
        fields.push({question,input});
        actions.push({button:input,allowed:()=>!responding&&controlState(control,task,owner).canAnswer});
      }
      const message=el('p',{id:'task-input-error-'+control.id,role:'status','aria-live':'polite'});
      const answer=button('提交补充输入',async()=>{
        if(responding)return;
        if(!fields.length||fields.some(field=>!field.input.value.trim())){for(const field of fields)field.input.setAttribute('aria-invalid',String(!field.input.value.trim()));message.textContent='请回答每个问题。';fields.find(field=>!field.input.value.trim())?.input.focus();return;}
        const answers=Object.fromEntries(fields.map(({question,input})=>[question.id,{answers:[input.value.trim()]}]));
        responding=true;controlButtons.forEach(action=>action.disabled=true);fields.forEach(field=>field.input.disabled=true);message.textContent='正在保存本次答复…';
        try{await api('/api/tasks/'+encodeURIComponent(task.id)+'/respond',{control_id:control.id,version:control.version,decision:'answer',answers});await renderTasks(target,options);focusTaskStatus(target,isCurrent);}
        catch(error){
          if(!isCurrent()||!section.isConnected)return;
          if(error.outcome==='NOT_COMMITTED'){
            if(error.status===401||error.status===403)owner=false;
            responding=false;
            for(const field of fields){field.input.disabled=!controlState(control,task,owner).canAnswer;field.input.setAttribute('aria-invalid','true');}
            answer.disabled=!controlState(control,task,owner).canAnswer;
            for(const action of controlButtons)if(action!==answer)action.disabled=!controlState(control,task,owner).canDecline;
            message.textContent=owner?'答复未保存，原输入保留；请修改后重新提交。':'当前身份无法保存答复，原输入保留；请重新核对所有者身份。';
            if(taskFocusIdle(target)||document.activeElement===answer)fields.find(field=>!field.input.disabled)?.input.focus();
          }else{
            message.textContent='答复结果未知，原输入保留；请重新对账原请求，不会自动再次提交。';
            if(taskFocusIdle(target)||document.activeElement===answer)refresh.focus();
          }
        }
      });
      answer.setAttribute('data-task-focus','answer-'+control.id);answer.disabled=!state.canAnswer;controlButtons.push(answer);actions.push({button:answer,allowed:()=>!responding&&controlState(control,task,owner).canAnswer});section.append(answer,message);
    }
    if(control.kind==='approval'&&control.allowed_decisions.includes('accept')){
      const accept=button('仅本次允许',async()=>{
        responding=true;controlButtons.forEach(action=>action.disabled=true);const message=el('p',{role:'status'},'正在保存本次答复…');section.append(message);
        try{await api('/api/tasks/'+encodeURIComponent(task.id)+'/respond',{control_id:control.id,version:control.version,decision:'accept'});await renderTasks(target,options);focusTaskStatus(target,isCurrent);}
        catch(error){message.textContent='答复结果需要核对：'+(error.code||'连接不可用')+'。请读取原任务，勿重复创建请求。';refresh.focus();}
      });accept.setAttribute('data-task-focus','accept-'+control.id);accept.disabled=!state.canAccept;controlButtons.push(accept);actions.push({button:accept,allowed:()=>!responding&&controlState(control,task,owner).canAccept});section.append(accept);
    }
    const decline=button('拒绝本次请求',async()=>{
      responding=true;controlButtons.forEach(action=>action.disabled=true);const message=el('p',{role:'status'},'正在保存拒绝…');section.append(message);
      try{await api('/api/tasks/'+encodeURIComponent(task.id)+'/respond',{control_id:control.id,version:control.version,decision:'decline'});await renderTasks(target,options);focusTaskStatus(target,isCurrent);}
      catch(error){message.textContent='答复结果需要核对：'+(error.code||'连接不可用')+'。请读取原任务，勿重复创建请求。';decline.disabled=true;refresh.focus();}
    });decline.setAttribute('data-task-focus','decline-'+control.id);decline.disabled=!state.canDecline;controlButtons.push(decline);actions.push({button:decline,allowed:()=>!responding&&controlState(control,task,owner).canDecline});section.append(decline);root.append(section);
  }
  if(task.result?.transport&&task.result.transport!=='app_server_stdio')root.append(el('p',{className:'warning'},'模拟执行结果 · 验收仅针对这份测试记录。'));
  if(task.result){
    for(const check of task.result.checks||[])root.append(el('section',{className:'panel stack'},el('h3',{},check.id+' · 退出码 '+check.exit),el('pre',{},check.stdout||check.stderr||'无输出')));
    if(task.result.diff)root.append(el('section',{className:'panel'},el('h3',{},'实际差异'),el('pre',{},task.result.diff)));
    if(task.result.error_class)root.append(el('p',{className:'warning'},'失败类别：'+task.result.error_class));
    root.append(el('details',{},el('summary',{},'执行证据'),el('pre',{},JSON.stringify(task.result,null,2))));
  }
  if(task.status==='checks_complete'&&task.result_version){
    const reviewPanel=el('section',{className:'panel stack'},el('h3',{},'审阅此次结果'),
      el('p',{},'请核对本次差异与实际检查。接受结果会保存验收记录；Git 交付另行核对。'));
    const reviewMessage=el('p',{role:'status','aria-live':'polite'});
    const reviewButtons=[];
    for(const [decision,label]of [['accept','接受此次结果'],['request_changes','需要修改'],['defer','暂缓验收']]){
      const action=button(label,async()=>{
        reviewButtons.forEach(b=>b.disabled=true);reviewMessage.textContent='正在保存验收记录…';
        const command={request_id:'review-'+crypto.randomUUID(),expected_result_version:task.result_version,
          expected_review_revision:task.result_review?.revision||0,decision};
        try{await api('/api/tasks/'+encodeURIComponent(task.id)+'/review',command);await renderTasks(target,options);focusTaskStatus(target,isCurrent);}
        catch{reviewMessage.textContent='验收结果需要核对。请重新读取此次任务，再决定；不会重新执行。';refresh.focus();}
      });
      action.setAttribute('data-task-focus','review-'+decision);action.disabled=!owner;reviewButtons.push(action);actions.push({button:action,allowed:()=>owner});reviewPanel.append(action);
    }
    reviewPanel.append(reviewMessage);root.append(reviewPanel);
  }
  if(['queued','running','waiting_input','waiting_approval','validating'].includes(task.status)){
    const cancel=button('取消这个任务',async()=>{cancel.disabled=true;try{await api('/api/tasks/'+encodeURIComponent(task.id)+'/cancel',{});await renderTasks(target,options);focusTaskStatus(target,isCurrent);}catch{root.append(el('p',{role:'status'},'取消结果待核对；请读取原任务，不会重新执行。'));cancel.disabled=false;cancel.focus();}});
    cancel.disabled=!owner;
    cancel.setAttribute('data-task-focus','cancel');
    actions.push({button:cancel,allowed:()=>owner});
    root.append(cancel);
    root.append(el('p',{className:'caption'},task.kind==='ordinary_project'?'取消只中断这个只读任务；执行状态和已经收到的回复仍保留。':'取消只中断这个任务；已经产生的修改可能保留，须查看差异后决定。'));
  }
  function identity(event){if(!root.isConnected){window.removeEventListener('hub:identity',identity);return;}owner=!recovery.blocked&&event.detail?.owner_authenticated===true;for(const action of actions)action.button.disabled=!action.allowed();}
  window.addEventListener('hub:identity',identity);
  root.append(el('details',{},el('summary',{},'有序事件'),el('pre',{},JSON.stringify(data.events,null,2))));
  restoreTaskFocus(target,focusSnapshot);
  if(task.kind==='ordinary_project'&&['queued','running','waiting_input','waiting_approval','validating'].includes(task.status)){
    const observation=el('p',{className:'caption',role:'status','aria-live':'polite'},'本任务状态已核对；尚无新的执行结果。');root.append(observation);
    const signature=value=>JSON.stringify([value.task?.id,value.task?.project,value.task?.status,
      value.task?.provider_status,value.task?.worker_state,value.task?.result_version,
      value.task?.lifecycle?.stage,value.controls]);
    const original=signature(data);
    const observe=async()=>{
      if(!root.isConnected||!isCurrent()){window.removeEventListener('hub:identity',identity);return;}
      if(document.hidden||navigator.onLine===false){setTimeout(observe,5000);return;}
      try{
        const next=await api(taskReadPath(task.id,expectedResultVersion));
        if(!root.isConnected||!isCurrent())return;
        if(next.task?.id!==task.id||next.task?.project!==task.project)throw new Error('TASK_RESPONSE_BINDING_REJECTED');
        if(signature(next)!==original){await renderTasks(target,options);return;}
        taskMessage(observation,'本任务状态已核对；尚无新的执行结果。');
      }catch(error){
        if(!root.isConnected||!isCurrent())return;
        if(expectedResultVersion&&error.status===409&&error.code==='RESULT_VERSION_STALE'){stale();return;}
        taskMessage(observation,'暂时无法核对本任务；保留当前回复和原请求，重连后继续读取。');
        if(error.status===401||error.status===403){owner=false;actions.forEach(action=>action.button.disabled=true);}
      }
      if(root.isConnected&&isCurrent())setTimeout(observe,1000);
    };
    setTimeout(observe,1000);
  }
}

export async function sendAnnotation(annotationId,status){
  status.textContent='正在核对项目授权…';
  try{
    const options=await api('/api/tasks/options/'+encodeURIComponent(annotationId));
    if(!options.execution_enabled){status.textContent='任务执行通道尚未就绪；意见已保留，请先核对连接信息。';return;}
    if(options.choices.length!==1){status.textContent='需要唯一有效的项目执行授权；意见已保存在 Linux。';return;}
    const command=options.choices[0];
    // The server supplies this grant-bound request ID; any uncertain result is read
    // by that same ID. This action never generates a replacement task on retry.
    try{const result=await api('/api/tasks',command);navigate('#tasks/'+encodeURIComponent(result.task.id));}
    catch(error){
      if(error.outcome==='NOT_COMMITTED'){status.textContent='未接收任务：'+(error.code||'请重新核对授权');return;}
      status.textContent='接收结果待对账 · '+command.request_id;
      status.append(el('a',{href:'#tasks/'+encodeURIComponent(command.request_id)},'读取原请求'));
    }
  }catch{status.textContent='暂时无法核对授权；意见已保留，尚未发送任务。';}
}
