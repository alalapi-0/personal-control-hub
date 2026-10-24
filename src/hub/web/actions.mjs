import {el} from './dom.mjs';
import {api as defaultApi} from './common.js';
const ids=['open_registered_artifact','verify_registered_artifact','prepare_ordinary_task','native_program'];
const fields=['schema_version','project_id','object_id','object_revision','artifact_id','artifact_sha256','candidate_id','candidate_revision','candidate_sha256','classification','source_version','host','backend_instance','binding_sha256'];
const validBinding=b=>b&&Object.keys(b).length===fields.length&&fields.every(k=>Object.hasOwn(b,k))&&b.schema_version==='1.0'&&fields.filter(k=>k!=='candidate_revision').every(k=>typeof b[k]==='string'&&b[k].length>0&&b[k].length<=4096&&!/[\u0000-\u001f]/.test(b[k]))&&Number.isInteger(b.candidate_revision)&&b.candidate_revision>=1&&b.candidate_revision<=999999999&&['artifact_sha256','candidate_sha256','source_version','binding_sha256'].every(k=>/^[a-f0-9]{64}$/.test(b[k]))&&/^[a-f0-9]{32}$/.test(b.backend_instance)&&/^http:\/\/127\.0\.0\.1:[0-9]{1,5}$/.test(b.host);
const sameKey=(a,b)=>a.id===b.id&&a.revision===b.revision;
const canonical=v=>v&&typeof v==='object'?Array.isArray(v)?v.map(canonical):Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
export async function actionRequest(action,binding){
 const bytes=new TextEncoder().encode(JSON.stringify(canonical({action_id:action,binding})));
 const hash=await crypto.subtle.digest('SHA-256',bytes);
 return {action_id:action,binding,request_id:'read-'+Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join('')};
}
const specs={open_registered_artifact:['read','读取登记产物','controlled_artifact_read'],verify_registered_artifact:['verification','只读核验产物','registered_artifact_sha256'],prepare_ordinary_task:['task_form','编写普通任务','ordinary_task_form'],native_program:['native','原生软件动作尚不可用','unavailable']};
function inputSchema(id){const properties={binding:{type:'object',additionalProperties:false,required:[...fields].sort(),properties:Object.fromEntries([...fields].sort().map(k=>[k,k==='candidate_revision'?{type:'integer',minimum:1,maximum:999999999}:{type:'string',minLength:1,maxLength:4096}]))}};for(const key of ['artifact_sha256','candidate_sha256','source_version','binding_sha256'])properties.binding.properties[key]={type:'string',pattern:'^[a-f0-9]{64}$'};properties.binding.properties.schema_version={const:'1.0'};properties.binding.properties.backend_instance={type:'string',pattern:'^[a-f0-9]{32}$'};properties.binding.properties.host={type:'string',pattern:'^http://127\\.0\\.0\\.1:[0-9]{1,5}$'};if(id==='verify_registered_artifact')Object.assign(properties,{action_id:{const:id},request_id:{type:'string',pattern:'^read-[a-f0-9]{64}$'}});return {schema_version:'action-input/1',type:'object',additionalProperties:false,required:Object.keys(properties).sort(),properties};}
function portContract(id,b){const handler=specs[id][2],task=id==='prepare_ordinary_task',native=id==='native_program',input=task?'task_form_navigation_context':'registered_artifact',output=task?'ordinary_task_form':id==='verify_registered_artifact'?'sha256_observation':'bytes';const version=Object.fromEntries(['binding_sha256','object_id','object_revision','source_version','artifact_sha256','candidate_id','candidate_revision','candidate_sha256'].map(k=>[k,b[k]]));const port=(direction,type)=>({id:direction,direction,type,version:{...version}});return {tool:{schema_version:'action-tool/1',handler,available:!native,capabilities:native?[]:task?['ordinary_task_form_navigation']:id==='verify_registered_artifact'?['registered_artifact_bounded_read','sha256_verification']:['registered_artifact_bounded_read'],input_types:native?[]:[input],output_types:native?[]:[output],native_execution:false,durable_completion:false},ports:{schema_version:'action-ports/1',tool:handler,inputs:native?[]:[port('input',input)],outputs:native?[]:[port('output',output)]}};}
export function actionDescriptor(id,b){if(!ids.includes(id))throw Error('ACTION_DESCRIPTOR_INVALID');const [kind,label,handler]=specs[id],task=id==='prepare_ordinary_task',native=id==='native_program';return {schema_version:'1.2',...portContract(id,b),id,kind,label,handler,available:!native,reason:native?'NATIVE_HANDLER_UNAVAILABLE':null,binding:b,object_type:'Artifact',host:{origin:b.host,backend_instance:b.backend_instance},input_schema:inputSchema(id),required_capabilities:task?['existing_ordinary_task_form']:native?['native_handler_unavailable']:['registered_artifact_bounded_read'],authorization:{session:'permitted_session',owner_csrf:id==='verify_registered_artifact',current_project_binding:true,execution_permission:false,deferred_task_authorization:task?'existing_owner_csrf_grant_cas_budget_executor_gates':null},effect_class:task?'task_form_navigation':native?'unavailable':'local_read_only',idempotency:{mode:task?'existing_ordinary_api_requests':native?'unavailable':'content_addressed_revalidation',durable_receipt:false},reconcile:{method:task?'existing_ordinary_context_draft_submit':native?'unavailable':'revalidate_exact_binding',auto_retry:false,execution_deferred:task}};}
const sha=async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
const hashValue=v=>sha(new TextEncoder().encode(JSON.stringify(canonical(v))));
const maximumBytes=8*1024*1024;
async function boundedBlob(response){
 const length=response.headers.get('Content-Length');if(length!==null&&(!/^[0-9]+$/.test(length)||Number(length)>maximumBytes))throw Error('ACTION_READ_SIZE');
 if(!response.body?.getReader){const blob=await response.blob();if(blob.size>maximumBytes)throw Error('ACTION_READ_SIZE');return blob;}
 const reader=response.body.getReader(),chunks=[];let size=0;
 try{for(;;){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>maximumBytes)throw Error('ACTION_READ_SIZE');chunks.push(value);}return new Blob(chunks,{type:response.headers.get('Content-Type')});}
 finally{await reader.cancel();reader.releaseLock();}
}
export async function validateReadReceipt(response,command,blob){
 const get=k=>response.headers.get('X-Hub-Read-'+k),b=command.binding;
 if(get('Schema')!=='local-read/1'||!/^local-read-[a-f0-9]{32}$/.test(get('Run-Id')||'')||get('Request-Id')!==command.request_id||get('Input-SHA256')!==b.artifact_sha256||get('Binding-SHA256')!==b.binding_sha256||get('Parameter-SHA256')!==await hashValue(command)||get('Durable-Receipt')!=='false'||get('Bytes')!==String(blob.size)||blob.size>maximumBytes||await sha(await blob.arrayBuffer())!==b.artifact_sha256)throw Error('ACTION_READ_RECEIPT_INVALID');
 return {run_id:get('Run-Id'),request_id:get('Request-Id'),input_sha256:get('Input-SHA256'),parameter_sha256:get('Parameter-SHA256'),binding_sha256:get('Binding-SHA256'),bytes_read:blob.size,durable_receipt:false};
}
export function actionCatalog(value,projectId,projectionVersion=null){
 if(value?.schema_version!=='1.0'||value.project_id!==projectId||value.read_only!==true||value.native_execution_available!==false||!Array.isArray(value.entries)||value.entries.length>1000||projectionVersion&&value.projection_version!==projectionVersion)throw new Error('ACTION_VERSION_CHANGED');
 for(const entry of value.entries){
  if(entry?.object?.project_id!==projectId||entry.object.type!=='Artifact'||entry.row?.object_id!==entry.object.id||entry.row.revision!==entry.object.revision||!Array.isArray(entry.actions)||entry.actions.length>4)throw new Error('ACTION_BINDING_INVALID');
  const seen=new Set();
  for(const action of entry.actions){const b=action.binding;
   if(!validBinding(b)||!ids.includes(action.id)||seen.has(action.id)||action.schema_version!=='1.2'||JSON.stringify(canonical(action))!==JSON.stringify(canonical(actionDescriptor(action.id,b)))||typeof action.available!=='boolean'||!b||Object.keys(b).length!==fields.length||!fields.every(k=>Object.hasOwn(b,k))||b.project_id!==projectId||b.object_id!==entry.object.id||b.object_revision!==entry.object.revision||b.host!==value.host||b.backend_instance!==value.backend_instance||b.source_version!==value.source_version)throw new Error('ACTION_BINDING_INVALID');
   seen.add(action.id);
  }
 }
 return value;
}
const taskRoute=p=>'#projects/'+encodeURIComponent(p)+'/tasks';
const errorText=e=>e?.status===401||e?.status===403?'当前身份不能读取或核验；请核对所有者连接。':e?.status===409||e?.message==='ACTION_VERSION_CHANGED'?'产物或来源版本已变化，请重新读取目录与图。':'读取或核验未确认；保留当前版本，可重试同一只读请求。';
export function createActionPanel({projectId,selected=null,projectionVersion=null,catalog=null,api=defaultApi,fetcher=globalThis.fetch,onTask=()=>{location.hash=taskRoute(projectId);},isCurrent=()=>true}={}){
 const element=el('section',{className:'action-panel stack','aria-label':'所选对象操作'}),status=el('p',{role:'status'},'正在核对登记操作…'),body=el('div',{className:'action-controls'}),output=el('div',{className:'stack'});
 element.append(el('h4',{},'产物与任务操作'),status,body);
 let disposed=false,generation=0,busy=false,objectURL=null,controller=null,current=null;
 const alive=g=>!disposed&&g===generation&&isCurrent();
 const release=()=>{output.replaceChildren();controller?.abort();controller=null;if(objectURL){URL.revokeObjectURL(objectURL);objectURL=null;}};
 function taskButton(){body.append(el('button',{type:'button',onClick:()=>{if(!disposed&&isCurrent())onTask(projectId);}},'编写普通任务（显式保存/发送）'));}
 async function perform(action){if(busy||disposed||!isCurrent())return;busy=true;const g=++generation;release();status.textContent='正在'+(action.id==='verify_registered_artifact'?'只读核验…':'读取登记产物…');
  for(const b of body.querySelectorAll('button'))b.disabled=true;
  try{
   if(!['open_registered_artifact','verify_registered_artifact'].includes(action.id)||JSON.stringify(canonical(action))!==JSON.stringify(canonical(actionDescriptor(action.id,action.binding))))throw Error('ACTION_PORT_REJECTED');
   const {binding_sha256,...unsigned}=action.binding;if(await hashValue(unsigned)!==binding_sha256)throw Error('ACTION_BINDING_INVALID');if(!alive(g))return;
   if(action.id==='verify_registered_artifact'){
    const command=await actionRequest(action.id,action.binding);if(!alive(g))return;
    const result=await api('/api/actions/verify',command);if(!alive(g))return;
    if(result?.status!=='verified_read'||result.durable_receipt!==false||result.external_effect!==false||result.native_execution!==false||result.request_id!==command.request_id||result.input_sha256!==action.binding.artifact_sha256||result.parameter_sha256!==await hashValue(command)||result.binding_sha256!==action.binding.binding_sha256||!/^local-read-[a-f0-9]{32}$/.test(result.run_id||'')||!Number.isInteger(result.bytes_read)||result.bytes_read<0||result.bytes_read>maximumBytes)throw new Error('ACTION_RESULT_INVALID');
    if(JSON.stringify(canonical(result.frozen_input))!==JSON.stringify(canonical(action.binding))||JSON.stringify(canonical(result.tool))!==JSON.stringify(canonical(action.tool))||JSON.stringify(canonical(result.ports))!==JSON.stringify(canonical(action.ports))||result.declaration_sha256!==await hashValue(action))throw Error('ACTION_RESULT_INVALID');
    if(!alive(g))return;
    status.textContent='已完成本次本地只读字节核验；不是原生软件执行或持久完成回执。';
    const detail=el('details',{},el('summary',{},'核验依据'),el('pre',{},JSON.stringify(result,null,2)));output.append(detail);body.append(output);
   }else{
    controller=new AbortController();const response=await fetcher('/api/projects/'+encodeURIComponent(projectId)+'/actions/artifact?'+new URLSearchParams({binding:JSON.stringify(action.binding)}),{credentials:'same-origin',signal:controller.signal});
    if(!alive(g))return;if(!response.ok)throw Object.assign(new Error('ACTION_READ_FAILED'),{status:response.status});
    if(!['application/octet-stream','image/png','image/jpeg','image/gif','image/webp'].includes(response.headers.get('Content-Type')?.split(';')[0]))throw new Error('ACTION_MIME_REJECTED');
    const command=await actionRequest(action.id,action.binding);if(!alive(g))return;
    const blob=await boundedBlob(response);if(!alive(g))return;
    const receipt=await validateReadReceipt(response,command,blob);if(!alive(g))return;
    const filename=/^attachment; filename="([A-Za-z0-9][A-Za-z0-9._-]{0,199})"$|^inline; filename="([A-Za-z0-9][A-Za-z0-9._-]{0,199})"$/.exec(response.headers.get('Content-Disposition')||'');
    objectURL=URL.createObjectURL(blob);output.append(el('a',{className:'button',href:objectURL,download:filename?.[1]||filename?.[2]||'registered-artifact.bin'},'保存已读取产物'),el('details',{},el('summary',{},'读取依据'),el('pre',{},JSON.stringify({read_receipt:receipt,catalog_declaration:{tool:action.tool,ports:action.ports}},null,2))));body.append(output);status.textContent='已受控读取登记版本；可保存字节，不代表本机软件已打开。';
   }
  }catch(error){if(alive(g))status.textContent=errorText(error);}
  finally{if(alive(g)){busy=false;for(const b of body.querySelectorAll('button'))b.disabled=false;}}
 }
 function show(value){current=actionCatalog(value,projectId,projectionVersion);body.replaceChildren();const entries=selected?current.entries.filter(e=>sameKey(e.object,selected)):[];
  if(!entries.length){status.textContent='此对象未发布精确登记产物与候选绑定；没有文件或原生打开权限。';taskButton();return;}
  status.textContent='候选产物；读取、只读核验与任务表单相互独立。';
  for(const entry of entries){body.append(el('p',{className:'caption'},'候选修订 '+(entry.actions[0]?.binding.candidate_revision??'未知')));
   if(!entry.actions.length)body.append(el('p',{},'缺少精确候选绑定，不能读取。'));
   const read=entry.actions.find(a=>a.id==='open_registered_artifact'),verify=entry.actions.find(a=>a.id==='verify_registered_artifact'),native=entry.actions.find(a=>a.id==='native_program');
   if(read?.ports.inputs[0]?.type==='registered_artifact'&&read.ports.outputs[0]?.type==='bytes')body.append(el('p',{className:'caption'},'接受：精确版本的登记产物引用。将做什么：受控读取字节；PNG 等图片不是模型图像输入或参考。'));
   if(verify?.tool.capabilities.includes('sha256_verification'))body.append(el('p',{className:'caption'},'只读核验：读取登记字节并检查 SHA，不运行模型或外部软件。'));
   body.append(el('p',{className:'caption'},native?.tool.available===false?'原生运行：不可用。普通任务仅打开原有表单，仍需显式保存、提交与原有权限检查。':'原生运行：未发布受支持能力。'));

   for(const a of entry.actions){if(a.id==='prepare_ordinary_task')continue;
    if(!a.available){body.append(el('p',{className:'caption'},'原生软件动作未配置受支持的处理器与权限。'));continue;}
    body.append(el('button',{type:'button',onClick:()=>perform(a)},a.id==='open_registered_artifact'?'读取登记产物（受控下载）':'只读核验产物'));
   }
  }taskButton();
 }
 async function load(){const g=++generation;status.textContent='正在核对登记操作…';try{const value=catalog||await api('/api/projects/'+encodeURIComponent(projectId)+'/actions');if(alive(g))show(value);}catch(error){if(alive(g)){body.replaceChildren(el('button',{type:'button',onClick:load},'重新核对登记操作'));status.textContent=errorText(error);}}}
 load();return {element,dispose(){disposed=true;generation++;release();element.replaceChildren();},reload:load};
}
export function createArtifactDirectory({projectId,readAllowed=true,api=defaultApi,isCurrent=()=>true,onTask}={}){
 const element=el('section',{className:'panel stack','aria-label':'登记产物目录'},el('h2',{},'登记产物目录')),status=el('p',{role:'status'}),list=el('div',{className:'stack'});element.append(status,list);let disposed=false,generation=0,panels=[];
 const clear=()=>{panels.forEach(p=>p.dispose());panels=[];list.replaceChildren();};
 async function load(){if(disposed)return;const g=++generation;clear();if(!readAllowed){status.textContent='此项目当前不允许读取产物目录。';return;}status.textContent='正在读取登记目录…';
  try{const value=actionCatalog(await api('/api/projects/'+encodeURIComponent(projectId)+'/actions'),projectId);if(disposed||g!==generation||!isCurrent())return;
   status.textContent=value.entries.length?'同一目录快照中的登记候选；未登记的路径不会推断为产物。':'尚无可读取的登记产物。';
   for(const entry of value.entries){const panel=createActionPanel({projectId,selected:entry.object,catalog:value,api,isCurrent:()=>!disposed&&isCurrent(),onTask});panels.push(panel);list.append(el('article',{className:'action-artifact stack'},el('h3',{},entry.row.label),panel.element));}
  }catch(error){if(!disposed&&g===generation&&isCurrent())status.textContent=errorText(error);}
 }
 element.append(el('button',{type:'button',onClick:load},'重新读取登记目录'));load();return {element,dispose(){disposed=true;generation++;clear();element.replaceChildren();}};
}

