import test from 'node:test';
import assert from 'node:assert/strict';
import {renderOrdinaryTasks,renderOrdinaryTaskResult,validOrdinarySpec,ordinaryTaskMatches} from '../src/hub/web/ordinary_tasks.mjs';

class N{
  constructor(tag){this.tag=tag;this.children=[];this.attrs={};this.events={};this.value='';this.disabled=false;this.hidden=false;this.isConnected=true;this.text='';}
  appendChild(child){this.children.push(child);return child;}
  append(...children){for(const child of children)this.children.push(typeof child==='string'?new N('#text'):child);}
  replaceChildren(...children){this.children=children;this.text='';}
  addEventListener(name,fn){(this.events[name]??=[]).push(fn);}
  setAttribute(name,value){this.attrs[name]=String(value);}
  get textContent(){return this.text+this.children.map(child=>child?.textContent??'').join('');}
  set textContent(value){this.text=String(value);this.children=[];}
  async fire(name){for(const fn of this.events[name]??[])await fn({});}
}
const walk=node=>[node,...node.children.filter(Boolean).flatMap(walk)];
const button=(node,name)=>walk(node).find(n=>n.tag==='button'&&n.textContent===name);
const field=(node,name)=>walk(node).find(n=>n.attrs['aria-label']===name);
const described=(node,control)=>walk(node).find(n=>n.attrs.id===control.attrs['aria-describedby']);
const sendReason=node=>described(node,button(node,'发送普通任务'));
const spec=(project='a',parent=null)=>({kind:'ordinary_project',request_id:'ordinary-saved-'+project,project_id:project,
  objective:'Read project '+project,scope:'Read-only owned scope',effect:'read_only',input_version:'a'.repeat(64),mode:parent?'idle_continue':'new',parent_task_id:parent});
const parentTask=(project='a',id='parent-a')=>({id,kind:'ordinary_project',project,task_spec:spec(project),status:'checks_complete',allowed_modes:['idle_continue']});
const context=(project='a',parent=null)=>({project_id:project,name:'Project '+project,execution_location:'Isolated synthetic project',input_version:'a'.repeat(64),
  draft_scope:(project==='a'?(parent?'d':'c'):'e').repeat(64),
  allowed_effects:['read_only'],ready:true,reason_code:null,capabilities:{},parents:[parentTask(project)],
  draft:{project_id:project,parent_task_id:parent,revision:0,value:null}});
const taskFor=value=>({id:value.request_id,kind:'ordinary_project',project:value.project_id,task_spec:structuredClone(value),status:'queued',classification:'synthetic_fixture'});
async function setup(run){
  const previous={document:globalThis.document,window:globalThis.window,location:globalThis.location,localStorage:globalThis.localStorage};
  const listeners=new Map();
  const storage=new Map();globalThis.localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,String(value)),removeItem:key=>storage.delete(key)};
  globalThis.document={createElement:tag=>new N(tag),createTextNode:text=>{const node=new N('#text');node.textContent=text;return node;}};
  globalThis.window={addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name)};
  globalThis.location={hash:'#projects/a/tasks'};
  try{await run({identity:detail=>listeners.get('hub:identity')?.({detail}),listeners,storage});}finally{Object.assign(globalThis,previous);}
}
async function fill(node,objective='A precise objective',scope='Only this synthetic project'){
  const target=field(node,'任务目标'),area=field(node,'任务范围');target.value=objective;await target.fire('input');area.value=scope;await area.fire('input');
}
function service(initial=context()){
  const state={context:structuredClone(initial),calls:[],owner:true,go:[],postTask:null,postDraft:null,getTask:null};
  state.request=async(path,body)=>{
    state.calls.push({path,body:body===undefined?undefined:structuredClone(body)});
    if(path==='/api/identity')return {owner_authenticated:state.owner};
    if(path.startsWith('/api/ordinary-context'))return structuredClone(state.context);
    if(path==='/api/ordinary-drafts'){
      if(state.postDraft)return state.postDraft(body);
      state.context.draft={project_id:body.project_id,parent_task_id:body.parent_task_id,revision:body.expected_revision+1,value:structuredClone(body.value)};
      return {draft:structuredClone(state.context.draft),replayed:false,request_id:body.request_id};
    }
    if(path==='/api/tasks')return state.postTask?state.postTask(body):{task:taskFor(body)};
    if(path.startsWith('/api/tasks/'))return state.getTask?state.getTask(path):{task:taskFor(state.calls.find(call=>call.path==='/api/tasks').body)};
    throw new Error('unexpected API');
  };
  state.render=(project={project_id:state.context.project_id},options={})=>renderOrdinaryTasks(project,{request:state.request,go:value=>state.go.push(value),...options});
  return state;
}

