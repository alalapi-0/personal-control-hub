import assert from "node:assert/strict";
import test from "node:test";

const { __test } = await import("../src/hub/web/designs.js");
const { api, validRefreshCommand, connectionState, ownerLogin, ownerLogout } = await import("../src/hub/web/common.js");
const { metricText, metricState } = await import('../src/hub/web/host.js');
const {materialUrl,reviewLink}=await import('../src/hub/web/materials.js');
const {sessionState,sourceState}=await import('../src/hub/web/sessions.js');
const { normalizedPoint, rectangle, sameBinding, validPending } = await import('../src/hub/web/workbench.js');
const {messageGate,validDescriptor}=await import('../src/hub/web/preview_bridge.js');
const {validLivePreview}=await import('../src/hub/web/live_preview.js');
const {controlState,feedbackState,recoveryState}=await import('../src/hub/web/tasks.js');

test('real preview permits only a fixed loopback readonly fixture descriptor',()=>{
  const d={source:'real_code_readonly_fixture',project_id:'computer-study-plan',execution_allowed:false,
    business_data:'fixture',source_mapping:null,code_head:'a'.repeat(40),manifest_hash:'b'.repeat(64),
    manifest_observed_at:'2026-10-07T00:00:00Z',available:true,origin:'http://127.0.0.1:12345',frame_url:'http://127.0.0.1:12345/progress.html'};
  assert.equal(validLivePreview(d),true);
  for(const origin of ['https://example.com','http://localhost:12345','http://user@127.0.0.1:12345','http://127.0.0.1:12345/other'])
    assert.equal(validLivePreview({...d,origin,frame_url:origin+'/progress.html'}),false);
  for(const patch of [{frame_url:d.origin+'/api/tasks'},{execution_allowed:true},{business_data:'production'},{project_id:'another-project'},{source_mapping:'guessed'}])
    assert.equal(validLivePreview({...d,...patch}),false);
  assert.equal(validLivePreview({...d,available:false,origin:null,frame_url:null}),true);
  assert.equal(validLivePreview({...d,manifest_observed_at:undefined,captured_at:'2026-10-07T00:00:00Z'}),false);
});

test('recovery distinguishes storage failure and unknown execution without retry authority',()=>{
  for(const reason_code of ['LEDGER_MISSING','REGISTRATION_MISSING','REGISTRATION_INCOMPLETE','LEDGER_IDENTITY_CHANGED','ROOT_UNAVAILABLE','ROOT_IDENTITY_CHANGED','PARENT_IDENTITY_CHANGED','MOUNT_IDENTITY_CHANGED']){
    const state=recoveryState({}, {state:'blocked',reason_code});assert.equal(state.recordUnavailable,true);assert.equal(state.canRetry,false);
    assert.notEqual(state.reason,'存储条件尚未确认');
  }
  assert.equal(recoveryState({status:'running'},{state:'ready'}).needsReconcile,false);
  for(const status of ['lost','requires_reconcile'])assert.equal(recoveryState({status},{state:'ready'}).needsReconcile,true);
  assert.equal(recoveryState({status:'running',worker_state:'lease_expired'},{state:'ready'}).needsReconcile,true);
  const low=recoveryState({status:'queued'},{state:'blocked',reason_code:'LOW_SPACE'});
  assert.equal(low.blocked,true);assert.equal(low.reason,'可用空间不足');assert.equal(low.canRetry,false);
  assert.equal(low.recordUnavailable,false);
  assert.equal(recoveryState({}, {state:'blocked',reason_code:'/private/untrusted-path'}).reason,'存储条件尚未确认');
});

