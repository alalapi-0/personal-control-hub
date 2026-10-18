import test from 'node:test';
import assert from 'node:assert/strict';
import {buildManagementCommand,managementFieldErrors,renderManagement} from '../src/hub/web/management.mjs';
const hash='a'.repeat(64);
const overlay=()=>({priority:null,start_date:null,end_date:null,watch:null,order:null,layout:null,relations:null});
const view=()=>({available:true,editable:true,revision:2,config_hash:hash,overlay:overlay(),relation_state:'unconfigured',warnings:[],project_options:[{id:'a',name:'A'},{id:'b',name:'B'},{id:'c',name:'C'}]});
class N{
 constructor(tag){this.tagName=tag;this.children=[];this.events={};this.attributes={};this._value='';this.disabled=false;this.selected=false;this._text='';}
 appendChild(n){this.children.push(n);return n;}
 replaceChildren(...ns){this.children=ns;this._text='';}
 addEventListener(k,f){(this.events[k]??=[]).push(f);}
 setAttribute(k,v){this.attributes[k]=v;}
 get value(){return this._value;}set value(v){this._value=String(v);if(this.tagName==='select'&&!this.multiple)for(const c of this.children)c.selected=c.value===this._value;}
 get selectedOptions(){return this.children.filter(n=>n.selected);}
 get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}set textContent(v){this._text=String(v);this.children=[];}
 getBoundingClientRect(){return {left:0,top:0,width:320,height:160};}
 async fire(type,data={}){for(const f of this.events[type]??[])await f(data);}
}
function mount(v,opts){globalThis.document={createElement:t=>new N(t),createElementNS:(_,t)=>new N(t),createTextNode:t=>{const n=new N('#text');n.textContent=t;return n;}};return renderManagement(v,opts);}
function all(n){return [n,...n.children.flatMap(all)];}
function button(n,label){return all(n).find(x=>x.tagName==='button'&&x.textContent===label);}
function input(n,label){const l=all(n).find(x=>x.tagName==='label'&&x.children[0]?.textContent===label);return l.children[1];}
function status(n){return all(n).find(x=>x.attributes['aria-label']==='管理配置保存状态').textContent;}
async function change(n,v,type='input'){n.value=v;await n.fire(type);}
function receipt(command,v=view()){return {receipt:{request_id:command.request_id,revision:v.revision,committed_revision:Math.min(3,v.revision),replayed:false},view:v};}
const proposalRef=()=>({source_sha256:'d'.repeat(64),proposal_id:'synthetic-related',proposal_hash:'e'.repeat(64),registry_sha256:'f'.repeat(64)});
const proposedView=()=>({...view(),proposals:[{proposal_ref:proposalRef(),project_ids:['a','b'],mapping:'related_to',evidence_scope:'configured_projection_only'}]});
function feedback(n,control){const id=control.attributes['aria-describedby'];assert.ok(id,'field names its error description');const message=all(n).find(node=>node.attributes.id===id);assert.ok(message,'description resolves to an actual node');return message;}

test('command uses exact overlay and semantic CAS; detached from source input',()=>{const v=view(),o=overlay();o.relations={belongs_to:'b',depends_on:['b'],related_to:['c'],independent:true};const c=buildManagementCommand(v,'a','set',o,'r1');assert.deepEqual(Object.keys(c),['request_id','expected_revision','expected_hash','project_id','operation','value']);assert.equal(c.expected_hash,hash);assert.equal(c.expected_revision,2);c.value.relations.depends_on.push('c');assert.deepEqual(o.relations.depends_on,['b']);assert.equal(o.relations.independent,true);assert.deepEqual(buildManagementCommand(v,'a','retract',null,'r2').value,null);});
test('invalid dates, counters, layout, references and unknown keys refuse command',()=>{const mutations=[o=>o.start_date='2026-02-30',o=>o.start_date='0000-01-01',o=>{o.start_date='2026-10-15';o.end_date='2026-10-14';},o=>o.priority=-1,o=>o.order=0.5,o=>o.watch='false',o=>o.layout={x:321,y:0},o=>o.layout={x:NaN,y:0},o=>o.source_progress=100,o=>o.relations={belongs_to:'a',depends_on:[],related_to:[],independent:null},o=>o.relations={belongs_to:null,depends_on:['missing'],related_to:[],independent:null},o=>o.relations={belongs_to:null,depends_on:['b','b'],related_to:[],independent:null},o=>o.relations={belongs_to:null,depends_on:[],related_to:[],independent:'yes'}];for(const mutate of mutations){const o=overlay();mutate(o);assert.throws(()=>buildManagementCommand(view(),'a','set',o,'r'));}});
test('missing unavailable malformed version and target prevent effects',()=>{for(const mutate of [v=>v.available=false,v=>v.editable=false,v=>v.revision=null,v=>v.config_hash='z'.repeat(64),v=>v.project_options=[]]){const v=view();mutate(v);assert.throws(()=>buildManagementCommand(v,'a','set',overlay(),'r'));}assert.throws(()=>buildManagementCommand(view(),'a','retract',overlay(),'r'));assert.throws(()=>buildManagementCommand(view(),'a','set',overlay(),'../r'));});
test('guest and missing config disable mutations, programmatic submit cannot call save',async()=>{for(const [v,owner]of [[view(),false],[{...view(),available:false},true]]){let calls=0;const n=mount(v,{projectId:'a',owner,save:async()=>calls++});assert.equal(button(n,'保存配置').disabled,true);assert.equal(button(n,'撤回全部管理配置').disabled,true);await button(n,'保存配置').fire('click');assert.equal(calls,0);assert.match(status(n),/只读|不可编辑/);}});
test('native draft controls, explicit independent with dependency and save are isolated',async()=>{const v=view(),before=structuredClone(v),sent=[];let refreshed=0;const n=mount(v,{projectId:'a',owner:true,save:async c=>{sent.push(c);return receipt(c,{...view(),revision:3,overlay:c.value});},onSaved:()=>refreshed++});assert.equal(input(n,'独立项目').disabled,true);await change(input(n,'关系配置'),'confirmed','change');await change(input(n,'独立项目'),'true','change');const dep=input(n,'依赖项目（可多选）');dep.children.find(o=>o.value==='b').selected=true;await dep.fire('change');await change(input(n,'优先级（数值）'),'4');assert.equal(sent.length,0);await button(n,'保存配置').fire('click');assert.equal(sent.length,1);assert.deepEqual(sent[0].value.relations,{belongs_to:null,depends_on:['b'],related_to:[],independent:true});assert.equal(sent[0].value.priority,4);assert.equal(refreshed,1);assert.deepEqual(v,before);assert.match(status(n),/当前版本 3，本次提交版本 3/);});
test('invalid draft disables save and reset restores loaded overlay',async()=>{let calls=0;const n=mount(view(),{projectId:'a',owner:true,save:async()=>calls++});await change(input(n,'开始日期'),'2026-10-15');await change(input(n,'结束日期'),'2026-10-14');assert.equal(button(n,'保存配置').disabled,true);await button(n,'保存配置').fire('click');assert.equal(calls,0);await button(n,'还原草稿').fire('click');assert.equal(input(n,'开始日期').value,'');assert.equal(button(n,'保存配置').disabled,false);});