test('ready false and unknown input still save isolated drafts without starting a task; saved draft reopens',()=>setup(async()=>{
  const state=service({...context(),ready:false,input_version:null,reason_code:'GRANT_UNAVAILABLE'});
  let node=await state.render();await fill(node);assert.equal(button(node,'发送普通任务').disabled,true);
  assert.equal(button(node,'保存任务草稿').disabled,false);await button(node,'保存任务草稿').fire('click');
  assert.equal(state.calls.filter(call=>call.path==='/api/tasks').length,0);
  const sent=state.calls.find(call=>call.path==='/api/ordinary-drafts').body;
  assert.equal(sent.value.input_version,null);assert.equal(sent.project_id,'a');assert.equal(sent.parent_task_id,null);
  assert.match(field(node,'普通任务状态').textContent,/已保存.*没有启动模型/);
  node=await state.render();assert.equal(field(node,'任务目标').value,'A precise objective');
  assert.equal(field(node,'任务范围').value,'Only this synthetic project');
}));

for(const recoveredVersion of ['a','b'])test('saved known input survives unavailable context and explicit recovery to '+(recoveredVersion==='a'?'the same':'a changed')+' version',()=>setup(async()=>{
  const saved=spec(),initial={...context(),ready:false,input_version:null,reason_code:'WORKER_UNAVAILABLE'};
  initial.draft={...initial.draft,revision:3,value:saved};
  const state=service(initial),node=await state.render();
  const assertPreserved=()=>{
    assert.equal(field(node,'任务目标').value,saved.objective);
    assert.equal(field(node,'任务范围').value,saved.scope);
    assert.match(node.textContent,new RegExp(saved.request_id));
    assert.equal(state.calls.filter(call=>call.path==='/api/tasks').length,0);
  };
  assertPreserved();
  assert.equal(button(node,'发送普通任务').disabled,true);
  assert.equal(button(node,'保存任务草稿').disabled,false);
  assert.match(node.textContent,/当前任务输入版本尚未核实.*核对任务条件.*草稿保留/);
  assert.doesNotMatch(node.textContent,/草稿绑定的输入版本与当前来源不同|输入版本已变化/);
  await button(node,'发送普通任务').fire('click');assertPreserved();
  await button(node,'保存任务草稿').fire('click');
  assert.deepEqual(state.calls.filter(call=>call.path==='/api/ordinary-drafts').at(-1).body.value,saved);
  assertPreserved();

  state.context.ready=true;state.context.input_version=recoveredVersion.repeat(64);state.context.reason_code=null;
  await button(node,'核对任务条件').fire('click');assertPreserved();
  assert.doesNotMatch(node.textContent,/当前任务输入版本尚未核实/);
  await button(node,'保存任务草稿').fire('click');
  assert.deepEqual(state.calls.filter(call=>call.path==='/api/ordinary-drafts').at(-1).body.value,saved);
  if(recoveredVersion==='a'){
    assert.equal(button(node,'发送普通任务').disabled,false);
    assert.doesNotMatch(node.textContent,/草稿绑定的输入版本与当前来源不同|输入版本已变化/);
  }else{
    assert.equal(button(node,'发送普通任务').disabled,true);
    assert.match(sendReason(node).textContent,/输入版本.*(?:不同|变化).*重新读取任务输入/);
    await button(node,'发送普通任务').fire('click');assertPreserved();
    await button(node,'重新读取任务输入').fire('click');
    assert.equal(field(node,'任务目标').value,saved.objective);
    assert.equal(field(node,'任务范围').value,saved.scope);
    assert.equal(button(node,'发送普通任务').disabled,false);
    await button(node,'保存任务草稿').fire('click');
    const rebound=state.calls.filter(call=>call.path==='/api/ordinary-drafts').at(-1).body.value;
    assert.equal(rebound.input_version,'b'.repeat(64));assert.notEqual(rebound.request_id,saved.request_id);
    assert.deepEqual({...rebound,input_version:saved.input_version,request_id:saved.request_id},saved);
  }
  assert.equal(state.calls.filter(call=>call.path==='/api/tasks').length,0);
}));

