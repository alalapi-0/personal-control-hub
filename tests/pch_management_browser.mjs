// Root starts the disposable browser. This journey controls synthetic HTTP only.
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';
import {runBrowserJourney} from './pch_browser_runtime.mjs';

await runBrowserJourney('o3',async({page,context,evidence,evidenceRoot,origin:initialOrigin,waitMessage,loginOwner})=>{
  let origin=initialOrigin;
  const A='fixture-project-a',B='fixture-project-b';
  const sample=await waitMessage(m=>m.management_ready===true,{action:'management-sample'});
  assert.equal(sample.blocked_project,B);assert.equal(sample.proposals.length,1);
  const panel=target=>target.getByRole('region',{name:'Hub 管理配置',exact:true});
  const field=(target,label)=>panel(target).getByLabel(label,{exact:true});
  const saveButton=target=>panel(target).getByRole('button',{name:'保存配置',exact:true});
  const status=target=>panel(target).getByLabel('管理配置保存状态',{exact:true});
  async function open(target,id,section='schedule'){
    const destination=origin+'/#projects/'+id+(section==='overview'?'':'/'+section);
    if(target.url()===destination)await target.reload();else await target.goto(destination);
    if(section==='overview')await target.getByRole('heading',{name:'这是什么项目',exact:true}).waitFor();
    else await panel(target).waitFor();
    await target.waitForFunction(()=>!document.querySelector('#main')?.hasAttribute('aria-busy'));
  }
  async function read(id){
    const response=await context.request.get(origin+'/api/projects/'+id);assert.equal(response.status(),200);
    return (await response.json()).data;
  }
  async function save(target,expected=200,action=null){
    const [response]=await Promise.all([target.waitForResponse(r=>new URL(r.url()).pathname==='/api/management/project'&&r.request().method()==='POST'),
      (action??saveButton(target)).click()]);assert.equal(response.status(),expected);const data=await response.json();
    if(expected===200)await status(target).filter({hasText:'已保存'}).waitFor();
    return {response,data:data.data??data,command:response.request().postDataJSON()};
  }
  function relationPanel(target){return target.getByRole('region',{name:'项目关系',exact:true});}
  await open(page,A);await loginOwner();
  const sourceBefore=(await read(A)).business.fields;
  // Real controls publish owner relations; reversed date errors retain exact input.
  await field(page,'关系配置').selectOption('confirmed');
  await field(page,'依赖项目（可多选）').selectOption([B]);
  await field(page,'关联项目（可多选）').selectOption([B]);
  await field(page,'开始日期').fill('2026-12-31');
  await field(page,'结束日期').fill('2026-12-30');
  const end=field(page,'结束日期');
  assert.equal(await end.getAttribute('aria-invalid'),'true');
  const description=await end.getAttribute('aria-describedby');assert.ok(description);
  assert.match(await page.locator('[id="'+description+'"]').innerText(),/结束|开始|日期/);
  assert.equal(await field(page,'开始日期').inputValue(),'2026-12-31');
  assert.equal(await end.inputValue(),'2026-12-30');assert.equal(await saveButton(page).isDisabled(),true);
  await end.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(evidenceRoot,'o3-date-error.png')});
  await end.fill('2027-01-02');
  await field(page,'计划时区（IANA，可留空）').fill('Asia/Tokyo');
  assert.equal(await end.getAttribute('aria-invalid'),'false');
  const initial=await save(page);assert.equal(initial.data.receipt.committed_revision,1);
  const configured=(await read(A)).management.overlay;
  assert.deepEqual(configured.relations.depends_on,[B]);
  assert.deepEqual((await read(A)).business.fields,sourceBefore);
  evidence.states.dateRecovery={invalidAria:true,preserved:true,correctedSaved:true,crossYear:true};
  for(const section of ['overview','schedule']){
    await open(page,A,section);
    const relations=relationPanel(page);await relations.waitFor();
    assert.match(await relations.innerText(),/依赖项目/);
    assert.match(await relations.innerText(),/Synthetic Project B/);
    assert.match(await relations.innerText(),/依赖影响（派生）|需要复核排期/);
    if(section==='schedule'){await relations.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(evidenceRoot,'o3-relations.png')});}
  }
  await relationPanel(page).getByRole('link',{name:'Synthetic Project B',exact:true}).first().click();
  await page.waitForURL(/#projects\/fixture-project-b(?:\/schedule)?$/);
  await page.getByRole('heading',{name:'Synthetic Project B',exact:true}).waitFor();
  await relationPanel(page).waitFor();
  assert.match(await relationPanel(page).innerText(),/被以下项目依赖/);
  assert.match(await relationPanel(page).innerText(),/Synthetic Project A/);
  await page.goBack();await page.getByRole('heading',{name:'Synthetic Project A',exact:true}).waitFor();await relationPanel(page).waitFor();
  assert.match(page.url(),/#projects\/fixture-project-a/);
  evidence.states.confirmedForwardReverse={overview:true,schedule:true,clickTarget:true,back:true,blockedImpact:true};

  // Two real app pages keep separate drafts and use the server's CAS fence.
  await open(page,A);
  const editor=await context.newPage();editor.setDefaultTimeout(5000);
  editor.on('pageerror',error=>evidence.console.push(String(error)));
  await open(editor,A);
  await field(editor,'优先级（数值）').fill('2');
  await field(page,'优先级（数值）').fill('1');await save(page);
  const conflict=await save(editor,409);
  assert.equal(conflict.data.error.code,'MANAGEMENT_CONFLICT');
  await status(editor).filter({hasText:'配置版本已变化'}).waitFor();
  assert.equal(await field(editor,'优先级（数值）').inputValue(),'2');
  assert.equal(await saveButton(editor).isDisabled(),true);
  await panel(editor).getByRole('button',{name:'重新读取当前版本',exact:true}).click();
  await status(editor).filter({hasText:'已重新读取当前版本'}).waitFor();
  assert.equal(await field(editor,'优先级（数值）').inputValue(),'2');
  assert.equal((await read(A)).management.overlay.priority,1);
  await save(editor);assert.equal((await read(A)).management.overlay.priority,2);
  evidence.states.twoEditors={conflict:409,draftRetained:true,reloadedWithoutOverwrite:true,explicitApply:true};
  await editor.close();

  await open(page,A);
  await panel(page).getByRole('button',{name:'撤销本项目关系',exact:true}).click();
  assert.deepEqual((await read(A)).management.overlay.relations.depends_on,[B]);
  await save(page);
  const cleared=(await read(A)).management.overlay;
  assert.equal(cleared.relations,null);assert.equal(cleared.start_date,configured.start_date);assert.equal(cleared.end_date,configured.end_date);
  assert.equal(cleared.priority,2);
  evidence.states.relationsOnlyRetraction={draftOnlyUntilSave:true,datesPreserved:true,priorityPreserved:true};

  const proposal=panel(page).getByRole('region',{name:'关系提议确认',exact:true});
  const accepted=await save(page,200,proposal.getByRole('button',{name:'按相关项目确认 · '+sample.proposal_id,exact:true}));
  assert.deepEqual(accepted.command.proposal_ref,sample.proposals[0].proposal_ref);
  let current=await read(A);
  assert.deepEqual(current.management.overlay.relations.related_to,[B]);
  assert.deepEqual(current.management.history_recent.at(-1).proposal_ref,sample.proposals[0].proposal_ref);
  assert.equal(evidence.tasks.length,0);
  assert.deepEqual(current.business.fields,sourceBefore);
  const history=panel(page).getByRole('region',{name:'管理配置历史',exact:true});
  await history.getByRole('button',{name:'恢复为版本 1 的配置',exact:true}).click();
  assert.equal((await read(A)).management.overlay.priority,2);
  await save(page);
  current=await read(A);assert.deepEqual(current.management.overlay,configured);
  evidence.states.proposalAndHistory={exactRef:true,ownerAccepted:true,noTaskDispatch:true,restoreDraftThenExplicitSave:true};

  const calendarCases=[{start:null,end:null,zone:'Asia/Tokyo'},
    {start:'2026-10-31',end:null,zone:'Asia/Tokyo'},
    {start:null,end:'2026-11-01',zone:'America/New_York'},
    {start:'2026-11-01',end:'2026-11-02',zone:'America/New_York'}];
  for(const item of calendarCases){
    // Clear the other endpoint first so every intermediate draft is intentional.
    await field(page,'开始日期').fill('');await field(page,'结束日期').fill('');
    await field(page,'开始日期').fill(item.start||'');await field(page,'结束日期').fill(item.end||'');
    await field(page,'计划时区（IANA，可留空）').fill(item.zone);await save(page);
    const actual=(await read(A)).management.overlay;
    assert.equal(actual.start_date,item.start);assert.equal(actual.end_date,item.end);assert.equal(actual.timezone,item.zone);
  }
  await field(page,'计划里程碑配置').selectOption('configured');
  await panel(page).getByRole('button',{name:'添加计划里程碑',exact:true}).click();
  const milestoneName=panel(page).getByLabel(/^计划里程碑名称/),milestoneDate=panel(page).getByLabel(/^计划里程碑日期/);
  await milestoneName.fill('');assert.equal(await milestoneName.getAttribute('aria-invalid'),'true');
  await milestoneName.fill('Synthetic review milestone');await milestoneDate.fill('2026-11-01');await save(page);
  current=await read(A);assert.equal(current.management.overlay.milestones[0].date,'2026-11-01');
  const timeline=page.getByRole('region',{name:'当前项目时间线',exact:true});
  const combinations=[];
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:1050});
    for(const scale of ['day','week','month'])for(const filter of ['all','dated','unscheduled']){
      await timeline.getByLabel('时间线缩放',{exact:true}).selectOption(scale);
      await timeline.getByLabel('时间线日期筛选',{exact:true}).selectOption(filter);
      const text=await timeline.innerText();assert.match(text,/当前项目日期筛选显示/);
      if(filter!=='unscheduled')assert.match(text,/Synthetic Project A|Synthetic review milestone/);
      const box=await timeline.boundingBox();assert.ok(box.width<=width,'timeline stays within viewport');
      combinations.push({width,scale,filter});
    }
  }
  await timeline.getByLabel('时间线日期筛选',{exact:true}).selectOption('all');
  await timeline.scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(evidenceRoot,'o3-calendar-narrow.png')});
  const graph=timeline.getByRole('region',{name:/日期图；左右方向键切换日期窗口/});
  await graph.focus();const oldWindow=await timeline.getByRole('status').innerText();await graph.press('ArrowRight');
  assert.notEqual(await timeline.getByRole('status').innerText(),oldWindow);
  assert.equal(await graph.evaluate(node=>node===document.activeElement),true);
  await page.setViewportSize({width:1440,height:1050});
  await history.getByRole('button',{name:'恢复为版本 1 的配置',exact:true}).click();await save(page);
  assert.deepEqual((await read(A)).management.overlay,configured);
  evidence.states.calendarAndTimeline={calendarCases,partialDates:true,milestone:true,combinations,keyboardFocusPreserved:true};

  const mutation=await waitMessage(m=>m.collected===false,{action:'source',project_id:A,completed:1,objective:'Synthetic O3 reimport'});
  const collected=await waitMessage(m=>m.exit_code!==undefined,{action:'sync',only_project_id:A});assert.equal(collected.exit_code,0);
  current=await read(A);assert.deepEqual(current.management.overlay,configured);
  assert.equal(current.business.fields.current_work.objective,mutation.source.objective);
  const beforeRestart={overlay:current.management.overlay,revision:current.management.revision,head:current.provenance.ledger_head};
  const restarted=await waitMessage(m=>m.restarted===true,{action:'restart'});
  assert.notEqual(restarted.backend_instance,restarted.previous_backend_instance);origin=restarted.origin;
  await open(page,A);await loginOwner();
  current=await read(A);
  assert.deepEqual(current.management.overlay,beforeRestart.overlay);
  assert.equal(current.management.revision,beforeRestart.revision);
  assert.deepEqual(current.provenance.ledger_head,beforeRestart.head);
  assert.ok(current.management.history_recent.length>=6);
  evidence.states.importAndRestart={sourceChanged:true,managementPreserved:true,historyPreserved:true,newBackendInstance:true,ownerReauthenticated:true};
  await page.screenshot({path:path.join(evidenceRoot,'o3-management.png')});
  await writeFile(path.join(evidenceRoot,'o3-management-final.json'),JSON.stringify({overlay:current.management.overlay,revision:current.management.revision,history:current.management.history_recent},null,2));
});
