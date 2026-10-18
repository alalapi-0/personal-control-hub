import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {timelineRows,renderTimeline} from '../src/hub/web/timeline.mjs';
const sha='a'.repeat(64),time='2026-10-14T12:00:00Z';
const d=(value,timezone=null)=>({kind:'date',value,timezone});
const ts=(value,timezone=null)=>({kind:'timestamp',value,timezone});
function fixture(id='a'){
 const proof={source_ref:'state',sha256:sha,observed_at:time,selector:{path:['timeline']}},value={schema_version:'1.0',project_id:id,plan_version:null,actual_start:d('2026-10-14'),actual_end:d('2026-10-18'),milestones:[],task_dates:[]};
 const record={schema_version:'2.0',project_id:id,success:true,observed_at:time,sources:[{id:'state',sha256:sha}],extensions:{schema_version:'1.2',fact:{task_timeline:{value,proof,unknown_reason:null}}}};
 return {project_id:id,name:'项目 '+id,declared:{connection_read_allowed:true},freshness:{state:'fresh',local_presence:'present'},operational:{latest_attempt:record,last_success:structuredClone(record)},business:{fields:{current_work:{phase:'来源字面阶段'}},timeline:{available:true,role:'current',value,proof,reason:null}},management:{available:true,overlay:{start_date:'2026-10-01',end_date:'2026-10-02',timezone:'Asia/Tokyo',milestones:[{id:'planned-1',label:'计划点',date:'2026-10-03'}]}}};
}
function update(p,fn){fn(p.business.timeline.value);return p;}
function plan(p){
 const nodes=[{id:p.project_id,parent:null,kind:'project',status:'active'},{id:'s1',parent:p.project_id,kind:'stage',status:'active'},{id:'s2',parent:p.project_id,kind:'stage',status:'unknown'}].map(n=>({...n,dependencies:[],condition:null,evidence:[]}));
 const value={schema_version:'1.0',project_id:p.project_id,plan_version:'v1',unit:'任务',counting_basis:'来源计划',catalog_sha256:sha,input_version:sha,nodes,labels:Object.fromEntries(nodes.map(n=>[n.id,n.id])),acceptance:[],counts:{total:0,implemented:0,verified:0,human:0,not_started:0,active:0,blocked:0,cancelled:0},baseline_denominator:0,history:null,history_unknown_reason:'未声明历史'};
 p.operational.latest_attempt.extensions.fact.task_plan={value,unknown_reason:null,proof:{source_ref:'state',sha256:sha,observed_at:time,selector:{path:['task_plan']}}};
 p.business.timeline.value.plan_version='v1';return p;
}
class N{
 constructor(tag){this.tagName=tag;this.children=[];this.events={};this.attributes={};this.value='';this.disabled=false;this._text='';}
 appendChild(n){this.children.push(n);return n;}append(...ns){this.children.push(...ns);}prepend(...ns){this.children.unshift(...ns);}replaceChildren(...ns){this.children=ns;this._text='';}
 addEventListener(k,f){(this.events[k]??=[]).push(f);}setAttribute(k,v){this.attributes[k]=String(v);}
 get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}set textContent(v){this._text=String(v);this.children=[];}
 focus(){document.activeElement=this;}
 fire(k,e={}){for(const f of this.events[k]??[])f(e);}
}
function mount(ps,opts){globalThis.document={createElement:t=>new N(t),createElementNS:(_,t)=>new N(t),createTextNode:t=>{const n=new N('#text');n.textContent=t;return n;}};return renderTimeline(ps,opts);}
const all=n=>[n,...n.children.flatMap(all)];
const label=(n,label)=>all(n).find(x=>x.attributes['aria-label']===label);
const button=(n,label)=>all(n).find(x=>x.tagName==='button'&&x.textContent===label);
const articles=n=>all(n).filter(x=>x.tagName==='article');
const status=n=>all(n).find(x=>x.attributes.role==='status').textContent;

test('definition extension 1.4 retains proven actual dates and rejects mismatched proof',()=>{
 const p=fixture();p.operational.latest_attempt.extensions.schema_version='1.4';
 assert.equal(timelineRows([p])[0].actual.points.length,2);
 p.business.timeline.proof={...p.business.timeline.proof,sha256:'b'.repeat(64)};
 assert.equal(timelineRows([p])[0].actual.points.length,0);
});