test('send first saves and then uses that exact task ID; double clicks cannot dispatch twice',()=>setup(async()=>{
  const state=service();let release;state.postTask=body=>new Promise(resolve=>{release=()=>resolve({task:taskFor(body)});});
  const node=await state.render();await fill(node);const sending=button(node,'发送普通任务').fire('click');
  await new Promise(resolve=>setImmediate(resolve));assert.equal(button(node,'发送普通任务').disabled,true);
  await button(node,'发送普通任务').fire('click');
  assert.equal(state.calls.filter(call=>call.path==='/api/tasks').length,1);
  const draft=state.calls.find(call=>call.path==='/api/ordinary-drafts').body,task=state.calls.find(call=>call.path==='/api/tasks').body;
  assert.deepEqual(task,draft.value);assert.ok(validOrdinarySpec(task,{submit:true}));release();await sending;
  assert.deepEqual(state.go,['#tasks/'+task.request_id]);
}));

test('uncertain dispatch reads the original request and never creates a second identity or POST',()=>setup(async()=>{
  const state=service();state.postTask=async()=>{throw {code:'NETWORK_TIMEOUT',outcome:'UNKNOWN'};};
  const node=await state.render();await fill(node);await button(node,'发送普通任务').fire('click');
  const sent=state.calls.find(call=>call.path==='/api/tasks').body;
  assert.ok(state.calls.some(call=>call.path==='/api/tasks/'+sent.request_id));assert.deepEqual(state.go,['#tasks/'+sent.request_id]);
  assert.equal(state.calls.filter(call=>call.path==='/api/tasks').length,1);
}));

test('unconfirmed or mismatched original task stays locked and only GET reconciliation is available',()=>setup(async()=>{
  const state=service();state.postTask=async()=>{throw {outcome:'UNKNOWN'};};state.getTask=async()=>{throw {status:404};};
  const node=await state.render();await fill(node);await button(node,'发送普通任务').fire('click');
  const sent=state.calls.find(call=>call.path==='/api/tasks').body;
  assert.match(field(node,'普通任务状态').textContent,/尚未查到.*不会自动重发或生成新请求/);
  assert.equal(field(node,'任务目标').disabled,true);assert.equal(button(node,'新建另一条任务草稿').disabled,true);
  assert.match(sendReason(node).textContent,/发送结果尚未确认.*核对原任务请求/);
  state.getTask=async()=>({task:{...taskFor(sent),project:'b'}});
  await button(node,'核对原任务请求').fire('click');assert.deepEqual(state.go,[]);
  assert.equal(state.calls.filter(call=>call.path==='/api/tasks').length,1);
  assert.ok(state.calls.filter(call=>call.path.startsWith('/api/tasks/')).every(call=>call.path==='/api/tasks/'+sent.request_id));
}));

test('draft CAS conflict preserves input until explicit version reread and explicit save',()=>setup(async()=>{
  const state=service();let first=true;state.postDraft=async command=>{
    if(first){first=false;throw {status:409,outcome:'NOT_COMMITTED'};}
    return {draft:{project_id:'a',parent_task_id:null,revision:8,value:command.value}};
  };
  const node=await state.render();await fill(node);await button(node,'保存任务草稿').fire('click');
  assert.equal(field(node,'任务目标').value,'A precise objective');assert.equal(button(node,'发送普通任务').disabled,true);
  assert.match(sendReason(node).textContent,/草稿版本存在冲突/);
  state.context.draft={...state.context.draft,revision:7,value:{...spec(),objective:'Other editor text'}};
  await button(node,'重读草稿版本').fire('click');assert.equal(field(node,'任务目标').value,'A precise objective');
  await button(node,'保存任务草稿').fire('click');
  const commands=state.calls.filter(call=>call.path==='/api/ordinary-drafts');assert.equal(commands[1].body.expected_revision,7);
  assert.equal(commands[1].body.value.objective,'A precise objective');
}));

