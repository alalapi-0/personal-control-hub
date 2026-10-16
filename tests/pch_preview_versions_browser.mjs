// Real isolated HTTP/UI/stores; synthetic owner, provider and saved media.
import assert from 'node:assert/strict';
import path from 'node:path';
import {runBrowserJourney} from './pch_browser_runtime.mjs';

await runBrowserJourney('o51',async({page,context,evidence,evidenceRoot,origin,waitMessage,loginOwner})=>{
  const A='fixture-project-a',B='fixture-project-b';
  const viewport=process.env.PCH_PREVIEW_VIEWPORT==='narrow'?{width:390,height:844}:{width:1440,height:1050};
  await page.setViewportSize(viewport);evidence.viewport=viewport;
  evidence.acceptance={classification:'synthetic_fixture',transport:'mock',realMedia:'unaccepted',
    scope:'HTTP/UI/version bindings and saved synthetic media; no actual capture or business effects'};
  const expectedFailures=new Set(),unexpected=[];
  evidence.expectedErrors=[];evidence.versionRequests=[];
  page.on('request',r=>{
    const u=new URL(r.url());
    if(u.origin===origin&&(u.pathname.startsWith('/api/materials/')||u.pathname.startsWith('/api/tasks/')))
      evidence.versionRequests.push({method:r.method(),path:u.pathname,query:u.search});
  });
  page.on('console',m=>{
    if(m.type()!=='error')return;
    if(/Failed to load resource:.*401/.test(m.text())||expectedFailures.has(m.location().url))
      evidence.expectedErrors.push({text:m.text(),url:m.location().url});
    else unexpected.push(m.text());
  });
  const panel=()=>page.getByRole('region',{name:'普通任务',exact:true});
  const reply=()=>page.getByLabel('普通任务执行回复',{exact:true});
  const materialLink=row=>'#materials/'+row.id+'?version='+row.version;
  async function getTask(id){const r=await context.request.get(origin+'/api/tasks/'+id);assert.equal(r.status(),200);return (await r.json()).data.task;}
  async function completed(id){
    const end=Date.now()+10000;
    while(Date.now()<end){const task=await getTask(id);if(task.status==='checks_complete')return task;await new Promise(r=>setTimeout(r,75));}
    assert.fail('Synthetic task did not complete');
  }
  async function capture(name){await page.bringToFront();assert.equal(await page.evaluate(()=>document.visibilityState),'visible');await page.screenshot({path:path.join(evidenceRoot,name)});}
  async function project(id,section='tasks'){
    await page.goto(origin+'/#projects/'+id+'/'+section);
    await page.getByRole('heading',{name:id===A?'Synthetic Project A':'Synthetic Project B',exact:true}).waitFor();
  }
  async function material(row){await page.goto(origin+'/'+materialLink(row));await page.getByRole('heading',{name:row.artifact_id,exact:true}).waitFor();}
  async function imageLoaded(alt){await page.getByAltText(alt,{exact:true}).waitFor();await page.waitForFunction(a=>{const x=[...document.images].find(i=>i.alt===a);return x?.complete&&x.naturalWidth>0;},alt);}
  async function send(id){
    await project(id);await panel().getByLabel('任务目标',{exact:true}).fill('Synthetic version-bound result for '+id);
    await panel().getByLabel('任务范围',{exact:true}).fill('Read only registered synthetic inputs and return text.');
    const [r]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/tasks'&&r.request().method()==='POST'),panel().getByRole('button',{name:'发送普通任务',exact:true}).click()]);
    assert.equal(r.status(),200);return (await r.json()).data.task.id;
  }
  await waitMessage(m=>m.preview_ready,{action:'preview-sample'});
  await waitMessage(m=>m.ordinary_ready,{action:'ordinary-sample'});
  await project(A);await loginOwner();
  const a=await send(A),b=await send(B);
  await waitMessage(m=>m.ordinary_runtime_started,{action:'ordinary-runtime-start'});
  const taskA=await completed(a),taskB=await completed(b);
  assert.notEqual(taskA.result_version,taskB.result_version);
  for(const task of [taskA,taskB]){
    const unsent='Unsent return draft for '+task.project;
    await project(task.project);
    await panel().getByRole('button',{name:'新建另一条任务草稿',exact:true}).click();
    await panel().getByLabel('任务目标',{exact:true}).fill(unsent);
    await panel().getByLabel('任务范围',{exact:true}).fill('Read only registered synthetic inputs; keep this draft unsent.');
    const [saved]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/ordinary-drafts'&&r.request().method()==='POST'),
      panel().getByRole('button',{name:'保存任务草稿',exact:true}).click()]);assert.equal(saved.status(),200);
    await panel().getByLabel('普通任务状态',{exact:true}).filter({hasText:'已保存'}).waitFor();
    await page.goto(origin+'/#tasks/'+task.id);
    await reply().filter({hasText:'Synthetic read-only response for '+task.project}).waitFor();
    const pin=page.getByRole('link',{name:'本次结果固定链接',exact:true});
    const href=await pin.getAttribute('href');assert.equal(href,'#tasks/'+task.id+'?result_version='+task.result_version);
    await pin.click();await page.waitForURL(origin+'/'+href);await reply().waitFor();await page.reload();
    await reply().filter({hasText:'Synthetic read-only response for '+task.project}).waitFor();
    await page.getByRole('button',{name:'重新对账',exact:true}).click();await reply().waitFor();
    assert.ok(evidence.versionRequests.some(r=>r.path==='/api/tasks/'+task.id&&r.query==='?expected_result_version='+task.result_version));
    assert.equal(await page.getByRole('link',{name:'返回项目任务区',exact:true}).getAttribute('href'),'#projects/'+task.project+'/tasks');
    await page.getByRole('link',{name:'返回项目任务区',exact:true}).click();await panel().waitFor();
    await page.getByRole('heading',{name:task.project===A?'Synthetic Project A':'Synthetic Project B',exact:true}).waitFor();
    assert.equal(await panel().getByLabel('任务目标',{exact:true}).inputValue(),unsent);
  }
  evidence.states.pinnedTaskReopen={A:a,B:b,correctReplies:true,refreshPreservesVersion:true};
  await page.goto(origin+'/#tasks/'+a+'?result_version='+taskA.result_version);await reply().waitFor();
  await capture('o51-pinned-result.png');
  const wrong=origin+'/api/tasks/'+a+'?expected_result_version='+taskB.result_version;
  expectedFailures.add(wrong);
  await page.goto(origin+'/#tasks/'+a+'?result_version='+taskB.result_version);
  await page.getByRole('heading',{name:'固定任务结果已变化',exact:true}).waitFor();
  assert.equal(await reply().count(),0);
  await page.getByRole('button',{name:'重新核对固定结果',exact:true}).click();
  await page.getByRole('heading',{name:'固定任务结果已变化',exact:true}).waitFor();
  await page.getByRole('link',{name:'查看当前任务',exact:true}).click();
  await reply().filter({hasText:'Synthetic read-only response for '+A}).waitFor();
  evidence.states.staleTask={crossTaskVersionRejected:true,noReplacementBody:true,explicitCurrent:true};

  const catalogResponse=await context.request.get(origin+'/api/materials');assert.equal(catalogResponse.status(),200);
  const rows=(await catalogResponse.json()).data.materials;assert.equal(rows.length,6);
  const row=(project,kind)=>rows.find(r=>r.project_id===project&&r.kind===kind);
  await page.goto(origin+'/#materials');
  for(const r of rows)assert.equal(await page.getByRole('link').filter({has:page.getByRole('heading',{name:r.artifact_id,exact:true})}).getAttribute('href'),materialLink(r));
  const imageB=row(B,'image');await material(imageB);await imageLoaded(imageB.artifact_id);
  await page.getByRole('link',{name:'返回项目来源与产物',exact:true}).click();
  await page.waitForURL(origin+'/#projects/'+B+'/sources');await page.getByRole('heading',{name:'Synthetic Project B',exact:true}).waitFor();
  await page.goBack();await imageLoaded(imageB.artifact_id);
  evidence.states.materialReturn={project:B,pinnedHistory:true,correctImage:true,multipleMaterials:6};
  for(const r of rows.filter(r=>['text','html_source'].includes(r.kind))){
    await material(r);
    await page.locator('main pre').filter({hasText:r.kind==='text'?'Synthetic saved report for '+r.project_id:'Synthetic HTML source '+r.project_id}).waitFor();
    assert.equal(await page.evaluate(()=>window.syntheticExecuted),undefined);
    assert.equal(await page.locator('main iframe').count(),0);
  }
  evidence.states.inertLongText={projects:2,htmlEscaped:true,noScriptExecution:true,longArtifactNames:true};

  const old=row(A,'text');const readsBefore=evidence.versionRequests.filter(r=>r.path==='/api/materials/'+old.id).length;
  await page.goto(origin+'/#materials/'+old.id);
  await page.getByRole('link',{name:'打开当前登记版本',exact:true}).waitFor();
  assert.equal(evidence.versionRequests.filter(r=>r.path==='/api/materials/'+old.id).length,readsBefore);
  await page.getByRole('link',{name:'打开当前登记版本',exact:true}).click();
  await page.locator('main pre').filter({hasText:'Synthetic saved report for '+A}).waitFor();
  evidence.states.unversionedMaterial={metadataOnly:true,explicitVersionSelection:true};

  await waitMessage(m=>m.preview_drifted,{action:'preview-drift'});await page.reload();
  await page.getByText('固定产物版本已变化；旧内容无法从当前登记恢复，没有读取新版内容。',{exact:true}).waitFor();
  assert.equal(await page.locator('main pre').count(),0);
  const stale=await context.request.get(origin+old.read_url);assert.equal(stale.status(),409);
  await capture('o51-stale-material.png');
  await page.getByRole('link',{name:'打开当前登记版本',exact:true}).click();
  await page.locator('main pre').filter({hasText:'Synthetic saved report for '+A}).waitFor();
  const currentRows=(await (await context.request.get(origin+'/api/materials')).json()).data.materials;
  const currentImage=currentRows.find(r=>r.project_id===A&&r.kind==='image');
  evidence.states.staleMaterial={oldApi409:true,noSilentCurrentRead:true,explicitCurrent:true};

  await waitMessage(m=>m.preview_invalidated&&m.kind==='material_image',{action:'preview-invalidate',project_id:A,kind:'material_image'});
  expectedFailures.add(origin+currentImage.read_url);await material(currentImage);
  await page.getByText('内容不可播放或登记版本已变化。请重新核对，或查看原审核记录。',{exact:true}).waitFor();
  assert.equal(await page.getByAltText(currentImage.artifact_id,{exact:true}).getAttribute('src'),null);
  const healthy=currentRows.find(r=>r.project_id===B&&r.kind==='image');await material(healthy);await imageLoaded(healthy.artifact_id);
  evidence.states.invalidMaterial={AImageFailed:true,srcRemoved:true,BStillReadable:true};

  await project(A,'sources');const saved=page.getByRole('region',{name:'实际页面预览',exact:true});
  await saved.getByRole('img').scrollIntoViewIfNeeded();await saved.getByText('已保存截图已读取；图像不提供页面操作，也不代表本人验收。',{exact:true}).waitFor();
  assert.ok((await saved.innerText()).includes('历史版本'));assert.ok((await saved.innerText()).includes('实际采集时间：未知'));
  assert.equal(await saved.locator('iframe').count(),0);await capture('o51-saved-history.png');
  const capturePath='/api/projects/'+A+'/captures/'+A+'-saved/image';expectedFailures.add(origin+capturePath);
  await waitMessage(m=>m.preview_invalidated&&m.kind==='saved_capture',{action:'preview-invalidate',project_id:A,kind:'saved_capture'});
  await page.reload();const failed=page.getByRole('region',{name:'实际页面预览',exact:true});
  await failed.scrollIntoViewIfNeeded();await failed.getByText('截图内容不可读取；版本状态无法核实。',{exact:true}).waitFor();
  assert.equal(await failed.locator('img').getAttribute('src'),null);assert.equal(await failed.getByRole('img').count(),0);
  assert.equal(await failed.locator('.image-stage').boundingBox(),null);
  assert.ok((await failed.innerText()).includes('实际采集时间：未知'));
  await failed.getByText('截图内容不可读取；版本状态无法核实。',{exact:true}).scrollIntoViewIfNeeded();
  await capture('o51-failed-capture.png');
  await project(B,'sources');await page.getByRole('region',{name:'实际页面预览',exact:true}).getByRole('img').scrollIntoViewIfNeeded();
  await page.getByText('已保存截图已读取；图像不提供页面操作，也不代表本人验收。',{exact:true}).waitFor();
  evidence.states.savedCapture={historical:true,unknownCaptureTimePreserved:true,failedReadTruthful:true,noPageExecution:true,BStillReadable:true};
  const proof=await waitMessage(m=>'ordinary_control_observations'in m,{action:'ordinary-observations'});assert.equal(proof.effects,0);
  assert.equal(evidence.tasks.length,2);assert.deepEqual(unexpected,[]);assert.deepEqual(evidence.consoleWarnings,[]);
});
