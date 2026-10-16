// Real HTTP/static UI/stores and owned mock workers. Root supplies the private CDP.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {runBrowserJourney,within} from './pch_browser_runtime.mjs';
import {ownedSampler} from './pch_runtime_samples.mjs';

const empty=process.env.PCH_RESILIENCE_MODE==='empty';
await runBrowserJourney('o54',async({page,context,origin,evidence,evidenceRoot,waitMessage,loginOwner})=>{
  const A='fixture-project-a',B='fixture-project-b',sampler=ownedSampler(evidenceRoot);
  const names=new Map([[A,'Synthetic Project A'],[B,'Synthetic Project B']]);
  evidence.acceptance={classification:'synthetic_fixture',provider:'mock',actualLive:'unaccepted',
    requestCoverage:'Visible primary page only; excludes context.request and harness/control HTTP.',
    loadConclusion:'Bounded samples of the supplied process identities; no whole-system capacity claim.'};
  evidence.navigationTimings=[];evidence.requestObservations=[];
  evidence.runtimeDiagnostics={consoleErrors:[],warnings:[],pageErrors:[],requestFailed:[],expected:[],navigationCancelled:[]};
  const diagnostics=evidence.runtimeDiagnostics,records=new Map(),expected=new Map(),consoleAllowances=new Map();
  let seq=0,inflight=0,maxInflight=0,epoch=0,offlinePhase=false,guestPhase=true,finished=false;
  const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  function expectRequest(request,reason,pattern){
    expected.set(request,reason);
    const list=consoleAllowances.get(request.url())||[];list.push({reason,pattern});consoleAllowances.set(request.url(),list);
  }
  page.on('request',request=>{
    if(new URL(request.url()).origin!==origin)return;
    records.set(request,{sequence:++seq,url:request.url(),method:request.method(),started_ms:performance.now(),
      route:page.url(),epoch,ended:false});inflight++;maxInflight=Math.max(maxInflight,inflight);
    const url=new URL(request.url());
    if(guestPhase&&request.method()==='GET'&&request.url()===origin+'/api/ordinary-context?project_id='+A)
      expectRequest(request,'Exact initial unauthenticated ordinary context',/401/);
    if(offlinePhase&&['/api/ordinary-drafts','/api/identity','/api/ledger-head','/api/ordinary-context'].includes(url.pathname))
      expectRequest(request,'Exact request attempted during intentional context offline interval',/ERR_INTERNET_DISCONNECTED|ERR_FAILED/);
  });
  page.on('response',response=>{const row=records.get(response.request());if(row)row.status=response.status();});
  function finishRequest(request,failed=false){
    const row=records.get(request);if(!row||row.ended)return;
    row.ended=true;row.finished_ms=performance.now();row.duration_ms=row.finished_ms-row.started_ms;inflight--;
    const reason=expected.get(request);if(reason)row.expected=reason;
    if(failed){
      row.failure=request.failure()?.errorText;
      if(reason)diagnostics.expected.push({kind:'requestfailed',url:row.url,reason,failure:row.failure});
      else if(request.method()==='GET'&&/ERR_ABORTED/.test(row.failure||'')&&(row.epoch!==epoch||row.route!==page.url())){
        diagnostics.navigationCancelled.push({url:row.url,from:row.route,to:page.url(),failure:row.failure});
        const list=consoleAllowances.get(row.url)||[];list.push({reason:'Exact GET cancelled by route navigation',pattern:/ERR_ABORTED/});consoleAllowances.set(row.url,list);
      }else diagnostics.requestFailed.push({url:row.url,method:row.method,failure:row.failure});
    }
    evidence.requestObservations.push({...row});
  }
  page.on('requestfinished',request=>finishRequest(request));
  page.on('requestfailed',request=>finishRequest(request,true));
  page.on('pageerror',error=>diagnostics.pageErrors.push(String(error)));
  page.on('console',message=>{
    const text=message.text();if(message.type()==='warning'){diagnostics.warnings.push(text);return;}
    if(message.type()!=='error')return;
    const url=message.location().url,list=consoleAllowances.get(url)||[],index=list.findIndex(item=>item.pattern.test(text));
    if(index>=0){const [allowance]=list.splice(index,1);diagnostics.expected.push({kind:'console',url,text,reason:allowance.reason});}
    else if(/ERR_ABORTED/.test(text)&&[...records.values()].some(row=>row.url===url&&row.method==='GET'&&(row.epoch!==epoch||row.route!==page.url())))
      diagnostics.navigationCancelled.push({kind:'console',url,text,reason:'Exact GET cancelled after recorded route navigation'});
    else diagnostics.consoleErrors.push({url,text});
  });
  const main=page.locator('#main'),panel=()=>page.getByRole('region',{name:'普通任务',exact:true});
  const field=label=>panel().getByLabel(label,{exact:true}),button=name=>panel().getByRole('button',{name,exact:true});
  async function go(hash,heading=null){
    epoch++;const start=performance.now();
    if(page.url()===origin+'/'+hash)await page.reload();else await page.goto(origin+'/'+hash);
    if(heading)await page.getByRole('heading',{name:heading,exact:true}).waitFor();
    await page.waitForFunction(()=>!document.querySelector('#main')?.hasAttribute('aria-busy'));
    evidence.navigationTimings.push({hash,elapsed_ms:performance.now()-start,requests_seen:evidence.network.length});
  }
  async function project(id,section='tasks'){
    await go('#projects/'+id+'/'+section,names.get(id));if(section==='tasks')await panel().waitFor();
  }
  async function get(path){const response=await context.request.get(origin+path);assert.equal(response.status(),200);return (await response.json()).data;}
  const task=id=>get('/api/tasks/'+id).then(data=>data.task);
  const ordinary=id=>get('/api/ordinary-context?project_id='+id);
  async function until(id,predicate,timeout=12000){const deadline=Date.now()+timeout;
    while(Date.now()<deadline){const value=await task(id);if(predicate(value))return value;await sleep(75);}
    assert.fail('Exact synthetic task did not reach required state: '+id);
  }
  async function clickSave(name='保存任务草稿',status=200){
    const [response]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/ordinary-drafts'&&r.request().method()==='POST'),button(name).click()]);
    assert.equal(response.status(),status);return {body:await response.json(),command:response.request().postDataJSON()};
  }
  async function delayed(id){await waitMessage(m=>m.ordinary_scenario==='delay',{action:'ordinary-scenario',project_id:id,scenario:'delay'});}
  function assertQuiet(){
    assert.deepEqual(diagnostics.consoleErrors,[]);assert.deepEqual(diagnostics.warnings,[]);
    assert.deepEqual(diagnostics.pageErrors,[]);assert.deepEqual(diagnostics.requestFailed,[]);
  }
  try{
    await sampler.sample('fixture-started',evidence.network.length);
    if(empty){
      const setup=await waitMessage(m=>m.empty_directory_ready===true,{action:'empty-directory'});
      assert.equal(setup.total,0);assert.equal(setup.ledger_unchanged,true);
      assert.equal(setup.sync.status,'NO_ELIGIBLE_PROJECTS');assert.equal(setup.sync.appended_count,0);
      await go('#projects','项目');await page.getByRole('heading',{name:'尚未登记项目',exact:true}).waitFor();
      await loginOwner();guestPhase=false;
      const directory=await get('/api/projects?offset=0&limit=100');assert.equal(directory.total,0);assert.deepEqual(directory.projects,[]);
      assert.deepEqual((await get('/api/ledger-head')).head,setup.ledger_head);
      const refresh=page.getByRole('button',{name:'刷新全部符合资格项目（0 项）',exact:true});assert.equal(await refresh.isDisabled(),true);
      const old=await context.request.get(origin+'/api/projects/'+A);assert.equal(old.status(),404);
      const handler=route=>{expectRequest(route.request(),'Exact removed synthetic A after true empty registry',/404/);return route.continue();};
      await page.route('**/api/projects/'+A,handler,{times:1});
      await go('#projects/'+A,'对象不存在或已移除');await page.getByRole('link',{name:'返回项目列表',exact:true}).click();
      await page.getByRole('heading',{name:'尚未登记项目',exact:true}).waitFor();
      const synced=await waitMessage(m=>'exit_code'in m,{action:'sync'});assert.equal(synced.exit_code,2);
      assert.equal(synced.result.status,'NO_ELIGIBLE_PROJECTS');assert.equal(synced.result.appended_count,0);
      assert.deepEqual((await get('/api/ledger-head')).head,setup.ledger_head);assert.equal(evidence.tasks.length,0);
      evidence.states.trueEmpty={authenticatedHTTP:true,UI:true,oldProject404:true,noFalseRefreshSuccess:true,ledgerUnchanged:true};
      await sampler.sample('empty-settled',evidence.network.length);assertQuiet();finished=true;return;
    }

    await go('#projects','项目');assert.equal(await main.locator('a[data-project-id]').count(),103);
    await sampler.sample('idle',evidence.network.length);
    const pages=await Promise.all([get('/api/projects?offset=0&limit=100'),get('/api/projects?offset=100&limit=100')]);
    assert.deepEqual(pages.map(value=>value.projects.length),[100,3]);assert.equal(pages[0].total,103);
    for(let repetition=0;repetition<3;repetition++){
      await project(A,'overview');await project(B,'overview');await go('#projects','项目');
      assert.equal(await main.locator('a[data-project-id]').count(),103);
      await sampler.sample('navigation-103-'+(repetition+1),evidence.network.length);
    }
    await project(A);await panel().getByText('请连接所有者后保存草稿或发送；本地输入保留。',{exact:true}).waitFor();
    await loginOwner();guestPhase=false;await project(A);
    await page.getByText('尚无这个项目的已接收任务。',{exact:true}).waitFor();
    assert.match(await panel().innerText(),/普通任务暂不可发送/);
    const unavailable=await ordinary(A);assert.equal(unavailable.ready,false);assert.equal(unavailable.reason_code,'ORDINARY_EXECUTION_DISABLED');
    assert.equal(await button('发送普通任务').isDisabled(),true);
    await go('#tasks','任务与结果');await page.getByRole('heading',{name:'尚无已接收任务',exact:true}).waitFor();
    await project(A,'schedule');
    await page.getByRole('region',{name:'项目关系',exact:true}).getByText('尚未配置本项目的管理关系；不能据此推断项目独立。',{exact:true}).waitFor();
    const timeline=page.getByRole('region',{name:'当前项目时间线',exact:true});
    assert.match(await timeline.getByRole('status').innerText(),/没有已知日期；未推断当前日期/);
    evidence.states.emptyVersusUnavailable={directory103:true,noTasks:true,unconfiguredRelations:true,undeclaredDates:true,
      ordinaryUnconfigured:true,unauthenticatedDistinct:true};

    const stress=await waitMessage(m=>m.stress_ready===true,{action:'stress-sample'});
    for(const row of stress.projects)names.set(row.project_id,row.name);
    await waitMessage(m=>m.ordinary_ready===true,{action:'ordinary-sample'});
    await project(A);await field('任务目标').fill('Synthetic resilient saved draft A');
    await field('任务范围').fill('Read registered synthetic inputs only; preserve draft and return text.');
    const originalObjective=await field('任务目标').inputValue();
    offlinePhase=true;await context.setOffline(true);
    await page.locator('#connection').getByText(/浏览器离线/).waitFor();
    const attempted=page.waitForRequest(r=>new URL(r.url()).pathname==='/api/ordinary-drafts'&&r.method()==='POST');
    await button('保存任务草稿').click();const offlineCommand=(await attempted).postDataJSON();
    await button('重试确认草稿保存').waitFor();assert.equal(await field('任务目标').inputValue(),originalObjective);
    assert.match(page.url(),/#projects\/fixture-project-a\/tasks$/);assert.equal(evidence.tasks.length,0);
    await context.setOffline(false);offlinePhase=false;
    const recovered=await clickSave('重试确认草稿保存');assert.deepEqual(recovered.command,offlineCommand);
    await panel().getByLabel('普通任务状态',{exact:true}).filter({hasText:'已保存'}).waitFor();
    assert.equal((await ordinary(A)).draft.value.objective,originalObjective);assert.equal(evidence.tasks.length,0);
    evidence.states.offlineDraftRecovery={routeRetained:true,textRetained:true,sameOriginalCommand:true,noTaskDispatch:true};

    await field('任务目标').fill('Synthetic admitted draft with lost 500 receipt');
    const before500=await ordinary(A);let admittedDraft,original500;
    const save500=async route=>{
      original500=route.request().postDataJSON();const response=await route.fetch();assert.equal(response.status(),200);
      admittedDraft=(await response.json()).data.draft;
      expectRequest(route.request(),'Exact single admitted ordinary draft with synthetic 500 UNKNOWN receipt',/500/);
      await route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({ok:false,error:{code:'SYNTHETIC_SAVE_RECEIPT_LOST',outcome:'UNKNOWN',retryable:false,details:{}}})});
    };
    await page.route('**/api/ordinary-drafts',save500,{times:1});await clickSave('保存任务草稿',500);
    await button('重试确认草稿保存').waitFor();assert.equal(await field('任务目标').inputValue(),original500.value.objective);
    const retry500=await clickSave('重试确认草稿保存');assert.deepEqual(retry500.command,original500);
    const after500=await ordinary(A);assert.equal(after500.draft.revision,before500.draft.revision+1);
    assert.equal(after500.draft.revision,admittedDraft.revision);assert.deepEqual(after500.draft.value,original500.value);
    assert.equal(evidence.tasks.length,0);
    evidence.states.admittedSave500={sameCommand:true,saveRequest:original500.request_id,taskRequest:original500.value.request_id,
      oneRevision:true,noTaskDispatch:true};

    await delayed(A);await delayed(B);
    await waitMessage(m=>m.ordinary_runtime_started===true,{action:'ordinary-runtime-start',request_timeout:30});
    let admittedSpec,admissionResolve,admissionReject;
    const admission=new Promise((resolve,reject)=>{admissionResolve=resolve;admissionReject=reject;});
    const loseTaskReceipt=async route=>{
      try{admittedSpec=route.request().postDataJSON();const response=await route.fetch();assert.equal(response.status(),200);
        const accepted=(await response.json()).data.task;assert.equal(accepted.id,admittedSpec.request_id);
        expectRequest(route.request(),'Exact single admitted task POST with intentionally lost receipt',/ERR_FAILED/);
        await route.abort('failed');admissionResolve(accepted.id);
      }catch(error){admissionReject(error);}
    };
    await page.route('**/api/tasks',loseTaskReceipt,{times:1});
    const reconciliation=page.waitForResponse(r=>admittedSpec&&r.request().method()==='GET'&&new URL(r.url()).pathname==='/api/tasks/'+admittedSpec.request_id);
    await button('发送普通任务').click();const a=await within(admission);assert.equal((await reconciliation).status(),200);
    await page.waitForURL(origin+'/#tasks/'+a);assert.equal(evidence.tasks.length,1);
    assert.equal(evidence.tasks[0].request_id,original500.value.request_id);
    evidence.states.taskReceiptUnknown={request:a,oneTaskPost:true,realAdmission:true,originalGETReconciled:true};
    await project(B);await button('新建另一条任务草稿').click();await field('任务目标').fill('Synthetic independent resilience B');
    await field('任务范围').fill('Read registered synthetic inputs only; return the B reply.');
    const [sentB]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/tasks'&&r.request().method()==='POST'),button('发送普通任务').click()]);
    assert.equal(sentB.status(),200);const b=(await sentB.json()).data.task.id;
    const runningA=await until(a,value=>value.status==='running'&&value.turn_id),runningB=await until(b,value=>value.status==='running'&&value.turn_id);
    assert.notEqual(runningA.thread_id,runningB.thread_id);assert.notEqual(runningA.turn_id,runningB.turn_id);
    assert.equal(evidence.tasks.length,2);await go('#tasks/'+a,'任务与结果');
    const steadyStart=performance.now(),steadyRecordStart=evidence.requestObservations.length;
    await sampler.sample('two-running-before-steady',evidence.network.length);await sleep(16000);
    assert.equal((await task(a)).status,'running');assert.equal((await task(b)).status,'running');
    const steady=evidence.requestObservations.slice(steadyRecordStart).filter(row=>row.started_ms>=steadyStart);
    const ledger=steady.filter(row=>new URL(row.url).pathname==='/api/ledger-head');
    const polls=steady.filter(row=>new URL(row.url).pathname==='/api/tasks/'+a);
    assert.ok(ledger.length>=3,'Visible page must observe at least three five-second ledger checks');
    assert.ok(polls.length>=3,'Visible running task must retain bounded observed polling');
    for(const [rows,maximum,minimumSpacing]of [[ledger,4,4500],[polls,17,900]]){
      assert.ok(rows.length<=maximum,'Stable page must not start an unbounded polling loop');
      for(let index=1;index<rows.length;index++){
        assert.ok(rows[index].started_ms-rows[index-1].started_ms>=minimumSpacing);
        assert.ok(rows[index].started_ms>=rows[index-1].finished_ms,'Same-resource polls must not overlap');
      }
    }
    evidence.states.visibleSteady={interval_ms:performance.now()-steadyStart,ledgerTimes:ledger.map(row=>row.started_ms),
      taskTimes:polls.map(row=>row.started_ms),maxInflight};

    const sourceBeforeA=(await get('/api/projects/'+A)).business.fields,sourceBeforeB=(await get('/api/projects/'+B)).business.fields;
    await waitMessage(m=>m.project_id===A&&m.collected===false,{action:'source',project_id:A,completed:null,objective:'Synthetic A source updated during resilience overlap'});
    const refreshed=await waitMessage(m=>'exit_code'in m,{action:'sync',only_project_id:A});assert.equal(refreshed.exit_code,0);
    const sourceAfterA=(await get('/api/projects/'+A)).business.fields;assert.notDeepEqual(sourceAfterA,sourceBeforeA);
    assert.equal(sourceAfterA.current_work.objective,'Synthetic A source updated during resilience overlap');
    assert.deepEqual((await get('/api/projects/'+B)).business.fields,sourceBeforeB);
    await project(A,'overview');await page.getByText('Synthetic A source updated during resilience overlap',{exact:true}).first().waitFor();
    for(const [id,binding]of [[a,runningA],[b,runningB]]){const current=await task(id);assert.equal(current.status,'running');
      assert.equal(Boolean(current.cancel),false);assert.equal(current.thread_id,binding.thread_id);assert.equal(current.turn_id,binding.turn_id);
      assert.deepEqual(current.task_spec,binding.task_spec);}
    await sampler.sample('two-tasks-plus-source-refresh',evidence.network.length);
    evidence.states.overlap={twoRunning:true,manualOnlyARefresh:true,BSourceUnchanged:true,taskBindingsUnchanged:true};
    for(const [id,projectId]of [[a,A],[b,B]]){
      await waitMessage(m=>m.ordinary_released===true,{action:'ordinary-release',task_id:id});
      const completed=await until(id,value=>value.status==='checks_complete'),expectedReply=stress.projects.find(row=>row.project_id===projectId);
      assert.equal(completed.project,projectId);assert.equal(completed.task_spec.project_id,projectId);
      if(projectId===A){const stillB=await task(b);assert.equal(stillB.status,'running');assert.equal(Boolean(stillB.cancel),false);}
      assert.equal(completed.result.transport,'mock');assert.equal(completed.result.response_truncated,false);
      assert.equal(Buffer.byteLength(completed.result.response),expectedReply.reply_bytes);
      assert.equal(createHash('sha256').update(completed.result.response).digest('hex'),expectedReply.reply_sha256);
      await go('#tasks/'+id,'任务与结果');await page.getByLabel('普通任务执行回复',{exact:true}).waitFor();
      assert.equal(await page.getByLabel('普通任务执行回复',{exact:true}).textContent(),completed.result.response);
      await sampler.sample('long-reply-'+projectId,evidence.network.length);
    }
    evidence.states.longReplyIsolation={bothHashesMatch:true,noTruncation:true,twoTaskPosts:true};
    await page.screenshot({path:path.join(evidenceRoot,'o54-long-reply.png')});
    const proof=await waitMessage(m=>'ordinary_control_observations'in m,{action:'ordinary-observations'});
    assert.equal(proof.effects,0);assert.deepEqual(proof.interrupts,[]);
    for(let index=1;index<=3;index++){await sleep(1000);await sampler.sample('settled-'+index,evidence.network.length);}
    evidence.states.settled={samples:3,interval_ms:1000,effects:0,interpretation:'Bounded post-release samples; no unbounded-growth conclusion from a single point.'};
    assert.equal(evidence.tasks.length,2);assertQuiet();finished=true;
  }finally{
    if(offlinePhase)await context.setOffline(false);
    await sampler.sample('journey-ending',evidence.network.length);
    evidence.maxInflight=maxInflight;evidence.loadSamples=sampler.finish();
    // End this page's polling while its fixture still serves requests. The
    // shared runner stops the server before closing the disposable browser.
    if(finished){epoch++;await page.goto('about:blank');assertQuiet();}
  }
},{directorySize:empty?2:103});