test('uncertain draft save retries the same draft command and never sends the task',()=>setup(async()=>{
  const state=service();let first=true;state.postDraft=async command=>{
    if(first){first=false;throw {outcome:'UNKNOWN'};}
    return {draft:{project_id:'a',parent_task_id:null,revision:1,value:command.value}};
  };
  const node=await state.render();await fill(node);await button(node,'发送普通任务').fire('click');
  assert.equal(button(node,'发送普通任务').disabled,true);assert.equal(field(node,'任务目标').disabled,true);
  assert.match(sendReason(node).textContent,/草稿保存结果尚未确认/);
  await button(node,'重试确认草稿保存').fire('click');
  const commands=state.calls.filter(call=>call.path==='/api/ordinary-drafts');assert.deepEqual(commands[0].body,commands[1].body);
  assert.equal(state.calls.filter(call=>call.path==='/api/tasks').length,0);
}));

test('identity metadata updates preserve text and input version until the explicit rebind action',()=>setup(async({identity})=>{
  const state=service();const node=await state.render();await fill(node);await button(node,'保存任务草稿').fire('click');
  const first=state.context.draft.value.request_id;state.context.input_version='b'.repeat(64);
  identity({owner_authenticated:true});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(field(node,'任务目标').value,'A precise objective');assert.equal(button(node,'发送普通任务').disabled,true);
  await button(node,'重新读取任务输入').fire('click');assert.equal(button(node,'发送普通任务').disabled,false);
  await button(node,'保存任务草稿').fire('click');assert.equal(state.context.draft.value.input_version,'b'.repeat(64));
  assert.notEqual(state.context.draft.value.request_id,first);assert.equal(state.calls.filter(call=>call.path==='/api/tasks').length,0);
}));

test('guest login initializes a previously unavailable context without a reload',()=>setup(async({identity})=>{
  const state=service();state.owner=false;const original=state.request;
  state.request=async(path,body)=>{if(path.startsWith('/api/ordinary-context')&&!state.owner)throw {status:403};return original(path,body);};
  const node=await state.render();assert.equal(field(node,'任务目标').disabled,true);
  state.owner=true;identity({owner_authenticated:true});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(field(node,'任务目标').disabled,false);await fill(node);assert.equal(button(node,'保存任务草稿').disabled,false);
}));

test('parent draft targets and allowed modes are server-bound and cannot cross project or kind',()=>setup(async()=>{
  const state=service();state.context.parents.push({...parentTask('b','foreign')},{...parentTask('a','other-kind'),kind:'story_exchange'});
  const node=await state.render();await fill(node);const parent=field(node,'续接任务');
  assert.deepEqual(parent.children.map(option=>option.value),['','parent-a']);
  // render captured the original request function, which reads this live context.
  parent.value='parent-a';state.context={...context('a','parent-a'),draft:{project_id:'a',parent_task_id:'parent-a',revision:2,value:spec('a','parent-a')}};
  // Each server draft key is independent; saving the old target cannot replace
  // the different parent draft that the next context read will return.
  state.postDraft=async command=>({draft:{project_id:command.project_id,parent_task_id:command.parent_task_id,
    revision:command.expected_revision+1,value:command.value}});
  await button(node,'载入所选任务草稿').fire('click');
  assert.equal(state.calls.find(call=>call.path==='/api/ordinary-drafts').body.parent_task_id,null);
  assert.equal(field(node,'任务目标').value,'Read project a');assert.deepEqual(field(node,'任务模式').children.map(option=>option.value),['idle_continue']);
  await button(node,'保存任务草稿').fire('click');const command=state.calls.filter(call=>call.path==='/api/ordinary-drafts').at(-1).body;
  assert.equal(command.parent_task_id,'parent-a');assert.equal(command.value.mode,'idle_continue');
  parent.value='foreign';const before=state.calls.length;await button(node,'载入所选任务草稿').fire('click');assert.equal(state.calls.length,before);
}));

