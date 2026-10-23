import "./test_hub_graph.mjs";
import assert from 'node:assert/strict';
import test from 'node:test';
import {projectDirectory,projectSearchMatch,renderDirectoryTree,overviewProject,overviewActivity,renderOverviewCard,renderOverviewFocus,graphObjects,createGraphReader,renderGraphObjects} from '../src/hub/web/directory.mjs';
import {attention,freshness} from '../src/hub/web/connection_view.mjs';

const statuses=['active','paused','blocked','complete','unknown'];
const types=['internal_control_plane','governance_program','code','creative','document'];
const projects=Array.from({length:257},(_,i)=>({
  project_id:`project-${i}`,name:`项目 ${i} ${i%2?'Alpha':'Beta'}`,
  business:{normalized_status:statuses[i%statuses.length]},
  declared:{project_type:types[i%types.length],connection_read_allowed:true,
    hub_connection_exception:i%11===0?{reason:'已登记'}:null},
  freshness:{state:i%3===0?'fresh':i%3===1?'stale':'unknown',
    local_presence:i%17===0?'removed_local':'present',authority_drift:i%13===0},
  operational:{latest_attempt:i%19===0?{success:false}:null},
}));

test('complete directory uses one ordered projection for every filter and grouping combination',()=>{
  const before=structuredClone(projects);
  for(const query of ['', 'ALPHA','PROJECT-25',' 项目 1 ','absent'])
    for(const status of ['',...statuses])
      for(const source of ['','attention','fresh'])
        for(const groupBy of ['none','status','freshness','type']){
          const view=projectDirectory(projects,{query,status,freshness:source,groupBy});
          const q=query.trim().toLocaleLowerCase();
          const expected=projects.filter(p=>(!q||`${p.name} ${p.project_id}`.toLocaleLowerCase().includes(q))
            &&(!status||p.business.normalized_status===status)
            &&(!source||(source==='attention'?attention(p):p.freshness.state==='fresh')));
          assert.equal(view.total,257);assert.equal(view.matched,expected.length);
          assert.deepEqual(view.rows,expected);
          assert.ok(view.rows.every((row,i)=>row===expected[i]));
          const grouped=view.groups.flatMap(group=>group.rows);
          assert.equal(grouped.length,expected.length);
          assert.equal(new Set(grouped.map(row=>row.project_id)).size,expected.length);
          assert.deepEqual(new Set(grouped),new Set(expected));
          const groupKeys=[];
          const key=p=>groupBy==='status'?p.business.normalized_status:groupBy==='type'?p.declared.project_type:groupBy==='freshness'?freshness(p):'all';
          for(const p of expected)if(!groupKeys.includes(key(p)))groupKeys.push(key(p));
          assert.deepEqual(view.groups.map(group=>group.key),groupKeys);
          for(const group of view.groups){
            assert.match(group.label,/[\u3400-\u9fff]/);
            assert.deepEqual(group.rows,expected.filter(p=>key(p)===group.key));
          }
        }
  assert.deepEqual(projects,before);
});

test('empty, unknown, removed and management identity survive without guessed relationships',()=>{
  assert.deepEqual(projectDirectory([]),{rows:[],groups:[],total:0,matched:0,matches:new Map()});
  const unknown={project_id:'unknown',name:'未知项目',business:{normalized_status:'novel'},declared:{project_type:'novel'}};
  const removed=structuredClone(projects[0]);removed.project_id='removed';
  const input=[unknown,removed,{project_id:'minimal'}];
  assert.deepEqual(projectDirectory(input).rows,input);
  for(const groupBy of ['status','type']){
    const view=projectDirectory(input,{groupBy});
    assert.equal(view.groups[0].key,'unknown');
    assert.match(view.groups[0].label,/[\u3400-\u9fff]/);
    assert.equal(view.groups.flatMap(group=>group.rows).length,3);
  }
  assert.equal(projectDirectory(input,{freshness:'attention'}).rows.includes(removed),false);
  assert.ok(projectDirectory(input,{groupBy:'freshness'}).groups.some(group=>group.label==='已从本地移除'));
  assert.deepEqual(projectDirectory(input,{groupBy:'unrecognized'}).rows,input);
});

