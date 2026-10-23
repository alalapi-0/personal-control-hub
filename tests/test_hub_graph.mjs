import './test_hub_actions.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {graphObjects,createGraphReader} from '../src/hub/web/directory.mjs';
import {objectKey,graphViewState,pendingGraphView,focusModel,createFocusGraph,relationText} from '../src/hub/web/graph.mjs';
function fixture(count=6,edgeCount=5){
  const objects=Array.from({length:count},(_,i)=>({id:`p/${i%2?'Artifact':'Task'}/${i}`,native_id:String(i),project_id:'p',type:i%2?'Artifact':'Task',revision:'r1',identity:i%3?'fact':'candidate',label:i===0?'<img src=x onerror=evil()>':'同名对象',source:{version:'v1',applicability:'current'},data:{}}));
  const relations=Array.from({length:edgeCount},(_,i)=>({id:`p/Relation/${i}`,native_id:String(i),project_id:'p',revision:'r1',type:'related_to',from:{id:objects[i%count].id,revision:'r1'},to:{id:objects[(i+1)%count].id,revision:'r1'},identity:'candidate',source:{version:'v1'},evidence:['公开合成依据'],run_id:null}));
  return {schema_version:'1.0',read_only:true,compatibility:{major:1,minimum_reader:'1.0'},project_id:'p',source_version:'a'.repeat(64),projection_version:'b'.repeat(64),objects,relations,layouts:[],unknown_types:{Goal:'无真实对象'},rows:objects.map(o=>({object_id:o.id,revision:o.revision,type:o.type,label:o.label,identity:o.identity,source_version:o.source.version,projection_version:'b'.repeat(64)}))};
}
function snapshot(n,e){return graphObjects(fixture(n,e),'p');}
// The same minimal DOM fixture pattern as the existing directory tests; no browser/socket.
class Node{
  constructor(tag,text=null){this.tag=tag;this.text=text;this.children=[];this.attrs={};this.events={};this.parentNode=null;}
  appendChild(n){this.children.push(n);n.parentNode=this;return n;}
  append(...ns){for(const n of ns)this.appendChild(n);}
  replaceChildren(...ns){for(const n of this.children)n.parentNode=null;this.children=[];this.append(...ns);}
  setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k]??null;}
  addEventListener(k,fn,options={}){(this.events[k]??=[]).push({fn,signal:options.signal});}
  fire(k,extra={}){const event={target:this,preventDefault(){this.prevented=true;},...extra};for(const {fn,signal} of this.events[k]||[])if(!signal?.aborted)fn(event);return event;}
  get textContent(){return this.text??this.children.map(n=>n.textContent).join('');}set textContent(v){this.text=String(v);this.children=[];}
  querySelectorAll(selector='[data-object-key]'){const key=selector==='[data-group-key]'?'data-group-key':'data-object-key';return walk(this).filter(n=>n.attrs[key]);}
  closest(selector='[data-object-key]'){const key=selector==='[data-group-key]'?'data-group-key':'data-object-key';let n=this;while(n){if(n.attrs[key])return n;n=n.parentNode;}return null;}
  contains(node){return walk(this).includes(node);}
  focus(){document.activeElement=this;}getBoundingClientRect(){return this.rect||{left:0,top:0,width:900,height:460};}
  setPointerCapture(id){this.captured=id;this.captureCount=(this.captureCount||0)+1;}hasPointerCapture(id){return this.captured===id;}releasePointerCapture(){this.captured=null;this.releaseCount=(this.releaseCount||0)+1;}
}
const walk=n=>[n,...n.children.flatMap(walk)];
function dom(fn){const previous=globalThis.document;globalThis.document={activeElement:null,createElement:t=>new Node(t),createElementNS:(_,t)=>new Node(t),createTextNode:t=>new Node('#text',t)};try{return fn();}finally{globalThis.document=previous;}}
const button=(root,label)=>walk(root).find(n=>n.tag==='button'&&n.textContent===label);