const pchProject='personal-control-hub',pchPath='/api/projects/personal-control-hub/domain';
const headValid=h=>h&&Number.isSafeInteger(h.sequence)&&h.sequence>=0&&/^[a-f0-9]{64}$/.test(h.hash||'')&&Object.keys(h).length===2;
const sameHead=(a,b)=>headValid(a)&&headValid(b)&&a.sequence===b.sequence&&a.hash===b.hash;
export function pchRefreshCommand(value){
 if(!value||Object.keys(value).sort().join(',')!=='expected_head,project_ids,request_id'||!Array.isArray(value.project_ids)||value.project_ids.length!==1||value.project_ids[0]!==pchProject||!headValid(value.expected_head)||!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value.request_id||''))throw Error('PCH_REFRESH_SCOPE_INVALID');
 return structuredClone(value);
}
export function pchDomainCatalog(value){
 if(value?.schema_version!=='1.0'||value.project_id!==pchProject||value.route!=='route:personal-control-hub'||value.read_only!==true||value.native_execution_available!==false||!(value.head===null&&value.detail?.provenance?.ledger_head===null||sameHead(value.head,value.detail?.provenance?.ledger_head))||value.detail?.project_id!==pchProject||value.graph?.project_id!==pchProject||!Array.isArray(value.graph?.objects)||!Array.isArray(value.requests)||!value.versions)throw Error('PCH_DOMAIN_SNAPSHOT_INVALID');
 for(const o of value.graph.objects)if(o.data?.observation&&!sameHead(o.data.observation.head,value.head))throw Error('PCH_DOMAIN_SNAPSHOT_INVALID');
 if(value.artifacts===null){if(!['ACTION_PROJECT_DENIED','ACTION_CATALOG_UNAVAILABLE'].includes(value.artifact_gap))throw Error('PCH_DOMAIN_SNAPSHOT_INVALID');}else {
  actionCatalog(value.artifacts,pchProject,value.graph.projection_version);
  if(value.artifacts.source_version!==value.graph.source_version)throw Error('PCH_DOMAIN_SNAPSHOT_INVALID');
 }return value;
}
/** Self-only, explicit refresh. This component never consumes the global refresh command. */
export function createPCHDomainPanel({api=defaultApi,readAllowed=true,isCurrent=()=>true,onRefresh=()=>{},pendingCommand=null,
 storage,makeRequestId=()=>crypto.randomUUID(),onTask=()=>{location.hash=taskRoute(pchProject);}}={}){
 const element=el('section',{className:'panel stack','aria-label':'PCH来源与独立入口'},el('h2',{},'PCH来源与独立入口')),
 status=el('p',{role:'status'}),body=el('div',{className:'stack'}),controls=el('div',{className:'row'});
 let disposed=false,generation=0,busy=false,current=null,artifacts=null,pending=null,blocked=false,knownFinished=false,recoveryNotice='';
 const key='hub:pch-domain-refresh-pending';
 const showStatus=message=>{status.textContent=recoveryNotice||message;};
 const storageFailure=()=>{blocked=true;recoveryNotice='恢复记录无法持久保存或读取核对，已阻止刷新；原请求保留，只读目录与任务导航仍可用。';showStatus('');return false;};
 try{
  if(storage===undefined)storage=globalThis.localStorage;
  if(!storage||!['getItem','setItem','removeItem'].every(method=>typeof storage[method]==='function'))throw Error('PCH_RECOVERY_STORAGE_UNAVAILABLE');
  const raw=storage.getItem(key);if(raw!==null)pending=pchRefreshCommand(JSON.parse(raw));
 }catch{storageFailure();}
 const alive=g=>!disposed&&g===generation&&isCurrent();
 const exactStored=command=>JSON.stringify(canonical(pchRefreshCommand(JSON.parse(storage.getItem(key)))))===JSON.stringify(canonical(command));
 function save(command,create){
  pending=command;
  try{
   if(create){
    // Never overwrite a different pending request, including one from another panel.
    const raw=storage.getItem(key);if(raw!==null&&!exactStored(command))throw Error('PCH_RECOVERY_CONFLICT');
    storage.setItem(key,JSON.stringify(command));
   }
   if(!exactStored(command))throw Error('PCH_RECOVERY_NOT_SAVED');
   return true;
  }catch{return storageFailure();}
 }
 function finish(receipt){
  knownFinished=true;
  try{
   const raw=storage.getItem(key);
   if(raw!==null){if(!exactStored(pending))throw Error('PCH_RECOVERY_CONFLICT');storage.removeItem(key);}
   if(storage.getItem(key)!==null)throw Error('PCH_RECOVERY_NOT_CLEARED');
   pending=null;knownFinished=false;blocked=false;recoveryNotice='';return true;
  }catch{
   blocked=true;
   recoveryNotice=(Array.isArray(receipt.results)&&receipt.results.some(r=>!r.success)?'原PCH刷新已确认结束，但来源读取失败。':'原PCH刷新已确认结束。')+'恢复记录清理未确认；保留原请求，仅可重新读取原结果，不发出新刷新。';
   showStatus('');return false;
  }
 }
 const clear=()=>{artifacts?.dispose();artifacts=null;body.replaceChildren();};
 const reload=el('button',{type:'button',onClick:()=>load()},'重新读取PCH目录'),refresh=el('button',{type:'button',onClick:()=>perform()},'显式刷新PCH自身');
 controls.append(reload,refresh,el('button',{type:'button',onClick:()=>{if(!disposed&&isCurrent())onTask(pchProject);}},'编写PCH普通任务'));
 element.append(status,controls,body);
 function render(v){clear();current=v;
  body.append(el('p',{},'只读页面与登记产物可独立查看；普通任务仍需显式保存、提交与原有执行准入。'),
   el('a',{className:'button',href:taskRoute(pchProject)},'进入PCH任务表单'));
  const versions=el('section',{className:'stack'},el('h3',{},'版本依据'));
  versions.append(el('p',{},v.versions.checkout?.state==='observed'?'Checkout：受信观察，非安装证明。':'Checkout：未知，未提供受信观察。'),
   el('p',{},v.versions.source?.authority_drift?'来源：当前登记与历史结果有漂移。':'来源：与本次目录账本头核对。'),
   el('p',{},'网页资源：当前处理程序文件哈希；不证明冻结程序已安装。'),
   el('p',{},v.versions.runtime_manifest?.state==='unknown'?'冻结运行程序：未知；不影响独立只读页面。':'冻结运行程序：既有带时间的观察，不代表本次采用。'),
   el('details',{},el('summary',{},'版本核对细节'),el('pre',{},JSON.stringify(v.versions,null,2))));body.append(versions);
  const pages=v.graph.objects.filter(o=>o.type==='Artifact'&&o.data?.kind==='declared_page'),list=el('section',{className:'stack'},el('h3',{},'已发布页面'));
  if(!pages.length)list.append(el('p',{},'来源未发布确切页面；不按文件名推断可读产物。'));
  for(const page of pages)list.append(el('p',{},page.label+' · '+(page.source.applicability==='current'?'当前来源声明':'历史来源声明')+' · 字节可用性未知'));
  body.append(list,el('details',{},el('summary',{},'原刷新请求结果'),...v.requests.map(r=>el('p',{},r.request_id+' · '+r.status+' · '+(r.results.some(x=>!x.success)?'来源读取失败，旧成功仅为历史':'请按确切结果版本核对')))));
  if(v.artifacts===null){body.append(el('p',{className:'warning'},'登记产物目录当前不可用；不以历史产物推断当前可读。'));return;}
  artifacts=createArtifactDirectory({projectId:pchProject,isCurrent:()=>!disposed&&isCurrent(),onTask,
   api:(path,command)=>path==='/api/projects/'+pchProject+'/actions'&&!command?Promise.resolve(v.artifacts):api(path,command)});
  body.append(artifacts.element);
 }
 function buttons(){refresh.disabled=busy||!readAllowed||!current||(blocked&&!knownFinished)||!!pendingCommand;reload.disabled=busy;
  refresh.textContent=knownFinished?'重新读取原PCH刷新结果':pending?'核对并重试原PCH刷新':'显式刷新PCH自身';}
 async function load(){if(disposed||busy)return;const g=++generation;clear();current=null;
  if(!readAllowed){showStatus('当前未授权读取PCH来源。');buttons();return;}
  showStatus('正在只读核对PCH来源与版本…');buttons();
  try{const v=pchDomainCatalog(await api(pchPath));if(!alive(g))return;render(v);showStatus(pendingCommand?'另有原刷新命令待核对；此入口不改写或执行它，请使用原刷新恢复入口。':blocked?'原恢复记录无效，已阻止新刷新；请核对原请求。':pending?'保留原PCH刷新请求；先核对结果再重试。':'只读目录已核对；读取不刷新来源或启动Agent。');}
  catch(error){if(alive(g))showStatus(errorText(error)+' 可重新读取PCH目录。');}finally{if(alive(g))buttons();}
 }
 async function perform(){if(busy||!current||!readAllowed||pendingCommand||(blocked&&!knownFinished)||disposed||!isCurrent())return;
  const command=pending||pchRefreshCommand({request_id:makeRequestId(),project_ids:[pchProject],expected_head:current.refresh_head||current.head});
  if(!knownFinished&&!save(command,!pending)){buttons();return;}
  busy=true;const g=++generation;buttons();showStatus('正在核对原PCH刷新请求…');
  try{const receipt=await api(pchPath+'/request?'+new URLSearchParams({command:JSON.stringify(command)}));if(!alive(g))return;
   if(receipt.project_id!==pchProject||receipt.request_id!==command.request_id||receipt.command_sha256!==await hashValue(command)||!(receipt.head===null||headValid(receipt.head)))throw Error('PCH_REFRESH_RECEIPT_INVALID');if(!alive(g))return;
   if(receipt.status==='FINISHED'){if(!finish(receipt))return;busy=false;await load();if(!disposed&&isCurrent())onRefresh();return;}
   if(knownFinished||!['NOT_FOUND','OPEN'].includes(receipt.status))throw Error('PCH_REFRESH_RECEIPT_INVALID');
   // Recheck persistence after the asynchronous receipt/hash read, immediately before POST.
   if(!save(command,false))return;
   const result=await api(pchPath+'/refresh',command);if(!alive(g))return;
   if(JSON.stringify(canonical(result.command))!==JSON.stringify(canonical(command))||result.request_result?.request_id!==command.request_id||result.request_result?.status!=='FINISHED'||result.request_result?.command_sha256!==await hashValue(command)||!sameHead(result.request_result.head,result.catalog?.head))throw Error('PCH_REFRESH_RECEIPT_INVALID');if(!alive(g))return;
   const next=pchDomainCatalog(result.catalog);render(next);if(!finish(result.request_result))return;showStatus(result.request_result.results.some(r=>!r.success)?'本次PCH刷新已结束但来源失败；历史成功未替代当前失败。':'本次PCH刷新已结束，详情与图引用同一结果版本。');onRefresh();
  }catch(error){if(alive(g))showStatus(knownFinished?'原请求此前已确认结束；本次只读核对未确认，原请求保留，不发出新刷新。':'刷新结果未确认；原请求完整保留，请核对并重试原PCH刷新。');}
  finally{if(alive(g)){busy=false;buttons();}}
 }
 load();return {element,reload:load,dispose(){disposed=true;generation++;clear();element.replaceChildren();}};
}