const sourced=(value,key='aliases')=>({value,state:'known',source:{kind:'registry',field:'project_definition.'+key,registry_sha256:'d'.repeat(64),sha256:'d'.repeat(64),observed_at:null},reason:'已登记'});
test('search explains sourced identity, aliases, purpose and outputs without guessing mappings',()=>{
  const p={project_id:'actor-four',name:'Actor 4',definition:{fields:{
    official_name:sourced('Actor Four Studio','official_name'),aliases:sourced(['演员工作台']),
    purpose:sourced('组织角色表演镜头','purpose'),outputs:sourced(['分镜视频','角色档案'],'outputs'),
  }}};
  const cases=[['ACTOR-FOUR','project_id','actor-four'],['four studio','official_name','Actor Four Studio'],
    ['演员','aliases','演员工作台'],['表演','purpose','组织角色表演镜头'],['分镜','outputs','分镜视频']];
  for(const [query,field,value] of cases){
    const result=projectSearchMatch(p,query);
    assert.equal(result.matched,true);
    assert.ok(result.reasons.some(reason=>reason.field===field&&reason.value===value&&reason.source));
    assert.ok(result.reasons.every(reason=>reason.label));
    assert.deepEqual(projectDirectory([p],{query}).matches.get(p.project_id),result.reasons);
  }
  assert.deepEqual(projectSearchMatch(p,'三十天'),{matched:false,reasons:[]});
  assert.equal(projectSearchMatch({...p,local_path:'/tmp/三十天'},'三十天').matched,false);
  for(const state of ['empty','unknown','missing','unavailable','not_applicable']){
    const unconfirmed={...p,definition:{fields:{aliases:{...sourced(['三十天']),state}}}};
    assert.equal(projectSearchMatch(unconfirmed,'三十天').matched,false);
  }
  assert.equal(projectSearchMatch({...p,definition:{fields:{aliases:{value:['三十天'],state:'known'}}}},'三十天').matched,false);
  const similar={project_id:'actor-four-copy',name:'Actor 4 working copy',definition:{fields:{aliases:sourced(['演员工作台'])}}};
  const view=projectDirectory([p,similar],{query:'演员'});
  assert.deepEqual(view.rows,[p,similar]);
  assert.equal(view.matches.size,2);
  assert.deepEqual(projectSearchMatch(p,'  '),{matched:true,reasons:[]});
});

test('103 identities remain complete in the shared view projection across searches, filters and groups',()=>{
  const input=projects.slice(0,103).map((p,i)=>({...p,definition:{fields:{
    aliases:sourced([i%2?'常用甲':'常用乙']),purpose:sourced(i%3?'镜头整理':'音频制作','purpose'),
  }}}));
  const before=structuredClone(input);
  for(const [query,accept] of [['',()=>true],['常用甲',i=>i%2===1],['音频',i=>i%3===0],['不存在',()=>false]])
    for(const status of ['',...statuses])for(const source of ['','attention','fresh'])
      for(const groupBy of ['none','status','type','freshness']){
        const view=projectDirectory(input,{query,status,freshness:source,groupBy});
        const expected=input.filter((p,i)=>accept(i)&&(!status||p.business.normalized_status===status)
          &&(!source||(source==='attention'?attention(p):p.freshness.state==='fresh')));
        assert.equal(view.total,103);
        assert.deepEqual(view.rows,expected);
        assert.deepEqual(new Set(view.groups.flatMap(g=>g.rows)),new Set(expected));
        assert.equal(view.groups.reduce((count,g)=>count+g.rows.length,0),expected.length);
        assert.deepEqual([...view.matches.keys()],expected.map(p=>p.project_id));
        const identities=view.rows.map(p=>p.project_id);
        assert.equal(new Set(identities).size,expected.length);
        assert.deepEqual(identities,expected.map(p=>p.project_id));
      }
  assert.deepEqual(input,before);
});

// Same small DOM surface used by test_hub_business_tasks; current source tree
// validation is covered there, while browser acceptance belongs to the UI pass.
class FakeNode{
  constructor(tag,text=null){this.tag=tag;this.text=text;this.children=[];this.attrs={};}
  appendChild(child){this.children.push(child);return child;}
  append(...children){for(const child of children)this.children.push(typeof child==='string'?new FakeNode('#text',child):child);}
  setAttribute(key,value){this.attrs[key]=value;}
  addEventListener(name,handler){(this.events??={})[name]=handler;}
  get textContent(){return this.text??this.children.map(child=>child?.textContent??String(child)).join('');}
}
test('tree preserves each project identity, safe detail route and explicit unknown source plan',()=>{
  const previous=globalThis.document;
  globalThis.document={createElement:tag=>new FakeNode(tag),createTextNode:text=>new FakeNode('#text',text)};
  try{
    const rows=[{project_id:'odd /#?',name:'<script>项目</script>',business:{normalized_status:'active'},declared:{connection_read_allowed:true},freshness:{state:'fresh'}},projects[0]];
    const rendered=renderDirectoryTree(rows);
    assert.deepEqual(rendered.children.map(node=>node.attrs['data-project-id']),rows.map(row=>row.project_id));
    assert.equal(rendered.children[0].children[0].tag,'summary');
    assert.match(rendered.children[0].children[0].textContent,/<script>项目<\/script> · 进行中/);
    assert.equal(rendered.children[0].children[1].attrs.href,'#projects/odd%20%2F%23%3F');
    assert.match(rendered.textContent,/当前业务计划证据不可用/);
    assert.match(rendered.textContent,/按当前读取权限，业务计划不可查看/);
    assert.match(renderDirectoryTree([]).textContent,/没有匹配/);
  }finally{globalThis.document=previous;}
});

