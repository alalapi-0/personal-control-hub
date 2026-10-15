import assert from 'node:assert/strict';
import test from 'node:test';

import {currentSourceAvailable, attention, freshness, refreshable, projectFacts, projectTasks, projectSummary, loadProjectDirectory, validateSyncStatus, loadSyncStatus, coreGapSummary, scheduleSummary, requestSummary} from '../src/hub/web/connection_view.mjs';

function project(overrides = {}) {
  return {
    declared: {
      enabled: true,
      summary_enabled: true,
      connection_read_allowed: true,
      hub_connection_exception: null,
      legacy_hub_connection_exception: null,
      ...overrides.declared,
    },
    operational: {latest_attempt: null, last_success: null, ...overrides.operational},
    freshness: {
      state: 'unknown',
      authority_drift: false,
      local_presence: 'registered_local',
      ...overrides.freshness,
    },
    errors: overrides.errors ?? [],
  };
}

test('actual-registry-shaped manga keeps current failure actionable and refreshable', () => {
  const manga = project({
    declared: {
      enabled: false,
      summary_enabled: true,
      connection_read_allowed: true,
      hub_connection_exception: null,
      legacy_hub_connection_exception: {status: 'AUTHORIZED_EXCEPTION'},
    },
    operational: {
      latest_attempt: {success: false, disposition: 'missing_declaration'},
      last_success: {success: true, disposition: 'resolved'},
    },
    errors: [{code: 'missing_declaration'}],
  });
  assert.equal(attention(manga), true);
  assert.equal(freshness(manga), '最新读取失败');
  assert.equal(refreshable(manga), true);
});

test('latest failure wins over current exception and historical success', () => {
  const failed = project({
    declared: {hub_connection_exception: {status: 'AUTHORIZED_EXCEPTION'}},
    operational: {
      latest_attempt: {success: false, disposition: 'invalid'},
      last_success: {success: true, disposition: 'resolved'},
    },
    freshness: {state: 'stale'},
    errors: [{code: 'invalid'}],
  });
  assert.equal(attention(failed), true);
  assert.equal(freshness(failed), '最新读取失败');
});

test('explicit current permission controls refresh without enabled fallback', () => {
  assert.equal(refreshable(project({
    declared: {enabled: true, connection_read_allowed: false},
  })), false);
  assert.equal(refreshable(project({
    declared: {enabled: true, connection_read_allowed: undefined},
  })), false);
});

test('removed project has its terminal label and no action or attention', () => {
  const removed = project({
    freshness: {state: 'removed_local', local_presence: 'removed_local'},
    operational: {latest_attempt: {success: false, disposition: 'removed_local'}},
    errors: [{code: 'removed_local'}],
  });
  assert.equal(freshness(removed), '已从本地移除');
  assert.equal(attention(removed), false);
  assert.equal(refreshable(removed), false);
});

test('all 23 missing declarations remain unknown failures', () => {
  const missing = Array.from({length: 23}, (_, index) => project({
    declared: {hub_connection_exception: index % 2 ? null : {status: 'AUTHORIZED_EXCEPTION'}},
    operational: {latest_attempt: {success: false, disposition: 'missing_declaration'}},
    freshness: {state: 'unknown'},
    errors: [{code: 'missing_declaration'}],
  }));
  assert.equal(missing.every(item => item.freshness.state === 'unknown'), true);
  assert.equal(missing.every(item => attention(item)), true);
  assert.equal(missing.every(item => freshness(item) === '最新读取失败'), true);
});

test('normal fresh, stale, unknown, drift, and exception labels remain clear', () => {
  assert.deepEqual([
    freshness(project({freshness: {state: 'fresh'}})),
    freshness(project({freshness: {state: 'stale'}})),
    freshness(project()),
    freshness(project({freshness: {state: 'stale', authority_drift: true}})),
    freshness(project({declared: {hub_connection_exception: {status: 'AUTHORIZED_EXCEPTION'}}})),
  ], ['已更新', '上次快照 · 尚未更新', '尚无成功快照',
      '来源规则变化 · 需重新核对', '已登记例外']);
});

