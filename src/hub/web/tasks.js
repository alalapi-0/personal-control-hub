import {el,button,api,navigate} from './common.js';

export const taskLabels={queued:'已接收 · 排队中',running:'Codex 执行中',waiting_input:'等待补充',waiting_approval:'等待审批',validating:'实际检查中',checks_complete:'检查完成 · 待用户验收',failed:'失败',cancelled:'已取消',lost:'执行器失联',requires_reconcile:'结果待对账'};
export function controlState(control,task,owner=true,now=Date.now()/1000){
  const labels={pending:control.kind==='input'?'等待补充输入':'等待审批',declined:'已拒绝',expired:'已过期',cancelled:'已取消',grant_revoked:'授权已撤销'};
  const active=['waiting_input','waiting_approval'].includes(task.status);
  const pending=control.status==='pending'&&control.expires_at>now;
  return {label:control.status==='pending'&&!pending?'已过期':labels[control.status]||'需要对账',
    canDecline:owner&&active&&pending&&control.version===1&&control.thread_id===task.thread_id&&control.turn_id===task.turn_id&&control.allowed_decisions.includes('decline')};
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

export async function renderTasks(target,{taskId=null,isCurrent=()=>true}={}){
  const data=await api(taskId?'/api/tasks/'+encodeURIComponent(taskId):'/api/tasks');
  if(!isCurrent())return;
  const root=el('section',{className:'stack'},el('h1',{},'任务与结果'),el('a',{href:'#workbench'},'← 可视化工作台'),
    el('p',{className:'muted'},'浏览器关闭后读取同一请求核对结果。执行回复、实际检查与用户验收分别记录。'),
    el('a',{href:'#sessions'},'核对既有 Codex 会话'));
  const refresh=button('重新对账',async()=>{refresh.disabled=true;try{await renderTasks(target,{taskId,isCurrent});if(isCurrent())target.querySelector('button')?.focus();}catch{root.append(el('p',{role:'status'},'连接暂不可用；保留当前记录，请重连后核对。'));refresh.disabled=false;refresh.focus();}});
  root.append(refresh);target.replaceChildren(root);
  const recovery=recoveryState(data.task,data.storage);
  if(recovery.blocked)root.append(el('section',{className:'panel stack'},el('h2',{},'存储写入暂停'),el('p',{},recovery.reason),
    el('p',{className:'muted'},recovery.recordUnavailable?'当前无法确认原账本。需要核对存储和原请求；不会建立空库或自动重发任务。':'原请求仍保留。请核对存储后读取同一记录；不会改写入口或自动重发任务。')));
  if(!taskId){
    root.append(el('p',{className:'warning'},data.test_gate?'隔离测试入口；不代表真实项目执行授权。':'执行入口尚未开放；需要有效的项目授权、隔离与后台配置。'));
    if(data.tasks_available===false)root.append(el('p',{className:'warning'},'目前无法读取任务账本；原记录未被初始化或覆盖。'));
    else if(!data.tasks.length)root.append(el('div',{className:'empty'},el('h2',{},'尚无已接收任务')));
    for(const t of data.tasks)root.append(el('a',{href:'#tasks/'+encodeURIComponent(t.id),className:'panel stack'},el('h2',{},t.project),el('p',{},t.id),el('p',{},taskLabels[t.status]||'状态未知')));
    return;
  }
  const task=data.task;
  let owner=!recovery.blocked;const actions=[];
  root.append(...[el('h2',{},task.id),el('p',{className:task.status==='checks_complete'?'success':'warning'},taskLabels[task.status]||'状态未知'),
    task.worker_state==='lease_expired'?el('p',{className:'warning'},'执行器租约已过期；需要核对精确进程与会话，不能重新发送。'):null,
    el('p',{},'项目：'+task.project),el('p',{className:'caption'},`会话 ${task.thread_id||'尚未绑定'} · 回合 ${task.turn_id||'尚未绑定'} · 原生状态 ${task.provider_status||'尚未取得'}`),
    el('p',{className:'caption'},'模式：'+({new:'新建会话',idle_continue:'继续明确的空闲会话',busy_feedback:'排队反馈'}[task.mode]||'待核对')+(task.parent?' · 前序任务 '+task.parent:'')),
    task.annotation_id?el('p',{className:'caption'},`保存意见 ${task.annotation_id} · revision ${task.annotation_revision}`):null,
    el('p',{className:'caption'},'用户验收：待决定；检查完成不代表用户接受或发布。')].filter(Boolean));
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
  for(const control of data.controls||[]){
    const state=controlState(control,task,owner),label=el('h3',{},state.label),section=el('section',{className:'panel stack'},label,
      el('p',{},control.kind==='input'?'本适配器尚未验证输入答复能力。可拒绝本次请求，保存的修改意见不会被删除。':'请求权限尚未确认在已有授权内。本入口只允许拒绝，不会扩大项目、命令或网络权限。'),
      el('p',{className:'caption'},'请求版本 '+control.version+' · '+new Date(control.expires_at*1000).toLocaleTimeString()+' 到期'));
    const decline=button('拒绝本次请求',async()=>{
      decline.disabled=true;const message=el('p',{role:'status'},'正在保存拒绝…');section.append(message);
      try{await api('/api/tasks/'+encodeURIComponent(task.id)+'/respond',{control_id:control.id,version:control.version,decision:'decline'});await renderTasks(target,{taskId,isCurrent});if(isCurrent())target.querySelector('button')?.focus();}
      catch(error){message.textContent='答复结果需要核对：'+(error.code||'连接不可用')+'。请读取原任务，勿重复创建请求。';decline.disabled=true;refresh.focus();}
    });decline.disabled=!state.canDecline;actions.push({button:decline,allowed:()=>controlState(control,task,owner).canDecline});section.append(decline);root.append(section);
  }
  if(task.result){
    for(const check of task.result.checks||[])root.append(el('section',{className:'panel stack'},el('h3',{},check.id+' · 退出码 '+check.exit),el('pre',{},check.stdout||check.stderr||'无输出')));
    if(task.result.diff)root.append(el('section',{className:'panel'},el('h3',{},'实际差异'),el('pre',{},task.result.diff)));
    if(task.result.error_class)root.append(el('p',{className:'warning'},'失败类别：'+task.result.error_class));
    root.append(el('details',{},el('summary',{},'执行证据'),el('pre',{},JSON.stringify(task.result,null,2))));
  }
  if(['queued','running','waiting_input','waiting_approval','validating'].includes(task.status)){
    const cancel=button('取消这个任务',async()=>{cancel.disabled=true;try{await api('/api/tasks/'+encodeURIComponent(task.id)+'/cancel',{});await renderTasks(target,{taskId,isCurrent});if(isCurrent())target.querySelector('button')?.focus();}catch{root.append(el('p',{role:'status'},'取消结果待核对；请读取原任务，不会重新执行。'));cancel.disabled=false;cancel.focus();}});
    cancel.disabled=!owner;
    actions.push({button:cancel,allowed:()=>owner});
    root.append(cancel);
    root.append(el('p',{className:'caption'},'取消只中断这个任务；已经产生的修改可能保留，须查看差异后决定。'));
  }
  function identity(event){if(!root.isConnected){window.removeEventListener('hub:identity',identity);return;}owner=!recovery.blocked&&event.detail?.owner_authenticated===true;for(const action of actions)action.button.disabled=!action.allowed();}
  window.addEventListener('hub:identity',identity);
  root.append(el('details',{},el('summary',{},'有序事件'),el('pre',{},JSON.stringify(data.events,null,2))));
}

export async function sendAnnotation(annotationId,status){
  status.textContent='正在核对项目授权…';
  try{
    const options=await api('/api/tasks/options/'+encodeURIComponent(annotationId));
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