test('owned request controls distinguish pending decline expiry binding and authority',()=>{
  const task={status:'waiting_approval',thread_id:'thread',turn_id:'turn'};
  const c={kind:'approval',status:'pending',version:1,expires_at:20,thread_id:'thread',turn_id:'turn',allowed_decisions:['decline']};
  assert.equal(controlState(c,task,true,10).canDecline,true);
  assert.equal(controlState(c,task,false,10).canDecline,false);
  assert.equal(controlState(c,task,true,20).label,'已过期');
  for(const change of [{version:2},{turn_id:'wrong'},{thread_id:'wrong'},{status:'declined'},{allowed_decisions:[]}])assert.equal(controlState({...c,...change},task,true,10).canDecline,false);
  assert.equal(controlState({...c,kind:'input'},task,true,10).label,'等待补充输入');
  assert.equal(controlState(c,{...task,status:'requires_reconcile'},true,10).canDecline,false);
});
test('queued feedback cannot be labeled active steering or pending after cancellation',()=>{
  assert.equal(feedbackState('queued').label,'意见已排队');
  assert.equal(feedbackState('cancelled').label,'排队意见已取消');
  assert.equal(feedbackState('running').label,'正在处理排队意见');
  assert.equal(feedbackState('requires_reconcile').label,'排队意见需要核对');
});

test('cross origin hint gate pins window nonce exact fields and monotonic load sequence',()=>{
  const source={},other={},d={available:true,classification:'synthetic_fixture',bridge_id:'owned-fixture',bridge_version:1,
    manifest_hash:'a'.repeat(64),binding_hash:'b'.repeat(64),origin:'http://localhost:34567',frame_url:'http://localhost:34567/fixture',
    nonce:'c'.repeat(64),load_id:'load-'+ 'd'.repeat(32),ttl_seconds:300,elements:[{id:'sample-card',role:'button'}]};
  assert.equal(validDescriptor(d),true);
  for(const patch of [{origin:'https://evil.invalid'},{frame_url:'data:text/html,foo'},{nonce:'bad'},{permission:'execute'},{elements:[{id:'private-field',role:'textbox'}]}])assert.equal(validDescriptor({...d,...patch}),false);
  const ready={schema:1,type:'ready',nonce:d.nonce,bridge_version:1,manifest_hash:d.manifest_hash,binding_hash:d.binding_hash,load_id:d.load_id,sequence:0,selection:null};
  const selection={...ready,type:'selection',sequence:1,selection:{id:'sample-card',role:'button',rect:{x1:.1,y1:.2,x2:.5,y2:.8}}};
  const gate=messageGate(d,source),event=data=>({origin:d.origin,source,data});
  assert.equal(gate.accept(event(selection)),null);
  assert.equal(gate.accept({...event(ready),origin:'null'}),null);
  assert.equal(gate.accept({...event(ready),source:other}),null);
  for(const patch of [{nonce:'e'.repeat(64)},{schema:2},{bridge_version:2},{manifest_hash:'e'.repeat(64)},{binding_hash:'e'.repeat(64)},{load_id:'load-'+ 'e'.repeat(32)},{extra:'script'}])assert.equal(gate.accept(event({...ready,...patch})),null);
  assert.deepEqual(gate.accept(event(ready)),{type:'ready'});assert.equal(gate.accept(event(ready)),null);
  for(const s of [{...selection,sequence:2},{...selection,selection:{...selection.selection,id:'private-input'}},
    {...selection,selection:{...selection.selection,text:'private'}},{...selection,selection:{...selection.selection,rect:{x1:0,x2:NaN,y1:0,y2:1}}},
    {...selection,selection:{...selection.selection,rect:{x1:0,x2:0,y1:0,y2:1}}}])assert.equal(gate.accept(event(s)),null);
  const accepted=gate.accept(event(selection));assert.equal(accepted.hint.source_mapping,'unknown');assert.equal(accepted.hint.element_id,'sample-card');
  assert.equal(gate.accept(event(selection)),null);gate.close();assert.equal(gate.accept(event({...selection,sequence:2})),null);
});