test('helpers do not mutate the project DTO', () => {
  const value = project({
    operational: {latest_attempt: {success: false, disposition: 'invalid'}},
    errors: [{code: 'invalid'}],
  });
  const before = JSON.stringify(value);
  attention(value);
  freshness(value);
  refreshable(value);
  assert.equal(JSON.stringify(value), before);
});

function authoritative() {
  const values={'current_work.objective':'Repair preview','current_work.phase':'M2',
    'current_work.round':'R4','current_work.status':'paused','current_work.completed':true,
    'current_work.accepted':false,'current_work.next_action':'Owner reviews version C',
    blockers:[{reason:'Choose version C',recovery_condition:'Owner replies',waiting_for_user:true}],
    'verification.status':'checks_passed','delivery.status':'pending_delivery'};
  const record={schema_version:'2.0',success:true,observed_at:'2026-10-07T00:00:00Z',
    business:{},sources:[{id:'state',path:'STATE.yaml',sha256:'a'.repeat(64)}],field_provenance:{},unknown_fields:{}};
  for(const [key,value] of Object.entries(values)){
    const parts=key.split('.');let target=record.business;
    for(const part of parts.slice(0,-1))target=target[part]??={};
    target[parts.at(-1)]=value;
    record.field_provenance[key]={source_ref:'state',sha256:'a'.repeat(64),selector:{path:parts}};
  }
  const p=project({freshness:{state:'fresh'},operational:{latest_attempt:record,last_success:record}});
  p.business={state:'current',source_record:record,unknown_fields:{'progress.completed':'No declared count.'}};
  return p;
}
const facts=(p,options)=>Object.fromEntries(projectFacts(p,options).flatMap(g=>g.fields.map(f=>[f.key,f])));

test('paused, completed, unaccepted and undelivered facts stay independent with exact provenance',()=>{
  const p=authoritative(),before=JSON.stringify(p),f=facts(p);
  assert.equal(Object.keys(f).length,21);
  assert.equal(f['current_work.status'].text,'已暂停');
  assert.equal(f['current_work.completed'].text,'已完成');
  assert.equal(f['current_work.accepted'].text,'尚未接受');
  assert.equal(f['delivery.status'].text,'待交付');
  assert.equal(f.blockers.text.includes('待用户决定：Choose version C'),true);
  assert.deepEqual(f['current_work.round'].provenance,{path:'STATE.yaml',selector:{path:['current_work','round']},sha256:'a'.repeat(64),observed_at:'2026-10-07T00:00:00Z'});
  assert.equal(f['progress.completed'].known,false);
  assert.equal(f['progress.completed'].reason,'No declared count.');
  assert.equal(JSON.stringify(p),before);
});

test('stale or newer failed read suppresses all current facts and labels only explicit history',()=>{
  for(const state of ['stale','unknown']){
    const p=authoritative();p.freshness.state=state;p.operational.latest_attempt={success:false};
    assert.equal(Object.values(facts(p)).every(f=>!f.known),true);
    assert.equal(facts(p,{history:true})['current_work.completed'].text,'已完成');
  }
});

test('removed and denied projects expose no present or historical business fields',()=>{
  for(const modify of [p=>p.freshness.local_presence='removed_local',p=>p.declared.connection_read_allowed=false]){
    const p=authoritative();modify(p);
    for(const history of [false,true])assert.equal(Object.values(facts(p,{history})).every(f=>!f.known),true);
  }
});

test('missing, mismatched or unsupported field proof never becomes a displayed fact',()=>{
  for(const modify of [p=>delete p.business.source_record.field_provenance['current_work.round'],
    p=>p.business.source_record.field_provenance['current_work.round'].sha256='b'.repeat(64),
    p=>p.business.source_record.schema_version='unsupported']){
    const p=authoritative();modify(p);assert.equal(facts(p)['current_work.round'].known,false);
  }
});

test('accepted work is distinct from publication and waiting requires source assertion',()=>{
  const p=authoritative();p.business.source_record.business.current_work.accepted=true;
  p.business.source_record.business.blockers[0].waiting_for_user=false;
  const f=facts(p);
  assert.equal(f['current_work.accepted'].text,'已接受');assert.equal(f['delivery.status'].text,'待交付');
  assert.equal(f.blockers.text.includes('待用户决定'),false);
});

