import {el,button,api} from './common.js';

const exact=(v,keys)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join('|')===[...keys].sort().join('|');
const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
export function boundedRect(r){return exact(r,['x1','y1','x2','y2'])&&Object.values(r).every(v=>Number.isFinite(v)&&v>=0&&v<=1)&&r.x1<r.x2&&r.y1<r.y2;}
export function validDescriptor(d){
  if(!exact(d,['available','classification','bridge_id','bridge_version','manifest_hash','binding_hash','origin','frame_url','nonce','load_id','ttl_seconds','elements']))return false;
  let u;try{u=new URL(d.origin);}catch{return false;}
  return d.available===true&&d.classification==='synthetic_fixture'&&d.bridge_id==='owned-fixture'&&d.bridge_version===1
    &&u.protocol==='http:'&&u.hostname==='localhost'&&u.port&&u.origin===d.origin&&d.frame_url===d.origin+'/fixture'
    &&hash(d.manifest_hash)&&hash(d.binding_hash)&&hash(d.nonce)&&/^load-[a-f0-9]{32}$/.test(d.load_id)
    &&Number.isInteger(d.ttl_seconds)&&d.ttl_seconds>0&&d.ttl_seconds<=300
    &&Array.isArray(d.elements)&&d.elements.length>0&&d.elements.length<=8
    &&d.elements.every(e=>exact(e,['id','role'])&&/^[a-z0-9-]{1,64}$/.test(e.id)&&['button','article','heading'].includes(e.role))
    &&new Set(d.elements.map(e=>e.id)).size===d.elements.length;
}

// A stateful gate: rejected or out-of-order messages never become selection data.
export function messageGate(d,source){
  let phase='loading',sequence=0;
  return {close(){phase='closed';},accept(event){
    if(phase==='closed'||event.origin!==d.origin||event.source!==source)return null;
    const m=event.data;
    if(!exact(m,['schema','type','nonce','bridge_version','manifest_hash','binding_hash','load_id','sequence','selection'])
      ||m.schema!==1||m.nonce!==d.nonce||m.bridge_version!==d.bridge_version||m.manifest_hash!==d.manifest_hash
      ||m.binding_hash!==d.binding_hash||m.load_id!==d.load_id||!Number.isInteger(m.sequence))return null;
    if(phase==='loading'&&m.type==='ready'&&m.sequence===0&&m.selection===null){phase='ready';return {type:'ready'};}
    if(phase!=='ready'||m.type!=='selection'||m.sequence!==sequence+1)return null;
    const s=m.selection;
    if(!exact(s,['id','role','rect'])||!boundedRect(s.rect)||!d.elements.some(e=>e.id===s.id&&e.role===s.role))return null;
    sequence=m.sequence;
    return {type:'selection',hint:{bridge_id:d.bridge_id,bridge_version:d.bridge_version,manifest_hash:d.manifest_hash,
      binding_hash:d.binding_hash,load_id:d.load_id,element_id:s.id,role:s.role,rect:{...s.rect},source_mapping:'unknown'}};
  }};
}

export async function mountElementBridge(target,previewId,{isOwner,onSelect,onFallback}){
  const status=el('p',{role:'status',className:'caption'},'正在核对受控元素预览…');
  target.append(status);let d,frame,gate,timer,expiry,active=false,ready=false,loads=0,commandSequence=0,closed=false;
  const choose=button('开始元素批注',()=>{
    if(!ready||!isOwner())return;active=!active;choose.textContent=active?'返回页面浏览':'开始元素批注';
    frame.contentWindow.postMessage({schema:1,type:'mode',nonce:d.nonce,load_id:d.load_id,sequence:++commandSequence,mode:active?'annotate':'browse'},d.origin);
    if(active){status.textContent='元素批注中 · 源码位置未知；底层控件不会执行。';frame.focus();}
    else status.textContent='受控隔离页面浏览 · 仅测试数据';
  },{disabled:true});
  target.append(choose);
  function fail(reason){
    if(closed)return;closed=true;active=ready=false;gate?.close();clearTimeout(timer);clearTimeout(expiry);
    window.removeEventListener('message',receive);window.removeEventListener('hub:identity',identity);
    frame?.remove();choose.disabled=true;status.textContent=reason+'；可继续使用注册图片的整版或区域意见。';onFallback();
  }
  function receive(event){
    if(!target.isConnected){fail('受控预览已关闭');return;}
    const result=gate?.accept(event);
    if(!result){if(event.origin===d?.origin&&event.source===frame?.contentWindow)fail('元素桥的版本或消息无效');return;}
    if(result.type==='ready'){ready=true;clearTimeout(timer);choose.disabled=!isOwner();status.textContent='受控隔离页面就绪 · 源码位置未知';}
    else if(active&&isOwner())onSelect(result.hint);
  }
  function identity(){if(!isOwner())fail('所有者连接已失效');}
  try{
    if(!isOwner()){fail('连接所有者后才能读取元素预览');return {close:()=>fail('已关闭')};}
    d=await api('/api/preview-bridge/'+encodeURIComponent(previewId));
    if(!target.isConnected)return {close:()=>{}};
    if(!validDescriptor(d)){fail('此预览未启用受控元素桥');return {close:()=>fail('已关闭')};}
    frame=el('iframe',{title:'受控隔离元素预览',src:d.frame_url,sandbox:'allow-scripts allow-same-origin',referrerPolicy:'no-referrer',className:'controlled-preview'});
    gate=messageGate(d,frame.contentWindow); // Set again after insertion, when browsing context exists.
    frame.addEventListener('load',()=>{
      if(++loads!==1){fail('预览发生了重新加载或导航');return;}
      gate=messageGate(d,frame.contentWindow);
      frame.contentWindow.postMessage({schema:1,type:'init',nonce:d.nonce,bridge_version:d.bridge_version,
        manifest_hash:d.manifest_hash,binding_hash:d.binding_hash,load_id:d.load_id},d.origin);
    });
    frame.addEventListener('error',()=>fail('元素预览载入失败'));
    window.addEventListener('message',receive);window.addEventListener('hub:identity',identity);
    target.append(frame);timer=setTimeout(()=>fail('元素桥未响应或被页面策略阻止'),3000);
    expiry=setTimeout(()=>fail('元素预览版本需要重新核对'),d.ttl_seconds*1000);
  }catch{fail('元素预览暂时不可用');}
  return {close:()=>fail('受控预览已关闭'),leave(){if(active&&ready){active=false;choose.textContent='开始元素批注';frame.contentWindow.postMessage({schema:1,type:'mode',nonce:d.nonce,load_id:d.load_id,sequence:++commandSequence,mode:'browse'},d.origin);}}};
}