test('reversed dates explain the end field, retain input and clear after correction before save',async()=>{
  const sent=[],n=mount(view(),{projectId:'a',owner:true,save:async command=>{sent.push(command);return receipt(command,{...view(),revision:3,overlay:command.value});}});
  const start=input(n,'开始日期'),end=input(n,'结束日期');
  await change(start,'2026-12-31');await change(end,'2026-01-01');
  assert.equal(end.attributes['aria-invalid'],'true');assert.match(feedback(n,end).textContent,/结束日期不能早于开始日期/);
  assert.equal(feedback(n,end).hidden,false);assert.equal(start.value,'2026-12-31');assert.equal(end.value,'2026-01-01');
  assert.match(n.textContent,/请修改配置：.*结束日期不能早于开始日期/);
  await button(n,'保存配置').fire('click');assert.equal(sent.length,0);assert.match(status(n),/配置未保存：.*结束日期不能早于开始日期/);
  await change(end,'2027-01-01');assert.equal(end.attributes['aria-invalid'],'false');assert.equal(feedback(n,end).hidden,true);
  assert.equal(button(n,'保存配置').disabled,false);await button(n,'保存配置').fire('click');
  assert.equal(sent.length,1);assert.equal(sent[0].value.end_date,'2027-01-01');assert.match(status(n),/已保存/);
});

test('each invalid date and native partial date input has a linked message and recoverable state',async()=>{
  const n=mount(view(),{projectId:'a',owner:true,save:async()=>assert.fail('invalid date submitted')});
  for(const [label,value] of [['开始日期','2026-02-30'],['结束日期','0000-01-01']]){
    const control=input(n,label);await change(control,value);
    assert.equal(control.attributes['aria-invalid'],'true');assert.match(feedback(n,control).textContent,/有效的日历日期/);
    await change(control,'');assert.equal(control.attributes['aria-invalid'],'false');assert.equal(feedback(n,control).hidden,true);
  }
  // Browsers can keep value empty while a partially typed native date is bad.
  const start=input(n,'开始日期');start.validity={badInput:true};await change(start,'');
  assert.equal(button(n,'保存配置').disabled,true);assert.equal(start.attributes['aria-invalid'],'true');
  assert.match(feedback(n,start).textContent,/完整有效/);await button(n,'保存配置').fire('click');
  start.validity={badInput:false};await change(start,'2026-02-28');
  assert.equal(start.attributes['aria-invalid'],'false');assert.equal(button(n,'保存配置').disabled,false);
});

test('timezone, numeric and layout fields explain their own limits without overwriting save status',async()=>{
  const n=mount(view(),{projectId:'a',owner:true,save:async()=>{throw {status:409,outcome:'NOT_COMMITTED'};}});
  for(const [label,bad,good,message] of [['计划时区（IANA，可留空）','Not/AZone','Asia/Tokyo',/IANA/],
    ['优先级（数值）','-1','0',/非负整数/],['显示顺序','0.5','1',/非负整数/],['布局 X','321','320',/0 至 320/],['布局 Y','161','160',/0 至 160/]]){
    const control=input(n,label);await change(control,bad);assert.equal(control.attributes['aria-invalid'],'true');
    assert.match(feedback(n,control).textContent,message);assert.equal(button(n,'保存配置').disabled,true);
    await change(control,good);assert.equal(control.attributes['aria-invalid'],'false');assert.equal(feedback(n,control).hidden,true);
  }
  await button(n,'保存配置').fire('click');assert.match(status(n),/配置版本已变化.*草稿保留/);
  await change(input(n,'计划时区（IANA，可留空）'),'UTC');assert.match(status(n),/配置版本已变化.*草稿保留/);
  assert.equal(button(n,'保存配置').disabled,true,'field correction cannot clear a CAS conflict');
});

test('dynamic milestone name and date feedback follow the current row after removal',async()=>{
  const n=mount(view(),{projectId:'a',owner:true,save:async()=>assert.fail('draft auto-saved')});
  await change(input(n,'计划里程碑配置'),'configured','change');await button(n,'添加计划里程碑').fire('click');await button(n,'添加计划里程碑').fire('click');
  let labels=all(n).filter(node=>node.attributes['aria-label']?.startsWith('计划里程碑名称（'));
  const first=labels[0],second=labels[1];
  assert.notEqual(first.attributes['aria-describedby'],second.attributes['aria-describedby']);
  await change(second,'   ');assert.equal(second.attributes['aria-invalid'],'true');assert.match(feedback(n,second).textContent,/名称不能为空/);
  const remove=all(n).find(node=>node.tagName==='button'&&node.attributes['aria-label']?.startsWith('删除计划里程碑（'));
  await remove.fire('click');labels=all(n).filter(node=>node.attributes['aria-label']?.startsWith('计划里程碑名称（'));
  assert.equal(labels.length,1);assert.equal(labels[0].value,'   ');assert.equal(labels[0].attributes['aria-invalid'],'true');
  await change(labels[0],'发布包');const dateControl=all(n).find(node=>node.attributes['aria-label']?.startsWith('计划里程碑日期（'));
  await change(dateControl,'2026-02-30');assert.match(feedback(n,dateControl).textContent,/里程碑日期必须是有效/);
  await change(dateControl,'');assert.equal(dateControl.attributes['aria-invalid'],'false');assert.equal(button(n,'保存配置').disabled,false);
});

test('relation self references, duplicate and unavailable targets have linked actionable errors',async()=>{
  const o=overlay();o.relations={belongs_to:'a',depends_on:['b','b'],related_to:['unavailable'],independent:null};
  const n=mount({...view(),overlay:o},{projectId:'a',owner:true,save:async()=>assert.fail('invalid relation submitted')});
  for(const [label,message] of [['归属项目',/不能指向项目自身/],['依赖项目（可多选）',/重复项目/],['关联项目（可多选）',/不可用或未获允许/]]){
    const control=input(n,label);assert.equal(control.attributes['aria-invalid'],'true');assert.match(feedback(n,control).textContent,message);
  }
  await change(input(n,'归属项目'),'b','change');
  for(const label of ['依赖项目（可多选）','关联项目（可多选）']){
    const control=input(n,label);for(const option of control.children)option.selected=option.value==='b';await control.fire('change');
    assert.equal(control.attributes['aria-invalid'],'false');assert.equal(feedback(n,control).hidden,true);
  }
  assert.equal(button(n,'保存配置').disabled,false);assert.equal(input(n,'归属项目').attributes['aria-invalid'],'false');
});

