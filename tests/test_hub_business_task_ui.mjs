import assert from 'node:assert/strict';
import test from 'node:test';
import {renderBusinessTaskPreview} from '../src/hub/web/tasks.js';

class Node{
  constructor(tag,text=null){this.tag=tag;this.text=text;this.children=[];this.attrs={};}
  appendChild(child){this.children.push(child);return child;}
  append(...children){children.forEach(child=>this.children.push(typeof child==='string'?new Node('#text',child):child));}
  replaceChildren(...children){this.children=[];this.append(...children);}
  setAttribute(key,value){this.attrs[key]=value;}
  set textContent(value){this.text=value;this.children=[];}
  get textContent(){return this.text??this.children.map(child=>child.textContent).join('');}
}
const walk=node=>[node,...node.children.flatMap(walk)];
const task=()=>({id:'story /task',kind:'story_exchange',classification:'real',status:'checks_complete',result:{source_promoted:true},task_spec:{requirement:'三个故事方向',scope:'当前 C01',acceptance:'完整候选不改原稿'}});
const preview=()=>({kind:'story_exchange',classification:'real',current:true,binding:{workspace_revision:51,workspace_sha256:'a'.repeat(64),request_id:'request-1',request_sha256:'b'.repeat(64),output_sha256:'c'.repeat(64)},cards:Array.from({length:3},(_,i)=>({id:`c${i}`,title:`候选${i}`,...Object.fromEntries(['goal','obstacle','action','choice','consequence','difference','strength','weakness'].map(key=>[key,`${key}正文`]))}))});
async function setup(run){
  const previous={document:globalThis.document,fetch:globalThis.fetch};
  globalThis.document={createElement:tag=>new Node(tag),createTextNode:text=>new Node('#text',text)};
  const calls=[];
  globalThis.fetch=async(path,options)=>{
    calls.push({path,options});
    return {ok:true,json:async()=>({ok:true,data:path==='/api/guest'?{csrf_token:'test-csrf'}:preview()})};
  };
  try{await run(new Node('main'),calls);}finally{Object.assign(globalThis,previous);}
}

test('completed promoted real task fetches read-only preview and displays specification and three full cards',()=>setup(async(root,calls)=>{
  await renderBusinessTaskPreview(root,task());
  assert.match(root.textContent,/三个故事方向.*当前 C01.*完整候选不改原稿/s);
  assert.equal(walk(root).filter(node=>node.tag==='article').length,3);
  const request=calls.find(call=>call.path.endsWith('/preview'));
  assert.equal(request.path,'/api/tasks/story%20%2Ftask/preview');assert.equal(request.options.method,'GET');
  assert.ok(calls.every(call=>!call.options.method||call.options.method==='GET'));
  assert.ok(walk(root).every(node=>!['button','form','input'].includes(node.tag)));
}));

test('nonbusiness and unpromoted, cancelled, failed or unverified tasks never fetch results',()=>setup(async(root,calls)=>{
  await renderBusinessTaskPreview(root,{...task(),kind:'fixture'});assert.equal(root.children.length,0);
  for(const status of ['queued','running','validating','cancelled','failed','requires_reconcile','checks_complete']){
    const entry=task();entry.status=status;entry.result.source_promoted=false;
    const target=new Node('main');await renderBusinessTaskPreview(target,entry);
    assert.equal(walk(target).filter(node=>node.tag==='article').length,0);
    assert.match(target.textContent,/本次写作任务/);
    if(status==='cancelled')assert.match(target.textContent,/已取消/);
  }
  await renderBusinessTaskPreview(root,{...task(),classification:'synthetic'});
  assert.match(root.textContent,/尚未核实为真实业务/);assert.equal(calls.length,0);
}));

test('loading and failure retain task specification and never claim current output',()=>setup(async(root,calls)=>{
  let reject;
  globalThis.fetch=()=>new Promise((resolve,fail)=>{reject=fail;});
  const pending=renderBusinessTaskPreview(root,task());
  assert.match(root.textContent,/正在读取本次完整故事候选/);
  reject(new Error('offline'));await pending;
  assert.match(root.textContent,/暂时无法核实完整故事候选/);assert.match(root.textContent,/当前 C01/);
  assert.equal(walk(root).filter(node=>node.tag==='article').length,0);
}));

test('stale route suppresses late result; historical and malformed response remain explicit',()=>setup(async(root)=>{
  let resolve;
  globalThis.fetch=()=>new Promise(done=>{resolve=done;});
  let current=true;const pending=renderBusinessTaskPreview(root,task(),{isCurrent:()=>current});
  current=false;resolve({ok:true,json:async()=>({ok:true,data:preview()})});await pending;
  assert.equal(walk(root).filter(node=>node.tag==='article').length,0);
  for(const value of [{...preview(),current:false},{}]){
    globalThis.fetch=async()=>({ok:true,json:async()=>({ok:true,data:value})});
    const target=new Node('main');await renderBusinessTaskPreview(target,task());
    assert.match(target.textContent,value.current===false?/历史结果：已不属于当前工作台版本/:/尚未核实为真实业务产出/);
  }
}));
