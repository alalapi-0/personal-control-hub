// Fixed disposable fixture only; no import, network, form or executable message input.
const parentOrigin=document.body.dataset.parentOrigin;
const exact=(v,keys)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join('|')===[...keys].sort().join('|');
let config=null,active=false,sequence=0,commandSequence=0;
function emit(type,selection=null){parent.postMessage({schema:1,type,nonce:config.nonce,bridge_version:config.bridge_version,
  manifest_hash:config.manifest_hash,binding_hash:config.binding_hash,load_id:config.load_id,sequence:sequence++,selection},parentOrigin);}
window.addEventListener('message',event=>{
  if(event.origin!==parentOrigin||event.source!==parent)return;
  const m=event.data;
  if(!config&&exact(m,['schema','type','nonce','bridge_version','manifest_hash','binding_hash','load_id'])&&m.schema===1&&m.type==='init'
    &&m.bridge_version===1&&[m.nonce,m.manifest_hash,m.binding_hash].every(v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v))&&/^load-[a-f0-9]{32}$/.test(m.load_id)){
    config={...m};emit('ready');return;
  }
  if(config&&exact(m,['schema','type','nonce','load_id','sequence','mode'])&&m.schema===1&&m.type==='mode'
    &&m.nonce===config.nonce&&m.load_id===config.load_id&&m.sequence===commandSequence+1&&['annotate','browse'].includes(m.mode)){
    commandSequence=m.sequence;active=m.mode==='annotate';document.body.dataset.annotating=String(active);
  }
});
function intercept(event){
  if(!active)return;
  if(event.type==='keydown'&&!['Enter',' ','Escape'].includes(event.key))return;
  event.preventDefault();event.stopImmediatePropagation();
  if(event.type!=='pointerdown'&&!(event.type==='keydown'&&['Enter',' '].includes(event.key)))return;
  const target=event.target.closest?.('[data-hint-id]');
  if(!target||!['sample-card','sample-heading'].includes(target.dataset.hintId))return;
  const r=target.getBoundingClientRect(),w=document.documentElement.clientWidth,h=document.documentElement.clientHeight;
  if(!w||!h||r.left<0||r.top<0||r.right>w||r.bottom>h)return;
  emit('selection',{id:target.dataset.hintId,role:target.dataset.hintRole,rect:{x1:r.left/w,y1:r.top/h,x2:r.right/w,y2:r.bottom/h}});
}
for(const type of ['pointerdown','pointerup','click','dblclick','keydown'])document.addEventListener(type,intercept,true);
if(document.body.dataset.mode==='navigation')setTimeout(()=>location.reload(),600);
document.getElementById('sample-card').addEventListener('click',()=>{
  const counter=document.getElementById('activation-count');counter.textContent=String(Number(counter.textContent)+1);
});
