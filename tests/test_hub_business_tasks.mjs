import assert from 'node:assert/strict';
import test from 'node:test';
import {businessPlan,renderBusinessPlan} from '../src/hub/web/business_tasks.mjs';

const sha='a'.repeat(64),time='2026-10-14T12:00:00+00:00';
function node(id,parent,kind,status='complete'){
  return {id,parent,kind,status,dependencies:[],condition:'明确的条件 '+id,evidence:['evidence/'+id]};
}
function fixture(){
  const nodes=[node('a',null,'project'),node('s','a','stage'),node('r','s','round'),node('one','r','leaf')];
  const plan={schema_version:'1.0',project_id:'a',plan_version:'v2',unit:'验收项',counting_basis:'只计当前版本业务叶子项',
    catalog_sha256:sha,input_version:sha,nodes,labels:Object.fromEntries(nodes.map(n=>[n.id,'业务 '+n.id])),
    acceptance:[{task_id:'one',plan_version:'v2',condition_version:sha,implemented:true,verified:true,human:false,
      evidence_version:'e2',evidence_refs:['evidence/pass'],unknown_reason:null}],
    counts:{total:1,implemented:1,verified:1,human:0,not_started:0,active:0,blocked:0,cancelled:0},
    baseline_denominator:1,history:{baseline_plan_version:'v1',baseline_leaf_ids:['one'],changes:[{
      from_plan_version:'v1',to_plan_version:'v2',kind:'revise',reason:'条件已经更新',before_leaf_ids:['one'],after_leaf_ids:['one'],
      mapping:[{old_ids:['one'],new_ids:['one']}],evidence_refs:['history/changed']}]},history_unknown_reason:null};
  const record={schema_version:'2.0',project_id:'a',success:true,observed_at:time,sources:[{id:'state',sha256:sha}],
    extensions:{schema_version:'1.1',fact:{task_plan:{value:plan,proof:{source_ref:'state',sha256:sha,observed_at:time,selector:{path:['task_plan']}},unknown_reason:null}},managed:{},derived:{},proposed:{}}};
  return {project_id:'a',declared:{connection_read_allowed:true},freshness:{state:'fresh',local_presence:'present'},
    operational:{latest_attempt:record,last_success:structuredClone(record)},business:{progress:{completed:999,total:999}}};
}

test('business tree has exact current source proof and leaf-only ratio without provider inference',()=>{
  const p=fixture();p.provider_tasks=[{status:'complete'}];
  const before=structuredClone(p),view=businessPlan(p);
  assert.equal(view.state,'current');assert.equal(view.history,false);assert.equal(view.progress.numerator,1);
  assert.equal(view.progress.denominator,1);assert.equal(view.progress.ratio,1);assert.equal(view.plan.nodes.length,4);
  assert.deepEqual(view.source,{source_ref:'state',sha256:sha,selector:{path:['task_plan']}});
  assert.deepEqual(p,before);
  view.plan.nodes[0].id='detached';assert.equal(p.operational.latest_attempt.extensions.fact.task_plan.value.nodes[0].id,'a');
});

test('permission and removed guards cover current and historical modes',()=>{
  for(const change of [p=>p.declared.connection_read_allowed=false,p=>p.freshness.local_presence='removed_local',p=>p.freshness.state='removed_local']){
    const p=fixture();change(p);
    for(const history of [false,true]){const view=businessPlan(p,{history});assert.equal(view.state,'unavailable');assert.equal(view.plan,null);}
  }
});

test('failed and stale reads never promote historical plan to current',()=>{
  const p=fixture();p.operational.latest_attempt.success=false;
  assert.equal(businessPlan(p).state,'failed');assert.equal(businessPlan(p).plan,null);
  const old=businessPlan(p,{history:true});assert.equal(old.state,'history');assert.equal(old.history,true);
  p.operational.latest_attempt.success=true;p.freshness.state='stale';
  assert.equal(businessPlan(p).state,'stale');assert.equal(businessPlan(p,{history:true}).state,'history');
  p.freshness.state='fresh';p.freshness.authority_drift=true;assert.equal(businessPlan(p).state,'stale');
});