test('field diagnostics preserve accepted empty/partial dates and do not reject independent dependencies',()=>{
  for(const o of [overlay(),{...overlay(),start_date:'2026-12-31'},{...overlay(),end_date:'2027-01-01'},
    {...overlay(),relations:{belongs_to:null,depends_on:['b'],related_to:['b'],independent:true}}]){
    const before=structuredClone(o);assert.deepEqual(managementFieldErrors(o,'a',new Set(['a','b'])),[]);assert.deepEqual(o,before);
    assert.doesNotThrow(()=>buildManagementCommand(view(),'a','set',o,'valid'));
  }
  const o={...overlay(),relations:{belongs_to:null,depends_on:['a','a','missing'],related_to:[],independent:null}};
  const errors=managementFieldErrors(o,'a',new Set(['b']));
  assert.ok(errors.every(error=>error.field==='relations.depends_on'));
  assert.match(errors.map(error=>error.message).join(' '),/重复.*自身.*不可用/);
});

test('proposal command carries an exact detached reference only on explicit set confirmation',()=>{
  const ref=proposalRef(),command=buildManagementCommand(view(),'a','set',overlay(),'proposal-set',ref);
  assert.deepEqual(command.proposal_ref,ref);command.proposal_ref.proposal_id='changed';assert.equal(ref.proposal_id,'synthetic-related');
  assert.ok(!Object.hasOwn(buildManagementCommand(view(),'a','set',overlay(),'ordinary'),'proposal_ref'));
  assert.throws(()=>buildManagementCommand(view(),'a','retract',null,'retract',ref),/MANAGEMENT_PROPOSAL_INVALID/);
  for(const invalid of [{...ref,extra:true},{...ref,source_sha256:'bad'},{...ref,proposal_id:'../bad'}])
    assert.throws(()=>buildManagementCommand(view(),'a','set',overlay(),'invalid',invalid),/MANAGEMENT_PROPOSAL_INVALID/);
});

test('explicit proposal confirmation unions related targets and saves the complete current draft',async()=>{
  const v=proposedView();v.overlay={...overlay(),priority:4,start_date:'2026-10-01',end_date:'2026-10-31',
    timezone:'Asia/Tokyo',milestones:[{id:'release',label:'Release',date:'2026-10-20'}],
    relations:{belongs_to:'c',depends_on:['b'],related_to:['c'],independent:true}};
  const before=structuredClone(v),sent=[],n=mount(v,{projectId:'a',owner:true,save:async command=>{sent.push(command);return receipt(command,{...v,revision:3,overlay:command.value});}});
  await change(input(n,'优先级（数值）'),'7');assert.equal(sent.length,0);
  assert.match(n.textContent,/会保存当前全部草稿/);assert.match(n.textContent,/不代表原始清单已完整验证/);
  await button(n,'按相关项目确认 · synthetic-related').fire('click');
  assert.equal(sent.length,1);assert.deepEqual(sent[0].proposal_ref,proposalRef());
  assert.equal(sent[0].value.priority,7);assert.equal(sent[0].value.timezone,'Asia/Tokyo');
  assert.equal(sent[0].value.start_date,'2026-10-01');assert.deepEqual(sent[0].value.milestones,v.overlay.milestones);
  assert.deepEqual(sent[0].value.relations,{belongs_to:'c',depends_on:['b'],related_to:['c','b'],independent:true});
  assert.deepEqual(v,before,'reading and accepting never mutates the proposal DTO');
  await change(input(n,'显示顺序'),'2');await button(n,'保存配置').fire('click');
  assert.equal(sent.length,2);assert.ok(!Object.hasOwn(sent[1],'proposal_ref'),'ordinary later save is not a second proposal confirmation');
});

test('invalid proposal references or unavailable targets expose no confirmation action',async()=>{
  for(const mutate of [p=>delete p.proposal_ref,p=>p.proposal_ref.proposal_hash='invalid',p=>p.project_ids=['a','missing'],
    p=>p.project_ids=['b','c'],p=>p.project_ids=['a','b','b'],p=>p.mapping='depends_on',p=>p.evidence_scope='inventory_accepted']){
    const v=proposedView();mutate(v.proposals[0]);const n=mount(v,{projectId:'a',owner:true,save:async()=>assert.fail('invalid proposal submitted')});
    assert.equal(all(n).filter(node=>node.tagName==='button'&&node.textContent.startsWith('按相关项目确认')).length,0);
    assert.match(n.textContent,/不能据此确认关系/);
  }
  const guest=mount(proposedView(),{projectId:'a',owner:false,save:async()=>assert.fail('guest proposal submitted')});
  assert.equal(button(guest,'按相关项目确认 · synthetic-related').disabled,true);
  await button(guest,'按相关项目确认 · synthetic-related').fire('click');
});

test('proposal UNKNOWN retries the exact accepted request and blocks history or relation changes',async()=>{
  const v=proposedView();v.history_recent=[{revision:1,request_id:'history',before:null,after:overlay()}];
  const sent=[],n=mount(v,{projectId:'a',owner:true,save:async command=>{sent.push(command);if(sent.length===1)throw {outcome:'UNKNOWN'};return receipt(command,{...v,revision:3,overlay:command.value});}});
  await button(n,'按相关项目确认 · synthetic-related').fire('click');
  assert.match(status(n),/结果未知/);assert.equal(button(n,'撤销本项目关系').disabled,true);
  assert.equal(button(n,'恢复为版本 1 的配置').disabled,true);assert.equal(button(n,'按相关项目确认 · synthetic-related').disabled,true);
  await button(n,'恢复为版本 1 的配置').fire('click');await button(n,'撤销本项目关系').fire('click');
  await button(n,'重试确认提交').fire('click');assert.deepEqual(sent[1],sent[0]);assert.deepEqual(sent[1].proposal_ref,proposalRef());
});