test('late project context never fills another route and confirmed HTTP failures keep text',()=>setup(async()=>{
  let current=true,release;
  const state=service();const request=async path=>path==='/api/identity'?{owner_authenticated:true}:new Promise(resolve=>{release=resolve;});
  const pending=state.render({project_id:'a'},{request,isCurrent:()=>current});current=false;release(context());const stale=await pending;
  assert.equal(field(stale,'任务目标').value,'');assert.deepEqual(state.go,[]);
  for(const status of [401,403,409,503]){
    const attempt=service();attempt.postTask=async()=>{throw {status,outcome:'NOT_COMMITTED'};};const node=await attempt.render();
    await fill(node);await button(node,'发送普通任务').fire('click');assert.equal(field(node,'任务目标').value,'A precise objective');
    assert.match(field(node,'普通任务状态').textContent,status===401?/连接所有者/:status===403?/权限/:status===409?/版本或执行条件/:/服务暂不可用/);
    assert.equal(attempt.calls.filter(call=>call.path==='/api/tasks').length,1);
  }
}));

test('ordinary result preserves bounded full response and instruction identity without HTML execution',()=>setup(async()=>{
  const original=spec(),task={...taskFor(original),execution_location:'Synthetic A',result:{response:'<script>literal</script>\n'+'答复'.repeat(5000),response_truncated:false}};
  const node=renderOrdinaryTaskResult(task);assert.equal(field(node,'普通任务执行回复').textContent,task.result.response);
  assert.match(node.textContent,/Read project a/);assert.equal(walk(node).filter(n=>n.tag==='script').length,0);
  assert.ok(walk(node).some(n=>n.attrs.href==='#projects/a/tasks?parent_task_id='+original.request_id));
  assert.equal(ordinaryTaskMatches({...task,task_spec:{...original,objective:'Wrong message'}},original),false);
  const wrong=renderOrdinaryTaskResult({...task,project:'b'});assert.match(wrong.textContent,/项目绑定不一致/);assert.ok(!wrong.textContent.includes(task.result.response));
  assert.match(renderOrdinaryTaskResult({...task,result:{...task.result,response_truncated:true}}).textContent,/已截断/);
  assert.equal(validOrdinarySpec({...original,extra:'not allowed'}),false);assert.equal(renderOrdinaryTaskResult({...task,kind:'story_exchange'}),null);
}));

test('ordinary result separates completed native execution, limited checks, quality and pending acceptance',()=>setup(async()=>{
  const task={...taskFor(spec()),status:'checks_complete',provider_status:'completed',human_acceptance:'pending',
    result:{response:'A recorded reply',checks:[{id:'ordinary_declared_inputs_unchanged',exit:0},
      {id:'ordinary_response_present',exit:0}]}};
  const node=renderOrdinaryTaskResult(task),checks=field(node,'普通任务工程检查');
  assert.equal(field(node,'普通任务原生执行结果').textContent,'执行已完成');
  assert.deepEqual(walk(checks).filter(n=>n.tag==='h4').map(n=>n.textContent),['声明输入未变化 · 通过','回复非空 · 通过']);
  assert.match(checks.textContent,/声明输入清单.*执行前后是否一致.*不涵盖未声明文件/);
  assert.match(checks.textContent,/非空白内容.*不判断答复是否完整、正确或符合业务要求/);
  assert.match(field(node,'普通任务质量结论').textContent,/没有独立记录.*工程检查通过不能证明答复正确/);
  assert.equal(field(node,'普通任务用户验收').textContent,'用户验收：待决定。');
  const details=walk(checks).filter(n=>n.tag==='details');assert.equal(details.length,2);
  assert.equal(details[0].children[0].textContent,'检查技术详情');
  assert.deepEqual(JSON.parse(details[0].children[1].textContent),task.result.checks[0]);
}));