test('tasks filter exact project identity without deriving phase, progress or acceptance',()=>{
  const p=authoritative(),before=JSON.stringify(p),running={id:'one',project:'a',status:'running'};
  const tasks=[running,{id:'two',project:'aa',status:'checks_complete'}];
  assert.deepEqual(projectTasks(tasks,'a'),[running]);assert.deepEqual(projectTasks(null,'a'),[]);
  assert.equal(JSON.stringify(p),before);assert.equal(facts(p)['current_work.accepted'].text,'尚未接受');
});

function setFact(p,key,value) {
  const record=p.business.source_record,parts=key.split('.');let target=record.business;
  for(const part of parts.slice(0,-1))target=target[part]??={};
  target[parts.at(-1)]=value;
  record.field_provenance[key]={source_ref:'state',sha256:'a'.repeat(64),selector:{path:parts}};
}
const summaryFields=p=>Object.fromEntries(projectSummary(p).fields);

test('daily summary preserves explicit zero, false and empty values without percentage inference',()=>{
  const p=authoritative();setFact(p,'progress.completed',0);setFact(p,'progress.total',0);
  setFact(p,'progress.counting_basis','Declared review units');setFact(p,'blockers',[]);
  setFact(p,'current_work.completed',false);setFact(p,'current_work.accepted',false);
  p.business.quality={integration_quality:'complete'};
  const before=JSON.stringify(p),summary=projectSummary(p),fields=summaryFields(p);
  assert.equal(fields['业务进度 V/N'],'0 / 0');assert.equal(fields['计数依据'],'Declared review units');
  assert.equal(fields['阻塞与待决定'],'来源未列出阻塞');
  assert.equal(facts(p)['current_work.completed'].text,'尚未完成');
  assert.equal(facts(p)['current_work.accepted'].text,'尚未接受');
  assert.equal(summary.quality,'日常查看信息完整');assert.equal(summary.factAt,null);
  assert.equal(JSON.stringify(p),before);
});

test('known numerator and unknown denominator remain independent and never gain a percentage',()=>{
  const p=authoritative();setFact(p,'progress.completed',7);
  const fields=summaryFields(p);assert.equal(fields['业务进度 V/N'],'7 / 未知');
  assert.equal(fields['计数依据'],'未知');assert.equal(projectSummary(p).quality,'可用程度未知');
});

test('daily summary separates observation time from current fact time and full next action',()=>{
  const p=authoritative();p.source={observed_at:'2026-10-14T10:00:00Z'};
  p.freshness.fact_time_state='known';p.freshness.fact_updated_at='2026-10-10T10:00:00Z';
  const next='完整下一步'.repeat(60);setFact(p,'current_work.next_action',next);
  const summary=projectSummary(p);assert.equal(summary.observedAt,p.source.observed_at);
  assert.equal(summary.factAt,p.freshness.fact_updated_at);
  assert.equal(summaryFields(p)['下一步'],next);
});

test('failed and stale summaries suppress history and complete quality while preserving read time',()=>{
  for(const failed of [false,true]){
    const p=authoritative();p.business.quality={integration_quality:'complete'};
    p.source={observed_at:'2026-10-14T10:00:00Z'};
    p.freshness.fact_time_state='known';p.freshness.fact_updated_at='2026-10-10T10:00:00Z';
    if(failed)p.operational.latest_attempt={success:false};else p.freshness.state='stale';
    const summary=projectSummary(p);
    assert.equal(summary.fields.every(([,text])=>text==='未知'||text==='未知 / 未知'),true);
    assert.equal(summary.quality,failed?'当前来源读取失败':'日常查看信息不完整');
    assert.equal(summary.factAt,null);assert.equal(summary.observedAt,p.source.observed_at);
    assert.equal(facts(p,{history:true})['current_work.phase'].text,'M2');
  }
});

test('summary never bypasses mismatched field proof or read denial',()=>{
  const p=authoritative();p.business.source_record.field_provenance['current_work.phase'].sha256='b'.repeat(64);
  assert.equal(summaryFields(p)['项目阶段'],'未知');
  p.declared.connection_read_allowed=false;
  assert.equal(summaryFields(p)['当前目标'],'未知');
});

