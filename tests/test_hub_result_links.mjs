import assert from 'node:assert/strict';
import test from 'node:test';
import {renderTasks,renderBusinessTaskPreview,taskReadPath,taskResultLink,taskResultQuery} from '../src/hub/web/tasks.js';
import {renderMaterials,materialLink,materialVersionQuery} from '../src/hub/web/materials.js';

const V='a'.repeat(64),NEW='b'.repeat(64),MID='material-'+'c'.repeat(32);
class N{
  constructor(tag,text=null){this.tag=tag;this.text=text;this.children=[];this.attrs={};this.listeners={};this.parent=null;this.isConnected=true;this.disabled=false;}
  appendChild(child){if(child===null||child===undefined)return child;child.parent=this;this.children.push(child);return child;}
  append(...children){for(const child of children)this.appendChild(typeof child==='string'?new N('#text',child):child);}
  get lastChild(){return this.children.at(-1);}
  prepend(child){child.parent=this;this.children.unshift(child);}
  replaceChildren(...children){this.children=[];this.text=null;this.append(...children);}
  setAttribute(key,value){this.attrs[key]=value;}
  getAttribute(key){return this.attrs[key]??null;}
  removeAttribute(key){delete this.attrs[key];}
  addEventListener(type,handler){this.listeners[type]=handler;}
  async click(){return this.listeners.click?.();}
  focus(){}
  remove(){this.parent.children=this.parent.children.filter(child=>child!==this);}
  set textContent(value){this.text=value;this.children=[];}
  get textContent(){return this.text??this.children.map(child=>child.textContent).join('');}
  querySelectorAll(selector){return walk(this).slice(1).filter(node=>{
    if(selector==='button')return node.tag==='button';
    const match=selector.match(/^\[([^=]+)="([^"]+)"\]$/);return match&&node.attrs[match[1]]===match[2];
  });}
  querySelector(selector){return this.querySelectorAll(selector)[0]??null;}
}
const walk=node=>[node,...node.children.flatMap(walk)];
const button=(node,label)=>walk(node).find(n=>n.tag==='button'&&n.textContent===label);
const link=(node,label)=>walk(node).find(n=>n.tag==='a'&&n.textContent===label);
function task(status='checks_complete'){
  return {id:'task-a',project:'project-a',kind:'ordinary_project',classification:'synthetic_fixture',
    status,mode:'new',parent:null,thread_id:'thread-a',turn_id:'turn-a',provider_status:'completed',
    task_spec:{project_id:'project-a',objective:'Original objective',scope:'Read only'},
    result_version:V,result:{response:'Original saved reply',checks:[]},human_acceptance:'pending',cancel:false};
}
const response=data=>({ok:true,status:200,headers:{get:()=> 'text/plain; charset=utf-8'},json:async()=>({ok:true,data}),text:async()=> 'Registered text'});
const stale=()=>({ok:false,status:409,json:async()=>({ok:false,error:{code:'RESULT_VERSION_STALE',outcome:'NOT_COMMITTED'}})});
async function setup(run){
  const names=['document','window','fetch','navigator','setTimeout'];
  const previous=Object.fromEntries(names.map(name=>[name,Object.getOwnPropertyDescriptor(globalThis,name)]));
  const timers=[],calls=[];let reader=()=>response({task:task(),controls:[],events:[]});
  globalThis.document={createElement:tag=>new N(tag),createTextNode:text=>new N('#text',text),hidden:false};
  globalThis.window={addEventListener(){},removeEventListener(){}};
  Object.defineProperty(globalThis,'navigator',{configurable:true,value:{onLine:true}});
  const nativeTimeout=globalThis.setTimeout;
  globalThis.setTimeout=(fn,delay)=>delay===1000||delay===5000?(timers.push(fn),timers.length):nativeTimeout(fn,delay);
  globalThis.fetch=async(url,options)=>{calls.push({url,options});return url==='/api/guest'?response({csrf_token:'synthetic'}):reader(url,options);};
  try{await run({root:new N('main'),calls,timers,setReader:value=>{reader=value;}});}
  finally{for(const name of names){if(previous[name])Object.defineProperty(globalThis,name,previous[name]);else delete globalThis[name];}}
}

