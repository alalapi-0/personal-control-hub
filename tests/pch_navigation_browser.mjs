// Real app/static assets + HTTP fixture; only Root starts the owned browser.
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';
import {runBrowserJourney,within} from './pch_browser_runtime.mjs';

await runBrowserJourney('o1',async({page,context,evidence,evidenceRoot,origin})=>{
  await page.addInitScript(()=>{
    window.historyWrites=[];
    const original=history.replaceState;
    history.replaceState=function(...args){window.historyWrites.push(performance.now());return original.apply(this,args);};
  });
  const A='fixture-project-a',B='fixture-project-b';
  const title=id=>id===A?'Synthetic Project A':id===B?'Synthetic Project B':id;
  const main=page.locator('#main'), search=()=>page.locator('#search-projects');
  async function go(hash){
    if(page.url()===new URL(origin+'/'+hash).href){
      await page.waitForFunction(()=>!document.querySelector('#main')?.hasAttribute('aria-busy'));
      return;
    }
    const ident=hash.split('?')[0].split('/')[1];
    const expectedPath=ident?'/api/projects/'+encodeURIComponent(ident):'/api/projects';
    const response=page.waitForResponse(r=>new URL(r.url()).pathname===expectedPath);
    await page.goto(origin+'/'+hash);
    await response;
    await page.waitForFunction(()=>!document.querySelector('#main')?.hasAttribute('aria-busy'));
  }
  const heading=(name)=>page.getByRole('heading',{name,exact:true});
  const tick=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const setControl=async(selector,value)=>{
    const control=page.locator(selector);
    if(await control.inputValue()===value)return;
    if(selector==='#search-projects')await control.fill(value);
    else await control.selectOption(value);
  };
  async function assertFront(locator,height){
    const box=await locator.boundingBox();
    assert.ok(box&&box.y>=0&&box.y+box.height<=height,JSON.stringify({box,height}));
  }
  evidence.states.firstScreen=[];
  for(const [width,height] of [[1280,800],[1440,1050]]){
    await page.setViewportSize({width,height});await go('#projects');
    assert.equal(await page.locator('#view-projects').inputValue(),'list');
    await assertFront(search(),height);
    await assertFront(main.locator('a[data-project-id]').first(),height);
    assert.equal(await main.locator('.timeline-project').count(),0);
    assert.equal(await main.locator('details').filter({has:page.locator('summary').filter({hasText:'来源同步与读取资格'})}).getAttribute('open'),null);
    await page.screenshot({path:path.join(evidenceRoot,`o1-first-screen-${width}.png`)});
    evidence.states.firstScreen.push({width,height,search:true,projectEntry:true,defaultView:'list'});
  }
  // Both HTTP pages must be fetched by the product, not reconstructed in a test.
  const extraIds=Array.from({length:101},(_,i)=>`fixture-directory-${String(i+2).padStart(3,'0')}`);
  const ids=[A,B,...extraIds].sort();
  assert.equal(await main.locator('a[data-project-id]').count(),103);
  assert.ok(evidence.network.filter(r=>r.path==='/api/projects'&&r.status===200).length>=2);
  const sourceData=[];
  for(const offset of [0,100]){
    const response=await context.request.get(origin+`/api/projects?offset=${offset}&limit=100`);
    assert.equal(response.status(),200);const payload=await response.json();
    assert.equal(payload.data.total,103);sourceData.push(...payload.data.projects);
  }
  assert.deepEqual(sourceData.map(p=>p.project_id),ids);
  evidence.states.realPagination={pages:[100,3],total:103};
  evidence.states.directoryMatrix=[];
  const cases=[
    {q:'',status:'',freshness:'',expected:ids},
    {q:'合成甲',status:'',freshness:'',expected:[A]},
    {q:'reusable asset',status:'complete',freshness:'fresh',expected:[A]},
    {q:'fixture-directory-',status:'complete',freshness:'fresh',expected:extraIds},
    {q:'三十天',status:'',freshness:'',expected:[]},
    {q:'',status:'active',freshness:'',expected:[]},
    {q:'',status:'',freshness:'attention',expected:[]},
  ];
  for(const group of ['none','status','type','freshness'])for(const view of ['list','tree','timeline'])for(const item of cases){
    await setControl('#search-projects',item.q);
    await setControl('#status-filter',item.status);
    await setControl('#fresh-filter',item.freshness);
    await setControl('#group-projects',group);
    await setControl('#view-projects',view);
    const selector={list:'a[data-project-id]',tree:'details[data-project-id]',timeline:'article[data-project-id]'}[view];
    const actual=await main.locator('[aria-label="项目目录视图"]').locator(selector).evaluateAll(nodes=>nodes.map(n=>n.dataset.projectId));
    assert.equal(new Set(actual).size,actual.length,'no duplicate directory identities');
    assert.deepEqual([...actual].sort(),[...item.expected].sort(),JSON.stringify({group,view,...item}));
    if(item.q==='合成甲'&&view==='list')assert.match(await main.innerText(),/匹配：.*已确认别名「合成甲」/);
    if(item.q==='reusable asset'&&view==='list')assert.match(await main.innerText(),/匹配：.*用途/);
    evidence.states.directoryMatrix.push({query:item.q,status:item.status,source:item.freshness,group,view,count:actual.length});
  }
  await page.waitForFunction(()=>{
    const query=new URLSearchParams(location.hash.split('?')[1]);
    return query.get('freshness')==='attention'&&query.get('group')==='freshness'&&query.get('view')==='timeline';
  });
  evidence.states.directoryHistoryWrites=await page.evaluate(()=>window.historyWrites.length);
  assert.ok(evidence.states.directoryHistoryWrites>0,'final filters persist in the shareable URL');
  assert.ok(evidence.states.directoryHistoryWrites<100,'rapid filtering coalesces history persistence');
  assert.ok(!evidence.consoleWarnings.some(text=>/Throttling navigation/.test(text)));
  await search().fill('三十天');
  await page.getByRole('button',{name:'清除筛选',exact:true}).click();
  assert.equal(await search().inputValue(),'');assert.equal(await page.locator('#status-filter').inputValue(),'');
  assert.equal(await page.locator('#fresh-filter').inputValue(),'');

  // Source-backed definition and known zero remain distinct from missing totals.
  for(const id of [A,B]){
    await go('#projects/'+id);
    await heading(title(id)).waitFor();
    const intro=main.getByRole('region',{name:'项目速览',exact:true});
    assert.match(await intro.innerText(),/项目用途：/);
    assert.match(await intro.innerText(),/工作状态：/);
    const definitionToggle=main.locator('.project-definition-disclosure');
    assert.equal(await definitionToggle.getAttribute('open'),null,'Full definition starts collapsed');
    await definitionToggle.locator(':scope > summary').press('Enter');
    assert.notEqual(await definitionToggle.getAttribute('open'),null,'Keyboard opens complete source-backed definition');
    const definition=main.locator('[aria-label="项目定义"]');
    const text=await definition.innerText();
    if(id===A){assert.match(text,/Synthetic reusable asset production/);assert.match(text,/合成甲/);assert.match(text,/Editable synthetic asset/);}
    else{assert.match(text,/来源未提供/);assert.ok(!text.includes('合成甲'));}
    const progress=main.locator('.project-summary dl > div').filter({has:page.locator('dt').getByText('业务进度 V/N',{exact:true})});
    assert.equal(await progress.locator('dd').innerText(),id===A?'0 / 7':'0 / 未知');
    await writeFile(path.join(evidenceRoot,id+'-overview-dom.txt'),await main.innerText());
  }
  evidence.states.definitions={knownA:true,missingB:true,zeroKnown:'0 / 7',zeroUnknown:'0 / 未知'};

  // One deliberate click reaches tasks; every section supports deep link/reload.
  await go('#projects/'+A);
  evidence.states.taskNavigationBefore={url:page.url(),href:await page.getByRole('link',{name:'编写与查看任务',exact:true}).getAttribute('href')};
  await page.evaluate(()=>{window.navigationTrace=[];window.addEventListener('hashchange',()=>window.navigationTrace.push({event:'hashchange',hash:location.hash}));document.addEventListener('click',event=>{const a=event.target.closest('a');if(a)queueMicrotask(()=>window.navigationTrace.push({event:'click',href:a.getAttribute('href'),prevented:event.defaultPrevented}));},true);const original=history.replaceState;history.replaceState=function(...args){window.navigationTrace.push({event:'replaceState',target:String(args[2])});return original.apply(this,args);};});
  await page.getByRole('link',{name:'编写与查看任务',exact:true}).click();
  try{await heading('Hub 执行任务').waitFor();}catch(error){evidence.states.taskNavigationTrace=await page.evaluate(()=>window.navigationTrace);throw error;}
  assert.ok(page.url().endsWith('#projects/'+A+'/tasks'));
  evidence.states.oneClickTasks=true;
  evidence.states.sections=[];
  for(const id of [A,B])for(const [section,label,content] of [['overview','概览','这是什么项目'],['tasks','任务','Hub 执行任务'],
    ['schedule','排期与关系','项目关系'],['sources','来源与产物','状态来源']]){
    await go('#projects/'+id+'/'+section);
    await heading(content).waitFor();
    const nav=page.getByRole('navigation',{name:'项目内导航'});
    assert.equal(await nav.locator('[aria-current="page"]').count(),1);
    assert.equal(await nav.locator('[aria-current="page"]').innerText(),label);
    assert.equal(await page.locator('#nav-projects').getAttribute('aria-current'),'page');
    await page.reload();await heading(content).waitFor();
    assert.equal(await nav.locator('[aria-current="page"]').innerText(),label);
    assert.equal(await main.locator('h1').innerText(),title(id));
    evidence.states.sections.push({project:id,section,deepLink:true,reload:true,current:label});
  }

  // A real long directory tests back/forward focus and scroll, not just URL text.
  await go('#projects?q=fixture&group=status&view=list');
  const selected=main.locator('a[data-project-id="fixture-directory-030"]');
  await selected.scrollIntoViewIfNeeded();await selected.focus();await tick();
  const scroll=await page.evaluate(()=>scrollY);
  assert.ok(scroll>0);
  await selected.click();await heading('fixture-directory-030').waitFor();
  await page.goBack();await search().waitFor();
  await page.waitForFunction(({scroll})=>Math.abs(scrollY-scroll)<3&&document.activeElement?.dataset.projectId==='fixture-directory-030',{scroll});
  assert.equal(await search().inputValue(),'fixture');
  assert.equal(await page.locator('#group-projects').inputValue(),'status');
  assert.equal(await page.locator('#view-projects').inputValue(),'list');
  await page.goForward();await heading('fixture-directory-030').waitFor();
  await page.reload();await heading('fixture-directory-030').waitFor();
  evidence.states.history={query:'fixture',group:'status',view:'list',scrollRestored:true,focusRestored:true,forward:true,reload:true};
  for(const view of ['tree','timeline']){
    await go('#projects?q=合成甲&group=type&view='+view);
    if(view==='tree')await main.locator('details[data-project-id]').locator('summary').click();
    const link=main.locator('[aria-label="项目目录视图"]').getByRole('link').first();
    await link.click();await heading(title(A)).waitFor();
    await page.goBack();await search().waitFor();
    assert.equal(await search().inputValue(),'合成甲');
    assert.equal(await page.locator('#group-projects').inputValue(),'type');
    assert.equal(await page.locator('#view-projects').inputValue(),view);
    await page.reload();await search().waitFor();
    assert.equal(await page.locator('#view-projects').inputValue(),view);
  }

  // Independent transport failures keep their class and explicit recovery.
  const target=origin+'/api/projects/'+A;
  evidence.states.errors=[];
  for(const [status,titleText] of [[401,'需要重新核对身份'],[403,'当前权限不足'],[404,'对象不存在或已移除'],
    [409,'版本发生冲突'],[500,'后台读取失败'],[503,'这项服务暂不可用']]){
    await go('#projects/'+B);
    const handler=route=>route.fulfill({status,contentType:'application/json',body:JSON.stringify({ok:false,
      error:{code:'SYNTHETIC_HTTP_'+status,outcome:'NOT_COMMITTED',retryable:false,details:{}}})});
    await page.route(target,handler);await go('#projects/'+A);await heading(titleText).waitFor();
    assert.equal(await page.getByRole('button',{name:'重新读取',exact:true}).count(),1);
    await page.unroute(target,handler);
    if(status===401){
      await page.getByRole('button',{name:'连接所有者',exact:true}).click();
      await page.getByRole('button',{name:'取消',exact:true}).click();
    }
    await page.getByRole('button',{name:'重新读取',exact:true}).click();await heading(title(A)).waitFor();
    evidence.states.errors.push({status,title:titleText,recovered:true});
  }
  await go('#projects/'+B);
  await context.setOffline(true);
  await page.evaluate(id=>{location.hash='#projects/'+id;},A);
  await heading('后台连接中断').waitFor();
  await context.setOffline(false);
  await page.getByRole('button',{name:'重新读取',exact:true}).click();await heading(title(A)).waitFor();
  evidence.states.errors.push({status:'offline',title:'后台连接中断',recovered:true});

  await go('#projects/'+B);
  await page.clock.install();
  let timeoutReady,releaseTimeout;
  const timedRequest=new Promise(resolve=>{timeoutReady=resolve;});
  const timeoutGate=new Promise(resolve=>{releaseTimeout=resolve;});
  const timeoutHandler=async route=>{
    const response=await route.fetch();timeoutReady();await timeoutGate;
    await route.fulfill({response}).catch(()=>{});
  };
  await page.route(target,timeoutHandler);
  try{
    await page.evaluate(id=>{location.hash='#projects/'+id;},A);await within(timedRequest,3000);
    await page.clock.fastForward(16000);await heading('等待后台超时').waitFor();
  }finally{releaseTimeout();await page.unroute(target,timeoutHandler);await page.clock.resume();}
  await page.getByRole('button',{name:'重新读取',exact:true}).click();await heading(title(A)).waitFor();
  evidence.states.errors.push({status:'timeout',title:'等待后台超时',recovered:true});

  // Deliver A after navigation to B; its successful real response stays stale.
  await go('#projects/'+B);
  let lateReady,releaseLate,lateDone;
  const lateRequest=new Promise(resolve=>{lateReady=resolve;});
  const lateGate=new Promise(resolve=>{releaseLate=resolve;});
  const lateFinished=new Promise(resolve=>{lateDone=resolve;});
  const lateHandler=async route=>{
    try{const response=await route.fetch();lateReady();await lateGate;await route.fulfill({response}).catch(()=>{});}
    finally{lateDone();}
  };
  await page.route(target,lateHandler);
  try{
    await page.evaluate(id=>{location.hash='#projects/'+id;},A);await within(lateRequest,3000);
    await page.evaluate(id=>{location.hash='#projects/'+id;},B);await heading(title(B)).waitFor();
    releaseLate();await within(lateFinished,3000);await tick();
    assert.equal(await main.locator('h1').innerText(),title(B));
    assert.ok(!await main.locator('[aria-label="项目定义"]').innerText().then(text=>text.includes('合成甲')));
  }finally{releaseLate();await page.unroute(target,lateHandler);}
  evidence.states.lateResponse={requested:A,current:B,preserved:true};
  await writeFile(path.join(evidenceRoot,'o1-final-dom.txt'),await main.innerText());
},{directorySize:103});
