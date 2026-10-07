import assert from 'node:assert/strict';
import test from 'node:test';

import {attention, freshness, refreshable, projectFacts, projectTasks} from '../src/hub/web/connection_view.mjs';

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