test('route-local version validation refuses empty duplicate malformed and foreign query keys',()=>{
  assert.equal(taskResultQuery(new URLSearchParams('result_version='+V)),V);
  assert.equal(materialVersionQuery(new URLSearchParams('version='+V)),V);
  assert.equal(taskResultQuery(new URLSearchParams()),null);assert.equal(materialVersionQuery(new URLSearchParams()),null);
  for(const [read,key]of [[taskResultQuery,'result_version'],[materialVersionQuery,'version']]){
    for(const query of [key+'=',key+'=not-sha',key+'='+V+'&'+key+'='+V,key+'='+V+'&foreign=yes'])
      assert.throws(()=>read(new URLSearchParams(query)),error=>error.code==='INVALID_QUERY'&&error.status===400);
  }
  assert.equal(taskResultLink(task()),'#tasks/task-a?result_version='+V);
  assert.equal(taskResultLink({...task(),result_version:null}),'#tasks/task-a');
  assert.equal(taskReadPath('task-a',V),'/api/tasks/task-a?expected_result_version='+V);
  assert.equal(materialLink({id:MID,version:V}),'#materials/'+MID+'?version='+V);
});

test('fixed task initial stale response never displays a newer result and offers explicit current task',()=>setup(async({root,calls,setReader})=>{
  setReader(()=>stale());await renderTasks(root,{taskId:'task-a',expectedResultVersion:V});
  assert.match(root.textContent,/旧正文无法.*恢复/);assert.equal(link(root,'查看当前任务').attrs.href,'#tasks/task-a');
  assert.equal(calls.at(-1).url,taskReadPath('task-a',V));assert.doesNotMatch(root.textContent,/Original saved reply/);
}));

test('fixed task refresh preserves old displayed reply on stale and retains the original request version',()=>setup(async({root,calls,setReader})=>{
  await renderTasks(root,{taskId:'task-a',expectedResultVersion:V});
  assert.match(root.textContent,/Original saved reply/);setReader(()=>stale());
  await button(root,'重新对账').click();assert.match(root.textContent,/原结果仍保留/);
  assert.match(root.textContent,/Original saved reply/);
  assert.equal(calls.filter(call=>call.url.includes('/api/tasks/')).every(call=>call.url===taskReadPath('task-a',V)),true);
  await button(root,'重新核对固定结果').click();assert.equal(calls.at(-1).url,taskReadPath('task-a',V));
}));

test('automatic pinned observation uses original version and stops on stale without overwriting the reply',()=>setup(async({root,calls,timers,setReader})=>{
  setReader(()=>response({task:task('running'),controls:[],events:[]}));
  await renderTasks(root,{taskId:'task-a',expectedResultVersion:V});assert.equal(timers.length,1);
  setReader(()=>stale());await timers.shift()();assert.match(root.textContent,/原结果仍保留/);
  assert.match(root.textContent,/Original saved reply/);assert.equal(timers.length,0);
  assert.equal(calls.at(-1).url,taskReadPath('task-a',V));
}));

test('review mutation rerenders the same pinned result and unpinned running observation remains live',()=>setup(async({root,calls,timers,setReader})=>{
  await renderTasks(root,{taskId:'task-a',expectedResultVersion:V});await button(root,'暂缓验收').click();
  assert.ok(calls.some(call=>call.url==='/api/tasks/task-a/review'&&call.options.method==='POST'));
  assert.equal(calls.at(-1).url,taskReadPath('task-a',V));
  setReader(()=>response({task:task('running'),controls:[],events:[]}));await renderTasks(root,{taskId:'task-a'});
  await timers.shift()();assert.equal(calls.at(-1).url,'/api/tasks/task-a');
}));

const material=()=>({id:MID,version:V,project_id:'project-a',project_name:'Synthetic A',candidate_id:'candidate-a',
  candidate_revision:1,artifact_id:'registered-report',kind:'text',available:true});
function materialsReader(url){return url==='/api/materials'?response({materials:[material()],store_revision:1,classification:'synthetic_fixture'}):response(null);}

test('material list links pin identity; missing or stale version never reads content and returns to its real project',()=>setup(async({root,calls,setReader})=>{
  setReader(materialsReader);await renderMaterials(root);
  assert.equal(walk(root).find(node=>node.tag==='a'&&node.attrs.href?.startsWith('#materials/')).attrs.href,materialLink(material()));
  for(const expectedVersion of [null,NEW]){
    calls.length=0;await renderMaterials(root,{materialId:MID,expectedVersion});
    assert.equal(calls.some(call=>call.url.startsWith('/api/materials/')),false);
    assert.equal(link(root,'打开当前登记版本').attrs.href,materialLink(material()));
    assert.equal(link(root,'返回项目来源与产物').attrs.href,'#projects/project-a/sources');
    assert.equal(link(root,'查看原审核记录').attrs.href,'#designs/project-a/candidate-a');
  }
}));