test('ordinary result never infers native completion from check success or task lifecycle',()=>setup(async()=>{
  const base={...taskFor(spec()),result:{checks:[{id:'ordinary_response_present',exit:0}]}};
  for(const [status,provider,expected]of [['queued',null,'尚未确认'],['running','inProgress','正在执行'],
    ['checks_complete','future_status','尚未确认'],['checks_complete','__proto__','尚未确认'],
    ['failed','failed','执行失败'],['cancelled','interrupted','执行已中断'],['lost',undefined,'尚未确认']]){
    const node=renderOrdinaryTaskResult({...base,status,provider_status:provider});
    assert.match(field(node,'普通任务原生执行结果').textContent,new RegExp(expected));
    assert.notEqual(field(node,'普通任务原生执行结果').textContent,'执行已完成');
  }
  for(const result of [undefined,{}, {checks:[]},{checks:null}]){
    const node=renderOrdinaryTaskResult({...base,result});
    assert.match(field(node,'普通任务工程检查').textContent,/尚无已记录.*不能视为检查通过/);
  }
}));

test('failed and unknown ordinary checks preserve all technical evidence without inventing success',()=>setup(async()=>{
  const checks=[{id:'ordinary_response_present',exit:1,stdout:'<b>out</b>',stderr:'err',stdout_truncated:true},
    {id:'legacy_custom_check',exit:null,stderr:'legacy detail',response_truncated:true},
    {id:'ordinary_declared_inputs_unchanged',exit:'0'},null];
  const node=renderOrdinaryTaskResult({...taskFor(spec()),status:'failed',provider_status:'completed',result:{checks}});
  const panel=field(node,'普通任务工程检查');
  assert.deepEqual(walk(panel).filter(n=>n.tag==='h4').map(n=>n.textContent),
    ['回复非空 · 未通过','其他工程检查 · 结果待核对','声明输入未变化 · 结果待核对','其他工程检查 · 结果待核对']);
  assert.match(panel.textContent,/未提供这项检查的含义说明/);
  assert.deepEqual(walk(panel).filter(n=>n.tag==='details').map(n=>JSON.parse(n.children[1].textContent)),checks);
  assert.equal(walk(panel).filter(n=>n.tag==='b').length,0);
  assert.equal(field(node,'普通任务原生执行结果').textContent,'执行已完成','native completion is separate from failed engineering checks');
}));

test('ordinary acceptance reports actual decisions without creating a quality verdict or effects',()=>setup(async({storage})=>{
  const labels={pending:'待决定',accepted:'已接受此次结果',changes_requested:'需要修改',deferred:'暂缓验收'};
  const previousFetch=globalThis.fetch;globalThis.fetch=()=>{throw new Error('result rendering must not request or mutate');};
  try{
    for(const [human_acceptance,label]of [...Object.entries(labels),[undefined,'验收状态待核对'],['future','验收状态待核对'],['__proto__','验收状态待核对']]){
      const task={...taskFor(spec()),provider_status:'completed',human_acceptance,result:{response:'Reply',checks:[]}};
      const before=structuredClone(task);Object.freeze(task.task_spec);Object.freeze(task.result.checks);Object.freeze(task.result);Object.freeze(task);
      const node=renderOrdinaryTaskResult(task);
      assert.equal(field(node,'普通任务用户验收').textContent,'用户验收：'+label+'。');
      assert.match(field(node,'普通任务质量结论').textContent,/本页没有独立记录/);
      assert.doesNotMatch(node.textContent,/从未.*审阅|质量已通过|质量评分/);
      assert.deepEqual(task,before);assert.equal(walk(node).filter(n=>n.tag==='button').length,0);
      assert.equal(walk(node).some(n=>Object.keys(n.events).length),false);
      assert.equal(renderOrdinaryTaskResult({...task,kind:'story_exchange'}),null);
      assert.equal(renderOrdinaryTaskResult({...task,kind:'css_trial'}),null);
    }
    assert.equal(storage.size,0);
  }finally{globalThis.fetch=previousFetch;}
}));