test('undeclared and null unknown plan are distinct from a true empty source tree',()=>{
  const p=fixture();delete p.operational.latest_attempt.extensions;
  assert.equal(businessPlan(p).state,'not_declared');assert.equal(businessPlan(p).plan,null);
  p.operational.latest_attempt.extensions={schema_version:'1.0',fact:{task_nodes:{value:[]}},managed:{},derived:{},proposed:{}};
  assert.equal(businessPlan(p).state,'not_declared');
  const q=fixture(),field=q.operational.latest_attempt.extensions.fact.task_plan;
  field.value=null;field.proof=null;field.unknown_reason='当前来源尚未登记';
  assert.equal(businessPlan(q).state,'unavailable');assert.equal(businessPlan(q).reason,'当前来源尚未登记');
  const r=fixture(),plan=r.operational.latest_attempt.extensions.fact.task_plan.value;
  plan.nodes=[];plan.labels={};plan.acceptance=[];for(const key of Object.keys(plan.counts))plan.counts[key]=0;
  plan.history=null;plan.history_unknown_reason='来源没有变更历史';plan.baseline_denominator=null;
  const view=businessPlan(r);assert.equal(view.state,'empty');assert.equal(view.progress.denominator,0);
  assert.equal(view.progress.ratio,null);assert.equal(view.progress.reason,'not_applicable');
});

test('unknown total or verified count stays unknown despite known source leaves and legacy V/N',()=>{
  for(const key of ['total','verified']){
    const p=fixture(),plan=p.operational.latest_attempt.extensions.fact.task_plan.value;
    plan.counts[key]=null;
    const view=businessPlan(p);assert.equal(view.progress.ratio,null);assert.equal(view.progress.reason,'unknown_count');
    assert.equal(view.progress[key==='total'?'denominator':'numerator'],null);
  }
});

test('missing mismatched or malformed source proof refuses the whole business plan',()=>{
  const changes=[
    p=>p.operational.latest_attempt.project_id='other',p=>p.operational.latest_attempt.schema_version='unsupported',
    p=>p.operational.latest_attempt.extensions.schema_version='1.0',
    p=>p.operational.latest_attempt.extensions.fact.task_plan.proof.sha256='b'.repeat(64),
    p=>p.operational.latest_attempt.extensions.fact.task_plan.proof.source_ref='missing',
    p=>p.operational.latest_attempt.extensions.fact.task_plan.proof.observed_at='2020-01-01',
    p=>p.operational.latest_attempt.extensions.fact.task_plan.proof.selector={},
    p=>p.operational.latest_attempt.extensions.fact.task_plan.proof.selector={path:[true]},
    p=>p.operational.latest_attempt.sources.push({...p.operational.latest_attempt.sources[0]}),
    p=>p.operational.latest_attempt.extensions.fact.task_plan.value.project_id='other',
    p=>p.operational.latest_attempt.extensions.fact.task_plan.value.counts.verified=2,
    p=>p.operational.latest_attempt.extensions.fact.task_plan.value.labels={},
    p=>p.operational.latest_attempt.extensions.fact.task_plan.value.acceptance[0].plan_version='v1',
    p=>p.operational.latest_attempt.extensions.fact.task_plan.value.acceptance[0].evidence_refs=[],
    p=>p.operational.latest_attempt.extensions.fact.task_plan.value.acceptance[0].implemented=false,
    p=>p.operational.latest_attempt.extensions.fact.task_plan.value.nodes[3].status='cancelled',
  ];
  for(const change of changes){const p=fixture();change(p);assert.equal(businessPlan(p).state,'unavailable');assert.equal(businessPlan(p).plan,null);}
});

class FakeNode{
  constructor(tag,text=null){this.tag=tag;this.text=text;this.children=[];this.attrs={};}
  appendChild(child){this.children.push(child);return child;}
  append(...children){for(const child of children)this.children.push(typeof child==='string'?new FakeNode('#text',child):child);}
  setAttribute(key,value){this.attrs[key]=value;}
  get textContent(){return this.text??this.children.map(child=>child?.textContent??String(child)).join('');}
}
function render(project,options){
  const old=globalThis.document;
  globalThis.document={createElement:tag=>new FakeNode(tag),createTextNode:text=>new FakeNode('#text',text)};
  try{return renderBusinessPlan(project,options);}finally{globalThis.document=old;}
}
function descendants(node){return [node,...node.children.flatMap(child=>child instanceof FakeNode?descendants(child):[])];}

