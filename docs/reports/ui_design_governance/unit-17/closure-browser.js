async (page) => {
 const origin='http://127.0.0.1:63078', errors=[],requests=[],routes=[],consoleErrors=[],badResponses=[];
 const onError=e=>errors.push(e.message),onRequest=r=>requests.push({method:r.method(),url:r.url()});
 const onConsole=m=>{if(m.type()==='error')consoleErrors.push(m.text());},onResponse=r=>{if(r.status()>=400)badResponses.push({url:r.url(),status:r.status()});};
 page.on('pageerror',onError);page.on('request',onRequest);page.on('console',onConsole);page.on('response',onResponse);
 try {
  const go=async(hash)=>{await page.goto(origin+'/'+hash);await page.locator('#main[aria-busy]').waitFor({state:'detached'});return await page.locator('#main').innerText();};
  await page.setViewportSize({width:1440,height:1000});
  for(const path of ['#designs','#designs/computer-study-plan','#designs/computer-study-plan/csp-home-quiet-workbench']){
   const text=await go(path);if(!text.includes('Computer Study Plan'))throw new Error('Missing project name '+path);routes.push({path,name:true});
   await page.reload();await page.locator('#main[aria-busy]').waitFor({state:'detached'});
   if(!(await page.locator('#main').innerText()).includes('Computer Study Plan'))throw new Error('Reload name lost');
  }
  await page.getByText(/当前：已暂缓/).waitFor();
  await page.locator('#history-title').focus();await page.keyboard.press('Enter');
  if(!(await page.locator('.history').innerText()).includes('学习计划保留原版'))throw new Error('Missing retained original history');
  await page.waitForFunction(()=>[...document.querySelectorAll('.compare-grid img')].every(e=>e.complete&&e.naturalWidth>0));
  await page.screenshot({path:'/Users/alalapi/PycharmProjects/personal-control-hub/docs/reports/ui_design_governance/unit-17/closure-desktop.png',fullPage:true});
  await page.locator('a[href="#designs/computer-study-plan"]').click();await page.locator('#main[aria-busy]').waitFor({state:'detached'});
  await page.goBack();await page.locator('#design-feedback').waitFor();
  const navigationFocus=await page.evaluate(()=>document.activeElement.id);
  await page.setViewportSize({width:390,height:844});
  await page.locator('#preview-view').selectOption('overview-mobile');
  const previews=[];
  for(const name of ['原始版本','候选设计']){
    await page.getByRole('button',{name,exact:true}).click();
    const id=name==='原始版本'?'original-canvas':'candidate-canvas';
    const img=page.locator('#'+id+' img');await img.waitFor({state:'visible'});
    await page.waitForFunction(id=>{const e=document.querySelector('#'+id+' img');return e&&e.complete&&e.naturalWidth===390&&e.naturalHeight===739;},id);
    previews.push(await img.evaluate(e=>({width:e.naturalWidth,height:e.naturalHeight,alt:e.alt})));
  }
  await page.locator('#history-title').click();
  const mobileOverflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
  if(mobileOverflow)throw new Error('Mobile overflow');
  await page.waitForFunction(()=>[...document.querySelectorAll('.compare-grid img')].every(e=>e.complete&&e.naturalWidth>0));
  await page.screenshot({path:'/Users/alalapi/PycharmProjects/personal-control-hub/docs/reports/ui_design_governance/unit-17/closure-mobile.png',fullPage:true});
  await page.emulateMedia({reducedMotion:'reduce'});
  const reduced=await page.locator('#design-feedback').evaluate(e=>({matches:matchMedia('(prefers-reduced-motion: reduce)').matches,transition:getComputedStyle(e).transitionDuration,animation:getComputedStyle(e).animationDuration}));
  const replay=await page.evaluate(async data=>{
    const session=await (await fetch('/api/session',{credentials:'same-origin'})).json();
    const post=async(path,command)=>{const r=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Hub-CSRF':session.data.csrf_token},body:JSON.stringify(command)});return {status:r.status,body:await r.json()};};
    const decision=await post('/api/designs/decisions',data.command);
    const exported=await post('/api/designs/exports',data.exportCommand);
    const snapshot=await (await fetch('/api/designs',{credentials:'same-origin'})).json();
    const response=await fetch(data.downloadHref,{credentials:'same-origin'});const buf=await response.arrayBuffer();
    const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',buf)),n=>n.toString(16).padStart(2,'0')).join('');
    return {decision,exported,download:{status:response.status,bytes:buf.byteLength,sha256:digest},storeRevision:snapshot.data.store_revision,historyCount:snapshot.data.history.length,queues:Object.fromEntries(Object.entries(snapshot.data.queues).map(([k,v])=>[k,v.length]))};
  },{"command":{"request_id":"tc17-defer-owner-01a07589-d513-7a01-92f4-eefb9ba5f05b","event_id":"tc17-owner-retain-original","created_at":"2026-09-06T07:10:45.123986+00:00","expected_revision":14,"action":"defer","candidate":{"id":"csp-home-quiet-workbench","revision":1,"content_hash":"1a1f1a59897ae6200e9361e3c4c692c2f58499bd045df8f3abd2d994d8e453a1"},"scope":{"family_id":null,"members":[{"pages":["home"],"project_id":"computer-study-plan"}]},"feedback":"所有者原话：学习计划的项目保留原版的设计，其他项目也不用继续推进了，只要先把hub的选稿页面做出来就行，后面其他项目的设计和推进我打算交给cursor去做。\n由 Root 按此指令代为记录：学习计划保留原版，本候选不采用；不授权外部实施。","supersedes":null},"exportCommand":{"request_id":"ui-export-3aec346e-2bed-4e59-bb26-a2744fd070b3","expected_revision":15,"candidate":{"id":"csp-home-quiet-workbench","revision":1,"content_hash":"1a1f1a59897ae6200e9361e3c4c692c2f58499bd045df8f3abd2d994d8e453a1"}},"downloadHref":"/api/exports/ui-export-3aec346e-2bed-4e59-bb26-a2744fd070b3?candidate_id=csp-home-quiet-workbench&candidate_revision=1&candidate_hash=1a1f1a59897ae6200e9361e3c4c692c2f58499bd045df8f3abd2d994d8e453a1&store_revision=15"});
  if(replay.storeRevision!==15||replay.historyCount!==2||replay.download.sha256!=='313bb3ec4b5bcbe4e96e6dac7349bf90eab23b7292c0da7f5440af8a3f6f621e')throw new Error('Replay/download mismatch');
  await page.emulateMedia({reducedMotion:'no-preference'});await page.setViewportSize({width:1440,height:1000});await go('#designs');
  return {routes,navigationFocus,previews,mobileOverflow,reduced,replay,errors,consoleErrors,badResponses,requests:{total:requests.length,external:requests.filter(r=>!r.url.startsWith(origin+'/')),methods:requests.reduce((a,r)=>(a[r.method]=(a[r.method]||0)+1,a),{})},screenshots:['closure-desktop.png','closure-mobile.png'],restart:{oldSession:6291,exit:0,newSession:90102,port:63078}};
 } finally {page.off('pageerror',onError);page.off('request',onRequest);page.off('console',onConsole);page.off('response',onResponse);}
}
