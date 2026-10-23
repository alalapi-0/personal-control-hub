import test from 'node:test';
import assert from 'node:assert/strict';
import {renderMaterials,materialLink,materialUrl} from '../src/hub/web/materials.js';

// Same lightweight DOM seam as the existing component tests; no HTTP/browser.
class N{
 constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this.events={};this._text='';this.parentNode=null;}
 appendChild(n){this.children.push(n);n.parentNode=this;return n;}append(...ns){for(const n of ns)this.appendChild(n);}
 replaceChildren(...ns){for(const n of this.children)n.parentNode=null;this.children=[];this._text='';this.append(...ns);}
 setAttribute(k,v){this.attributes[k]=String(v);}removeAttribute(k){delete this.attributes[k];}
 addEventListener(k,f){(this.events[k]??=[]).push(f);}removeEventListener(k,f){this.events[k]=(this.events[k]??[]).filter(x=>x!==f);}
 get textContent(){return this._text+this.children.map(n=>n.textContent).join('');}set textContent(v){this.replaceChildren();this._text=String(v);}
 get lastChild(){return this.children.at(-1);}remove(){const p=this.parentNode;if(p)p.children=p.children.filter(x=>x!==this);this.parentNode=null;}
 querySelector(tag){return walk(this).find(n=>n!==this&&n.tagName===tag)??null;}focus(){document.activeElement=this;}
 async fire(type){for(const fn of this.events[type]??[])await fn({target:this});}
}
const walk=n=>[n,...n.children.flatMap(walk)];
const base={id:'material-'+'a'.repeat(32),version:'b'.repeat(64),project_id:'synthetic',project_name:'合成项目',candidate_id:'candidate',candidate_revision:1,candidate_hash:'c'.repeat(64),artifact_id:'original-a',sha256:'d'.repeat(64),display_name:'同名报告.txt',classification:'mock',kind:'text',historical:true,registration_state:'historical',purpose:null,purpose_reason:'PURPOSE_NOT_DECLARED',provenance:{method:'synthetic',source_refs:['logical-source'],reason:null},observation_version:'e'.repeat(64),observed_at:'2026-10-24T00:00:00Z',available:true,content_readable:true,execution_allowed:false,review_reference:null};
const catalog=rows=>({store_revision:9,classification:'synthetic_fixture',content_cache:false,real_media_enabled:false,materials:rows});
const json=data=>({ok:true,status:200,json:async()=>({ok:true,data})});
const text=body=>({ok:true,status:200,headers:{get:k=>k==='Content-Type'?'text/plain; charset=utf-8':null},text:async()=>body});
async function dom(rows,fn,content=()=>text('SYNTHETIC OLD')){
 const old={document:globalThis.document,fetch:globalThis.fetch};const calls=[];
 globalThis.document={createElement:t=>new N(t),createTextNode:t=>{const n=new N('#text');n.textContent=t;return n;}};
 globalThis.fetch=async(path,options)=>{calls.push([path,options]);if(path==='/api/guest')return json({csrf_token:'synthetic-token'});if(path==='/api/materials')return typeof rows==='function'?await rows():json(catalog(rows));return content(path,options);};
 try{await fn(new N('main'),calls);}finally{globalThis.document=old.document;globalThis.fetch=old.fetch;}
}
async function until(predicate){const end=performance.now()+3000;while(!predicate()&&performance.now()<end)await new Promise(r=>setTimeout(r,2));assert.ok(predicate(),'observable async component boundary');}
const contentCalls=calls=>calls.filter(([p])=>p.startsWith('/api/materials/'));

