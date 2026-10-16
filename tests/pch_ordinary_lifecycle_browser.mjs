// Real HTTP/static UI/queue/worker. Provider events and owner are synthetic.
import assert from 'node:assert/strict';
import path from 'node:path';
import {runBrowserJourney} from './pch_browser_runtime.mjs';

await runBrowserJourney('o44',async({page,context,evidence,evidenceRoot,origin:initialOrigin,waitMessage,loginOwner})=>{
  let origin=initialOrigin;
  const A='fixture-project-a',B='fixture-project-b';
  await waitMessage(m=>m.ordinary_ready,{action:'ordinary-sample'});
  await page.goto(origin+'/#projects/'+A+'/tasks');await loginOwner();
  async function get(id){const r=await context.request.get(origin+'/api/tasks/'+id);assert.equal(r.status(),200);return (await r.json()).data;}
  async function until(id,predicate,timeout=12000){
    const end=Date.now()+timeout;
    while(Date.now()<end){const data=await get(id);if(predicate(data))return data;await new Promise(resolve=>setTimeout(resolve,100));}
    assert.fail('Task did not reach required observed state: '+id);
  }
  async function scenario(project_id,value){await waitMessage(m=>m.ordinary_scenario===value,{action:'ordinary-scenario',project_id,scenario:value});}
  async function send(project,objective){
    await page.goto(origin+'/#projects/'+project+'/tasks');
    const panel=page.getByRole('region',{name:'普通任务',exact:true});await panel.waitFor();
    await panel.getByRole('button',{name:'新建另一条任务草稿',exact:true}).click();
    await panel.getByLabel('任务目标',{exact:true}).fill(objective);
    await panel.getByLabel('任务范围',{exact:true}).fill('Read registered synthetic inputs only; return text.');
    const [response]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/tasks'&&r.request().method()==='POST'),
      panel.getByRole('button',{name:'发送普通任务',exact:true}).click()]);
    assert.equal(response.status(),200);return (await response.json()).data.task.id;
  }
  async function open(id){await page.goto(origin+'/#tasks/'+id);await page.getByRole('heading',{name:id,exact:true}).waitFor();}
  // Queue survives the actual HTTP service restart before explicit runtime start.
  const recovered=await send(A,'Synthetic restart recovery');
  assert.equal((await get(recovered)).task.status,'queued');
  const restart=await waitMessage(m=>m.restarted,{action:'restart'});origin=restart.origin;
  await page.goto(origin+'/#tasks/'+recovered);await loginOwner();
  await waitMessage(m=>m.ordinary_runtime_started,{action:'ordinary-runtime-start',request_timeout:8});
  await until(recovered,d=>d.task.status==='checks_complete');
  evidence.states.restartQueueRecovery={sameRequest:recovered,automaticWorker:true};

  await scenario(A,'input');const input=await send(A,'Synthetic input question');
  const waiting=await until(input,d=>d.task.status==='waiting_input');
  assert.equal(waiting.controls[0].questions[0].id,'focus');assert.ok(!('answers'in waiting.controls[0]));
  const wrong=await context.request.post(origin+'/api/tasks/'+input+'/respond',{headers:{'X-Hub-CSRF':'synthetic-invalid'},
    data:{control_id:waiting.controls[0].id,version:1,decision:'answer',answers:{foreign:{answers:['synthetic']}}}});
  // The UI sends the valid CSRF through its shared API. This negative request
  // may be rejected at CSRF before answer validation; backend tests cover both.
  assert.equal(wrong.status(),403);
  await open(input);
  await page.getByLabel('检查关注点：请补充本次只读检查的关注点',{exact:true}).fill('Inspect the registered input version.');
  await page.getByLabel('检查关注点：请补充本次只读检查的关注点',{exact:true}).locator('..').screenshot({path:path.join(evidenceRoot,'o44-input.png')});
  let lost=0;
  await context.route('**/api/tasks/'+input+'/respond',async route=>{
    const response=await route.fetch();assert.equal(response.status(),200);lost++;await route.abort('failed');
  });
  await page.getByRole('button',{name:'提交补充输入',exact:true}).click();
  await until(input,d=>d.task.status==='checks_complete');await context.unroute('**/api/tasks/'+input+'/respond');
  await page.reload();await page.getByText('Synthetic read-only response for fixture-project-a.',{exact:true}).waitFor();
  const answered=(await get(input)).controls[0];assert.equal(answered.status,'answered');assert.ok(!('answers'in answered));assert.equal(lost,1);
  evidence.states.inputAnswerReceiptRecovery={answered:true,singlePost:true,reopened:true};
  await page.screenshot({path:path.join(evidenceRoot,'o44-answer.png')});

  await scenario(A,'delay');const cancelled=await send(A,'Synthetic exact cancellation');
  const running=await until(cancelled,d=>d.task.status==='running'&&d.task.turn_id);
  await scenario(B,'normal');const afterCancel=await send(B,'Synthetic independent parallel task');
  await until(afterCancel,d=>d.task.status==='checks_complete');
  assert.equal((await get(cancelled)).task.status,'running');
  await open(cancelled);await page.getByRole('button',{name:'取消这个任务',exact:true}).click();
  const stopped=await until(cancelled,d=>d.task.status==='cancelled');
  await until(afterCancel,d=>d.task.status==='checks_complete');
  const proof=await waitMessage(m=>'ordinary_control_observations'in m,{action:'ordinary-observations'});
  assert.equal(proof.effects,0);assert.deepEqual(proof.interrupts,[[running.task.thread_id,running.task.turn_id]]);
  assert.equal(proof.ordinary_control_observations.length,1);assert.equal(proof.ordinary_control_observations[0].decision,'answer');
  evidence.states.exactCancellation={target:cancelled,providerStatus:stopped.task.provider_status,otherCompleted:afterCancel,interrupts:1};

  await scenario(A,'approval');const approval=await send(A,'Synthetic denied out-of-grant request');
  const gated=await until(approval,d=>d.task.status==='waiting_approval');
  assert.deepEqual(gated.controls[0].allowed_decisions,['decline']);
  await open(approval);assert.equal(await page.getByRole('button',{name:'仅本次允许',exact:true}).count(),0);
  await page.getByRole('button',{name:'拒绝本次请求',exact:true}).click();
  await until(approval,d=>d.task.status==='requires_reconcile');
  evidence.states.approvalBoundary={denyOnly:true,unknownNotRetried:true};
  await scenario(B,'normal');const independent=await send(B,'Synthetic independent after unknown A');
  await until(independent,d=>d.task.status==='checks_complete');
  assert.equal((await get(approval)).task.status,'requires_reconcile');
  evidence.states.workspaceRecovery={unresolvedAPreserved:true,independentBCompleted:true};

  await scenario(B,'timeout_input');const expired=await send(B,'Synthetic bounded input timeout');
  await until(expired,d=>d.task.status==='waiting_input');await open(expired);
  const timed=await until(expired,d=>d.task.status==='requires_reconcile',12000);
  assert.equal(timed.controls[0].status,'expired');assert.deepEqual(timed.controls[0].allowed_decisions,[]);
  await page.reload();await page.getByText('已过期',{exact:true}).waitFor();
  assert.ok(!(await page.getByText('已过期',{exact:true}).locator('..').innerText()).includes('可拒绝'));
  assert.equal(await page.getByRole('button',{name:'提交补充输入',exact:true}).count(),0);
  evidence.states.inputTimeout={expired:true,noAutomaticRedispatch:true,noEnabledReply:true};
  await page.getByText('已过期',{exact:true}).locator('..').screenshot({path:path.join(evidenceRoot,'o44-expired.png')});
});