const directoryHead={sequence:9,hash:'c'.repeat(64)};
function directoryPage(total,offset) {
  return {head:{...directoryHead},total,offset,limit:100,
    projects:Array.from({length:Math.min(100,total-offset)},(_,i)=>({
      project_id:'p'+String(offset+i).padStart(4,'0'),name:'Project '+(offset+i),
      provenance:{ledger_head:{...directoryHead}},
    }))};
}

test('full directory exhausts more than 100 projects exactly once and preserves server order',async()=>{
  const calls=[],result=await loadProjectDirectory(async url=>{
    calls.push(url);return directoryPage(243,Number(new URL(url,'http://fixture').searchParams.get('offset')));
  });
  assert.deepEqual(calls,['/api/projects?offset=0&limit=100','/api/projects?offset=100&limit=100','/api/projects?offset=200&limit=100']);
  assert.equal(result.total,243);assert.equal(result.projects.length,243);
  assert.equal(new Set(result.projects.map(p=>p.project_id)).size,243);
  assert.deepEqual(result.projects.map(p=>p.project_id),Array.from({length:243},(_,i)=>'p'+String(i).padStart(4,'0')));
  assert.deepEqual(result.head,directoryHead);
});

test('empty directory is a valid zero, with no extra read',async()=>{
  let calls=0;const result=await loadProjectDirectory(async()=>{calls++;return directoryPage(0,0);});
  assert.equal(calls,1);assert.deepEqual(result.projects,[]);assert.equal(result.total,0);
});

test('missing, duplicated, mismatched or mixed-head pages refuse the entire directory',async()=>{
  const changes=[
    data=>data.projects.pop(),
    data=>data.projects[0].project_id='p0000',
    data=>data.head.hash='d'.repeat(64),
    data=>data.projects[0].provenance.ledger_head.hash='d'.repeat(64),
    data=>data.total++,
    data=>data.offset=0,
    data=>data.limit=99,
    data=>delete data.head,
  ];
  for(const change of changes){
    let calls=0;
    await assert.rejects(loadProjectDirectory(async()=>{
      const data=directoryPage(201,calls++*100);if(calls===2)change(data);return data;
    }),error=>error.code==='PROJECT_DIRECTORY_INCONSISTENT');
    assert.equal(calls,2);
  }
});

test('directory bound and missing row identities fail without a partial success',async()=>{
  for(const change of [d=>d.total=100001,d=>d.total=null,d=>d.projects[0].project_id='',
    d=>delete d.projects[0].provenance]){
    let calls=0;await assert.rejects(loadProjectDirectory(async()=>{
      calls++;const data=directoryPage(1,0);change(data);return data;
    }),error=>error.code==='PROJECT_DIRECTORY_INCONSISTENT');assert.equal(calls,1);
  }
});

test('fresh installation without a ledger accepts explicit null heads for empty and full registry pages',async()=>{
  for(const total of [0,243]){
    let calls=0;
    const result=await loadProjectDirectory(async()=>{
      const data=directoryPage(total,calls++*100);data.head=null;
      for(const project of data.projects)project.provenance.ledger_head=null;
      return data;
    });
    assert.equal(result.head,null);assert.equal(result.total,total);
    assert.equal(result.projects.length,total);assert.equal(calls,total===0?1:3);
    assert.equal(new Set(result.projects.map(p=>p.project_id)).size,total);
  }
});

test('null heads cannot hide ledger creation, missing row heads or malformed page heads',async()=>{
  const changes=[
    data=>{data.head={...directoryHead};for(const p of data.projects)p.provenance.ledger_head={...directoryHead};},
    data=>delete data.projects[0].provenance.ledger_head,
    data=>data.projects[0].provenance.ledger_head={},
    data=>delete data.head,
    data=>data.head={},
  ];
  for(const change of changes){
    let calls=0;
    await assert.rejects(loadProjectDirectory(async()=>{
      const data=directoryPage(201,calls++*100);data.head=null;
      for(const project of data.projects)project.provenance.ledger_head=null;
      if(calls===2)change(data);
      return data;
    }),error=>error.code==='PROJECT_DIRECTORY_INCONSISTENT');
    assert.equal(calls,2);
  }
});


