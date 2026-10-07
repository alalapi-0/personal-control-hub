import {el,button,api,navigate,notify} from './common.js';
import {sendAnnotation} from './tasks.js';
import {mountElementBridge} from './preview_bridge.js';
import {mountLivePreview} from './live_preview.js';

export function normalizedPoint(rect,x,y){
  if(!rect||rect.width<=0||rect.height<=0||![x,y,rect.left,rect.top,rect.width,rect.height].every(Number.isFinite))return null;
  const point={x:(x-rect.left)/rect.width,y:(y-rect.top)/rect.height};
  return point.x<0||point.x>1||point.y<0||point.y>1?null:point;
}
export function rectangle(a,b){
  if(!a||!b)return null;
  const r={x1:Math.min(a.x,b.x),y1:Math.min(a.y,b.y),x2:Math.max(a.x,b.x),y2:Math.max(a.y,b.y)};
  return Object.values(r).every(v=>Number.isFinite(v)&&v>=0&&v<=1)&&r.x1<r.x2&&r.y1<r.y2?r:null;
}
const stable=value=>JSON.stringify(value&&typeof value==='object'?Array.isArray(value)?value.map(v=>JSON.parse(stable(v))):Object.fromEntries(Object.keys(value).sort().map(k=>[k,JSON.parse(stable(value[k]))])):value);
export const sameBinding=(a,b)=>stable(a)===stable(b);
export function validPending(value){
  return value&&typeof value.request_id==='string'&&/^annotation-[a-zA-Z0-9-]{1,100}$/.test(value.request_id)
    &&Number.isInteger(value.expected_revision)&&value.expected_revision>=0
    &&value.binding&&typeof value.binding.preview_id==='string'
    &&['whole','region','element'].includes(value.kind)&&typeof value.requested_change==='string'&&value.requested_change.length<=2000
    &&typeof value.preserve_scope==='string'&&value.preserve_scope.length<=2000&&value.view?value:null;
}
function readLocal(key){try{return JSON.parse(localStorage.getItem(key)||'null');}catch{return null;}}
function writeLocal(key,value){try{value===null?localStorage.removeItem(key):localStorage.setItem(key,JSON.stringify(value));return true;}catch{return false;}}
function unavailableHistory(root,annotations,previewId){
  const rows=annotations.records.filter(r=>!previewId||r.record.annotation.binding.preview_id===previewId);
  if(!rows.length)return;
  const panel=el('section',{className:'panel stack'},el('h2',{},'历史意见 · 当前素材不可用'),
    el('p',{className:'warning'},'原记录保留供对账；请核对新版本后重新批注，当前记录不能发送执行。'));
  for(const row of rows){const a=row.record.annotation;
    panel.append(el('article',{className:'stack'},el('p',{className:'caption'},`${row.request_id} · revision ${row.revision} · ${a.binding.project_id}`),
      el('p',{},a.requested_change),el('p',{},'保留范围：'+a.preserve_scope)));
  }root.append(panel);
}
const labels={real:'登记实物',mock:'模拟方案','dry-run':'试演方案',imported:'导入方案'};