test('session discovery separates loaded status from continuation authority and empty from failure',()=>{
  assert.equal(sessionState({state:'notLoaded'}),'未加载 · 不能据此判断空闲');
  assert.equal(sessionState({state:'idle_unverified'}),'报告空闲 · 继续权限待核对');
  assert.equal(sessionState({state:'busy'}),'正在执行 · 未取得运行控制权');
  for(const state of ['supported_empty','error','unsupported','unprobed','stale'])assert.notEqual(sourceState({state}),'来源能力未知');
  assert.notEqual(sourceState({state:'supported_empty'}),sourceState({state:'error'}));
});

test('material navigation accepts only registered identity and original safe review reference',()=>{
  assert.equal(materialUrl({id:'material-'+'a'.repeat(32),version:'b'.repeat(64)}),'/api/materials/material-'+ 'a'.repeat(32)+'?version='+ 'b'.repeat(64));
  for(const id of ['/etc/passwd','https://evil.invalid','material-'+ 'g'.repeat(32)])assert.equal(materialUrl({id,version:'b'.repeat(64)}),null);
  assert.equal(reviewLink('https://www.figma.com/design/original?node-id=1'), 'https://www.figma.com/design/original?node-id=1');
  for(const link of ['javascript:alert(1)','https://evil.invalid','https://www.figma.com.evil.invalid/design/a','https://user:secret@www.figma.com/design/a','figma://offline-file/node'])assert.equal(reviewLink(link),null);
});

test('region coordinates follow image content under scroll, zoom and different display size',()=>{
  assert.deepEqual(normalizedPoint({left:20,top:-100,width:300,height:400},50,100),{x:.1,y:.5});
  assert.deepEqual(normalizedPoint({left:40,top:-200,width:600,height:800},100,200),{x:.1,y:.5});
  assert.equal(normalizedPoint({left:20,top:0,width:300,height:400},19,100),null);
  assert.equal(normalizedPoint({left:0,top:0,width:0,height:400},0,100),null);
  assert.equal(rectangle({x:0,y:0},{x:0,y:.2}),null);
  assert.equal(rectangle({x:0,y:0},{x:Infinity,y:.2}),null);
  assert.deepEqual(rectangle({x:.5,y:.8},{x:.1,y:.2}),{x1:.1,y1:.2,x2:.5,y2:.8});
  assert.equal(sameBinding({version:1,id:'a'},{id:'a',version:1}),true);
  assert.equal(sameBinding({version:1,id:'a'},{version:2,id:'a'}),false);
  assert.equal(validPending({request_id:'../../other'}),null);
});

test('host values distinguish observed zero, unavailable, no swap and stale sampling',()=>{
  assert.equal(metricText('cpu_usage',{value:0}),'0%');
  assert.equal(metricText('cpu_usage',{value:null}),'暂不可用');
  assert.equal(metricText('cpu_usage',{value:null,reason:'FIRST_SAMPLE'}),'等待第二次采样');
  assert.equal(metricText('swap',{value:{total:0,free:0}}),'未配置交换空间');
  assert.equal(metricText('gpu_usage',{value:[{name:'Test GPU',value:0}]}),'Test GPU：0%');
  assert.equal(metricText('gpu_decode',{value:null,reason:'UNSUPPORTED_SENSOR'}),'暂不可用');
  assert.equal(metricText('io_hub',{value:{read:1048576,write:0}}),'读取 1.00 MiB/s · 写入 0.00 MiB/s');
  assert.equal(metricText('cpu_temperature',{value:[{name:'k10temp',label:'Tctl',value:41.5}]}),'k10temp Tctl：41.5 °C');
  const metric={state:'fresh',age_seconds:2,max_age_seconds:8};
  assert.equal(metricState(metric,5),'fresh');
  assert.equal(metricState(metric,7),'stale');
  assert.equal(metricState({...metric,state:'stale'},0),'stale');
  assert.equal(metricState(undefined),'unavailable');
  assert.equal(metricState({...metric,age_seconds:undefined}),'stale');
});