test('complete pagination-fed 243-row identity set includes unknown failed removed; input remains untouched',()=>{
 const pages=[100,100,43].map((size,page)=>Array.from({length:size},(_,i)=>fixture('id-'+(page*100+i))));const projects=pages.flat();projects[140].freshness.state='removed_local';projects[180].operational.latest_attempt.success=false;projects[220].business.timeline={available:false,reason:'unknown',role:'current',value:null,proof:null};
 const before=structuredClone(projects),rows=timelineRows(projects);assert.equal(rows.length,243);assert.deepEqual(rows.map(r=>r.id),projects.map(p=>p.project_id));assert.equal(rows[140].actual.points.length,0);assert.equal(rows[180].actual.points.length,0);assert.equal(rows[220].actual.points.length,0);assert.deepEqual(projects,before);
 const n=mount(projects,{total:300});assert.equal(articles(n).length,243);assert.match(n.textContent,/目录筛选可见 243 \/ 完整目录 300/);assert.match(status(n),/243.*243.*300/);
});
test('actual requires fresh read allowed current proof latest success identity source and literal equality; historical facts never substitute',()=>{
 const mutations=[p=>p.freshness.state='stale',p=>p.freshness.authority_drift=true,p=>p.declared.connection_read_allowed=false,p=>p.operational.latest_attempt.success=false,p=>p.operational.latest_attempt.project_id='wrong',p=>p.business.timeline.role='history',p=>p.business.timeline.available=false,p=>p.business.timeline.proof={...p.business.timeline.proof,sha256:'b'.repeat(64)},p=>p.operational.latest_attempt.sources=[],p=>p.operational.latest_attempt.extensions.fact.task_timeline.value=structuredClone({...p.business.timeline.value,actual_end:d('2026-10-19')}),p=>p.business.timeline.proof.selector={path:[]}];
 for(const change of mutations){const p=fixture();change(p);p.updated_at='2026-10-15';p.mtime='2026-10-15';p.round=99;assert.equal(timelineRows([p])[0].actual.points.length,0);assert.equal(timelineRows([p])[0].planned.points.length,3);}
});
test('planned actual distinct, unknown null/end-only/milestones retain exact labels and date markers',()=>{
 const p=update(fixture(),v=>{v.actual_start=null;v.actual_end=d('2026-11-01');v.milestones=[{id:'m1',label:'来源点',at:ts('2026-10-14T23:00:00-04:00','America/New_York')},{id:'m2',label:'未知点',at:null}];});const r=timelineRows([p])[0];
 assert.equal(r.planned.start.value,'2026-10-01');assert.equal(r.actual.start,null);assert.equal(r.actual.continuous,false);assert.equal(r.actual.milestones[0].at.axis,'2026-10-15');assert.equal(r.actual.milestones[0].at.value,'2026-10-14T23:00:00-04:00');assert.equal(r.actual.milestones[1].at,null);
 const n=mount([p]);assert.match(n.textContent,/未知点.*未知 · 未排期/);assert.match(n.textContent,/来源实际/);assert.match(n.textContent,/Hub 管理计划/);assert.match(n.textContent,/America\/New_York/);assert.ok(all(n).some(x=>x.tagName==='a'&&x.attributes.href==='#projects/a'&&x.textContent.includes('2026-11-01')));
});
test('continuous bars only comparable endpoints; local dates preserve basis, mixed zones/granularity stay markers',()=>{
 for(const [a,b,continuous]of [[d('2026-10-14','Asia/Tokyo'),d('2026-10-15','America/New_York'),false],[d('2026-10-14'),ts('2026-10-15T00:00:00Z'),false],[d('2026-10-14','Asia/Tokyo'),d('2026-10-15','Asia/Tokyo'),true],[ts('2026-10-14T23:00:00-04:00','America/New_York'),ts('2026-10-15T05:00:00Z','Asia/Tokyo'),true]]){
 const r=timelineRows([update(fixture(),v=>{v.actual_start=a;v.actual_end=b;})])[0];assert.equal(r.actual.continuous,continuous);if(a.kind==='date')assert.equal(r.actual.start.axis,a.value);
 }
});
test('stage dates use matching source task ids/version without project planned/actual date inheritance',()=>{
 const p=plan(fixture());p.business.timeline.value.task_dates=[{task_id:'s1',actual_start:d('2026-11-01'),actual_end:null,milestones:[]}];let r=timelineRows([p])[0];assert.equal(r.stages.length,2);assert.equal(r.stages[0].start.value,'2026-11-01');assert.equal(r.stages[0].end,null);assert.equal(r.stages[1].points.length,0);assert.equal(r.stages[1].unscheduled,true);
 p.business.timeline.value.plan_version='old';r=timelineRows([p])[0];assert.ok(r.stages.every(s=>s.points.length===0));
 const noPlan=fixture();noPlan.business.fields.current_work.phase=null;const n=mount([noPlan]);assert.match(n.textContent,/来源阶段：未知/);assert.doesNotMatch(n.textContent,/0%|零完成率/);
});
test('year 0001 to 9999 bounded coordinates with full text and jumps; day week month windows/navigation',()=>{
 const p=update(fixture(),v=>{v.actual_start=d('0001-01-01');v.actual_end=d('9999-12-31');});p.management.overlay=null;const n=mount([p]);assert.match(status(n),/0001-01-01 → 0001-03-25/);assert.match(n.textContent,/9999-12-31/);assert.match(n.textContent,/窗口外/);
 const scale=label(n,'时间线缩放');for(const [value,range]of [['day','0001-01-14'],['month','0001-12-31'],['week','0001-03-25']]){scale.value=value;scale.fire('change');assert.ok(status(n).includes(range));}
 button(n,'下一个日期窗口').fire('click');assert.match(status(n),/0001-03-26/);
 const jump=label(n,'跳转到日历日期');jump.value='9999-12-31';button(n,'跳转日期').fire('click');assert.match(status(n),/9999-12-31/);assert.equal(button(n,'下一个日期窗口').disabled,true);
 for(const node of all(n))for(const attr of ['x','cx','x1','x2','width'])if(node.attributes[attr]!==undefined)assert.ok(Number(node.attributes[attr])>=0&&Number(node.attributes[attr])<=640,attr);
 assert.ok(all(n).filter(x=>x.tagName==='text').length<=5);
});
test('native filters and keyboard/touch-equivalent navigation/edit controls keep full counts',()=>{
 const a=fixture(),b=fixture('b');b.management.overlay=null;b.business.timeline.available=false;let edited=null;const n=mount([a,b],{total:243,onEdit:p=>edited=p});const f=label(n,'时间线日期筛选');f.value='dated';f.fire('change');assert.equal(articles(n).length,1);assert.match(status(n),/1.*2.*243/);f.value='unscheduled';f.fire('change');assert.ok(articles(n).some(x=>x.attributes['data-project-id']==='b'));f.value='all';f.fire('change');assert.equal(articles(n).length,2);
 const region=all(n).find(x=>x.attributes.role==='region');assert.equal(region.attributes.tabIndex,'0');let prevented=false;const old=status(n);region.fire('keydown',{key:'ArrowRight',preventDefault:()=>prevented=true});assert.equal(prevented,true);assert.notEqual(status(n),old);button(n,'编辑管理计划').fire('click');assert.equal(edited,a);
});
test('no dates never use wall-clock fallback; no inline style/canvas/grid explosion or markup injection',()=>{
 const p=fixture();p.management.overlay=null;p.business.timeline.available=false;p.name='<script>unsafe</script>';const n=mount([p]);assert.match(status(n),/没有已知日期/);assert.equal(all(n).filter(x=>x.tagName==='svg').length,0);assert.equal(all(n).filter(x=>x.tagName==='script').length,0);assert.ok(all(n).every(x=>!Object.hasOwn(x.attributes,'style')));
 const source=readFileSync(new URL('../src/hub/web/timeline.mjs',import.meta.url),'utf8');assert.doesNotMatch(source,/innerHTML|\.style\b|<canvas|Date\.now\(|new Date\(\)/);
});

test('published phase uses current_work literal; complete date lanes have no false unknown reason; source codes readable',()=>{
 const p=plan(fixture());p.business.timeline.value.task_dates=[{task_id:'s1',actual_start:d('2026-11-01'),actual_end:d('2026-11-02'),milestones:[]}];const r=timelineRows([p])[0];assert.equal(r.planned.reason,null);assert.equal(r.stages[0].reason,null);
 const noPlan=fixture();noPlan.business.phase='wrong legacy field';assert.match(mount([noPlan]).textContent,/来源阶段：来源字面阶段/);assert.doesNotMatch(mount([noPlan]).textContent,/wrong legacy field/);
 for(const code of ['TIMELINE_NOT_DECLARED','SOURCE_STALE','SOURCE_UNAVAILABLE','READ_DISABLED']){const unknown=fixture();unknown.business.timeline.available=false;unknown.business.timeline.reason=code;assert.doesNotMatch(mount([unknown]).textContent,new RegExp(code));}
 const n=mount([p]);assert.ok(all(n).some(x=>x.tagName==='details'&&x.children[0]?.textContent==='查看日期来源'));
});
test('aware timestamp with minute precision follows server contract and SVG height remains bounded for stages',()=>{
 const p=plan(fixture());p.business.timeline.value.actual_start=ts('2026-10-14T23:00-04:00','America/New_York');p.business.timeline.value.actual_end=ts('2026-10-15T05:00Z');p.business.timeline.value.task_dates=[{task_id:'s1',actual_start:d('2026-10-15'),actual_end:d('2026-10-16'),milestones:[]}];const r=timelineRows([p])[0];assert.equal(r.actual.continuous,true);assert.equal(r.actual.start.axis,'2026-10-15');assert.equal(r.actual.start.value,'2026-10-14T23:00-04:00');
 for(const s of all(mount([p])).filter(n=>n.tagName==='svg'))assert.ok(Number(s.attributes.viewBox.split(' ')[3])<=64);
});

test('project context names current project without claiming a complete directory; directory default unchanged',()=>{
 const p=fixture(),n=mount([p],{context:'project',total:243});assert.equal(n.attributes['aria-label'],'当前项目时间线');assert.match(n.textContent,/当前项目日期与未排期阶段/);assert.match(status(n),/当前项目日期筛选显示 1 \/ 当前项目 1/);assert.doesNotMatch(n.textContent,/完整目录|目录筛选|243/);
 const filter=label(n,'时间线日期筛选');filter.value='unscheduled';filter.fire('change');assert.match(status(n),/当前项目日期筛选显示 0/);assert.doesNotMatch(n.textContent,/完整目录|目录筛选/);
 const directory=mount([p],{total:243});assert.equal(directory.attributes['aria-label'],'完整目录时间线');assert.match(directory.textContent,/目录筛选可见 1 \/ 完整目录 243/);
});

test('redraw restores matching region and date button focus for repeated keyboard navigation',()=>{
 const n=mount([fixture()]);let region=all(n).find(x=>x.attributes.role==='region');region.focus();const initial=status(n);
 for(let i=0;i<3;i++){const before=region;region.fire('keydown',{key:'ArrowRight',preventDefault(){}});region=all(n).find(x=>x.attributes.role==='region');assert.notEqual(region,before);assert.equal(document.activeElement,region);}
 assert.notEqual(status(n),initial);const target=all(n).find(x=>x.tagName==='button'&&x.attributes['aria-label']?.startsWith('定位 a'));target.focus();target.fire('click');const replacement=all(n).find(x=>x.attributes['aria-label']===target.attributes['aria-label']);assert.notEqual(replacement,target);assert.equal(document.activeElement,replacement);
});

test('latest failed source gets precise failure reason even when freshness is failed; never uses history',()=>{const p=fixture();p.operational.latest_attempt.success=false;p.freshness.state='failed';const r=timelineRows([p])[0];assert.equal(r.actual.reason,'最新来源读取未成功');assert.equal(r.actual.points.length,0);assert.equal(r.planned.points.length,3);});

test('daily stage status labels and bounded datewindow input match visible axis after navigation',()=>{const n=mount([plan(fixture())]);assert.match(n.textContent,/来源阶段 s1 · 进行中/);assert.match(n.textContent,/来源阶段 s2 · 未知/);assert.doesNotMatch(n.textContent,/· unknown|· active/);const jump=label(n,'跳转到日历日期');assert.equal(jump.value,'2026-10-01');button(n,'下一个日期窗口').fire('click');assert.equal(jump.value,'2026-12-24');const locate=all(n).find(x=>x.attributes['aria-label']==='定位 a Hub 管理计划 开始 2026-10-01');locate.fire('click');assert.equal(jump.value,'2026-10-01');});

function rolling(p=fixture('forecast')){
 p.management.overlay={start_date:null,end_date:null,timezone:'Asia/Tokyo',milestones:null,rolling_plan:{schema_version:'1.0',kind:'rolling_estimate',as_of:'2026-10-15',next_review:'2026-10-22',completion_window:{earliest:'2026-12-01',latest:'2027-01-15'},confidence:'low',basis:[{kind:'source',reference:'canonical-task',sha256:sha,summary:'仍有原编号任务未验收'},{kind:'assumption',reference:'review-policy',sha256:null,summary:'按逐步完成推进，不要求固定截止日期'}],conditions:['先补齐来源事实，不自动推进业务'],next_milestone:{id:'milestone-next',label:'下一阶段核对',window:{earliest:'2026-10-20',latest:'2026-10-22'},condition:'依赖所有者提供事实'},acceptance_windows:[{id:'auto',kind:'automatic',label:'本地自动检查',window:{earliest:'2026-10-23',latest:'2026-10-24'},depends_on:['milestone-next'],scope:'自动测试通过'},{id:'human',kind:'human',label:'本人查看结果',window:null,depends_on:['auto'],scope:'由本人实际操作并确认'}]}};
 p.business.timeline.available=false;return p;
}
test('timeline keeps proven current counts separate from an older estimate and labels historical fallback',()=>{
 const p=rolling(),record=p.operational.latest_attempt;
 record.business={current_work:{status:'blocked'},progress:{completed:3,total:13,counting_basis:'3 of 13 roadmap results accepted; overall percentage unknown'}};
 record.field_provenance=Object.fromEntries(['current_work.status','progress.completed','progress.total','progress.counting_basis'].map(key=>[key,{source_ref:'state',sha256:sha,selector:{path:key.split('.')}}]));
 p.business.state='current';p.business.source_record=structuredClone(record);p.operational.last_success=structuredClone(record);
 p.management.overlay.rolling_plan.basis[0].summary='当前范围完成数 未知/13';
 const before=structuredClone(p),n=mount([p]);
 assert.match(n.textContent,/当前来源：受阻 · 业务进度 3 \/ 13/);
 assert.match(n.textContent,/3 of 13 roadmap results accepted; overall percentage unknown/);
 assert.match(n.textContent,/估计基准 2026-10-15 的来源依据：当前范围完成数 未知\/13/);
 assert.match(n.textContent,/不随来源刷新自动改写/);assert.deepEqual(p,before);
 const invalid=structuredClone(p);invalid.business.source_record.field_provenance['progress.completed'].sha256='b'.repeat(64);
 assert.match(mount([invalid]).textContent,/当前来源：受阻 · 业务进度 未知 \/ 13/);
 const stale=structuredClone(p);stale.freshness.state='stale';const history=mount([stale]);
 assert.match(history.textContent,/历史快照：受阻 · 业务进度 3 \/ 13/);
 assert.doesNotMatch(history.textContent,/当前来源：受阻/);
 for(const change of [q=>q.freshness.local_presence='removed_local',q=>q.declared.connection_read_allowed=false,q=>{q.business.source_record=null;q.operational.last_success=null;}]){
  const unavailable=structuredClone(p);change(unavailable);const view=mount([unavailable]);
  assert.match(view.textContent,/当前来源不可用：未知 · 业务进度 未知 \/ 未知/);
  assert.match(view.textContent,/当前业务事实待核对；尚无可用成功快照/);
  assert.doesNotMatch(view.textContent,/当前来源：|历史快照：|业务进度 3 \/ 13/);
 }
});
test('rolling forecasts retain independent completion milestone automatic human lanes and explicit accessible uncertainty',()=>{
 const p=rolling(),before=structuredClone(p),r=timelineRows([p])[0];assert.equal(r.forecasts.length,4);assert.deepEqual(r.forecasts.map(l=>l.subkind),['completion','milestone','automatic','human']);assert.equal(r.planned.points.length,0);assert.equal(r.actual.points.length,0);assert.equal(r.forecasts[0].start.value,'2026-12-01');assert.equal(r.forecasts[3].points.length,0);assert.equal(r.dated,true);assert.equal(r.unscheduled,true);assert.deepEqual(p,before);
 const n=mount([p]);for(const expected of ['滚动估计（非截止日期）','估计基准 2026-10-15','下次复核 2026-10-22','信心 低','Asia/Tokyo','先补齐来源事实','仍有原编号任务未验收','按逐步完成推进','窗口最早','窗口最晚','前置条件：依赖所有者提供事实','验收范围：由本人实际操作并确认','依赖：自动验收：本地自动检查','尚非验收回执','实际验收日期未知'])assert.ok(n.textContent.includes(expected),expected);
 assert.ok(all(n).some(x=>x.attributes.class?.includes('timeline-forecast-completion')));assert.ok(all(n).some(x=>x.attributes.className?.includes('timeline-forecast-human')));assert.ok(all(n).every(x=>!Object.hasOwn(x.attributes,'style')));assert.doesNotMatch(n.textContent,/实际工期为|本人验收已通过/);
});
test('forecast-only rows enter date filter while unknown completion and conditional human windows remain unscheduled without invented dates',()=>{
 const a=rolling(),b=rolling(fixture('unknown'));b.management.overlay.rolling_plan.completion_window=null;b.management.overlay.rolling_plan.next_milestone.window=null;b.management.overlay.rolling_plan.acceptance_windows.forEach(w=>w.window=null);
 const rows=timelineRows([a,b]);assert.equal(rows[1].points.length,0);assert.equal(rows[1].dated,false);assert.equal(rows[1].unscheduled,true);
 const unknown=mount([b]);assert.match(status(unknown),/没有已知日期；未推断当前日期/);assert.match(unknown.textContent,/尚无法估计日期窗口/);assert.match(unknown.textContent,/拟议本人验收：本人查看结果/);assert.equal(all(unknown).filter(n=>n.tagName==='svg').length,0);
 const n=mount([a,b],{total:243}),f=label(n,'时间线日期筛选');f.value='dated';f.fire('change');assert.deepEqual(articles(n).map(n=>n.attributes['data-project-id']),['forecast']);assert.match(status(n),/1.*2.*243/);f.value='unscheduled';f.fire('change');assert.equal(articles(n).length,2);f.value='all';f.fire('change');assert.equal(articles(n).length,2);
});
test('forecast windows keep source stages unscheduled and restore keyboard and date-locate focus',()=>{
 const p=rolling(plan(fixture('forecast'))),r=timelineRows([p])[0];assert.equal(r.stages.length,2);assert.ok(r.stages.every(s=>s.points.length===0&&s.unscheduled));
 const n=mount([p]);let region=all(n).find(x=>x.attributes.role==='region');region.focus();region.fire('keydown',{key:'ArrowRight',preventDefault(){}});region=all(n).find(x=>x.attributes.role==='region');assert.equal(document.activeElement,region);
 const locate=all(n).find(x=>x.attributes['aria-label']==='定位 forecast 预计完成窗口（非截止日期） 窗口最早 2026-12-01');assert.ok(locate);locate.fire('click');assert.equal(label(n,'跳转到日历日期').value,'2026-12-01');assert.equal(document.activeElement.attributes['aria-label'],locate.attributes['aria-label']);assert.match(n.textContent,/来源阶段 s2 · 未知/);
});
test('engineering verification requires current proof and never turns generic accepted or forecast dates into personal acceptance receipts',()=>{
 const p=rolling(),r=p.operational.latest_attempt;p.business.fields.current_work.accepted=true;p.business.fields.verification={status:'checks_passed'};r.business={verification:{status:'checks_passed'}};r.field_provenance={'verification.status':{source_ref:'state',sha256:sha,selector:{path:['verification','status']}}};
 assert.equal(timelineRows([p])[0].verification,'checks_passed');let n=mount([p]);assert.match(n.textContent,/来源自动／工程核验状态：checks_passed/);assert.match(n.textContent,/实际自动验收日期：未知/);assert.match(n.textContent,/本人实际验收状态及日期：未知/);assert.doesNotMatch(n.textContent,/本人已通过。?$/);
 for(const mutate of [p=>p.freshness.state='stale',p=>p.declared.connection_read_allowed=false,p=>p.operational.latest_attempt.success=false,p=>p.operational.latest_attempt.field_provenance['verification.status'].sha256='b'.repeat(64),p=>p.operational.latest_attempt.business.verification.status='failed']){const q=structuredClone(p);mutate(q);assert.equal(timelineRows([q])[0].verification,null);}
});

test('optional1.3 retains literal actual timeline and source task stages without bypassing proof',()=>{
 for(const version of ['1.2','1.3']){
  const p=plan(fixture());p.operational.latest_attempt.extensions.schema_version=version;
  let row=timelineRows([p])[0];assert.equal(row.actual.start.value,'2026-10-14');assert.equal(row.stages.length,2);
  p.operational.latest_attempt.extensions.fact.task_timeline.proof={...p.business.timeline.proof,sha256:'b'.repeat(64)};
  row=timelineRows([p])[0];assert.equal(row.actual.start,null);assert.equal(row.actual.end,null);
 }
 const p=fixture();p.operational.latest_attempt.extensions.schema_version='1.1';assert.equal(timelineRows([p])[0].actual.start,null);
});