test('unsaved and incomplete text survives route changes and refresh without crossing project or owner scope',()=>setup(async({storage,identity})=>{
  const state=service();let node=await state.render();field(node,'任务目标').value='Unsaved A objective';await field(node,'任务目标').fire('input');
  assert.equal(state.calls.filter(call=>call.body).length,0,'browser backup does not start a backend operation');
  const cached=JSON.parse(storage.get('hub:ordinary-draft:'+'c'.repeat(64)));
  assert.deepEqual(Object.keys(cached).sort(),['base_revision','spec']);assert.equal(cached.spec.scope,'');
  const other=service(context('b'));const b=await other.render({project_id:'b'});await fill(b,'Unsaved B','B scope');
  node=await state.render();assert.equal(field(node,'任务目标').value,'Unsaved A objective');assert.equal(field(node,'任务范围').value,'');
  assert.match(field(node,'普通任务状态').textContent,/已恢复浏览器/);
  state.context.draft_scope='f'.repeat(64);identity({owner_authenticated:true});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(field(node,'任务目标').value,'','new owner scope cannot inherit the prior owner backup or current form');
  assert.ok(storage.has('hub:ordinary-draft:'+'c'.repeat(64)));
  await fill(node,'Current owner draft','Current owner scope');await button(node,'保存任务草稿').fire('click');
  assert.equal(storage.has('hub:ordinary-draft:'+'f'.repeat(64)),false,'confirmed formal save clears only this scope backup');
  assert.ok(storage.has('hub:ordinary-draft:'+'e'.repeat(64)),'other project backup remains untouched');
}));

test('stale backup keeps its original base across edits and refresh until explicit application',()=>setup(async({storage})=>{
  const state=service();let node=await state.render();await fill(node,'Local unsaved text','Local scope');
  state.context.draft={...state.context.draft,revision:4,value:{...spec(),objective:'Newer formal draft'}};
  node=await state.render();assert.equal(field(node,'任务目标').value,'Local unsaved text');
  assert.equal(button(node,'保存任务草稿').disabled,true);assert.equal(button(node,'发送普通任务').disabled,true);
  assert.match(field(node,'普通任务状态').textContent,/基础版本.*不同/);
  await fill(node,'Still local text','Still local scope');
  assert.equal(JSON.parse(storage.get('hub:ordinary-draft:'+'c'.repeat(64))).base_revision,0);
  node=await state.render();assert.equal(button(node,'保存任务草稿').disabled,true);
  await button(node,'按当前版本应用本地草稿').fire('click');assert.equal(state.calls.filter(call=>call.body).length,0);
  assert.equal(field(node,'任务目标').value,'Still local text');await button(node,'保存任务草稿').fire('click');
  const command=state.calls.find(call=>call.path==='/api/ordinary-drafts').body;assert.equal(command.expected_revision,4);assert.equal(command.value.objective,'Still local text');
}));

test('unavailable browser backup and mismatched cached targets stay explicit without importing foreign text',()=>setup(async({storage})=>{
  storage.set('hub:ordinary-draft:'+'c'.repeat(64),JSON.stringify({base_revision:0,spec:spec('b')}));
  const state=service(),node=await state.render();assert.equal(field(node,'任务目标').value,'');assert.match(node.textContent,/备份与当前项目.*不一致/);
  globalThis.localStorage={getItem:()=>null,setItem:()=>{throw new Error('storage unavailable');},removeItem:()=>{}};
  await fill(node);assert.match(node.textContent,/本次未保存输入仅在当前页面/);assert.equal(field(node,'任务目标').value,'A precise objective');
  await button(node,'保存任务草稿').fire('click');assert.match(field(node,'普通任务状态').textContent,/已保存/);
}));


