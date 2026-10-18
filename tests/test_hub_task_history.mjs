import test from 'node:test';
import assert from 'node:assert/strict';
import {relatedOrdinaryTasks,renderTaskHistory} from '../src/hub/web/task_history.mjs';

class N {
  constructor(tag){this.tag=tag;this.children=[];this.attrs={};this.events={};this.disabled=false;this.text='';this.isConnected=true;}
  appendChild(child){this.children.push(child);return child;}
  append(...children){for(const child of children)this.appendChild(typeof child==='string'?Object.assign(new N('#text'),{text:child}):child);}
  replaceChildren(...children){this.children=children;this.text='';}
  addEventListener(name,fn){(this.events[name]??=[]).push(fn);}
  setAttribute(name,value){this.attrs[name]=String(value);}
  get textContent(){return this.text+this.children.map(child=>child?.textContent??'').join('');}
  set textContent(value){this.text=String(value);this.children=[];}
  async fire(name){for(const fn of this.events[name]??[])await fn({});}
}
const walk=node=>[node,...node.children.filter(Boolean).flatMap(walk)];
const readButton=node=>walk(node).find(n=>n.tag==='button'&&n.textContent==='读取同会话任务结果');
const row=(id,sequence,overrides={})=>({id,sequence,kind:'ordinary_project',project:'alpha',thread_id:'thread-a',status:'checks_complete',...overrides});
async function setup(run){
  const prior=globalThis.document;
  globalThis.document={createElement:tag=>new N(tag),createTextNode:text=>{const node=new N('#text');node.textContent=text;return node;}};
  try{await run();}finally{globalThis.document=prior;}
}
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};

test('ordinary history requires exact kind, project and native thread, orders valid sequences and preserves inputs',()=>{
  const task=row('current',4),tasks=[row('later',7),row('other-project',1,{project:'beta'}),
    row('earlier',0),row('other-thread',2,{thread_id:'thread-b'}),row('legacy',3,{kind:'legacy'}),
    row('negative',-1),row('fraction',1.5),row('unsafe',Number.MAX_SAFE_INTEGER+1),row('missing',undefined),task];
  const before=structuredClone({task,tasks});
  assert.deepEqual(relatedOrdinaryTasks(task,tasks).map(t=>t.id),['earlier','current','later','fraction','missing','negative','unsafe']);
  assert.deepEqual({task,tasks},before);
  for(const change of [{kind:'legacy'},{project:''},{project:null},{project:'  '},{thread_id:''},{thread_id:null},{thread_id:'  '}]){
    assert.deepEqual(relatedOrdinaryTasks({...task,...change},tasks),[]);
  }
  assert.deepEqual(relatedOrdinaryTasks(task,null),[]);
});

test('missing native task binding explains absence without reading or silently opening Hub sessions',()=>setup(async()=>{
  for(const task of [row('no-thread',1,{thread_id:null}),row('no-project',1,{project:''}),row('legacy',1,{kind:'legacy'})]){
    let calls=0;const node=renderTaskHistory(task,{request:async()=>{calls++;throw Error('unexpected');},linkFor:t=>'#test/'+t.id});
    assert.match(node.textContent,/未|无|不/);assert.equal(readButton(node),undefined);
    assert.equal(calls,0);assert.equal(walk(node).some(n=>n.tag==='a'),false);
  }
}));

test('history reads only after explicit action and uses supplied result links for the isolated thread',()=>setup(async()=>{
  const calls=[],linked=[],task=row('current',2);
  const node=renderTaskHistory(task,{request:async(...args)=>{calls.push(args);return {tasks:calls.length===1?[row('later',3),row('foreign',0,{project:'beta'}),task]:[]};},
    linkFor:t=>{linked.push(t.id);return '#fixed-results/'+t.id;}});
  assert.equal(node.tag,'details');assert.equal(walk(node).find(n=>n.tag==='summary').textContent,'本任务会话与历史');
  assert.equal(calls.length,0);await readButton(node).fire('click');
  assert.equal(calls.length,1);assert.equal(calls[0][0],'/api/tasks');assert.equal(calls[0][1],undefined);
  assert.deepEqual(linked,['current','later']);
  const links=walk(node).filter(n=>n.tag==='a');assert.deepEqual(links.map(n=>n.attrs.href),['#fixed-results/current','#fixed-results/later']);
  assert.equal(node.textContent.includes('foreign'),false);assert.equal(links.some(n=>n.attrs.href?.includes('sessions')),false);
  await readButton(node).fire('click');assert.equal(calls.length,2);assert.equal(walk(node).filter(n=>n.tag==='a').length,0);
  assert.match(node.textContent,/没有匹配/);assert.doesNotMatch(node.textContent,/无法读取/);
}));

test('401, network, unreadable ledger and malformed responses remain retryable errors rather than empty history',()=>setup(async()=>{
  const cases=[{error:Object.assign(Error('owner required'),{status:401,code:'OWNER_REQUIRED'})},
    {error:Object.assign(Error('network down'),{code:'NETWORK_ERROR'})},
    {error:Object.assign(Error('ledger unavailable'),{status:503,code:'TASK_LEDGER_UNAVAILABLE'})},
    {data:{tasks_available:false,tasks:[]}},{data:{}},{data:{tasks:null}}];
  for(const outcome of cases){
    let calls=0;const node=renderTaskHistory(row('current',1),{request:async()=>{
      calls++;if(calls===1){if(outcome.error)throw outcome.error;return outcome.data;}
      return {tasks:[row('recovered',2)]};
    },linkFor:t=>'#result/'+t.id});
    await readButton(node).fire('click');assert.equal(calls,1);assert.match(node.textContent,/无法读取/);
    assert.doesNotMatch(node.textContent,/没有匹配/);
    assert.equal(walk(node).some(n=>n.tag==='a'),false);assert.equal(readButton(node).disabled,false);
    await readButton(node).fire('click');assert.equal(calls,2);assert.equal(walk(node).filter(n=>n.tag==='a').length,1);
    assert.ok(node.textContent.includes('recovered'));
  }
}));

for(const failure of [false,true])test('route invalidation discards delayed '+(failure?'error':'history')+' without stale page updates',()=>setup(async()=>{
  const pending=deferred();let active=true,calls=0;
  const node=renderTaskHistory(row('current',1),{request:()=>{calls++;return pending.promise;},isCurrent:()=>active,linkFor:t=>'#result/'+t.id});
  const work=readButton(node).fire('click');assert.equal(calls,1);const before=node.textContent;
  active=false;
  if(failure)pending.reject(Object.assign(Error('late network error'),{code:'NETWORK_ERROR'}));
  else pending.resolve({tasks:[row('late-history',2)]});
  await work;assert.equal(calls,1);assert.equal(node.textContent,before);assert.equal(walk(node).some(n=>n.tag==='a'),false);
}));

test('completion restores keyboard focus lost while disabled without stealing a new user focus',()=>setup(async()=>{
  document.body={tag:'body'};
  for(const movedElsewhere of [false,true]){
    const pending=deferred();
    const node=renderTaskHistory(row('current',1),{request:()=>pending.promise,linkFor:t=>'#result/'+t.id});
    const read=readButton(node);read.focus=()=>{document.activeElement=read;};
    document.activeElement=read;
    const work=read.fire('click');assert.equal(read.disabled,true);
    const next=movedElsewhere?{tag:'a'}:document.body;document.activeElement=next;
    pending.resolve({tasks:[row('current',1)]});await work;
    assert.equal(read.disabled,false);assert.equal(document.activeElement,movedElsewhere?next:read);
  }
}));