test('matching material version reads registered bytes and explicit reload cannot silently advance versions',()=>setup(async({root,calls,setReader})=>{
  setReader(materialsReader);await renderMaterials(root,{materialId:MID,expectedVersion:V});
  assert.match(root.textContent,/Registered text/);assert.ok(calls.some(call=>call.url==='/api/materials/'+MID+'?version='+V));
  setReader(url=>url==='/api/materials'?response({materials:[{...material(),version:NEW}],store_revision:2}):response(null));
  calls.length=0;await button(root,'重新核对产物').click();
  assert.match(root.textContent,/固定产物版本已变化/);assert.doesNotMatch(root.textContent,/Registered text/);
  assert.equal(calls.some(call=>call.url.startsWith('/api/materials/')),false);
  assert.equal(link(root,'打开当前登记版本').attrs.href,'#materials/'+MID+'?version='+NEW);
}));

const storyBinding={workspace_revision:1,workspace_sha256:V,request_id:'story-request',request_sha256:V,output_sha256:NEW};
const storyTask=()=>({...task(),kind:'story_exchange',classification:'real',result:{source_promoted:true,
  source_binding:{...storyBinding},output_sha256:NEW},task_spec:{requirement:'Original writing brief',scope:'Read-only preview',acceptance:'Three cards'}});
const storyPreview=()=>({kind:'story_exchange',classification:'real',current:true,binding:{...storyBinding},
  cards:Array.from({length:3},(_,i)=>({id:'card-'+i,title:'Original candidate '+i,
    ...Object.fromEntries(['goal','obstacle','action','choice','consequence','difference','strength','weakness'].map(key=>[key,'Original '+key]))}))});

test('pinned story cards remain undisplayed until binding and second version read both pass',()=>setup(async({root,calls,setReader})=>{
  let recheckStarted,releaseRecheck;
  const started=new Promise(resolve=>{recheckStarted=resolve;}),gate=new Promise(resolve=>{releaseRecheck=resolve;});
  setReader(url=>url.endsWith('/preview')?response(storyPreview()):(recheckStarted(),gate));
  const pending=renderBusinessTaskPreview(root,storyTask(),{expectedResultVersion:V});
  await started;assert.equal(walk(root).filter(node=>node.tag==='article').length,0);
  assert.equal(calls.at(-1).url,taskReadPath('task-a',V));
  releaseRecheck(response({task:storyTask()}));await pending;
  assert.equal(walk(root).filter(node=>node.tag==='article').length,3);
}));

test('pinned story preview rejects changed bindings or a stale second read without flashing cards',()=>setup(async({root,calls,setReader})=>{
  setReader(()=>response({...storyPreview(),binding:{...storyBinding,request_id:'another-request'}}));
  const mismatched=await renderBusinessTaskPreview(root,storyTask(),{expectedResultVersion:V});
  assert.equal(mismatched,false);assert.equal(walk(root).filter(node=>node.tag==='article').length,0);
  assert.match(root.textContent,/没有展示其他版本/);assert.equal(calls.some(call=>call.url.includes('expected_result_version')),false);
  root.replaceChildren();let staleReported=false;
  setReader(url=>url.endsWith('/preview')?response(storyPreview()):stale());
  await renderBusinessTaskPreview(root,storyTask(),{expectedResultVersion:V,onVersionStale:()=>{staleReported=true;}});
  assert.equal(staleReported,true);assert.equal(walk(root).filter(node=>node.tag==='article').length,0);
}));

