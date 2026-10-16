import {el,button,api,navigate,parseHubRoute} from './common.js';

const modes={new:'新建任务',idle_continue:'继续空闲任务',busy_feedback:'排队反馈'};
const sha=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const id=value=>typeof value==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(value);
const clone=value=>structuredClone(value);
const sameSpec=(left,right)=>left&&right&&Object.keys(left).length===Object.keys(right).length
  &&Object.entries(left).every(([key,value])=>right[key]===value);
const requestId=()=>`ordinary-${crypto.randomUUID()}`;
export function ordinaryFailure(error={}){
  if(error.code==='ORDINARY_SPEC_INVALID')return '请先填写完整的任务目标和范围，再保存或切换草稿；当前输入保留。';
  if(error.status===401)return '请连接所有者后重试；当前输入保留。';
  if(error.status===403)return '当前身份没有这项操作权限，请核对授权范围；当前输入保留。';
  if(error.status===409)return '版本或执行条件已变化，请重新核对后显式保存；当前输入保留。';
  if(error.status===503)return '普通任务服务暂不可用，请核对后台条件；仍可保留或保存草稿。';
  if(error.code==='NETWORK_TIMEOUT')return '等待后台超时；请核对原请求，当前输入保留。';
  return '连接或读取暂不可用；当前输入保留，请核对原请求后再操作。';
}
export function validOrdinarySpec(spec,{submit=false}={}){
  const fields=['kind','request_id','project_id','objective','scope','effect','input_version','mode','parent_task_id'];
  return Boolean(spec&&Object.keys(spec).length===fields.length&&fields.every(field=>Object.hasOwn(spec,field))
    &&spec.kind==='ordinary_project'&&id(spec.request_id)&&id(spec.project_id)
    &&typeof spec.objective==='string'&&spec.objective.trim().length>0&&spec.objective.length<=2000
    &&typeof spec.scope==='string'&&spec.scope.trim().length>0&&spec.scope.length<=1000
    &&!spec.objective.includes('\0')&&!spec.scope.includes('\0')
    &&spec.effect==='read_only'&&(sha(spec.input_version)||!submit&&spec.input_version===null)
    &&Object.hasOwn(modes,spec.mode)&&(spec.mode==='new'?spec.parent_task_id===null:id(spec.parent_task_id)));
}
export function ordinaryTaskMatches(task,spec){
  return task?.kind==='ordinary_project'&&task.id===spec.request_id&&task.project===spec.project_id
    &&sameSpec(task.task_spec,spec);
}
export function renderOrdinaryTaskResult(task){
  if(task.kind!=='ordinary_project')return null;
  if(!id(task.id)||!id(task.project)||task.task_spec?.project_id!==task.project)return el('section',{
    className:'panel stack','aria-label':'普通任务指令与回复'},el('h2',{},'普通任务指令与回复'),
    el('p',{className:'warning'},'原任务的项目绑定不一致，请核对原请求；当前不展示或续接其他项目的指令。'));
  const spec=task.task_spec??{},panel=el('section',{className:'panel stack','aria-label':'普通任务指令与回复'},
    el('h2',{},'普通任务指令与回复'),el('p',{},task.classification==='real'?'真实任务记录':'隔离测试任务记录'),
    el('p',{},'执行位置：'+(task.execution_location||'尚未提供')),
    el('dl',{},...Object.entries({'任务目标':spec.objective,'任务范围':spec.scope,'允许效果':'只读答复，不改写项目文件'}).map(([label,value])=>
      el('div',{},el('dt',{},label),el('dd',{},value||'原指令尚不可用')))),
    el('p',{className:'caption'},'本次请求：'+task.id),
    el('a',{href:'#projects/'+encodeURIComponent(spec.project_id||task.project)+'/tasks'},'返回项目任务区'));
  if(typeof task.result?.response==='string'){
    panel.append(el('h3',{},'执行回复'),
      el('pre',{'aria-label':'普通任务执行回复'},task.result.response||'执行器返回了空回复。'));
    if(task.result.response_truncated===true)panel.append(el('p',{className:'warning'},'回复超过后台保留上限，当前记录已截断；不能视为完整回复。'));
  }
  else panel.append(el('p',{},'尚无已记录的执行回复，请读取原请求核对。'));
  if(id(task.id)&&id(spec.project_id)&&spec.project_id===task.project)panel.append(el('a',{
    href:'#projects/'+encodeURIComponent(task.project)+'/tasks?parent_task_id='+encodeURIComponent(task.id)},'继续此普通任务'));
  if(['waiting_input','waiting_approval'].includes(task.status))panel.append(el('p',{className:'warning'},'请在下方处理这个任务的待答复请求；任务和原指令会继续保留。'));
  return panel;
}

