import assert from 'node:assert/strict';
import test from 'node:test';
import {projectDirectory,projectSearchMatch,renderDirectoryTree} from '../src/hub/web/directory.mjs';
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

const sourced=value=>({value,state:'known',source:'registry:definition',reason:'已登记'});
test('search explains sourced identity, aliases, purpose and outputs without guessing mappings',()=>{
  const p={project_id:'actor-four',name:'Actor 4',definition:{fields:{
    official_name:sourced('Actor Four Studio'),aliases:sourced(['演员工作台']),
    purpose:sourced('组织角色表演镜头'),outputs:sourced(['分镜视频','角色档案']),
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
    aliases:sourced([i%2?'常用甲':'常用乙']),purpose:sourced(i%3?'镜头整理':'音频制作'),
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