test('malformed definition sources cannot create alias or purpose search matches',()=>{
  for(const field of ['aliases','purpose','outputs'])for(const source of [{},'registry:definition',
    {...sourced('x',field).source,field:'project_definition.roles'}]){
    const p={project_id:'a',name:'Alpha',definition:{fields:{[field]:{value:['Unproven needle'],state:'known',source}}}};
    assert.deepEqual(projectSearchMatch(p,'needle'),{matched:false,reasons:[]});
  }
});

function overviewFixture(id='a'){
  const business={current_work:{objective:'当前主线',next_action:'完成下一步',owner_role:'项目作者',status:'active'},
    progress:{completed:4,total:4,counting_basis:'本轮四个局部步骤'},blockers:[],verification:{status:'passed'}};
  const keys=['current_work.objective','current_work.next_action','current_work.owner_role','current_work.status','progress.completed','progress.total','progress.counting_basis','blockers','verification.status'];
  const record={schema_version:'2.0',success:true,business,sources:[{id:'state',sha256:'a'.repeat(64),path:'/data/private/state.json'}],
    field_provenance:Object.fromEntries(keys.map(k=>[k,{source_ref:'state',sha256:'a'.repeat(64),selector:{path:k.split('.')}}]))};
  return {project_id:id,name:'项目 '+id,declared:{connection_read_allowed:true,summary_enabled:true,project_type:'code'},
    definition:{fields:{purpose:sourced('长期目标','purpose'),outputs:sourced(['源码','文档'],'outputs')}},
    freshness:{state:'fresh',authority_drift:false,local_presence:'registered_local',fact_time_state:'known',fact_updated_at:'2026-10-23T00:00:00Z'},
    business:{state:'current',normalized_status:'active',source_record:record,unknown_fields:{}},operational:{latest_attempt:{success:true}},
    semantics:{explanations:{next_action:{unknown_reason:'No declared next action.'}}}};
}
const documentFixture=fn=>{
  const previous=globalThis.document;
  globalThis.document={createElement:tag=>new FakeNode(tag),createTextNode:text=>new FakeNode('#text',text)};
  try{return fn();}finally{globalThis.document=previous;}
};
const nodes=root=>[root,...root.children.flatMap(child=>child instanceof FakeNode?nodes(child):[])];
function graphFixture(projectId='a',version='1'){
  const objects=[['Project',projectId,'v1','fact'],['Artifact','output','v1','candidate'],['Artifact','output','v2','candidate']].map(([type,native_id,revision,identity])=>({
    id:projectId+'/'+type+'/'+native_id,project_id:projectId,type,native_id,revision,identity,label:'同名 <script>数据</script>',
    source:{version:'source-'+revision,applicability:'current'}}));
  const projection_version=version.repeat(64);
  return {schema_version:'1.0',project_id:projectId,read_only:true,source_version:'a'.repeat(64),projection_version,
    compatibility:{major:1,minimum_reader:'1.0'},objects,rows:objects.map(o=>({object_id:o.id,revision:o.revision,type:o.type,label:o.label,identity:o.identity,source_version:o.source.version,projection_version})),unknown_types:{Goal:'No true goal ID',Run:'No true run ID'}};
}