test('proposal CAS conflict preserves acceptance intent through explicit reload and fresh-CAS save',async()=>{
  const v=proposedView(),sent=[],n=mount(v,{projectId:'a',owner:true,save:async command=>{sent.push(command);if(sent.length===1)throw {status:409,outcome:'NOT_COMMITTED'};return receipt(command,{...v,revision:5,overlay:command.value});},reload:async()=>({...v,revision:4,config_hash:'b'.repeat(64)})});
  await button(n,'按相关项目确认 · synthetic-related').fire('click');assert.match(status(n),/配置版本已变化/);
  assert.equal(button(n,'保存配置').disabled,true);await button(n,'重新读取当前版本').fire('click');
  await button(n,'保存配置').fire('click');assert.equal(sent[1].expected_revision,4);assert.equal(sent[1].expected_hash,'b'.repeat(64));
  assert.notEqual(sent[1].request_id,sent[0].request_id);assert.deepEqual(sent[1].proposal_ref,proposalRef());
  assert.deepEqual(sent[1].value.relations.related_to,['b']);
});

test('history restore is only a detached draft and saves a new version without replaying old provenance',async()=>{
  const v=proposedView(),historic=rolling();historic.priority=8;historic.start_date='2026-10-05';
  v.revision=7;v.history_recent=[{revision:3,request_id:'old-request',before:null,after:historic,proposal_ref:proposalRef()},
    {revision:4,request_id:'withdrawn',before:historic,after:null}];
  const before=structuredClone(v),sent=[],n=mount(v,{projectId:'a',owner:true,save:async command=>{sent.push(command);return receipt(command,{...v,revision:8,overlay:command.value});}});
  assert.match(n.textContent,/历史未配置/);assert.match(n.textContent,/提议确认来源：synthetic-related/);
  assert.equal(button(n,'恢复为版本 4 的配置'),undefined,'withdrawn whole overlay is displayed without silently converting null into a configured blank overlay');
  await button(n,'恢复为版本 3 的配置').fire('click');assert.equal(sent.length,0);
  assert.equal(input(n,'优先级（数值）').value,'8');assert.equal(input(n,'开始日期').value,'2026-10-05');assert.match(status(n),/当前版本仍为 7/);
  await button(n,'保存配置').fire('click');assert.equal(sent[0].expected_revision,7);
  assert.notEqual(sent[0].request_id,'old-request');assert.ok(!Object.hasOwn(sent[0],'proposal_ref'));
  assert.deepEqual(sent[0].value.rolling_plan,historic.rolling_plan);assert.deepEqual(v,before);
});

test('relation-only withdrawal retains all other draft fields and requires a separate save',async()=>{
  const v=proposedView();v.overlay={...rolling(),priority:5,relations:{belongs_to:'b',depends_on:['b'],related_to:['c'],independent:false}};
  const before=structuredClone(v.overlay),sent=[],n=mount(v,{projectId:'a',owner:true,save:async command=>{sent.push(command);return receipt(command,{...v,revision:3,overlay:command.value});}});
  await button(n,'撤销本项目关系').fire('click');assert.equal(sent.length,0);assert.match(status(n),/仅撤销.*草稿.*日期和其他配置保留/);
  assert.equal(input(n,'关系配置').value,'unconfigured');assert.equal(input(n,'优先级（数值）').value,'5');
  await button(n,'保存配置').fire('click');assert.equal(sent[0].operation,'set');assert.equal(sent[0].value.relations,null);
  assert.deepEqual({...sent[0].value,relations:before.relations},before);assert.ok(!Object.hasOwn(sent[0],'proposal_ref'));
});
test('409 preserves draft and requires explicit current-version reload before save',async()=>{const sent=[];let reads=0;const n=mount(view(),{projectId:'a',owner:true,save:async c=>{sent.push(c);if(sent.length===1)throw {status:409,code:'MANAGEMENT_CONFLICT',outcome:'NOT_COMMITTED'};return receipt(c,{...view(),revision:5,overlay:c.value});},reload:async()=>{reads++;return {...view(),revision:4,config_hash:'b'.repeat(64)};}});await change(input(n,'优先级（数值）'),'7');await button(n,'保存配置').fire('click');assert.equal(input(n,'优先级（数值）').value,'7');assert.equal(button(n,'保存配置').disabled,true);assert.equal(reads,0);await button(n,'重新读取当前版本').fire('click');assert.equal(input(n,'优先级（数值）').value,'7');assert.equal(reads,1);await button(n,'保存配置').fire('click');assert.equal(sent[1].expected_revision,4);assert.equal(sent[1].expected_hash,'b'.repeat(64));assert.notEqual(sent[0].request_id,sent[1].request_id);});
test('unknown commit retries exact request and body, blocks competing edits and withdrawal',async()=>{const sent=[];const n=mount(view(),{projectId:'a',owner:true,save:async c=>{sent.push(c);if(sent.length===1)throw {outcome:'UNKNOWN',details:{private:'secret'}};return {...receipt(c,{...view(),revision:8,overlay:c.value}),receipt:{request_id:c.request_id,revision:8,committed_revision:3,replayed:true}};},reload:async()=>view()});await change(input(n,'显示顺序'),'9');await button(n,'保存配置').fire('click');assert.equal(input(n,'显示顺序').disabled,true);assert.equal(button(n,'还原草稿').disabled,true);assert.equal(button(n,'重新读取当前版本').disabled,true);assert.equal(button(n,'撤回全部管理配置').disabled,true);assert.doesNotMatch(status(n),/secret/);await button(n,'重试确认提交').fire('click');assert.deepEqual(sent[0],sent[1]);assert.match(status(n),/当前版本 8，本次提交版本 3（重试已确认）/);});
test('in-flight save disables editing and double submission',async()=>{let release,calls=0;const waiting=new Promise(r=>release=r);const n=mount(view(),{projectId:'a',owner:true,save:async c=>{calls++;await waiting;return receipt(c);}});const p=button(n,'保存配置').fire('click');assert.equal(button(n,'保存配置').disabled,true);assert.equal(input(n,'关注').disabled,true);await button(n,'保存配置').fire('click');assert.equal(calls,1);release();await p;});
test('permission failure preserves draft and reports without private error content',async()=>{const n=mount(view(),{projectId:'a',owner:true,save:async()=>{throw {status:403,details:{token:'secret',path:'/private'}};}});await change(input(n,'优先级（数值）'),'8');await button(n,'保存配置').fire('click');assert.equal(input(n,'优先级（数值）').value,'8');assert.match(status(n),/权限失效/);assert.doesNotMatch(n.textContent,/secret|\/private/);});
test('retract sends null without modifying source facts',async()=>{const sent=[],v=view();v.overlay.priority=4;const n=mount(v,{projectId:'a',owner:true,save:async c=>{sent.push(c);return receipt(c,{...view(),overlay:null});}});await button(n,'撤回全部管理配置').fire('click');assert.equal(sent[0].operation,'retract');assert.equal(sent[0].value,null);assert.equal(v.overlay.priority,4);assert.equal(input(n,'优先级（数值）').value,'');});
test('layout dragging changes only draft, no inline style or auto save, coordinates keyboard editable',async()=>{const sent=[],v=view();v.overlay.relations={belongs_to:'b',depends_on:['c'],related_to:[],independent:true};const n=mount(v,{projectId:'a',owner:true,save:async c=>{sent.push(c);return receipt(c);}});const svg=all(n).find(x=>x.tagName==='svg');await svg.fire('pointerdown',{pointerId:1,clientX:120,clientY:40});await svg.fire('pointermove',{pointerId:1,clientX:500,clientY:-10});await svg.fire('pointerup',{pointerId:1});assert.equal(sent.length,0);assert.equal(input(n,'布局 X').value,'320');assert.equal(input(n,'布局 Y').value,'0');await change(input(n,'布局 X'),'25');await button(n,'保存配置').fire('click');assert.deepEqual(sent[0].value.layout,{x:25,y:0});assert.deepEqual(sent[0].value.relations,v.overlay.relations);assert.ok(all(n).every(x=>!Object.hasOwn(x.attributes,'style')));});

