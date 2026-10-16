// O0 product journey against Root's owned browser and disposable complete app.
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';
import {runBrowserJourney} from './pch_browser_runtime.mjs';

await runBrowserJourney('o0',async({page,evidence,evidenceRoot,origin,waitMessage,loginOwner})=>{
  async function preview(index){
    await page.goto(origin+'/#workbench');
    await page.getByRole('link',{name:'查看并批注',exact:true}).nth(index).click();
    await page.getByRole('button',{name:'发送给 Codex',exact:true}).waitFor();
  }
  await preview(0);
  const send=()=>page.getByRole('button',{name:'发送给 Codex',exact:true});
  evidence.states.guestDisabled=await send().isDisabled();assert.equal(evidence.states.guestDisabled,true);
  await loginOwner();
  evidence.states.ownerEnabled=await send().isEnabled();assert.equal(evidence.states.ownerEnabled,true);
  const image=page.locator('#main img');
  await image.evaluate(img=>img.decode());
  assert.equal(await image.evaluate(img=>img.naturalWidth),32);
  for(const [index,project] of [[0,'fixture-project-a'],[1,'fixture-project-b']]){
    if(index)await preview(index);
    await send().click();
    await page.waitForURL('**/#tasks/'+project+'-task');
    await page.getByText('已接收 · 排队中',{exact:true}).waitFor();
    await waitMessage(m=>m.ran===true,{action:'run',project_id:project});
    await page.reload();
    await page.getByText('检查完成 · 等待验收',{exact:true}).waitFor();
    const body=await page.locator('#main').innerText();
    assert.ok(body.includes('thread-'+project));
    assert.ok(!body.includes('thread-fixture-project-'+(index?'a':'b')));
    await writeFile(path.join(evidenceRoot,project+'-dom.txt'),body);
  }
  assert.equal(evidence.tasks.length,2);
  await preview(0);
  for(const [changes,label,key] of [
    [{worker_runtime:false},'任务执行器尚未就绪','workerDisabled'],
    [{worker_runtime:true,provider:false},'任务通道尚未就绪','providerDisabled']]){
    await waitMessage(m=>m.runtime_updated,{action:'runtime',...changes});
    await page.reload();await page.getByText(label,{exact:true}).waitFor();
    evidence.states[key]=await send().isDisabled();assert.equal(evidence.states[key],true);
  }
  await waitMessage(m=>m.runtime_updated,{action:'runtime',provider:true});
  await page.reload();await page.getByRole('button',{name:'断开所有者',exact:true}).waitFor();
  evidence.states.recovered=await send().isEnabled();assert.equal(evidence.states.recovered,true);
  await page.goto(origin+'/#projects');
  await page.locator('#search-projects').waitFor();
  await page.locator('summary').filter({hasText:'来源同步与读取资格'}).click();
  await page.getByRole('heading',{name:'项目来源同步',exact:true}).waitFor();
  assert.ok(evidence.network.some(r=>r.path==='/api/sync-status'&&r.status===200));
  await page.screenshot({path:path.join(evidenceRoot,'o0-candidate.png')});
  assert.deepEqual(evidence.network.filter(r=>r.status>=400),[]);
});