test('owner expiry, configuration absence, unavailable version and offline are distinct',()=>{
  assert.equal(connectionState({owner_authenticated:true}).kind,'owner');
  assert.equal(connectionState({owner_authenticated:false},null,true).kind,'expired');
  assert.match(connectionState({owner_configured:false}).label,/未配置/);
  assert.match(connectionState({owner_configured:true}).label,/未认证/);
  assert.equal(connectionState(null,{code:'NETWORK_UNAVAILABLE'}).kind,'offline');
  assert.equal(connectionState(null,{code:'NOT_FOUND'}).kind,'unavailable');
});

test("refresh recovery preserves the exact valid command and ignores corrupt browser data", () => {
  const command = { request_id: "refresh-1", project_ids: ["project-1"], expected_head: { sequence: 0, hash: "0".repeat(64) } };
  assert.equal(validRefreshCommand(command), command);
  for (const invalid of [null, {}, { ...command, project_ids: [] }, { ...command, project_ids: ["project-1", "project-1"] },
    { ...command, expected_head: { sequence: -1, hash: "0".repeat(64) } }, { ...command, expected_head: { sequence: 0 } }]) {
    assert.equal(validRefreshCommand(invalid), null);
  }
});

test("scope identity ignores member and page presentation order", () => {
  const first = { family_id: null, members: [
    { project_id: "b", pages: ["detail", "home"] },
    { project_id: "a", pages: ["review"] },
  ] };
  const second = { family_id: null, members: [
    { project_id: "a", pages: ["review"] },
    { project_id: "b", pages: ["home", "detail"] },
  ] };
  assert.equal(__test.scopeKey(first), __test.scopeKey(second));
});

test("effective decision is selected by exact scope", () => {
  const candidate = { id: "candidate-b", revision: 1, content_hash: "b".repeat(64),
    scope: { family_id: null, members: [{ project_id: "hub", pages: ["designs"] }] } };
  const event = { event: { action: "select", scope: candidate.scope,
    candidate: { id: "candidate-a", revision: 1, content_hash: "a".repeat(64) } }, superseded: false };
  assert.equal(__test.effectiveFor({ effective: { opaque: event } }, candidate), event);
  assert.equal(__test.exactEffectiveFor({ effective: { opaque: event } }, candidate), null);
  const exact = { ...event, event: { ...event.event, candidate: {
    id: candidate.id, revision: candidate.revision, content_hash: candidate.content_hash,
  } } };
  assert.equal(__test.exactEffectiveFor({ effective: { opaque: exact } }, candidate), exact);
  assert.equal(__test.effectiveFor({ effective: {} }, candidate), null);
});

test("candidate lists keep only the newest revision for each identity", () => {
  const values = [
    { id: "a", revision: 1 }, { id: "b", revision: 2 },
    { id: "a", revision: 3 }, { id: "b", revision: 1 },
  ];
  assert.deepEqual(__test.latestCandidates(values), [values[2], values[1]]);
});

test("preview roles come from imported artifact identities", () => {
  assert.equal(__test.artifactRole("hub-p5-overview-mobile"), "overview-mobile");
  assert.equal(__test.artifactRole("hub-p5-states-mobile"), "states-mobile");
  assert.equal(__test.artifactRole("hub-p5-compare-desktop"), "compare-desktop");
  assert.equal(__test.artifactRole("unclassified"), "default");
});

test("stale candidate and baseline bindings are explicit", () => {
  const hash = "a".repeat(64);
  const candidate = { kind: "candidate", id: "choice", revision: 1, content_hash: hash,
    baseline_bindings: [{ project_id: "hub", baseline_id: "before", baseline_revision: 1,
      baseline_hash: hash, pages: ["designs"] }] };
  const baseline = { kind: "baseline", id: "before", revision: 1, content_hash: hash,
    project_id: "hub", scope: { pages: ["designs"] } };
  assert.deepEqual(__test.staleReasons([baseline, candidate], candidate), []);
  const replacement = { ...baseline, revision: 2, content_hash: "b".repeat(64) };
  assert.deepEqual(__test.staleReasons([baseline, candidate, replacement], candidate),
    ["页面「designs」的原始版本已变化"]);
});