function syncFixture(total=43,scope=39,head=directoryHead){
  const directory=directoryPage(total,0);
  directory.projects=Array.from({length:total},(_,i)=>({project_id:'p'+i,
    provenance:{ledger_head:head},declared:{connection_read_allowed:i<scope,summary_enabled:true},freshness:{state:'fresh'}}));
  directory.head=head;
  const status={schema_version:'1.0',scope_id:'frozen-test-scope',scope_total:scope,head,
    counts:{registered:total,scope,eligible:scope,excluded:total-scope,ineligible:0,
      complete:0,partial:scope,failed:0,stale:0,unread:0},
    eligible_project_ids:directory.projects.slice(0,scope).map(p=>p.project_id),
    excluded:directory.projects.slice(scope).map(p=>({project_id:p.project_id,reason:'legacy excluded'})),
    discovery:{mode:'registered_scope_only',registered_candidates:scope,unregistered_count:null},
    last_request:null,failure_codes:{},core_field_gaps:{'current_work.phase':scope}};
  return {directory,status};
}

test('sync counters preserve frozen scope across a complete directory larger than 100',async()=>{
  const {directory,status}=syncFixture(243,239);
  let calls=0;
  const result=await loadSyncStatus(async url=>{calls++;assert.equal(url,'/api/sync-status');return status;},directory);
  assert.equal(calls,1);assert.equal(result.scope_total,239);assert.equal(result.counts.registered,243);
  assert.equal(result.eligible_project_ids.length,239);
  assert.equal(coreGapSummary(result),'项目阶段 239 项');
  // A search result or first page cannot substitute for the complete directory.
  assert.throws(()=>validateSyncStatus({...directory,projects:directory.projects.slice(0,100)},status),{code:'SYNC_STATUS_INCONSISTENT'});
});

test('zero scope and explicit null genesis heads remain real known zero values',()=>{
  for(const total of [0,4]){
    const {directory,status}=syncFixture(total,0,null);
    assert.equal(validateSyncStatus(directory,status).scope_total,0);
    assert.equal(status.counts.partial,0);assert.equal(status.discovery.unregistered_count,null);
  }
});

test('scope withholding preserves denominator and includes missing registrations as unread',()=>{
  const {directory,status}=syncFixture();
  status.eligible_project_ids.pop();status.counts.eligible--;status.counts.ineligible++;
  status.counts.partial--;status.counts.unread++;
  status.excluded.push({project_id:'missing-registration',reason:'current registered identity missing'});status.counts.excluded++;
  assert.equal(validateSyncStatus(directory,status).scope_total,39);
  assert.equal(status.counts.eligible,38);assert.equal(status.counts.excluded,5);
});

test('missing, malformed, mixed-head and permission-conflicting sync data never reduces scope',()=>{
  const changes=[
    (d,s)=>delete s.head,(d,s)=>s.head=null,(d,s)=>s.head={...s.head,hash:'d'.repeat(64)},
    (d,s)=>delete s.counts,(d,s)=>delete s.counts.unread,(d,s)=>s.counts.partial--,
    (d,s)=>delete d.projects.at(-1).provenance.ledger_head,
    (d,s)=>d.projects.at(-1).project_id='',
    (d,s)=>s.scope_total=38,(d,s)=>s.counts.registered--,(d,s)=>s.counts.ineligible++,
    (d,s)=>s.eligible_project_ids.pop(),(d,s)=>s.eligible_project_ids[1]=s.eligible_project_ids[0],
    (d,s)=>s.eligible_project_ids[0]='missing',(d,s)=>d.projects[0].declared.connection_read_allowed=false,
    (d,s)=>d.projects[0].freshness.local_presence='removed_local',(d,s)=>s.excluded[0].project_id=s.eligible_project_ids[0],
    (d,s)=>s.discovery.unregistered_count=0,(d,s)=>delete s.core_field_gaps,(d,s)=>s.failure_codes={READ_FAILED:-1},
    (d,s)=>s.last_request={request_id:'old',status:'FINISHED',completed:39,remaining:0,failed:40,authority_drift:false},
  ];
  for(const change of changes){
    const {directory,status}=syncFixture();change(directory,status);
    assert.throws(()=>validateSyncStatus(directory,status),{code:'SYNC_STATUS_INCONSISTENT'});
  }
});