test('actual component groups eight same names without merging identities or versions; metadata comparison reads zero bytes',()=>dom(Array.from({length:8},(_,i)=>({...base,id:'material-'+String(i).repeat(32),version:String(i).repeat(64),artifact_id:'original-'+i,candidate_revision:i+1})),async(target,calls)=>{
 await renderMaterials(target);const links=walk(target).filter(n=>n.tagName==='a');assert.equal(links.length,8);assert.equal(new Set(links.map(n=>n.attributes.href)).size,8);assert.match(target.textContent,/同名 8 个登记版本/);assert.match(target.textContent,/元数据对照，不代表内容已读/);assert.match(target.textContent,/用途：未知/);assert.equal(contentCalls(calls).length,0);assert.equal(walk(target).filter(n=>n.tagName==='details').length,8);
}));
test('exact historical pair selects old bytes rather than first id or newest candidate',()=>dom([{...base,version:'f'.repeat(64),historical:false,candidate_revision:2},base],async(target,calls)=>{
 await renderMaterials(target,{materialId:base.id,expectedVersion:base.version});assert.equal(contentCalls(calls).length,1);assert.equal(contentCalls(calls)[0][0],materialUrl(base));assert.match(target.textContent,/SYNTHETIC OLD/);assert.match(target.textContent,/历史登记（非当前决策）/);assert.match(target.textContent,/UTF-8 文本已读取/);
}));
test('missing or forged version offers explicit choices without falling forward or reading',()=>dom([base,{...base,version:'f'.repeat(64),candidate_revision:2}],async(target,calls)=>{
 for(const expectedVersion of [null,'0'.repeat(64)]){await renderMaterials(target,{materialId:base.id,expectedVersion});assert.equal(contentCalls(calls).length,0);assert.equal(walk(target).filter(n=>n.tagName==='a'&&n.textContent==='选择此精确版本').length,2);assert.doesNotMatch(target.textContent,/文本已读取/);}
}));
test('text is safe DOM, observation time differs from content version and unknown purpose remains explicit',()=>dom([{...base,display_name:'<script>name</script>',purpose:'<img src=x onerror=alert(1)>'}],async(target)=>{
 await renderMaterials(target,{materialId:base.id,expectedVersion:base.version});assert.match(target.textContent,/<script>name<\/script>/);assert.match(target.textContent,/不是内容变更时间/);assert.equal(walk(target).some(n=>['script','iframe'].includes(n.tagName)),false);assert.equal(walk(target).filter(n=>n.tagName==='pre')[0].textContent,'<script>body</script>');
},()=>text('<script>body</script>')));
test('image/video only report successful content observation after actual load or metadata event; failures never swap versions',async()=>{
 for(const [kind,event,message]of [['image','load','此版本图片已读取'],['video','loadedmetadata','此版本视频元数据已读取']])await dom([{...base,kind}],async(target,calls)=>{
  await renderMaterials(target,{materialId:base.id,expectedVersion:base.version});assert.match(target.textContent,/正在读取此版本内容/);assert.doesNotMatch(target.textContent,new RegExp(message));const media=walk(target).find(n=>n.tagName===(kind==='image'?'img':'video'));assert.equal(media.attributes.src,materialUrl(base));await media.fire(event);assert.match(target.textContent,new RegExp(message));await media.fire('error');assert.equal(media.attributes.src,undefined);assert.match(target.textContent,/没有切换到新版/);assert.equal(contentCalls(calls).length,0);
 });
});
test('Figma reference is an external review link, never local content read or execution',()=>dom([{...base,kind:'review_reference',content_readable:false,review_reference:'https://www.figma.com/design/synthetic?node-id=1'}],async(target,calls)=>{
 await renderMaterials(target,{materialId:base.id,expectedVersion:base.version});assert.match(target.textContent,/未读取本地内容，也未验证外部页面/);assert.equal(contentCalls(calls).length,0);assert.equal(walk(target).find(n=>n.attributes.target==='_blank').attributes.rel,'noopener noreferrer');assert.doesNotMatch(target.textContent,/文本已读取|图片已读取/);
}));
test('closed gate never fetches; failed text read and wrong MIME give recovery with no success claim',async()=>{
 await dom([{...base,available:false,content_readable:false}],async(target,calls)=>{await renderMaterials(target,{materialId:base.id,expectedVersion:base.version});assert.equal(contentCalls(calls).length,0);assert.match(target.textContent,/尚未开放内容读取/);assert.ok(walk(target).some(n=>n.tagName==='button'&&n.textContent==='重新核对产物'));});
 for(const response of [{ok:false,status:409},{ok:true,headers:{get:()=> 'text/html'},text:async()=>'<script>unsafe</script>'}])await dom([base],async(target)=>{await renderMaterials(target,{materialId:base.id,expectedVersion:base.version});assert.match(target.textContent,/内容不可读取或已变化/);assert.doesNotMatch(target.textContent,/文本已读取|unsafe/);},()=>response);
});
test('loading empty denied and malformed catalog have truthful recovery with no content reads',async()=>{
 let resolve;await dom(()=>new Promise(r=>resolve=r),async(target,calls)=>{const pending=renderMaterials(target);await until(()=>Boolean(resolve));assert.match(target.textContent,/正在读取产物记录/);resolve(json(catalog([])));await pending;assert.match(target.textContent,/没有可展示/);assert.equal(contentCalls(calls).length,0);});
 for(const response of [{ok:false,status:401,json:async()=>({ok:false,error:{code:'OWNER_AUTH_REQUIRED'}})},json(catalog([{...base,id:'../../bad'}]))])await dom(()=>response,async(target,calls)=>{await renderMaterials(target);assert.match(target.textContent,/连接所有者|记录异常/);assert.equal(contentCalls(calls).length,0);});
});
test('late catalog or text cannot pollute another selected version or revoked view',async()=>{
 let resolve,allowed=true;await dom([base],async(target,calls)=>{const pending=renderMaterials(target,{materialId:base.id,expectedVersion:base.version,isCurrent:()=>allowed});await until(()=>Boolean(resolve));allowed=false;resolve('PRIVATE SYNTHETIC OLD');await pending;assert.doesNotMatch(target.textContent,/PRIVATE SYNTHETIC OLD|文本已读取/);},()=>({ok:true,headers:{get:()=> 'text/plain; charset=utf-8'},text:()=>new Promise(r=>resolve=r)}));
 let resolveCatalog,request=0;await dom(()=>{request++;return request===1?new Promise(r=>resolveCatalog=r):json(catalog([]));},async(target)=>{const first=renderMaterials(target);await until(()=>Boolean(resolveCatalog));await renderMaterials(target);resolveCatalog(json(catalog([base])));await first;assert.match(target.textContent,/没有可展示/);assert.doesNotMatch(target.textContent,/同名报告/);});
});
test('rerender removes old media listeners/source; exact version URLs never follow a display name',()=>dom([{...base,kind:'image'}],async(target)=>{
 await renderMaterials(target,{materialId:base.id,expectedVersion:base.version});const img=walk(target).find(n=>n.tagName==='img');assert.equal(img.events.load.length,1);await renderMaterials(target);assert.equal(img.events.load.length,0);assert.equal(img.attributes.src,undefined);assert.equal(materialLink({...base,display_name:'/private/text'}),materialLink(base));
}));

test('legacy catalog remains readable and revoked detached reload cannot replace the active view',async()=>{
 const legacy={...base};for(const k of ['content_readable','display_name','historical','provenance','observation_version','observed_at','purpose'])delete legacy[k];
 await dom([legacy],async(target)=>{await renderMaterials(target,{materialId:base.id,expectedVersion:base.version});assert.match(target.textContent,/内容入口已核对/);assert.match(target.textContent,/UTF-8 文本已读取/);});
 let allowed=true;await dom([base],async(target,calls)=>{await renderMaterials(target,{isCurrent:()=>allowed});const reload=walk(target).find(n=>n.tagName==='button');allowed=false;target.textContent='New selected view';const before=calls.length;await reload.fire('click');assert.equal(target.textContent,'New selected view');assert.equal(calls.length,before);});
});
