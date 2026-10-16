import test from 'node:test';
import assert from 'node:assert/strict';
import {renderRelations} from '../src/hub/web/relations.mjs';
class N{
  constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this._text='';}
  appendChild(n){this.children.push(n);return n;}append(...ns){this.children.push(...ns);}
  setAttribute(k,v){this.attributes[k]=String(v);}
  get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
  set textContent(v){this._text=String(v);this.children=[];}
}
const nodes=n=>[n,...n.children.flatMap(nodes)];
function mount(p){globalThis.document={createElement:t=>new N(t),createTextNode:t=>{const n=new N('#text');n.textContent=t;return n;}};return renderRelations(p,{names:new Map([['a','Alpha'],['b','Beta'],['c','Copy']])});}
function project(state='owner_confirmed'){
  return {project_id:'a',definition:{fields:{}},management:{relation_view:{available:true,state,revision:9,config_hash:'a'.repeat(64),independent:null,outgoing:[],incoming:[],warnings:[],dependency_impact:{status:'clear',paths:[],truncated:false}}}};
}
test('unknown, explicit empty, false independence and incoming confirmed edges remain distinct',()=>{
  const p=project('unconfigured');p.management.relation_view.incoming=[{relation:'depends_on',direction:'incoming',project_id:'b',name:'Beta',eligible:true}];
  let n=mount(p);assert.match(n.textContent,/尚未配置.*不能据此推断项目独立/);assert.match(n.textContent,/被以下项目依赖Beta/);
  p.management.relation_view.state='explicit_empty';assert.match(mount(p).textContent,/所有者明确登记本项目暂无管理关系/);
  p.management.relation_view.state='owner_confirmed';p.management.relation_view.independent=false;assert.match(mount(p).textContent,/明确登记为非独立/);assert.doesNotMatch(mount(p).textContent,/暂无管理关系/);
});
test('same-version outgoing and incoming types jump by stable IDs; orphan target has no link',()=>{
  const p=project();p.management.relation_view.outgoing=['belongs_to','depends_on','related_to'].map(relation=>({relation,project_id:'b',name:'Beta',eligible:true}));
  p.management.relation_view.incoming=[{relation:'belongs_to',project_id:'secret',name:'private path',eligible:false}];
  const before=structuredClone(p),n=mount(p);assert.match(n.textContent,/管理版本 9.*配置 aaaaaaaaaaaa/);
  for(const title of ['归属项目','依赖项目','相关项目','包含项目'])assert.ok(n.textContent.includes(title));
  assert.equal(nodes(n).filter(x=>x.tagName==='a'&&x.attributes.href==='#projects/b').length,3);
  assert.doesNotMatch(n.textContent,/private path/);assert.ok(nodes(n).every(x=>x.attributes.href!=='#projects/secret'));assert.deepEqual(p,before);
});
test('source roles and main/copy facts retain evidence and differing owner parent',()=>{
  const p=project(),source={sha256:'f'.repeat(64),observed_at:'2026-10-16T00:00:00Z'};
  p.definition.fields={roles:{state:'known',value:['supplier','consumer'],source},primary_project_id:{state:'known',value:'c',source},working_copies:{state:'empty',value:[],source}};
  p.management.relation_view.outgoing=[{relation:'belongs_to',project_id:'b',name:'Beta',eligible:true}];
  const n=mount(p);assert.match(n.textContent,/supplierconsumer/);assert.match(n.textContent,/来源明确未列出/);assert.match(n.textContent,/来源主项目与管理归属不同/);assert.match(n.textContent,/ffffffffffff/);
  assert.ok(nodes(n).some(x=>x.attributes.href==='#projects/c'));
});
test('derived dependency path exposes exact source basis and review warning without source mutation',()=>{
  const p=project();p.business={normalized_status:'active'};p.management.relation_view.dependency_impact={status:'affected',truncated:true,paths:[{path:['a','b'],source_status:'blocked',source_freshness:'fresh',reason:'dependency_blocked',source_version:{ledger_head:{sequence:77},latest_result_hash:'b'.repeat(64),registry_sha256:'c'.repeat(64)}}]};
  const before=structuredClone(p),n=mount(p);assert.match(n.textContent,/依赖影响（派生）.*需要复核排期/);assert.match(n.textContent,/Alpha → Beta/);assert.match(n.textContent,/来源账本 77.*bbbbbbbbbbbb.*cccccccccccc/);assert.match(n.textContent,/结论不完整/);assert.match(n.textContent,/不改变项目来源状态或日期/);assert.deepEqual(p,before);
});