test('focus preserves exact entries, rows, relations and layouts without modifying source',()=>{
  const raw=fixture(),before=structuredClone(raw);raw.layouts=[{view_id:'focus',object_id:raw.objects[0].id,object_revision:'r1',x:99,y:88,width:240,height:100}];before.layouts=structuredClone(raw.layouts);
  const s=graphObjects(raw,'p'),v=graphViewState(s),m=focusModel(s,v);
  assert.equal(s.entries[0].object,raw.objects[0]);assert.equal(m.nodes[0].row,raw.rows[0]);assert.equal(m.edges[0],raw.relations[0]);assert.equal(s.layouts,raw.layouts);assert.equal(m.nodes[0].x,99);assert.deepEqual(raw,before);
});
test('same id different revisions have distinct keys and exact endpoint resolution',()=>{
  const raw=fixture();raw.objects[1]={...raw.objects[0],revision:'r2'};raw.rows[1]={...raw.rows[0],revision:'r2'};raw.relations=[];
  const s=graphObjects(raw,'p');assert.notEqual(s.entries[0].key,s.entries[1].key);assert.equal(focusModel(s,graphViewState(s)).nodes.length,6);
  raw.relations=[{...fixture().relations[0],from:{id:raw.objects[0].id,revision:'missing'}}];assert.throws(()=>graphObjects(raw,'p'),/INVALID/);
});
test('malformed relations, duplicate identity, foreign endpoints and unsafe layouts reject',()=>{
  for(const mutate of [g=>g.relations[0].project_id='foreign',g=>g.relations[0].to.revision='r2',g=>g.relations.push(g.relations[0]),g=>g.relations[0].type='exec',g=>g.layouts=[{view_id:'focus',object_id:g.objects[0].id,object_revision:'r1',x:Infinity,y:0,width:10,height:10}]]){const g=fixture();mutate(g);assert.throws(()=>graphObjects(g,'p'),/INVALID/);}
});
test('view state is project and projection bound, bounded and rejects arbitrary imported data',()=>{
  const s=snapshot(),input={projectId:'p',projectionVersion:s.projectionVersion,selected:s.entries[0].key,viewport:{x:Infinity,y:-1e9,zoom:100},grouping:'evil',collapsed:['Task',{},'Task'],pins:[{key:s.entries[0].key,x:1e8,y:-2},{key:'foreign',x:0,y:0}],relatedOnly:true};
  const v=graphViewState(s,input);assert.deepEqual(v.viewport,{x:0,y:-100000,zoom:3});assert.equal(v.grouping,'type');assert.deepEqual(v.collapsed,['Task']);assert.deepEqual(v.pins,[{key:s.entries[0].key,x:100000,y:0}]);
  assert.equal(graphViewState(s,{...input,projectId:'x'}).selected,null);assert.equal(graphViewState(s,{...input,projectionVersion:'c'.repeat(64)}).selected,null);assert.equal(graphViewState(s,{...input,selected:'absent'}).selected,null);
});
test('groups collapse locally, preserve entries and count cross-group references',()=>{
  const s=snapshot(),v=graphViewState(s),m=focusModel(s,v);assert.equal(m.groups.length,2);assert.equal(m.crossGroup,5);
  v.collapsed=['Task'];const folded=focusModel(s,v);assert.equal(folded.nodes.length,3);assert.equal(s.entries.length,6);assert.equal(folded.hiddenEdges,5);
  v.grouping='none';assert.equal(focusModel(s,v).nodes.length,6);
});
test('related focus filters exact endpoints and parent groups do not change dependencies',()=>{
  const raw=fixture(6,6);raw.relations[0].type='part_of';const s=graphObjects(raw,'p'),v=graphViewState(s);v.selected=s.entries[0].key;v.relatedOnly=true;
  assert.deepEqual(new Set(focusModel(s,v).nodes.map(n=>n.key)),new Set([s.entries[0].key,s.entries[1].key,s.entries[5].key]));v.grouping='parent';assert.ok(focusModel(s,v).groups.some(g=>g.id===s.entries[1].key));assert.equal(raw.relations[0].type,'part_of');
});
test('pin positions are local, dynamic labels increase size and layout never changes domain data',()=>{
  const raw=fixture();raw.objects[0].label='长标签'.repeat(60);raw.rows[0].label=raw.objects[0].label;raw.objects[0].data={domain_coordinates:[1,2,3]};const s=graphObjects(raw,'p'),v=graphViewState(s),before=structuredClone(raw);v.pins=[{key:s.entries[0].key,x:600,y:700}];
  const m=focusModel(s,v);assert.equal(m.nodes[0].x,600);assert.equal(m.nodes[0].y,700);assert.ok(m.nodes[0].height>m.nodes[1].height);assert.deepEqual(raw,before);
});
test('accepted management overlay remains candidate and relation direction is explicit',()=>{
  const edge={type:'blocks',identity:'candidate',management:{state:'accepted'}};assert.match(relationText(edge),/阻塞.*已确认管理覆盖.*仍非来源事实/);assert.match(relationText({type:'related_to',identity:'fact'}),/相关.*来源事实/);
});
test('SVG and list share exact selection, text safety and no automatic reference reads',()=>dom(()=>{
  const raw=fixture();raw.objects[0].data.reference_project_id='registered-target';const s=graphObjects(raw,'p');let selectedProject=null;
  const view=createFocusGraph(s,{onProject:id=>selectedProject=id});assert.equal(selectedProject,null);assert.ok(!walk(view.element).some(n=>['img','script'].includes(n.tag)));assert.match(view.element.textContent,/<img src=x onerror=evil/);
  view.select(s.entries[0].key);assert.equal(view.state().selected,s.entries[0].key);assert.equal(walk(view.element).filter(n=>n.attrs['data-object-key']===s.entries[0].key&&n.attrs['aria-pressed']==='true').length,2); // Both SVG and list expose the same exact selected key.
  assert.equal(selectedProject,null);button(view.element,'切换到登记项目').fire('click');assert.equal(selectedProject,'registered-target');view.dispose();assert.equal(view.element.children.length,0);
}));
test('keyboard navigation, pin, zoom, pan and reset have reachable text controls',()=>dom(()=>{
  const s=snapshot(),view=createFocusGraph(s);const svg=walk(view.element).find(n=>n.tag==='svg');svg.fire('keydown',{key:'ArrowRight'});assert.equal(view.state().selected,s.entries[0].key);svg.fire('keydown',{key:'ArrowRight',shiftKey:true});assert.equal(view.state().pins.length,1);svg.fire('keydown',{key:'p'});assert.equal(view.state().pins.length,0);svg.fire('keydown',{key:'+'});assert.ok(view.state().viewport.zoom>1);button(view.element,'右移').fire('click');assert.equal(view.state().viewport.x,60);button(view.element,'重置视图').fire('click');assert.deepEqual(view.state().viewport,{x:0,y:0,zoom:1});assert.equal(svg.fire('keydown',{key:'Tab'}).prevented,undefined);view.dispose();assert.doesNotThrow(()=>svg.fire('keydown',{key:'ArrowRight'}));
}));
test('pointer pan and node drag only update local viewport and pins',()=>dom(()=>{
  const s=snapshot(),before=structuredClone(s),view=createFocusGraph(s),svg=walk(view.element).find(n=>n.tag==='svg');
  svg.fire('pointerdown',{button:0,pointerId:1,clientX:10,clientY:10});svg.fire('pointermove',{pointerId:1,clientX:50,clientY:60});svg.fire('pointerup',{pointerId:1});assert.equal(view.state().viewport.x,40);
  const node=walk(svg).find(n=>n.attrs['data-object-key']);svg.fire('pointerdown',{target:node,button:0,pointerId:2,clientX:20,clientY:20});svg.fire('pointermove',{pointerId:2,clientX:40,clientY:50});svg.fire('pointerup',{pointerId:2});assert.equal(view.state().pins.length,1);assert.deepEqual(s,before);view.dispose();
}));
test('collapse keeps complete same-source list available and selection survives view grouping',()=>dom(()=>{
  const s=snapshot(),view=createFocusGraph(s);view.select(s.entries[0].key);const group=walk(view.element).find(n=>n.attrs.class==='focus-group');group.fire('click');assert.equal(view.state().selected,s.entries[0].key);assert.equal(walk(view.element).filter(n=>n.tag==='button'&&n.attrs['data-object-key']).length,6);assert.match(view.element.textContent,/因折叠或聚焦隐藏/);view.dispose();
}));
test('revision change drops stale local selection and pins, disposal clears all render data',()=>dom(()=>{
  const s=snapshot(),old=createFocusGraph(s);old.select(s.entries[0].key);const saved=old.state();old.dispose();const changed={...s,projectionVersion:'c'.repeat(64)},view=createFocusGraph(changed,{saved});assert.equal(view.state().selected,null);assert.equal(old.element.children.length,0);view.dispose();
}));
test('reader generation and explicit clear prevent stale graph after selection or logout',async()=>{
  const pending=[];const reader=createGraphReader(()=>new Promise(r=>pending.push(r)));const first=reader.select('p');reader.clear();pending[0](fixture());await first;assert.equal(reader.state.snapshot,null);assert.equal(reader.state.projectId,null);
});
test('synthetic 500 nodes 1000 edges layout stays bounded, complete and deterministic',()=>{
  const s=snapshot(500,1000),before=structuredClone(s),v=graphViewState(s),a=focusModel(s,v),b=focusModel(s,v);assert.equal(a.nodes.length,500);assert.equal(a.edges.length,1000);assert.deepEqual(a,b);assert.ok(a.nodes.every(n=>Number.isFinite(n.x)&&Number.isFinite(n.y)&&n.height>=90));assert.ok(a.height<100000);assert.deepEqual(s,before);dom(()=>{const rendered=createFocusGraph(s);assert.equal(walk(rendered.element).filter(n=>n.attrs.class?.startsWith('focus-node ')).length,500);assert.equal(walk(rendered.element).filter(n=>n.attrs.class?.startsWith('focus-edge')).length,1000);rendered.dispose();});
});


