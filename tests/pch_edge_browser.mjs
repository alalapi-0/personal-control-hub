// O6.1 targeted cases through real UI/HTTP/TaskStore; synthetic sources/provider only.
import assert from 'node:assert/strict';
import path from 'node:path';
import {runBrowserJourney} from './pch_browser_runtime.mjs';

const mode=process.env.PCH_EDGE_MODE||'management';
assert.ok(['management','source','runtime','definition'].includes(mode));
await runBrowserJourney('o61-edge-'+mode,async({page,context,origin,evidence,evidenceRoot,waitMessage,loginOwner})=>{
  const A='fixture-project-a',B='fixture-project-b';
  evidence.acceptance={mode,classification:'synthetic_fixture',transport:'mock',actualLive:'unaccepted',
    coverage:'Only named original-case deterministic branches; no full 98 or owner acceptance claim.'};
  const diagnostics=evidence.edgeDiagnostics={consoleErrors:[],warnings:[],pageErrors:[],requestFailed:[],httpFailures:[],expected:[],navigationCancelled:[]};
  const requestRecords=new Map(),consoleAllowances=new Map();let expectedNext=null,epoch=0;
  page.on('request',request=>{
    const url=new URL(request.url());if(url.origin!==origin)return;
    const row={url:request.url(),method:request.method(),epoch,route:page.url()};requestRecords.set(request,row);
    if(expectedNext&&url.pathname===expectedNext.path&&request.method()===expectedNext.method){
      row.expected=expectedNext;expectedNext=null;
      const list=consoleAllowances.get(row.url)||[];list.push({status:row.expected.status,reason:row.expected.reason});consoleAllowances.set(row.url,list);
    }
  });
  page.on('console',message=>{
    if(message.type()==='warning'){diagnostics.warnings.push(message.text());return;}
    if(message.type()!=='error')return;
    const url=message.location().url,text=message.text(),list=consoleAllowances.get(url)||[];
    const index=list.findIndex(item=>new RegExp('\\b'+item.status+'\\b').test(text));
    if(index>=0){const [item]=list.splice(index,1);diagnostics.expected.push({kind:'console',url,text,...item});}
    else if(/ERR_ABORTED/.test(text)&&[...requestRecords.values()].some(row=>row.url===url&&row.method==='GET'&&(row.epoch!==epoch||row.route!==page.url())))
      diagnostics.navigationCancelled.push({kind:'console',url,text});
    else diagnostics.consoleErrors.push({url,text});
  });
  page.on('requestfailed',request=>{
    const row=requestRecords.get(request),failure=request.failure()?.errorText;
    if(row&&row.method==='GET'&&/ERR_ABORTED/.test(failure||'')&&(row.epoch!==epoch||row.route!==page.url()))
      diagnostics.navigationCancelled.push({...row,failure});
    else diagnostics.requestFailed.push({url:request.url(),method:request.method(),failure});
  });
  page.on('response',async response=>{
    const url=new URL(response.url());if(url.origin!==origin||response.status()<400)return;
    const row={path:url.pathname,method:response.request().method(),status:response.status()};
    diagnostics.httpFailures.push(row);
    try{row.body=await response.json();}catch(error){row.bodyReadError=String(error);}
  });
  page.on('pageerror',error=>diagnostics.pageErrors.push(String(error)));
  const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms)),main=page.locator('#main');
  async function go(hash,heading=null){epoch++;if(page.url()===origin+'/'+hash)await page.reload();else await page.goto(origin+'/'+hash);
    if(heading)await page.getByRole('heading',{name:heading,exact:true}).waitFor();
    await page.waitForFunction(()=>!document.querySelector('#main')?.hasAttribute('aria-busy'));
  }
  async function get(path){const response=await context.request.get(origin+path);assert.equal(response.status(),200);return (await response.json()).data;}
  const readProject=id=>get('/api/projects/'+id),readTask=id=>get('/api/tasks/'+id).then(data=>data.task);
  const project=(id,section='schedule')=>go('#projects/'+id+'/'+section,id===A?'Synthetic Project A':'Synthetic Project B');
  async function sync(only_project_id){return waitMessage(m=>'exit_code'in m,{action:'sync',...(only_project_id?{only_project_id}:{})});}
  async function until(id,predicate,timeout=18000){const end=Date.now()+timeout;
    while(Date.now()<end){const task=await readTask(id);if(predicate(task))return task;await sleep(80);}
    assert.fail('Exact synthetic task failed to reach required state: '+id);
  }
  await go('#projects','项目');await loginOwner();

  if(mode==='definition'){
    const sample=await waitMessage(m=>m.stress_ready===true,{action:'stress-sample',active_blocked:true});
    assert.equal(sample.active_blocked,true);
    const names=Object.fromEntries(sample.projects.map(row=>[row.project_id,row.name]));
    const roles={ [A]:['Producer','Library','Synthetic read-only reviewer'],[B]:['Consumer','Working copy','Synthetic read-only reviewer']};
    const fields=target=>target.evaluate(node=>Object.fromEntries([...node.querySelectorAll(':scope > dl > div')].map(row=>[row.querySelector('dt')?.textContent,row.querySelector('dd')?.textContent])));
    const definitions=[];
    for(const id of [A,B]){
      await go('#projects/'+id+'/overview',names[id]);
      const shown=await fields(page.getByRole('region',{name:'项目定义',exact:true}));assert.equal(shown['项目角色'],roles[id].join('、'));
      if(id===A){const summary=await fields(main.locator('.project-summary'));assert.equal(summary['工作状态'],'进行中');
        assert.match(summary['阻塞与待决定'],/Candidate has changed/);assert.match(summary['阻塞与待决定'],/恢复条件：Review B/);}
      definitions.push({id,roles:shown['项目角色']});
    }
    const source=()=>page.getByRole('region',{name:'来源声明关系',exact:true});
    for(const id of [A,B]){
      await go('#projects/'+id+'/schedule',names[id]);
      const group=source().locator(':scope > div').filter({has:page.getByRole('heading',{name:'项目角色',exact:true})});
      assert.deepEqual(await group.locator(':scope > ul > li').allTextContents(),roles[id]);
    }
    await go('#projects/'+A+'/schedule',names[A]);
    const proposals=main.locator('details').filter({has:page.locator(':scope > summary').filter({hasText:'关系提议 · 尚未确认'})});
    assert.equal(await proposals.count(),1);await proposals.locator(':scope > summary').click();
    assert.deepEqual(new Set(await proposals.locator(':scope > article > .row > h3').allTextContents()),new Set(['流程衔接','相似审核方式','相似视觉语言']));
    const copy=source().getByRole('link',{name:names[B],exact:true});assert.equal(await copy.getAttribute('href'),'#projects/'+B);await copy.click();
    await page.getByRole('heading',{name:names[B],exact:true}).waitFor();assert.equal(new URL(page.url()).hash,'#projects/'+B);
    await go('#projects/'+B+'/schedule',names[B]);
    const parent=source().getByRole('link',{name:names[A],exact:true});assert.equal(await parent.getAttribute('href'),'#projects/'+A);await parent.click();
    await page.getByRole('heading',{name:names[A],exact:true}).waitFor();assert.equal(new URL(page.url()).hash,'#projects/'+A);
    const final=await readProject(A);assert.equal(final.business.normalized_status,'active');assert.equal(final.business.fields.blockers[0].recovery_condition,'Review B');
    assert.equal(evidence.tasks.length,0);
    evidence.states.B02B03B05R03Definitions={definitions,threeProposals:['流程衔接','相似审核方式','相似视觉语言'],workingCopyRoundTrip:true,
      exactProjectIDs:true,activeWithLocalBlocker:true,blockerReason:'Candidate has changed',recovery:'Review B',noTaskDispatch:true};
    await page.screenshot({path:path.join(evidenceRoot,'edge-definition-active-blocked.png')});
  }else if(mode==='management'){
    const management=()=>page.getByRole('region',{name:'Hub 管理配置',exact:true});
    const field=label=>management().getByLabel(label,{exact:true});
    async function save(expectedStatus=200){
      if(expectedStatus!==200)expectedNext={path:'/api/management/project',method:'POST',status:expectedStatus,reason:'Exact UI ownership cycle admission'};
      const [response]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/management/project'&&r.request().method()==='POST'),
        management().getByRole('button',{name:'保存配置',exact:true}).click()]);
      assert.equal(response.status(),expectedStatus);const body=await response.json();
      if(expectedStatus===200)await management().getByLabel('管理配置保存状态',{exact:true}).filter({hasText:'已保存'}).waitFor();
      return {body,command:response.request().postDataJSON()};
    }
    const optionsEvidence=[];
    for(const id of [A,B]){
      await project(id);await field('关系配置').selectOption('confirmed');
      for(const label of ['归属项目','依赖项目（可多选）','关联项目（可多选）']){
        const values=await field(label).locator('option').evaluateAll(nodes=>nodes.map(node=>node.value).filter(Boolean));
        assert.equal(values.includes(id),false);assert.equal(new Set(values).size,values.length);
        assert.ok(values.includes(id===A?B:A));optionsEvidence.push({project:id,label,values});
      }
    }
    await project(A);await field('关系配置').selectOption('confirmed');await field('归属项目').selectOption(B);
    await field('独立项目').selectOption('false');const assigned=await save();
    assert.equal((await readProject(A)).management.overlay.relations.belongs_to,B);
    assert.equal((await readProject(A)).management.overlay.relations.independent,false);
    evidence.states.R08Options={selfExcluded:true,unique:true,options:optionsEvidence};

    const beforeInvalid=(await readProject(A)).management;
    const malformed=[{kind:'self',relations:{...assigned.command.value.relations,related_to:[A]}},
      {kind:'duplicate',relations:{...assigned.command.value.relations,depends_on:[B,B]}},
      {kind:'missing',relations:{...assigned.command.value.relations,related_to:['fixture-absent-project']}}];
    const invalidEvidence=[];
    for(const item of malformed){
      const command={...assigned.command,request_id:'edge-invalid-'+item.kind,expected_revision:beforeInvalid.revision,
        expected_hash:beforeInvalid.config_hash,value:{...assigned.command.value,relations:item.relations}};
      expectedNext={path:'/api/management/project',method:'POST',status:400,reason:'Exact malformed management '+item.kind+' request '+command.request_id};
      const outcome=await page.evaluate(async command=>{
        const {api}=await import('/assets/common.js');try{await api('/api/management/project',command);return {unexpectedSuccess:true};}
        catch(error){return {status:error.status,code:error.code,outcome:error.outcome};}
      },command);
      assert.equal(outcome.status,400);assert.equal(outcome.outcome,'NOT_COMMITTED');
      const after=(await readProject(A)).management;assert.equal(after.revision,beforeInvalid.revision);assert.equal(after.config_hash,beforeInvalid.config_hash);
      assert.deepEqual(after.overlay,beforeInvalid.overlay);invalidEvidence.push({kind:item.kind,...outcome});
    }
    evidence.states.R08Malformed={cases:invalidEvidence,realOwnerCSRF:true,revisionAndHashUnchanged:true};

    await project(B);await field('关系配置').selectOption('confirmed');await field('归属项目').selectOption(A);
    const cycle=await save(409);assert.equal(cycle.body.error.code,'MANAGEMENT_RELATION_CONFLICT');
    await management().getByLabel('管理配置保存状态',{exact:true}).filter({hasText:'归属关系形成循环'}).waitFor();
    assert.equal(await field('归属项目').inputValue(),A);assert.equal((await readProject(B)).management.overlay,null);
    await field('归属项目').selectOption('');await field('依赖项目（可多选）').selectOption([A]);
    await field('独立项目').selectOption('true');await save();
    await project(A);await field('依赖项目（可多选）').selectOption([B]);await save();
    const a=await readProject(A),b=await readProject(B);
    assert.deepEqual(a.management.overlay.relations.depends_on,[B]);assert.deepEqual(b.management.overlay.relations.depends_on,[A]);
    assert.equal(a.management.overlay.relations.independent,false);assert.equal(b.management.overlay.relations.independent,true);
    assert.ok(a.management.warnings.includes('PROJECT_DEPENDENCY_CYCLE'));assert.ok(b.management.warnings.includes('PROJECT_DEPENDENCY_CYCLE'));
    await page.getByRole('region',{name:'项目关系',exact:true}).getByText('项目依赖形成循环；当前允许保留，但需要人工复核。',{exact:true}).waitFor();
    assert.equal(evidence.tasks.length,0);
    evidence.states.R08CycleSemantics={ownershipRejected409:true,BDraftRetained:true,explicitRecovery:true,
      dependencyCycleAllowed200:true,literalIndependentPreserved:true,noTaskDispatch:true};
    await page.screenshot({path:path.join(evidenceRoot,'edge-management-cycle.png')});
  }else if(mode==='source'){
    const failures=[];
    for(const kind of ['missing','invalid','permission']){
      const beforeA=await readProject(A),beforeB=await readProject(B);
      const fault=await waitMessage(m=>m.source_fault===kind&&m.project_id===A,{action:'source-fault',project_id:A,kind});assert.equal(fault.collected,false);
      const collected=await sync(A);assert.notEqual(collected.exit_code,0);
      const failed=await readProject(A),unchangedB=await readProject(B);
      const expectedAvailability={missing:'missing_source',invalid:'invalid',permission:'permission_denied'}[kind];
      assert.equal(failed.source.availability,expectedAvailability);assert.equal(failed.source.latest_observation.success,false);
      assert.equal(failed.source.last_success_at,beforeA.source.last_success_at);
      assert.equal(failed.business.state,'unknown');assert.equal(typeof failed.business.unknown_fields['current_work.objective'],'string');
      assert.equal(failed.operational.latest_attempt.success,false);assert.ok(failed.operational.last_success?.success);
      assert.equal(failed.freshness.state,'stale');assert.equal(failed.business.normalized_status,'unknown');
      assert.equal(failed.business.fields.current_work.objective,null);
      assert.deepEqual(unchangedB.business.fields,beforeB.business.fields);assert.deepEqual(unchangedB.source,beforeB.source);
      assert.deepEqual(failed.operational.last_success,beforeA.operational.last_success);
      await project(A,'sources');
      const history=page.locator('details').filter({has:page.locator('summary').filter({hasText:'历史成功快照'})});
      assert.equal(await history.count(),1);assert.equal(await history.getAttribute('open'),null);
      await history.locator(':scope > summary').click();
      await history.getByText('这是过去的项目记录，不能替代当前失败或未知。',{exact:true}).waitFor();
      const ordinaryCurrentText=await main.evaluate(node=>{const copy=node.cloneNode(true);for(const history of copy.querySelectorAll('details'))history.remove();return copy.textContent;});
      assert.ok(!ordinaryCurrentText.includes(beforeA.business.fields.current_work.objective),'Past objective must not appear as current outside history');
      assert.match(ordinaryCurrentText,/失败|未知|上次快照|尚未更新/);
      failures.push({kind,api:'/api/projects/'+A,availability:failed.source.availability,latestObservation:failed.source.latest_observation,
        businessState:failed.business.state,status:failed.freshness.state,errorCodes:failed.operational.latest_attempt.errors?.map(error=>error.code)||[],historicalExplicit:true,BUnchanged:true});
      await waitMessage(m=>m.source_restored===true&&m.project_id===A,{action:'source-restore',project_id:A});
      assert.equal((await sync(A)).exit_code,0);const restored=await readProject(A);
      assert.equal(restored.source.availability,'resolved');assert.equal(restored.source.latest_observation.success,true);
      assert.equal(restored.freshness.state,'fresh');assert.equal(restored.operational.latest_attempt.success,true);
      assert.deepEqual(restored.business.fields,beforeA.business.fields);
      await project(A,'sources');assert.equal(await page.locator('summary').filter({hasText:'历史成功快照'}).count(),0);
    }
    evidence.states.D06D07SourceFailures={cases:failures,restoredFreshEachTime:true,noHistoricalCurrentFallback:true};
    const before=await get('/api/sync-status');
    const excluded=await waitMessage(m=>m.excluded_ready===true,{action:'excluded-sample'});
    assert.equal(excluded.scope_total,before.scope_total);assert.equal(excluded.excluded.length,4);
    assert.equal(excluded.excluded_probe_count,0,'Fixture brackets its own full sync Resolver source-refresh probes for excluded IDs');
    assert.deepEqual(new Set(excluded.excluded.map(row=>row.category)),new Set(['removed','cloud','read-disabled','no_current_goal_access']));
    const status=await get('/api/sync-status');assert.equal(status.scope_total,before.scope_total);
    for(const row of excluded.excluded)assert.equal(status.eligible_project_ids.includes(row.project_id),false);
    assert.equal(excluded.sync.exit_code,0);
    await go('#projects','项目');
    const [fullResponse]=await Promise.all([page.waitForResponse(response=>new URL(response.url()).pathname==='/api/refresh'&&response.request().method()==='POST',{timeout:30000}),
      page.getByRole('button',{name:'刷新全部符合资格项目（'+status.eligible_project_ids.length+' 项）',exact:true}).click()]);
    assert.equal(fullResponse.status(),200);const fullBody=(await fullResponse.json()).data,fullCommand=fullResponse.request().postDataJSON();
    assert.equal(fullBody.request.status,'FINISHED');assert.deepEqual(new Set(fullCommand.project_ids),new Set(status.eligible_project_ids));
    for(const row of excluded.excluded)assert.equal(fullCommand.project_ids.includes(row.project_id),false);
    const after=await get('/api/sync-status');assert.equal(after.scope_total,before.scope_total);
    for(const row of excluded.excluded)assert.equal(after.eligible_project_ids.includes(row.project_id),false);
    await page.locator('summary').filter({hasText:'来源同步与读取资格'}).click();
    const scope=page.getByRole('region',{name:'刷新范围',exact:true});await scope.getByText(new RegExp('固定范围 '+before.scope_total+' 项')).waitFor();
    await scope.locator('summary').filter({hasText:'查看排除项及原因'}).click();
    const reasonLabels={removed:'已从本地移除',cloud:'云项目，不读取本地来源',
      'read-disabled':'当前来源读取未获允许',no_current_goal_access:'当前来源读取未获允许'};
    for(const row of excluded.excluded)await scope.getByText(row.project_id+'：'+reasonLabels[row.category],{exact:true}).waitFor();
    evidence.states.D11D12Excluded={categories:excluded.excluded,scopeTotal:before.scope_total,scopeUnchanged:true,
      fullSyncCompleted:true,actualRefreshPOST:fullCommand.request_id,notEligible:true,
      fixtureFullSync:excluded.sync,fixtureExcludedResolverProbeCount:excluded.excluded_probe_count,
      probeClaim:'Fixture-bracketed Resolver source-refresh probes for its full sync only; no whole-OS or later HTTP zero-probe claim.'};
    await page.screenshot({path:path.join(evidenceRoot,'edge-source-exclusions.png')});
  }else{
    const setup=await waitMessage(m=>m.ordinary_ready===true,{action:'ordinary-sample',max_turn_seconds:10});assert.equal(setup.max_turn_seconds,10);
    await waitMessage(m=>m.ordinary_runtime_started===true,{action:'ordinary-runtime-start',request_timeout:20});
    const panel=()=>page.getByRole('region',{name:'普通任务',exact:true});
    async function send(projectId,scenario,objective){
      await waitMessage(m=>m.ordinary_scenario===scenario,{action:'ordinary-scenario',project_id:projectId,scenario});
      await project(projectId,'tasks');await panel().getByRole('button',{name:'新建另一条任务草稿',exact:true}).click();
      await panel().getByLabel('任务目标',{exact:true}).fill(objective);await panel().getByLabel('任务范围',{exact:true}).fill('Read registered synthetic inputs only; preserve the original task identity.');
      const [response]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/tasks'&&r.request().method()==='POST'),
        panel().getByRole('button',{name:'发送普通任务',exact:true}).click()]);assert.equal(response.status(),200);
      return {id:(await response.json()).data.task.id,spec:response.request().postDataJSON()};
    }
    async function reopen(entry,expectedStatus,errorClass=null){
      await go('#tasks/'+entry.id,'任务与结果');const actual=await readTask(entry.id);
      assert.equal(actual.status,expectedStatus);assert.deepEqual(actual.task_spec,entry.spec);assert.ok(actual.thread_id);assert.ok(actual.turn_id);
      assert.equal(actual.project,entry.spec.project_id);await page.getByText(entry.spec.objective,{exact:true}).waitFor();
      await page.getByText(entry.spec.scope,{exact:true}).waitFor();assert.match(await main.innerText(),new RegExp(actual.thread_id));
      assert.match(await main.innerText(),new RegExp(actual.turn_id));
      if(errorClass){assert.equal(actual.result.error_class,errorClass);await page.getByText('失败类别：'+errorClass,{exact:true}).waitFor();
        const retry=page.getByRole('button',{name:'重新执行（尚未开放）',exact:true});assert.equal(await retry.isDisabled(),true);
        assert.match(await main.innerText(),/此处不会重新执行/);
      }else{assert.equal(actual.provider_status,'failed');assert.equal(actual.result.provider_status,'failed');assert.match(await main.innerText(),/原生状态 failed/);
        assert.equal(await page.getByLabel('任务执行状态',{exact:true}).innerText(),'失败');
        assert.match(await main.innerText(),/读取同一请求核对结果/);
        assert.equal(actual.result.response,'');await page.getByLabel('普通任务执行回复',{exact:true}).filter({hasText:'执行器返回了空回复。'}).waitFor();
        assert.equal(await page.getByRole('button',{name:'重新执行（尚未开放）',exact:true}).count(),0);}
      return actual;
    }
    const bBefore=(await get('/api/tasks')).tasks.filter(task=>task.project===B);
    const nativeFailed=await send(A,'failed_turn','Synthetic provider failed terminal');await until(nativeFailed.id,task=>task.status==='failed');
    const failed=await reopen(nativeFailed,'failed');await sleep(2200);assert.equal(evidence.tasks.length,1);
    assert.equal((await readTask(nativeFailed.id)).status,'failed');
    assert.deepEqual((await get('/api/tasks')).tasks.filter(task=>task.project===B),bBefore);
    const disconnected=await send(A,'disconnect','Synthetic exact disconnect without redispatch');await until(disconnected.id,task=>task.status==='requires_reconcile');
    const lost=await reopen(disconnected,'requires_reconcile','FIXTURE_CONNECTION_LOST');assert.equal(evidence.tasks.length,2);
    assert.deepEqual((await get('/api/tasks')).tasks.filter(task=>task.project===B),bBefore);
    const timed=await send(B,'delay','Synthetic actual ten second deadline');
    const running=await until(timed.id,task=>task.status==='running'&&task.turn_id);const observedRunning=performance.now();
    const timeout=await until(timed.id,task=>task.status==='requires_reconcile',16000);
    const elapsed=performance.now()-observedRunning;
    const timeoutRecord=await get('/api/tasks/'+timed.id);
    const turnIntent=timeoutRecord.events.find(event=>event.kind==='turn'&&event.data.thread_id===running.thread_id);
    assert.ok(turnIntent&&Number.isFinite(turnIntent.received),'The real worker must record its turn intent');
    const sinceWorkerIntentMs=Date.now()-turnIntent.received*1000;
    assert.ok(sinceWorkerIntentMs>=10000,'A real ten-second deadline must elapse after worker turn intent, without accelerated clocks');
    const expired=await reopen(timed,'requires_reconcile','TASK_RUNTIME_TIMEOUT');assert.equal(evidence.tasks.length,3);
    assert.equal(expired.thread_id,running.thread_id);assert.equal(expired.turn_id,running.turn_id);
    assert.deepEqual((await readTask(nativeFailed.id)).task_spec,failed.task_spec);assert.equal((await readTask(nativeFailed.id)).status,'failed');
    assert.equal((await readTask(disconnected.id)).status,'requires_reconcile');assert.equal((await readTask(disconnected.id)).turn_id,lost.turn_id);
    assert.deepEqual(new Set(evidence.tasks.map(spec=>spec.request_id)),new Set([nativeFailed.id,disconnected.id,timed.id]));
    evidence.states.T12TerminalBranches={nativeFailed:{id:nativeFailed.id,provider_status:failed.provider_status,visibleFailureStatus:true,originalRequestReconcileText:true,
        noRedispatchObservedMs:2200,providerDetailedCause:'not provided; no invented cause'},
      disconnect:{id:disconnected.id,error:lost.result.error_class},deadline:{id:timed.id,error:timeout.result.error_class,observedRunningMs:elapsed,sinceWorkerIntentMs},
      originalIdentitiesPreserved:true,exactlyThreeTaskPosts:true,AFailuresDoNotDispatchB:true,unknownNotAutomaticallyRetried:true};
    await page.screenshot({path:path.join(evidenceRoot,'edge-runtime-timeout.png')});
  }
  assert.deepEqual(diagnostics.consoleErrors,[]);assert.deepEqual(diagnostics.warnings,[]);
  assert.deepEqual(diagnostics.pageErrors,[]);assert.deepEqual(diagnostics.requestFailed,[]);
  // Only successful completion stops polling. Keep its DOM snapshot for any late cleanup diagnostic.
  evidence.finalDOM=await main.innerText();epoch++;await page.goto('about:blank');await sleep(100);
  assert.deepEqual(diagnostics.consoleErrors,[]);assert.deepEqual(diagnostics.warnings,[]);
  assert.deepEqual(diagnostics.pageErrors,[]);assert.deepEqual(diagnostics.requestFailed,[]);
},{directorySize:mode==='source'?103:2});