test('renderer preserves I/V/H, applicable versions, conditions and explicit historical changes as text',()=>{
  const p=fixture();p.operational.latest_attempt.extensions.fact.task_plan.value.labels.one='<script>raw label</script>';
  const rendered=render(p),text=rendered.textContent;
  for(const required of ['业务计划任务','业务验收 V/N：1 / 1','已实现 I：1','已验证 V：1','用户验收 H：0','明确的条件 one','证据版本：e2','evidence/pass','基线 v1：1','当前 v2：1','条件已经更新','<script>raw label</script>'])assert.ok(text.includes(required),required);
  assert.equal(descendants(rendered).filter(node=>node.tag==='script').length,0);
  assert.ok(descendants(rendered).filter(node=>node.tag==='details').length>=5);
  assert.ok(!text.includes('null'));assert.ok(!text.includes('999'));
  const historical=render(p,{history:true}).textContent;assert.ok(historical.includes('历史版本与证据，仅供回看，不代表当前验收。'));
});

test('renderer distinguishes unknown acceptance and absent declaration without generating controls',()=>{
  const p=fixture(),plan=p.operational.latest_attempt.extensions.fact.task_plan.value;
  plan.acceptance[0].verified=null;plan.acceptance[0].human=null;plan.acceptance[0].unknown_reason='尚未核实证据';plan.counts.verified=null;plan.counts.human=null;
  const rendered=render(p),text=rendered.textContent;
  assert.ok(text.includes('V 已验证：未知'));assert.ok(text.includes('H 用户验收：未知'));assert.ok(text.includes('比例未知'));assert.ok(text.includes('尚未核实证据'));
  assert.equal(descendants(rendered).filter(node=>['button','input','form'].includes(node.tag)).length,0);
  delete p.operational.latest_attempt.extensions;assert.ok(render(p).textContent.includes('尚未声明业务任务计划'));
});


test('old unaccepted evidence may remain visible but old true flags never count as current acceptance',()=>{
  for(const nullable of [false,true]){
    const p=fixture(),plan=p.operational.latest_attempt.extensions.fact.task_plan.value,row=plan.acceptance[0];
    row.plan_version='old-version';row.implemented=nullable?null:false;row.verified=false;row.human=false;
    row.unknown_reason=nullable?'旧版实现尚未核实':'旧版条件已失效';
    plan.counts.implemented=nullable?null:0;plan.counts.verified=0;plan.counts.human=0;
    const view=businessPlan(p);assert.equal(view.state,'current');assert.equal(view.progress.numerator,0);
    assert.ok(render(p).textContent.includes('旧版验收不适用于当前计划；不计为当前已验证。'));
    row.implemented=true;plan.counts.implemented=1;
    assert.equal(businessPlan(p).state,'unavailable');
  }
});

test('known flags may retain a source reason but null flags require a nonempty reason',()=>{
  const p=fixture(),row=p.operational.latest_attempt.extensions.fact.task_plan.value.acceptance[0];
  row.unknown_reason='来源额外说明';assert.equal(businessPlan(p).state,'current');
  row.human=null;row.unknown_reason=null;assert.equal(businessPlan(p).state,'unavailable');
  row.unknown_reason='用户决定尚未提供';assert.equal(businessPlan(p).state,'current');
  row.unknown_reason='';assert.equal(businessPlan(p).state,'unavailable');
});

test('renderer uses a scoped summary class and no inline styles under the existing CSP',()=>{
  const rendered=render(fixture()),nodes=descendants(rendered);
  assert.ok(nodes.some(node=>node.tag==='summary'&&node.attrs.className==='business-task-summary'));
  assert.ok(nodes.every(node=>!Object.hasOwn(node.attrs,'style')));
});