test('group and node taps do not capture pointers; threshold drag alone captures and releases',()=>dom(()=>{
  const s=snapshot(),view=createFocusGraph(s),svg=walk(view.element).find(n=>n.tag==='svg');
  const group=walk(svg).find(n=>n.attrs['data-group-key']);
  svg.fire('pointerdown',{target:group,button:0,pointerId:1,clientX:10,clientY:10});svg.fire('pointermove',{target:group,pointerId:1,clientX:90,clientY:10});svg.fire('pointerup',{pointerId:1});
  assert.equal(svg.captureCount,undefined);group.fire('click');assert.equal(view.state().collapsed.length,1);assert.equal(walk(svg).filter(n=>n.attrs.class?.startsWith('focus-node ')).length,3);
  const node=walk(svg).find(n=>n.attrs['data-object-key']);
  svg.fire('pointerdown',{target:node,button:0,pointerId:2,clientX:10,clientY:10});svg.fire('pointermove',{target:node,pointerId:2,clientX:12,clientY:12});svg.fire('pointerup',{pointerId:2});node.fire('click');
  assert.equal(svg.captureCount,undefined);assert.equal(view.state().selected,node.attrs['data-object-key']);assert.equal(view.state().pins.length,0);
  const current=walk(svg).find(n=>n.attrs['data-object-key']);svg.fire('pointerdown',{target:current,button:0,pointerId:3,clientX:10,clientY:10});svg.fire('pointermove',{pointerId:3,clientX:25,clientY:10});assert.equal(svg.captureCount,1);assert.equal(view.state().pins.length,1);svg.fire('pointercancel',{pointerId:3});assert.equal(svg.releaseCount,1);view.dispose();assert.equal(svg.releaseCount,1);
}));
test('dispose releases only its active captured pointer and aborts later events',()=>dom(()=>{
  const view=createFocusGraph(snapshot()),svg=walk(view.element).find(n=>n.tag==='svg');svg.fire('pointerdown',{button:0,pointerId:7,clientX:0,clientY:0});svg.fire('pointermove',{pointerId:7,clientX:20,clientY:10});view.dispose();assert.equal(svg.releaseCount,1);svg.fire('pointermove',{pointerId:7,clientX:40,clientY:10});assert.equal(svg.releaseCount,1);
}));
test('pending delayed history keeps bounded qualified selection until a fresh projection validates it',()=>{
  const s=snapshot(),key=s.entries[2].key,input={...graphViewState(s),selected:key,pins:[{key,x:55,y:66}],viewport:{x:-40,y:20,zoom:1.2}};
  let pending=input;for(let n=0;n<10;n++)pending=pendingGraphView(pending,'p');assert.deepEqual(pending,input);assert.deepEqual(graphViewState(s,pending),input);
  assert.equal(pendingGraphView(input,'other'),null);assert.equal(pendingGraphView({...input,projectionVersion:'bad'},'p'),null);assert.equal(pendingGraphView({...input,projectionVersion:[input.projectionVersion]},'p'),null);
  assert.equal(graphViewState({...s,projectionVersion:'c'.repeat(64)},pending).selected,null);
  const invalid={...input,selected:'private arbitrary body',pins:[{key:'not an object key',x:2,y:2}]};assert.equal(pendingGraphView(invalid,'p').selected,null);assert.deepEqual(pendingGraphView(invalid,'p').pins,[]);
});
test('measured mobile viewport retains CSS-sized labels and list selection recenters without SVG focus',()=>dom(()=>{
  const original=Node.prototype.getBoundingClientRect;Node.prototype.getBoundingClientRect=function(){return {left:0,top:0,width:319,height:360};};
  try{const s=snapshot(),view=createFocusGraph(s),svg=walk(view.element).find(n=>n.tag==='svg');assert.equal(svg.attrs.viewBox,'0 0 319 360');
    const key=s.entries[4].key,listed=walk(view.element).find(n=>n.tag==='button'&&n.attrs['data-object-key']===key);listed.fire('click');
    assert.equal(document.activeElement.tag,'button');assert.equal(document.activeElement.attrs['data-object-key'],key);const v=view.state(),node=focusModel(s,v).nodes.find(n=>n.key===key);assert.ok(node.x*v.viewport.zoom+v.viewport.x>=0);assert.ok((node.x+node.width)*v.viewport.zoom+v.viewport.x<=319);assert.equal(v.viewport.zoom,1);view.dispose();
  }finally{Node.prototype.getBoundingClientRect=original;}
}));
test('one resize observer updates viewport and is disconnected on dispose; late callback is inert',()=>dom(()=>{
  const previous=globalThis.ResizeObserver;const observers=[];globalThis.ResizeObserver=class{constructor(fn){this.fn=fn;observers.push(this);}observe(target){this.target=target;this.observed=(this.observed||0)+1;}disconnect(){this.disconnected=true;}};
  try{const view=createFocusGraph(snapshot()),observer=observers[0];assert.equal(observers.length,1);assert.equal(observer.observed,1);observer.target.rect={left:0,top:0,width:319,height:360};observer.fn();assert.equal(observer.target.attrs.viewBox,'0 0 319 360');view.dispose();assert.equal(observer.disconnected,true);assert.doesNotThrow(()=>observer.fn());}finally{globalThis.ResizeObserver=previous;}
}));
test('exact object focus restoration is project/revision bound and supports list without leaking selection',()=>dom(()=>{
  const s=snapshot(),view=createFocusGraph(s);view.select(s.entries[2].key,true);const identity=view.focusIdentity(document.activeElement);assert.equal(identity.key,s.entries[2].key);assert.equal(identity.surface,'svg');
  assert.equal(view.restoreFocus({...identity,projectId:'other'}),false);assert.equal(view.restoreFocus({...identity,projectionVersion:'c'.repeat(64)}),false);assert.equal(view.restoreFocus({...identity,key:'missing'}),false);
  assert.equal(view.restoreFocus({...identity,surface:'list'}),true);assert.equal(document.activeElement.tag,'button');assert.equal(view.focusIdentity(document.activeElement).surface,'list');assert.equal(view.focusIdentity(new Node('input')),null);view.dispose();
}));