test('plain transport failure is uncertain and retries same command',async()=>{const sent=[];const n=mount(view(),{projectId:'a',owner:true,save:async c=>{sent.push(c);if(sent.length===1)throw new Error('private transport detail');return receipt(c);}});await button(n,'保存配置').fire('click');assert.match(status(n),/结果未知/);assert.doesNotMatch(status(n),/private/);await button(n,'重试确认提交').fire('click');assert.deepEqual(sent[0],sent[1]);});
test('post-commit page refresh failure never marks committed save uncertain',async()=>{let calls=0;const n=mount(view(),{projectId:'a',owner:true,save:async c=>{calls++;return receipt(c);},onSaved:()=>{throw new Error('private');}});await button(n,'保存配置').fire('click');assert.equal(calls,1);assert.match(status(n),/配置已保存.*页面刷新失败/);assert.equal(button(n,'重试确认提交'),undefined);});

test('setOwner preserves draft across live disconnect and guards actual save',async()=>{let calls=0;const n=mount(view(),{projectId:'a',owner:true,save:async c=>{calls++;return receipt(c);}});await change(input(n,'优先级（数值）'),'6');n.setOwner(false);assert.equal(button(n,'保存配置').disabled,true);assert.equal(input(n,'优先级（数值）').value,'6');await button(n,'保存配置').fire('click');assert.equal(calls,0);n.setOwner(true);assert.equal(input(n,'优先级（数值）').value,'6');await button(n,'保存配置').fire('click');assert.equal(calls,1);});
test('confirmed non-committed 503 names unavailable service and preserves draft',async()=>{const n=mount(view(),{projectId:'a',owner:true,save:async()=>{throw {status:503,outcome:'NOT_COMMITTED',code:'MANAGEMENT_NOT_INITIALIZED'};}});await change(input(n,'优先级（数值）'),'6');await button(n,'保存配置').fire('click');assert.equal(input(n,'优先级（数值）').value,'6');assert.match(status(n),/服务暂不可用/);});

test('known commit with null view keeps draft and forbids new effects until explicit reload succeeds',async()=>{let calls=0,reads=0,updates=0;const n=mount(view(),{projectId:'a',owner:true,save:async c=>{calls++;return {receipt:{request_id:c.request_id,revision:5,committed_revision:3,replayed:true},view:null,view_error:{code:'PRIVATE_DETAIL'}};},onSaved:()=>updates++,reload:async()=>{reads++;if(reads===1)throw new Error('private read error');return {...view(),revision:6,config_hash:'b'.repeat(64)};}});await change(input(n,'优先级（数值）'),'12');await button(n,'保存配置').fire('click');assert.equal(calls,1);assert.equal(updates,0);assert.match(status(n),/本次提交版本 3 已保存.*当前值读取失败/);assert.doesNotMatch(status(n),/PRIVATE_DETAIL/);assert.equal(input(n,'优先级（数值）').value,'12');assert.equal(button(n,'重试确认提交'),undefined);assert.equal(button(n,'保存配置').disabled,true);assert.equal(button(n,'撤回全部管理配置').disabled,true);assert.equal(button(n,'还原草稿').disabled,true);assert.equal(button(n,'重新读取当前版本').disabled,false);await button(n,'保存配置').fire('click');await button(n,'撤回全部管理配置').fire('click');await button(n,'还原草稿').fire('click');assert.equal(calls,1);assert.equal(input(n,'优先级（数值）').value,'12');n.setOwner(false);n.setOwner(true);assert.equal(button(n,'保存配置').disabled,true);await button(n,'重新读取当前版本').fire('click');assert.equal(button(n,'保存配置').disabled,true);assert.equal(input(n,'优先级（数值）').value,'12');await button(n,'重新读取当前版本').fire('click');assert.equal(reads,2);assert.equal(button(n,'保存配置').disabled,false);assert.equal(input(n,'优先级（数值）').value,'12');assert.equal(calls,1);});

test('confirmed receipt plus malformed view never becomes unknown or loses submitted draft',async()=>{let calls=0;const n=mount(view(),{projectId:'a',owner:true,save:async c=>{calls++;return {receipt:{request_id:c.request_id,revision:3,committed_revision:3,replayed:false},view:{...view(),revision:3,overlay:{priority:99},project_options:{invalid:true}}};},reload:async()=>view()});await change(input(n,'优先级（数值）'),'7');await button(n,'保存配置').fire('click');assert.equal(calls,1);assert.equal(input(n,'优先级（数值）').value,'7');assert.match(status(n),/版本 3 已保存.*读取失败/);assert.equal(button(n,'重试确认提交'),undefined);assert.equal(button(n,'保存配置').disabled,true);assert.equal(button(n,'重新读取当前版本').disabled,false);});

test('belonging cycle rejection preserves editable draft without claiming version change',async()=>{let calls=0;const n=mount(view(),{projectId:'a',owner:true,save:async c=>{calls++;if(calls===1)throw {status:409,code:'MANAGEMENT_RELATION_CONFLICT',outcome:'NOT_COMMITTED'};return receipt(c,{...view(),revision:3,overlay:c.value});}});await change(input(n,'关系配置'),'confirmed','change');await change(input(n,'归属项目'),'b','change');await button(n,'保存配置').fire('click');assert.match(status(n),/归属关系形成循环.*配置未保存/);assert.doesNotMatch(status(n),/版本已变化/);assert.equal(input(n,'归属项目').value,'b');assert.equal(button(n,'保存配置').disabled,false);await change(input(n,'归属项目'),'','change');await button(n,'保存配置').fire('click');assert.equal(calls,2);assert.match(status(n),/已保存/);});