test('overview five sourced explanations keep health, local denominator and business separate',()=>{
  const p=overviewFixture(),before=structuredClone(p),v=overviewProject(p);
  assert.equal(v.fields.length,5);assert.ok(v.fields.every(f=>f.known&&f.source));
  assert.equal(v.fields.find(f=>f.key==='outputs').text,'源码、文档');assert.match(v.progress,/4 \/ 4/);assert.match(v.progress,/局部步骤/);
  assert.equal(v.business,'进行中');assert.equal(v.completionInferred,false);assert.match(v.availability,/未知/);assert.match(v.collection,/已更新/);assert.deepEqual(p,before);
  p.freshness.state='stale';const stale=overviewProject(p);assert.equal(stale.fields.find(f=>f.key==='current_work').known,false);assert.equal(stale.business,'未知');assert.equal(stale.factAt,null);assert.doesNotMatch(stale.collection,/业务失败/);
  p.declared.connection_read_allowed=false;assert.ok(overviewProject(p).fields.every(f=>!f.known));assert.equal(overviewProject(p).readAllowed,false);
});

test('overview unknown proof and private values do not become current facts or recent changes',()=>{
  const p=overviewFixture();p.business.source_record.field_provenance['current_work.objective'].sha256='f'.repeat(64);
  assert.equal(overviewProject(p).fields.find(f=>f.key==='current_work').known,false);
  p.definition.fields.purpose.value='See /data/private/purpose.md';assert.equal(overviewProject(p).fields.find(f=>f.key==='purpose').known,false);
  p.freshness.fact_time_state='unknown';p.freshness.observed_at='2026-10-23T09:00:00Z';assert.equal(overviewActivity([p]).recent.length,0);
  const other=overviewFixture('b');other.freshness.fact_updated_at='2026-10-23T01:00:00Z';const list=[overviewFixture('a'),other];
  assert.deepEqual(overviewActivity(list).recent.map(v=>v.project_id),['b','a']);assert.deepEqual(list.map(p=>p.project_id),['a','b']);
});

test('overview cards and focus use text DOM, keep every identity and include five explanation evidence',()=>documentFixture(()=>{
  const p=overviewFixture('odd /#?');p.name='<img src=x onerror=alert(1)>';let selected=null;
  const card=renderOverviewCard(p,{onSelect:id=>selected=id}),focus=renderOverviewFocus(p);
  assert.equal(card.attrs['data-project-id'],p.project_id);assert.ok(nodes(card).some(n=>n.tag==='a'&&n.attrs.href==='#projects/odd%20%2F%23%3F'));
  assert.ok(card.textContent.includes('<img src=x onerror=alert(1)>'));assert.ok(!nodes(card).some(n=>['script','img'].includes(n.tag)));
  assert.match(focus.textContent,/目标与用途.*长期目标.*当前工作.*当前主线.*下一步.*完成下一步/s);
  for(const label of ['目标与用途','主要产出','当前工作','下一步','行动人'])assert.ok(card.textContent.includes(label));
  assert.doesNotMatch(card.textContent,/\/data\/private/);const select=nodes(card).find(n=>n.tag==='button');select.events.click();assert.equal(selected,p.project_id);
  assert.deepEqual(projectDirectory([p],{query:'absent'}).rows,[]);assert.equal(p.project_id,'odd /#?');
}));

test('graph rows and objects preserve same-name revisions and reject foreign or mixed versions',()=>{
  const graph=graphFixture(),before=structuredClone(graph),v=graphObjects(graph,'a');
  assert.equal(v.entries.length,3);assert.equal(new Set(v.entries.map(v=>v.key)).size,3);
  assert.equal(v.entries[1].object.id,v.entries[2].object.id);assert.notEqual(v.entries[1].row.revision,v.entries[2].row.revision);
  assert.equal(v.entries[0].object,graph.objects[0]);assert.equal(v.entries[0].row,graph.rows[0]);assert.deepEqual(graph,before);
  const changes=[g=>g.project_id='b',g=>g.objects[0].project_id='b',g=>g.rows[0].projection_version='b'.repeat(64),
    g=>g.rows[0].source_version='foreign',g=>g.rows[1].revision='v2',g=>g.read_only=false,g=>g.compatibility.major=2];
  for(const mutate of changes){const g=graphFixture();mutate(g);assert.throws(()=>graphObjects(g,'a'),/GRAPH_SNAPSHOT_INVALID/);}
});

test('graph reader isolates project selection, clears on logout/route and never mixes responses',async()=>{
  const waiting=[],states=[];const reader=createGraphReader((...args)=>new Promise((resolve,reject)=>waiting.push({args,resolve,reject})),s=>states.push(s));
  const a=reader.select('a'),b=reader.select('b');waiting[1].resolve(graphFixture('b'));await b;waiting[0].resolve(graphFixture('a'));await a;
  assert.equal(reader.state.projectId,'b');assert.equal(reader.state.snapshot.projectId,'b');assert.equal(states.at(-1).kind,'ready');
  assert.ok(waiting.every(v=>v.args.length===1));assert.equal(waiting[0].args[0],'/api/projects/a/graph');
  const c=reader.select('c');reader.clear();waiting[2].resolve(graphFixture('c'));await c;assert.equal(reader.state.projectId,null);assert.equal(reader.state.snapshot,null);
});

