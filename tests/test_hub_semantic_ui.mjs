import test from 'node:test';
import assert from 'node:assert/strict';
import {timelineRows,renderTimeline} from '../src/hub/web/timeline.mjs';
import {renderManagement,buildManagementCommand} from '../src/hub/web/management.mjs';
const sha='a'.repeat(64),observed='2026-10-15T00:00:00Z';
const date=value=>({kind:'date',value,timezone:'Asia/Tokyo'});
function estimate(){return {schema_version:'1.0',kind:'rolling_estimate',as_of:'2020-01-01',next_review:'2020-01-08',completion_window:{earliest:'2020-02-01',latest:'2020-02-02'},confidence:'low',basis:[{kind:'source',reference:'state',sha256:sha,summary:'Original local forecast only'}],conditions:['Historical review condition'],next_milestone:null,acceptance_windows:[{id:'human',kind:'human',label:'Old owner review',window:null,depends_on:[],scope:'Original usability review only'},{id:'auto',kind:'automatic',label:'Local checks',window:{earliest:'2020-01-09',latest:'2020-01-10'},depends_on:['human'],scope:'Local engineering scope'}]};}
function management(){return {available:true,editable:true,revision:2,config_hash:'b'.repeat(64),relation_state:'unconfigured',warnings:[],project_options:[{id:'p',name:'Synthetic project'}],overlay:{priority:null,start_date:'2026-10-01',end_date:'2026-10-02',watch:null,order:null,layout:null,relations:null,timezone:'Asia/Tokyo',milestones:null,rolling_plan:estimate()}};}
function project(){
 const proof={source_ref:'state',sha256:sha,observed_at:observed,selector:{path:['timeline']}};
 const value={schema_version:'1.0',project_id:'p',plan_version:null,actual_start:date('2026-10-03'),actual_end:date('2026-10-04'),milestones:[],task_dates:[]};
 const business={current_work:{status:'active'},progress:{completed:3,total:13,counting_basis:'3/13 roadmap results; overall project completion unknown'},verification:{status:'4/4 local engineering checks passed'}};
 const record={schema_version:'2.0',project_id:'p',success:true,observed_at:observed,sources:[{id:'state',sha256:sha}],business,field_provenance:Object.fromEntries(['current_work.status','progress.completed','progress.total','progress.counting_basis','verification.status'].map(k=>[k,{source_ref:'state',sha256:sha,selector:{path:k.split('.')}}])),extensions:{schema_version:'1.2',fact:{task_timeline:{value,proof,unknown_reason:null}}}};
 return {project_id:'p',name:'Synthetic project',declared:{connection_read_allowed:true},freshness:{state:'fresh',authority_drift:false,local_presence:'registered_local'},operational:{latest_attempt:record,last_success:structuredClone(record)},business:{state:'current',fields:structuredClone(business),source_record:structuredClone(record),timeline:{available:true,role:'current',value,proof,reason:null}},management:management()};
}
class N{
 constructor(tag){this.tagName=tag;this.children=[];this.events={};this.attributes={};this._value='';this._text='';this.disabled=false;this.selected=false;}
 appendChild(n){this.children.push(n);return n;}append(...ns){this.children.push(...ns);}prepend(...ns){this.children.unshift(...ns);}replaceChildren(...ns){this.children=ns;this._text='';}
 setAttribute(k,v){this.attributes[k]=String(v);}addEventListener(k,f){(this.events[k]??=[]).push(f);}
 get value(){return this._value;}set value(v){this._value=String(v);if(this.tagName==='select'&&!this.multiple)for(const n of this.children)n.selected=n.value===this._value;}
 get selectedOptions(){return this.children.filter(n=>n.selected);}get textContent(){return this._text+this.children.map(n=>n.textContent).join('');}set textContent(v){this._text=String(v);this.children=[];}
 getBoundingClientRect(){return {left:0,top:0,width:320,height:160};}async fire(type,event={}){for(const f of this.events[type]??[])await f(event);}focus(){document.activeElement=this;}contains(n){return all(this).includes(n);}
}
const all=n=>[n,...n.children.flatMap(all)],button=(n,label)=>all(n).find(x=>x.tagName==='button'&&x.textContent===label),control=(n,label)=>all(n).find(x=>x.attributes['aria-label']===label);
async function dom(fn){const before=globalThis.document;globalThis.document={createElement:t=>new N(t),createElementNS:(_,t)=>new N(t),createTextNode:t=>{const n=new N('#text');n.textContent=t;return n;}};try{await fn();}finally{globalThis.document=before;}}
test('historical human dates and links never create current unscheduled work or executing dependencies',()=>dom(async()=>{
 const p=project(),before=structuredClone(p),row=timelineRows([p])[0];assert.equal(row.historicalWindows.length,1);assert.equal(row.historicalWindows[0].unscheduled,true);assert.equal(row.unscheduled,false);assert.equal(row.forecasts.some(x=>x.subkind==='human'),false);
 assert.deepEqual(row.forecasts.find(x=>x.subkind==='automatic').dependsOn,['human']);assert.equal(row.forecasts.find(x=>x.subkind==='automatic').dependencyRole,'historical_forecast_association');
 const node=renderTimeline([p]),filter=control(node,'时间线日期筛选');assert.match(node.textContent,/历史预测关联（非执行依赖）：历史审核窗口：Old owner review/);assert.match(node.textContent,/历史未记录日期（不计当前未排期）/);filter.value='unscheduled';await filter.fire('change');assert.equal(all(node).filter(x=>x.tagName==='article').length,0);assert.deepEqual(p,before);
}));
test('historical-only dates stay visible as history and never enter current dated filter',()=>dom(async()=>{
 const p=project();p.management.overlay.start_date=null;p.management.overlay.end_date=null;p.management.overlay.rolling_plan.completion_window=null;p.management.overlay.rolling_plan.acceptance_windows[1].window=null;p.management.overlay.rolling_plan.acceptance_windows[0].window={earliest:'2019-01-01',latest:'2019-01-02'};p.business.timeline.available=false;
 const row=timelineRows([p])[0];assert.equal(row.dated,false);assert.equal(row.historicalWindows[0].points.length,2);const node=renderTimeline([p]);assert.match(node.textContent,/2019-01-01/);const filter=control(node,'时间线日期筛选');filter.value='dated';await filter.fire('change');assert.equal(all(node).filter(x=>x.tagName==='article').length,0);
}));
test('source refresh does not refresh old estimate baseline hash scope or historical associations',()=>dom(async()=>{
 const p=project(),frozen=structuredClone(p.management.overlay.rolling_plan);let node=renderTimeline([p]);assert.match(node.textContent,/原估计基准 2020-01-01/);assert.match(node.textContent,new RegExp(sha));
 const current='2026-10-24T00:00:00Z';p.operational.latest_attempt.observed_at=current;p.operational.latest_attempt.extensions.fact.task_timeline.proof.observed_at=current;p.business.source_record=structuredClone(p.operational.latest_attempt);
 node=renderTimeline([p]);assert.match(node.textContent,/估计基准 2020-01-01/);assert.match(node.textContent,/不随来源刷新自动改写/);assert.deepEqual(p.management.overlay.rolling_plan,frozen);
}));
test('engineering PASS generic accepted and human windows cannot synthesize an agent or personal acceptance',()=>dom(async()=>{
 for(const value of [true,'accepted','owner_accepted']){const p=project();p.business.fields.current_work.accepted=value;p.business.fields.owner_accepted=true;p.operational.latest_attempt.business.owner_accepted=true;const node=renderTimeline([p]);assert.match(node.textContent,/来源工程核验状态：4\/4 local engineering checks passed/);assert.match(node.textContent,/业务进度 3 \/ 13/);assert.match(node.textContent,/3\/13 roadmap results; overall project completion unknown/);assert.match(node.textContent,/代理决定：未知／未提供独立记录/);assert.match(node.textContent,/流程外本人回看，不参与状态、依赖或阶段退出/);assert.doesNotMatch(node.textContent,/待本人验收|本人实际验收状态及日期|本人已通过/);}
}));
test('management suggestions cannot become proven actual dates and unknown data stays unknown',()=>dom(async()=>{
 const p=project();p.business.timeline.available=false;const node=renderTimeline([p]);assert.match(node.textContent,/Hub 管理建议日期/);assert.match(node.textContent,/建议开始：2026-10-01/);assert.equal(timelineRows([p])[0].actual.start,null);
 for(const mutate of [p=>p.operational.latest_attempt.success=false,p=>p.freshness.state='stale',p=>p.declared.connection_read_allowed=false,p=>p.business.fields.verification={status:'PASS'}]){const q=project();mutate(q);const n=renderTimeline([q]);assert.match(n.textContent,/代理决定：未知／未提供独立记录/);assert.doesNotMatch(n.textContent,/本人已通过|代理决定：通过/);}
 const empty=renderTimeline([]);assert.match(empty.textContent,/当前日期筛选没有项目/);
}));
test('actual management component preserves historical raw plan while editing explicit suggestion dates',()=>dom(async()=>{
 const view=management(),before=structuredClone(view),sent=[];const n=renderManagement(view,{projectId:'p',owner:true,save:async command=>{sent.push(command);return {receipt:{request_id:command.request_id,revision:3,committed_revision:3},view:{...view,revision:3,overlay:command.value}};}});
 assert.match(n.textContent,/历史审核窗口（|历史审核窗口 ·/);assert.match(n.textContent,/历史预测关联（非执行依赖）/);assert.match(n.textContent,/原估计基准：2020-01-01/);assert.match(n.textContent,new RegExp(sha));assert.match(n.textContent,/代理决定：未知／未提供独立记录/);
 const start=control(n,'管理建议开始日期'),end=control(n,'管理建议结束日期');assert.ok(start);assert.ok(end);assert.equal(sent.length,0);start.value='2026-09-30';await start.fire('input');await button(n,'保存配置').fire('click');assert.equal(sent.length,1);assert.equal(sent[0].value.start_date,'2026-09-30');assert.deepEqual(sent[0].value.rolling_plan,before.overlay.rolling_plan);assert.deepEqual(view,before);
}));
test('strict existing rolling command rejects historical cycles and missing links without a schema migration',()=>dom(async()=>{
 for(const dependency of ['auto','missing']){const v=management();v.overlay.rolling_plan.acceptance_windows[0].depends_on=[dependency];assert.throws(()=>buildManagementCommand(v,'p','set',v.overlay,'bad'),/MANAGEMENT_VALUE_INVALID/);const n=renderManagement(v,{projectId:'p',owner:true,save:async()=>assert.fail('invalid plan saved')});assert.equal(button(n,'保存配置').disabled,true);}
 const v=management(),command=buildManagementCommand(v,'p','set',v.overlay,'valid');assert.deepEqual(command.value.rolling_plan,v.overlay.rolling_plan);assert.equal(command.value.rolling_plan.acceptance_windows.length,2);assert.deepEqual(command.value.rolling_plan.acceptance_windows[1].depends_on,['human']);
}));
