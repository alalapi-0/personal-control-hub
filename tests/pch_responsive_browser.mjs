// Synthetic A/B through the real app/stores/worker. Root provides private CDP.
// Root also supplies PCH_BROWSER_RESOURCES and PCH_TASK_ID for owned X11 input.
// Zoom mode uses Chrome's actual setting and an owned X11 window capture.
import assert from 'node:assert/strict';
import path from 'node:path';
import {readFileSync,writeFileSync,lstatSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {runBrowserJourney} from './pch_browser_runtime.mjs';

const luminance=rgb=>rgb.reduce((n,v,i)=>n+[.2126,.7152,.0722][i]*(v/255<=.04045?v/255/12.92:((v/255+.055)/1.055)**2.4),0);
const ratio=(a,b)=>(Math.max(luminance(a),luminance(b))+.05)/(Math.min(luminance(a),luminance(b))+.05);
function rgb(value){
  const m=value.match(/^rgba?\(([^)]+)\)$/);assert.ok(m,'Measured CSS color must resolve to sRGB: '+value);
  const values=m[1].split(/[,\s/]+/).filter(Boolean).map(Number);assert.ok(values.length===3||values[3]===1,'Transparent foreground requires separate measurement');return values.slice(0,3);
}
function privateBrowser(){
  const file=process.env.PCH_BROWSER_RESOURCES;assert.ok(file&&path.isAbsolute(file));
  const r=JSON.parse(readFileSync(file,'utf8'));assert.ok(process.env.PCH_TASK_ID&&r.task===process.env.PCH_TASK_ID);
  for(const [pid,ticks]of [[r.display_pid,r.display_ticks],[r.browser_pid,r.browser_ticks]]){
    const fields=readFileSync('/proc/'+pid+'/stat','utf8').split(') ').at(-1).split(' ');
    assert.equal(Number(fields[19]),ticks);assert.notEqual(fields[0],'Z');
  }
  assert.equal(Number(new URL(process.argv[2]).port),r.cdp_port);
  const argv=readFileSync('/proc/'+r.browser_pid+'/cmdline','utf8').split('\0');
  const profile=argv.find(arg=>arg.startsWith('--user-data-dir='))?.slice('--user-data-dir='.length);
  assert.ok(profile&&path.dirname(profile)===path.dirname(file)&&/^profile(?:-[a-f0-9]{32})?$/.test(path.basename(profile)),
    'Browser preferences may change only inside the supplied task-private profile');
  assert.ok(lstatSync(profile).isDirectory()&&!lstatSync(profile).isSymbolicLink());
  assert.match(r.display,/^:\d+$/);
  const env={...process.env,DISPLAY:r.display,XAUTHORITY:path.join(path.dirname(file),'Xauthority')};delete env.WAYLAND_DISPLAY;
  return {r,env};
}
function ownedWindow(bounds){
  const {r,env}=privateBrowser();
  const viewable=[];
  // The main window can map after the launcher's initial inventory. Resolve
  // current windows by the verified PID and the exact CDP target bounds.
  const windows=execFileSync('xdotool',['search','--onlyvisible','--pid',String(r.browser_pid)],{env,encoding:'utf8',timeout:3000}).trim().split(/\s+/);
  for(const window of windows){
    assert.match(window,/^\d+$/);
    const property=execFileSync('xprop',['-id',window,'_NET_WM_PID'],{env,encoding:'utf8',timeout:3000});
    assert.equal(Number(property.trim().split('=').at(-1).trim()),r.browser_pid);
    const info=execFileSync('xwininfo',['-id',window],{env,encoding:'utf8',timeout:3000});
    if(/Class: InputOutput/.test(info)&&/Map State: IsViewable/.test(info)
        &&Number(info.match(/Width: (\d+)/)[1])===bounds.width&&Number(info.match(/Height: (\d+)/)[1])===bounds.height
        &&Number(info.match(/Absolute upper-left X:\s+(-?\d+)/)[1])===bounds.left
        &&Number(info.match(/Absolute upper-left Y:\s+(-?\d+)/)[1])===bounds.top)viewable.push(window);
  }
  assert.equal(viewable.length,1,'Exactly one mapped owned browser content window is required');
  return {r,window:viewable[0],env};
}
function nativeCapture(destination,bounds){
  const {r,window,env}=ownedWindow(bounds);
  const xwd=destination+'.xwd';execFileSync('xwd',['-silent','-id',window,'-out',xwd],{env,timeout:3000});
  execFileSync('magick',[xwd,destination],{timeout:5000});
  const dimensions=execFileSync('magick',['identify','-format','%w %h',destination],{encoding:'utf8',timeout:3000}).trim().split(' ').map(Number);
  const geometry=execFileSync('xdotool',['getwindowgeometry','--shell',window],{env,encoding:'utf8',timeout:3000});
  assert.deepEqual(dimensions,['WIDTH','HEIGHT'].map(k=>Number(geometry.match(new RegExp('^'+k+'=(\\d+)$','m'))[1])));
  return {method:'Exact owned X11 window xwd, decoded PNG',dimensions,window,browser_pid:r.browser_pid,browser_ticks:r.browser_ticks};
}

