// Only invoke with Root's explicitly identified, disposable loopback CDP.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {randomBytes} from 'node:crypto';
import {createRequire} from 'node:module';
import {readFile,writeFile,mkdir,mkdtemp,realpath,stat,statfs,rm,readdir,readlink} from 'node:fs/promises';
import path from 'node:path';

export function within(promise,ms=5000){
  let timer;
  return Promise.race([promise,new Promise((_,reject)=>{
    timer=setTimeout(()=>reject(new Error('bounded fixture operation timed out')),ms);
  })]).finally(()=>clearTimeout(timer));
}

export async function testStorage(directory){
  assert.ok(path.isAbsolute(directory),'test storage must be absolute');
  let existing=path.resolve(directory);
  while(true){
    try{assert.equal(await realpath(existing),existing,'test storage must not use symlinks');break;}
    catch(error){if(error.code!=='ENOENT')throw error;existing=path.dirname(existing);}
  }
  const ancestorFilesystem=await statfs(existing);
  assert.ok(![0x01021994,0x858458f6].includes(ancestorFilesystem.type),'test storage must not be tmpfs or ramfs');
  await mkdir(directory,{recursive:true,mode:0o700});
  assert.equal(await realpath(directory),path.resolve(directory),'test storage must not use symlinks');
  const identity=await stat(directory), filesystem=await statfs(directory);
  assert.ok(![0x01021994,0x858458f6].includes(filesystem.type),'test storage must not be tmpfs or ramfs');
  assert.equal(identity.uid,process.getuid(),'test storage must be owned by this user');
  assert.ok(identity.isDirectory());
  return identity;
}