test('renderer drills into bounded project stage round leaf details with independent native folds',()=>{
  const p=fixture(),plan=p.operational.latest_attempt.extensions.fact.task_plan.value;
  const initial=descendants(render(p)).filter(node=>node.tag==='details'&&node.attrs.className==='stack business-task-node');
  assert.equal(initial[0].attrs.open,'true');
  assert.ok(initial.slice(1).every(node=>!Object.hasOwn(node.attrs,'open')));
  plan.nodes[3].status='active';
  const rendered=render(p);
  const taskNodes=descendants(rendered).filter(node=>node.tag==='details'&&node.attrs.className==='stack business-task-node');
  assert.equal(taskNodes.length,4);
  assert.equal(taskNodes[0].attrs.open,'true');assert.equal(taskNodes[1].attrs.open,'true');assert.equal(taskNodes[2].attrs.open,'true');
  assert.ok(!Object.hasOwn(taskNodes[3].attrs,'open'));
  for(let index=0;index<3;index++){
    const childContainer=taskNodes[index].children.find(node=>node.attrs?.className==='stack business-task-children');
    assert.ok(childContainer);assert.equal(childContainer.children[0],taskNodes[index+1]);
    const ownContent=taskNodes[index].children.filter(node=>node!==childContainer);
    assert.ok(ownContent.every(node=>!node.textContent.includes('I 已实现：')));
  }
  const summaries=taskNodes.map(node=>node.children[0]);assert.ok(summaries.every(node=>node.tag==='summary'));
});

test('orphan roots duplicated roots mismatched project and skipped hierarchy kinds are refused',()=>{
  const changes=[
    plan=>plan.nodes[0].id='foreign-project',
    plan=>plan.nodes[1].parent=null,
    plan=>plan.nodes[2].parent='a',
    plan=>plan.nodes[3].parent='s',
    plan=>plan.nodes[1].parent='one',
    plan=>plan.nodes[2].parent='missing',
    plan=>plan.nodes[0].kind='round',
  ];
  for(const change of changes){const p=fixture();change(p.operational.latest_attempt.extensions.fact.task_plan.value);assert.equal(businessPlan(p).state,'unavailable');}
});


test('each node exposes stable identity and leaves show explicit source dependencies or none',()=>{
  const p=fixture(),plan=p.operational.latest_attempt.extensions.fact.task_plan.value;
  let rendered=render(p);
  for(const id of ['a','s','r','one'])assert.ok(rendered.textContent.includes('任务 ID：'+id));
  assert.ok(rendered.textContent.includes('依赖任务：无依赖'));
  plan.nodes[3].dependencies=['r'];rendered=render(p);
  assert.ok(rendered.textContent.includes('依赖任务：r（业务 r）'));
  assert.equal(businessPlan(p).progress.denominator,1);
});

test('structural project stage round with no leaves is empty business work without invented denominator',()=>{
  for(const total of [0,null]){
    const p=fixture(),plan=p.operational.latest_attempt.extensions.fact.task_plan.value;
    plan.nodes.pop();delete plan.labels.one;plan.acceptance=[];
    for(const key of Object.keys(plan.counts))plan.counts[key]=0;
    plan.counts.total=total;plan.history=null;plan.history_unknown_reason='来源未提供版本变更';plan.baseline_denominator=null;
    const view=businessPlan(p);
    assert.equal(view.state,'empty');assert.equal(view.plan.nodes.length,3);
    assert.equal(view.progress.denominator,total);assert.equal(view.progress.ratio,null);
    assert.equal(view.progress.reason,total===0?'not_applicable':'unknown_count');
    const rendered=render(p),text=rendered.textContent;
    assert.ok(text.includes('没有业务叶子任务'));
    assert.ok(text.includes(total===0?'业务验收 V/N：0 / 0':'业务验收 V/N：0 / 未知'));
    assert.ok(!text.includes('100%'));
    assert.equal(descendants(rendered).filter(node=>node.attrs.className==='stack business-task-node').length,3);
  }
});

test('renderer exposes source-declared leaf status counts and explicit gaps without deriving parent counts',()=>{
  const p=fixture(),plan=p.operational.latest_attempt.extensions.fact.task_plan.value;
  plan.counts={total:1,implemented:0,verified:0,human:0,not_started:0,active:0,blocked:1,cancelled:0};
  plan.nodes[3].status='blocked';plan.acceptance[0].implemented=false;plan.acceptance[0].verified=false;
  plan.acceptance[0].unknown_reason='来源尚缺真实运行验收';
  const text=render(p).textContent;
  assert.ok(text.includes('受阻 1'));assert.ok(text.includes('进行中 0'));assert.ok(text.includes('缺口与未知原因：来源尚缺真实运行验收'));
  assert.ok(text.includes('业务验收 V/N：0 / 1'));assert.ok(!text.includes('受阻 4'));
});