test('all field names remain exact accessible labels independent of select options',()=>{const n=mount(view(),{projectId:'a',owner:true,save:async()=>null});for(const label of ['优先级（数值）','开始日期','结束日期','显示顺序','关注','关系配置','归属项目','依赖项目（可多选）','关联项目（可多选）','独立项目','布局 X','布局 Y'])assert.equal(input(n,label).attributes['aria-label'],label);});

test('rejected config shows unavailable unknown revision while source page can retain facts',async()=>{let calls=0;const n=mount({...view(),available:false,editable:false,reason:'MANAGEMENT_CONFIG_REJECTED',revision:null,config_hash:null,overlay:null},{projectId:'a',owner:false,save:async()=>calls++});assert.match(n.textContent,/管理配置读取失败，当前版本未知；项目来源事实仍可查看/);assert.doesNotMatch(n.textContent,/管理配置尚未初始化/);await button(n,'保存配置').fire('click');assert.equal(calls,0);});

test('actual relation warnings use readable messages and retain backend code only in DTO',()=>{const v={...view(),warnings:['PROJECT_DEPENDENCY_CYCLE','MANAGEMENT_ORPHAN_REFERENCE']};const n=mount(v,{projectId:'a',owner:true,save:async()=>null});assert.match(n.textContent,/项目依赖存在循环/);assert.match(n.textContent,/部分关系项目当前不在可编辑范围内/);assert.doesNotMatch(n.textContent,/PROJECT_DEPENDENCY_CYCLE|MANAGEMENT_ORPHAN_REFERENCE/);assert.deepEqual(v.warnings,['PROJECT_DEPENDENCY_CYCLE','MANAGEMENT_ORPHAN_REFERENCE']);});

const planned=()=>({...overlay(),timezone:null,milestones:null});
function milestones(n){return all(n).filter(x=>x.attributes['data-milestone-id']);}
function milestoneInput(n,key){return all(n).find(x=>x.tagName==='input'&&x.attributes['aria-label']?.startsWith(`计划里程碑${key}（`));}
async function addPlanned(n){await change(input(n,'计划里程碑配置'),'configured','change');await button(n,'添加计划里程碑').fire('click');}

test('new nine-field commands preserve literal timezone, date-only and unknown planned values',()=>{const p=planned();p.timezone='Asia/Tokyo';p.start_date='0001-01-01';p.end_date='9999-12-31';p.milestones=[{id:'stable',label:'计划发布',date:'2026-10-14'},{id:'unknown',label:'待排期',date:null}];const command=buildManagementCommand(view(),'a','set',p,'nine');assert.deepEqual(command.value,p);assert.equal(Object.keys(command.value).length,9);command.value.milestones[0].label='changed';assert.equal(p.milestones[0].label,'计划发布');assert.equal(Object.keys(buildManagementCommand(view(),'a','set',overlay(),'old').value).length,7);});

test('new overlay refuses missing added field, unknown fields, invalid timezone and invalid milestone semantics',()=>{const mutate=[p=>delete p.timezone,p=>delete p.milestones,p=>p.timezone='',p=>p.timezone='Mars/Olympus',p=>p.timezone='../Tokyo',p=>p.timezone='/etc/passwd',p=>p.timezone='Asia/Tokyo '.repeat(30),p=>p.actual_start=null,p=>p.milestones={},p=>p.milestones=[{id:'a',label:'A',date:'2026-02-30'}],p=>p.milestones=[{id:'a',label:'A',date:'2026-10-14T00:00:00Z'}],p=>p.milestones=[{id:'a',label:'A',date:'0000-01-01'}],p=>p.milestones=[{id:'a',label:' ',date:null}],p=>p.milestones=[{id:'',label:'A',date:null}],p=>p.milestones=[{id:'a',label:'A',date:null,actual:true}],p=>p.milestones=[{id:'a',label:'A',date:null},{id:'a',label:'B',date:null}],p=>p.milestones=Array.from({length:1001},(_,i)=>({id:String(i),label:'A',date:null}))];for(const f of mutate){const p=planned();f(p);assert.throws(()=>buildManagementCommand(view(),'a','set',p,'invalid'),/MANAGEMENT_VALUE_INVALID/);}});

test('legacy and absent overlay normalize only local draft with no loaded-view rewrite or auto save',async()=>{for(const old of [overlay(),null]){const v={...view(),overlay:old},before=structuredClone(v),sent=[];const n=mount(v,{projectId:'a',owner:true,save:async c=>{sent.push(c);return receipt(c,{...view(),overlay:c.value});}});assert.equal(input(n,'计划时区（IANA，可留空）').value,'');assert.equal(input(n,'计划里程碑配置').value,'unconfigured');assert.equal(button(n,'添加计划里程碑').disabled,true);assert.equal(sent.length,0);assert.deepEqual(v,before);await button(n,'保存配置').fire('click');assert.deepEqual(sent[0].value,planned());assert.deepEqual(v,before);}});

test('planned null, explicit empty list, unknown date, stable ID edits and removal stay local until save',async()=>{const v=view(),sent=[];const n=mount(v,{projectId:'a',owner:true,save:async c=>{sent.push(c);return receipt(c,{...view(),overlay:c.value});}});await change(input(n,'计划里程碑配置'),'configured','change');await button(n,'保存配置').fire('click');assert.deepEqual(sent[0].value.milestones,[]);await button(n,'添加计划里程碑').fire('click');const id=milestones(n)[0].attributes['data-milestone-id'];assert.match(id,/^planned-/);await change(milestoneInput(n,'名称'),'发布候选');await change(milestoneInput(n,'日期'),'2026-10-14');await change(input(n,'计划时区（IANA，可留空）'),'Asia/Tokyo');assert.equal(milestones(n)[0].attributes['data-milestone-id'],id);assert.equal(sent.length,1);await button(n,'保存配置').fire('click');assert.deepEqual(sent[1].value.milestones,[{id,label:'发布候选',date:'2026-10-14'}]);await change(milestoneInput(n,'日期'),'');await button(n,'保存配置').fire('click');assert.equal(sent[2].value.milestones[0].date,null);assert.equal(sent[2].value.milestones[0].id,id);await button(n,'删除计划里程碑').fire('click');await button(n,'保存配置').fire('click');assert.deepEqual(sent[3].value.milestones,[]);await change(input(n,'计划里程碑配置'),'unconfigured','change');await change(input(n,'计划时区（IANA，可留空）'),'');await button(n,'保存配置').fire('click');assert.equal(sent[4].value.milestones,null);assert.equal(sent[4].value.timezone,null);assert.deepEqual(v.overlay,overlay());});

