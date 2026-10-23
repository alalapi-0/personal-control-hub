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
export function actionDescriptor(id,b){if(!ids.includes(id))throw Error('ACTION_DESCRIPTOR_INVALID');const [kind,label,handler]=specs[id],task=id==='prepare_ordinary_task',native=id==='native_program';return {schema_version:'1.1',id,kind,label,handler,available:!native,reason:native?'NATIVE_HANDLER_UNAVAILABLE':null,binding:b,object_type:'Artifact',host:{origin:b.host,backend_instance:b.backend_instance},input_schema:inputSchema(id),required_capabilities:task?['existing_ordinary_task_form']:native?['native_handler_unavailable']:['registered_artifact_bounded_read'],authorization:{session:'permitted_session',owner_csrf:id==='verify_registered_artifact',current_project_binding:true,execution_permission:false,deferred_task_authorization:task?'existing_owner_csrf_grant_cas_budget_executor_gates':null},effect_class:task?'task_form_navigation':native?'unavailable':'local_read_only',idempotency:{mode:task?'existing_ordinary_api_requests':native?'unavailable':'content_addressed_revalidation',durable_receipt:false},reconcile:{method:task?'existing_ordinary_context_draft_submit':native?'unavailable':'revalidate_exact_binding',auto_retry:false,execution_deferred:task}};}
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
   if(!validBinding(b)||!ids.includes(action.id)||seen.has(action.id)||action.schema_version!=='1.1'||JSON.stringify(canonical(action))!==JSON.stringify(canonical(actionDescriptor(action.id,b)))||typeof action.available!=='boolean'||!b||Object.keys(b).length!==fields.length||!fields.every(k=>Object.hasOwn(b,k))||b.project_id!==projectId||b.object_id!==entry.object.id||b.object_revision!==entry.object.revision||b.host!==value.host||b.backend_instance!==value.backend_instance||b.source_version!==value.source_version)throw new Error('ACTION_BINDING_INVALID');
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
   const {binding_sha256,...unsigned}=action.binding;if(await hashValue(unsigned)!==binding_sha256)throw Error('ACTION_BINDING_INVALID');if(!alive(g))return;
   if(action.id==='verify_registered_artifact'){
    const command=await actionRequest(action.id,action.binding);if(!alive(g))return;
    const result=await api('/api/actions/verify',command);if(!alive(g))return;
    if(result?.status!=='verified_read'||result.durable_receipt!==false||result.external_effect!==false||result.native_execution!==false||result.request_id!==command.request_id||result.input_sha256!==action.binding.artifact_sha256||result.parameter_sha256!==await hashValue(command)||result.binding_sha256!==action.binding.binding_sha256||!/^local-read-[a-f0-9]{32}$/.test(result.run_id||'')||!Number.isInteger(result.bytes_read)||result.bytes_read<0||result.bytes_read>maximumBytes)throw new Error('ACTION_RESULT_INVALID');
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
    objectURL=URL.createObjectURL(blob);output.append(el('a',{className:'button',href:objectURL,download:filename?.[1]||filename?.[2]||'registered-artifact.bin'},'保存已读取产物'),el('details',{},el('summary',{},'读取依据'),el('pre',{},JSON.stringify(receipt,null,2))));body.append(output);status.textContent='已受控读取登记版本；可保存字节，不代表本机软件已打开。';
   }
  }catch(error){if(alive(g))status.textContent=errorText(error);}
  finally{if(alive(g)){busy=false;for(const b of body.querySelectorAll('button'))b.disabled=false;}}
 }
 function show(value){current=actionCatalog(value,projectId,projectionVersion);body.replaceChildren();const entries=selected?current.entries.filter(e=>sameKey(e.object,selected)):[];
  if(!entries.length){status.textContent='此对象未发布精确登记产物与候选绑定；没有文件或原生打开权限。';taskButton();return;}
  status.textContent='候选产物；读取、只读核验与任务表单相互独立。';
  for(const entry of entries){body.append(el('p',{className:'caption'},'候选修订 '+(entry.actions[0]?.binding.candidate_revision??'未知')));
   if(!entry.actions.length)body.append(el('p',{},'缺少精确候选绑定，不能读取。'));
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
