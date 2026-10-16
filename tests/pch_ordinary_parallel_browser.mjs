// Actual HTTP, static UI, store and runtime; owner and provider transport are synthetic.
// Root supplies an exact private disposable CDP. This script never starts a browser.
import assert from 'node:assert/strict';
import path from 'node:path';
import {runBrowserJourney,within} from './pch_browser_runtime.mjs';

await runBrowserJourney('o46',async({page,context,evidence,evidenceRoot,origin,waitMessage,loginOwner})=>{
  const A='fixture-project-a',B='fixture-project-b';
  const viewport=process.env.PCH_PARALLEL_VIEWPORT==='narrow'?{width:390,height:844}:{width:1440,height:1050};
  await page.setViewportSize(viewport);
  evidence.viewport={mode:process.env.PCH_PARALLEL_VIEWPORT==='narrow'?'narrow':'desktop',...viewport};
  evidence.acceptance={transport:'mock',classification:'synthetic_fixture',actualLive:'unaccepted',
    scope:'HTTP/static UI/TaskStore/runtime isolation only'};
  evidence.expectedBrowserErrors=[];evidence.secondaryNetwork=[];
  const unexpectedConsole=[];
  let injectedReceiptSpec=null,receiptAbortConsoleAllowance=0;
  function observe(target,secondary=false){
    if(secondary)target.on('pageerror',error=>evidence.console.push(String(error)));
    target.on('console',message=>{
      if(secondary&&message.type()==='warning')evidence.consoleWarnings.push('secondary: '+message.text());
      if(message.type()!=='error')return;
      const text=message.text();
      if(receiptAbortConsoleAllowance>0&&message.location().url===origin+'/api/tasks'&&/net::ERR_FAILED/.test(text)){
        receiptAbortConsoleAllowance--;evidence.expectedBrowserErrors.push({kind:'injectedReceiptConsole',text,request_id:injectedReceiptSpec?.request_id});
      }else if(/Failed to load resource:.*(?:401|409|ERR_ABORTED)|net::ERR_ABORTED/.test(text))evidence.expectedBrowserErrors.push({kind:'console',text});
      else unexpectedConsole.push(text);
    });
    target.on('requestfailed',request=>{
      const failure={kind:'requestfailed',path:new URL(request.url()).pathname,reason:request.failure()?.errorText};
      const injected=request.method()==='POST'&&failure.path==='/api/tasks'&&injectedReceiptSpec
        &&request.postDataJSON()?.request_id===injectedReceiptSpec.request_id;
      if(injected&&/ERR_FAILED/.test(failure.reason||''))evidence.expectedBrowserErrors.push({...failure,kind:'injectedReceiptAbort',request_id:injectedReceiptSpec.request_id});
      else if(/ERR_ABORTED|cancel/i.test(failure.reason||''))evidence.expectedBrowserErrors.push(failure);
      else unexpectedConsole.push(JSON.stringify(failure));
    });
    if(secondary)target.on('response',response=>{
      const url=new URL(response.url());
      if(url.origin===origin)evidence.secondaryNetwork.push({method:response.request().method(),path:url.pathname,
        status:response.status(),expectedConflict:response.status()===409});
    });
  }
  observe(page);
  let editor;
  const panel=target=>target.getByRole('region',{name:'普通任务',exact:true});
  async function project(target,id,section='tasks'){
    await target.goto(origin+'/#projects/'+id+'/'+section);
    if(section==='tasks')await panel(target).waitFor();
    else await target.getByRole('region',{name:'Hub 管理配置',exact:true}).waitFor();
    await target.getByRole('heading',{name:id===A?'Synthetic Project A':'Synthetic Project B',exact:true}).waitFor();
  }
  async function getTask(id){
    const response=await context.request.get(origin+'/api/tasks/'+id);assert.equal(response.status(),200);
    return (await response.json()).data.task;
  }
  async function getProject(id){
    const response=await context.request.get(origin+'/api/projects/'+id);assert.equal(response.status(),200);
    return (await response.json()).data;
  }
  async function until(id,predicate,timeout=12000){
    const end=Date.now()+timeout;
    while(Date.now()<end){const task=await getTask(id);if(predicate(task))return task;
      await new Promise(resolve=>setTimeout(resolve,75));}
    assert.fail('Exact synthetic task did not reach required state: '+id);
  }
  async function scenario(project_id,value='delay'){
    await waitMessage(m=>m.ordinary_scenario===value,{action:'ordinary-scenario',project_id,scenario:value});
  }
  async function newDraft(target,id,objective){
    await project(target,id);
    await panel(target).getByRole('button',{name:'新建另一条任务草稿',exact:true}).click();
    await panel(target).getByLabel('任务目标',{exact:true}).fill(objective);
    await panel(target).getByLabel('任务范围',{exact:true}).fill('Read only registered synthetic AGENTS.md and README.md; return text.');
  }
  async function saveDraft(target){
    const [response]=await Promise.all([target.waitForResponse(r=>new URL(r.url()).pathname==='/api/ordinary-drafts'&&r.request().method()==='POST'),
      panel(target).getByRole('button',{name:'保存任务草稿',exact:true}).click()]);
    assert.equal(response.status(),200);
    await panel(target).getByLabel('普通任务状态',{exact:true}).filter({hasText:'已保存'}).waitFor();
    return (await response.json()).data.draft;
  }
  async function send(id,objective){
    await newDraft(page,id,objective);
    const [response]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/tasks'&&r.request().method()==='POST'),
      panel(page).getByRole('button',{name:'发送普通任务',exact:true}).click()]);
    assert.equal(response.status(),200);return (await response.json()).data.task.id;
  }
  async function stillB(id,binding){
    const task=await getTask(id);assert.equal(task.status,'running');assert.equal(Boolean(task.cancel),false);
    assert.equal(task.thread_id,binding.thread_id);assert.equal(task.turn_id,binding.turn_id);return task;
  }
  async function capture(name){
    const observation={name,viewport:page.viewportSize(),before:await page.evaluate(()=>document.visibilityState)};
    (evidence.captures??=[]).push(observation);
    await page.bringToFront();
    observation.after=await page.evaluate(()=>document.visibilityState);
    assert.equal(observation.after,'visible');
    await page.screenshot({path:path.join(evidenceRoot,name)});
    observation.complete=true;
  }
  try{
    await waitMessage(m=>m.ordinary_ready,{action:'ordinary-sample'});
    await project(page,A);await loginOwner();await project(page,A);
    await scenario(A);await scenario(B);
    const a=await send(A,'Synthetic parallel A task'),b=await send(B,'Synthetic parallel B task');
    await waitMessage(m=>m.ordinary_runtime_started,{action:'ordinary-runtime-start',request_timeout:30});
    const runningA=await until(a,t=>t.status==='running'&&t.thread_id&&t.turn_id);
    const runningB=await until(b,t=>t.status==='running'&&t.thread_id&&t.turn_id);
    assert.notEqual(runningA.thread_id,runningB.thread_id);assert.notEqual(runningA.turn_id,runningB.turn_id);
    assert.equal(runningA.classification,'synthetic_fixture');assert.equal(runningB.classification,'synthetic_fixture');
    assert.equal(runningA.kind,'ordinary_project');assert.equal(runningB.kind,'ordinary_project');
    await stillB(b,runningB);assert.equal((await getTask(a)).status,'running');
    evidence.states.simultaneousRunning={A:a,B:b,distinctThread:true,distinctTurn:true};

    // Separate server-backed drafts remain editable while these exact tasks run.
    await newDraft(page,A,'Unsent A draft while A and B run');const draftA=await saveDraft(page);
    await newDraft(page,B,'Unsent B draft while A and B run');const draftB=await saveDraft(page);
    await project(page,A);assert.equal(await panel(page).getByLabel('任务目标',{exact:true}).inputValue(),draftA.value.objective);
    await project(page,B);await page.reload();await panel(page).waitFor();
    assert.equal(await panel(page).getByLabel('任务目标',{exact:true}).inputValue(),draftB.value.objective);
    assert.equal(evidence.tasks.length,2);await stillB(b,runningB);
    evidence.states.activeDraftIsolation={A:draftA.value.objective,B:draftB.value.objective,noExtraDispatch:true};

    await page.goto(origin+'/#tasks/'+a);await page.getByRole('heading',{name:a,exact:true}).waitFor();
    const [cancel]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/tasks/'+a+'/cancel'),
      page.getByRole('button',{name:'取消这个任务',exact:true}).click()]);assert.equal(cancel.status(),200);
    const cancelled=await until(a,t=>t.status==='cancelled');
    await stillB(b,runningB);await stillB(b,runningB);
    const observations=await waitMessage(m=>'ordinary_control_observations'in m,{action:'ordinary-observations'});
    assert.deepEqual(observations.interrupts,[[runningA.thread_id,runningA.turn_id]]);assert.equal(observations.effects,0);
    evidence.states.cancelIsolation={cancelled:a,AStatus:cancelled.status,BStatus:'running',BCancel:false,exactInterrupt:true};
    await page.reload();await page.getByRole('heading',{name:a,exact:true}).waitFor();
    await capture('o46-exact-a-cancel.png');

    // Retrying A is an explicit new user task; cancellation is never redispatched.
    await scenario(A,'normal');
    const retriedA=await send(A,'Synthetic parallel A explicit task after cancellation');
    const completedA=await until(retriedA,t=>t.status==='checks_complete');
    assert.notEqual(completedA.id,a);assert.equal(completedA.parent,null);
    assert.match(completedA.result.response,/Synthetic read-only response for fixture-project-a/);
    await stillB(b,runningB);assert.equal((await getTask(a)).status,'cancelled');
    evidence.states.explicitRetryIsolation={originalCancelled:a,newTask:retriedA,newRequest:true,
      completed:true,BRemainsRunning:true,noAutomaticRedispatch:true};

    await page.goto(origin+'/#tasks/'+retriedA);await page.getByRole('heading',{name:retriedA,exact:true}).waitFor();
    await page.getByRole('link',{name:'继续此普通任务',exact:true}).click();await panel(page).waitFor();
    await panel(page).getByLabel('任务目标',{exact:true}).fill('Synthetic A idle continuation with lost receipt');
    await panel(page).getByLabel('任务范围',{exact:true}).fill('Continue the same owned read-only thread; return text.');
    assert.equal(await panel(page).getByLabel('任务模式',{exact:true}).inputValue(),'idle_continue');
    await scenario(A,'delay');
    let receiptAccepted,receiptReject;
    const admission=new Promise((resolve,reject)=>{receiptAccepted=resolve;receiptReject=reject;});
    const loseReceipt=async route=>{
      if(route.request().method()!=='POST'){await route.continue();return;}
      try{
        injectedReceiptSpec=route.request().postDataJSON();
        assert.equal(injectedReceiptSpec.parent_task_id,retriedA);assert.equal(injectedReceiptSpec.mode,'idle_continue');
        const response=await route.fetch();assert.equal(response.status(),200);
        const actual=(await response.json()).data.task;assert.equal(actual.id,injectedReceiptSpec.request_id);
        receiptAbortConsoleAllowance=1;await route.abort('failed');receiptAccepted(actual.id);
      }catch(error){receiptReject(error);}
    };
    const postsBeforeFollowup=evidence.tasks.length;
    await page.route('**/api/tasks',loseReceipt,{times:1});
    try{
      const reconciled=page.waitForResponse(r=>injectedReceiptSpec&&r.request().method()==='GET'
        &&new URL(r.url()).pathname==='/api/tasks/'+injectedReceiptSpec.request_id);
      await panel(page).getByRole('button',{name:'发送普通任务',exact:true}).click();
      const childId=await within(admission);assert.equal((await reconciled).status(),200);
      await page.waitForURL(origin+'/#tasks/'+childId);
      const child=await until(childId,t=>t.status==='running'&&t.turn_id);
      assert.equal(child.parent,retriedA);assert.equal(child.thread_id,completedA.thread_id);
      assert.notEqual(child.turn_id,completedA.turn_id);assert.equal(child.task_spec.request_id,childId);
      assert.equal(evidence.tasks.length,postsBeforeFollowup+1);
      assert.equal(evidence.tasks.filter(spec=>spec.request_id===childId).length,1);
      await stillB(b,runningB);assert.equal((await getTask(a)).status,'cancelled');
      evidence.states.followupIsolation={parent:retriedA,child:childId,sameThread:true,newTurn:true,BRemainsRunning:true};
      evidence.states.unknownReceiptReconciliation={request:childId,realAdmission200:true,
        originalGet200:true,singleTaskPost:true,noReplacementRequest:true};
      await waitMessage(m=>m.ordinary_released===true,{action:'ordinary-release',task_id:childId});
      await until(childId,t=>t.status==='checks_complete');await stillB(b,runningB);
      assert.equal((await getTask(a)).status,'cancelled');
      evidence.states.followupIsolation.completedAfterExactRelease=true;
    }finally{await page.unroute('**/api/tasks',loseReceipt);}

    // The real server replies first; only delivery to the old route is held.
    // Its completion (or browser navigation abort) must never replace B's DOM.
    async function lateResponse(pattern,oldRoute){
      let release,held,finished;
      const gate=new Promise(resolve=>{release=resolve;});
      const received=new Promise(resolve=>{held=resolve;});
      const done=new Promise(resolve=>{finished=resolve;});
      const handler=async route=>{
        try{const response=await route.fetch();assert.equal(response.status(),200);held();await gate;
          try{await route.fulfill({response});}catch(error){
            if(!/closed|cancel|abort|intercept/i.test(String(error)))throw error;
            evidence.expectedBrowserErrors.push({kind:'lateNavigationAbort',path:new URL(route.request().url()).pathname});
          }
        }finally{finished();}
      };
      await page.route(pattern,handler,{times:1});
      try{
        await page.goto(origin+oldRoute);await within(received);
        await project(page,B);assert.equal(await panel(page).getByLabel('任务目标',{exact:true}).inputValue(),draftB.value.objective);
        release();await within(done);
        assert.match(page.url(),/#projects\/fixture-project-b\/tasks$/);
        await page.getByRole('heading',{name:'Synthetic Project B',exact:true}).waitFor();
        assert.equal(await panel(page).getByLabel('任务目标',{exact:true}).inputValue(),draftB.value.objective);
        assert.equal(await page.getByRole('heading',{name:a,exact:true}).count(),0);
      }finally{release();await page.unroute(pattern,handler);}
    }
    await lateResponse('**/api/ordinary-context?project_id='+A,'/#projects/'+A+'/tasks');
    await lateResponse('**/api/tasks/'+a,'/#tasks/'+a);
    await stillB(b,runningB);
    evidence.states.lateResponseIsolation={ordinaryContext:true,taskGet:true,realFetchedResponses:true,BUnchanged:true};

    // Global management revision is shared, while A/B editor drafts remain local.
    editor=await context.newPage();await editor.setViewportSize(viewport);editor.setDefaultTimeout(5000);observe(editor,true);
    const management=target=>target.getByRole('region',{name:'Hub 管理配置',exact:true});
    const priority=target=>management(target).getByLabel('优先级（数值）',{exact:true});
    const saveManagement=async(target,expected)=>{
      const [response]=await Promise.all([target.waitForResponse(r=>new URL(r.url()).pathname==='/api/management/project'&&r.request().method()==='POST'),
        management(target).getByRole('button',{name:'保存配置',exact:true}).click()]);assert.equal(response.status(),expected);
      if(expected===200)await management(target).getByLabel('管理配置保存状态',{exact:true}).filter({hasText:'已保存'}).waitFor();
      return await response.json();
    };
    await project(page,A,'schedule');await project(editor,B,'schedule');
    await priority(page).fill('1');await priority(editor).fill('2');
    await saveManagement(page,200);const conflict=await saveManagement(editor,409);
    assert.equal(conflict.error.code,'MANAGEMENT_CONFLICT');assert.equal(await priority(editor).inputValue(),'2');
    assert.equal(await management(editor).getByRole('button',{name:'保存配置',exact:true}).isDisabled(),true);
    await management(editor).getByRole('button',{name:'重新读取当前版本',exact:true}).click();
    await management(editor).getByLabel('管理配置保存状态',{exact:true}).filter({hasText:'已重新读取当前版本'}).waitFor();
    assert.equal(await priority(editor).inputValue(),'2');assert.equal((await getProject(A)).management.overlay.priority,1);
    await saveManagement(editor,200);assert.equal((await getProject(B)).management.overlay.priority,2);
    assert.equal((await getProject(A)).management.overlay.priority,1);await stillB(b,runningB);
    evidence.states.managementIsolation={globalCAS409:true,BDraftPreserved:true,explicitRereadThenApply:true,AUnaffected:true};

    const sourceBeforeA=await getProject(A),sourceBeforeB=await getProject(B);
    await waitMessage(m=>m.project_id===A&&m.collected===false,{action:'source',project_id:A,completed:3,objective:'Synthetic A source updated while B runs'});
    const synced=await waitMessage(m=>'exit_code'in m,{action:'sync',only_project_id:A});assert.equal(synced.exit_code,0);
    const sourceAfterA=await getProject(A),sourceAfterB=await getProject(B);
    assert.notDeepEqual(sourceAfterA.business.fields,sourceBeforeA.business.fields);
    assert.deepEqual(sourceAfterB.business.fields,sourceBeforeB.business.fields);
    assert.deepEqual((await getTask(a)).task_spec,runningA.task_spec);assert.equal((await getTask(a)).status,'cancelled');
    assert.deepEqual((await stillB(b,runningB)).task_spec,runningB.task_spec);
    evidence.states.sourceIsolation={manualOnlyA:true,AChanged:true,BSourceUnchanged:true,taskBindingsUnchanged:true};

    await page.goto(origin+'/#tasks/'+b);await page.getByRole('heading',{name:b,exact:true}).waitFor();
    await capture('o46-b-still-running.png');
    await waitMessage(m=>m.ordinary_released===true,{action:'ordinary-release',task_id:b});
    const complete=await until(b,t=>t.status==='checks_complete');assert.equal(complete.result.transport,'mock');
    assert.match(complete.result.response,/Synthetic read-only response for fixture-project-b/);
    await page.reload();await page.getByLabel('普通任务执行回复',{exact:true}).filter({hasText:'Synthetic read-only response for fixture-project-b'}).waitFor();
    assert.equal((await getTask(a)).status,'cancelled');
    const finalProof=await waitMessage(m=>'ordinary_control_observations'in m,{action:'ordinary-observations'});
    assert.equal(finalProof.effects,0);assert.deepEqual(finalProof.interrupts,[[runningA.thread_id,runningA.turn_id]]);
    evidence.states.independentCompletion={releasedExact:b,reopenedCorrectReply:true,ACancelPreserved:true,noWorkspaceEffects:true};
    assert.deepEqual(unexpectedConsole,[]);assert.deepEqual(evidence.consoleWarnings,[]);assert.equal(evidence.tasks.length,4);
  }finally{if(editor)await editor.close();}
});