test('graph and list selected detail share one exact action entry with disposal',()=>dom(()=>{
 const s=snapshot();let shown=[],closed=0;const view=createFocusGraph(s,{renderActions:(entry,snap)=>{shown.push([entry,snap]);return {element:document.createElement('aside'),dispose:()=>closed++};}});
 view.select(s.entries[1].key);assert.equal(shown[0][0],s.entries[1]);assert.equal(shown[0][1],s);
 const list=walk(view.element).find(n=>n.tag==='button'&&n.attrs['data-object-key']===s.entries[2].key);list.fire('click');assert.equal(shown[1][0],s.entries[2]);assert.equal(closed,1);view.dispose();assert.equal(closed,2);
}));

function publishedImpactFixture(){
 const raw=fixture(6,2);raw.relations[0].type='produces';raw.relations[1].type='derived_from';
 const source={ref:'source:p',version:'v1',sha256:'a'.repeat(64),fact_time:null,collected_at:'2026-10-24T00:00:00Z',origin:'source_fact',applicability:'current',unknown_reason:'No fact timestamp is published.'};
 for(const object of raw.objects)object.source={...source,origin:object.identity==='candidate'?'hub_management':'source_fact'};
 for(const edge of raw.relations)edge.source={...source,origin:'hub_management'};
 const ref=o=>({id:o.id,revision:o.revision}),edge=raw.relations[0];
 raw.objects[0].data.impact={schema_version:'graph-impact/1',subject:ref(raw.objects[0]),state:'available',fact_paths:[],candidate_paths:[{target:ref(raw.objects[1]),path:[{relation:ref(edge),type:edge.type,from:{...edge.from},to:{...edge.to},evidence:[...edge.evidence],source:{...edge.source}}]}],gaps:['NO_PUBLISHED_SOURCE_FACT_IO'],traversal_complete:true,scope:'published_exact_version_io_only',rerun:{available:false,reason:'NO_REPLAY_AUTHORITY_OR_EFFECT_RECONCILIATION'}};
 return raw;
}
test('impact panel renders one backend projection for graph/list and only selects exact targets',()=>dom(()=>{
 const raw=publishedImpactFixture(),before=structuredClone(raw),s=graphObjects(raw,'p');let projects=0,actionExecutions=0;
 const view=createFocusGraph(s,{onProject:()=>projects++,renderActions:()=>{const action=document.createElement('button');action.addEventListener('click',()=>actionExecutions++);return {element:action,dispose(){}};}});
 view.select(s.entries[0].key);const detail=walk(view.element).find(n=>n.attrs.className==='focus-graph-detail'||n.className==='focus-graph-detail');
 const textBefore=view.element.textContent;assert.match(textBefore,/候选声明路径（非真实消费者证明） · 1/);assert.match(textBefore,/缺少真实来源 IO 事实/);assert.match(textBefore,/重跑不可用/);
 const rerun=button(view.element,'重跑不可用');assert.equal(rerun.getAttribute('disabled'),'true');assert.equal(rerun.events.click,undefined);
 const target=button(view.element,'查看影响对象：'+s.entries[1].row.label);target.focus();target.fire('click');assert.equal(document.activeElement.getAttribute('data-object-key'),s.entries[1].key);assert.equal(view.state().selected,s.entries[1].key);assert.equal(projects,0);assert.equal(actionExecutions,0);
 const listed=walk(view.element).find(n=>n.tag==='button'&&n.attrs['data-object-key']===s.entries[0].key);listed.fire('click');assert.match(view.element.textContent,/候选声明路径（非真实消费者证明） · 1/);assert.deepEqual(raw,before);view.dispose();
}));
test('impact consumer keeps exact same-id revisions and fact/candidate categories from backend',()=>dom(()=>{
 const raw=publishedImpactFixture();raw.objects[2]={...raw.objects[1],revision:'r2',data:{}};raw.rows[2]={...raw.rows[1],revision:'r2'};
 raw.relations[1].to={id:raw.objects[2].id,revision:'r2'};
 for(const object of raw.objects){object.identity='fact';object.source.origin='source_fact';}for(const row of raw.rows)row.identity='fact';raw.relations[0].identity='fact';raw.relations[0].source.origin='source_fact';
 raw.objects[0].data.impact.candidate_paths[0].path[0].source={...raw.relations[0].source};
 const impact=raw.objects[0].data.impact;impact.fact_paths=impact.candidate_paths;impact.candidate_paths=[];impact.gaps=[];
 const s=graphObjects(raw,'p'),view=createFocusGraph(s);view.select(s.entries[0].key);assert.match(view.element.textContent,/来源事实路径 · 1/);
 button(view.element,'查看影响对象：'+s.entries[1].row.label).fire('click');assert.equal(view.state().selected,s.entries[1].key);assert.notEqual(view.state().selected,s.entries[2].key);view.dispose();
}));
test('old or malformed impact stays unknown; changed snapshot and disposal clear all old impact controls',()=>dom(()=>{
 const raw=publishedImpactFixture(),s=graphObjects(raw,'p'),view=createFocusGraph(s);view.select(s.entries[0].key);
 const old=button(view.element,'查看影响对象：'+s.entries[1].row.label),saved=view.state();view.dispose();assert.doesNotThrow(()=>old.fire('click'));assert.equal(view.element.children.length,0);
 const changed=fixture();changed.projection_version='c'.repeat(64);changed.rows.forEach(r=>r.projection_version=changed.projection_version);
 const next=createFocusGraph(graphObjects(changed,'p'),{saved});assert.equal(next.state().selected,null);assert.equal(button(next.element,'查看影响对象：'+s.entries[1].row.label),undefined);
 next.select(objectKey(changed.objects[0]));assert.match(next.element.textContent,/没有可核对的影响投影/);next.dispose();
 for(const mutate of [v=>v.subject.revision='foreign',v=>v.candidate_paths[0].target.revision='missing',v=>v.candidate_paths[0].path[0].relation.id='unknown',v=>v.rerun.available=true]){const bad=publishedImpactFixture();mutate(bad.objects[0].data.impact);const v=createFocusGraph(graphObjects(bad,'p'));v.select(objectKey(bad.objects[0]));assert.match(v.element.textContent,/没有可核对的影响投影/);v.dispose();}
}));