test('invalid timezone and milestone date or blank label disable save without effects',async()=>{let calls=0;const n=mount(view(),{projectId:'a',owner:true,save:async()=>calls++});await change(input(n,'计划时区（IANA，可留空）'),'Not/AZone');assert.equal(button(n,'保存配置').disabled,true);await button(n,'保存配置').fire('click');assert.equal(calls,0);await change(input(n,'计划时区（IANA，可留空）'),'UTC');await addPlanned(n);await change(milestoneInput(n,'日期'),'2026-02-30');assert.equal(button(n,'保存配置').disabled,true);await change(milestoneInput(n,'日期'),'2026-02-28');await change(milestoneInput(n,'名称'),'');assert.equal(button(n,'保存配置').disabled,true);await change(milestoneInput(n,'名称'),'计划');assert.equal(button(n,'保存配置').disabled,false);assert.equal(calls,0);});

test('new dynamic controls are native, labelled and preserve repeated-label distinct IDs',async()=>{const sent=[],n=mount(view(),{projectId:'a',owner:true,save:async c=>{sent.push(c);return receipt(c);}});await addPlanned(n);await button(n,'添加计划里程碑').fire('click');const rows=milestones(n);assert.equal(rows.length,2);assert.notEqual(rows[0].attributes['data-milestone-id'],rows[1].attributes['data-milestone-id']);for(const row of rows){for(const field of all(row).filter(x=>x.tagName==='input')){assert.match(field.attributes['aria-label'],/计划里程碑(名称|日期)（planned-/);assert.equal(field.attributes.type,field.attributes['aria-label'].includes('日期')?'date':'text');}const remove=button(row,'删除计划里程碑');assert.match(remove.attributes['aria-label'],/删除计划里程碑（planned-/);}assert.ok(all(n).every(x=>!Object.hasOwn(x.attributes,'style')));assert.equal(input(n,'计划时区（IANA，可留空）').attributes['aria-label'],'计划时区（IANA，可留空）');await button(n,'保存配置').fire('click');assert.equal(sent[0].value.milestones.length,2);assert.equal(sent[0].value.milestones[0].label,sent[0].value.milestones[1].label);});

for(const scenario of ['guest','revoked','inflight','unknown','known-receipt'])test(`planned controls protect draft during ${scenario}`,async()=>{let release;const sent=[],p=planned();p.milestones=[{id:'stable',label:'Original',date:null}];const n=mount({...view(),overlay:p},{projectId:'a',owner:scenario!=='guest',save:async c=>{sent.push(c);if(scenario==='unknown')throw {outcome:'UNKNOWN'};if(scenario==='known-receipt')return {receipt:{request_id:c.request_id,revision:3,committed_revision:3},view:null};if(scenario==='inflight')await new Promise(r=>release=r);return receipt(c);}});let saving;if(scenario==='revoked')n.setOwner(false);else if(scenario==='inflight')saving=button(n,'保存配置').fire('click');else if(scenario==='unknown'||scenario==='known-receipt')await button(n,'保存配置').fire('click');const fields=[input(n,'计划时区（IANA，可留空）'),input(n,'计划里程碑配置'),milestoneInput(n,'名称'),milestoneInput(n,'日期'),button(n,'添加计划里程碑'),button(n,'删除计划里程碑')];for(const field of fields)assert.equal(field.disabled,true);await change(input(n,'计划时区（IANA，可留空）'),'UTC');await change(milestoneInput(n,'名称'),'Forbidden');await change(milestoneInput(n,'日期'),'2026-10-14');await button(n,'添加计划里程碑').fire('click');await button(n,'删除计划里程碑').fire('click');await change(input(n,'计划里程碑配置'),'unconfigured','change');assert.equal(milestones(n).length,1);if(release){release();await saving;}if(scenario==='revoked'||scenario==='guest'){n.setOwner(true);await button(n,'保存配置').fire('click');assert.deepEqual(sent[0].value,p);}else assert.deepEqual(sent[0].value,p);});

test('409 keeps planned draft IDs and dates through explicit reload and sends fresh CAS',async()=>{const sent=[],n=mount(view(),{projectId:'a',owner:true,save:async c=>{sent.push(c);if(sent.length===1)throw {status:409,outcome:'NOT_COMMITTED'};return receipt(c,{...view(),revision:5,overlay:c.value});},reload:async()=>({...view(),revision:4,config_hash:'c'.repeat(64)})});await addPlanned(n);await change(milestoneInput(n,'日期'),'2026-10-14');await change(input(n,'计划时区（IANA，可留空）'),'Asia/Tokyo');await button(n,'保存配置').fire('click');const id=sent[0].value.milestones[0].id;await button(n,'重新读取当前版本').fire('click');assert.equal(milestones(n)[0].attributes['data-milestone-id'],id);assert.equal(milestoneInput(n,'日期').value,'2026-10-14');await button(n,'保存配置').fire('click');assert.equal(sent[1].expected_revision,4);assert.equal(sent[1].expected_hash,'c'.repeat(64));assert.deepEqual(sent[1].value,sent[0].value);});

test('unknown planned save retries exact stable IDs, dates and timezone request',async()=>{const sent=[],n=mount(view(),{projectId:'a',owner:true,save:async c=>{sent.push(c);if(sent.length===1)throw {outcome:'UNKNOWN'};return receipt(c,{...view(),revision:3,overlay:c.value});}});await addPlanned(n);await change(milestoneInput(n,'日期'),'0001-01-01');await change(input(n,'计划时区（IANA，可留空）'),'Asia/Tokyo');await button(n,'保存配置').fire('click');await button(n,'重试确认提交').fire('click');assert.deepEqual(sent[1],sent[0]);assert.equal(milestones(n)[0].attributes['data-milestone-id'],sent[0].value.milestones[0].id);});

const estimate=()=>({schema_version:'1.0',kind:'rolling_estimate',as_of:'2026-10-15',next_review:'2026-10-22',completion_window:null,confidence:'low',basis:[{kind:'source',reference:'current-state',sha256:'b'.repeat(64),summary:'阶段尚有待验收事项'},{kind:'assumption',reference:'weekly-review',sha256:null,summary:'每周复核一次'}],conditions:['实际验收时间取决于所有者安排'],next_milestone:{id:'review-candidate',label:'复核当前候选',window:{earliest:'2026-10-16',latest:'2026-10-20'},condition:'自动检查通过'},acceptance_windows:[{id:'automatic-check',kind:'automatic',label:'工程自动检查',window:{earliest:'2026-10-16',latest:'2026-10-18'},depends_on:['review-candidate'],scope:'当前候选自动检查'},{id:'owner-review',kind:'human',label:'本人实际验收',window:null,depends_on:['automatic-check'],scope:'本人确认可用性'}]});
const rolling=()=>({...planned(),timezone:'Asia/Tokyo',rolling_plan:estimate()});