for(const [label,status,kind,decision]of [
  ['提交补充输入','waiting_input','input','answer'],
  ['仅本次允许','waiting_approval','approval','accept'],
  ['拒绝本次请求','waiting_approval','approval','decline'],
  ['取消这个任务','running',null,null],
])test('fixed task '+label+' preserves expected version across the actual action callback',()=>setup(async({root,calls,setReader})=>{
  const control={id:'control-a',version:1,kind,status:'pending',expires_at:Date.now()/1000+60,
    thread_id:'thread-a',turn_id:'turn-a',allowed_decisions:kind==='input'?['answer','decline']:['accept','decline'],
    questions:[{id:'question-a',header:'Focus',question:'Which focus?',options:[]}]};
  setReader(()=>response({task:task(status),events:[],controls:kind?[control]:[]}));
  await renderTasks(root,{taskId:'task-a',expectedResultVersion:V});
  if(kind==='input')walk(root).find(node=>node.tag==='textarea').value='Original answer';
  await button(root,label).click();
  const mutation=calls.find(call=>call.options.method==='POST');assert.ok(mutation);
  assert.equal(mutation.url,'/api/tasks/task-a/'+(kind?'respond':'cancel'));
  if(decision)assert.equal(JSON.parse(mutation.options.body).decision,decision);
  assert.equal(calls.at(-1).url,taskReadPath('task-a',V));
}));

test('an unrelated HTTP409 is not mislabeled as fixed result staleness',()=>setup(async({root,setReader})=>{
  setReader(()=>({ok:false,status:409,json:async()=>({ok:false,error:{code:'TASK_SCOPE_CHANGED',outcome:'NOT_COMMITTED'}})}));
  await assert.rejects(renderTasks(root,{taskId:'task-a',expectedResultVersion:V}),error=>error.code==='TASK_SCOPE_CHANGED');
  assert.doesNotMatch(root.textContent,/固定任务结果已变化/);
}));

const defaultText=node=>node.tag==='details'?node.children.find(n=>n.tag==='summary')?.textContent||'':(node.text??node.children.map(defaultText).join(''));
test('ordinary result titles use full bound goal with exact metadata retained only in collapsed details',()=>setup(async({root,setReader,calls})=>{
  const original={...task(),id:'request-unique',thread_id:'thread-unique',turn_id:'turn-unique',parent:'parent-unique',
    annotation_id:'annotation-unique',annotation_revision:13,
    feedback:{sequence:2,parent_binding:{turn_id:'bound-turn-unique'},writer_status:'known'},
    task_spec:{...task().task_spec,parent_task_id:'parent-unique',objective:'😀'.repeat(130)+'\nOriginal full goal'}};
  const before=structuredClone(original);setReader(()=>response({task:original,controls:[],events:[]}));
  await renderTasks(root,{taskId:original.id,expectedResultVersion:V});
  const visible=defaultText(root);
  for(const value of ['request-unique','thread-unique','turn-unique','completed','parent-unique','annotation-unique','bound-turn-unique'])
    assert.equal(visible.includes(value),false,value+' must not be default metadata');
  assert.match(visible,/执行已完成|检查完成/);assert.match(visible,/模式：新建会话/);assert.match(visible,/排队意见已执行/);
  assert.equal(walk(root).find(n=>n.tag==='h2').textContent,'😀'.repeat(119)+'…');
  assert.ok(visible.includes(original.task_spec.objective));assert.ok(visible.includes(original.result.response));
  const details=walk(root).find(n=>n.tag==='details'&&n.children[0].textContent==='任务技术详情');
  assert.equal(details.attrs.open,undefined);
  assert.deepEqual(JSON.parse(details.children[1].textContent),{request_id:original.id,thread_id:original.thread_id,turn_id:original.turn_id,
    provider_status:original.provider_status,parent:original.parent,parent_task_id:original.task_spec.parent_task_id,
    annotation_id:original.annotation_id,annotation_revision:13,feedback:original.feedback});
  assert.equal(link(root,'本次结果固定链接').attrs.href,taskResultLink(original));
  assert.equal(calls[0].url,taskReadPath(original.id,V));assert.deepEqual(original,before);
}));
test('ordinary list cards name goals while legacy cards and identity links stay exact',()=>setup(async({root,setReader})=>{
  const ordinary=task(),legacy={...task(),id:'legacy-request',kind:'legacy',project:'legacy-project'};
  setReader(()=>response({tasks:[ordinary,legacy],tasks_available:true}));await renderTasks(root);
  const ordinaryLink=walk(root).find(n=>n.tag==='a'&&n.attrs.href===taskResultLink(ordinary));
  assert.equal(ordinaryLink.children[0].textContent,ordinary.task_spec.objective);assert.equal(defaultText(ordinaryLink).includes(ordinary.id),false);
  const legacyLink=walk(root).find(n=>n.tag==='a'&&n.attrs.href===taskResultLink(legacy));
  assert.equal(legacyLink.children[0].textContent,'legacy-project');assert.match(legacyLink.textContent,/legacy-request/);
}));
