// Root explicitly opts into real owned timer effects with PCH_REAL_TIMER=1.
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';
import {runBrowserJourney} from './pch_browser_runtime.mjs';

await runBrowserJourney('o2',async({page,context,evidence,evidenceRoot,origin,waitMessage,loginOwner})=>{
  const A='fixture-project-a',B='fixture-project-b';
  const command=(action,fields,test)=>waitMessage(test,{action,...fields});
  const source=(n,objective)=>command('source',{project_id:A,completed:n,objective},m=>m.collected===false);
  const main=page.locator('#main');
  const readA=async()=>{const r=await context.request.get(origin+'/api/projects/'+A);assert.equal(r.status(),200);return (await r.json()).data;};
  await page.goto(origin+'/#projects/'+A);await page.getByRole('heading',{name:'这是什么项目',exact:true}).waitFor();
  await loginOwner();
  await command('runtime',{worker_runtime:false,provider:false},m=>m.runtime_updated===true);
  const before=await readA();assert.equal(before.business.fields.progress.completed,0);
  const editor=await context.newPage();editor.setDefaultTimeout(5000);
  const editorHeadRequests=[],editorHeadResponses=[];
  editor.on('request',request=>{
    if(request.url()===origin+'/api/ledger-head')editorHeadRequests.push({method:request.method(),path:'/api/ledger-head'});
  });
  editor.on('response',response=>{
    if(response.url()===origin+'/api/ledger-head')editorHeadResponses.push({status:response.status(),path:'/api/ledger-head'});
  });
  editor.on('pageerror',error=>evidence.console.push(String(error)));
  await editor.goto(origin+'/#projects/'+B+'/schedule');
  const date=editor.getByRole('textbox',{name:'计划时区（IANA，可留空）',exact:true});
  await date.waitFor();await date.fill('Asia/Tokyo');await date.focus();
  await editor.evaluate(()=>{window.savedEditor=document.querySelector('[aria-label="计划时区（IANA，可留空）"]');});
  let editorScroll=await editor.evaluate(()=>scrollY);
  let expectedDraft='Asia/Tokyo';
  await page.bringToFront();
  const mutation=await source(1,'Synthetic manual source revision');
  assert.notEqual(mutation.old_sha256,mutation.new_sha256);
  const held=await command('hold-batch',{},m=>m.batch_held===true);
  assert.ok(held.pid&&held.start_ticks);evidence.states.batchHolder=held;
  const [busy]=await Promise.all([
    page.waitForResponse(r=>r.url()===origin+'/api/refresh'&&r.request().method()==='POST'),
    page.getByRole('button',{name:'刷新此项目',exact:true}).click(),
  ]);assert.equal(busy.status(),409);
  const busyBody=await busy.json();assert.equal(busyBody.error.code,'REFRESH_BATCH_BUSY');
  assert.equal(busyBody.error.outcome,'NOT_COMMITTED');
  await page.getByText('另一个刷新批次正在运行。已保留原请求，可稍后重试。',{exact:false}).waitFor();
  const retained=JSON.parse(await page.evaluate(()=>localStorage.getItem('hub:refresh-pending')));
  assert.deepEqual((await readA()).provenance.ledger_head,before.provenance.ledger_head);
  await command('release-batch',{},m=>m.batch_released===true);
  const [retried]=await Promise.all([
    page.waitForResponse(r=>r.url()===origin+'/api/refresh'&&r.request().method()==='POST'),
    page.getByRole('button',{name:'重试原刷新',exact:true}).click(),
  ]);assert.equal(retried.status(),200);
  assert.equal(retried.request().postDataJSON().request_id,retained.request_id);
  await page.getByText('Synthetic manual source revision',{exact:true}).waitFor();
  evidence.states.busyRecovery={request_id:retained.request_id,busy:409,notCommitted:true,sameRequest:true,recovered:200};

  async function assertEditor(head){
    await editor.bringToFront();
    const check=editor.getByRole('button',{name:'核对已存快照',exact:true});
    const preActionViewport=await editor.evaluate(()=>({x:scrollX,y:scrollY}));
    // Playwright scrolls this toolbar into view before pointerdown. Capture the
    // resulting user-action viewport before the click handler starts reconciliation.
    await check.evaluate(button=>{
      window.storedSnapshotPointerdown=null;
      button.addEventListener('pointerdown',()=>{
        window.storedSnapshotPointerdown={x:scrollX,y:scrollY};
      },{capture:true,once:true});
    });
    const [explicitHeadResponse]=await Promise.all([
      editor.waitForResponse(response=>response.url()===origin+'/api/ledger-head'&&response.request().method()==='GET'),
      check.click(),
    ]);
    assert.equal(explicitHeadResponse.status(),200,'editor explicit stored-snapshot check reads the existing ledger head');
    evidence.states.explicitStoredSnapshotCheck={page:'editor',method:'GET',path:'/api/ledger-head',status:explicitHeadResponse.status()};
    const pointerdownViewport=await editor.evaluate(()=>window.storedSnapshotPointerdown);
    assert.ok(pointerdownViewport,'the explicit stored-snapshot check received pointerdown');
    await editor.waitForFunction(sequence=>Number(document.querySelector('#main').dataset.ledgerSequence)>=sequence,head.sequence,{timeout:10000});
    assert.equal(await date.inputValue(),expectedDraft);
    assert.ok(await editor.evaluate(()=>window.savedEditor===document.querySelector('[aria-label="计划时区（IANA，可留空）"]')));
    assert.equal(await editor.locator('h1').innerText(),'Synthetic Project B');
    const finalViewport=await editor.evaluate(()=>({x:scrollX,y:scrollY}));
    evidence.states.editorScroll={before:editorScroll,preAction:preActionViewport,
      pointerdown:pointerdownViewport,final:finalViewport,
      wake:'explicit stored snapshot check',intentionalToolbarNavigation:true};
    assert.ok(Math.abs(finalViewport.y-pointerdownViewport.y)<3&&Math.abs(finalViewport.x-pointerdownViewport.x)<3,
      JSON.stringify({reason:'snapshot reconciliation preserves the viewport after intentional toolbar navigation',
        preAction:preActionViewport,pointerdown:pointerdownViewport,final:finalViewport}));
    await page.bringToFront();
  }
  const manual=await readA();assert.equal(manual.business.fields.progress.completed,1);
  assert.ok(manual.source.sources.some(s=>s.sha256===mutation.new_sha256));
  await assertEditor(manual.provenance.ledger_head);
  evidence.states.manualSource={old:mutation.old_sha256,new:mutation.new_sha256,head:manual.provenance.ledger_head,api:1,dom:'1 / 7',providerDisabled:true};
  await editor.bringToFront();
  await editor.getByLabel('开始日期',{exact:true}).fill('2026-10-18');
  await editor.getByLabel('结束日期',{exact:true}).fill('2026-10-20');
  await editor.getByRole('button',{name:'保存配置',exact:true}).click();
  const timeline=editor.locator('[data-live-project-timeline]');
  await timeline.getByRole('button',{name:/定位 .*2026-10-18/}).first().waitFor();
  await timeline.getByRole('button',{name:/定位 .*2026-10-20/}).first().waitFor();
  assert.deepEqual((await readA()).provenance.ledger_head,manual.provenance.ledger_head,'management dates do not advance source ledger');
  evidence.states.saveAfterReconcile={start:'2026-10-18',end:'2026-10-20',currentTimeline:true,sourceHeadUnchanged:true};
  expectedDraft='Europe/London';await date.fill(expectedDraft);await date.focus();
  editorScroll=await editor.evaluate(()=>scrollY);
  await page.bringToFront();

  if(process.env.PCH_REAL_TIMER==='1'){
    const timer=await command('timer-start',{},m=>m.started===true);
    evidence.states.timer=timer;
    try{
      assert.ok([12,24].includes(timer.interval_hours));
      assert.equal(timer.expected_interval_seconds,timer.interval_hours*3600);
      const status=await command('timer-status',{},m=>m.configured===true&&Array.isArray(m.units));
      assert.equal(status.systemctl_exit,0);
      const observed=status.units.find(unit=>unit.Id===timer.timer);
      assert.ok(observed,'exact owned timer observed');
      const calendars=observed.TimersCalendar;
      assert.ok(Array.isArray(calendars),'actual calendars were observed');
      assert.equal(calendars.length,timer.interval_hours===12?2:1);
      assert.ok(calendars.some(value=>value.includes('09:00:00')&&value.includes('Asia/Tokyo')));
      if(timer.interval_hours===12)assert.ok(calendars.some(value=>value.includes('21:00:00')&&value.includes('Asia/Tokyo')));
      assert.ok(calendars.every(value=>!value.includes('*:*:00')),'no minute trigger');
      assert.equal(observed.ActiveState,'active');
      assert.ok(observed.NextElapseUSecRealtime&&!['n/a','0'].includes(observed.NextElapseUSecRealtime),'next planned trigger observed');
      // Persistent catch-up on start is distinct from waiting for the next calendar boundary.
      evidence.states.snapshotCadence={interval_hours:timer.interval_hours,actualCalendar:calendars,
        nextPlanned:observed.NextElapseUSecRealtime,natural_wait_verification:'UNVERIFIED',
        basis:'effective owned timer configuration and next plan; no natural boundary wait',
        invocationsObserved:status.invocations.length};
    }finally{
      const stopped=await command('timer-stop',{},m=>m.stopped===true);evidence.states.timerCleanup=stopped;
    }
  }
  assert.equal(evidence.tasks.length,0,'source collection does not dispatch provider tasks');
  evidence.states.editorPreserved={project:B,value:expectedDraft,sameNode:true,scroll:true};
  evidence.states.headRequests={primary:evidence.network.filter(r=>r.path==='/api/ledger-head').length,
    editor:editorHeadRequests.length,editorResponses:editorHeadResponses};
  assert.ok(evidence.states.headRequests.editor>=1,'the editor explicit stored-snapshot check issued a ledger-head request');
  assert.ok(editorHeadResponses.some(response=>response.status===200),'the editor observed a successful ledger-head response');
  await page.screenshot({path:path.join(evidenceRoot,'o2-updated.png')});
  const snapshotFields=main.locator('.project-summary');
  await snapshotFields.getByText('最后成功快照：',{exact:false}).waitFor();
  await snapshotFields.screenshot({path:path.join(evidenceRoot,'o2-snapshot-fields.png')});
  await writeFile(path.join(evidenceRoot,'o2-dom.txt'),await main.innerText());
  await editor.close();
},{directorySize:39});
