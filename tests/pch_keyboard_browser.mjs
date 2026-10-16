// Actual keyboard actions and Chromium accessibility tree on Root's owned CDP.
// Owner/provider/source data are explicitly synthetic; this is not a screen reader.
import assert from 'node:assert/strict';
import path from 'node:path';
import {writeFile} from 'node:fs/promises';
import {runBrowserJourney} from './pch_browser_runtime.mjs';

await runBrowserJourney('o52',async({page,context,evidence,evidenceRoot,origin,waitMessage,loginOwner})=>{
  const A='fixture-project-a',B='fixture-project-b';
  evidence.acceptance={classification:'synthetic_fixture',transport:'mock',input:'keyboard',
    accessibility:'Chromium AX tree and targeted automatic rules; real screen reader unverified'};
  evidence.keyboard=[];evidence.ax=[];evidence.expectedErrors=[];
  const expectedConsole=new Set();const unexpected=[];
  page.on('console',m=>{if(m.type()==='error'){
    if(/Failed to load resource:.*401/.test(m.text())||expectedConsole.has(m.location().url))
      evidence.expectedErrors.push({text:m.text(),url:m.location().url});
    else unexpected.push(m.text());
  }});
  await page.setViewportSize({width:1440,height:1050});
  await waitMessage(m=>m.management_ready,{action:'management-sample'});
  await waitMessage(m=>m.ordinary_ready,{action:'ordinary-sample'});
  await page.goto(origin+'/#projects');
  await page.locator('#search-projects').waitFor();
  const ax=await context.newCDPSession(page);await ax.send('Accessibility.enable');
  async function focused(locator){return locator.evaluate(n=>n===document.activeElement);}
  async function track(action){
    evidence.keyboard.push({action,...await page.evaluate(()=>{
      const n=document.activeElement;return {tag:n?.tagName,id:n?.id,role:n?.getAttribute('role'),
        label:n?.getAttribute('aria-label'),text:(n?.textContent||'').trim().slice(0,100),hash:location.hash};
    })});
  }
  async function tabTo(locator,label){
    await locator.waitFor();assert.ok(await locator.isEnabled(),label+' must be enabled');
    let reached=false;
    for(let i=0;i<140;i++){
      if(await focused(locator)){reached=true;break;}
      await page.keyboard.press('Tab');
    }
    assert.ok(reached,label+' must be reachable with Tab');await track('Tab to '+label);
  }
  async function activate(locator,label){await tabTo(locator,label);await page.keyboard.press('Enter');}
  async function type(locator,text,label){
    await tabTo(locator,label);await page.keyboard.press('Control+A');await page.keyboard.insertText(text);
    await track('Type '+label);
  }
  async function select(locator,value,label){
    const index=await locator.locator('option').evaluateAll((nodes,v)=>nodes.findIndex(n=>n.value===v),value);
    assert.ok(index>=0);await tabTo(locator,label);await page.keyboard.press('Home');
    for(let i=0;i<index;i++)await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Tab');assert.equal(await locator.inputValue(),value);
  }
  async function captureAX(stage,required=[]){
    const {nodes}=await ax.send('Accessibility.getFullAXTree');
    const rows=nodes.filter(n=>!n.ignored).map(n=>({role:n.role?.value,name:n.name?.value,description:n.description?.value,
      properties:n.properties?.map(p=>({name:p.name,value:p.value?.value}))}));
    for(const [role,name] of required)assert.ok(rows.some(n=>n.role===role&&n.name===name),stage+': '+role+' '+name);
    if(stage==='empty draft')for(const name of ['任务目标','任务范围']){
      const field=rows.find(n=>n.role==='textbox'&&n.name===name);
      assert.ok(field.properties.some(p=>p.name==='invalid'&&p.value==='true'));
      assert.match(field.description,/请填写/);
    }
    if(stage==='waiting input'||stage==='completed result'){
      const state=rows.find(n=>n.role==='status'&&n.name==='任务执行状态');
      assert.ok(state.properties.some(p=>p.name==='live'&&p.value==='polite'));
    }
    const unnamed=rows.filter(n=>['button','link','textbox','combobox','listbox'].includes(n.role)&&!n.name?.trim());
    assert.deepEqual(unnamed,[],stage+': interactive AX nodes need names');
    assert.deepEqual(await page.locator('[id]').evaluateAll(nodes=>{
      const ids=nodes.map(n=>n.id);return [...new Set(ids.filter((id,i)=>ids.indexOf(id)!==i))];
    }),[],stage+': duplicate DOM ids');
    evidence.ax.push({stage,rows});await track(stage);
  }
  const button=name=>page.getByRole('button',{name,exact:true});
  const projectNav=name=>page.getByRole('navigation',{name:'项目内导航',exact:true}).getByRole('link',{name,exact:true});
  await loginOwner({keyboard:true});await button('断开所有者').waitFor();
  await page.waitForFunction(()=>document.activeElement?.textContent==='断开所有者');
  await activate(button('断开所有者'),'disconnect owner');await button('连接所有者').waitFor();
  await activate(button('连接所有者'),'open owner dialog');
  await page.getByRole('dialog',{name:'连接所有者',exact:true}).waitFor();
  assert.ok(await focused(page.getByLabel('所有者凭据',{exact:true})));
  await captureAX('owner dialog',[['dialog','连接所有者'],['textbox','所有者凭据']]);
  await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'detached'});
  assert.ok(await focused(button('连接所有者')));
  await loginOwner({keyboard:true});await button('断开所有者').waitFor();
  await page.waitForFunction(()=>document.activeElement?.textContent==='断开所有者');
  evidence.states.ownerDialog={keyboardLogin:true,escapeReturnsFocus:true,connectedReplacementFocus:true};

  await type(page.locator('#search-projects'),'合成甲','project search');
  const entry=page.locator('#main a[data-project-id="'+A+'"]');
  await entry.waitFor();await activate(entry,'project A');
  await page.getByRole('heading',{name:'Synthetic Project A',exact:true}).waitFor();
  await activate(projectNav('排期与关系'),'schedule and relations');
  const management=page.getByRole('region',{name:'Hub 管理配置',exact:true});await management.waitFor();
  await select(management.getByLabel('关系配置',{exact:true}),'confirmed','relationship configuration');
  const dependency=management.getByLabel('依赖项目（可多选）',{exact:true});
  await tabTo(dependency,'dependency project');await page.keyboard.press('Control+A');await page.keyboard.press('Tab');
  assert.deepEqual(await dependency.locator('option:checked').evaluateAll(nodes=>nodes.map(n=>n.value)),[B]);
  const [savedRelation]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/management/project'&&r.request().method()==='POST'),
    activate(management.getByRole('button',{name:'保存配置',exact:true}),'save relationship')]);
  assert.equal(savedRelation.status(),200);await management.getByLabel('管理配置保存状态',{exact:true}).filter({hasText:'已保存'}).waitFor();
  await page.waitForFunction(()=>document.activeElement?.textContent==='保存配置');
  const relations=page.getByRole('region',{name:'项目关系',exact:true});
  await captureAX('relations',[['region','项目关系']]);
  await activate(relations.getByRole('link',{name:'Synthetic Project B',exact:true}).first(),'related project B');
  await page.getByRole('heading',{name:'Synthetic Project B',exact:true}).waitFor();
  await activate(page.getByRole('region',{name:'项目关系',exact:true}).getByRole('link',{name:'Synthetic Project A',exact:true}).first(),'return relationship to A');
  await page.getByRole('heading',{name:'Synthetic Project A',exact:true}).waitFor();
  await activate(projectNav('排期与关系'),'return to schedule section');await management.waitFor();
  assert.match(page.url(),/\/schedule$/);evidence.states.keyboardRelationships={searched:true,configured:true,targetCorrect:true,reciprocalReturn:true,saveFocusRetained:true};

  await activate(projectNav('任务'),'project tasks');
  const panel=page.getByRole('region',{name:'普通任务',exact:true});await panel.waitFor();
  const objective=panel.getByLabel('任务目标',{exact:true}),scope=panel.getByLabel('任务范围',{exact:true});
  for(const field of [objective,scope]){
    assert.equal(await field.getAttribute('aria-required'),'true');assert.equal(await field.getAttribute('aria-invalid'),'true');
    const refs=(await field.getAttribute('aria-describedby')||'').split(/\s+/).filter(Boolean);assert.ok(refs.length);
    for(const id of refs)assert.ok((await page.locator('[id="'+id+'"]').innerText()).trim());
  }
  assert.ok(await panel.getByRole('button',{name:'发送普通任务',exact:true}).isDisabled());
  await captureAX('empty draft',[['textbox','任务目标'],['textbox','任务范围']]);
  await type(objective,'Keyboard synthetic read-only task','task objective');
  assert.equal(await objective.getAttribute('aria-invalid'),'false');assert.equal(await scope.getAttribute('aria-invalid'),'true');
  await type(scope,'Read only registered synthetic inputs and return a summary.','task scope');
  assert.equal(await scope.getAttribute('aria-invalid'),'false');
  const [saved]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/ordinary-drafts'&&r.request().method()==='POST'),
    activate(panel.getByRole('button',{name:'保存任务草稿',exact:true}),'save task draft')]);assert.equal(saved.status(),200);
  await panel.getByLabel('普通任务状态',{exact:true}).filter({hasText:'已保存'}).waitFor();
  await waitMessage(m=>m.ordinary_scenario==='input',{action:'ordinary-scenario',project_id:A,scenario:'input'});
  const [sent]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/tasks'&&r.request().method()==='POST'),
    activate(panel.getByRole('button',{name:'发送普通任务',exact:true}),'send task')]);
  assert.equal(sent.status(),200);const taskId=(await sent.json()).data.task.id;
  await waitMessage(m=>m.ordinary_runtime_started,{action:'ordinary-runtime-start',request_timeout:30});
  const input=page.getByLabel('检查关注点：请补充本次只读检查的关注点',{exact:true});await input.waitFor();
  evidence.states.keyboardDraft={fieldErrorsAssociated:true,editable:true,saved:true,singleTask:taskId};
  await type(input,'Keep this answer editable after a rejected response.','requested answer');
  const taskState=page.getByLabel('任务执行状态',{exact:true});await taskState.waitFor();
  await page.evaluate(()=>{
    const node=document.querySelector('[aria-label="任务执行状态"]');window.__taskStateNode=node;window.__sameStateWrites=0;
    window.__sameStateObserver=new MutationObserver(()=>window.__sameStateWrites++);
    window.__sameStateObserver.observe(node,{subtree:true,childList:true,characterData:true});
  });
  for(let i=0;i<3;i++)await page.waitForResponse(r=>new URL(r.url()).pathname==='/api/tasks/'+taskId&&r.request().method()==='GET');
  assert.ok(await focused(input));assert.equal(await input.inputValue(),'Keep this answer editable after a rejected response.');
  const stable=await page.evaluate(()=>({sameNode:window.__taskStateNode===document.querySelector('[aria-label="任务执行状态"]'),writes:window.__sameStateWrites}));
  assert.deepEqual(stable,{sameNode:true,writes:0});
  await captureAX('waiting input',[['status','任务执行状态'],['textbox','检查关注点：请补充本次只读检查的关注点']]);
  evidence.states.quietPolling={cycles:3,focusAndValuePreserved:true,...stable};

  const respondURL=origin+'/api/tasks/'+taskId+'/respond';expectedConsole.add(respondURL);let rejected=0;
  await context.route(respondURL,async route=>{rejected++;await route.fulfill({status:400,contentType:'application/json',
    body:JSON.stringify({ok:false,error:{code:'CONTROL_RESPONSE_INVALID',outcome:'NOT_COMMITTED',retryable:false}})});},{times:1});
  const [reject]=await Promise.all([page.waitForResponse(r=>r.url()===respondURL),activate(button('提交补充输入'),'submit answer with injected rejection')]);
  assert.equal(reject.status(),400);await page.waitForFunction(id=>{
    const n=document.getElementById(id);return n&&!n.disabled&&n===document.activeElement;
  },await input.getAttribute('id'));
  assert.equal(await input.inputValue(),'Keep this answer editable after a rejected response.');assert.equal(await input.getAttribute('aria-invalid'),'true');
  const answerDescription=await input.getAttribute('aria-describedby');assert.ok(answerDescription);assert.ok((await page.locator('[id="'+answerDescription+'"]').innerText()).trim());
  await type(input,'Inspect only the registered synthetic files.','corrected answer');
  await captureAX('rejected answer remains editable');
  const [answered]=await Promise.all([page.waitForResponse(r=>r.url()===respondURL),activate(button('提交补充输入'),'submit corrected answer')]);assert.equal(answered.status(),200);
  await page.getByLabel('普通任务执行回复',{exact:true}).filter({hasText:'Synthetic read-only response for '+A}).waitFor();
  assert.equal(rejected,1);assert.ok(await page.evaluate(()=>document.activeElement?.isConnected&&document.activeElement!==document.body));
  await captureAX('completed result',[['status','任务执行状态']]);
  assert.match(await taskState.innerText(),/检查完成|等待验收/);
  const observation=await waitMessage(m=>'ordinary_control_observations'in m,{action:'ordinary-observations'});
  assert.equal(observation.effects,0);assert.equal(observation.ordinary_control_observations.length,1);assert.equal(observation.ordinary_control_observations[0].decision,'answer');
  evidence.states.keyboardAnswerRecovery={rejectedBeforeCommit:1,originalValuePreserved:true,editable:true,acceptedOnce:true,replyCorrect:true};
  await activate(page.getByRole('link',{name:'返回项目任务区',exact:true}),'return to project task area');await panel.waitFor();
  await page.getByRole('heading',{name:'Synthetic Project A',exact:true}).waitFor();
  evidence.states.keyboardReturn={correctProject:A,correctSection:'tasks'};

  const projectURL=origin+'/api/projects/'+A;expectedConsole.add(projectURL);
  await context.route(projectURL,route=>route.fulfill({status:404,contentType:'application/json',
    body:JSON.stringify({ok:false,error:{code:'NOT_FOUND',outcome:'NOT_COMMITTED'}})}),{times:1});
  await activate(projectNav('排期与关系'),'schedule with injected read error');
  await page.getByRole('heading',{name:'对象不存在或已移除',exact:true}).waitFor();
  assert.ok(await focused(page.locator('#main')));await captureAX('route error',[['heading','对象不存在或已移除']]);
  await activate(page.getByRole('button',{name:'重新读取',exact:true}),'recover original route');
  await page.getByRole('heading',{name:'Synthetic Project A',exact:true}).waitFor();
  await page.waitForFunction(()=>document.activeElement?.id==='main');
  evidence.states.keyboardRouteError={readErrorFocused:true,recoveredSameProject:true,recoveredFocus:true};
  assert.deepEqual(unexpected,[]);assert.equal(evidence.tasks.length,1);
  await page.bringToFront();await page.screenshot({path:path.join(evidenceRoot,'o52-keyboard.png')});
  await writeFile(path.join(evidenceRoot,'o52-keyboard-ax.json'),JSON.stringify({keyboard:evidence.keyboard,ax:evidence.ax},null,2));
});