test("external artifact links allow only HTTPS Figma", () => {
  const artifact = (value) => ({ delivery: { kind: "figma_link", value } });
  assert.equal(__test.trustedFigmaUrl(artifact("https://www.figma.com/design/abc")), "https://www.figma.com/design/abc");
  assert.equal(__test.trustedFigmaUrl(artifact("http://www.figma.com/design/abc")), null);
  assert.equal(__test.trustedFigmaUrl(artifact("https://figma.example/design/abc")), null);
  assert.equal(__test.trustedFigmaUrl(artifact("javascript:alert(1)")), null);
});

test("stored retries remain exactly bound to candidate and API route", () => {
  const candidate = { id: "hub-p5", revision: 2, content_hash: "a".repeat(64) };
  const pending = {
    kind: "decision", path: "/api/designs/decisions", candidate: { ...candidate },
    command: { request_id: "ui-request-1", expected_revision: 7, candidate: { ...candidate } },
  };
  assert.equal(__test.validPending(pending, candidate), pending);
  assert.equal(__test.validPending(pending), pending);
  assert.equal(__test.validPending({ ...pending, path: "https://evil.invalid" }, candidate), null);
  assert.equal(__test.validPending({ ...pending, kind: "export" }, candidate), null);
  assert.equal(__test.validPending({ ...pending, command: { ...pending.command, expected_revision: 8 } },
    { ...candidate, revision: 3 }), null);
  assert.equal(__test.validPending(pending, { ...candidate, revision: 3 }), null);
});

test("mutation network failure is normalized as an unknown outcome", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("offline"); };
  try {
    await assert.rejects(api("/api/designs/decisions", {}),
      (error) => error.code === "NETWORK_UNAVAILABLE" && error.outcome === "UNKNOWN");
  } finally { globalThis.fetch = originalFetch; }
});

test("unparseable mutation response remains an unknown outcome", async () => {
  const originalFetch = globalThis.fetch;
  const responses = [
    { ok: true, status: 200, json: async () => ({ ok: true, data: { csrf_token: "runtime-only" } }) },
    { ok: true, status: 200, json: async () => null },
  ];
  globalThis.fetch = async () => responses.shift();
  try {
    await assert.rejects(api("/api/designs/decisions", {}),
      (error) => error.code === "INVALID_RESPONSE" && error.outcome === "UNKNOWN");
  } finally { globalThis.fetch = originalFetch; }
});

test('owner login adopts its rotated CSRF and logout discards it without browser persistence',async()=>{
  const originalFetch=globalThis.fetch, originalStorage=globalThis.localStorage;
  const proof=crypto.randomUUID()+crypto.randomUUID(), requests=[];
  globalThis.localStorage={setItem(){throw Error('credential must not be persisted');},getItem(){throw Error('credential must not be read');}};
  globalThis.fetch=async(path,options)=>{
    requests.push({path,options});
    return {ok:true,status:200,json:async()=>({ok:true,data:path==='/api/owner/login'?{csrf_token:'rotated-fixture-csrf'}:
      path==='/api/session'?{csrf_token:'guest-fixture-csrf'}:{}})};
  };
  try {
    await ownerLogin(proof);
    await api('/api/refresh',{request_id:'isolated-fixture'});
    assert.equal(JSON.parse(requests.find(r=>r.path==='/api/owner/login').options.body).token,proof);
    assert.equal(requests.find(r=>r.path==='/api/refresh').options.headers['X-Hub-CSRF'],'rotated-fixture-csrf');
    await ownerLogout();
    await api('/api/identity');
    assert.equal(requests.at(-2).path,'/api/session');
    assert.ok(requests.every(r=>!r.path.includes(proof)));
  } finally {globalThis.fetch=originalFetch;globalThis.localStorage=originalStorage;}
});
