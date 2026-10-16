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
    await editor.waitForFunction(sequence=>Number(document.querySelector('#main').dataset.ledgerSequence)>=sequence,head.sequence,{timeout:10000});
    assert.equal(await date.inputValue(),expectedDraft);
    assert.ok(await editor.evaluate(()=>window.savedEditor===document.querySelector('[aria-label="计划时区（IANA，可留空）"]')));
    assert.equal(await editor.locator('h1').innerText(),'Synthetic Project B');
    const afterScroll=await editor.evaluate(()=>scrollY);
    evidence.states.editorScroll={before:editorScroll,after:afterScroll};
    assert.ok(Math.abs(afterScroll-editorScroll)<3,JSON.stringify({reason:'background source updates retain editor scroll',before:editorScroll,after:afterScroll}));
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
    const first=await source(2,'Synthetic scheduled revision 2');
    const timer=await command('timer-start',{},m=>m.started===true);
    evidence.states.timer=timer;console.log(JSON.stringify({phase:'timer_started',unit:timer.unit_prefix}));
    evidence.states.cycles=[];
    const seen=new Set();
    try{
      for(const n of [2,3,4]){
        const change=n===2?first:await source(n,'Synthetic scheduled revision '+n);
        const started=Date.now();
        await page.getByText('Synthetic scheduled revision '+n,{exact:true}).waitFor({timeout:85000});
        let status,invocation;
        const completionDeadline=Date.parse(change.updated_at)+90000;
        do{
          status=await command('timer-status',{},m=>m.configured===true&&Array.isArray(m.invocations));
          assert.equal(status.systemctl_exit,0);assert.equal(status.journal_exit,0);
          invocation=status.invocations.find(v=>v.result_readable&&!seen.has(v.invocation_id));
          if(!invocation)await new Promise(resolve=>setTimeout(resolve,250));
        }while(!invocation&&Date.now()<completionDeadline);
        assert.ok(invocation,JSON.stringify({reason:status.journal_reason,invocations:status.invocations}));
        await page.waitForFunction(sequence=>Number(document.querySelector('#main').dataset.ledgerSequence)>=sequence,
          invocation.head.sequence,{timeout:Math.max(1,completionDeadline-Date.now())});
        const current=await readA();
        assert.equal(current.business.fields.progress.completed,n);
        assert.ok(current.source.sources.some(s=>s.sha256===change.new_sha256));
        assert.equal(await main.getByText(n+' / 7',{exact:true}).innerText(),n+' / 7');
        assert.deepEqual(current.provenance.ledger_head,invocation.head);
        assert.equal(await main.getAttribute('data-ledger-sequence'),String(invocation.head.sequence));
        const elapsed=Date.now()-Date.parse(change.updated_at);
        assert.ok(elapsed>=0&&elapsed<=90000,'stable source reaches matching API/DOM head within original90s target');
        assert.ok(invocation.request_id&&invocation.pid);seen.add(invocation.invocation_id);
        assert.ok(status.units.some(u=>u.Id===timer.timer&&u.LastTriggerUSec&&u.LastTriggerUSec!=='n/a'));
        evidence.states.cycles.push({source:{old:change.old_sha256,new:change.new_sha256,updated_at:change.updated_at},
          apiValue:n,domValue:n+' / 7',head:current.provenance.ledger_head,elapsed_ms:elapsed,
          invocation,timerStatus:status.units,observed_ms:Date.now()-started});
        await assertEditor(current.provenance.ledger_head);
        console.log(JSON.stringify({phase:'automatic_cycle',cycle:n-1,elapsed_ms:elapsed,sequence:current.provenance.ledger_head.sequence}));
      }
      assert.equal(seen.size,3);evidence.states.originalFreshnessTargets={incremental_seconds:60,visible_head_seconds:5,end_to_end_seconds:90,passed:true};
    }finally{
      const stopped=await command('timer-stop',{},m=>m.stopped===true);evidence.states.timerCleanup=stopped;
    }
  }
  assert.equal(evidence.tasks.length,0,'source collection does not dispatch provider tasks');
  evidence.states.editorPreserved={project:B,value:expectedDraft,sameNode:true,scroll:true};
  evidence.states.headRequests=evidence.network.filter(r=>r.path==='/api/ledger-head').length;
  assert.ok(evidence.states.headRequests>=1);
  await page.screenshot({path:path.join(evidenceRoot,'o2-updated.png')});
  await writeFile(path.join(evidenceRoot,'o2-dom.txt'),await main.innerText());
  await editor.close();
},{directorySize:39});
