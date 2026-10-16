// Synthetic ordinary text only. Root supplies a disposable, private loopback CDP.
import assert from 'node:assert/strict';
import path from 'node:path';
import {runBrowserJourney} from './pch_browser_runtime.mjs';

await runBrowserJourney('o4',async({page,context,evidence,evidenceRoot,origin:initialOrigin,waitMessage,loginOwner})=>{
  let origin=initialOrigin;
  const A='fixture-project-a',B='fixture-project-b';
  await waitMessage(m=>m.ordinary_ready===true,{action:'ordinary-sample'});
  const panel=()=>page.getByRole('region',{name:'普通任务',exact:true});
  const field=label=>panel().getByLabel(label,{exact:true});
  const button=name=>panel().getByRole('button',{name,exact:true});
  async function open(id){
    const url=origin+'/#projects/'+id+'/tasks';
    if(page.url()===url)await page.reload();else await page.goto(url);
    await panel().waitFor();
  }
  async function ordinaryContext(id,parent=null){
    const response=await context.request.get(origin+'/api/ordinary-context?project_id='+id+(parent?'&parent_task_id='+parent:''));
    assert.equal(response.status(),200);return (await response.json()).data;
  }
  async function task(id){
    const response=await context.request.get(origin+'/api/tasks/'+id);
    assert.equal(response.status(),200);return (await response.json()).data.task;
  }
  async function save(expected=200){
    const [response]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/ordinary-drafts'&&r.request().method()==='POST'),button('保存任务草稿').click()]);
    assert.equal(response.status(),expected);return (await response.json()).data;
  }
  async function send(expected=200){
    const [response]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/tasks'&&r.request().method()==='POST'),button('发送普通任务').click()]);
    assert.equal(response.status(),expected);
    return {body:await response.json(),spec:response.request().postDataJSON()};
  }
  async function worker(id,project=A){
    const reply=await waitMessage(m=>'ran'in m,{action:'run',project_id:project,task_id:id});assert.equal(reply.ran,true);
    const actual=await task(id);assert.equal(actual.status,'checks_complete');
    assert.equal(actual.kind,'ordinary_project');
    assert.equal(actual.classification,'synthetic_fixture');
    assert.match(actual.result.response,/Synthetic read-only response/);
    assert.ok(actual.result.checks.length);return actual;
  }
  await open(A);
  // Owner gate is a real HTTP rejection before the real UI handshake.
  const guest=await context.request.get(origin+'/api/ordinary-context?project_id='+A);
  assert.equal(guest.status(),401);
  evidence.states.initialOwnerGate={status:guest.status(),code:(await guest.json()).error.code};
  await loginOwner();await open(A);
  assert.equal((await ordinaryContext(A)).ready,true);
  assert.equal(await field('任务目标').inputValue(),'');
  await field('任务目标').fill('Synthetic A read-only review');
  await field('任务范围').fill('Read AGENTS.md and README.md; return text only.');
  await open(B);assert.equal(await field('任务目标').inputValue(),'');
  await open(A);assert.equal(await field('任务目标').inputValue(),'Synthetic A read-only review');
  await page.reload();await panel().waitFor();assert.equal(await field('任务目标').inputValue(),'Synthetic A read-only review');
  evidence.states.unsavedDraft={route:true,reload:true,noSend:true};await save();
  const aDraft=await ordinaryContext(A);assert.equal(evidence.tasks.length,0);
  await open(B);assert.equal(await field('任务目标').inputValue(),'');
  await field('任务目标').fill('Synthetic B independent review');
  await field('任务范围').fill('Read declared inputs only.');await save();
  await open(A);assert.equal(await field('任务目标').inputValue(),'Synthetic A read-only review');
  await page.reload();await panel().waitFor();assert.equal(await field('任务目标').inputValue(),'Synthetic A read-only review');
  const restart=await waitMessage(m=>m.restarted===true,{action:'restart'});origin=restart.origin;
  await open(A);await loginOwner();await open(A);
  assert.equal(await field('任务目标').inputValue(),'Synthetic A read-only review');
  await open(B);assert.equal(await field('任务目标').inputValue(),'Synthetic B independent review');await open(A);
  assert.equal(evidence.tasks.length,0);
  evidence.states.drafts={isolated:true,refresh:true,serverRestart:true,saveDoesNotDispatch:true};

  // Lose only the response after the real server admission. App must reconcile the
  // original request ID, never manufacture a replacement request.
  let accepted;
  await page.route('**/api/tasks',async route=>{
    if(route.request().method()!=='POST'){await route.continue();return;}
    accepted=route.request().postDataJSON();
    const response=await route.fetch();assert.equal(response.status(),200);
    await route.abort('failed');
  },{times:1});
  const originalRequest=page.waitForRequest(r=>new URL(r.url()).pathname==='/api/tasks'&&r.method()==='POST');
  await button('发送普通任务').click();
  accepted=(await originalRequest).postDataJSON();
  await page.waitForFunction(()=>!document.querySelector('#main')?.hasAttribute('aria-busy'));
  assert.ok(accepted?.request_id);assert.equal(evidence.tasks.length,1);
  const admitted=await task(accepted.request_id);assert.equal(admitted.status,'queued');
  const completed=await worker(accepted.request_id);
  await page.goto(origin+'/#tasks/'+accepted.request_id);
  await page.getByText(/Synthetic read-only response/).first().waitFor();
  await page.screenshot({path:path.join(evidenceRoot,'o4-response.png')});
  evidence.states.unknownReconciled={taskId:accepted.request_id,singleDispatch:true,actualWorker:true,textResult:true};

  // An idle continuation uses the parent thread; busy feedback is queued without
  // starting a second worker turn while its parent is still running.
  await page.goto(origin+'/#projects/'+A+'/tasks?parent_task_id='+accepted.request_id);await panel().waitFor();
  await field('任务目标').fill('Synthetic idle follow-up');await field('任务范围').fill('Continue read-only.');
  const follow=await send();assert.equal(follow.spec.mode,'idle_continue');assert.equal(follow.spec.parent_task_id,accepted.request_id);
  const followed=await worker(follow.spec.request_id);assert.equal(followed.thread_id,completed.thread_id);
  evidence.states.idleContinuation={parent:accepted.request_id,taskId:follow.spec.request_id};

  await open(A);await button('新建另一条任务草稿').click();
  await field('任务目标').fill('Synthetic busy parent');await field('任务范围').fill('Read declared inputs only.');
  const busyParent=await send();
  await waitMessage(m=>m.ordinary_scenario==='delay',{action:'ordinary-scenario',project_id:A,scenario:'delay'});
  await waitMessage(m=>m.ran===true&&m.background===true,{action:'run',project_id:A,task_id:busyParent.spec.request_id,background:true});
  let running;
  for(let i=0;i<30;i++){
    running=await task(busyParent.spec.request_id);
    if(running.status==='running'&&running.turn_id)break;
    await new Promise(resolve=>setTimeout(resolve,50));
  }
  assert.equal(running.status,'running');assert.ok(running.turn_id);
  await page.goto(origin+'/#projects/'+A+'/tasks?parent_task_id='+busyParent.spec.request_id);await panel().waitFor();
  await field('任务目标').fill('Synthetic queued feedback');await field('任务范围').fill('Apply only after parent is idle.');
  const queued=await send();assert.equal(queued.spec.mode,'busy_feedback');
  const waiting=await task(queued.spec.request_id);assert.equal(waiting.status,'queued');
  assert.equal(waiting.feedback.applied_to_active_turn,false);
  await waitMessage(m=>m.ordinary_released===true,{action:'ordinary-release'});
  assert.equal((await task(busyParent.spec.request_id)).status,'checks_complete');
  await waitMessage(m=>m.ordinary_scenario==='normal',{action:'ordinary-scenario',project_id:A,scenario:'normal'});
  await worker(queued.spec.request_id);
  evidence.states.busyFeedback={queued:true,notAppliedToActiveTurn:true,ranAfterIdle:true};

  // Refreshing live inputs changes admission version; the existing draft remains
  // visible and requires an explicit user rebind before it can be sent.
  await open(B);const before=await ordinaryContext(B);
  await waitMessage(m=>m.ordinary_input_changed===true,{action:'ordinary-input',project_id:B});
  const stale=await send(409);assert.equal(stale.spec.input_version,before.input_version);
  assert.equal(await field('任务目标').inputValue(),'Synthetic B independent review');
  assert.equal(await button('发送普通任务').isDisabled(),true);
  await waitMessage(m=>m.ordinary_regranted===true,{action:'ordinary-regrant',project_id:B});
  await button('重新读取任务输入').click();
  assert.notEqual((await ordinaryContext(B)).input_version,before.input_version);
  assert.equal(await field('任务目标').inputValue(),'Synthetic B independent review');
  evidence.states.inputConflict={status:409,draftRetained:true,explicitRebind:true};

  await waitMessage(m=>m.runtime_updated===true,{action:'runtime',provider:false});
  const unavailable=await send(503);
  assert.equal(unavailable.body.error.code,'TASK_PROVIDER_UNAVAILABLE');
  assert.equal(await field('任务目标').inputValue(),'Synthetic B independent review');
  await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/ordinary-context'),button('核对任务条件').click()]);
  await panel().getByText('普通任务暂不可发送；可用时仍需核对授权范围。',{exact:true}).waitFor();
  assert.equal(await button('发送普通任务').isDisabled(),true,'provider unavailable disables submit after the fresh context is applied');
  assert.match(await panel().innerText(),/PROVIDER|provider|提供|运行|不可用/);
  await waitMessage(m=>m.runtime_updated===true,{action:'runtime',provider:true});
  await waitMessage(m=>m.ordinary_revoked===true,{action:'ordinary-revoke',project_id:B});
  await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/ordinary-context'),button('核对任务条件').click()]);
  await panel().getByText(/条件代码：GRANT_EXPIRED/).waitFor();
  assert.equal(await button('发送普通任务').isDisabled(),true,'revoked grant disables submit after the fresh context is applied');
  assert.match(await panel().innerText(),/GRANT|授权|过期/);
  evidence.states.runtimeAndRevocation={runtimeUnavailableDisabled:true,revocationDisabled:true};
  await page.screenshot({path:path.join(evidenceRoot,'o4-revocation.png')});
  const csrfDenied=await context.request.post(origin+'/api/ordinary-drafts',{
    data:{},headers:{Origin:origin,'X-Hub-CSRF':'synthetic-invalid-csrf'}});
  assert.equal(csrfDenied.status(),403);assert.equal((await csrfDenied.json()).error.code,'CSRF_REJECTED');
  await page.getByRole('button',{name:'断开所有者',exact:true}).click();
  await page.getByRole('button',{name:'连接所有者',exact:true}).waitFor();
  const denied=await context.request.get(origin+'/api/ordinary-context?project_id='+B);
  assert.equal(denied.status(),401);assert.equal((await denied.json()).error.code,'OWNER_AUTH_REQUIRED');
  await page.getByRole('button',{name:'连接所有者',exact:true}).click();
  await page.getByLabel('所有者凭据',{exact:true}).fill('synthetic-invalid-owner-proof-000000000000000000000');
  const [invalidLogin]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/owner/login'),
    page.getByRole('button',{name:'认证',exact:true}).click()]);
  assert.equal(invalidLogin.status(),401);assert.equal((await invalidLogin.json()).error.code,'OWNER_AUTH_FAILED');
  await page.getByRole('dialog',{name:'连接所有者',exact:true}).getByText('身份未通过，请重新输入。',{exact:true}).waitFor();
  evidence.states.authenticationReasons={ownerRequired401:true,invalidSyntheticProof401:true,csrf403:true,provider503:true};
});
