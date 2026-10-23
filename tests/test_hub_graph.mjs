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