test('unavailable sync endpoint propagates its real error instead of deriving totals',async()=>{
  const {directory}=syncFixture();
  const error=Object.assign(new Error('scope missing'),{code:'SYNC_SCOPE_UNAVAILABLE'});
  await assert.rejects(loadSyncStatus(async()=>{throw error;},directory),problem=>problem===error);
  assert.throws(()=>validateSyncStatus(directory,undefined),{code:'SYNC_STATUS_INCONSISTENT'});
});

test('historical full-scope batch and failure details remain distinct from present counts',()=>{
  const {directory,status}=syncFixture();
  status.last_request={request_id:'historical-full-scope',status:'FINISHED',completed:39,remaining:0,failed:2,authority_drift:true};
  status.failure_codes={SOURCE_INVALID:2};status.counts.failed=2;status.counts.partial-=2;
  const result=validateSyncStatus(directory,status);
  assert.equal(result.last_request.authority_drift,true);assert.equal(result.last_request.failed,2);
  assert.equal(result.counts.complete,0);assert.equal(result.counts.failed,2);
  assert.equal(result.failure_codes.SOURCE_INVALID,2);
});


test('sync scope remains exact after a directory traversal exceeding 100 pages',async()=>{
  const {directory,status}=syncFixture(10001,9997);
  let calls=0;
  const traversed=await loadProjectDirectory(async url=>{
    calls++;const offset=Number(new URL(url,'http://fixture').searchParams.get('offset'));
    return {head:directory.head,total:directory.total,limit:100,offset,projects:directory.projects.slice(offset,offset+100)};
  });
  assert.equal(calls,101);
  assert.equal(validateSyncStatus(traversed,status).scope_total,9997);
});

test('alternative core facts and unavailable observations retain distinct readable gap names',()=>{
  const text=coreGapSummary({core_field_gaps:{'current_work.phase|current_work.objective':17,
    'current_work.next_action|blockers':18,'source.current_observation':1,
    'declared.connection_read_allowed':2,'current_work.status':19}});
  for(const label of ['项目阶段或目标 17 项','下一步或阻塞说明 18 项','当前来源观察 1 项','来源读取资格 2 项','工作状态 19 项'])assert.ok(text.includes(label));
  assert.equal(text.includes('其他核心字段'),false);
});

test('per-project quality report preserves the exact baseline and category counts',()=>{
  const {directory,status}=syncFixture();
  status.projects=status.eligible_project_ids.map(project_id=>({project_id,category:'partial',eligible:true,
    core_missing:['progress.completed'],sources:[{id:'state',sha256:'a'.repeat(64)}]}));
  status.reconciliation={scope_sha256:'b'.repeat(64),registry_sha256:'c'.repeat(64),
    baseline_project_ids:[...status.eligible_project_ids]};
  assert.equal(validateSyncStatus(directory,status).projects.length,39);
  for(const mutate of [s=>s.projects.pop(),s=>s.projects[0].project_id=s.projects[1].project_id,
    s=>s.projects[0].category='complete',s=>s.projects[0].eligible=false,
    s=>s.projects[0].sources[0].sha256='invalid',s=>s.reconciliation.scope_sha256='invalid',
    s=>s.reconciliation.baseline_project_ids[0]='outside']){
    const changed=structuredClone(status);mutate(changed);
    assert.throws(()=>validateSyncStatus(directory,changed),{code:'SYNC_STATUS_INCONSISTENT'});
  }
});

test('schedule and recovery text retains failure and unverified real-trigger boundaries',()=>{
  assert.ok(scheduleSummary({available:false,reason:'query_failed'}).includes('无法确认'));
  const text=scheduleSummary({available:true,status:'active',timer:{UnitFileState:'disabled',
    LastTriggerUSec:'',NextElapseUSecRealtime:'tomorrow'},service:{Result:'exit-code',ExecMainStatus:2}});
  for(const value of ['当前运行','disabled','退出码 2','尚未验收','计划时间不代表已经执行'])assert.ok(text.includes(value));
  const request=requestSummary({request_id:'same-id',started_at:'2026-10-14T00:00:00Z',finished_at:null,
    completed:1,failed:1,remaining_project_ids:['b'],authority_drift:true});
  for(const value of ['same-id','尚未结束','失败 1','剩余 1','原请求不能直接续接'])assert.ok(request.includes(value));
});