await runBrowserJourney('o53',async({page,context,browser,evidence,evidenceRoot,origin,waitMessage,loginOwner})=>{
  const pageControl=await context.newCDPSession(page),browserControl=await browser.newBrowserCDPSession();
  async function currentBounds(){
    const {targetInfo}=await pageControl.send('Target.getTargetInfo');
    return (await browserControl.send('Browser.getWindowForTarget',{targetId:targetInfo.targetId})).bounds;
  }
  const zoomMode=process.env.PCH_RESPONSIVE_MODE==='zoom';
  evidence.acceptance={classification:'synthetic_fixture',transport:'mock',realPhone:'unverified',
    scope:'Browser responsive/actual zoom/composited contrast/reduced-motion only; no full WCAG certification'};
  evidence.layouts=[];evidence.contrast=[];evidence.motion=[];evidence.expectedErrors=[];evidence.nativeCaptures=[];
  const expected=new Set(),unexpected=[];
  page.on('console',m=>{if(m.type()==='error'){
    if(/Failed to load resource:.*401/.test(m.text())||expected.has(m.location().url))evidence.expectedErrors.push({url:m.location().url,text:m.text()});
    else unexpected.push(m.text());
  }});
  privateBrowser();
  // These two offers belong to this new disposable profile, not a user account.
  // Prevent browser chrome from obscuring the synthetic owner's app journey.
  await page.goto('chrome://password-manager/settings');
  const saveOffer=page.getByRole('switch',{name:'Offer to save passwords and passkeys',exact:true});await saveOffer.waitFor();
  if(await saveOffer.getAttribute('aria-checked')==='true')await saveOffer.click();
  assert.equal(await saveOffer.getAttribute('aria-checked'),'false');
  await page.goto('chrome://settings/languages');
  const translateOffer=page.getByRole('switch',{name:'Use Google Translate',exact:true});await translateOffer.waitFor();
  if(await translateOffer.getAttribute('aria-checked')==='true')await translateOffer.click();
  assert.equal(await translateOffer.getAttribute('aria-checked'),'false');
  if(zoomMode){
    assert.equal(page.viewportSize(),null,'Actual zoom must start without viewport emulation');
    const before=await page.evaluate(()=>({inner:innerWidth,outer:outerWidth,dpr:devicePixelRatio}));
    await page.goto('chrome://settings/appearance');const zoom=page.locator('#zoomLevel');await zoom.waitFor();assert.equal(await zoom.inputValue(),'1');
    await zoom.selectOption('2');assert.equal((await zoom.locator('option:checked').innerText()).trim(),'200%');
    evidence.zoom={method:'Fresh owned Chrome Settings Appearance Page zoom200%',before,viewportOverride:false,pageScale:false,cssTransform:false};
  }else await page.setViewportSize({width:1440,height:1050});
  const sample=await waitMessage(m=>m.stress_ready,{action:'stress-sample'});
  assert.equal(sample.classification,'synthetic_fixture');assert.equal(sample.projects.length,2);assert.equal(sample.proposal_ids.length,3);
  await waitMessage(m=>m.ordinary_ready,{action:'ordinary-sample'});
  await page.goto(origin+'/#projects');await page.locator('#search-projects').waitFor();
  if(zoomMode){
    evidence.zoom.after=await page.evaluate(()=>({inner:innerWidth,outer:outerWidth,dpr:devicePixelRatio,scale:visualViewport.scale}));
    assert.equal(evidence.zoom.after.dpr,2);assert.equal(evidence.zoom.after.outer,evidence.zoom.before.outer);
    assert.ok(Math.abs(evidence.zoom.after.inner*2-evidence.zoom.before.inner)<=2);assert.equal(evidence.zoom.after.scale,1);
  }
  await loginOwner();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal(await page.getByRole('button',{name:'断开所有者',exact:true}).count(),1);
  evidence.browserSetup={syntheticOwnerOnly:true,privateProfileSaveOffer:false,privateProfileTranslationOffer:false,
    capture:nativeCapture(path.join(evidenceRoot,'o53-browser-ready.png'),await currentBounds())};
  await waitMessage(m=>m.ordinary_runtime_started,{action:'ordinary-runtime-start'});
  const panel=()=>page.getByRole('region',{name:'普通任务',exact:true});
  const nav=name=>page.getByRole('navigation',{name:'项目内导航',exact:true}).getByRole('link',{name,exact:true});
  async function project(row,section='tasks'){
    await page.goto(origin+'/#projects/'+row.project_id+'/'+section);
    await page.getByRole('heading',{name:row.name,exact:true}).waitFor();
    await page.waitForFunction(()=>!document.querySelector('#main')?.hasAttribute('aria-busy'));
  }
  async function layout(label){
    const result=await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio,
      scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth,
      bodyScroll:document.body.scrollWidth,busy:document.querySelector('#main').hasAttribute('aria-busy')}));
    evidence.layouts.push({label,...result});assert.ok(!result.busy);assert.ok(result.scroll<=result.client+1,label+': document horizontal overflow');
    const scroll=await page.evaluate(()=>({x:scrollX,y:scrollY}));
    const target=page.getByRole('button',{name:'断开所有者',exact:true});await target.scrollIntoViewIfNeeded();
    const box=await target.boundingBox();assert.ok(box&&box.height>=43.5&&box.width>=43.5&&box.x>=-1&&box.x+box.width<=result.width+1,label+': owner action unreachable');
    await page.evaluate(({x,y})=>scrollTo(x,y),scroll);
  }
  async function contrast(target,label,{border=false}={}){
    if(zoomMode)return; // Color measurements use exact DPR1 pixels; zoom has separate actual-window evidence.
    await target.scrollIntoViewIfNeeded();const box=await target.boundingBox();assert.ok(box&&box.width>12&&box.height>12);
    const style=await target.evaluate(n=>{const s=getComputedStyle(n);return {color:s.color,border:s.borderTopColor,fontSize:parseFloat(s.fontSize),weight:parseInt(s.fontWeight),opacity:s.opacity};});
    assert.equal(Number(style.opacity),1);
    const original=await target.evaluate(n=>({attribute:n.getAttribute('style'),css:n.style.cssText}));
    const file=path.join(evidenceRoot,'contrast-'+evidence.contrast.length+'.png');
    try{
      await target.evaluate(n=>{n.style.setProperty('color','transparent','important');n.style.setProperty('text-shadow','none','important');});
      await page.bringToFront();await page.screenshot({path:file});
    }finally{
      // Restore through the same CSSOM interface; setAttribute(style, ...) is
      // rejected by the app's unchanged style-src policy.
      await target.evaluate((n,value)=>{n.style.cssText=value.css;if(value.attribute===null)n.removeAttribute('style');},original);
      assert.equal(await target.evaluate(n=>n.style.cssText),original.css);
    }
    const [width,height]=execFileSync('magick',['identify','-format','%w %h',file],{encoding:'utf8',timeout:3000}).trim().split(' ').map(Number);
    const raw=execFileSync('magick',[file,'-colorspace','sRGB','-depth','8','rgb:-'],{timeout:5000,maxBuffer:width*height*3+4096});assert.equal(raw.length,width*height*3);
    const pixel=(x,y)=>{const offset=(Math.min(height-1,Math.max(0,Math.round(y)))*width+Math.min(width-1,Math.max(0,Math.round(x))))*3;return [...raw.subarray(offset,offset+3)];};
    const foreground=rgb(style.color);const backgrounds=[];
    const visible={left:Math.max(0,box.x),top:Math.max(0,box.y),right:Math.min(width,box.x+box.width),bottom:Math.min(height,box.y+box.height)};
    assert.ok(visible.right-visible.left>12&&visible.bottom-visible.top>12,label+': sample must be inside the rendered viewport');
    for(const x of [.25,.5,.75])for(const y of [.25,.5,.75])backgrounds.push(pixel(visible.left+(visible.right-visible.left)*x,visible.top+(visible.bottom-visible.top)*y));
    const minimum=Math.min(...backgrounds.map(background=>ratio(foreground,background)));
    const threshold=style.fontSize>=24||style.fontSize>=18.666&&style.weight>=700?3:4.5;
    const record={label,viewport:page.viewportSize(),foreground,backgrounds,minimum,threshold,box,visible,style,restoredCSS:original.css,file:path.basename(file)};evidence.contrast.push(record);
    assert.ok(minimum>=threshold,label+': text contrast '+minimum+' below '+threshold);
    if(border){
      const adjacent=[pixel(box.x-8,box.y+box.height*.5),pixel(box.x+box.width+8,box.y+box.height*.5)];
      record.controlBorder={color:rgb(style.border),adjacent,minimum:Math.min(...adjacent.map(c=>ratio(rgb(style.border),c))),threshold:3};
      assert.ok(record.controlBorder.minimum>=3,label+': control boundary contrast below3');
    }
  }
  const tasks=new Map();
  for(const row of sample.projects){
    await project(row);await panel().waitFor();
    await panel().getByLabel('任务目标',{exact:true}).fill('Synthetic responsive read-only check for '+row.project_id);
    await panel().getByLabel('任务范围',{exact:true}).fill('Read only registered synthetic inputs and return the fixed synthetic stress response.');
    const [sent]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/tasks'&&r.request().method()==='POST'),panel().getByRole('button',{name:'发送普通任务',exact:true}).click()]);
    assert.equal(sent.status(),200);const id=(await sent.json()).data.task.id;tasks.set(row.project_id,id);
    const reply=page.getByLabel('普通任务执行回复',{exact:true});await reply.waitFor();
    const text=await reply.textContent();assert.ok(text.includes(row.reply_marker)&&text.includes(row.reply_unbroken_marker));
    assert.equal(Buffer.byteLength(text,'utf8'),row.reply_bytes);assert.equal(createHash('sha256').update(text).digest('hex'),row.reply_sha256);
  }
  evidence.states.longReplies={tasks:[...tasks],bothExact:true,actualWorkerStore:true};
  const viewports=zoomMode?[null]:[{width:1440,height:1050},{width:1280,height:800},{width:390,height:844}];
  for(const viewport of viewports){
    if(viewport)await page.setViewportSize(viewport);
    for(const row of sample.projects){
      const label=row.project_id+' '+(viewport?viewport.width+'x'+viewport.height:'actual200percent');
      await project(row,'overview');await layout(label+' overview');
      assert.ok((await page.getByRole('region',{name:'项目定义',exact:true}).innerText()).includes('来源'));
      await contrast(page.getByRole('heading',{name:row.name,exact:true}),label+' long heading');
      await nav('排期与关系').click();const management=page.getByRole('region',{name:'Hub 管理配置',exact:true});await management.waitFor();
      await management.getByLabel('关系配置',{exact:true}).selectOption('confirmed');
      const other=sample.projects.find(p=>p.project_id!==row.project_id);
      await management.getByLabel('关联项目（可多选）',{exact:true}).selectOption([other.project_id]);
      const [saved]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/management/project'&&r.request().method()==='POST'),management.getByRole('button',{name:'保存配置',exact:true}).click()]);assert.equal(saved.status(),200);
      await management.getByLabel('管理配置保存状态',{exact:true}).filter({hasText:'已保存'}).waitFor();await layout(label+' schedule');
      await nav('任务').click();await panel().waitFor();
      await panel().getByRole('button',{name:'新建另一条任务草稿',exact:true}).click();
      const objective=panel().getByLabel('任务目标',{exact:true}),scope=panel().getByLabel('任务范围',{exact:true});
      const draft='SYNTHETIC_LONG_DRAFT_'+row.project_id+'_'+('D'.repeat(480));
      await objective.fill(draft);await scope.fill('Read registered synthetic files only. '+('S'.repeat(200)));
      await contrast(objective,label+' editable objective',{border:true});
      await contrast(scope,label+' editable scope',{border:true});
      await contrast(panel().getByRole('button',{name:'发送普通任务',exact:true}),label+' enabled send',{border:true});
      await scope.fill('');assert.equal(await scope.getAttribute('aria-invalid'),'true');
      const description=await scope.getAttribute('aria-describedby');await contrast(page.locator('[id="'+description+'"]'),label+' explicit field error');
      await scope.fill('Read registered synthetic files only.');
      const control=panel().getByRole('button',{name:'核对任务条件',exact:true});
      const contextURL=origin+'/api/ordinary-context?project_id='+row.project_id;
      for(const reduced of [false,true]){
        await page.emulateMedia({reducedMotion:reduced?'reduce':'no-preference'});await control.focus();
        if(reduced){expected.add(contextURL);await context.route(contextURL,route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({ok:false,error:{code:'TASK_SERVICE_UNAVAILABLE',outcome:'NOT_COMMITTED'}})}),{times:1});}
        await page.keyboard.down('Space');
        if(!reduced)await page.waitForFunction(()=>getComputedStyle(document.activeElement).transform!=='none'&&Math.abs(new DOMMatrix(getComputedStyle(document.activeElement).transform).m42-1)<.01);
        const motion=await control.evaluate(n=>{const s=getComputedStyle(n);return {reduce:matchMedia('(prefers-reduced-motion:reduce)').matches,transform:s.transform,transition:s.transitionDuration,animations:n.getAnimations().length};});
        evidence.motion.push({label,reduced,...motion});await page.keyboard.up('Space');
        if(reduced){assert.equal(motion.reduce,true);assert.equal(motion.transform,'none');assert.equal(motion.transition,'0s');
          await panel().getByLabel('普通任务状态',{exact:true}).filter({hasText:'暂不可用'}).waitFor();assert.equal(await objective.inputValue(),draft);assert.ok(await control.isEnabled());
          const [recovered]=await Promise.all([page.waitForResponse(r=>r.url()===contextURL),control.click()]);assert.equal(recovered.status(),200);assert.equal(await objective.inputValue(),draft);
        }
      }
      await layout(label+' editable draft and error recovery');
      await page.goto(origin+'/#tasks/'+tasks.get(row.project_id));const reply=page.getByLabel('普通任务执行回复',{exact:true});await reply.waitFor();
      assert.ok((await reply.textContent()).includes(row.reply_marker));await reply.scrollIntoViewIfNeeded();await layout(label+' long result');
      await contrast(page.getByLabel('任务执行状态',{exact:true}),label+' completed status');await contrast(reply,label+' long response');
      await reply.evaluate(n=>n.scrollIntoView({block:'start'}));
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      assert.ok(Math.abs((await reply.boundingBox()).y)<=1,'Long-reply capture must show its beginning');
      if(zoomMode)evidence.nativeCaptures.push({label,...nativeCapture(path.join(evidenceRoot,row.project_id+'-actual200.png'),await currentBounds())});
      else{await page.bringToFront();await page.screenshot({path:path.join(evidenceRoot,row.project_id+'-'+viewport.width+'.png')});}
      await page.getByRole('link',{name:'返回项目任务区',exact:true}).click();await panel().waitFor();assert.equal(await panel().getByLabel('任务目标',{exact:true}).inputValue(),draft);
    }
  }
  evidence.states.layoutMatrix={viewports:zoomMode?'actualChrome200%':viewports,projects:sample.projects.map(p=>p.project_id),checks:evidence.layouts.length};
  evidence.states.contrast={measured:evidence.contrast.length,method:'Opaque computed foreground against browser-composited background pixels with only text paint temporarily transparent; source inline style restored exactly',zoomReuse:zoomMode?'same candidate colors; numeric DPR1 matrix separately':'none'};
  evidence.states.reducedMotion={observations:evidence.motion.length,actualTaskReplies:true,contextFailureAndRecovery:true,draftPreserved:true};
  const observations=await waitMessage(m=>'ordinary_control_observations'in m,{action:'ordinary-observations'});assert.equal(observations.effects,0);
  assert.equal(evidence.tasks.length,2);assert.deepEqual(unexpected,[]);
  writeFileSync(path.join(evidenceRoot,'o53-measurements.json'),JSON.stringify({zoom:evidence.zoom,layouts:evidence.layouts,contrast:evidence.contrast,motion:evidence.motion,native:evidence.nativeCaptures},null,2));
});