test('graph unavailable/error/empty/disabled expose honest readonly recovery',async()=>{
  let calls=0,mode='missing';const reader=createGraphReader(async()=>{calls++;if(mode==='missing')throw {status:404};if(mode==='offline')throw {code:'NETWORK_UNAVAILABLE'};return {...graphFixture(),objects:[],rows:[]};});
  await reader.select('a',false);assert.equal(calls,0);assert.equal(reader.state.kind,'disabled');
  await reader.select('a');assert.equal(reader.state.kind,'unavailable');mode='offline';await reader.retry();assert.equal(reader.state.kind,'error');
  mode='empty';await reader.retry();assert.equal(reader.state.kind,'empty');assert.equal(reader.state.snapshot.entries.length,0);
  documentFixture(()=>{
    for(const kind of ['loading','empty','disabled','unavailable','error']){
      let retry=0;const panel=renderGraphObjects({kind},{retry:()=>retry++});
      assert.ok(panel.textContent.length>30);const b=nodes(panel).find(n=>n.tag==='button');
      if(['error','unavailable','empty'].includes(kind)){assert.ok(b);b.events.click();assert.equal(retry,1);}else assert.equal(b,undefined);
    }
    const panel=renderGraphObjects({kind:'ready',snapshot:graphObjects(graphFixture(),'a')});
    assert.match(panel.textContent,/候选，尚非事实/);assert.match(panel.textContent,/不补造目标或运行身份/);assert.equal(nodes(panel).filter(n=>n.attrs['data-object-key']).length,3);
    assert.ok(!nodes(panel).some(n=>n.tag==='script'));
  });
});


test('overview permission gates cached business, progress, engineering and fact dates together',()=>documentFixture(()=>{
  for(const deny of [p=>p.declared.access_profile='no_current_goal_access',p=>p.declared.current_state_status='removed_local',
    p=>p.freshness.local_presence='removed_local',p=>p.declared.connection_read_allowed=false]){
    const p=overviewFixture();deny(p);const before=structuredClone(p),v=overviewProject(p);
    assert.equal(v.readAllowed,false);assert.ok(v.fields.every(f=>!f.known&&f.source===null));
    assert.equal(v.business,'未知');assert.equal(v.engineering,'未知');assert.equal(v.factAt,null);
    assert.doesNotMatch(v.progress,/4|局部步骤/);assert.equal(overviewActivity([p]).recent.length,0);
    const text=renderOverviewCard(p).textContent+renderOverviewFocus(p).textContent;
    assert.doesNotMatch(text,/当前主线|完成下一步|项目作者|进行中|passed|2026-10-23|4 \/ 4/);
    assert.deepEqual(p,before);
  }
}));

test('rendered evidence retains verified logical source and field selector without physical paths',()=>documentFixture(()=>{
  const p=overviewFixture(),before=structuredClone(p),focus=renderOverviewFocus(p);
  const proofs=nodes(focus).filter(n=>n.tag==='pre').map(n=>JSON.parse(n.textContent));
  const current=proofs.find(v=>v.selector?.path?.join('.')==='current_work.objective');
  assert.ok(current);assert.equal(current.source_ref,'state');assert.equal(current.sha256,'a'.repeat(64));
  assert.deepEqual(current.selector.path,['current_work','objective']);assert.equal(Object.hasOwn(current,'path'),false);
  assert.doesNotMatch(focus.textContent,/\/data\/private|state.json/);assert.deepEqual(p,before);
  p.business.source_record.field_provenance['current_work.objective'].source_ref='unverified';
  const invalid=overviewProject(p).fields.find(f=>f.key==='current_work');assert.equal(invalid.known,false);assert.equal(invalid.source,null);
  const card=renderOverviewCard(overviewFixture(),{onSelect:()=>{}});
  assert.ok(nodes(card).some(n=>n.tag==='summary'&&n.textContent==='说明与依据'));
  assert.ok(nodes(card).some(n=>n.tag==='button'&&n.textContent==='聚焦'&&n.attrs['aria-label']==='聚焦项目 a'));
}));