test('invalid success-with-errors DTO and errors without a latest observation remain conservative unknown',()=>{
  for(const latestPresent of [true,false]){
    const p=authoritative();
    if(!latestPresent)p.operational.latest_attempt=null;
    p.errors=[{code:'INVALID_RECORD',message:'Successful source results cannot carry errors'}];
    const before=JSON.stringify(p);
    assert.equal(currentSourceAvailable(p),false);
    assert.equal(Object.values(facts(p)).every(f=>!f.known),true);
    assert.equal(summaryFields(p)['项目阶段'],'未知');
    assert.equal(projectSummary(p).quality,'当前来源读取失败');
    assert.equal(freshness(p),'最新读取失败');
    assert.equal(attention(p),true);
    assert.equal(JSON.stringify(p),before);
  }
});

test('one current gate rejects drift, stale, explicit failure and denied or removed authority',()=>{
  for(const change of [p=>p.freshness.authority_drift=true,p=>p.freshness.state='stale',
    p=>p.operational.latest_attempt={success:false},p=>p.declared.connection_read_allowed=false,
    p=>p.freshness.local_presence='removed_local',p=>p.business.source_record.success=false]){
    const p=authoritative();change(p);
    assert.equal(currentSourceAvailable(p),false);
    assert.equal(Object.values(facts(p)).every(f=>!f.known),true);
    assert.equal(summaryFields(p)['项目阶段'],'未知');
  }
});

function managedPage(total,offset,available=true){
  const data=directoryPage(total,offset);
  for(const project of data.projects)project.management={available,revision:available?3:null,
    config_hash:available?'a'.repeat(64):null,source_sha256:available?'b'.repeat(64):null};
  return data;
}

test('directory with one management snapshot exhausts pages without confusing per-project overlays',async()=>{
  for(const available of [false,true]){
    let calls=0;
    const result=await loadProjectDirectory(async()=>{
      const data=managedPage(243,calls++*100,available);
      for(const project of data.projects)project.management.overlay={priority:project.project_id};
      return data;
    });
    assert.equal(result.projects.length,243);assert.equal(calls,3);
  }
});

test('management-only writes between pages or within a page refuse mixed complete publication',async()=>{
  for(const change of [p=>p.management.revision++,p=>p.management.config_hash='c'.repeat(64),
    p=>p.management.source_sha256='d'.repeat(64),p=>p.management={available:false,revision:null,config_hash:null,source_sha256:null},
    p=>delete p.management]){
    let calls=0;
    await assert.rejects(loadProjectDirectory(async()=>{
      const data=managedPage(201,calls++*100);if(calls===2)change(data.projects[0]);return data;
    }),{code:'PROJECT_DIRECTORY_INCONSISTENT'});
    assert.equal(calls,2);
    await assert.rejects(loadProjectDirectory(async()=>{
      const data=managedPage(2,0);change(data.projects[1]);return data;
    }),{code:'PROJECT_DIRECTORY_INCONSISTENT'});
  }
});

test('management availability changes, mixed legacy omissions and malformed snapshots never publish',async()=>{
  for(const change of [p=>delete p.management,p=>p.management=null,
    p=>p.management={available:true,revision:0,config_hash:'a'.repeat(64),source_sha256:'b'.repeat(64)},
    p=>p.management.available='false',p=>p.management.revision=0]){
    await assert.rejects(loadProjectDirectory(async()=>{
      const data=managedPage(2,0,false);change(data.projects[1]);return data;
    }),{code:'PROJECT_DIRECTORY_INCONSISTENT'});
  }
  for(const change of [p=>delete p.management.config_hash,p=>p.management.revision=-1,
    p=>p.management.source_sha256='invalid',p=>p.management.config_hash=null]){
    await assert.rejects(loadProjectDirectory(async()=>{
      const data=managedPage(1,0);change(data.projects[0]);return data;
    }),{code:'PROJECT_DIRECTORY_INCONSISTENT'});
  }
  await assert.rejects(loadProjectDirectory(async()=>{
    const data=managedPage(2,0);delete data.projects[0].management;return data;
  }),{code:'PROJECT_DIRECTORY_INCONSISTENT'});
});