test('exact ten-field rolling commands preserve detached estimates without adding keys to legacy inputs',()=>{for(const o of [overlay(),planned(),rolling(),{...rolling(),rolling_plan:null}]){const before=structuredClone(o),c=buildManagementCommand(view(),'a','set',o,'rolling');assert.deepEqual(c.value,before);assert.deepEqual(Object.keys(c.value),Object.keys(o));if(c.value.rolling_plan)c.value.rolling_plan.conditions.push('changed');assert.deepEqual(o,before);}assert.throws(()=>buildManagementCommand(view(),'a','set',{...overlay(),rolling_plan:estimate()},'missing'),/MANAGEMENT_VALUE_INVALID/);});

test('rolling validation rejects malformed ranges, source proof, bounds, ids, dependency references and cycles',()=>{const changes=[p=>p.kind='deadline',p=>p.actual_start=null,p=>p.as_of=null,p=>p.as_of='2026-02-30',p=>p.next_review='2026-10-14',p=>p.confidence='certain',p=>p.completion_window={earliest:'2026-10-20',latest:'2026-10-19'},p=>p.completion_window={earliest:null,latest:null},p=>p.basis[0].sha256=null,p=>p.basis[0].summary=' ',p=>p.basis[0].unknown=true,p=>p.basis=Array(21).fill(p.basis[0]),p=>p.conditions=[' '],p=>p.next_milestone.id='../private',p=>p.next_milestone.condition='',p=>p.acceptance_windows[0].kind='completed',p=>p.acceptance_windows[0].id=p.next_milestone.id,p=>p.acceptance_windows[0].depends_on=['missing'],p=>p.acceptance_windows[0].depends_on=['automatic-check'],p=>p.acceptance_windows[0].depends_on=['owner-review'],p=>p.acceptance_windows[0].depends_on=['review-candidate','review-candidate'],p=>p.acceptance_windows[0].scope=' ',p=>p.acceptance_windows[0].window={earliest:'2026-02-30',latest:'2026-03-01'}];for(const mutate of changes){const o=rolling();mutate(o.rolling_plan);assert.throws(()=>buildManagementCommand(view(),'a','set',o,'bad-rolling'),/MANAGEMENT_VALUE_INVALID/);}});

test('readonly rolling summary names estimates, timezone and automatic versus human windows without technical ids',async()=>{let calls=0;const o=rolling(),before=structuredClone(o),n=mount({...view(),overlay:o},{projectId:'a',owner:true,save:async()=>calls++});assert.match(n.textContent,/评估日期当时的预估与验收范围/);assert.match(n.textContent,/不随来源刷新自动改写/);assert.match(n.textContent,/不是截止日期/);assert.match(n.textContent,/不是实际完成或验收记录/);assert.match(n.textContent,/计划时区：Asia\/Tokyo/);assert.match(n.textContent,/下次复核：2026-10-22/);assert.match(n.textContent,/可信度：低/);assert.match(n.textContent,/自动验收预估：工程自动检查/);assert.match(n.textContent,/本人验收预估：本人实际验收/);assert.doesNotMatch(n.textContent,/review-candidate|automatic-check|owner-review|current-state|weekly-review/);assert.equal(calls,0);assert.deepEqual(o,before);assert.ok(all(n).every(x=>!Object.hasOwn(x.attributes,'style')));});

test('ordinary edit, reset and owner revocation preserve existing rolling plan with no autosave or legacy key migration',async()=>{for(const o of [overlay(),planned(),rolling(),{...rolling(),rolling_plan:null}]){const sent=[],v={...view(),overlay:o},before=structuredClone(v),n=mount(v,{projectId:'a',owner:true,save:async c=>{sent.push(c);return receipt(c,{...view(),overlay:c.value});}});await change(input(n,'优先级（数值）'),'7');await change(input(n,'结束日期'),'2026-10-25');await button(n,'还原草稿').fire('click');assert.equal(input(n,'优先级（数值）').value,'');n.setOwner(false);await button(n,'保存配置').fire('click');assert.equal(sent.length,0);n.setOwner(true);await change(input(n,'优先级（数值）'),'6');await button(n,'保存配置').fire('click');assert.equal(sent.length,1);assert.equal(Object.hasOwn(sent[0].value,'rolling_plan'),Object.hasOwn(o,'rolling_plan'));if(Object.hasOwn(o,'rolling_plan'))assert.deepEqual(sent[0].value.rolling_plan,o.rolling_plan);assert.deepEqual(v,before);}});

test('rolling estimate survives 409 reload and same-request UNKNOWN retry',async()=>{for(const failure of ['conflict','unknown']){const o=rolling(),sent=[],n=mount({...view(),overlay:o},{projectId:'a',owner:true,save:async c=>{sent.push(c);if(sent.length===1)throw failure==='conflict'?{status:409,outcome:'NOT_COMMITTED'}:{outcome:'UNKNOWN'};return receipt(c,{...view(),revision:5,overlay:c.value});},reload:async()=>({...view(),overlay:o,revision:4,config_hash:'c'.repeat(64)})});await change(input(n,'优先级（数值）'),'4');await button(n,'保存配置').fire('click');if(failure==='conflict')await button(n,'重新读取当前版本').fire('click');await button(n,failure==='conflict'?'保存配置':'重试确认提交').fire('click');assert.equal(sent.length,2);assert.deepEqual(sent[1].value.rolling_plan,o.rolling_plan);assert.deepEqual(sent[0].value,sent[1].value);if(failure==='unknown')assert.deepEqual(sent[0],sent[1]);else{assert.equal(sent[1].expected_revision,4);assert.notEqual(sent[0].request_id,sent[1].request_id);}}});

test('known rolling commit with unavailable view preserves submitted estimate until explicit reload and reset',async()=>{const o=rolling(),fresh=rolling(),sent=[];fresh.rolling_plan.next_review='2026-10-23';const n=mount({...view(),overlay:o},{projectId:'a',owner:true,save:async c=>{sent.push(c);return {receipt:{request_id:c.request_id,revision:3,committed_revision:3},view:null};},reload:async()=>({...view(),overlay:fresh,revision:4})});await change(input(n,'优先级（数值）'),'9');await button(n,'保存配置').fire('click');assert.match(n.textContent,/下次复核：2026-10-22/);assert.equal(button(n,'保存配置').disabled,true);await button(n,'重新读取当前版本').fire('click');assert.match(n.textContent,/下次复核：2026-10-22/);await button(n,'还原草稿').fire('click');assert.match(n.textContent,/下次复核：2026-10-23/);assert.deepEqual(sent[0].value.rolling_plan,o.rolling_plan);});