function factImpactFixture(){const raw=publishedImpactFixture();for(const object of raw.objects){object.identity='fact';object.source.origin='source_fact';}for(const row of raw.rows)row.identity='fact';raw.relations[0].identity='fact';raw.relations[0].source.origin='source_fact';const value=raw.objects[0].data.impact;value.candidate_paths[0].path[0].source={...raw.relations[0].source};value.fact_paths=value.candidate_paths;value.candidate_paths=[];value.gaps=[];return raw;}
const certificateStep=(edge,from=edge.from,to=edge.to)=>({relation:{id:edge.id,revision:edge.revision},type:edge.type,from:{...from},to:{...to},evidence:[...edge.evidence],source:{...edge.source}});
test('impact verifies canonical consumes/derived reverse direction and continuous multihop without traversing new paths',()=>dom(()=>{
 const raw=factImpactFixture(),a=raw.objects[0],b=raw.objects[1],c=raw.objects[2],first=raw.relations[0],second=raw.relations[1];
 first.type='consumes';first.from={id:b.id,revision:b.revision};first.to={id:a.id,revision:a.revision};
 second.type='derived_from';second.from={id:c.id,revision:c.revision};second.to={id:b.id,revision:b.revision};second.identity='fact';second.source.origin='source_fact';
 raw.objects[0].data.impact.fact_paths=[{target:{id:c.id,revision:c.revision},path:[certificateStep(first,first.to,first.from),certificateStep(second,second.to,second.from)]}];
 const before=structuredClone(raw),s=graphObjects(raw,'p'),view=createFocusGraph(s);view.select(s.entries[0].key);assert.match(view.element.textContent,/来源事实路径 · 1/);assert.equal(button(view.element,'查看影响对象：'+c.label)!==undefined,true);assert.deepEqual(raw,before);view.dispose();
}));
test('stale forged or malformed impact certificates safely become unknown rather than facts or render exceptions',()=>dom(()=>{
 const mutations=[
  ['wrong type',r=>r.objects[0].data.impact.fact_paths[0].path[0].type='consumes'],
  ['canonical non IO',r=>r.relations[0].type='references'],
  ['reverse',r=>{const p=r.objects[0].data.impact.fact_paths[0].path[0];[p.from,p.to]=[p.to,p.from];}],
  ['canonical endpoint',r=>r.relations[0].to={id:r.objects[2].id,revision:r.objects[2].revision}],
  ['wrong target',r=>r.objects[0].data.impact.fact_paths[0].target={id:r.objects[2].id,revision:r.objects[2].revision}],
  ['stale revision',r=>r.objects[0].data.impact.fact_paths[0].path[0].relation.revision='stale'],
  ['evidence string',r=>r.objects[0].data.impact.fact_paths[0].path[0].evidence='private forged body'],
  ['evidence differs',r=>r.objects[0].data.impact.fact_paths[0].path[0].evidence=['forged']],
  ['source malformed',r=>r.objects[0].data.impact.fact_paths[0].path[0].source='forged'],
  ['source differs',r=>r.objects[0].data.impact.fact_paths[0].path[0].source.sha256='f'.repeat(64)],
  ['source extra body',r=>r.objects[0].data.impact.fact_paths[0].path[0].source.body='not publishable'],
  ['candidate edge promoted',r=>r.relations[0].identity='candidate'],
  ['candidate object promoted',r=>{r.objects[1].identity='candidate';r.rows[1].identity='candidate';r.relations[0].identity='candidate';}],
  ['unknown applicability',r=>{r.relations[0].source.applicability='unknown';r.objects[0].data.impact.fact_paths[0].path[0].source.applicability='unknown';}],
  ['missing version',r=>{r.relations[0].source.version=null;r.objects[0].data.impact.fact_paths[0].path[0].source.version=null;}],
  ['missing hash',r=>{r.objects[1].source.sha256=null;}],
  ['registry origin facts',r=>{r.relations[0].source.origin='registry';r.objects[0].data.impact.fact_paths[0].path[0].source.origin='registry';}],
  ['discontinuous',r=>{const e=r.relations[1];e.type='produces';e.from={id:r.objects[3].id,revision:r.objects[3].revision};e.to={id:r.objects[4].id,revision:r.objects[4].revision};e.identity='fact';e.source.origin='source_fact';const p=r.objects[0].data.impact.fact_paths[0];p.path.push(certificateStep(e));p.target={...e.to};}],
 ];
 for(const [name,mutate]of mutations){const raw=factImpactFixture();mutate(raw);const before=structuredClone(raw),s=graphObjects(raw,'p'),view=createFocusGraph(s);assert.doesNotThrow(()=>view.select(s.entries[0].key),name);assert.match(view.element.textContent,/没有可核对的影响投影/,name);assert.equal(walk(view.element).some(n=>n.tag==='button'&&n.textContent.startsWith('查看影响对象：')),false,name);assert.deepEqual(raw,before,name);view.dispose();}
 const raw=publishedImpactFixture(),value=raw.objects[0].data.impact;value.fact_paths=value.candidate_paths;value.candidate_paths=[];const s=graphObjects(raw,'p'),view=createFocusGraph(s);view.select(s.entries[0].key);assert.match(view.element.textContent,/没有可核对的影响投影/);view.dispose();
}));
test('Enter activation restores exact target focus on SVG or same-source list when its group is collapsed',()=>dom(()=>{
 for(const collapsed of [false,true]){const s=graphObjects(publishedImpactFixture(),'p'),view=createFocusGraph(s);view.select(s.entries[0].key);
  if(collapsed)walk(view.element).find(n=>n.getAttribute('data-group-key')==='Artifact').fire('click');
  const target=button(view.element,'查看影响对象：'+s.entries[1].row.label);target.focus();const event=target.fire('keydown',{key:'Enter'});if(!event.prevented)target.fire('click',{detail:0}); // Native button Enter activation, without a browser fixture.
  assert.equal(view.state().selected,s.entries[1].key);assert.equal(document.activeElement.getAttribute('data-object-key'),s.entries[1].key);assert.equal(document.activeElement.tag,collapsed?'button':'g');assert.ok(view.element.contains(document.activeElement));view.dispose();
 }
}));