test('objective and scope expose separate required errors and clear only when their values are valid',()=>setup(async()=>{
  const state=service(),node=await state.render();
  const objective=field(node,'任务目标'),scope=field(node,'任务范围');
  assert.equal(objective.attrs['aria-required'],'true');assert.equal(scope.attrs['aria-required'],'true');
  assert.notEqual(objective.attrs['aria-describedby'],scope.attrs['aria-describedby']);
  for(const [control,label]of [[objective,'任务目标'],[scope,'任务范围']]){
    assert.equal(control.attrs['aria-invalid'],'true');
    const error=described(node,control);assert.ok(error);assert.equal(error.hidden,false);
    assert.equal(error.textContent,'请填写'+label+'。');
  }
  objective.value='Specific objective';await objective.fire('input');
  assert.equal(objective.attrs['aria-invalid'],'false');assert.equal(described(node,objective).hidden,true);
  assert.equal(scope.attrs['aria-invalid'],'true');assert.equal(button(node,'发送普通任务').disabled,true);
  assert.match(sendReason(node).textContent,/任务目标或任务范围中标明的问题/);
  scope.value='Read registered files only';await scope.fire('input');
  assert.equal(scope.attrs['aria-invalid'],'false');assert.equal(described(node,scope).hidden,true);
  assert.equal(button(node,'发送普通任务').disabled,false);
  assert.match(sendReason(node).textContent,/发送前会先保存同一份/);
  assert.equal(state.calls.filter(call=>call.body).length,0);
}));

test('field errors retain overlong and invalid text without silently truncating or saving it',()=>setup(async()=>{
  const state=service(),node=await state.render();
  await fill(node,'X'.repeat(2001),'Y'.repeat(1001));
  const objective=field(node,'任务目标'),scope=field(node,'任务范围');
  assert.equal(objective.value.length,2001);assert.equal(scope.value.length,1001);
  assert.match(described(node,objective).textContent,/最多 2000 字符.*当前输入保留/);
  assert.match(described(node,scope).textContent,/最多 1000 字符.*当前输入保留/);
  assert.equal(objective.disabled,false);assert.equal(scope.disabled,false);
  assert.equal(button(node,'保存任务草稿').disabled,true);assert.equal(button(node,'发送普通任务').disabled,true);
  await fill(node,'Objective\0suffix','   ');
  assert.match(described(node,objective).textContent,/无效字符/);assert.match(described(node,scope).textContent,/请填写任务范围/);
  assert.equal(objective.value,'Objective\0suffix');assert.equal(scope.value,'   ');
  assert.equal(state.calls.filter(call=>call.body).length,0);
}));

test('disabled-send description distinguishes identity readiness and stale input while retaining editable fields',()=>setup(async({identity})=>{
  const state=service({...context(),ready:false});const node=await state.render();await fill(node);
  assert.match(sendReason(node).textContent,/执行条件尚未就绪.*仍可保存草稿/);
  assert.equal(field(node,'任务目标').disabled,false);assert.equal(button(node,'保存任务草稿').disabled,false);
  state.owner=false;identity({owner_authenticated:false});
  assert.match(sendReason(node).textContent,/请连接所有者后发送/);
  assert.equal(field(node,'任务范围').disabled,false);
  state.owner=true;state.context.ready=true;state.context.input_version='b'.repeat(64);
  identity({owner_authenticated:true});await new Promise(resolve=>setImmediate(resolve));
  assert.match(sendReason(node).textContent,/输入版本.*变化.*重新读取任务输入/);
  assert.equal(field(node,'任务目标').value,'A precise objective');
  assert.equal(state.calls.filter(call=>call.body).length,0);
}));

test('feedback description IDs belong to each rendered form and survive draft reload',()=>setup(async()=>{
  const state=service(),first=await state.render();await fill(first);await button(first,'保存任务草稿').fire('click');
  const reopened=await state.render();
  for(const name of ['任务目标','任务范围']){
    const original=field(first,name),current=field(reopened,name);
    assert.notEqual(original.attrs['aria-describedby'],current.attrs['aria-describedby']);
    assert.ok(described(reopened,current));assert.equal(current.attrs['aria-invalid'],'false');
  }
  assert.notEqual(button(first,'发送普通任务').attrs['aria-describedby'],button(reopened,'发送普通任务').attrs['aria-describedby']);
  assert.ok(sendReason(reopened));assert.equal(field(reopened,'任务目标').value,'A precise objective');
  assert.equal(state.calls.filter(call=>call.path==='/api/tasks').length,0);
}));
