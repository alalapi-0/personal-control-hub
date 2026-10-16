// Only invoke with Root's explicitly identified, disposable loopback CDP.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {randomBytes} from 'node:crypto';
import {createRequire} from 'node:module';
import {readFile,writeFile,mkdir,readlink,realpath} from 'node:fs/promises';
import {once} from 'node:events';
import path from 'node:path';

export function within(promise,ms=5000){
  let timer;
  return Promise.race([promise,new Promise((_,reject)=>{
    timer=setTimeout(()=>reject(new Error('bounded fixture operation timed out')),ms);
  })]).finally(()=>clearTimeout(timer));
}

export async function runBrowserJourney(name,journey,{directorySize=2}={}){
  const [cdp,evidenceRoot]=process.argv.slice(2);
  assert.match(cdp||'',/^http:\/\/127\.0\.0\.1:\d+$/);
  assert.ok(path.isAbsolute(evidenceRoot||''));
  await mkdir(evidenceRoot,{recursive:true,mode:0o700});
  const require=createRequire(import.meta.url);
  const {chromium}=require(process.env.PCH_PLAYWRIGHT_MODULE||'playwright');
  let owner=randomBytes(32).toString('hex');
  const secret=owner;
  const sanitize=value=>String(value).replaceAll(secret,'[redacted]');
  const fixture={messages:[],waiters:[],stderr:''};
  const python=process.env.PCH_PYTHON_EXECUTABLE||'/usr/bin/python3';
  assert.ok(path.isAbsolute(python),'Fixture interpreter must be explicitly absolute');
  fixture.child=spawn(python,['tests/pch_functional_fixture.py','--serve','--owner-fd','3',
    '--directory-size',String(directorySize)],{cwd:path.resolve(import.meta.dirname,'..'),stdio:['pipe','pipe','pipe','pipe']});
  let buffered='',parseFailure;
  fixture.child.stdout.on('data',chunk=>{
    buffered+=chunk;
    let end;
    while((end=buffered.indexOf('\n'))>=0){
      const line=buffered.slice(0,end);buffered=buffered.slice(end+1);
      try{
        const message=JSON.parse(line);fixture.messages.push(message);
        for(const waiter of [...fixture.waiters])if(waiter.test(message))waiter.resolve(message);
      }catch{parseFailure=new Error('fixture emitted invalid control JSON');}
    }
  });
  fixture.child.stderr.on('data',chunk=>{fixture.stderr=(fixture.stderr+sanitize(chunk)).slice(-12000);});
  fixture.child.stdio[3].end(owner+'\n');
  async function waitMessage(test,action=null){
    if(parseFailure)throw parseFailure;
    let waiter;
    const promise=new Promise(resolve=>{waiter={test,resolve};fixture.waiters.push(waiter);});
    if(action)fixture.child.stdin.write(JSON.stringify(action)+'\n');
    try{return await within(promise);}finally{fixture.waiters=fixture.waiters.filter(w=>w!==waiter);}
  }
  const evidence={classification:'synthetic_fixture',states:{},tasks:[],network:[],console:[],consoleWarnings:[]};
  let browser,page;
  try{
    const info=fixture.messages.find(m=>m.ready)||await waitMessage(m=>m.ready);
    const stat=await readFile('/proc/'+fixture.child.pid+'/stat','utf8');
    const pythonExecutable=await readlink('/proc/'+fixture.child.pid+'/exe');
    assert.equal(pythonExecutable,await realpath(python),'Fixture interpreter identity must match its launch');
    await writeFile(path.join(evidenceRoot,'active-fixture.json'),JSON.stringify({pid:fixture.child.pid,
      start_ticks:stat.slice(stat.lastIndexOf(') ')+2).split(' ')[19],origin:info.origin,python_executable:pythonExecutable}));
    evidence.pythonExecutable=pythonExecutable;
    browser=await chromium.connectOverCDP(cdp,{timeout:3000});
    const context=browser.contexts()[0];
    await context.clearCookies();
    page=context.pages()[0]||await context.newPage();
    page.setDefaultTimeout(3000);page.setDefaultNavigationTimeout(3000);
    page.on('pageerror',error=>evidence.console.push(sanitize(error)));
    page.on('console',message=>{
      if(message.type()==='warning')evidence.consoleWarnings.push(sanitize(message.text()));
    });
    page.on('response',response=>{
      const url=new URL(response.url());
      if(url.origin===info.origin)evidence.network.push({method:response.request().method(),path:url.pathname,status:response.status()});
    });
    page.on('request',request=>{
      if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/tasks')evidence.tasks.push(request.postDataJSON());
    });
    async function loginOwner({keyboard=false}={}){
      // The isolated restart journey reauthenticates through the real UI using
      // only this fixture's in-memory synthetic proof, never a provider secret.
      owner=secret;
      if(keyboard){
        const connect=page.getByRole('button',{name:'连接所有者',exact:true});
        let reached=false;
        for(let step=0;step<100;step++){
          if(await connect.evaluate(node=>node===document.activeElement)){reached=true;break;}
          await page.keyboard.press('Tab');
        }
        assert.ok(reached,'Owner connection must be reachable by keyboard');
        await page.keyboard.press('Enter');
        const proof=page.getByLabel('所有者凭据',{exact:true});
        await proof.waitFor();assert.ok(await proof.evaluate(node=>node===document.activeElement));
        await page.keyboard.insertText(owner);await page.keyboard.press('Enter');
      }else{
        await page.getByRole('button',{name:'连接所有者',exact:true}).click();
        await page.getByLabel('所有者凭据',{exact:true}).fill(owner);
        await page.getByRole('button',{name:'认证',exact:true}).click();
      }
      await page.getByRole('button',{name:'断开所有者',exact:true}).waitFor();owner=null;
    }
    await journey({page,context,evidence,evidenceRoot,origin:info.origin,waitMessage,loginOwner,browser});
    assert.deepEqual(evidence.console,[]);
    evidence.result='PASS';
  }catch(error){
    evidence.result='FAIL';evidence.failure=sanitize(error);process.exitCode=1;
    if(page)try{evidence.lastURL=page.url();evidence.lastDOM=sanitize((await page.locator('body').innerText()).slice(0,9000));
      evidence.historyWrites=await page.evaluate(()=>window.historyWrites||null);}catch{}
    if(fixture.stderr)evidence.fixtureError=fixture.stderr;
  }finally{
    owner=null;
    if(fixture.child.exitCode===null){
      const exited=once(fixture.child,'exit');
      fixture.child.stdin.end(JSON.stringify({action:'stop'})+'\n');
      try{await within(exited);}catch{
        // This child was spawned above and remains uncollected: no PID lookup,
        // broad process match, or unrelated worker can be affected.
        fixture.child.kill('SIGTERM');await within(exited).catch(()=>{});
        evidence.result='FAIL';evidence.cleanupFailure='fixture required termination';process.exitCode=1;
      }
    }
    evidence.fixtureExit=fixture.child.exitCode;
    if(evidence.fixtureExit!==0){evidence.result='FAIL';process.exitCode=1;}
    if(browser)try{
      const control=await browser.newBrowserCDPSession();await control.send('Browser.close');
      evidence.browserClose=true;
    }catch{evidence.browserClose=false;}
    if(browser)await browser.close().catch(()=>{});
    await writeFile(path.join(evidenceRoot,name+'-browser.json'),sanitize(JSON.stringify(evidence,null,2)));
    console.log(JSON.stringify({result:evidence.result,stateChecks:Object.keys(evidence.states),directoryCombinations:evidence.states.directoryMatrix?.length,taskRequests:evidence.tasks.length,
      consoleErrors:evidence.console.length,fixtureExit:evidence.fixtureExit,failure:evidence.failure}));
  }
}