export async function renderWorkbench(target,{previewId=null,isCurrent=()=>true}={}){
  const [catalog,annotations,identity]=await Promise.all([api('/api/previews'),api('/api/annotations'),api('/api/identity')]);
  if(!isCurrent())return;
  const root=el('section',{className:'stack'},el('h1',{},'可视化工作台'),
    el('p',{className:'muted'},'查看注册方案图，记录整版或区域意见。修改要求和保留范围会保存到 Linux；执行入口尚未开放。'));
  const reload=button('重新核对版本',async()=>{reload.disabled=true;try{await renderWorkbench(target,{previewId,isCurrent});if(isCurrent())target.querySelector('button')?.focus();}catch{notify('无法核对版本；当前意见已保留。','warning');}finally{reload.disabled=false;}});
  root.append(el('a',{href:'#tasks'},'查看任务与结果'),reload);target.replaceChildren(root);
  if(catalog.workflow_classification==='synthetic_fixture')root.append(el('p',{className:'warning'},'隔离 Hub 验证：共享意见仅保存技术测试记录，不是真实用户批注或设计决定。'));
  if(!catalog.previews.length){root.append(el('div',{className:'empty'},el('h2',{},'尚无可用注册图片'),el('p',{},'需要有效项目、当前候选版本及安全图片工件。请核对登记与授权。')));unavailableHistory(root,annotations,previewId);return;}
  if(!previewId){
    const cards=el('div',{className:'host-grid'});
    for(const preview of catalog.previews){const b=preview.binding;
      cards.append(el('article',{className:'panel stack'},el('h2',{},preview.project_name),
        el('p',{},`${labels[b.classification]||'注册方案'} · ${b.candidate_id} · v${b.candidate_revision}`),
        el('p',{className:'caption'},preview.external_code_version?'真实代码截图 · '+preview.external_code_version.slice(0,12)+' · 只读夹具':'注册图片，外部源码版本未登记'),
        el('a',{className:'button',href:'#workbench/'+encodeURIComponent(b.preview_id)},'查看并批注')));
    }
    root.append(cards);return;
  }
  const preview=catalog.previews.find(p=>p.binding.preview_id===previewId);
  if(!preview){root.append(el('p',{className:'warning'},'该预览已不可用，请核对当前目录、版本与授权。'));unavailableHistory(root,annotations,previewId);return;}
  const binding=preview.binding;
  const draftKey=`hub:annotation-draft:${previewId}:${binding.candidate_hash}:${binding.artifact_sha256}:${binding.registry_hash}`;
  const pendingKey=`hub:annotation-pending:${previewId}`;
  let pending=validPending(readLocal(pendingKey)),revision=annotations.revision,owner=identity.owner_authenticated===true;
  let loaded=false,mode='browse',region=null,start=null,canRetry=false,elementHint=null,designReference=null,bridge=null,historyData=annotations,live=null;
  const draft=readLocal(draftKey);
  const image=el('img',{src:preview.image_url,alt:`${preview.project_name}的注册方案图`,draggable:false,referrerPolicy:'no-referrer'});
  const overlay=el('div',{className:'annotation-overlay',tabIndex:0,role:'group','aria-label':'区域批注层。空格创建选区，方向键移动，Shift 加方向键调整大小，Escape 退出。',hidden:true});
  const box=el('div',{className:'annotation-box',hidden:true});overlay.append(box);
  const stage=el('div',{className:'image-stage'},image,overlay);
  const message=el('p',{role:'status','aria-live':'polite',className:'caption'});
  const change=el('textarea',{id:'annotation-change',maxLength:2000,rows:5,required:true});
  const preserve=el('textarea',{id:'annotation-preserve',maxLength:2000,rows:4,required:true});
  const kind=el('select',{id:'annotation-kind'},el('option',{value:'whole'},'整版意见'),el('option',{value:'region'},'区域意见'));
  if(draft&&sameBinding(draft.binding,binding)){
    if(typeof draft.requested_change==='string'&&draft.requested_change.length<=2000)change.value=draft.requested_change;
    if(typeof draft.preserve_scope==='string'&&draft.preserve_scope.length<=2000)preserve.value=draft.preserve_scope;
    if(['whole','region'].includes(draft.kind))kind.value=draft.kind;
    if(draft.region)region=rectangle({x:draft.region.x1,y:draft.region.y1},{x:draft.region.x2,y:draft.region.y2});
  }
  const modeButton=button('开始区域批注',()=>setMode(mode==='browse'?'annotate':'browse'));
  const reset=button('清除选区',()=>{region=start=null;paint();saveDraft();if(mode==='annotate')overlay.focus();});
  const save=button(pending?'核对原请求':'保存到 Linux',()=>pending?reconcile():submit(),{className:'primary'});
  const retry=button('重试原请求',()=>submit(pending),{hidden:true});
  const shared=el('section',{className:'panel stack'},el('h2',{},'Linux 共享意见'));
  const elementPanel=el('section',{className:'panel stack'},el('h2',{},preview.capture?'只读页面与截图批注':'受控元素提示'),
    el('p',{className:'caption'},preview.capture?'区域批注使用固定截图；没有元素语义或源码位置映射。':'元素位置只作为修改线索；源码映射未知。此测试预览不提供执行权限。'));
  const designPanel=el('section',{className:'panel stack'},el('h2',{},preview.decision_eligible===false?'当前代码截图':'整版设计决定'));
  const design=preview.design_feedback;
  if(draft?.design_reference&&design&&sameBinding(draft.design_reference,design.reference))designReference={...design.reference};
  if(preview.decision_eligible!==false)designPanel.append(el('a',{href:'#designs/'+encodeURIComponent(binding.project_id)+'/'+encodeURIComponent(binding.candidate_id)},'比较并记录整版设计决定'));
  if(design){
    designPanel.append(el('p',{},'当前：'+({select:'已选择',request_changes:'已请求修改',defer:'已暂缓'}[design.action]||'待核对')),
      el('p',{},design.feedback||'此决定未附文字反馈'),button('将此决定与反馈带入意见',()=>{
        if(pending)return;designReference={...design.reference};kind.value='whole';region=elementHint=null;mode='browse';bridge?.leave();
        if(!change.value.trim()&&design.feedback)change.value=design.feedback;
        paint();controls();saveDraft();message.textContent='已关联当前整版决定；请补充修改要求与不改范围，再保存意见。';change.focus();
      }));
  }else designPanel.append(el('p',{className:'caption'},preview.decision_eligible===false?'代码截图登记不构成设计选择；可以记录整版或区域修改意见。':'尚无当前整版决定；可以先保存独立意见。'));
  const form=el('section',{className:'panel stack'},el('h2',{},'修改意见'),
    el('label',{className:'field',htmlFor:'annotation-kind'},'意见范围',kind),
    el('div',{className:'row'},modeButton,reset),
    el('p',{className:'caption'},'区域模式只操作批注层。空格创建选区，方向键移动，Shift 调整大小，Esc 退出。'),
    el('label',{className:'field',htmlFor:'annotation-change'},'希望改成什么',change),
    el('label',{className:'field',htmlFor:'annotation-preserve'},'哪些范围不改',preserve),message,
    el('div',{className:'row'},save,retry),el('p',{className:'caption'},'保存意见不授予子项目写入权限，也不代表接受设计。'));
  root.append(el('a',{href:'#workbench'},'← 所有方案'),el('h2',{},preview.project_name),
    el('p',{className:'caption'},`${labels[binding.classification]} · 注册方案图 · ${binding.candidate_id} v${binding.candidate_revision} · 页面范围 ${binding.pages.join('、')}`),
    el('details',{className:'artifact-provenance'},el('summary',{},'版本与来源'),
      el('p',{className:'caption'},'图片摘要：'+binding.artifact_sha256),
      el('p',{className:'caption'},preview.external_code_version?'源码版本：'+preview.external_code_version+' · 资产摘要：'+preview.capture.manifest_hash+' · 来源清单观测时间：'+preview.capture.manifest_observed_at:'外部源码版本尚未登记；工件视图不提供源码位置。')),
    preview.capture?el('p',{className:'warning'},'当前项目代码的固定截图 · 只读夹具业务回答。区域坐标绑定此截图；实时页面滚动与元素、源码位置不在批注范围内。'):catalog.classification==='synthetic_fixture'?el('p',{className:'warning'},'隔离测试数据；不是业务活动或真实用户验收。'):el('p',{className:'caption'},'图像来自登记工件；不是当前外部网页的实时捕获。'),
    designPanel,el('div',{className:'workbench-grid'},el('div',{className:'stack'},stage,elementPanel,shared),form));

  function controls(){
    modeButton.disabled=!loaded||Boolean(pending);reset.disabled=!loaded||Boolean(pending);
    save.disabled=Boolean(pending)?false:!owner||!loaded||(kind.value==='element'&&!elementHint);
    retry.hidden=!pending||!canRetry;retry.disabled=!owner;
    for(const field of [kind,change,preserve])field.disabled=Boolean(pending);
  }
  function paint(){
    box.hidden=!region;
    if(region){box.style.left=region.x1*100+'%';box.style.top=region.y1*100+'%';box.style.width=(region.x2-region.x1)*100+'%';box.style.height=(region.y2-region.y1)*100+'%';}
    overlay.hidden=mode!=='annotate';modeButton.textContent=mode==='annotate'?'返回浏览':'开始区域批注';
  }
  function setMode(next){
    if(!loaded)return;mode=next;live?.setMode(next);start=null;if(next==='annotate'){kind.value='region';elementHint=null;bridge?.leave();overlay.hidden=false;overlay.focus();}else modeButton.focus();paint();controls();saveDraft();
  }
  function saveDraft(){
    const value={binding,kind:kind.value,region,requested_change:change.value,preserve_scope:preserve.value,design_reference:designReference};
    const durable=writeLocal(draftKey,value);
    message.textContent=durable?'本地草稿 · 尚未保存到 Linux':'当前浏览器内存草稿 · 本地存储不可用';
  }
  function view(){
    const r=image.getBoundingClientRect();
    return {viewport_width:window.innerWidth,viewport_height:window.innerHeight,dpr:window.devicePixelRatio,
      scroll_x:window.scrollX,scroll_y:window.scrollY,zoom:window.visualViewport?.scale||1,
      image_width:r.width,image_height:r.height,natural_width:image.naturalWidth,natural_height:image.naturalHeight,
      image_left:r.left,image_top:r.top,fit:'image-content-box'};
  }
  function showHistory(data){
    historyData=data;
    shared.replaceChildren(el('h2',{},'Linux 共享意见'));
    const rows=data.records.filter(r=>r.record.annotation.binding.preview_id===previewId);
    if(!rows.length)shared.append(el('p',{className:'muted'},'尚无 Linux 共享意见。'));
    for(const row of rows){const a=row.record.annotation;
      shared.append(el('article',{className:'stack'},el('p',{className:row.material_state==='stale'?'warning':'caption'},`${row.request_id} · revision ${row.revision} · ${row.material_state==='stale'?'素材或关联版本已变化':'当前注册版本'} · ${{whole:'整版',region:'区域',element:'元素'}[a.kind]}意见`),
        el('p',{},a.requested_change),el('p',{},'保留范围：'+a.preserve_scope),
        a.design_reference?el('p',{className:'caption'},'关联整版决定：'+({select:'选择',request_changes:'请求修改',defer:'暂缓'}[a.design_reference.action]||'待核对')):null,
        a.element_hint?el('p',{className:'caption'},'元素：'+a.element_hint.element_id+' · 源码映射未知'):null,
        button('发送给 Codex',()=>sendAnnotation(row.request_id,message),{disabled:!owner||row.material_state==='stale'})));
    }
  }
  async function accepted(result){
    if(!pending||!sameBinding(result.receipt?.command,pending))throw new Error('RECEIPT_MISMATCH');
    const id=pending.request_id;revision=result.receipt.revision;pending=null;canRetry=false;
    writeLocal(pendingKey,null);writeLocal(draftKey,null);controls();
    message.textContent=`Linux 共享已保存 · ${id} · revision ${revision}`;save.textContent='保存到 Linux';
    try{const data=await api('/api/annotations');if(root.isConnected){revision=data.revision;showHistory(data);}}catch{message.textContent+='；历史列表待重连读取。';}
  }
  async function reconcile(){
    if(!pending)return;save.disabled=true;
    try{const result=await api('/api/annotations/requests/'+encodeURIComponent(pending.request_id));await accepted(result);}
    catch(error){canRetry=error.status===404;message.textContent=canRetry?'尚未找到原请求回执。可继续核对，或以同一 ID 重试原请求。':'无法确认原请求结果；意见已保留，请重连后核对。';}
    finally{if(root.isConnected){controls();save.focus();}}
  }
  async function submit(original=null){
    if(!owner||!loaded||!root.isConnected)return;
    if(!original){
      if(!change.reportValidity()||!preserve.reportValidity())return;
      if(!change.value.trim()||!preserve.value.trim()){message.textContent='请填写修改要求与保留范围。';return;}
      if(kind.value==='region'&&!region){message.textContent='请先在图片中选择一个有效区域。';overlay.focus();return;}
      if(kind.value==='element'&&!elementHint){message.textContent='请重新选择受控元素，或改用图片区域。';return;}
      pending={request_id:'annotation-'+crypto.randomUUID(),expected_revision:revision,binding,
        kind:kind.value,region:kind.value==='region'?region:null,requested_change:change.value,preserve_scope:preserve.value,view:view()};
      if(designReference)pending.design_reference={...designReference};
      if(kind.value==='element')pending.element_hint=elementHint;
      if(!writeLocal(pendingKey,pending))message.textContent='原请求只保留在当前内存；请记下请求 ID：'+pending.request_id;
    }
    if(!sameBinding(pending.binding,binding)){message.textContent='原请求属于旧素材版本，只能核对回执；不能发送到当前版本。';return;}
    controls();save.disabled=true;retry.disabled=true;message.textContent='正在保存 · '+pending.request_id;
    try{await accepted(await api('/api/annotations',pending));}
    catch(error){
      if(error.outcome==='NOT_COMMITTED'){
        pending=null;writeLocal(pendingKey,null);save.textContent='保存到 Linux';
        if(['BRIDGE_LOAD_STALE','BRIDGE_VERSION_STALE','BRIDGE_DISABLED'].includes(error.code))bridge?.close();
        message.textContent=({PREVIEW_STALE:'素材版本已变化；草稿保留，请重新核对版本。',DESIGN_REFERENCE_STALE:'整版决定已变化；草稿保留，请重新核对。',BRIDGE_LOAD_STALE:'元素预览已变化；请重新载入并选择元素。',ANNOTATION_REVISION_CONFLICT:'其他意见已先保存；草稿保留，请核对 Linux 共享记录。',OWNER_AUTH_REQUIRED:'请先连接所有者。',OWNER_AUTH_UNAVAILABLE:'后台所有者身份尚未配置。'})[error.code]||'未保存；草稿保留，请核对连接与当前版本。';
      }else{save.textContent='核对原请求';message.textContent='保存结果待核对 · '+pending.request_id+'。不会自动重放。';}
    }finally{if(root.isConnected){controls();save.focus();}}
  }
  image.addEventListener('load',()=>{loaded=image.naturalWidth>0&&image.naturalHeight>0;paint();controls();if(!pending)message.textContent=owner?'本地草稿 · 尚未保存到 Linux':'可准备本地草稿；保存前请连接所有者。';});
  image.addEventListener('error',()=>{loaded=false;region=start=null;mode='browse';paint();controls();message.textContent='图片不可用或摘要不匹配，区域批注已停用。';});
  overlay.addEventListener('pointerdown',event=>{if(mode!=='annotate'||!loaded||pending)return;event.preventDefault();event.stopPropagation();start=normalizedPoint(image.getBoundingClientRect(),event.clientX,event.clientY);region=null;overlay.setPointerCapture(event.pointerId);paint();});
  overlay.addEventListener('pointermove',event=>{if(!start)return;event.preventDefault();event.stopPropagation();region=rectangle(start,normalizedPoint(image.getBoundingClientRect(),event.clientX,event.clientY));paint();});
  overlay.addEventListener('pointerup',event=>{if(!start)return;event.preventDefault();event.stopPropagation();region=rectangle(start,normalizedPoint(image.getBoundingClientRect(),event.clientX,event.clientY));start=null;paint();saveDraft();});
  overlay.addEventListener('pointercancel',()=>{start=region=null;paint();saveDraft();});
  overlay.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();});
  overlay.addEventListener('keydown',event=>{
    if(event.key==='Escape'){event.preventDefault();region=null;setMode('browse');return;}
    if(event.key===' '){event.preventDefault();region={x1:.25,y1:.25,x2:.5,y2:.5};paint();saveDraft();return;}
    const direction={ArrowLeft:[-.01,0],ArrowRight:[.01,0],ArrowUp:[0,-.01],ArrowDown:[0,.01]}[event.key];
    if(!direction||!region)return;event.preventDefault();
    const [dx,dy]=direction,r=region;
    const next=event.shiftKey?{...r,x2:r.x2+dx,y2:r.y2+dy}:{x1:r.x1+dx,x2:r.x2+dx,y1:r.y1+dy,y2:r.y2+dy};
    const candidate=rectangle({x:next.x1,y:next.y1},{x:next.x2,y:next.y2});
    if(candidate){region=candidate;paint();saveDraft();}
  });
  for(const field of [change,preserve])field.addEventListener('input',saveDraft);
  kind.addEventListener('change',()=>{if(kind.value!=='element'){elementHint=null;bridge?.leave();}if(kind.value==='whole'){mode='browse';start=null;paint();}live?.setMode(kind.value==='region'?'annotate':'browse');controls();saveDraft();});
  function onIdentity(event){if(!root.isConnected){live?.close();window.removeEventListener('hub:identity',onIdentity);return;}owner=event.detail?.owner_authenticated===true;if(!owner)live?.close();controls();showHistory(historyData);}
  window.addEventListener('hub:identity',onIdentity);
  showHistory(annotations);paint();controls();
  if(pending){message.textContent='发现原请求 · '+pending.request_id+'；请先核对结果。';save.textContent='核对原请求';}
  if(image.complete&&image.naturalWidth){loaded=true;controls();}
  if(preview.capture){live=mountLivePreview(elementPanel,previewId,{isOwner:()=>owner&&!pending,isCurrent});}
  else bridge=await mountElementBridge(elementPanel,previewId,{isOwner:()=>owner&&!pending,onSelect:hint=>{
    if(pending||!loaded)return;elementHint=hint;region=start=null;mode='browse';
    if(![...kind.options].some(o=>o.value==='element'))kind.append(el('option',{value:'element'},'元素意见'));
    kind.value='element';paint();controls();message.textContent='已选择元素 '+hint.element_id+' · 源码映射未知。请填写意见再保存。';
  },onFallback:()=>{elementHint=null;[...kind.options].find(o=>o.value==='element')?.remove();
    if(!pending&&kind.value==='element')kind.value='whole';controls();}});
}