export async function renderOrdinaryTasks(project,{isCurrent=()=>true,request=api,go=navigate}={}){
  const projectId=project.project_id;
  if(!id(projectId))throw new Error('ORDINARY_PROJECT_INVALID');
  const section=el('section',{className:'panel stack','aria-label':'普通任务'},el('h2',{},'普通任务'),
    el('p',{},'编写本项目的只读任务。保存草稿不会启动模型；发送前会先保存同一份任务指令。'));
  const status=el('p',{role:'status','aria-live':'polite','aria-label':'普通任务状态'}),conditions=el('div',{className:'stack'});
  const objective=el('textarea',{maxLength:2000,rows:4,'aria-label':'任务目标'}),scope=el('textarea',{maxLength:1000,rows:3,'aria-label':'任务范围'});
  const feedbackPrefix='ordinary-fields-'+crypto.randomUUID();
  const objectiveError=el('span',{id:feedbackPrefix+'-objective',className:'management-field-error warning'});
  const scopeError=el('span',{id:feedbackPrefix+'-scope',className:'management-field-error warning'});
  const sendReason=el('p',{id:feedbackPrefix+'-send',className:'caption',role:'status','aria-live':'polite'});
  for(const [control,error,name]of [[objective,objectiveError,'objective'],[scope,scopeError,'scope']]){
    control.setAttribute('aria-required','true');control.setAttribute('aria-invalid','false');
    control.setAttribute('aria-describedby',feedbackPrefix+'-'+name);
    error.hidden=true;
  }
  const parent=el('select',{'aria-label':'续接任务'}),mode=el('select',{'aria-label':'任务模式'});
  let context=null,owner=false,busy=false,dirty=false,conflict=false,pendingDraft=null,uncertain=null,loadEpoch=0;
  let parentId=null,spec=null,revision=0,blocked=false,backupConflict=false,backupBaseRevision=0,backupWarning='';
  const parsed=typeof location==='undefined'?null:parseHubRoute(location.hash);
  if(parsed?.parts[1]===projectId&&id(parsed.query.get('parent_task_id')))parentId=parsed.query.get('parent_task_id');
  const alive=()=>isCurrent();
  const ownedParents=()=>Array.isArray(context?.parents)?context.parents.filter(task=>task.kind==='ordinary_project'
    &&task.project===projectId&&task.task_spec?.project_id===projectId&&id(task.id)):[];
  const allowedModes=()=>parentId?(ownedParents().find(task=>task.id===parentId)?.allowed_modes??[]):['new'];
  function freshSpec(version){return {kind:'ordinary_project',request_id:requestId(),project_id:projectId,objective:'',scope:'',effect:'read_only',
    input_version:sha(version)?version:null,mode:parentId?(allowedModes()[0]||'idle_continue'):'new',parent_task_id:parentId};}
  const backupKey=()=>owner&&sha(context?.draft_scope)?'hub:ordinary-draft:'+context.draft_scope:null;
  function backupShape(value){
    return value&&value.project_id===projectId&&value.parent_task_id===parentId
      &&typeof value.objective==='string'&&value.objective.length<=2000&&typeof value.scope==='string'&&value.scope.length<=1000
      &&validOrdinarySpec({...value,objective:value.objective.trim()||'draft',scope:value.scope.trim()||'draft'});
  }
  function writeBackup(){
    const key=backupKey();
    if(!key){backupWarning='当前没有可核对的所有者草稿范围，本次未保存输入仅在当前页面。';return false;}
    try{globalThis.localStorage.setItem(key,JSON.stringify({spec:clone(spec),base_revision:backupConflict?backupBaseRevision:revision}));backupWarning='';return true;}
    catch{backupWarning='浏览器草稿备份不可用，本次未保存输入仅在当前页面；请显式保存任务草稿。';return false;}
  }
  function restoreBackup(){
    const key=backupKey();if(!key)return;
    try{
      const raw=globalThis.localStorage.getItem(key);if(!raw)return;
      const value=JSON.parse(raw);
      if(!Number.isSafeInteger(value?.base_revision)||value.base_revision<0||!backupShape(value.spec)){
        backupWarning='浏览器备份与当前项目或续接目标不一致，未载入；请核对当前正式草稿。';return;
      }
      spec=clone(value.spec);dirty=true;backupBaseRevision=value.base_revision;backupConflict=value.base_revision!==revision;conflict=backupConflict;
      status.textContent=backupConflict?'浏览器未保存输入的基础版本与正式草稿不同；输入已保留，需显式按当前版本应用后再保存。'
        :'已恢复浏览器中尚未正式保存的输入；请检查后保存任务草稿。';
    }catch{backupWarning='浏览器草稿备份不可用，本次未保存输入仅在当前页面；请显式保存任务草稿。';}
  }
  function readFields(){if(!spec||!alive()||busy||pendingDraft||uncertain)return;spec.objective=objective.value;spec.scope=scope.value;spec.mode=mode.value;dirty=true;writeBackup();refresh();}
  for(const field of [objective,scope])field.addEventListener('input',readFields);mode.addEventListener('change',readFields);
  const save=button('保存任务草稿',()=>saveDraft().catch(()=>{}));
  const send=button('发送普通任务',submit,{'aria-describedby':feedbackPrefix+'-send'});
  const reconcile=button('核对原任务请求',()=>lookupOriginal().catch(()=>{}));
  const reload=button('核对任务条件',()=>loadContext(true,parentId,true).catch(error=>{if(alive())status.textContent=ordinaryFailure(error);}));
  const rebind=button('重新读取任务输入',async()=>{
    if(busy||pendingDraft||uncertain)return;
    try{if(!await loadContext(true,parentId,true)||!alive()||!sha(context?.input_version))return;
      spec.input_version=context.input_version;spec.request_id=requestId();dirty=true;blocked=false;
      writeBackup();
      status.textContent='已显式绑定当前输入版本并准备新请求身份；文本保留，尚未发送，请先保存或发送任务。';refresh();
    }catch(error){if(alive())status.textContent=ordinaryFailure(error);}
  });
  const resolveDraft=button('重读草稿版本',async()=>{
    if(!conflict||busy||pendingDraft||uncertain)return;
    try{const value=await request(contextPath(parentId));if(!alive())return;checkContext(value,parentId);
      if(context?.draft_scope&&value.draft_scope!==context.draft_scope)throw new Error('ORDINARY_OWNER_SCOPE_CHANGED');
      revision=value.draft.revision;context=value;conflict=false;backupConflict=false;backupBaseRevision=revision;writeBackup();status.textContent='已读取当前草稿版本；当前输入保留，请检查后显式保存。';refresh();
    }catch(error){if(alive())status.textContent=ordinaryFailure(error);}
  });
  const switchDraft=button('载入所选任务草稿',async()=>{
    if(busy||pendingDraft||uncertain)return;
    const selected=parent.value||null;
    if(selected===parentId)return;
    if(selected&&!ownedParents().some(task=>task.id===selected)){status.textContent='续接目标不属于当前项目的已核实普通任务。';return;}
    try{if(dirty){if(validOrdinarySpec(spec))await saveDraft();else if(!writeBackup())throw Object.assign(new Error(),{code:'ORDINARY_SPEC_INVALID'});}if(!alive()||!await loadContext(false,selected))return;
      status.textContent='已载入该目标独立保存的草稿；尚未发送任务。';
    }catch(error){if(alive())status.textContent=ordinaryFailure(error);}
  });
  const newDraft=button('新建另一条任务草稿',()=>{
    if(busy||pendingDraft||uncertain||!context)return;
    spec=freshSpec(context.input_version);dirty=true;blocked=false;writeBackup();fill();status.textContent='已准备新请求草稿；原任务不会重发，请填写目标和范围后保存。';refresh();
  });
  section.append(conditions,el('label',{},'任务目标',objective,objectiveError),el('label',{},'任务范围',scope,scopeError),
    el('label',{},'续接任务',parent),switchDraft,el('label',{},'任务模式',mode),
    sendReason,el('div',{className:'row'},save,send,reconcile,reload,rebind,resolveDraft,newDraft),status);
  function contextPath(target){return '/api/ordinary-context?project_id='+encodeURIComponent(projectId)+(target?'&parent_task_id='+encodeURIComponent(target):'');}
  function checkContext(value,target){
    if(value?.project_id!==projectId||value?.draft?.project_id!==projectId||value.draft.parent_task_id!==target
      ||!Number.isSafeInteger(value.draft.revision)||value.draft.revision<0)throw new Error('ORDINARY_CONTEXT_BINDING_INVALID');
    if(target&&!value.parents?.some(task=>task.id===target&&task.kind==='ordinary_project'&&task.project===projectId&&task.task_spec?.project_id===projectId))
      throw new Error('ORDINARY_PARENT_BINDING_INVALID');
  }
  function fill(){objective.value=spec?.objective||'';scope.value=spec?.scope||'';renderOptions();}
  function renderOptions(){
    parent.replaceChildren(el('option',{value:''},'本项目新任务'),...ownedParents().map(task=>el('option',{value:task.id},task.id)));
    parent.value=parentId||'';
    const values=allowedModes();mode.replaceChildren(...values.map(value=>el('option',{value},modes[value]||value)));
    if(spec&&!values.includes(spec.mode))mode.append(el('option',{value:spec.mode,disabled:true},'原任务模式当前不可用'));
    mode.value=spec?.mode||(parentId?'idle_continue':'new');
  }
  function fieldMessage(value,label,limit){
    if(typeof value!=='string'||!value.trim())return '请填写'+label+'。';
    if(value.length>limit)return label+'最多 '+limit+' 字符；当前输入保留，请缩短后再保存或发送。';
    if(value.includes('\0'))return label+'包含无效字符，请移除后再保存或发送。';
    return '';
  }
  function refresh(){
    const messages=[fieldMessage(spec?.objective,'任务目标',2000),fieldMessage(spec?.scope,'任务范围',1000)];
    for(const [index,control,error]of [[0,objective,objectiveError],[1,scope,scopeError]]){
      const message=spec?messages[index]:'';
      control.setAttribute('aria-invalid',String(Boolean(message)));error.textContent=message;error.hidden=!message;
    }
    const locked=busy||Boolean(pendingDraft)||Boolean(uncertain);
    for(const field of [objective,scope,parent,mode])field.disabled=locked||!context;
    save.disabled=!owner||busy||Boolean(uncertain)||!spec||conflict||!pendingDraft&&!validOrdinarySpec(spec);
    save.textContent=pendingDraft?'重试确认草稿保存':'保存任务草稿';
    const valid=validOrdinarySpec(spec,{submit:true});
    send.disabled=!owner||locked||conflict||blocked||!context?.ready||!valid||!allowedModes().includes(spec?.mode)
      ||!context.allowed_effects?.includes('read_only')||spec?.input_version!==context.input_version;
    sendReason.textContent=!send.disabled?'任务目标、范围和只读条件已核对；发送前会先保存同一份任务草稿。'
      :!context?'尚未取得本项目任务条件，请连接所有者并核对任务条件。'
      :!owner?'请连接所有者后发送；当前输入保留。'
      :uncertain?'发送结果尚未确认，请核对原任务请求；不会重新发送。'
      :pendingDraft?'草稿保存结果尚未确认，请先重试确认原草稿保存。'
      :busy?'正在处理原请求，请等待结果；不会重复发送。'
      :conflict?'草稿版本存在冲突，请先重读或显式应用当前草稿版本；当前输入保留。'
      :blocked?'执行条件已变化，请先核对任务条件；当前输入保留。'
      :messages.some(Boolean)?'请先修正任务目标或任务范围中标明的问题，再保存或发送。'
      :!context.ready?'普通任务执行条件尚未就绪，请核对任务条件；仍可保存草稿。'
      :!allowedModes().includes(spec?.mode)?'当前续接目标不允许所选任务模式，请核对续接任务。'
      :!context.allowed_effects?.includes('read_only')?'当前授权没有允许只读任务，请核对任务条件。'
      :!sha(spec?.input_version)||spec.input_version!==context.input_version?'草稿输入版本尚未核实或已经变化，请显式重新读取任务输入。'
      :'任务指令尚未通过校验，请核对原任务草稿。';
    reconcile.disabled=busy||!uncertain;reconcile.hidden=!uncertain;
    reload.disabled=busy||Boolean(pendingDraft)||Boolean(uncertain);
    rebind.disabled=locked||!context;resolveDraft.disabled=locked||!conflict;resolveDraft.hidden=!conflict;
    resolveDraft.textContent=backupConflict?'按当前版本应用本地草稿':'重读草稿版本';
    switchDraft.disabled=locked||!context;newDraft.disabled=locked||!context;
    conditions.replaceChildren(...[el('p',{},'执行位置：'+(context?.execution_location||'尚未核对')),
      el('p',{},owner?'所有者已连接':'请连接所有者后保存草稿或发送；本地输入保留。'),
      el('p',{},context?.ready?'只读任务条件已就绪':'普通任务暂不可发送；可用时仍需核对授权范围。'),
      context?.reason_code?el('p',{className:'caption'},'条件代码：'+context.reason_code):null,
      spec&&!validOrdinarySpec(spec)?el('p',{className:'warning'},'请填写非空任务目标（最多 2000 字符）和任务范围（最多 1000 字符），并选择对应任务模式。'):null,
      spec&&spec.input_version!==context?.input_version?el('p',{className:'warning'},'草稿绑定的输入版本与当前来源不同，请显式重新读取任务输入。'):null,
      spec?el('p',{className:'caption'},'草稿请求：'+spec.request_id+' · 版本 '+revision):null].filter(Boolean));
    if(backupWarning)conditions.append(el('p',{className:'warning'},backupWarning));
  }
  async function loadContext(preserve=false,target=parentId,allowBlockedReset=false){
    const epoch=++loadEpoch;
    const [value,identity]=await Promise.all([request(contextPath(target)),request('/api/identity')]);
    if(!alive()||epoch!==loadEpoch)return false;
    checkContext(value,target);const scopeChanged=context?.draft_scope&&context.draft_scope!==value.draft_scope;
    owner=identity?.owner_authenticated===true;context=value;
    if(!preserve||!spec||scopeChanged){parentId=target;revision=value.draft.revision;
      const saved=value.draft.value;
      if(saved&&(saved.kind!=='ordinary_project'||saved.project_id!==projectId||saved.parent_task_id!==target))throw new Error('ORDINARY_DRAFT_BINDING_INVALID');
      spec=saved?clone(saved):freshSpec(value.input_version);dirty=false;conflict=false;backupConflict=false;blocked=false;backupWarning='';restoreBackup();fill();
    }else renderOptions();
    if(!preserve||allowBlockedReset)blocked=false;refresh();return true;
  }
  async function saveDraft(){
    if(busy||uncertain||!spec||!owner||conflict)throw new Error('ORDINARY_DRAFT_NOT_READY');
    if(!pendingDraft&&!validOrdinarySpec(spec))throw Object.assign(new Error('ORDINARY_SPEC_INVALID'),{code:'ORDINARY_SPEC_INVALID'});
    busy=true;refresh();
    const command=pendingDraft??{request_id:'draft-'+crypto.randomUUID(),expected_revision:revision,project_id:projectId,parent_task_id:parentId,value:clone(spec)};
    const savedBackupKey=backupKey();
    try{
      const value=await request('/api/ordinary-drafts',command);
      if(!alive())return false;
      const saved=value?.draft;
      if(saved?.project_id!==projectId||saved.parent_task_id!==parentId||!Number.isSafeInteger(saved.revision)||saved.revision<revision
        ||!sameSpec(saved.value,command.value))throw Object.assign(new Error('ORDINARY_DRAFT_RECEIPT_INVALID'),{outcome:'UNKNOWN'});
      revision=saved.revision;pendingDraft=null;dirty=false;
      if(savedBackupKey)try{globalThis.localStorage.removeItem(savedBackupKey);backupWarning='';}catch{backupWarning='正式草稿已保存；浏览器旧备份未能清除，返回时请核对版本。';}
      status.textContent='任务草稿已保存；没有启动模型。';return true;
    }catch(error){
      if(alive()){
        if(error.status===401||error.status===403)owner=false;
        if(error.status===409)conflict=true;
        if(error.outcome!=='NOT_COMMITTED'&&!(error.status>=400&&error.status<500))pendingDraft=clone(command);
        status.textContent=ordinaryFailure(error)+(pendingDraft?' 请使用原草稿请求重试确认保存。':'');
      }
      throw error;
    }finally{busy=false;if(alive())refresh();}
  }
  async function lookupOriginal(){
    if(!uncertain||busy)return false;
    busy=true;refresh();
    try{
      const value=await request('/api/tasks/'+encodeURIComponent(uncertain.request_id));
      if(!alive())return false;
      if(!ordinaryTaskMatches(value?.task,uncertain))throw new Error('ORDINARY_RECEIPT_BINDING_INVALID');
      const found=uncertain.request_id;uncertain=null;status.textContent='已核对原请求；没有重新发送任务。';go('#tasks/'+encodeURIComponent(found));return true;
    }catch(error){if(alive())status.textContent=error.status===404?'原请求尚未查到，提交结果仍需核对；不会自动重发或生成新请求。':ordinaryFailure(error)+' 请继续核对原任务请求。';return false;}
    finally{busy=false;if(alive())refresh();}
  }
  async function submit(){
    if(send.disabled||busy||pendingDraft||uncertain)return;
    try{if(!await saveDraft()||!alive())return;}catch{return;}
    busy=true;const command=clone(spec);refresh();status.textContent='正在发送已保存的只读任务…';
    try{
      const value=await request('/api/tasks',command);
      if(!alive())return;
      if(!ordinaryTaskMatches(value?.task,command))throw Object.assign(new Error('ORDINARY_RECEIPT_BINDING_INVALID'),{outcome:'UNKNOWN'});
      status.textContent='任务已接收，请查看原请求的独立执行与回复。';go('#tasks/'+encodeURIComponent(value.task.id));
    }catch(error){
      if(!alive())return;
      if(error.status===401||error.status===403)owner=false;
      if(error.outcome==='NOT_COMMITTED'){
        ++loadEpoch;
        blocked=error.status===409||error.status===503;
        if(error.status===503&&context)context={...context,ready:false,reason_code:error.code};
        status.textContent=ordinaryFailure(error);
      }else{uncertain=command;status.textContent='发送结果未知，正在按原请求身份核对…';}
    }finally{busy=false;if(alive())refresh();}
    if(uncertain&&alive())await lookupOriginal();
  }
  try{await loadContext();}catch(error){if(alive()){status.textContent=ordinaryFailure(error);refresh();}}
  const identity=event=>{
    if(!alive()||!section.isConnected){window.removeEventListener('hub:identity',identity);return;}
    owner=event.detail?.owner_authenticated===true;refresh();
    if(owner&&!busy&&!pendingDraft&&!uncertain)loadContext(true).catch(error=>{if(alive())status.textContent=ordinaryFailure(error);});
  };
  if(typeof window!=='undefined')window.addEventListener('hub:identity',identity);
  return section;
}