// Optional injected transports support finite lifecycle regression tests without a browser.
export async function runBrowserJourney(name,journey,{directorySize=2,timeoutMs=120000,
  cleanupTimeoutMs=5000,signal,cdp=process.argv[2],evidenceRoot=process.argv[3],
  storageRoot=process.env.PCH_TEST_STORAGE||path.resolve(import.meta.dirname,'../.venv/pch-test-workspaces'),
  fixtureCommand,connectBrowser,signalProcess=(pid,terminationSignal)=>process.kill(pid,terminationSignal)}={}){
  assert.match(name,/^[a-zA-Z0-9_-]+$/);
  assert.match(cdp||'',/^http:\/\/127\.0\.0\.1:\d+$/);
  assert.ok(path.isAbsolute(evidenceRoot||''));
  assert.ok(timeoutMs>0&&cleanupTimeoutMs>0);
  await mkdir(evidenceRoot,{recursive:true,mode:0o700});
  const evidence={classification:'synthetic_fixture',states:{},tasks:[],network:[],console:[],consoleWarnings:[],
    browserTransport:{consoleErrors:[],requestFailed:[]}};
  let observationPhase='journey';
  function signalSafely(action){
    try{return action();}
    catch(error){
      // The verified process can exit between /proc inspection and kill(2).
      if(error.code==='ESRCH')return true;
      evidence.result='FAIL';process.exitCode=1;
      (evidence.cleanupSignalErrors||=[]).push({code:error.code||'SIGNAL_ERROR'});
      return false;
    }
  }
  let owner=randomBytes(32).toString('hex');
  let secret=owner;
  const sanitize=value=>secret?String(value).replaceAll(secret,'[redacted]'):String(value);
  const fixture={messages:[],waiters:[],stderr:''};
  let browser,page,workspace,workspaceIdentity,childDone,childTerminal=false,parseFailure,groupIdentity;
  let cancelReject,aborted=false;
  const cancelled=new Promise((_,reject)=>{cancelReject=reject;});
  // Attach immediately: cancellation may arrive during asynchronous storage setup.
  cancelled.catch(()=>{});
  const abort=()=>{aborted=true;cancelReject(new Error('browser journey cancelled'));};
  const signals=['SIGINT','SIGTERM'];
  for(const event of signals)process.on(event,abort);
  signal?.addEventListener('abort',abort,{once:true});
  if(signal?.aborted)abort();
  const timer=setTimeout(()=>{aborted=true;cancelReject(new Error('browser journey overall timeout'));},timeoutMs);
  async function waitMessage(test,action=null){
    if(parseFailure)throw parseFailure;
    const previous=!action&&fixture.messages.find(test);
    if(previous)return previous;
    if(childTerminal)throw new Error('fixture exited before control response');
    let waiter;
    const response=new Promise((resolve,reject)=>{waiter={test,resolve,reject};fixture.waiters.push(waiter);});
    if(action)fixture.child.stdin.write(JSON.stringify(action)+'\n');
    try{return await Promise.race([within(response),cancelled]);}
    finally{fixture.waiters=fixture.waiters.filter(w=>w!==waiter);}
  }
  try{
    await testStorage(storageRoot);
    workspace=await mkdtemp(path.join(storageRoot,'pch-browser-'));
    workspaceIdentity=await stat(workspace);
    if(aborted)throw new Error('browser journey cancelled');
    const childEnv={TMPDIR:workspace,PYTHONDONTWRITEBYTECODE:'1'};
    for(const key of ['PATH','HOME','LANG','LC_ALL','XDG_RUNTIME_DIR','XDG_CONFIG_HOME','DBUS_SESSION_BUS_ADDRESS'])
      if(process.env[key])childEnv[key]=process.env[key];
    const python=process.env.PCH_PYTHON_EXECUTABLE||'/usr/bin/python3';
    assert.ok(path.isAbsolute(python),'Fixture interpreter must be explicitly absolute');
    const command=fixtureCommand||[python,'tests/pch_functional_fixture.py','--serve','--owner-fd','3',
      '--directory-size',String(directorySize)];
    fixture.child=spawn(command[0],command.slice(1),{cwd:path.resolve(import.meta.dirname,'..'),
      env:childEnv,detached:true,stdio:['pipe','pipe','pipe','pipe']});
    childDone=new Promise(resolve=>{
      const finish=result=>{childTerminal=true;for(const waiter of fixture.waiters)waiter.reject(new Error('fixture exited before control response'));resolve(result);};
      fixture.child.once('error',()=>finish({spawnError:true}));
      fixture.child.once('close',(code,terminationSignal)=>finish({code,signal:terminationSignal}));
    });
    for(const stream of [fixture.child.stdin,fixture.child.stdio[3]])stream.on('error',()=>{});
    async function processIdentity(pid){
      const raw=await readFile('/proc/'+pid+'/stat','utf8');
      const fields=raw.slice(raw.lastIndexOf(') ')+2).split(' ');
      return {pid,start_ticks:fields[19],pgrp:Number(fields[2]),session:Number(fields[3]),state:fields[0]};
    }
    if(fixture.child.pid){
      groupIdentity=await processIdentity(fixture.child.pid).catch(()=>null);
      if(groupIdentity){assert.equal(groupIdentity.pgrp,fixture.child.pid);assert.equal(groupIdentity.session,fixture.child.pid);}
    }
    fixture.groupSignal=async terminationSignal=>{
      if(!groupIdentity)return false;
      const current=await processIdentity(groupIdentity.pid).catch(()=>null);
      if(!current||current.start_ticks!==groupIdentity.start_ticks||current.pgrp!==groupIdentity.pid||current.session!==groupIdentity.pid)return false;
      return signalSafely(()=>signalProcess(-groupIdentity.pid,terminationSignal));
    };
    fixture.groupMembers=async()=>{
      if(!groupIdentity)return [];
      const entries=await readdir('/proc');
      const members=await Promise.all(entries.filter(entry=>/^\d+$/.test(entry)).map(entry=>processIdentity(Number(entry)).catch(()=>null)));
      return members.filter(member=>member&&member.pgrp===groupIdentity.pid&&member.session===groupIdentity.pid&&member.state!=='Z');
    };
    fixture.killMember=async member=>{
      const current=await processIdentity(member.pid).catch(()=>null);
      if(current&&current.start_ticks===member.start_ticks&&current.pgrp===groupIdentity.pid&&current.session===groupIdentity.pid)signalSafely(()=>signalProcess(member.pid,'SIGKILL'));
    };
    let buffered='';
    fixture.child.stdout.on('data',chunk=>{
      buffered+=chunk;
      if(buffered.length>1024*1024){parseFailure=new Error('fixture control output exceeded limit');abort();return;}
      let end;
      while((end=buffered.indexOf('\n'))>=0){
        const line=buffered.slice(0,end);buffered=buffered.slice(end+1);
        try{
          const message=JSON.parse(line);fixture.messages.push(message);
          for(const waiter of [...fixture.waiters])if(waiter.test(message))waiter.resolve(message);
        }catch{parseFailure=new Error('fixture emitted invalid control JSON');for(const waiter of fixture.waiters)waiter.reject(parseFailure);}
      }
    });
    fixture.child.stderr.on('data',chunk=>{fixture.stderr=(fixture.stderr+sanitize(chunk)).slice(-12000);});
    fixture.child.stdio[3].end(owner+'\n');
    const operation=(async()=>{
      const info=await waitMessage(m=>m.ready);
      const raw=await readFile('/proc/'+fixture.child.pid+'/stat','utf8');
      evidence.fixtureIdentity={pid:fixture.child.pid,start_ticks:raw.slice(raw.lastIndexOf(') ')+2).split(' ')[19]};
      const pythonExecutable=await readlink('/proc/'+fixture.child.pid+'/exe');
      assert.equal(pythonExecutable,await realpath(command[0]),'Fixture interpreter identity must match its launch');
      evidence.pythonExecutable=pythonExecutable;
      await writeFile(path.join(evidenceRoot,'active-fixture.json'),JSON.stringify({...evidence.fixtureIdentity,origin:info.origin,python_executable:pythonExecutable}));
      const connector=connectBrowser||(async target=>{
        const require=createRequire(import.meta.url);
        const {chromium}=require(process.env.PCH_PLAYWRIGHT_MODULE||'playwright');
        return chromium.connectOverCDP(target,{timeout:3000});
      });
      const connected=await connector(cdp);
      if(aborted){
        const control=await within(connected.newBrowserCDPSession(),cleanupTimeoutMs);
        await within(control.send('Browser.close'),cleanupTimeoutMs);
        await within(connected.close(),cleanupTimeoutMs);
        throw new Error('browser journey cancelled during connection');
      }
      browser=connected;
      const context=browser.contexts()[0];
      await context.clearCookies();
      page=context.pages()[0]||await context.newPage();
      page.setDefaultTimeout(3000);page.setDefaultNavigationTimeout(3000);
      page.on('pageerror',error=>evidence.console.push(sanitize(error)));
      page.on('console',message=>{
        if(message.type()==='warning')evidence.consoleWarnings.push(sanitize(message.text()));
        if(message.type()==='error')evidence.browserTransport.consoleErrors.push({phase:observationPhase,
          url:message.location().url,text:sanitize(message.text())});
      });
      page.on('requestfailed',request=>evidence.browserTransport.requestFailed.push({phase:observationPhase,
        method:request.method(),url:request.url(),reason:request.failure()?.errorText}));
      page.on('response',response=>{
        const url=new URL(response.url());
        if(url.origin===info.origin)evidence.network.push({method:response.request().method(),path:url.pathname,status:response.status()});
      });
      page.on('request',request=>{
        if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/tasks')evidence.tasks.push(request.postDataJSON());
      });
      async function loginOwner({keyboard=false}={}){
        // Restarts reauthenticate with this fixture's in-memory synthetic proof.
        assert.ok(!aborted&&secret,'synthetic owner is unavailable after cancellation');
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
    })();
    await Promise.race([operation,cancelled]);
    evidence.result='PASS';
  }catch(error){
    evidence.result='FAIL';evidence.failure=sanitize(error);process.exitCode=1;
    if(page)try{
      evidence.lastURL=page.url();
      evidence.lastDOM=sanitize((await within(page.locator('body').innerText(),1000)).slice(0,9000));
      evidence.historyWrites=await within(page.evaluate(()=>window.historyWrites||null),1000);
    }catch{}
    if(fixture.stderr)evidence.fixtureError=fixture.stderr;
  }finally{
    observationPhase='teardown';
    clearTimeout(timer);owner=null;
    if(browser)try{
      const control=await within(browser.newBrowserCDPSession(),cleanupTimeoutMs);
      await within(control.send('Browser.close'),cleanupTimeoutMs);evidence.browserClose=true;
      await within(browser.close(),cleanupTimeoutMs);
    }catch{evidence.browserClose=false;evidence.result='FAIL';process.exitCode=1;}
    if(fixture.child){
      if(!childTerminal)fixture.child.stdin.end(JSON.stringify({action:'stop'})+'\n');
      try{evidence.fixtureTermination=await within(childDone,cleanupTimeoutMs);}
      catch{
        // ChildProcess retains the spawned identity; never signal a looked-up PID or process name.
        if(!await fixture.groupSignal('SIGTERM'))signalSafely(()=>fixture.child.kill('SIGTERM'));
        try{evidence.fixtureTermination=await within(childDone,cleanupTimeoutMs);}
        catch{if(!await fixture.groupSignal('SIGKILL'))signalSafely(()=>fixture.child.kill('SIGKILL'));evidence.fixtureTermination=await within(childDone,cleanupTimeoutMs).catch(()=>null);}
        evidence.result='FAIL';evidence.cleanupFailure='fixture required termination';process.exitCode=1;
      }
      // A leader may exit while its spawned batch still owns the session/socket.
      const survivors=await fixture.groupMembers?.()||[];
      for(const member of survivors)await fixture.killMember(member);
      if(survivors.length){
        evidence.result='FAIL';evidence.cleanupFailure='fixture descendants required termination';process.exitCode=1;
        const deadline=Date.now()+cleanupTimeoutMs;
        while((await fixture.groupMembers()).length&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,10));
      }
      evidence.fixtureGroupTerminal=!(await fixture.groupMembers?.()||[]).length;
      if(!evidence.fixtureGroupTerminal){evidence.result='FAIL';evidence.cleanupFailure='fixture descendants remain';process.exitCode=1;}
      evidence.fixtureTerminal=childTerminal;
      evidence.fixtureExit=evidence.fixtureTermination?.code;
      if(evidence.fixtureExit!==0){evidence.result='FAIL';process.exitCode=1;}
    }
    if(workspace&&(!fixture.child||(childTerminal&&evidence.fixtureGroupTerminal))){
      const current=await stat(workspace).catch(()=>null);
      if(current&&current.ino===workspaceIdentity.ino&&current.dev===workspaceIdentity.dev&&await realpath(workspace)===workspace){
        await rm(workspace,{recursive:true});evidence.workspaceRemoved=true;
      }else{evidence.result='FAIL';evidence.cleanupFailure='workspace identity changed';process.exitCode=1;}
    }
    for(const event of signals)process.removeListener(event,abort);
    signal?.removeEventListener('abort',abort);
    await writeFile(path.join(evidenceRoot,name+'-browser.json'),sanitize(JSON.stringify(evidence,null,2)));
    secret=null;
    console.log(JSON.stringify({result:evidence.result,stateChecks:Object.keys(evidence.states),directoryCombinations:evidence.states.directoryMatrix?.length,taskRequests:evidence.tasks.length,
      consoleErrors:evidence.console.length,fixtureExit:evidence.fixtureExit,failure:evidence.failure}));
  }
  return evidence;
}
